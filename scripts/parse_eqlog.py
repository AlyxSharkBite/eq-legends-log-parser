#!/usr/bin/env python3
"""EverQuest Legends combat-log parser.

Reads an eqlog_<Character>_<server>.txt file and writes, into --out:
  summary.md    markdown report (also printed to stdout)
  summary.json  the same numbers, machine readable
  fights.csv    one row per fight
  *.png         charts (needs matplotlib; skipped with a notice if missing)

Standard library only, apart from the optional matplotlib import.
Run with --help for filters. See ../SKILL.md for how to interpret the output.
"""
import argparse, csv, json, os, re, sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

# --------------------------------------------------------------------------- line grammar
MON = {m: i + 1 for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}
LINE = re.compile(r"^\[\w{3} (\w{3}) (\d{2}) (\d{2}):(\d{2}):(\d{2}) (\d{4})\] (.*)$")

# Melee verbs: base form -> 3rd-person form. Add new ones here if summary.md reports unparsed lines.
VERBS = {
    "hit": "hits", "slash": "slashes", "crush": "crushes", "pierce": "pierces", "bash": "bashes",
    "kick": "kicks", "punch": "punches", "bite": "bites", "claw": "claws", "strike": "strikes",
    "slice": "slices", "smash": "smashes", "backstab": "backstabs", "maul": "mauls", "sting": "stings",
    "cleave": "cleaves", "reave": "reaves", "smite": "smites", "shoot": "shoots", "gore": "gores",
    "rend": "rends", "slam": "slams", "sweep": "sweeps", "gouge": "gouges", "burn": "burns",
    "frenzy on": "frenzies on",
}
VERB_BASE = {v: k for k, v in VERBS.items()}
VERB_BASE.update({k: k for k in VERBS})
_V = "|".join(sorted(map(re.escape, VERB_BASE), key=len, reverse=True))
_F = r"(?: \((?P<f>[^)]*)\))?\s*$"

MELEE = re.compile(rf"^(?P<a>.+?) (?P<v>{_V}) (?P<t>.+?) for (?P<n>\d+) points? of damage\." + _F)
MISS = re.compile(r"^(?P<a>.+?) (?:try|tries) to (?P<v>[a-z]+(?: on)?) (?P<t>.+?), but (?P<o>.+?)!" + _F)
SPELL = re.compile(r"^(?P<a>.+?) hit (?P<t>.+?) for (?P<n>\d+) points? of (?P<ty>[a-z]+) damage by (?P<s>.+?)\." + _F)
DOT_MINE = re.compile(r"^(?P<t>.+?) has taken (?P<n>\d+) damage from your (?P<s>.+?)\." + _F)
DOT_BY = re.compile(r"^(?P<t>.+?) (?:has|have) taken (?P<n>\d+) damage from (?P<s>.+?) by (?P<a>.+?)\." + _F)
DOT_ANON = re.compile(r"^You have taken (?P<n>\d+) damage from (?P<s>.+?)\." + _F)
DSHIELD = re.compile(r"^(?P<t>.+?) (?:is|are) \w+ by (?:(?P<me>YOUR)|(?P<a>.+?)'s) (?P<s>\w+) "
                     r"for (?P<n>\d+) points? of non-melee damage[.!]")
HEAL = re.compile(r"^(?P<a>.+?) healed (?P<t>.+?)(?P<hot> over time)? for (?P<n>\d+)(?: \((?P<g>\d+)\))? "
                  r"hit points? by (?P<s>.+?)\." + _F)
SLAIN_ME = re.compile(r"^You have slain (.+?)!$")
SLAIN_BY = re.compile(r"^(.+?) has been slain by (.+?)!$")
DIED = re.compile(r"^You have been slain by (.+?)!$")
ZONE = re.compile(r"^You have entered (?!an area)(.+?)\.$")
PET_TELL = re.compile(r"^(\w+) told you, '.*Master[.!]?'$")
# Classic EQ generated pet names: Xabtik, Jarektik, Koner, Xebarab, Kebn ...
PET_NAME = re.compile(r"^[GJKLVXZ][aeio][bknrs]?(?:[aeio][bknrs])*(?:ab|er|n|tik)$")
CRIT_FLAGS = ("Critical", "Crippling Blow")
FLAG_TOKENS = ["Critical", "Crippling Blow", "Finishing Blow", "Slay Undead", "Double Bow Shot",
               "Wild Rampage", "Rampage", "Flurry", "Riposte", "Strikethrough"]
AVOID = (("dodge", "dodge"), ("parr", "parry"), ("block", "block"), ("riposte", "riposte"),
         ("absorb", "absorb"), ("INVULNERABLE", "invulnerable"))


def split_flags(f):
    out, rest = [], f or ""
    for tok in FLAG_TOKENS:
        if tok in rest:
            out.append(tok)
            rest = rest.replace(tok, "")
    return out


