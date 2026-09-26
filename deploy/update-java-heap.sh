#!/usr/bin/env bash
# Run as root on pve01 while submissions are paused; explicit node numbers required.
# Changes only the Java heap method/version. This is not an atomic queue drain.
set -euo pipefail
set +x
release=64e52f70fbc905a9d532831ce6921acdc9b61c30
base="https://raw.githubusercontent.com/ZERONE-Online-Judge/judge-agent-v1/$release"
[[ $# -gt 0 ]] || { echo '사용법: bash update-java-heap.sh 1 2 3 4 5 6' >&2; exit 1; }
for n in "$@"; do [[ "$n" =~ ^[1-7]$ ]] || { echo '노드 번호는 1~7입니다.' >&2; exit 1; }; done
command -v sshpass >/dev/null || { echo 'sshpass가 필요합니다.' >&2; exit 1; }
scratch=$(mktemp -d)
trap 'unset SSHPASS; rm -rf "$scratch"' EXIT
for file in executor.py java_heap_smoke.py; do
  curl -fSL --connect-timeout 10 --max-time 60 "$base/app/$file" -o "$scratch/$file"
done
curl -fSL --connect-timeout 10 --max-time 60 "$base/deploy/java_heap_patch.py" -o "$scratch/java_heap_patch.py"
payload=$(tar -C "$scratch" -czf - executor.py java_heap_smoke.py java_heap_patch.py | base64 -w0)
read -r -s -p '채점 노드 공통 SSH/sudo 비밀번호: ' SSHPASS
printf '\n'
export SSHPASS
remote_script=$(cat <<'REMOTE'
set -euo pipefail
cd "$1"
compose=(docker compose -f deploy/compose.yaml)
cid=$("${compose[@]}" ps -q judge-agent)
[[ -n "$cid" ]] || { echo '실행 중인 judge-agent가 없습니다.' >&2; exit 1; }
# API connectivity and local active-job directories: do not reveal node secrets.
check_idle() {
  docker exec "$cid" python -c '
import json, uuid, urllib.request
from app.settings import settings
assert settings.isolate_box_id_base >= 32, "Smoke checks need reserved box IDs 0..31"
with urllib.request.urlopen(settings.internal_api_base_url + "/health", timeout=10) as r:
    assert json.load(r)["data"]["status"] == "ok"
for path in settings.work_root.iterdir():
    if not path.is_dir(): continue
    try: uuid.UUID(path.name)
    except ValueError: continue
    raise SystemExit("채점 작업 디렉터리가 있습니다. 작업 종료 후 다시 실행하세요.")
'
}
check_idle
stamp=$(date -u +%Y%m%dT%H%M%S)-$$
backup="/var/backups/zoj-java-heap/$stamp"
mkdir -p "$backup"/{assets,runtime,source,patched-runtime,patched-source}
chmod 700 "$backup"
printf '%s' "$2" | base64 -d | tar -xzf - -C "$backup/assets"
for file in executor.py settings.py; do
  cp -p "app/$file" "$backup/source/$file"
  docker cp "$cid:/app/app/$file" "$backup/runtime/$file"
done
if [[ -f app/java_heap_smoke.py ]]; then cp -p app/java_heap_smoke.py "$backup/source/java_heap_smoke.py"; fi
python3 "$backup/assets/java_heap_patch.py" "$backup/runtime" "$backup/assets" "$backup/patched-runtime"
python3 "$backup/assets/java_heap_patch.py" "$backup/source" "$backup/assets" "$backup/patched-source"
image_id=$(docker inspect --format '{{.Image}}' "$cid")
image_tag=$(docker inspect --format '{{.Config.Image}}' "$cid")
[[ "$image_tag" != sha256:* ]] || { echo '태그 없는 이미지는 자동 교체하지 않습니다.' >&2; exit 1; }
saved_image="zerone-judge-agent:before-java-heap-$stamp"
new_image="zerone-judge-agent:java-heap-$stamp"
docker tag "$image_id" "$saved_image"
printf 'FROM %s\nCOPY executor.py settings.py java_heap_smoke.py /app/app/\n' "$saved_image" > "$backup/patched-runtime/Dockerfile"
DOCKER_BUILDKIT=0 docker build --network=none --pull=false -t "$new_image" "$backup/patched-runtime"
printf 'services:\n  judge-agent:\n    image: %s\n    network_mode: none\n' "$new_image" > "$backup/smoke.yaml"
# Same compiler/runtime/isolate/cgroup mounts as this VM. No backend requests.
"${compose[@]}" -f "$backup/smoke.yaml" run --rm --no-deps -T \
  -e JUDGE_ISOLATE_BOX_ID_BASE=0 -e JUDGE_ISOLATE_BOX_ID_COUNT=32 \
  --entrypoint python judge-agent -m app.java_heap_smoke
check_idle
printf '#!/usr/bin/env bash\nset -euo pipefail\ncd %q\n' "$PWD" > "$backup/rollback.sh"
for file in executor.py settings.py; do
  printf 'cp -p %q %q\n' "$backup/source/$file" "app/$file" >> "$backup/rollback.sh"
done
if [[ -f "$backup/source/java_heap_smoke.py" ]]; then
  printf 'cp -p %q app/java_heap_smoke.py\n' "$backup/source/java_heap_smoke.py" >> "$backup/rollback.sh"
else
  printf 'rm -f app/java_heap_smoke.py\n' >> "$backup/rollback.sh"
fi
printf 'docker tag %q %q\ndocker compose -f deploy/compose.yaml up -d --no-build --force-recreate judge-agent\n' "$saved_image" "$image_tag" >> "$backup/rollback.sh"
chmod 700 "$backup/rollback.sh"
trap 'echo "교체 실패: 이전 이미지로 복구합니다." >&2; bash "$backup/rollback.sh"; exit 1' ERR
for file in executor.py settings.py java_heap_smoke.py; do cp "$backup/patched-source/$file" "app/$file"; done
docker tag "$new_image" "$image_tag"
"${compose[@]}" up -d --no-build --force-recreate judge-agent
cid=$("${compose[@]}" ps -q judge-agent)
docker exec "$cid" python -c '
from pathlib import Path
from app.executor import JudgeExecutor
from app.settings import settings
assert settings.agent_version == "0.2.19"
executor = JudgeExecutor(Path("/tmp/java-heap-policy-check"))
assert executor._java_heap_mb(1024) == 768
assert executor._java_heap_mb(2048) == 1536
print("Java heap policy 0.2.19 loaded")
'
registered=false
for attempt in {1..60}; do
  if docker logs "$cid" 2>&1 | grep -q '\[judge-agent\] registered node='; then registered=true; break; fi
  sleep 2
done
[[ "$registered" == true ]]
trap - ERR
printf '0.2.19 교체·등록 완료. 복구 명령: sudo bash %s/rollback.sh\n' "$backup"
REMOTE
)
for n in "$@"; do
  user="zoj-a$n"
  printf '\n[%s] Java 힙 업데이트 및 VM 격리 검증\n' "$user"
  printf -v remote_command 'sudo -S -p "" bash -c %q -- %q %q' "$remote_script" "/home/$user/judge-agent-v1" "$payload"
  if ! printf '%s\n' "$SSHPASS" | sshpass -e ssh \
    -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 -o ServerAliveInterval=15 \
    "$user@10.10.10.11$n" "$remote_command"; then
    echo "[$user] 실패: 이후 노드 적용은 중단합니다. 이미 완료된 노드는 새 버전을 유지합니다." >&2
    exit 1
  fi
done
echo '선택 노드 적용 완료. 운영 화면에서 노드별 heartbeat를 확인하세요.'
