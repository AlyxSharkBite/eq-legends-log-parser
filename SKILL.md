---
name: eq-legends-log-parser
description: Parse and analyze EverQuest Legends (EQL) game files - combat logs (named like eqlog_CharacterName_server.txt) and inventory dumps (named like CharacterName_server-Inventory.txt). Use whenever the user uploads or points at an EverQuest / EQ Legends .txt log or inventory file, or asks about DPS, damage parses, hit/miss ratios, crits, pet or charm-pet damage, burst vs sustained damage, damage taken, healing, deaths, fights, or what gear/items a character has. Produces a markdown summary table and charts.
---

# EQ Legends Log Parser

You are acting as an EverQuest Legends combat analyst. The numbers come from the bundled scripts, not
from reading the log yourself: logs run to hundreds of thousands of lines (the reference log is 47 MB)
and must never be loaded into context. Your job is to pick the right slice of the log, run the parser,
check the result for the known pitfalls below, and explain what the numbers mean.

| File | Looks like | Script |
|---|---|---|
| Combat log | `eqlog_Tamsin_halas.txt`, lines start `[Wed Nov 04 20:27:49 2026] ` | `scripts/parse_eqlog.py` |
| Inventory dump | `Brannoc_rivervale-Inventory.txt`, tab-separated, header `Location  Name  ID  Count  Slots` | `scripts/parse_inventory.py` |

Both scripts are standard-library Python 3.8+. Charts additionally need `matplotlib`; if it is missing
the parser still writes every table and says the charts were skipped. Install it (`pip install matplotlib`)
and rerun rather than hand-drawing charts.

## Combat log workflow

1. **Identify the file.** Character and server come from the filename (`eqlog_<Character>_<server>.txt`).
   If the upload was renamed, pass `--me <Character>`; "You"/"YOU" in the log is that character.
   Do not `cat` the file; `head -5` is enough to confirm the format.

2. **Scope it.** A log usually spans many days and play sessions, so a whole-file DPS figure is rarely what
   the user wants. Unless they asked for everything, list sessions first:

   ```bash
   python scripts/parse_eqlog.py <log> --list
   ```

   Then choose a slice from what they asked ("last night" → `--session -1`; "my Plane of Hate run" →
   `--zone "plane of hate"`; a boss → `--target "innoruuk"`). If the request gives no hint, parse the most
   recent session and say that is what you did, mentioning that other sessions exist.

3. **Run the parse.**

   ```bash
   python scripts/parse_eqlog.py <log> --session -1 --out <output-dir>
   ```

   It prints `summary.md` to stdout and writes `summary.md`, `summary.json`, `fights.csv` and the PNG charts
   into `--out`.

4. **Resolve pets before trusting the totals** (see "Pets and charm pets"). Check the report's
   *Pets counted as yours* line and the *Other combatants* table, and rerun with `--pet` / `--all-pets`
   if damage that belongs to the user is sitting in "Other combatants".

5. **Check "Parser notes".** If the report lists unparsed damage-looking lines, a melee verb is missing:
   add it to the `VERBS` dict at the top of `parse_eqlog.py` (base form → third-person form) and rerun.

6. **Report** using the template in "What to give the user", and show the charts.

### Filters

| Flag | Use |
|---|---|
| `--list` | Only list sessions (start, end, zones); no parse |
| `--session N` | Session N from `--list`; negative counts from the end (`-1` = latest) |
| `--since` / `--until` | `"YYYY-MM-DD[ HH:MM[:SS]]"` bounds |
| `--zone TEXT` | Only while in a zone whose name contains TEXT (zone comes from `You have entered …`) |
| `--target TEXT` | Only fights against mobs whose name contains TEXT |
| `--actor TEXT` | Only damage dealt by this actor (the character's name for "just me", or one pet's name) |
| `--pet NAME` | Count NAME as the user's pet; repeatable; exact mob name for a charm pet |
| `--all-pets` | Count every pet-style name as the user's (solo play) |
| `--no-pets` | Ignore auto-detected pets |
| `--fight-gap S` | Seconds of silence that end a fight (default 15) |
| `--burst-window S` | Burst window in seconds (default 6) |
| `--chart-fights 12,40` | Draw these fight numbers in the burst chart instead of the 3 biggest |
| `--top N` | Rows per table (default 12) |
| `--light-charts` | Draw charts on a light background (default is dark) |

Filters combine. Names match case-insensitively by substring.

### Pets and charm pets

The log never states who owns a pet, so ownership is inferred. Three cases:

