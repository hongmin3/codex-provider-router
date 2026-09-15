# Project overview

## Purpose
<!-- akela: id=purpose scope=all tier=must -->
ChatGPT 로그인 Codex를 기본으로 유지하고 usage limit에서만 DeepSeek로 전환하며, 사용자는 `codex` 명령 하나를 계속 사용합니다.

## Boundaries
<!-- akela: id=boundaries scope=all tier=must -->
Wrapper, provider profile, state, checkpoint, log, Keychain 연결만 소유합니다. Codex binary·ChatGPT 인증·MCP·project trust는 변경하지 않으며 provider hot-swap을 지원한다고 가정하지 않습니다.
