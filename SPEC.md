# Codex Provider Router 사양서

<!-- spec-template: v1 -->

| 항목 | 값 |
|---|---|
| Document Version | 1.2.0 |
| Project Version | 1.0.0 |
| Last Updated | 2026-09-25 |
| Status | active |
| Owner | Router 운영 담당자 (역할명) |

이 문서는 `codex-provider-router`가 **어떻게 동작해야 하는가**를 정한 기준이다.
지금 코드가 어떻게 동작하는지는 사양이 되지 않는다.
사양과 코드가 다르면 코드에 맞춰 이 문서를 고치지 않고 `SPEC / CODE MISMATCH`로 알린다. 절차는 `AGENTS.md`에 있다.

## 문서 경계: 이 문서와 `USAGE_AND_SPEC.md`

이 프로젝트에는 `USAGE_AND_SPEC.md`라는 문서가 따로 있다. 두 문서에 같은 내용을 두 번 적지 않는다.

| 문서 | 담는 것 |
|---|---|
| `SPEC.md` (이 문서) | Router가 **꼭 지켜야 하는 요구사항**(REQ/NFR), 그것을 확인하는 테스트(TEST), 요구사항·코드·테스트 연결표, 아직 정하지 못한 것 |
| `USAGE_AND_SPEC.md` | 사용자가 직접 입력하는 **명령·옵션과 출력 예**, 기준 환경, 설치·상태·로그 파일의 **경로**, 상태가 바뀌는 규칙과 오류 분류를 자세히 적은 표 |

명령 사용법, 경로, 화면 출력, 환경 값은 `USAGE_AND_SPEC.md`에서 찾는다.
이 문서는 그 내용을 옮겨 적지 않고 절 번호만 가리킨다.

## 1. 목적

Codex를 ChatGPT 로그인으로 쓰던 사람이 OpenAI 사용 한도(usage limit)가 다 찬 뒤에도 하던 작업을 이어 가게 하는 것이 목적이다.
명령은 **바꾸지 않는다**. 늘 쓰던 `codex --yolo`를 그대로 쓴다.

Router가 하는 일은 다음과 같다.

1. 평소에는 OpenAI를 쓴다.
2. OpenAI 한도가 정말 다 찬 경우에만 사용자에게 묻고, 허락하면 DeepSeek로 넘겨 쓴다.
3. 한도가 풀리면 다시 OpenAI로 돌아온다.

성공 조건은 두 가지다.

- 사용자는 provider마다 다른 명령을 외우지 않아도 된다.
- provider가 바뀌어도 작업 맥락, 파일, Codex 대화 기록이 그대로 남는다.

### 이 문서에서 쓰는 말

아래 말은 `docs/SPEC.html`에서 마우스를 올리면 뜻이 보인다.

| 용어 | 뜻 |
|---|---|
| provider | Codex가 요청을 보내는 AI 서비스 회사. 이 문서에서는 OpenAI와 DeepSeek 두 곳이다 |
| 넘겨 쓰기(`fallback`) | OpenAI 한도가 다 찼을 때 사용자 허락을 받아 같은 작업을 DeepSeek로 이어 가는 것 |
| wrapper | `codex` 명령을 먼저 받아 provider를 고른 뒤 원본 Codex를 실행해 주는 Router 프로그램 |
| 원본 Codex | Router를 설치하기 전부터 있던 Codex 실행 파일. Router는 이것을 절대 경로로 부른다 |
| profile | Codex가 어느 provider와 모델로 실행할지 적어 둔 설정 묶음 |
| reasoning | 모델이 답하기 전에 얼마나 깊이 생각할지 정하는 값. DeepSeek는 `low`, `high`, `max`를 쓴다 |
| 모델 목록(catalog) | DeepSeek가 공식으로 내놓는 모델 목록. Router가 내려받아 둔다 |
| slug | 모델 목록에서 모델 하나를 가리키는 이름 |
| 대기 상태(`OPENAI_COOLDOWN`) | DeepSeek로 넘긴 뒤 OpenAI 한도가 풀리기를 기다리는 상태 |
| 확인 요청(probe) | 한도가 풀렸는지 보려고 OpenAI에 보내는 아주 작은 요청 |
| checkpoint | 넘겨 쓰기 직전에 작업 디렉터리의 상태(`git status`, `git diff`, 안내 문서)를 담아 둔 기록 |
| thread | Codex가 저장해 두는 대화 하나. 요청, 명령 결과, TODO가 들어 있다 |
| 이어 열기(`resume`) | 저장된 thread를 다시 열어 대화를 이어 가는 Codex 명령 |
| session id | Codex 대화 하나를 가리키는 값. rollout 파일의 메타 정보에 적혀 있다 |
| rollout 파일 | Codex가 세션마다 남기는 기록 파일. token 사용량과 session id가 들어 있다 |
| token 사용량 | 모델이 읽고 쓴 글의 양을 세는 값. DeepSeek 비용은 이것으로 계산한다 |
| TTY | 사람이 키보드로 입력하는 터미널. stdin이 TTY가 아니면 사람이 없는 자동 실행(비대화형 실행)으로 본다 |
| PTY | Router가 Codex를 실행할 때 만드는 가상 터미널 |
| TUI | Codex가 터미널 안에 그리는 화면 |
| cache | 한 번 조회한 provider 상태와 잔액을 저장해 두고 다시 쓰는 것 |
| TTL | cache를 그대로 믿고 쓸 수 있는 시간. 이보다 오래되면 다시 조회한다 |
| shim | Windows에서 `codex`, `deep` 같은 명령 이름을 Router로 이어 주는 작은 실행 파일 |
| exit code | 프로그램이 끝날 때 돌려주는 숫자. 0은 성공이다 |
| 비용 원장(`spend.jsonl`) | DeepSeek 세션마다 쓴 token 수와 돈으로 바꾼 비용을 쌓아 두는 파일 |
| Keychain | macOS의 암호 보관함. DeepSeek Key를 `codex-router-deepseek` 항목에 둔다 |
| DPAPI | Windows가 사용자 계정 기준으로 파일을 암호화해 주는 기능. Windows에서는 Key 파일을 이것으로 암호화한다 |
| `ai` | 작업 성격을 보고 알맞은 모델과 reasoning을 추천하는 명령(Model Router) |
| `deep` | OpenAI 상태와 상관없이 바로 DeepSeek로 Codex를 실행하는 명령 |

## 2. 프로젝트 범위

### 포함

- `codex` 명령을 먼저 받는 wrapper, 그리고 provider를 고르고, 바꾸고, 되돌리는 동작.
- DeepSeek로 넘겨 쓸 때의 profile(모델·reasoning·모델 목록) 설정을 계속 유지하는 것과 그 설정을 바꾸는 명령.
- 넘겨 쓰는 시점에 작업 맥락을 담은 checkpoint 만들기, 그리고 Codex가 저장해 둔 대화(persisted thread) 이어 열기(resume).
- DeepSeek 잔액 조회와 잔액 경고, provider 상태 cache.
- DeepSeek token 사용량으로 비용을 계산하고, 소유자가 정한 하루·한 달 한도를 넘으면 실행을 막는 기능.
- 작업 성격에 맞는 모델을 추천하고, 사용자가 승인하면 실행하는 `ai` Model Router.
- macOS(zsh)와 Windows(PowerShell·cmd)용 설치·제거 스크립트, 그리고 자격증명 저장소(Key 보관함) 연결.

### 제외

- Codex 프로그램(binary) 자체를 고치는 것, ChatGPT 인증 방식을 바꾸는 것, API Key 로그인으로 바꾸는 것.
- OpenAI 모델 선택. 이것은 Codex TUI의 `/model`이 맡고, Router는 덮어쓰지 않는다.
- Codex 세션이 열려 있는 도중에 provider를 바꿔 끼우는 것(hot-swap). Codex CLI에 그런 기능이 없으므로 있다고 가정하지 않는다.
- DeepSeek 다음에 세 번째 provider로 또 넘기는 것.
- MCP·plugin·project trust 설정 관리.

