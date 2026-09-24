#!/usr/bin/env bash
# Owner-operated enrollment of the returning VMs 6 and 7, followed by TLS rollout.
# Run on pve01 as root. Secrets travel only through SSH stdin, never CLI arguments.
set +x
set -euo pipefail
command -v sshpass >/dev/null || { echo 'sshpass가 필요합니다.' >&2; exit 1; }
nodes=("$@")
if [[ ${#nodes[@]} -eq 0 ]]; then nodes=(6 7); fi
for n in "${nodes[@]}"; do
  [[ "$n" =~ ^[67]$ ]] || { echo '이 복귀 스크립트는 6·7번 전용입니다.' >&2; exit 1; }
done
scratch=$(mktemp -d)
cleanup() {
  unset node_password backend_password credentials SSHPASS
  rm -rf "$scratch"
}
trap cleanup EXIT

curl -fSL --connect-timeout 10 --max-time 60 \
  https://raw.githubusercontent.com/ZERONE-Online-Judge/judge-agent-v1/4b1deff6fa1de30e10eea471c315396c56465e58/deploy/use-internal-tls.sh \
  -o "$scratch/use-internal-tls.sh"
printf '%s  %s\n' '48d49af3712609980f2c5fdf2fd021164f22226f8eda72e91c5a75afd799bc2c' "$scratch/use-internal-tls.sh" | sha256sum -c -

# Pin the backend host key obtained through an already authenticated SSH session.
printf '%s\n' '10.10.10.110 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDUTIK4tk6nuUCKXbdZyJI+lbrQMhpOzJJrTjQfQ7ZWb' > "$scratch/backend_known_hosts"
backend_options=(-o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$scratch/backend_known_hosts" -o HostKeyAlgorithms=ssh-ed25519 -o ConnectTimeout=8 -o ServerAliveInterval=15)
backend_password=
backend_key_auth=false
if ssh "${backend_options[@]}" -o BatchMode=yes zoj@10.10.10.110 true </dev/null 2>/dev/null; then
  backend_key_auth=true
else
  read -r -s -p '백엔드 zoj@10.10.10.110 SSH 비밀번호: ' backend_password
  printf '\n'
fi
backend_ssh() {
  if [[ "$backend_key_auth" == true ]]; then
    ssh "${backend_options[@]}" -o BatchMode=yes zoj@10.10.10.110 "$@"
  else
    SSHPASS="$backend_password" sshpass -e ssh "${backend_options[@]}" -o PubkeyAuthentication=no -o PreferredAuthentications=password -o NumberOfPasswordPrompts=1 zoj@10.10.10.110 "$@"
  fi
}
backend_ssh 'docker ps >/dev/null' </dev/null

read -r -s -p '6·7번 채점 노드 공통 SSH/sudo 비밀번호: ' node_password
printf '\n'

reader=$(cat <<'READ_NODE'
set -euo pipefail
cd "/home/$1/judge-agent-v1"
docker compose -f deploy/compose.yaml exec -T judge-agent python -c '
import json
from app.settings import settings
print(json.dumps({"node_name":settings.node_name,"node_secret":settings.node_secret,"total_slots":settings.total_slots}))
'
READ_NODE
)

provision=$(cat <<'PROVISION_NODE'
import json, sys
from app.services.store import store

number = int(sys.argv[1])
if number not in (6, 7):
    raise SystemExit("Only the approved returning nodes 6 and 7 are supported")
payload = json.load(sys.stdin)
expected_name = f"zoj-judge-agent-{number:02d}"
secret = payload.get("node_secret")
slots = payload.get("total_slots")
if payload.get("node_name") != expected_name:
    raise SystemExit("Node name does not match the selected VM; no changes made")
if not isinstance(secret, str) or not 32 <= len(secret) <= 1024:
    raise SystemExit("Node secret must be 32 to 1024 characters; no changes made")
if type(slots) is not int or not 1 <= slots <= 1024:
    raise SystemExit("Invalid node capacity; no changes made")
existing = next((node for node in store.judge_nodes.values() if node.node_name == expected_name), None)
if existing:
    if not store.verify_node_secret(existing.judge_node_id, secret):
        raise SystemExit("Existing credential differs or is revoked; refusing to overwrite it")
    print(expected_name + ": already approved")
else:
    store.provision_node(expected_name, secret, slots)
    print(expected_name + ": approved")
PROVISION_NODE
)

for n in "${nodes[@]}"; do
  printf '[%s번] 기존 자격증명을 SSH로 읽어 서버에 사전 등록합니다.\n' "$n"
  printf -v read_command 'sudo -S -p "" bash -c %q -- %q' "$reader" "zoj-a$n"
  credentials=$(printf '%s\n' "$node_password" | SSHPASS="$node_password" sshpass -e ssh \
    -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 -o ServerAliveInterval=15 \
    "zoj-a$n@10.10.10.11$n" "$read_command")
  printf -v provision_command 'cd /home/zoj/zerone-online-judge/backend-v1 && color=$(sh deploy/bluegreen.sh active) && docker compose -f deploy/compose.backend.yaml exec -T "api-$color" python -c %q %q' "$provision" "$n"
  printf '%s\n' "$credentials" | backend_ssh "$provision_command"
  unset credentials
done

# The nested rollout consumes this password once, without echoing it.
printf '%s\n' "$node_password" | bash "$scratch/use-internal-tls.sh" "${nodes[@]}"
