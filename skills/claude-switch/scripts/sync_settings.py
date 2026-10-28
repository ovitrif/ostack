#!/usr/bin/env python3
"""Give every Claude app profile the same settings, so a switch changes the account and nothing else.

  sync_settings.py <from>            write, with every app profile quit
  sync_settings.py <from> --dry-run  print each setting that would change, per profile

<from> is the account whose profile was used last (a cswap slot, alias or email): its
value wins a setting the profiles disagree on, except a Local Storage setting with a
`timestamp`, where the newest edit wins. A setting only another profile has is kept and
given to all. A setting keyed by account (`...ByAccount.<uuid>`, `<key>.<uuid>`) takes
the winner's value under each profile's own account. Synced: the config's `preferences`
and top-level keys, Local Storage's settings keys (both copies of an `epitaxyPrefs`
key: the config and `LSS-persisted.<key>`), session presets, MCP tool toggles, the
spelling dictionary and the claude.ai zoom. Never synced: groups, sections and panes
(`port_groups.py`, `carry_layout`), `config.json` (login and token caches), Cookies,
and what names a device or account. `app_switch.py` runs this between the quit and
the launch.
"""
import copy
import json
import re
import shutil
import sys
import time
from pathlib import Path

PFX = b"_https://claude.ai\x00\x01"
from private_config import STATE_DIR as STATE
ACCOUNT_IDS = set()  # every stored account and org id; a key naming one is per account (session ids are not)
CONFIG = "claude_desktop_config.json"
# Owned by port_groups.py and carry_layout, or naming this device or session.
OWNED_PREFS = ("dframe-group-scopes", "dframe-code-sections", "desktop-frame.paneStore.v1", "epitaxy.sidePaneStore.v1")
# Local Storage keys that are settings; the rest is caches, drafts and one-off latches.
# Approval decisions belong to the profile/account where the user made them.
CONSENT_PREFS = ("localAgentModeTrustedFolders", "remoteFolderConsentFromSettings",
                 "bypassPermissionsOptInByAccount", "bypassPermissionsGateByAccount",
                 "simulatorDeviceConsent")
CONSENT_LS = ("epitaxy-folder-permission-mode", "epitaxy-folder-permission-mode-v2")


def is_consent(key):
    key = key.removeprefix("LSS-persisted.")
    return key in CONSENT_PREFS or any(key == prefix or key.startswith(prefix + ".") for prefix in CONSENT_LS)


LS_SETTINGS = re.compile(r"^(LSS-persisted\.|persisted\.(ccd-session-presets|epitaxy-split-suggestion|epitaxy\.sessionRailShown)"
                         r"|epitaxy-editor-prefs$|LSS-cmdk-mode$|claudeai\.lastChatLook\.v1$|default-model$"
                         r"|LSS-code-default-landing-applied$|LSS-cowork-default-experiment-applied$)")
LS_OWNED = ("LSS-persisted.dframe-group-scopes", "LSS-persisted.dframe-code-sections")
# Optional user preferences applied to every profile.
from private_config import CONFIG as PRIVATE_CONFIG
FORCED = PRIVATE_CONFIG.get("forced_prefs", {})


