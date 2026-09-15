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
    r"|(?:usage not included)|(?:0%\s+left)"
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
                "routing": {"auto_fallback": True, "auto_return": True, "probe_minutes": [10, 20, 30, 60], "max_fallback_minutes": 480, "catalog_check_hours": 24},
                "cost": {"daily_limit_usd": 25.0, "monthly_limit_usd": 100.0},
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
    data = f'''[primary]\nprovider = "openai"\n\n[fallback]\nprovider = "deepseek"\nmodel = "{cfg['fallback']['model']}"\nreasoning_effort = "{cfg['fallback']['reasoning_effort']}"\n\n[routing]\nauto_fallback = {boolean(cfg['routing']['auto_fallback'])}\nauto_return = {boolean(cfg['routing']['auto_return'])}\nprobe_minutes = [{', '.join(str(int(v)) for v in cfg['routing']['probe_minutes'])}]\nmax_fallback_minutes = {int(cfg['routing']['max_fallback_minutes'])}\ncatalog_check_hours = {int(cfg['routing']['catalog_check_hours'])}\n\n[cost]\ndaily_limit_usd = {float(cfg['cost']['daily_limit_usd'])}\nmonthly_limit_usd = {float(cfg['cost']['monthly_limit_usd'])}\n\n[model_router]\nrecommendation = {boolean(cfg['model_router']['recommendation'])}\nauto_model_switch = {boolean(cfg['model_router']['auto_model_switch'])}\nauto_provider_failover = {boolean(cfg['model_router']['auto_provider_failover'])}\nauto_escalation = {boolean(cfg['model_router']['auto_escalation'])}\n\n[classifier]\nenabled = {boolean(cfg['classifier']['enabled'])}\nconfidence_threshold = {float(cfg['classifier']['confidence_threshold'])}\nmax_output_tokens = {int(cfg['classifier']['max_output_tokens'])}\n\n[provider_cache]\nttl_minutes = {int(cfg['provider_cache']['ttl_minutes'])}\nrate_limit_minutes = {int(cfg['provider_cache']['rate_limit_minutes'])}\n'''
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(data); os.chmod(tmp, 0o600); tmp.replace(CONFIG)


def fallback_model() -> str:
    return str(config()["fallback"]["model"])


def reasoning_effort() -> str:
    return str(config()["fallback"]["reasoning_effort"])


def catalog_models() -> list[str]:
    try:
        return [m["slug"] for m in json.loads((BASE / "deepseek-models.json").read_text()).get("models", [])]
    except (OSError, KeyError, json.JSONDecodeError):
        return ["deepseek-flash", "deepseek-v4-pro"]


def parse_official_catalog(script: str) -> dict:
    match = re.search(r"<<'CODEX_MODELS_JSON'\s*\n(.*?)\nCODEX_MODELS_JSON\s*$", script, re.S | re.M)
    if not match:
        raise ValueError("official catalog block not found")
    catalog = json.loads(match.group(1))
    models = catalog.get("models")
    if not isinstance(models, list) or not models or any(not isinstance(m.get("slug"), str) for m in models):
        raise ValueError("official catalog is missing valid model slugs")
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
        if path.exists() and path.read_text() != json.dumps(remote, ensure_ascii=False, indent=2) + "\n":
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
    if not STATE_FILE.exists():
        return {"state": "OPENAI_ACTIVE", "probe_attempt": 0, "updated_at": iso()}
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {"state": "OPENAI_ACTIVE", "probe_attempt": 0, "updated_at": iso()}


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
        save_state({"state": "OPENAI_ACTIVE", "probe_attempt": 0, "updated_at": iso()})
        model_router.update_provider("openai", "AVAILABLE", "OpenAI probe succeeded", source="codex-runtime")
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


