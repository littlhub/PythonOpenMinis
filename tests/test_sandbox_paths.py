"""[T-sandbox-paths] 出站路径形态：模型只该看到 ``/var/minis/workspace/…``。

用户要求「绝对路径脱敏，不出现 ``C:/Users/loo/``，只出现 ``workspace/``」。
两件事必须同时对，少一件就出 bug：

1. **出站替换**：发给模型的内容里，工作区 / 数据目录 / 用户主目录前缀换成沙箱
   写法（``/var/minis/workspace`` / ``/var/minis/data`` / ``/var/minis/home``）。
2. **回填解析**：模型把 ``/var/minis/workspace/uploads/x.jpg`` 原样给回工具时，
   必须解析回**工作区根**下的真实文件 —— 以前它被当成「会话相对」，
   于是去 ``workspace/<sid>/uploads/`` 里找，必然找不到（现场症状：
   「图生成了、路径也告诉它了，它却读不到」）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openminis.core import context
from openminis.data.model import LLMMessage
from openminis.data.model.agent_content_part import Text
from openminis.sandbox.guard import sanitize_message_parts, sanitize_outbound
from openminis.tools.file_read_tool import _resolve_session_host_path as read_resolver
from openminis.tools.file_write_tool import _resolve_session_host_path as write_resolver
from openminis.tools.path_utils import resolve_workspace_path, scrub_machine_paths


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    home = tmp_path / "openminis"
    home.mkdir()
    monkeypatch.setenv("MINIS_HOME", str(home))
    context.set_app_context(context.AppContext(data_dir=home, cache_dir=home))
    ws = Path(context.app_context().external_files_dir)
    (ws / "uploads").mkdir(parents=True, exist_ok=True)
    return ws


def test_scrub_replaces_workspace_prefix(workspace):
    raw = f"![图]({(workspace / 'image' / 'a.png').as_posix()})"
    out = scrub_machine_paths(raw)
    assert out == "![图](/var/minis/workspace/image/a.png)"
    assert "C:" not in out and str(Path.home()) not in out


def test_scrub_handles_backslashes(workspace):
    raw = "存到了 " + str(workspace / "image" / "b.png")
    out = scrub_machine_paths(raw)
    assert out == "存到了 /var/minis/workspace/image/b.png"


def test_scrub_covers_data_and_home(workspace):
    assert scrub_machine_paths(
        f"配置在 {context.app_context().data_dir / 'settings.json'}"
    ).endswith("/var/minis/data/settings.json")
    assert scrub_machine_paths(
        str(Path.home() / "Desktop" / "x.txt")
    ) == "/var/minis/home/Desktop/x.txt"


def test_scrub_leaves_unrelated_text_alone():
    """别把普通句子咬掉半截 —— 收紧口子比多替一点重要。"""
    text = "C 盘和 /usr/local 都不是我们管的路径"
    assert scrub_machine_paths(text) == text
    assert scrub_machine_paths("") == ""


def test_sandbox_prefix_maps_to_workspace_root(workspace):
    """``/var/minis/workspace/…`` = 工作区根，不是会话根。"""
    target = workspace / "uploads" / "a.jpg"
    target.write_bytes(b"x")
    for resolver in (read_resolver, write_resolver):
        assert resolver("sess-1", "/var/minis/workspace/uploads/a.jpg") == target.resolve()
    # path_utils 那套按工作区根解析（签名只收 path）
    assert resolve_workspace_path("/var/minis/workspace/uploads/a.jpg") == target.resolve()
    # 会话相对的老行为不受影响
    assert read_resolver("sess-1", "notes/a.txt") == (
        workspace / "sess-1" / "notes" / "a.txt"
    ).resolve()


def test_read_image_round_trip(workspace):
    """出站看到的路径，回填给 read_image 必须能解析回来（完整闭环）。"""
    img = workspace / "uploads" / "20260918-073217-qq-dd1bf336-A39C8122FF85B3B700719B9836EA6C4D.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 40)

    msg = LLMMessage(
        LLMMessage.Role.USER, "", content_parts=[Text(f"![{img.name}]({img.as_posix()})")]
    )
    sanitize_message_parts([msg], session_id="s1")
    seen = msg.content_parts[0].text

    assert "C:" not in seen
    assert "已拦截" not in seen          # 不能被当成密钥遮掉（用户踩过）
    assert str(Path.home()) not in seen
    sandbox_path = seen.split("](")[1].rstrip(")")
    assert sandbox_path.startswith("/var/minis/workspace/")
    assert read_resolver("s1", sandbox_path) == img.resolve()


def test_frontend_payloads_keep_real_paths(workspace):
    """前端要拿真实路径去 /api/fs/raw 取图 —— 不能给它换沙箱写法。"""
    raw = (workspace / "uploads" / "a.jpg").as_posix()
    out = sanitize_outbound(raw, where="frontend")
    assert out == raw

    out_llm = sanitize_outbound(raw, where="llm", scrub_paths=True)
    assert out_llm.startswith("/var/minis/workspace/")


def test_sandbox_path_is_idempotent(workspace):
    """已经是沙箱写法就不要再动它。"""
    text = "看图 /var/minis/workspace/uploads/a.jpg"
    assert scrub_machine_paths(text) == text


# ---------------------------------------------------------------------------
# 反向：沙箱写法 → 本机路径（模型照着出站看到的路径拼命令，必须能用）
# ---------------------------------------------------------------------------
def test_unscrub_rewrites_shell_commands(workspace):
    """``cd /var/minis/data/skills/x`` 这种命令要换成真实路径再执行。

    实测这就是「魔搭生图技能跑不起来」的直接原因：出站把技能目录写成
    ``/var/minis/data/skills/modelscope-image``，模型照着 cd —— Windows 上没有
    ``/var/minis``，于是 "No such file or directory"。
    """
    from openminis.tools.path_utils import unscrub_sandbox_paths

    data = context.app_context().data_dir
    out = unscrub_sandbox_paths(
        "cd /var/minis/data/skills/modelscope-image/scripts && python image_generation.py"
    )
    assert "/var/minis" not in out
    assert str(data).replace("\\", "/") in out
    assert "modelscope-image/scripts" in out

    ws_out = unscrub_sandbox_paths("ls /var/minis/workspace/generated")
    assert str(workspace).replace("\\", "/") in ws_out
    home_out = unscrub_sandbox_paths("ls /var/minis/home/Desktop")
    assert str(Path.home()).replace("\\", "/") in home_out

    # 不含沙箱写法的命令原样不动
    assert unscrub_sandbox_paths("python -m pytest -q") == "python -m pytest -q"
    # 别从半个目录名中间咬一口
    assert unscrub_sandbox_paths("echo /var/minis/workspaceX") == "echo /var/minis/workspaceX"


def test_to_host_path_covers_three_roots(workspace):
    from openminis.tools.path_utils import to_host_path

    data = context.app_context().data_dir
    assert to_host_path("/var/minis/workspace/uploads/a.jpg") == workspace / "uploads" / "a.jpg"
    assert to_host_path("/var/minis/data/skills/x") == data / "skills" / "x"
    assert to_host_path("/var/minis/data") == data
    assert to_host_path("/var/minis/home/Desktop/a.txt") == Path.home() / "Desktop" / "a.txt"
    assert to_host_path("C:/tmp/other/a.txt") is None
    assert to_host_path("") is None


def test_data_dir_paths_reach_the_skills_root(workspace):
    """技能目录在数据目录下，file_read 得能顺着沙箱写法找到它。"""
    skills = context.app_context().data_dir / "skills" / "demo"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text("---\nname: demo\n---\n", encoding="utf-8")
    resolved = read_resolver("s1", "/var/minis/data/skills/demo/SKILL.md")
    assert resolved == (skills / "SKILL.md").resolve()


def test_attachment_ref_from_sandbox_form(workspace):
    """模型在回复里回填的沙箱图片路径要还原，否则图发不出去。"""
    from openminis.settings.attachments import _resolve_local

    img = workspace / "generated" / "a.png"
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(b"\x89PNG")
    assert _resolve_local("/var/minis/workspace/generated/a.png") == img.resolve()
    assert _resolve_local(img.as_posix()) == img.resolve()
    # 工作区外面的还是不认
    assert _resolve_local("/var/minis/home/secret.png") is None


@pytest.mark.asyncio
async def test_shell_tool_rewrites_before_executing(workspace):
    """真实工具入口：命令在交给 shell 之前已经被换成真实路径。"""
    from openminis.sandbox.execution_coordinator import CommandResult
    from openminis.tools.shell_execute_tool import ShellExecuteTool, install_coordinator

    recorded: list[str] = []

    class Recording:
        def cwd_for(self, session_id: str) -> str:
            return ""

        async def execute(self, session_id, command, timeout=0.0,
                          line_callback=None, env_vars=None):
            recorded.append(command)
            return CommandResult(output="ok", exit_code=0, duration_ms=0)

    install_coordinator(Recording())  # type: ignore[arg-type]
    tool = ShellExecuteTool()
    await tool.execute(
        json.dumps({
            "tool_title": "跑技能",
            "command": "cd /var/minis/data/skills/modelscope-image/scripts && ls",
        }),
        "s1",
    )
    assert recorded and "/var/minis" not in recorded[0]
    assert str(context.app_context().data_dir).replace("\\", "/") in recorded[0]
