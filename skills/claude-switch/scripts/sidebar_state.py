#!/usr/bin/env python3
"""Read the Claude app's Code sidebar by session id, from every place it is stored.

Read-only; works while the app runs (it writes its state on quit, so read after a
quit for a settled view). Point it at an app profile with CLAUDE_APP_SUPPORT.
  [summary] [scope]     each scope: live titles, counts, groups in sidebar order,
                        and `problems` (unparsable records, group members with no
                        record, archive index or group copies that disagree)
  sessions [scope]      every session by id: title, cwd, archived, lastActivityAt,
                        bridge, group, pinned; plus groups, routines and backlog
  tree <scope>         readable persisted sidebar, with full IDs and data issues
  snapshot <scope>     save YAML baseline and readable text under the skill state directory
  verify <file> <scope> compare saved baseline with a target profile; exit 0 match,
                        1 differences, 2 inconclusive data sources
  diff <a> <b>          sessions missing or different between two scopes, by id;
                        exit 1 when anything differs
A scope is a cswap alias (`work`, `personal`), or a prefix of `<account>/<org>`; in `diff` it may also
be `<file.yaml>:<scope>`, a file `sessions` wrote (an inventory).
Needs `uv run --with pyyaml --with plyvel-ci python sidebar_state.py ...`.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from local_storage import APP_SUPPORT, PREFIX, decode, open_copy

def cswap_aliases():
    """cswap's account aliases (`work`, `personal`) as scopes, so a new account needs no edit here."""
    try:
        seq = json.loads((Path.home() / ".claude-swap-backup/sequence.json").read_text())["accounts"]
    except (OSError, json.JSONDecodeError, KeyError):
        return {}
    return {a["alias"]: f"{a['uuid']}/{a['organizationUuid']}" for a in seq.values()
            if a.get("alias") and a.get("uuid") and a.get("organizationUuid")}


ALIASES = cswap_aliases()
COMPARED = ("title", "cwd", "archived", "group", "pinned", "parent", "side_chat", "scheduled_task_id")


def iso(ms):
    if not isinstance(ms, (int, float)):
        return ms
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_app(app=APP_SUPPORT):
    """The config's epitaxyPrefs and Local Storage's dframe-store state and pins."""
    config = json.loads((app / "claude_desktop_config.json").read_text())
    prefs = config.get("preferences", {}).get("epitaxyPrefs", {})
    store, ls_pins, ls_error = {}, None, None
    separate = {"groups": {}, "sections": {}}
    try:
        db = open_copy(app)
        raw = db.get(PREFIX + b"dframe-store")
        pins = db.get(PREFIX + b"LSS-persisted.starred-local-code-sessions")
        group_raw = db.get(PREFIX + b"LSS-persisted.dframe-group-scopes")
        section_raw = db.get(PREFIX + b"LSS-persisted.dframe-code-sections")
        separate = {"groups": json.loads(decode(group_raw)).get("value", {}) if group_raw else {},
                    "sections": json.loads(decode(section_raw)).get("value", {}) if section_raw else {}}
        db.close()
        store = json.loads(decode(raw)).get("state", {}) if raw else {}
        ls_pins = json.loads(decode(pins)) if pins else None
    except Exception as e:  # a missing or locked store is reported, not fatal
        ls_error = str(e)
    account = ""
    try:
        account = json.loads((app / "config.json").read_text()).get("lastKnownAccountUuid", "")
    except (OSError, json.JSONDecodeError):
        pass
    return {"prefs": prefs, "store": store, "ls_pins": ls_pins, "ls_error": ls_error, "account": account, "separate": separate}


