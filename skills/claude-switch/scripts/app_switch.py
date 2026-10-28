#!/usr/bin/env python3
"""Switch Claude Code and the Claude app to another stored account, sessions included.

  app_switch.py <account>            switch (account: cswap slot, alias or email)
  app_switch.py <account> --dry-run  print the plan and save a baseline without changing app data
  app_switch.py <account> --from <a>  name the account left, when the record of the last switch is wrong
  app_switch.py <account> --wake <s,…>  wake exactly these sessions (titles or local ids), overriding the open-pane default

An account never signed in to the app is signed in first: every app window quits, only its
profile opens, and the switch waits for you to sign in.

Steps: `cswap switch <account>` for the CLI; quit every Claude app window (the
stock one and aswap's profiles); copy a session record into the account's session
folder when another account holds a newer copy, or when the account lacks one that
is live elsewhere (a `deleted_` marker there wins), and rewrite its archive index;
enable on the account every recurring routine enabled on another, and disable it there;
open the app as that account (the stock profile when it is signed in to it, else
`aswap desktop <slot>`, whose profile shares the session folder); print the sessions
still missing or different by id against each other account; port the groups
from the account left (`port_groups.py`, skipped with `--no-groups`) and give every
profile the same settings (`sync_settings.py`) before the launch; archive the Offline Remote Control rows after it; then type a wake prompt into
each session open in a pane before the switch (needs cua-driver). Sessions it cannot reach are
printed, or handed to `$CLAUDE_APP_WAKER_CMD` when set: a command that gets the
request as its last argument (for example one that messages a coordinating session).
Run with `uv run --with pyyaml --with plyvel-ci python app_switch.py` from a
terminal, not an app session, since quitting the app ends those. Needs cswap and
aswap (`uv tool install aswap`).
"""
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time

from private_config import CONFIG, STATE_DIR
from pathlib import Path

STOCK = Path.home() / "Library/Application Support/Claude"
SESSIONS = STOCK / "claude-code-sessions"
APP_BINARY = "/Applications/Claude.app/Contents/MacOS/Claude"
HERE = Path(__file__).resolve().parent
WAKER_CMD = os.environ.get("CLAUDE_APP_WAKER_CMD", CONFIG.get("waker_cmd", ""))
# The prompt each woken session gets; {email} is the account it is back on. Override with CLAUDE_APP_WAKE_TEXT.
WAKE_TEXT = os.environ.get("CLAUDE_APP_WAKE_TEXT") or CONFIG.get("wake_text") or (
    "app-switch: you are back on {email}; re-read your latest checkpoint or notes and the messages you had not "
    "answered yet, then resume where you left off. Check your existing helpers and delegated work against "
    "their current status; resume idle helpers with unfinished authorized work and confirm they respond. "
    "Do not duplicate running work or restart completed, archived, or user-paused work. Report anything "
    "you could not resume; a restored session ID alone does not prove work resumed.")


def accounts():
    seq = json.loads((Path.home() / ".claude-swap-backup/sequence.json").read_text())["accounts"]
    return {n: a for n, a in seq.items()}


def resolve(name):
    for n, a in accounts().items():
        if name in (n, a.get("alias"), a.get("email")):
            return n, a
    sys.exit(f"no stored account {name!r}; see `cswap list`")


def app_pids():
    out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True).stdout
    pids = []
    for line in out.splitlines():
        pid, _, command = line.strip().partition(" ")
        if command == APP_BINARY or command.startswith(APP_BINARY + " "):
            pids.append(int(pid))
    return pids


def quit_app():
    subprocess.run(["aswap", "desktop", "--quit", "--all"], capture_output=True)
    for pid in app_pids():
        os.kill(pid, signal.SIGTERM)  # the app saves its state on a normal quit
    for _ in range(60):
        if not app_pids():
            return
        time.sleep(1)
    sys.exit("the Claude app did not quit; quit it and run again")


