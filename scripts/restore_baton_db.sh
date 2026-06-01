#!/usr/bin/env bash
# Restore baton.db from the most recent pre-migration backup.
#
# ADR-13b rollback aid. Migrations call migration_backup.backup_db() before
# applying, writing `<db>.bak-<schema_version>-<timestamp>`. This script lists
# those snapshots and restores the newest (or a named one) after confirmation.
#
# Usage:
#   scripts/restore_baton_db.sh [--db PATH] [--list] [--file BACKUP] [--yes]
#
# Defaults to .claude/team-context/baton.db under the current directory.
set -euo pipefail

DB=".claude/team-context/baton.db"
LIST_ONLY=false
PICK=""
ASSUME_YES=false

while [ $# -gt 0 ]; do
    case "$1" in
        --db)   DB="$2"; shift 2 ;;
        --list) LIST_ONLY=true; shift ;;
        --file) PICK="$2"; shift 2 ;;
        --yes|-y) ASSUME_YES=true; shift ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

shopt -s nullglob
backups=( "${DB}".bak-* )
shopt -u nullglob

if [ ${#backups[@]} -eq 0 ]; then
    echo "No backups found matching ${DB}.bak-*"
    echo "(Migrations create these automatically via migration_backup.backup_db.)"
    exit 1
fi

# Sort newest-last by name (timestamps are zero-padded ISO, so lexical = chronological).
IFS=$'\n' sorted=($(printf '%s\n' "${backups[@]}" | sort)); unset IFS
newest="${sorted[-1]}"

if [ "$LIST_ONLY" = true ]; then
    echo "Backups for ${DB}:"
    printf '  %s\n' "${sorted[@]}"
    echo ""
    echo "Newest: ${newest}"
    exit 0
fi

target="${PICK:-$newest}"
if [ ! -f "$target" ]; then
    echo "Backup not found: $target" >&2
    exit 1
fi

echo "About to restore:"
echo "  from: $target"
echo "  to:   $DB"
if [ "$ASSUME_YES" != true ]; then
    read -rp "Proceed? This overwrites the current DB. [y/N] " ans
    case "$ans" in y|Y|yes|YES) ;; *) echo "Aborted."; exit 1 ;; esac
fi

# Snapshot the current (broken) DB before overwriting, so the restore itself
# is reversible.
if [ -f "$DB" ]; then
    pre="${DB}.pre-restore-$(date -u +%Y%m%dT%H%M%SZ)"
    cp "$DB" "$pre"
    echo "Saved current DB to $pre"
fi

# Remove stale WAL/SHM sidecars so the restored snapshot is authoritative.
rm -f "${DB}-wal" "${DB}-shm"
cp "$target" "$DB"
echo "Restored ${DB} from $(basename "$target")."
