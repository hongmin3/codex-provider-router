#!/usr/bin/env python3
"""Single-command Codex provider router for macOS."""
from __future__ import annotations

import datetime as dt
import fcntl
import getpass
import hashlib
import json
import os
import pathlib
import re
import select
import shutil
import signal
import subprocess
import struct
import sys
import termios
import time
import tty
import tomllib
import unicodedata
import urllib.error
import urllib.request

import model_router

REAL_CODEX = "/opt/homebrew/bin/codex"
DEFAULT_MODEL = "deepseek-flash"
BASE = pathlib.Path.home() / ".codex" / "router"
STATE_FILE = BASE / "state.json"
LOG_DIR = BASE / "logs"
CHECKPOINTS = BASE / "fallback-state"
CONFIG = pathlib.Path.home() / ".config" / "codex-router" / "config.toml"
KEYCHAIN_SERVICE = "codex-router-deepseek"
KEYCHAIN_ACCOUNT = os.environ.get("USER", "codex")
USAGE_RE = re.compile(
    r"(?:(?:you(?:'|’)?ve|you have)\s+(?:hit|reached)\s+(?:your\s+)?(?:usage|session|workspace credit|\w+[- ]hour|weekly)\s+limit)"
    r"|(?:(?:usage|session|rate|quota)\s+limit\s+(?:reached|exceeded))"
    r"|(?:quota\s+(?:exceeded|reached))|(?:too many requests)"
    r"|(?:no\s+(?:usage|requests?|tokens?)\s+(?:remaining|left))"
    r"|(?:(?:you(?:'|’)?re|your workspace is)\s+out of credits)"
    r"|(?:usage not included)|(?:(?<![\d.])0%\s+left)"
    r"|(?:(?:http|status(?:\s+code)?)\s*[:=]?\s*429)",
    re.I,
)
AUTH_RE = re.compile(r"unauthorized|forbidden|invalid\s*(?:auth|token)|login\s*expired|status\s*40[13]|http\s*40[13]", re.I)
NETWORK_RE = re.compile(r"timed?\s*out|dns|connection\s*(?:reset|refused)|network\s*(?:is\s*)?unreachable", re.I)
BILLING_RE = re.compile(r"insufficient\s*(?:balance|funds|credits?)|balance\s*(?:is\s*)?(?:insufficient|low)|payment\s*required|billing|recharge|top[ -]?up|status\s*402|http\s*402", re.I)
DEEPSEEK_QUOTA_RE = re.compile(r"quota\s*(?:exceeded|reached)|rate\s*limit|too many requests|status\s*429|http\s*429", re.I)
SERVER_RE = re.compile(r"internal server error|bad gateway|service unavailable|gateway timeout|status\s*5\d\d|http\s*5\d\d", re.I)
ANSI_RE = re.compile(rb"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
DEEPSEEK_CATALOG_URL = "https://cdn.deepseek.com/api-docs/codex-deepseek-setup-en.sh"


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(value: dt.datetime | None = None) -> str:
    return (value or now()).isoformat(timespec="seconds")


def ensure_dirs() -> None:
    for path in (BASE, LOG_DIR, CHECKPOINTS, CONFIG.parent):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(BASE, 0o700)


def config() -> dict:
    defaults = {"primary": {"provider": "openai"},
                "fallback": {"provider": "deepseek", "model": DEFAULT_MODEL, "reasoning_effort": "high"},
                "routing": {"auto_fallback": True, "auto_return": True, "probe_minutes": [10, 20, 30, 60], "catalog_check_hours": 24},
                "cost": {"daily_limit_usd": 0.0, "monthly_limit_usd": 0.0,
                         "low_balance_usd": model_router.LOW_BALANCE_USD},
                "model_router": {"recommendation": True, "auto_model_switch": False,
                                 "auto_provider_failover": False, "auto_escalation": False},
                "classifier": {"enabled": False, "confidence_threshold": 0.70,
                               "max_output_tokens": 150},
                "provider_cache": {"ttl_minutes": 15, "rate_limit_minutes": 5}}
    if CONFIG.exists():
        loaded = tomllib.loads(CONFIG.read_text())
        for section, values in loaded.items():
            defaults.setdefault(section, {}).update(values)
    return defaults


def write_config(cfg: dict) -> None:
    def boolean(value: object) -> str:
        return "true" if value else "false"
    data = f'''[primary]\nprovider = "openai"\n\n[fallback]\nprovider = "deepseek"\nmodel = "{cfg['fallback']['model']}"\nreasoning_effort = "{cfg['fallback']['reasoning_effort']}"\n\n[routing]\nauto_fallback = {boolean(cfg['routing']['auto_fallback'])}\nauto_return = {boolean(cfg['routing']['auto_return'])}\nprobe_minutes = [{', '.join(str(int(v)) for v in cfg['routing']['probe_minutes'])}]\ncatalog_check_hours = {int(cfg['routing']['catalog_check_hours'])}\n\n[cost]\ndaily_limit_usd = {float(cfg['cost']['daily_limit_usd'])}\nmonthly_limit_usd = {float(cfg['cost']['monthly_limit_usd'])}\n# DeepSeek 잔액이 이 금액(USD) 미만이면 세션 시작 시 경고합니다.\nlow_balance_usd = {float(cfg['cost'].get('low_balance_usd', model_router.LOW_BALANCE_USD))}\n\n[model_router]\nrecommendation = {boolean(cfg['model_router']['recommendation'])}\nauto_model_switch = {boolean(cfg['model_router']['auto_model_switch'])}\nauto_provider_failover = {boolean(cfg['model_router']['auto_provider_failover'])}\nauto_escalation = {boolean(cfg['model_router']['auto_escalation'])}\n\n[classifier]\n# 기본 OFF: 일반 라우팅은 local heuristic만 사용하므로 추가 LLM token은 0입니다.\nenabled = {boolean(cfg['classifier']['enabled'])}\nconfidence_threshold = {float(cfg['classifier']['confidence_threshold'])}\nmax_output_tokens = {int(cfg['classifier']['max_output_tokens'])}\n\n[provider_cache]\nttl_minutes = {int(cfg['provider_cache']['ttl_minutes'])}\nrate_limit_minutes = {int(cfg['provider_cache']['rate_limit_minutes'])}\n'''
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(data); os.chmod(tmp, 0o600); tmp.replace(CONFIG)


def fallback_model() -> str:
    return str(config()["fallback"]["model"])


def reasoning_effort() -> str:
    return str(config()["fallback"]["reasoning_effort"])


def low_balance_usd() -> float:
    """USD amount below which a DeepSeek balance is warned about."""
    try:
        value = float(config()["cost"].get("low_balance_usd", model_router.LOW_BALANCE_USD))
    except (KeyError, TypeError, ValueError):
        return model_router.LOW_BALANCE_USD
    return value if value >= 0 else model_router.LOW_BALANCE_USD


# --- 비용 한도 ------------------------------------------------------------
# 한도는 실제로 실행을 막는다. DeepSeek은 token당 과금이라 여기서 계산한 비용이 실제
# 청구액이고, OpenAI(ChatGPT 로그인)는 정액제라 한도 계산에서 제외한다.
#
# 세션 사용량은 Codex가 남기는 rollout(`~/.codex/sessions/**/rollout-*.jsonl`)에서 읽는다.
# 세션 전에 파일 크기를 스냅샷하고, 끝난 뒤 늘어난 부분의 `last_token_usage`만 합산하므로
# `resume`으로 이어진 세션도 이번 실행분만 계산된다.
SPEND_LOG = BASE / "spend.jsonl"
SESSIONS_DIR = pathlib.Path.home() / ".codex" / "sessions"
ROLLOUT_WINDOW_HOURS = 6.0
EX_LIMIT = 75


def _positive_limit(section: object, key: str, default: float) -> float | None:
    """설정값을 한도로 읽는다. 0 이하는 '한도 없음'을 뜻한다."""
    value = default
    if isinstance(section, dict):
        try:
            value = float(section.get(key, default))
        except (TypeError, ValueError):
            value = default
    return value if value > 0 else None


def cost_limits() -> dict:
    section = config().get("cost", {})
    return {
        "daily": _positive_limit(section, "daily_limit_usd", 0.0),
        "monthly": _positive_limit(section, "monthly_limit_usd", 0.0),
    }


def model_prices() -> dict[str, tuple[float, float, float]]:
    prices: dict[str, tuple[float, float, float]] = {}
    try:
        models = model_router.load_models()
    except (OSError, ValueError, KeyError, tomllib.TOMLDecodeError):
        return prices
    for model in models.values():
        if model.input_price is None and model.output_price is None:
            continue
        cached = model.cached_input_price
        prices[model.model_id] = (
            float(model.input_price or 0.0),
            float(cached if cached is not None else model.input_price or 0.0),
            float(model.output_price or 0.0),
        )
    return prices


def estimate_cost(model_id: str | None, input_tokens: int, cached_tokens: int,
                  output_tokens: int) -> float | None:
    """token 사용량을 models.toml 단가로 환산한다. 단가를 모르면 None."""
    if not model_id:
        return None
    prices = model_prices().get(model_id)
    if prices is None:
        return None
    input_price, cached_price, output_price = prices
    cached = max(min(int(cached_tokens or 0), int(input_tokens or 0)), 0)
    fresh = max(int(input_tokens or 0) - cached, 0)
    return (fresh * input_price + cached * cached_price +
            int(output_tokens or 0) * output_price) / 1_000_000


def _rollout_paths() -> list[pathlib.Path]:
    try:
        return sorted(SESSIONS_DIR.rglob("*.jsonl"))
    except OSError:
        return []


def _rollout_meta(path: pathlib.Path) -> dict | None:
    """rollout 파일의 session_meta 필드만 읽는다. 대화·지시문·자격증명은 읽지 않는다."""
    try:
        handle = path.open(errors="replace")
    except OSError:
        return None
    with handle:
        for line in handle:
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict) or record.get("type") != "session_meta":
                continue
            payload = record.get("payload")
            if not isinstance(payload, dict):
                return None
            return {
                "session_id": payload.get("session_id"),
                "cwd": payload.get("cwd"),
                "originator": payload.get("originator"),
                "model_provider": payload.get("model_provider"),
            }
    return None