## 3. 시스템 구성

| 구성 요소 | 책임 |
|---|---|
| `src/codex_router.py` | wrapper 시작점. provider 선택, PTY 실행, 상태 전환, checkpoint, 로그, 잔액·모델·모델 목록 명령 |
| `src/model_router.py` | `ai` 명령에서 prompt 점수 매기기, 모델 후보 만들기, provider 상태 cache, 잔액 조회 |
| `config/config.toml` | Router 기본 설정의 원본(template) |
| `config/deepseek.config.toml` | Codex가 읽는 DeepSeek provider profile의 원본(template) |
| `config/models.toml` | `ai`가 쓰는 모델 목록. 모델 ID·reasoning·context 크기·가격·할 수 있는 일(capability)을 적는다 |
| `scripts/install.sh`, `scripts/uninstall.sh` | macOS 설치·제거 |
| `scripts/install.ps1`, `scripts/uninstall.ps1`, `scripts/codex-router.ps1`, `scripts/codex.cmd`, `scripts/deep.cmd`, `scripts/codex-router.cmd` | Windows 설치·제거와 shim |

Router 바깥에서 맞닿는 곳은 네 곳이다.

- Codex 프로그램(binary)
- OpenAI(ChatGPT 로그인)
- DeepSeek API
- macOS Keychain / Windows DPAPI

설치한 뒤 파일이 어디에 놓이는지는 `USAGE_AND_SPEC.md` 10절에 있다.

## 4. 전체 동작 흐름

1. 사용자가 `codex ...`를 실행하면, PATH에서 앞쪽에 있는 Router wrapper가 먼저 받는다.
2. Router는 DeepSeek 모델 목록을 확인할 때가 됐는지 보고(REQ-CATALOG-001) provider를 고른다.
3. 상태가 `OPENAI_ACTIVE`면 OpenAI로 Codex를 실행한다(REQ-ROUTE-001).
4. 세션 도중 OpenAI 한도가 다 찼다는 문구가 보이면 checkpoint를 만들고(REQ-CTX-001), 사용자에게 넘겨 쓸지 묻는다(REQ-ROUTE-002).
5. 사용자가 승인하면 같은 thread를 DeepSeek로 이어 열고, 상태를 대기 상태(cooldown)로 바꾼다.
6. 다음 실행부터는 확인(probe) 시각이 되면 OpenAI가 되는지 확인하고, 되면 OpenAI로 돌아온다(REQ-ROUTE-004).
7. `deep` 계열 명령과 `FORCE_DEEPSEEK=1`은 1~6단계와 상관없이 바로 DeepSeek로 실행한다(REQ-ROUTE-005).
8. DeepSeek로 실행하기 전에 오늘과 이번 달에 쓴 비용 합계를 확인한다. 한도를 넘었으면 실행하지 않고, 이유와 한도를 바꾸는 방법을 알린다(REQ-COST-001).

상태가 어떻게 바뀌는지 정리한 표는 `USAGE_AND_SPEC.md` 5절에 있다.

## 5. 기능 요구사항

ID 규칙: `REQ-<CATEGORY>-NNN`. CATEGORY는 영어 대문자와 숫자로, NNN은 세 자리 숫자로 쓴다.
한 번 붙인 ID는 다시 쓰거나 뜻을 바꾸지 않는다. 지운 ID를 다른 기능에 다시 붙이지 않는다.

기능 그룹: CATEGORY마다 사람이 읽는 이름을 붙인다. `docs/SPEC.html`의 기능 목록이 이 이름으로 묶인다.

| 카테고리 | 이름 |
|---|---|
| ROUTE | provider 라우팅 |
| STATE | 상태 관리 |
| CTX | 작업 맥락 |
| MODEL | 모델 설정 |
| CATALOG | 모델 목록 |
| BALANCE | 잔액 확인 |
| ERR | 오류 안내 |
| AIROUTE | 모델 추천 |
| COST | 비용 한도 |
| SEC | 보안 |
| COMPAT | 호환성 |
| UX | 화면 표시 |

### REQ-ROUTE-001 기존 codex 명령 그대로 쓰기

#### 목적
사용자가 명령을 바꾸지 않고 전과 같이 Codex를 쓰게 한다.

#### 동작
- Router는 `codex`라는 이름으로 불리면 wrapper로 동작한다.
- 다른 이름(`codex-router`, `deep`, `ai`)으로 불리면 그 이름에 맞는 명령을 제공한다.
- 원본 Codex는 **절대 경로**로 부른다.
  이유: 이름으로 부르면 wrapper가 자기 자신을 다시 부르게 된다.
- 자식 Codex process에는 Router를 건너뛰라는 표시(우회 표시)를 넘긴다. 그래서 wrapper 안에 wrapper가 또 뜨지 않는다.
- 사람이 입력하지 않는 실행(stdin이 TTY가 아닌 비대화형 실행)은 원본 Codex로 그대로 넘긴다. DeepSeek를 강제로 지정한 경우만 예외다.

#### 기대 결과
`codex --yolo`를 입력하면 Router를 설치하기 전과 똑같이 ChatGPT 로그인 Codex가 뜬다.

#### 관련 구현
`src/codex_router.py`

#### 관련 테스트
(없음)

### REQ-ROUTE-002 provider를 바꾸기 전에 묻기

#### 목적
사용자가 모르는 사이에 provider가 바뀌면 안 된다.

#### 선행 조건
다음 가운데 하나에 해당한다.

- 대화형 세션에서 OpenAI 한도가 다 찬 것을 알아챘다.
- 이전 세션의 대기 상태(cooldown) 때문에 DeepSeek가 골라지려 한다.

#### 동작
- 바꾸기 전에 `y/N`로 묻는다. **기본값은 바꾸지 않는 것**이다.
- `y`나 `yes`(대문자·소문자 구분 없음)만 승인으로 본다. 그 밖의 입력, 빈 입력, 입력 끝(EOF), Ctrl-C는 모두 거절이다.
- stdin이 TTY가 아니면 묻지 않고 거절로 처리한다.
  이유: 사람이 없는 자동 실행이 사용자 모르게 돈이 드는 provider로 넘어가면 안 된다.
- 사용자가 거절하고 OpenAI 세션이 정상으로 끝나면 상태를 `OPENAI_ACTIVE`로 되돌린다.
- 사용자가 직접 DeepSeek를 지정한 경우(REQ-ROUTE-005)에는 묻지 않는다.

#### 예외 처리
승인했는데 DeepSeek Key가 없으면 Codex를 다시 띄우지 않는다. 안내를 보여 주고 exit code 78로 끝낸다.

#### 관련 구현
`src/codex_router.py`

#### 관련 테스트
TEST-ROUTE-001

### REQ-ROUTE-003 남은 한도 안내를 소진으로 잘못 읽지 않기

#### 목적
남은 한도를 알려 주는 안내를 한도가 다 찬 것으로 잘못 읽고 넘겨 쓰지 않게 한다.

#### 입력
OpenAI Codex가 터미널(TTY)에 찍은 문구.

#### 동작
- 한도가 정말 다 찼다는 뜻의 문구만 넘겨 쓰기 후보로 본다. 예: `hit`, `reached`, `exceeded`, `no usage left`, `0% left`, HTTP 429.
- 정상 안내는 넘겨 쓰기 후보에서 뺀다. 예: `usage limit resets available`, `less than 25% left`, `26% left`, `resets in ...`, Codex `/status` 화면의 `Weekly limit: 70% left`.
- 남은 비율이 0보다 크면, 글자 일부가 `0% left`와 겹치더라도 소진 문구로 보지 않는다.
- 인증 실패(401/403)가 나면 사용자에게 알리지 않은 채 DeepSeek로 넘기지 않는다.
- network 오류와 서버 오류(5xx)는 Codex가 정해진 횟수만큼 다시 시도하도록 맡긴다.
- 한도 종류(weekly / session / 그 밖)를 나눠 상태에 적어 둔다.
- 한도가 풀리는 시각(reset 시각)은 provider가 **절대 ISO 시각**을 준 경우에만 저장한다. `in 5h` 같은 상대 표현으로 시각을 계산해 만들지 않는다.

