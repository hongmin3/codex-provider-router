# Codex Provider Router

ChatGPT 로그인 Codex를 Primary로 유지하고 필요할 때 DeepSeek Responses API를 사용하는 Codex wrapper입니다. macOS에서는 usage limit 감지 시 y/n 확인 후 전환과 강제 전환을 지원하고, Windows에서는 `deep` 명령(또는 `FORCE_DEEPSEEK`) 강제 전환을 지원합니다. 평소에는 기존처럼 `codex --yolo`를 사용합니다.

## Windows에 설치하기

요구사항은 Windows 10/11, Windows PowerShell 5.1 이상, Git, 그리고 미리 설치·로그인한 Codex CLI입니다. [DeepSeek도 Codex의 Windows PowerShell 설정과 Responses API를 공식 지원](https://api-docs.deepseek.com/quick_start/agent_integrations/codex/) 합니다.

DeepSeek API Key 한 줄만 들어 있는 txt 파일을 준비한 뒤 PowerShell에서 실행합니다.

```powershell
git clone https://github.com/hongmin3/codex-provider-router.git
cd codex-provider-router
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -KeyFile "C:\secure\deepseek-api-key.txt"
```

저장소 루트에 `deepseek-api-key.txt`를 두면 `-KeyFile`을 생략해도 자동으로 가져옵니다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

Installer는 Key를 `%LOCALAPPDATA%\CodexProviderRouter\deepseek-api-key.dpapi`에 Windows DPAPI로 암호화해 저장합니다. 암호화된 Key는 현재 Windows 사용자 계정에서만 복호화할 수 있고, 평문 Key는 Router 설정·로그·Git에 복사되지 않습니다. 원본 txt는 Installer가 자동 삭제하지 않습니다.

새 PowerShell 창을 열고 설치 상태를 확인합니다. `test deepseek`는 소량의 API를 실제 호출합니다.

```powershell
codex-router doctor
codex-router test deepseek
```

### Windows에서 DeepSeek로 실행하기

가장 짧은 방법은 `deep`입니다. PowerShell과 `cmd.exe` 양쪽에서 동작하고, 환경변수를 남기지 않으며, 해당 실행 한 번에만 DeepSeek를 강제합니다.

```powershell
deep --yolo
```

`sudo`처럼 앞에 붙여 쓰는 형태도 같은 결과입니다. 맨 앞의 `codex` 토큰은 제거되고 나머지 인자만 전달됩니다.

```powershell
deep codex --yolo
```

프롬프트를 바로 전달할 수도 있습니다.

```powershell
deep codex --yolo "이 프로젝트의 테스트를 실행하고 실패 원인을 수정해줘"
```

> **`FORCE_DEEPSEEK=1 codex --yolo`는 PowerShell에서 동작하지 않습니다.** `VAR=1 command` 형태의 앞붙임 환경변수는 POSIX shell 문법이며, PowerShell은 이를 명령 이름으로 해석해 `CommandNotFoundException`을 냅니다. PowerShell에서 환경변수 방식을 쓰려면 다음과 같이 분리해야 합니다. Codex를 종료한 뒤 `finally`가 환경변수를 제거하여 다음 실행은 다시 OpenAI를 사용합니다.

```powershell
$env:FORCE_DEEPSEEK = "1"
try { codex --yolo } finally { Remove-Item Env:FORCE_DEEPSEEK -ErrorAction SilentlyContinue }
```

Windows에서 Git Bash를 사용하면 macOS와 완전히 같은 명령을 쓸 수 있습니다.

```bash
FORCE_DEEPSEEK=1 codex --yolo
```

`deep`은 `codex-router deepseek`의 짧은 별칭입니다. 아래 형태도 그대로 사용할 수 있습니다.

```powershell
codex-router deepseek --yolo
deep codex --yolo
```

`deep`는 Windows에서는 `deep.cmd`, macOS에서는 `~/.local/bin/deep`으로 설치되는 같은 이름의 명령입니다. 두 플랫폼 모두 `deep codex --yolo`와 `deep --yolo`가 동일한 실행입니다.

프롬프트를 바로 전달하려면:

```powershell
codex-router deepseek --yolo "이 프로젝트의 테스트를 실행하고 실패 원인을 수정해줘"
deep --yolo "이 프로젝트의 테스트를 실행하고 실패 원인을 수정해줘"
```

기본 OpenAI/ChatGPT 로그인 Codex를 사용할 때는 그대로 실행합니다.

```powershell
codex --yolo
```

Windows wrapper는 원본 Codex 경로를 별도 보존하고 `FORCE_DEEPSEEK=1`일 때만 `deepseek-flash + high`를 주입합니다. 현재 Windows에서는 강제 전환만 지원하며, macOS wrapper의 TTY usage-limit 자동 감지·같은 세션 `resume` failover는 적용되지 않습니다.

보존한 원본 경로가 다른 Router 제거나 Codex 재설치로 사라지면, Windows wrapper는 자신의 shim을 제외한 다음 `codex` 실행기를 PATH에서 찾아 경로를 자동 복구합니다.

## 새 Mac에 동일하게 설치하기

설치 후에는 어느 폴더에서든 다음 명령으로 DeepSeek를 강제 사용할 수 있습니다.

```bash
FORCE_DEEPSEEK=1 codex --yolo
deep codex --yolo
```

`deep`는 Router가 함께 설치하는 DeepSeek 전용 명령입니다. `deep codex --yolo`, `deep --yolo`, `codex-router deep|deepseek --yolo`가 모두 같은 실행이며, 뒤에 프롬프트를 붙이거나 파이프로 넘길 수도 있습니다. `deep exec "..."`처럼 비대화형으로 실행해도 DeepSeek profile이 유지됩니다.

이 설치 방식은 macOS용입니다. 새 Mac에는 Codex CLI가 `/opt/homebrew/bin/codex`에 설치되어 있고 ChatGPT 로그인이 완료되어 있어야 합니다. Git, `curl`, `python3`, macOS Keychain도 사용합니다.

### 권장: Key txt를 저장소 밖에 두기

DeepSeek API Key 한 줄만 들어 있는 txt 파일을 안전한 방식으로 새 Mac에 전달한 뒤 다음과 같이 설치합니다. Key는 Keychain으로 가져오며 Router 설정이나 로그에 복사되지 않습니다.

```bash
git clone https://github.com/hongmin3/codex-provider-router.git
cd codex-provider-router
chmod 600 /path/to/deepseek-api-key.txt
./scripts/install.sh --key-file /path/to/deepseek-api-key.txt
source ~/.zshrc
```

### 간편: 저장소 루트에 Key txt 두기

txt 파일을 클론한 저장소 루트에 `deepseek-api-key.txt`라는 이름으로 두면 설치 스크립트가 자동으로 찾습니다. 이 경로는 `.gitignore`에 등록되어 있습니다.

```bash
cd codex-provider-router
chmod 600 deepseek-api-key.txt
./scripts/install.sh
source ~/.zshrc
```

설치 후 다음으로 상태와 실제 DeepSeek 연결을 확인합니다.

```bash
codex-router doctor
codex-router test deepseek
FORCE_DEEPSEEK=1 codex --yolo
```

`doctor`의 모든 항목이 `PASS`면 설치가 완료된 것입니다. `test deepseek`는 소량의 DeepSeek API를 실제로 호출합니다.

## 프롬프트 입력 방법

### 1. 대화형으로 입력하기

작업할 프로젝트 폴더로 이동한 뒤 Codex를 시작합니다.

```bash
cd /path/to/your-project
FORCE_DEEPSEEK=1 codex --yolo
```

Codex 입력창의 `›` 표시 뒤에 일반 한국어로 작업을 입력하고 Enter를 누르면 됩니다.

```text
› 이 프로젝트의 README를 검토하고 설치 방법을 더 쉽게 정리해줘. 수정 후 테스트도 실행해줘.
```

첫 작업이 끝난 뒤에도 같은 세션에서 추가 지시를 계속 입력할 수 있습니다.

```text
› 방금 변경한 내용을 기준으로 테스트가 빠진 경우를 추가해줘.
```

### 2. 명령에 프롬프트를 바로 넣기

간단한 작업은 Codex를 시작하면서 프롬프트를 함께 전달할 수 있습니다.

```bash
FORCE_DEEPSEEK=1 codex --yolo "README의 오타를 찾아 수정하고 테스트해줘"
```

### 3. 긴 프롬프트를 파일로 입력하기

`task.md`에 작업 내용을 작성한 뒤 zsh에서 파일 내용을 하나의 Prompt로 전달합니다.

```bash
FORCE_DEEPSEEK=1 codex --yolo "$(<task.md)"
```

Prompt는 다음 형식으로 쓰면 결과가 안정적입니다.

```text
목표: 무엇을 완성해야 하는지
대상: 수정할 파일이나 기능
제약: 보존할 동작, 보안, 버전 조건
완료 조건: 필요한 테스트와 결과물
```

예시:

```text
목표: 새 Mac에서 설치가 실패하는 원인을 찾고 수정해줘.
대상: scripts/install.sh와 README.md
제약: API Key를 터미널이나 로그에 출력하지 마. 기존 Codex 로그인은 보존해.
완료 조건: 설치 스크립트 문법 검사와 단위 테스트를 통과해야 해.
```

DeepSeek를 강제하지 않고 자동 failover를 사용하려면 기존처럼 `codex --yolo`만 실행하면 됩니다. `FORCE_DEEPSEEK=1`은 해당 명령 한 번에만 적용됩니다.

## 업데이트하거나 다시 설치하기

기존 설치를 업데이트할 때도 같은 명령을 반복하면 됩니다. 설치 전 `~/.codex-backup/<timestamp>`에 관련 설정을 백업하고, 기존 Router 설정과 Codex 로그인은 보존합니다.

```bash
cd codex-provider-router
git pull --ff-only
./scripts/install.sh --key-file /path/to/deepseek-api-key.txt
source ~/.zshrc
codex-router doctor
```

Key txt 경로는 `DEEPSEEK_KEY_FILE` 환경 변수로도 지정할 수 있습니다. 환경 변수에는 Key 값이 아니라 파일 경로만 들어갑니다.

```bash
DEEPSEEK_KEY_FILE=/path/to/deepseek-api-key.txt ./scripts/install.sh
```

Key 파일 없이 설치해도 Router는 정상 설치됩니다. 이 경우 `source ~/.zshrc`를 실행한 뒤 `codex-router key set`으로 Key를 숨겨서 두 번 입력하면 됩니다.

> Key txt를 Git에 commit하거나 메신저·메일에 평문으로 올리지 마세요. 장기 보관한다면 저장소 밖의 암호화된 위치를 사용하고 파일 권한을 `600`으로 유지하세요. Installer는 원본 txt를 자동 삭제하지 않습니다.

## Model Router 시작

`codex`는 기존 자동 failover 동작을 그대로 유지합니다. 새 `ai` 명령은 Prompt를 로컬에서 분석해 모델과 reasoning을 추천하고, 승인 후 동일 Codex CLI를 실행합니다.

```bash
ai
ai "README 버전을 1.0.2로 변경해줘"
ai --prompt-file task.md
cat task.md | ai
```

기본 승인 UI는 `Y` 추천 실행, `M` 모델 직접 선택, `R` reasoning 변경, `D` 상세 점수, `S` Provider 상태, `N` 취소입니다. 전역 `~/.codex/config.toml`을 변경하지 않고 실행별 override를 사용합니다.

```bash
ai --dry-run "현재 repository 전체를 리팩토링해줘"
ai --explain "README 수정해줘"
ai status
ai status --refresh
ai doctor
```

일반 요청은 local heuristic만 사용하므로 Router LLM token이 0입니다. `classifier.enabled` 기본값은 `false`입니다. 명시적으로 켜더라도 confidence가 threshold 미만일 때만 DeepSeek V4 Flash/Low를 최대 150 output token으로 사용하며 Sol/Pro는 classifier로 사용하지 않습니다.

모델 정의는 `~/.config/codex-router/models.toml`, routing 정책은 `~/.config/codex-router/config.toml`, Provider cache는 `~/.codex/router/provider-status.json`, 익명화된 사용 기록은 `~/.codex/router/logs/model-router-usage.jsonl`, DeepSeek 비용 원장은 `~/.codex/router/spend.jsonl`에 있습니다. 원본 Prompt 대신 SHA-256 축약 hash만 기록하며 API Key와 Authorization header는 기록하지 않습니다. Codex TUI 종료 출력에서 정형 token usage를 얻은 경우에만 task token/cost를 계산하고, 얻지 못한 값은 추측하지 않고 `null`로 둡니다.

`codex-router balance`와 `ai status --refresh`는 DeepSeek 공식 `/user/balance` endpoint에서 USD 잔액을 읽어 `provider-status.json`에 기록하므로, `codex-router status`와 `ai status`는 network 호출 없이 같은 금액을 보여줍니다. 잔액이 `cost.low_balance_usd`(기본 USD 1.00) 미만이면 DeepSeek 세션이 시작될 때 경고가 표시되며, provider가 중단되지는 않습니다.

`ai status --refresh`는 OpenAI ChatGPT 로그인과 DeepSeek 공식 balance/models endpoint만 확인합니다. OpenAI 구독 quota 확인을 위해 별도 LLM Prompt를 소비하지 않습니다. OpenAI session/weekly limit과 reset 시각은 실제 Codex 응답에서 확인된 정보만 기록하며, 없으면 unavailable로 표시합니다.

- 상태: `codex-router status`
- 비용·한도: `codex-router cost`(또는 `--json`)
- DeepSeek 잔액: `codex-router balance`(또는 `ai balance`), 자동화용 `codex-router balance --json`
- 잔액 경고 기준: `codex-router balance threshold`, `codex-router balance threshold 2.5` (기본 USD 1.00 미만이면 DeepSeek 세션 시작 시 경고)
- DeepSeek 세션 진입 시 배너에 잔액이 표시되고(`... | Balance: USD 2.67`), 세션 종료 후 새로 조회한 잔액을 한 줄 더 표시합니다. Codex TUI의 `/status` 화면 자체에는 넣지 않습니다(그 화면은 Codex binary가 그립니다).
- DeepSeek 기본값: `deepseek-flash + high` (`deepseek-v4-flash`의 현행 공식 매핑)
- DeepSeek 모델: `codex-router model`, `codex-router model flash|pro|vision`
- 최신 모델: 24시간마다 공식 catalog를 자동 확인하며, `codex-router models check|list|refresh`로 수동 관리
- Reasoning: `codex-router reasoning`, `codex-router reasoning low|high|max`
- OpenAI 모델: Codex 세션 내 `/model` (라우터 설정과 독립)
- Key 설정: `codex-router key set` (macOS Keychain에 숨겨서 저장)
- 강제 DeepSeek: `deep codex --yolo`, `FORCE_DEEPSEEK=1 codex --yolo`
- 진단/테스트: `codex-router doctor`, `codex-router test deepseek`
- 로그: `~/.codex/router/logs/`
- 설정: `~/.config/codex-router/config.toml` (API Key 미포함)
- 체크포인트: `~/.codex/router/fallback-state/`

Usage limit이 감지되면 사용자에게 `y/N` 확인을 받은 뒤에만 방금 끝난 OpenAI 세션의 id로 같은 Codex thread를 `resume <session_id>`로 DeepSeek에서 재개합니다(기본값은 전환 안 함). 세션 id를 식별하지 못하면 안내 후 세션 선택창에서 직접 선택합니다. 10/20/30/60분 backoff 후 다음 실행 시 OpenAI를 probe하고, 성공하면 그 요청부터 OpenAI로 복귀합니다.

문제 시 `/opt/homebrew/bin/codex` 로 wrapper를 우회할 수 있습니다. 제거는 `codex-router uninstall`의 안내에 따라 `~/.codex/router/uninstall.sh`를 실행합니다. 제거 시 router 상태는 Trash로 이동되며 Codex 로그인과 기존 `config.toml`은 삭제하지 않습니다.

현재 Codex에는 interactive session 중 provider hot-swap API가 없어 usage-limit 문구를 TTY에서 감지한 후 `y/N` 확인을 받고 같은 저장 thread를 방금 끝난 세션 id로 DeepSeek profile `resume <session_id>`합니다. Codex 내부 connect timeout은 custom provider config로 조절할 수 없습니다.

비용 한도는 기본값이 무제한(0)입니다. 양수로 설정한 경우에만 실제로 실행을 막습니다 — DeepSeek(과금 provider) 세션 전에 오늘·이번 달 누적 비용을 확인하고, `daily_limit_usd`·`monthly_limit_usd`를 넘었으면 세션을 시작하지 않고 종료 코드 `75`로 끝냅니다(OpenAI ChatGPT 로그인은 정액제라 계산에서 제외). 사용량은 Codex rollout(`~/.codex/sessions/`)에서 세션 전후 차이만 읽어 `models.toml` 단가로 환산하고, `resume`으로 이어진 세션도 이번 실행분만 계산합니다. 한 값을 0으로 두면 그 한도를 쓰지 않습니다. 시간 기반 한도는 없습니다 — DeepSeek는 선불 잔액을 소비하는 과금형이라 지출 상한은 잔액이 맡고, fallback 경과 시간은 `cost` 출력에 진단 정보로만 남습니다.

```bash
codex-router cost          # 오늘·이번 달 누적, 한도, fallback 경과, 커버리지
codex-router cost --json   # 자동화용
```

단가를 모르는 모델은 `cost_usd: null`로 남고 한도 계산에서 0으로 봅니다. `codex-router cost`의 "without price" 수가 0이 아니면 누적이 실제보다 작을 수 있으니 그 값을 함께 확인하세요.

DeepSeek 실패 시 Router는 잔액/결제, 429 한도, API Key 인증, 네트워크, 서버 장애를 구분해 터미널에 한국어 원인과 해결 방법을 표시합니다. 원본 API 응답이나 Key는 Router 로그에 복사하지 않습니다.

Router는 `codex` 실행 시 최대 24시간에 한 번 DeepSeek 공식 Codex catalog를 확인합니다. 새 모델이 있으면 검증 후 catalog를 백업·갱신하고 터미널에 알리지만, 비용·호환성·재현성을 위해 fallback 기본 모델은 자동 변경하지 않습니다. 현재 선택 모델이 최신 catalog에서 사라진 경우에는 기존 catalog를 유지하고 경고합니다.

## AI Agent Context

이 프로젝트는 작업별 AI 컨텍스트 관리를 위해 Akela를 사용합니다. Akela는 애플리케이션 Runtime Dependency가 아닙니다.

- Knowledge: `knowledge/`
- Protocol: `akela/PROTOCOL.md`
- Configuration: `akela.json`

Codex와 Claude Code는 Knowledge 전체를 읽는 대신 Task에 맞는 slice를 compile하고, 작업 후 Evidence와 outcome을 기록합니다. 정기적으로 `akela stats`를 확인하고 `akela/CURATE.md` 절차에 따라 사람이 Knowledge 변경을 승인합니다.
