import importlib.util
import fcntl
import os
import pathlib
import tempfile
import struct
import termios
import unittest
import sys
from unittest import mock

SOURCE = pathlib.Path(__file__).parents[1] / "src" / "codex_router.py"
sys.path.insert(0, str(SOURCE.parent))
spec = importlib.util.spec_from_file_location("router", SOURCE)
router = importlib.util.module_from_spec(spec)
spec.loader.exec_module(router)

class RouterTests(unittest.TestCase):
    def test_error_classes_do_not_overlap(self):
        self.assertTrue(router.AUTH_RE.search("401 unauthorized"))
        self.assertTrue(router.NETWORK_RE.search("connection reset"))
        self.assertFalse(router.USAGE_RE.search("connection reset"))
        self.assertFalse(router.USAGE_RE.search("401 unauthorized"))

    def test_usage_exhaustion_messages_trigger_fallback(self):
        errors = (
            "You've hit your usage limit.",
            "You’ve hit your usage limit for this week.",
            "You have hit your 5-hour limit.",
            "You've reached your usage limit.",
            "Usage limit reached.",
            "Session limit exceeded.",
            "Rate limit exceeded.",
            "Quota exceeded. Check your plan and billing details.",
            "Too many requests",
            "No usage remaining",
            "No requests left",
            "You're out of credits.",
            "Your workspace is out of credits.",
            "You've reached your workspace credit limit",
            "usage not included",
            "0% left",
            "HTTP 429",
            "status 429",
            "status code: 429",
        )
        for error in errors:
            with self.subTest(error=error):
                self.assertIsNotNone(router.USAGE_RE.search(error))

    def test_usage_notices_do_not_trigger_fallback(self):
        notices = (
            "You have 2 usage limit resets available. Run /usage to use one.",
            "Usage limit resets",
            "No usage limit resets are available.",
            "Your usage does not need a reset right now.",
            "Heads up, you have less than 25% of your 5h limit left.",
            "5-hour limit: 26% left; resets in 57m",
            "Weekly limit: 79% left; resets in 6d 1h",
            "Approaching rate limits",
            "Uses your plan's rate limits and training data preferences",
            "Consumes usage limits faster",
            "Remaining usage on the weekly usage limit",
            "Visit for up-to-date information on rate limits and credits",
            "failed to fetch codex rate limits",
            "Goal hit usage limits (/goal resume)",
            "rate limiter has requested a token",
        )
        for notice in notices:
            with self.subTest(notice=notice):
                self.assertIsNone(router.USAGE_RE.search(notice))

    def test_deepseek_args_removes_user_provider_overrides(self):
        args = router.deepseek_args(["--yolo", "-m", "x", "-p", "x"])
        self.assertEqual(args[:4], ["--profile", "deepseek", "--model", "deepseek-flash"])
        self.assertIn("--yolo", args)

    def test_deep_shortcut_accepts_an_optional_leading_codex_word(self):
        self.assertEqual(router.remove_leading_codex_token(["codex", "--yolo", "작업"]),
                         ["--yolo", "작업"])
        self.assertEqual(router.remove_leading_codex_token(["--yolo"]), ["--yolo"])
        self.assertEqual(router.remove_leading_codex_token([]), [])

    def test_deepseek_shortcut_without_key_exits_before_starting_codex(self):
        with mock.patch.object(router, "keychain_key", return_value=None), \
                mock.patch.object(router, "subprocess") as proc:
            self.assertEqual(router.run_codex_deepseek(["--yolo"]), 78)
            proc.call.assert_not_called()

    def test_staged_backoff(self):
        self.assertEqual(router.config()["routing"]["probe_minutes"], [10, 20, 30, 60])

    def test_deepseek_error_classification(self):
        cases = {
            "402 payment required; insufficient balance": "billing",
            "HTTP 429 too many requests": "quota",
            "401 unauthorized": "auth",
            "connection reset by peer": "network",
            "unexpected provider response": "unknown",
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertEqual(router.classify_deepseek_error(message), expected)

    def test_provider_failure_mapping_keeps_402_and_429_distinct(self):
        with mock.patch.object(router.model_router, "update_provider") as update:
            self.assertEqual(router._mark_runtime_failure("deepseek", None, "HTTP 402 insufficient balance"),
                             "PROVIDER_AVAILABILITY_FAILURE")
            self.assertEqual(update.call_args.args[1], "BALANCE_EXHAUSTED")
        with mock.patch.object(router.model_router, "update_provider") as update:
            self.assertEqual(router._mark_runtime_failure("deepseek", None, "HTTP 429 rate limit"),
                             "TRANSIENT_INFRA_FAILURE")
            self.assertEqual(update.call_args.args[1], "RATE_LIMITED")

    def test_openai_window_classification(self):
        self.assertEqual(router.openai_limit_type("weekly limit reached"), "WEEKLY_LIMITED")
        self.assertEqual(router.openai_limit_type("5-hour limit reached"), "SESSION_LIMITED")
        self.assertEqual(router.openai_limit_type("usage limit reached"), "LIMITED")

    def test_reset_time_is_only_accepted_when_provider_supplies_absolute_iso(self):
        self.assertEqual(router.explicit_reset_at("Weekly limit reached; resets at 2026-09-18T09:00:00+09:00"),
                         "2026-09-18T09:00:00+09:00")
        self.assertIsNone(router.explicit_reset_at("Weekly limit resets in 2d 4h"))

    def test_token_usage_parser(self):
        self.assertEqual(router.parse_task_tokens("Token usage: total=4,545 input=4,536 (+ 9,984 cached) output=9"),
                         (4536, 9))
        self.assertEqual(router.parse_task_tokens("no structured usage"), (None, None))

    def test_sync_window_size(self):
        source_master, source_slave = os.openpty()
        target_master, target_slave = os.openpty()
        try:
            expected = struct.pack("HHHH", 42, 132, 0, 0)
            fcntl.ioctl(source_slave, termios.TIOCSWINSZ, expected)
            self.assertTrue(router.sync_window_size(source_slave, target_master))
            actual = fcntl.ioctl(target_slave, termios.TIOCGWINSZ, struct.pack("HHHH", 0, 0, 0, 0))
            self.assertEqual(struct.unpack("HHHH", actual)[:2], (42, 132))
        finally:
            for fd in (source_master, source_slave, target_master, target_slave):
                os.close(fd)

    def test_parse_official_catalog(self):
        script = """write_models_json() {\n  cat > \"$1\" <<'CODEX_MODELS_JSON'\n{\"models\":[{\"slug\":\"deepseek-next\"}]}\nCODEX_MODELS_JSON\n}\n"""
        catalog = router.parse_official_catalog(script)
        self.assertEqual(catalog["models"][0]["slug"], "deepseek-next")

    def test_parse_official_catalog_rejects_invalid_input(self):
        with self.assertRaises(ValueError):
            router.parse_official_catalog("no catalog here")

if __name__ == "__main__": unittest.main()