#### 기대 결과
한도가 남아 있는 세션은 끊기지 않고, 넘겨 쓸지 묻는 질문도 뜨지 않는다.

#### 관련 구현
`src/codex_router.py`

#### 관련 테스트
TEST-ROUTE-002

### REQ-ROUTE-004 한도가 풀리면 OpenAI로 자동 복귀

#### 목적
한도가 풀리면 사람이 손대지 않아도 OpenAI로 돌아온다.

#### 동작
1. 넘겨 쓰기를 승인하면 상태를 `OPENAI_COOLDOWN`(대기 상태)으로 두고 다음 확인(probe) 시각을 계산한다.
2. 확인 간격은 설정한 단계를 따른다. 기본은 10, 20, 30, 60분이고, 마지막 값에 이르면 그 값에서 멈춘다.
3. 확인 요청은 가장 작은 요청으로 보낸다. 읽기만 하고(read-only), 승인을 묻지 않고, 세션 기록을 남기지 않는(ephemeral) 방식이다. 실패해도 사용자 작업을 멈추지 않는다.
4. 확인이 성공하면 상태를 `OPENAI_ACTIVE`로 되돌리고, 대기 상태에 쓰던 값을 모두 지운다.

**DeepSeek 세션이 열려 있는 동안에는 provider를 바꾸지 않는다.** OpenAI로 돌아오는 것은 다음 실행부터다.

#### 관련 구현
`src/codex_router.py`

#### 관련 테스트
TEST-ROUTE-003

### REQ-ROUTE-005 DeepSeek 바로 쓰기

#### 목적
OpenAI 상태와 상관없이 DeepSeek를 쓰고 싶을 때 한 단어로 지정할 수 있어야 한다.

#### 입력
다음 세 가지 가운데 하나.

- `deep [codex] <CODEX_ARGS>`
- `codex-router deep|deepseek <CODEX_ARGS>`
- `FORCE_DEEPSEEK=1 codex <CODEX_ARGS>`

#### 동작
- 세 방법 모두 상태, 대기 상태, 확인 질문과 상관없이 DeepSeek profile로 Codex를 시작한다.
- `deep`은 `sudo`처럼 명령 앞에 붙이는 말로 읽힌다. 그래서 첫 인자가 `codex`이면 그 단어를 뺀다. macOS와 Windows가 똑같이 동작한다.
- 사용자가 준 `-p/--profile`, `-m/--model`(그리고 `=`를 붙인 형태)은 빼고, Router의 profile과 넘겨 쓰기용 모델·reasoning을 강제로 쓴다.
- `--` 뒤의 인자는 prompt로 보고 건드리지 않는다.
- 비대화형 `deep exec "..."`도 DeepSeek profile을 그대로 쓴다.
- `FORCE_DEEPSEEK=1`은 그 실행 한 번에만 적용된다. Router의 OpenAI 상태와 대기 상태는 바꾸지 않는다.
- 값 `1`은 모델 번호가 아니다. 켜고 끄는 표시(boolean flag)다.

#### 예외 처리
DeepSeek Key가 없으면 Codex를 시작하지 않는다. 안내를 보여 주고 exit code 78로 끝낸다.

#### 관련 구현
`src/codex_router.py`, `scripts/deep.cmd`, `scripts/codex-router.ps1`

#### 관련 테스트
TEST-ROUTE-004

### REQ-STATE-001 상태 파일이 깨져도 계속 쓸 수 있게 하기

#### 목적
상태 파일이 깨져도 사용자가 Codex를 못 쓰게 되면 안 된다.

#### 동작
- 올바른 상태는 `OPENAI_ACTIVE`, `OPENAI_COOLDOWN`, `DEEPSEEK_ACTIVE` 세 가지뿐이다.
- 다음 경우에는 모두 기본값 `OPENAI_ACTIVE`로 되돌린다. 오류를 내며 멈추지 않는다.
  - 알 수 없는 상태값
  - 형식(타입)이 틀린 값
  - 음수인 시도 횟수
  - 시간대(timezone)가 없는 확인(probe) 시각
  - 파일을 읽지 못한 경우
- 상태는 임시 파일에 먼저 쓴 뒤 원래 파일과 바꿔 끼우는 방식으로 저장한다. 파일은 소유자만 읽고 쓸 수 있다.
- `codex-router reset`은 Router 상태만 처음으로 돌린다. ChatGPT 로그인, Codex config, MCP, DeepSeek Key, 모델 설정은 지우지 않는다.

#### 관련 구현
`src/codex_router.py`

#### 관련 테스트
TEST-STATE-001

### REQ-CTX-001 provider가 바뀌어도 작업 맥락 유지

#### 목적
provider가 바뀌어도 하던 작업의 맥락이 이어지게 한다.

#### 동작
- 한도가 다 찬 것을 알아챈 순간, 작업 디렉터리마다 checkpoint를 만든다. checkpoint에는 다음을 담는다.
  - 시각, 원인, 작업 디렉터리
  - `git status`, `git diff`
  - 있으면 `AGENTS.md`·`README.md`·`progress.md`
- git 명령이 실패하거나 시간 초과(timeout)가 나도 checkpoint 만들기는 실패로 치지 않는다. 실패했다는 사실만 적어 둔다.
- 대화, 요청, tool call, 명령 결과, TODO는 Codex가 저장해 둔 대화(persisted thread)에 이미 있다. 그래서 checkpoint에 다시 담지 않는다.
- 넘겨 쓸 때는 방금 끝난 OpenAI 세션의 session id를 rollout 메타 정보에서 찾고, 같은 thread를 `resume <session_id>`로 다시 연다.
- session id를 찾지 못하면 resume 선택 화면(picker)에서 사용자가 직접 고른다.
- 오류가 났다고 해서 작업 파일, Codex thread, checkpoint를 지우지 않는다.

#### 관련 구현
`src/codex_router.py`

#### 관련 테스트
TEST-CTX-001

### REQ-MODEL-001 DeepSeek 모델·reasoning 설정 유지

#### 목적
사용자가 정한 DeepSeek 모델과 reasoning 값이 다음 실행에서도 그대로 쓰여야 한다.

#### 동작
- 넘겨 쓰기용 모델과 reasoning은 Router config에 저장한다. 새로 띄운 process에서도 같은 값을 읽는다.
- 모델은 별명(alias: `flash`, `pro`, `vision`)으로 지정한다. Router가 마음대로 바꾸지 않는다.
- reasoning으로 쓸 수 있는 값은 `low`, `high`, `max`이고 기본값은 `high`다. 지원하지 않는 값은 저장하지 않고 exit code 2로 끝낸다.
- config를 다시 쓸 때 template에 있던 설명 주석을 그대로 남긴다.
- OpenAI 모델 선택값은 이 설정과 따로 움직인다. Router는 그 값을 덮어쓰지 않는다.

#### 관련 구현
`src/codex_router.py`, `config/config.toml`

#### 관련 테스트
TEST-MODEL-001

### REQ-CATALOG-001 DeepSeek 모델 목록 자동 갱신

#### 목적
DeepSeek 모델 목록이 바뀌어도 사용자가 손으로 챙기지 않게 한다.

#### 동작
- `codex`를 실행할 때 공식 모델 목록(catalog)을 확인한다. 확인은 설정한 주기(기본 24시간)에 한 번을 넘지 않는다.
- 내려받은 내용은 JSON 구조와 model slug를 검사한다. slug가 겹치거나 형식이 틀리면 받아들이지 않는다.
- 갱신하기 전에 기존 목록을 백업한다. 검사 실패, network 실패, 시간 초과(timeout)가 나면 **기존 목록을 그대로 둔다**.
- 새 slug가 생기면 터미널에 알리고 목록에 `new`로 표시한다.
- **지금 쓰는 넘겨 쓰기용 모델은 자동으로 바꾸지 않는다.**
- 지금 쓰는 모델이 공식 목록에서 없어졌으면 갱신을 멈추고 경고한다.
- 목록 파일이 없거나 읽을 수 없으면 알려진 기본 모델로 계속 동작한다.

