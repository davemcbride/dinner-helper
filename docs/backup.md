# Database backups (local + Cloudflare R2)

**Status: complete (2026-09-22).** Nightly snapshots of `data/dinners.db` are
written to `/home/dmcbride/backups/dinner-helper/` and copied to the
`r2:dinner-helper-backup` bucket. Runs from a systemd **user** timer, not cron.

## What is backed up, and why

Only `data/dinners.db` matters. `data/clean_meals.json` is regenerable from
`docs/dinner_list.md` via `scripts/clean_list.py`, and `static/` and `app/` are
in git. The DB is the sole record of merges, aliases, flags, history and plans —
everything the two of you have done in the app, none of which exists anywhere
else. It is also the thing `app.seed --fresh` destroys, which is the most likely
way to lose it.

## How it works

`scripts/backup.sh`:

1. Snapshots the live DB with SQLite's own backup API —
   `sqlite3 data/dinners.db ".backup <tmpfile>"`. This is safe while `uvicorn`
   is writing; a plain `cp` of a live database is not.
2. Verifies the snapshot with `PRAGMA integrity_check` and logs the meal and
   history row counts.
3. Gzips it to `dinners-YYYYMMDD-HHMMSS.db.gz` (~16 KB for a 216-meal, 344-row
   database).
4. Copies it to `r2:dinner-helper-backup/` with rclone.
5. Rotates: 15 days locally, 30 days in R2.

It follows the same shape as `home-lab/jellyfin/backup.sh` so both backups are
operated the same way. There is no Docker here — the app is a plain systemd
user service, so the backup is too.

### Schedule

Systemd user units (the timer replaces the host-crontab approach used for
Jellyfin, so no sudo is needed):

| File | Purpose |
|---|---|
| `~/.config/systemd/user/dinner-helper-backup.service` | `Type=oneshot`, runs `scripts/backup.sh` |
| `~/.config/systemd/user/dinner-helper-backup.timer` | `OnCalendar=*-*-* 02:30:00`, `Persistent=true` |

02:30 is deliberate: Jellyfin's backup runs at 02:00 and both write to
`/home/dmcbride/backups/`. `Persistent=true` means a run missed because the
machine was off fires shortly after boot. `RandomizedDelaySec=10m` keeps the
R2 upload from colliding with anything else on the same second.

The timer is enabled by the
`~/.config/systemd/user/timers.target.wants/dinner-helper-backup.timer`
symlink; `loginctl enable-linger dmcbride` (already set for
`dinner-helper.service`) is what lets a user timer run with nobody logged in.

## Setup record

```bash
# 1. R2 bucket (rclone can create it; the existing r2: remote's token is
#    scoped wide enough to make new buckets)
rclone mkdir r2:dinner-helper-backup

# 2. Script
chmod +x scripts/backup.sh
scripts/backup.sh          # first run, verify output

# 3. Units written to ~/.config/systemd/user/, then
systemctl --user daemon-reload
systemctl --user enable --now dinner-helper-backup.timer
```

No new rclone remote or API token was needed — the existing `r2:` remote
(local, outside the repo, in `~/.config/rclone/rclone.conf`) already has
object read/write on this account.

## Operating it

```bash
# timer status: when it last ran and when it runs next
systemctl --user list-timers dinner-helper-backup.timer

# service result and full output of the last run
systemctl --user status dinner-helper-backup.service

# run a backup right now
systemctl --user start dinner-helper-backup.service
# or directly, which prints to the terminal
scripts/backup.sh

# logs: the script logs to backup.log (gitignored) and to the journal
tail -f backup.log
journalctl --user -u dinner-helper-backup -f

# what exists
ls -lh /home/dmcbride/backups/dinner-helper/
rclone lsl r2:dinner-helper-backup
rclone size r2:dinner-helper-backup
```

## Restoring

Local copy:

```bash
systemctl --user stop dinner-helper
cd /home/dmcbride/git/dinner-helper
cp data/dinners.db data/dinners.db.before-restore   # keep the broken one for a look
gunzip -c /home/dmcbride/backups/dinner-helper/dinners-YYYYMMDD-HHMMSS.db.gz > data/dinners.db
systemctl --user start dinner-helper
```

From R2 (the disaster case):

```bash
rclone copy r2:dinner-helper-backup/dinners-YYYYMMDD-HHMMSS.db.gz /tmp/
gunzip -c /tmp/dinners-YYYYMMDD-HHMMSS.db.gz > /tmp/restored.db
sqlite3 /tmp/restored.db "PRAGMA integrity_check; SELECT count(*) FROM meals;"
systemctl --user stop dinner-helper
cp /tmp/restored.db /home/dmcbride/git/dinner-helper/data/dinners.db
systemctl --user start dinner-helper
```

Check the last line twice: a restore replaces merges, aliases, flags and history
with whatever the snapshot held, silently undoing anything done since.

## Verified

- `scripts/backup.sh` run twice by hand: snapshot integrity `ok`,
  `meals=216`, `history=344`, 16 KB gzip.
- Upload present in R2: `rclone lsl r2:dinner-helper-backup` lists the file.
- Round trip: downloaded from R2, gunzipped, `integrity_check` = `ok`,
  `meals=216`, `plans=13`.
- `systemd-analyze --user verify` on both units passes; next elapse computed as
  the coming 02:30.

## Gotchas

- **A 501 `NotImplemented` from R2 is normal here.** The first upload attempt
  of each file logs
  `ERROR : ...: Failed to copy: NotImplemented: Not Implemented, status code:
  501` and then `Attempt 2/3 succeeded`. It is a retryable API quirk of R2 (not
  a permissions or bucket problem) and the object does land. Do not "fix" it by
  loosening the token.
- **`rclone v1.60.1-DEV` (Ubuntu 24.04) has no `--no-progress` flag on `copy`.**
  The Jellyfin script's `--progress` works, but the negation does not exist in
  this version; the script just omits it.
- **The old `r2:jellyfin-backup` bucket is unrelated** — separate bucket, so
  Jellyfin's 30-day cleanup (`--include "jellyfin-config-*.tar.gz"`) can never
  touch these files, and vice versa.
- **The script must not need the server stopped.** If a future change makes it
  reach for `data/*.db` directly instead of `.backup`, it will either fail or
  produce a torn copy while `uvicorn` is writing.
- **Cost is negligible**: ~16 KB/day, so ~0.5 MB with 30 days retained
  (storage $0.015/GB/month, egress free).
- Restore halts the app, so it is not a hot operation to run casually on a night
  someone is picking dinner.