# Player Snapshots and Progress Dashboard

## Purpose

AutoVirtualFisher currently parses pasted Virtual Fisher responses into an in-memory player state. That state disappears when the page reloads or the CLI exits. This feature will keep a local history for multiple Discord accounts and turn consecutive imports into a useful progress view.

Success means a user can paste current bot responses, see which account they belong to, have a valid full import saved automatically, save a partial import deliberately, reopen the application later, and compare the two most recent valid full snapshots for an account.

## Scope

This feature includes:

- account-name parsing from `/profile`;
- local snapshot persistence;
- automatic and manual saves;
- duplicate suppression;
- per-account history;
- comparison of the two latest valid full snapshots;
- web controls and status messages;
- CLI commands for saving, history, and comparison;
- tests for the storage and interface behavior.

It does not connect to Discord, schedule bot actions, synchronize snapshots between computers, provide authentication, or build long-term charts. Those can build on the snapshot API later.

## Account Identity

The parser will add `account_name` to the profile state. It comes from the heading `Inventory of <name>` in `/profile`. The original display spelling is retained. A normalized, filesystem-safe account key is derived internally and is never accepted directly as a path.

If a pasted response has no account name, the web interface requires the user to select an existing account before a manual save. Nothing is preselected for this case. The CLI requires `--account`. An unknown account name may be created by a manual save, but it must be supplied explicitly.

## Snapshot Validity

A valid full snapshot requires all of the following:

- a parsed account name;
- a nonnegative numeric balance;
- a nonnegative integer level;
- no conflicting values for those fields.

Pet, buffs, prestige-shop, inventory, bait, and equipment data remain optional because the user may not paste every supported response each time. Their absence does not invalidate the core profile snapshot.

Snapshots that do not meet the full criteria are partial snapshots. Partial snapshots may be saved manually but are never used as the account's latest valid full snapshot and never participate in headline progress comparisons.

## Storage Model

Snapshots are stored under `src/data/player_snapshots/`, which is already covered by the repository's ignored `src/data/` area. Data remains local to the computer.

Each snapshot is one UTF-8 JSON file stored under an account-specific directory. Its filename contains a UTC timestamp and a collision-resistant suffix. A snapshot contains:

- schema version;
- snapshot identifier;
- UTC capture timestamp;
- display account name;
- normalized account key;
- save mode (`automatic` or `manual`);
- validity (`full` or `partial`);
- parsed player state;
- parser warnings present when it was saved.

The store writes to a temporary sibling file, flushes it, and replaces the destination atomically. It never edits an existing snapshot. History is append-only.

The store derives account lists and history by scanning snapshot metadata. It does not maintain a separate index initially, avoiding index-repair and synchronization problems at this scale.

Malformed or unreadable snapshot files are skipped. The caller receives warnings naming the affected snapshot file, while valid history remains available.

## Duplicate Suppression

Before an automatic save, the store compares the parsed state with the account's newest valid full snapshot. Snapshot metadata and warning order are excluded from comparison. If the state is identical, no new file is written and the interface reports that the latest state was unchanged.

Manual saves are not deduplicated because the user explicitly requested a historical record. They remain marked manual and partial or full according to validation.

## Web Flow

The web page adds an account selector with no selection for unnamed imports, an explicit **Save snapshot** button, save-status feedback, recent history, and a progress card.

**Read player state** continues to parse without requiring storage. After parsing, it automatically saves only when the state qualifies as a valid full snapshot. The result remains visible if saving fails. The status distinguishes saved, unchanged, partial, and failed outcomes.

**Save snapshot** saves the current parsed state manually. If the parser found an account name, that account is used. Otherwise, the user must select an account. A missing selection produces an inline error and does not write a file.

Opening the page lists known accounts. Selecting one shows its latest valid full snapshot, recent snapshot metadata, and progress comparison. Raw pasted Discord responses are never stored or restored; only the parsed state is persisted.

The first dashboard version compares the newest two valid full snapshots for:

- balance;
- level and current XP;
- fish inventory value;
- bait quantity when the bait type is unchanged;
- fish, exotic fish, and special inventory counts;
- owned-pet levels and current pet XP.

Missing or incomparable values display as unknown rather than zero. A bait-type change is reported as a change instead of a misleading quantity delta. The dashboard does not combine partial snapshots with full snapshots.

## CLI Flow

File-based parsing remains available without persistence. The CLI adds:

- `--save` to save the parsed result;
- `--account NAME` for a manual save without a parsed profile name;
- `--history` to list recent snapshots for an account;
- `--compare` to compare the latest two valid full snapshots for an account;
- `--snapshot-dir PATH` for tests and deliberate alternate local storage.

When `--save` is used with a valid full state, it follows automatic-save validation and duplicate suppression. When the state is partial, `--account` is required and the save is marked manual. JSON output remains machine-readable; save messages go to standard error when `--json` is active.

The interactive terminal menu gains account selection, manual save, history, and comparison actions. It uses the same storage service as the web interface.

## Components

`src/player_state.py` remains responsible for parsing and display-neutral state values. It will parse the account name but will not read or write snapshots.

`src/player_snapshots.py` will own validation, account-key normalization, atomic writes, history loading, duplicate detection, and comparisons. Its public functions accept a storage root so tests can use temporary directories.

`src/player_web.py` and `src/player_cli.py` will call the snapshot service and translate results into their respective presentation formats. Neither interface will implement independent persistence rules.

## Error Handling

All storage errors are returned as user-facing results rather than terminating the parser. A failed automatic save leaves the parsed state usable. Invalid manual saves explain whether the account selection or required state is missing. Files outside the configured snapshot root are never read through account input.

Comparisons only use values present in both snapshots. Unsupported schema versions and malformed files are skipped with warnings. An account with fewer than two valid full snapshots receives a clear “another full snapshot is needed” message.

## Testing

Unit tests will cover:

- account-name parsing, including Discord emoji in the heading;
- full versus partial validation;
- safe account-key generation;
- atomic snapshot creation;
- automatic duplicate suppression;
- intentional manual duplicates;
- multiple-account isolation;
- malformed-file recovery;
- latest-full selection;
- numeric, inventory, bait, and pet comparisons;
- missing-value behavior.

Web tests will cover automatic saving, manual account selection, save-status messages, account history, comparison rendering, and a failed save that still renders the parsed state. CLI tests will cover save, history, comparison, JSON-output separation, and alternate snapshot directories.

The existing parser and scraper suites must continue to pass.

## Privacy and Compatibility

Snapshots contain game account state and Discord display names, so the snapshot directory remains ignored by Git. The server continues to bind only to `127.0.0.1`, uses no external assets, and does not transmit snapshot data.

Snapshot JSON includes a schema version. Readers accept the current version and skip unsupported versions with a warning, allowing a future migration tool without silently misreading data.
