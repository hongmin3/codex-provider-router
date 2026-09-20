# Codex Provider Router 사양서

<!-- spec-template: v1 -->

| 항목 | 값 |
|---|---|
| Document Version | 1.0.0 |
| Project Version | 1.0.0 |
| Last Updated | 2026-09-19 |
| Status | active |
| Owner | Router 운영 담당자 (역할명) |

이 문서는 `codex-provider-router`가 **어떻게 동작해야 하는가**를 정의하는 기준이다.
코드가 현재 그렇게 동작한다는 사실은 사양이 아니다. 사양과 구현이 다르면 코드에 맞춰 이
문서를 고치지 말고 `SPEC / CODE MISMATCH`로 식별한다 — 절차는 `AGENTS.md`에 있다.

## 문서 경계 — 이 문서와 `USAGE_AND_SPEC.md`

이 프로젝트에는 이미 `USAGE_AND_SPEC.md`가 있다. 두 문서는 같은 내용을 반복하지 않는다.

| 문서 | 담는 것 |
|---|---|
| `SPEC.md` (이 문서) | Router가 **반드시 만족해야 하는 규범적 요구사항**(REQ/NFR), 그 검증(TEST), 추적성, 미확정 사항 |
| `USAGE_AND_SPEC.md` | 사용자가 실제로 입력하는 **명령·옵션·출력 예시**, 기준 환경, 설치·상태·로그 **경로**, 상태 머신·오류 분류의 상세 표 |

명령 사용법, 경로, 화면 출력, 환경 값이 필요하면 이 문서가 아니라 `USAGE_AND_SPEC.md`를
본다. 이 문서는 그 내용을 복제하지 않고 절 번호로 참조한다.

## 1. 목적

ChatGPT 로그인으로 쓰던 Codex 사용자가 **명령을 바꾸지 않고**(`codex --yolo` 그대로)
OpenAI usage limit 상황에서도 작업을 이어갈 수 있게 하는 것이 목적이다. Router는 OpenAI를
primary로 유지하고, 한도가 실제로 소진된 경우에만 사용자 확인을 받아 DeepSeek로 전환하며,
한도가 풀리면 다시 OpenAI로 돌아온다.

성공 조건: 사용자는 provider별 명령을 외우지 않고, 전환 때문에 작업 맥락·파일·Codex 대화
기록을 잃지 않는다.

## 2. 프로젝트 범위

### 포함

- `codex` 명령을 가로채는 wrapper와 provider 선택·전환·복귀 로직.
- DeepSeek fallback profile(모델·reasoning·catalog)의 영구 설정과 관리 명령.
- 전환 시점의 context checkpoint 생성과 Codex persisted thread resume.
- DeepSeek 잔액 조회·경고와 provider 상태 cache.
- 작업 성격에 맞는 모델을 추천·승인 실행하는 `ai` Model Router.
- macOS(zsh) 및 Windows(PowerShell·cmd) 설치·제거 스크립트와 자격증명 저장소 연결.

### 제외

- Codex binary 자체의 수정, ChatGPT 인증 방식 변경, API Key 로그인으로의 전환.
- OpenAI 모델 선택 — Codex TUI의 `/model`이 소유하며 Router는 덮어쓰지 않는다.
- Codex 세션 내부의 provider hot-swap. Codex CLI가 제공하지 않으므로 가정하지 않는다.
- DeepSeek 외 제3 provider로의 연쇄 전환.
- MCP·plugin·project trust 설정 관리.
- 토큰 단가 기반 비용 hard-stop(13절 참조).

## 3. 시스템 구성

| 구성 요소 | 책임 |
|---|---|
| `src/codex_router.py` | wrapper 진입점. provider 선택, PTY 실행, 상태 머신, checkpoint, 로그, 잔액·모델·catalog 명령 |
| `src/model_router.py` | `ai` 명령의 prompt 점수화·모델 후보 생성·provider 상태 cache·잔액 조회 |
| `config/config.toml` | Router 기본 설정 template |
| `config/deepseek.config.toml` | Codex가 읽는 DeepSeek provider profile template |
| `config/models.toml` | `ai`가 쓰는 모델 ID·reasoning·context·가격·capability 목록 |
| `scripts/install.sh`, `scripts/uninstall.sh` | macOS 설치·제거 |
| `scripts/install.ps1`, `scripts/uninstall.ps1`, `scripts/codex-router.ps1`, `scripts/codex.cmd`, `scripts/deep.cmd`, `scripts/codex-router.cmd` | Windows 설치·제거·shim |

외부 경계는 Codex binary, OpenAI(ChatGPT 로그인), DeepSeek API, macOS Keychain /
Windows DPAPI 네 곳이다. 설치 후 실제 경로 배치는 `USAGE_AND_SPEC.md` 10절에 있다.

## 4. 전체 동작 흐름

1. 사용자가 `codex ...`를 실행하면 PATH 앞단의 Router wrapper가 받는다.
2. Router가 DeepSeek catalog 확인 주기를 점검하고(REQ-CATALOG-001) provider를 고른다.
3. 상태가 `OPENAI_ACTIVE`면 OpenAI로 Codex를 실행한다(REQ-ROUTE-001).
4. 세션 중 OpenAI 한도 소진 문구가 보이면 checkpoint를 만들고(REQ-CTX-001) 사용자에게
   전환 여부를 묻는다(REQ-ROUTE-002).
5. 승인하면 같은 thread를 DeepSeek로 resume하고 상태를 cooldown으로 바꾼다.
6. 다음 실행부터는 probe 시각에 도달했을 때 OpenAI를 확인하고, 성공하면 복귀한다
   (REQ-ROUTE-004).
7. `deep` 계열 명령과 `FORCE_DEEPSEEK=1`은 1~6과 무관하게 DeepSeek로 직행한다
   (REQ-ROUTE-005).
8. DeepSeek로 실행하기 전에 오늘·이번 달 누적 비용과 fallback 경과 시간을 확인하고, 한도를
   넘었으면 실행하지 않고 이유와 조정 방법을 알린다(REQ-COST-001).

상태 머신의 전이 표는 `USAGE_AND_SPEC.md` 5절에 있다.

## 5. 기능 요구사항