def group_copy(groups, sections):
    """{session id: group name} and group names in sidebar order, from one copy."""
    if not groups:
        return None
    names = {g["id"]: g["name"] for g in groups.get("groups", [])}
    members = {k[5:]: names.get(v, v) for k, v in groups.get("assignments", {}).items()
               if k.startswith("code:")}
    order = [s["name"] for s in sorted((sections or {}).get("sections", []), key=lambda s: s.get("order", 0))
             if s.get("kind") == "manual"]
    order = order or [g["name"] for g in groups.get("groups", [])]
    sections_list = []
    for section in sorted((sections or {}).get("sections", []), key=lambda x: x.get("order", 0)):
        item = {k: section.get(k) for k in ("kind", "name", "sortBy", "ascending", "shown", "collapsed", "show", "groupBy") if k in section}
        item["name"] = names.get(section.get("id"), section.get("name")) or section.get("kind", "Unknown")
        sections_list.append(item)
    raw_order = groups.get("order", {})
    member_order = {names.get(k, k): [i.removeprefix("code:") for i in v] for k, v in raw_order.items()} if isinstance(raw_order, dict) else {}
    return {"members": members, "order": order, "sections": sections_list, "member_order": member_order}


def read_scope(folder, scope, app_state):
    prefs, store = app_state["prefs"], app_state["store"]
    problems = []
    records = {}
    for f in sorted(folder.glob("local_*.json")):
        try:
            records[f.stem] = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError) as e:
            problems.append({"unparsable": f.name, "error": str(e)[:120]})
    idx = set()
    idx_file = folder / "archived-sessions.idx"
    if idx_file.exists():
        try:
            idx = set(json.loads(idx_file.read_text()).get("archived", []))
        except (OSError, json.JSONDecodeError) as e:
            problems.append({"unparsable": idx_file.name, "error": str(e)[:120]})
    copies = {
        "config": group_copy(prefs.get("dframe-group-scopes", {}).get(scope),
                             prefs.get("dframe-code-sections", {}).get(scope)),
        "local_storage": group_copy(store.get("customGroupsByScope", {}).get(scope),
                                    store.get("codeSidebarByScope", {}).get(scope)),
    }
    separate = app_state.get("separate", {})
    copies["local_storage_keys"] = group_copy(separate.get("groups", {}).get(scope), separate.get("sections", {}).get(scope))
    found = {k: v for k, v in copies.items() if v is not None}
    main = next(iter(found.values()), {"members": {}, "order": []})
    for label, candidate in found.items():
        if candidate != main:
            problems.append({"group_copies_disagree": label, "reference": next(iter(found))})
    if not found:
        problems.append({"sidebar_structure_missing": True})
    if len(found) != 3:
        problems.append({"sidebar_copies_missing": sorted(set(copies) - set(found))})
    for section in main.get("sections", []):
        if section.get("kind") not in ("manual", "pinned", "routines", "sessions"):
            problems.append({"unsupported_section": section})
        if section.get("sortBy", "recency") not in ("recency", "manual", "title", "name", "alphabetical"):
            problems.append({"unsupported_sort": section})
        if section.get("groupBy", "none") != "none":
            problems.append({"unsupported_grouping": section})
    pins = set(prefs.get("starred-local-code-sessions", []))
    if app_state["ls_pins"] is not None:
        pins |= set(app_state["ls_pins"]) if isinstance(app_state["ls_pins"], list) else set()
    if isinstance(app_state["ls_pins"], list) and set(prefs.get("starred-local-code-sessions", [])) != set(app_state["ls_pins"]):
        problems.append({"pin_copies_disagree": True})
    sessions = {}
    for sid, r in records.items():
        archived = bool(r.get("isArchived"))
        if archived != (sid in idx) and idx_file.exists():
            problems.append({"archive_index_disagrees": sid, "record": archived, "index": sid in idx})
        sessions[sid] = {
            "title": r.get("title"),
            "cwd": r.get("cwd"),
            "archived": archived or sid in idx,
            "lastActivityAt": iso(r.get("lastActivityAt")),
            "bridge": bool(r.get("bridgeSessionIds")),
            "group": main["members"].get(sid),
            "pinned": sid in pins or bool(r.get("isStarred")),
            "scheduled_task_id": r.get("scheduledTaskId"),
            "parent": (r.get("spawnedFrom") or {}).get("sessionId"),
            "side_chat": bool((r.get("spawnedFrom") or {}).get("btw")),
        }
    for sid, name in sorted(main["members"].items()):
        if sid not in records:
            problems.append({"member_without_record": sid, "group": name,
                             "deleted": (folder / f"deleted_{sid[6:]}").exists()})
    routines = []
    tasks = folder / "scheduled-tasks.json"
    if tasks.exists():
        try:
            routines = [{k: t.get(k) for k in ("id", "displayName", "cronExpression", "enabled", "filePath")
                         if t.get(k) is not None}
                        for t in json.loads(tasks.read_text()).get("scheduledTasks", [])]
        except (OSError, json.JSONDecodeError) as e:
            problems.append({"unparsable": tasks.name, "error": str(e)[:120]})
    backlog = 0
    for f in folder.glob("backlog/*.json"):
        try:
            backlog += len(json.loads(f.read_text()).get("items", []))
        except (OSError, json.JSONDecodeError) as e:
            problems.append({"unparsable": f"backlog/{f.name}", "error": str(e)[:120]})
    return {
        "signed_in": bool(app_state["account"]) and scope.startswith(app_state["account"]) and bool(records),
        "groups": main["order"],
        "sections": main.get("sections", []),
        "member_order": main.get("member_order", {}),
        "sidebar_copies": copies,
        "sessions": sessions,
        "deleted": sorted(p.name[8:] for p in folder.glob("deleted_*")),
        "routines": routines,
        "backlog_items": backlog,
        "problems": problems,
    }


