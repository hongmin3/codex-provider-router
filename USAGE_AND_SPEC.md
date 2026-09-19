# Codex Provider Router 사용법 및 요구사항 기반 사양서

## 1. 목적

```text
codex --yolo
├─ OpenAI 사용 가능
│  └─ ChatGPT 로그인 기반 Codex 사용
└─ OpenAI usage limit
   ├─ Context checkpoint 생성
   ├─ 사용자에게 y/N 확인 (기본값: 전환 안 함)
   ├─ y: 같은 Codex thread를 DeepSeek로 resume
   │  ├─ 현재 작업은 DeepSeek에서 완료
   │  └─ 다음 Codex 실행 전 OpenAI probe
   │     ├─ 성공: OpenAI로 복귀
   │     └─ 실패: DeepSeek 유지
   └─ n: 전환 없이 종료
```

사용자는 provider별 명령을 따로 실행하지 않고 평소와 같이 `codex --yolo`를 사용한다.

## 2. 현재 기준 환경

```text
Mac
├─ macOS 26.6.2
├─ Apple M5 / arm64
├─ Shell: zsh 5.9
├─ Homebrew: /opt/homebrew/bin/brew
└─ Codex
   ├─ Version: 0.154.0
   ├─ Install: npm global (@openai/codex)
   ├─ Original binary: /opt/homebrew/bin/codex
   └─ Authentication: ChatGPT login
```

## 3. Provider 사양

```text
Providers
├─ Primary: OpenAI
│  ├─ Auth: ChatGPT account login
│  ├─ API Key 방식으로 변경하지 않음
│  ├─ Model: Codex 기본 또는 TUI `/model` 선택값
│  └─ Router가 OpenAI model을 덮어쓰지 않음
└─ Fallback: DeepSeek
   ├─ Endpoint: https://api.deepseek.com
   ├─ Protocol: Responses API
   ├─ Default model: deepseek-flash
   ├─ Default reasoning: high
   └─ Auth environment: DEEPSEEK_API_KEY
```

DeepSeek의 과거 명칭 `deepseek-v4-flash`와 `deepseek-v4-flash-vision-exp`는 현재 공식 catalog에서 `deepseek-flash`로 매핑된다.

## 4. 사용 방법

### 4.1 평소 실행

```bash
codex --yolo
```

Router는 `~/.local/bin/codex`에 있으며 실제 Codex는 절대 경로 `/opt/homebrew/bin/codex`로 호출하여 recursion을 방지한다.

### 4.2 OpenAI 모델 변경

Codex TUI 안에서 실행한다.

```text
/model
```

OpenAI 모델 선택값은 DeepSeek fallback 설정과 독립적이다.

### 4.3 DeepSeek 모델 관리

```bash
# 현재 모델과 선택 가능한 alias
codex-router model

# 일반 coding·자동화·반복 작업
codex-router model flash

# 복잡한 architecture·refactoring·debugging
codex-router model pro

# screenshot·image·GUI 분석
codex-router model vision
```

```text
Alias mapping
├─ flash  -> deepseek-flash
├─ pro    -> deepseek-v4-pro
└─ vision -> deepseek-flash (현재 image-capable Flash)
```

모델은 임의로 자동 변경되지 않으며 사용자가 명시적으로 바꾼 값이 다음 fallback에서도 유지된다.

### 4.4 DeepSeek Reasoning 관리

```bash
codex-router reasoning
codex-router reasoning low
codex-router reasoning high
codex-router reasoning max
```

허용값은 `low`, `high`, `max`이며 기본값은 `high`다. 지원하지 않는 값은 config에 저장하지 않고 exit code 2로 종료한다.

### 4.5 DeepSeek 최신 모델 탐지

```bash
# 설치된 catalog·new/current 표시·마지막 확인 시각
codex-router models list

# 공식 catalog를 즉시 확인하고 새 모델이 있으면 갱신
codex-router models check

# cache 시각과 관계없이 공식 catalog를 다시 받아 검증·갱신
codex-router models refresh
```

