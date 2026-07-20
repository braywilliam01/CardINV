# MTG Inventory Manager

FastAPI + SQLite app for managing physical MTG card inventory, decklist
parsing with fuzzy matching, and deck checkout/check-in tracking.

## Features

- **Collection Search** — paste a decklist, get it split into "available"
  and "missing" outputs based on current inventory, with fuzzy matching
  for typos.
- **Deck Checkout / Check-In** — assign cards to named decks and return
  them to the available pool, with partial-fulfillment support and
  per-line status feedback.
- **Bulk Update** — upload a ManaBox CSV export to replace your entire
  inventory in one shot. Deck assignments are preserved across reloads,
  with warnings surfaced for any assignment left referencing a card no
  longer in your collection.

## Stack

- Backend: FastAPI + SQLAlchemy
- Database: SQLite (single file, `mtg_inventory.db`)
- Frontend: vanilla HTML/JS + Tailwind (via CDN), no build step
- Fuzzy matching: rapidfuzz

## Local development

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Visit `http://localhost:8000`.

## Deployment

See `DEPLOY.md` for the full runbook — targets a Proxmox LXC (unprivileged,
Debian/Ubuntu) with venv + systemd + nginx.
