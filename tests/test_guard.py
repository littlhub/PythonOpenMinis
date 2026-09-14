"""沙箱守卫：异常删除 / 敏感信息拦截 + 控制台密码。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from openminis.core import context
from openminis.sandbox.console_auth import ACCESS_COOKIE, access_auth, console_auth
from openminis.sandbox.guard import (
    Guard,
    check_shell_command,
    guard,
    redact_secrets,
    sanitize_outbound,
    scan_delete,
    scan_escape,
    scan_secret_command,
)
from openminis.server import chat_store  # noqa: F401  (db init consistency)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIS_HOME", str(tmp_path))
    context.set_app_context(context.AppContext(data_dir=tmp_path, cache_dir=tmp_path))
    guard.reset()
    console_auth.reset()
    access_auth.reset()
    yield tmp_path
    guard.reset()
    console_auth.reset()
    access_auth.reset()
    context._context = None


OUTSIDE = str(Path.home() / "openminis-guard-outside")


# ---------------------------------------------------------------------------
# 异常删除
# ---------------------------------------------------------------------------
def test_scan_delete_flags_destructive(env):
    risk = scan_delete(f"rm -rf {OUTSIDE}")
    assert risk.risky
    assert any("rm" in r for r in risk.reasons)
    assert OUTSIDE in risk.targets
    assert any("工作区之外" in r for r in risk.reasons)


def test_scan_delete_ignores_plain_commands(env):
    assert not scan_delete("ls -la").risky
    assert not scan_delete("python -m pytest").risky
    assert not scan_delete("cat notes.md").risky


def test_scan_delete_flags_windows_style(env):
    assert scan_delete("del /f /q *.txt").risky
    assert scan_delete("rd /s /q build").risky


def test_check_shell_command_blocks_and_records(env):
    msg = check_shell_command(f"rm -rf {OUTSIDE}", session_id="s1", cwd="C:/work")
    assert msg is not None
    assert "沙箱拦截" in msg
    assert "调用目录" in msg and "C:/work" in msg
    events = guard.events()
    assert len(events) == 1
    event = events[0]
    assert event.family == "delete"
    assert event.status == "blocked"
    assert event.cwd == "C:/work"
    assert event.targets and OUTSIDE in event.targets


def test_allow_once_lets_next_attempt_through(env):
    cmd = f"rm -rf {OUTSIDE}"
    first = check_shell_command(cmd, session_id="s1", cwd="C:/work")
    assert first is not None
    event = guard.events()[0]
    assert guard.allow(event.id, "once") is not None
    # 放行后同一条命令不再被拦（一次性，用掉即恢复拦截）
    assert check_shell_command(cmd, session_id="s1", cwd="C:/work") is None
    assert check_shell_command(cmd, session_id="s1", cwd="C:/work") is not None


def test_allow_always_persists(env):
    cmd = f"rm -rf {OUTSIDE}"
    check_shell_command(cmd, session_id="s1", cwd="C:/work")
    guard.allow(guard.events()[0].id, "always")
    # 换一个 Guard 实例（模拟重启）仍然放行
    fresh = Guard()
    assert fresh.is_allowed(family="delete", session_id="other", targets=[OUTSIDE])


def test_deny_keeps_blocking(env):
    cmd = f"rm -rf {OUTSIDE}"
    check_shell_command(cmd, session_id="s1", cwd="C:/work")
    event = guard.events()[0]
    guard.allow(event.id, "session")
    guard.deny(event.id)
    assert check_shell_command(cmd, session_id="s1", cwd="C:/work") is not None


def test_shell_tool_returns_block_message(env):
    """shell 工具层就要拦住，别让命令真的跑出去。"""
    from openminis.tools.shell_execute_tool import ShellExecuteTool

    tool = ShellExecuteTool()
    result = asyncio.run(
        tool.execute(json.dumps({"command": f"rm -rf {OUTSIDE}"}), session_id="s1")
    )
    assert result.success  # 交给模型的是"解释"，不是崩溃
    assert "沙箱拦截" in result.output
    assert guard.events()[0].family == "delete"


# ---------------------------------------------------------------------------
# 敏感信息
# ---------------------------------------------------------------------------
def test_scan_secret_command_detects_reads(env):
    assert scan_secret_command("cat .env")
    assert scan_secret_command("echo $OPENAI_API_KEY")
    assert scan_secret_command("printenv")
    assert not scan_secret_command("cat README.md")


def test_secret_command_is_blocked(env):
    msg = check_shell_command("cat .env", session_id="s1", cwd="C:/work")
    assert msg is not None
    assert "敏感信息" in msg
    assert guard.events()[0].family == "secret"


def test_redact_secrets_partially_masks(env):
    text = "config: password=supersecret123 and key=sk-abcdefghijklmnopqrst"
    out, hits = redact_secrets(text)
    assert hits
    assert "supersecret123" not in out
    assert "sk-abcdefghijklmnopqrst" not in out
    assert "「已拦截」" in out
    # 部分显示：保留了首尾，方便用户确认是哪一条
    assert "supe…" in out


def test_redact_keeps_plain_mentions(env):
    """只是提到"密码"这种正常文本不该被当成凭据。"""
    text = "用户忘了密码，请引导他重置；password 字段可以为空。"
    out, hits = redact_secrets(text)
    assert out == text
    assert hits == []


def test_redact_keeps_variable_references(env):
    """用户反馈：``api_key = openai_key`` 这类是**读变量**，不该被遮。"""
    for text in (
        "api_key = api_key",
        "api_key = openai_key",
        "api_key = openai_api_key",
        "token = my_token",
        'api_key = os.environ["OPENAI_API_KEY"]',
        "token = <your-token>",
        "secret = None",
        "password: 你的密码",
        "token = changeme",
    ):
        out, hits = redact_secrets(text)
        assert out == text, text
        assert hits == [], text


def test_redact_masks_real_looking_values(env):
    """真凭据照遮，且**只遮一次**（曾经高置信度规则遮完又被通用规则再遮一遍）。"""
    for text in (
        "api_key = sk-abcdefghijklmnopqrstuvwx",
        "password = Xk92!dLm#",
        "token = ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        "password=supersecret123",
    ):
        out, hits = redact_secrets(text)
        assert hits, text
        assert "「已拦截」" in out
        assert "」「已拦截」" not in out  # 双重遮蔽的垃圾串
    assert "abcdefghijklmnopqrstuvwx" not in redact_secrets(
        "api_key = sk-abcdefghijklmnopqrstuvwx"
    )[0]


def test_sanitize_outbound_records_and_masks(env):
    out = sanitize_outbound(
        "把 token=Xk92dLm3q 写进配置", where="frontend", session_id="s1"
    )
    assert "Xk92dLm3q" not in out
    event = guard.events()[0]
    assert event.family == "secret"
    assert event.cwd == "outbound:frontend"


def test_sanitize_message_parts_redacts_tool_results(env):
    from openminis.data.model import LLMMessage
    from openminis.data.model.agent_content_part import Text, ToolResult
    from openminis.sandbox.guard import sanitize_message_parts

    messages = [
        LLMMessage(LLMMessage.Role.USER, "看看配置"),
        LLMMessage(
            LLMMessage.Role.ASSISTANT,
            "",
            content_parts=[
                Text("读到 api_key=sk-abcdefghijklmnopqrst 了"),
                ToolResult(id="t1", name="shell_execute",
                           content="password=supersecret123"),
            ],
        ),
    ]
    changed = sanitize_message_parts(messages, session_id="s1")
    assert changed >= 2
    parts = messages[1].content_parts
    assert "sk-abcdefghijklmnopqrst" not in parts[0].text
    assert "supersecret123" not in parts[1].content
    # 部件类型与其它字段必须保住
    assert isinstance(parts[1], ToolResult)
    assert parts[1].id == "t1" and parts[1].name == "shell_execute"


# ---------------------------------------------------------------------------
# 控制台密码
# ---------------------------------------------------------------------------
def test_console_password_flow(env):
    assert not console_auth.has_password()
    assert not console_auth.status()["locked"]

    ok, err = console_auth.set_password("", "hunter2")
    assert ok and not err
    assert console_auth.has_password()
    assert console_auth.is_unlocked()  # 刚设完不用再解锁

    console_auth.lock()
    status = console_auth.status()
    assert status["locked"] is True
    assert console_auth.unlock("wrong") is None  # 失败返回 None（成功返回令牌）
    assert console_auth.is_unlocked() is False
    assert console_auth.unlock("hunter2")  # 成功返回访问令牌
    assert console_auth.is_unlocked()

    # 改密码要先过当前密码
    console_auth.lock()
    assert console_auth.set_password("bad", "newpass")[0] is False
    assert console_auth.set_password("hunter2", "newpass")[0] is True
    console_auth.lock()
    assert console_auth.unlock("hunter2") is None
    assert console_auth.unlock("newpass")

    # 清空密码 → 不再锁
    assert console_auth.set_password("newpass", "")[0] is True
    assert not console_auth.has_password()
    assert not console_auth.status()["locked"]


def test_console_password_never_stored_plaintext(env):
    console_auth.set_password("", "hunter2")
    raw = (env / "auth_gates.json").read_text(encoding="utf-8")
    assert "hunter2" not in raw


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
def test_guard_api_events_and_allow(env):
    from fastapi.testclient import TestClient

    from openminis.server.main import app

    check_shell_command(f"rm -rf {OUTSIDE}", session_id="s1", cwd="C:/work")
    with TestClient(app) as c:
        body = c.get("/api/guard/events").json()
        assert body["counts"]["delete"] == 1
        event = body["events"][0]
        assert event["familyLabel"] == "异常删除"
        assert event["cwd"] == "C:/work"
        assert set(body["scopes"]) == {"once", "session", "always"}

        allowed = c.post(
            f"/api/guard/events/{event['id']}/allow", json={"scope": "always"}
        )
        assert allowed.status_code == 200
        assert allowed.json()["event"]["status"] == "allowed"

        assert c.post(
            f"/api/guard/events/{event['id']}/allow", json={"scope": "nope"}
        ).status_code == 400
        assert c.post(
            "/api/guard/events/g-missing/allow", json={"scope": "once"}
        ).status_code == 404
        assert c.get("/api/guard/allowlist").json()["allowlist"]["delete"]

        assert c.post("/api/guard/events/clear").json()["ok"]
        assert c.get("/api/guard/events").json()["events"] == []


def test_guard_api_console_endpoints(env):
    from fastapi.testclient import TestClient

    from openminis.server.main import app

    with TestClient(app) as c:
        assert c.get("/api/guard/console/status").json()["hasPassword"] is False
        r = c.post("/api/guard/console/password", json={"password": "hunter2"})
        assert r.status_code == 200 and r.json()["hasPassword"] is True
        assert c.get("/api/guard/console/status").json()["locked"] is False

        c.post("/api/guard/console/lock")
        assert c.get("/api/guard/console/status").json()["locked"] is True
        assert c.post(
            "/api/guard/console/unlock", json={"password": "nope"}
        ).status_code == 403
        assert c.post(
            "/api/guard/console/unlock", json={"password": "hunter2"}
        ).status_code == 200
        assert c.get("/api/guard/console/status").json()["locked"] is False


# ---------------------------------------------------------------------------
# 目录越界（escape）
# ---------------------------------------------------------------------------
def test_scan_escape_blocks_cd_outside(env):
    risk = scan_escape(f"cd {OUTSIDE}")
    assert risk.risky
    assert any("目录越界" in r for r in risk.reasons)
    assert OUTSIDE in risk.targets


def test_scan_escape_blocks_cd_dotdot_beyond_root(env):
    ws = Path(env) / "workspace"
    risk = scan_escape("cd ../../..", cwd=str(ws / "sub" / "dir"))
    assert risk.risky


def test_scan_escape_allows_cd_inside(env):
    ws = Path(env) / "workspace"
    assert not scan_escape("cd sub/dir", cwd=str(ws)).risky
    assert not scan_escape("cd ..", cwd=str(ws / "sub")).risky  # 回到根，不算越界


def test_scan_escape_blocks_read_outside(env):
    risk = scan_escape(f"cat {OUTSIDE}/secret.txt")
    assert risk.risky


def test_scan_escape_blocks_redirect_outside(env):
    risk = scan_escape(f"echo hi > {OUTSIDE}/x.txt")
    assert risk.risky
    assert any("重定向" in r for r in risk.reasons)


def test_scan_escape_ignores_windows_flags(env):
    assert not scan_escape("taskkill /F /PID 123").risky
    assert not scan_escape("del /q tmp.txt").risky or scan_delete("del /q tmp.txt").risky


def test_check_shell_command_escape_records_event(env):
    msg = check_shell_command(f"cd {OUTSIDE}", session_id="s1", cwd="")
    assert msg and "目录越界" in msg
    event = guard.events()[0]
    assert event.family == "escape"
    assert event.status == "blocked"


def test_escape_allow_once_lets_next_through(env):
    cmd = f"cd {OUTSIDE}"
    first = check_shell_command(cmd, session_id="s1", cwd="")
    assert first
    event = guard.events()[0]
    assert guard.allow(event.id, "once")
    assert check_shell_command(cmd, session_id="s1", cwd="") is None


# ---------------------------------------------------------------------------
# 页面访问密码（access）：设了密码就把整个前端挡在门外
# ---------------------------------------------------------------------------
def test_access_gate_off_when_no_password(env):
    """没设密码时闸门完全不起作用（默认行为不变）。"""
    from fastapi.testclient import TestClient

    from openminis.server.main import app

    with TestClient(app) as c:
        assert c.get("/api/health").status_code == 200


def test_access_gate_blocks_api_until_unlocked(env):
    """设了密码且处于锁定态 → /api/* 一律 423，解锁接口本身放行。"""
    from fastapi.testclient import TestClient

    from openminis.server.main import app

    access_auth.set_password("", "hunter2")
    access_auth.lock()

    with TestClient(app) as c:
        blocked = c.get("/api/health")
        assert blocked.status_code == 423
        assert blocked.json().get("locked") is True

        # 解锁接口自己不能被拦，否则用户永远进不来
        assert c.get("/api/guard/access/status").status_code == 200

        # 输对密码 → 拿到令牌 → 之后畅通
        r = c.post("/api/guard/access/unlock", json={"password": "hunter2"})
        assert r.status_code == 200
        assert c.get("/api/health").status_code == 200


def test_access_gate_wrong_password_stays_locked(env):
    from fastapi.testclient import TestClient

    from openminis.server.main import app

    access_auth.set_password("", "hunter2")
    access_auth.lock()

    with TestClient(app) as c:
        assert c.post(
            "/api/guard/access/unlock", json={"password": "nope"}
        ).status_code == 403
        assert c.get("/api/health").status_code == 423


def test_access_gate_rejects_junk_token_and_issues_cookie(env):
    """锁定态下乱给令牌照样 423；解锁后拿到的 cookie 是真令牌。"""
    from fastapi.testclient import TestClient

    from openminis.server.main import app

    access_auth.set_password("", "hunter2")
    access_auth.lock()

    with TestClient(app) as c:
        assert (
            c.get("/api/health", headers={"x-minis-access": "junk"}).status_code == 423
        )
        r = c.post("/api/guard/access/unlock", json={"password": "hunter2"})
        assert r.status_code == 200
        token = r.cookies.get(ACCESS_COOKIE) or c.cookies.get(ACCESS_COOKIE)
        assert token
        # 非浏览器客户端可以不靠 cookie，直接带 x-minis-access 头
        assert (
            c.get("/api/health", headers={"x-minis-access": token}).status_code == 200
        )


def test_access_cookie_name_is_shared_with_gate(env):
    """cookie 名只有一个来源 —— 签发方与校验方失配过（曾导致闸门形同虚设）。"""
    from openminis.server import guard_api

    assert guard_api.ACCESS_COOKIE == ACCESS_COOKIE


def test_ws_rejected_while_locked(env):
    """WS 不走 /api/，闸门要单独管 —— 否则锁屏状态下还能通过 WS 聊天。"""
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from openminis.server.main import app

    access_auth.set_password("", "hunter2")
    access_auth.lock()

    with TestClient(app) as c:
        with pytest.raises(WebSocketDisconnect) as exc:
            with c.websocket_connect("/ws"):
                pass
        assert exc.value.code == 4401


def test_ws_open_when_no_password(env):
    from fastapi.testclient import TestClient

    from openminis.server.main import app

    with TestClient(app) as c:
        with c.websocket_connect("/ws") as ws:
            ws.send_json({"type": "ping"})
            assert ws.receive_json()["type"] == "pong"


def test_scan_escape_ignores_relative_path_fragments(env):
    """相对路径 `scripts/x.py` 里的 `/x.py` 曾被当成绝对路径误报目录越界。"""
    from openminis.tools.path_utils import readonly_roots

    roots = list(readonly_roots())
    skills = (roots[0] if roots else Path(env) / "skills") / "agnes-image"
    risk = scan_escape(f'cd "{skills}" && python scripts/image_generation.py "x"')
    assert not risk.risky, risk.reasons
    assert not scan_escape("python scripts/train.py").risky
    assert not scan_escape("python -m pytest tests/test_guard.py").risky


def test_scan_escape_ignores_dev_null(env):
    """`> /dev/null` 是惯用法，不是「写到工作区外面」。"""
    assert not scan_escape("foo > /dev/null 2>&1").risky
    assert not scan_escape("python app.py > NUL").risky


def test_scan_escape_still_blocks_real_absolutes(env):
    """修误报不能把真越界放掉。"""
    assert scan_escape(f"cd {OUTSIDE}").risky
    assert scan_escape(f'cat "{Path.home() / "Desktop" / "x.txt"}').risky
    assert scan_escape("rm -rf /tmp/xyz").risky
