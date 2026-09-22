#!/bin/bash
#
# Dinner Helper backup
#
# Takes a consistent snapshot of the live SQLite database (the .backup command
# reads through SQLite itself, so it is safe while uvicorn is writing), checks
# it, compresses it, keeps a copy locally and uploads it to Cloudflare R2.
#
# Mirrors the pattern of home-lab/jellyfin/backup.sh so both backups behave the
# same way. Run by systemd timer: dinner-helper-backup.timer.
#
# Restore: see docs/backup.md
#

set -euo pipefail

# Configuration
REPO_DIR="/home/dmcbride/git/dinner-helper"
DB_PATH="$REPO_DIR/data/dinners.db"
LOCAL_BACKUP_DIR="/home/dmcbride/backups/dinner-helper"
R2_REMOTE="r2:dinner-helper-backup"   # rclone remote + bucket
DATE=$(date +%Y%m%d-%H%M%S)
BACKUP_NAME="dinners-$DATE.db.gz"
KEEP_LOCAL_DAYS=15
KEEP_CLOUD_FILES=3
LOG_FILE="$REPO_DIR/backup.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# R2 lifecycle rules expire objects by age only; there is no "keep the newest N
# objects" rule, and setting them needs a bucket-admin token rather than the
# object-scoped one rclone uses. So the count cap is enforced here: list the
# bucket, and delete everything past the newest $KEEP_CLOUD_FILES.
prune_cloud() {
    local excess old
    excess=$(rclone lsf "$R2_REMOTE/" --files-only --include "dinners-*.db.gz" \
        | sort -r | tail -n "+$((KEEP_CLOUD_FILES + 1))")

    if [ -z "$excess" ]; then
        log "Cloud cleanup: newest $KEEP_CLOUD_FILES kept, nothing to remove"
        return 0
    fi

    while IFS= read -r old; do
        [ -n "$old" ] || continue
        if rclone deletefile "$R2_REMOTE/$old"; then
            log "Removed old cloud backup: $old"
        else
            log "WARNING: could not remove old cloud backup: $old"
        fi
    done <<< "$excess"
}

log "=== Starting Dinner Helper backup ==="

if [ ! -f "$DB_PATH" ]; then
    log "ERROR: database not found at $DB_PATH"
    exit 1
fi

mkdir -p "$LOCAL_BACKUP_DIR"

# Consistent snapshot via SQLite's backup API (copies a plain file, not safe).
SNAPSHOT=$(mktemp "$LOCAL_BACKUP_DIR/.snapshot-XXXXXX.db")
cleanup() { rm -f "$SNAPSHOT"; }
trap cleanup EXIT

log "Snapshotting $DB_PATH"
if ! sqlite3 "$DB_PATH" ".backup '$SNAPSHOT'"; then
    log "ERROR: sqlite3 .backup failed"
    exit 1
fi

INTEGRITY=$(sqlite3 "$SNAPSHOT" "PRAGMA integrity_check;")
if [ "$INTEGRITY" != "ok" ]; then
    log "ERROR: snapshot failed integrity check: $INTEGRITY"
    exit 1
fi

MEALS=$(sqlite3 "$SNAPSHOT" "SELECT count(*) FROM meals;")
HISTORY=$(sqlite3 "$SNAPSHOT" "SELECT count(*) FROM history;")
log "Snapshot OK (integrity=$INTEGRITY, meals=$MEALS, history=$HISTORY)"

gzip -c "$SNAPSHOT" > "$LOCAL_BACKUP_DIR/$BACKUP_NAME"

if [ -f "$LOCAL_BACKUP_DIR/$BACKUP_NAME" ]; then
    BACKUP_SIZE=$(du -h "$LOCAL_BACKUP_DIR/$BACKUP_NAME" | cut -f1)
    log "Local backup created: $BACKUP_NAME ($BACKUP_SIZE)"
else
    log "ERROR: local backup was not created"
    exit 1
fi

# Upload to Cloudflare R2 (degrades gracefully, local backup is the safety net)
if command -v rclone &> /dev/null && rclone listremotes | grep -q "^r2:$"; then
    log "Uploading to Cloudflare R2 ($R2_REMOTE)..."
    if rclone copy "$LOCAL_BACKUP_DIR/$BACKUP_NAME" "$R2_REMOTE/"; then
        log "Cloud backup uploaded"
    else
        log "WARNING: cloud upload failed, local backup is safe"
    fi

    prune_cloud
else
    log "WARNING: rclone or the r2: remote is unavailable, skipping cloud backup"
fi

log "Cleaning up local backups older than $KEEP_LOCAL_DAYS days..."
find "$LOCAL_BACKUP_DIR" -name "dinners-*.db.gz" -mtime "+$KEEP_LOCAL_DAYS" -delete

log "=== Backup summary ==="
log "Local backups: $(find "$LOCAL_BACKUP_DIR" -name 'dinners-*.db.gz' | wc -l) files in $LOCAL_BACKUP_DIR"
if command -v rclone &> /dev/null && rclone listremotes | grep -q "^r2:$"; then
    CLOUD_COUNT=$(rclone lsf "$R2_REMOTE/" --include "dinners-*.db.gz" 2>/dev/null | wc -l || echo 0)
    log "Cloud backups: $CLOUD_COUNT files (newest $KEEP_CLOUD_FILES kept)"
fi
log "=== Backup complete ==="