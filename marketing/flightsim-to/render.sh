#!/bin/sh
# The flightsim.to images: each src/*.html at 1920 x 1080, to a PNG beside this script. Needs Google Chrome.
cd "$(dirname "$0")"
CH="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
for page in src/0*.html; do
  name=$(basename "$page" .html)
  "$CH" --headless=new --disable-gpu --hide-scrollbars --allow-file-access-from-files --window-size=1920,1080 \
    --virtual-time-budget=4000 --screenshot="$PWD/$name.png" "file://$PWD/$page" 2>/dev/null
  echo "$name.png"
done