#### 관련 구현
`src/codex_router.py`

#### 관련 테스트
TEST-CATALOG-001

### REQ-BALANCE-001 DeepSeek 잔액 부족 미리 알리기

#### 목적
DeepSeek 잔액이 떨어져 작업이 끊기기 전에 사용자가 알 수 있어야 한다.

#### 동작
잔액 조회 명령은 다음과 같이 동작한다.

- `codex-router balance`(와 `ai balance`)는 DeepSeek 공식 잔액 조회 주소(endpoint)에서 USD 총액, 충전 잔액, 증정 잔액, 사용 가능 여부를 가져와 보여 준다.
- `--json`을 붙이면 프로그램이 읽을 수 있는 형태로 낸다. `status`, `checked_at`, `threshold_usd`, `low_balance`, `balance`가 들어간다.
- 조회에 성공하면 exit code 0, 실패하면 1이다. **잔액이 기준보다 적어도 조회에 성공했으면 0**이다.
- 경고 기준은 설정값이고 기본값은 USD 1.00이다. 읽을 수 없는 값이면 기본값으로 되돌린다.
- 조회 결과는 provider 상태 cache에 적어 둔다. 그래서 `status` 계열 명령이 network 호출 없이 같은 금액을 보여 준다.

세션 배너에는 다음과 같이 보여 준다.

- DeepSeek 세션을 시작할 때 배너에 잔액을 함께 보여 준다. 세션이 끝나면 다시 조회한 잔액을 한 줄로 보여 준다.
- 시작할 때 금액은 cache에서 읽는다. cache가 TTL보다 오래되었으면 **자식 process**에서 새로 조회한다.
- PTY를 만드는(`forkpty()`) process에서는 잔액을 조회하지 않는다.
- Codex TUI가 그리는 화면에는 잔액 줄을 넣지 않는다(NFR-UX-001).

잔액이 부족하거나 조회에 실패하면 다음과 같이 한다.

- 잔액 부족은 경고일 뿐이고 provider를 멈추지 않는다. DeepSeek가 계정 잔액이 다 떨어졌다고 알릴 때만 provider를 고르는 대상에서 뺀다.
- 잔액 조회에 실패해도 세션을 멈추지 않는다. 금액을 지어내서 보여 주지도 않는다.

#### 관련 구현
`src/codex_router.py`, `src/model_router.py`

#### 관련 테스트
TEST-BALANCE-001, TEST-BALANCE-002

### REQ-ERR-001 DeepSeek 실패 안내

#### 목적
DeepSeek가 실패하면 사용자가 다음에 무엇을 해야 할지 알 수 있게 안내한다.

#### 입력
DeepSeek 세션이 비정상으로 끝난 exit code와, TTY에 마지막으로 찍힌 출력.

#### 동작
- 실패를 `BILLING`, `QUOTA`, `AUTH`, `NETWORK`, `SERVER`, `UNKNOWN` 가운데 하나로 나누고, 각각 한국어로 원인과 해결 방법을 보여 준다. 나누는 기준은 서로 겹치지 않는다.
- HTTP 402(잔액)와 429(속도 제한)는 서로 다른 원인으로 나눈다.
- 어느 분류에도 맞지 않으면 짐작하지 않는다. `UNKNOWN`으로 두고 로그를 확인하라고 안내한다.
- 어떤 분류든 작업 파일과 Codex 대화 기록이 지워지지 않았다는 것을 함께 알린다.
- DeepSeek도 쓸 수 없으면 세 번째 provider로 넘어가지 않는다. 이유와 대처 방법을 보여 주고 끝낸다.

#### 관련 구현
`src/codex_router.py`

#### 관련 테스트
TEST-ERR-001

### REQ-AIROUTE-001 작업별 모델 추천

#### 목적
작업 성격에 맞는 모델을 추천한다. 실행할지는 사용자가 정한다.

#### 동작
- prompt와 저장소 정보(metadata)로 점수를 매겨 모델과 reasoning을 추천한다. 저장소 **내용**은 분류기에 넘기지 않는다.
- 지금 쓸 수 있는 provider의 모델만 후보로 만든다.
  - OpenAI 한도가 다 찼으면 GPT 계열을 뺀다.
  - DeepSeek 잔액이 다 떨어졌거나 인증에 실패했으면 DeepSeek 계열을 뺀다.
- 추천은 승인 화면을 거쳐야 실행된다. 기본 설정에서는 알아서 모델을 바꾸거나 더 높은 모델로 올리지 않는다.
- 실행할 때는 그 실행에만 쓰는 인자(`--model`, reasoning 지정)만 쓴다. **Codex 전체 설정(전역 config)은 고치지 않는다.**
- MCP, trust, history, 승인 정책은 원래 설정을 그대로 쓴다.
- 사용자가 쓴 prompt는 줄바꿈과 한글을 포함해 바꾸지 않고 인자 하나로 넘긴다.
- 같은 종류의 작업이 거듭 실패하면 원인에 따라 더 높은 모델을 추천한다. 이때도 실행 전에 다시 승인을 받는다.
- 사용 기록에는 원래 prompt를 저장하지 않고 hash 값만 남긴다.

#### 관련 구현
`src/model_router.py`, `config/models.toml`

#### 관련 테스트
TEST-AIROUTE-001

### REQ-COST-001 비용 한도를 넘으면 실행 막기

#### 목적
설정에 적어 둔 비용 한도는 보여 주기만 하는 값이 아니다. 한도를 넘으면 **실행을 막는다**.

시간 기반 한도는 2026-09-22 소유자 결정으로 없앤다.
이유: DeepSeek는 미리 충전한 잔액을 쓰는 유료 provider다. 쓸 수 있는 돈의 상한은 잔액과 비용 한도가 이미 맡고, 시간 한도는 이와 겹치면서 정당한 작업까지 막는다.

같은 결정으로 비용 한도의 기본값도 0(한도 없음)이다. 소유자가 0보다 큰 값을 직접 적었을 때만 한도가 실행을 막는다.
이유: 잔액 자체가 쓸 수 있는 돈의 상한이다.

#### 동작
한도 설정은 다음과 같다.

- 한도는 `[cost] daily_limit_usd`(하루)와 `[cost] monthly_limit_usd`(한 달) 두 가지다.
- **0 이하면 한도가 없다**는 뜻이다. 기본값은 0(한도 없음)이다.
- 한도는 돈을 내는 provider인 DeepSeek에만 계산한다. OpenAI(ChatGPT 로그인)는 정해진 요금제라 비용 한도에 넣지 않고, 그 사실을 `cost` 출력에 적는다.

비용은 짐작하지 않고 token 사용량으로 계산한다. 순서는 다음과 같다.

1. 세션을 시작하기 전에 Codex rollout 파일(`~/.codex/sessions/**/rollout-*.jsonl`)의 크기를 적어 둔다.
2. 세션이 끝나면 그 뒤로 늘어난 부분의 `last_token_usage`만 더한다.
3. 더한 값을 `config/models.toml`의 단가로 돈으로 바꾼다.
4. 세션 결과를 비용 원장(`~/.codex/router/spend.jsonl`)에 적는다.

`resume`으로 이어 연 세션도 이번 실행분만 계산된다. `ai` 실행 기록(`~/.codex/router/logs/model-router-usage.jsonl`)도 같은 계산에 넣는다.

실행을 막는 규칙은 다음과 같다.

