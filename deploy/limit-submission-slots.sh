#!/usr/bin/env bash
# Run as root on pve01 in an idle maintenance window.
# Sets submission slots to 1; keeps testcase parallelism, image, CPU and RAM.
# Local idle checks are not an atomic global queue drain.
set -euo pipefail
set +x
nodes=("$@")
if [[ ${#nodes[@]} -eq 0 ]]; then nodes=(1 2 3 4 5 6); fi
for n in "${nodes[@]}"; do [[ "$n" =~ ^[1-6]$ ]] || { echo '실험 대상은 1~6번입니다.' >&2; exit 1; }; done
command -v sshpass >/dev/null || { echo 'sshpass가 필요합니다.' >&2; exit 1; }
read -r -s -p '채점 노드 공통 SSH/sudo 비밀번호: ' SSHPASS
printf '\n'
export SSHPASS
trap 'unset SSHPASS' EXIT
remote_script=$(cat <<'REMOTE'
set -euo pipefail
set +x
repo="$1"
cd "$repo/deploy"
compose=(docker compose -f compose.yaml)
cid=$("${compose[@]}" ps -q judge-agent)
[[ -n "$cid" ]] || { echo '실행 중인 judge-agent가 없습니다.' >&2; exit 1; }
image_before=$(docker inspect --format '{{.Image}}' "$cid")
memory_before=$(docker inspect --format '{{.HostConfig.Memory}}' "$cid")
cpus_before=$(docker inspect --format '{{.HostConfig.NanoCpus}}' "$cid")
[[ "$memory_before" -gt 0 && "$cpus_before" -gt 0 ]] || { echo 'CPU/메모리 제한이 예상과 다릅니다. 수동 확인이 필요합니다.' >&2; exit 1; }
# Preserve effective Docker limits even if Compose interpolation defaults differ.
export JUDGE_AGENT_CONTAINER_MEMORY="$memory_before"
export JUDGE_AGENT_CONTAINER_CPUS
JUDGE_AGENT_CONTAINER_CPUS=$(python3 -c 'import sys; print(int(sys.argv[1])/1e9)' "$cpus_before")
check_idle() {
  docker exec "$cid" python -c '
import json, uuid, urllib.request
from app.settings import settings
assert settings.testcase_parallelism == 4, "테스트 병렬 수가 4가 아닙니다. 비교 조건을 확인하세요."
with urllib.request.urlopen(settings.internal_api_base_url + "/health", timeout=10) as r:
    assert json.load(r)["data"]["status"] == "ok"
for path in settings.work_root.iterdir():
    if not path.is_dir(): continue
    try: uuid.UUID(path.name)
    except ValueError: continue
    raise SystemExit("채점 작업 디렉터리가 있습니다. 작업 종료 후 다시 실행하세요.")
print("변경 전: 제출 슬롯", settings.total_slots, "테스트 병렬", settings.testcase_parallelism)
'
}
check_idle
stamp=$(date -u +%Y%m%dT%H%M%S)-$$
backup="/var/backups/zoj-slot-limit/$stamp"
mkdir -p "$backup"
chmod 700 "$backup"
cp -p env/judge-agent.env "$backup/judge-agent.env"
chmod 600 "$backup/judge-agent.env"
rollback() {
  trap - ERR
  echo '설정 적용 실패: 기존 환경파일로 복구합니다.' >&2
  cp -p "$backup/judge-agent.env" env/judge-agent.env
  "${compose[@]}" up -d --no-build --force-recreate judge-agent || true
  exit 1
}
trap rollback ERR
python3 - <<'PY'
from pathlib import Path
import os, re, tempfile
p=Path('env/judge-agent.env')
lines=[line for line in p.read_text().splitlines() if not re.match(r'^\s*(?:export\s+)?JUDGE_TOTAL_SLOTS\s*=', line)]
lines.append('JUDGE_TOTAL_SLOTS=1')
fd,name=tempfile.mkstemp(prefix='.slot-limit-',dir=p.parent)
try:
    with os.fdopen(fd,'w') as f: f.write('\n'.join(lines)+'\n')
    os.chmod(name,p.stat().st_mode & 0o777)
    os.replace(name,p)
finally:
    if os.path.exists(name): os.unlink(name)
PY
# Recheck before recreation; do not run while new participant work is arriving.
check_idle
"${compose[@]}" up -d --no-build --force-recreate judge-agent
cid=$("${compose[@]}" ps -q judge-agent)
[[ $(docker inspect --format '{{.Image}}' "$cid") == "$image_before" ]]
[[ $(docker inspect --format '{{.HostConfig.Memory}}' "$cid") == "$memory_before" ]]
[[ $(docker inspect --format '{{.HostConfig.NanoCpus}}' "$cid") == "$cpus_before" ]]
docker exec "$cid" python -c '
from app.settings import settings
assert settings.total_slots == 1, "슬롯 설정이 Compose에서 덮어써졌습니다."
assert settings.testcase_parallelism == 4
print("확인:", settings.node_name, "slots=1 testcase_parallelism=4 version="+settings.agent_version)
'
trap - ERR
printf 'CPU·메모리·이미지 유지. 환경파일 백업: %s/judge-agent.env\n' "$backup"
REMOTE
)
failed=()
for n in "${nodes[@]}"; do
  printf '\n[%s번] 제출 슬롯 1개로 변경\n' "$n"
  printf -v command_line 'sudo -S -p "" bash -c %q -- %q' "$remote_script" "/home/zoj-a$n/judge-agent-v1"
  if printf '%s\n' "$SSHPASS" | sshpass -e ssh \
    -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 \
    -o ServerAliveInterval=15 -o ServerAliveCountMax=2 \
    "zoj-a$n@10.10.10.11$n" "$command_line"; then
    printf '[%s번] 완료\n' "$n"
  else
    failed+=("$n")
    printf '[%s번] 실패. 다음 노드로 계속합니다.\n' "$n" >&2
  fi
done
if [[ ${#failed[@]} -gt 0 ]]; then
  printf '미완료 노드: %s. 모두 슬롯 1개로 확인되기 전에는 비교 실험을 시작하지 마세요.\n' "${failed[*]}" >&2
  exit 1
fi
printf '\n선택 노드의 설정 변경 완료. 서버의 새 heartbeat에서 slots=1을 확인한 뒤 실험하세요.\n'
