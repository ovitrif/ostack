---
name: claude-switch
description: >-
  Switch Claude Code (CLI) and the Claude desktop app between several Claude accounts on one Mac with
  app_switch.py: one app profile per account, sessions carried by id, sidebar groups, routines and settings
  ported, and open app pane sessions resumed on the new account. Use when the user asks to switch
  accounts, or says sessions or sidebar groups are missing after a switch.
license: MIT
metadata:
  version: "0.1.0"
---

# Claude Switch

Switch only when the user asks you to. "I need to switch" said to a session doing other work is notice: save your notes, say you are ready, and leave the switch to them.

The app's storage changes between releases. Read the current state before planning, and prove the result after a real launch. When something here no longer holds, investigate, finish the job, then update this file.

## Private configuration

Read `${XDG_CONFIG_HOME:-~/.config}/ostack/claude-switch/NOTES.md` first when it exists. Keep private instructions outside this skill.

The scripts read `config.yaml` from the same directory at runtime. Optional keys are `wake_text` (with `{email}`), `waker_cmd` (a command receiving the request as its final argument), `bundle_prefix`, and `forced_prefs` (app preference overrides). Missing configuration uses generic defaults. Existing `CLAUDE_APP_WAKE_TEXT`, `CLAUDE_APP_WAKER_CMD` and `CLAUDE_ACTIVE_BUNDLE_PREFIX` environment overrides take precedence.

Discover accounts and aliases from cswap/aswap each time; do not maintain a separate account map. `cswap list --json` lists aliases but omits the account UUID, so the scripts read cswap's registry for desktop scope IDs. Never copy credentials into this configuration.

## Requirements

Use macOS with the Claude desktop app, cswap (claude-swap), aswap and uv.