def find_recent_session_id(started_after: dt.datetime, provider: str = "openai",
                           cwd: str | pathlib.Path | None = None,
                           originator: str = "codex-tui") -> str | None:
    """방금 시작된 세션의 session id를 rollout 메타에서 찾는다.

    `resume --last`는 SIGINT로 끝난 세션을 "기록된 마지막 세션"으로 보지 않아 다른
    thread를 열 수 있다. 대신 시작 시각 이후에 쓰인 rollout 중 provider·cwd·
    originator가 맞는 최신 세션의 id를 돌려주고, resume이 그 id를 직접 받는다.
    """
    cutoff = started_after.timestamp()
    cwd_key = unicodedata.normalize("NFC", str(cwd)) if cwd is not None else None
    best: tuple[float, str] | None = None
    for path in _rollout_paths():
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_mtime < cutoff:
            continue
        meta = _rollout_meta(path)
        if not meta or not meta.get("session_id"):
            continue
        if meta.get("model_provider") != provider or meta.get("originator") != originator:
            continue
        if cwd_key is not None and unicodedata.normalize("NFC", str(meta.get("cwd") or "")) != cwd_key:
            continue
        if best is None or stat.st_mtime > best[0]:
            best = (stat.st_mtime, str(meta["session_id"]))
    return best[1] if best else None


def rollout_snapshot() -> dict[str, int]:
    """최근 사용한 rollout 파일의 현재 크기. 세션 전후 사용량 차이를 재는 기준점."""
    cutoff = time.time() - ROLLOUT_WINDOW_HOURS * 3600
    snapshot: dict[str, int] = {}
    for path in _rollout_paths():
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_mtime >= cutoff:
            snapshot[str(path)] = stat.st_size
    return snapshot


def _sum_token_events(data: bytes) -> dict[str, int]:
    totals = {"input": 0, "cached": 0, "output": 0, "turns": 0}
    for line in data.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        payload = record.get("payload") if isinstance(record, dict) else None
        if not isinstance(payload, dict) or payload.get("type") != "token_count":
            continue
        info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
        usage = info.get("last_token_usage") or info.get("total_token_usage")
        if not isinstance(usage, dict):
            continue
        for name, key in (("input", "input_tokens"), ("cached", "cached_input_tokens"),
                          ("output", "output_tokens")):
            try:
                totals[name] += int(usage.get(key, 0) or 0)
            except (TypeError, ValueError):
                continue
        totals["turns"] += 1
    return totals


def rollout_usage_since(snapshot: dict[str, int]) -> dict[str, int]:
    """스냅샷 이후 늘어난 token_count만 합산한다."""
    totals = {"input": 0, "cached": 0, "output": 0, "turns": 0, "files": 0}
    cutoff = time.time() - ROLLOUT_WINDOW_HOURS * 3600
    for path in _rollout_paths():
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_mtime < cutoff:
            continue
        start = snapshot.get(str(path), 0)
        if stat.st_size <= start:
            continue
        try:
            with path.open("rb") as handle:
                handle.seek(start)
                data = handle.read()
        except OSError:
            continue
        counted = _sum_token_events(data)
        if counted["turns"] == 0:
            continue
        totals["files"] += 1
        for name in ("input", "cached", "output", "turns"):
            totals[name] += counted[name]
    return totals


def spend_records() -> list[dict]:
    """비용 원장. router 자체 원장 + model_router 사용 기록을 함께 본다."""
    records: list[dict] = []
    for path in (SPEND_LOG, model_router.USAGE_LOG):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                records.append(record)
    return records


def _record_cost(record: dict) -> float:
    total = 0.0
    if "cost_usd" in record:
        try:
            return float(record.get("cost_usd") or 0.0)
        except (TypeError, ValueError):
            return 0.0
    for key in ("task_cost", "routing_cost"):
        try:
            total += float(record.get(key) or 0.0)
        except (TypeError, ValueError):
            continue
    return total


def _record_time(record: dict) -> dt.datetime | None:
    for key in ("timestamp", "recorded_at"):
        value = record.get(key)
        if not value:
            continue
        try:
            when = dt.datetime.fromisoformat(str(value))
        except ValueError:
            continue
        return when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)
    return None


def spend_totals(now_value: dt.datetime | None = None) -> dict:
    """오늘·이번 달 비용과, 비용 근거를 찾지 못한 기록 수(커버리지)."""
    moment = (now_value or now()).astimezone()
    totals = {"today": 0.0, "month": 0.0, "records": 0, "unpriced": 0,
              "sessions": 0, "token_sessions": 0}
    for record in spend_records():
        when = _record_time(record)
        if when is None:
            continue
        totals["records"] += 1
        if "cost_usd" in record:
            totals["sessions"] += 1
        has_tokens = (record.get("input_tokens") is not None or
                      record.get("task_input_tokens") is not None)
        has_cost = (("cost_usd" in record and record.get("cost_usd") is not None) or
                    record.get("task_cost") is not None)
        if has_tokens:
            totals["token_sessions"] += 1
        if not has_tokens and not has_cost:
            totals["unpriced"] += 1
        cost = _record_cost(record)
        local = when.astimezone()
        if local.date() == moment.date():
            totals["today"] += cost
        if (local.year, local.month) == (moment.year, moment.month):
            totals["month"] += cost
    return totals


def record_session_spend(provider: str, model_id: str, snapshot: dict[str, int],
                         output: str = "") -> dict | None:
    """세션 사용량을 비용 원장에 남긴다. 단가를 모르면 cost_usd는 null이다."""
    usage = rollout_usage_since(snapshot)
    if usage["turns"] == 0:
        input_tokens, output_tokens = parse_task_tokens(output)
        if input_tokens is None:
            return None
        usage = {"input": input_tokens, "cached": 0, "output": output_tokens or 0,
                 "turns": 1, "files": 0}
    cost = estimate_cost(model_id, usage["input"], usage["cached"], usage["output"])
    record = {
        "timestamp": iso(), "provider": provider, "model": model_id,
        "input_tokens": usage["input"], "cached_input_tokens": usage["cached"],
        "output_tokens": usage["output"], "turns": usage["turns"],
        "rollout_files": usage["files"], "cost_usd": cost,
    }
    ensure_dirs()
    with SPEND_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.chmod(SPEND_LOG, 0o600)
    return record


