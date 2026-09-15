#!/usr/bin/env python3
"""Local-first model recommendation layer for the existing Codex provider router."""
from __future__ import annotations

import datetime as dt
import getpass
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from typing import Callable

HOME = pathlib.Path.home()
CONFIG = HOME / ".config" / "codex-router" / "config.toml"
MODELS_CONFIG = HOME / ".config" / "codex-router" / "models.toml"
STATE_DIR = HOME / ".codex" / "router"
FAILOVER_STATE = STATE_DIR / "state.json"
PROVIDER_STATUS = STATE_DIR / "provider-status.json"
USAGE_LOG = STATE_DIR / "logs" / "model-router-usage.jsonl"
FAILURE_STATE = STATE_DIR / "model-failures.json"
REAL_CODEX = "/opt/homebrew/bin/codex"
KEYCHAIN_SERVICE = "codex-router-deepseek"
KEYCHAIN_ACCOUNT = os.environ.get("USER", "codex")
CLASSIFIER_MODEL_KEY = "deepseek_flash"

UNAVAILABLE = {
    "LIMITED", "SESSION_LIMITED", "WEEKLY_LIMITED", "BALANCE_EXHAUSTED",
    "RATE_LIMITED", "AUTH_ERROR", "SERVER_ERROR", "UNKNOWN_ERROR", "DISABLED",
}


@dataclass(frozen=True)
class Model:
    key: str
    display_name: str
    model_id: str
    provider: str
    supported_reasoning_levels: tuple[str, ...]
    context_window: int | None
    input_price: float | None
    cached_input_price: float | None
    output_price: float | None
    enabled: bool
    priority: int
    capabilities: tuple[str, ...]


@dataclass(frozen=True)
class RepoContext:
    path: str
    is_git: bool
    file_count: int
    modified_count: int
    languages: tuple[str, ...]
    has_tests: bool
    build_systems: tuple[str, ...]


@dataclass
class Decision:
    scores: dict[str, int]
    task_type: str
    recommended_key: str | None
    ideal_key: str | None
    alternative_key: str | None
    cheapest_key: str | None
    reasoning: str | None
    confidence: float
    reasons: list[str]
    routing_method: str = "local"
    classifier_used: bool = False
    routing_input_tokens: int = 0
    routing_output_tokens: int = 0
    routing_cost: float = 0.0


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(value: dt.datetime | None = None) -> str:
    return (value or utc_now()).isoformat(timespec="seconds")


