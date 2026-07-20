# MTG Inventory Manager

FastAPI + SQLite app for managing physical MTG card inventory, decklist
parsing with fuzzy matching, and deck checkout/check-in tracking. Multi-user:
each account gets its own isolated database behind a login.

## Features

- **Accounts** — username/password login (bcrypt-hashed, signed session
  cookie). Each user's inventory, decks, prices, and search history live in
  their own SQLite file — nothing is shared between accounts.
- **Collection Search** — paste a decklist, get it split into "available"
  and "missing" outputs based on current inventory, with fuzzy matching
  for typos.
- **Decks** — one tab for everything deck-related: granular per-card
  add/remove, bulk paste-a-decklist editing, favoriting, renaming, and
  deleting a deck (which checks its cards back into available inventory).
- **Bulk Update** — upload a ManaBox CSV export to replace your entire
  inventory in one shot. Deck assignments are preserved across reloads,
  with warnings surfaced for any assignment left referencing a card no
  longer in your collection.
- **Card Search** — fuzzy Scryfall lookup for any card's full printed info
  (image, oracle text, prices, legalities), with how many you own and a
  one-click add to inventory.

## Stack

- Backend: FastAPI + SQLAlchemy
- Database: SQLite — a shared `data/users.db` for accounts, plus one file per
  user at `data/users/<username>/mtg_inventory.db`
- Auth: bcrypt password hashing + Starlette signed-cookie sessions
- Frontend: vanilla HTML/JS + Tailwind, compiled to a static `static/app.css`
  (not the CDN build — the Play CDN script is fine for local dev but silently
  produces an unstyled page if anything on the network path, e.g. a reverse
  proxy's CSP header, blocks that third-party request)
- Fuzzy matching: rapidfuzz

## Local development

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Visit `http://localhost:8000` and register an account — that's the
first thing you'll see.

Running without a `SESSION_SECRET_KEY` env var logs a warning and falls
back to an insecure dev default; that's fine locally but must be set to a
real random value before deploying anywhere reachable (see `DEPLOY.md`).

### Rebuilding CSS

`static/app.css` is a compiled, static file — it is **not** regenerated
automatically, so if you add a Tailwind class to `static/index.html` or
`static/app.js` that isn't already used elsewhere, rebuild it:

```bash
curl -fsSL -o /tmp/tailwindcss https://github.com/tailwindlabs/tailwindcss/releases/latest/download/tailwindcss-linux-x64
chmod +x /tmp/tailwindcss
/tmp/tailwindcss -i static/tailwind-input.css -o static/app.css --minify
```

(swap `tailwindcss-linux-x64` for your platform's binary name from the
[releases page](https://github.com/tailwindlabs/tailwindcss/releases/latest)
if you're not on Linux x64)

## Deployment

See `DEPLOY.md` for the full runbook — targets a Proxmox LXC (unprivileged,
Debian/Ubuntu) with venv + systemd + nginx.
