"""交付链路的三类现场回归。

来源都是同一轮真实会话（QQ 群里「魔搭生图 → 发过来」）：

1. **发错文件** —— 交付路径是模型从会话历史里的 ``![生成图](…)`` 抄的，会话里
   攒了两张就抄成上一轮那张；工具只检查「文件在不在」，于是 ``ok=True``，
   用户收到的是几小时前的图，模型还以为交付成功了。
2. **同一个 send 连打 14 次** —— 跨轮的同参重复：``seen_in_round`` 每轮清空。
3. **6 分 23 秒里有两分钟是白等** —— 模型给生图命令传 ``timeout: 120``，而魔搭
   实测 90–150 秒，必然被杀、只能重跑；群聊被动回复窗口只有 5 分钟，于是
   结果发不回去（而机器人没有主动消息权限，连兜底都没有）。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from openminis.core import context
from openminis.plugins.base import IncomingMessage
from openminis.tools.send_tool import SendTool, begin_turn, end_turn


def _workspace() -> Path:
    return Path(context.app_context().external_files_dir)


def _make_image(name: str, *, age_sec: float = 0.0) -> Path:
    """在生图目录里造一张图；``age_sec`` > 0 表示「早就生成了」。"""
    out = _workspace() / "image" / "modelscope"
    out.mkdir(parents=True, exist_ok=True)
    path = out / name
    path.write_bytes(b"\xff\xd8\xff\xe0")
    if age_sec:
        old = time.time() - age_sec
        import os

        os.utime(path, (old, old))
    return path


# ---------------------------------------------------------------------------
# 1. 同一轮同一文件只交付一次
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_send_refuses_duplicate_in_same_turn():
    """同一批次里并发重复调用 —— 第二次必须当场拒掉。

    工具调用是 ``asyncio.gather`` 并发跑的，``repeat_guard`` 的历史要等执行完才
    写入，同批次的重复它互相看不见（用户现场被连打 14 次，界面上是一屏
    「执行中…」）。所以去重必须在工具内部、在 ``await`` 之前完成。
    """
    img = _make_image("dup.jpg")
    begin_turn("db-s1")
    try:
        first = await SendTool.execute(
            f'{{"tool_title": "交付", "path": "{img.as_posix()}"}}', "db-s1"
        )
        assert first.success is True
        second = await SendTool.execute(
            f'{{"tool_title": "交付", "path": "{img.as_posix()}"}}', "db-s1"
        )
        assert second.success is False
        assert "已经交付过" in second.output
        assert "不要再重复调用 send" in second.output
    finally:
        end_turn("db-s1")


@pytest.mark.asyncio
async def test_send_allows_same_file_in_a_new_turn():
    """下一轮用户再说「发过来」，同一个文件还要能发。"""
    img = _make_image("again.jpg")
    begin_turn("db-s2")
    try:
        assert (await SendTool.execute(
            f'{{"tool_title": "x", "path": "{img.as_posix()}"}}', "db-s2"
        )).success is True
    finally:
        end_turn("db-s2")
    begin_turn("db-s2")
    try:
        assert (await SendTool.execute(
            f'{{"tool_title": "x", "path": "{img.as_posix()}"}}', "db-s2"
        )).success is True
    finally:
        end_turn("db-s2")


# ---------------------------------------------------------------------------
# 2. 交付了较旧的那张 → 必须当场提醒
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_send_warns_when_an_older_image_is_delivered():
    """现场：用户要刚生成的那张，模型发了上一轮那张（3.7MB 的旧图）。"""
    old = _make_image("old.jpg", age_sec=3600)
    fresh = _make_image("fresh.jpg")
    assert fresh.stat().st_mtime > old.stat().st_mtime

    begin_turn("db-s3")
    try:
        result = await SendTool.execute(
            f'{{"tool_title": "交付", "path": "{old.as_posix()}"}}', "db-s3"
        )
        assert result.success is True
        assert "较旧" in result.output
        # 路径用沙箱写法列出来（模型在别处看到的也是这套）
        assert "/var/minis/workspace/image/modelscope/fresh.jpg" in result.output
    finally:
        end_turn("db-s3")


@pytest.mark.asyncio
async def test_send_does_not_nag_when_delivering_the_newest():
    fresh = _make_image("newest.jpg")
    begin_turn("db-s4")
    try:
        result = await SendTool.execute(
            f'{{"tool_title": "交付", "path": "{fresh.as_posix()}"}}', "db-s4"
        )
        assert result.success is True
        assert "较旧" not in result.output
    finally:
        end_turn("db-s4")


@pytest.mark.asyncio
async def test_send_without_begin_turn_still_works():
    """没有台账（老调用方/子代理路径）时不能因为缺状态就报错。"""
    img = _make_image("noTurn.jpg")
    result = await SendTool.execute(
        f'{{"tool_title": "x", "path": "{img.as_posix()}"}}', "db-unknown"
    )
    assert result.success is True


# ---------------------------------------------------------------------------
# 3. 生图命令的 timeout 自动抬到安全值
# ---------------------------------------------------------------------------
def test_image_command_timeout_is_raised():
    """实测 90–150 秒；给 120 秒 = 必然被杀 → 白等两分钟再重跑。"""
    from openminis.tools import shell_execute_tool as sh

    assert sh._looks_like_image_gen(
        'cd /var/minis/data/skills/modelscope-image && python scripts/image_generation.py "x"'
    )
    assert sh._looks_like_image_gen("python agnes-image/generate.py") is True
    assert sh._looks_like_image_gen("ls -la") is False
    assert sh._IMAGE_CMD_MIN_TIMEOUT >= 300


@pytest.mark.asyncio
async def test_shell_tool_raises_timeout_for_image_gen():
    """走真实工具入口：120 秒被抬到 300，并且把这件事写在输出里。"""
    import json as _json

    from openminis.sandbox.execution_coordinator import CommandResult
    from openminis.tools.shell_execute_tool import ShellExecuteTool, install_coordinator

    seen: list[float] = []

    class Recording:
        def cwd_for(self, session_id: str) -> str:
            return ""

        async def execute(self, session_id, command, timeout=0.0,
                          line_callback=None, env_vars=None):
            seen.append(timeout)
            return CommandResult(output="ok", exit_code=0, duration_ms=0)

    install_coordinator(Recording())  # type: ignore[arg-type]
    tool = ShellExecuteTool()
    result = await tool.execute(
        _json.dumps({
            "tool_title": "生图",
            "command": "python scripts/image_generation.py \"a cat\"",
            "timeout": 120,
        }),
        "s1",
    )
    assert seen and seen[0] >= 300
    assert "已自动把 timeout" in result.output


# ---------------------------------------------------------------------------
# 4. QQ：窗口余量与「主动消息无权限」
# ---------------------------------------------------------------------------
def _adapter():
    from openminis.plugins.drivers.qq import QQAdapter

    return QQAdapter(plugin_id="qq", config={"appId": "1", "clientSecret": "s"})


def test_group_window_margin_keeps_late_replies_passive():
    """4 分 40 秒仍算窗口内 —— 原来 20 秒余量会把它判死，白丢一次机会。

    现场：那张图在用户消息后 4 分 36 秒落盘，距 5 分钟还剩 24 秒，却被判成
    「已过期」→ 改走主动消息 → 「无权限」→ 图丢了。
    """
    from openminis.plugins.drivers.qq import PASSIVE_REPLY_MARGIN

    assert PASSIVE_REPLY_MARGIN <= 15
    adapter = _adapter()
    late = IncomingMessage(scope="group", peer_id="G1", sender_id="U1", text="x",
                           message_id="m1", received_at=time.time() - 280)
    assert adapter._passive_expired(late) is False
    # 真的过了 5 分钟才判死
    gone = IncomingMessage(scope="group", peer_id="G1", sender_id="U1", text="x",
                           message_id="m2", received_at=time.time() - 320)
    assert adapter._passive_expired(gone) is True


@pytest.mark.asyncio
async def test_proactive_permission_denied_is_remembered():
    """「主动消息失败, 无权限」是平台权限问题：记一次、说人话、不再重复撞墙。"""
    from openminis.plugins.drivers.qq import QQBotError

    adapter = _adapter()
    calls: list[dict] = []

    async def fake_api(path, *, method="POST", body=None):
        calls.append(dict(body or {}))
        raise QQBotError("QQ 接口 400：主动消息失败, 无权限", status=400)

    adapter._api = fake_api  # type: ignore[assignment]
    stale = IncomingMessage(scope="group", peer_id="G1", sender_id="U1", text="x",
                            message_id="m1", received_at=time.time() - 320)
    await adapter.send_text(stale, "迟到的结果")
    assert adapter._proactive_blocked("group", "G1") is True
    logs = "".join(str(row["text"]) for row in adapter.logs())
    assert "没有主动消息权限" in logs
    assert "让用户再发一句" in logs

    # 第二次不再调接口（省一次必然失败的往返，也不再刷三条一样的错误）
    before = len(calls)
    await adapter.send_text(stale, "又一条")
    assert len(calls) == before


@pytest.mark.asyncio
async def test_proactive_denied_skips_media_upload_too():
    """附件连上传都省掉 —— 上传本身就要十几秒。"""
    from openminis.plugins.drivers.qq import QQBotError

    adapter = _adapter()
    uploads: list[str] = []

    async def fake_upload(*a, **kw):  # pragma: no cover - 不该被调用
        uploads.append("called")
        raise QQBotError("不该走到这里")

    adapter._upload_media = fake_upload  # type: ignore[assignment]
    adapter._proactive_denied[("group", "G1")] = time.time()
    img = _make_image("blocked.jpg")
    stale = IncomingMessage(scope="group", peer_id="G1", sender_id="U1", text="x",
                            message_id="m1", received_at=time.time() - 320)
    ok = await adapter.send_image(stale, img.as_posix())
    assert ok is False
    assert uploads == []
    logs = "".join(str(row["text"]) for row in adapter.logs())
    assert "没有主动消息权限" in logs