ID 규칙: `REQ-<CATEGORY>-NNN`. CATEGORY는 대문자·숫자, NNN은 세 자리.
한 번 부여한 ID는 재사용하거나 의미를 바꾸지 않는다. 삭제한 ID를 다른 기능에 돌려쓰지 않는다.

### REQ-ROUTE-001

#### 목적
사용자가 명령을 바꾸지 않고 기존 Codex를 그대로 쓰게 한다.

#### 동작
- Router는 `codex`라는 이름으로 호출되었을 때 wrapper로 동작하고, 그 외 이름
  (`codex-router`, `deep`, `ai`)으로는 각자의 명령 표면을 제공한다.
- 실제 Codex는 **절대 경로**로 호출해 wrapper가 자기 자신을 재귀 호출하지 않는다.
- 자식 Codex process에는 우회 표시를 넘겨 중첩 wrapper가 생기지 않게 한다.
- 비대화형 실행(stdin이 TTY가 아님)은 강제 DeepSeek 지정이 없는 한 원본 Codex로 그대로
  넘긴다.

#### 기대 결과
`codex --yolo`는 Router 설치 전과 동일하게 ChatGPT 로그인 Codex를 띄운다.

#### 관련 구현
`src/codex_router.py`

#### 관련 테스트
(없음)

### REQ-ROUTE-002

#### 목적
provider 전환은 사용자가 모르는 사이에 일어나면 안 된다.

#### 선행 조건
대화형 세션에서 OpenAI 한도 소진이 감지되었거나, 이전 세션의 cooldown 때문에 DeepSeek가
선택되려 한다.

#### 동작
- 전환 전에 `y/N`로 묻고, **기본값은 전환하지 않음**이다.
- `y`/`yes`(대소문자 무시)만 승인으로 본다. 그 외 입력, 빈 입력, EOF, Ctrl-C는 거절이다.
- stdin이 TTY가 아니면 묻지 않고 거절로 처리한다 — 무인 실행이 조용히 유료 provider로
  넘어가지 않는다.
- 사용자가 거절하고 OpenAI 세션이 정상 종료되면 상태를 `OPENAI_ACTIVE`로 되돌린다.
- 명시적 요청(REQ-ROUTE-005)은 이 확인을 거치지 않는다.

#### 예외 처리
승인했지만 DeepSeek Key가 없으면 Codex를 다시 띄우지 않고 안내 후 exit code 78로 끝낸다.

#### 관련 구현
`src/codex_router.py`

#### 관련 테스트
TEST-ROUTE-001

### REQ-ROUTE-003

#### 목적
잔여 한도 안내를 소진으로 오인해 전환하지 않는다.

#### 입력
OpenAI Codex가 TTY에 출력한 문구.

#### 동작
- `hit`, `reached`, `exceeded`, `no usage left`, `0% left`, HTTP 429처럼 **실제 소진**을
  뜻하는 문구만 전환 후보로 판단한다.
- `usage limit resets available`, `less than 25% left`, `26% left`, `resets in ...`,
  Codex `/status` 화면의 `Weekly limit: 70% left` 같은 정상 안내는 전환 후보에서 제외한다.
  양수 잔여율이 `0% left`에 부분 일치해서는 안 된다.
- 인증 실패(401/403)는 조용히 fallback하지 않는다. network/5xx는 Codex의 제한된 retry에
  맡긴다.
- 한도 종류(weekly / session / 그 외)를 구분해 상태에 남긴다.
- reset 시각은 provider가 **절대 ISO 시각**을 준 경우에만 저장한다. `in 5h` 같은 상대
  표현에서 시각을 만들어내지 않는다.

#### 기대 결과
잔여 한도가 남아 있는 세션은 중단되지도, 전환 질문을 띄우지도 않는다.

#### 관련 구현
`src/codex_router.py`

#### 관련 테스트
TEST-ROUTE-002

### REQ-ROUTE-004

#### 목적
한도가 풀리면 사람 개입 없이 OpenAI로 돌아온다.

#### 동작
- 전환 승인 시 상태를 `OPENAI_COOLDOWN`으로 두고 다음 probe 시각을 계산한다.
- probe 간격은 설정된 단계(기본 10, 20, 30, 60분)를 따르고 마지막 값에서 멈춘다.
- probe는 read-only·승인 없음·ephemeral 모드의 최소 요청이며 실패해도 사용자 작업을
  중단시키지 않는다.
- probe가 성공하면 상태를 `OPENAI_ACTIVE`로 되돌리고 cooldown 관련 필드를 모두 제거한다.
- **DeepSeek 세션이 진행 중인 동안에는 provider를 바꾸지 않는다.** 복귀는 다음 실행부터다.

#### 관련 구현
`src/codex_router.py`

#### 관련 테스트
TEST-ROUTE-003

### REQ-ROUTE-005

#### 목적
OpenAI 상태와 무관하게 DeepSeek를 쓰고 싶을 때 한 단어로 지정할 수 있어야 한다.

#### 입력
`deep [codex] <CODEX_ARGS>`, `codex-router deep|deepseek <CODEX_ARGS>`,
`FORCE_DEEPSEEK=1 codex <CODEX_ARGS>`.

#### 동작
- 세 경로 모두 상태·cooldown·확인 절차와 무관하게 DeepSeek profile로 Codex를 시작한다.
- `deep`은 `sudo`처럼 접두어로 읽히므로 첫 인자가 `codex`이면 제거한다. macOS와 Windows가
  같게 동작한다.
- 사용자가 준 `-p/--profile`, `-m/--model`(및 `=` 형태)은 제거하고 Router의 profile과
  fallback 모델·reasoning을 강제한다. `--` 뒤의 인자는 prompt로 보고 손대지 않는다.
- 비대화형 `deep exec "..."`도 DeepSeek profile을 유지한다.
- `FORCE_DEEPSEEK=1`은 그 실행 한 번에만 적용되며 Router의 OpenAI 상태와 cooldown을
  변경하지 않는다. 값 `1`은 모델 번호가 아니라 boolean flag다.

#### 예외 처리
DeepSeek Key가 없으면 Codex를 시작하지 않고 안내 후 exit code 78로 종료한다.

#### 관련 구현
`src/codex_router.py`, `scripts/deep.cmd`, `scripts/codex-router.ps1`

#### 관련 테스트
TEST-ROUTE-004

