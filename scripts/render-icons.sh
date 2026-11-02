#!/usr/bin/env bash
# Render share/icons/claude-launcher.svg to the PNG sizes install.sh installs.
# Needs rsvg-convert (librsvg2-bin) or ImageMagick (7's magick, or 6's
# convert). Run after editing the SVG.
set -euo pipefail
dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/../share/icons" && pwd)
if command -v rsvg-convert >/dev/null; then
  render() { rsvg-convert -w "$1" -h "$1" -o "$2" "$dir/claude-launcher.svg"; }
else
  magick=$(command -v magick || command -v convert) || {
    echo "render-icons.sh needs rsvg-convert (librsvg2-bin) or ImageMagick" >&2
    exit 1
  }
  render() { "$magick" -background none -density 1200 "$dir/claude-launcher.svg" -resize "$1x$1" "PNG32:$2"; }
fi
for size in 48 64 128 256 512; do
  out=$dir/claude-launcher-$size.png
  render "$size" "$out"
  echo "wrote $out"
done
