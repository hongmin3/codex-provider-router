# Workflow

## Development workflow
<!-- akela: id=development scope=develop tier=should -->
Python 표준 라이브러리 wrapper와 zsh installer를 사용하고 `python3 -m unittest discover -s tests` 및 `zsh -n scripts/*.sh`로 검증합니다.

## Test workflow
<!-- akela: id=test scope=test tier=must -->
오류 분류·backoff·profile parsing·checkpoint를 로컬로 검증한 후 OpenAI probe와 Key가 있을 때만 DeepSeek 실 API를 테스트합니다.

## Operation workflow
<!-- akela: id=operation scope=operate tier=must -->
설치 전 `~/.codex-backup/<timestamp>`를 만들고 Key는 Keychain에만 저장합니다. Uninstall은 wrapper/profile/Keychain 항목만 제거하고 기존 Codex 인증과 config를 보존합니다.