### REQ-STATE-001

#### 목적
상태 파일이 깨져도 사용자가 Codex를 못 쓰는 상황이 생기면 안 된다.

#### 동작
- 상태는 `OPENAI_ACTIVE`, `OPENAI_COOLDOWN`, `DEEPSEEK_ACTIVE` 세 가지만 유효하다.
- 알 수 없는 상태값, 잘못된 타입, 음수 시도 횟수, timezone 없는 probe 시각, 읽기 실패는
  모두 기본값 `OPENAI_ACTIVE`로 복구한다. 예외로 중단하지 않는다.
- 상태 저장은 임시 파일에 쓴 뒤 교체하는 방식으로 하고 권한은 소유자 전용이다.
- `codex-router reset`은 Router 상태만 초기화하며 ChatGPT 로그인·Codex config·MCP·
  DeepSeek Key·모델 설정을 지우지 않는다.

#### 관련 구현
`src/codex_router.py`

#### 관련 테스트
TEST-STATE-001

### REQ-CTX-001

#### 목적
provider가 바뀌어도 작업 맥락을 잃지 않는다.

#### 동작
- 한도 감지 시점에 작업 디렉터리별 checkpoint를 만들고 시각·원인·작업 디렉터리와 함께
  `git status`, `git diff`, 그리고 존재하는 `AGENTS.md`·`README.md`·`progress.md`를
  담는다.
- git 명령 실패·timeout은 checkpoint 생성을 실패시키지 않고 실패 사실만 남긴다.
- 대화·요청·tool call·명령 결과·TODO는 Codex persisted thread에 있으므로 복제하지 않고,
  전환 시 방금 종료된 OpenAI 세션의 session id를 rollout 메타에서 찾아 같은 thread를
  `resume <session_id>`로 재개한다. session id를 식별하지 못하면 resume picker에서
  사용자가 직접 선택한다.
- 오류가 났다는 이유로 작업 파일·Codex thread·checkpoint를 삭제하지 않는다.

#### 관련 구현
`src/codex_router.py`

#### 관련 테스트
TEST-CTX-001

### REQ-MODEL-001

#### 목적
DeepSeek 모델과 reasoning은 사용자가 정한 값이 다음 실행에서도 유지되어야 한다.

#### 동작
- fallback 모델과 reasoning은 Router config에 저장되고 새 process에서도 같은 값이 읽힌다.
- 모델은 alias(`flash`, `pro`, `vision`)로 지정하며 Router가 임의로 바꾸지 않는다.
- reasoning 허용값은 `low`, `high`, `max`이고 기본값은 `high`다. 지원하지 않는 값은
  저장하지 않고 exit code 2로 끝낸다.
- config를 다시 쓸 때 template에 있던 설명 주석을 잃지 않는다.
- OpenAI 모델 선택값은 이 설정과 독립이며 Router가 덮어쓰지 않는다.

#### 관련 구현
`src/codex_router.py`, `config/config.toml`

#### 관련 테스트
TEST-MODEL-001

### REQ-CATALOG-001

#### 목적
DeepSeek 모델 목록이 바뀌어도 사용자가 수동으로 추적하지 않게 한다.

#### 동작
- `codex` 실행 시 공식 catalog를 확인하되 빈도는 설정된 주기(기본 24시간)를 넘지 않는다.
- 내려받은 내용은 JSON 구조와 model slug를 검증한다. 중복 slug나 형식 오류는 거절한다.
- 갱신 전 기존 catalog를 백업하고, 검증 실패·network 실패·timeout이면 **기존 catalog를
  그대로 유지**한다.
- 새 slug는 터미널에 알리고 목록에 `new`로 표시한다. **현재 fallback 모델은 자동으로 바꾸지
  않는다.**
- 현재 모델이 공식 catalog에서 사라졌으면 갱신을 중단하고 경고한다.
- catalog 파일이 없거나 읽을 수 없으면 알려진 기본 모델로 동작을 계속한다.

#### 관련 구현
`src/codex_router.py`

#### 관련 테스트
TEST-CATALOG-001

### REQ-BALANCE-001

#### 목적
DeepSeek 잔액이 떨어져 작업이 끊기기 전에 사용자가 알 수 있어야 한다.

#### 동작
- `codex-router balance`(및 `ai balance`)는 DeepSeek 공식 잔액 endpoint에서 USD 총액·
  충전·증정 잔액과 사용 가능 여부를 가져와 표시한다.
- `--json`은 `status`, `checked_at`, `threshold_usd`, `low_balance`, `balance`를 담은
  기계 판독 출력을 낸다.
- 조회 성공은 exit code 0, 실패는 1이다. **잔액이 기준 미만이어도 조회가 성공했으면 0**이다.
- 경고 기준은 설정값이며 기본값은 USD 1.00이다. 읽을 수 없는 값은 기본값으로 되돌린다.
- 조회 결과는 provider 상태 cache에 기록되어 `status` 계열 명령이 network 호출 없이 같은
  금액을 보여준다.
- DeepSeek 세션 시작 배너에 잔액을 함께 표시하고, 세션이 끝나면 다시 조회한 잔액을 한 줄
  보여준다. 시작 시 금액은 cache에서 읽고, cache가 TTL보다 오래되었으면 **자식 process**로
  갱신한다 — PTY를 `forkpty()`하는 process에서는 잔액 조회를 하지 않는다.
- 잔액 부족은 경고일 뿐 provider를 중단시키지 않는다. DeepSeek가 계정 소진을 보고할 때만
  라우팅에서 제외한다.
- 잔액 조회 실패는 세션을 중단시키지 않고 금액을 지어내지도 않는다.
- Codex TUI가 그리는 화면에는 잔액 줄을 넣지 않는다(NFR-UX-001).

#### 관련 구현
`src/codex_router.py`, `src/model_router.py`

#### 관련 테스트
TEST-BALANCE-001, TEST-BALANCE-002

### REQ-ERR-001

#### 목적
DeepSeek 실패는 사용자가 다음에 무엇을 해야 하는지 알 수 있는 형태로 안내한다.

#### 입력
DeepSeek 세션의 비정상 exit code와 TTY의 최근 출력.

