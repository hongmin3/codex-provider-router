# Changelog

이 파일은 **무엇이 바뀌었는가**만 담는다. 현재 사양은 `SPEC.md`, 현재 작업 진행 상태는
`progress.md`에 있다. 같은 내용을 두 곳에 쓰지 않는다.

가능하면 각 항목 앞에 Requirement ID를 붙인다.

```text
### Changed
- REQ-EXPORT-002: HTML 파일명에 실행 시간을 포함하도록 변경

### Fixed
- REQ-QUERY-001: 인증 만료 시 잘못된 성공 상태를 반환하던 문제 수정
```

Semantic Versioning은 강제하지 않는다. 프로젝트에 Versioning 정책이 있으면 그것을 따른다.

## [Unreleased]

### Added

- REQ-COST-001: 비용·시간 한도 실제 강제. DeepSeek 세션 전에 `daily_limit_usd`,
  `monthly_limit_usd`, `max_fallback_minutes`를 확인하고 한도를 넘으면 실행하지 않고 종료
  코드 75로 끝낸다.
- REQ-COST-001: 비용 원장 `~/.codex/router/spend.jsonl`과 `codex-router cost [--json]` 명령.
  Codex rollout에서 세션 전후 token 차이만 읽어 `models.toml` 단가로 환산한다(`resume` 대응).
- TEST-COST-002 (`tests/test_router.py` `CostLimitTests`) 8개 테스트.

### Changed

- 한도를 0 이하로 두면 그 한도를 쓰지 않는다. OpenAI(ChatGPT 로그인)는 정액제라 비용 한도
  계산에서 제외하고, 그 사실을 `cost` 출력에 표시한다.
- SPEC.md: Project Version 1.0.0, Owner 역할명 지정. `daily_limit_usd`류가 "참고값"이라는
  미확정 항목을 닫고 REQ-COST-001로 확정.

### Fixed

- NFR-COMPAT-001: Windows에서 저장된 원본 Codex 경로가 사라지거나 Router 자신의 shim을
  가리키면 PATH의 다음 Codex 실행기를 찾아 경로를 복구한다. 유효 경로 보존, 자기 재귀 방지,
  대체 실행기 없음 오류를 PowerShell 회귀 테스트로 검증한다.
- CI(macOS)가 깨끗한 홈에서 실패하던 문제 수정. `test_router.py`의 세 테스트가
  실제 `~/.codex/router/logs/`에 기록을 시도했다. 세 테스트에 `log` mock을 추가하고,
  missing-key 테스트에는 `ensure_dirs` mock도 추가했다. live state guard가 로그 디렉터리까지
  보도록 넓혀 같은 유출이 다시 생기면 suite가 실패하게 했다.
- REQ-CTX-001: usage limit에서 `y`로 전환할 때 `resume --last`가 방금 종료된 OpenAI
  세션 대신 다른 thread를 열던 문제 수정. 전환 시 방금 끝난 세션의 id를 rollout 메타에서
  찾아 `resume <session_id>`로 이어가고, id를 찾지 못하면 안내와 함께 picker를 연다.
  `--last` 사용 제거. TEST-CTX-001 회귀 테스트 6개.

### Removed

## [1.0.0] - 2026-09-19

### Added

- SPEC.md 도입 — 기존 동작을 Requirement로 문서화 (REQ-*/NFR-*)
