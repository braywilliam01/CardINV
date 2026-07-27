#!/usr/bin/env bash
# Wipes one user's collection for one game on a running CardINV instance,
# via the app's own public API (no direct database access needed/used).
# Deletes every deck (checking cards back in first) and every inventory
# card (all its printings/finishes, force=true) for that user+game.
# Does NOT touch the account/login, the other game's collection, or
# "Last Viewed" search history (no API to clear that; harmless, it's
# just a rolling 3-entry cache that overwrites itself with use).
#
# Usage:
#   BASE_URL="https://tcg.williambray.top" CARDINV_USERNAME="Will" GAME="mtg" ./scripts/wipe_collection.sh
# Password is read via a hidden prompt, not an env var, so it never ends
# up in shell history.
set -euo pipefail

BASE_URL="${BASE_URL:-https://tcg.williambray.top}"
USERNAME="${CARDINV_USERNAME:?Set CARDINV_USERNAME to the account to wipe}"
GAME="${GAME:?Set GAME to mtg or pokemon}"

if [[ "$GAME" != "mtg" && "$GAME" != "pokemon" ]]; then
  echo "GAME must be 'mtg' or 'pokemon', got '$GAME'" >&2
  exit 1
fi

read -rsp "Password for '$USERNAME': " PASSWORD
echo

COOKIE_JAR="$(mktemp)"
trap 'rm -f "$COOKIE_JAR"' EXIT

echo "Logging in as '$USERNAME'..."
login_status=$(curl -s -o /dev/null -w "%{http_code}" -c "$COOKIE_JAR" \
  -X POST "$BASE_URL/api/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"$USERNAME\",\"password\":\"$PASSWORD\"}")
if [[ "$login_status" != "200" ]]; then
  echo "Login failed (HTTP $login_status) -- check the username/password." >&2
  exit 1
fi

curl -s -b "$COOKIE_JAR" -c "$COOKIE_JAR" -X PUT "$BASE_URL/api/session/game" \
  -H "Content-Type: application/json" -d "{\"game\":\"$GAME\"}" > /dev/null

card_count=$(curl -s -b "$COOKIE_JAR" "$BASE_URL/api/inventory/names" | python3 -c "import json,sys; print(len(json.load(sys.stdin)['card_names']))")
deck_count=$(curl -s -b "$COOKIE_JAR" "$BASE_URL/api/decks" | python3 -c "import json,sys; print(len(json.load(sys.stdin)['decks']))")

echo
echo "About to permanently delete, for '$USERNAME' / $GAME:"
echo "  - $card_count card(s) in inventory (every printing/finish)"
echo "  - $deck_count deck(s) (cards checked back in first, then the deck removed)"
echo "This cannot be undone through the app. Ctrl-C now to abort."
read -rp "Type the username again to confirm ($USERNAME): " CONFIRM
if [[ "$CONFIRM" != "$USERNAME" ]]; then
  echo "Confirmation didn't match -- aborting, nothing was deleted." >&2
  exit 1
fi

echo
echo "Deleting decks..."
curl -s -b "$COOKIE_JAR" "$BASE_URL/api/decks" | python3 -c "import json,sys; print('\n'.join(json.load(sys.stdin)['decks']))" |
while IFS= read -r deck; do
  [[ -z "$deck" ]] && continue
  encoded=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$deck")
  status=$(curl -s -o /dev/null -w "%{http_code}" -b "$COOKIE_JAR" -X DELETE "$BASE_URL/api/decks/$encoded")
  echo "  [$status] $deck"
done

echo "Deleting inventory cards..."
curl -s -b "$COOKIE_JAR" "$BASE_URL/api/inventory/names" | python3 -c "import json,sys; print('\n'.join(json.load(sys.stdin)['card_names']))" |
while IFS= read -r card; do
  [[ -z "$card" ]] && continue
  encoded=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$card")
  status=$(curl -s -o /dev/null -w "%{http_code}" -b "$COOKIE_JAR" -X DELETE "$BASE_URL/api/inventory?card_name=$encoded&force=true")
  echo "  [$status] $card"
done

echo
remaining_cards=$(curl -s -b "$COOKIE_JAR" "$BASE_URL/api/inventory/names" | python3 -c "import json,sys; print(len(json.load(sys.stdin)['card_names']))")
remaining_decks=$(curl -s -b "$COOKIE_JAR" "$BASE_URL/api/decks" | python3 -c "import json,sys; print(len(json.load(sys.stdin)['decks']))")
echo "Done. Remaining for '$USERNAME' / $GAME: $remaining_cards card(s), $remaining_decks deck(s)."