class Names:
    """Case-insensitive name registry ('A puma' at line start == 'a puma' mid-sentence)."""
    def __init__(self, me):
        self.me, self.mekey, self.disp = me, me.lower(), {me.lower(): me}

    def key(self, raw, actor=None, mid=False):
        raw = raw.strip()
        if raw in ("You", "YOU", "you", "yourself"):
            return self.mekey
        if raw in ("itself", "himself", "herself", "themselves") and actor:
            return actor
        k = raw.lower()
        if mid or k not in self.disp:
            self.disp[k] = raw
        return k


def new_stat():
    return {"total": 0, "hits": 0, "crits": 0, "max": 0, "miss": 0, "avoided": 0, "kind": ""}


# --------------------------------------------------------------------------- parse
def parse(path, a):
    m = re.match(r"eqlog_(.+?)_(.+?)\.txt$", os.path.basename(path), re.I)
    me = a.me or (m.group(1) if m else "You")
    server = m.group(2) if m else "?"
    names = Names(me)
    ev, misses, heals, slain, deaths, zones, unparsed = [], [], [], [], [], [], Counter()
    unparsed_ex, pets_auto = [], set()
    zone, nlines, first, last, tcache = None, 0, None, None, {}
    zf = a.zone.lower() if a.zone else None
    sessions = []  # [start, end, Counter(zone), my damage lines]
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            mm = LINE.match(raw.rstrip("\r\n"))
            if not mm:
                continue
            mon, d, H, M, S, Y, b = mm.groups()
            tk = raw[1:25]
            t = tcache.get(tk)
            if t is None:
                tcache.clear()
                t = tcache[tk] = datetime(int(Y), MON[mon], int(d), int(H), int(M), int(S))
            if not sessions or (t - sessions[-1][1]).total_seconds() > a.session_gap * 60:
                sessions.append([t, t, Counter(), 0])
            sess = sessions[-1]
            sess[1] = t
            zm = ZONE.match(b) if b.startswith("You have entered") else None
            if zm:
                zone = zm.group(1)
                zones.append((t, zone))
            if zone:
                sess[2][zone] += 1
            if b.endswith("Master.'") or b.endswith("Master!'"):  # pet acknowledgements, whole file
                pm = PET_TELL.match(b)
                if pm:
                    pets_auto.add(pm.group(1).lower())
                    names.key(pm.group(1))
            if (a.since and t < a.since) or (a.until and t > a.until) or (zf and (not zone or zf not in zone.lower())):
                continue
            first = first or t
            last = t
            nlines += 1
            if b.endswith("'"):  # chat / tells
                continue
            if " damage" in b:
                if b.startswith("You "):
                    sess[3] += 1
                x = None
                if "non-melee" in b:
                    x = DSHIELD.match(b)
                    if x:
                        act = names.mekey if x["me"] else names.key(x["a"], mid=True)
                        ev.append((t, act, names.key(x["t"]), int(x["n"]), "ds", "damage shield (%s)" % x["s"], (), zone))
                        continue
                elif " of damage." in b:
                    x = MELEE.match(b)
                    if x:
                        act = names.key(x["a"])
                        ev.append((t, act, names.key(x["t"], mid=True), int(x["n"]), "melee",
                                   VERB_BASE[x["v"]], tuple(split_flags(x["f"])), zone))
                        continue
                elif " damage by " in b:
                    x = SPELL.match(b)
                    if x:
                        act = names.key(x["a"])
                        ev.append((t, act, names.key(x["t"], mid=True), int(x["n"]), "spell", x["s"],
                                   tuple(split_flags(x["f"])), zone))
                        continue
                if " taken " in b:
                    x = DOT_MINE.match(b)
                    if x:
                        ev.append((t, names.mekey, names.key(x["t"]), int(x["n"]), "dot", x["s"] + " (DoT)",
                                   tuple(split_flags(x["f"])), zone))
                        continue
                    x = DOT_BY.match(b)
                    if x:
                        ev.append((t, names.key(x["a"], mid=True), names.key(x["t"]), int(x["n"]), "dot",
                                   x["s"] + " (DoT)", tuple(split_flags(x["f"])), zone))
                        continue
                    x = DOT_ANON.match(b)
                    if x:
                        ev.append((t, "(unknown)", names.mekey, int(x["n"]), "dot", x["s"] + " (DoT)", (), zone))
                        continue
                if re.search(r"\d+ points? of (?:\w+ )?damage", b):
                    unparsed[re.sub(r"\d+", "N", b)[:90]] += 1
                    if len(unparsed_ex) < 8:
                        unparsed_ex.append(b[:160])
                continue
            if ", but " in b and (" try to " in b or " tries to " in b):
                x = MISS.match(b)
                if x:
                    o, res = x["o"], "miss"
                    for needle, lab in AVOID:
                        if needle in o:
                            res = lab
                            break
                    act = names.key(x["a"])
                    misses.append((t, act, names.key(x["t"], mid=True), VERB_BASE.get(x["v"], x["v"]), res, zone))
                continue
            if " healed " in b:
                x = HEAL.match(b)
                if x:
                    act = names.key(x["a"])
                    heals.append((t, act, names.key(x["t"], actor=act, mid=True), int(x["n"]),
                                  int(x["g"]) if x["g"] else int(x["n"]), x["s"] + (" (HoT)" if x["hot"] else ""),
                                  "Critical" in (x["f"] or "")))
                continue
            if "slain" in b:
                x = DIED.match(b)
                if x:
                    deaths.append((t, x.group(1), zone))
                    continue
                x = SLAIN_ME.match(b)
                if x:
                    slain.append((t, names.key(x.group(1), mid=True), names.mekey))
                    continue
                x = SLAIN_BY.match(b)
                if x:
                    slain.append((t, names.key(x.group(1)), names.key(x.group(2), mid=True)))
    return dict(me=me, server=server, names=names, ev=ev, misses=misses, heals=heals, slain=slain,
                deaths=deaths, zones=zones, unparsed=unparsed, unparsed_ex=unparsed_ex, pets_auto=pets_auto,
                nlines=nlines, first=first, last=last, sessions=sessions)


