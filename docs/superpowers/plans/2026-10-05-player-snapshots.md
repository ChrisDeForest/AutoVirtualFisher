# Player Snapshots and Progress Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist parsed Virtual Fisher states for multiple local accounts and show reliable progress between the latest two valid full snapshots in the web and CLI interfaces.

**Architecture:** Keep parsing display-neutral in `player_state.py`, put all persistence and comparison rules in a new `player_snapshots.py` service, and make the web and CLI thin consumers of that service. Store one immutable JSON document per snapshot under an ignored local directory and derive history by scanning it.

**Tech Stack:** Python 3.12 standard library, `unittest`, `http.server`, JSON, HTML/CSS.

**Spec:** `docs/superpowers/specs/2026-10-05-player-snapshots-design.md`

## Global Constraints

- Store snapshots locally under `src/data/player_snapshots/` by default; do not transmit them.
- Do not store or restore raw Discord response text.
- A full snapshot requires account name, nonnegative balance, and nonnegative integer level.
- Partial snapshots require an explicit account and never participate in headline comparisons.
- Automatic saves deduplicate against the newest valid full snapshot; manual saves do not.
- Every storage API accepts an alternate root for isolated tests.
- Keep the server bound to `127.0.0.1` and add no runtime dependency.
- Missing or incomparable values remain unknown rather than becoming zero.

## Review Focus

- Two display names that sanitize to the same slug must remain isolated; Task 2 tests hash-suffixed account keys.
- A manually chosen account must not silently override a different account parsed from `/profile`; Task 2 tests rejection of that mismatch.
- A corrupt file mixed with valid history must produce a warning while preserving valid results; Task 2 tests mixed history.
- Equal states with parser warnings in a different order must deduplicate; Task 2 tests canonical warning ordering.
- Changing bait types must report the type change without a quantity delta; Task 3 tests this comparison branch.

---

### Task 1: Parse Account Identity

**Files:**
- Modify: `src/player_state.py:10,75-110`
- Modify: `tests/test_player_state.py:11-115`

**Interfaces:**
- Produces: `parse_responses(...)["profile"]["account_name"] -> str | None`
- Consumes: existing `_clean()` Discord emoji normalization.

- [ ] **Step 1: Write failing parser tests**

Add `test_profile_heading_records_account_name` and `test_profile_heading_strips_discord_badge` asserting `Inventory of BugParticle` and `Inventory of epicmouse :badge_bronze:` produce the exact display names. Extend the empty-input expectation so `account_name` is `None`.

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `.venv/Scripts/python.exe -m unittest tests.test_player_state.PlayerStateTests.test_profile_heading_records_account_name tests.test_player_state.PlayerStateTests.test_profile_heading_strips_discord_badge -v`

Expected: FAIL because `account_name` is absent or unknown.

- [ ] **Step 3: Add account-name parsing**

Add `account_name` to `PROFILE_FIELDS`. In the existing `Inventory of ...` heading branch, assign the cleaned suffix through the existing conflict-aware `assign()` helper before continuing.

- [ ] **Step 4: Run the parser suite**

Run: `.venv/Scripts/python.exe -m unittest tests.test_player_state -v`

Expected: all player-state tests pass.

- [ ] **Step 5: Commit the parser change**

```powershell
git add src/player_state.py tests/test_player_state.py
git commit -m "feat: parse player account names"
```

### Task 2: Add the Snapshot Store

**Files:**
- Create: `src/player_snapshots.py`
- Create: `tests/test_player_snapshots.py`

**Interfaces:**
- Consumes: parsed state dictionaries from `parse_responses()`.
- Produces: `validate_full_state(state: dict) -> tuple[bool, list[str]]`.
- Produces: `account_key(name: str) -> str`, using a readable slug plus a stable digest of `name.casefold()`.
- Produces: `save_snapshot(state: dict, root: Path, mode: str = "automatic", account_name: str | None = None) -> dict` with `status` equal to `saved`, `unchanged`, or `rejected`, plus `snapshot`/`reason` where applicable.
- Produces: `load_history(account_name: str, root: Path, limit: int | None = None) -> tuple[list[dict], list[str]]`, newest first.
- Produces: `list_accounts(root: Path) -> tuple[list[str], list[str]]`.

- [ ] **Step 1: Write validation and account-key tests**

Test full versus partial states, negative/noninteger core values, names containing slashes and punctuation, collision resistance for `A/B` versus `A:B`, and case-insensitive stability for the same account name.

