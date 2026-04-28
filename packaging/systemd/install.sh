#!/usr/bin/env bash
# Install the Agent Baton systemd service unit.
#
# Usage:
#   sudo bash install.sh          # install (refuses to overwrite)
#   sudo bash install.sh --force  # overwrite existing unit
#
# Requirements:
#   - Run as root (or via sudo).
#   - systemd must be available.

set -euo pipefail

UNIT_NAME="agent-baton-daemon.service"
UNIT_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/${UNIT_NAME}"
UNIT_DST="/etc/systemd/system/${UNIT_NAME}"
FORCE=false

for arg in "$@"; do
  case "$arg" in
    --force) FORCE=true ;;
    *)
      echo "Unknown argument: $arg" >&2
      echo "Usage: sudo bash install.sh [--force]" >&2
      exit 1
      ;;
  esac
done

# Require root
if [[ "$(id -u)" -ne 0 ]]; then
  echo "error: this script must be run as root (use sudo)." >&2
  exit 1
fi

# Refuse to overwrite without --force
if [[ -f "${UNIT_DST}" && "${FORCE}" == false ]]; then
  echo "error: ${UNIT_DST} already exists. Pass --force to overwrite." >&2
  exit 1
fi

if [[ ! -f "${UNIT_SRC}" ]]; then
  echo "error: unit file not found: ${UNIT_SRC}" >&2
  exit 1
fi

cp "${UNIT_SRC}" "${UNIT_DST}"
chmod 644 "${UNIT_DST}"

systemctl daemon-reload

echo "Unit installed: ${UNIT_DST}"
echo ""
echo "To activate the service:"
echo "  sudo systemctl enable --now ${UNIT_NAME}"
echo ""
echo "To check status:"
echo "  sudo systemctl status ${UNIT_NAME}"
echo ""
echo "To view logs:"
echo "  journalctl -u ${UNIT_NAME} -f"
