# AutoVirtualFisher

### Experimental Windows interaction adapter

The native adapter supports `/profile`, `/buffs`, and `/fish` through Discord's
visible command picker in a DM (server channels are not supported yet).
It is disabled by default. A supervised live probe verified profile/buffs bootstrap
and one fish submission; result reading was repaired against that existing reply.
Live runs have since completed dozens of trips in a row. The web automation page
starts live runs too (see Automation below); the command-line runner remains.

Inspect the configured target without sending input (substitute your actual names):

```powershell
.venv/Scripts/python.exe -m src.automation_live --channel "Virtual Fisher" --account "YOUR_PROFILE_NAME" --username "YOUR_DISCORD_USERNAME"
```

The runner gives you five seconds to foreground Discord. Keep its composer empty
and a complete VF reply visible. `--window HANDLE` pins a particular window.
Add `--live` to start fishing until stopped. Every launch first fetches fresh
`/profile` and `/buffs` replies. Startup confirms the account name, balance, level
and positive fishing cooldown individually. Readable fields are retained across
confirmed replies during that launch, with observation times for each field.
Garbled XP, inventory, equipment and optional multipliers do not block fishing;
they are not imported as reliable player data by this startup reader. A confirmed
startup reply missing required fields retries that command after two seconds, up to three
attempts total. Other validated startup data is retained. After three failures,
the runner stops and lists the unreadable fields in Needs review.

Optional `--trips` allows 1–25 confirmed trips and `--seconds` allows 1–900 seconds;
omit them for continuous fishing. Ctrl+C stops the runner; input already sent cannot
be undone. Do not manually invoke VF commands while it is running.

The run is about progress, not a perfect logbook. A command whose outcome is
unknown (no reply within 20 s, an unreadable result, a failed hand-over) is noted
in Needs review without blocking; the run waits one full cooldown and carries on.
Three unknown outcomes in a row stop the run. Verification prompts, a changed
window or channel, and a message box that is not empty (Discord hides its
`Message @...` placeholder when it holds anything) stop or pause it. A typed
command whose picker cannot be verified is cleared and never submitted. Live
journal, trips, and review items are stored separately under ignored
`src/data/automation_live/`.

A reply is the newest message at the bottom of the chat: it must differ from the
newest message before sending (or repeat it once more in a row), be invoked by
you, be of the expected kind, and read the same twice. Nothing above it is
compared, so scrolling and older OCR noise cannot stop a run.

The current picker matcher requires an exact slash-command OCR line and an adjacent
Virtual Fisher label. Entry focuses the empty composer, types the command, then
presses Enter twice (selection, then submission), with a short pause between keys.
The command-picker row is never clicked.
If Discord renders those differently, the adapter stops before submission; the
matcher needs a supervised capture-based adjustment.

Native reply rereads use Tesseract when its executable is already on PATH, alongside
Windows OCR. A missing required field (balance, level, fishing cooldown) is first
reread from a crop of its own line, prepared several ways (inverted and thresholded
for Tesseract); it is accepted only when every readable value agrees and either both
engines or three Tesseract preparations read it. Otherwise the whole reply is reread
and both engines must agree on all parsed profile/buffs values. A startup retry
whose reply reads exactly like the previous one stops at once instead of resending. Nothing is installed automatically.

This repo contains my personal project of completely automating the Discord bot "Virtual Fisher".

The current implementation collects commands and wiki pages, then exports game
data as JSON. The web logbook supports simulation; the experimental native
adapter above adds a live runner with optional limits that still needs end-to-end live verification.

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

### Paste anything

Instead of filling each box, select a stretch of messages in Discord, copy, and
paste it into **Paste anything**, then select **Sort into boxes**. Discord's
message headers (`Virtual Fisher` / `APP` / ` — <time>`) split the paste into
messages; each Virtual Fisher reply is recognized as a profile, buffs, prestige
shop, pet, quests, or fishing result and placed in its box. When the same kind
appears more than once, the newest (last) one is used; all fishing results are
combined into one batch. Your own messages and other bot replies (shop,
boosts, "You don't have enough…") are skipped and counted. Text pasted without
headers is split where each response's first line appears. Nothing is saved
until you select **Read player state** and **Read fishing results** (or **Save
session**) as usual.

### Screenshots

With Windows OCR support installed, **Read screenshots** next to **Paste
anything** reads PNG or JPEG screenshots of Virtual Fisher replies and sorts the
text into the same boxes for review:

```powershell
uv pip install --python .venv -r requirements-ocr.txt
```

The repository's `.venv` was created with uv, which does not include pip. In a
standard `python -m venv` environment, use
`.venv/Scripts/python.exe -m pip install -r requirements-ocr.txt` instead.