def fallback_elapsed_minutes(state: dict | None = None,
                             now_value: dt.datetime | None = None) -> float | None:
    """OpenAI 한도 감지 후 DeepSeek로 버틴 시간. 기준 시각이 없으면 None."""
    state = state if state is not None else load_state()
    started = state.get("cooldown_started_at")
    if not started:
        return None
    try:
        when = dt.datetime.fromisoformat(str(started))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    moment = now_value or now()
    return (moment - when).total_seconds() / 60


def limit_block(provider: str, state: dict | None = None) -> str | None:
    """실행을 막아야 하면 이유 문장을, 아니면 None을 돌려준다."""
    if provider != "deepseek":
        return None
    limits = cost_limits()
    totals = spend_totals()
    if limits["daily"] is not None and totals["today"] >= limits["daily"]:
        return (f"일일 비용 한도 USD {limits['daily']:.2f}에 도달했습니다"
                f"(오늘 USD {totals['today']:.2f}).")
    if limits["monthly"] is not None and totals["month"] >= limits["monthly"]:
        return (f"월간 비용 한도 USD {limits['monthly']:.2f}에 도달했습니다"
                f"(이번 달 USD {totals['month']:.2f}).")
    return None


def block_notice(reason: str) -> str:
    return (
        f"\n[Codex Router] {reason}\n"
        "[Codex Router] 한도 때문에 실행을 시작하지 않았습니다.\n"
        "[Codex Router] 한도 조정: ~/.config/codex-router/config.toml "
        "([cost] daily_limit_usd·monthly_limit_usd, 0은 한도 없음)\n"
        "[Codex Router] 누적 확인: `codex-router cost` · fallback 상태 초기화: `codex-router reset`\n"
    )


def enforce_limit(provider: str) -> int | None:
    """한도에 걸리면 안내를 출력하고 종료 코드를 돌려준다."""
    reason = limit_block(provider)
    if not reason:
        return None
    print(block_notice(reason), file=sys.stderr)
    log("blocked", provider=provider, reason=reason)
    return EX_LIMIT


def cost_command(args: list[str]) -> int:
    if [arg for arg in args if arg != "--json"]:
        print("Usage: codex-router cost [--json]", file=sys.stderr)
        return 2
    limits, totals = cost_limits(), spend_totals()
    elapsed = fallback_elapsed_minutes()
    payload = {
        "limits": {"daily_usd": limits["daily"], "monthly_usd": limits["monthly"]},
        "spend": {"today_usd": round(totals["today"], 4),
                  "month_usd": round(totals["month"], 4)},
        "coverage": {"records": totals["records"], "sessions": totals["sessions"],
                     "sessions_with_tokens": totals["token_sessions"],
                     "sessions_without_price": totals["unpriced"]},
        "fallback_elapsed_minutes": None if elapsed is None else round(elapsed, 1),
        "metered_provider": "deepseek",
    }
    if "--json" in args:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    daily = "unlimited" if limits["daily"] is None else f"USD {limits['daily']:.2f}"
    monthly = "unlimited" if limits["monthly"] is None else f"USD {limits['monthly']:.2f}"
    print(f"Metered provider: DeepSeek (OpenAI ChatGPT login is flat-rate and not counted)")
    print(f"Today: USD {totals['today']:.2f} / {daily}")
    print(f"This month: USD {totals['month']:.2f} / {monthly}")
    print(f"Fallback elapsed: " +
          ("none" if elapsed is None else f"{elapsed:.0f} min"))
    print(f"Records: {totals['records']} · sessions: {totals['sessions']} · "
          f"with tokens: {totals['token_sessions']} · without price: {totals['unpriced']}")
    return 0


def catalog_models() -> list[str]:
    try:
        catalog = json.loads((BASE / "deepseek-models.json").read_text())
        validate_catalog(catalog)
        return [m["slug"] for m in catalog["models"]]
    except (OSError, ValueError):
        return ["deepseek-flash", "deepseek-v4-pro"]


def validate_catalog(catalog: object) -> None:
    models = catalog.get("models") if isinstance(catalog, dict) else None
    if not isinstance(models, list) or not models or any(
        not isinstance(m, dict) or not isinstance(m.get("slug"), str) or not m["slug"].strip()
        for m in models
    ):
        raise ValueError("official catalog is missing valid model slugs")
    if len({m["slug"] for m in models}) != len(models):
        raise ValueError("official catalog contains duplicate model slugs")


def parse_official_catalog(script: str) -> dict:
    match = re.search(r"<<'CODEX_MODELS_JSON'\s*\n(.*?)\nCODEX_MODELS_JSON\s*$", script, re.S | re.M)
    if not match:
        raise ValueError("official catalog block not found")
    catalog = json.loads(match.group(1))
    validate_catalog(catalog)
    return catalog


def check_model_catalog(force: bool = False, announce: bool = True) -> tuple[str, list[str]]:
    state, cfg = load_state(), config()
    last = state.get("last_catalog_check_at")
    if not force and last:
        try:
            age = now() - dt.datetime.fromisoformat(last)
            if age < dt.timedelta(hours=int(cfg["routing"]["catalog_check_hours"])):
                return "cached", list(state.get("new_deepseek_models", []))
        except (TypeError, ValueError):
            pass
    state["last_catalog_check_at"] = iso()
    try:
        request = urllib.request.Request(DEEPSEEK_CATALOG_URL, headers={"User-Agent": "codex-provider-router/1"})
        with urllib.request.urlopen(request, timeout=5) as response:
            remote = parse_official_catalog(response.read().decode("utf-8"))
        old_models = set(catalog_models())
        remote_models = {m["slug"] for m in remote["models"]}
        new_models = sorted(remote_models - old_models)
        selected = fallback_model()
        if selected not in remote_models:
            state["catalog_warning"] = f"selected model {selected} is absent from the latest catalog"
            save_state(state)
            log("model_catalog_check", result="selected_model_missing", model=selected)
            if announce:
                print(f"[Codex Router] 모델 알림: 현재 선택 모델 `{selected}`이 최신 공식 catalog에 없습니다. 기존 catalog와 설정을 유지합니다.", file=sys.stderr)
            return "selected_model_missing", []
        path = BASE / "deepseek-models.json"
        if not path.exists() or path.read_text() != json.dumps(remote, ensure_ascii=False, indent=2) + "\n":
            if path.exists():
                backup = BASE / "deepseek-models.previous.json"
                backup.write_text(path.read_text()); os.chmod(backup, 0o600)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(remote, ensure_ascii=False, indent=2) + "\n"); os.chmod(tmp, 0o600); tmp.replace(path)
        state["new_deepseek_models"] = new_models
        state.pop("catalog_warning", None)
        save_state(state)
        log("model_catalog_check", result="updated" if new_models else "current", new_models=new_models)
        if new_models and announce:
            print(f"[Codex Router] 새 DeepSeek 모델 발견: {', '.join(new_models)}", file=sys.stderr)
            print("[Codex Router] 기본 fallback은 변경하지 않았습니다. `codex-router models list`에서 확인하세요.", file=sys.stderr)
        return "updated" if new_models else "current", new_models
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
        save_state(state)
        log("model_catalog_check", result="failed", failure_type=type(exc).__name__)
        if announce:
            print("[Codex Router] DeepSeek 모델 목록 확인에 실패했습니다. 기존 catalog를 계속 사용합니다.", file=sys.stderr)
        return "failed", []


