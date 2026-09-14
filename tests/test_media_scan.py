"""生图自动预览：从工作区生图目录里按 mtime 扫出**本次新产生**的图片。

背景：生图脚本只打印文件名，模型又常常忘记在回复里写 ``![](绝对路径)``，
用户「图生成了但看不到」。服务端在 ``toolEnd`` 帧上带出这些路径（前端即时
预览），落库时再把它们补成 markdown（刷新后仍可见）。
"""

from __future__ import annotations

import os
import time

import pytest

from openminis.agent.repeat_guard import looks_like_image_generation
from openminis.server.media_scan import append_image_refs, collect_recent_images


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "workspace"
    (ws / "image").mkdir(parents=True)
    (ws / "generated").mkdir(parents=True)

    class _Ctx:
        external_files_dir = ws

    import openminis.core.context as ctx

    monkeypatch.setattr(ctx, "app_context", lambda: _Ctx())
    return ws


def _touch(path, age_seconds: float = 0.0) -> str:
    path.write_bytes(b"\x89PNG\r\n\x1a\n")
    when = time.time() - age_seconds
    os.utime(path, (when, when))
    return str(path)


# ---------------------------------------------------------------------------
# collect_recent_images
# ---------------------------------------------------------------------------
def test_collects_only_files_newer_than_since(workspace):
    old = _touch(workspace / "image" / "old.png", age_seconds=3600)
    new = _touch(workspace / "image" / "new.png", age_seconds=1)
    also = _touch(workspace / "generated" / "gen.png", age_seconds=2)

    found = collect_recent_images(since=time.time() - 60)
    assert old not in found
    assert new in found
    assert also in found


def test_ignores_non_image_files(workspace):
    _touch(workspace / "image" / "note.txt", age_seconds=1)
    _touch(workspace / "image" / "clip.mp4", age_seconds=1)
    assert collect_recent_images(since=time.time() - 60) == []


def test_sorted_by_mtime_and_limited(workspace):
    for i, age in enumerate((30.0, 20.0, 10.0)):
        _touch(workspace / "image" / f"p{i}.png", age_seconds=age)
    found = collect_recent_images(since=time.time() - 60, limit=2)
    assert len(found) == 2
    # 最旧的两张（age 30 / 20）排在前
    assert found[0].endswith("p0.png")
    assert found[1].endswith("p1.png")


def test_missing_workspace_is_silent(tmp_path, monkeypatch):
    class _Ctx:
        external_files_dir = tmp_path / "nope"

    import openminis.core.context as ctx

    monkeypatch.setattr(ctx, "app_context", lambda: _Ctx())
    assert collect_recent_images(since=0) == []


# ---------------------------------------------------------------------------
# append_image_refs
# ---------------------------------------------------------------------------
def test_append_image_refs_adds_missing():
    out = append_image_refs("做好了。", [r"C:\ws\image\a.png"])
    assert out.startswith("做好了。")
    assert r"![生成图](C:\ws\image\a.png)" in out


def test_append_image_refs_skips_already_mentioned():
    text = "看这张 ![图](C:/ws/image/a.png)"
    assert append_image_refs(text, ["C:/ws/image/a.png"]) == text


def test_append_image_refs_noop_without_images():
    assert append_image_refs("hi", []) == "hi"
    assert append_image_refs("hi", [""]) == "hi"


def test_append_image_refs_handles_empty_text():
    out = append_image_refs("   ", ["/ws/a.png"])
    assert out.strip() == "![生成图](/ws/a.png)"


# ---------------------------------------------------------------------------
# looks_like_image_generation（模块级，服务端与护栏共用）
# ---------------------------------------------------------------------------
def test_looks_like_image_generation_paths():
    assert looks_like_image_generation("image_gen", {"prompt": "x"})
    assert looks_like_image_generation(
        "shell_execute",
        {"command": "python C:/skills/agnes-image/scripts/image_generation.py \"x\""},
    )
    assert looks_like_image_generation(
        "shell_execute", {"command": "bash run-imagegen.sh"}
    )
    assert not looks_like_image_generation("shell_execute", {"command": "ls -la"})
    assert not looks_like_image_generation("web_search", {"query": "cat"})
    assert not looks_like_image_generation("shell_execute", None)