- DeepSeek로 실행하기 바로 전에 오늘과 이번 달의 비용 합계를 확인한다.
- 한도에 닿았으면 **실행하지 않고** 종료 코드 `75`로 끝낸다. 이때 어느 한도인지, 지금까지의 합계, 한도를 바꾸는 방법, `codex-router cost` 안내를 출력한다.
- 막았다는 결정은 로그에 `blocked` 이벤트로 남긴다.
- 넘겨 쓴 지 지난 시간은 OpenAI 한도를 알아챈 시각(`cooldown_started_at`)부터 잰다. 이 값은 `codex-router cost`에 참고 정보로만 보여 주고, 실행을 막는 근거로 쓰지 않는다.

비용 보기:

- `codex-router cost [--json]`은 한도, 오늘·이번 달 합계, 넘겨 쓴 지 지난 시간, 커버리지를 보여 준다.
- 커버리지는 "기록 수 / token이 있는 기록 수 / 비용 근거가 없는 기록 수"다.

#### 예외 처리
- rollout 파일이나 원장 파일을 읽지 못하면 그 기록은 건너뛰고 계산을 이어 간다.
- 단가를 모르는 모델은 `cost_usd: null`로 적고, 한도 계산에서는 0으로 본다.

> **주의** 이런 기록이 있으면 `cost`의 "비용 근거가 없는 기록" 수가 늘어난다. 이때 0으로 보이는 합계를 그대로 믿지 않는다.

#### 관련 구현
`src/codex_router.py` (`cost_limits`, `rollout_usage_since`, `record_session_spend`,
`spend_totals`, `limit_block`, `enforce_limit`, `cost_command`)

#### 관련 테스트
TEST-COST-002

## 6. 비기능 요구사항

### NFR-SEC-001 API Key 안전 보관

DeepSeek API Key는 **process 환경 변수(environment)나 OS 자격증명 저장소**에서만 읽는다.
자격증명 저장소는 macOS에서는 Keychain의 `codex-router-deepseek` 항목, Windows에서는 DPAPI로 암호화한 파일이다.

- Key를 평문 파일, Router config, 저장소, 터미널 출력, 로그 어디에도 남기지 않는다.
- 설치할 때 가져온(import) Key 파일은 설치본에 복사하지 않는다.
- Key를 입력할 때는 화면에 보이지 않게 하고, 두 번 입력받아 같은지 확인한다. 두 값이 다르거나 비어 있으면 exit code 2다.
- 로그에는 `timestamp`, `event`, provider, model, reasoning, 전환 이유, 확인(probe) 결과, checkpoint 경로, `failure_type`, `exit_code`만 남긴다.
- 오류 원문, token, Authorization header는 로그에 남기지 않는다.
- 상태·설정·로그·checkpoint 파일은 소유자만 읽고 쓸 수 있게 한다(파일 600, 디렉터리 700).
- 로그는 달마다 파일을 따로 쓰고, 최근 6개만 남긴다.

이 문서와 `knowledge/`에도 Key 값이나 개인 절대경로를 적지 않는다.

### NFR-COMPAT-001 기존 Codex 환경 보존

Router는 원래 있던 Codex 환경을 그대로 둔다.
Codex config, ChatGPT 인증, MCP 설정, plugin 설정, project trust, OpenAI 모델 선택, 원본 Codex 실행 파일을 마음대로 바꾸거나 지우지 않는다.

- 설치는 시작하기 전에 백업 디렉터리를 만든다.
- 설치 도중 모델 목록 내려받기가 실패하면 기존 설치와 설정을 건드리지 않은 채 멈춘다.
- 제거할 때는 wrapper, profile, Router 설정, 자격증명 항목만 지운다. 기존 Codex 인증과 config는 남긴다.
- Router 상태, 로그, checkpoint는 바로 지우지 않고 휴지통으로 옮긴다.
- macOS와 Windows는 같은 명령 이름을 쓰고, 인자도 같은 방식으로 읽는다.
- Windows에서 저장해 둔 원본 Codex 경로가 없어졌거나 Router 자신의 shim을 가리키면, PATH에서 자신의 shim을 뺀 다음 `codex` 실행 파일을 찾는다. 찾으면 경로를 새로 저장하고 실행을 이어 간다.
- 대신 쓸 실행 파일이 없으면 무엇이 문제인지 알 수 있는 오류를 내고 멈춘다.
- 급할 때는 원본 Codex를 절대 경로로 직접 실행해 Router를 거치지 않고 쓸 수 있어야 한다.

### NFR-UX-001 Codex TUI 화면 보존

Router 때문에 Codex TUI 화면이 깨지면 안 된다.

- 자식 PTY에 사용자 터미널의 행·열 수(rows/columns)를 복사한다. 터미널 크기가 바뀌면 `SIGWINCH` 신호를 받아 바로 다시 맞춘다.
- Codex 프로그램이 그리는 화면(예: TUI의 `/status`)에 wrapper가 줄을 덧붙이지 않는다. Router 안내는 Router 자체 배너와 stderr로만 낸다.

### NFR-COST-001 provider 고르기에 추가 비용 없음

provider와 모델을 고르는 일 때문에 돈이 드는 호출이 늘어나면 안 된다.

- `ai`의 LLM 분류기는 기본으로 꺼져 있다. 이때 모델 고르기에 드는 추가 LLM token은 0이다.
- 분류기를 켜더라도 값싼 모델, 낮은 reasoning, 제한된 출력 token만 쓴다. 비싼 모델은 분류에 쓰지 않는다.
- provider 상태를 새로 알아볼 때는 공식 상태·잔액 조회 주소(endpoint)만 쓴다. 한도(quota)를 알아보려고 LLM 요청을 만들지 않는다.
- 한도가 풀리는 시각을 얻지 못하면 `unavailable`로 표시한다.
- provider 상태는 cache에 두고, 새로 알아보는 횟수에 상한을 둔다.

## 7. 데이터 사양

Router가 다루는 데이터는 여섯 가지다.

1. provider 상태와 확인(probe) 일정
2. DeepSeek 모델 목록(catalog)
3. 넘겨 쓰는 시점의 checkpoint
4. 이벤트 로그
5. provider 상태·잔액 cache
6. `ai` 사용 기록

파일이 놓이는 곳과 필드 목록은 `USAGE_AND_SPEC.md` 8·10·11절에 있다.

지켜야 할 규칙은 다음과 같다.

- 어떤 데이터에도 API Key, token, cookie, 오류 원문, 원래 prompt를 담지 않는다(NFR-SEC-001, REQ-AIROUTE-001).
- checkpoint는 작업 디렉터리마다 따로 두고, 사용자가 지우기 전까지 남겨 둔다.
- 로그는 달마다 한 파일씩, 최근 6개만 남긴다.
- 비용 원장(`spend.jsonl`)에는 세션마다 token 수, 돈으로 바꾼 비용, 시각만 담는다. prompt나 응답 내용은 담지 않는다.
- 비용 원장은 한도를 판단하려고 달 단위로 쌓아 가며, 자동으로 지우지 않는다.
- 상태·cache 파일이 깨졌을 때는 오류로 멈추지 않고 안전한 기본값으로 돌아간다(REQ-STATE-001, REQ-CATALOG-001).

## 8. Configuration 사양

설정 항목과 기본값의 원본은 `config/config.toml`이다.
Codex가 읽는 DeepSeek profile은 `config/deepseek.config.toml`, `ai`의 모델 목록은 `config/models.toml`에 있다. 설치한 뒤의 경로는 `USAGE_AND_SPEC.md` 10절에 있다.

지켜야 할 규칙은 다음과 같다.

- API Key는 어떤 설정 파일에도 저장하지 않는다(NFR-SEC-001).
- 설정 파일이 없어도 모든 항목은 코드에 적힌 기본값으로 동작해야 한다. 설정 파일은 기본값을 덮어쓰기만 한다.
- `[cost] daily_limit_usd`·`monthly_limit_usd`는 실행을 막는 한도다(REQ-COST-001).
- 이 한도의 기본값은 0(한도 없음)이고, 소유자가 0보다 큰 값을 넣었을 때만 막는다.
- 비용 한도는 돈을 내는 provider인 DeepSeek에만 적용한다. 시간 기반 한도는 두지 않는다(REQ-COST-001).
- 설정을 다시 쓸 때 template의 설명 주석을 남겨 둔다(REQ-MODEL-001).
- 사용자가 쓰는 환경 변수는 `DEEPSEEK_API_KEY`(Key를 읽는 곳)와 `FORCE_DEEPSEEK`(그 실행 한 번만 DeepSeek로 바꾸기) 둘뿐이다.
- 그 밖에 내부 표시용으로 쓰는 변수는 사용자 문서에 적지 않는다.

