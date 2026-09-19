import contextlib
import importlib.util
import datetime as dt
import fcntl
import io
import json
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


LIVE_STATE_FILES = (
    pathlib.Path.home() / ".codex" / "router" / "provider-status.json",
    pathlib.Path.home() / ".codex" / "router" / "state.json",
    pathlib.Path.home() / ".config" / "codex-router" / "config.toml",
)
_live_snapshot: dict = {}


def setUpModule():
    for path in LIVE_STATE_FILES:
        _live_snapshot[path] = path.read_bytes() if path.exists() else None


def tearDownModule():
    """No test may touch the user's live router state; restore it and fail if one did."""
    damaged = []
    for path, before in _live_snapshot.items():
        after = path.read_bytes() if path.exists() else None
        if after == before:
            continue
        damaged.append(str(path))
        if before is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(before)
    if damaged:
        raise AssertionError("tests wrote to live router state (restored): " + ", ".join(damaged))

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
            "5h limit: 12% left (resets 16:39)",
            "Weekly limit: 70% left (resets 15:47 on 25 Sep)",
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

    def test_confirm_answer_accepts_only_yes_variants(self):
        for answer in ("y", "Y", "yes", "YES", " Yes "):
            with self.subTest(answer=answer):
                self.assertTrue(router.confirm_answer(answer))
        for answer in ("", "n", "N", "no", "maybe", "yolo"):
            with self.subTest(answer=answer):
                self.assertFalse(router.confirm_answer(answer))

    def test_confirm_fallback_declines_when_stdin_is_not_a_tty(self):
        with mock.patch.object(router.sys, "stdin") as stdin, \
                mock.patch.object(router, "input") as input_func:
            stdin.isatty.return_value = False
            self.assertFalse(router.confirm_fallback("테스트"))
            input_func.assert_not_called()

    def test_mark_openai_active_resets_state(self):
        with mock.patch.object(router, "save_state") as save, \
                mock.patch.object(router.model_router, "update_provider") as update:
            router.mark_openai_active("test reason")
            self.assertEqual(save.call_args.args[0]["state"], "OPENAI_ACTIVE")
            self.assertEqual(save.call_args.args[0]["probe_attempt"], 0)
            self.assertEqual(update.call_args.args[1], "AVAILABLE")


def balance_result(total, ok=True, status="AVAILABLE", checked="2026-09-19T00:00:00+00:00"):
    payload = {"ok": ok, "status": status, "reason": "test", "checked_at": checked, "balance": None}
    if total is not None:
        payload["balance"] = {"currency": "USD", "total": total, "granted": 0.0,
                              "topped_up": total, "is_available": status != "BALANCE_EXHAUSTED"}
    return payload


