# Judge Agent Standalone Deploy

## 내부 HTTPS 전환 (pve01)

`deploy/use-internal-tls.sh`를 pve01 root에서 실행합니다. 현재 설치의 공개
서버 인증서를 포함하며, API와 공개 `/minio/` 다운로드를 함께
`https://10.10.10.110:6443`으로 전환합니다. 기본 대상은 1~7번이며,
꺼진 노드는 제외해서 실행하세요.

```bash
bash use-internal-tls.sh 1 2 3 4 5
```

기존 배포처럼 `sshpass`와 노드의 공통 SSH/sudo 비밀번호가 필요합니다.
비밀번호는 실행 중 입력하며 저장하지 않습니다. 노드 비밀키는 유지합니다.
백엔드의 내부 HTTPS gateway가 먼저 배포되어 있어야 합니다.

선택한 에이전트 컨테이너를 순차 재생성하므로 진행 중인 채점이 완료된 뒤
실행하세요. 변경 전 실제 컨테이너에서 서버 인증서와 IP를 검증하고 health를
확인합니다. 기존 런타임 이미지에 URL 처리와 인증서 신뢰만 추가하므로 노드에서
패키지 다운로드나 전체 빌드가 필요하지 않습니다. 인증서 검증은 계속 켜져 있습니다.
신뢰 인증서 묶음은 `deploy/env/judge-tls/`에 저장하고 읽기 전용으로 연결하여
나중에 이미지를 다시 빌드해도 유지됩니다.

설정, 소스, 이미지 태그를 백업하고 검증 실패 시 해당 노드를 자동 복구합니다.
수동 복구 명령은 노드별 출력의
`sudo bash /var/backups/zoj-internal/<시각>/rollback.sh`입니다. 등록과 health를
확인한 뒤 다음 노드로 진행하며, 실패하면 이후 노드는 변경하지 않습니다.
마지막으로 운영 화면의 heartbeat와 테스트 제출을 확인하세요.

백엔드는 관리자가 사전 등록한 이름/비밀키만 인증합니다. 과거 버전에서 등록
기록이 삭제된 6·7번은 다시 켤 때 서버의 `app.tools.judge_nodes provision`으로
먼저 승인해야 합니다. 새 노드와 비밀키 회수·교체 절차는 백엔드 배포 README를
따르세요. 공개 도메인 경유 judge API는 외부에서 차단됩니다.

서버 인증서는 1년 유효합니다. 인증서 교체 시 이 wrapper의 공개 인증서도
갱신하고 노드에 새 신뢰를 먼저 설치해야 합니다. 개인키는 서버 밖으로 옮기지
않습니다. `use-internal-api.sh`의 HTTP 모드는 명시적인 긴급 복구 옵션으로만
남아 있으며 비밀키가 암호화되지 않으므로 기본 경로로 사용하지 않습니다.

`judge_agent_v1`만 별도 저장소/폴더로 분리해서 배포할 때 사용하는 구성입니다.

## 1) 준비

```bash
cd judge_agent_v1/deploy
cp env/judge-agent.env.example env/judge-agent.env
```

`env/judge-agent.env`에서 최소 아래 값은 반드시 수정하세요.

- `INTERNAL_API_BASE_URL` (내부 HTTPS: `https://10.10.10.110:6443/api`, 서버 인증서 신뢰 설치 필요)
- `JUDGE_NODE_NAME` (VM마다 고유)
- `JUDGE_NODE_SECRET` (VM마다 고유)

성능 튜닝:

- `JUDGE_TOTAL_SLOTS`: 동시에 처리할 제출 job 수
- `JUDGE_TESTCASE_PARALLELISM`: 제출 1개 안에서 동시에 실행할 테스트케이스 수. 기본값 `1`

동시 실행량은 대략 `JUDGE_TOTAL_SLOTS * JUDGE_TESTCASE_PARALLELISM`입니다. 10 vCPU / 20GB VM에서는 먼저 `JUDGE_TOTAL_SLOTS=4`, `JUDGE_TESTCASE_PARALLELISM=2` 정도로 시작하는 것을 권장합니다.

또는 긴 명령 없이, 파일 안 설정값만 바꾸는 부트스트랩 스크립트를 사용하세요.

```bash
cd judge_agent_v1/deploy
sudo bash bootstrap_ubuntu_judge_agent.sh
```

## 2) 실행

```bash
docker compose -f compose.yaml up -d --build
```

## 3) 상태 확인

```bash
docker compose -f compose.yaml ps
docker compose -f compose.yaml logs -f judge-agent
```

## 4) 업데이트 (git pull + 재빌드/재기동)

```bash
cd judge_agent_v1/deploy
./update.sh
```

로컬 변경이 있는 서버는 아래 중 하나 사용:

```bash
./update.sh --stash
# 또는
./update.sh --discard-local
```

## 참고

- 이 구성은 중앙 백엔드/MinIO를 사용합니다.
- judge VM에는 MinIO를 띄울 필요가 없습니다.
- 샌드박스는 `isolate` 고정입니다. compose는 isolate 실행을 위해 privileged 컨테이너와 cgroup 마운트를 사용합니다.
- 루트 `deploy/compose.judge-agent.yaml`은 제거되었고, judge-agent 배포는 이 폴더 기준으로만 사용합니다.

## 기존 7개 노드의 API 도메인 변경

`deploy/update-api-domain.sh`를 기존 `deploy_env.sh`를 실행하던 내부망 머신에서
실행합니다. `zoj-a1@10.10.10.111`부터 `zoj-a7@10.10.10.117`까지 순차 적용합니다.
SSH와 sudo에 쓰는 공통 비밀번호는 실행할 때 입력하며 파일에 저장하지 않습니다.

```bash
bash update-api-domain.sh        # 1~7 전체
bash update-api-domain.sh 6 7    # 실패했던 노드만 재시도
```

진행 중인 대회/제출이 없는 시간에 실행하세요. 스크립트는 공개 채점 상태에서 진행/대기
작업이 있으면 해당 노드 변경을 중단하지만, 신규 제출을 차단하거나 작업을 drain하지는
않습니다. 기존 env는 각 서버 `/var/backups/zoj-domain`에 권한 600으로 백업합니다.
API URL만 변경하고 기존 이미지로 컨테이너를 재생성합니다. 단순 `docker restart`로는
변경된 env 파일이 적용되지 않습니다. 실패한 노드는 명시적으로 보고하며 종료 코드 1을
반환합니다. 완료 후 운영 화면에서 각 노드의 heartbeat와 채점 동작을 확인하세요.