def read_toml(path: pathlib.Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def router_config() -> dict:
    cfg = read_toml(CONFIG)
    cfg.setdefault("model_router", {})
    cfg["model_router"].setdefault("recommendation", True)
    cfg["model_router"].setdefault("auto_model_switch", False)
    cfg["model_router"].setdefault("auto_provider_failover", False)
    cfg["model_router"].setdefault("auto_escalation", False)
    cfg.setdefault("classifier", {})
    cfg["classifier"].setdefault("enabled", False)
    cfg["classifier"].setdefault("confidence_threshold", 0.70)
    cfg["classifier"].setdefault("max_output_tokens", 150)
    cfg.setdefault("provider_cache", {})
    cfg["provider_cache"].setdefault("ttl_minutes", 15)
    cfg["provider_cache"].setdefault("rate_limit_minutes", 5)
    return cfg


def load_models() -> dict[str, Model]:
    raw = read_toml(MODELS_CONFIG).get("models", {})
    models: dict[str, Model] = {}
    for key, item in raw.items():
        models[key] = Model(
            key=key, display_name=str(item["display_name"]), model_id=str(item["model_id"]),
            provider=str(item["provider"]),
            supported_reasoning_levels=tuple(item.get("supported_reasoning_levels", [])),
            context_window=item.get("context_window"), input_price=item.get("input_price"),
            cached_input_price=item.get("cached_input_price"), output_price=item.get("output_price"),
            enabled=bool(item.get("enabled", True)), priority=int(item.get("priority", 100)),
            capabilities=tuple(item.get("capabilities", [])),
        )
    return models


def _read_json(path: pathlib.Path, default: dict) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else default
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: pathlib.Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def _keychain_key() -> str | None:
    if os.environ.get("DEEPSEEK_API_KEY"):
        return os.environ["DEEPSEEK_API_KEY"]
    result = subprocess.run(
        ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"],
        text=True, capture_output=True,
    )
    return result.stdout.rstrip("\n") if result.returncode == 0 else None


def default_provider_status() -> dict:
    failover = _read_json(FAILOVER_STATE, {})
    openai_state = failover.get("state", "OPENAI_ACTIVE")
    if openai_state == "OPENAI_ACTIVE":
        openai = {"status": "AVAILABLE", "reason": "No cached OpenAI limit", "source": "failover-cache"}
    else:
        openai = {
            "status": failover.get("limit_type", "LIMITED"),
            "reason": failover.get("reason", "OpenAI usage limit"),
            "source": "failover-cache",
        }
        if failover.get("reset_at"):
            openai["reset_at"] = failover["reset_at"]
        if failover.get("next_probe_at"):
            openai["next_check_at"] = failover["next_probe_at"]
    return {
        "openai": {**openai, "checked_at": failover.get("updated_at", iso())},
        "deepseek": {"status": "AVAILABLE" if _keychain_key() else "AUTH_ERROR",
                     "reason": "Keychain credential present" if _keychain_key() else "API key missing",
                     "source": "local-keychain", "checked_at": iso()},
    }


def load_provider_status() -> dict:
    current = _read_json(PROVIDER_STATUS, default_provider_status())
    baseline = default_provider_status()
    for provider in ("openai", "deepseek"):
        current.setdefault(provider, baseline[provider])
        reset = current[provider].get("reset_at") or current[provider].get("retry_at") or current[provider].get("next_check_at")
        if reset:
            try:
                if dt.datetime.fromisoformat(reset) <= utc_now() and current[provider].get("status") in UNAVAILABLE:
                    current[provider]["status"] = "CHECK_REQUIRED"
                    current[provider]["reason"] = "Cached recovery time passed; verification required"
            except (TypeError, ValueError):
                pass
    # Existing failover state is authoritative for an active OpenAI cooldown.
    if baseline["openai"]["status"] != "AVAILABLE":
        current["openai"] = baseline["openai"]
    return current


def save_provider_status(status: dict) -> None:
    _write_json(PROVIDER_STATUS, status)


def update_provider(provider: str, status: str, reason: str, *, reset_at: str | None = None,
                    retry_at: str | None = None, next_check_at: str | None = None,
                    source: str = "runtime") -> None:
    current = load_provider_status()
    value = {"status": status, "reason": reason, "checked_at": iso(), "source": source}
    if reset_at:
        value["reset_at"] = reset_at
    if retry_at:
        value["retry_at"] = retry_at
    if next_check_at:
        value["next_check_at"] = next_check_at
    current[provider] = value
    save_provider_status(current)


def refresh_provider_status() -> dict:
    status = load_provider_status()
    login = subprocess.run([REAL_CODEX, "login", "status"], text=True, capture_output=True, timeout=15)
    if login.returncode != 0:
        status["openai"] = {"status": "AUTH_ERROR", "reason": "ChatGPT login is not valid",
                            "checked_at": iso(), "source": "codex-login-status"}
    elif status["openai"].get("status") not in {"LIMITED", "SESSION_LIMITED", "WEEKLY_LIMITED"}:
        status["openai"] = {"status": "AVAILABLE", "reason": "ChatGPT login valid; no cached limit",
                            "checked_at": iso(), "source": "codex-login-status"}
    key = _keychain_key()
    if not key:
        status["deepseek"] = {"status": "AUTH_ERROR", "reason": "DeepSeek API key is missing",
                              "checked_at": iso(), "source": "local-keychain"}
    else:
        headers = {"Authorization": f"Bearer {key}", "User-Agent": "codex-model-router/1"}
        request = urllib.request.Request("https://api.deepseek.com/user/balance", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                balance = json.load(response)
            if not bool(balance.get("is_available")):
                status["deepseek"] = {"status": "BALANCE_EXHAUSTED", "reason": "DeepSeek reports insufficient balance",
                                      "checked_at": iso(), "source": "deepseek-balance-endpoint"}
            else:
                models_request = urllib.request.Request("https://api.deepseek.com/models", headers=headers)
                with urllib.request.urlopen(models_request, timeout=10) as response:
                    json.load(response)
                status["deepseek"] = {"status": "AVAILABLE", "reason": "Balance and model endpoints succeeded",
                                      "checked_at": iso(), "source": "deepseek-balance-endpoint"}
        except urllib.error.HTTPError as exc:
            mapping = {401: "AUTH_ERROR", 402: "BALANCE_EXHAUSTED", 429: "RATE_LIMITED"}
            state = mapping.get(exc.code, "SERVER_ERROR" if exc.code >= 500 else "UNKNOWN_ERROR")
            status["deepseek"] = {"status": state, "reason": f"DeepSeek HTTP {exc.code}",
                                  "checked_at": iso(), "source": "deepseek-balance-endpoint"}
            retry = exc.headers.get("Retry-After")
            if retry and retry.isdigit():
                status["deepseek"]["retry_at"] = iso(utc_now() + dt.timedelta(seconds=int(retry)))
        except (OSError, urllib.error.URLError, TimeoutError):
            status["deepseek"] = {"status": "SERVER_ERROR", "reason": "DeepSeek preflight connection failed",
                                  "checked_at": iso(), "source": "deepseek-balance-endpoint"}
    save_provider_status(status)
    return status


def provider_available(value: dict) -> bool:
    return value.get("status") in {"AVAILABLE", "CHECK_REQUIRED"}


def repository_context(cwd: pathlib.Path | None = None) -> RepoContext:
    root = (cwd or pathlib.Path.cwd()).resolve()
    ignored = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".cache"}
    counts: dict[str, int] = {}
    file_count = 0
    has_tests = False
    build: set[str] = set()
    suffix_names = {".py": "Python", ".js": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript",
                    ".go": "Go", ".rs": "Rust", ".java": "Java", ".swift": "Swift", ".sh": "Shell"}
    try:
        for path in root.rglob("*"):
            if any(part in ignored for part in path.parts[len(root.parts):]):
                continue
            if not path.is_file():
                continue
            file_count += 1
            if file_count > 5000:
                break
            if path.suffix in suffix_names:
                lang = suffix_names[path.suffix]
                counts[lang] = counts.get(lang, 0) + 1
            low = path.name.lower()
            has_tests = has_tests or low.startswith("test_") or low.endswith("_test.py") or "tests" in path.parts
            if low in {"pyproject.toml", "package.json", "go.mod", "cargo.toml", "makefile", "pom.xml", "build.gradle"}:
                build.add(path.name)
    except OSError:
        pass
    is_git = (root / ".git").exists()
    modified = 0
    if is_git:
        result = subprocess.run(["git", "status", "--porcelain"], cwd=root, text=True, capture_output=True, timeout=5)
        if result.returncode == 0:
            modified = len([line for line in result.stdout.splitlines() if line])
    languages = tuple(k for k, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:5])
    return RepoContext(str(root), is_git, file_count, modified, languages, has_tests, tuple(sorted(build)))


