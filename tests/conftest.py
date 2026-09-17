"""Shared fixtures."""

from __future__ import annotations

import pytest

from openminis.core import context
from openminis.server import chat_store


@pytest.fixture()
def isolated_chat_db(tmp_path):
    """Point the chat store at a throwaway SQLite file (never the real one)."""
    chat_store.set_database_path(tmp_path / "chat_test.db")
    yield
    chat_store.set_database_path(None)


@pytest.fixture(autouse=True)
def isolated_app_context(tmp_path, monkeypatch):
    """每个测试一份干净的数据目录。

    ``context.app_context()`` 是**进程级单例**，而且没设 ``MINIS_HOME`` 时会落到
    **真实的用户目录**（``~/openminis``）。所以不显式隔离就会出两类事故：

    1. 测试读到用户真机上的插件/记忆/会话（曾在 ``test_settings`` 上表现为
       「单独跑通、整个套件一起跑就挂」—— 因为真实插件目录里有个已装的插件）；
    2. 某个测试把上下文指到自己的 tmp 目录后不还原，后续测试跟着读它。

    这里两头都堵上：测试开始前指到 tmp，结束后清掉单例（下个测试再按新的
    ``MINIS_HOME`` 重新推导）。已有测试自己 ``set_app_context`` 的照旧生效，
    它们只是把这份隔离再收紧一层。
    """
    home = tmp_path / "minis-home"
    home.mkdir()
    monkeypatch.setenv("MINIS_HOME", str(home))
    context.set_app_context(context.AppContext(data_dir=home, cache_dir=tmp_path / "minis-cache"))
    yield
    context.reset_app_context()
