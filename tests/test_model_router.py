import importlib.util
import pathlib
import tempfile
import unittest
import sys
from unittest import mock

ROOT = pathlib.Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("model_router", ROOT / "src" / "model_router.py")
mr = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mr
spec.loader.exec_module(mr)
mr.MODELS_CONFIG = ROOT / "config" / "models.toml"


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


if __name__ == "__main__":
    unittest.main()
