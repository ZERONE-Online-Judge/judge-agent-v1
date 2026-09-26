# Judge Agent Standalone Deploy

## 꺼져 있던 6·7번 복귀

승인 기록이 없는 6·7번은 `deploy/resume-approved-agents.sh`를 pve01 root에서
실행합니다. 기본 대상은 6·7번이며 번호 인자로 하나만 선택할 수도 있습니다.

```bash
bash resume-approved-agents.sh 6 7
```

백엔드 `zoj@10.10.10.110`에 키로 접속할 수 없으면 SSH 비밀번호를 묻고,
이어서 채점 노드의 공통 SSH/sudo 비밀번호를 묻습니다. 백엔드 호스트키는
고정하여 확인합니다. 실제 컨테이너의 기존 이름·비밀키·슬롯 수를 읽어
관리자 SSH 경로로 사전 등록한 다음 아래 HTTPS 보안 업데이트를 실행합니다.
자격증명은 SSH 표준입력으로만 전달하며 터미널이나 파일에 출력하지 않습니다.
노드 이름이 선택한 VM과 다르거나, 기존 등록 키가 다르거나, 권한이 회수된
노드는 덮어쓰지 않고 중단합니다. 1~5번은 변경하지 않습니다.

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
확인합니다. 기존 런타임 이미지에 URL 처리, 인증서 신뢰와 executor 보안 수정을 추가하므로 노드에서
패키지 다운로드나 전체 빌드가 필요하지 않습니다. 인증서 검증은 계속 켜져 있습니다.
신뢰 인증서 묶음은 `deploy/env/judge-tls/`에 저장하고 읽기 전용으로 연결하여
나중에 이미지를 다시 빌드해도 유지됩니다.

컴파일도 isolate 안에서 수행하여 절대 경로 include 등으로 채점기 파일을
읽는 것을 막습니다. 제출 프로그램에는 해당 테스트의 실행 파일만 보이며,
다른 테스트나 checker의 정답 파일은 마운트하지 않습니다. isolate 결과
메타데이터도 제출 프로그램이 접근할 수 없는 경로에 저장합니다.
isolate 실패 시 호스트 실행으로 재시도하지 않습니다. `local` 모드는 개발용이며
운영에서는 제출과 checker 모두 `isolate`를 사용해야 합니다.

0.2.18부터 checker 컴파일은 캐시별 파일 잠금으로 직렬화합니다. 별도 임시
폴더에서 컴파일을 마친 실행 파일만 원자적으로 공개하므로 동시 채점의 UID 권한
충돌이나 부분 생성된 바이너리 재사용을 막습니다. 이전 캐시는 삭제하지 않고
새 v2 경로를 사용합니다. 배포 전 검사에는 C/C++ checker 4건 동시 채점과
오답 검출도 포함됩니다.

스크립트는 알려진 executor 버전만 교체하고 수정된 알 수 없는 버전은 중단합니다.
교체 전 별도 컨테이너에서 C/C++/Java/Python과 가짜 파일 접근 차단을 검증합니다.
이 검사는 서버에 제출을 생성하거나 실제 비밀파일을 읽지 않습니다. 검사에 사용하는
isolate ID 0~31은 예약되어 있어야 하며, 기존 채점기의 기본 ID 시작값은 100입니다.

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
않습니다. HTTPS 전환을 마친 서버는 HTTP 채점기 인증을 차단합니다.
과거 HTTP 복구 명령은 사용하지 마세요. 예전 백업으로 수동 복구하여 HTTP 설정이
되살아난 경우에도 현재 TLS 설정과 신뢰 인증서를 다시 적용해야 연결됩니다.

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

### Java 힙 정책 (에이전트 0.2.19)

Java `-Xmx`는 언어별 보정 및 테스트별 재정의를 적용한 **유효 메모리 제한**에서
계산합니다. 512MiB 고정 상한은 없습니다.

| 유효 전체 제한 | 최대 힙 |
|---|---|
| 128MiB | 64MiB |
| 256MiB | 192MiB |
| 512MiB | 384MiB |
| 1GiB | 768MiB |
| 2GiB | 1.5GiB |

128MiB 이하에서는 절반을 힙에 배정합니다. 그보다 큰 제한에서는 최소 64MiB 또는
전체의 25% 중 큰 값을 JVM의 네이티브 메모리·스택·메타데이터 등을 위해 남깁니다.
MiB 정수 단위로 내림하며 제한을 1MiB 올릴 때 힙도 최대 1MiB만 증가합니다.
전체 메모리는 기존 isolate cgroup 제한으로 계속 집행합니다.

`-Xmx` 전체를 하나의 배열로 할당할 수 있다는 뜻은 아닙니다. 기존 SerialGC의 세대
구분과 객체 오버헤드·단편화도 할당 가능 크기에 영향을 줍니다. 시간/언어 보정,
`-Xss256k`, GC 설정, 컴파일 제한은 변경하지 않습니다.

`app/java_heap_smoke.py`는 큰 단일 배열, 600/1200MiB의 실제 데이터 유지,
문자열 객체/정렬, 작은/큰 제한에서 OOM→MLE를 검증합니다. 운영 큐에 제출하거나
기존 판정을 변경하지 않으며, 배포 스크립트가 교체 전 각 VM의 isolate에서 실행합니다.
서버의 별도 제한 컨테이너(OpenJDK 17)에서 9개 실행을 확인했으며, 그 결과를 다른
JDK/VM에서의 성공으로 간주하지 않습니다. VM 검증이 실패하면 해당 노드를 교체하지 않습니다.

현재 UI의 2026-09-26 메모리 벤치마크는 이전 정책의 역사적 실행 기록입니다.
새 정책의 재측정값으로 해석하지 마세요. 유효 128MiB의 힙 64MiB는 유지됩니다.

`pve01` root에서 `bash update-java-heap.sh 1 2 3 4 5 6`처럼 명시한 노드만
순차 적용합니다. 노드 SSH/sudo 비밀번호는 터미널에서 한 번 입력합니다.
스크립트는 고정 커밋의 코드에서 힙 메서드만 추출하며, 알 수 없는 커스텀 정책은
덮어쓰지 않습니다. 각 VM에서 기존 이미지를 기반으로 네트워크 다운로드 없이
후보 이미지를 만들고, 실제 isolate 검증이 통과한 뒤에 교체합니다.
실패하면 이후 노드는 중단하며, 교체 이후 실패는 백업 이미지와 소스로 복구합니다.
백업은 각 VM의 `/var/backups/zoj-java-heap/`에 보관됩니다.

제출을 중지한 유지보수 시간에 실행하세요. 진행 중인 로컬 작업 디렉터리는 검사하지만
큐를 원자적으로 drain하지 않으므로 새 제출을 막는 기능은 아닙니다. 과거 판정은
자동으로 다시 채점하지 않습니다. 모든 대상 노드의 교체를 마친 뒤 제출을 재개하세요.

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
