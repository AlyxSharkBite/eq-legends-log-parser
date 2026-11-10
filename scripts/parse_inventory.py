#!/usr/bin/env python3
"""EverQuest Legends inventory-dump parser (<Character>_<server>-Inventory.txt, tab separated).

Prints a markdown report; --json FILE also writes every row as structured data.
Standard library only. See ../SKILL.md for the file layout.
"""
import argparse, json, os, re, sys
from collections import Counter, defaultdict

EQUIP = ["Any Slot", "Ear", "Head", "Face", "Neck", "Shoulders", "Arms", "Back", "Wrist", "Range", "Hands",
         "Primary", "Secondary", "Fingers", "Chest", "Legs", "Feet", "Waist", "Ammo", "Held"]
SOCKET_TAG = re.compile(r"\s*\((Exaltation|Ornamentation|[A-Z][a-z]+)\)$")
PLUS = re.compile(r"\s\+(\d+)$")


def area(root):
    for pre, lab in (("SharedBank", "Shared bank"), ("Bank", "Bank"), ("General", "Bags")):
        if root.startswith(pre):
            return lab
    return "Equipped" if root in EQUIP else root


def load(path):
    items, keyring, section2 = [], [], False
    seen = Counter()
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            c = raw.rstrip("\r\n").split("\t")
            if not c or not c[0]:
                continue
            if c[0] == "Location":
                continue
            if c[0] == "KeyRing" and len(c) > 1 and c[1] == "Name":
                section2 = True
                continue
            if section2:
                ring, _, parent = c[0].partition("-")   # "Equipment-<item>" = augment socketed in that stored item
                keyring.append(dict(keyring=ring, socketed_in=parent or None, name=c[1] if len(c) > 1 else "", id=int(c[2]) if len(c) > 2 and c[2].isdigit() else 0))
                continue
            if len(c) < 5:
                continue
            loc, name, iid, count, slots = c[0], c[1], int(c[2] or 0), int(c[3] or 0), int(c[4] or 0)
            parts = loc.split("-")
            root = parts[0]
            a = area(root)
            # depth: Equipped item = 1 part; its sockets = 2. Bag = 1, item in bag = 2, its sockets = 3.
            is_socket = (a == "Equipped" and len(parts) == 2) or (a != "Equipped" and len(parts) == 3)
            if a == "Equipped" and len(parts) == 1:   # Ear / Wrist / Fingers appear twice
                seen[root] += 1
                inst = seen[root]
            else:
                inst = seen[root] if a == "Equipped" else 0
            m = PLUS.search(name)
            t = SOCKET_TAG.search(name)
            items.append(dict(location=loc, area=a, root=root, instance=inst, name=name, id=iid, count=count,
                              slots=slots, empty=(name == "Empty" or iid == 0), socket=is_socket,
                              container=(a != "Equipped" and len(parts) == 1 and iid != 0),
                              plus=int(m.group(1)) if m else None,
                              socket_type=t.group(1) if (t and is_socket) else None))
    return items, keyring


