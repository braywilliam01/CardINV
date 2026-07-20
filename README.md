# MTG Inventory Manager

FastAPI + SQLite app for managing a physical trading card collection —
**Magic: The Gathering and Pokémon**, kept as two completely separate
collections per account — with decklist parsing, fuzzy matching, and deck
checkout/check-in tracking. Multi-user: each account gets its own isolated
data behind a login.

## Features

- **Accounts** — username/password login (bcrypt-hashed, signed session
  cookie). Each user's inventory, decks, prices, and search history are
  fully isolated from every other user's.
- **Two games, kept separate** — a switcher in the side drawer (Magic /
  Pokémon / Everything) swaps your active game; each has its own database
  file, so nothing about one game's collection touches the other's. The
  **Everything** screen shows combined stats across both.
- **Collection Search** — paste a decklist, get it split into "available"
  and "missing" outputs based on current inventory, with fuzzy matching
  for typos (Magic only — see Limitations below).
- **Decks** — one tab for everything deck-related: granular per-card
  add/remove, bulk paste-a-decklist editing, favoriting, renaming, and
  deleting a deck (which checks its cards back into available inventory).
- **Bulk Update** — upload a ManaBox CSV export to replace your entire
  Magic inventory in one shot. Deck assignments are preserved across
  reloads, with warnings surfaced for any assignment left referencing a
  card no longer in your collection.
- **Card Search** — fuzzy lookup for any card's full printed info (image,
  rules text, prices, legalities) — Scryfall for Magic, pokemontcg.io for
  Pokémon — with how many you own and a one-click add to inventory.

## Limitations

- Pokémon card search has weaker typo tolerance than Magic's: Scryfall has
  a dedicated fuzzy-match endpoint, pokemontcg.io doesn't, so Pokémon
  lookups fall back to exact/substring matching plus local ranking.
- "Ignore Basic Lands" and the ManaBox CSV bulk importer are Magic-specific
  concepts with no Pokémon equivalent — they're hidden in Pokémon mode.

## Stack

- Backend: FastAPI + SQLAlchemy
- Database: SQLite — a shared `data/users.db` for accounts, plus one file
  per (user, game) pair at `data/users/<username>/<mtg|pokemon>/inventory.db`
- Auth: bcrypt password hashing + Starlette signed-cookie sessions
- Card data: [Scryfall](https://scryfall.com) (Magic) and
  [pokemontcg.io](https://pokemontcg.io) (Pokémon) — the latter works
  keyless for personal use; set `POKEMONTCG_API_KEY` to raise its rate
  limit from 1,000 to 20,000 requests/day if needed
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
