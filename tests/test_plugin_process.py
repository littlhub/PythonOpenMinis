"""[T-plugins-manager] 外部程序插件（runtime=process）：找可执行文件、传 PATH、起 .cmd。

用户实测的场景：导入 dsh-bridge（一个现成的 Node 桥接项目）后点启用，报
「找不到可执行文件：node」—— 因为引擎进程是用户从 .bat 起的，PATH 很干净，而
这台机器上唯一的 node 由别的工具托管、不在 PATH 里。这组测试钉住三件事：

1. 找不到时不能只报错，得**主动去本机常见位置找**，并给用户一个能填的搜索目录；
2. 找到之后要把可执行文件所在目录顶进子进程 PATH（npm 自己会去调 node）；
3. Windows 上 ``.cmd`` / ``.bat`` 不能直接 CreateProcess，必须套一层 shell。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from openminis.plugins import store
from openminis.plugins.manifest import RUNTIME_PATH_KEY, ChannelManifest
from openminis.plugins.process import (
    ProcessRunner,
    candidate_bin_dirs,
    extra_bin_dirs,
    resolve_executable,
)


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    from openminis.core import context

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("MINIS_HOME", str(home))
    context.set_app_context(context.AppContext(data_dir=home, cache_dir=home))
    return home


def test_process_manifest_gets_runtime_path_field():
    """外部程序插件一律自带「可执行文件搜索目录」，否则用户没法自救。"""
    m = ChannelManifest.from_dict({
        "id": "dsh-bridge", "runtime": "process",
        "process": {"command": "node", "args": ["index.js"]},
    })
    assert RUNTIME_PATH_KEY in [f.key for f in m.fields]
    # 引擎内插件不塞这一项（它不用找外部程序）
    m2 = ChannelManifest.from_dict({"id": "qq", "runtime": "engine", "driver": "qq"})
    assert RUNTIME_PATH_KEY not in [f.key for f in m2.fields]
    # 清单里自己声明过就不重复加
    m3 = ChannelManifest.from_dict({
        "id": "x", "runtime": "process",
        "process": {"command": "node"},
        "fields": [{"key": RUNTIME_PATH_KEY, "label": "自定义标签", "type": "list"}],
    })
    assert [f.key for f in m3.fields].count(RUNTIME_PATH_KEY) == 1
    assert m3.fields[0].label == "自定义标签"


def test_extra_bin_dirs_accepts_list_and_text():
    assert extra_bin_dirs({RUNTIME_PATH_KEY: ["C:/a", " C:/b "]}) == ["C:/a", "C:/b"]
    assert extra_bin_dirs({RUNTIME_PATH_KEY: "C:/a\n\nC:/b"}) == ["C:/a", "C:/b"]
    assert extra_bin_dirs({}) == []
    assert extra_bin_dirs({"runtimePath": 42}) == []


def test_candidate_bin_dirs_covers_common_installs():
    """候选目录要覆盖常见的 node 装法 —— 漏找的代价是用户对着报错发呆。"""
    joined = " ".join(str(p).lower() for p in candidate_bin_dirs())
    assert "nodejs" in joined          # 官方安装器
    assert "nvm" in joined             # nvm-windows
    assert "binaries" in joined        # 本机托管运行时
    assert "scoop" in joined and "chocolatey" in joined


def test_resolve_executable_searches_extra_dirs(tmp_path, monkeypatch):
    """用户填的搜索目录要能找到东西（PATH 干净时的唯一出路）。"""
    monkeypatch.setenv("PATH", "")   # 模拟 run.bat 起出来的干净环境
    assert resolve_executable("工作台没有的程序") is None

    bindir = tmp_path / "runtime" / "bin"
    bindir.mkdir(parents=True)
    exe = bindir / "mytool.exe"
    exe.write_bytes(b"MZ")
    assert resolve_executable("mytool", [bindir]) == str(exe)
    assert resolve_executable("mytool.exe", [bindir]) == str(exe)
    # 命令本来就带路径时直接核存在性
    assert resolve_executable(str(exe)) == str(exe)


def test_resolve_executable_python_points_at_current_interpreter():
    import sys

    assert resolve_executable("python") == sys.executable
    assert resolve_executable("python3") == sys.executable


def test_child_env_puts_exe_dir_on_path(data_dir, tmp_path):
    """必须把可执行文件所在目录顶到 PATH 最前面 —— npm 会去调 node。"""
    bindir = tmp_path / "nodejs"
    bindir.mkdir()
    exe = bindir / "node.exe"
    exe.write_bytes(b"MZ")

    manifest = ChannelManifest.from_dict({
        "id": "p", "runtime": "process",
        "process": {"command": "node", "args": ["i.js"]},
    })
    runner = ProcessRunner(manifest, {RUNTIME_PATH_KEY: [str(bindir)]})
    env = runner._child_env([str(bindir)], str(exe))
    assert env["PATH"].split(os.pathsep)[0] == str(bindir)


@pytest.mark.asyncio
async def test_process_runner_launches_batch_script(data_dir, tmp_path):
    """``.cmd`` / ``.bat`` 不能直接 CreateProcess，得套一层 shell（npm 就是这种）。"""
    root = store.plugins_dir() / "cmdplug"
    root.mkdir(parents=True)
    (root / "say.cmd").write_text("@echo hello-from-cmd\r\n", encoding="ascii")

    manifest = ChannelManifest.from_dict({
        "id": "cmdplug", "runtime": "process",
        "process": {"command": "say.cmd", "autoRestart": False},
    })
    runner = ProcessRunner(manifest, {RUNTIME_PATH_KEY: [str(root)]})
    await runner.start()
    try:
        await asyncio.sleep(1.2)
        texts = " ".join(row["text"] for row in runner.logs())
        assert "hello-from-cmd" in texts
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_process_runner_reports_actionable_missing_exe(data_dir, tmp_path):
    """找不到可执行文件时的报错要指出「去哪儿填」—— 光说找不到等于没说。"""
    from openminis.plugins.manifest import ManifestError

    root = store.plugins_dir() / "notool"
    root.mkdir(parents=True)
    manifest = ChannelManifest.from_dict({
        "id": "notool", "runtime": "process",
        "process": {"command": "definitely-not-installed-xyz"},
    })
    runner = ProcessRunner(manifest, {})
    with pytest.raises(ManifestError) as err:
        await runner.start()
    assert "搜索目录" in str(err.value)
    assert "可执行文件" in str(err.value)


def test_preflight_warns_about_missing_node_modules(data_dir, tmp_path):
    """Node 项目没装依赖时先说清楚 —— 不然用户看着「Cannot find module」发懵。"""
    root = tmp_path / "nodemod"
    root.mkdir()
    (root / "package.json").write_text('{"name":"x"}', encoding="utf-8")

    manifest = ChannelManifest.from_dict({
        "id": "nodemod", "runtime": "process",
        "process": {"command": "node", "args": ["index.js"]},
    })
    runner = ProcessRunner(manifest, {})
    assert any("npm install" in h for h in runner._preflight(root))
    (root / "node_modules").mkdir()
    assert runner._preflight(root) == []


def test_preflight_silent_for_plain_directory(data_dir, tmp_path):
    root = tmp_path / "plain"
    root.mkdir()
    manifest = ChannelManifest.from_dict({
        "id": "plain", "runtime": "process", "process": {"command": "node"},
    })
    assert ProcessRunner(manifest, {})._preflight(root) == []


@pytest.mark.asyncio
async def test_cwd_must_stay_inside_plugin_dir(data_dir, tmp_path):
    """工作目录不许越界（清单里的 cwd 是相对插件目录的）。"""
    from openminis.plugins.manifest import ManifestError

    root = store.plugins_dir() / "escapee"
    root.mkdir(parents=True)
    manifest = ChannelManifest.from_dict({
        "id": "escapee", "runtime": "process",
        "process": {"command": "python", "cwd": "../../.."},
    })
    runner = ProcessRunner(manifest, {})
    with pytest.raises(ManifestError) as err:
        await runner.start()
    assert "越界" in str(err.value) or "不存在" in str(err.value)
