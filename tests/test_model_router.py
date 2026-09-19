import contextlib
import importlib.util
import io
import json
import pathlib
import tempfile
import unittest
import urllib.error
import sys
from unittest import mock

ROOT = pathlib.Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("model_router", ROOT / "src" / "model_router.py")
mr = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mr
spec.loader.exec_module(mr)
mr.MODELS_CONFIG = ROOT / "config" / "models.toml"



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

class _Response(io.BytesIO):
    """Minimal stand-in for the object urlopen returns."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def response(payload):
    return _Response(json.dumps(payload).encode())


def repo(files=20):
    return mr.RepoContext("/tmp/project", True, files, 0, ("Python",), True, ("pyproject.toml",))


def statuses(openai="AVAILABLE", deepseek="AVAILABLE"):
    return {
        "openai": {"status": openai, "reason": "test", "checked_at": mr.iso(), "source": "test"},
        "deepseek": {"status": deepseek, "reason": "test", "checked_at": mr.iso(), "source": "test"},
    }


class ModelRouterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.models = mr.load_models()

    def route(self, prompt, state=None, files=20):
        return mr.route(prompt, repo(files), state or statuses(), self.models)

    def test_tc01_readme_version_uses_flash_low_without_classifier(self):
        decision = self.route("README 버전 1.0.1 → 1.0.2 변경")
        self.assertEqual((decision.recommended_key, decision.reasoning), ("deepseek_flash", "low"))
        self.assertFalse(mr.should_use_classifier(decision))

    def test_tc02_bulk_csv_to_json_uses_flash_low(self):
        decision = self.route("CSV 100개를 JSON으로 일괄 변환")
        self.assertEqual((decision.recommended_key, decision.reasoning), ("deepseek_flash", "low"))

    def test_tc03_python_file_error_uses_luna_or_pro(self):
        decision = self.route("Python FileNotFoundError 원인을 찾아 수정해줘")
        self.assertIn(decision.recommended_key, {"gpt_luna", "deepseek_pro"})

    def test_tc04_repository_architecture_uses_pro_high(self):
        decision = self.route("repository 전체 architecture를 분석하고 refactoring 해줘", files=800)
        self.assertEqual((decision.recommended_key, decision.reasoning), ("deepseek_pro", "high"))

    def test_tc05_auth_architecture_uses_sol_high(self):
        decision = self.route("인증 architecture를 변경하고 regression test까지 실행해줘")
        self.assertEqual((decision.recommended_key, decision.reasoning), ("gpt_sol", "high"))

    def test_tc06_weekly_limit_filters_gpt(self):
        decision = self.route("repository 전체 구조 개선", statuses("WEEKLY_LIMITED", "AVAILABLE"))
        self.assertTrue(decision.recommended_key.startswith("deepseek_"))

    def test_tc07_session_limit_filters_gpt(self):
        decision = self.route("Python 자동화 구현", statuses("SESSION_LIMITED", "AVAILABLE"))
        self.assertTrue(decision.recommended_key.startswith("deepseek_"))

    def test_tc08_balance_exhausted_filters_deepseek(self):
        decision = self.route("CSV 100개 JSON 변환", statuses("AVAILABLE", "BALANCE_EXHAUSTED"))
        self.assertTrue(decision.recommended_key.startswith("gpt_"))

    def test_tc09_rate_limit_is_distinct_from_balance(self):
        state = statuses("AVAILABLE", "RATE_LIMITED")
        self.assertNotEqual(state["deepseek"]["status"], "BALANCE_EXHAUSTED")
        self.assertFalse(mr.provider_available(state["deepseek"]))

    def test_tc10_no_provider_available(self):
        decision = self.route("README 변경", statuses("WEEKLY_LIMITED", "BALANCE_EXHAUSTED"))
        self.assertIsNone(decision.recommended_key)

    def test_tc11_flash_repeated_failure_escalates_by_cause(self):
        candidate = mr.escalation_candidate("deepseek_flash", {**{k: 2 for k in (
            "complexity", "reasoning", "coding", "context_size", "failure_cost", "agentic_work",
            "repetitiveness", "deterministic_level", "ambiguity", "dependency_depth")},
            "reasoning": 7, "dependency_depth": 8}, list(self.models))
        self.assertEqual(candidate, "deepseek_pro")

    def test_tc12_openai_quota_does_not_retry_luna(self):
        decision = self.route("인증 architecture 변경", statuses("LIMITED", "AVAILABLE"))
        self.assertEqual(decision.recommended_key, "deepseek_pro")

    def test_tc13_deepseek_402_does_not_retry_flash(self):
        decision = self.route("repository dependency 분석", statuses("AVAILABLE", "BALANCE_EXHAUSTED"))
        self.assertTrue(decision.recommended_key.startswith("gpt_"))

    def test_tc14_high_confidence_never_calls_classifier_by_default(self):
        decision = self.route("CSV 100개를 JSON으로 일괄 변환")
        decision.confidence = 0.90
        self.assertFalse(mr.should_use_classifier(decision))

    def test_tc15_low_confidence_can_call_enabled_classifier(self):
        decision = self.route("무언가 개선해줘")
        decision.confidence = 0.50
        cfg = {"classifier": {"enabled": True, "confidence_threshold": 0.70}}
        self.assertTrue(mr.should_use_classifier(decision, cfg))

    def test_tc16_classifier_is_never_sol_or_pro(self):
        self.assertEqual(mr.CLASSIFIER_MODEL_KEY, "deepseek_flash")

    def test_tc17_runtime_arguments_do_not_mutate_global_config(self):
        config_path = pathlib.Path.home() / ".codex" / "config.toml"
        before = config_path.read_bytes() if config_path.exists() else None
        model = self.models["gpt_luna"]
        router_spec = importlib.util.spec_from_file_location("codex_router_for_test", ROOT / "src" / "codex_router.py")
        router = importlib.util.module_from_spec(router_spec)
        router_spec.loader.exec_module(router)
        argv, _ = router.selected_model_args(model, "low", "테스트")
        self.assertIn("--model", argv)
        if before is None:
            self.assertFalse(config_path.exists())
        else:
            self.assertEqual(config_path.read_bytes(), before)

    def test_tc18_korean_multiline_prompt(self):
        decision = self.route("이 프로젝트 전체를 분석해줘.\n여러 파일의 의존성을 확인하고 테스트해줘.")
        self.assertIn(decision.recommended_key, {"deepseek_pro", "gpt_sol"})

    def test_original_prompt_is_preserved_as_single_argument(self):
        prompt = "첫 줄\n둘째 줄: 한글 그대로"
        import sys
        sys.path.insert(0, str(ROOT / "src"))
        import codex_router as router
        argv, _ = router.selected_model_args(self.models["gpt_luna"], "low", prompt)
        self.assertEqual(argv[-1], prompt)

    def test_model_definitions_have_supported_reasoning(self):
        self.assertEqual(set(self.models), {"gpt_luna", "deepseek_flash", "deepseek_pro", "gpt_sol"})
        for model in self.models.values():
            self.assertTrue(model.model_id)
            self.assertTrue(model.supported_reasoning_levels)

    def test_usage_log_never_stores_original_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            old = mr.USAGE_LOG
            mr.USAGE_LOG = pathlib.Path(directory) / "usage.jsonl"
            try:
                prompt = "민감할 수 있는 원본 Prompt"
                decision = self.route("README 버전 변경")
                mr.record_usage(prompt, decision, self.models[decision.recommended_key], "dry-run")
                content = mr.USAGE_LOG.read_text()
                self.assertNotIn(prompt, content)
                self.assertIn("prompt_hash", content)
            finally:
                mr.USAGE_LOG = old

    def test_unavailable_without_reset_does_not_invent_time(self):
        value = statuses("WEEKLY_LIMITED", "BALANCE_EXHAUSTED")
        rendered = mr.status_text(value)
        self.assertIn("Reset: unavailable", rendered)

    def test_manual_model_selection_persists_for_execution_decision(self):
        decision = self.route("일반 Python 코드 작성")
        with mock.patch.object(mr, "_tty_input", side_effect=["m", "3", "y"]):
            selected, effort = mr.choose_interactively(decision, self.models, statuses())
        self.assertEqual(selected.key, "deepseek_pro")
        self.assertIn(effort, selected.supported_reasoning_levels)

    def test_reasoning_override_rejects_unsupported_value(self):
        decision = self.route("README 버전 변경")
        with mock.patch.object(mr, "_tty_input", side_effect=["r", "medium", "n"]):
            selected, effort = mr.choose_interactively(decision, self.models, statuses())
        self.assertIsNone(selected)
        self.assertIsNone(effort)
        self.assertEqual(decision.reasoning, "low")



class DeepSeekBalanceTests(unittest.TestCase):
    payload = {
        "is_available": True,
        "balance_infos": [
            {"currency": "CNY", "total_balance": "72.50", "granted_balance": "2.50", "topped_up_balance": "70.00"},
            {"currency": "USD", "total_balance": "10.25", "granted_balance": "0.25", "topped_up_balance": "10.00"},
        ],
    }

    def test_parse_balance_prefers_the_usd_entry(self):
        parsed = mr.parse_balance(self.payload)
        self.assertEqual(parsed["currency"], "USD")
        self.assertAlmostEqual(parsed["total"], 10.25)
        self.assertAlmostEqual(parsed["granted"], 0.25)
        self.assertAlmostEqual(parsed["topped_up"], 10.00)

    def test_parse_balance_returns_none_when_no_amount_is_readable(self):
        self.assertIsNone(mr.parse_balance({"is_available": True, "balance_infos": []}))
        self.assertIsNone(mr.parse_balance({"balance_infos": [{"currency": "USD"}]}))
        self.assertIsNone(mr.parse_balance("not a payload"))

    def test_low_balance_threshold_defaults_to_one_usd(self):
        with mock.patch.object(mr, "router_config", return_value={}):
            self.assertAlmostEqual(mr.low_balance_threshold(), 1.0)

    def test_low_balance_threshold_reads_the_configured_amount(self):
        with mock.patch.object(mr, "router_config", return_value={"cost": {"low_balance_usd": 5}}):
            self.assertAlmostEqual(mr.low_balance_threshold(), 5.0)

    def test_low_balance_threshold_ignores_an_unusable_configured_value(self):
        with mock.patch.object(mr, "router_config", return_value={"cost": {"low_balance_usd": "매우 적음"}}):
            self.assertAlmostEqual(mr.low_balance_threshold(), 1.0)

    def test_balance_below_the_threshold_is_flagged_low(self):
        result = {"ok": True, "status": "AVAILABLE", "balance": {"currency": "USD", "total": 0.42,
                                                                "granted": 0.0, "topped_up": 0.42, "is_available": True}}
        self.assertTrue(mr.is_low_balance(result, 1.0))

    def test_balance_at_or_above_the_threshold_is_not_flagged_low(self):
        result = {"ok": True, "status": "AVAILABLE", "balance": {"currency": "USD", "total": 1.0,
                                                                "granted": 0.0, "topped_up": 1.0, "is_available": True}}
        self.assertFalse(mr.is_low_balance(result, 1.0))

    def test_a_failed_lookup_is_not_flagged_low(self):
        self.assertFalse(mr.is_low_balance({"ok": False, "status": "SERVER_ERROR", "balance": None}, 1.0))

    def test_balance_text_warns_below_the_threshold(self):
        result = {"ok": True, "status": "AVAILABLE", "reason": "Balance endpoint succeeded", "checked_at": mr.iso(),
                  "balance": {"currency": "USD", "total": 0.42, "granted": 0.0, "topped_up": 0.42, "is_available": True}}
        text = mr.balance_text(result, 1.0)
        self.assertIn("USD 0.42", text)
        self.assertIn("WARNING", text)
        self.assertIn("USD 1.00", text)

    def test_balance_text_is_quiet_above_the_threshold(self):
        result = {"ok": True, "status": "AVAILABLE", "reason": "Balance endpoint succeeded", "checked_at": mr.iso(),
                  "balance": {"currency": "USD", "total": 10.25, "granted": 0.25, "topped_up": 10.00, "is_available": True}}
        text = mr.balance_text(result, 1.0)
        self.assertIn("USD 10.25", text)
        self.assertNotIn("WARNING", text)

    def test_balance_text_reports_a_failed_lookup_without_inventing_an_amount(self):
        result = {"ok": False, "status": "AUTH_ERROR", "reason": "DeepSeek API key is missing",
                  "checked_at": mr.iso(), "balance": None}
        text = mr.balance_text(result, 1.0)
        self.assertIn("AUTH_ERROR", text)
        self.assertIn("DeepSeek API key is missing", text)
        self.assertNotIn("USD 0.00", text)

    def test_fetch_balance_reports_a_missing_key_without_calling_the_api(self):
        with mock.patch.object(mr, "_keychain_key", return_value=None), \
                mock.patch.object(mr.urllib.request, "urlopen") as urlopen:
            result = mr.fetch_deepseek_balance()
        urlopen.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "AUTH_ERROR")
        self.assertIsNone(result["balance"])

    def test_fetch_balance_returns_the_parsed_usd_amounts(self):
        with mock.patch.object(mr, "_keychain_key", return_value="secret"), \
                mock.patch.object(mr.urllib.request, "urlopen", return_value=response(self.payload)):
            result = mr.fetch_deepseek_balance()
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "AVAILABLE")
        self.assertAlmostEqual(result["balance"]["total"], 10.25)
        self.assertTrue(result["checked_at"])

    def test_fetch_balance_never_returns_the_api_key(self):
        with mock.patch.object(mr, "_keychain_key", return_value="sk-secret-value"), \
                mock.patch.object(mr.urllib.request, "urlopen", return_value=response(self.payload)):
            result = mr.fetch_deepseek_balance()
        self.assertNotIn("sk-secret-value", json.dumps(result))

    def test_fetch_balance_maps_http_402_to_balance_exhausted(self):
        error = urllib.error.HTTPError(mr.BALANCE_URL, 402, "Payment Required", {}, None)
        with mock.patch.object(mr, "_keychain_key", return_value="secret"), \
                mock.patch.object(mr.urllib.request, "urlopen", side_effect=error):
            result = mr.fetch_deepseek_balance()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "BALANCE_EXHAUSTED")
        self.assertIsNone(result["balance"])

    def test_fetch_balance_survives_a_network_failure(self):
        with mock.patch.object(mr, "_keychain_key", return_value="secret"), \
                mock.patch.object(mr.urllib.request, "urlopen", side_effect=OSError("no route")):
            result = mr.fetch_deepseek_balance()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "SERVER_ERROR")

    def test_fetch_balance_reports_an_exhausted_account_with_its_amounts(self):
        payload = {"is_available": False, "balance_infos": [
            {"currency": "USD", "total_balance": "0.00", "granted_balance": "0.00", "topped_up_balance": "0.00"}]}
        with mock.patch.object(mr, "_keychain_key", return_value="secret"), \
                mock.patch.object(mr.urllib.request, "urlopen", return_value=response(payload)):
            result = mr.fetch_deepseek_balance()
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "BALANCE_EXHAUSTED")
        self.assertAlmostEqual(result["balance"]["total"], 0.0)

    def refresh_with_balance(self, result):
        baseline = statuses()
        with mock.patch.object(mr.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="ChatGPT", stderr="")), \
                mock.patch.object(mr, "load_provider_status", return_value=baseline), \
                mock.patch.object(mr, "save_provider_status") as save, \
                mock.patch.object(mr, "fetch_deepseek_balance", return_value=result), \
                mock.patch.object(mr, "_keychain_key", return_value="secret"), \
                mock.patch.object(mr.urllib.request, "urlopen", return_value=response({"data": []})), \
                mock.patch.object(mr, "low_balance_threshold", return_value=1.0):
            status = mr.refresh_provider_status()
        save.assert_called_once()
        return status

    def test_refresh_records_the_balance_amounts(self):
        status = self.refresh_with_balance({"ok": True, "status": "AVAILABLE", "reason": "ok", "checked_at": mr.iso(),
                                            "balance": {"currency": "USD", "total": 10.25, "granted": 0.25,
                                                        "topped_up": 10.0, "is_available": True}})
        self.assertEqual(status["deepseek"]["status"], "AVAILABLE")
        self.assertAlmostEqual(status["deepseek"]["balance"]["total"], 10.25)
        self.assertFalse(status["deepseek"]["low_balance"])

    def test_a_low_balance_still_leaves_the_provider_usable(self):
        status = self.refresh_with_balance({"ok": True, "status": "AVAILABLE", "reason": "ok", "checked_at": mr.iso(),
                                            "balance": {"currency": "USD", "total": 0.42, "granted": 0.0,
                                                        "topped_up": 0.42, "is_available": True}})
        self.assertEqual(status["deepseek"]["status"], "AVAILABLE")
        self.assertTrue(status["deepseek"]["low_balance"])
        self.assertTrue(mr.provider_available(status["deepseek"]))

    def test_refresh_keeps_reporting_an_exhausted_balance_as_unavailable(self):
        status = self.refresh_with_balance({"ok": True, "status": "BALANCE_EXHAUSTED", "reason": "insufficient",
                                            "checked_at": mr.iso(),
                                            "balance": {"currency": "USD", "total": 0.0, "granted": 0.0,
                                                        "topped_up": 0.0, "is_available": False}})
        self.assertEqual(status["deepseek"]["status"], "BALANCE_EXHAUSTED")
        self.assertFalse(mr.provider_available(status["deepseek"]))

    def test_status_text_shows_the_balance_and_warns_when_it_is_low(self):
        value = statuses()
        value["deepseek"].update(balance={"currency": "USD", "total": 0.42, "granted": 0.0,
                                          "topped_up": 0.42, "is_available": True}, low_balance=True)
        rendered = mr.status_text(value)
        self.assertIn("USD 0.42", rendered)
        self.assertIn("WARNING", rendered)

    def test_status_text_shows_a_healthy_balance_without_a_warning(self):
        value = statuses()
        value["deepseek"].update(balance={"currency": "USD", "total": 10.25, "granted": 0.25,
                                          "topped_up": 10.0, "is_available": True}, low_balance=False)
        rendered = mr.status_text(value)
        self.assertIn("USD 10.25", rendered)
        self.assertNotIn("WARNING", rendered)


    def record(self, result, cached=None):
        status = cached if cached is not None else statuses()
        with mock.patch.object(mr, "load_provider_status", return_value=status), \
                mock.patch.object(mr, "save_provider_status") as save:
            merged = mr.record_balance(result, 1.0)
        return merged, save

    def test_record_balance_merges_the_amounts_into_the_cached_entry(self):
        result = {"ok": True, "status": "AVAILABLE", "reason": "ok", "checked_at": "2026-09-19T00:00:00+00:00",
                  "balance": {"currency": "USD", "total": 10.25, "granted": 0.25, "topped_up": 10.0,
                              "is_available": True}}
        merged, save = self.record(result)
        save.assert_called_once()
        self.assertEqual(merged["deepseek"]["status"], "AVAILABLE")
        self.assertAlmostEqual(merged["deepseek"]["balance"]["total"], 10.25)
        self.assertEqual(merged["deepseek"]["balance_checked_at"], "2026-09-19T00:00:00+00:00")
        self.assertFalse(merged["deepseek"]["low_balance"])

    def test_record_balance_flags_a_low_amount_without_disabling_the_provider(self):
        result = {"ok": True, "status": "AVAILABLE", "reason": "ok", "checked_at": mr.iso(),
                  "balance": {"currency": "USD", "total": 0.42, "granted": 0.0, "topped_up": 0.42,
                              "is_available": True}}
        merged, _ = self.record(result)
        self.assertTrue(merged["deepseek"]["low_balance"])
        self.assertTrue(mr.provider_available(merged["deepseek"]))

    def test_record_balance_marks_an_exhausted_account_unavailable(self):
        result = {"ok": True, "status": "BALANCE_EXHAUSTED", "reason": "DeepSeek reports insufficient balance",
                  "checked_at": mr.iso(),
                  "balance": {"currency": "USD", "total": 0.0, "granted": 0.0, "topped_up": 0.0,
                              "is_available": False}}
        merged, _ = self.record(result)
        self.assertEqual(merged["deepseek"]["status"], "BALANCE_EXHAUSTED")
        self.assertFalse(mr.provider_available(merged["deepseek"]))

    def test_record_balance_ignores_a_lookup_that_carries_no_amount(self):
        merged, save = self.record({"ok": False, "status": "SERVER_ERROR", "reason": "down",
                                    "checked_at": mr.iso(), "balance": None})
        save.assert_not_called()
        self.assertNotIn("balance", merged["deepseek"])


    def balance_cli(self, argv, result):
        buffer = io.StringIO()
        with mock.patch.object(mr, "fetch_deepseek_balance", return_value=result), \
                mock.patch.object(mr, "record_balance") as record, \
                mock.patch.object(mr, "low_balance_threshold", return_value=1.0), \
                contextlib.redirect_stdout(buffer):
            code = mr.cli(argv, lambda *args, **kwargs: 99)
        return code, buffer.getvalue(), record

    def test_ai_balance_reports_the_amount_instead_of_routing_it_as_a_prompt(self):
        result = {"ok": True, "status": "AVAILABLE", "reason": "ok", "checked_at": mr.iso(),
                  "balance": {"currency": "USD", "total": 10.25, "granted": 0.25, "topped_up": 10.0,
                              "is_available": True}}
        code, output, record = self.balance_cli(["balance"], result)
        self.assertEqual(code, 0)
        self.assertIn("USD 10.25", output)
        record.assert_called_once()

    def test_ai_balance_json_carries_the_low_flag(self):
        result = {"ok": True, "status": "AVAILABLE", "reason": "ok", "checked_at": mr.iso(),
                  "balance": {"currency": "USD", "total": 0.42, "granted": 0.0, "topped_up": 0.42,
                              "is_available": True}}
        code, output, _ = self.balance_cli(["balance", "--json"], result)
        payload = json.loads(output)
        self.assertEqual(code, 0)
        self.assertTrue(payload["low_balance"])
        self.assertAlmostEqual(payload["threshold_usd"], 1.0)

    def test_ai_balance_exits_nonzero_when_the_lookup_fails(self):
        result = {"ok": False, "status": "SERVER_ERROR", "reason": "down", "checked_at": mr.iso(), "balance": None}
        code, output, record = self.balance_cli(["balance"], result)
        self.assertEqual(code, 1)
        self.assertIn("SERVER_ERROR", output)
        record.assert_not_called()


    def test_refresh_stamps_the_balance_check_time_like_a_direct_lookup(self):
        """Without it, the staleness check has no timestamp and every session re-fetches."""
        status = self.refresh_with_balance({"ok": True, "status": "AVAILABLE", "reason": "ok",
                                            "checked_at": "2026-09-19T07:59:22+00:00",
                                            "balance": {"currency": "USD", "total": 2.67, "granted": 0.0,
                                                        "topped_up": 2.67, "is_available": True}})
        self.assertEqual(status["deepseek"]["balance_checked_at"], "2026-09-19T07:59:22+00:00")



if __name__ == "__main__":
    unittest.main()
