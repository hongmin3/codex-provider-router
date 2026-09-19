# Workflow

## Development workflow
<!-- akela: id=development scope=develop tier=should -->
Python 표준 라이브러리 wrapper에 macOS(zsh)·Windows(PowerShell·cmd) installer를 둡니다. 검증은 `python3 -m unittest discover -s tests`, `zsh -n scripts/*.sh`, 그리고 CI windows job의 `scripts/*.ps1` parse입니다.

## Test workflow
<!-- akela: id=test scope=test tier=should -->
오류 분류·backoff·profile parsing·checkpoint를 로컬로 검증한 후 OpenAI probe와 Key가 있을 때만 DeepSeek 실 API를 테스트합니다.

## Operation workflow
<!-- akela: id=operation scope=operate tier=should -->
설치 전 `~/.codex-backup/<timestamp>`(Windows는 `%USERPROFILE%\.codex-backup\<timestamp>`)를 만들고 Key는 macOS Keychain 또는 Windows DPAPI로 암호화한 `deepseek-api-key.dpapi`에만 저장합니다. Uninstall은 wrapper·profile과 macOS Keychain 항목을 제거하고 기존 Codex 인증과 config를 보존합니다. Windows는 실행 중인 InstallDir을 지울 수 없어 DPAPI Key 파일이 남으며, uninstall이 남은 파일 경로만 안내합니다.
