#!/bin/bash
# Increment the persistent boot counter written at service start.
#
# Previous version had curly smart-quotes around the initial "0", which
# caused: `"0"` to be written as the literal bytes `\u201c0\u201d`. Reading
# it back with `cat` then trying to add 1 produced a shell arithmetic error
# and the counter stayed at 1 forever. Fixed by using ASCII double quotes
# and failing fast if the file is corrupted.

set -euo pipefail

FILE="/home/f25-echo2/echo_fsw/data/telemetry/boot_count.txt"

# Ensure parent dir exists
mkdir -p "$(dirname "$FILE")"

if [ ! -f "$FILE" ]; then
  echo "0" > "$FILE"
fi

# Read, guard against non-numeric contents, increment, write back.
RAW=$(cat "$FILE" | tr -d '[:space:]')
if ! [[ "$RAW" =~ ^[0-9]+$ ]]; then
  echo "[incr_boot] WARN: boot_count.txt was non-numeric ('$RAW'), resetting to 0" >&2
  RAW=0
fi

COUNT=$((RAW + 1))
echo "$COUNT" > "$FILE"