def model_aliases() -> dict[str, str]:
    available = catalog_models()
    flash = "deepseek-flash" if "deepseek-flash" in available else next((m for m in available if "flash" in m), DEFAULT_MODEL)
    pro = "deepseek-v4-pro" if "deepseek-v4-pro" in available else next((m for m in available if "pro" in m), flash)
    return {"flash": flash, "pro": pro, "vision": flash}


def load_state() -> dict:
    default = {"state": "OPENAI_ACTIVE", "probe_attempt": 0, "updated_at": iso()}
    try:
        state = json.loads(STATE_FILE.read_text())
        if not isinstance(state, dict):
            return default
        if state.get("state") not in ("OPENAI_ACTIVE", "OPENAI_COOLDOWN", "DEEPSEEK_ACTIVE"):
            return default
        attempt = state.get("probe_attempt", 0)
        if type(attempt) is not int or attempt < 0:
            return default
        if state["state"] != "OPENAI_ACTIVE":
            due = dt.datetime.fromisoformat(state["next_probe_at"])
            if due.tzinfo is None:
                return default
        return state
    except (OSError, ValueError, TypeError, KeyError):
        return default


def save_state(value: dict) -> None:
    value["updated_at"] = iso()
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=2) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(STATE_FILE)


def log(event: str, **fields: object) -> None:
    ensure_dirs()
    path = LOG_DIR / f"router-{now():%Y-%m}.jsonl"
    record = {"timestamp": iso(), "event": event, **fields}
    with path.open("a") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.chmod(path, 0o600)
    files = sorted(LOG_DIR.glob("router-*.jsonl"), reverse=True)
    for old in files[6:]:
        old.unlink()


def keychain_key() -> str | None:
    env = os.environ.get("DEEPSEEK_API_KEY")
    if env:
        return env
    result = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE,
                             "-a", KEYCHAIN_ACCOUNT, "-w"], text=True, capture_output=True)
    return result.stdout.rstrip("\n") if result.returncode == 0 else None


def set_key() -> int:
    first = getpass.getpass("DeepSeek API Key (hidden): ")
    second = getpass.getpass("Confirm API Key (hidden): ")
    if not first or first != second:
        print("Key is empty or does not match.", file=sys.stderr)
        return 2
    result = subprocess.run(["security", "add-generic-password", "-U", "-s", KEYCHAIN_SERVICE,
                             "-a", KEYCHAIN_ACCOUNT, "-w", first], stdout=subprocess.DEVNULL)
    first = second = ""
    if result.returncode == 0:
        print("DeepSeek key stored in macOS Keychain; no plaintext file was created.")
    return result.returncode


def checkpoint(reason: str) -> pathlib.Path:
    cwd = pathlib.Path.cwd()
    ident = hashlib.sha256(str(cwd).encode()).hexdigest()[:12]
    out = CHECKPOINTS / ident / now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, mode=0o700)
    def capture(name: str, cmd: list[str]) -> None:
        try:
            result = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, timeout=15)
            (out / name).write_text(result.stdout + result.stderr)
        except Exception as exc:
            (out / name).write_text(f"capture failed: {type(exc).__name__}\n")
    capture("git-status.txt", ["git", "status", "--short", "--branch"])
    capture("git-diff.txt", ["git", "diff", "--no-ext-diff"])
    instructions = []
    for name in ("AGENTS.md", "README.md", "progress.md"):
        file = cwd / name
        if file.is_file() and file.stat().st_size <= 200_000:
            instructions.append(f"\n## {name}\n{file.read_text(errors='replace')}")
    (out / "checkpoint.md").write_text(
        f"# Codex provider fallback checkpoint\n\nTime: {iso()}\nReason: {reason}\n"
        f"Working directory: {cwd}\n\nCodex will resume the same persisted thread with DeepSeek.\n"
        "Conversation, user request, tool calls, command results, and TODOs remain in Codex thread history.\n"
        + "".join(instructions))
    os.chmod(out / "checkpoint.md", 0o600)
    log("checkpoint_created", path=str(out), reason=reason)
    return out


def openai_limit_type(output: str) -> str:
    if re.search(r"weekly\s+limit", output, re.I):
        return "WEEKLY_LIMITED"
    if re.search(r"(?:session|5[- ]?hour)\s+limit", output, re.I):
        return "SESSION_LIMITED"
    return "LIMITED"


def explicit_reset_at(output: str) -> str | None:
    """Accept only provider-supplied ISO timestamps; never derive a time from 'in 5h'."""
    match = re.search(r"reset(?:s)?(?:\s+at|\s*:)\s*(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2}))", output, re.I)
    if not match:
        return None
    value = match.group(1).replace(" ", "T").replace("Z", "+00:00")
    try:
        return dt.datetime.fromisoformat(value).isoformat(timespec="seconds")
    except ValueError:
        return None


def cooldown(reason: str, output: str = "") -> None:
    cfg, state = config(), load_state()
    attempt = 0
    delay = cfg["routing"]["probe_minutes"][attempt]
    state.update({"state": "OPENAI_COOLDOWN", "reason": reason,
                  "limit_type": openai_limit_type(output), "probe_attempt": attempt,
                  "cooldown_started_at": iso(), "next_probe_at": iso(now() + dt.timedelta(minutes=delay))})
    reset_at = explicit_reset_at(output)
    if reset_at:
        state["reset_at"] = reset_at
    save_state(state)
    log("fallback", provider="deepseek", model=fallback_model(), reason=reason, next_probe_at=state["next_probe_at"])


def mark_openai_active(reason: str) -> None:
    state = load_state()
    for field in ("reason", "limit_type", "cooldown_started_at", "next_probe_at", "reset_at"):
        state.pop(field, None)
    state.update(state="OPENAI_ACTIVE", probe_attempt=0)
    save_state(state)
    model_router.update_provider("openai", "AVAILABLE", reason, source="codex-runtime")


def confirm_answer(answer: str) -> bool:
    return answer.strip().lower() in ("y", "yes")


def confirm_fallback(reason: str) -> bool:
    """Ask before an automatic OpenAI→DeepSeek switch; default is No.

    Explicit requests (`FORCE_DEEPSEEK=1`, the `deep` command) never reach here.
    Non-interactive runs are handled by the earlier bypass branch in `run_codex`.
    """
    if not sys.stdin.isatty():
        return False
    try:
        answer = input(f"\n[Codex Router] {reason} - DeepSeek로 전환할까요? [y/N]: ")
    except (EOFError, KeyboardInterrupt):
        return False
    return confirm_answer(answer)


def probe_openai() -> bool:
    state, cfg = load_state(), config()
    print("[Codex Router] OpenAI probe...", file=sys.stderr)
    log("openai_probe", attempt=state.get("probe_attempt", 0) + 1)
    env = os.environ.copy()
    env["CODEX_ROUTER_BYPASS"] = "1"
    try:
        result = subprocess.run([REAL_CODEX, "-s", "read-only", "-a", "never", "exec",
                                 "--ephemeral", "--skip-git-repo-check", "Reply exactly: OK"],
                                env=env, text=True, capture_output=True, timeout=90)
    except subprocess.TimeoutExpired:
        result = subprocess.CompletedProcess([], 124, "", "timeout")
    body = result.stdout + result.stderr
    if result.returncode == 0 and not USAGE_RE.search(body):
        mark_openai_active("OpenAI probe succeeded")
        log("openai_return", result="success")
        return True
    attempt = min(int(state.get("probe_attempt", 0)) + 1, len(cfg["routing"]["probe_minutes"]) - 1)
    state.update({"state": "OPENAI_COOLDOWN", "probe_attempt": attempt,
                  "next_probe_at": iso(now() + dt.timedelta(minutes=cfg["routing"]["probe_minutes"][attempt]))})
    save_state(state)
    log("openai_probe", result="failed", failure_type="usage" if USAGE_RE.search(body) else "other")
    return False


def select_provider() -> str:
    if os.environ.get("FORCE_DEEPSEEK") == "1":
        return "deepseek"
    state = load_state()
    if state.get("state") == "OPENAI_ACTIVE":
        return "openai"
    due = dt.datetime.fromisoformat(state.get("next_probe_at", iso())) <= now()
    return "openai" if due and probe_openai() else "deepseek"