def _contains(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def analyze_prompt(prompt: str, repo: RepoContext) -> tuple[dict[str, int], str, list[str]]:
    text = prompt.lower()
    scores = {name: 2 for name in (
        "complexity", "reasoning", "coding", "context_size", "failure_cost", "agentic_work",
        "repetitiveness", "deterministic_level", "ambiguity", "dependency_depth",
    )}
    reasons: list[str] = []
    deterministic = _contains(text, ("변경", "치환", "rename", "이름 변경", "버전", "정리", "포맷", "format", "filter", "필터"))
    bulk = _contains(text, ("csv", "json", "markdown", "100개", "대량", "반복", "일괄", "boilerplate"))
    coding = _contains(text, ("코드", "구현", "수정", "버그", "bug", "python", "shell", "script", "함수", "test", "테스트"))
    repo_wide = _contains(text, ("repository 전체", "repo 전체", "프로젝트 전체", "전체 분석", "multi-file", "여러 파일", "모듈"))
    architecture = _contains(text, ("architecture", "아키텍처", "구조 개선", "설계", "대규모 리팩", "전면 리팩", "subsystem"))
    dependency = _contains(text, ("dependency", "의존성", "root cause", "원인 분석", "복잡한 debugging", "복잡한 디버깅"))
    debugging = _contains(text, ("error", "오류", "exception", "traceback", "디버깅", "debug", "실패"))
    high_risk = _contains(text, ("인증", "auth", "credential", "배포", "deploy", "production", "운영", "보안", "결제", "migration"))
    agentic = _contains(text, ("실행", "테스트", "재수정", "끝까지", "자동화", "명령", "tool", "에이전트"))
    ambiguous = len(prompt.strip()) < 12 or _contains(text, ("알아서", "적당히", "뭔가", "개선해줘"))
    if deterministic:
        scores.update(complexity=3, reasoning=2, deterministic_level=8)
        reasons.append("명확한 규칙 기반 파일 변경")
    if bulk:
        scores.update(repetitiveness=9, deterministic_level=max(scores["deterministic_level"], 8))
        reasons.append("대량·반복·정형 처리")
    if coding:
        scores["coding"] = 6
        scores["agentic_work"] = max(scores["agentic_work"], 4)
    if debugging:
        scores.update(complexity=max(scores["complexity"], 5), reasoning=max(scores["reasoning"], 5),
                      deterministic_level=min(scores["deterministic_level"], 4))
        reasons.append("오류 진단과 코드 수정")
    if repo_wide:
        scores.update(complexity=7, reasoning=7, context_size=8, coding=8, agentic_work=7, dependency_depth=7)
        reasons.append("저장소 전체 또는 여러 파일 분석")
    if architecture:
        scores.update(complexity=max(scores["complexity"], 8), reasoning=8, coding=max(scores["coding"], 7),
                      context_size=max(scores["context_size"], 7), failure_cost=7, agentic_work=8, dependency_depth=8)
        reasons.append("아키텍처·대규모 구조 판단")
    if dependency:
        scores.update(complexity=max(scores["complexity"], 7), reasoning=8, dependency_depth=9)
        reasons.append("깊은 의존성 또는 실패 원인 분석")
    if high_risk:
        scores.update(complexity=max(scores["complexity"], 7), reasoning=max(scores["reasoning"], 7),
                      failure_cost=9, agentic_work=max(scores["agentic_work"], 7))
        reasons.append("인증·배포·운영 등 실패 비용이 높은 변경")
    if agentic:
        scores["agentic_work"] = max(scores["agentic_work"], 6)
    if ambiguous:
        scores["ambiguity"] = 7
    if repo.file_count > 500:
        scores["context_size"] = max(scores["context_size"], 6)
    if repo.file_count > 2000:
        scores["context_size"] = max(scores["context_size"], 8)
    if repo.modified_count:
        scores["failure_cost"] = min(10, scores["failure_cost"] + 1)
    if architecture and high_risk:
        task_type = "high-risk architecture change"
    elif architecture or repo_wide:
        task_type = "repository architecture/refactoring"
    elif dependency:
        task_type = "dependency debugging"
    elif bulk:
        task_type = "bulk deterministic transformation"
    elif coding:
        task_type = "general coding/automation"
    else:
        task_type = "simple deterministic task" if deterministic else "general task"
    return {key: min(10, max(0, value)) for key, value in scores.items()}, task_type, reasons or ["일반 작업"]


def ideal_model(scores: dict[str, int]) -> str:
    if scores["failure_cost"] >= 8 and (scores["complexity"] >= 7 or scores["reasoning"] >= 7):
        return "gpt_sol"
    if scores["complexity"] >= 7 or scores["reasoning"] >= 7 or scores["dependency_depth"] >= 7:
        return "deepseek_pro"
    if scores["deterministic_level"] >= 7 or scores["repetitiveness"] >= 7:
        return "deepseek_flash"
    return "gpt_luna"


def confidence_for(scores: dict[str, int], ideal: str) -> float:
    if ideal == "deepseek_flash":
        base = 0.78 + 0.02 * max(scores["deterministic_level"] - 7, 0) + 0.015 * max(scores["repetitiveness"] - 7, 0)
    elif ideal == "gpt_sol":
        base = 0.84 + 0.02 * max(scores["failure_cost"] - 8, 0)
    elif ideal == "deepseek_pro":
        base = 0.80 + 0.015 * max(scores["reasoning"] - 7, 0)
    else:
        base = 0.76
    base -= 0.035 * max(scores["ambiguity"] - 3, 0)
    return round(min(0.97, max(0.45, base)), 2)


def reasoning_for(scores: dict[str, int], model: Model) -> str:
    peak = max(scores["reasoning"], scores["complexity"], scores["dependency_depth"], scores["failure_cost"])
    desired = "low" if peak <= 4 else "medium" if peak <= 6 else "high"
    if scores["complexity"] >= 9 and max(scores["reasoning"], scores["dependency_depth"]) >= 9:
        desired = "max"
    supported = model.supported_reasoning_levels
    if desired in supported:
        return desired
    order = ("low", "medium", "high", "xhigh", "max")
    target = order.index(desired)
    return min(supported, key=lambda value: abs(order.index(value) - target))


def candidate_keys(models: dict[str, Model], statuses: dict) -> list[str]:
    return [key for key, model in models.items()
            if model.enabled and provider_available(statuses.get(model.provider, {}))]


def nearest_available(ideal: str, candidates: list[str], scores: dict[str, int]) -> str | None:
    if ideal in candidates:
        return ideal
    fallback_order = {
        "gpt_sol": ("deepseek_pro", "gpt_luna", "deepseek_flash"),
        "deepseek_pro": ("gpt_sol", "gpt_luna", "deepseek_flash"),
        "gpt_luna": (("deepseek_pro" if scores["complexity"] >= 6 else "deepseek_flash"), "gpt_sol"),
        "deepseek_flash": ("gpt_luna", "deepseek_pro", "gpt_sol"),
    }
    return next((key for key in fallback_order[ideal] if key in candidates), None)


def route(prompt: str, repo: RepoContext | None = None, statuses: dict | None = None,
          models: dict[str, Model] | None = None) -> Decision:
    repo = repo or repository_context()
    statuses = statuses or load_provider_status()
    models = models or load_models()
    scores, task_type, reasons = analyze_prompt(prompt, repo)
    ideal = ideal_model(scores)
    candidates = candidate_keys(models, statuses)
    selected = nearest_available(ideal, candidates, scores)
    confidence = confidence_for(scores, ideal)
    if selected != ideal and selected:
        reasons.append(f"{models[ideal].provider} Provider를 사용할 수 없어 후보를 필터링")
        confidence = max(0.55, round(confidence - 0.08, 2))
    alternatives = [key for key in candidates if key != selected]
    alternative = min(alternatives, key=lambda key: abs(models[key].priority - models[ideal].priority)) if alternatives else None
    viable_by_tier = {
        "deepseek_flash": {"deepseek_flash", "gpt_luna", "deepseek_pro", "gpt_sol"},
        "gpt_luna": {"deepseek_flash", "gpt_luna", "deepseek_pro", "gpt_sol"},
        "deepseek_pro": {"deepseek_pro", "gpt_sol"},
        "gpt_sol": {"gpt_sol", "deepseek_pro"},
    }
    viable = [key for key in candidates if key in viable_by_tier[ideal]]
    cheapest = min(viable, key=lambda key: (models[key].output_price if models[key].output_price is not None else 1e9)) if viable else None
    reasoning = reasoning_for(scores, models[selected]) if selected else None
    return Decision(scores, task_type, selected, ideal, alternative, cheapest, reasoning, confidence, reasons)


def should_use_classifier(decision: Decision, cfg: dict | None = None) -> bool:
    cfg = cfg or router_config()
    return bool(cfg["classifier"]["enabled"] and
                decision.confidence < float(cfg["classifier"]["confidence_threshold"]))


def _extract_response_text(payload: dict) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    pieces: list[str] = []
    for item in payload.get("output", []):
        for content in item.get("content", []) if isinstance(item, dict) else []:
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                pieces.append(content["text"])
    return "".join(pieces)


def classify_low_confidence(prompt: str, repo: RepoContext, decision: Decision,
                            statuses: dict, models: dict[str, Model]) -> Decision:
    """Optional cheap classifier. Never uses Sol or Pro and is disabled by default."""
    if not should_use_classifier(decision) or not provider_available(statuses.get("deepseek", {})):
        return decision
    key = _keychain_key()
    if not key:
        return decision
    cfg = router_config()
    summary = prompt.strip().replace("\n", " ")[:500]
    compact = {
        "task_summary": summary, "prompt_length": len(prompt), "repo_files": repo.file_count,
        "modified_files": repo.modified_count, "has_tests": repo.has_tests,
        "build_systems": repo.build_systems, "local_scores": decision.scores,
        "allowed": candidate_keys(models, statuses),
    }
    body = {
        "model": models[CLASSIFIER_MODEL_KEY].model_id,
        "instructions": "Select one allowed model key and low/medium/high/max. Return JSON only: {recommended_key,reasoning,confidence}.",
        "input": json.dumps(compact, ensure_ascii=False),
        "max_output_tokens": int(cfg["classifier"]["max_output_tokens"]),
        "reasoning": {"effort": "low"},
    }
    request = urllib.request.Request("https://api.deepseek.com/responses", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "codex-model-router/1"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
        result = json.loads(_extract_response_text(payload))
        allowed = candidate_keys(models, statuses)
        chosen = result.get("recommended_key")
        if chosen not in allowed:
            return decision
        effort = result.get("reasoning")
        if effort not in models[chosen].supported_reasoning_levels:
            effort = reasoning_for(decision.scores, models[chosen])
        usage = payload.get("usage", {})
        decision.recommended_key = chosen
        decision.reasoning = effort
        decision.confidence = max(0.0, min(1.0, float(result.get("confidence", decision.confidence))))
        decision.routing_method = "cheap-classifier"
        decision.classifier_used = True
        decision.routing_input_tokens = int(usage.get("input_tokens", 0) or 0)
        decision.routing_output_tokens = int(usage.get("output_tokens", 0) or 0)
        flash = models[CLASSIFIER_MODEL_KEY]
        decision.routing_cost = (decision.routing_input_tokens * (flash.input_price or 0) +
                                 decision.routing_output_tokens * (flash.output_price or 0)) / 1_000_000
    except (OSError, ValueError, KeyError, json.JSONDecodeError, urllib.error.URLError):
        pass
    return decision


def record_capability_failure(model_key: str, task_type: str) -> int:
    state = _read_json(FAILURE_STATE, {})
    fingerprint = hashlib.sha256(f"{model_key}:{task_type}".encode()).hexdigest()[:16]
    count = int(state.get(fingerprint, {}).get("count", 0)) + 1
    state[fingerprint] = {"model_key": model_key, "task_type": task_type, "count": count, "updated_at": iso()}
    _write_json(FAILURE_STATE, state)
    return count


def clear_capability_failure(model_key: str, task_type: str) -> None:
    state = _read_json(FAILURE_STATE, {})
    fingerprint = hashlib.sha256(f"{model_key}:{task_type}".encode()).hexdigest()[:16]
    if fingerprint in state:
        del state[fingerprint]
        _write_json(FAILURE_STATE, state)


def escalation_candidate(current: str, scores: dict[str, int], candidates: list[str]) -> str | None:
    if current == "deepseek_flash":
        order = ("deepseek_pro", "gpt_luna", "gpt_sol") if max(scores["reasoning"], scores["dependency_depth"]) >= 6 else ("gpt_luna", "deepseek_pro", "gpt_sol")
    elif current == "gpt_luna":
        order = ("deepseek_pro", "gpt_sol") if scores["dependency_depth"] >= 6 else ("gpt_sol", "deepseek_pro")
    elif current == "deepseek_pro":
        order = ("gpt_sol",)
    else:
        order = ()
    return next((key for key in order if key in candidates), None)


def record_usage(prompt: str, decision: Decision, selected: Model | None, result: str,
                 *, escalated: bool = False, provider_failover: bool = False,
                 task_input_tokens: int | None = None, task_output_tokens: int | None = None) -> None:
    USAGE_LOG.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    task_cost = None
    if selected and selected.provider == "deepseek" and task_input_tokens is not None and task_output_tokens is not None:
        task_cost = (task_input_tokens * (selected.input_price or 0) +
                     task_output_tokens * (selected.output_price or 0)) / 1_000_000
    overhead = None
    task_total = (task_input_tokens or 0) + (task_output_tokens or 0)
    routing_total = decision.routing_input_tokens + decision.routing_output_tokens
    if task_total:
        overhead = routing_total / task_total
    record = {
        "timestamp": iso(), "prompt_hash": hashlib.sha256(prompt.encode()).hexdigest()[:16],
        "task_type": decision.task_type, "routing_method": decision.routing_method,
        "routing_confidence": decision.confidence, "classifier_used": decision.classifier_used,
        "routing_input_tokens": decision.routing_input_tokens,
        "routing_output_tokens": decision.routing_output_tokens, "routing_cost": decision.routing_cost,
        "recommended_model": decision.recommended_key,
        "selected_model": selected.model_id if selected else None,
        "reasoning": decision.reasoning, "provider": selected.provider if selected else None,
        "scores": decision.scores, "task_input_tokens": task_input_tokens,
        "task_output_tokens": task_output_tokens, "task_cost": task_cost,
        "router_overhead_ratio": overhead, "result": result, "escalated": escalated,
        "provider_failover": provider_failover,
    }
    with USAGE_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.chmod(USAGE_LOG, 0o600)


def usage_metrics() -> dict:
    records = []
    try:
        records = [json.loads(line) for line in USAGE_LOG.read_text(encoding="utf-8").splitlines() if line]
    except (OSError, json.JSONDecodeError):
        pass
    total = len(records)
    classifier = sum(bool(item.get("classifier_used")) for item in records)
    accepted = sum(item.get("result") not in {"cancelled", "dry-run", "no-provider"} for item in records)
    return {"requests": total, "classifier_calls": classifier,
            "local_rate": (total - classifier) / total if total else 1.0,
            "acceptance_rate": accepted / total if total else 0.0,
            "routing_tokens": sum(int(item.get("routing_input_tokens", 0) or 0) + int(item.get("routing_output_tokens", 0) or 0) for item in records)}


def status_text(statuses: dict, *, details: bool = True) -> str:
    lines = []
    for name in ("openai", "deepseek"):
        value = statuses[name]
        label = "OpenAI" if name == "openai" else "DeepSeek"
        lines.extend([label, f"  Status: {value.get('status', 'UNKNOWN')}",
                      f"  Reason: {value.get('reason', 'Unavailable')}",
                      f"  Checked: {value.get('checked_at', '-')}", f"  Source: {value.get('source', '-')}"])
        if value.get("reset_at"):
            lines.append(f"  Reset: {value['reset_at']}")
        elif value.get("retry_at"):
            lines.append(f"  Retry: {value['retry_at']}")
        elif value.get("next_check_at"):
            lines.append(f"  Next check: {value['next_check_at']}")
        elif details and value.get("status") in UNAVAILABLE:
            lines.append("  Reset: unavailable")
    return "\n".join(lines)


def decision_text(decision: Decision, models: dict[str, Model], statuses: dict, *, explain: bool = False) -> str:
    rule = "─" * 32
    lines = [rule, "AI MODEL ROUTER", rule, "", "Provider Status",
             f"OpenAI       {statuses['openai']['status']}", f"DeepSeek     {statuses['deepseek']['status']}", "",
             f"Task: {decision.task_type}", ""]
    if decision.recommended_key is None:
        lines.extend(["No AI Provider Available", "", status_text(statuses), "", "Reset time is shown only when supplied by a provider."])
        return "\n".join(lines + [rule])
    selected = models[decision.recommended_key]
    lines.extend(["Recommended", f"★ {selected.display_name}", f"Model ID: {selected.model_id}",
                  f"Reasoning: {decision.reasoning.upper()}", f"Confidence: {decision.confidence:.0%}", "",
                  "Reason", *[f"- {reason}" for reason in decision.reasons]])
    if decision.ideal_key and decision.ideal_key != decision.recommended_key:
        ideal = models[decision.ideal_key]
        lines.extend(["", f"Ideal if provider available: {ideal.display_name}"])
    if decision.alternative_key:
        alt = models[decision.alternative_key]
        lines.extend(["", f"Alternative: {alt.display_name}"])
    if decision.cheapest_key:
        cheap = models[decision.cheapest_key]
        lines.append(f"Cheapest viable: {cheap.display_name}")
    lines.extend(["", "Router", f"Routing method: {decision.routing_method}",
                  f"Classifier: {'Used' if decision.classifier_used else 'Not used'}",
                  f"Routing tokens: {decision.routing_input_tokens + decision.routing_output_tokens}"])
    if explain:
        lines.extend(["", "Scores", *[f"{key:20} {value}/10" for key, value in decision.scores.items()],
                      "", "Policy: 충분히 해결 가능한 가장 저렴한 모델과 가장 낮은 충분한 reasoning을 우선합니다."])
    return "\n".join(lines + [rule])


def _tty_input(prompt: str) -> str:
    if sys.stdin.isatty():
        return input(prompt)
    try:
        with open("/dev/tty", "r+", encoding="utf-8", buffering=1) as tty_file:
            tty_file.write(prompt)
            return tty_file.readline().rstrip("\n")
    except OSError:
        return ""


def choose_interactively(decision: Decision, models: dict[str, Model], statuses: dict) -> tuple[Model | None, str | None]:
    if not decision.recommended_key:
        return None, None
    while True:
        answer = _tty_input("Run? [Y] 추천 / [M] 모델 / [R] reasoning / [D] 상세 / [S] 상태 / [N] 취소: ").strip().lower() or "y"
        if answer in {"y", "yes"}:
            return models[decision.recommended_key], decision.reasoning
        if answer in {"n", "no"}:
            return None, None
        if answer == "d":
            print(decision_text(decision, models, statuses, explain=True))
        elif answer == "s":
            print(status_text(statuses))
        elif answer == "m":
            available = set(candidate_keys(models, statuses))
            ordered = sorted(models.values(), key=lambda model: model.priority)
            for index, model in enumerate(ordered, 1):
                marker = "" if model.key in available else " [DISABLED: provider unavailable]"
                print(f"{index}. {model.display_name} ({model.model_id}){marker}")
            raw = _tty_input("Model number: ").strip()
            if raw.isdigit() and 1 <= int(raw) <= len(ordered) and ordered[int(raw)-1].key in available:
                chosen = ordered[int(raw)-1]
                decision.recommended_key = chosen.key
                decision.reasoning = reasoning_for(decision.scores, chosen)
                print(f"Selected: {chosen.display_name} / {decision.reasoning}")
        elif answer == "r":
            model = models[decision.recommended_key]
            print("Supported: " + ", ".join(model.supported_reasoning_levels))
            value = _tty_input("Reasoning: ").strip().lower()
            if value in model.supported_reasoning_levels:
                decision.reasoning = value
            else:
                print("지원하지 않는 reasoning 값입니다.")


def parse_prompt(argv: list[str]) -> tuple[str | None, dict]:
    options = {"dry_run": False, "explain": False, "yes": False, "refresh": False}
    prompt_file = None
    positional: list[str] = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--dry-run": options["dry_run"] = True
        elif arg == "--explain": options["explain"] = True
        elif arg in {"--yes", "-y"}: options["yes"] = True
        elif arg == "--refresh": options["refresh"] = True
        elif arg == "--prompt-file" and index + 1 < len(argv):
            index += 1; prompt_file = argv[index]
        else: positional.append(arg)
        index += 1
    if prompt_file:
        try: return pathlib.Path(prompt_file).read_text(encoding="utf-8"), options
        except OSError as exc:
            print(f"Prompt file error: {exc}", file=sys.stderr); return None, options
    if positional:
        return " ".join(positional), options
    if not sys.stdin.isatty():
        return sys.stdin.read(), options
    value = _tty_input("작업 Prompt를 입력하세요: ")
    return value, options


def doctor() -> int:
    models = load_models()
    cfg = router_config()
    login = subprocess.run([REAL_CODEX, "login", "status"], text=True, capture_output=True)
    router_target = str(STATE_DIR / "codex_router.py")
    def owned_link(name: str) -> bool:
        path = HOME / ".local" / "bin" / name
        return path.is_symlink() and os.readlink(path) == router_target
    checks = {
        "Codex CLI": pathlib.Path(REAL_CODEX).exists(),
        "Codex version": subprocess.run([REAL_CODEX, "--version"], capture_output=True).returncode == 0,
        "OpenAI ChatGPT auth": login.returncode == 0 and "ChatGPT" in login.stdout + login.stderr,
        "DeepSeek Keychain auth": bool(_keychain_key()),
        "Router config": CONFIG.is_file(), "Model config": bool(models),
        "DeepSeek profile": (HOME / ".codex" / "deepseek.config.toml").is_file(),
        "Usage log directory": USAGE_LOG.parent.is_dir(),
        "ai command installed": owned_link("ai"),
        "codex wrapper preserved": owned_link("codex"),
        "Classifier default off": not bool(cfg["classifier"]["enabled"]),
    }
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'} {name}")
    print("API keys and tokens were not displayed.")
    return 0 if all(checks.values()) else 1


def cli(argv: list[str], executor: Callable[[str, Model, str, Decision, dict], int]) -> int:
    if argv and argv[0] in {"status", "provider-status"}:
        refresh = "--refresh" in argv[1:]
        statuses = refresh_provider_status() if refresh else load_provider_status()
        print(status_text(statuses))
        metrics = usage_metrics()
        print(f"Router\n  Requests: {metrics['requests']}\n  Classifier calls: {metrics['classifier_calls']}\n"
              f"  Local routing rate: {metrics['local_rate']:.0%}\n  Routing tokens: {metrics['routing_tokens']}")
        return 0
    if argv and argv[0] == "doctor":
        return doctor()
    prompt, options = parse_prompt(argv)
    if prompt is None or not prompt.strip():
        print("작업 Prompt가 비어 있습니다.", file=sys.stderr)
        return 2
    repo = repository_context()
    statuses = refresh_provider_status() if options["refresh"] else load_provider_status()
    models = load_models()
    if not models:
        print(f"Model config를 읽을 수 없습니다: {MODELS_CONFIG}", file=sys.stderr)
        return 78
    decision = route(prompt, repo, statuses, models)
    decision = classify_low_confidence(prompt, repo, decision, statuses, models)
    print(decision_text(decision, models, statuses, explain=options["explain"] or options["dry_run"]))
    if not decision.recommended_key:
        record_usage(prompt, decision, None, "no-provider")
        return 69
    if options["dry_run"]:
        record_usage(prompt, decision, models[decision.recommended_key], "dry-run")
        return 0
    if options["yes"]:
        selected, effort = models[decision.recommended_key], decision.reasoning
    else:
        selected, effort = choose_interactively(decision, models, statuses)
    if not selected or not effort:
        record_usage(prompt, decision, None, "cancelled")
        print("취소했습니다.")
        return 0
    return executor(prompt, selected, effort, decision, statuses)
