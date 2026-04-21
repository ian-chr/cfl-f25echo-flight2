#!/bin/bash
# Install Echo FSW systemd units.
#
# Usage:  sudo bash systemd/install.sh
#
# Safe to re-run. Will overwrite existing unit files with the versions in
# this directory. Does NOT destroy data or reboot the Pi.

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
UNIT_DIR=/etc/systemd/system

if [[ "${EUID}" -ne 0 ]]; then
  echo "Must be run as root (use sudo)." >&2
  exit 1
fi

VENV_PY=/home/f25-echo2/.venv/bin/python
if [[ ! -x "$VENV_PY" ]]; then
  echo "ERROR: venv python not found at $VENV_PY" >&2
  echo "The service files reference this path. Either:" >&2
  echo "  (a) create the venv:  sudo -u f25-echo2 python3 -m venv /home/f25-echo2/.venv" >&2
  echo "      then install deps: sudo -u f25-echo2 /home/f25-echo2/.venv/bin/pip install ..." >&2
  echo "  (b) or edit ExecStart= in the .service files to point at your actual venv." >&2
  exit 1
fi

UNITS=(
  echo-incr-boot.service
  echo-telem.service
  echo-radio.service
  echo-watchdog.service
  apogee-video.service
)

for u in "${UNITS[@]}"; do
  echo "[install] $u -> $UNIT_DIR/$u"
  install -m 0644 -o root -g root "$SCRIPT_DIR/$u" "$UNIT_DIR/$u"
done

# Drop stale units from the previous architecture.
STALE=(
  echo-beacon.service
  echo-image-downlink.service
)
for u in "${STALE[@]}"; do
  if [[ -f "$UNIT_DIR/$u" ]]; then
    echo "[install] disabling stale unit: $u"
    systemctl disable --now "$u" 2>/dev/null || true
    rm -f "$UNIT_DIR/$u"
  fi
done

echo "[install] reloading systemd"
systemctl daemon-reload

echo "[install] enabling units (start at boot)"
systemctl enable echo-incr-boot.service
systemctl enable echo-telem.service
systemctl enable echo-radio.service
systemctl enable echo-watchdog.service
# apogee-video is NOT enabled -- it is triggered on demand by telem_deps.app

echo
echo "Done. Services are enabled for next boot."
echo "To start them right now without rebooting:"
echo "  sudo systemctl start echo-telem.service echo-radio.service echo-watchdog.service"
echo
echo "To watch logs:"
echo "  journalctl -u echo-telem.service -f"
echo "  journalctl -u echo-radio.service -f"
echo "  journalctl -u echo-watchdog.service -f"