def scopes(app=APP_SUPPORT):
    root = app / "claude-code-sessions"
    return {f"{d.parent.name}/{d.name}": d for d in sorted(root.glob("*/*")) if d.is_dir()}


def pick(all_scopes, arg):
    arg = ALIASES.get(arg, arg)
    hits = [s for s in all_scopes if s.startswith(arg) or s.split("/")[1].startswith(arg)]
    if len(hits) != 1:
        sys.exit(f"scope {arg!r} matches {len(hits)} of: {', '.join(all_scopes) or 'none'}")
    return hits[0]


def profile_for(arg):
    if os.environ.get("CLAUDE_APP_SUPPORT"):
        return APP_SUPPORT
    registry = json.loads((Path.home() / ".claude-swap-backup/sequence.json").read_text())
    stock = Path.home() / "Library/Application Support/Claude"
    stock_id = json.loads((stock / "config.json").read_text()).get("lastKnownAccountUuid")
    matches = [(n, a) for n, a in registry["accounts"].items()
               if arg in (n, a.get("alias"), a.get("email"), f"{a.get('uuid')}/{a.get('organizationUuid')}")]
    if not matches:
        return APP_SUPPORT
    n, account = matches[0]
    if stock_id == account.get("uuid"):
        return stock
    paths = sorted((Path.home() / ".claude-swap-backup/desktop").glob(f"{n}-*"))
    if len(paths) != 1:
        raise ValueError(f"Expected one desktop profile for {arg}; found {len(paths)}")
    return paths[0]


def read_all(arg=None, app=None):
    app = app or (profile_for(arg) if arg else APP_SUPPORT)
    found = scopes(app)
    if arg:
        found = {s: found[s] for s in [pick(found, arg)]}
    state = load_app(app)
    out = {s: read_scope(d, s, state) for s, d in found.items()}
    for scope, v in out.items():
        stale = []
        for p in v["problems"]:
            if "member_without_record" in p:
                sid = p["member_without_record"]
                paths = [(o, d / f"{sid}.json") for o, d in scopes(app).items()
                         if o != scope and (d / f"{sid}.json").exists()]
                p["record_in"] = [o for o, _ in paths]
                archived = []
                for _, path in paths:
                    try:
                        archived.append(bool(json.loads(path.read_text()).get("isArchived")))
                    except (OSError, ValueError):
                        archived.append(False)
                if p.get("deleted") or (archived and all(archived)):
                    stale.append(p)
        # An absent source record with only archived copies elsewhere is not a
        # visible sidebar row. Keep the evidence, but do not call it uncertainty.
        v["ignored_stale_members"] = stale
        v["problems"] = [p for p in v["problems"] if p not in stale]
    if state["ls_error"]:
        for v in out.values():
            v["problems"].append({"local_storage_not_read": state["ls_error"]})
    for value in out.values():
        value["profile"] = str(app)
        value["captured_at"] = datetime.now(timezone.utc).isoformat()
        value["coverage"] = "Persisted local Code sessions and sidebar configuration; Remote Control/cloud rows and unsaved UI state are not verified."
    return out


