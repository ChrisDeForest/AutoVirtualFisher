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
The menu can display the state as a readable summary or JSON. It retains input
only for the current session.

Start the local web interface:

```powershell
.venv/Scripts/python.exe -m src.player_web
```

Open <http://127.0.0.1:8765>, paste either or both responses, and select
**Read player state**. **Download JSON** exports a snapshot. The server binds
only to this computer, uses no external assets, and does not persist responses.
Use `--port 8766` if the default port is occupied; press Ctrl+C to stop it.

For file input and machine-readable output:

```powershell
.venv/Scripts/python.exe -m src.player_cli --profile profile.txt --buffs buffs.txt --prestige-shop shop.txt --pet pet.txt --json
```

Both interfaces use `src/player_state.py`. Missing values are `null` (shown as
Unknown), an explicit `Pet: None` stays distinct from missing pet information,
and conflicting duplicate fields are left unknown. Percentages, multipliers,
and seconds retain their units. Unrecognized lines appear in warnings and in
the JSON's `unparsed` section. Imports replace the displayed snapshot rather
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
automatic account tracking, and action scheduling are future work.

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