def record(path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def copy_missing(scope, dry, src_scope=None):
    """Give the account the sidebar left on `src_scope`: each session record there wins as it is,
    archive state and title included, and one deleted there is archived here so the
    sidebar is exactly as it was left. A session the left account lacks comes
    from the account holding the newest copy, when it is live there; a `deleted_` marker here wins."""
    target = SESSIONS / scope
    deleted = {p.name[8:] for p in target.glob("deleted_*")}
    src = SESSIONS / src_scope if src_scope and src_scope != scope else None
    src_deleted = {p.name[8:] for p in src.glob("deleted_*")} if src else set()
    best, left = {}, {}
    for other in SESSIONS.glob("*/*"):
        if other == target or not other.is_dir():
            continue
        for f in other.glob("local_*.json"):
            r = record(f)
            if not r:
                continue
            if other == src:
                left[f.name] = (f, r)
            elif f.name not in best or r.get("lastActivityAt", 0) > best[f.name][1].get("lastActivityAt", 0):
                best[f.name] = (f, r)
    copied = []
    for name, (f, r) in sorted({**best, **left}.items()):
        mine = record(target / name)
        if name[6:-5] in deleted:
            continue
        if name in left:
            if mine == r or (not mine and r.get("isArchived")):
                continue
        elif (mine and mine.get("lastActivityAt", 0) >= r.get("lastActivityAt", 0)) or (not mine and r.get("isArchived")):
            continue
        copied.append(f"{r.get('title')} ({name[:-5]})")
        if not dry:
            shutil.copy2(f, target / name)
    for sid in sorted(src_deleted - deleted):  # deleted on the left account: archived here
        name = f"local_{sid}.json"
        mine = record(target / name)
        if mine and not mine.get("isArchived") and name not in left:
            copied.append(f"{mine.get('title')} (local_{sid}, archived: deleted on the account left)")
            if not dry:
                mine["isArchived"] = True
                (target / name).write_text(json.dumps(mine))
    if copied and not dry:
        idx_path = target / "archived-sessions.idx"
        idx = record(idx_path) or {"v": 1, "archived": []}
        idx["archived"] = sorted(p.stem for p in target.glob("local_*.json") if (record(p) or {}).get("isArchived"))
        idx_path.write_text(json.dumps(idx, separators=(",", ":")))
    return copied


RUN_STATE = ("lastRunAt", "lastScheduledFor")


def move_routines(scope, dry):
    """A recurring routine runs on one account only, so two app windows never run it
    twice: each one enabled on another account is enabled on the target and disabled
    where it was. The target takes its whole definition (title, schedule, prompt file,
    folder), so a rename travels with it, and keeps the newer
    of the two last runs."""
    path = SESSIONS / scope / "scheduled-tasks.json"
    mine = record(path) or {"scheduledTasks": []}
    by_id = {t["id"]: t for t in mine["scheduledTasks"]}
    moved = []
    for other in SESSIONS.glob("*/*/scheduled-tasks.json"):
        if other == path:
            continue
        theirs = record(other)
        if not theirs:
            continue
        changed = False
        for t in theirs.get("scheduledTasks", []):
            if not (t.get("cronExpression") and t.get("enabled")):
                continue
            moved.append(f"{t.get('displayName') or t['id']} (from {other.parent.relative_to(SESSIONS)})")
            had = by_id.get(t["id"])
            moved_task = {**t, "enabled": True}
            if had:
                for key in RUN_STATE:
                    if (had.get(key) or "") > (t.get(key) or ""):
                        moved_task[key] = had[key]
                had.clear()
                had.update(moved_task)
            else:
                by_id[t["id"]] = moved_task
                mine["scheduledTasks"].append(moved_task)
            t["enabled"] = False
            changed = True
        if changed and not dry:
            other.write_text(json.dumps(theirs, indent=2))
    if moved and not dry:
        path.write_text(json.dumps(mine, indent=2))
    return moved


LAYOUT_FILES = ("session-popouts.json", "window-state.json")
LAYOUT_KEYS = ("desktop-frame.paneStore.v1", "epitaxy.sidePaneStore.v1")  # Local Storage, prefix match
LS_PREFIX = b"_https://claude.ai\x00\x01"


def profile_dir(slot, acct):
    """The stock profile when it is signed in to the account, else aswap's `<slot>-<email>`."""
    if (record(STOCK / "config.json") or {}).get("lastKnownAccountUuid") == acct["uuid"]:
        return STOCK
    found = sorted((Path.home() / ".claude-swap-backup/desktop").glob(f"{slot}-*"))
    return found[0] if found else None


def first_sign_in(slot, acct, scope):
    """A new account's profile signs in once, with every other app window quit: the sign-in's
    `claude://` callback goes to an app already running (the callback can reach another
    window). Opens only the target's profile and waits for its session folder, which the app
    creates once you sign in and opens the Code tab."""
    quit_app()
    subprocess.run(["aswap", "desktop", slot], check=True)
    print(f"sign in as {acct['email']} in the window that opened, then open the Code tab; waiting up to 15 min", flush=True)
    for _ in range(180):
        if (SESSIONS / scope).is_dir():
            break
        time.sleep(5)
    else:
        sys.exit(f"no session folder for {acct['email']} after 15 min; sign in and run again")
    time.sleep(10)  # the app writes its first records
    quit_app()


def carry_layout(src, dst, dry):
    """Each profile keeps its own panes and pop-outs, so the switch carries the left
    profile's layout (main window panes, their side panes, pop-out windows, window
    frame) to the opened one. Both profiles are quit by now."""
    if not src or not dst or src == dst:
        return []
    keys = []
    import plyvel
    import tempfile
    copy = Path(tempfile.mkdtemp(prefix="claude-ls-")) / "leveldb"  # a dry run reads while the app runs
    shutil.copytree(src / "Local Storage/leveldb", copy, ignore=shutil.ignore_patterns("LOCK"))
    sdb = plyvel.DB(str(copy))
    rows = [(k, v) for k, v in sdb.iterator(prefix=LS_PREFIX)
            if k[len(LS_PREFIX):].decode("utf-8", "replace").startswith(LAYOUT_KEYS)]
    sdb.close()
    shutil.rmtree(copy.parent)
    keys += [k[len(LS_PREFIX):].decode("utf-8", "replace") for k, _ in rows]
    keys += [f for f in LAYOUT_FILES if (src / f).exists()]
    pane = (((record(src / "claude_desktop_config.json") or {}).get("preferences") or {})
            .get("epitaxyPrefs") or {}).get(LAYOUT_KEYS[0])
    if dry:
        return keys
    backup = STATE_DIR / f"layout-{time.strftime('%Y%m%d-%H%M%S')}"
    backup.mkdir(parents=True)
    shutil.copytree(dst / "Local Storage", backup / "Local Storage")
    for f in LAYOUT_FILES + ("claude_desktop_config.json",):
        if (dst / f).exists():
            shutil.copy2(dst / f, backup / f)
    ddb = plyvel.DB(str(dst / "Local Storage/leveldb"))
    for k, v in rows:
        ddb.put(k, v)
    ddb.close()
    for f in LAYOUT_FILES:  # no file on the left profile means none of its windows: drop the opened one's stale copy
        if (src / f).exists():
            shutil.copy2(src / f, dst / f)
        elif (dst / f).exists():
            (dst / f).unlink()
    config = record(dst / "claude_desktop_config.json")
    if pane and config:
        config.setdefault("preferences", {}).setdefault("epitaxyPrefs", {})[LAYOUT_KEYS[0]] = pane
        (dst / "claude_desktop_config.json").write_text(json.dumps(config, indent=2))
    print(f"layout backup: {backup}")
    return keys


KEEP_FLAGS = {"-n": 1, "--name": 1, "--remote-control": 1, "--channels": 1, "--permission-mode": 1,
              "--model": 1, "--effort": 1, "--dangerously-skip-permissions": 0}


def remote_control_clis():
    """Terminal Claude Code sessions in tmux with Remote Control: their sidebar row lives on
    the account they started under, so they restart under the new one."""
    found = []
    for meta in (Path.home() / ".claude/sessions").glob("*.json"):
        m = record(meta) or {}
        pid, pane = m.get("pid"), m.get("tmux")
        if not (pid and pane):  # the bridge id is gone once the credentials change
            continue
        argv = subprocess.run(["ps", "-ww", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout.split()
        if "--remote-control" not in argv:
            continue
        keep, i = ["claude"], 1
        while i < len(argv):
            n = KEEP_FLAGS.get(argv[i])
            if n is not None:
                keep += argv[i:i + 1 + n]
                i += 1 + n
            else:
                i += 1
        pane_id = pane.split(":", 1)[1].split(".")[-1]
        found.append({"pid": pid, "pane": pane_id, "name": m.get("name"),
                      "session": m["sessionId"], "argv": keep, "prefix": shell_prefix(pane_id)})
    return found


def shell_prefix(pane):
    """What the pane's start command ran before `claude` (an export such as the
    Telegram bot token), since the pane's login shell resets it from ~/.zshrc."""
    start = subprocess.run(["tmux", "display", "-p", "-t", pane, "#{pane_start_command}"],
                           capture_output=True, text=True).stdout.strip().replace("\\$", "$")  # tmux escapes `$`
    try:
        words = shlex.split(start)
        words = shlex.split(words[0]) if len(words) == 1 else words
    except ValueError:
        return ""
    script = words[-1] if len(words) > 1 and words[-2] in ("-c", "-ic", "-lc") else " ".join(words)
    head = script.split("claude ", 1)[0] if "claude " in script else ""
    return head if head.rstrip().endswith(";") else ""


def restart_remote_control(email, dry, clis=None):
    """Resume existing tmux CLIs under the new account."""
    clis = remote_control_clis() if clis is None else clis
    text = WAKE_TEXT.format(email=email)
    for c in clis:
        print(f"{'would restart' if dry else 'restarting'} {c['name']} in tmux pane {c['pane']} (session {c['session']})")
        if dry:
            continue
        os.kill(c["pid"], signal.SIGTERM)
        for _ in range(30):
            try:
                os.kill(c["pid"], 0)
                time.sleep(1)
            except ProcessLookupError:
                break
        else:
            raise RuntimeError(f"CLI in {c['pane']} did not exit; not sending a shell command")
        time.sleep(2)
        cmd = c["prefix"] + " ".join(shlex.quote(a) for a in c["argv"] + ["--resume", c["session"], text])
        subprocess.run(["tmux", "send-keys", "-l", "-t", c["pane"], cmd], check=True)
        subprocess.run(["tmux", "send-keys", "-t", c["pane"], "Enter"], check=True)
    return clis


def archive_offline_remote_rows(dry):
    """A Remote Control CLI restarted on the opened account adds a new row there, and its
    registration from an earlier run stays as an Offline row of the same name, so each
    such CLI shows twice in Recents. Archive the Offline ones (row
    `More options` -> Archive), so each shows once."""
    if dry:
        print("would archive the Offline Remote Control rows")
        return
    pids = app_pids()
    if not pids or subprocess.run(["which", "cua-driver"], capture_output=True).returncode:
        print("Offline Remote Control rows left: no app or no cua-driver")
        return
    time.sleep(20)  # the restarted CLIs connect
    session, archived = f"app-switch-rc-{os.getpid()}", []
    cua("start_session", {"session": session, "capture_scope": "window"})
    try:
        window = main_window(pids[0])
        for _ in range(20):
            els = sorted(cua("get_window_state", {"pid": pids[0], "window_id": window, "session": session,
                                                  "include_screenshot": False}).get("elements", []),
                         key=lambda e: e.get("element_index", 0))
            row = next((n for n, e in enumerate(els) if e.get("role") == "AXButton"
                        and (e.get("label") or "").endswith("· Offline")), None)
            if row is None:
                break
            more = next((e for e in els[row:] if e.get("role") == "AXPopUpButton"), None)
            cua("click", {"pid": pids[0], "element_token": more["element_token"], "session": session})
            time.sleep(1)
            menu = [e for e in cua("get_window_state", {"pid": pids[0], "window_id": window, "session": session,
                                                       "include_screenshot": False}).get("elements", [])
                    if (e.get("label") or e.get("title")) == "Archive"]
            if not menu:
                break
            cua("click", {"pid": pids[0], "element_token": menu[0]["element_token"], "session": session})
            archived.append(els[row]["label"].split(" ")[0])
            time.sleep(2)
    except Exception as error:
        print(f"archiving Offline Remote Control rows stopped: {error}")
    finally:
        cua("end_session", {"session": session})
    print(f"archived {len(archived)} Offline Remote Control row(s): {', '.join(archived)}")


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        sys.exit(__doc__)
    dry = "--dry-run" in argv
    if not dry and os.environ.get("CLAUDE_CODE_ENTRYPOINT") == "claude-desktop":
        # Quitting the app kills this app session's process group, and this script
        # with it, so run on in tmux.
        log = STATE_DIR / f"switch-{time.strftime('%Y%m%d-%H%M%S')}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        cmd = " ".join(shlex.quote(a) for a in [sys.executable, str(Path(__file__).resolve()), *argv])
        forwarded = [arg for key in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "CLAUDE_APP_WAKER_CMD", "CLAUDE_APP_WAKE_TEXT")
                     if key in os.environ for arg in ("-e", f"{key}={os.environ[key]}")]
        subprocess.run(["tmux", "new-session", "-d", *forwarded, "-s", f"app-switch-{os.getpid()}",
                        f"umask 077; env -u CLAUDE_CODE_ENTRYPOINT OSTACK_SWITCH_LOG={shlex.quote(str(log))} PYTHONUNBUFFERED=1 {cmd} > {shlex.quote(str(log))} 2>&1"], check=True)
        sys.exit(f"started in tmux, outside the app, which quits now; log: {log}")
    slot, acct = resolve(argv[0])
    scope = f"{acct['uuid']}/{acct['organizationUuid']}"
    chosen = wake_choice(argv[argv.index("--wake") + 1]) if "--wake" in argv else None
    dst = profile_dir(slot, acct)
    # The profile left: `--from`, else the account the last switch opened, else the other one used last
    # (cswap may already point at the target). Not by file times alone: a sign-in opens another profile
    # temporarily and make its timestamps newer.
    left = argv[argv.index("--from") + 1] if "--from" in argv else (record(OPENED) or {}).get("slot")
    if left and resolve(left)[0] == slot:
        print("The target is already the last opened desktop account; use --from if the desktop account differs.")
        return
    others = [d for n, a in accounts().items() if n != slot and (d := profile_dir(n, a)) and d != dst]
    if left and left != slot:
        src = profile_dir(*resolve(left))
    else:
        src = max(others, key=lambda d: (d / "Local Storage/leveldb").stat().st_mtime, default=None)
    awake = chosen if chosen is not None else remember_awake(src, dry)
    rc_clis = remote_control_clis()
    baseline_issues = False
    baseline = {}
    import sidebar_state
    source_scope = next((f"{a['uuid']}/{a['organizationUuid']}" for n, a in accounts().items()
                         if src and profile_dir(n, a) == src), None)
    if source_scope:
        _, baseline = sidebar_state.snapshot(source_scope)
        baseline_issues = any(v["problems"] for v in baseline.values())
    else:
        print("Baseline unavailable: source profile could not be resolved.")
        baseline_issues = True
    if baseline_issues and not dry:
        sys.exit("Source baseline is inconclusive; resolve its data issues before switching.")
    if not (SESSIONS / scope).is_dir():
        if dry:
            sys.exit(f"no session folder for {acct['email']} ({scope}); the switch signs it in first")
        first_sign_in(slot, acct, scope)
    dst = profile_dir(slot, acct)
    if not dry:
        subprocess.run(["cswap", "switch", slot], check=True)
        quit_app()
        backup_sessions()
    carried = carry_layout(src, dst, dry)
    print(f"{'would carry' if dry else 'carried'} {len(carried)} layout item(s) from {src} to {dst}")
    src_scope = next((f"{a['uuid']}/{a['organizationUuid']}" for n, a in accounts().items()
                      if src and profile_dir(n, a) == src), None)
    if src_scope and "--no-groups" not in argv:
        # One sidebar across accounts: the groups follow the switch.
        import port_groups
        out = port_groups.port(src, src_scope, dst, scope, SESSIONS, dry)
        print(f"would port the groups: {out}" if dry else f"ported the groups; backup {out}")
    # Every profile gets the same settings, the left one's winning a disagreement.
    import sync_settings
    order = [d for d in [src, dst] if d] + [d for n, a in accounts().items() if (d := profile_dir(n, a)) and d not in (src, dst)]
    ids = {profile_dir(n, a): (a["uuid"], a["organizationUuid"]) for n, a in accounts().items()}
    profiles = [(d, ids[d]) for d in order if (d / sync_settings.CONFIG).exists()]
    all_ids = [i for a in accounts().values() for i in (a["uuid"], a["organizationUuid"])]
    for d, changes in sync_settings.sync(profiles, dry, all_ids).items():
        print(f"{'would sync' if dry else 'synced'} {len(changes)} setting(s) on {d.name}")
    copied = copy_missing(scope, dry, src_scope)
    print(f"{'would copy' if dry else 'copied'} {len(copied)} session record(s) to {acct['email']}:")
    for c in copied:
        print(f"  {c}")
    moved = move_routines(scope, dry)
    print(f"{'would enable' if dry else 'enabled'} {len(moved)} routine(s) on {acct['email']}, disabled where they ran:")
    for m in moved:
        print(f"  {m}")
    if not dry:
        stock_account = (record(STOCK / "config.json") or {}).get("lastKnownAccountUuid")
        if stock_account == acct["uuid"]:
            subprocess.run(["open", "-a", "Claude"], check=True)
        else:
            subprocess.run(["aswap", "desktop", slot], check=True)
        OPENED.parent.mkdir(parents=True, exist_ok=True)
        OPENED.write_text(json.dumps({"slot": slot, "email": acct["email"], "at": time.time()}))
    # Groups live in each profile and on the server, so the check is by session only.
    sys.path.insert(0, str(HERE))
    import sidebar_state
    import yaml
    sides = sidebar_state.read_all(scope)
    target_side = sides.get(scope)
    source_side = baseline.get(source_scope) if source_scope else None
    if source_side is not None and target_side is not None:
        result = sidebar_state.diff(source_side, target_side)
        print(yaml.safe_dump({"source baseline -> target": result}, sort_keys=False, allow_unicode=True))
    wake_side = source_side if dry else target_side
    if wake_side is not None:
        wake(wake_side, acct["email"], dry, awake, primary_session(dst))
    restart_remote_control(acct["email"], dry, rc_clis)
    archive_offline_remote_rows(dry)
    if dry and baseline_issues:
        print("INCONCLUSIVE baseline: dry-run plan printed, but stored sidebar data has unresolved issues.")
        sys.exit(2)


def wake_list(side):
    """Live sessions by id, without routine runs (a run's title is its routine's name)."""
    return {i: v["title"] for i, v in sorted(side["sessions"].items())
            if not v["archived"] and not v.get("scheduled_task_id")}


def running_sessions():
    """App sessions with a process: their record's cliSessionId is on a `--resume=` argument.
    A session the opened app already restored needs no wake, and waking it costs the cap."""
    out = subprocess.run(["ps", "-axo", "command="], capture_output=True, text=True).stdout
    resumed = set(re.findall(r"--resume=([0-9a-f-]+)", out))
    return {f.stem for f in SESSIONS.glob("*/*/local_*.json")
            if (record(f) or {}).get("cliSessionId") in resumed}


AWAKE = STATE_DIR / "awake.json"
OPENED = STATE_DIR / "opened.json"  # the account the last switch opened


def wake_choice(names):
    """The sessions named for the wake, by local id or title; a title means its live session with the latest
    activity on any account. Exits before anything changes when a name matches no live session."""
    live = {}
    for f in SESSIONS.glob("*/*/local_*.json"):
        r = record(f) or {}
        if not r.get("isArchived") and r.get("lastActivityAt", 0) >= live.get(f.stem, {}).get("lastActivityAt", 0):
            live[f.stem] = r
    chosen = set()
    for name in filter(None, (n.strip() for n in names.split(","))):
        ids = [i for i in live if i == name or i == f"local_{name}"]
        if not ids:
            titled = [i for i, r in live.items() if r.get("title") == name]
            ids = [max(titled, key=lambda i: live[i].get("lastActivityAt", 0))] if titled else []
        if not ids:
            sys.exit(f"--wake: no live session named {name}")
        chosen.add(ids[0])
    return chosen


def pane_ids(store):
    """Read the known main-window layout, never infer open panes from activity."""
    state = store.get("state") if isinstance(store, dict) else None
    if not isinstance(state, dict) or "extraPanesByMode" not in state:
        raise ValueError("open-pane layout missing or unsupported; inspect the app and pass --wake")
    refs = [state.get("lastPrimaryCodeSession")] + [
        p.get("ref") for p in state["extraPanesByMode"].get("code", [])]
    ids = set()
    for ref in refs:
        if ref is None:
            continue
        sid = ref.get("id", "")
        if not sid.startswith("local_"):
            raise ValueError("non-local or unidentified open pane; inspect the app and pass --wake")
        ids.add(sid)
    return ids


def remember_awake(profile, dry):
    """Capture all source-profile open panes, including paused and idle sessions."""
    if profile is None:
        sys.exit("source profile unknown; specify --from and inspect open panes")
    prefs = (record(profile / "claude_desktop_config.json") or {}).get("preferences", {}).get("epitaxyPrefs", {})
    try:
        awake = pane_ids(prefs.get(LAYOUT_KEYS[0]))
        import local_storage
        db = local_storage.open_copy(profile)
        try:
            raw = db.get(LS_PREFIX + LAYOUT_KEYS[0].encode())
            if raw and pane_ids(json.loads(local_storage.decode(raw))) != awake:
                raise ValueError("open-pane copies disagree; inspect the app and pass --wake")
        finally:
            db.close()
        popouts = record(profile / "session-popouts.json")
        if popouts:
            raise ValueError("pop-out windows need app inspection; include them explicitly with --wake")
    except (ValueError, KeyError, TypeError) as error:
        sys.exit(str(error))
    print(f"Detected {len(awake)} open app pane session(s), including idle/paused:")
    for sid in sorted(awake):
        recs = [record(f) or {} for f in SESSIONS.glob(f"*/*/{sid}.json")]
        rec = max(recs, key=lambda r: r.get("lastActivityAt", 0), default={})
        if not rec:
            sys.exit(f"open pane {sid} has no record; inspect the app and pass --wake")
        print(f"  {rec.get('title', '(untitled)')} ({sid})")
    if not dry:
        AWAKE.parent.mkdir(parents=True, exist_ok=True)
        AWAKE.write_text(json.dumps({"at": time.time(), "profile": str(profile), "sessions": sorted(awake)}))
    return awake


def cua(tool, args):
    out = subprocess.run(["cua-driver", tool, json.dumps(args)], capture_output=True, text=True)
    try:
        data = json.loads(out.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"cua-driver {tool}: {(out.stderr or out.stdout).strip()[:200]}")
    return data.get("structuredContent", data)


def cua_daemon():
    """Whether the cua-driver daemon runs, started here when it does not (it is not always up)."""
    if subprocess.run(["which", "cua-driver"], capture_output=True).returncode:
        print("cua-driver is not installed")
        return False
    if not subprocess.run(["cua-driver", "status"], capture_output=True).returncode:
        return True
    subprocess.Popen(["cua-driver", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    for _ in range(20):
        time.sleep(0.5)
        if not subprocess.run(["cua-driver", "status"], capture_output=True).returncode:
            print("started the cua-driver daemon")
            return True
    print("the cua-driver daemon did not start")
    return False


def main_window(pid):
    """The app's main window: the titled one that is not a session pop-out (the biggest)."""
    wins = [w for w in cua("list_windows", {"pid": pid}).get("windows", [])
            if w.get("title") and w.get("is_on_screen")]
    return max(wins, key=lambda w: w["bounds"]["width"] * w["bounds"]["height"])["window_id"] if wins else None


def prompt_and_send(pid, window, title, session):
    """In a window's tree each pane starts with `<title>, rename session`; that pane's
    Prompt and Send follow it, before the next pane's header."""
    # The default walk (2 000 nodes, depth 25) stops before the last panes of a split view.
    els = cua("get_window_state", {"pid": pid, "window_id": window, "session": session, "include_screenshot": False,
                                   "max_elements": 20000, "max_depth": 60}).get("elements", [])
    inside, prompt = False, None
    for e in sorted(els, key=lambda e: e.get("element_index", 0)):
        label = e.get("label") or e.get("title") or ""
        if label.endswith(", rename session"):
            inside = label == f"{title}, rename session"
            continue
        if inside and e.get("role") == "AXTextArea" and label == "Prompt":
            prompt = e["element_token"]
        elif inside and prompt and e.get("role") == "AXButton" and label in ("Send", "Send message"):
            return prompt, e["element_token"]
    return prompt, None


def wake_here(sessions, text, primary):
    """Wake each session from this one, as you would: open it with the app's own link
    (`claude://code/continue?session=<local id>`, into the main window's left pane), type
    the wake into that pane's prompt and send it; the session running the switch does all
    of it. Returns the ones not
    started; the left pane is put back on `primary`."""
    pids = app_pids()
    if not pids or not cua_daemon():
        return dict(sessions)
    session = f"app-switch-{os.getpid()}"
    cua("start_session", {"session": session, "capture_scope": "window"})
    failed = {}
    for sid, title in sessions.items():
        try:
            subprocess.run(["open", f"claude://code/continue?session={sid}"], check=True)
            window, prompt = None, None
            for _ in range(15):  # the pane loads the session, and opening it starts its process
                time.sleep(1)
                window = window or main_window(pids[0])
                prompt, _send = prompt_and_send(pids[0], window, title, session) if window else (None, None)
                if prompt:
                    break
            else:
                raise RuntimeError("it did not open in the main window")
            cua("set_value", {"pid": pids[0], "element_token": prompt, "value": text, "session": session})
            prompt, send = prompt_and_send(pids[0], window, title, session)
            if not send:
                raise RuntimeError("no Send button after typing the wake")
            cua("click", {"pid": pids[0], "element_token": send, "session": session})
            for _ in range(20):
                if sid in running_sessions():
                    break
                time.sleep(1)
            else:
                raise RuntimeError("sent, but no process after 20 s")
            print(f"  woke {title}")
        except Exception as error:  # one session failing leaves the rest to try
            print(f"  {title}: {error}")
            failed[sid] = title
    if primary:
        subprocess.run(["open", f"claude://code/continue?session={primary}"])
    cua("end_session", {"session": session})
    return failed


def primary_session(profile):
    """The session the left pane showed before the switch (the carried pane store)."""
    prefs = (((record(profile / "claude_desktop_config.json") or {}).get("preferences") or {})
             .get("epitaxyPrefs") or {}) if profile else {}
    state = (prefs.get("desktop-frame.paneStore.v1") or {}).get("state") or {}
    return (state.get("lastPrimaryCodeSession") or {}).get("id")


def wake(side, email, dry, awake=None, primary=None):
    """An app session has no process after the quit, so it does nothing until it gets a prompt.
    This session types the wake into each one (`wake_here`); the ones it could not reach are
    printed, and handed to `CLAUDE_APP_WAKER_CMD` when it is set."""
    sessions = wake_list(side)
    if awake is not None:
        missing = awake - sessions.keys()
        if missing:
            sys.exit(f"selected open panes missing from live sessions: {sorted(missing)}")
    if awake is not None:
        idle = len(sessions) - len([i for i in sessions if i in awake])
        sessions = {i: t for i, t in sessions.items() if i in awake}
        print(f"leaving {idle} session(s) asleep")
    else:
        print("no record of which sessions ran before the switch; waking every live one")
    text = WAKE_TEXT.format(email=email)
    print(f"{'would wake' if dry else 'waking'} {len(sessions)} session(s) from this session")
    for sid, title in sessions.items():
        print(f"  {title} ({sid})")
    if dry:
        return
    # The opened app restores its panes and pop-outs with a process, which is all an idle
    # one needs; a turn the quit cut short still needs the prompt, so none is skipped.
    time.sleep(15)
    sessions = wake_here(sessions, text, primary)
    if not sessions:
        return
    ids = "\n".join(f"- {i} ({t})" for i, t in sessions.items())
    print(f"{len(sessions)} not woken from here; open each and send the wake prompt:\n{ids}")
    if not WAKER_CMD:
        return
    out = subprocess.run(shlex.split(WAKER_CMD) + [f"Wake these app sessions after the account switch with "
                    f"SendMessage to each id, text: '{text}'. When your own id is listed, do what that text says yourself after the others. The app stops your messages to "
                    f"other app sessions after 10, so wake at most 10 and report the rest back. "
                    f"Sessions:\n{ids}"], capture_output=True, text=True)
    print(out.stdout.strip() or out.stderr.strip())


def backup_sessions():
    """Back up records, archive/deletion markers and routines before changing them."""
    backup = STATE_DIR / f"sessions-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns()}"
    backup.mkdir(parents=True, mode=0o700)
    for scope in SESSIONS.glob("*/*"):
        if not scope.is_dir():
            continue
        dest = backup / scope.relative_to(SESSIONS)
        dest.mkdir(parents=True)
        for file in scope.iterdir():
            if file.is_file() and (file.suffix == ".json" or file.name == "archived-sessions.idx" or file.name.startswith("deleted_")):
                shutil.copy2(file, dest / file.name)
    print(f"session backup: {backup}")
    return backup


def run_cli(argv):
    """Record terminal and detached switches, including subprocess output and errors."""
    if not argv or argv[0] in ("-h", "--help") or "--dry-run" in argv or os.environ.get("OSTACK_SWITCH_LOG") or os.environ.get("CLAUDE_CODE_ENTRYPOINT") == "claude-desktop":
        return main(argv)
    path = STATE_DIR / f"switch-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns()}.log"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    print(f"Switch log: {path}", flush=True)
    env = {**os.environ, "OSTACK_SWITCH_LOG": str(path), "PYTHONUNBUFFERED": "1"}
    with open(path, "x", opener=lambda p, flags: os.open(p, flags, 0o600)) as log:
        with subprocess.Popen([sys.executable, str(Path(__file__).resolve()), *argv],
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as child:
            for line in child.stdout:
                log.write(line)
                log.flush()
                print(line, end="", flush=True)
            code = child.wait()
            log.write(f"\nExit status: {code}\n")
    return code


if __name__ == "__main__":
    sys.exit(run_cli(sys.argv[1:]))
