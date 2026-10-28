#!/bin/bash
# Build "Claude Active.app", a Dock and Raycast launcher that opens the Claude app as the
# account cswap has active: the stock profile when it is signed in to that account,
# else `aswap desktop <slot>`. Icon: assets/claude-glyph.svg, white on black. Needs Xcode.
#   dock_launcher.sh [--dock]   build into ~/Applications; --dock also puts it in the
#                               Dock in place of Claude.app (restarts the Dock)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PREFIX="${CLAUDE_ACTIVE_BUNDLE_PREFIX:-$(uv run --quiet --with pyyaml python "$HERE/private_config.py" bundle_prefix)}"
APP="$HOME/Applications/Claude Active.app"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/MacOS/claude-active" <<'EOF'
#!/bin/bash
# Open the Claude app as cswap's active account.
SEQ="$HOME/.claude-swap-backup/sequence.json"
STOCK="$HOME/Library/Application Support/Claude/config.json"
slot=$(plutil -extract activeAccountNumber raw "$SEQ" 2>/dev/null)
want=$(plutil -extract "accounts.$slot.uuid" raw "$SEQ" 2>/dev/null)
have=$(plutil -extract lastKnownAccountUuid raw "$STOCK" 2>/dev/null)
if [ -z "$slot" ] || [ "$want" = "$have" ]; then
  exec open -a Claude
fi
exec "$HOME/.local/bin/aswap" desktop "$slot"
EOF
chmod +x "$APP/Contents/MacOS/claude-active"

cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Claude Active</string>
  <key>CFBundleDisplayName</key><string>Claude Active</string>
  <key>CFBundleIdentifier</key><string>$PREFIX.claude-active</string>
  <key>CFBundleExecutable</key><string>claude-active</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundleIconName</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
</dict></plist>
EOF

# An Icon Composer icon (macOS 26 draws it in the system shape at the stock app's size;
# a bare .icns gets a grey plate around it), compiled with Xcode's actool.
ICON="$TMP/AppIcon.icon"
mkdir -p "$ICON/Assets"
cp "$HERE/../assets/claude-glyph.svg" "$ICON/Assets/glyph.svg"
cat > "$ICON/icon.json" <<'JSON'
{
  "fill": {"solid": "srgb:0.00000,0.00000,0.00000,1.00000"},
  "groups": [{"layers": [{"image-name": "glyph.svg", "name": "glyph",
                          "position": {"scale": 0.7, "translation-in-points": [0, 0]}}],
              "shadow": {"kind": "neutral", "opacity": 0.5},
              "translucency": {"enabled": false, "value": 0.5}}],
  "supported-platforms": {"squares": "shared"}
}
JSON
xcrun actool "$ICON" --compile "$APP/Contents/Resources" --platform macosx \
  --minimum-deployment-target 13.0 --app-icon AppIcon --output-partial-info-plist "$TMP/partial.plist" >/dev/null
codesign --force --sign - "$APP" >/dev/null 2>&1 || true
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP"
echo "built $APP"

if [ "${1:-}" = "--dock" ]; then
  python3 - "$APP" <<'EOF'
import plistlib, subprocess, sys, urllib.parse
app = sys.argv[1]
raw = subprocess.run(["defaults", "export", "com.apple.dock", "-"], capture_output=True, check=True).stdout
dock = plistlib.loads(raw)
url = "file://" + urllib.parse.quote(app) + "/"
apps = [a for a in dock["persistent-apps"] if a.get("tile-data", {}).get("file-data", {}).get("_CFURLString") != url]
tile = {"tile-type": "file-tile", "tile-data": {"file-label": "Claude Active",
        "file-data": {"_CFURLString": url, "_CFURLStringType": 15}}}
at = next((i for i, a in enumerate(apps)
           if a.get("tile-data", {}).get("bundle-identifier") == "com.anthropic.claudefordesktop"), None)
if at is None:
    apps.append(tile)
else:
    apps[at] = tile
dock["persistent-apps"] = apps
subprocess.run(["defaults", "import", "com.apple.dock", "-"], input=plistlib.dumps(dock), check=True)
subprocess.run(["killall", "Dock"], check=True)
print("Dock: Claude Active in place of Claude" if at is not None else "Dock: Claude Active added")
EOF
fi