```text
Automatic model discovery
├─ `codex` 실행 시 확인
├─ 최대 빈도: 24시간에 1회
├─ Source: DeepSeek 공식 Codex setup catalog
├─ Download timeout: 5초
├─ JSON·model slug 구조 검증
├─ 기존 catalog backup: deepseek-models.previous.json
├─ 새 model slug 터미널 알림
├─ 새 모델을 목록에 `new`로 표시
├─ 현재 fallback model은 자동 변경하지 않음
├─ 현재 model이 공식 catalog에서 제거된 경우 갱신 중단·경고
└─ Network/format 실패 시 기존 catalog 유지
```

### 4.6 상태·진단·로그

```bash
codex-router status
codex-router doctor
codex-router logs
```

`status`는 Primary provider/model, fallback provider/model/reasoning, active provider, OpenAI 상태, cooldown, next probe, Key 설정 여부를 보여준다. 마지막으로 확인한 DeepSeek 잔액이 cache에 있으면 금액과 확인 시각을 함께 표시하고, 경고 기준 미만이면 경고 줄을 덧붙인다.

### 4.7 DeepSeek 잔액 확인과 부족 경고

```bash
codex-router balance
codex-router balance --json
codex-router balance threshold
codex-router balance threshold 2.5
ai balance
```

- `balance`는 DeepSeek 공식 `/user/balance` endpoint를 호출해 USD 잔액(총액·충전·증정)과 사용 가능 여부를 표시한다. `ai balance`도 같은 결과를 낸다.
- `--json`은 `status`, `checked_at`, `threshold_usd`, `low_balance`, `balance`를 담은 JSON을 출력한다. 자동화·모니터링에서 사용한다.
- 조회에 성공하면 종료 코드는 0, 실패하면 1이다. 잔액이 기준 미만이어도 조회 자체가 성공했으면 0이다.
- 조회 결과는 `~/.codex/router/provider-status.json`에 기록되어 `codex-router status`와 `ai status`가 network 호출 없이 같은 금액을 보여준다.
- `balance threshold`는 경고 기준(USD)을 읽고, 값을 주면 config에 저장한다. 기본값은 USD 1.00이다.
- DeepSeek 세션에 들어가면 시작 배너에 잔액이 함께 나오고, 세션이 끝나면 새로 조회한 잔액을 한 줄 더 표시한다. 실제 출력:

```text
[Codex Router] Provider: DeepSeek | Model: deepseek-flash | Reasoning: high | requested | Balance: USD 2.67
codex-cli 0.155.1
[Codex Router] DeepSeek 잔액: USD 2.67
```

- 시작 배너의 금액은 cache에서 읽는다. cache가 `provider_cache.ttl_minutes`(기본 15분)보다 오래되었으면 **자식 process**(`codex_router.py balance --json`)로 갱신한다. wrapper가 Codex 세션을 `forkpty()`로 띄우기 때문에, 이 process에서 HTTPS 요청을 하면 macOS resolver가 남긴 thread 때문에 multi-thread 상태로 fork하게 되고 Python이 deadlock 경고를 낸다. 조회는 자식에게 맡기고 부모는 cache만 읽는다.
- 잔액이 기준 미만이면 DeepSeek 세션이 시작될 때 경고를 stderr에 한 번 표시한다. OpenAI 사용 한도로 자동 전환되는 경우에도 같다.
- Codex TUI의 `/status` 화면에는 잔액을 넣지 않는다. 그 화면은 Codex binary가 그리는 것이라 wrapper가 줄을 추가하면 TUI가 다시 그릴 때 깨진다. 세션 중간에 확인하려면 다른 터미널에서 `codex-router balance`를 실행한다.
- 잔액 부족 경고는 provider를 중단시키지 않는다. DeepSeek가 `is_available: false`(잔액 소진)를 보고할 때만 `BALANCE_EXHAUSTED`로 기록되어 routing에서 제외된다.
- API Key는 요청 header로만 사용하고 출력·로그·config에 남기지 않는다.

### 4.8 테스트

```bash
codex-router test openai
codex-router test deepseek
codex-router test fallback
codex-router test return
codex-router test checkpoint

# Interactive 강제 fallback
FORCE_DEEPSEEK=1 codex --yolo

# 같은 실행을 줄여 쓰는 DeepSeek 전용 명령
deep codex --yolo
codex-router deep --yolo
codex-router deepseek --yolo
```

