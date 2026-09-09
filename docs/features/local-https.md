# Local HTTPS for Mobile (dinner.dave.lan)

## Problem

Chrome's HTTPS-First mode upgrades `http://dinner.dave.lan:8000` to
`https://` automatically. Because the app runs plain HTTP, Chrome then shows
an SSL warning instead of loading the page. It's not the DNS entry — it's the
missing TLS.

## Solution

Run the app over HTTPS with a locally-trusted certificate. A local CA signs a
cert for `dinner.dave.lan`; every device that trusts that CA gets a green
lock, and no third-party is involved.

- Desktop/Ubuntu box: `mkcert` installs its CA into the system trust store.
- Android phone: install the same CA once via Settings.

## One-time setup (machine running the app)

Prerequisites: `mkcert` (`sudo apt install mkcert`), and a venv with the
app's dependencies (see README).

```bash
# 1) Trust the mkcert CA in this machine's trust store
mkcert -install

# 2) Issue a cert for the hostname (add localhost/IP too if useful)
mkdir -p certs && cd certs
mkcert dinner.dave.lan localhost

# 3) Chain the leaf cert with the CA so phone clients can validate the chain
#    without extra fetches
cat dinner.dave.lan.pem "$(mkcert -CAROOT)/rootCA.pem" > dinner.dave.lan-chain.pem
```

`certs/` is git-ignored. The leaf cert is signed directly by the CA, so the
chain file is leaf + root only (no intermediate).

## Running the server with TLS

`app/main.py` accepts a `--ssl` flag:

```bash
# from the repo root
.venv/bin/python -m app.main --host 0.0.0.0 --port 8000 --ssl
```

It serves the chained cert (`certs/dinner.dave.lan-chain.pem`) and key
(`certs/dinner.dave.lan+2-key.pem`). This is only for local development — for
a public server use a real CA (e.g. Certbot) instead.

Without `--ssl`, it stays plain HTTP (still fine for `http://<ip>:8000`).

## Trusting the phone

On the Android phone (Chrome):

1. Copy `$( mkcert -CAROOT )/rootCA.pem` to the phone (email/SD/web server).
2. Settings → Security and privacy → More security settings → Install a
   certificate.
3. Pick the file, give it any name (e.g. "dinner dave"), confirm.
4. The phone may warn about the cert authority being untrusted; accept it —
   it's your own CA.

## Using it

- Visit `https://dinner.dave.lan:8000` (type the full URL once; Chrome's
  https rocket upgrades plain `http://` there on its own afterward).
- The cert is valid for the hostname **only**. Browsing by raw IP
  (`https://192.168.1.159:8000`) will still warn — stick to the name.

## Troubleshooting

- **`curl` warns on this machine:** `mkcert -install` was not run, or run in a
  different trust store. Check with:
  `curl https://dinner.dave.lan:8000/` (should connect cleanly after install).
- **Phone still warns:** the CA `rootCA.pem` isn't installed, or you're using
  a different device. Re-check the phone install and use the hostname.
- **Different hostname / expiry:** re-run the mkcert steps inside `certs/`.
  Default validity is ~2 years; re-issue when it lapses and rebuild the chain.

## Durations

- The cert expires (mkcert default ~2 years, printed when generated).
- The CA has no expiry tracked by the app; `mkcert -install` remains valid
  until re-run.