- [ ] **Step 2: Run those tests and verify failure**

Run: `.venv/Scripts/python.exe -m unittest tests.test_player_snapshots.SnapshotStoreTests.test_validation tests.test_player_snapshots.SnapshotStoreTests.test_account_keys_are_safe_and_collision_resistant -v`

Expected: ERROR because `src.player_snapshots` does not exist.

- [ ] **Step 3: Implement validation and account keys**

Set `DEFAULT_SNAPSHOT_ROOT = Path(__file__).parent / "data" / "player_snapshots"` and `SNAPSHOT_SCHEMA_VERSION = 1`. Account keys must contain only lowercase ASCII letters, digits, and hyphens, followed by an eight-character SHA-256 prefix derived from the case-folded display name.

- [ ] **Step 4: Write failing save and history tests**

Cover atomic file creation with the required metadata, automatic duplicate suppression, warning-order-insensitive deduplication, deliberate manual duplicates, multiple-account isolation, explicit-account requirements for partial saves, rejection when an explicit account conflicts with parsed `/profile`, limit ordering, unsupported schema versions, and one malformed file mixed with valid history.

- [ ] **Step 5: Run the store tests and verify failure**

Run: `.venv/Scripts/python.exe -m unittest tests.test_player_snapshots -v`

Expected: validation/key tests pass; save/history tests fail because persistence is not implemented.

- [ ] **Step 6: Implement immutable atomic persistence and history scanning**

Write each document to a temporary sibling path, flush and `os.fsync()` it, then publish it with `os.replace()`. Use a UTC ISO-8601 timestamp and a random suffix for the snapshot ID. Canonicalize warning lists only for duplicate comparison. Never construct a path directly from user input.

- [ ] **Step 7: Run the snapshot-store suite**

Run: `.venv/Scripts/python.exe -m unittest tests.test_player_snapshots -v`

Expected: all snapshot-store tests pass.

- [ ] **Step 8: Commit the store**

```powershell
git add src/player_snapshots.py tests/test_player_snapshots.py
git commit -m "feat: persist local player snapshots"
```

### Task 3: Compare Valid Full Snapshots

**Files:**
- Modify: `src/player_snapshots.py`
- Modify: `tests/test_player_snapshots.py`

**Interfaces:**
- Consumes: `load_history(account_name, root)` from Task 2.
- Produces: `latest_full_snapshots(account_name: str, root: Path, count: int = 2) -> tuple[list[dict], list[str]]`.
- Produces: `compare_latest(account_name: str, root: Path) -> dict` with `status`, `older`, `newer`, `changes`, and `warnings`.
- `changes` uses stable keys for `balance`, `level`, `xp_current`, `fish_value`, `bait`, `inventory`, and `pets` so both interfaces render the same result.

- [ ] **Step 1: Write failing comparison tests**

Assert deltas for balance, level/current XP, fish value, unchanged-bait quantity, each inventory category, pet levels, and pet current XP. Assert unknown for a field missing in either snapshot, a type-change result for different bait names without a quantity delta, exclusion of partial snapshots, and `needs_another_full_snapshot` with fewer than two valid full snapshots.

- [ ] **Step 2: Run comparison tests and verify failure**

Run: `.venv/Scripts/python.exe -m unittest tests.test_player_snapshots.SnapshotComparisonTests -v`

Expected: FAIL because comparison functions are absent.

- [ ] **Step 3: Implement latest-full selection and comparison**

Use small private helpers for scalar deltas, inventory-map deltas, and pet progress. Include only fields known in both snapshots. Represent bait changes as `{from, to}` and same-bait stock changes as `{bait, delta}`.

- [ ] **Step 4: Run all snapshot tests**

Run: `.venv/Scripts/python.exe -m unittest tests.test_player_snapshots -v`

Expected: all storage and comparison tests pass.

- [ ] **Step 5: Commit comparison support**

```powershell
git add src/player_snapshots.py tests/test_player_snapshots.py
git commit -m "feat: compare player snapshot progress"
```

### Task 4: Integrate Snapshots into the Web Logbook

**Files:**
- Modify: `src/player_web.py:15-96`
- Modify: `src/player_web.html`
- Modify: `tests/test_player_web.py:12-75`

