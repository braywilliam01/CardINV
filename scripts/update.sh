#!/bin/bash
#
# CardINV update script — stops the service, pulls the latest code from
# git, installs any new/changed Python dependencies (the app's own
# requirements.txt, plus requirements-mcp.txt if mcp_server.py is in use),
# restores file ownership, and restarts the service. Verifies it actually
# came back up before exiting.
#
# Usage (on the LXC): sudo /opt/CardINV/scripts/update.sh
#
# Must run as root (or via sudo) — it controls the systemd service and
# resets ownership back to www-data at the end regardless of which user
# git/pip ran as during the update, so a bad ownership reset can't leave
# the app unable to write to data/ (see DEPLOY.md's troubleshooting
# notes for what that looks like).

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/CardINV}"
SERVICE="${SERVICE:-CardINV}"
SERVICE_USER="${SERVICE_USER:-www-data}"
BRANCH="${BRANCH:-main}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

if [[ $EUID -ne 0 ]]; then
  echo "This script must be run as root (try: sudo $0)" >&2
  exit 1
fi

cd "$APP_DIR"

log "Checking for uncommitted local changes in $APP_DIR..."
if [[ -n "$(git status --porcelain)" ]]; then
  echo "" >&2
  echo "Refusing to update: $APP_DIR has uncommitted local changes:" >&2
  git status --short >&2
  echo "" >&2
  echo "Commit, stash, or discard them first, then re-run this script." >&2
  exit 1
fi

OLD_HEAD=$(git rev-parse HEAD)

log "Stopping $SERVICE..."
systemctl stop "$SERVICE"

log "Pulling latest changes from origin/$BRANCH..."
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

NEW_HEAD=$(git rev-parse HEAD)
if [[ "$OLD_HEAD" == "$NEW_HEAD" ]]; then
  log "Already up to date — no new commits."
else
  log "Updated $OLD_HEAD -> $NEW_HEAD:"
  git log --oneline "$OLD_HEAD..$NEW_HEAD"
fi

if [[ ! -f "$APP_DIR/venv/bin/activate" ]]; then
  log "venv missing at $APP_DIR/venv — creating it..."
  python3 -m venv "$APP_DIR/venv"
fi

log "Installing/updating Python dependencies..."
"$APP_DIR/venv/bin/pip" install --upgrade pip --quiet
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

if [[ -f "$APP_DIR/requirements-mcp.txt" ]]; then
  log "Installing/updating MCP server dependencies (requirements-mcp.txt)..."
  "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements-mcp.txt"
fi

log "Restoring ownership to $SERVICE_USER:$SERVICE_USER..."
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"

log "Starting $SERVICE..."
systemctl start "$SERVICE"

sleep 2
if systemctl is-active --quiet "$SERVICE"; then
  log "$SERVICE is active."
  git log -1 --format="  Now running commit %h: %s"
else
  echo "" >&2
  echo "$SERVICE failed to start. Recent logs:" >&2
  journalctl -u "$SERVICE" -n 30 --no-pager >&2
  exit 1
fi
