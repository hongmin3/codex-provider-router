# Project AI instructions

Follow `akela/PROTOCOL.md` for every task. Start from the compiled slice and inspect only the source files directly related to the request. Preserve runtime behavior, secrets, user changes, and deployment settings unless the task explicitly requires changing them.

## task-observer

첫 tool 호출 전에 — 그리고 계획을 쓰거나 제안하기 전에 — `task-observer` skill을 호출하고 Session Start Protocol(저장소 확인, frontmatter scan, review trigger)까지 실행한다. skill을 load하는 것과 protocol을 실행하는 것은 별개의 단계이며, 파일만 읽고 멈춘 세션은 아무것도 활성화하지 않은 것이다.

워크스페이스는 아래 절대 경로 하나만 쓴다. 현재 작업 디렉터리나 CLI별 기본값에서 유도하지 않는다. CLI마다 다른 경로를 만들면 같은 skill에 대한 관찰 기록이 갈라지고, 빈 로그는 깨끗한 로그와 구분되지 않아 어느 쪽에서도 드러나지 않는다.

```text
/Users/hongmin/.claude/task-observer
  skill-observations/observation-log/            관찰 로그
  skill-observations/cross-cutting-principles.md 공통 원칙
  skill-updates/                                 staging (PENDING.md)
```

`~/.codex/task-observer-workspace/`는 2026-09-19에 잘못 생성된 빈 워크스페이스다. 관찰 기록이 없으므로 사용하지 않는다.

skill을 load할 때마다 그 skill을 지목한 OPEN 관찰의 본문을 읽고 현재 작업에 적용한다. skill 파일이 아직 갱신되지 않았어도 마찬가지다. session 시작의 frontmatter scan은 이 확인을 대신하지 못한다.

```bash
grep -l "skill:.*<skill-name>" /Users/hongmin/.claude/task-observer/skill-observations/observation-log/*.md
```

작업을 마치면 이번 세션에 기록한 관찰을 한 줄로 보고한다(id와 제목, 또는 "기록 없음"과 그 이유).

## 읽기 범위

요청에 직접 관련된 wrapper·provider·state 코드와 테스트만 확인한다.

이 절은 이전에 `CLAUDE.md`에만 있어 Codex가 볼 수 없던 규칙이다. AI 지침의 원본은 이 파일 하나다. 같은 디렉터리의 `CLAUDE.md`는 이 파일을 `@` import 하는 두 줄짜리 파일이다. runtime이 import를 펼쳐 주므로 본문을 복제하거나 동기화할 필요가 없다. Claude 전용 지침도 여기에 적는다.