class BalanceCommandTests(unittest.TestCase):
    def capture(self, call):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = call()
        return code, buffer.getvalue()

    def test_config_defaults_warn_below_one_dollar(self):
        with mock.patch.object(router, "CONFIG", pathlib.Path("/nonexistent/codex-router/config.toml")):
            self.assertAlmostEqual(router.config()["cost"]["low_balance_usd"], 1.0)

    def test_write_config_round_trips_the_warning_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "config.toml"
            with mock.patch.object(router, "CONFIG", path):
                cfg = router.config()
                cfg["cost"]["low_balance_usd"] = 2.5
                router.write_config(cfg)
                self.assertAlmostEqual(router.config()["cost"]["low_balance_usd"], 2.5)

    def test_balance_command_prints_the_remaining_amount(self):
        with mock.patch.object(router.model_router, "fetch_deepseek_balance", return_value=balance_result(10.25)), \
                mock.patch.object(router.model_router, "record_balance"), \
                mock.patch.object(router, "low_balance_usd", return_value=1.0):
            code, output = self.capture(lambda: router.balance_command([]))
        self.assertEqual(code, 0)
        self.assertIn("USD 10.25", output)
        self.assertNotIn("WARNING", output)

    def test_balance_command_warns_below_the_threshold(self):
        with mock.patch.object(router.model_router, "fetch_deepseek_balance", return_value=balance_result(0.42)), \
                mock.patch.object(router.model_router, "record_balance"), \
                mock.patch.object(router, "low_balance_usd", return_value=1.0):
            code, output = self.capture(lambda: router.balance_command([]))
        self.assertEqual(code, 0)
        self.assertIn("USD 0.42", output)
        self.assertIn("WARNING", output)

    def test_balance_command_json_reports_the_low_flag_and_threshold(self):
        with mock.patch.object(router.model_router, "fetch_deepseek_balance", return_value=balance_result(0.42)), \
                mock.patch.object(router.model_router, "record_balance"), \
                mock.patch.object(router, "low_balance_usd", return_value=1.0):
            code, output = self.capture(lambda: router.balance_command(["--json"]))
        payload = json.loads(output)
        self.assertEqual(code, 0)
        self.assertTrue(payload["low_balance"])
        self.assertAlmostEqual(payload["threshold_usd"], 1.0)
        self.assertAlmostEqual(payload["balance"]["total"], 0.42)

    def test_balance_command_caches_a_successful_lookup(self):
        with mock.patch.object(router.model_router, "fetch_deepseek_balance", return_value=balance_result(10.25)), \
                mock.patch.object(router.model_router, "record_balance") as record, \
                mock.patch.object(router, "low_balance_usd", return_value=1.0):
            self.capture(lambda: router.balance_command([]))
        record.assert_called_once()

    def test_balance_command_exits_nonzero_when_the_lookup_fails(self):
        failed = balance_result(None, ok=False, status="AUTH_ERROR")
        with mock.patch.object(router.model_router, "fetch_deepseek_balance", return_value=failed), \
                mock.patch.object(router.model_router, "record_balance") as record, \
                mock.patch.object(router, "low_balance_usd", return_value=1.0):
            code, output = self.capture(lambda: router.balance_command([]))
        self.assertEqual(code, 1)
        self.assertIn("AUTH_ERROR", output)
        record.assert_not_called()

    def test_balance_threshold_command_shows_the_current_amount(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(router, "CONFIG", pathlib.Path(tmp) / "config.toml"):
                code, output = self.capture(lambda: router.balance_command(["threshold"]))
        self.assertEqual(code, 0)
        self.assertIn("USD 1.00", output)

    def test_balance_threshold_command_persists_a_new_amount(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "config.toml"
            with mock.patch.object(router, "CONFIG", path):
                code, _ = self.capture(lambda: router.balance_command(["threshold", "2.5"]))
                self.assertEqual(code, 0)
                self.assertAlmostEqual(router.low_balance_usd(), 2.5)

    def test_balance_threshold_command_rejects_a_non_numeric_amount(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "config.toml"
            with mock.patch.object(router, "CONFIG", path):
                code, _ = self.capture(lambda: router.balance_command(["threshold", "조금"]))
                self.assertEqual(code, 2)
                self.assertFalse(path.exists())

    def test_main_routes_the_balance_argument_to_the_balance_command(self):
        with mock.patch.object(router, "balance_command", return_value=0) as command, \
                mock.patch.object(router, "ensure_dirs"), \
                mock.patch.object(router.sys, "argv", ["codex-router", "balance", "--json"]):
            self.assertEqual(router.main(), 0)
        command.assert_called_once_with(["--json"])

    def test_low_balance_notice_reports_the_cached_amount(self):
        cached = {"deepseek": {"status": "AVAILABLE", "balance": {"currency": "USD", "total": 0.42,
                                                                 "granted": 0.0, "topped_up": 0.42,
                                                                 "is_available": True},
                               "low_balance": True, "low_balance_threshold": 1.0}}
        with mock.patch.object(router.model_router, "load_provider_status", return_value=cached), \
                mock.patch.object(router.model_router, "fetch_deepseek_balance") as fetch:
            notice = router.low_balance_notice()
        fetch.assert_not_called()
        self.assertIn("USD 0.42", notice)
        self.assertIn("WARNING", notice)

    def test_low_balance_notice_is_silent_for_a_healthy_balance(self):
        cached = {"deepseek": {"status": "AVAILABLE", "balance": {"currency": "USD", "total": 10.25,
                                                                 "granted": 0.25, "topped_up": 10.0,
                                                                 "is_available": True},
                               "low_balance": False, "low_balance_threshold": 1.0}}
        with mock.patch.object(router.model_router, "load_provider_status", return_value=cached):
            self.assertIsNone(router.low_balance_notice())

    def test_low_balance_notice_is_silent_without_a_cached_balance(self):
        with mock.patch.object(router.model_router, "load_provider_status", return_value={"deepseek": {}}):
            self.assertIsNone(router.low_balance_notice())

    def test_a_fresh_cache_is_not_refetched(self):
        cached = {"deepseek": {"balance": {"currency": "USD", "total": 10.25, "granted": 0.0,
                                           "topped_up": 10.25, "is_available": True},
                               "balance_checked_at": router.iso()}}
        with mock.patch.object(router.model_router, "load_provider_status", return_value=cached), \
                mock.patch.object(router.subprocess, "run") as run:
            router.refresh_balance_cache(15)
        run.assert_not_called()

    def test_a_stale_cache_is_refreshed_in_a_child_process(self):
        """The parent must not open a socket: run_pty() forks, and forking a
        multi-threaded process can deadlock (macOS resolver starts threads)."""
        stale = router.iso(router.now() - dt.timedelta(minutes=30))
        cached = {"deepseek": {"balance": {"currency": "USD", "total": 10.25, "granted": 0.0,
                                           "topped_up": 10.25, "is_available": True},
                               "balance_checked_at": stale}}
        with mock.patch.object(router.model_router, "load_provider_status", return_value=cached), \
                mock.patch.object(router.model_router, "fetch_deepseek_balance") as fetch, \
                mock.patch.object(router.subprocess, "run") as run:
            router.refresh_balance_cache(15)
        fetch.assert_not_called()
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], sys.executable)
        self.assertTrue(argv[1].endswith("codex_router.py"))
        self.assertEqual(argv[2:], ["balance", "--json"])
        self.assertTrue(run.call_args.kwargs.get("capture_output"))
        self.assertLessEqual(run.call_args.kwargs.get("timeout", 999), 15)

    def test_a_failed_refresh_never_interrupts_the_session(self):
        with mock.patch.object(router.model_router, "load_provider_status", return_value={"deepseek": {}}), \
                mock.patch.object(router.subprocess, "run",
                                  side_effect=router.subprocess.TimeoutExpired("codex-router", 10)):
            router.refresh_balance_cache(15)

    def test_no_balance_lookup_happens_in_the_process_that_forks_the_pty(self):
        """Regression guard: a pre-fork urllib call made the parent multi-threaded and
        Python warned that forkpty() may deadlock."""
        stale = {"deepseek": {"balance": {"currency": "USD", "total": 0.42, "granted": 0.0,
                                          "topped_up": 0.42, "is_available": True},
                              "low_balance": True, "low_balance_threshold": 1.0,
                              "balance_checked_at": router.iso(router.now() - dt.timedelta(days=1))}}
        calls_before_fork = []
        with mock.patch.object(router.model_router, "fetch_deepseek_balance",
                               return_value=balance_result(0.42)) as fetch, \
                mock.patch.object(router.model_router, "record_balance"), \
                mock.patch.object(router, "ensure_dirs"), \
                mock.patch.object(router, "check_model_catalog", return_value=("skipped", [])), \
                mock.patch.object(router, "select_provider", return_value="deepseek"), \
                mock.patch.object(router, "keychain_key", return_value="secret"), \
                mock.patch.object(router, "load_state", return_value={"state": "OPENAI_COOLDOWN"}), \
                mock.patch.object(router, "save_state"), \
                mock.patch.object(router, "log"), \
                mock.patch.object(router, "confirm_fallback", return_value=True), \
                mock.patch.object(router.subprocess, "run") as child, \
                mock.patch.object(router.model_router, "load_provider_status", return_value=stale), \
                mock.patch.object(router.sys, "stdin", mock.Mock(isatty=lambda: True)), \
                mock.patch.object(router, "run_pty",
                                  side_effect=lambda *a, **k: (calls_before_fork.append(fetch.call_count),
                                                               (0, None, ""))[1]), \
                contextlib.redirect_stderr(io.StringIO()):
            router.run_codex(["--help"])
        self.assertEqual(calls_before_fork, [0], "balance was fetched in the forking process")
        child.assert_called()

    def test_status_shows_the_cached_balance_with_its_warning(self):
        cached = {"deepseek": {"status": "AVAILABLE", "balance": {"currency": "USD", "total": 0.42,
                                                                 "granted": 0.0, "topped_up": 0.42,
                                                                 "is_available": True},
                               "low_balance": True, "low_balance_threshold": 1.0,
                               "balance_checked_at": "2026-09-19T00:00:00+00:00"}}
        with mock.patch.object(router.model_router, "load_provider_status", return_value=cached), \
                mock.patch.object(router, "load_state", return_value={"state": "OPENAI_ACTIVE"}), \
                mock.patch.object(router, "keychain_key", return_value="secret"):
            code, output = self.capture(router.status)
        self.assertEqual(code, 0)
        self.assertIn("USD 0.42", output)
        self.assertIn("WARNING", output)


    low_cache = {"deepseek": {"status": "AVAILABLE", "balance": {"currency": "USD", "total": 0.42,
                                                                "granted": 0.0, "topped_up": 0.42,
                                                                "is_available": True},
                              "low_balance": True, "low_balance_threshold": 1.0,
                              "balance_checked_at": "2026-09-19T00:00:00+00:00"}}

    @contextlib.contextmanager
    def session(self, provider, run_pty_results, cache, fetch=None):
        stderr = io.StringIO()
        if fetch is None:
            fetch = balance_result(None, ok=False, status="SERVER_ERROR")
        with mock.patch.object(router.model_router, "fetch_deepseek_balance", return_value=fetch), \
                mock.patch.object(router.model_router, "record_balance"), \
                mock.patch.object(router.model_router, "save_provider_status"), \
                mock.patch.object(router, "ensure_dirs"), \
                mock.patch.object(router, "check_model_catalog", return_value=("skipped", [])), \
                mock.patch.object(router, "select_provider", return_value=provider), \
                mock.patch.object(router, "keychain_key", return_value="secret"), \
                mock.patch.object(router, "load_state", return_value={"state": "OPENAI_COOLDOWN"}), \
                mock.patch.object(router, "save_state"), \
                mock.patch.object(router, "checkpoint"), \
                mock.patch.object(router, "cooldown"), \
                mock.patch.object(router, "log"), \
                mock.patch.object(router, "confirm_fallback", return_value=True), \
                mock.patch.object(router, "run_pty", side_effect=run_pty_results), \
                mock.patch.object(router, "refresh_balance_cache"), \
                mock.patch.object(router.model_router, "load_provider_status", return_value=cache), \
                mock.patch.object(router.model_router, "record_usage"), \
                mock.patch.object(router.sys, "stdin", mock.Mock(isatty=lambda: True)), \
                mock.patch.dict(os.environ, {}, clear=False), \
                contextlib.redirect_stderr(stderr):
            os.environ.pop("FORCE_DEEPSEEK", None)
            os.environ.pop("CODEX_ROUTER_BYPASS", None)
            yield stderr

    def test_starting_a_deepseek_session_warns_about_a_low_balance(self):
        with self.session("deepseek", [(0, None, "")], self.low_cache) as stderr:
            router.run_codex(["--help"])
        self.assertIn("USD 0.42", stderr.getvalue())

    def test_starting_a_deepseek_session_is_quiet_when_the_balance_is_healthy(self):
        healthy = {"deepseek": {"status": "AVAILABLE", "balance": {"currency": "USD", "total": 10.25,
                                                                  "granted": 0.25, "topped_up": 10.0,
                                                                  "is_available": True},
                                "low_balance": False, "low_balance_threshold": 1.0}}
        with self.session("deepseek", [(0, None, "")], healthy) as stderr:
            router.run_codex(["--help"])
        self.assertNotIn("WARNING", stderr.getvalue())

    def test_falling_back_after_a_usage_limit_warns_about_a_low_balance(self):
        with self.session("openai", [(0, "usage_limit", "limit"), (0, None, "")], self.low_cache) as stderr:
            router.run_codex(["--help"])
        self.assertIn("USD 0.42", stderr.getvalue())


    def test_write_config_keeps_the_shipped_explanatory_comments(self):
        shipped = (pathlib.Path(__file__).parents[1] / "config" / "config.toml").read_text(encoding="utf-8")
        comments = [line for line in shipped.splitlines() if line.startswith("#")]
        self.assertTrue(comments, "config template carries no comments to preserve")
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "config.toml"
            with mock.patch.object(router, "CONFIG", path):
                router.write_config(router.config())
                written = path.read_text(encoding="utf-8")
        for comment in comments:
            self.assertIn(comment, written)


    def test_balance_summary_renders_the_cached_amount(self):
        self.assertEqual(router.balance_summary(self.low_cache["deepseek"]), "USD 0.42")

    def test_balance_summary_is_none_without_a_cached_amount(self):
        self.assertIsNone(router.balance_summary({"status": "AVAILABLE"}))

    def test_the_deepseek_banner_shows_the_remaining_balance(self):
        with self.session("deepseek", [(0, None, "")], self.low_cache) as stderr:
            router.run_codex(["--help"])
        banner = [line for line in stderr.getvalue().splitlines() if "Provider: DeepSeek" in line]
        self.assertEqual(len(banner), 1)
        self.assertIn("Balance: USD 0.42", banner[0])

    def test_the_fallback_banner_shows_the_remaining_balance(self):
        with self.session("openai", [(0, "usage_limit", "limit"), (0, None, "")], self.low_cache) as stderr:
            router.run_codex(["--help"])
        banner = [line for line in stderr.getvalue().splitlines() if "Usage limit detected. Continuing" in line]
        self.assertEqual(len(banner), 1)
        self.assertIn("USD 0.42", banner[0])

    def test_a_finished_deepseek_session_reports_the_updated_balance(self):
        with self.session("deepseek", [(0, None, "")], self.low_cache,
                          fetch=balance_result(1.73)) as stderr:
            router.run_codex(["--help"])
        self.assertIn("USD 1.73", stderr.getvalue())

    def test_a_finished_session_stays_silent_when_the_final_lookup_fails(self):
        with self.session("deepseek", [(0, None, "")], self.low_cache,
                          fetch=balance_result(None, ok=False, status="SERVER_ERROR")) as stderr:
            router.run_codex(["--help"])
        self.assertNotIn("SERVER_ERROR", stderr.getvalue())

    def test_an_openai_only_session_reports_no_deepseek_balance(self):
        healthy = {"deepseek": {"status": "AVAILABLE", "balance": {"currency": "USD", "total": 10.25,
                                                                  "granted": 0.25, "topped_up": 10.0,
                                                                  "is_available": True},
                                "low_balance": False, "low_balance_threshold": 1.0}}
        with self.session("openai", [(0, None, "")], healthy, fetch=balance_result(9.99)) as stderr:
            router.run_codex(["--help"])
        self.assertNotIn("USD", stderr.getvalue())


    def test_a_noninteractive_deep_run_prints_no_balance_line(self):
        """Scripted output stays clean: `deep exec ...` must not gain router chatter."""
        stderr = io.StringIO()
        with mock.patch.object(router, "ensure_dirs"), \
                mock.patch.object(router, "keychain_key", return_value="secret"), \
                mock.patch.object(router.sys, "stdin", mock.Mock(isatty=lambda: False)), \
                mock.patch.object(router.subprocess, "call", return_value=0), \
                mock.patch.object(router.model_router, "fetch_deepseek_balance") as fetch, \
                contextlib.redirect_stderr(stderr):
            self.assertEqual(router.run_codex_deepseek(["exec", "hi"]), 0)
        fetch.assert_not_called()
        self.assertNotIn("USD", stderr.getvalue())


if __name__ == "__main__": unittest.main()