- `cswap` and `aswap`: `uv tool install 'claude-swap[menubar]'` and `uv tool install aswap`. Every account is stored in cswap first (`claude` → `/login`, `aswap add`, `aswap alias <slot> <name>`), so names like `work` and `personal` work everywhere below.
- Upgrade existing installations with `cswap upgrade` and `aswap upgrade` when the user requests it. Before switching after an upgrade, check `cswap --version` and `aswap --version`, then inspect the current account/profile data and run the dry run; investigate changed formats rather than assuming compatibility or downgrading.
- `uv` runs the scripts: `uv run --with pyyaml --with plyvel-ci python scripts/<tool>.py …`, from this skill's folder. Use uv's default Python; plyvel has no Python 3.14 wheel.
- Optional: [cua-driver](https://cua.ai/docs/cua-driver) types the wake prompt into each session and archives leftover Offline rows. Without it the switch prints what is left to do by hand.

## Where the app keeps the sidebar

All under `~/Library/Application Support/Claude/`. Each account is a scope `<account uuid>/<org uuid>`.

- Session records: `claude-code-sessions/<scope>/local_<id>.json` (`title`, `lastActivityAt`, `isArchived`), plus `archived-sessions.idx` and `deleted_<id>` markers. A record keeps its id across accounts.
- Groups live in local copies that must agree:
  - `claude_desktop_config.json` → `preferences.epitaxyPrefs`: `dframe-group-scopes[<scope>]` (`groups`, `assignments`, `order`) and `dframe-code-sections[<scope>]`.
  - Local Storage (`Local Storage/leveldb`, origin `https://claude.ai`): `LSS-persisted.dframe-group-scopes`, `LSS-persisted.dframe-code-sections`, and `dframe-store` → `state.customGroupsByScope[<scope>]`, `state.codeSidebarByScope[<scope>]`, `state.sidebarRowCountsByScope`.
- The server copy of `dframe-store` replaces local edits at launch, unless Local Storage holds `ccd-sync-pending:ccd/dframe-store` set to the signed-in `<account>/<org>`; then the app uploads the local state and clears the marker. To inspect a changed format, locate `ccd-sync-pending`, `serverMerge` and `dframe-store` in the current renderer bundle.
- A group id belongs to one account: a group new to the target gets a fresh `cg-<uuid4>` id, or the app drops it at launch.
- Transcripts (`~/.claude/projects/<cwd>/<cliSessionId>.jsonl`) are shared by all accounts. Pins are shared within each app profile. Artifacts, docs and cloud sessions stay with their account. Sessions retain their creation-time tool snapshot; use a fresh session when newly added tools are missing.
- Remote Control sessions show in the sidebar but live on the account's server, so `sidebar_state.py` does not list them.
- Bypass permissions is opted into per account. After the first switch to a new account, the user turns it on in Settings → Claude Code; never write that opt-in yourself, it is their consent.

## Profiles

Each account has its own app profile, so a switch needs no new login. The stock profile stays signed in to one account; aswap keeps the others in `~/.claude-swap-backup/desktop/<slot>-<email>` and opens one with `aswap desktop <slot>`. A profile links the stock `claude-code-sessions` folder; everything else (config, Local Storage, layout, login) is its own. Never copy a profile's `Cookies` or `config.json` token caches.

The first switch to an account with no profile quits every app window and waits up to 15 minutes for the user to sign in and open the Code tab: the sign-in's `claude://` callback goes to whichever app is running.

## Tools

- `sidebar_state.py` is read-only and works while the app runs. A scope is a cswap alias or an `<account>/<org>` prefix.
  - `[summary] [scope]`: each account's live sessions, groups and routines, with `problems` to fix.
  - `tree <scope>`: readable stored sidebar with IDs, section order, sorting and hidden/collapsed markers.
  - `snapshot <scope>`: save YAML and text baseline, checking for changes during capture. Both dry runs and real switches capture the source automatically.
  - `verify <baseline.yaml> <scope>`: compare the baseline with the target profile; exit 0 means represented data matches, 1 differences, 2 inconclusive source data.
  - `sessions [scope]`: every session by id; save it before a switch to prove the result against.
  - `diff <a> <b>`: every session missing or different between two scopes, or a scope and a saved file (`<file.yaml>:<scope>`); exits 1 when any differ.
- `app_switch.py <account> [--dry-run] [--from <account>] [--wake <title|id>,…] [--no-groups]` coordinates the desktop switch. If the CLI account cannot run a session, switch the CLI first with `cswap tui`, start a fresh terminal session, then pass `--from <desktop-account>` to this script. It runs `cswap switch`, quits the app, carries sessions (each record on the account left wins; one deleted there is archived; one only another account has comes when live there and newer), recurring routines, layout, groups, settings and Remote Control CLIs, opens the app as the account, archives leftover Offline Remote Control rows, wakes the sessions open in the source app panes, and logs to `${XDG_STATE_HOME:-~/.local/state}/ostack/claude-switch/switch-<stamp>.log`. It saves session records, archive/deletion markers and routines under `sessions-<stamp>` before changing them; layout, groups and settings have separate backups in the same private state directory. Run it from a plain terminal session: quitting the app ends app sessions, so from inside one it restarts itself in a detached tmux session.
  - Without `--wake`, capture and print all open source app pane sessions before quitting, including idle or paused sessions, then wake exactly those. Use `--wake` only to override the list. Ask only when detection is ambiguous (such as conflicting layout copies or unsupported pop-outs), and resolve that before switching.
  - The wake opens `claude://code/continue?session=<local id>` and types the wake prompt (`CLAUDE_APP_WAKE_TEXT` overrides it). Never send a bare `continue`: it reads as the user's answer to a pending question. Sessions it cannot reach are printed, or handed to `CLAUDE_APP_WAKER_CMD` when set.
- `port_groups.py <from> <to> [--dry-run]` gives `<to>` the groups of `<from>` in every copy.
- `sync_settings.py <from> [--dry-run]` copies app preferences (`app_switch.py` runs it). It leaves permission approvals, trusted folders, `config.json`, Cookies and device ids untouched. Server-managed preferences may need verification in the app after launch.
- `local_storage.py list [substring] | get <key> | put <key> <file|->` reads or writes one Local Storage key. `put` needs the app quit.
- `dock_launcher.sh [--dock]` builds `~/Applications/Claude Active.app`, which opens the app as cswap's active account (needs Xcode); `--dock` puts it in the Dock in place of Claude.app.

Set `CLAUDE_APP_SUPPORT=<dir>` to read an app profile other than the stock one.

## Switch

1. **Before.** Run `sidebar_state.py snapshot <current>`; keep its YAML path and review its readable tree and data issues. Screenshots are optional, never a prerequisite. Resolve data-source discrepancies before calling the inventory verified. Remote Control/cloud rows and unsaved UI state need a separate read-only app inspection. Show the detected open-pane wake list; ask only if ambiguous.
2. **Dry run.** `app_switch.py <target> --dry-run` and show the user what it would carry.
3. **Switch.** `app_switch.py <target>` (or `--wake <names>` for an explicit override) and read its diff by id at the end.
4. **After.** Confirm the desktop account and browser preference in the app; server-managed preferences can override copied local values. Leave per-account permission approvals to the user. Run `sidebar_state.py verify <baseline.yaml> <target>` after the app settles, then `tree <target>`. Inspect differences by full ID; distinguish expected target-only records from lost or altered source records. Do not call an inconclusive result a match. Independently check Remote Control rows and wake behavior through the app; no screenshot is required.
5. **Fix.** A missing session or group: run `port_groups.py`, or follow Port below. A new account: remind the user to turn on Bypass permissions.

## Port

For restoring a sidebar by hand ("restore my sidebar from work").

1. **Read.** Run `sidebar_state.py`; the signed-in account shows `signed_in: true`. If the copies disagree or the groups are not where this file says, find them first: search the app folder for a known group id (`grep -rlaF cg-…`) and Local Storage with `local_storage.py`.
2. **Plan from the current state.** Compare edits with the saved `last-port.yaml` baseline when available; preserve edits made since that port and ask when both sides changed groups. The later `lastActivityAt` wins a session on both accounts, archive state included. A session only on the source comes over when live there. A `deleted_<id>` on the target stays deleted. The target's groups become the source's: same names, order and live members; keep the target's id for a group of the same name, give a new one a fresh id. Show the user the plan (added, updated, archived, kept, groups after) and wait for a go.
3. **Write with the app quit.** Quit it (`osascript -e 'tell application "Claude" to quit'`) and wait until no process ends in `.app/Contents/MacOS/Claude` (`ps`). Back up the config, the target's session folder and `Local Storage/leveldb` to `~/.local/state/ostack/claude-switch/<stamp>/`. Copy the session records and archive index, write the groups and sections to every copy, drop the target's `sidebarRowCountsByScope` entry, and set the pending-upload marker.
4. **Verify in the app.** Launch it, wait about 45 seconds, quit it, and run `sidebar_state.py`: the target's groups must equal the plan in every copy, and the marker must be gone. Launch and quit once more to prove the server copy holds. Then archive every Offline Remote Control row on the target.
5. **Report** the result and the backup folder.

When the app restores something other than what you wrote, compare the state before and after the launch to see which copy won, find its source, and fix the port.

The text view nests helper sessions under their recorded parent and omits hidden side chats and empty sections from the main tree, while retaining their records in YAML. Compare recency-sorted rows by membership rather than position; compare explicit manual order when configured. Reconcile Remote Control rows with a read-only app inspection, recording names and connection status separately from local records.

Archived sessions stay out of the visible sidebar. A dangling group reference with no source record and only archived records elsewhere is informational, not an inconclusive baseline; do not restore it as a live row. Unknown references or conflicting live data still require investigation.

Switch comparisons ignore archived records absent on the other account and differences between two archived copies. Keep them in the full inventory, but do not list them as lost sessions or ask to restore them. A live-to-archived transition still appears for review.