#### 동작
- 실패를 `BILLING`, `QUOTA`, `AUTH`, `NETWORK`, `SERVER`, `UNKNOWN`으로 분류하고 각각
  한국어 원인·해결 방법을 표시한다. 분류 기준은 서로 겹치지 않는다.
- HTTP 402(잔액)와 429(속도 제한)는 서로 다른 원인으로 구분한다.
- 어느 분류에도 맞지 않으면 추측하지 않고 `UNKNOWN`으로 두고 로그 확인을 안내한다.
- 공통으로 작업 파일과 Codex 대화 기록이 삭제되지 않았음을 알린다.
- DeepSeek도 쓸 수 없으면 제3 provider로 넘어가지 않고 이유와 대응 방법을 표시한 뒤
  종료한다.

#### 관련 구현
`src/codex_router.py`

#### 관련 테스트
TEST-ERR-001

### REQ-AIROUTE-001

#### 목적
작업 성격에 맞는 모델을 추천하되 실행 여부는 사용자가 정한다.

#### 동작
- prompt와 저장소 metadata로 점수를 매겨 모델과 reasoning을 추천한다. 저장소 **내용**은
  분류기에 넘기지 않는다.
- 현재 사용 가능한 provider의 모델만 후보로 만든다. OpenAI 한도면 GPT 계열을, DeepSeek
  잔액 소진·인증 실패면 DeepSeek 계열을 후보에서 제외한다.
- 추천은 승인 UI를 거쳐야 실행된다. 자동 전환·자동 상향은 기본적으로 하지 않는다.
- 실행은 runtime 인자(`--model`, reasoning override)로만 하고 **전역 Codex config를
  수정하지 않는다.** MCP·trust·history·승인 정책은 기존 설정을 그대로 쓴다.
- 원본 prompt는 변형 없이 하나의 인자로 전달한다(줄바꿈·한글 포함).
- 같은 작업 유형이 반복 실패하면 원인별로 상위 모델을 추천하되 실행은 다시 승인을 받는다.
- 사용 기록에 원본 prompt를 저장하지 않고 hash만 남긴다.

#### 관련 구현
`src/model_router.py`, `config/models.toml`

#### 관련 테스트
TEST-AIROUTE-001

### REQ-COST-001

#### 목적
설정에 적어 둔 비용·시간 한도가 표시용 값이 아니라 **실제 실행 차단**으로 동작한다.

#### 동작
- 한도는 `[cost] daily_limit_usd`, `[cost] monthly_limit_usd`,
  `[routing] max_fallback_minutes` 세 가지다. **0 이하는 한도 없음**을 뜻한다.
- 한도 계산 대상은 과금 provider인 DeepSeek이다. OpenAI(ChatGPT 로그인)는 정액제이므로
  비용 한도에 넣지 않고, 그 사실을 `cost` 출력에 적는다.
- 비용은 추정이 아니라 실제 token 사용량으로 계산한다. 세션 전에 Codex rollout 파일
  (`~/.codex/sessions/**/rollout-*.jsonl`)의 크기를 기록하고, 세션 뒤 늘어난 부분의
  `last_token_usage`만 합산해 `config/models.toml` 단가로 환산한다. `resume`으로 이어진
  세션도 이번 실행분만 계산된다.
- 세션 결과는 비용 원장(`~/.codex/router/spend.jsonl`)에 기록한다. `ai` 실행 기록
  (`~/.codex/router/logs/model-router-usage.jsonl`)도 같은 계산에 포함한다.
- DeepSeek 실행 직전에 오늘·이번 달 누적과 fallback 경과 시간을 확인한다. 한도에
  도달했으면 **실행하지 않고** 종료 코드 `75`로 끝내며, 어느 한도인지·현재 누적·조정
  방법·`codex-router cost` 안내를 출력한다.
- fallback 경과 시간은 OpenAI 한도가 감지된 시각(`cooldown_started_at`)부터 잰다. 기준
  시각이 없으면(예: `deep` 직접 실행) 시간 한도는 적용하지 않는다.
- 차단 결정은 로그에 `blocked` 이벤트로 남긴다.
- `codex-router cost [--json]`이 한도·오늘/이번 달 누적·fallback 경과·커버리지를 보여
  준다. 커버리지는 "기록 수 / token이 있는 기록 수 / 비용 근거가 없는 기록 수"다.

#### 예외 처리
rollout 파일이나 원장 파일을 읽지 못하면 그 기록은 건너뛰고 계산을 계속한다. 단가를 모르는
모델은 `cost_usd: null`로 기록하고 한도 계산에서 0으로 본다. 이 경우 `cost`의 "비용 근거가
없는 기록" 수가 늘어나므로, 0으로 보이는 누적을 그대로 신뢰하지 않는다.

#### 관련 구현
`src/codex_router.py` (`cost_limits`, `rollout_usage_since`, `record_session_spend`,
`spend_totals`, `limit_block`, `enforce_limit`, `cost_command`)

#### 관련 테스트
TEST-COST-002

## 6. 비기능 요구사항

### NFR-SEC-001

DeepSeek API Key는 **process environment 또는 OS 자격증명 저장소**(macOS Keychain의
`codex-router-deepseek` 항목, Windows는 DPAPI로 암호화한 파일)에서만 읽는다.

- Key를 평문 파일·Router config·저장소·터미널 출력·로그에 남기지 않는다. 설치 시 import한
  Key 파일은 설치본으로 복사하지 않는다.
- Key 입력은 화면에 표시하지 않고 두 번 입력해 일치를 확인한다. 불일치·빈 값은 exit code 2다.
- 로그에는 `timestamp`, `event`, provider, model, reasoning, 전환 사유, probe 결과,
  checkpoint 경로, `failure_type`, `exit_code`만 남긴다. 원본 오류 본문, token,
  Authorization header는 남기지 않는다.
- 상태·설정·로그·checkpoint 파일 권한은 소유자 전용(파일 600, 디렉터리 700)이다.
- 로그는 월별 파일로 쓰고 최근 6개만 보존한다.

이 문서와 `knowledge/`에도 Key 값이나 개인 절대경로를 적지 않는다.

### NFR-COMPAT-001

Router는 기존 Codex 환경을 보존한다. Codex config, ChatGPT 인증, MCP 설정, plugin 설정,
project trust, OpenAI 모델 선택, 원본 Codex 실행 파일을 임의로 바꾸거나 지우지 않는다.

