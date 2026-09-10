"""打包相关的行为：前端目录解析 + 端口/监听地址的优先级。

这两件事都只在「跑起来」的时候才出错 —— exe 里找不到 web/dist 会退化成
一个没有出口的 404 页面,而端口读错会让用户在设置里改了却不生效。都不
容易在开发机上碰到,所以在这里钉住。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

from openminis.server import main as server_main

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:  # app.py 在仓库根,不是包
    sys.path.insert(0, str(_ROOT))


def _plant_dist(root: Path, name: str = "web/dist") -> Path:
    """造一个含 index.html 的假前端目录。"""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.html").write_text("<!doctype html><html></html>", encoding="utf-8")
    return d


# ---------------------------------------------------------------------------
# _resolve_web_dist
# ---------------------------------------------------------------------------
def test_from_source_uses_the_checkout_dist() -> None:
    """源码运行时就是 <repo>/web/dist,与改动前一致。"""
    resolved = server_main._resolve_web_dist()
    assert resolved == Path(server_main.__file__).resolve().parents[3] / "web" / "dist"


def test_frozen_prefers_a_dist_next_to_the_exe(tmp_path, monkeypatch) -> None:
    """打包后,exe 同级的前端优先 —— 这样换界面不必重新打包。

    两份都存在时,如果选错了 _internal 里那份,用户把新前端放到 exe 旁边
    会毫无反应,而这是最难自查的一类问题。
    """
    exe_dir = tmp_path / "app"
    exe_dir.mkdir()
    exe = exe_dir / "OpenMinis.exe"
    exe.write_bytes(b"MZ")
    meipass = tmp_path / "_MEIPASS"
    meipass.mkdir()

    beside = _plant_dist(exe_dir)
    _plant_dist(meipass)

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe), raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)

    assert server_main._resolve_web_dist() == beside


def test_frozen_falls_back_to_the_bundled_copy(tmp_path, monkeypatch) -> None:
    """exe 旁边没有时用 PyInstaller 打进去的那份 —— exe 才能独立运行。"""
    exe_dir = tmp_path / "app"
    exe_dir.mkdir()
    exe = exe_dir / "OpenMinis.exe"
    exe.write_bytes(b"MZ")
    meipass = tmp_path / "_MEIPASS"
    meipass.mkdir()
    bundled = _plant_dist(meipass)

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe), raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)

    assert server_main._resolve_web_dist() == bundled


def test_no_dist_anywhere_falls_back_to_the_checkout_path(tmp_path, monkeypatch) -> None:
    """哪都没有前端时返回 checkout 路径,让「未构建」提示页说出该构建哪个目录。"""
    exe_dir = tmp_path / "app"
    exe_dir.mkdir()
    exe = exe_dir / "OpenMinis.exe"
    exe.write_bytes(b"MZ")
    meipass = tmp_path / "_MEIPASS"
    meipass.mkdir()

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe), raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)

    expected = Path(server_main.__file__).resolve().parents[3] / "web" / "dist"
    assert server_main._resolve_web_dist() == expected


def test_a_dist_without_index_is_not_treated_as_built(tmp_path, monkeypatch) -> None:
    """空目录(或只剩 assets)不算「已构建」—— 判据是 index.html 真的在。"""
    exe_dir = tmp_path / "app"
    exe_dir.mkdir()
    exe = exe_dir / "OpenMinis.exe"
    exe.write_bytes(b"MZ")
    meipass = tmp_path / "_MEIPASS"
    meipass.mkdir()
    (exe_dir / "web" / "dist" / "assets").mkdir(parents=True)  # 只有 assets

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe), raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)

    expected = Path(server_main.__file__).resolve().parents[3] / "web" / "dist"
    assert server_main._resolve_web_dist() == expected


# ---------------------------------------------------------------------------
# app.py 的端口 / 监听地址解析
# ---------------------------------------------------------------------------
@pytest.fixture()
def app_module():
    import app as mod

    return mod


def test_cli_flag_beats_the_saved_setting(app_module, monkeypatch) -> None:
    """命令行显式给的端口必须赢过界面里存的值 —— 否则排障时改不动端口。"""
    monkeypatch.setattr(app_module, "_configured_bind", lambda: ("0.0.0.0", 9000))
    args = argparse.Namespace(host="127.0.0.1", port=8899)
    assert app_module._resolve_bind(args) == ("127.0.0.1", 8899)


def test_saved_setting_is_used_when_no_flag_given(app_module, monkeypatch) -> None:
    """没有命令行参数时读界面里存的设置 —— 这正是「后台运行与服务」页的意义。"""
    monkeypatch.setattr(app_module, "_configured_bind", lambda: ("0.0.0.0", 9000))
    args = argparse.Namespace(host=None, port=None)
    assert app_module._resolve_bind(args) == ("0.0.0.0", 9000)


def test_builtin_defaults_when_nothing_is_configured(app_module, monkeypatch) -> None:
    """配置读不出来(文件损坏/首次运行)也要能起来,退回内置默认值。"""
    monkeypatch.setattr(app_module, "_configured_bind", lambda: (None, None))
    args = argparse.Namespace(host=None, port=None)
    assert app_module._resolve_bind(args) == (
        app_module.DEFAULT_HOST,
        app_module.DEFAULT_PORT,
    )


def test_configured_bind_survives_a_broken_store(app_module, monkeypatch) -> None:
    """注册表炸了也不能把启动带崩 —— 返回 (None, None) 让默认值接管。"""

    def _boom():
        raise RuntimeError("registry exploded")

    monkeypatch.setattr(
        "openminis.config.config_registry.ConfigRegistry.init", staticmethod(_boom)
    )
    assert app_module._configured_bind() == (None, None)
