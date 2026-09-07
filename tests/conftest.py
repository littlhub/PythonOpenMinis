"""Shared fixtures."""

from __future__ import annotations

import pytest

from openminis.server import chat_store


@pytest.fixture()
def isolated_chat_db(tmp_path):
    """Point the chat store at a throwaway SQLite file (never the real one)."""
    chat_store.set_database_path(tmp_path / "chat_test.db")
    yield
    chat_store.set_database_path(None)