`FORCE_DEEPSEEK=1`은 해당 명령 한 번에만 DeepSeek를 선택하며 Router의 OpenAI/cooldown 상태는 변경하지 않는다. 숫자 `1`은 model 번호가 아니라 활성화를 의미하는 boolean flag다.

`deep` 명령과 `codex-router deep|deepseek`는 OpenAI 상태·cooldown과 무관하게 DeepSeek를 선택한다. 첫 인자가 `codex`이면 무시하므로 `deep codex --yolo`와 `deep --yolo`가 동일하며, 비대화형 `deep exec "..."`도 DeepSeek profile을 유지한다. Key가 없으면 Codex를 시작하지 않고 exit code 78로 종료한다.

일반 `codex` 명령은 기존과 동일하게 ChatGPT 로그인 Codex(GPT 모델)를 사용한다. DeepSeek는 `deep` 접두어를 붙였을 때만 선택되며, 두 명령은 같은 Codex binary와 같은 config·MCP·project trust를 공유한다.

### 4.9 상태 초기화

```bash
codex-router reset
```

Router 상태만 `OPENAI_ACTIVE`로 복귀한다. ChatGPT 로그인, Codex config, MCP, DeepSeek Key와 모델 설정은 삭제하지 않는다.

## 5. 상태 머신

```text
OPENAI_ACTIVE
├─ OpenAI 정상
│  └─ OpenAI Codex 실행
└─ Usage limit 감지
   ├─ Checkpoint 생성
   ├─ 사용자 y/N 확인
   ├─ n: 전환 없이 종료
   └─ y: OPENAI_COOLDOWN
      ├─ DeepSeek 설정·Key 확인
      └─ DEEPSEEK_ACTIVE
         ├─ 현재 작업은 DeepSeek에서 종료
         └─ 다음 `codex` 실행
            ├─ Probe 시간 전: 사용자 확인 후 DeepSeek 유지
            └─ Probe 시간 도달
               ├─ OpenAI 성공: OPENAI_ACTIVE
               └─ OpenAI 실패: cooldown 연장
```

Probe backoff는 10분, 20분, 30분, 이후 60분이다. DeepSeek 작업 진행 중에는 OpenAI로 바꾸지 않는다.

## 6. OpenAI 오류 분류

```text
OpenAI failure
├─ Usage/quota
│  ├─ usage limit
│  ├─ session limit
│  ├─ quota exceeded
│  ├─ too many requests / rate limit
│  └─ HTTP 429
│     └─ DeepSeek fallback 후보
├─ Authentication
│  ├─ unauthorized / forbidden
│  ├─ login expired
│  └─ HTTP 401 / 403
│     └─ 조용히 fallback하지 않음
└─ Network/server
   ├─ timeout / DNS / connection reset
   └─ HTTP 5xx
      └─ Codex의 제한된 retry 적용
```

`usage limit resets available`, `less than 25% left`, `26% left`, `resets in ...`과 같은 정상 잔여량·reset 안내는 fallback 조건에서 제외한다. `hit`, `reached`, `exceeded`, `no usage left`, `0% left`, HTTP 429처럼 실제 실패를 나타내는 문구만 전환 후보로 판단한다.

## 7. DeepSeek 오류 안내

DeepSeek 세션이 비정상 exit code로 종료되면 Router가 TTY의 최근 오류 문구를 분류하고 한국어로 안내한다.

```text
DeepSeek failure
├─ BILLING
│  ├─ HTTP 402
│  ├─ insufficient balance/funds/credits
│  └─ 안내: DeepSeek Platform 잔액 충전 후 연결 테스트
├─ QUOTA
│  ├─ HTTP 429
│  ├─ quota exceeded
│  └─ 안내: 잠시 후 재시도 또는 한도 확인
├─ AUTH
│  ├─ HTTP 401 / 403
│  ├─ unauthorized / forbidden
│  └─ 안내: `codex-router key set`
├─ NETWORK
│  ├─ timeout / DNS
│  ├─ connection reset/refused
│  └─ 안내: 인터넷·DNS 확인 후 재시도
├─ SERVER
│  ├─ HTTP 5xx
│  └─ 안내: DeepSeek 임시 장애, 잠시 후 재시도
└─ UNKNOWN
   └─ 안내: 화면의 Codex 오류와 `codex-router logs` 확인
```

