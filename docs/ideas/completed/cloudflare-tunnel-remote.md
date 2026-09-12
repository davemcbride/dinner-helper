# Remote HTTPS + Auth via Cloudflare Tunnel (dinner.davemcbride.org)

**Status: complete (2026-09-12).** Live at `https://dinner.davemcbride.org`,
gated by Cloudflare Access (one-time PIN). See checklist at the end for the
implementation record.

## Goal

Make Dinner Helper reachable over the **public internet** at
`https://dinner.davemcbride.org` with trusted HTTPS and access limited to the
two of us. Deliver both via the existing Cloudflare Zero Trust setup in
`/home/dmcbride/git/home-lab` — no Caddy changes, no Let's Encrypt, no app
code changes.

## Why a tunnel (not the Caddy + LE plan)

The home network is behind CGNAT, so inbound port-forwards won't work. The
same constraint already drives `requests.davemcbride.org` (Seerr): cloudflared
opens outbound connections to Cloudflare's edge, no inbound ports needed. The
homelab already runs a remote-managed tunnel (`cloudflared/docker-compose.yaml`,
token in `cloudflared/.env`) and the Cloudflare DNS-01 Caddy setup —
everything except the app-side route already exists.

## Architecture

```
phone (anywhere)
   │  https://dinner.davemcbride.org
   ▼
Cloudflare edge  ──(TLS terminates HERE, CF-managed cert, auto-renewed)──►  Cloudflare Access (email OTP challenge)
   │  outbound QUIC tunnel
   ▼
cloudflared (docker, shared_media_network)
   │  HTTP
   ▼
uvicorn app.main:app  (host process, 0.0.0.0:8000, plain HTTP)
```

- TLS terminates at Cloudflare's edge (publicly trusted, CF-issued cert), not
  on the origin. Caddy and the mkcert `certs/` are **not** part of this path.
- Authentication is enforced by Cloudflare Access *before* traffic reaches the
  tunnel: unauthenticated requests get a 302 to the Access login.
- The app stays a plain HTTP host process on `:8000`; zero code changes.

## Prerequisites (assumed done)

- `cloudflared` container running (`homelab/cloudflared`), token in `.env`.
- Cloudflare Zero Trust organization with a tunnel (remote-managed) already
  serving `requests.davemcbride.org` → `seerr:5055`.
- The tunnel's cloudflared container sits on `shared_media_network`; the app
  (on the host) binds `0.0.0.0:8000`, so the container can reach it at the
  host LAN IP.

## Setup steps (all Cloudflare Zero Trust dashboard, no repo changes)

### 1. Add a public hostname to the existing tunnel

Zero Trust → Networks → Tunnels → [the tunnel] → Configure →
Public Hostname → **Add a public hostname**:

- Subdomain: `dinner`  (→ `dinner.davemcbride.org`)
- Service: `HTTP`
- URL: `192.168.1.159:8000`

Notes:
- Cloudflare creates a proxied CNAME (`*.davemcbride.org.cfargotunnel.com`)
  automatically. Do **not** add `dinner` to `cloudflare-ddns` `DOMAINS` in
  `homelab` — that DDNS overwrites records with the public IP and would fight
  the tunnel's proxied CNAME (same rule as `requests` per homelab CLAUDE.md).
- No Caddyfile edit, no Caddy restart.

### 2. Protect the hostname with Cloudflare Access

Zero Trust → Access → Applications → **Add an application**:

- Type: **Self-hosted** (or Saas if we want IdP later)
- Domain: `dinner.davemcbride.org`
- Policy "Allow": authentication method **One-time PIN**, identity rules
  `Emails = <my email>, <wife's email>`. (Free tier allows up to 50.)
- Optional: Application session duration (cookie) — default is per-session;
  set to e.g. 1 month so the Access prompt is rare per device.

That's the whole auth story: emailed code = our password, zero code in app.

## Result / behavior

- External: `https://dinner.davemcbride.org` → Access OTP → app. Verified with
  `curl -sI https://dinner.davemcbride.org` → `302` to Access when logged out,
  and full reachability from a phone off-LAN.
- LAN-anywhere vs single URL: no hairpinning at home was written off — the
  phone hits CF edge and tunnels back to the house. For a tiny dinner app this
  is a non-issue; if it ever bothers us, revisit with LAN-direct
  (`http://192.168.1.159:8000` still works on the LAN as-is).

## Auth model tradeoffs

- **One-time PIN (chosen)**: emailed code each device/session. The two of us
  already get email; no shared secret to manage, no app code.
- Alternative (rejected for now): app-level login (own `users` table + hashed
  passwords + signed session cookie in FastAPI). More code to maintain and
  audit for a 2-user app; Cloudflare Access is 100% dashboard clicks. We can
  add app-level auth later as defense-in-depth if ever needed.

## Out of scope (decisions recorded)

- Let's Encrypt on the origin: unnecessary behind a tunnel — CF edge cert is
  already publicly trusted. (A "full (strict)" + Caddy LE origin variant was
  considered; strictly more moving parts, buys nothing for phone use.)
- DNS-only A record / local routing: dropped per "focus on tunnel as the only
  access method."
- Caddy reverse proxy of dinner: not needed when the tunnel is the route.
- `docs/features/local-https.md` (mkcert) stays as the offline-ish LAN
  fallback if the tunnel/edge is ever unreachable.

## To pick up later (checklist)

1. [x] Add `dinner` public hostname + `HTTP 192.168.1.159:8000` to the tunnel
     (done via Routes → Add route → Published application; CNAME
     `dbcd61db-…​.cfargotunnel.com` created). Note: `cloudflared` was found
     stopped on the host and had to be started (`docker compose up -d` in
     `home-lab/cloudflared`) before the tunnel connected.
2. [x] Add `dinner.davemcbride.org` as a Self-hosted Access application
     (UI: Access controls → Applications → Create new application →
     Self-hosted and private). Creating the policy alone does nothing —
     it must be **applied to the application**.
3. [x] Policy: Allow an Emails rule (+ One-time PIN auth). One-time PIN IdP
     created and enabled on the app (login page shows "Send login code").
     Only one address is in the allow rule so far; add the second when needed.
4. [x] Test from a phone on mobile data (off-LAN): OTP email arrives, app loads
     Gotcha: the LAN resolver (Technitium at 192.168.1.159) may hold a **cached
     NXDOMAIN** from before the tunnel route existed, giving
     `ERR_NAME_NOT_RESOLVED` on devices that use it. Flush Technitium's cache
     (web UI → Cache, or `api/cache/flush`) and toggle Wi-Fi/airplane mode on
     the phone to drop its own cached negative.
     Separately: `requests.davemcbride.org` no longer resolves and no seerr
     container exists on the host — the tunnel now serves only `dinner`.
5. [x] `curl -sI https://dinner.davemcbride.org` → 302 to
     `…cloudflareaccess.com/cdn-cgi/access/login/dinner.davemcbride.org` when
     logged out
6. [x] Update README quick-start / hosting notes to document this
     access method in place of (or alongside) LAN-direct