- 설치는 시작 전에 백업 디렉터리를 만든다. 설치 도중 catalog 내려받기가 실패하면 기존
  설치·설정을 건드리지 않은 채 중단한다.
- 제거는 wrapper·profile·Router 설정·자격증명 항목만 지우고 기존 Codex 인증과 config는
  남긴다. Router 상태·로그·checkpoint는 즉시 삭제하지 않고 휴지통으로 옮긴다.
- macOS와 Windows는 같은 명령 이름과 같은 인자 해석을 제공한다.
- 긴급 시 원본 Codex를 절대 경로로 직접 실행해 Router를 우회할 수 있어야 한다.

### NFR-UX-001

Router 때문에 Codex TUI 화면이 깨지지 않는다.

- 자식 PTY에 실제 터미널의 rows/columns를 복사하고, 터미널 크기가 바뀌면 `SIGWINCH`를 받아
  즉시 다시 동기화한다.
- Codex binary가 그리는 화면(예: TUI의 `/status`)에 wrapper가 줄을 덧붙이지 않는다. Router
  안내는 자체 배너와 stderr로만 낸다.

### NFR-COST-001

라우팅 자체가 유료 호출을 늘리지 않는다.

- `ai`의 LLM 분류기는 기본 OFF다. 이때 라우팅에 드는 추가 LLM token은 0이다.
- 분류기를 켜더라도 저비용 모델·낮은 reasoning·제한된 출력 token만 쓰며 고가 모델을
  분류에 쓰지 않는다.
- provider 상태 갱신은 공식 상태·잔액 endpoint만 쓰고 quota 확인용 LLM 요청을 만들지
  않는다. reset 시각을 얻지 못하면 `unavailable`로 표시한다.
- provider 상태는 cache하고 갱신 빈도에 상한을 둔다.

## 7. 데이터 사양

Router가 다루는 데이터는 (1) provider 상태와 probe 일정, (2) DeepSeek 모델 catalog,
(3) 전환 시점 checkpoint, (4) 이벤트 로그, (5) provider 상태·잔액 cache, (6) `ai` 사용
기록이다. 실제 파일 배치와 필드 목록은 `USAGE_AND_SPEC.md` 8·10·11절에 있다.

규범적 제약은 다음과 같다.

- 어떤 데이터에도 API Key, token, cookie, 원본 오류 본문, 원본 prompt를 담지 않는다
  (NFR-SEC-001, REQ-AIROUTE-001).
- checkpoint는 작업 디렉터리별로 분리하고 사용자가 지우기 전까지 보존한다.
- 로그는 월별 파일 최근 6개만 보존한다.
- 비용 원장(`spend.jsonl`)은 세션별 token 수·환산 비용·시각만 담고 prompt나 응답 내용은
  담지 않는다. 한도 판정을 위해 월 단위로 누적되며 자동 삭제하지 않는다.
- 상태·cache 파일은 손상되었을 때 예외로 중단하지 않고 안전한 기본값으로 되돌린다
  (REQ-STATE-001, REQ-CATALOG-001).

## 8. Configuration 사양

설정 항목과 기본값은 `config/config.toml`이 원본이고, Codex가 읽는 DeepSeek profile은
`config/deepseek.config.toml`, `ai`의 모델 목록은 `config/models.toml`이다. 설치 후 경로는
`USAGE_AND_SPEC.md` 10절에 있다.

규범적 제약은 다음과 같다.

- API Key는 어떤 설정 파일에도 저장하지 않는다(NFR-SEC-001).
- 설정 파일이 없어도 모든 항목은 코드의 기본값으로 동작해야 한다. 설정 파일은 기본값을
  덮어쓰기만 한다.
- `[cost] daily_limit_usd`·`monthly_limit_usd`와 `[routing] max_fallback_minutes`는
  실행을 막는 한도다(REQ-COST-001). 값을 0으로 두면 그 한도를 쓰지 않는다. 비용 한도는
  과금 provider에만 적용하고, `max_fallback_minutes`는 OpenAI 한도 감지 후 자동 fallback
  구간에만 적용한다.
- 설정을 다시 쓸 때 template의 설명 주석을 보존한다(REQ-MODEL-001).
- 환경 변수는 `DEEPSEEK_API_KEY`(Key 출처)와 `FORCE_DEEPSEEK`(그 실행 한 번의 강제 전환)
  둘만 사용자 인터페이스다. 그 외 내부 표시용 변수는 사용자 문서에 노출하지 않는다.

## 9. 오류 처리 정책

| 상황 | 정책 |
|---|---|
| OpenAI 한도 소진 | checkpoint 생성 → 사용자 확인 → 승인 시에만 전환 (REQ-ROUTE-002) |
| OpenAI 잔여량 안내 | 실패로 보지 않는다. 세션을 중단하지도 질문하지도 않는다 (REQ-ROUTE-003) |
| OpenAI 인증 실패 | 조용히 fallback하지 않는다. 사용자에게 드러낸다 (REQ-ROUTE-003) |
| OpenAI network/5xx | Codex의 제한된 retry에 맡기고 Router는 전환하지 않는다 |
| DeepSeek 실패 | 분류 후 한국어 원인·해결 방법 안내. 무한 retry 하지 않는다 (REQ-ERR-001) |
| DeepSeek Key 없음 | Codex를 시작하지 않고 exit code 78 (REQ-ROUTE-002, REQ-ROUTE-005) |
| 잔액 조회 실패 | 세션을 중단하지 않고 금액을 지어내지 않는다 (REQ-BALANCE-001) |
| catalog 내려받기·검증 실패 | 기존 catalog 유지, 설치본 미변경 (REQ-CATALOG-001, NFR-COMPAT-001) |
| 상태 파일 손상 | `OPENAI_ACTIVE`로 복구하고 계속 진행 (REQ-STATE-001) |
| probe 실패 | cooldown 연장. 사용자 작업을 중단시키지 않는다 (REQ-ROUTE-004) |
| DeepSeek도 불가 | 제3 provider로 넘어가지 않고 이유를 표시한 뒤 종료 (REQ-ERR-001) |

공통 원칙 세 가지.

1. **부분 성공을 허용한다.** 부가 기능(잔액 조회, catalog 확인, checkpoint의 git 캡처)의
   실패가 Codex 세션 자체를 막지 않는다.