- **Summoned pets with generated names** (`Xabtik`, `Jarektik`, `Koner`, `Lonartik`, `Kebn` …). The parser
  auto-claims one when it tells the character something ending in "Master" (`Xabtik told you, 'Attacking a
  scareling Master.'`), and `<Character>`s warder`-style names. A pet that never spoke to its owner stays
  unclaimed and shows up under *Other combatants* tagged "pet-style name". If the user was solo (no player
  names in that table), rerun with `--all-pets`; in a group, claim only the ones the user confirms with
  `--pet`, because other players' pets use the same naming scheme.
- **Charm pets keep the mob's own name** (`a gorgon`, `Elite Guard`) and the log has no reliable
  charm-landed line. Look in *Other combatants* for an NPC-named actor damaging other NPCs and ask the user
  (or accept what they told you), then rerun with `--pet "<exact mob name>"`. The parser labels it
  `charm pet`, counts its damage to other mobs as the user's, and treats its hits on the user or on the
  user's damage to it (charm break) as a normal enemy fight. Limitation to state: every mob sharing that
  name is treated the same way, so guards or same-named wild mobs fighting nearby inflate the number.
  Narrow with `--zone` / `--since` / `--until` to the time the charm was held.
- **Enemy pets** are named `<owner> pet` (`an orc thaumaturgist pet`) and are just targets.

"Filter by character or pet": `--actor <Character>` for the character only, `--actor <PetName>` for one
pet; with no `--actor` the report gives the combined total plus one row per actor.

### How the numbers are defined

State these when the user asks what a figure means, and whenever a number could be misread.

- **Fight**: all damage between the user's side and one mob name, ended by that mob's `slain` line or by
  `--fight-gap` seconds without an event. Two mobs with the same name fought at once merge into one fight.
- **DPS**: damage ÷ seconds from a fight's first to last event (inclusive). The headline DPS is total damage
  ÷ the union of all fight time, i.e. *active combat time*, not wall-clock time. An actor's DPS uses the
  time of fights that actor took part in, so a pet that joined few fights can show a higher DPS than its share suggests.
- **Burst DPS**: the best `--burst-window`-second stretch of a fight (default 6 s). **Sustained**: the
  fight-average. Burst ÷ sustained is the "spikiness" ratio.
- **Hit %**: melee hits ÷ all melee swings (hits + misses + target dodged/parried/blocked/riposted/absorbed).
  Spells, DoTs and damage shields have no miss line, so they show "–"; resists are not tracked per spell.
- **Crit %**: hits flagged `(Critical)` or `(Crippling Blow)` ÷ hits. Other trailing flags are tallied as
  special attacks: `Finishing Blow`, `Slay Undead`, `Double Bow Shot`, `Flurry`, `Rampage`, `Wild Rampage`,
  `Riposte`, `Strikethrough`; they can combine (`(Riposte Critical)`).
- **Source types**: `melee` (by verb: slash, crush, kick, bash, frenzy on, shoot …), `spell` (direct
  damage, named), `dot` (ticks, suffixed "(DoT)"), `ds` (damage shield: thorns/flames/frost). Damage-shield
  damage is included in totals; call it out when it is a large share, since it is passive.
- **Healing**: `healed X for 5 (6)` means 5 landed of 6 possible; overheal = 1 − landed ÷ potential.
- **Damage taken**: avoidance percentages are over melee swings at the character only.

### Log line formats

Every line is `[Www Mmm DD HH:MM:SS YYYY] <text>`, one-second resolution, local time. Lines the parser uses
(names at the start of a line are capitalized, mid-line they are not: `A puma` = `a puma`):

```
You slash a spite golem for 212 points of damage. (Critical)
A lizardman warrior hits YOU for 18 points of damage.
Lonartik slashes The Game Master for 173 points of damage.
You try to crush a greater skeleton, but miss!
You try to slash a spite golem, but a spite golem dodges!        (also parries / blocks / ripostes)
A spite golem tries to hit YOU, but YOU block!
A ghoul tries to hit YOU, but misses! (Riposte)
You hit orc centurion for 58 points of magic damage by Reaving Strike.
a necro apprentice hit you for 42 points of magic damage by Lifebite.
Royal guard has taken 8 damage from your Clinging Darkness.
The Game Master has taken 20 damage from Dooming Darkness by Lonartik.
You have taken 1 damage from Rabies by a lesser mummy.
The Prophet is pierced by YOUR thorns for 3 points of non-melee damage.
YOU are burned by Marrowbane's flames for 6 points of non-melee damage!
You healed Tamsin for 5 (6) hit points by Blessing of the Lord Commander.
You healed Brannoc over time for 140 hit points by Celestial Health. (Critical)
You have slain orc centurion!      /  A water moccasin has been slain by a froglok guardsman!
You have been slain by an orc warrior!
You have entered Clan Crushbone.
Xabtik told you, 'Attacking a scareling Master.'
```

Chat (`… tells General:2, '…'`, `… tells the guild, '…'`, `You say, '…'`) is skipped, so quoted combat text
in chat is never counted. Present but not parsed: `You begin casting X.`, `Your X spell fizzles!`,
`Your X spell is interrupted.`, `You resist a spite golem's Daze!`, `YOUR magical skin absorbs the damage
of X's thorns.`, `You gain a rune for N points of absorption.`, `You gain experience! (4.000%)`,
`You receive … from the corpse.`, `You looted a … and sold it for …`. For questions about those (cast
counts, fizzle rate, experience per hour, coin looted), use `grep -c` / a short one-off script over the
same time range instead of reading the log.

