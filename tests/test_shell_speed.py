"""[T-tool-speed] 工具调用的固定开销：能省的两笔。

实测（本机）：
- 会话第一条 shell 命令 ~1.1s（bash 启动），之后每条 ~0.4s；
- 但 ``echo``（内建）只要 1~9ms —— 那 0.4s 是 **Windows 上每次外部进程启动**
  的成本（``ls`` 是外部程序），代码层面改不掉；
- 每次都重发 ``export …`` 注入环境变量，等于白加一次 shell 往返。

所以能省的是后两类：轮次开始**预热** shell、环境变量**没变就不重发**。
"""

from __future__ import annotations

import pytest

from openminis.core import context
from openminis.sandbox.execution_coordinator import ExecutionCoordinator
from openminis.sandbox.persistent_shell import PersistentShell


@pytest.fixture()
def env_ctx(tmp_path, monkeypatch):
    home = tmp_path / "openminis"
    home.mkdir()
    monkeypatch.setenv("MINIS_HOME", str(home))
    context.set_app_context(context.AppContext(data_dir=home, cache_dir=home))
    return home


@pytest.mark.asyncio
async def test_env_is_injected_once_when_unchanged(env_ctx, monkeypatch):
    """环境变量没变就不再重发 ``export``（每次都要等一个 shell 回显）。"""
    calls: list[dict] = []

    async def fake_apply(self, env_vars, previous_keys=None):
        calls.append(dict(env_vars))

    monkeypatch.setattr(PersistentShell, "apply_environment", fake_apply)
    monkeypatch.setattr(
        PersistentShell, "execute_command", _fake_execute_command
    )

    env = {"MODELSCOPE_API_KEY": "ms-1"}
    coord = ExecutionCoordinator(extra_env=env)
    await coord.execute("S", "echo 1", timeout=5, env_vars=env)
    await coord.execute("S", "echo 2", timeout=5, env_vars=env)
    await coord.execute("S", "echo 3", timeout=5, env_vars=env)
    assert len(calls) == 1, "值没变却重复注入了"

    # 值变了要重新注入
    await coord.execute("S", "echo 4", timeout=5, env_vars={"MODELSCOPE_API_KEY": "ms-2"})
    assert len(calls) == 2
    assert calls[-1]["MODELSCOPE_API_KEY"] == "ms-2"

    # 清空也要注入一次（把旧值 unset 掉）
    await coord.execute("S", "echo 5", timeout=5, env_vars={})
    assert len(calls) == 3 and calls[-1] == {}


@pytest.mark.asyncio
async def test_fresh_shell_reinjects_env(env_ctx, monkeypatch):
    """换了新 shell（旧的死了/被停掉）必须重新注入 —— 新进程里什么都没有。"""
    calls: list[dict] = []

    async def fake_apply(self, env_vars, previous_keys=None):
        calls.append(dict(env_vars))

    monkeypatch.setattr(PersistentShell, "apply_environment", fake_apply)
    monkeypatch.setattr(PersistentShell, "execute_command", _fake_execute_command)

    env = {"K": "v"}
    coord = ExecutionCoordinator(extra_env=env)
    await coord.execute("S", "echo 1", timeout=5, env_vars=env)
    assert len(calls) == 1
    await coord.stop_session("S")          # shell 没了
    await coord.execute("S", "echo 2", timeout=5, env_vars=env)
    assert len(calls) == 2, "新 shell 没有重新注入"


@pytest.mark.asyncio
async def test_warm_actually_runs_a_command(env_ctx, monkeypatch):
    """预热必须真跑一条命令 —— 光 spawn 不等就绪，省不掉启动开销。"""
    commands: list[str] = []

    async def fake_exec(self, command, timeout=0.0, line_callback=None):
        commands.append(command)
        return ("", 0)

    monkeypatch.setattr(PersistentShell, "execute_command", fake_exec)
    coord = ExecutionCoordinator()
    await coord.warm("S")
    assert commands == ["echo"], commands


async def _fake_execute_command(self, command, timeout=0.0, line_callback=None):
    return ("", 0)