## 9. 오류 처리 정책

| 상황 | 정책 |
|---|---|
| OpenAI 한도 소진 | checkpoint 만들기 → 사용자에게 묻기 → 승인했을 때만 넘겨 쓰기 (REQ-ROUTE-002) |
| OpenAI 남은 한도 안내 | 실패로 보지 않는다. 세션을 멈추지도, 묻지도 않는다 (REQ-ROUTE-003) |
| OpenAI 인증 실패 | 알리지 않은 채 DeepSeek로 넘기지 않는다. 사용자에게 보여 준다 (REQ-ROUTE-003) |
| OpenAI network 오류·5xx | Codex가 정해진 횟수만큼 다시 시도하게 두고, Router는 provider를 바꾸지 않는다 |
| DeepSeek 실패 | 종류를 나눈 뒤 한국어로 원인과 해결 방법을 알린다. 끝없이 다시 시도하지 않는다 (REQ-ERR-001) |
| DeepSeek Key 없음 | Codex를 시작하지 않고 exit code 78 (REQ-ROUTE-002, REQ-ROUTE-005) |
| 잔액 조회 실패 | 세션을 멈추지 않고 금액을 지어내지 않는다 (REQ-BALANCE-001) |
| 모델 목록 내려받기·검사 실패 | 기존 목록을 그대로 두고 설치본도 바꾸지 않는다 (REQ-CATALOG-001, NFR-COMPAT-001) |
| 상태 파일 손상 | `OPENAI_ACTIVE`로 되돌리고 계속 진행 (REQ-STATE-001) |
| 확인(probe) 실패 | 대기 상태(cooldown)를 늘린다. 사용자 작업을 멈추지 않는다 (REQ-ROUTE-004) |
| DeepSeek도 쓸 수 없음 | 세 번째 provider로 넘어가지 않고 이유를 보여 준 뒤 끝낸다 (REQ-ERR-001) |

공통 원칙은 세 가지다.

1. **일부만 성공해도 된다.** 덧붙은 기능(잔액 조회, 모델 목록 확인, checkpoint의 git 기록)이 실패해도 Codex 세션 자체는 막지 않는다.
2. **모르면 지어내지 않는다.** 분류할 수 없으면 `UNKNOWN`, 시각을 모르면 `unavailable`, 금액을 모르면 표시하지 않는다.
3. **사용자 자산은 그대로 둔다.** 어떤 오류가 나도 작업 파일, Codex thread, checkpoint, 기존 Codex 설정을 지우지 않는다.

## 10. 보안 요구사항

기준은 NFR-SEC-001이다. 자격증명을 어디에 두는지, Key를 바꾸는 명령은 어떻게 쓰는지는 `USAGE_AND_SPEC.md` 9절에 있다.
이 Git 저장소에는 Key 파일, Keychain 항목 값, 개인 절대경로를 넣지 않는다.

## 11. 테스트 사양

ID 규칙: `TEST-<CATEGORY>-NNN`. 아래 항목은 모두 저장소에 있는 테스트 파일과 짝을 이룬다.
모든 테스트는 프로젝트 루트에서 `python3 -m unittest discover -s tests`로 실행한다.

### TEST-ROUTE-001

#### 검증 대상
REQ-ROUTE-002

#### 선행 조건
없음. 확인 함수와 TTY 여부를 직접 불러서 검사한다.

#### 절차
`tests/test_router.py`에서 다음 테스트를 실행한다.

- `test_confirm_answer_accepts_only_yes_variants`
- `test_confirm_fallback_declines_when_stdin_is_not_a_tty`
- `test_mark_openai_active_resets_state`

#### Expected Result
- `y`/`yes` 계열만 승인으로 본다.
- stdin이 TTY가 아니면 묻지 않고 거절한다.
- OpenAI로 돌아올 때 대기 상태(cooldown)에 쓰던 값이 모두 지워진다.

### TEST-ROUTE-002

#### 검증 대상
REQ-ROUTE-003

#### 선행 조건
없음. Codex가 찍는 문구를 문자열로 넣어 분류기를 검사한다.

#### 절차
`tests/test_router.py`에서 다음 테스트를 실행한다.

- `test_usage_exhaustion_messages_trigger_fallback`
- `test_usage_notices_do_not_trigger_fallback`
- `test_openai_window_classification`
- `test_reset_time_is_only_accepted_when_provider_supplies_absolute_iso`

`tests/test_hardening.py`에서 다음 테스트를 실행한다.

- `test_positive_remaining_percent_does_not_interrupt_session`

#### Expected Result
- 한도가 다 찼다는 문구만 넘겨 쓰기 후보가 된다.
- 남은 한도·reset 안내와 `Weekly limit: 70% left`는 세션을 멈추지 않는다.
- 절대 ISO 시각이 아닌 reset 표현으로는 시각을 만들지 않는다.

### TEST-ROUTE-003

#### 검증 대상
REQ-ROUTE-004

#### 선행 조건
없음.

#### 절차
`tests/test_router.py`의 `test_staged_backoff`를 실행한다.

#### Expected Result
확인(probe) 간격이 설정한 단계를 차례로 따르고, 마지막 값을 넘지 않는다.

### TEST-ROUTE-004

#### 검증 대상
REQ-ROUTE-005

#### 선행 조건
Key가 없는 경우는 자격증명 조회를 가짜로 바꿔 검사한다.

#### 절차
`tests/test_router.py`에서 다음 테스트를 실행한다.

- `test_deep_shortcut_accepts_an_optional_leading_codex_word`
- `test_deepseek_shortcut_without_key_exits_before_starting_codex`
- `test_deepseek_args_removes_user_provider_overrides`

`tests/test_hardening.py`에서 다음 테스트를 실행한다.

- `test_forced_noninteractive_execution_keeps_deepseek`
- `test_deepseek_args_respects_prompt_separator_and_equals_options`

`tests/test_windows_scripts.py`에서 다음 테스트를 실행한다.

- `test_force_flag_injects_deepseek_profile`
- `test_deep_shortcut_forces_deepseek_without_an_environment_variable`
- `test_deep_accepts_an_optional_leading_codex_token`

#### Expected Result
- `deep codex ...`와 `deep ...`이 똑같이 동작한다.
- 사용자가 준 provider 지정은 빠지고, `--` 뒤 prompt는 그대로 남는다.
- Key가 없으면 Codex를 시작하지 않고 exit code 78로 끝난다.
- 비대화형 강제 실행도 DeepSeek profile을 그대로 쓴다.
- Windows shim도 같은 규칙을 따른다.

### TEST-ROUTE-005

#### 검증 대상
REQ-ROUTE-001

#### 선행 조건
원본 Codex 자리에 받은 인자(argv)를 적어 두는 실행 파일을 두고, stdin이 TTY인지를 직접 정한다.
자격증명과 network는 쓰지 않는다.

#### 절차
`tests/test_hardening.py`에서 다음 테스트를 실행한다.

- `test_bypass_marker_passes_through_untouched_even_on_a_tty`
- `test_noninteractive_without_force_passes_through_to_plain_codex`
- `test_real_codex_is_an_absolute_path`

#### Expected Result
- `CODEX_ROUTER_BYPASS=1`이면 stdin이 TTY여도 provider를 고르지 않고 인자를 그대로 넘긴다. 그래서 wrapper가 겹쳐 뜨지 않는다.
- 강제 지정이 없는 비대화형 실행은 `--profile`/`--model`을 붙이지 않고 원본 Codex로 그대로 넘어간다.
- `REAL_CODEX`는 절대 경로다.
  이유: 이름으로 부르면 PATH에서 wrapper 자신을 다시 찾는다.

