# MTG Inventory Manager

FastAPI + SQLite app for managing a physical trading card collection —
**Magic: The Gathering and Pokémon**, kept as two completely separate
collections per account — with decklist parsing, fuzzy matching, and deck
checkout/check-in tracking. Multi-user: each account gets its own isolated
data behind a login.

## Features

- **Accounts** — username/password login (bcrypt-hashed, signed session
  cookie). Each user's inventory, decks, prices, and search history are
  fully isolated from every other user's. The first account ever
  registered becomes an admin, who can reset any other user's password
  from Settings; every account can change its own password there too.
- **Two games, kept separate** — a switcher in the side drawer (Magic /
  Pokémon / Everything) swaps your active game; each has its own database
  file, so nothing about one game's collection touches the other's. The
  **Everything** screen shows combined stats across both.
- **Per-printing tracking** — inventory is keyed by card name plus
  (optionally) set and collector number, so the same card across
  different printings is tracked separately with its own quantity and
  price. Copies without a known printing sit in an "unresolved" bucket
  until you assign them to a specific printing via Manage Collection's
  fix-up workflow — nothing is ever guessed.
- **Manage Collection** — a grouped table (one row per card name,
  expandable to its individual printings), quantity edits and price
  lookups at either the card or printing level, and the fix-up flow for
  resolving unresolved copies to a specific printing.
- **Per-printing pricing** — Magic prices come from Scryfall's full
  per-printing bulk data; Pokémon prices from pokemontcg.io. An
  unresolved bucket gets an *estimated* price (the cheapest known
  printing of that name) instead of pretending to know which printing
  it is — estimated prices are flagged as such everywhere they appear.
- **Collection Search** — paste a decklist, get it split into "available"
  and "missing" outputs based on current inventory, with fuzzy matching
  for typos (Magic only — see Limitations below).
- **Decks** — one tab for everything deck-related: granular per-card
  add/remove, bulk paste-a-decklist editing, favoriting, renaming, and
  deleting a deck (which checks its cards back into available inventory).
  Checkout/check-in is printing-aware: pin an exact printing with a
  trailing `(SET) NUM` (e.g. `4 Lightning Bolt (CLB) 304`), or leave a
  line unpinned and it draws from the cheapest known printing first,
  keeping pricier copies on the shelf. A deck's contents round-trip
  through the bulk-edit box in that same format — load, edit, paste back.
- **Bulk Update** — upload a ManaBox CSV export to reconcile your Magic
  inventory against it, printing by printing: printings in the file are
  added or updated, printings no longer in the file are removed, and (if
  the export includes Set code / Collector number columns) everything is
  tracked per printing instead of lumped into one bucket per card. Deck
  assignments are always preserved, with warnings surfaced for any
  assignment left short after the reconciliation.
- **Card Search** — fuzzy lookup for any card's full printed info (image,
  rules text, prices, legalities) — Scryfall for Magic, pokemontcg.io for
  Pokémon — showing exactly how many of that specific printing you own,
  with a one-click add to inventory.

## Limitations

- Pokémon card search has weaker typo tolerance than Magic's: Scryfall has
  a dedicated fuzzy-match endpoint, pokemontcg.io doesn't, so Pokémon
  lookups fall back to exact/substring matching plus local ranking.
- "Ignore Basic Lands" and the ManaBox CSV bulk importer are Magic-specific
  concepts with no Pokémon equivalent — they're hidden in Pokémon mode.
- Registration is open to anyone who can reach the app — there's no invite
  code, approval step, or login rate limiting. Fine behind your own
  network or a tunnel you control; put access control in front of it
  (e.g. a Cloudflare Access policy) if exposing it more broadly. See
  `DEPLOY.md`.

## Stack

- Backend: FastAPI + SQLAlchemy
- Database: SQLite — a shared `data/users.db` for accounts, plus one file
  per (user, game) pair at `data/users/<username>/<mtg|pokemon>/inventory.db`
- Auth: bcrypt password hashing + Starlette signed-cookie sessions
- Card data: [Scryfall](https://scryfall.com) (Magic) and
  [pokemontcg.io](https://pokemontcg.io) (Pokémon) — the latter works
  keyless for personal use; set `POKEMONTCG_API_KEY` to raise its rate
  limit from 1,000 to 20,000 requests/day if needed. Magic pricing pulls
  Scryfall's `default_cards` bulk file (every printing, 500MB+ gzipped) —
  see `DEPLOY.md` for the memory/timing implications of that
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
first thing you'll see. (That first account becomes the admin — see
Features above.)

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