def summary(state):
    out = {}
    for scope, s in state.items():
        live = {i: v for i, v in s["sessions"].items() if not v["archived"]}
        groups = {g: [f"{v['title']} ({i[6:14]})" for i, v in sorted(live.items(), key=lambda x: x[1]["title"] or "")
                      if v["group"] == g] for g in s["groups"]}
        out[scope] = {
            "signed_in": s["signed_in"],
            "live": len(live),
            "archived": len(s["sessions"]) - len(live),
            "deleted_markers": len(s["deleted"]),
            "ungrouped_live": sorted(f"{v['title']} ({i[6:14]})" for i, v in live.items() if not v["group"]),
            "groups": groups,
            "routines": [r.get("displayName") for r in s["routines"]],
            "problems": s["problems"],
        }
    return out


def side(arg):
    if ":" in arg and Path(arg.rsplit(":", 1)[0]).exists():
        path, scope = arg.rsplit(":", 1)
        data = yaml.safe_load(Path(path).read_text())
        data = data.get("scopes", data)
        return data[pick(data, scope)]
    return next(iter(read_all(arg).values()))


def diff(a, b):
    """Compare live membership and shared records; absent archived history is expected."""
    sa, sb = a["sessions"], b["sessions"]
    out = {
        "only_a": {i: sa[i]["title"] for i in sorted(set(sa) - set(sb)) if not sa[i].get("archived")},
        "only_b": {i: sb[i]["title"] for i in sorted(set(sb) - set(sa)) if not sb[i].get("archived")},
        "different": {},
    }
    for i in sorted(set(sa) & set(sb)):
        if sa[i].get("archived") and sb[i].get("archived"):
            continue
        d = {k: [sa[i].get(k), sb[i].get(k)] for k in COMPARED if sa[i].get(k) != sb[i].get(k)}
        if d:
            out["different"][i] = {"title": sa[i]["title"], **d}
    if a.get("groups") != b.get("groups"):
        out["group_order"] = {"a": a.get("groups"), "b": b.get("groups")}
    for key in ("sections", "routines", "deleted"):
        if a.get(key) != b.get(key):
            out[key] = {"a": a.get(key), "b": b.get(key)}
    manual = {section.get("name") for side in (a, b) for section in side.get("sections", [])
              if section.get("sortBy") == "manual"}
    order_a = {name: a.get("member_order", {}).get(name, []) for name in manual}
    order_b = {name: b.get("member_order", {}).get(name, []) for name in manual}
    if order_a != order_b:
        out["member_order"] = {"a": order_a, "b": order_b}
    return out


def dump(data):
    print(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120), end="")


