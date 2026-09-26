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

- REQ-ROUTE-001: wrapper 계약 회귀 테스트(TEST-ROUTE-005). 우회 표시가 있으면 stdin이
  TTY여도 routing 없이 인자를 그대로 넘기고, 강제 지정 없는 비대화형 실행은 원본 Codex로
  그대로 넘어가며, 실제 Codex는 절대 경로로 부른다. 이전에는 추적성 표에서 Test가
  `(없음)`이었다.

- REQ-COST-001: 비용·시간 한도 실제 강제. DeepSeek 세션 전에 `daily_limit_usd`,
  `monthly_limit_usd`, `max_fallback_minutes`를 확인하고 한도를 넘으면 실행하지 않고 종료
  코드 75로 끝낸다.
- REQ-COST-001: 비용 원장 `~/.codex/router/spend.jsonl`과 `codex-router cost [--json]` 명령.
  Codex rollout에서 세션 전후 token 차이만 읽어 `models.toml` 단가로 환산한다(`resume` 대응).
- TEST-COST-002 (`tests/test_router.py` `CostLimitTests`) 8개 테스트.

### Changed

- REQ-COST-001: 2절 범위에서 비용 한도 기능을 제외한다고 적힌 오래된 문장을 고쳤다. 이 기능은 2026-09-20부터 구현·테스트된 현재 범위다.
- SPEC 문장을 처음 보는 사람도 읽을 수 있게 다시 썼다. 긴 문단은 나누고, 차례는 번호 목록, 예외는 `> **예외**` 상자, 이유는 `이유:` 줄로 옮겼다. 1절에 용어 표(`이 문서에서 쓰는 말`)를 더했다. 사양 내용(규칙·숫자·날짜·코드 이름·요구사항 ID·추적성 표)은 바꾸지 않았다(공통 SPEC workflow v5·렌더러 v5).
- 문서: 공통 SPEC workflow v4(AGENTS.md) — SPEC·CHANGELOG 작성 규칙(제목 이름, 기능 그룹 표, CHANGELOG ID, 예시 블록 금지, flow 흐름도, 로컬 이미지, HTML 재생성)을 한 절로 모았다. `docs/SPEC.html` 렌더러 v4: 왼쪽 목차에 지금 읽는 절·요구사항 표시.
- 준비 검사: CHANGELOG 형식 예시 블록에 실제 항목이 들어가면 `CHANGELOG_EXAMPLE_MODIFIED`로 경고한다(키트 관리 사본 `.project-check/project-readiness.js`, 기준 `.project-check/changelog-template.md`).
- 문서: `docs/SPEC.html`을 렌더러 v3로 다시 만들었다 — 이력이 없는 요구사항에 "기록된 변경 없음" 표시.
- 문서: `SPEC.md`의 REQ·NFR 제목 줄 17개에 기능 이름을 붙이고 5절에 기능 그룹 표를 추가했다.
  사양 내용은 바꾸지 않았다. 사람이 읽는 `docs/SPEC.html`(기능 목록·요구사항 카드·요구사항별
  변경 이력)과 렌더러 `.project-check/render-spec-html.js`를 추가하고 공통 SPEC workflow를 v3로
  갱신했다 — `SPEC.md`나 `CHANGELOG.md`를 고치면 HTML을 다시 만든다.
- REQ-COST-001: 비용 한도 기본값을 0(무제한)으로 변경. DeepSeek 잔액 자체가 실제 지출
  상한이라는 소유자 결정에 따라 `daily_limit_usd`·`monthly_limit_usd`는 소유자가 양수로
  명시한 경우에만 실행을 막는다. `config/config.toml`·코드 기본값·설치본 설정 모두 0으로
  반영.
- REQ-COST-001: 시간 기반 한도(`[routing] max_fallback_minutes`) 제거. DeepSeek는 선불
  잔액을 소비하는 과금형 provider라 지출 상한은 잔액과 `daily_limit_usd`·
  `monthly_limit_usd`가 이미 맡고, 시간 한도는 정당한 작업까지 막는 중복이라는 소유자
  결정에 따른다. fallback 경과 시간은 `codex-router cost` 출력에 진단 정보로만 남고
  실행을 막지 않는다. 관련 테스트는 `test_a_long_fallback_does_not_block_a_deepseek_run`
  으로 대체.
- 한도를 0 이하로 두면 그 한도를 쓰지 않는다. OpenAI(ChatGPT 로그인)는 정액제라 비용 한도
  계산에서 제외하고, 그 사실을 `cost` 출력에 표시한다.
- SPEC.md: Project Version 1.0.0, Owner 역할명 지정. `daily_limit_usd`류가 "참고값"이라는
  미확정 항목을 닫고 REQ-COST-001로 확정.
- 2026-09-25: 공통 키트 이름이 Botyard로 바뀌어 `.project-check/`의 SPEC HTML 렌더러와 `docs/SPEC.html`의 생성기 표시를 갱신했다(형식·내용 변경 없음). 준비 검사와 자체 테스트 통과.
- 2026-09-26: 공통 키트 자동 반영용 검증 명령 파일 `botyard.json` 추가(키트 관리 파일만 바뀌면 이 명령과 준비 검사를 통과한 뒤 자동으로 커밋·push된다).
- 2026-09-26: 공통 키트(Botyard 59e195f) 갱신 반영 — .project-check/agents-spec-section.md, .project-check/manifest.json, .project-check/project-readiness.js, .project-check/render-spec-html.js, AGENTS.md, docs/SPEC.html. 준비 검사와 자체 테스트 통과.

### Fixed

- TEST-COST-002·잔액 회귀 테스트 환경 격리. Codex 에이전트 실행 환경이 물려주는
  `CODEX_ROUTER_BYPASS=1` 때문에 `run_codex`를 직접 부르는 두 테스트가 bypass 경로로
  빠져 실패하던 문제를, `test_router.py`의 `setUpModule`/`tearDownModule`이
  `CODEX_ROUTER_BYPASS`·`FORCE_DEEPSEEK`를 suite 동안 제거하고 끝나면 복원하도록 고침.
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
