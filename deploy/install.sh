#!/usr/bin/env bash
set -euo pipefail

###############################################################################
# Zerone Judge Agent Installer (Ubuntu, repo already cloned)
#
# 사용법:
#   1) 저장소 클론
#   2) cd judge-agent-v1/deploy
#   3) 아래 설정값 수정
#   4) sudo bash install.sh
#
# 이 스크립트가 수행하는 작업:
#   - apt update / 필수 패키지 설치
#   - docker / docker compose 설치
#   - env 파일 생성/업데이트
#   - docker compose up -d --build
###############################################################################

### ====== 사용자 설정 ======
INTERNAL_API_BASE_URL="https://judge.zerone01.kr/api"
JUDGE_NODE_NAME="zoj-judge-agent-0"
JUDGE_NODE_SECRET=""

JUDGE_TOTAL_SLOTS="8"
JUDGE_AGENT_CONTAINER_CPUS="10"
JUDGE_AGENT_CONTAINER_MEMORY="20g"
### =========================

if [[ $EUID -ne 0 ]]; then
  echo "[ERROR] sudo/root 로 실행해야 합니다."
  exit 1
fi

log() { echo "[bootstrap] $*"; }

generate_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
    return
  fi
  tr -dc 'A-Za-z0-9' </dev/urandom | head -c 64
}

if [[ -z "$JUDGE_NODE_SECRET" || "$JUDGE_NODE_SECRET" == "change-me-very-strong-secret" ]]; then
  JUDGE_NODE_SECRET="$(generate_secret)"
  echo "[bootstrap] JUDGE_NODE_SECRET 미설정 -> 자동 생성됨"
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

log "apt update / base package 설치"
apt-get update
apt-get install -y ca-certificates curl gnupg lsb-release git

if ! command -v docker >/dev/null 2>&1; then
  log "docker 설치"
  apt-get install -y docker.io || true
fi

if ! docker compose version >/dev/null 2>&1; then
  log "docker compose plugin 설치"
  apt-get install -y docker-compose-plugin || true
fi

if ! docker compose version >/dev/null 2>&1; then
  log "docker-compose fallback 설치"
  apt-get install -y docker-compose
fi

systemctl enable --now docker

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
  echo "[ERROR] compose 파일이 없습니다: $COMPOSE_FILE"
  exit 1
fi

mkdir -p "$ENV_DIR"
if [[ ! -f "$ENV_EXAMPLE" ]]; then
  echo "[ERROR] env 예시 파일이 없습니다: $ENV_EXAMPLE"
  exit 1
fi
if [[ ! -f "$ENV_FILE" ]]; then
  cp "$ENV_EXAMPLE" "$ENV_FILE"
fi
chmod 600 "$ENV_FILE"

log "judge-agent env 설정"
upsert_env "$ENV_FILE" "APP_ENV" "production"
upsert_env "$ENV_FILE" "INTERNAL_API_BASE_URL" "$INTERNAL_API_BASE_URL"
upsert_env "$ENV_FILE" "JUDGE_NODE_NAME" "$JUDGE_NODE_NAME"
upsert_env "$ENV_FILE" "JUDGE_NODE_SECRET" "$JUDGE_NODE_SECRET"
upsert_env "$ENV_FILE" "JUDGE_TOTAL_SLOTS" "$JUDGE_TOTAL_SLOTS"
upsert_env "$ENV_FILE" "JUDGE_AGENT_CONTAINER_CPUS" "$JUDGE_AGENT_CONTAINER_CPUS"
upsert_env "$ENV_FILE" "JUDGE_AGENT_CONTAINER_MEMORY" "$JUDGE_AGENT_CONTAINER_MEMORY"
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

log "judge-agent compose 실행"
cd "$SCRIPT_DIR"
docker compose -f "$COMPOSE_FILE" up -d --build

log "완료"
docker compose -f "$COMPOSE_FILE" ps
echo
echo "로그 확인:"
echo "  docker compose -f $COMPOSE_FILE logs -f judge-agent"
echo "  (node secret: $JUDGE_NODE_SECRET)"
echo
echo "백엔드 연결 확인:"
echo "  curl -sS ${INTERNAL_API_BASE_URL%/api}/api/public/judge-status"
