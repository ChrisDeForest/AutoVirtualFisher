# AutoVirtualFisher
This repo contains my personal project of completely automating the Discord bot "Virtual Fisher".

The current implementation collects commands and wiki pages, then exports game
data as JSON. Gameplay automation is not implemented yet.

## Player logbook: terminal and web

The player logbook reads pasted `/profile`, `/buffs`, `/prestige shop`, `/pet`, and `/quests` responses into a shared
player-state snapshot. It does not connect to Discord. Tests include text
transcribed from the user's October 5, 2026 live screenshots, plus synthetic
edge cases. Screenshot images themselves are not parsed by the application.

Start the interactive terminal menu:

```powershell
.venv/Scripts/python.exe -m src.player_cli
```

Choose a response type (profile, buffs, prestige shop, pet, or quests), paste it, then enter `.` on its own line.
The menu can display the state as a readable summary or JSON. Pasted text is
kept only for the current session. Use **a** to choose an account (or type a new
name), **s** to save a manual snapshot, **h** for recent history, and **c** to
compare the latest two full snapshots.

Start the local web interface:

```powershell
.venv/Scripts/python.exe -m src.player_web
```

Open <http://127.0.0.1:8765>, paste any of the responses, and select
**Read player state**. **Download JSON** exports the parsed state without saving
it. The server binds only to this computer and uses no external assets. Use
`--port 8766` if the default port is occupied; press Ctrl+C to stop it.

### Snapshot history

The logbook keeps a local history of parsed states for each account, so you can
see progress between imports.

- **Automatic saves (web):** **Read player state** saves a snapshot when the
  `/profile` paste contains the `Inventory of <name>` heading, a balance, and a
  level. If nothing changed since the latest full snapshot, nothing new is
  written.
- **Manual saves:** **Save snapshot** always writes a record, including partial
  ones such as a `/pet` paste on its own. When the paste has no profile heading,
  choose an existing account under **Save to account**. A chosen account must
  match the account named in a pasted profile.
- **Dashboard:** choose a **Saved account** and select **Show history** to see
  the latest full snapshot, recent saves, and changes between the two most
  recent full snapshots: balance, level, current XP, fish value, bait stock,
  inventory counts, and pet levels/XP. Partial snapshots never take part in the
  comparison, and values missing from either snapshot show as Unknown.
- **Growth charts:** the same view charts balance, level progress (level plus
  the fraction toward the next one), fish value, and the exotic fish total
  across every full snapshot, spaced by real date. Hover a point for its exact
  value. A snapshot missing a value leaves a gap rather than a zero; level
  progress needs the `current/required XP` form of the profile line.

Snapshots are JSON files in `src/data/player_snapshots/`, one folder per
account. That folder is ignored by Git and never leaves this computer. Only the
parsed state is saved; the pasted text is not stored or shown again. Lines the
parser did not recognize are saved only as a count per response, and warnings
that would quote a line are saved as `(line omitted)`.

From the command line:

```powershell
# Save a full profile (duplicates of the latest full snapshot are skipped)
.venv/Scripts/python.exe -m src.player_cli --profile profile.txt --save
# Save a partial paste to a named account
.venv/Scripts/python.exe -m src.player_cli --pet pet.txt --save --account BugParticle
# Show recent snapshots, or progress between the latest two full ones
.venv/Scripts/python.exe -m src.player_cli --history --account BugParticle
.venv/Scripts/python.exe -m src.player_cli --compare --account BugParticle
```

With `--json`, standard output stays a single JSON document and save messages go
to standard error. `--snapshot-dir PATH` uses a different snapshot folder.

### Fishing results

Paste one or more fishing results into **Fishing results** on the web page, as
copied from Discord. **Read fishing results** tallies the batch: trips, fish
caught per type, fishing XP per trip, finds (chests, charms), chest contents,
charm levels, level-ups and their money, unlocks, completed quests with their
rewards, and bait running out. Quest XP is kept separate from fishing XP.
Cooldown replies and the message author line are skipped; anything else the
parser does not recognize is listed for review.

