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