### TEST-STATE-001

#### 검증 대상
REQ-STATE-001

#### 선행 조건
임시 상태 파일에 깨진 값을 써 둔다.

#### 절차
`tests/test_hardening.py`에서 다음 테스트를 실행한다.

- `test_invalid_state_recovers_to_openai`
- `test_return_to_openai_preserves_catalog_metadata`

#### Expected Result
깨진 상태는 `OPENAI_ACTIVE`로 되돌아간다. OpenAI로 돌아올 때 모델 목록 관련 정보(metadata)는 지우지 않는다.

### TEST-MODEL-001

#### 검증 대상
REQ-MODEL-001

#### 선행 조건
임시 config 경로를 쓴다.

#### 절차
`tests/test_router.py`에서 다음 테스트를 실행한다.

- `test_config_defaults_warn_below_one_dollar`
- `test_write_config_round_trips_the_warning_threshold`
- `test_write_config_keeps_the_shipped_explanatory_comments`

`tests/test_model_router.py`에서 다음 테스트를 실행한다.

- `test_model_definitions_have_supported_reasoning`
- `test_reasoning_override_rejects_unsupported_value`

#### Expected Result
- 저장한 설정을 다시 읽어도 같은 값이다.
- 기본값이 문서와 같다.
- 다시 쓴 config에 설명 주석이 남아 있다.
- 지원하지 않는 reasoning 값은 받아들이지 않는다.

### TEST-CATALOG-001

#### 검증 대상
REQ-CATALOG-001

#### 선행 조건
공식 모델 목록(catalog) 응답을 고정된 문자열로 바꿔 쓴다.

#### 절차
`tests/test_router.py`에서 다음 테스트를 실행한다.

- `test_parse_official_catalog`
- `test_parse_official_catalog_rejects_invalid_input`

`tests/test_hardening.py`에서 다음 테스트를 실행한다.

- `test_catalog_rejects_wrong_json_shapes`
- `test_refresh_creates_missing_catalog`
- `test_invalid_remote_catalog_preserves_existing_catalog`

#### Expected Result
- 정상 목록은 slug 목록으로 읽힌다.
- 형식이 깨진 응답은 받아들이지 않는다.
- 잘못된 원격 목록이 기존 목록을 덮어쓰지 않는다.

### TEST-BALANCE-001

#### 검증 대상
REQ-BALANCE-001

#### 선행 조건
잔액 조회 주소(endpoint)의 응답과 자격증명 조회를 가짜로 바꿔 쓴다.

#### 절차
다음 테스트를 실행한다.

- `tests/test_model_router.py`의 `DeepSeekBalanceTests`: 읽기, 기준값, `is_available` 처리, API Key가 드러나지 않는지, 402/network 처리, cache 기록을 본다.
- `tests/test_router.py`의 `BalanceCommandTests` 가운데 명령, `--json`, exit code, threshold, cache 관련 테스트.

#### Expected Result
- USD 금액을 정확히 읽는다.
- 잔액이 기준보다 적으면 `low_balance`로 표시하지만 exit code는 0이다.
- 조회에 실패하면 exit code 1이고, 금액을 지어내지 않는다.
- 출력 어디에도 API Key가 없다.

### TEST-BALANCE-002

#### 검증 대상
REQ-BALANCE-001

#### 선행 조건
provider 상태 cache를 임시 경로에 둔다. 사용자의 진짜 상태를 건드리지 않도록 막는 장치(guard)도 둔다.

#### 절차
`tests/test_router.py`에서 다음 테스트를 실행한다.

- `test_the_deepseek_banner_shows_the_remaining_balance`
- `test_the_fallback_banner_shows_the_remaining_balance`
- `test_a_fresh_cache_is_not_refetched`
- `test_a_stale_cache_is_refreshed_in_a_child_process`
- `test_no_balance_lookup_happens_in_the_process_that_forks_the_pty`
- `test_a_failed_refresh_never_interrupts_the_session`
- `test_a_finished_deepseek_session_reports_the_updated_balance`
- `test_an_openai_only_session_reports_no_deepseek_balance`
- `test_a_noninteractive_deep_run_prints_no_balance_line`

#### Expected Result
- 배너에 cache에 있는 금액이 보인다.
- 오래되지 않은 cache는 다시 조회하지 않는다.
- 오래된 cache는 자식 process에서 새로 조회한다.
- PTY를 만드는(fork) process에서는 조회하지 않는다.
- 조회에 실패해도 세션을 멈추지 않는다.

### TEST-ERR-001

#### 검증 대상
REQ-ERR-001

#### 선행 조건
없음. 오류 문구를 문자열로 넣어 분류기를 검사한다.

#### 절차
`tests/test_router.py`에서 다음 테스트를 실행한다.

- `test_error_classes_do_not_overlap`
- `test_deepseek_error_classification`
- `test_provider_failure_mapping_keeps_402_and_429_distinct`

`tests/test_model_router.py`에서 다음 테스트를 실행한다.

- `test_tc09_rate_limit_is_distinct_from_balance`

#### Expected Result
분류 기준이 서로 겹치지 않는다. 402(잔액)와 429(속도 제한)가 서로 다른 원인으로 남는다.

### TEST-AIROUTE-001

#### 검증 대상
REQ-AIROUTE-001

#### 선행 조건
provider 상태를 고정하고, Codex 실행을 가짜로 바꿔 쓴다.

#### 절차
`tests/test_model_router.py`에서 다음 테스트를 실행한다.

- `test_tc01_*`~`test_tc18_*`
- `test_original_prompt_is_preserved_as_single_argument`
- `test_manual_model_selection_persists_for_execution_decision`
- `test_usage_log_never_stores_original_prompt`
- `test_unavailable_without_reset_does_not_invent_time`

#### Expected Result
- 작업 성격별 추천이 기대한 모델·reasoning과 같다.
- 쓸 수 없는 provider의 모델은 후보에서 빠진다.
- 그 실행에만 쓰는 인자가 전역 config를 바꾸지 않는다.
- 원래 prompt가 인자 하나로 그대로 넘어간다.
- 사용 기록에 원래 prompt가 남지 않는다.

### TEST-SEC-001

#### 검증 대상
NFR-SEC-001

#### 선행 조건
임시 홈 디렉터리와 시험용 Key 파일(fixture)을 쓴다.

#### 절차
다음 테스트를 실행한다.

- `tests/test_install.py`의 `test_key_file_is_imported_but_not_copied_to_installed_files`
- `tests/test_model_router.py`의 `test_fetch_balance_never_returns_the_api_key`, `test_usage_log_never_stores_original_prompt`
- `tests/test_windows_scripts.py`의 `test_cmd_shims_do_not_contain_secrets`, `test_installer_accepts_key_file_and_ignores_plaintext_storage`

#### Expected Result
Key는 자격증명 저장소에만 들어간다. 설치본, shim, 출력, 사용 기록 어디에도 남지 않는다.

### TEST-COMPAT-001

#### 검증 대상
NFR-COMPAT-001

#### 선행 조건
설치 스크립트를 임시 디렉터리를 대상으로 실행하거나, 실행하지 않고 내용만 검사한다.

#### 절차
`tests/test_install.py`에서 다음 테스트를 실행한다.

- `test_failed_catalog_download_leaves_installation_untouched`

`tests/test_windows_scripts.py`에서 다음 테스트를 실행한다.

- `test_installer_preserves_original_codex_and_uses_dpapi`
- `test_normal_run_forwards_to_original_codex`
- `test_installer_decodes_the_downloaded_setup_script`
- `test_installer_deploys_the_deep_shim`
- `test_router_recovers_when_saved_codex_path_disappears`
- `test_router_rejects_its_own_saved_shim_and_recovers`
- `test_router_preserves_a_valid_saved_codex_path`
- `test_router_keeps_the_missing_path_error_when_no_alternative_exists`