# --------------------------------------------------------------------------- analysis
def analyse(P, a):
    names, mek = P["names"], P["names"].mekey
    pets = set() if a.no_pets else set(P["pets_auto"])
    if not a.no_pets:  # "Tamsin`s warder", "Tamsin`s pet" ...
        pets |= {k for k in names.disp if k.startswith(mek + "`s ") or k.startswith(mek + "'s ")}
    if a.all_pets:  # every classic pet-style name seen dealing damage
        pets |= {e[1] for e in P["ev"] if PET_NAME.match(names.disp.get(e[1], ""))}
    named = {p.lower() for p in a.pet}
    pets |= named
    for p in a.pet:
        names.disp.setdefault(p.lower(), p)
    friendly = pets | {mek}
    charm = {p for p in named if not PET_NAME.match(names.disp.get(p, p))}  # NPC-named (charmed) pets
    tf = a.target.lower() if a.target else None
    af = a.actor.lower() if a.actor else None

    def side(act, tgt):
        """-> (enemy, 'out'|'in') or None for third-party events."""
        fa, ft = act in friendly, tgt in friendly
        if act == tgt:
            return None
        if fa and ft:  # charm pet fighting its owner (charm broke) or owner re-engaging it
            return (act, "in") if act in charm else (tgt, "out")
        if fa:
            return tgt, "out"
        if ft:
            return act, "in"
        return None

    out = defaultdict(lambda: defaultdict(new_stat))   # actor -> source -> stat
    flags = defaultdict(Counter)                        # actor -> flag -> n
    taken = defaultdict(lambda: defaultdict(new_stat))  # victim -> attacker -> stat
    taken_src = defaultdict(Counter)
    avoid = defaultdict(Counter)                        # victim -> outcome -> n
    others = defaultdict(lambda: [0, 0, Counter()])     # third-party actor -> [dmg, hits, targets]
    fights, open_f = [], {}
    kills = {}
    for t, victim, killer in P["slain"]:
        kills.setdefault(victim, []).append((t, killer))

    def close(f, result="idle"):
        f["result"] = result
        del open_f[f["enemy"]]
        if f["out"] > 0:
            fights.append(f)

    def touch(t, enemy, zone):
        f = open_f.get(enemy)
        if f and (t - f["end"]).total_seconds() > a.fight_gap:
            close(f)
            f = None
        if not f:
            f = open_f[enemy] = dict(enemy=enemy, start=t, end=t, zone=zone, out=0, inc=0,
                                     by=Counter(), sec=Counter(), result="")
        f["end"] = t
        return f

    stream = [(e[0], 0, e) for e in P["ev"]] + [(m[0], 1, m) for m in P["misses"]] + \
             [(s[0], 2, s) for s in P["slain"]]
    stream.sort(key=lambda r: (r[0], r[1]))
    for t, typ, e in stream:
        if typ == 2:
            f = open_f.get(e[1])
            if f:
                stale = (t - f["end"]).total_seconds() > a.fight_gap
                if not stale:
                    f["end"] = t
                close(f, "idle" if stale else "killed")
            continue
        act, tgt = e[1], e[2]
        sd = side(act, tgt)
        if sd is None:
            if typ == 0 and act != tgt and act != "(unknown)":
                o = others[act]
                o[0] += e[3]; o[1] += 1; o[2][tgt] += 1
            elif typ == 0 and tgt == mek:
                taken[mek][act]["total"] += e[3]; taken[mek][act]["hits"] += 1
                taken_src[mek][e[5]] += e[3]
            continue
        enemy, direction = sd
        if tf and tf not in names.disp.get(enemy, enemy).lower():
            continue
        if direction == "out" and af and af not in names.disp.get(act, act).lower():
            continue
        f = touch(t, enemy, e[-1])
        if typ == 0:
            _, _, _, n, kind, src, fl, _ = e
            if direction == "out":
                s = out[act][src]
                s["kind"] = kind
                s["total"] += n; s["hits"] += 1; s["max"] = max(s["max"], n)
                if any(c in fl for c in CRIT_FLAGS):
                    s["crits"] += 1
                for x in fl:
                    flags[act][x] += 1
                f["out"] += n; f["by"][act] += n
                f["sec"][int((t - f["start"]).total_seconds())] += n
            else:
                s = taken[tgt][act]
                s["total"] += n; s["hits"] += 1; s["max"] = max(s["max"], n)
                taken_src[tgt][src if kind != "melee" else "melee"] += n
                avoid[tgt]["hit" if kind == "melee" else kind] += 1
                f["inc"] += n
        else:
            _, _, _, verb, res, _ = e
            if direction == "out":
                s = out[act][verb]
                s["kind"] = "melee"
                s["miss" if res == "miss" else "avoided"] += 1
            else:
                avoid[tgt][res] += 1
    for f in list(open_f.values()):
        close(f, "open at end of range")
    fights.sort(key=lambda f: f["start"])

    W = a.burst_window
    for i, f in enumerate(fights, 1):
        f["n"] = i
        f["dur"] = int((f["end"] - f["start"]).total_seconds()) + 1
        f["dps"] = f["out"] / f["dur"]
        w = min(W, f["dur"])
        secs = sorted(f["sec"])
        best = cur = lo = 0
        for hi, s in enumerate(secs):
            cur += f["sec"][s]
            while secs[lo] <= s - w:
                cur -= f["sec"][secs[lo]]; lo += 1
            best = max(best, cur)
        f["burst"] = best / w
        f["win"] = w

    def active(actor=None):
        iv = sorted((f["start"], f["end"]) for f in fights if actor is None or f["by"].get(actor))
        tot, cs, ce = 0, None, None
        for s, e in iv:
            if ce is None or s > ce:
                if ce is not None:
                    tot += (ce - cs).total_seconds() + 1
                cs, ce = s, e
            else:
                ce = max(ce, e)
        if ce is not None:
            tot += (ce - cs).total_seconds() + 1
        return int(tot)

    actors = []
    grand = sum(s["total"] for srcs in out.values() for s in srcs.values()) or 1
    for act, srcs in out.items():
        tot = sum(s["total"] for s in srcs.values())
        hits = sum(s["hits"] for s in srcs.values())
        mh = sum(s["hits"] for s in srcs.values() if s["kind"] == "melee")
        mm = sum(s["miss"] + s["avoided"] for s in srcs.values())
        crit = sum(s["crits"] for s in srcs.values())
        sec = active(act)
        role = "you" if act == mek else ("charm pet" if act in charm else "pet")
        actors.append(dict(actor=names.disp.get(act, act), key=act, role=role, damage=tot, share=tot / grand,
                           active_s=sec, dps=tot / sec if sec else 0, hits=hits, melee_hits=mh, misses=mm,
                           hit_pct=mh / (mh + mm) if mh + mm else None, crits=crit,
                           crit_pct=crit / hits if hits else 0, max=max((s["max"] for s in srcs.values()), default=0)))
    actors.sort(key=lambda r: -r["damage"])
    tsec = active()
    total = dict(damage=grand if out else 0, active_s=tsec, dps=(grand / tsec) if out and tsec else 0)
    return dict(out=out, flags=flags, taken=taken, taken_src=taken_src, avoid=avoid, others=others,
                fights=fights, actors=actors, total=total, pets=pets, charm=charm, kills=kills)


