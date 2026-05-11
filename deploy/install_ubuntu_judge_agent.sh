#!/usr/bin/env bash
set -euo pipefail

# Ubuntu judge-agent bootstrap script
# - apt update / base packages
# - Docker + compose install
# - repo clone or pull
# - env generation for judge-agent
# - docker compose up -d --build
#
# Usage:
#   sudo bash install_ubuntu_judge_agent.sh \
#     --repo-url https://github.com/<org>/<repo>.git \
#     --api-base-url https://judge.zerone01.kr/api \
#     --node-name judge-vm-01 \
#     --node-secret 'very-strong-secret'
#
# Optional:
#   --repo-dir /opt/zerone_online_judge
#   --branch main
#   --slots 8
#   --agent-cpus 10
#   --agent-memory 20g

REPO_URL=""
REPO_DIR="/opt/zerone_online_judge"
REPO_BRANCH="main"
API_BASE_URL=""
NODE_NAME="$(hostname -s)"
NODE_SECRET=""
SLOTS="8"
AGENT_CPUS="10"
AGENT_MEMORY="20g"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  sed -n '1,40p' "$0"
  exit 0
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-url) REPO_URL="${2:-}"; shift 2 ;;
    --repo-dir) REPO_DIR="${2:-}"; shift 2 ;;
    --branch) REPO_BRANCH="${2:-}"; shift 2 ;;
    --api-base-url) API_BASE_URL="${2:-}"; shift 2 ;;
    --node-name) NODE_NAME="${2:-}"; shift 2 ;;
    --node-secret) NODE_SECRET="${2:-}"; shift 2 ;;
    --slots) SLOTS="${2:-}"; shift 2 ;;
    --agent-cpus) AGENT_CPUS="${2:-}"; shift 2 ;;
    --agent-memory) AGENT_MEMORY="${2:-}"; shift 2 ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

if [[ -z "$REPO_URL" ]]; then
  echo "ERROR: --repo-url is required" >&2
  exit 1
fi
if [[ -z "$API_BASE_URL" ]]; then
  echo "ERROR: --api-base-url is required (e.g. https://judge.zerone01.kr/api)" >&2
  exit 1
fi
if [[ -z "$NODE_SECRET" ]]; then
  echo "ERROR: --node-secret is required" >&2
  exit 1
fi

if [[ $EUID -ne 0 ]]; then
  echo "ERROR: run this script with sudo/root" >&2
  exit 1
fi

TARGET_USER="${SUDO_USER:-root}"

log() { echo "[judge-agent-install] $*"; }

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

log "Updating apt and installing base packages"
apt-get update
apt-get install -y ca-certificates curl gnupg lsb-release git

if ! command -v docker >/dev/null 2>&1; then
  log "Installing Docker engine"
  apt-get install -y docker.io || true
fi

if ! docker compose version >/dev/null 2>&1; then
  log "Installing Docker compose plugin"
  apt-get install -y docker-compose-plugin || true
fi

if ! docker compose version >/dev/null 2>&1; then
  log "Installing docker-compose fallback"
  apt-get install -y docker-compose
fi

log "Enabling Docker service"
systemctl enable --now docker

if id -u "$TARGET_USER" >/dev/null 2>&1; then
  usermod -aG docker "$TARGET_USER" || true
fi

if [[ -d "$REPO_DIR/.git" ]]; then
  log "Repository exists, pulling latest (${REPO_BRANCH})"
  git -C "$REPO_DIR" fetch --all --tags
  git -C "$REPO_DIR" checkout "$REPO_BRANCH"
  git -C "$REPO_DIR" pull --ff-only origin "$REPO_BRANCH"
else
  log "Cloning repository to $REPO_DIR"
  git clone --branch "$REPO_BRANCH" "$REPO_URL" "$REPO_DIR"
fi

ENV_DIR="$REPO_DIR/judge_agent/deploy/env"
ENV_FILE="$ENV_DIR/judge-agent.env"
ENV_EXAMPLE="$ENV_DIR/judge-agent.env.example"

mkdir -p "$ENV_DIR"
if [[ ! -f "$ENV_EXAMPLE" ]]; then
  echo "ERROR: env example not found: $ENV_EXAMPLE" >&2
  exit 1
fi
if [[ ! -f "$ENV_FILE" ]]; then
  cp "$ENV_EXAMPLE" "$ENV_FILE"
fi

log "Configuring judge-agent env"
upsert_env "$ENV_FILE" "APP_ENV" "production"
upsert_env "$ENV_FILE" "INTERNAL_API_BASE_URL" "$API_BASE_URL"
upsert_env "$ENV_FILE" "JUDGE_NODE_NAME" "$NODE_NAME"
upsert_env "$ENV_FILE" "JUDGE_NODE_SECRET" "$NODE_SECRET"
upsert_env "$ENV_FILE" "JUDGE_TOTAL_SLOTS" "$SLOTS"
upsert_env "$ENV_FILE" "JUDGE_AGENT_CONTAINER_CPUS" "$AGENT_CPUS"
upsert_env "$ENV_FILE" "JUDGE_AGENT_CONTAINER_MEMORY" "$AGENT_MEMORY"
upsert_env "$ENV_FILE" "JUDGE_WORK_ROOT" "/var/lib/zerone-judge"
upsert_env "$ENV_FILE" "JUDGE_SANDBOX_MODE" "docker"
upsert_env "$ENV_FILE" "JUDGE_CHECKER_SANDBOX_MODE" "docker"
upsert_env "$ENV_FILE" "JUDGE_SANDBOX_IMAGE" "zerone-judge-agent:latest"
upsert_env "$ENV_FILE" "JUDGE_SANDBOX_CPUS" "1"
upsert_env "$ENV_FILE" "JUDGE_SANDBOX_MEMORY_MB" "512"
upsert_env "$ENV_FILE" "JUDGE_SANDBOX_PIDS_LIMIT" "128"
upsert_env "$ENV_FILE" "JUDGE_OUTPUT_LIMIT_BYTES" "1048576"
upsert_env "$ENV_FILE" "JUDGE_LONG_POLL_SECONDS" "20"
upsert_env "$ENV_FILE" "JUDGE_POLL_INTERVAL_SECONDS" "1"

mkdir -p /var/lib/zerone-judge
chown -R root:root /var/lib/zerone-judge

log "Starting judge-agent via docker compose"
cd "$REPO_DIR/judge_agent/deploy"
docker compose -f compose.yaml up -d --build

log "Done"
docker compose -f compose.yaml ps
echo
echo "Next:"
echo "  docker compose -f $REPO_DIR/judge_agent/deploy/compose.yaml logs -f judge-agent"
echo "  curl -sS ${API_BASE_URL%/api}/api/public/judge-status"

