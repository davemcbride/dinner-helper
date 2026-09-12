# Dinner Helper

A small mobile-first web app for choosing and tracking dinners, shared between
both your phones. Data (your `docs/dinner_list.md`) was cleaned once and seeded
into SQLite; from then on the app tracks what you actually cook.

## Quick start (on your home machine)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# one-time: build data/clean_meals.json from docs/dinner_list.md
.venv/bin/python scripts/clean_list.py

# one-time: create data/dinners.db (add --fresh to wipe and reseed)
.venv/bin/python -m app.seed

# run the server on your network
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
# or with the local HTTPS cert (see "HTTPS for your phone" below)
.venv/bin/python -m app.main --host 0.0.0.0 --port 8000 --ssl
```

Then open **`http://<your-pc-ip>:8000`** on both phones. Find your PC's IP
with `ip addr` (something like `192.168.1.20`). For access away from home, use
the Cloudflare Tunnel URL — see "Remote access from anywhere" below.

## Local HTTPS (friendly hostname without SSL warnings)

Chrome's HTTPS-First mode upgrades `http://dinner.dave.lan:8000` to https://,
showing an SSL warning because the app runs plain HTTP. Fix it with mkcert:

```bash
# one-time, on the machine that runs the app
sudo apt install mkcert
mkcert -install                      # trust the local CA on this machine
mkdir -p certs
cd certs && mkcert dinner.dave.lan localhost
# chain the leaf with the CA so phones can validate without extra fetches
cat dinner.dave.lan.pem "$(mkcert -CAROOT)/rootCA.pem" > dinner.dave.lan-chain.pem
```

Then run with `python -m app.main ... --ssl` (as above) and open
**`https://dinner.dave.lan:8000`**.

On her Android phone, install the root CA once so Chrome trusts it:

1. Copy `$( mkcert -CAROOT )/rootCA.pem` to the phone (email/SD card/etc).
2. Settings → Security and privacy → More security settings → Install a
   certificate → pick the file → OK (name it anything, e.g. "dinner dave").

The cert names the hostname only, so keep using `dinner.dave.lan` on both
phones — visiting by raw IP will still warn. Certs in `certs/` are
git-ignored; re-run `mkcert` if they expire.

## Remote access from anywhere (Cloudflare Tunnel)

The LAN URL only works at home (the network is behind CGNAT, so port-forwards
aren't an option). To reach the app off-LAN we use the existing Cloudflare
Zero Trust tunnel from the `home-lab` repo:

- **`https://dinner.davemcbride.org`** → Cloudflare edge (trusted HTTPS) →
  cloudflared → this app at `192.168.1.159:8000`.
- Cloudflare **Access** gates the hostname behind an emailed one-time PIN
  limited to our two addresses, so no login exists in the app itself.
- Nothing in this repo changes: the server stays plain HTTP on `:8000`; the
  tunnel hostname and Access policy are configured in the CF dashboard.

Setup details live in `docs/ideas/cloudflare-tunnel-remote.md`. On the LAN the
direct `http://192.168.1.159:8000` (or `dinner.dave.lan`) still works as-is.

## What it does

- **Pick** – suggests a meal, biased towards ones you've never logged or only
  had once. Choose *Rare first*, *Random* or *Favourites*. Tap **We cooked
  this!** to log tonight's dinner.
- **Meals** – search the list (by name or known aliases), filter by Never
  had / Had once / Favourites, tap any meal to log, merge or delete it.
- **Stats** – favourites, never-had and rare lists, recent meals with undo,
  plus a *Clear imported history* button if you'd rather start counting fresh.
- **Review** – the 21 entries the importer flagged (plans, ready meals, "eat
  out" notes, typos) plus suggested near-duplicate merges. Confirm **Keep**,
  **Merge** or **Delete** each one.

## How the data got cleaned

`scripts/clean_list.py` never touches `docs/dinner_list.md`. It:

1. strips weekday prefixes ("Tues slow cooked chilli" → "Slow cooked chilli"),
2. fixes spelling variants so duplicates fold together ("Fajetas"/"Fajitas",
   "Rissoto"/"Risotto", "Spag bol"/"Spaghetti Bolognese", "Meat ball(s)"…),
3. keeps every variant as an **alias** so search still finds them,
4. **flags** entries that look like plans, ready meals, or eating-out notes,
5. writes a *merge suggestion* where two near-identical meals exist.

The output goes to `data/clean_meals.json`. Re-run the script any time you
paste a fresh list and re-seed with `app.seed --fresh`.

## About the numbers on day one

Your list records how many times a meal was *written down*, not cooked. The
seed uses those counts to backfill history so favourites/rare/never-had are
meaningful immediately. Those synthetic rows are marked `note='seed'` and the
Stats tab's **Clear imported history** button drops them so you can start
from zero.

## Hosting notes

- Built for a home LAN, so there's deliberately no login in the app. On the LAN
  anyone who can reach `http://<ip>:8000` can edit the list.
- Off-LAN access goes through Cloudflare Tunnel + Access (emailed one-time
  PIN), so the public URL is authenticated at the edge — see "Remote access
  from anywhere" above.
- If you expose it some other way, put a PIN/proxy in front of it.
- Data lives in `data/dinners.db` – back it up / copy it between machines.

## Layout

```
app/            FastAPI backend (routes in main.py)
app/db.py       SQLite schema + connection
app/seed.py     builds data/dinners.db from the cleaned JSON
scripts/        clean_list.py (one-time import)
data/           clean_meals.json + dinners.db (generated)
static/         mobile UI (index.html, app.js, style.css) — no build step
```