공통으로 “작업 파일과 Codex 대화 기록은 삭제되지 않았음”을 안내한다. Router 로그에는 원본 API 응답이 아닌 `failure_type`, provider, model, exit code만 기록한다.

## 8. Context 보존

```text
~/.codex/router/fallback-state/
└─ <working-directory-hash>/
   └─ <YYYYMMDD-HHMMSS>/
      ├─ checkpoint.md
      │  ├─ 시각·원인·working directory
      │  ├─ AGENTS.md
      │  ├─ README.md
      │  └─ progress.md
      ├─ git-status.txt
      └─ git-diff.txt
```

대화, 사용자 요청, tool call, 명령 결과, TODO는 Codex persisted thread에 보존되며 DeepSeek 전환 시 `resume --last`로 재개한다.

## 9. 보안

```text
Secrets
├─ DEEPSEEK_API_KEY
│  ├─ 1순위: process environment
│  └─ 2순위: macOS Keychain
├─ Keychain service: codex-router-deepseek
├─ Key 변경: codex-router key set
├─ Key 파일·config·Git·log 저장 금지
└─ 민감 파일·config·state·log 권한: 600/700
```

Key 설정 시 입력은 화면에 표시되지 않고 두 번 입력해 일치 여부를 확인한다.

## 10. 설정·상태·로그 경로

```text
~/.local/bin/
├─ codex -> ~/.codex/router/codex_router.py
└─ codex-router -> ~/.codex/router/codex_router.py

~/.config/codex-router/
└─ config.toml

~/.codex/
├─ config.toml                 # 기존 Codex config, 보존
├─ auth.json                   # 기존 ChatGPT 인증, 보존
├─ deepseek.config.toml        # DeepSeek Codex profile
└─ router/
   ├─ codex_router.py
   ├─ deepseek-models.json
   ├─ state.json
   ├─ logs/
   ├─ fallback-state/
   └─ uninstall.sh

~/.codex-backup/<timestamp>/            # 설치 전 백업
```

### Router config

```toml
[primary]
provider = "openai"

[fallback]
provider = "deepseek"
model = "deepseek-flash"
reasoning_effort = "high"

[routing]
auto_fallback = true
auto_return = true
probe_minutes = [10, 20, 30, 60]
max_fallback_minutes = 480
catalog_check_hours = 24

[cost]
daily_limit_usd = 25.0
monthly_limit_usd = 100.0
low_balance_usd = 1.0
```

API Key는 이 파일에 저장하지 않는다.

## 11. 로그 사양

```text
~/.codex/router/logs/router-YYYY-MM.jsonl
├─ timestamp
├─ event
├─ provider
├─ model
├─ reasoning
├─ fallback reason
├─ probe result
├─ checkpoint path
├─ failure_type
└─ exit_code
```

API Key, access token, session token, password, 원본 오류 본문은 기록하지 않는다. 월별 파일 중 최근 6개를 보존한다.

## 12. 비용 보호

- fallback 시작 시 provider, model, reasoning을 터미널에 표시한다.
- 세션 시작·종료·실패를 로그한다.
- `daily_limit_usd`, `monthly_limit_usd`, `max_fallback_minutes`를 config에서 관리한다.
- `low_balance_usd`(기본 1.0) 미만이면 DeepSeek 세션 시작 시 잔액 경고를 표시한다. `codex-router balance`로 현재 잔액을 확인한다.
- 현재 Codex TUI가 wrapper에 안정적인 정형 token/cost event를 제공하지 않아 일간·월간 비용 hard-stop은 적용되지 않는다.

## 13. 기존 Codex 보존

Router는 다음 항목을 임의로 초기화하거나 삭제하지 않는다.

- `~/.codex/config.toml`
- `~/.codex/auth.json`
- ChatGPT 로그인
- MCP 설정
- Plugin 설정
- Project trust
- OpenAI 모델 선택
- `/opt/homebrew/bin/codex` 원본 실행 파일

긴급 시 Router를 우회하려면 다음을 사용한다.

```bash
/opt/homebrew/bin/codex --yolo
```

## 14. 제거 및 Rollback

```bash
codex-router uninstall
```

