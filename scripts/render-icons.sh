#!/usr/bin/env bash
# Render share/icons/claude-launcher.svg to the PNG sizes install.sh installs.
# Needs rsvg-convert (librsvg2-bin) or ImageMagick. Run after editing the SVG.
set -euo pipefail
dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/../share/icons" && pwd)
for size in 48 64 128 256 512; do
  out=$dir/claude-launcher-$size.png
  if command -v rsvg-convert >/dev/null; then
    rsvg-convert -w "$size" -h "$size" -o "$out" "$dir/claude-launcher.svg"
  else
    magick -background none -density 1200 "$dir/claude-launcher.svg" -resize "${size}x${size}" "PNG32:$out"
  fi
  echo "wrote $out"
done
