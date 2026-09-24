#!/usr/bin/env bash
# Run as root on pve01. Optional arguments select nodes (default: 1..7).
# Restarts selected agents. No package/image downloads are required on agents.
set -euo pipefail
command -v sshpass >/dev/null || { echo 'sshpass가 필요합니다.' >&2; exit 1; }
nodes=("$@")
if [[ ${#nodes[@]} -eq 0 ]]; then nodes=(1 2 3 4 5 6 7); fi
for n in "${nodes[@]}"; do
  [[ "$n" =~ ^[1-7]$ ]] || { echo '노드 번호는 1~7입니다.' >&2; exit 1; }
done
read -r -s -p '채점 노드 공통 SSH/sudo 비밀번호: ' SSHPASS
printf '\n'
export SSHPASS
trap 'unset SSHPASS' EXIT

remote_script=$(cat <<'REMOTE'
set -euo pipefail
cd "$1"
compose=(docker compose -f deploy/compose.yaml)
cid=$("${compose[@]}" ps -q judge-agent)
test -n "$cid" || { echo '실행 중인 judge-agent를 찾지 못했습니다.' >&2; exit 1; }

# Verify connectivity from the actual container before changing anything.
docker exec "$cid" python -c '
import json, urllib.request
with urllib.request.urlopen("http://10.10.10.110:6001/api/health", timeout=10) as r:
    assert json.load(r)["data"]["status"] == "ok"
print("내부 API 연결 확인 완료")
'

stamp=$(date -u +%Y%m%dT%H%M%S)-$$
backup="/var/backups/zoj-internal/$stamp"
mkdir -p "$backup/context"
chmod 700 "$backup" "$backup/context"
cp -p deploy/env/judge-agent.env "$backup/judge-agent.env"
cp -p app/executor.py "$backup/source-executor.py"
if [[ -f app/object_urls.py ]]; then cp -p app/object_urls.py "$backup/source-object_urls.py"; fi
docker cp "$cid:/app/app/executor.py" "$backup/context/executor.py"
image_tag=$(docker inspect --format '{{.Config.Image}}' "$cid")
image_id=$(docker inspect --format '{{.Image}}' "$cid")
[[ "$image_tag" != sha256:* ]] || { echo '태그 없는 이미지는 자동 전환하지 않습니다.' >&2; exit 1; }
saved_image="zerone-judge-agent:before-internal-$stamp"
docker tag "$image_id" "$saved_image"

cat > "$backup/context/object_urls.py" <<'PY'
from urllib.parse import urljoin, urlsplit, urlunsplit


def resolve_object_url(url, api_base, public_origins=""):
    if url.startswith("/"):
        return urljoin(api_base.rstrip("/") + "/", url)
    parts = urlsplit(url)
    allowed = {s.strip().rstrip("/") for s in public_origins.split(",") if s.strip()}
    if f"{parts.scheme}://{parts.netloc}" in allowed and parts.path.startswith("/minio/"):
        internal = urlsplit(api_base)
        return urlunsplit((internal.scheme, internal.netloc, parts.path, parts.query, parts.fragment))
    return url
PY

# Patch only the download resolver, retaining each node's existing runtime code.
python3 - "$backup" <<'PY'
from pathlib import Path
import ast, sys
backup = Path(sys.argv[1])
old = '''    def _absolute_url(self, url: str) -> str:
        if url.startswith("/"):
            return urljoin(settings.internal_api_base_url.rstrip("/") + "/", url)
        return url'''
new = '''    def _absolute_url(self, url: str) -> str:
        return resolve_object_url(
            url, settings.internal_api_base_url, os.getenv("JUDGE_OBJECT_URL_ORIGINS", "")
        )'''
for src, dst in [(backup / "context/executor.py", backup / "context/executor.py"),
                 (Path("app/executor.py"), backup / "patched-source-executor.py")]:
    text = src.read_text()
    if old in text:
        text = text.replace(old, new, 1)
    elif new not in text:
        raise SystemExit("지원하지 않는 executor 버전입니다. 변경 없이 중단합니다.")
    if "from app.object_urls import resolve_object_url" not in text:
        text = text.replace("from app.settings import settings", "from app.settings import settings\nfrom app.object_urls import resolve_object_url", 1)
    assert "import os\n" in text
    assert "from app.object_urls import resolve_object_url" in text
    ast.parse(text)
    dst.write_text(text)
PY

printf 'FROM %s\nCOPY executor.py /app/app/executor.py\nCOPY object_urls.py /app/app/object_urls.py\n' "$saved_image" > "$backup/context/Dockerfile"
new_image="zerone-judge-agent:internal-$stamp"
DOCKER_BUILDKIT=0 docker build --network=none --pull=false -t "$new_image" "$backup/context"
docker run --rm --entrypoint python "$new_image" -c '
from app.object_urls import resolve_object_url
from app.executor import JudgeExecutor
assert resolve_object_url("https://zoj.kr/minio/b/a%20b?sig=a%2Fb", "http://10.10.10.110:6001/api", "https://zoj.kr") == "http://10.10.10.110:6001/minio/b/a%20b?sig=a%2Fb"
'

# Keep a standalone rollback command, including the original image tag and env.
printf '#!/usr/bin/env bash\nset -euo pipefail\ncd %q\n' "$PWD" > "$backup/rollback.sh"
printf 'cp -p %q deploy/env/judge-agent.env\ncp -p %q app/executor.py\n' "$backup/judge-agent.env" "$backup/source-executor.py" >> "$backup/rollback.sh"
if [[ -f "$backup/source-object_urls.py" ]]; then
  printf 'cp -p %q app/object_urls.py\n' "$backup/source-object_urls.py" >> "$backup/rollback.sh"
else
  printf 'rm -f app/object_urls.py\n' >> "$backup/rollback.sh"
fi
printf 'docker tag %q %q\ndocker compose -f deploy/compose.yaml up -d --no-build --force-recreate judge-agent\n' "$saved_image" "$image_tag" >> "$backup/rollback.sh"
chmod 700 "$backup/rollback.sh"
trap 'echo "전환 실패: 이전 설정/이미지로 복구합니다." >&2; bash "$backup/rollback.sh"; exit 1' ERR

python3 - <<'PY'
from pathlib import Path
path = Path("deploy/env/judge-agent.env")
values = {
    "INTERNAL_API_BASE_URL": "http://10.10.10.110:6001/api",
    "JUDGE_OBJECT_URL_ORIGINS": "https://zoj.kr,http://zoj.kr,https://judge.zerone01.kr,http://judge.zerone01.kr",
}
lines = [line for line in path.read_text().splitlines() if line.split("=", 1)[0].strip() not in values]
lines += [f"{key}={value}" for key, value in values.items()]
path.write_text("\n".join(lines) + "\n")
PY
cp "$backup/patched-source-executor.py" app/executor.py
cp "$backup/context/object_urls.py" app/object_urls.py
docker tag "$new_image" "$image_tag"
"${compose[@]}" up -d --no-build --force-recreate judge-agent
cid=$("${compose[@]}" ps -q judge-agent)
registered=false
for attempt in {1..60}; do
  if docker logs "$cid" 2>&1 | grep -q '\[judge-agent\] registered node='; then
    registered=true
    break
  fi
  sleep 2
done
test "$registered" = true
docker exec "$cid" python -c '
import json, os, urllib.request
from app.settings import settings
from app.object_urls import resolve_object_url
assert settings.internal_api_base_url == "http://10.10.10.110:6001/api"
assert resolve_object_url("https://zoj.kr/minio/b/file?sig=abc", settings.internal_api_base_url, os.environ["JUDGE_OBJECT_URL_ORIGINS"]) == "http://10.10.10.110:6001/minio/b/file?sig=abc"
with urllib.request.urlopen(settings.internal_api_base_url + "/health", timeout=10) as r:
    assert json.load(r)["data"]["status"] == "ok"
print("내부 API 설정 및 채점 파일 URL 전환 확인 완료")
'
trap - ERR
echo "등록 완료. 롤백: sudo bash $backup/rollback.sh"
REMOTE
)

for n in "${nodes[@]}"; do
  user="zoj-a$n"
  host="10.10.10.11$n"
  printf '\n[%s] 내부 주소 전환 시작\n' "$user"
  printf -v remote_command 'sudo -S -p "" bash -c %q -- %q' "$remote_script" "/home/$user/judge-agent-v1"
  if ! printf '%s\n' "$SSHPASS" | sshpass -e ssh \
    -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 -o ServerAliveInterval=15 \
    "$user@$host" "$remote_command"; then
    echo "[$user] 실패. 이후 노드 변경을 중단합니다. 성공한 앞선 노드는 내부 주소를 유지합니다." >&2
    exit 1
  fi
  echo "[$user] 내부 API 등록 및 파일 다운로드 경로 전환 완료"
done
echo '선택한 노드 전환 완료. 운영 화면의 heartbeat와 테스트 제출 결과를 확인하세요.'
