#!/usr/bin/env bash
# Run on the same internal-network machine as deploy_env.sh.
# Usage: bash update-api-domain.sh [node numbers...]; default: 1 through 7.
# Run outside a contest / while submissions are paused. Existing work is not drained.
set -euo pipefail

command -v sshpass >/dev/null || { echo 'sshpass가 필요합니다.' >&2; exit 1; }
command -v ssh >/dev/null || { echo 'ssh가 필요합니다.' >&2; exit 1; }
node_numbers=("$@")
if [[ ${#node_numbers[@]} -eq 0 ]]; then node_numbers=(1 2 3 4 5 6 7); fi
for node_number in "${node_numbers[@]}"; do
  [[ "$node_number" =~ ^[1-7]$ ]] || { echo '노드 번호는 1~7만 사용할 수 있습니다.' >&2; exit 1; }
done
read -r -s -p '채점 서버 SSH/sudo 비밀번호: ' SSHPASS
printf '\n'
export SSHPASS
trap 'unset SSHPASS' EXIT

remote_script=$(cat <<'REMOTE'
set -euo pipefail
cd "$1"
python3 - <<'PY'
from pathlib import Path
import datetime, json, os, shutil, urllib.request

base = "https://zoj.kr"
with urllib.request.urlopen(base + "/api/health", timeout=15) as response:
    assert json.load(response)["data"]["status"] == "ok", "새 API가 정상 상태가 아닙니다."
with urllib.request.urlopen(base + "/api/public/judge-status", timeout=15) as response:
    status = json.load(response)["data"]
if status["total_running_jobs"] or status["total_queue_depth"]:
    raise SystemExit("진행/대기 중인 채점이 있습니다. 제출이 없는 시간에 다시 실행하세요.")

path = Path("deploy/env/judge-agent.env")
lines = path.read_text().splitlines()
backup_dir = Path("/var/backups/zoj-domain")
backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
backup = backup_dir / ("judge-agent-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + ".env")
shutil.copy2(path, backup)
os.chmod(backup, 0o600)
updated = []
for line in lines:
    if line.startswith("INTERNAL_API_BASE_URL="):
        continue
    updated.append(line)
updated.append("INTERNAL_API_BASE_URL=https://zoj.kr/api")
path.write_text("\n".join(updated) + "\n")
print("API 주소 수정 완료. 이전 환경파일 백업:", backup)
PY
# A restart alone does not load new env_file values; recreate the container.
# Keep the existing image, node secret, name, and all other environment values.
docker compose -f deploy/compose.yaml up -d --no-build --force-recreate judge-agent
for attempt in {1..10}; do
  if docker compose -f deploy/compose.yaml exec -T judge-agent python -c '
import json, urllib.request
from app.settings import settings
assert settings.internal_api_base_url == "https://zoj.kr/api"
with urllib.request.urlopen(settings.internal_api_base_url + "/health", timeout=10) as response:
    assert json.load(response)["data"]["status"] == "ok"
print("컨테이너 환경변수 및 zoj.kr API 연결 확인 완료")
'; then
    exit 0
  fi
  sleep 2
done
echo '컨테이너 확인 실패. 위 백업과 해당 서버의 컨테이너 상태를 확인하세요.' >&2
exit 1
REMOTE
)

failed_nodes=()
for node_number in "${node_numbers[@]}"; do
  node_host="10.10.10.11${node_number}"
  node_user="zoj-a${node_number}"
  node_repo="/home/${node_user}/judge-agent-v1"
  printf '\n===== %s (%s) =====\n' "$node_user" "$node_host"
  # Quote the script/path as shell arguments. The password travels only on stdin,
  # not inside shell source or process arguments. Use the user's existing SSH keys.
  printf -v remote_command 'sudo -S -p "" bash -c %q -- %q' "$remote_script" "$node_repo"
  if printf '%s\n' "$SSHPASS" | sshpass -e ssh \
    -o StrictHostKeyChecking=accept-new \
    -o ConnectTimeout=8 -o ServerAliveInterval=15 -o ServerAliveCountMax=2 \
    "${node_user}@${node_host}" "$remote_command"; then
    printf '===== %s 완료 =====\n' "$node_user"
  else
    failed_nodes+=("$node_user")
    printf '===== %s 실패: 다음 서버를 확인합니다 =====\n' "$node_user" >&2
  fi
done
if [[ ${#failed_nodes[@]} -gt 0 ]]; then
  printf '\n미완료 노드: %s\n' "${failed_nodes[*]}" >&2
  echo '전체 이전 완료로 판단하지 마세요. 실패한 노드를 확인한 뒤 해당 번호로 다시 실행하세요.' >&2
  exit 1
fi
printf '\n선택한 노드의 API 주소 변경 및 컨테이너 재생성이 완료됐습니다. 운영 화면에서 노드별 heartbeat를 확인하세요.\n'
