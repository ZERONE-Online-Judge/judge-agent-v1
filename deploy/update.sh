#!/usr/bin/env bash
set -euo pipefail

# Judge Agent update helper
# - git pull latest
# - docker compose rebuild/restart
#
# Usage:
#   cd ~/judge-agent-v1/deploy
#   ./update.sh
#
# Options:
#   --stash         stash local changes before pull, then try pop
#   --discard-local discard local changes before pull (dangerous)
#   --branch <name> pull specific branch (default: current branch)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/compose.yaml"

MODE="abort"
TARGET_BRANCH=""

log() { echo "[update] $*"; }
err() { echo "[update][ERROR] $*" >&2; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --stash)
      MODE="stash"
      shift
      ;;
    --discard-local)
      MODE="discard"
      shift
      ;;
    --branch)
      TARGET_BRANCH="${2:-}"
      if [[ -z "$TARGET_BRANCH" ]]; then
        err "--branch requires a branch name"
        exit 1
      fi
      shift 2
      ;;
    *)
      err "Unknown option: $1"
      exit 1
      ;;
  esac
done

if ! command -v git >/dev/null 2>&1; then
  err "git not found"
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  err "docker not found"
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  err "docker compose v2 not found"
  exit 1
fi

if [[ ! -f "$COMPOSE_FILE" ]]; then
  err "compose file not found: $COMPOSE_FILE"
  exit 1
fi

cd "$REPO_DIR"
if [[ ! -d .git ]]; then
  err "not a git repository: $REPO_DIR"
  exit 1
fi

if [[ "$MODE" == "discard" ]]; then
  log "Discarding local changes"
  git reset --hard HEAD
  git clean -fd
elif [[ "$MODE" == "stash" ]]; then
  if [[ -n "$(git status --porcelain)" ]]; then
    log "Stashing local changes"
    git stash push -u -m "judge-agent update $(date -u +%Y%m%dT%H%M%SZ)"
    STASHED=1
  else
    STASHED=0
  fi
else
  if [[ -n "$(git status --porcelain)" ]]; then
    err "Local changes detected. Re-run with --stash or --discard-local."
    git status --short
    exit 1
  fi
fi

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
BRANCH="${TARGET_BRANCH:-$CURRENT_BRANCH}"

log "Fetching origin"
git fetch origin

if [[ "$CURRENT_BRANCH" != "$BRANCH" ]]; then
  log "Checking out branch: $BRANCH"
  git checkout "$BRANCH"
fi

log "Pulling latest from origin/$BRANCH"
git pull --rebase origin "$BRANCH"

if [[ "${STASHED:-0}" == "1" ]]; then
  log "Restoring stashed changes"
  if ! git stash pop; then
    err "stash pop had conflicts. Resolve manually."
    exit 1
  fi
fi

cd "$SCRIPT_DIR"
log "Rebuilding and restarting judge-agent"
docker compose -f "$COMPOSE_FILE" up -d --build

log "Service status"
docker compose -f "$COMPOSE_FILE" ps

log "Recent logs (last 80 lines)"
docker compose -f "$COMPOSE_FILE" logs --tail=80 judge-agent || true

log "Done"
