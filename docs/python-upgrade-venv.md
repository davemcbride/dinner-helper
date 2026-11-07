# Runbook: Rebuilding the venv After a Host Python Upgrade

## Symptom

`https://dinner.davemcbride.org` stops working. The Cloudflare Tunnel is healthy and
Cloudflare Access still gates the hostname, so the public URL returns an error rather
than an Access login page — the **origin app is dead**, not the network path.

On the host:

```bash
systemctl --user status dinner-helper.service
```

```
Active: activating (auto-restart) (Result: exit-code)
Restart: restart counter is at 187
python[28994]: ModuleNotFoundError: No module named 'fastapi'
```

`Restart=on-failure` with `RestartSec=5` keeps retrying forever, so the unit never shows
as `failed` and `systemctl --user list-units --state=failed` is empty. The climbing
restart counter is the tell.

## Root cause

The virtualenv was created on **2026-09-09** against **Python 3.12.3** (Ubuntu 24.04).
The host was later upgraded to **Ubuntu 26.04.1 LTS**, which provides **Python 3.14.4**
and no longer ships 3.12.

A venv is tied to the exact interpreter version. `.venv/bin/python3` is a symlink to
`/usr/bin/python3`, so after the upgrade it runs **3.14**, but the packages live in a
directory named for the version they were installed under:

```bash
$ cat .venv/pyvenv.cfg
version = 3.12.3

$ ls .venv/lib/
python3.12          # 3.14 looks in lib/python3.14/site-packages -> empty
```

Because `site-packages` is effectively empty, even `pip` is missing:

```
$ .venv/bin/pip --version
ModuleNotFoundError: No module named 'pip'
```

Nothing was corrupted and no application data was involved — only the interpreter-to-
packages mapping broke.

## Diagnosis

Confirm the version mismatch before rebuilding:

```bash
cd /home/dmcbride/git/dinner-helper
grep '^version' .venv/pyvenv.cfg      # venv's build version
/usr/bin/python3 --version            # what it actually runs now
ls .venv/lib/                         # only the OLD pythonX.Y directory should be here
```

To check every project on the box at once:

```bash
find ~/git -maxdepth 3 -name pyvenv.cfg | while read f; do
  printf '%s: ' "$f"; grep '^version' "$f"
done
```

Compare each against `python3 --version`. A directory under `.venv/lib/` that does not
match the current interpreter means that venv needs rebuilding.

## Fix

The venv is gitignored (`.venv/`), so rebuilding it is not a code change and cannot
affect `data/dinners.db`.

```bash
systemctl --user stop dinner-helper.service      # stop the crash loop first

cd /home/dmcbride/git/dinner-helper
python3 -m venv --clear .venv                    # rebuild with the current interpreter
.venv/bin/pip install -r requirements.txt

.venv/bin/python -c "import fastapi, uvicorn; from app import main; print('imports OK')"

systemctl --user start dinner-helper.service
```

`--clear` wipes the old environment but keeps the `.venv` path that the systemd unit
hardcodes in `ExecStart`.

## Verification

```bash
systemctl --user is-active dinner-helper.service        # active
curl -s -o /dev/null -w '%{http_code}\n' http://192.168.1.159:8000/          # 200
curl -s -o /dev/null -w '%{http_code}\n' http://192.168.1.159:8000/api/meals # 200
```

Then the public path — a **302 to `cloudflareaccess.com` is a success**, because it
means the tunnel reached the app and Access is doing its job:

```bash
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' https://dinner.davemcbride.org/
```

Finally confirm the crash loop is gone in `journalctl --user -u dinner-helper.service`,
and that the unit shows `active (running)` rather than `activating (auto-restart)`.

## Result of the 2026-11-07 rebuild

| Package | Version |
|---|---|
| Python | 3.14.4 |
| fastapi | 0.162.0 |
| starlette | 1.10.0 |
| pydantic / pydantic_core | 2.14.3 / 2.50.4 |
| uvicorn | 0.54.0 |
| uvloop | 0.23.0 |
| httptools | 0.9.0 |
| watchfiles | 1.3.0 |

All of `uvicorn[standard]`'s compiled extras (uvloop, httptools, watchfiles) installed
with 3.14 wheels, so `requirements.txt` needed no changes.

## Prevention

- **After any Ubuntu release upgrade** (`do-release-upgrade`) or a manual `python3`
  change, rebuild every venv on the machine. The old interpreter is removed with the
  OS version, so the breakage is silent until the next service restart.
- `systemd` hides this failure mode: because `Restart=on-failure` is set, the unit
  stays in `activating (auto-restart)` instead of `failed`. Watch the restart counter
  or alert on restart loops, not just on `failed` state.
- The nightly backup (`dinner-helper-backup.timer`, 02:30) is unaffected by a broken
  venv — it calls `sqlite3` and `rclone` directly, not the app's Python environment.
  Data kept flowing into `/home/dmcbride/backups/dinner-helper/` throughout the outage.
- A quick audit of other venvs on the box during this incident found
  `~/git/irish-helper/venv` still functional (pip OK) and
  `~/git/oil-price-tracker/.venv` without a usable `python` binary, but neither has a
  systemd unit, so dinner-helper was the only service actually down.
