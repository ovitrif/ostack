# Claude Switch

Switch Claude Code and the Claude desktop app between accounts while preserving local sessions, sidebar groups, routines and settings.

## Requirements and setup

Requires macOS, the Claude desktop app and [uv](https://docs.astral.sh/uv/getting-started/installation/). Install the account tools:

1. **[claude-swap](https://github.com/realiti4/claude-swap)** (`cswap`): stores each account's Claude Code login and switches the CLI between them. The `menubar` extra adds a menu bar switcher.

   ```bash
   uv tool install 'claude-swap[menubar]'
   ```

2. **[aswap](https://github.com/BlackHole1/aswap)**: claude-swap with patches, plus one Claude desktop app profile per account (`aswap desktop <slot>` opens one).

   ```bash
   uv tool install aswap
   ```

3. **This skill's scripts**, run with `uv` (no install step): `app_switch.py` does the switch, `sidebar_state.py` reads and compares sidebars, `port_groups.py` and `sync_settings.py` carry groups and settings.
4. Optional: **[cua-driver](https://cua.ai/docs/cua-driver)**, which lets the switch type a wake prompt into each session open in the app panes.

Update existing installations with:

```bash
cswap upgrade
aswap upgrade
```

The scripts read cswap's account registry and the desktop app's profile data. After upgrading, run a dry run before switching to check for storage-format changes.

### One-time setup

1. Add each account to cswap: run `claude`, `/login` with the account, then `aswap add`.
2. Give the accounts short names: `aswap alias 1 work`, `aswap alias 2 personal`.
3. Make one Claude Code session in a plain terminal the "switch session", for example `claude -n cswap`. Run it in a terminal, not inside the desktop app: the switch quits the app, which would end an app session halfway through.

## Copy-paste prompts

Use these in a Claude Code terminal session with the skill installed. Replace `work` and `personal` with your account aliases from `cswap list`. The source is the account still open in the desktop app, even if you already switched the CLI with `cswap tui` and started a fresh session on the target account.

### Switch in one prompt

```text
Use claude-switch to switch the desktop app from work to personal and resume my open panes.
```

### Check first, then switch

```text
Use claude-switch to dry-run work → personal. Show the sidebar as text and which
open panes will resume. Wait for my go before switching.
```

After reviewing the output:

```text
Go ahead with the switch.
```

## Daily switch, step by step

1. **Pause the work.** Ask the sessions that are working to save their notes and stop.
2. **Open the switch session.** `claude --resume cswap` in a terminal. It stays alive while the app restarts. If the CLI cannot run on its current account, use `cswap tui` first and start a fresh CLI session. Leave the desktop on the source account and use `--from work` in the switch command.
3. **Capture the sidebar.** Save a machine-readable baseline and a text view of the stored sidebar. Screenshots are optional:

   ```bash
   uv run --with pyyaml --with plyvel-ci python scripts/sidebar_state.py snapshot work
   ```

4. **Dry run.** "Switch to personal, dry run first." It shows what will be carried: sessions, groups, routines.
5. **Switch.** "Switch to personal." The session runs, from the skill's folder:

   ```bash
   uv run --with pyyaml --with plyvel-ci python scripts/app_switch.py personal
   ```

   This switches the CLI login (`cswap switch`), quits the app, carries the sessions, groups, routines, layout and settings, opens the app as the new account, and types a wake prompt into each session open in the source app panes, including idle or paused sessions. The dry run prints this list; use `--wake api-fix,docs,notes` to override it.
6. **Verify.** The session compares the target profile with the saved baseline:

   ```bash
   uv run --with pyyaml --with plyvel-ci python scripts/sidebar_state.py verify <baseline.yaml> personal
   ```

   Exit 0 means the represented local data matches; exit 1 reports differences and exit 2 means data-source issues make verification inconclusive. Remote Control/cloud rows and unsaved UI changes require a separate agent check; the local snapshot does not claim to cover them. The session also checks leftover "Offline" Remote Control rows after a restart.
7. **First time on an account only:** turn on Bypass permissions in Settings → Claude Code, if you use it. That consent is per account.

Next time, resume the same switch session, say "switch to work", and review the detected open-pane list.

The skill's [SKILL.md](SKILL.md) holds the full runbook for agents, including how to restore a sidebar by hand when a release changes the app's storage.


## Private settings

Optional settings and notes live outside the skill in `${XDG_CONFIG_HOME:-~/.config}/ostack/claude-switch/`. See [the configuration reference](SKILL.md#private-configuration).

## Read the sidebar without a screenshot

```bash
uv run --with pyyaml --with plyvel-ci python scripts/sidebar_state.py tree <account>
```

The text follows persisted section order and sorting preferences, includes full session IDs, and marks hidden/collapsed sections. `snapshot <account>` saves both YAML and text under the skill's private state directory. Both dry runs and real switches capture the source baseline and print its path.

Compare with `verify <baseline.yaml> <target>` after the app has settled. Verification checks session identity, archive state, pins, group membership/order, section settings, routines and deletion markers. Conflicting storage copies, missing records and a changing capture are reported rather than treated as a clean match. The text is a persisted-data view, not a claim to reproduce every visible remote or unsaved UI row. Screenshots are not required; use an agent's read-only UI inspection when those rows need reconciliation.

The text view nests helper sessions under their recorded parent and omits hidden side chats and empty sections from the main tree, while retaining their records in YAML. Compare recency-sorted rows by membership rather than position; compare explicit manual order when configured. Reconcile Remote Control rows with a read-only app inspection, recording names and connection status separately from local records.

## Logs, backups and limits

Real switches save output and errors in the private state directory as `switch-*.log`, including runs started from a terminal. Session records, routines, layout, groups and settings are backed up there before changes. Keep these files private: they can contain local paths and session titles.

The desktop storage format is not a public API. Review the dry run after app upgrades and verify the visible result after switching. Local settings are copied, but server-managed preferences (including the Cowork browser choice) can override them after launch; confirm these in Settings. Permission approvals and trusted folders stay with their existing account/profile. Cloud conversations, documents and artifacts are not migrated.

To restore files from a failed switch, quit all Claude desktop profiles first and use the printed backup paths. Restore only the affected profile files and session records, then reopen the source profile. Do not replace authentication stores.

## Development

```bash
uv run --with pyyaml --with plyvel-ci --with pytest python -m pytest tests
```
