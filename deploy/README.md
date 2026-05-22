# Judge Agent Standalone Deploy

`judge_agent_v1`만 별도 저장소/폴더로 분리해서 배포할 때 사용하는 구성입니다.

## 1) 준비

```bash
cd judge_agent_v1/deploy
cp env/judge-agent.env.example env/judge-agent.env
```

`env/judge-agent.env`에서 최소 아래 값은 반드시 수정하세요.

- `INTERNAL_API_BASE_URL` (예: `https://judge.zerone01.kr/api`)
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