# --------------------------------------------------------------------------- report
def fmt(n):
    return f"{n:,.0f}"


def pct(x):
    return "–" if x is None else f"{100 * x:.1f}%"


def dur(s):
    s = int(s)
    return f"{s // 3600}h {s % 3600 // 60:02d}m" if s >= 3600 else f"{s // 60}m {s % 60:02d}s"


def table(head, rows):
    L = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    L += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(L)


def report(P, R, a):
    names, mek, D = P["names"], P["names"].mekey, P["names"].disp
    L = [f"# EQ Legends parse — {P['me']} ({P['server']})", ""]
    if not P["first"]:
        return "\n".join(L + ["No log lines matched the selected range/zone."])
    zs = Counter(z for _, z in P["zones"])
    L += [f"- **Range:** {P['first']} → {P['last']}  ({P['nlines']:,} log lines read)",
          f"- **Filters:** " + (", ".join(f"{k}={v}" for k, v in (("zone", a.zone), ("target", a.target),
                                ("actor", a.actor), ("since", a.since), ("until", a.until)) if v) or "none"),
          f"- **Pets counted as yours:** " + (", ".join(sorted(
              D.get(p, p) for p in R["pets"] if p in R["out"] or p in R["taken"])) or "none active in this range"),
          f"- **Fights:** {len(R['fights'])} (gap {a.fight_gap}s), "
          f"{sum(1 for f in R['fights'] if f['result'] == 'killed')} ended in a kill; "
          f"**your deaths:** {len(P['deaths'])}", ""]
    T = R["total"]
    if not R["fights"]:
        L += ["**No damage dealt by you or your pets matched these filters.** Check `--list` for sessions/zones, "
              "loosen `--target`/`--actor`, or see *Other combatants* below for uncounted pets.", ""]
    L += ["## Damage dealt", "",
          f"**{fmt(T['damage'])} damage over {dur(T['active_s'])} of combat = {T['dps']:.1f} DPS (you + pets)**", "",
          table(["Actor", "Role", "Damage", "Share", "Active", "DPS", "Hits", "Misses", "Hit %", "Crits", "Crit %", "Max hit"],
                [[r["actor"], r["role"], fmt(r["damage"]), pct(r["share"]), dur(r["active_s"]), f"{r['dps']:.1f}",
                  fmt(r["hits"]), fmt(r["misses"]), pct(r["hit_pct"]), fmt(r["crits"]), pct(r["crit_pct"]),
                  fmt(r["max"])] for r in R["actors"]]), ""]
    for r in R["actors"][: a.top_actors]:
        srcs = sorted(R["out"][r["key"]].items(), key=lambda kv: -kv[1]["total"])
        rows = []
        for src, s in srcs[: a.top]:
            sw = s["hits"] + s["miss"] + s["avoided"]
            rows.append([src, s["kind"], fmt(s["total"]), pct(s["total"] / (r["damage"] or 1)), fmt(s["hits"]),
                         fmt(s["miss"]), fmt(s["avoided"]),
                         pct(s["hits"] / sw) if s["kind"] == "melee" and sw else "–",
                         fmt(s["crits"]), pct(s["crits"] / s["hits"]) if s["hits"] else "–",
                         f"{s['total'] / s['hits']:.0f}" if s["hits"] else "–", fmt(s["max"])])
        L += [f"### {r['actor']} — by source", "",
              table(["Source", "Type", "Damage", "Share", "Hits", "Missed", "Avoided", "Hit %", "Crits", "Crit %", "Avg", "Max"], rows), ""]
        fl = R["flags"].get(r["key"])
        if fl:
            L += ["Special attacks: " + ", ".join(f"{k} ×{v:,}" for k, v in fl.most_common()), ""]
    F = R["fights"]
    if F:
        W = a.burst_window
        big = [f for f in F if f["dur"] >= W] or F
        pk = max(big, key=lambda f: f["burst"])
        med = sorted(f["dps"] for f in F)[len(F) // 2]
        L += ["## Burst vs. sustained", "",
              f"- **Sustained:** {T['dps']:.1f} DPS across all combat time; median fight {med:.1f} DPS.",
              f"- **Peak burst:** {pk['burst']:.1f} DPS over a {pk['win']}s window (fight #{pk['n']}, "
              f"{D.get(pk['enemy'], pk['enemy'])}, {pk['start']}) — {pk['burst'] / T['dps']:.1f}× sustained." if T["dps"] else "", ""]
        top = sorted(F, key=lambda f: -f["out"])[: a.top]
        L += [f"## Top {len(top)} fights by damage", "",
              table(["#", "Start", "Zone", "Target", "Length", "Damage", "DPS", f"Burst DPS ({W}s)", "Taken", "Result"],
                    [[f["n"], f["start"], f["zone"] or "?", D.get(f["enemy"], f["enemy"]), dur(f["dur"]), fmt(f["out"]),
                      f"{f['dps']:.1f}", f"{f['burst']:.1f}", fmt(f["inc"]), f["result"]] for f in top]), ""]
    prow = []
    for p in sorted(R["pets"]):
        tk = R["taken"].get(p)
        if tk:
            worst = max(tk.items(), key=lambda kv: kv[1]["total"])
            prow.append([D.get(p, p), fmt(sum(s["total"] for s in tk.values())), fmt(sum(s["hits"] for s in tk.values())),
                         f"{D.get(worst[0], worst[0])} ({fmt(worst[1]['total'])})"])
    for victim in [mek]:
        tk = R["taken"].get(victim)
        av = R["avoid"].get(victim)
        if not tk and not av:
            continue
        tot = sum(s["total"] for s in tk.values()) if tk else 0
        L += [f"## Damage taken — {D.get(victim, victim)}", "", f"**{fmt(tot)} total.**", ""]
        if av:
            sw = sum(av[k] for k in ("hit", "miss", "dodge", "parry", "block", "riposte", "absorb", "invulnerable"))
            L += ["Melee swings at you: " + ", ".join(
                f"{k} {av[k]:,} ({pct(av[k] / sw)})" for k in ("hit", "miss", "dodge", "parry", "block", "riposte", "absorb", "invulnerable") if av[k]), ""]
        if tk:
            L += [table(["Attacker", "Damage", "Hits", "Max"],
                        [[D.get(k, k), fmt(s["total"]), fmt(s["hits"]), fmt(s["max"])]
                         for k, s in sorted(tk.items(), key=lambda kv: -kv[1]["total"])[: a.top]]), ""]
        if victim != mek:
            continue
        ts = R["taken_src"].get(victim)
        if ts:
            L += ["By type: " + ", ".join(f"{k} {fmt(v)}" for k, v in ts.most_common(8)), ""]
    if prow:
        L += ["## Damage taken — pets", "", table(["Pet", "Damage taken", "Hits", "Worst attacker"], prow), ""]
    hs = defaultdict(lambda: [0, 0, 0, 0])
    for t, act, tgt, n, g, sp, crit in P["heals"]:
        if act == mek or act in R["pets"]:
            h = hs[(D.get(act, act), sp)]
            h[0] += n; h[1] += g; h[2] += 1; h[3] += crit
    if hs:
        L += ["## Healing done", "",
              table(["Healer", "Spell", "Healed", "Potential", "Overheal", "Casts/ticks", "Crits"],
                    [[k[0], k[1], fmt(v[0]), fmt(v[1]), pct(1 - v[0] / v[1]) if v[1] else "–", fmt(v[2]), fmt(v[3])]
                     for k, v in sorted(hs.items(), key=lambda kv: -kv[1][0])[: a.top]]), ""]
    if P["deaths"]:
        L += ["## Deaths", "", table(["Time", "Killed by", "Zone"], [[t, k, z or "?"] for t, k, z in P["deaths"]]), ""]
    oth = sorted(R["others"].items(), key=lambda kv: -kv[1][0])[: a.top]
    if oth:
        rows = []
        for k, (dmg, hits, tg) in oth:
            nm = D.get(k, k)
            hint = "pet-style name — yours? rerun with --pet" if PET_NAME.match(nm) else (
                "player or named NPC" if " " not in nm and nm[:1].isupper() else "NPC (charm pet? guard?)")
            rows.append([nm, fmt(dmg), fmt(hits), ", ".join(D.get(x, x) for x, _ in tg.most_common(2)), hint])
        L += ["## Other combatants (not counted above)", "",
              "Anyone here who is actually your pet or charm pet can be folded in with `--pet \"<name>\"` "
              "(or `--all-pets` for every pet-style name).", "",
              table(["Actor", "Damage", "Hits", "Main targets", "Guess"], rows), ""]
    if P["unparsed"]:
        L += ["## Parser notes", "", f"{sum(P['unparsed'].values()):,} damage-looking lines did not match any pattern "
              "(likely a melee verb missing from `VERBS` in the script):", ""]
        L += [f"- `{k}` ×{v}" for k, v in P["unparsed"].most_common(8)] + [""]
    return "\n".join(L)


def listing(P):
    L = [f"# Sessions in log — {P['me']} ({P['server']})", "",
         table(["#", "Start", "End", "Length", "Your damage lines", "Zones (by log lines)"],
               [[i, s[0], s[1], dur((s[1] - s[0]).total_seconds()), fmt(s[3]),
                 ", ".join(z for z, _ in s[2].most_common(5)) or "?"] for i, s in enumerate(P["sessions"], 1)]), ""]
    return "\n".join(L)


# --------------------------------------------------------------------------- charts
# Dark theme is the default; --light-charts switches to the light values.
THEMES = {"dark": ("#f0efec", "#a3a29b", "#2c2c2a", "#1a1a19", "#4a4a46", "#3987e5", "#d95926"),
          "light": ("#0b0b0b", "#898781", "#e1e0d9", "#fcfcfb", "#c3c2b7", "#2a78d6", "#eb6834")}
INK, MUTED, GRID, SURF, AXIS, BLUE, ORANGE = THEMES["dark"]
# series colors, fixed roles: BLUE = sustained, ORANGE = burst


def charts(P, R, a, outdir):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.ticker
    except ImportError:
        return [], "matplotlib is not installed — charts skipped (pip install matplotlib)."
    global INK, MUTED, GRID, SURF, AXIS, BLUE, ORANGE
    INK, MUTED, GRID, SURF, AXIS, BLUE, ORANGE = THEMES["light" if a.light_charts else "dark"]
    F, D, W = R["fights"], P["names"].disp, a.burst_window
    if not F:
        return [], "no fights in range — charts skipped."
    plt.rcParams.update({"figure.facecolor": SURF, "axes.facecolor": SURF, "axes.edgecolor": AXIS, "savefig.facecolor": SURF,
                         "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED, "text.color": INK,
                         "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
                         "grid.color": GRID, "grid.linewidth": 0.6, "font.size": 10, "axes.titlesize": 12,
                         "axes.titleweight": "bold", "axes.titlelocation": "left", "legend.frameon": False})
    made = []

    def save(fig, name):
        p = os.path.join(outdir, name)
        fig.savefig(p, dpi=140, bbox_inches="tight")
        plt.close(fig)
        made.append(p)

    def label(f):
        return f"#{f['n']} {D.get(f['enemy'], f['enemy'])}"

    # 1. Burst vs sustained inside the biggest fights (rolling window vs running average)
    pick = [int(x) for x in a.chart_fights.split(",")] if a.chart_fights else None
    sel = [f for f in F if f["n"] in pick] if pick else sorted(
        [f for f in F if f["dur"] >= 2 * W] or F, key=lambda f: -f["out"])[:3]
    sel.sort(key=lambda f: f["n"])
    fig, axes = plt.subplots(len(sel), 1, figsize=(10, 2.9 * len(sel)), squeeze=False)
    for ax, f in zip(axes[:, 0], sel):
        xs = list(range(f["dur"]))
        per = [f["sec"].get(s, 0) for s in xs]
        roll, run, cum, win = [], [], 0, 0
        for i, v in enumerate(per):
            cum += v
            win += v - (per[i - W] if i >= W else 0)
            roll.append(win / min(W, i + 1))
            run.append(cum / (i + 1))
        ax.plot(xs, roll, color=ORANGE, lw=1.6, label=f"Burst — rolling {W}s DPS")
        ax.plot(xs, run, color=BLUE, lw=2, label="Sustained — running average DPS")
        ax.set_title(f"{label(f)} — {fmt(f['out'])} dmg, {f['dps']:.0f} DPS, peak {f['burst']:.0f}  ({f['start']})")
        ax.set_ylim(bottom=0)
        ax.set_ylabel("DPS")
    axes[-1, 0].set_xlabel("Seconds into fight")
    fig.tight_layout()
    h, lab = axes[0, 0].get_legend_handles_labels()
    fig.legend(h, lab, loc="lower center", ncol=2, bbox_to_anchor=(0.5, -0.04))
    save(fig, "burst_vs_sustained_fights.png")

    # 2. Peak burst vs sustained DPS for the top fights (dumbbell)
    top = sorted(F, key=lambda f: -f["out"])[:15][::-1]
    fig, ax = plt.subplots(figsize=(10, 0.36 * len(top) + 1.6))
    ys = range(len(top))
    ax.hlines(ys, [f["dps"] for f in top], [f["burst"] for f in top], color=AXIS, lw=2, zorder=1)
    ax.scatter([f["dps"] for f in top], ys, s=52, color=BLUE, zorder=2, label="Sustained (fight average)")
    ax.scatter([f["burst"] for f in top], ys, s=52, color=ORANGE, zorder=2, label=f"Burst (best {W}s window)")
    ax.set_yticks(list(ys))
    ax.set_yticklabels([label(f)[:38] for f in top], color=INK)
    ax.set_xlabel("DPS")
    ax.set_xlim(left=0)
    ax.grid(axis="y", visible=False)
    ax.set_title("Burst vs. sustained DPS — top fights by damage")
    ax.legend(loc="lower right")
    save(fig, "burst_vs_sustained_summary.png")

    # 3. Sustained DPS fight by fight across the range
    if len(F) >= 8:
        k = max(5, len(F) // 25)
        xs = [f["n"] for f in F]
        ysv = [f["dps"] for f in F]
        rm = [sum(ysv[max(0, i - k + 1): i + 1]) / len(ysv[max(0, i - k + 1): i + 1]) for i in range(len(ysv))]
        fig, ax = plt.subplots(figsize=(10, 3.6))
        ax.scatter(xs, ysv, s=14, color=BLUE, alpha=0.35, linewidths=0, label="Fight DPS")
        ax.plot(xs, rm, color=BLUE, lw=2, label=f"Rolling mean ({k} fights)")
        ax.set_ylim(bottom=0)
        ax.set_xlabel("Fight number (chronological)")
        ax.set_ylabel("DPS")
        ax.set_title("Sustained DPS over time, fight by fight")
        ax.legend(loc="upper left", ncol=2)
        save(fig, "dps_by_fight.png")

    # 4. Damage by source (single series -> no legend)
    tot = Counter()
    multi = len(R["actors"]) > 1
    for r in R["actors"]:
        for src, s in R["out"][r["key"]].items():
            tot[f"{r['actor']}: {src}" if multi else src] += s["total"]
    top = tot.most_common(12)[::-1]
    if top:
        fig, ax = plt.subplots(figsize=(10, 0.36 * len(top) + 1.4))
        bars = ax.barh([k[:44] for k, _ in top], [v for _, v in top], color=BLUE, height=0.62)
        g = sum(tot.values())
        ax.bar_label(bars, labels=[f" {fmt(v)}  ({100 * v / g:.0f}%)" for _, v in top], color=MUTED, fontsize=9)
        ax.grid(axis="y", visible=False)
        ax.tick_params(axis="y", labelcolor=INK)
        ax.set_xlabel("Damage")
        ax.margins(x=0.16)
        ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
        ax.set_title("Damage by source")
        save(fig, "damage_by_source.png")
    return made, None


# --------------------------------------------------------------------------- main
def when(s):
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, f)
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(f"bad time {s!r}; use 'YYYY-MM-DD[ HH:MM[:SS]]'")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log")
    ap.add_argument("--out", default="eql_parse_out", help="output directory (default: ./eql_parse_out)")
    ap.add_argument("--list", action="store_true", help="only list play sessions and zones, then exit")
    ap.add_argument("--me", help="character name (default: taken from the eqlog_<Name>_<server>.txt filename)")
    ap.add_argument("--pet", action="append", default=[], metavar="NAME",
                    help="count NAME as your pet; repeatable. Use the mob's exact name for a charm pet, e.g. --pet \"a gorgon\"")
    ap.add_argument("--all-pets", action="store_true",
                    help="count every pet-style name (Xabtik, Koner, Kebn...) as yours; use when playing solo")
    ap.add_argument("--no-pets", action="store_true", help="ignore auto-detected pets (only --pet names count)")
    ap.add_argument("--actor", help="only damage dealt by actors whose name contains this (e.g. your name, or a pet's)")
    ap.add_argument("--target", help="only fights against targets whose name contains this")
    ap.add_argument("--zone", help="only events while in a zone whose name contains this")
    ap.add_argument("--since", type=when, help="start time, 'YYYY-MM-DD[ HH:MM[:SS]]'")
    ap.add_argument("--until", type=when, help="end time")
    ap.add_argument("--session", type=int, help="restrict to session N from --list (negative counts from the end; -1 = latest)")
    ap.add_argument("--fight-gap", type=int, default=15, help="seconds of silence that end a fight (default 15)")
    ap.add_argument("--burst-window", type=int, default=6, help="burst window in seconds (default 6)")
    ap.add_argument("--session-gap", type=int, default=20, help="minutes without log lines that split sessions (default 20)")
    ap.add_argument("--top", type=int, default=12, help="rows per table (default 12)")
    ap.add_argument("--top-actors", type=int, default=4, help="actors that get a by-source table (default 4)")
    ap.add_argument("--chart-fights", help="comma-separated fight numbers to draw in the burst chart (default: 3 biggest)")
    ap.add_argument("--no-charts", action="store_true")
    ap.add_argument("--light-charts", action="store_true", help="draw charts on a light background (default: dark)")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if a.session is not None:
        full = argparse.Namespace(**{**vars(a), "since": None, "until": None, "zone": None})
        S = parse(a.log, full)["sessions"]
        try:
            s = S[a.session - 1 if a.session > 0 else a.session]
        except IndexError:
            sys.exit(f"--session {a.session}: log has {len(S)} sessions")
        a.since, a.until = max(a.since or s[0], s[0]), min(a.until or s[1], s[1])
    P = parse(a.log, a)
    if a.list:
        print(listing(P))
        return
    R = analyse(P, a)
    os.makedirs(a.out, exist_ok=True)
    md = report(P, R, a)
    made, note = ([], None) if a.no_charts else charts(P, R, a, a.out)
    if made:
        md += "\n## Charts\n\n" + "\n".join(f"- `{os.path.basename(p)}`" for p in made) + "\n"
    if note:
        md += f"\n> Charts: {note}\n"
    with open(os.path.join(a.out, "summary.md"), "w", encoding="utf-8") as fh:
        fh.write(md)
    D = P["names"].disp
    with open(os.path.join(a.out, "fights.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["fight", "start", "end", "zone", "target", "seconds", "damage", "dps", "burst_dps",
                    "burst_window_s", "damage_taken", "result", "damage_by_actor"])
        for f in R["fights"]:
            w.writerow([f["n"], f["start"], f["end"], f["zone"], D.get(f["enemy"], f["enemy"]), f["dur"], f["out"],
                        round(f["dps"], 1), round(f["burst"], 1), f["win"], f["inc"], f["result"],
                        "; ".join(f"{D.get(k, k)}={v}" for k, v in f["by"].most_common())])
    js = dict(character=P["me"], server=P["server"], first=str(P["first"]), last=str(P["last"]), lines=P["nlines"],
              pets=sorted(D.get(p, p) for p in R["pets"]), total=R["total"],
              actors=[{k: v for k, v in r.items() if k != "key"} for r in R["actors"]],
              sources={r["actor"]: R["out"][r["key"]] for r in R["actors"]},
              special_attacks={D.get(k, k): dict(v) for k, v in R["flags"].items()},
              fights=len(R["fights"]), deaths=len(P["deaths"]), unparsed_lines=sum(P["unparsed"].values()),
              charts=[os.path.basename(p) for p in made])
    with open(os.path.join(a.out, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(js, fh, indent=1, default=str)
    print(md)
    print(f"\n[written to {os.path.abspath(a.out)}]", file=sys.stderr)


if __name__ == "__main__":
    main()