#### Expected Result
- 모델 목록 내려받기가 실패하면 기존 설치를 건드리지 않는다.
- 설치가 원본 Codex를 그대로 둔다.
- 평소 실행은 원본 Codex로 넘어간다.
- Windows에서 저장한 경로가 없어졌거나 자신의 shim이면 PATH의 다음 실행 파일로 되살린다.
- 올바른 저장 경로는 그대로 둔다.
- 대신 쓸 실행 파일이 없으면 실패한다.

### TEST-UX-001

#### 검증 대상
NFR-UX-001

#### 선행 조건
없음.

#### 절차
`tests/test_router.py`의 `test_sync_window_size`를 실행한다.

#### Expected Result
자식 PTY의 rows/columns가 원래 터미널 값과 같아진다.

### TEST-COST-001

#### 검증 대상
NFR-COST-001

#### 선행 조건
분류기가 몇 번 불렸는지 셀 수 있게 가짜로 바꿔 쓴다.

#### 절차
`tests/test_model_router.py`에서 다음 테스트를 실행한다.

- `test_tc14_high_confidence_never_calls_classifier_by_default`
- `test_tc15_low_confidence_can_call_enabled_classifier`
- `test_tc16_classifier_is_never_sol_or_pro`
- `test_tc17_runtime_arguments_do_not_mutate_global_config`
- `test_refresh_stamps_the_balance_check_time_like_a_direct_lookup`

#### Expected Result
- 기본 설정에서는 분류기를 한 번도 부르지 않는다(0회).
- 분류기를 켜도 비싼 모델은 쓰지 않는다.
- 실행이 전역 config를 바꾸지 않는다.
- 상태를 새로 알아보는 일이 세션마다 되풀이되지 않는다.

### TEST-COST-002

#### 검증 대상
REQ-COST-001

#### 선행 조건
Node가 아닌 Python `unittest`로 실행한다. 합성 rollout 파일과 임시 원장 경로를 쓴다.
사용자의 진짜 `~/.codex/router` 상태는 건드리지 않는다.

#### 절차
`tests/test_router.py`의 `CostLimitTests`를 실행한다. 이 테스트는 다음을 확인한다.

1. 합성 rollout에 `token_count` 이벤트를 넣어 세션 사용량을 계산한다.
2. `last_token_usage`가 스냅샷 뒤에 늘어난 부분만 더하는지 본다(`resume` 대응).
3. 오늘·월간 한도를 넘긴 상태에서 `limit_block`이 막는 이유를 돌려주는지 본다.
4. 넘겨 쓴 지 아무리 오래되어도 시간 때문에는 막지 않는지 본다.
5. 한도를 0으로 두면 막지 않는지 본다.
6. 막힌 실행이 PTY를 만들지 않고 종료 코드 75로 끝나는지 본다.

#### Expected Result
- 비용 한도를 넘은 DeepSeek 실행은 시작되지 않고, 이유가 출력된다.
- OpenAI 실행, 한도 0 설정, 넘겨 쓴 지 오래된 경우는 막지 않는다.
- 비용은 token 사용량 × `models.toml` 단가로 계산된다.
- 이어서 실행한 세션은 이번 실행분만 기록된다.
- 단가를 모르는 모델은 `cost_usd: null`로 남는다.

### TEST-CTX-001

#### 검증 대상
REQ-CTX-001

#### 선행 조건
임시 `SESSIONS_DIR`에 합성 rollout 파일을 쓴다. 사용자의 진짜 `~/.codex/router` 상태와 `~/.codex/sessions`은 건드리지 않는다.

#### 절차
`tests/test_router.py`에서 다음 테스트를 실행한다.

- `FallbackResumeTests`
- `BalanceCommandTests`의 `test_usage_limit_fallback_resumes_the_same_session_by_id`
- `BalanceCommandTests`의 `test_usage_limit_fallback_opens_the_picker_when_no_session_id_is_found`

이 테스트는 사용 한도(usage limit) 문구를 알아챈 뒤 승인했을 때 다음을 확인한다.

- 두 번째 Codex 호출이 `resume --last`를 쓰지 않고, 방금 끝난 세션의 id를 받는다.
- id를 찾지 못하면 선택 화면(picker, `resume`에 id 없음)으로 가고 안내가 출력된다.

#### Expected Result
- 넘겨 쓸 때의 인자(argv)에 `--last`가 없고, 찾은 session id가 `resume`의 위치 인자로 넘어간다.
- session id는 provider(`openai`), 작업 디렉터리(Unicode 정규화 포함), originator가 맞는 가장 최근 rollout에서만 고른다.
- 시각 범위 밖, 다른 provider, 다른 cwd, TUI가 아닌 세션은 무시한다.
- id가 없으면 사용자에게 알리고 선택 화면(picker)을 연다.

## 12. 요구사항 추적성

| Requirement | Implementation | Test | Status |
|---|---|---|---|
| REQ-ROUTE-001 | `src/codex_router.py` | TEST-ROUTE-005 | verified |
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
| NFR-COMPAT-001 | `scripts/install.sh`, `scripts/uninstall.sh`, `scripts/install.ps1`, `scripts/uninstall.ps1`, `scripts/codex-router.ps1` | TEST-COMPAT-001 | verified |
| NFR-UX-001 | `src/codex_router.py` | TEST-UX-001 | verified |
| NFR-COST-001 | `src/model_router.py`, `config/config.toml` | TEST-COST-001 | verified |
| REQ-COST-001 | `src/codex_router.py` | TEST-COST-002 | implemented |

Status 값: `draft` (사양만 있음) / `implemented` / `verified` (실제 실행까지 확인) /
`deprecated`.

## 13. 미확정 사항

- **비용 계산에 빠지는 기록(커버리지).** 2026-09-19 이전 세션은 원장에 없어서 한도 계산에 들어가지 않는다. rollout에 token 기록을 남기지 않은 실행도 `cost_usd: null`로 남는다. 그래서 `codex-router cost`의 "비용 근거가 없는 기록" 수가 0이 아닌 동안에는 합계가 쓴 돈보다 작게 나올 수 있다.
- **macOS 원본 Codex 경로.** 원본 실행 파일 경로가 Apple Silicon Homebrew 기준의 한 값으로 코드에 박혀 있다. Intel Mac이나 npm 설치 위치(prefix)가 다른 경우 어떻게 동작해야 하는지 정해지지 않았다. 자동으로 찾을지, 설치할 때 정할지, 설정 항목으로 뺄지가 남아 있다.
- **같은 cwd에 Codex 세션이 동시에 여러 개 열려 있을 때 이어 열 대상.** 넘겨 쓸 때는 방금 끝난 OpenAI 세션의 id를 rollout 메타 정보에서 찾아 `resume <session_id>`로 이어 간다. 같은 cwd에 OpenAI 세션이 여럿 열려 있으면 가장 최근에 바뀐 세션을 고르므로, 둘 이상이 동시에 한도에 걸리면 이어 열 대상이 섞일 수 있다. id를 찾지 못하면 선택 화면(picker)에서 사용자가 고른다.
- **Windows에서 제거한 뒤 남는 DPAPI Key 파일.** 실행 중인 설치 디렉터리는 지울 수 없어서 Key 파일이 남고, 그 경로만 안내한다. 이것을 괜찮은 동작으로 볼지, 다음 로그인 때 정리해야 하는 결함으로 볼지 정해야 한다.

## 14. 향후 개선 후보

- DeepSeek도 쓸 수 없을 때 세 번째 provider로 넘기기(지금은 범위에서 뺀 항목이다).
- 한도가 다 찬 것을 화면 문구 대신 정해진 형식의 신호로 알아내는 방법(지금은 문구가 바뀌면 찾는 패턴도 고쳐야 한다).
- Codex custom provider의 연결 대기 시간(connect timeout) 줄이기(지금은 공식 설정 항목이 없다).
