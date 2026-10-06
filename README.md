# AutoVirtualFisher
This repo contains my personal project of completely automating the Discord bot "Virtual Fisher".

The current implementation collects commands and wiki pages, then exports game
data as JSON. Gameplay automation is not implemented yet.

## Player logbook: terminal and web

The player logbook reads pasted `/profile`, `/buffs`, and `/prestige shop` responses into a shared
player-state snapshot. It does not connect to Discord. Tests include text
transcribed from the user's October 5, 2026 live screenshots, plus synthetic
edge cases. Screenshot images themselves are not parsed by the application.

Start the interactive terminal menu:

```powershell
.venv/Scripts/python.exe -m src.player_cli
```

Choose profile, buffs, or prestige shop, paste the response, then enter `.` on its own line.
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

For file input and machine-readable output:

```powershell
.venv/Scripts/python.exe -m src.player_cli --profile profile.txt --buffs buffs.txt --prestige-shop shop.txt --pet pet.txt --json
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
pet parsing needs a response that lists at least one pet. Quests, fishing results,
and action scheduling are future work.

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