def deepseek_args(args: list[str], resume: bool = False,
                  session_id: str | None = None) -> list[str]:
    filtered, skip = [], False
    for index, arg in enumerate(args):
        if arg == "--":
            filtered.extend(args[index:])
            break
        if skip:
            skip = False
            continue
        if arg in ("-p", "--profile", "-m", "--model"):
            skip = True
            continue
        if arg.startswith(("--profile=", "--model=")):
            continue
        filtered.append(arg)
    if resume:
        prefix = ["resume", session_id] if session_id else ["resume"]
    else:
        prefix = []
    return [*prefix, "--profile", "deepseek", "--model", fallback_model(),
            "-c", f'model_reasoning_effort="{reasoning_effort()}"', *filtered]


def remove_leading_codex_token(args: list[str]) -> list[str]:
    """`deep` reads as a prefix, like `sudo`, so drop a leading `codex`.

    Codex has no subcommand called `codex`, so removing one can never swallow a real
    argument, and `deep codex --yolo` stays identical to `deep --yolo` on both platforms.
    """
    if args and args[0] == "codex":
        return list(args[1:])
    return list(args)


def run_codex_deepseek(args: list[str]) -> int:
    """Start Codex on DeepSeek without waiting for an OpenAI usage limit.

    `deep codex --yolo` (the `deep` command) and `codex-router deepseek --yolo`
    both come here. A non-interactive `deep exec ...` keeps the DeepSeek profile
    instead of falling back to the plain Codex binary, because the bypass branch in
    `run_codex` would otherwise drop the forced provider.
    """
    ensure_dirs()
    args = remove_leading_codex_token(args)
    blocked = enforce_limit("deepseek")
    if blocked is not None:
        return blocked
    key = keychain_key()
    if not key:
        print("[Codex Router] DeepSeek key missing. Run: codex-router key set", file=sys.stderr)
        log("failure", provider="deepseek", failure_type="missing_key")
        return 78
    if not sys.stdin.isatty():
        env = os.environ.copy()
        env.update(DEEPSEEK_API_KEY=key, CODEX_ROUTER_BYPASS="1")
        snapshot = rollout_snapshot()
        code = subprocess.call([REAL_CODEX, *deepseek_args(args)], env=env)
        record_session_spend("deepseek", fallback_model(), snapshot)
        log("session_spend", provider="deepseek", model=fallback_model(), exit_code=code)
        return code
    os.environ["FORCE_DEEPSEEK"] = "1"
    return run_codex(args)


def classify_deepseek_error(output: str) -> str:
    if BILLING_RE.search(output): return "billing"
    if DEEPSEEK_QUOTA_RE.search(output): return "quota"
    if AUTH_RE.search(output): return "auth"
    if NETWORK_RE.search(output): return "network"
    if SERVER_RE.search(output): return "server"
    return "unknown"


def report_deepseek_error(output: str, exit_code: int) -> None:
    kind = classify_deepseek_error(output)
    messages = {
        "billing": ("DeepSeek 잔액이 부족하거나 결제가 필요합니다.", "DeepSeek Platform에서 잔액을 충전한 뒤 `codex-router test deepseek`를 실행해 주세요."),
        "quota": ("DeepSeek 사용량 또는 요청 한도에 도달했습니다.", "잠시 후 다시 시도하거나 DeepSeek Platform의 사용량·한도를 확인해 주세요."),
        "auth": ("DeepSeek API 인증에 실패했습니다.", "API Key를 확인하고 필요하면 `codex-router key set`으로 다시 저장해 주세요."),
        "network": ("DeepSeek 서버에 연결하지 못했습니다.", "인터넷·DNS 상태를 확인한 뒤 다시 시도해 주세요."),
        "server": ("DeepSeek 서비스가 일시적으로 응답하지 않습니다.", "잠시 후 다시 시도해 주세요."),
        "unknown": ("DeepSeek 요청을 완료하지 못했습니다.", "`codex-router logs`와 위의 Codex 오류를 확인한 뒤 다시 시도해 주세요."),
    }
    title, action = messages[kind]
    print(f"\n[Codex Router] DeepSeek 오류: {title}\n[Codex Router] 해결 방법: {action}\n[Codex Router] 작업 파일과 Codex 대화 기록은 삭제되지 않았습니다.", file=sys.stderr)
    log("failure", provider="deepseek", model=fallback_model(), failure_type=kind, exit_code=exit_code)


def sync_window_size(source_fd: int, target_fd: int) -> bool:
    """Copy terminal rows/columns from the real TTY to the child PTY."""
    try:
        size = fcntl.ioctl(source_fd, termios.TIOCGWINSZ, struct.pack("HHHH", 0, 0, 0, 0))
        fcntl.ioctl(target_fd, termios.TIOCSWINSZ, size)
        return True
    except OSError:
        return False


def run_pty(argv: list[str], env: dict[str, str], watch_usage: bool) -> tuple[int, str | None, str]:
    pid, fd = os.forkpty()
    if pid == 0:
        os.execve(argv[0], argv, env)
    tty_fd = None
    if sys.stdin.isatty():
        stdin_fd = sys.stdin.fileno()
    else:
        try:
            tty_fd = os.open("/dev/tty", os.O_RDWR)
            stdin_fd = tty_fd
        except OSError:
            stdin_fd = -1
    old = termios.tcgetattr(stdin_fd) if stdin_fd >= 0 and os.isatty(stdin_fd) else None
    previous_winch = signal.getsignal(signal.SIGWINCH)
    if old:
        sync_window_size(stdin_fd, fd)
        signal.signal(signal.SIGWINCH, lambda _signum, _frame: sync_window_size(stdin_fd, fd))
    if old:
        tty.setraw(stdin_fd)
    detected = None
    tail = ""
    try:
        while True:
            sources = [fd] + ([stdin_fd] if stdin_fd >= 0 else [])
            readable, _, _ = select.select(sources, [], [], 0.25)
            if fd in readable:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    data = b""
                if not data:
                    break
                os.write(sys.stdout.fileno(), data)
                clean = ANSI_RE.sub(b"", data).decode(errors="ignore")
                tail = (tail + clean)[-12000:]
                if watch_usage and USAGE_RE.search(tail):
                    detected = "usage_limit"
                    os.kill(pid, signal.SIGINT)
                    watch_usage = False
            if stdin_fd >= 0 and stdin_fd in readable:
                data = os.read(stdin_fd, 4096)
                if data:
                    # stdin is in raw mode, so the terminal does not generate
                    # SIGINT for Ctrl-C. Handle it here instead of relying on
                    # the child TUI to interpret the byte.
                    if b"\x03" in data:
                        try:
                            os.killpg(pid, signal.SIGINT)
                        except ProcessLookupError:
                            pass
                        watch_usage = False
                        remaining = data.replace(b"\x03", b"")
                        if remaining:
                            os.write(fd, remaining)
                    else:
                        os.write(fd, data)
        _, status = os.waitpid(pid, 0)
        return os.waitstatus_to_exitcode(status), detected, tail
    finally:
        if old:
            signal.signal(signal.SIGWINCH, previous_winch)
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old)
        if tty_fd is not None:
            os.close(tty_fd)


