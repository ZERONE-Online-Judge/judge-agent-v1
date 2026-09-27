#!/usr/bin/env bash
# Run as root on pve01 while submissions are paused; explicit node numbers required.
# Installs agent 0.2.21 and restores the measured 1-slot / 4-testcase policy.
# This is not an atomic global queue drain.
set -euo pipefail
set +x

release=63eb55e3f5380738e23a88045b7cb6d4d0cf2496
previous=b4e7809860e9220e49e481ac13d270cb2ca9b28a
base="https://raw.githubusercontent.com/ZERONE-Online-Judge/judge-agent-v1"

[[ $# -gt 0 ]] || { echo '사용법: bash update-tle-confirmation.sh 1 2 3 4 5 6' >&2; exit 1; }
for n in "$@"; do
  [[ "$n" =~ ^[1-6]$ ]] || { echo '적용 대상은 1~6번입니다.' >&2; exit 1; }
done
command -v sshpass >/dev/null || { echo 'sshpass가 필요합니다.' >&2; exit 1; }

scratch=$(mktemp -d)
trap 'unset SSHPASS; rm -rf "$scratch"' EXIT
mkdir -p "$scratch/previous" "$scratch/release"
for file in executor.py settings.py; do
  curl -fSL --connect-timeout 10 --max-time 60 "$base/$previous/app/$file" -o "$scratch/previous/$file"
  curl -fSL --connect-timeout 10 --max-time 60 "$base/$release/app/$file" -o "$scratch/release/$file"
done
curl -fSL --connect-timeout 10 --max-time 60 "$base/$release/app/timing_smoke.py" -o "$scratch/release/timing_smoke.py"
payload=$(tar -C "$scratch" -czf - previous release | base64 -w0)

read -r -s -p '채점 노드 공통 SSH/sudo 비밀번호: ' SSHPASS
printf '\n'
export SSHPASS

remote_script=$(cat <<'REMOTE'
set -euo pipefail
set +x
cd "$1"
compose=(docker compose -f deploy/compose.yaml)
cid=$("${compose[@]}" ps -q judge-agent)
[[ -n "$cid" ]] || { echo '실행 중인 judge-agent가 없습니다.' >&2; exit 1; }

check_idle() {
  docker exec "$cid" python -c '
import json, uuid, urllib.request
from app.settings import settings
assert settings.isolate_box_id_base >= 32, "Smoke checks need reserved box IDs 0..31"
with urllib.request.urlopen(settings.internal_api_base_url + "/health", timeout=10) as r:
    assert json.load(r)["data"]["status"] == "ok"
for path in settings.work_root.iterdir():
    if not path.is_dir():
        continue
    try:
        uuid.UUID(path.name)
    except ValueError:
        continue
    raise SystemExit("채점 작업 디렉터리가 있습니다. 작업 종료 후 다시 실행하세요.")
print("변경 전:", settings.node_name, settings.agent_version,
      "slots=", settings.total_slots, "testcase_parallelism=", settings.testcase_parallelism)
'
}

check_idle
stamp=$(date -u +%Y%m%dT%H%M%S)-$$
backup="/var/backups/zoj-timing-policy/$stamp"
mkdir -p "$backup"/{assets,runtime,source,candidate}
chmod 700 "$backup"
printf '%s' "$2" | base64 -d | tar -xzf - -C "$backup/assets"
for file in executor.py settings.py; do
  cp -p "app/$file" "$backup/source/$file"
  docker cp "$cid:/app/app/$file" "$backup/runtime/$file"
done
if [[ -f app/timing_smoke.py ]]; then cp -p app/timing_smoke.py "$backup/source/timing_smoke.py"; fi
cp -p deploy/env/judge-agent.env "$backup/judge-agent.env"
chmod 600 "$backup/judge-agent.env"

classify_pair() {
  local directory=$1
  if cmp -s "$directory/executor.py" "$backup/assets/previous/executor.py" \
     && cmp -s "$directory/settings.py" "$backup/assets/previous/settings.py"; then
    printf 'previous'
  elif cmp -s "$directory/executor.py" "$backup/assets/release/executor.py" \
       && cmp -s "$directory/settings.py" "$backup/assets/release/settings.py"; then
    printf 'release'
  else
    printf 'unknown'
  fi
}
runtime_state=$(classify_pair "$backup/runtime")
source_state=$(classify_pair "$backup/source")
[[ "$runtime_state" != unknown && "$source_state" != unknown ]] || {
  echo "알 수 없는 executor/settings입니다. runtime=$runtime_state source=$source_state" >&2
  exit 1
}

image_id=$(docker inspect --format '{{.Image}}' "$cid")
image_tag=$(docker inspect --format '{{.Config.Image}}' "$cid")
memory_before=$(docker inspect --format '{{.HostConfig.Memory}}' "$cid")
cpus_before=$(docker inspect --format '{{.HostConfig.NanoCpus}}' "$cid")
[[ "$image_tag" != sha256:* && "$memory_before" -gt 0 && "$cpus_before" -gt 0 ]] || {
  echo '이미지 태그 또는 CPU/메모리 제한을 자동 보존할 수 없습니다.' >&2
  exit 1
}
# A VM can have been downsized since the current container was created.
# Docker rejects a new container with a CPU quota above the online CPU count.
printf '%s\n' "$cpus_before" > "$backup/original-nanocpus"
cpus_before=$(python3 -c 'import os,sys; print(min(int(sys.argv[1]), (os.cpu_count() or 1)*10**9))' "$cpus_before")
export JUDGE_AGENT_CONTAINER_MEMORY="$memory_before"
export JUDGE_AGENT_CONTAINER_CPUS
JUDGE_AGENT_CONTAINER_CPUS=$(python3 -c 'import sys; print(int(sys.argv[1])/1e9)' "$cpus_before")
saved_image="zerone-judge-agent:before-timing-$stamp"
new_image="zerone-judge-agent:timing-$stamp"
docker tag "$image_id" "$saved_image"
cp "$backup/assets/release/"{executor.py,settings.py,timing_smoke.py} "$backup/candidate/"
printf 'FROM %s\nCOPY executor.py settings.py timing_smoke.py /app/app/\n' "$saved_image" > "$backup/candidate/Dockerfile"
DOCKER_BUILDKIT=0 docker build --network=none --pull=false -t "$new_image" "$backup/candidate"

printf 'services:\n  judge-agent:\n    image: %s\n    network_mode: none\n' "$new_image" > "$backup/smoke.yaml"
"${compose[@]}" -f "$backup/smoke.yaml" run --rm --no-deps -T \
  -e JUDGE_ISOLATE_BOX_ID_BASE=0 -e JUDGE_ISOLATE_BOX_ID_COUNT=32 \
  --entrypoint python judge-agent -m app.timing_smoke

# Recheck immediately before replacing the running agent.
check_idle
python3 - <<'PY'
from pathlib import Path
import os, re, tempfile

p = Path('deploy/env/judge-agent.env')
pattern = re.compile(r'^\s*(?:export\s+)?(?:JUDGE_TOTAL_SLOTS|JUDGE_TESTCASE_PARALLELISM)\s*=')
lines = [line for line in p.read_text().splitlines() if not pattern.match(line)]
lines.extend(('JUDGE_TOTAL_SLOTS=1', 'JUDGE_TESTCASE_PARALLELISM=4'))
fd, name = tempfile.mkstemp(prefix='.timing-policy-', dir=p.parent)
try:
    with os.fdopen(fd, 'w') as f:
        f.write('\n'.join(lines) + '\n')
    os.chmod(name, p.stat().st_mode & 0o777)
    os.replace(name, p)
finally:
    if os.path.exists(name):
        os.unlink(name)
PY

if [[ -f deploy/.env ]]; then cp -p deploy/.env "$backup/compose.env"; fi
python3 - <<'PYENV'
from pathlib import Path
import os, re
p = Path('deploy/.env')
lines = p.read_text().splitlines() if p.exists() else []
lines = [line for line in lines if not re.match(r'^\s*(?:export\s+)?JUDGE_AGENT_CONTAINER_(?:CPUS|MEMORY)\s*=', line)]
lines += ['JUDGE_AGENT_CONTAINER_CPUS=' + os.environ['JUDGE_AGENT_CONTAINER_CPUS'],
          'JUDGE_AGENT_CONTAINER_MEMORY=' + os.environ['JUDGE_AGENT_CONTAINER_MEMORY']]
p.write_text('\n'.join(lines) + '\n')
p.chmod(0o600)
PYENV

printf '#!/usr/bin/env bash\nset -euo pipefail\ncd %q\n' "$PWD" > "$backup/rollback.sh"
for file in executor.py settings.py; do
  printf 'cp -p %q %q\n' "$backup/source/$file" "app/$file" >> "$backup/rollback.sh"
done
if [[ -f "$backup/source/timing_smoke.py" ]]; then
  printf 'cp -p %q app/timing_smoke.py\n' "$backup/source/timing_smoke.py" >> "$backup/rollback.sh"
else
  printf 'rm -f app/timing_smoke.py\n' >> "$backup/rollback.sh"
fi
printf 'cp -p %q deploy/env/judge-agent.env\n' "$backup/judge-agent.env" >> "$backup/rollback.sh"
printf 'export JUDGE_AGENT_CONTAINER_MEMORY=%q\nexport JUDGE_AGENT_CONTAINER_CPUS=%q\n' \
  "$memory_before" "$JUDGE_AGENT_CONTAINER_CPUS" >> "$backup/rollback.sh"
printf 'docker tag %q %q\ndocker compose -f deploy/compose.yaml up -d --no-build --force-recreate judge-agent\n' \
  "$saved_image" "$image_tag" >> "$backup/rollback.sh"
chmod 700 "$backup/rollback.sh"

trap 'echo "교체 실패: 이전 이미지·소스·환경파일로 복구합니다." >&2; bash "$backup/rollback.sh"; exit 1' ERR
cp "$backup/assets/release/"{executor.py,settings.py,timing_smoke.py} app/
docker tag "$new_image" "$image_tag"
"${compose[@]}" up -d --no-build --force-recreate judge-agent
cid=$("${compose[@]}" ps -q judge-agent)
[[ $(docker inspect --format '{{.HostConfig.Memory}}' "$cid") == "$memory_before" ]]
[[ $(docker inspect --format '{{.HostConfig.NanoCpus}}' "$cid") == "$cpus_before" ]]
docker exec "$cid" python -c '
from pathlib import Path
from app.executor import JudgeExecutor
from app.settings import settings
assert settings.agent_version == "0.2.21"
assert settings.total_slots == 1
assert settings.testcase_parallelism == 4
executor = JudgeExecutor(Path("/tmp/timing-policy-check"))
assert executor._isolate_wall_time_seconds(1.0) == 3.0
assert executor._isolate_runtime_ms({"time": "0.123"}) == 123
print("확인:", settings.node_name, "version=0.2.21 slots=1 testcase_parallelism=4")
'

registered=false
for attempt in {1..60}; do
  if docker logs "$cid" 2>&1 | grep -q '\[judge-agent\] registered node='; then
    registered=true
    break
  fi
  sleep 2
done
[[ "$registered" == true ]]
trap - ERR
printf '0.2.21 교체·등록 완료. 복구 명령: sudo bash %s/rollback.sh\n' "$backup"
REMOTE
)

for n in "$@"; do
  user="zoj-a$n"
  printf '\n[%s] 첫 확정 실패에서 TLE 재확인 종료 업데이트\n' "$user"
  printf -v remote_command 'sudo -S -p "" bash -c %q -- %q %q' \
    "$remote_script" "/home/$user/judge-agent-v1" "$payload"
  if ! printf '%s\n' "$SSHPASS" | sshpass -e ssh \
    -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 \
    -o ServerAliveInterval=15 -o ServerAliveCountMax=2 \
    "$user@10.10.10.11$n" "$remote_command"; then
    echo "[$user] 실패: 이후 노드 적용을 중단합니다. 완료된 노드는 새 버전을 유지합니다." >&2
    exit 1
  fi
done
echo '선택 노드 적용 완료. 운영 화면에서 0.2.21 heartbeat를 확인하세요.'