Text recognition uses the OCR engine built into Windows 10/11 (no separate
program), runs on this computer, and the uploaded images are deleted after
reading. Discord's buttons, "used /command" lines, timestamps, and tips are
dropped, and known misreads are corrected (`Lv130` → `Lvl 30`, a period after
a buff name, emoji fragments such as `160 to Hooks`). Select screenshots in
time order, since the last of each kind is used. Profiles, buffs, and fishing
results read reliably; Windows OCR skips bold numbers with commas, so pet XP
progress from a `/pet` screenshot usually stays Unknown. Copied text remains the
most accurate input. Without the OCR packages, the page shows the install
command and everything else works as before.

### Live capture

Open **Live capture** (top right of the page, or <http://127.0.0.1:8765/live>),
choose the account you are playing, enter your Discord username (the name
above "You caught:" in your fishing results; remembered per account), choose
your bait, and select **Start**. Every few seconds the logbook copies the
Discord window's pixels, reads them with Windows OCR, and discards the image.
It only reads the screen: nothing is typed, clicked, or sent to Discord. Needs
Windows and the OCR packages above.

- **Pace follows your fishing cooldown.** Your cooldown changes as you level
  up and resets when you prestige, so capture runs at three quarters of the
  cooldown in your newest `/buffs` (from your saved snapshots at Start, then
  from any `/buffs` it reads), between 1 and 3 seconds; 3 seconds until a
  cooldown is known. If one capture finds two or more new fishing results, your
  cooldown has become quicker than it assumed, so it speeds up at once until
  the next `/buffs`. Run `/buffs` after a level-up or prestige to set it
  exactly. The live page shows the current pace and why.

- **Only your replies are saved.** Your display name (`Inventory of <name>`,
  `<name> used /fish`) and Discord username (fishing results'
  author line, `<username>'s current multipliers`) are kept as separate
  settings and never assumed to match each other. A reply naming anyone else
  goes to review; so does a reply that names no one (for example a `/pet`
  reply whose `used /pet` line OCR missed), because the account chosen on the
  page is not evidence of whose reply it is. Messages from other bots and
  players are left out even if their text looks like Virtual Fisher's.
- **One channel.** Capture only uses a window owned by the Discord program,
  and pins the window and channel it started in, checked two ways: the window
  title (Discord names it after the open channel) and the channel name in the
  capture's own pixels (the search box, `Search Virtual Fisher#7036`). If
  either changes, nothing is read until you return or restart. When OCR does
  not read the search box, replies are still read (each is checked for your
  names) but the channel counts as unconfirmed.
- **Review items block.** Anything in Needs review about your replies (an
  unreadable result, a possible repeat, a reply that may be yours) means
  what happened is unknown, so the page warns until you fix or dismiss it.
  Replies clearly naming another player are queued but do not block.

- Only the chat column is used. It is found from the content itself: the left
  edge shared by Virtual Fisher's message text, with distances measured in OCR
  text heights. Window size, resolution, display scaling, Discord's zoom,
  collapsed sidebars, and side panels do not matter, locally or over Remote
  Desktop.
- Messages already on screen when you start count as seen. Each capture is
  lined up against the previous one, and only messages below the overlap are
  new, so identical results in a row still count as separate trips. Captures
  are matched on the lines the parsers understand, so stray text such as the
  message box below the newest message cannot break the matching.
- Identical replies can make a capture impossible to count exactly: `[A, A]`
  on screen may be unchanged, or one `A` may have scrolled away as a new `A`
  arrived. The cut-off text above the first message and the send times break
  the tie when they differ. When nothing on screen tells, the possible repeat
  goes to review once (never guessed into the trip count) and blocks like any
  other review item until you dismiss it. The newest
  message is processed once something follows it or it reads the same in two
  captures in a row (or when you select **Stop**). A message whose header is
  cut off at the top is ignored until it is fully visible.
- Clean fishing results are saved as fishing sessions, each recording the
  observation it came from and, once seen this run, the biome, rod, and pet
  from `/profile` and the multipliers from `/buffs` it was fished with.
- The bait chosen on the page is only an assumption: trips are saved with it
  as "assumed bait", not as their bait, so bait comparisons do not use them.
  Bait is recorded once a reply confirms it: your profile's `Bait:` line,
  `/bait` (`Current bait: …`), `You are already using …`, the bait shop's
  `Selected: …`, or `You ran out of X` (which shows that trip used X). A
  change applies to later trips only. `You ran out of …` ends the bait even
  when the rest of that trip went to review; if it contradicts a confirmed
  bait, that trip's bait is saved as unknown.
- Clean profile, buffs, prestige shop, pet, and quests replies are saved as
  snapshots. Each section keeps when it was observed, so a new `/buffs` does
  not make an older balance look current, and charts plot each profile once at
  the time it was seen. Boost and "You don't have enough…" replies, and shop
  pages (`/shop …`, `/clan shop`; not parsed yet), are ignored.
- Windows OCR sometimes drops a count together with the emoji beside it
  (`Salmon` for `5 🐟 Salmon`). Such a line is reread from the same capture,
  enlarged 2× and 3×, and the count is used only when both reads agree on the
  same words with a number in front; otherwise the message goes to review.
- Anything else goes to **Needs review** with its reasons: parser warnings,
  fish missing from the wiki table, a profile for another account, pet replies
  whose XP OCR could not read, and unrecognized messages. Use **Load into
  boxes** to fix and save one by hand, or keep it as an example for improving
  the parsers. Review items keep the message text in the ignored
  `src/data/review_queue/` folder, which is the one place message text is stored.
- If the view changes (you scroll up, open another channel, or a menu covers
  the messages), capture pauses and resumes when the newest messages are
  visible again. After about a minute it continues from the new view and sends
  messages it cannot place to review rather than saving or skipping them.
- Capture pauses while Discord is minimized. Over Remote Desktop, Windows may
  stop drawing the session while the Remote Desktop window is minimized; the
  page then reports a blank capture.
- To analyse misreads, start the server with
  `--keep-captures docs/screenshots/ocr`. Each capture whose chat text changed
  is kept there as a PNG with a JSON file of the OCR lines (with positions) and
  the final chat lines. These are full Discord window images that can include
  unrelated conversations: the live page warns while they are kept, only the
  newest 200 are kept, and you should delete the folder when done; `docs/` is
  ignored by Git.

### Automation

Start the server with the project's `.venv` Python (it has the Windows OCR
packages): `.venv/Scripts/python.exe -m src.player_web`. Open **Automation**
(<http://127.0.0.1:8765/automation>), choose **Live** or **Simulation**, enter your
Virtual Fisher name and Discord username (remembered for next time), and
optionally a trip or minute limit; blank means run until **Stop now**. A live run
waits until Discord is in front, so click **Start**, then switch to Discord. If
Discord leaves the foreground, the run pauses and resumes when you come back.
Keep the message box empty. Live data stays in `src/data/automation_live/`.

While a run goes, the page shows your level with progress toward the next one,
and boxes for your profile, buffs, this run (trips, XP, money and rates),
inventory, and recent trips, refreshed every two seconds. Your full `/profile`
and `/buffs` are read at the start and again every 100 trips; in between, each
trip's XP (with level-ups from the wiki level table), catches, chest finds, quest
rewards and money are added to the last reading. A reply that cannot be read never
stops fishing: it is noted and tried again every 10 trips until it reads (without
a readable cooldown, fishing uses 5 s and "You must wait" replies correct it).
**Pause** holds the run without ending it; **Resume** carries on. The page also
counts chests by type, special fish and charms (with their levels) for this run
and across all automation runs, and splits quest, chest and level-up rewards.
Simulation runs the loop against a simulated Virtual Fisher.
Each step is: choose the next action (refresh `/profile`, then `/buffs` for the
cooldown, then fish), record it in the journal, send it, confirm the reply that
answers it, update state, and wait out the verified cooldown plus 0.5 s.

- At most one command is unanswered at a time. Every command is journaled
  (`src/data/automation_sim/journal/`) before it is sent, and its hand-over and
  reply after.
- A command whose outcome is unknown (no reply within 20 s, a failed hand-over,
  a reply with no evidence of which command it answers, or an unreadable result)
  is noted in Needs review without blocking. The loop waits one cooldown and
  carries on; three in a row stop the run.
- After a crash, the next start rebuilds the state from the journal. Commands
  that were never sent are dropped, unfinished logbook saves are redone (each
  trip is saved once, keyed by its command), and commands that may have been
  sent are noted in Needs review without blocking; a reply to one that is still
  visible is still counted once.
- Simulated trips are saved in `src/data/automation_sim/`, never in your
  logbook. Tests cover 100 simulated trips, with and without a crash in the
  middle, with exact totals.

The web page remains simulation-only and sends nothing to Discord. The separate
experimental `src.automation_live` runner implements the same contract as the
simulation (`src/automation_transport.py`). Project direction and the platform
rules on record are in `docs/planning/interaction-feasibility.md`.

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

Below the tally, an **XP check** lists every trip: the wiki base XP of what was
caught (from `src/reference/fish.json`), the XP the game gave, and their
ratio. A steady ratio is your overall XP multiplier; a change larger than the
game's rounding is marked ▲ next to that trip's events, so you can see effects
such as a charm levelling up. With an account chosen, catches the wiki says
cannot occur in that account's latest-snapshot biome are flagged, as are fish
missing from the wiki table. The batch's sell value is shown at wiki base
prices, without your sale multiplier. Where the wiki's two sources disagree
about a biome, the per-fish flags in `fish.json` are used. Saved sessions keep
these per-trip catches, XP, and events.

**Show history** adds an all-time fishing card for the account: trips, fish
and XP per trip, finds per 100 trips, chest contents, quest rewards, and
level-ups.

### Bait comparison

Set **Bait used** when reading or saving a fishing batch (smart paste fills it
from the profile's `Bait:` line). Each trip records that bait until the trip
with "You ran out of …"; later trips count as no bait. **Show history** then
compares baits across saved sessions: trips (and how many were boosted), fish
per trip, median XP ratio, gross value per trip at your sell price, bait cost
and net value per trip (unknown, not zero, when a price is missing), and finds per
100 trips, beside the wiki's listed effects from `src/reference/bait.json`.
Rows with fewer than 20 trips are marked as rough, and sessions saved without a
bait are left out. Global boosts, purchased boosts (which do not appear in
fishing results), charms, and pets also move these numbers, so compare batches
fished under similar conditions.

### Planner

**Show history** also opens with a planner. Enter an optional **Target level**
and **Target balance** next to the account. Using the latest full snapshot and
your saved fishing sessions, it estimates:

- XP and fishing trips to the next level, five and ten levels ahead, and the
  target level;
- gross catch value per trip (all saved trips, whatever conditions they were
  fished in), bait cost per trip (wiki price, one bait per cast as the bait
  shop says), net per trip, an upper bound per hour from your observed
  cooldown, and trips until your balance plus unsold fish reach the target
  balance (assuming you sell everything you catch). Net is shown only when the
  bait's price is known; an unknown cost is never treated as zero;
- a same-conditions estimate from your most recent trips (up to 500) fished
  with your current biome, rod, pet (and its level), bait, and `/buffs`
  multipliers (which reflect charms, bought boosts, and upgrades), leaving out
  trips under a global boost. It says how many trips were left out and which
  conditions your latest snapshots do not show. Pasted sessions and live
  sessions saved before a `/profile` and `/buffs` record no conditions, so
  they are not used for it;
- how long ago your profile, buffs, and quests were observed (pasted ones say
  their observation time is unknown);
- trips to finish each quest from the latest pasted `/quests`: fish caught per
  trip for fishing quests, chest finds (boxes, chests, crates) per trip for
  chest quests, and the XP model for level-up quests. Pet quests and unfamiliar
  quest types say there is not enough data rather than guessing.

The XP needed to advance from level L is the running total of the wiki level
table through L, shipped as `src/reference/level_xp.json`. This matched live
profiles exactly at levels 29, 30, and 131. The planner checks the model against
every snapshot that shows current/required XP and, if any disagree, projects
only the next level, which comes straight from the game's own "XP to next
level". The wiki's level-up money does not match the game, so level-up rewards
are not projected. Estimates count fishing XP only; quest rewards make
progress faster.

Money uses the wiki base sell prices (`src/reference/fish.json`) times the
**Sell price** multiplier from your newest `/buffs`: a profile's Fish Value
equals its fish at base prices times that multiplier (confirmed on two
accounts, 2.20× and 4.83×). The planner rechecks this on every snapshot that
has both `/profile` and `/buffs` and warns if a Fish Value disagrees. The XP
check on a fishing batch also shows each trip's sell value at the chosen
account's sell price.

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

## Run logs

The live runner prints readable progress updates instead of raw JSON. Each
launch saves the same timestamped output under `docs/runs/` and keeps the latest
five run logs. Startup retries, confirmed commands, saved trips, and stop or
review reasons are included. Logs are flushed as they happen, so partial runs
remain available after a crash. This folder is ignored by Git. Detailed
transport diagnostics remain in `src/data/automation_live/`.

OCR fish labels are matched against the local fish reference. Exact names win;
otherwise common `rn`/`m` and `vv`/`w` confusions and small letter errors are
accepted only with a clear lead over other candidates. Short or ambiguous
labels remain unreadable and are reread. Fish quantities and XP are never
corrected by fuzzy matching.

## Tests

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

Tests use temporary output folders, local HTML fixtures, and simulated HTTP
responses. They do not contact the websites or modify your collected data.