def run_codex(args: list[str]) -> int:
    ensure_dirs()
    if os.environ.get("CODEX_ROUTER_BYPASS") == "1":
        return subprocess.call([REAL_CODEX, *args])
    if not sys.stdin.isatty():
        if os.environ.get("FORCE_DEEPSEEK") == "1":
            return run_codex_deepseek(args)
        return subprocess.call([REAL_CODEX, *args])
    check_model_catalog(force=False, announce=True)
    provider = select_provider()
    forced = os.environ.get("FORCE_DEEPSEEK") == "1"
    blocked = enforce_limit(provider)
    if blocked is not None:
        return blocked
    if provider == "deepseek" and not forced and not confirm_fallback("이전에 OpenAI 사용 한도가 감지되었습니다"):
        provider = "openai"
    if provider == "deepseek" and not keychain_key():
        print("[Codex Router] DeepSeek key missing. Run: codex-router key set", file=sys.stderr)
        log("failure", provider="deepseek", failure_type="missing_key")
        return 78
    env = os.environ.copy()
    env["CODEX_ROUTER_BYPASS"] = "1"
    if provider == "deepseek":
        env["DEEPSEEK_API_KEY"] = keychain_key() or ""
        if not forced:
            state = load_state(); state["state"] = "DEEPSEEK_ACTIVE"; save_state(state)
        argv = [REAL_CODEX, *deepseek_args(args)]
        trigger = "OpenAI usage limit" if load_state().get("state") == "OPENAI_COOLDOWN" else "requested"
        summary, notice = session_balance()
        banner = f"[Codex Router] Provider: DeepSeek | Model: {fallback_model()} | Reasoning: {reasoning_effort()} | {trigger}"
        print(banner + (f" | Balance: {summary}" if summary else ""), file=sys.stderr)
        if notice:
            print(f"[Codex Router] {notice}", file=sys.stderr)
    else:
        argv = [REAL_CODEX, *args]
        print("[Codex Router] Provider: OpenAI | ChatGPT login", file=sys.stderr)
    log("session_start", provider=provider, model=fallback_model() if provider == "deepseek" else "configured-default", reasoning=reasoning_effort() if provider == "deepseek" else None)
    snapshot = rollout_snapshot()
    openai_started_at = now() if provider == "openai" else None
    code, detected, output_tail = run_pty(argv, env, provider == "openai")
    if provider == "deepseek":
        record_session_spend("deepseek", fallback_model(), snapshot, output_tail)
        log("session_spend", provider="deepseek", model=fallback_model(), exit_code=code)
    fell_back = False
    if detected == "usage_limit":
        checkpoint(detected)
        if not confirm_fallback("OpenAI 사용 한도가 감지되었습니다"):
            print("\n[Codex Router] DeepSeek 전환을 건너뜁니다.", file=sys.stderr)
            log("fallback_declined", reason="usage_limit")
        else:
            cooldown(detected, output_tail)
            blocked = enforce_limit("deepseek")
            if blocked is not None:
                return blocked
            key = keychain_key()
            if not key:
                print("\n[Codex Router] Usage limit detected; DeepSeek key is not configured. Run: codex-router key set", file=sys.stderr)
                return 78
            summary, notice = session_balance()
            details = f"{fallback_model()}, {reasoning_effort()}" + (f", 잔액 {summary}" if summary else "")
            print(f"\n[Codex Router] Usage limit detected. Continuing with DeepSeek ({details})...", file=sys.stderr)
            env["DEEPSEEK_API_KEY"] = key
            if notice:
                print(f"[Codex Router] {notice}", file=sys.stderr)
            fell_back = True
            snapshot = rollout_snapshot()
            session_id = None
            if openai_started_at is not None:
                session_id = find_recent_session_id(openai_started_at, cwd=pathlib.Path.cwd())
            if session_id:
                print(f"\n[Codex Router] 방금 대화(세션 {session_id[:8]}…)를 DeepSeek로 이어갑니다.", file=sys.stderr)
            else:
                print("\n[Codex Router] 방금 끝난 OpenAI 세션 id를 찾지 못했습니다. "
                      "세션 선택창에서 같은 대화를 선택해 주세요.", file=sys.stderr)
            log("fallback_resume", provider="deepseek", session_id=session_id)
            code, _, output_tail = run_pty(
                [REAL_CODEX, *deepseek_args(args, resume=True, session_id=session_id)],
                env, False)
            record_session_spend("deepseek", fallback_model(), snapshot, output_tail)
            log("session_spend", provider="deepseek", model=fallback_model(), exit_code=code)
            if code != 0:
                report_deepseek_error(output_tail, code)
    elif provider == "deepseek" and code != 0:
        report_deepseek_error(output_tail, code)
    elif provider == "openai" and code == 0:
        mark_openai_active("OpenAI session completed successfully")
    if provider == "deepseek" or fell_back:
        report_session_balance()
    log("session_end", provider="deepseek" if fell_back else provider, exit_code=code)
    return code


def selected_model_args(model: model_router.Model, effort: str, prompt: str) -> tuple[list[str], dict[str, str]]:
    env = os.environ.copy()
    env["CODEX_ROUTER_BYPASS"] = "1"
    if model.provider == "deepseek":
        key = keychain_key()
        if not key:
            raise RuntimeError("deepseek_key_missing")
        env["DEEPSEEK_API_KEY"] = key
        args = ["--profile", "deepseek", "--model", model.model_id,
                "-c", f'model_reasoning_effort="{effort}"', prompt]
    else:
        args = ["--model", model.model_id, "-c", f'model_reasoning_effort="{effort}"', prompt]
    return [REAL_CODEX, *args], env


def _run_selected_model(prompt: str, model: model_router.Model, effort: str) -> tuple[int, str | None, str]:
    try:
        argv, env = selected_model_args(model, effort, prompt)
    except RuntimeError:
        print("[AI Router] DeepSeek API Key가 없습니다. `codex-router key set`을 실행해 주세요.", file=sys.stderr)
        return 78, "deepseek_auth", "DeepSeek key missing"
    print(f"[AI Router] 실행: {model.display_name} | {model.provider} | Reasoning: {effort}", file=sys.stderr)
    log("ai_session_start", provider=model.provider, model=model.model_id, reasoning=effort)
    code, detected, tail = run_pty(argv, env, model.provider == "openai")
    log("ai_session_end", provider=model.provider, model=model.model_id, reasoning=effort,
        exit_code=code, detected=detected)
    return code, detected, tail


def parse_task_tokens(output: str) -> tuple[int | None, int | None]:
    match = re.search(r"Token usage:\s*total=[\d,]+\s+input=([\d,]+)(?:\s+\(\+\s*[\d,]+\s+cached\))?\s+output=([\d,]+)", output, re.I)
    if not match:
        return None, None
    return tuple(int(value.replace(",", "")) for value in match.groups())


def _mark_runtime_failure(provider: str, detected: str | None, tail: str) -> str:
    if provider == "openai" and detected == "usage_limit":
        limit = openai_limit_type(tail)
        checkpoint("usage_limit")
        cooldown("usage_limit", tail)
        model_router.update_provider("openai", limit, "OpenAI usage limit detected by Codex",
                                     reset_at=explicit_reset_at(tail), source="codex-runtime")
        return "PROVIDER_AVAILABILITY_FAILURE"
    if provider == "deepseek":
        kind = classify_deepseek_error(tail)
        if kind == "billing":
            model_router.update_provider("deepseek", "BALANCE_EXHAUSTED", "DeepSeek HTTP 402 / insufficient balance",
                                         source="codex-runtime")
            return "PROVIDER_AVAILABILITY_FAILURE"
        if kind == "auth":
            model_router.update_provider("deepseek", "AUTH_ERROR", "DeepSeek authentication failed",
                                         source="codex-runtime")
            return "PROVIDER_AVAILABILITY_FAILURE"
        if kind == "quota":
            retry = now() + dt.timedelta(minutes=int(config()["provider_cache"]["rate_limit_minutes"]))
            model_router.update_provider("deepseek", "RATE_LIMITED", "DeepSeek HTTP 429 / rate limited",
                                         next_check_at=iso(retry), source="codex-runtime")
            return "TRANSIENT_INFRA_FAILURE"
        if kind in {"network", "server"}:
            model_router.update_provider("deepseek", "SERVER_ERROR", f"DeepSeek {kind} error",
                                         source="codex-runtime")
            return "TRANSIENT_INFRA_FAILURE"
    return "MODEL_CAPABILITY_FAILURE"


