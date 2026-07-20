# Deployment Runbook — Proxmox LXC (venv + systemd)

Docker is intentionally not used here — Docker-in-LXC requires nesting
(`nesting=1`, sometimes `keyctl=1`) and is redundant overhead for an app
that's still just SQLite files on disk, even with multiple users each
tracking two separate games. Bare venv + systemd sidesteps all of that
and keeps backups to a simple file copy.

## 1. Provision the LXC

- Template: Debian 12 or Ubuntu 24.04
- **Unprivileged** container — nothing here needs elevated host access
- Resources: 1 vCPU, **2GB RAM**, 4–8GB disk. The app itself is light at
  rest, but a Magic price refresh downloads and parses Scryfall's
  `default_cards` bulk file (every printing, 500MB+ gzipped and growing)
  — parsing that into memory can spike well past 512MB-1GB. Don't
  undersize this if you'll use "Refresh All Prices" or the weekly cron
  job in step 9.
- Proxmox UI → container → Options → **Start at boot** → Yes

## 2. Base packages

```bash
apt update && apt install -y python3 python3-venv python3-pip nginx git
```

## 3. Clone and set up the app

```bash
cd /opt
git clone https://github.com/<you>/mtg-inventory.git
cd mtg-inventory

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 4. Set the session secret

The app is multi-user: each account gets its own database (one file per
game it tracks), gated behind a login. Sessions are signed cookies, so a
real secret is required before this goes anywhere reachable — the app runs
with an insecure default and logs a warning if you skip this.

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Put the result in an env file the systemd unit will load (step 6):

```bash
# /opt/mtg-inventory/.env
SESSION_SECRET_KEY=<paste the generated value>
SESSION_HTTPS_ONLY=true
# Optional — raises the Pokemon price-refresh rate limit from 1,000 to
# 20,000 requests/day. Free at https://dev.pokemontcg.io. Not required;
# a full refresh (~82 paginated requests) fits comfortably without it.
# POKEMONTCG_API_KEY=<your key>

# Optional — relocates all SQLite data (default: ./data, i.e.
# /opt/mtg-inventory/data). Useful for putting data on a separate
# mounted volume.
# DATA_DIR=/mnt/mtg-data
# Optional — overrides just the shared accounts database's location/URL
# (default: sqlite:///<DATA_DIR>/users.db). Rarely needed on its own —
# DATA_DIR above already moves this along with everything else.
# AUTH_DATABASE_URL=sqlite:////mnt/mtg-data/users.db
```

`SESSION_HTTPS_ONLY=true` marks the session cookie HTTPS-only — correct once
this sits behind TLS termination (step 7), but leave it unset (defaults to
`false`) if you're sanity-checking over plain `http://127.0.0.1:8000` in the
next step first.

## 5. Sanity check before wiring up systemd

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Visit `http://<lxc-ip>:8000`, register an account, and confirm the app
loads past login. Ctrl+C once confirmed.