**Interfaces:**
- Consumes: `save_snapshot`, `list_accounts`, `load_history`, `latest_full_snapshots`, `compare_latest`, and `DEFAULT_SNAPSHOT_ROOT` from Tasks 2-3.
- Produces: `Handler.snapshot_root`, overridable by a test subclass.
- Produces: POST actions `parse`, `save`, and `json`; GET query `account=<display name>`.

- [ ] **Step 1: Isolate web tests from real player data**

Create a temporary snapshot directory in `WebTests.setUpClass`, subclass `Handler` with `snapshot_root` pointing to it, and clean it in `tearDownClass`.

- [ ] **Step 2: Write failing automatic-save web tests**

Post a valid named profile and assert a snapshot is created plus a “Snapshot saved” status. Repeat the post and assert the status says unchanged and the file count stays one. Post an invalid/partial state and assert it renders without autosaving.

- [ ] **Step 3: Implement automatic-save handling**

Refactor form handling so parse results are produced first, valid full states call `save_snapshot(..., mode="automatic")`, and storage errors become page warnings without replacing parser results.

- [ ] **Step 4: Write failing manual-save and dashboard tests**

Assert an unnamed partial save is rejected without an account, succeeds with a selected existing account, and does not become the latest full state. Assert account options, recent history metadata, latest-state rendering on GET, comparison deltas, no raw Discord text restoration, and malformed-history warnings. Add the failed-write case by pointing `snapshot_root` at an unusable path and assert the parsed balance still renders.

- [ ] **Step 5: Implement web controls and dashboard rendering**

Add the account selector, named submit actions, **Save snapshot**, save status, recent history, and progress card. Parse GET queries with `urllib.parse.urlsplit()`/`parse_qs()`. Escape every account name and stored value before inserting HTML.

- [ ] **Step 6: Run web and parser tests**

Run: `.venv/Scripts/python.exe -m unittest tests.test_player_web tests.test_player_state -v`

Expected: all tests pass and no test writes under the default snapshot directory.

- [ ] **Step 7: Commit the web integration**

```powershell
git add src/player_web.py src/player_web.html tests/test_player_web.py
git commit -m "feat: add snapshot history dashboard"
```

### Task 5: Integrate Snapshots into the CLI and Document Usage

**Files:**
- Modify: `src/player_cli.py:27-88`
- Create: `tests/test_player_cli.py`
- Modify: `README.md:7-62`

**Interfaces:**
- Consumes: all public snapshot APIs from Tasks 2-3.
- Produces: CLI options `--save`, `--account`, `--history`, `--compare`, and `--snapshot-dir`.

- [ ] **Step 1: Write failing file-mode CLI tests**

Using a temporary snapshot directory, test a valid `--save`, duplicate `--save`, partial save requiring `--account`, `--history --account`, `--compare --account`, and JSON mode keeping stdout valid JSON while save status appears on stderr.

- [ ] **Step 2: Run CLI tests and verify failure**

Run: `.venv/Scripts/python.exe -m unittest tests.test_player_cli -v`

Expected: FAIL because the new options are unrecognized.

- [ ] **Step 3: Implement file-mode snapshot commands**

Add the five arguments, validate incompatible combinations through `argparse`, and render history/comparison in plain text or JSON. `--snapshot-dir` defaults to `DEFAULT_SNAPSHOT_ROOT` and must be passed to every storage call.

- [ ] **Step 4: Write and implement interactive-menu coverage**

Extract the account/history/comparison formatting into pure helpers and test them directly. Add menu choices for selecting an account, manual save, recent history, and latest comparison; keep existing paste choices intact.

- [ ] **Step 5: Update the README**

Document automatic web saves, manual partial saves, the ignored snapshot location, multi-account selection, progress comparisons, CLI examples, and the fact that raw Discord text is never persisted.

- [ ] **Step 6: Run full verification**

Run: `.venv/Scripts/python.exe -m unittest discover -s tests -v`

Run: `.venv/Scripts/python.exe -m compileall -q src tests`

Run: `git diff --check`

Expected: every test passes, compilation emits no errors, and the diff check emits no errors.

- [ ] **Step 7: Manually verify the local web flow**

Start the server on a free port, paste a valid named profile twice, confirm one automatic snapshot, manually save a pet-only response to the selected account, and confirm the dashboard still compares the two latest valid full snapshots.

- [ ] **Step 8: Commit CLI and documentation**

```powershell
git add src/player_cli.py tests/test_player_cli.py README.md
git commit -m "feat: add snapshot commands to player CLI"
```