def execute_ai(prompt: str, selected: model_router.Model, effort: str,
               decision: model_router.Decision, statuses: dict) -> int:
    if selected.provider == "deepseek":
        blocked = enforce_limit("deepseek")
        if blocked is not None:
            return blocked
    code, detected, tail = _run_selected_model(prompt, selected, effort)
    provider_failure = (selected.provider == "openai" and detected == "usage_limit") or (
        selected.provider == "deepseek" and code != 0 and classify_deepseek_error(tail) != "unknown")
    if code == 0 and not detected:
        model_router.update_provider(selected.provider, "AVAILABLE", "Codex task completed",
                                     source="codex-runtime")
        model_router.clear_capability_failure(selected.key, decision.task_type)
        task_input, task_output = parse_task_tokens(tail)
        model_router.record_usage(prompt, decision, selected, "success",
                                  task_input_tokens=task_input, task_output_tokens=task_output)
        return 0
    if code in {130, -signal.SIGINT} and not detected:
        model_router.record_usage(prompt, decision, selected, "interrupted")
        return code
    failure_type = _mark_runtime_failure(selected.provider, detected, tail)
    if selected.provider == "deepseek" and code != 0:
        report_deepseek_error(tail, code)
    if provider_failure:
        refreshed = model_router.load_provider_status()
        fallback = model_router.route(prompt, model_router.repository_context(), refreshed, model_router.load_models())
        models = model_router.load_models()
        if fallback.recommended_key and models[fallback.recommended_key].provider != selected.provider:
            print(f"\n[AI Router] {failure_type}: {selected.provider} Provider를 현재 사용할 수 없습니다.", file=sys.stderr)
            print(model_router.decision_text(fallback, models, refreshed))
            automatic = bool(config()["model_router"]["auto_provider_failover"])
            if automatic:
                next_model, next_effort = models[fallback.recommended_key], fallback.reasoning
            else:
                next_model, next_effort = model_router.choose_interactively(fallback, models, refreshed)
            if next_model and next_effort:
                retry_code, retry_detected, retry_tail = _run_selected_model(prompt, next_model, next_effort)
                if retry_code == 0 and not retry_detected:
                    model_router.update_provider(next_model.provider, "AVAILABLE", "Failover task completed",
                                                 source="codex-runtime")
                    task_input, task_output = parse_task_tokens(retry_tail)
                    model_router.record_usage(prompt, fallback, next_model, "success", provider_failover=True,
                                              task_input_tokens=task_input, task_output_tokens=task_output)
                    return 0
                _mark_runtime_failure(next_model.provider, retry_detected, retry_tail)
                model_router.record_usage(prompt, fallback, next_model, "failed", provider_failover=True)
                return retry_code
        print("[AI Router] 현재 사용할 수 있는 대체 Provider가 없습니다.", file=sys.stderr)
    if failure_type == "MODEL_CAPABILITY_FAILURE" and code != 0:
        count = model_router.record_capability_failure(selected.key, decision.task_type)
        models = model_router.load_models()
        candidates = model_router.candidate_keys(models, model_router.load_provider_status())
        escalation = model_router.escalation_candidate(selected.key, decision.scores, candidates)
        if count >= 2 and escalation:
            escalated = models[escalation]
            escalated_effort = model_router.reasoning_for(decision.scores, escalated)
            print(f"[AI Router] 동일 작업 유형의 capability failure가 {count}회 기록되었습니다.", file=sys.stderr)
            print(f"[AI Router] 권장 escalation: {escalated.display_name} / {escalated_effort}", file=sys.stderr)
            automatic = bool(config()["model_router"]["auto_escalation"])
            answer = "y" if automatic else model_router._tty_input("Escalation을 실행할까요? [Y/n]: ").strip().lower()
            if answer in {"", "y", "yes"}:
                retry_code, retry_detected, retry_tail = _run_selected_model(prompt, escalated, escalated_effort)
                if retry_code == 0 and not retry_detected:
                    model_router.clear_capability_failure(selected.key, decision.task_type)
                    task_input, task_output = parse_task_tokens(retry_tail)
                    model_router.record_usage(prompt, decision, escalated, "success", escalated=True,
                                              task_input_tokens=task_input, task_output_tokens=task_output)
                    return 0
                _mark_runtime_failure(escalated.provider, retry_detected, retry_tail)
                model_router.record_usage(prompt, decision, escalated, "failed", escalated=True)
                return retry_code
    model_router.record_usage(prompt, decision, selected, "failed")
    return code


def status() -> int:
    state = load_state()
    try:
        primary_model = tomllib.loads((pathlib.Path.home()/".codex/config.toml").read_text()).get("model", "Codex default")
    except (OSError, tomllib.TOMLDecodeError):
        primary_model = "Codex default"
    active = "OpenAI" if state.get("state") == "OPENAI_ACTIVE" else "DeepSeek"
    updates = ", ".join(state.get("new_deepseek_models", [])) or "None"
    print(f"Primary: OpenAI / {primary_model}\nFallback: DeepSeek / {fallback_model()}\n"
          f"Reasoning: {reasoning_effort()}\nActive Provider: {active}\n"
          f"OpenAI Status: {state.get('state', 'OPENAI_ACTIVE')}\n"
          f"Cooldown: {'ON' if active == 'DeepSeek' else 'OFF'}\n"
          f"Next Probe: {state.get('next_probe_at', '-')}\n"
          f"DeepSeek Key: {'Configured' if keychain_key() else 'Not Configured'}\n"
          f"New DeepSeek Models: {updates}\n"
          f"Last Catalog Check: {state.get('last_catalog_check_at', '-')}")
    entry = model_router.load_provider_status().get("deepseek", {})
    balance = entry.get("balance")
    if isinstance(balance, dict):
        print(f"DeepSeek Balance: {balance.get('currency', 'USD')} {float(balance['total']):.2f} "
              f"(checked {entry.get('balance_checked_at', '-')})")
    limits, totals = cost_limits(), spend_totals()
    daily = "unlimited" if limits["daily"] is None else f"USD {limits['daily']:.2f}"
    monthly = "unlimited" if limits["monthly"] is None else f"USD {limits['monthly']:.2f}"
    print(f"DeepSeek Spend Today: USD {totals['today']:.2f} / {daily}\n"
          f"DeepSeek Spend This Month: USD {totals['month']:.2f} / {monthly}")
    notice = low_balance_notice(entry)
    if notice:
        print(notice)
    return 0


def low_balance_notice(entry: dict | None = None) -> str | None:
    """Warning line built from the cached provider status; makes no network call."""
    if entry is None:
        entry = model_router.load_provider_status().get("deepseek", {})
    balance = entry.get("balance")
    if not entry.get("low_balance") or not isinstance(balance, dict):
        return None
    currency = balance.get("currency", "USD")
    message = model_router.low_balance_message(currency, entry.get("low_balance_threshold"))
    return f"{message} (현재 {currency} {float(balance['total']):.2f})"


