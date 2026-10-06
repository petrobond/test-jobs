#!/usr/bin/env bash
# Сборка PDF из markdown: pandoc -> HTML -> печать через headless Chrome.
# Использование:  ./build_pdf.sh
# Требуется: pandoc и Google Chrome (путь можно переопределить переменной CHROME).
set -euo pipefail
cd "$(dirname "$0")"

CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if ! command -v pandoc >/dev/null 2>&1; then
  echo "Не найден pandoc. Установите: brew install pandoc" >&2
  exit 1
fi
if [ ! -x "$CHROME" ]; then
  echo "Не найден Chrome: $CHROME" >&2
  exit 1
fi

# Chrome в headless-режиме печатает PDF, но не завершает процесс.
# Поэтому ждём появления файла, убеждаемся, что он перестал расти, и снимаем процесс.
print_pdf() {
  local out="$1" url="$2" dir pid i s1 s2
  dir="$(mktemp -d "$TMP/chrome.XXXXXX")"
  rm -f "$out"
  "$CHROME" --headless=new --disable-gpu --no-first-run --no-pdf-header-footer \
    --user-data-dir="$dir" --virtual-time-budget=4000 \
    --print-to-pdf="$out" "$url" >/dev/null 2>&1 &
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

  if [ ! -s "$out" ]; then
    echo "Не удалось собрать $out" >&2
    return 1
  fi
  echo "   $out ($(wc -c < "$out" | tr -d ' ') байт)"
}

for name in part1 part2; do
  echo "→ собираю $name.pdf"
  pandoc "$name.md" -s -o "$TMP/$name.html" \
    --metadata title="Systematica · $name" \
    --css=doc.css --embed-resources
  print_pdf "$PWD/$name.pdf" "file://$TMP/$name.html"
done

echo "готово: part1.pdf, part2.pdf"
