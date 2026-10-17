#!/usr/bin/env bash
# Render the orchestration diagram and its animation (see agent-orchestration.mjs).
# Needs Node, ffmpeg and a Chromium; CHROME defaults to Playwright's headless shell.
set -euo pipefail
cd "$(dirname "$0")"
CHROME="${CHROME:-$(ls -d "$HOME"/Library/Caches/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell 2>/dev/null | tail -1)}"
[ -x "$CHROME" ] || { echo "Set CHROME to a Chromium binary (npx playwright install chromium-headless-shell)"; exit 1; }

rm -rf out && node agent-orchestration.mjs

shot() { # svg png width height scale
  "$CHROME" --headless --hide-scrollbars --force-device-scale-factor="$5" --window-size="$3,$4" \
    --screenshot="$2" "file://$PWD/$1" >/dev/null 2>&1
}
export -f shot; export CHROME

for theme in light dark; do
  shot "out/static-$theme.svg" "../agent-orchestration-$theme.png" 1600 1010 2
  ls out/$theme-*.svg | xargs -P 8 -I{} bash -c 'shot "$1" "${1%.svg}.png" 1600 1120 1' _ {}
  # Two passes: one shared palette for the whole animation keeps colours steady and the file small.
  ffmpeg -loglevel error -y -f concat -safe 0 -i "out/$theme-frames.txt" \
    -vf "scale=1200:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=full[p];[b][p]paletteuse=dither=none" \
    -loop 0 "../agent-orchestration-$theme.gif"
done
rm -rf out
ls -lh ../agent-orchestration-*.{png,gif}
