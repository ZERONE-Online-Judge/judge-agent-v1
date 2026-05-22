#!/usr/bin/env bash
set -euo pipefail

###############################################################################
# Zerone Judge Agent Installer (Ubuntu, repo already cloned)
#
# Usage:
#   1) Clone repository
#   2) cd judge-agent-v1/deploy
#   3) Edit configuration values below
#   4) sudo bash install.sh
#
# What this script does:
#   - apt update / install required packages
#   - install docker / docker compose
#   - create/update env file
#   - docker compose up -d --build
###############################################################################

### ====== USER CONFIG ======
INTERNAL_API_BASE_URL="https://judge.zerone01.kr/api"
JUDGE_NODE_NAME="zoj-judge-agent-0"
JUDGE_NODE_SECRET=""

JUDGE_TOTAL_SLOTS="8"
JUDGE_AGENT_CONTAINER_CPUS="10"
JUDGE_AGENT_CONTAINER_MEMORY="20g"
### ========================

if [[ $EUID -ne 0 ]]; then
  echo "[ERROR] Run this script with sudo/root."
  exit 1
fi

log() { echo "[bootstrap] $*"; }

ensure_docker_systemd() {
  log "Resetting docker systemd units"
  systemctl unmask docker.service docker.socket || true
  systemctl daemon-reload
  systemctl reset-failed docker.service docker.socket || true

  log "Enabling docker.socket first"
  systemctl enable --now docker.socket

  log "Enabling docker.service"
  systemctl enable --now docker.service
}

generate_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
    return
  fi
  tr -dc 'A-Za-z0-9' </dev/urandom | head -c 64
}

if [[ -z "$JUDGE_NODE_SECRET" || "$JUDGE_NODE_SECRET" == "change-me-very-strong-secret" ]]; then
  JUDGE_NODE_SECRET="$(generate_secret)"
  echo "[bootstrap] JUDGE_NODE_SECRET not set -> generated automatically"
fi

upsert_env() {
  local file="$1"
  local key="$2"
  local value="$3"
  local escaped
  escaped="$(printf '%s' "$value" | sed 's/[\/&]/\\&/g')"
  if grep -qE "^${key}=" "$file"; then
    sed -i "s/^${key}=.*/${key}=${escaped}/" "$file"
  else
    echo "${key}=${value}" >> "$file"
  fi
}

log "apt update / install base packages"
apt-get update
apt-get install -y ca-certificates curl gnupg lsb-release git

if ! docker compose version >/dev/null 2>&1; then
  log "Registering Docker official apt repository"
  install -m 0755 -d /etc/apt/keyrings
  if [[ ! -f /etc/apt/keyrings/docker.gpg ]]; then
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
  fi

  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list

  apt-get update
  log "Installing docker-ce and compose v2"
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "[ERROR] Failed to install docker compose v2."
  exit 1
fi

ensure_docker_systemd

if ! systemctl is-active --quiet docker.socket; then
  echo "[ERROR] docker.socket is not active."
  systemctl status docker.socket --no-pager -l || true
  exit 1
fi

if ! systemctl is-active --quiet docker.service; then
  echo "[ERROR] docker.service is not active."
  systemctl status docker.service --no-pager -l || true
  exit 1
fi

TARGET_USER="${SUDO_USER:-root}"
if id -u "$TARGET_USER" >/dev/null 2>&1; then
  usermod -aG docker "$TARGET_USER" || true
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_DIR="$SCRIPT_DIR/env"
ENV_EXAMPLE="$ENV_DIR/judge-agent.env.example"
ENV_FILE="$ENV_DIR/judge-agent.env"
COMPOSE_FILE="$SCRIPT_DIR/compose.yaml"

if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "[ERROR] Compose file not found: $COMPOSE_FILE"
  exit 1
fi

mkdir -p "$ENV_DIR"
if [[ ! -f "$ENV_EXAMPLE" ]]; then
  echo "[ERROR] Env example file not found: $ENV_EXAMPLE"
  exit 1
fi
if [[ ! -f "$ENV_FILE" ]]; then
  cp "$ENV_EXAMPLE" "$ENV_FILE"
fi
chmod 600 "$ENV_FILE"

log "Configuring judge-agent env"
upsert_env "$ENV_FILE" "APP_ENV" "production"
upsert_env "$ENV_FILE" "INTERNAL_API_BASE_URL" "$INTERNAL_API_BASE_URL"
upsert_env "$ENV_FILE" "JUDGE_NODE_NAME" "$JUDGE_NODE_NAME"
upsert_env "$ENV_FILE" "JUDGE_NODE_SECRET" "$JUDGE_NODE_SECRET"
upsert_env "$ENV_FILE" "JUDGE_TOTAL_SLOTS" "$JUDGE_TOTAL_SLOTS"
upsert_env "$ENV_FILE" "JUDGE_AGENT_CONTAINER_CPUS" "$JUDGE_AGENT_CONTAINER_CPUS"
upsert_env "$ENV_FILE" "JUDGE_AGENT_CONTAINER_MEMORY" "$JUDGE_AGENT_CONTAINER_MEMORY"
upsert_env "$ENV_FILE" "JUDGE_WORK_ROOT" "/var/lib/zerone-judge"
upsert_env "$ENV_FILE" "JUDGE_SANDBOX_MODE" "isolate"
upsert_env "$ENV_FILE" "JUDGE_CHECKER_SANDBOX_MODE" "isolate"
upsert_env "$ENV_FILE" "JUDGE_SANDBOX_MEMORY_MB" "512"
upsert_env "$ENV_FILE" "JUDGE_SANDBOX_PIDS_LIMIT" "128"
upsert_env "$ENV_FILE" "JUDGE_ISOLATE_BOX_ID_BASE" "0"
upsert_env "$ENV_FILE" "JUDGE_ISOLATE_BOX_ID_COUNT" "1000"
upsert_env "$ENV_FILE" "JUDGE_OUTPUT_LIMIT_BYTES" "1048576"
upsert_env "$ENV_FILE" "JUDGE_LONG_POLL_SECONDS" "20"
upsert_env "$ENV_FILE" "JUDGE_POLL_INTERVAL_SECONDS" "1"

mkdir -p /var/lib/zerone-judge
chown -R root:root /var/lib/zerone-judge

log "Starting judge-agent with docker compose"
cd "$SCRIPT_DIR"
docker compose -f "$COMPOSE_FILE" up -d --build

log "Done"
docker compose -f "$COMPOSE_FILE" ps
echo
echo "Check logs:"
echo "  docker compose -f $COMPOSE_FILE logs -f judge-agent"
echo "  (node secret: $JUDGE_NODE_SECRET)"
echo
echo "Backend connectivity check:"
echo "  curl -sS ${INTERNAL_API_BASE_URL%/api}/api/public/judge-status"
