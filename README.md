# EQ Legends Log Parser

A [Claude skill](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/overview) that analyzes
EverQuest Legends game files:

- **Combat logs** (`eqlog_CharacterName_server.txt`) — total DPS, hit/miss ratios, critical strikes,
  per-source damage, pet and charm-pet damage, burst vs. sustained damage, damage taken, healing and deaths.
- **Inventory dumps** (`CharacterName_server-Inventory.txt`) — equipped gear with enhancement levels and
  socketed augments, bag and bank usage, largest stacks, and item search.

The skill tells Claude how to scope a log (session, zone, boss), run the bundled parsers, and explain the
results. The parsers also work on their own from a terminal.

## Install as a Claude skill

**Claude Code** — copy this folder into your personal skills directory:

```bash
git clone https://github.com/AlyxSharkBite/eq-legends-log-parser ~/.claude/skills/eq-legends-log-parser
```

**Claude chat (claude.ai / desktop app)** — download the repository as a zip, then upload it under
*Skills → Manage skills*.

Then give Claude a log or inventory file and ask, for example, "what was my DPS in last night's session?"

## Use the scripts directly

Requires Python 3.8+. Charts need `matplotlib` (`pip install matplotlib`); without it the tables are still
produced and the charts are skipped.

```bash
# list play sessions in a log
python scripts/parse_eqlog.py eqlog_Mychar_server.txt --list

# parse the latest session; writes summary.md, summary.json, fights.csv and charts
python scripts/parse_eqlog.py eqlog_Mychar_server.txt --session -1 --out parse_out

# one zone, counting every pet-style name as yours
python scripts/parse_eqlog.py eqlog_Mychar_server.txt --zone "plane of hate" --all-pets

# a charm pet (use the mob's exact name)
python scripts/parse_eqlog.py eqlog_Mychar_server.txt --pet "a gorgon" --since "2026-11-07 19:00"

# inventory report, or search for an item
python scripts/parse_inventory.py Mychar_server-Inventory.txt
python scripts/parse_inventory.py Mychar_server-Inventory.txt --find "golem"
```

Run either script with `--help` for all options. Charts are dark themed by default; add `--light-charts`
for a light background.

## Output

| File | Contents |
|---|---|
| `summary.md` | Damage dealt per actor, by-source breakdown, top fights, burst vs. sustained, damage taken, healing, deaths |
| `summary.json` | The same numbers, machine readable |
| `fights.csv` | One row per fight: target, zone, length, damage, DPS, burst DPS, damage taken, result |
| `burst_vs_sustained_fights.png` | Rolling burst-window DPS vs. running-average DPS for the biggest fights |
| `burst_vs_sustained_summary.png` | Sustained vs. peak-burst DPS for the top 15 fights |
| `dps_by_fight.png` | Fight DPS across the slice with a rolling mean |
| `damage_by_source.png` | Where the damage came from |

## Known limitations

- The log never says who owns a pet. Summoned pets are claimed automatically only after they send their
  owner a "Master" tell; otherwise use `--pet NAME` or `--all-pets`.
- A charmed mob keeps its own name, so every mob sharing that name is counted as the charm pet. Narrow the
  time range to when the charm was held.
- Two mobs with the same name fought at the same time are merged into one fight.
- If the report lists unparsed damage lines, a melee verb is missing from the `VERBS` table at the top of
  `scripts/parse_eqlog.py`.

See [SKILL.md](SKILL.md) for the full list of recognized log lines and how each number is defined.

## License

MIT — see [LICENSE](LICENSE). Not affiliated with or endorsed by the publishers of EverQuest.
