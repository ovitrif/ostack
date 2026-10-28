#!/usr/bin/env python3
"""Give one account's Code sidebar the groups of another: same names, order, sections and members.

  port_groups.py <from> <to>            write, with both app profiles quit
  port_groups.py <from> <to> --dry-run  print each group's live members on <to> after the port

A group keeps <to>'s id when <to> has one of that name (a group id belongs to one
account), else gets a fresh `cg-<uuid4>`. A session <from> has takes its
group there; one only <to> has keeps its group when that name survives. The groups
go to every copy (config, Local Storage's LSS keys and `dframe-store`), the row-count
cache is dropped, and the pending-upload marker makes the next launch upload them in
place of the server copy. `app_switch.py` runs this between the quit and the launch.
"""
import copy
import json
import shutil
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
PFX = b"_https://claude.ai\x00\x01"
from private_config import STATE_DIR as STATE


def prefs(profile):
    return json.loads((profile / "claude_desktop_config.json").read_text())["preferences"]["epitaxyPrefs"]


def plan(src, src_scope, dst, dst_scope, sessions):
    """The groups and sections <to> gets: <from>'s, with <to>'s ids where the names match."""
    sp, dp = prefs(src), prefs(dst)
    sg, ss = sp["dframe-group-scopes"][src_scope], sp["dframe-code-sections"][src_scope]
    tg = dp.get("dframe-group-scopes", {}).get(dst_scope) or {"groups": [], "assignments": {}}
    byname = {g["name"]: g["id"] for g in tg["groups"]}
    idmap = {g["id"]: byname.get(g["name"]) or f"cg-{uuid.uuid4()}" for g in sg["groups"]}
    names = {idmap[g["id"]]: g["name"] for g in sg["groups"]}
    groups = copy.deepcopy(tg)
    groups["groups"] = [{**g, "id": idmap[g["id"]]} for g in sg["groups"]]
    kept = {k: v for k, v in tg["assignments"].items()
            if k.split(":", 1)[-1] not in sessions and v in names}
    groups["assignments"] = {**kept, **{k: idmap[v] for k, v in sg["assignments"].items() if v in idmap}}
    if "order" in sg:
        o = sg["order"]
        groups["order"] = {idmap.get(k, k): v for k, v in o.items()} if isinstance(o, dict) else [idmap.get(x, x) for x in o]
    sections = copy.deepcopy(ss)
    for s in sections["sections"]:
        s["id"] = idmap.get(s["id"], s["id"])
    return groups, sections, names


def port(src, src_scope, dst, dst_scope, sessions_dir, dry=False):
    sessions = {p.stem for p in (sessions_dir / src_scope).glob("local_*.json")}
    groups, sections, names = plan(src, src_scope, dst, dst_scope, sessions)
    if dry:  # live members by title; a session from <from> arrives with the switch
        live = {}
        for scope in (dst_scope, src_scope):  # the switch brings <from>'s newer copy
            for p in (sessions_dir / scope).glob("local_*.json"):
                d = json.loads(p.read_text())
                live[p.stem] = None if d.get("isArchived") else d.get("title")
        out = {}
        for k, v in groups["assignments"].items():
            if title := live.get(k.split(":", 1)[-1]):
                out.setdefault(names[v], []).append(title)
        return {n: sorted(out.get(n, [])) for n in names.values()}
    import plyvel
    sys.path.insert(0, str(HERE))
    from local_storage import decode, encode
    backup = STATE / f"port-{time.strftime('%Y%m%d-%H%M%S')}"
    backup.mkdir(parents=True)
    shutil.copy2(dst / "claude_desktop_config.json", backup)
    shutil.copytree(dst / "Local Storage", backup / "Local Storage")
    config = json.loads((dst / "claude_desktop_config.json").read_text())
    p = config["preferences"]["epitaxyPrefs"]
    p.setdefault("dframe-group-scopes", {})[dst_scope] = groups
    p.setdefault("dframe-code-sections", {})[dst_scope] = sections
    (dst / "claude_desktop_config.json").write_text(json.dumps(config, indent=2))
    db = plyvel.DB(str(dst / "Local Storage/leveldb"))

    def rewrite(key, change):
        raw = db.get(PFX + key.encode())
        if raw is None:
            return
        value = json.loads(decode(raw))
        change(value)
        db.put(PFX + key.encode(), encode(json.dumps(value)))

    def store(v):
        st = v["state"]
        st.setdefault("customGroupsByScope", {})[dst_scope] = groups
        st.setdefault("codeSidebarByScope", {})[dst_scope] = sections
        st.get("sidebarRowCountsByScope", {}).pop(dst_scope, None)

    rewrite("LSS-persisted.dframe-group-scopes", lambda v: v.setdefault("value", {}).__setitem__(dst_scope, groups))
    rewrite("LSS-persisted.dframe-code-sections", lambda v: v.setdefault("value", {}).__setitem__(dst_scope, sections))
    rewrite("dframe-store", store)
    db.put(PFX + b"ccd-sync-pending:ccd/dframe-store", encode(dst_scope))
    db.close()
    return backup


def main(argv):
    if len(argv) < 2 or argv[0] in ("-h", "--help"):
        sys.exit(__doc__)
    sys.path.insert(0, str(HERE))
    import app_switch as a
    ends = []
    for name in argv[:2]:
        slot, acct = a.resolve(name)
        ends += [a.profile_dir(slot, acct), f"{acct['uuid']}/{acct['organizationUuid']}"]
    dry = "--dry-run" in argv
    if not dry and a.app_pids():
        sys.exit("quit the app first: it writes its sidebar back on quit")
    out = port(*ends, a.SESSIONS, dry)
    print(json.dumps(out, indent=1, ensure_ascii=False) if dry else f"ported; backup {out}")


if __name__ == "__main__":
    main(sys.argv[1:])
