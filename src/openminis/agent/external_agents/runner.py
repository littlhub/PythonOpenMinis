"""外部 agent 调用器 —— 按描述符把任务送出去，把回传信息带回来。

三条传输都收敛到 :class:`ExternalAgentResult`：调用方只需要「文本 + 成败」，
不关心对端是进程内实现、CLI 子进程，还是 HTTP 服务。

``internal`` 入口的统一契约（写自己的内置 agent 时照这个实现即可）：:

    async def handler(task: str, *, session_id: str, workspace: str,
                      config: dict, provider: Any = None) -> str | ExternalAgentResult

``config`` 是子代理那条配置（可含 ``tools`` / ``skills`` / ``persona`` / ``model``），
``provider`` 由宿主按子代理绑定解析好后传入（外部 CLI agent 一般不需要）。
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .protocol import (
    ExternalAgentDescriptor,
    ExternalAgentError,
    extract_path,
    get_descriptor,
    render_template,
)

__all__ = [
    "ExternalAgentResult",
    "run_external_agent",
    "call_internal_entry",
]


@dataclass(slots=True)
class ExternalAgentResult:
    """一次外部 agent 调用的结果（回传信息）。"""

    text: str
    ok: bool = True
    error: Optional[str] = None
    agent_id: str = ""
    duration_ms: int = 0
    raw: Any = None
    exit_code: Optional[int] = None
    meta: dict[str, Any] = field(default_factory=dict)

    def as_text(self) -> str:
        """委派链路需要的最终文本（失败也必须是可读的一句话）。"""
        if self.ok:
            return self.text or "(外部 agent 未产出文字)"
        reason = self.error or self.text or "未知错误"
        return f"(外部 agent {self.agent_id} 调用失败：{reason})"


# ---------------------------------------------------------------------------
# 统一变量表
# ---------------------------------------------------------------------------
def _variables(
    *,
    task: str,
    session_id: str,
    workspace: str,
    config: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "task": task,
        "session_id": session_id,
        "sessionId": session_id,
        "workspace": workspace,
        "cwd": workspace,
    }
    # 子代理配置里的标量也能进模板（如 {{model}}），数组/对象序列化成 JSON。
    for key, value in (config or {}).items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            values.setdefault(str(key), "" if value is None else value)
        elif isinstance(value, (list, dict)):
            values.setdefault(str(key), json.dumps(value, ensure_ascii=False))
    return values


def _payload(task: str, session_id: str, workspace: str, config: dict[str, Any]) -> dict[str, Any]:
    return {
        "task": task,
        "sessionId": session_id,
        "workspace": workspace,
        "config": config,
    }


# ---------------------------------------------------------------------------
# transport = internal
# ---------------------------------------------------------------------------
async def call_internal_entry(
    descriptor: ExternalAgentDescriptor,
    task: str,
    *,
    session_id: str,
    workspace: str,
    config: dict[str, Any],
    provider: Any = None,
) -> ExternalAgentResult:
    """``entry: module:callable`` → 导入并调用（统一契约见模块 docstring）。"""
    module_name, _, attr = descriptor.entry.partition(":")
    if not module_name or not attr:
        return ExternalAgentResult(
            text="",
            ok=False,
            error=f"entry 需要 '<module>:<callable>' 形态，实际: {descriptor.entry!r}",
            agent_id=descriptor.id,
        )
    try:
        module = importlib.import_module(module_name)
        handler = getattr(module, attr)
    except Exception as exc:  # noqa: BLE001
        return ExternalAgentResult(
            text="",
            ok=False,
            error=f"无法导入 entry {descriptor.entry}: {type(exc).__name__}: {exc}",
            agent_id=descriptor.id,
        )

    started = time.monotonic()
    try:
        outcome = handler(
            task,
            session_id=session_id,
            workspace=workspace,
            config=config,
            provider=provider,
        )
        if asyncio.iscoroutine(outcome):
            outcome = await outcome
    except Exception as exc:  # noqa: BLE001 - 外部 agent 的异常是它的输出之一
        return ExternalAgentResult(
            text="",
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
            agent_id=descriptor.id,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    duration = int((time.monotonic() - started) * 1000)
    if isinstance(outcome, ExternalAgentResult):
        outcome.agent_id = outcome.agent_id or descriptor.id
        outcome.duration_ms = outcome.duration_ms or duration
        return outcome
    if isinstance(outcome, str):
        return ExternalAgentResult(
            text=outcome, ok=True, agent_id=descriptor.id, duration_ms=duration
        )
    if isinstance(outcome, dict):
        text = outcome.get("text") or outcome.get("content") or ""
        return ExternalAgentResult(
            text=str(text),
            ok=bool(outcome.get("ok", True)),
            error=outcome.get("error"),
            agent_id=descriptor.id,
            duration_ms=duration,
            raw=outcome,
        )
    return ExternalAgentResult(
        text=str(outcome or ""), ok=True, agent_id=descriptor.id, duration_ms=duration
    )


# ---------------------------------------------------------------------------
# transport = stdio
# ---------------------------------------------------------------------------
def _resolve_stdio_cwd(descriptor: ExternalAgentDescriptor, workspace: str) -> str:
    mode = (descriptor.cwd_mode or "workspace").strip()
    if mode in ("workspace", "", "."):
        return workspace
    return os.path.expanduser(mode)


def _decode_output(stdout: bytes, descriptor: ExternalAgentDescriptor) -> tuple[Any, list[dict[str, Any]]]:
    text = stdout.decode("utf-8", "replace")
    if descriptor.output_format == "text":
        return text, []
    if descriptor.output_format == "json":
        try:
            return json.loads(text), []
        except json.JSONDecodeError as exc:
            raise ExternalAgentError(f"回传不是合法 JSON：{exc}") from exc
    # json-lines：逐行解析，坏行跳过（CLI agent 常把日志混在 stdout 里）
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            records.append(parsed)
    return records, records


def _pick_text(data: Any, descriptor: ExternalAgentDescriptor, fallback: str) -> str:
    if descriptor.text_path:
        value = extract_path(data, descriptor.text_path)
        if value not in (None, ""):
            return str(value)
    if descriptor.result_path:
        value = extract_path(data, descriptor.result_path)
        if value not in (None, ""):
            return str(value)
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("text", "content", "message", "result", "output", "response"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return json.dumps(data, ensure_ascii=False, indent=1)
    if isinstance(data, list):
        # json-lines：取最后一条记录当结论（CLI 通常把最终结果放最后）。
        if data:
            return _pick_text(data[-1], descriptor, fallback)
        return fallback
    return fallback


def _pick_error(data: Any, descriptor: ExternalAgentDescriptor) -> Optional[str]:
    if not descriptor.error_path:
        return None
    value = extract_path(data, descriptor.error_path)
    if value in (None, "", False):
        return None
    return str(value)


async def _run_stdio(
    descriptor: ExternalAgentDescriptor,
    task: str,
    *,
    session_id: str,
    workspace: str,
    config: dict[str, Any],
) -> ExternalAgentResult:
    started = time.monotonic()
    variables = _variables(task=task, session_id=session_id, workspace=workspace, config=config)
    command = [render_template(part, variables) for part in descriptor.command]
    cwd = _resolve_stdio_cwd(descriptor, workspace)
    env = dict(os.environ)
    env.update({key: render_template(value, variables) for key, value in descriptor.env.items()})

    stdin_bytes: Optional[bytes] = None
    if descriptor.input_mode == "argv-template":
        command.extend(render_template(arg, variables) for arg in descriptor.input_args)
    elif descriptor.input_mode == "stdin-text":
        stdin_bytes = task.encode("utf-8")
    elif descriptor.input_mode == "stdin-json":
        stdin_bytes = json.dumps(
            _payload(task, session_id, workspace, config), ensure_ascii=False
        ).encode("utf-8")

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE if stdin_bytes is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd if os.path.isdir(cwd) else None,
            env=env,
        )
    except FileNotFoundError:
        return ExternalAgentResult(
            text="",
            ok=False,
            error=f"找不到可执行文件: {command[0]}（检查 stdio.command）",
            agent_id=descriptor.id,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    except OSError as exc:
        return ExternalAgentResult(
            text="",
            ok=False,
            error=f"启动子进程失败: {type(exc).__name__}: {exc}",
            agent_id=descriptor.id,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(stdin_bytes), timeout=descriptor.timeout_seconds
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return ExternalAgentResult(
            text="",
            ok=False,
            error=f"超时（{descriptor.timeout_seconds:.0f}s）",
            agent_id=descriptor.id,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    duration = int((time.monotonic() - started) * 1000)
    exit_code = process.returncode
    stderr_text = stderr.decode("utf-8", "replace").strip()
    try:
        data, _records = _decode_output(stdout, descriptor)
    except ExternalAgentError as exc:
        return ExternalAgentResult(
            text=stdout.decode("utf-8", "replace"),
            ok=False,
            error=str(exc),
            agent_id=descriptor.id,
            duration_ms=duration,
            exit_code=exit_code,
        )

    text = _pick_text(data, descriptor, stdout.decode("utf-8", "replace").strip())
    declared_error = _pick_error(data, descriptor)
    if declared_error:
        return ExternalAgentResult(
            text=text, ok=False, error=declared_error, agent_id=descriptor.id,
            duration_ms=duration, exit_code=exit_code, raw=data,
        )
    if exit_code not in (0, None):
        detail = stderr_text or text
        return ExternalAgentResult(
            text=text,
            ok=False,
            error=f"退出码 {exit_code}" + (f": {detail[:400]}" if detail else ""),
            agent_id=descriptor.id,
            duration_ms=duration,
            exit_code=exit_code,
            raw=data,
        )
    return ExternalAgentResult(
        text=text, ok=True, agent_id=descriptor.id, duration_ms=duration,
        exit_code=exit_code, raw=data,
        meta={"stderr": stderr_text} if stderr_text else {},
    )


# ---------------------------------------------------------------------------
# transport = http
# ---------------------------------------------------------------------------
async def _run_http(
    descriptor: ExternalAgentDescriptor,
    task: str,
    *,
    session_id: str,
    workspace: str,
    config: dict[str, Any],
) -> ExternalAgentResult:
    """POST/PUT JSON 到 ``endpoint``，按 ``output`` 解析回传。

    # PORT: 用 stdlib ``urllib`` + 线程池，不引 httpx —— 描述符驱动层要能在
    最简环境（CLI/测试）里也跑得起来。
    """
    started = time.monotonic()
    variables = _variables(task=task, session_id=session_id, workspace=workspace, config=config)
    url = render_template(descriptor.endpoint, variables)
    headers = {"content-type": "application/json"}
    headers.update({key: render_template(v, variables) for key, v in descriptor.headers.items()})
    body = json.dumps(_payload(task, session_id, workspace, config), ensure_ascii=False).encode("utf-8")

    def _post() -> tuple[int, str]:
        import urllib.error
        import urllib.request

        request = urllib.request.Request(url, data=body, method=descriptor.method)
        for key, value in headers.items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=descriptor.timeout_seconds) as response:
                return int(getattr(response, "status", 200)), response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            return int(error.code), (error.read() or b"").decode("utf-8", "replace")
        except OSError as error:
            raise ExternalAgentError(f"{type(error).__name__}: {error}") from None

    try:
        status, raw_text = await asyncio.to_thread(_post)
    except ExternalAgentError as exc:
        return ExternalAgentResult(
            text="", ok=False, error=str(exc), agent_id=descriptor.id,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    duration = int((time.monotonic() - started) * 1000)
    try:
        if descriptor.output_format == "json":
            data: Any = json.loads(raw_text)
        elif descriptor.output_format == "json-lines":
            data = [json.loads(line) for line in raw_text.splitlines() if line.strip()]
        else:
            data = raw_text
    except json.JSONDecodeError as exc:
        return ExternalAgentResult(
            text=raw_text, ok=False, error=f"回传不是合法 JSON：{exc}",
            agent_id=descriptor.id, duration_ms=duration, exit_code=status,
            meta={"status": status},
        )

    text = _pick_text(data, descriptor, raw_text)
    declared_error = _pick_error(data, descriptor)
    ok = 200 <= status < 300 and not declared_error
    return ExternalAgentResult(
        text=text,
        ok=ok,
        error=declared_error or (None if ok else f"HTTP {status}"),
        agent_id=descriptor.id,
        duration_ms=duration,
        exit_code=status,
        raw=data,
        meta={"status": status},
    )


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
async def run_external_agent(
    agent_id: str,
    task: str,
    *,
    session_id: str,
    workspace: str,
    config: Optional[dict[str, Any]] = None,
    provider: Any = None,
) -> ExternalAgentResult:
    """按描述符调用一个外部 agent。"""
    descriptor = get_descriptor(agent_id)
    if descriptor is None:
        return ExternalAgentResult(
            text="", ok=False,
            error=f"未注册的外部 agent: {agent_id}（检查 ~/openminis/agents 下的描述符）",
            agent_id=agent_id,
        )
    if not descriptor.enabled:
        return ExternalAgentResult(
            text="", ok=False, error="该外部 agent 已被描述符禁用（enabled: false）",
            agent_id=agent_id,
        )
    config = dict(config or {})
    if descriptor.transport == "internal":
        return await call_internal_entry(
            descriptor, task, session_id=session_id, workspace=workspace,
            config=config, provider=provider,
        )
    if descriptor.transport == "stdio":
        return await _run_stdio(
            descriptor, task, session_id=session_id, workspace=workspace, config=config
        )
    return await _run_http(
        descriptor, task, session_id=session_id, workspace=workspace, config=config
    )
