#!/usr/bin/env bash
# Скриншоты прототипа: headless Chrome -> PNG с обрезкой пустых полей.
# Использование:  ./shots.sh      (нужен Google Chrome и python3 с Pillow)
# Результат: screenshots/bi_overview.png (срез по дилерам), screenshots/bi_managers.png (по менеджерам).
set -euo pipefail
cd "$(dirname "$0")"

CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if [ ! -x "$CHROME" ]; then
  echo "Не найден Chrome: $CHROME" >&2
  exit 1
fi

# когда нужен только блок аналитики — прячем всё остальное
HIDE='.top,.kpis,.stages,.legend,.bar,.board,.note,.foot{display:none!important}.wrap{padding:10px 16px;max-width:1800px}'

shoot() { # $1 — файл результата, $2 — query-строка, $3 — высота окна
  local out="$1" query="$2" h="$3"
  local page="$TMP/page.html" dir pid i s1 s2
  HIDE="$HIDE" PAGE="$page" python3 -c "
import os, pathlib
src = pathlib.Path('tool/index.html').read_text(encoding='utf-8')
src = src.replace('</head>', '<style>' + os.environ['HIDE'] + '</style></head>')
pathlib.Path(os.environ['PAGE']).write_text(src, encoding='utf-8')
"
  dir="$(mktemp -d "$TMP/chrome.XXXXXX")"
  mkdir -p "$(dirname "$out")"
  rm -f "$out"

  # Chrome в headless-режиме не завершает процесс сам: ждём, пока файл перестанет расти, и снимаем его
  "$CHROME" --headless=new --disable-gpu --no-first-run --hide-scrollbars \
    --user-data-dir="$dir" --virtual-time-budget=5000 --window-size=1760,"$h" \
    --screenshot="$out" "file://$page$query" >/dev/null 2>&1 &
  pid=$!

  i=0
  while [ "$i" -lt 80 ]; do
    if [ -s "$out" ]; then
      s1="$(wc -c < "$out" | tr -d ' ')"
      perl -e 'select(undef,undef,undef,0.25)'
      s2="$(wc -c < "$out" | tr -d ' ')"
      if [ "$s1" = "$s2" ]; then break; fi
    fi
    perl -e 'select(undef,undef,undef,0.25)'
    i=$((i + 1))
  done

  kill "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
  rm -rf "$dir"

  if [ ! -s "$out" ]; then echo "Не удалось снять $out" >&2; return 1; fi

  OUT="$out" python3 -c "
import os
from PIL import Image, ImageChops
p = os.environ['OUT']
im = Image.open(p).convert('RGB')
bg = Image.new('RGB', im.size, im.getpixel((1, 1)))
bbox = ImageChops.difference(im, bg).getbbox()
if bbox:
    x0, y0, x1, y1 = bbox
    pad = 16
    im = im.crop((max(0, x0 - pad), max(0, y0 - pad), min(im.width, x1 + pad), min(im.height, y1 + pad)))
im.save(p)
print('   %s (%dx%d)' % (p, im.width, im.height))
"
}

echo '→ снимаю блок аналитики'
shoot "$PWD/screenshots/bi_overview.png" "" 2500
shoot "$PWD/screenshots/bi_managers.png" "?bi=manager" 2500
echo 'готово: screenshots/bi_overview.png, screenshots/bi_managers.png'
