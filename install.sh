#!/usr/bin/env bash
# Hunyuan 3D Multiview skill - one-line installer (macOS / Linux)
# Usage:  curl -fsSL https://raw.githubusercontent.com/hwdemtv/hunyuan-3d-multiview/main/install.sh | bash
set -euo pipefail

OWNER="hwdemtv"
NAME="hunyuan-3d-multiview"
DEST="$HOME/.workbuddy/skills/$NAME"
TMP="$(mktemp -d)"

echo ">> Downloading $OWNER/$NAME ..."
curl -fsSL "https://github.com/$OWNER/$NAME/archive/refs/heads/main.zip" -o "$TMP/$NAME.zip"
unzip -q "$TMP/$NAME.zip" -d "$TMP"
mkdir -p "$DEST"
cp -r "$TMP/$NAME-main/SKILL.md" "$TMP/$NAME-main/scripts" "$TMP/$NAME-main/references" "$TMP/$NAME-main/vendor" "$DEST/"

rm -rf "$TMP"
echo ">> Installed to $DEST"
echo ">> Restart WorkBuddy, then say: 用 hunyuan-3d-multiview 技能把这张图变成 3D 手办"