2. **모르면 지어내지 않는다.** 분류 불가는 `UNKNOWN`, 시각 불명은 `unavailable`,
   금액 불명은 표시 생략이다.
3. **사용자 자산은 보존한다.** 어떤 오류 경로에서도 작업 파일·Codex thread·checkpoint·
   기존 Codex 설정을 삭제하지 않는다.

## 10. 보안 요구사항

NFR-SEC-001이 규범이다. 자격증명 저장 위치와 Key 변경 명령의 사용법은
`USAGE_AND_SPEC.md` 9절에 있다. 저장소(이 Git repository)에는 Key 파일, Keychain 항목 값,
개인 절대경로를 넣지 않는다.

## 11. 테스트 사양

ID 규칙: `TEST-<CATEGORY>-NNN`. 아래 항목은 모두 실재하는 테스트 파일에 대응한다.
공통 선행 조건: 프로젝트 루트에서 `python3 -m unittest discover -s tests`.

### TEST-ROUTE-001

#### 검증 대상
REQ-ROUTE-002

#### 선행 조건
없음. 확인 함수와 TTY 여부를 직접 호출해 검증한다.

#### 절차
`tests/test_router.py`의 `test_confirm_answer_accepts_only_yes_variants`,
`test_confirm_fallback_declines_when_stdin_is_not_a_tty`,
`test_mark_openai_active_resets_state`를 실행한다.

#### Expected Result
`y`/`yes` 계열만 승인이고, stdin이 TTY가 아니면 묻지 않고 거절하며, 복귀 시 cooldown
관련 필드가 모두 제거된다.

### TEST-ROUTE-002

#### 검증 대상
REQ-ROUTE-003

#### 선행 조건
없음. 실제 Codex 출력 문구를 문자열로 넣어 분류기를 검증한다.

#### 절차
`tests/test_router.py`의 `test_usage_exhaustion_messages_trigger_fallback`,
`test_usage_notices_do_not_trigger_fallback`, `test_openai_window_classification`,
`test_reset_time_is_only_accepted_when_provider_supplies_absolute_iso`와
`tests/test_hardening.py`의 `test_positive_remaining_percent_does_not_interrupt_session`을
실행한다.

#### Expected Result
소진 문구만 전환 후보가 되고, 잔여량·reset 안내와 `Weekly limit: 70% left`는 세션을
중단시키지 않으며, 절대 ISO 시각이 아닌 reset 표현에서 시각을 만들어내지 않는다.

### TEST-ROUTE-003

#### 검증 대상
REQ-ROUTE-004

#### 선행 조건
없음.

#### 절차
`tests/test_router.py`의 `test_staged_backoff`를 실행한다.

#### Expected Result
probe 간격이 설정된 단계를 순서대로 따르고 마지막 값을 넘지 않는다.

### TEST-ROUTE-004

#### 검증 대상
REQ-ROUTE-005

#### 선행 조건
Key 없음 경로는 자격증명 조회를 대체해 검증한다.

#### 절차
`tests/test_router.py`의 `test_deep_shortcut_accepts_an_optional_leading_codex_word`,
`test_deepseek_shortcut_without_key_exits_before_starting_codex`,
`test_deepseek_args_removes_user_provider_overrides`,
`tests/test_hardening.py`의 `test_forced_noninteractive_execution_keeps_deepseek`,
`test_deepseek_args_respects_prompt_separator_and_equals_options`,
`tests/test_windows_scripts.py`의 `test_force_flag_injects_deepseek_profile`,
`test_deep_shortcut_forces_deepseek_without_an_environment_variable`,
`test_deep_accepts_an_optional_leading_codex_token`을 실행한다.

#### Expected Result
`deep codex ...`와 `deep ...`이 동일하고, 사용자 provider override는 제거되며 `--` 뒤
prompt는 보존되고, Key가 없으면 Codex를 시작하지 않고 exit code 78이며, 비대화형 강제
실행도 DeepSeek profile을 유지한다. Windows shim이 같은 규칙을 따른다.

### TEST-STATE-001

#### 검증 대상
REQ-STATE-001

#### 선행 조건
임시 상태 파일에 손상된 값을 써 둔다.

#### 절차
`tests/test_hardening.py`의 `test_invalid_state_recovers_to_openai`,
`test_return_to_openai_preserves_catalog_metadata`를 실행한다.

#### Expected Result
손상된 상태는 `OPENAI_ACTIVE`로 복구되고, 복귀가 catalog 관련 metadata를 지우지 않는다.

### TEST-MODEL-001

#### 검증 대상
REQ-MODEL-001

#### 선행 조건
임시 config 경로를 사용한다.

#### 절차
`tests/test_router.py`의 `test_config_defaults_warn_below_one_dollar`,
`test_write_config_round_trips_the_warning_threshold`,
`test_write_config_keeps_the_shipped_explanatory_comments`와
`tests/test_model_router.py`의 `test_model_definitions_have_supported_reasoning`,
`test_reasoning_override_rejects_unsupported_value`를 실행한다.

#### Expected Result
저장한 설정이 새로 읽어도 같은 값이고, 기본값은 문서와 일치하며, 다시 쓴 config에 설명
주석이 남아 있고, 지원하지 않는 reasoning 값은 거절된다.

### TEST-CATALOG-001

#### 검증 대상
REQ-CATALOG-001

#### 선행 조건
공식 catalog 응답을 고정 문자열로 대체한다.

#### 절차
`tests/test_router.py`의 `test_parse_official_catalog`,
`test_parse_official_catalog_rejects_invalid_input`와 `tests/test_hardening.py`의
`test_catalog_rejects_wrong_json_shapes`, `test_refresh_creates_missing_catalog`,
`test_invalid_remote_catalog_preserves_existing_catalog`를 실행한다.

#### Expected Result
정상 catalog는 slug 목록으로 파싱되고, 형식이 깨진 응답은 거절되며, 잘못된 원격 catalog는
기존 catalog를 덮어쓰지 않는다.

### TEST-BALANCE-001

#### 검증 대상
REQ-BALANCE-001

#### 선행 조건
잔액 endpoint 응답과 자격증명 조회를 대체한다.