def deepseek_args(args: list[str], resume: bool = False) -> list[str]:
    filtered, skip = [], False
    for arg in args:
        if skip:
            skip = False
            continue
        if arg in ("-p", "--profile", "-m", "--model"):
            skip = True
            continue
        filtered.append(arg)
    prefix = ["resume", "--last"] if resume else []
    return [*prefix, "--profile", "deepseek", "--model", fallback_model(),
            "-c", f'model_reasoning_effort="{reasoning_effort()}"', *filtered]


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
    if os.environ.get("CODEX_ROUTER_BYPASS") == "1" or not sys.stdin.isatty():
        return subprocess.call([REAL_CODEX, *args])
    check_model_catalog(force=False, announce=True)
    provider = select_provider()
    if provider == "deepseek" and not keychain_key():
        print("[Codex Router] DeepSeek key missing. Run: codex-router key set", file=sys.stderr)
        log("failure", provider="deepseek", failure_type="missing_key")
        return 78
    env = os.environ.copy()
    env["CODEX_ROUTER_BYPASS"] = "1"
    if provider == "deepseek":
        env["DEEPSEEK_API_KEY"] = keychain_key() or ""
        if os.environ.get("FORCE_DEEPSEEK") != "1":
            state = load_state(); state["state"] = "DEEPSEEK_ACTIVE"; save_state(state)
        argv = [REAL_CODEX, *deepseek_args(args)]
        print(f"[Codex Router] Provider: DeepSeek | Model: {fallback_model()} | Reasoning: {reasoning_effort()} | OpenAI cooldown", file=sys.stderr)
    else:
        argv = [REAL_CODEX, *args]
        print("[Codex Router] Provider: OpenAI | ChatGPT login", file=sys.stderr)
    log("session_start", provider=provider, model=fallback_model() if provider == "deepseek" else "configured-default", reasoning=reasoning_effort() if provider == "deepseek" else None)
    code, detected, output_tail = run_pty(argv, env, provider == "openai")
    if detected == "usage_limit":
        checkpoint(detected)
        cooldown(detected, output_tail)
        key = keychain_key()
        if not key:
            print("\n[Codex Router] Usage limit detected; DeepSeek key is not configured. Run: codex-router key set", file=sys.stderr)
            return 78
        print(f"\n[Codex Router] Usage limit detected. Continuing with DeepSeek ({fallback_model()}, {reasoning_effort()})...", file=sys.stderr)
        env["DEEPSEEK_API_KEY"] = key
        code, _, output_tail = run_pty([REAL_CODEX, *deepseek_args(args, resume=True)], env, False)
        if code != 0:
            report_deepseek_error(output_tail, code)
    elif provider == "deepseek" and code != 0:
        report_deepseek_error(output_tail, code)
    log("session_end", provider="deepseek" if detected else provider, exit_code=code)
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
    return 0


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
    if invoked == "codex":
        return run_codex(sys.argv[1:])
    if invoked == "ai":
        return model_router.cli(sys.argv[1:], execute_ai)
    args = sys.argv[1:]
    if not args or args[0] == "status": return status()
    if args[:2] == ["key", "set"]: return set_key()
    if args[0] == "doctor": return doctor()
    if args[0] == "model": return model_command(args[1] if len(args) > 1 else None)
    if args[0] == "models": return models_command(args[1] if len(args) > 1 else None)
    if args[0] == "reasoning": return reasoning_command(args[1] if len(args) > 1 else None)
    if args[0] == "logs":
        files=sorted(LOG_DIR.glob("*.jsonl")); print("".join(files[-1].read_text().splitlines(True)[-50:]) if files else "No logs", end=""); return 0
    if args[0] == "reset":
        save_state({"state":"OPENAI_ACTIVE", "probe_attempt":0, "updated_at":iso()})
        model_router.update_provider("openai", "AVAILABLE", "Router state reset", source="manual-reset")
        print("Router state reset; Codex auth untouched."); return 0
    if args[0] == "test" and len(args) == 2: return test(args[1])
    if args[0] == "uninstall":
        return subprocess.call([str(BASE / "uninstall.sh")])
    print("Usage: codex-router {status|model [flash|pro|vision]|models [list|check|refresh]|reasoning [low|high|max]|doctor|logs|key set|test NAME|reset|uninstall}", file=sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(main())