**Save session** stores the tally for an existing account, chosen explicitly:
the name on fishing messages (e.g. `epicmouse9925`) is not the same as the
`/profile` heading, so it cannot pick the account. Sessions are JSON files in
`src/data/fishing_sessions/`, ignored by Git. Only the tally and a one-way
fingerprint of the batch are kept, never the pasted text. The fingerprint stops
the same batch from being counted twice; batches that only partly overlap
cannot be detected, so paste each fishing run once.

**Show history** adds an all-time fishing card for the account: trips, fish
and XP per trip, finds per 100 trips, chest contents, quest rewards, and
level-ups.

### Planner

**Show history** also opens with a planner. Enter an optional **Target level**
next to the account. Using the latest full snapshot and your saved fishing
sessions, it estimates:

- XP and fishing trips to the next level, five and ten levels ahead, and the
  target level;
- trips to finish each quest from the latest pasted `/quests`: fish caught per
  trip for fishing quests, chest finds (boxes, chests, crates) per trip for
  chest quests, and the XP model for level-up quests. Pet quests and unfamiliar
  quest types say there is not enough data rather than guessing.

The XP needed to advance from level L is the running total of the wiki level
table through L, shipped as `src/reference/level_xp.json`. This matched a live
level-131 profile exactly (477,500). The planner checks the model against every
snapshot that shows current/required XP and, if any disagree, projects only the
next level, which comes straight from the game's own "XP to next level". The
wiki's level-up money does not match the game, so money is not projected.
Estimates count fishing XP only; quest rewards make progress faster.

For file input and machine-readable output:

```powershell
.venv/Scripts/python.exe -m src.player_cli --profile profile.txt --buffs buffs.txt --prestige-shop shop.txt --pet pet.txt --quests quests.txt --json
```

Both interfaces use `src/player_state.py`. Missing values are `null` (shown as
Unknown), an explicit `Pet: None` stays distinct from missing pet information,
and conflicting duplicate fields are left unknown. Percentages, multipliers,
and seconds retain their units. Unrecognized lines appear in warnings and in
the JSON's `unparsed` section. Imports replace the displayed state rather
than silently merging older account values.

Currently supported fields: balance, level, prestige, current/required/remaining
XP, rod, biome, bait and quantity, pet if present, fish inventory value,
fish/exotic/special inventory entries, sale multiplier, fishing/treasure
bonuses, XP multiplier, and fishing cooldown. Remaining XP is calculated as
required minus current XP when the profile supplies a fraction.

The prestige shop records Azure Fish and upgrade ranks. The player logbook calculates an
inferred prestige minimum as unspent Azure Fish plus visibly maxed upgrades. It labels this
as an inference; a prestige shown in `/profile` remains the authoritative value.

The live pet command is `/pet` (confirmed by the user), even though the command
website lists `/pets`. A `/pet` response records the average pet XP per fishing
trip and distinguishes an explicit no-pets result from missing pet data. Detailed
pet parsing needs a response that lists at least one pet.

A `/quests` response records each daily and special quest's progress, goal, and
task, plus the reset countdown in minutes. Quests are saved with the snapshot,
so later snapshots show quest progress as of when they were pasted. Action
scheduling is future work.

## Run the scrapers

Use Python 3.12 (tested) and run from the repository root:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe src/data_scraping/data_processing.py
```

The default run reuses cached HTML and downloads missing files. To request fresh
HTML, including every selected wiki page:

```powershell
.venv/Scripts/python.exe src/data_scraping/data_processing.py --refresh
```

For only command export and wiki HTML collection, run
`src/data_scraping/web_scraper.py` with the same optional `--refresh` flag.
HTML is stored in `src/data/html/`, command JSON in `src/data/commands/`, and
game JSON in `src/data/json/`. These data folders are ignored by Git.

Refresh stops on HTTP errors rather than silently using old data. Individual
files downloaded before an error remain refreshed; the whole run is not a
transaction. Back up `src/data/` before refreshing if you need a comparison.

On October 5, 2026, the command site was reachable but Fandom returned HTTP 403.
The pipeline was verified using fresh command HTML and existing wiki HTML;
wiki values were not verified as current. Bait prices now come from the wiki
table, but level rules and shared bait rules remain hard-coded. Some wiki
parsers still depend on the source page's internal layout.

## Tests

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

Tests use temporary output folders, local HTML fixtures, and simulated HTTP
responses. They do not contact the websites or modify your collected data.