#### 절차
`tests/test_model_router.py`의 `DeepSeekBalanceTests`(파싱·기준값·`is_available` 처리·
API Key 미노출·402/network 처리·cache 기록)와 `tests/test_router.py`의
`BalanceCommandTests` 중 명령·`--json`·exit code·threshold·cache 관련 테스트를 실행한다.

#### Expected Result
USD 금액을 정확히 읽고, 기준 미만이면 `low_balance`로 표시하되 exit code는 0이며, 조회
실패는 exit code 1이고 금액을 지어내지 않는다. 출력 어디에도 API Key가 없다.

### TEST-BALANCE-002

#### 검증 대상
REQ-BALANCE-001

#### 선행 조건
provider 상태 cache를 임시 경로로 두고 실제 사용자 상태를 건드리지 않는 guard를 둔다.

#### 절차
`tests/test_router.py`의 `test_the_deepseek_banner_shows_the_remaining_balance`,
`test_the_fallback_banner_shows_the_remaining_balance`,
`test_a_fresh_cache_is_not_refetched`,
`test_a_stale_cache_is_refreshed_in_a_child_process`,
`test_no_balance_lookup_happens_in_the_process_that_forks_the_pty`,
`test_a_failed_refresh_never_interrupts_the_session`,
`test_a_finished_deepseek_session_reports_the_updated_balance`,
`test_an_openai_only_session_reports_no_deepseek_balance`,
`test_a_noninteractive_deep_run_prints_no_balance_line`를 실행한다.

#### Expected Result
배너에 cache 금액이 표시되고, 신선한 cache는 다시 조회하지 않으며, 오래된 cache는 자식
process로 갱신되고, PTY를 fork하는 process에서는 조회가 일어나지 않는다. 조회 실패가
세션을 중단시키지 않는다.

### TEST-ERR-001

#### 검증 대상
REQ-ERR-001

#### 선행 조건
없음. 오류 문구를 문자열로 넣어 분류기를 검증한다.

#### 절차
`tests/test_router.py`의 `test_error_classes_do_not_overlap`,
`test_deepseek_error_classification`,
`test_provider_failure_mapping_keeps_402_and_429_distinct`와
`tests/test_model_router.py`의 `test_tc09_rate_limit_is_distinct_from_balance`를 실행한다.

#### Expected Result
분류 기준이 서로 겹치지 않고, 402(잔액)와 429(속도 제한)가 다른 원인으로 남는다.

### TEST-AIROUTE-001

#### 검증 대상
REQ-AIROUTE-001

#### 선행 조건
provider 상태를 고정하고 Codex 실행을 대체한다.

#### 절차
`tests/test_model_router.py`의 `test_tc01_*`~`test_tc18_*`,
`test_original_prompt_is_preserved_as_single_argument`,
`test_manual_model_selection_persists_for_execution_decision`,
`test_usage_log_never_stores_original_prompt`,
`test_unavailable_without_reset_does_not_invent_time`를 실행한다.

#### Expected Result
작업 성격별 추천이 기대 모델·reasoning과 일치하고, 사용 불가 provider의 모델이 후보에서
빠지며, runtime 인자가 전역 config를 바꾸지 않고, 원본 prompt가 하나의 인자로 보존되며
사용 기록에 원본 prompt가 남지 않는다.

### TEST-SEC-001

#### 검증 대상
NFR-SEC-001

#### 선행 조건
임시 홈 디렉터리와 Key 파일 fixture를 사용한다.

#### 절차
`tests/test_install.py`의 `test_key_file_is_imported_but_not_copied_to_installed_files`,
`tests/test_model_router.py`의 `test_fetch_balance_never_returns_the_api_key`,
`test_usage_log_never_stores_original_prompt`,
`tests/test_windows_scripts.py`의 `test_cmd_shims_do_not_contain_secrets`,
`test_installer_accepts_key_file_and_ignores_plaintext_storage`를 실행한다.

#### Expected Result
Key는 자격증명 저장소에만 들어가고 설치본·shim·출력·사용 기록 어디에도 남지 않는다.

### TEST-COMPAT-001

#### 검증 대상
NFR-COMPAT-001

#### 선행 조건
설치 스크립트를 임시 디렉터리 대상으로 실행하거나 정적으로 검사한다.

#### 절차
`tests/test_install.py`의 `test_failed_catalog_download_leaves_installation_untouched`와
`tests/test_windows_scripts.py`의
`test_installer_preserves_original_codex_and_uses_dpapi`,
`test_normal_run_forwards_to_original_codex`,
`test_installer_decodes_the_downloaded_setup_script`,
`test_installer_deploys_the_deep_shim`를 실행한다.

#### Expected Result
catalog 내려받기가 실패하면 기존 설치를 건드리지 않고, 설치가 원본 Codex를 보존하며,
일반 실행은 원본 Codex로 전달된다.

### TEST-UX-001

#### 검증 대상
NFR-UX-001

#### 선행 조건
없음.

#### 절차
`tests/test_router.py`의 `test_sync_window_size`를 실행한다.

#### Expected Result
자식 PTY의 rows/columns가 원본 터미널 값과 같아진다.

### TEST-COST-001

#### 검증 대상
NFR-COST-001

#### 선행 조건
분류기 호출을 계측하도록 대체한다.

#### 절차
`tests/test_model_router.py`의
`test_tc14_high_confidence_never_calls_classifier_by_default`,
`test_tc15_low_confidence_can_call_enabled_classifier`,
`test_tc16_classifier_is_never_sol_or_pro`,
`test_tc17_runtime_arguments_do_not_mutate_global_config`,
`test_refresh_stamps_the_balance_check_time_like_a_direct_lookup`를 실행한다.

#### Expected Result
기본 설정에서는 분류기 호출이 0회이고, 켜더라도 고가 모델을 쓰지 않으며, 실행이 전역
config를 변경하지 않고, 상태 갱신이 매 세션 반복되지 않는다.

### TEST-COST-002

#### 검증 대상
REQ-COST-001

#### 선행 조건
Node가 아니라 Python `unittest`. 합성 rollout 파일과 임시 원장 경로를 쓴다. 실제
`~/.codex/router` 상태는 건드리지 않는다.

