# Progress

- 2026-09-13: Codex 0.154.0, ChatGPT login, DeepSeek Responses API 기준 Router 구축·설치.
- 기본 fallback은 `deepseek-flash + high`; `flash`, `pro`, `vision` alias와 reasoning 영구 설정 지원.
- OpenAI/DeepSeek 실 API, state simulation, checkpoint, Keychain, shell/apply_patch/Git/MCP 보존 테스트 완료.
- 제한: 동일 process hot-swap 대신 persisted-thread resume; Codex connect timeout/cost hard-stop은 정식 hook 부재로 미지원.
- 2026-09-13: DeepSeek 실패를 billing, quota, auth, network, server, unknown으로 분류하고 터미널에 한국어 원인·해결 방법을 표시하도록 확장.
- 2026-09-13: 프로젝트 루트에 `USAGE_AND_SPEC.md` 사용법·사양서 추가.
- 2026-09-13: Router child PTY에 실제 터미널 크기를 복사하고 `SIGWINCH`로 resize를 동기화하여 Codex TUI 줄바꿈 깨짐 수정.
- 2026-09-13: DeepSeek 공식 Codex catalog를 24시간마다 자동 확인하고 새 모델 발견 시 백업·검증·갱신·알림하는 기능과 `models list|check|refresh` 명령 추가.
- 2026-09-13: Codex의 `usage limit resets available`·잔여량 경고를 실제 한도 소진으로 오인해 DeepSeek `resume --last`로 전환하던 문제 수정. 명확한 hit/reached/exceeded/429만 감지.
- 2026-09-14: 기존 failover 엔진을 재사용하는 `ai` Model Router 추가. Local heuristic, Provider 후보 필터, 승인 UI, runtime model/reasoning override, dry-run/status/doctor, 익명 usage log, 승인형 provider failover 및 capability escalation을 구현.
- 기본 classifier는 OFF이며 일반 라우팅 추가 token은 0. DeepSeek status refresh는 공식 balance/models endpoint만 사용하고 OpenAI quota 확인용 LLM probe는 실행하지 않음.
- 2026-09-19: DeepSeek 전용 실행 명령 추가. `deep codex --yolo`(= `deep --yolo`)와 `codex-router deepseek --yolo`가 OpenAI 상태와 무관하게 DeepSeek profile로 Codex를 시작하며, 비대화형 `deep exec "..."`도 DeepSeek를 유지한다. Key가 없으면 Codex를 시작하지 않고 exit 78. 설치본 반영, 45개 테스트·`zsh -n`·실 DeepSeek 호출 확인.
- 2026-09-19: 기본 provider는 OpenAI primary + usage limit 시 DeepSeek fallback 그대로 유지. `codex --yolo` 자체를 DeepSeek 기본으로 바꾸는 것은 기존 Knowledge(purpose)와 충돌하므로 소유자 결정 대기.
- 2026-09-19: 소유자 확인 결과 `deep` 접두어 = DeepSeek, 일반 `codex` = GPT로 역할 분리 확정. macOS에도 `~/.local/bin/deep` 심볼릭 링크와 `codex-router deep|deepseek [CODEX_ARGS]`를 추가해 Windows `deep.cmd`와 동작·이름을 맞춤(첫 인자 `codex`는 양쪽 모두 제거). DeepSeek 실행 시 표시되던 `OpenAI cooldown` 문구를 실제 원인(`requested`/`OpenAI usage limit`)으로 수정. TTY 경로에서 `deep codex --help` = DeepSeek, `codex --help` = OpenAI 출력 확인.