def table(head, rows):
    L = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    return "\n".join(L + ["| " + " | ".join(str(c) for c in r) + " |" for r in rows])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inventory")
    ap.add_argument("--find", action="append", default=[], metavar="TEXT", help="list every row whose name contains TEXT (repeatable)")
    ap.add_argument("--top", type=int, default=25, help="rows in the stack/duplicate tables (default 25)")
    ap.add_argument("--json", metavar="FILE", help="also write all rows as JSON")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    items, keyring = load(a.inventory)
    m = re.match(r"(.+?)_(.+?)-Inventory\.txt$", os.path.basename(a.inventory), re.I)
    who = f"{m.group(1)} ({m.group(2)})" if m else os.path.basename(a.inventory)
    L = [f"# Inventory — {who}", ""]

    if a.find:
        for q in a.find:
            hits = [i for i in items if q.lower() in i["name"].lower()]
            hits += [dict(location="KeyRing: " + k["keyring"], name=k["name"], id=k["id"], count=1) for k in keyring if q.lower() in k["name"].lower()]
            L += [f"## Matches for “{q}” ({len(hits)})", "",
                  table(["Location", "Item", "ID", "Count"], [[h["location"], h["name"], h["id"], h["count"]] for h in hits]) if hits else "_none_", ""]
        print("\n".join(L))
        return

    real = [i for i in items if not i["empty"]]
    L += ["## Overview", ""]
    rows = []
    for ar in ("Equipped", "Bags", "Bank", "Shared bank"):
        sub = [i for i in items if i["area"] == ar]
        if not sub:
            continue
        top = [i for i in sub if not i["socket"] and (ar == "Equipped" or len(i["location"].split("-")) == 1)]
        inside = [i for i in sub if not i["socket"] and ar != "Equipped" and len(i["location"].split("-")) == 2]
        cap = sum(i["slots"] for i in top if i["container"] and any(x["location"].startswith(i["location"] + "-") for x in inside))
        rows.append([ar, sum(1 for i in top if not i["empty"]), sum(1 for i in top if i["empty"]),
                     sum(1 for i in inside if not i["empty"]), sum(1 for i in inside if i["empty"]) if inside else "–", cap or "–",
                     sum(1 for i in sub if i["socket"] and not i["empty"])])
    L += [table(["Area", "Top-level filled", "Top-level empty", "Items inside containers", "Free container slots",
                 "Container capacity", "Socketed augments"], rows), ""]

    L += ["## Equipped", ""]
    rows = []
    for i in items:
        if i["area"] == "Equipped" and not i["socket"]:
            socks = [s for s in items if s["socket"] and s["area"] == "Equipped" and s["root"] == i["root"] and s["instance"] == i["instance"]]
            filled = [s["name"] for s in socks if not s["empty"]]
            rows.append([i["location"], i["name"], i["plus"] if i["plus"] is not None else "–",
                         f"{len(filled)}/{len(socks)}", "; ".join(filled) or "–"])
    L += [table(["Slot", "Item", "+", "Sockets used", "Socketed"], rows), ""]
    plus = [i["plus"] for i in items if i["area"] == "Equipped" and not i["socket"] and not i["empty"] and i["plus"] is not None]
    if plus:
        L += [f"Average enhancement on equipped gear: **+{sum(plus) / len(plus):.1f}** (min +{min(plus)}, max +{max(plus)}); "
              f"empty equipment slots: {', '.join(i['location'] for i in items if i['area'] == 'Equipped' and not i['socket'] and i['empty']) or 'none'}.", ""]

    tot, where = Counter(), defaultdict(Counter)
    for i in real:
        if i["socket"] or i["container"] and i["area"] != "Equipped":
            continue
        tot[i["name"]] += i["count"]
        where[i["name"]][i["area"]] += i["count"]
    stacks = [(n, c) for n, c in tot.most_common() if c > 1][: a.top]
    if stacks:
        L += [f"## Largest holdings (top {len(stacks)})", "",
              table(["Item", "Total", "Where"], [[n, f"{c:,}", ", ".join(f"{k} {v:,}" for k, v in where[n].most_common())] for n, c in stacks]), ""]

    base = defaultdict(list)
    for i in real:
        if not i["socket"] and i["plus"] is not None:
            base[PLUS.sub("", i["name"])].append(i)
    dup = sorted(((b, v) for b, v in base.items() if len(v) > 1), key=lambda kv: -len(kv[1]))[: a.top]
    if dup:
        L += ["## Same item at several enhancement levels", "",
              table(["Item", "Copies", "Levels and locations"],
                    [[b, len(v), "; ".join(f"+{x['plus']} @ {x['location']}" for x in sorted(v, key=lambda x: -x["plus"]))] for b, v in dup]), ""]

    if keyring:
        kc = Counter(k["keyring"] for k in keyring if not k["socketed_in"])
        sc = Counter(k["keyring"] for k in keyring if k["socketed_in"])
        L += ["## Key rings / collections", "",
              table(["Key ring", "Entries", "Augments socketed in them"], [[k, v, sc.get(k, 0)] for k, v in kc.most_common()]), ""]
    print("\n".join(L))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(dict(character=who, items=items, keyring=keyring), fh, indent=1)


if __name__ == "__main__":
    main()