#### 절차
`tests/test_router.py`의 `CostLimitTests`를 실행한다. 합성 rollout에 `token_count`
이벤트를 넣어 세션 사용량을 계산하고, `last_token_usage`가 스냅샷 이후분만 합산되는지
확인한다(`resume` 대응). 오늘·월간 한도와 fallback 경과 시간을 각각 넘긴 상태에서
`limit_block`이 차단 사유를 돌려주는지, 한도를 0으로 두면 막지 않는지, 차단된 실행이
PTY를 만들지 않고 종료 코드 75로 끝나는지 확인한다.

#### Expected Result
한도를 넘은 DeepSeek 실행은 시작되지 않고 사유가 출력된다. OpenAI 실행과 한도 0 설정은
막지 않는다. 비용은 token 사용량 × `models.toml` 단가로 계산되고, 이어서 실행한 세션은
이번 실행분만 기록된다. 단가를 모르는 모델은 `cost_usd: null`로 남는다.

### TEST-CTX-001

#### 검증 대상
REQ-CTX-001

#### 선행 조건
임시 `SESSIONS_DIR`에 합성 rollout 파일을 쓰고, 실제 `~/.codex/router` 상태와
`~/.codex/sessions`은 건드리지 않는다.

#### 절차
`tests/test_router.py`의 `FallbackResumeTests`와 `BalanceCommandTests`의
`test_usage_limit_fallback_resumes_the_same_session_by_id`,
`test_usage_limit_fallback_opens_the_picker_when_no_session_id_is_found`를 실행한다.
usage limit 문구를 감지한 뒤 승인하면 두 번째 Codex 호출이 `resume --last`가 아니라
방금 끝난 세션의 id를 받는지, id를 찾지 못하면 picker(`resume`에 id 없음)로 가고 안내가
출력되는지 확인한다.

#### Expected Result
전환 argv에 `--last`가 없고, 식별된 session id가 `resume`의 위치 인자로 전달된다.
session id 탐색은 provider(`openai`)·작업 디렉터리(Unicode 정규화 포함)·originator가
일치하는 최신 rollout만 고르고, 시각 창 밖·다른 provider·다른 cwd·비 TUI 세션은
무시된다. id가 없으면 사용자에게 알리고 picker를 연다.

## 12. 요구사항 추적성

| Requirement | Implementation | Test | Status |
|---|---|---|---|
| REQ-ROUTE-001 | `src/codex_router.py` | (없음) | implemented |
| REQ-ROUTE-002 | `src/codex_router.py` | TEST-ROUTE-001 | verified |
| REQ-ROUTE-003 | `src/codex_router.py` | TEST-ROUTE-002 | verified |
| REQ-ROUTE-004 | `src/codex_router.py` | TEST-ROUTE-003 | verified |
| REQ-ROUTE-005 | `src/codex_router.py` | TEST-ROUTE-004 | verified |
| REQ-STATE-001 | `src/codex_router.py` | TEST-STATE-001 | verified |
| REQ-CTX-001 | `src/codex_router.py` | TEST-CTX-001 | implemented |
| REQ-MODEL-001 | `src/codex_router.py` | TEST-MODEL-001 | verified |
| REQ-CATALOG-001 | `src/codex_router.py` | TEST-CATALOG-001 | verified |
| REQ-BALANCE-001 | `src/codex_router.py`, `src/model_router.py` | TEST-BALANCE-001, TEST-BALANCE-002 | verified |
| REQ-ERR-001 | `src/codex_router.py` | TEST-ERR-001 | verified |
| REQ-AIROUTE-001 | `src/model_router.py` | TEST-AIROUTE-001 | verified |
| NFR-SEC-001 | `src/codex_router.py`, `scripts/install.sh`, `scripts/install.ps1` | TEST-SEC-001 | verified |
| NFR-COMPAT-001 | `scripts/install.sh`, `scripts/uninstall.sh`, `scripts/install.ps1`, `scripts/uninstall.ps1` | TEST-COMPAT-001 | verified |
| NFR-UX-001 | `src/codex_router.py` | TEST-UX-001 | verified |
| NFR-COST-001 | `src/model_router.py`, `config/config.toml` | TEST-COST-001 | verified |
| REQ-COST-001 | `src/codex_router.py` | TEST-COST-002 | implemented |

Status 값: `draft` (사양만 있음) / `implemented` / `verified` (실제 실행까지 확인) /
`deprecated`.

## 13. 미확정 사항

- **비용 계산의 커버리지.** 2026-09-19 이전 세션은 원장에 없어 한도 계산에 포함되지
  않는다. 또 rollout에 token 기록을 남기지 않은 실행은 `cost_usd: null`로 남는다.
  `codex-router cost`의 "비용 근거가 없는 기록" 수가 0이 아닌 동안에는 누적이 실제보다
  작을 수 있다.
- **원본 Codex 경로.** 원본 실행 파일 경로가 Apple Silicon Homebrew 기준 한 값으로
  하드코딩되어 있다. Intel Mac이나 npm prefix가 다른 설치에서의 기대 동작(탐지할 것인가,
  설치 시 확정할 것인가, 설정 항목으로 뺄 것인가)이 정해져 있지 않다.
- **같은 cwd에서 여러 Codex 세션이 동시에 열려 있을 때의 resume 대상.** 전환 시 방금
  종료된 OpenAI 세션의 id를 rollout 메타에서 찾아 `resume <session_id>`로 이어간다.
  같은 cwd에 동시에 열린 OpenAI 세션이 여럿이면 가장 최근에 갱신된 세션을 고르므로,
  둘 이상이 동시에 한도에 걸리면 대상이 섞일 수 있다. id를 찾지 못하면 picker에서
  사용자가 선택한다.
- **Windows uninstall이 남기는 DPAPI Key 파일.** 실행 중인 설치 디렉터리를 지울 수 없어
  Key 파일이 남고 경로만 안내한다. 이것이 허용되는 동작인지, 다음 로그인 시 정리해야 하는
  결함인지 확정이 필요하다.

## 14. 향후 개선 후보

- DeepSeek도 사용할 수 없을 때의 제3 provider 전환(현재는 명시적 제외 항목).
- 한도 감지를 화면 문구 대신 구조화된 신호로 바꾸는 방법(현재는 문구가 바뀌면 패턴 갱신이
  필요하다).
- Codex custom provider의 connect timeout 단축(현재 공식 설정 항목이 없다).
