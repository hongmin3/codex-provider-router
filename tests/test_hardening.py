import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "src"))
import codex_router as router


class HardeningTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = pathlib.Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for name, path in {
            "BASE": self.root, "STATE_FILE": self.root / "state.json",
            "LOG_DIR": self.root / "logs", "CHECKPOINTS": self.root / "checkpoints",
            "CONFIG": self.root / "config.toml",
        }.items():
            self.stack.enter_context(mock.patch.object(router, name, path))
        self.stack.enter_context(mock.patch.dict(os.environ, {}, clear=True))
        router.ensure_dirs()

    def test_invalid_state_recovers_to_openai(self):
        for value in ([], None, {"state": "unknown"},
                      {"state": "OPENAI_COOLDOWN", "next_probe_at": "bad"},
                      {"state": "OPENAI_COOLDOWN", "next_probe_at": "2099-01-01T00:00:00"},
                      {"state": "OPENAI_COOLDOWN", "next_probe_at": "2099-01-01T00:00:00Z", "probe_attempt": "bad"}):
            with self.subTest(value=value):
                router.STATE_FILE.write_text(json.dumps(value))
                self.assertEqual(router.select_provider(), "openai")

    def test_return_to_openai_preserves_catalog_metadata(self):
        router.save_state({"state": "OPENAI_COOLDOWN", "next_probe_at": "2099-01-01T00:00:00Z",
                           "last_catalog_check_at": "2026-09-19T00:00:00Z", "reset_at": "old"})
        with mock.patch.object(router.model_router, "update_provider"):
            router.mark_openai_active("success")
        state = router.load_state()
        self.assertEqual(state.get("last_catalog_check_at"), "2026-09-19T00:00:00Z")
        self.assertNotIn("reset_at", state)

    def test_catalog_rejects_wrong_json_shapes(self):
        for payload in ([], None, {"models": [None]}, {"models": ["bad"]},
                        {"models": [{"slug": ""}]}, {"models": [{"slug": "x"}, {"slug": "x"}]}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                router.parse_official_catalog("<<'CODEX_MODELS_JSON'\n" + json.dumps(payload) + "\nCODEX_MODELS_JSON\n")

    def test_refresh_creates_missing_catalog(self):
        body = b'<<\'CODEX_MODELS_JSON\'\n{"models":[{"slug":"deepseek-flash"}]}\nCODEX_MODELS_JSON\n'
        with mock.patch.object(router.urllib.request, "urlopen", return_value=io.BytesIO(body)):
            result, _ = router.check_model_catalog(force=True, announce=False)
        self.assertNotEqual(result, "failed")
        path = self.root / "deepseek-models.json"
        self.assertTrue(path.exists())
        self.assertEqual(json.loads(path.read_text())["models"][0]["slug"], "deepseek-flash")

    def test_invalid_remote_catalog_preserves_existing_catalog(self):
        path = self.root / "deepseek-models.json"
        previous = '{"models":[{"slug":"deepseek-flash"}]}'
        path.write_text(previous)
        with mock.patch.object(router.urllib.request, "urlopen", return_value=io.BytesIO(
                b"<<'CODEX_MODELS_JSON'\n{\"models\":[null]}\nCODEX_MODELS_JSON\n")):
            result, _ = router.check_model_catalog(force=True, announce=False)
        self.assertEqual(result, "failed")
        self.assertEqual(path.read_text(), previous)

    def test_forced_noninteractive_execution_keeps_deepseek(self):
        executable = self.root / "fake-codex"
        output = self.root / "argv.json"
        executable.write_text(f"#!{sys.executable}\nimport json, sys\nfrom pathlib import Path\nPath({str(output)!r}).write_text(json.dumps(sys.argv[1:]))\n")
        executable.chmod(0o700)
        with mock.patch.object(router, "REAL_CODEX", str(executable)), \
                mock.patch.object(router.sys, "stdin", io.StringIO()), \
                mock.patch.dict(os.environ, {"FORCE_DEEPSEEK": "1", "DEEPSEEK_API_KEY": "dummy"}):
            self.assertEqual(router.run_codex(["exec", "hello"]), 0)
        args = json.loads(output.read_text())
        self.assertEqual(args[:4], ["--profile", "deepseek", "--model", "deepseek-flash"])
        self.assertEqual(args[-2:], ["exec", "hello"])

    def _capturing_codex(self):
        """실제 Codex 자리에 argv를 기록하는 실행 파일을 둔다 (test_forced_... 와 같은 방식)."""
        executable = self.root / "fake-codex"
        output = self.root / "wrapper-argv.json"
        executable.write_text(
            f"#!{sys.executable}\nimport json, sys\nfrom pathlib import Path\n"
            f"Path({str(output)!r}).write_text(json.dumps(sys.argv[1:]))\n")
        executable.chmod(0o700)
        return executable, output

    def test_bypass_marker_passes_through_untouched_even_on_a_tty(self):
        """REQ-ROUTE-001: 자식 Codex에 넘긴 우회 표시가 중첩 wrapper를 막는다.

        이 분기는 TTY 검사보다 먼저 와야 한다 — 그렇지 않으면 대화형 자식 process가
        다시 routing을 타고 wrapper가 자기 자신을 재귀 호출한다.
        """
        executable, output = self._capturing_codex()
        tty = mock.Mock(); tty.isatty.return_value = True
        with mock.patch.object(router, "REAL_CODEX", str(executable)), \
                mock.patch.object(router.sys, "stdin", tty), \
                mock.patch.dict(os.environ, {"CODEX_ROUTER_BYPASS": "1"}):
            self.assertEqual(router.run_codex(["--yolo", "인자 그대로"]), 0)
        self.assertEqual(json.loads(output.read_text()), ["--yolo", "인자 그대로"])

    def test_noninteractive_without_force_passes_through_to_plain_codex(self):
        """REQ-ROUTE-001: 강제 지정이 없는 비대화형 실행은 원본 Codex로 그대로 넘긴다."""
        executable, output = self._capturing_codex()
        with mock.patch.object(router, "REAL_CODEX", str(executable)), \
                mock.patch.object(router.sys, "stdin", io.StringIO()):
            self.assertEqual(router.run_codex(["exec", "hello"]), 0)
        # DeepSeek 경로였다면 --profile/--model 이 앞에 붙는다. 그대로여야 한다.
        self.assertEqual(json.loads(output.read_text()), ["exec", "hello"])

    def test_real_codex_is_an_absolute_path(self):
        """REQ-ROUTE-001: 실제 Codex는 절대 경로로 부른다 — PATH를 다시 타면 wrapper가
        자기 자신을 찾아 재귀한다. 이름만으로 부르지 않는지 상수로 고정한다."""
        self.assertTrue(os.path.isabs(router.REAL_CODEX), router.REAL_CODEX)

    def test_deepseek_args_respects_prompt_separator_and_equals_options(self):
        args = router.deepseek_args(["--profile=other", "--model=other", "--", "--model", "literal prompt"])
        self.assertNotIn("--profile=other", args)
        self.assertNotIn("--model=other", args)
        self.assertEqual(args[-3:], ["--", "--model", "literal prompt"])

    def test_positive_remaining_percent_does_not_interrupt_session(self):
        for percent in (10, 20, 30, 50, 60, 70, 80, 90, 100, 1.0):
            with self.subTest(percent=percent):
                self.assertIsNone(router.USAGE_RE.search(f"Weekly limit: {percent}% left"))
        self.assertIsNotNone(router.USAGE_RE.search("Weekly limit: 0% left"))


if __name__ == "__main__":
    unittest.main()