def read(path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def generic(key, ids):
    """The key with its account and org ids replaced by placeholders, so one setting matches across profiles."""
    acct, org = ids
    return key.replace(acct, "{acct}").replace(org, "{org}")


def foreign(key):
    return any(i in key for i in ACCOUNT_IDS)


def own(key, ids):
    return key.replace("{acct}", ids[0]).replace("{org}", ids[1])


def merge(values):
    """The first value wins; dicts merge key by key, so a key only a later one has is kept."""
    out = copy.deepcopy(values[0])
    if isinstance(out, dict):
        for later in values[1:]:
            if isinstance(later, dict):
                for k, v in later.items():
                    out[k] = merge([out[k], v]) if k in out else copy.deepcopy(v)
    return out


def by_account(tree, ids):
    """`{...: {<acct uuid>: v}}` maps and `<key>.<uuid>` keys under the profile's own ids,
    rewritten to placeholders (other accounts' entries dropped: each profile gets its own)."""
    if not isinstance(tree, dict):
        return tree
    out = {}
    for k, v in tree.items():
        g = generic(k, ids)
        if foreign(g):  # another account's entry; the winner's is given to each
            continue
        out[g] = by_account(v, ids) if k not in OWNED_PREFS else v
    return out


def config_view(profile, ids):
    c = read(profile / CONFIG) or {}
    view = {k: v for k, v in c.items() if k != "preferences"}
    prefs = {k: copy.deepcopy(v) for k, v in (c.get("preferences") or {}).items() if not is_consent(k)}
    for k in list(prefs.get("epitaxyPrefs") or {}):
        if is_consent(k):
            prefs["epitaxyPrefs"].pop(k)
    for k in OWNED_PREFS:
        (prefs.get("epitaxyPrefs") or {}).pop(k, None)
    view["preferences"] = by_account(prefs, ids)
    return view


def ls_read(profile):
    import plyvel
    import tempfile
    copy_dir = Path(tempfile.mkdtemp(prefix="claude-ls-")) / "leveldb"
    shutil.copytree(profile / "Local Storage/leveldb", copy_dir, ignore=shutil.ignore_patterns("LOCK"))
    db = plyvel.DB(str(copy_dir))
    out = {}
    for k, v in db.iterator(prefix=PFX):
        name = k[len(PFX):].decode("utf-8", "replace")
        if LS_SETTINGS.search(name) and name not in LS_OWNED and not is_consent(name):
            out[name] = v[1:].decode("latin-1" if v[:1] == b"\x01" else "utf-16-le")
    db.close()
    shutil.rmtree(copy_dir.parent)
    return out


def encode(text):
    try:
        return b"\x01" + text.encode("latin-1")
    except UnicodeEncodeError:
        return b"\x00" + text.encode("utf-16-le")


def stamp(text):
    try:
        return float(json.loads(text).get("timestamp") or 0)
    except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
        return 0.0


def union(newer, older):
    """A per-folder map or list (permission modes, their acks) keeps the older one's entries
    under the newer one's: each profile set modes for its own folders (otherwise one profile's
    entry for one folder replaces another profile's entries for other folders)."""
    try:
        n, o = json.loads(newer), json.loads(older)
    except json.JSONDecodeError:
        return newer
    if not (isinstance(n, dict) and isinstance(o, dict)):
        return newer
    nv, ov = n.get("value"), o.get("value")
    if isinstance(nv, dict) and isinstance(ov, dict) and g_is_folder_map(nv, ov):
        n["value"] = {**ov, **nv}
    elif isinstance(nv, list) and isinstance(ov, list) and all(isinstance(x, str) for x in nv + ov):
        n["value"] = nv + [x for x in ov if x not in nv]
    else:
        return newer
    return json.dumps(n, separators=(",", ":"))


def g_is_folder_map(*maps):
    return all(k.startswith("/") for m in maps for k in m)


def plan(profiles):
    """profiles: [(dir, (acct, org))], the left one first. Returns the settings every profile gets."""
    config = merge([config_view(p, ids) for p, ids in profiles])
    if any(is_consent(k) for k in FORCED):
        raise ValueError("forced_prefs cannot override account-specific permission approvals")
    config["preferences"].update(FORCED)
    ls = {}
    for rank, (p, ids) in enumerate(profiles):
        for k, v in ls_read(p).items():
            g = generic(k, ids)
            if foreign(g):
                continue
            # newest edit wins; on a tie (or no timestamp) the earlier profile, the left one first
            if g not in ls:
                ls[g] = v
            else:
                ls[g] = union(v, ls[g]) if stamp(v) > stamp(ls[g]) else union(ls[g], v)
    # The config mirrors each LSS-persisted key in epitaxyPrefs: one value for both.
    prefs = config["preferences"].setdefault("epitaxyPrefs", {})
    for g, v in ls.items():
        if g.startswith("LSS-persisted."):
            try:
                prefs[g[len("LSS-persisted."):]] = json.loads(v)["value"]
            except (json.JSONDecodeError, KeyError, TypeError):
                pass
    files = {
        "ccd-session-presets.json": merge([by_account(read(p / "ccd-session-presets.json") or {}, ids) for p, ids in profiles]),
        "mcp-user-tool-toggles.json": merge([by_account(read(p / "mcp-user-tool-toggles.json") or {}, ids) for p, ids in profiles]),
    }
    words = []
    for p, _ in profiles:
        f = p / "Custom Dictionary.txt"
        for w in (f.read_text().splitlines() if f.exists() else []):
            if w and not w.startswith("checksum_v1") and w not in words:
                words.append(w)
    zoom = merge([((read(p / "Preferences") or {}).get("partition") or {}).get("per_host_zoom_levels") or {} for p, _ in profiles])
    return {"config": config, "ls": ls, "files": files, "words": words, "zoom": zoom}


def localize(tree, ids):
    if isinstance(tree, dict):
        return {own(k, ids): localize(v, ids) for k, v in tree.items()}
    return tree


def apply(profile, ids, want, dry):
    """Write the settings into one profile; returns what changed."""
    changed = []
    c = read(profile / CONFIG) or {}
    new = copy.deepcopy(c)
    target = localize(want["config"], ids)
    for k, v in target.items():
        if k == "preferences":
            continue
        if new.get(k) != v:
            new[k], _ = v, changed.append(k)
    prefs = new.setdefault("preferences", {})
    for k, v in target["preferences"].items():
        if k == "epitaxyPrefs":
            ep = prefs.setdefault("epitaxyPrefs", {})
            for ek, ev in v.items():
                if ep.get(ek) != ev:
                    ep[ek], _ = ev, changed.append(f"epitaxyPrefs.{ek}")
        elif isinstance(v, dict) and isinstance(prefs.get(k), dict):
            for sk, sv in v.items():
                if prefs[k].get(sk) != sv:
                    prefs[k][sk], _ = sv, changed.append(f"preferences.{k}.{sk}")
        elif prefs.get(k) != v:
            prefs[k], _ = v, changed.append(f"preferences.{k}")
    have = ls_read(profile)
    ls = {own(g, ids): v for g, v in want["ls"].items()}
    ls_changed = [k for k, v in ls.items() if have.get(k) != v]
    changed += [f"Local Storage {k}" for k in ls_changed]
    file_new = {}
    for name, tree in want["files"].items():
        mine = read(profile / name) or {}
        theirs = merge([localize(tree, ids), mine]) if tree else mine
        if theirs != mine:
            file_new[name], _ = theirs, changed.append(name)
    dic = profile / "Custom Dictionary.txt"
    old_words = [w for w in (dic.read_text().splitlines() if dic.exists() else []) if w and not w.startswith("checksum_v1")]
    if want["words"] and sorted(old_words) != sorted(want["words"]):
        changed.append("Custom Dictionary.txt")
    pref = read(profile / "Preferences") or {}
    zoom_old = (pref.get("partition") or {}).get("per_host_zoom_levels") or {}
    if want["zoom"] and merge([zoom_old, want["zoom"]]) != zoom_old:
        changed.append("Preferences zoom")
    if dry or not changed:
        return changed
    backup = STATE / f"settings-{time.strftime('%Y%m%d-%H%M%S')}" / profile.name
    backup.mkdir(parents=True, exist_ok=True)
    for f in (CONFIG, "Preferences", "Custom Dictionary.txt", *want["files"]):
        if (profile / f).exists():
            shutil.copy2(profile / f, backup / f)
    shutil.copytree(profile / "Local Storage", backup / "Local Storage")
    (profile / CONFIG).write_text(json.dumps(new, indent=2))
    for name, tree in file_new.items():
        (profile / name).write_text(json.dumps(tree, indent=2))
    if "Custom Dictionary.txt" in changed:
        dic.write_text("\n".join(want["words"]) + "\n")  # the app rewrites the checksum line
    if "Preferences zoom" in changed:
        pref.setdefault("partition", {})["per_host_zoom_levels"] = merge([want["zoom"], zoom_old])
        (profile / "Preferences").write_text(json.dumps(pref, separators=(",", ":")))
    if ls_changed:
        import plyvel
        db = plyvel.DB(str(profile / "Local Storage/leveldb"))
        for k in ls_changed:
            db.put(PFX + k.encode(), encode(ls[k]))
        db.close()
    print(f"settings backup: {backup}")
    return changed


def sync(profiles, dry=False, account_ids=()):
    """profiles: [(dir, (acct, org))], the left one first. Returns {profile dir: changes}."""
    ACCOUNT_IDS.update(account_ids or [i for _, ids in profiles for i in ids])
    want = plan(profiles)
    return {p: apply(p, ids, want, dry) for p, ids in profiles}


def profiles_from(left):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import app_switch
    slot, _ = app_switch.resolve(left)
    found = []
    for n, a in sorted(app_switch.accounts().items(), key=lambda item: item[0] != slot):
        d = app_switch.profile_dir(n, a)
        if d and (d / CONFIG).exists() and d not in [p for p, _ in found]:
            found.append((d, (a["uuid"], a["organizationUuid"])))
    return found


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        sys.exit(__doc__)
    dry = "--dry-run" in argv
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import app_switch
    ids = [i for a in app_switch.accounts().values() for i in (a["uuid"], a["organizationUuid"])]
    for p, changes in sync(profiles_from(argv[0]), dry, ids).items():
        print(f"{p}: {'would change' if dry else 'changed'} {len(changes)}")
        for c in changes:
            print(f"  {c}")


if __name__ == "__main__":
    main(sys.argv[1:])
