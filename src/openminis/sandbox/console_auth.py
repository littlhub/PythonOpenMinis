"""口令闸门：沙箱控制台密码 + 前端页面访问密码。

两把锁共用一套实现（``PasswordGate``），各自独立存储、独立解锁状态：

* ``console`` —— 「沙箱」页的控制台：设了密码后必须先解锁才能执行命令；
* ``access``  —— 整个前端页面：设了密码后，未解锁的请求会被挡在门外
  （见 ``main._access_gate`` 中间件），用户在「用户」按钮里设置/解锁。

安全约定：

* 只存 ``sha256(salt + 密码)``，**不存明文**（落 ``auth_gates.json``）；
* 解锁状态是**进程内**的：重启即回到锁定；
* 没设密码时闸门完全不起作用（默认行为与以前一致）。
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from typing import Optional

from ..core.context import app_context
from ..core.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "PasswordGate",
    "console_auth",
    "access_auth",
    "ACCESS_COOKIE",
    "UNLOCK_TTL_SECONDS",
    "ACCESS_TTL_SECONDS",
]

#: 控制台解锁后多久自动重新上锁（秒）。
UNLOCK_TTL_SECONDS = 3600
#: 页面访问解锁后多久失效（秒）—— 比控制台宽松些，免得频繁输密码。
ACCESS_TTL_SECONDS = 12 * 3600

#: 页面访问令牌的 cookie 名（``guard_api`` 签发、``main._access_gate`` 校验）。
#: 常量放在这里 —— 签发方与校验方都从这里取，避免两边各写一份而悄悄失配。
ACCESS_COOKIE = "minis_access"


class PasswordGate:
    """一把锁：存密码哈希、记住解锁状态、签发访问令牌。"""

    def __init__(self, name: str, ttl: float = UNLOCK_TTL_SECONDS) -> None:
        self.name = name
        self.ttl = ttl
        self._unlocked_until: float = 0.0
        self._tokens: dict[str, float] = {}
        self._cache: Optional[dict[str, dict[str, str]]] = None

    # -- 存储 ---------------------------------------------------------
    def _path(self) -> Path:
        return app_context().data_dir / "auth_gates.json"

    def _read(self) -> dict[str, dict[str, str]]:
        if self._cache is not None:
            return self._cache
        data: dict[str, dict[str, str]] = {}
        try:
            p = self._path()
            if p.is_file():
                raw = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    data = {
                        str(k): {
                            "salt": str(v.get("salt", "")),
                            "hash": str(v.get("hash", "")),
                        }
                        for k, v in raw.items()
                        if isinstance(v, dict)
                    }
        except Exception:  # pragma: no cover - 坏文件当作没设密码
            logger.warning("auth_gates 读取失败", exc_info=True)
        self._cache = data
        return data

    def _write(self, data: dict[str, dict[str, str]]) -> None:
        self._cache = data
        try:
            p = self._path()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:  # pragma: no cover
            logger.warning("auth_gates 落盘失败", exc_info=True)

    @staticmethod
    def _digest(salt: str, password: str) -> str:
        return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()

    def _mine(self) -> dict[str, str]:
        return self._read().get(self.name, {})

    # -- 查询 ---------------------------------------------------------
    def has_password(self) -> bool:
        return bool(self._mine().get("hash"))

    def is_unlocked(self) -> bool:
        return time.time() < self._unlocked_until

    def expires_at(self) -> Optional[float]:
        return self._unlocked_until if self.is_unlocked() else None

    def status(self) -> dict[str, object]:
        return {
            "hasPassword": self.has_password(),
            "locked": self.has_password() and not self.is_unlocked(),
            "expiresAt": self.expires_at(),
            "ttlSeconds": self.ttl,
        }

    def verify(self, password: str) -> bool:
        data = self._mine()
        if not data.get("hash"):
            return True
        return self._digest(data.get("salt", ""), password or "") == data["hash"]

    # -- 解锁 / 令牌 ---------------------------------------------------
    def _prune(self) -> None:
        now = time.time()
        for token, exp in list(self._tokens.items()):
            if exp < now:
                self._tokens.pop(token, None)

    def unlock(self, password: str) -> Optional[str]:
        """校验密码；通过则返回访问令牌（失败返回 ``None``）。"""
        if not self.verify(password):
            return None
        self._unlocked_until = time.time() + self.ttl
        self._prune()
        token = secrets.token_urlsafe(24)
        self._tokens[token] = self._unlocked_until
        return token

    def token_valid(self, token: Optional[str]) -> bool:
        if not token:
            return False
        self._prune()
        return token in self._tokens

    def lock(self) -> None:
        self._unlocked_until = 0.0
        self._tokens.clear()

    def set_password(self, current: str, new: str) -> tuple[bool, str]:
        """设置/修改/清除密码。返回 ``(ok, 错误说明)``。"""
        if self.has_password() and not self.is_unlocked() and not self.verify(current):
            return False, "当前密码不正确"
        data = self._read()
        if not new:
            data.pop(self.name, None)
            self._write(data)
            self.lock()
            return True, ""
        if len(new) < 4:
            return False, "密码至少 4 位"
        salt = hashlib.sha256(os.urandom(16)).hexdigest()[:16]
        data[self.name] = {"salt": salt, "hash": self._digest(salt, new)}
        self._write(data)
        # 刚设完密码就当作已解锁，免得用户马上又被挡一次
        self._unlocked_until = time.time() + self.ttl
        return True, ""

    def reset(self) -> None:
        """测试用：清空内存态。"""
        self._unlocked_until = 0.0
        self._tokens.clear()
        self._cache = None


#: 沙箱控制台（「沙箱」页执行命令前要先解锁）。
console_auth = PasswordGate("console", UNLOCK_TTL_SECONDS)
#: 前端页面访问（「用户」按钮里设置；未解锁时 API 返回 423）。
access_auth = PasswordGate("access", ACCESS_TTL_SECONDS)