```text
Uninstall
├─ ~/.local/bin/codex symlink 제거
├─ ~/.local/bin/codex-router symlink 제거
├─ ~/.zshrc Router PATH block 제거
├─ ~/.codex/deepseek.config.toml 제거
├─ ~/.config/codex-router/config.toml 제거
├─ Router용 Keychain 항목 제거
├─ Router state/log/checkpoint를 Trash로 이동
└─ 기존 Codex config·ChatGPT login·MCP는 보존
```

## 15. 테스트 기준

```text
Tests
├─ OpenAI 정상: ChatGPT login provider 사용
├─ DeepSeek 연결: Responses API 정상 응답
├─ Forced fallback: 현재 model/reasoning 적용
├─ Context: checkpoint·Git status·diff 생성
├─ Return: 다음 실행부터 OpenAI 복귀
├─ Missing Key: crash 없이 exit code 78
├─ DeepSeek failure: 유한 retry 후 사용자 안내
├─ Existing tools: shell·file·apply_patch·Git·MCP
├─ Model persistence: 새 process에서도 변경값 유지
└─ Error classification: billing·quota·auth·network·server·unknown
```

## 16. 현재 제한사항

1. Codex CLI 0.154.0에는 interactive provider hot-swap API가 없어 persisted thread의 `resume --last`로 대체한다.
2. Usage-limit 감지는 interactive TTY에 표시된 문구를 기준으로 하므로 서버 문구가 바뀌면 패턴 갱신이 필요하다.
3. `resume --last`를 사용하므로 여러 Codex 세션을 동시에 운영하는 경우 마지막 thread 선택에 주의한다.
4. Custom provider config에 Codex connect timeout을 별도로 단축하는 공식 필드가 없어 network failure의 최종 종료까지 시간이 걸릴 수 있다.
5. DeepSeek도 사용할 수 없으면 제3 provider로 전환하지 않고 이유와 대응 방법을 표시한 후 종료한다.
6. 작업 파일, Codex thread, checkpoint는 오류 발생으로 삭제되지 않는다.

## 17. TTY 화면 크기 동기화

Router가 `forkpty()`로 Codex를 실행할 때 실제 터미널의 rows/columns를 child PTY에 복사한다. 터미널 창 크기가 바뀌면 `SIGWINCH`를 받아 즉시 다시 동기화하므로 Codex TUI가 한 글자씩 줄바꿈되는 현상을 방지한다.

## 18. 관리 명령 빠른 참조

| 목적 | 명령 |
|---|---|
| 평소 Codex | `codex --yolo` |
| OpenAI 모델 | Codex TUI의 `/model` |
| 상태 | `codex-router status` |
| DeepSeek 잔액 | `codex-router balance`, `ai balance` |
| 잔액 JSON | `codex-router balance --json` |
| 잔액 경고 기준 | `codex-router balance threshold`, `codex-router balance threshold 2.5` |
| DeepSeek 모델 확인 | `codex-router model` |
| 최신 모델 목록 | `codex-router models list` |
| 최신 모델 확인 | `codex-router models check` |
| 공식 catalog 강제 갱신 | `codex-router models refresh` |
| Flash | `codex-router model flash` |
| Pro | `codex-router model pro` |
| Vision | `codex-router model vision` |
| Reasoning 확인 | `codex-router reasoning` |
| Reasoning 변경 | `codex-router reasoning high` |
| Key 변경 | `codex-router key set` |
| 진단 | `codex-router doctor` |
| 로그 | `codex-router logs` |
| OpenAI 테스트 | `codex-router test openai` |
| DeepSeek 테스트 | `codex-router test deepseek` |
| DeepSeek 전용 실행 | `deep codex --yolo`, `codex-router deep|deepseek --yolo` |
| 강제 fallback | `FORCE_DEEPSEEK=1 codex --yolo` |
| Router 상태 초기화 | `codex-router reset` |
| 제거 | `codex-router uninstall` |

## 19. 작업별 Model Router (`ai`)

