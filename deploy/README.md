# Judge Agent Standalone Deploy

`judge_agent`만 별도 저장소/폴더로 분리해서 배포할 때 사용하는 구성입니다.

## 1) 준비

```bash
cd judge_agent/deploy
cp env/judge-agent.env.example env/judge-agent.env
```

`env/judge-agent.env`에서 최소 아래 값은 반드시 수정하세요.

- `INTERNAL_API_BASE_URL` (예: `https://judge.zerone01.kr/api`)
- `JUDGE_NODE_NAME` (VM마다 고유)
- `JUDGE_NODE_SECRET` (VM마다 고유)

## 2) 실행

```bash
docker compose -f compose.yaml up -d --build
```

## 3) 상태 확인

```bash
docker compose -f compose.yaml ps
docker compose -f compose.yaml logs -f judge-agent
```

## 참고

- 이 구성은 중앙 백엔드/MinIO를 사용합니다.
- judge VM에는 MinIO를 띄울 필요가 없습니다.
- 기존 루트 `deploy/compose.judge-agent.yaml`은 호환을 위해 유지됩니다.

