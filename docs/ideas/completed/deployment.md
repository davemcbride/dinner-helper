# Auto-start Dinner Helper on boot (systemd user service)

**Status: complete (2026-09-12).** The app now starts automatically on machine
restart via a systemd **user** service with lingering enabled. No Docker.

## Goal

Stop launching `uvicorn` by hand: the app should come back up on its own after
the machine reboots, so both phones can reach it without someone SSHing in to
start it.

## Decision: systemd user service, not Docker

Docker was on the table (the `home-lab` repo runs everything in containers), but
it's heavy-handed for this app:

- One Python process reading local files (`data/`, `certs/`, `static/`) on the
  same host — no isolation, scaling, or multi-service benefit.
- A container would add a Dockerfile, image rebuilds on dependency changes, and
  bind mounts for `data/` (the SQLite DB) and `certs/`.
- The machine already runs a systemd user service (`deluge-proxy.service`), so
  this matches existing convention with a single small unit file.

Docker becomes worth it only if the app moves to another host or gets folded
into the `home-lab` compose stack.

## Plain HTTP, not `--ssl` (important)

The unit runs the app **without `--ssl`** (plain HTTP on `:8000`), even though
`certs/` exists. That's deliberate:

- The public path is Cloudflare Tunnel → this app. TLS terminates at the
  Cloudflare edge, and cloudflared talks plain HTTP to
  `192.168.1.159:8000` (see `cloudflare-tunnel-remote.md` and the README).
  Serving `--ssl` here would break the tunnel origin.
- The previous hand-run process was plain HTTP for the same reason.
- LAN-direct `http://192.168.1.159:8000` works as-is. The mkcert `--ssl` mode
  (`docs/features/local-https.md`) remains available for manual/LAN-HTTPS runs
  but is **not** what autostart uses.

## What was done

Unit file: `~/.config/systemd/user/dinner-helper.service`

```ini
[Unit]
Description=Dinner Helper (FastAPI)
After=network.target

[Service]
WorkingDirectory=/home/dmcbride/git/dinner-helper
ExecStart=/home/dmcbride/git/dinner-helper/.venv/bin/python -m app.main --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

Commands run:

```bash
systemctl --user daemon-reload
systemctl --user enable --now dinner-helper.service
loginctl enable-linger dmcbride      # start at boot without logging in
```

`Linger=no` was the default: user services only start after login. Enabling
lingering makes `dinner-helper` start on boot with no interactive login, which
is what "run on restart" requires.

Verified: service `active (running)`; `http://localhost:8000/` and
`/api/meals` return 200.

## Gotchas

- A stray hand-run `uvicorn` was holding `:8000` at first, so systemd crash-
  looped with `[Errno 98] address already in use`. Don't run a second copy by
  hand; use the service.
- After editing `app/`, restart the service — `uvicorn` has no `--reload`:
  `systemctl --user restart dinner-helper`. (Same backend-restart gotcha as the
  README.)
- If the DB is missing (`data/dinners.db`), the app is useless until reseeded;
  autostart doesn't change that.

## Operating it

```bash
systemctl --user status dinner-helper
systemctl --user restart dinner-helper
systemctl --user stop dinner-helper
journalctl --user -u dinner-helper -f
```