def render_tree(state, label="sidebar"):
    """Render stored sections without silently dropping hidden, archived or unknown rows."""
    lines = [f"Sidebar: {label}"]
    for scope, side_state in state.items():
        lines += [f"Profile: {side_state.get('profile', 'unknown')}",
                  f"Read: {side_state.get('captured_at', 'unknown')}",
                  side_state.get("coverage", "Local persisted data only."), ""]
        sessions = side_state["sessions"]
        live = {i: v for i, v in sessions.items() if not v["archived"] and not v.get("scheduled_task_id")}
        sections = side_state.get("sections") or ([{"kind": "manual", "name": g} for g in side_state["groups"]] + [{"kind": "sessions", "name": "Recents"}])
        accounted = set()
        omitted = []
        nested = {sid: row["parent"] for sid, row in live.items() if row.get("parent") in live and not row.get("side_chat")}
        def emit_row(sid, row, depth=1, ancestors=()):
            if sid in ancestors:
                lines.append("  " * depth + f"! cyclic parent relationship [{sid}]")
                return
            accounted.add(sid)
            title = " ".join(str(row.get("title") or "(untitled)").splitlines())
            lines.append("  " * depth + f"- {title}  [{sid}]" + (" *" if row.get("pinned") else ""))
            children = [(i, v) for i, v in live.items() if nested.get(i) == sid]
            children.sort(key=lambda item: (str(item[1].get("lastActivityAt") or ""), item[0]), reverse=True)
            for child_id, child in children:
                emit_row(child_id, child, depth + 1, (*ancestors, sid))
        for section in sections:
            kind = section.get("kind")
            name = {"pinned": "Pinned", "routines": "Routines", "sessions": "Recents"}.get(kind, section.get("name", "Unknown"))
            flags = [word for condition, word in ((section.get("shown") is False, "hidden"), (section.get("collapsed"), "collapsed")) if condition]
            if section.get("shown") is False:
                omitted.append(f"{name} (hidden)")
                continue
            if kind == "routines":
                lines.append(name + (f" [{', '.join(flags)}]" if flags else ""))
                for routine in side_state["routines"]:
                    lines.append(f"  - {routine.get('displayName', '(untitled)')} [{routine.get('id')}] {'enabled' if routine.get('enabled') else 'disabled'}; {routine.get('cronExpression', '')}")
                continue
            if kind == "manual":
                rows = [(i, v) for i, v in live.items() if v.get("group") == name]
            elif kind == "pinned":
                rows = [(i, v) for i, v in live.items() if v.get("pinned")]
            elif kind == "sessions":
                rows = [(i, v) for i, v in live.items() if not v.get("group") and not v.get("pinned")]
            else:
                lines.append(f"{name} [unsupported section kind: {kind}]")
                continue
            rows = [(i, v) for i, v in rows if i not in nested and
                    (not v.get("side_chat") or section.get("show", {}).get("sideChats", False))]
            if not rows and kind != "sessions" and not section.get("show", {}).get("emptyGroups", False):
                omitted.append(f"{name} (empty)")
                continue
            sort = section.get("sortBy", "recency")
            if sort == "recency":
                rows.sort(key=lambda row: (str(row[1].get("lastActivityAt") or ""), row[0]), reverse=not section.get("ascending", False))
            elif sort in ("title", "name", "alphabetical"):
                rows.sort(key=lambda row: ((row[1].get("title") or "").casefold(), row[0]), reverse=not section.get("ascending", True))
            elif sort == "manual":
                order = side_state.get("member_order", {}).get(name, [])
                rows.sort(key=lambda row: (order.index(row[0]) if row[0] in order else len(order), row[0]))
            else:
                flags.append(f"unsupported sort: {sort}; listed by ID")
                rows.sort()
            lines.append(f"{name} ({len(rows)})" + (f" [{', '.join(flags)}]" if flags else ""))
            for sid, row in rows:
                emit_row(sid, row)
        leftovers = sorted(sid for sid in set(live) - accounted if not live[sid].get("side_chat"))
        hidden_side_chats = sum(bool(v.get("side_chat")) for i, v in live.items() if i not in accounted)
        if omitted or hidden_side_chats:
            lines.append("Not shown: " + "; ".join(omitted + ([f"{hidden_side_chats} side chat(s); retained in YAML"] if hidden_side_chats else [])))
        if leftovers:
            lines.append("Unmapped live records (not verified in sidebar)")
            for sid in leftovers:
                lines.append(f"  - {live[sid].get('title')} [{sid}]")
        lines += ["", f"Totals: {len(live)} live chats, {sum(bool(v.get('scheduled_task_id')) and not v['archived'] for v in sessions.values())} live routine-run records (in YAML), {sum(v['archived'] for v in sessions.values())} archived, {len(side_state.get('deleted', []))} deletion markers."]
        stale = side_state.get("ignored_stale_members", [])
        if stale:
            lines.append(f"Ignored {len(stale)} stale group reference(s): deleted here or only archived records elsewhere; not visible and not restored.")
        problems = side_state.get("problems", [])
        lines.append(f"Data checks: {len(problems)} issue(s)." if problems else "Data checks: stored copies agree; UI/remote coverage remains unverified.")
        for problem in problems:
            lines.append("  ! " + json.dumps(problem, ensure_ascii=False))
    return "\n".join(lines)


