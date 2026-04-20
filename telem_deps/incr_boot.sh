#!/bin/bash
FILE="/home/f25-echo/echo_fsw/data/telemetry/boot_count.txt"
if [ ! -f "$FILE" ]; then
  echo “0” > "$FILE"
fi
COUNT=$(cat "$FILE")
COUNT=$((COUNT+1))
echo "$COUNT" > "$FILE"