**Register your own account first.** The very first account ever created
on a fresh install automatically becomes an admin (Settings → Manage
Users lets an admin reset any other user's password) — nobody who
registers afterward gets this automatically, and there's no way to
promote an account to admin later without editing the database directly.

**Registration is open to anyone who can reach the app** — there's no
invite code, approval step, or login rate limiting. That's fine behind
your own network or a tunnel/proxy you control, which covers the
family/friends scale this is built for; if you're exposing it more
broadly, put access control (e.g. a Cloudflare Access policy) in front
of it.

## 6. Ownership

Make sure the user the service runs as owns the directory (needed to write
into `data/`, which holds the shared accounts database plus one SQLite file
per user per game):

```bash
sudo chown -R www-data:www-data /opt/mtg-inventory
```

## 7. systemd service

Create `/etc/systemd/system/mtg-inventory.service`:

```ini
[Unit]
Description=MTG Inventory Manager
After=network.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=/opt/mtg-inventory
EnvironmentFile=/opt/mtg-inventory/.env
ExecStart=/opt/mtg-inventory/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**Note:** `--workers 1` is intentional. SQLite serializes writers; multiple
uvicorn workers can cause `database is locked` errors under concurrent
write load within a single user's database. Each user's data lives in its
own file, so this only matters for concurrent writes from the *same*
account — still correct to keep at 1 rather than reasoning about WAL mode
tradeoffs for a handful of users.

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mtg-inventory
sudo systemctl status mtg-inventory   # confirm "active (running)"
journalctl -u mtg-inventory -f        # tail logs
```

## 8. Reverse proxy (nginx)

If this LXC has its own IP on your LAN, install nginx here (already done
in step 2) and use:

```nginx
server {
    listen 80;
    server_name mtg.yourdomain.com;

    client_max_body_size 10M;  # ManaBox CSVs can run a few MB for large collections

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

If you're fronting this with an existing reverse-proxy LXC (e.g. nginx
proxy manager or Cloudflare Tunnel), skip installing nginx in this
container and instead:
- Change `ExecStart` above to `--host 0.0.0.0` so uvicorn accepts
  connections from outside the container's loopback.
- Point the existing proxy at `<mtg-lxc-ip>:8000`.

Then apply your usual TLS termination (certbot / Cloudflare) on top.

## 9. Backups

**File-level (granular, daily):**

Everything worth backing up lives under `data/` — the shared accounts
database plus one SQLite file per user per game:

```bash
# /etc/cron.daily/mtg-inventory-backup
#!/bin/bash
mkdir -p /opt/mtg-inventory/backups
tar -czf /opt/mtg-inventory/backups/data_$(date +%F).tar.gz -C /opt/mtg-inventory data
find /opt/mtg-inventory/backups -mtime +30 -delete
```

```bash
chmod +x /etc/cron.daily/mtg-inventory-backup
```

**Weekly price refresh (Scryfall + pokemontcg.io):**

Prices are cached per (user, game) — each user's Magic and Pokemon
databases each have their own `card_prices` table — and only update when
refreshed, either manually from the "Refresh All Prices" button on the
Manage Collection tab, or on a schedule via cron. `/api/pricing/refresh-bulk`
requires a logged-in session *and* only refreshes whichever game is
active in that session (defaults to Magic, same as a brand-new login) —
so refreshing both games means switching games between two refresh calls
on the same cookie jar, not just calling it twice:

```bash
# /etc/cron.weekly/mtg-inventory-price-refresh
#!/bin/bash
# Run once per account you want refreshed automatically.
USERNAME="alice"
PASSWORD="the account's password"

COOKIE_JAR=$(mktemp)
curl -s -c "$COOKIE_JAR" -X POST http://127.0.0.1:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"username\": \"$USERNAME\", \"password\": \"$PASSWORD\"}" > /dev/null

# Magic
curl -s -b "$COOKIE_JAR" -X PUT http://127.0.0.1:8000/api/session/game \
  -H "Content-Type: application/json" -d '{"game": "mtg"}' > /dev/null
curl -s -b "$COOKIE_JAR" -X POST http://127.0.0.1:8000/api/pricing/refresh-bulk > /dev/null

# Pokemon — drop this block if the account doesn't track Pokemon.
curl -s -b "$COOKIE_JAR" -X PUT http://127.0.0.1:8000/api/session/game \
  -H "Content-Type: application/json" -d '{"game": "pokemon"}' > /dev/null
curl -s -b "$COOKIE_JAR" -X POST http://127.0.0.1:8000/api/pricing/refresh-bulk > /dev/null

rm -f "$COOKIE_JAR"
```

```bash
chmod 700 /etc/cron.weekly/mtg-inventory-price-refresh   # contains a password
```

For more than one account, either repeat the whole block per user in the
same script, or give each user their own cron entry. This hits the app's
own API rather than calling the card data providers directly from cron,
so it reuses the same matching logic as the in-app button.

The Magic refresh is a single bulk download from Scryfall regardless of
collection size, but it's larger than it looks: pricing is tracked per
*printing*, not deduplicated by name, so the app downloads Scryfall's
`default_cards` file — every printing of every card, currently **500MB+
gzipped and growing**. Expect it to take anywhere from a minute to
several minutes depending on the LXC's connection, and to briefly use
well over 1GB of RAM while it's parsed (see the RAM note in step 1) —
that memory spike is expected for this specific request, not a leak. The
Pokemon refresh has no bulk-download equivalent — pokemontcg.io doesn't
publish one — so it instead paginates the full catalog (~82 requests),
which is slower per card but never spikes memory the way the Magic
refresh does. Both are expected and fine for an unattended weekly job.
If you'd rather refresh more or less often, adjust by moving the script
to `/etc/cron.daily/` or a custom crontab entry instead of `cron.weekly`.

**Checking refresh progress server-side:** while a refresh is running
(triggered by cron, the "Refresh All Prices" button, or a manual curl),
you can watch it from either angle:

```bash
# Live logs — shows each stage (fetching index, downloading, matching, committing)
journalctl -u mtg-inventory -f

# Or poll the status endpoint directly (needs a logged-in session with the
# same game active as the refresh you're checking on — reuse a cookie jar
# from an authenticated login, and set the game first if it's not Magic)
curl -s -b "$COOKIE_JAR" http://127.0.0.1:8000/api/pricing/status | python3 -m json.tool
```

The status endpoint reports `in_progress`, the current `stage`, a
`cards_processed` / `total_cards_in_file` counter while matching is
underway, and the result (or error) of the most recent run — for
*whichever game is active in that session*. Useful for confirming the
weekly cron job actually completed without having to dig through logs.

**Container-level (whole-LXC disaster recovery):**

Proxmox Datacenter → Backup → schedule `vzdump` snapshots for this LXC.
Use both — vzdump for full-container recovery, the cron copy for
restoring an individual day's DB without touching the whole container.

## 10. Verify end-to-end

- [ ] `systemctl status mtg-inventory` shows active
- [ ] App loads via the reverse-proxy URL, not just `127.0.0.1:8000`
- [ ] `SESSION_SECRET_KEY` is set — no warning about the insecure default in `journalctl -u mtg-inventory`
- [ ] The account you registered first is the admin (Settings → Manage Users shows a user list)
- [ ] Registering a new account works, and its data is isolated from any other account (`data/users/<name>/<mtg|pokemon>/inventory.db` is a separate file per user per game)
- [ ] Logging out and back in preserves that account's data
- [ ] `/healthz` returns `{"status": "ok"}` without needing a session
- [ ] The drawer's Magic/Pokemon/Everything switcher works, and each game's data stays isolated from the other's
- [ ] Homepage, Manage Collection (including Bulk Update), Decks, Collection Search, Card Search, and Settings all load once logged in
- [ ] Cron backup script is executable and cron.daily picks it up
- [ ] Weekly price-refresh cron script is executable, and covers Pokemon too if the account tracks it
- [ ] LXC "Start at boot" is enabled in Proxmox UI