def snapshot(arg):
    from private_config import STATE_DIR
    state = read_all(arg)
    second = read_all(arg)
    for scope, value in state.items():
        a = {k: v for k, v in value.items() if k != "captured_at"}
        b = {k: v for k, v in second.get(scope, {}).items() if k != "captured_at"}
        if a != b:
            value["problems"].append({"changed_during_capture": True})
    root = STATE_DIR / "snapshots"
    root.mkdir(parents=True, exist_ok=True)
    path = root / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S-%f") + ".yaml")
    path.write_text(yaml.safe_dump({"schema_version": 1, "scopes": state}, sort_keys=False, allow_unicode=True))
    path.chmod(0o600)
    text = render_tree(state, arg)
    path.with_suffix(".txt").write_text(text + "\n")
    path.with_suffix(".txt").chmod(0o600)
    print(text)
    print(f"\nBaseline: {path}")
    print(f"Compare after switching: sidebar_state.py verify {path} <target>")
    return path, state


def verify(path, target):
    baseline = yaml.safe_load(Path(path).read_text())["scopes"]
    if len(baseline) != 1:
        raise ValueError("Verification requires a single-scope snapshot")
    before = next(iter(baseline.values()))
    current = next(iter(read_all(target).values()))
    again = next(iter(read_all(target).values()))
    if {k: v for k, v in current.items() if k != "captured_at"} != {k: v for k, v in again.items() if k != "captured_at"}:
        current["problems"].append({"changed_during_verification": True})
    result = diff(before, current)
    issues = {"baseline": before.get("problems", []), "current": current.get("problems", [])}
    changed = any(result.values())
    dump({"baseline": path, "target": target, "differences": result, "data_issues": issues,
          "coverage": current.get("coverage")})
    if any(issues.values()):
        print("INCONCLUSIVE: resolve data-source issues before treating this as verified.")
        return 2
    print("DIFFERENT: inspect the changes above." if changed else "MATCH: represented local data matches; remote rows and unsaved UI state are not verified.")
    return 1 if changed else 0


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = argv[0] if argv[0] in ("summary", "sessions", "diff", "tree", "snapshot", "verify") else "summary"
    rest = argv[1:] if argv[0] == cmd else argv
    if cmd == "verify":
        if len(rest) != 2:
            sys.exit(__doc__)
        return verify(*rest)
    if cmd == "snapshot":
        if len(rest) != 1:
            sys.exit(__doc__)
        _, state = snapshot(rest[0])
        return 2 if any(v["problems"] for v in state.values()) else 0
    if cmd == "diff":
        if len(rest) != 2:
            sys.exit(__doc__)
        result = diff(side(rest[0]), side(rest[1]))
        dump({"a": rest[0], "b": rest[1], **result})
        return 1 if any(result.values()) else 0
    state = read_all(rest[0] if rest else None)
    if cmd == "tree":
        print(render_tree(state, rest[0] if rest else "all scopes"))
        return 2 if any(v["problems"] for v in state.values()) else 0
    dump({"scopes": state} if cmd == "sessions" else summary(state))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
