#!/usr/bin/env bash
# Bangun PANDUAN_ODIN.pdf dari sumber Markdown.
#   ./docs/panduan/build.sh            -> PANDUAN_ODIN.pdf di root repo
# Prasyarat: pandoc + xelatex (TeX Live), font Charter / Avenir Next / Menlo (macOS).
set -euo pipefail
cd "$(dirname "$0")/../.."
pandoc docs/panduan/PANDUAN_ODIN.md \
    -o PANDUAN_ODIN.pdf \
    --pdf-engine=xelatex \
    --include-in-header=docs/panduan/00-header.tex \
    --syntax-highlighting=tango
echo "✓ PANDUAN_ODIN.pdf ($(pdfinfo PANDUAN_ODIN.pdf | awk '/^Pages/{print $2}') halaman)"