def refresh_balance_cache(max_age_minutes: float) -> None:
    """Refresh a stale balance in a CHILD process, never in this one.

    `run_pty` forks a PTY for the Codex session, and on macOS an HTTPS request leaves
    resolver threads behind; forking a multi-threaded process is deadlock-prone and
    Python warns about it. So the lookup runs as `codex_router.py balance --json`,
    which writes the cache this process then reads. A failure is silent: the session
    starts with whatever the cache already holds.
    """
    entry = model_router.load_provider_status().get("deepseek", {})
    checked = entry.get("balance_checked_at")
    if checked and isinstance(entry.get("balance"), dict):
        try:
            if dt.datetime.fromisoformat(checked) > now() - dt.timedelta(minutes=float(max_age_minutes)):
                return
        except (TypeError, ValueError):
            pass
    try:
        subprocess.run([sys.executable, str(pathlib.Path(__file__).resolve()), "balance", "--json"],
                       capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def balance_summary(entry: dict) -> str | None:
    """`USD 2.79` for a cached DeepSeek entry, or None when no amount is cached."""
    balance = entry.get("balance") if isinstance(entry, dict) else None
    if not isinstance(balance, dict):
        return None
    try:
        return f"{balance.get('currency', 'USD')} {float(balance['total']):.2f}"
    except (KeyError, TypeError, ValueError):
        return None


def session_balance() -> tuple[str | None, str | None]:
    """(balance summary, low-balance warning) for a DeepSeek session start, from cache."""
    try:
        refresh_balance_cache(config()["provider_cache"]["ttl_minutes"])
    except (KeyError, TypeError, ValueError):
        pass
    entry = model_router.load_provider_status().get("deepseek", {})
    return balance_summary(entry), low_balance_notice(entry)


def report_session_balance() -> None:
    """Fresh balance after a DeepSeek session so the remaining amount is visible on exit."""
    result = model_router.fetch_deepseek_balance(timeout=5.0)
    if not result.get("ok"):
        return
    threshold = low_balance_usd()
    model_router.record_balance(result, threshold)
    entry = {"balance": result.get("balance"),
             "low_balance": model_router.is_low_balance(result, threshold),
             "low_balance_threshold": threshold}
    summary = balance_summary(entry)
    if summary:
        print(f"[Codex Router] DeepSeek 잔액: {summary}", file=sys.stderr)
    notice = low_balance_notice(entry)
    if notice:
        print(f"[Codex Router] {notice}", file=sys.stderr)


def balance_threshold_command(value: str | None) -> int:
    if value is None:
        print(f"Low balance warning: USD {low_balance_usd():.2f}")
        return 0
    try:
        amount = float(value)
    except (TypeError, ValueError):
        print(f"Unsupported amount: {value}. Use a number of USD, e.g. 1 or 2.5", file=sys.stderr)
        return 2
    if amount < 0:
        print(f"Unsupported amount: {value}. Use 0 or more.", file=sys.stderr)
        return 2
    cfg = config()
    cfg["cost"]["low_balance_usd"] = amount
    write_config(cfg)
    print(f"Low balance warning: USD {amount:.2f}")
    return 0


def balance_command(args: list[str]) -> int:
    """Show the remaining DeepSeek balance, or read/set the warning threshold."""
    if args and args[0] == "threshold":
        return balance_threshold_command(args[1] if len(args) > 1 else None)
    if [arg for arg in args if arg != "--json"]:
        print("Usage: codex-router balance [--json] | codex-router balance threshold [AMOUNT]", file=sys.stderr)
        return 2
    return model_router.balance_report(args, low_balance_usd())


def model_command(value: str | None) -> int:
    aliases, available = model_aliases(), catalog_models()
    if value is None:
        print("DeepSeek fallback models")
        for alias in ("flash", "pro", "vision"):
            note = " (image-capable current Flash)" if alias == "vision" else ""
            print(f"  {alias:6} -> {aliases[alias]}{note}")
        print(f"Current: {fallback_model()}")
        return 0
    chosen = aliases.get(value, value)
    if chosen not in available:
        print(f"Unsupported model: {value}. Catalog: {', '.join(available)}", file=sys.stderr); return 2
    cfg = config(); cfg["fallback"]["model"] = chosen; write_config(cfg)
    print(f"DeepSeek fallback model: {chosen}"); return 0


def models_command(action: str | None) -> int:
    if action in (None, "list"):
        state = load_state()
        new = set(state.get("new_deepseek_models", []))
        print("Installed DeepSeek models")
        for model in catalog_models():
            labels = []
            if model == fallback_model(): labels.append("current")
            if model in new: labels.append("new")
            suffix = f" ({', '.join(labels)})" if labels else ""
            print(f"  {model}{suffix}")
        print(f"Last check: {state.get('last_catalog_check_at', '-')}")
        return 0
    if action in ("check", "refresh"):
        result, new = check_model_catalog(force=True, announce=True)
        print(f"Catalog: {result}")
        if new: print(f"New models: {', '.join(new)}")
        return 1 if result == "failed" else 0
    print("Usage: codex-router models {list|check|refresh}", file=sys.stderr); return 2


def reasoning_command(value: str | None) -> int:
    supported = ("low", "high", "max")
    if value is None:
        print(f"DeepSeek reasoning: {reasoning_effort()}\nSupported: {', '.join(supported)}"); return 0
    if value not in supported:
        print(f"Unsupported reasoning: {value}. Use: {', '.join(supported)}", file=sys.stderr); return 2
    cfg = config(); cfg["fallback"]["reasoning_effort"] = value; write_config(cfg)
    print(f"DeepSeek reasoning: {value}"); return 0


def doctor() -> int:
    checks = {"real_codex": pathlib.Path(REAL_CODEX).exists(), "chatgpt_login": False,
              "deepseek_profile": (pathlib.Path.home()/".codex/deepseek.config.toml").exists(),
              "deepseek_key": bool(keychain_key()), "router_on_path": shutil.which("codex") == str(pathlib.Path.home()/".local/bin/codex")}
    login = subprocess.run([REAL_CODEX, "login", "status"], text=True, capture_output=True)
    checks["chatgpt_login"] = login.returncode == 0 and "ChatGPT" in login.stdout + login.stderr
    for name, ok in checks.items(): print(f"{'PASS' if ok else 'FAIL'} {name}")
    return 0 if all(checks.values()) else 1


def test(name: str) -> int:
    if name == "checkpoint":
        print(checkpoint("simulation")); return 0
    if name == "fallback":
        cooldown("simulation"); print("fallback state simulated"); return 0
    if name == "return":
        save_state({"state":"OPENAI_ACTIVE", "probe_attempt":0, "updated_at":iso()})
        model_router.update_provider("openai", "AVAILABLE", "OpenAI return simulation", source="simulation")
        print("OpenAI return simulated"); return 0
    if name == "openai":
        return 0 if probe_openai() else 1
    if name == "deepseek":
        key = keychain_key()
        if not key: print("DeepSeek key missing. Run: codex-router key set", file=sys.stderr); return 78
        env=os.environ.copy(); env.update(DEEPSEEK_API_KEY=key, CODEX_ROUTER_BYPASS="1")
        return subprocess.call([REAL_CODEX, "-s", "read-only", "-a", "never", *deepseek_args([]),
                                "exec", "--ephemeral", "--skip-git-repo-check", "Reply exactly: DEEPSEEK_OK"], env=env)
    print("tests: openai, deepseek, fallback, return, checkpoint", file=sys.stderr); return 2


def main() -> int:
    ensure_dirs()
    invoked = pathlib.Path(sys.argv[0]).name
    if invoked == "deep":
        return run_codex_deepseek(sys.argv[1:])
    if invoked == "codex":
        return run_codex(sys.argv[1:])
    if invoked == "ai":
        return model_router.cli(sys.argv[1:], execute_ai)
    args = sys.argv[1:]
    if not args or args[0] == "status": return status()
    if args[:2] == ["key", "set"]: return set_key()
    if args[0] == "doctor": return doctor()
    if args[0] == "balance": return balance_command(args[1:])
    if args[0] == "cost": return cost_command(args[1:])
    if args[0] == "model": return model_command(args[1] if len(args) > 1 else None)
    if args[0] == "models": return models_command(args[1] if len(args) > 1 else None)
    if args[0] == "reasoning": return reasoning_command(args[1] if len(args) > 1 else None)
    if args[0] in ("deep", "deepseek"): return run_codex_deepseek(args[1:])
    if args[0] == "logs":
        files=sorted(LOG_DIR.glob("*.jsonl")); print("".join(files[-1].read_text().splitlines(True)[-50:]) if files else "No logs", end=""); return 0
    if args[0] == "reset":
        save_state({"state":"OPENAI_ACTIVE", "probe_attempt":0, "updated_at":iso()})
        model_router.update_provider("openai", "AVAILABLE", "Router state reset", source="manual-reset")
        print("Router state reset; Codex auth untouched."); return 0
    if args[0] == "test" and len(args) == 2: return test(args[1])
    if args[0] == "uninstall":
        return subprocess.call([str(BASE / "uninstall.sh")])
    print("Usage: codex-router {status|cost [--json]|balance [--json]|balance threshold [AMOUNT]|deep|deepseek [CODEX_ARGS]|model [flash|pro|vision]|models [list|check|refresh]|reasoning [low|high|max]|doctor|logs|key set|test NAME|reset|uninstall}", file=sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(main())