```text
ai
├─ Prompt 입력
│  ├─ ai "..."
│  ├─ ai --prompt-file task.md
│  ├─ cat task.md | ai
│  └─ 인자 없는 ai → 터미널에서 한 줄 입력
├─ Provider status cache 확인
│  ├─ OpenAI: 기존 failover state + ChatGPT login
│  ├─ DeepSeek: Keychain + 공식 /user/balance + /models
│  └─ reset_at: Provider가 절대시각을 제공한 경우에만 저장
├─ Local repository metadata 수집
│  ├─ file count (최대 5,000개까지만 순회)
│  ├─ 주요 language
│  ├─ Git modified count
│  ├─ test/build system 존재 여부
│  └─ repository 내용은 classifier에 전달하지 않음
├─ Prompt score (각 0~10)
│  ├─ complexity / reasoning / coding / context_size
│  ├─ failure_cost / agentic_work / repetitiveness
│  └─ deterministic_level / ambiguity / dependency_depth
├─ 사용 가능한 모델만 후보로 생성
│  ├─ GPT-5.6 Luna → gpt-5.6-luna
│  ├─ DeepSeek V4 Flash → 실제 계정 ID deepseek-flash
│  ├─ DeepSeek V4 Pro → deepseek-v4-pro
│  └─ GPT-5.6 Sol → gpt-5.6-sol
├─ Local heuristic 추천
│  ├─ 정형·대량·반복 → Flash / Low
│  ├─ 일반 coding·shell·Python → Luna / Low~Medium
│  ├─ repo·multi-file·dependency → Pro / High
│  └─ 인증·배포·고위험 architecture → Sol / High~Max
├─ Confidence
│  ├─ 기본 classifier.enabled=false → 항상 추가 LLM token 0
│  └─ 명시적으로 활성화 + threshold 미만
│     └─ DeepSeek Flash / Low, output 최대 150 token
├─ 승인 UI
│  ├─ Y: 추천 실행
│  ├─ M: 사용 가능한 모델 직접 선택
│  ├─ R: 해당 모델 지원 reasoning으로 변경
│  ├─ D: 상세 점수
│  ├─ S: Provider 상태
│  └─ N: 취소
├─ Codex 실행
│  ├─ 전역 config를 수정하지 않음
│  ├─ --model + -c model_reasoning_effort runtime override
│  ├─ 원본 Prompt를 하나의 인자로 그대로 전달
│  └─ MCP·trust·history·승인 정책은 기존 Codex config 유지
├─ 실패 처리
│  ├─ Provider availability
│  │  ├─ OpenAI limit → GPT 전체 제외, DeepSeek 추천 후 승인
│  │  └─ DeepSeek 402/auth → DeepSeek 전체 제외, GPT 추천 후 승인
│  ├─ Transient infrastructure
│  │  ├─ DeepSeek 429 → RATE_LIMITED (balance와 구분)
│  │  └─ timeout/5xx → SERVER_ERROR
│  └─ Model capability
│     └─ 동일 작업 유형 2회 실패 → 원인별 상위 모델 추천 후 승인
└─ 사용 기록
   ├─ 원본 Prompt 미저장, prompt_hash만 기록
   ├─ routing/task token을 분리 (TUI가 미제공한 값은 null)
   ├─ classifier/local, confidence, model, reasoning, result 기록
   └─ API Key·token·Authorization header 기록 금지
```

### 명령

| 목적 | 명령 |
|---|---|
| 추천 후 실행 | `ai "작업 내용"` |
| 실행 없이 추천 | `ai --dry-run "작업 내용"` |
| 상세 점수 포함 | `ai --explain "작업 내용"` |
| 캐시 상태 | `ai status` |
| 저비용 endpoint로 갱신 | `ai status --refresh` |
| 설치·인증·설정 진단 | `ai doctor` |

### 설정 및 상태

```text
~/.config/codex-router/
├─ config.toml          # threshold, classifier, 자동 실행 정책
└─ models.toml          # 모델 ID, reasoning, context, 가격, capability

~/.codex/router/
├─ provider-status.json # Provider 상태 cache
├─ model-failures.json  # capability failure 횟수
└─ logs/
   └─ model-router-usage.jsonl
```

OpenAI ChatGPT 구독 quota에는 별도의 공개 preflight를 사용하지 않는다. `ai status --refresh`는 로그인 유효성만 확인하며 quota 확인용 LLM 요청을 만들지 않는다. Session/Weekly 구분은 실제 Codex 오류에 해당 문구가 있을 때만 사용하고, reset 시각을 얻지 못하면 `unavailable`로 표시한다.
