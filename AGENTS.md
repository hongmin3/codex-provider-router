# Project AI instructions

Follow `akela/PROTOCOL.md` for every task. Start from the compiled slice and inspect only the source files directly related to the request. Preserve runtime behavior, secrets, user changes, and deployment settings unless the task explicitly requires changing them.

## 읽기 범위

요청에 직접 관련된 wrapper·provider·state 코드와 테스트만 확인한다.

이 절은 이전에 `CLAUDE.md`에만 있어 Codex가 볼 수 없던 규칙이다. AI 지침의 원본은 이 파일 하나다. 같은 디렉터리의 `CLAUDE.md`는 이 파일을 `@` import 하는 두 줄짜리 파일이며 `scripts/sync-agent-docs.sh`가 관리한다. Claude 전용 지침도 여기에 적는다.
