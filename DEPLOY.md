# Deployment Runbook — Proxmox LXC (venv + systemd)

Docker is intentionally not used here — Docker-in-LXC requires nesting
(`nesting=1`, sometimes `keyctl=1`) and is redundant overhead for a
single-app single-user tool. Bare venv + systemd sidesteps all of that
and keeps backups to a simple file copy.

## 1. Provision the LXC

- Template: Debian 12 or Ubuntu 24.04
- **Unprivileged** container — nothing here needs elevated host access
- Resources: 1 vCPU, 512MB–1GB RAM, 4–8GB disk is plenty
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

## 4. Sanity check before wiring up systemd

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Visit `http://<lxc-ip>:8000` and confirm all three tabs load. Ctrl+C once confirmed.

## 5. Ownership

Make sure the user the service runs as owns the directory (needed to write
`mtg_inventory.db`):

```bash
sudo chown -R www-data:www-data /opt/mtg-inventory
```

## 6. systemd service

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
ExecStart=/opt/mtg-inventory/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**Note:** `--workers 1` is intentional. SQLite serializes writers; multiple
uvicorn workers can cause `database is locked` errors under concurrent
write load. For a single-user tool, 1 worker is correct and simpler than
reasoning about WAL mode tradeoffs.

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mtg-inventory
sudo systemctl status mtg-inventory   # confirm "active (running)"
journalctl -u mtg-inventory -f        # tail logs
```

## 7. Reverse proxy (nginx)

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

## 8. Backups

**File-level (granular, daily):**

```bash
# /etc/cron.daily/mtg-inventory-backup
#!/bin/bash
mkdir -p /opt/mtg-inventory/backups
cp /opt/mtg-inventory/mtg_inventory.db /opt/mtg-inventory/backups/mtg_inventory_$(date +%F).db
find /opt/mtg-inventory/backups -mtime +30 -delete
```

```bash
chmod +x /etc/cron.daily/mtg-inventory-backup
```

**Weekly price refresh (Scryfall):**

Card values are cached in the database and only update when you trigger
a refresh — either manually from the "Refresh All Prices" button on the
Manage Collection tab, or automatically on a schedule via cron:

```bash
# /etc/cron.weekly/mtg-inventory-price-refresh
#!/bin/bash
curl -s -X POST http://127.0.0.1:8000/api/pricing/refresh-bulk
```

```bash
chmod +x /etc/cron.weekly/mtg-inventory-price-refresh
```

This hits the app's own API rather than calling Scryfall directly from
cron, so it reuses the same matching logic as the in-app button. It's a
single bulk download from Scryfall regardless of collection size
(Scryfall's oracle_cards file — one row per unique card — typically
runs 100-150MB gzipped, so the full request usually takes anywhere
from 15 seconds to a couple of minutes depending on the LXC's
connection). That's expected and fine for an unattended weekly job. If
you'd rather refresh more or less often, adjust by moving the script
to `/etc/cron.daily/` or a custom crontab entry instead of
`cron.weekly`.

**Checking refresh progress server-side:** while a refresh is running
(triggered by cron, the "Refresh All Prices" button, or a manual curl),
you can watch it from either angle:

```bash
# Live logs — shows each stage (fetching index, downloading, matching, committing)
journalctl -u mtg-inventory -f

# Or poll the status endpoint directly, e.g. from a second terminal
curl -s http://127.0.0.1:8000/api/pricing/status | python3 -m json.tool
```

The status endpoint reports `in_progress`, the current `stage`, a
`cards_processed` / `total_cards_in_file` counter while matching is
underway, and the result (or error) of the most recent run — useful
for confirming the weekly cron job actually completed without having
to dig through logs.

**Container-level (whole-LXC disaster recovery):**

Proxmox Datacenter → Backup → schedule `vzdump` snapshots for this LXC.
Use both — vzdump for full-container recovery, the cron copy for
restoring an individual day's DB without touching the whole container.

## 9. Verify end-to-end

- [ ] `systemctl status mtg-inventory` shows active
- [ ] App loads via the reverse-proxy URL, not just `127.0.0.1:8000`
- [ ] All three tabs functional (search, checkout/check-in, bulk upload)
- [ ] Cron backup script is executable and cron.daily picks it up
- [ ] LXC "Start at boot" is enabled in Proxmox UI