### What to give the user

Lead with the answer, then the tables. Keep the script's markdown tables intact (do not retype numbers).

1. One or two sentences: what slice was parsed, the headline DPS, and the single most notable finding.
2. **Summary table** — the *Damage dealt* table (total damage, DPS, hits, misses, hit %, crits, crit %,
   max hit per actor: the character, each pet, each charm pet).
3. **By source** table for the character (and for a pet if pets matter to the question).
4. **Burst vs. sustained** — the two bullet lines plus the *Top fights* table, trimmed to what is relevant.
5. **Charts** — display the PNGs from `--out`:
   - `burst_vs_sustained_fights.png`: rolling burst-window DPS against running-average DPS, second by
     second, for the three biggest fights (or `--chart-fights`).
   - `burst_vs_sustained_summary.png`: sustained vs. peak-burst DPS for the top 15 fights.
   - `dps_by_fight.png`: fight DPS across the whole slice with a rolling mean (8+ fights only).
   - `damage_by_source.png`: where the damage came from.
6. Damage taken / healing / deaths only if asked about or clearly relevant (a death during the boss fight they asked about).
7. Caveats that actually apply: unclaimed pet-style names, charm-pet name collisions, a large passive
   damage-shield share, fights marked `open at end of range`, any unparsed lines.

If a filter returns no fights, say so and show what `--list` and *Other combatants* suggest instead of
presenting an empty table. For anything the report does not cover, compute from `fights.csv` /
`summary.json` (per-fight rows and per-source stats) rather than re-reading the log.

If you need a chart the script does not draw, build it from `fights.csv` with matplotlib in the same style:
dark background `#1a1a19` with light text `#f0efec`, one y-axis, burst in orange `#d95926`, sustained in
blue `#3987e5`, a legend whenever there are two series, axis text in grey rather than in the series colors.

## Inventory dump workflow

```bash
python scripts/parse_inventory.py <Character>_<server>-Inventory.txt
python scripts/parse_inventory.py <file> --find "golem" --find "ornamentation"
python scripts/parse_inventory.py <file> --json inventory.json
```

The default report gives an overview (filled/free slots per area), the equipped set with each item's
enhancement level and socketed augments, the largest stacks, items owned at several enhancement levels,
and key-ring counts. Use `--find` for "do I have / where is X" and `--json` when you need to compute
something else.

File layout (tab-separated, CRLF, two sections separated by a blank line):

- **Section 1** — header `Location  Name  ID  Count  Slots`.
  - Equipped slots: `Any Slot`, `Ear`, `Head`, `Face`, `Neck`, `Shoulders`, `Arms`, `Back`, `Wrist`,
    `Range`, `Hands`, `Primary`, `Secondary`, `Fingers`, `Chest`, `Legs`, `Feet`, `Waist`, `Ammo`, `Held`.
    `Ear`, `Wrist` and `Fingers` appear twice; rows are in order, so sockets belong to the item above them.
  - `General N` = carried bag N, `BankN` = bank container, `SharedBankN` = shared bank slot.
    `…-SlotM` is the item in slot M of that container.
  - One further `-SlotK` level is a socket on the item: `Head-Slot2`, `General 1-Slot1-Slot7`.
    Socketed augments carry a type suffix: `(Exaltation)` or `(Ornamentation)`.
  - An empty slot or socket is `Empty  0  0  0`.
  - A trailing ` +N` on a name is the item's enhancement level (`Insidious Halo +8`).
  - `Count` is the stack size. `Slots` is the capacity for containers (12, 24, 50); on ordinary items it
    is a fixed value that carries no capacity meaning, so ignore it there.
- **Section 2** — header `KeyRing  Name  ID`: collection entries not held in bags, grouped by the first
  column (`Equipment`, `Augmentation`, `Activated`). A first column of `Equipment-<item name>` is an
  augment socketed into that stored item.

The inventory file is a snapshot with no timestamps; say so if the user asks when something was acquired
(the combat log's `You looted …` lines can answer that).

## Using both together

When the user supplies a log and an inventory for the same character, tie them together only where the
data supports it: weapon in `Primary`/`Range` against the melee verbs and `shoot` in the by-source table,
enhancement levels against max hits, damage-shield or proc augments against `ds`/spell sources. Do not
invent item stats; neither file contains them.
