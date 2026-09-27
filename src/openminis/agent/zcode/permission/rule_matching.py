"""Rule matching — wildcard/regex and WebFetch rule-subject helpers.

Ported from: packages/core/src/permission/rule-matching.ts
Original package: @zcode/core (permission)
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlsplit

__all__ = ["wildcard_to_regexp", "web_fetch_rule_subjects"]


# PORT: TS ``/[.*+?^${}()|[\]\\]/g`` —— 正则元字符集合，转义时逐字符前置反斜杠。
_REGEXP_SPECIAL_CHARS = re.compile(r"[.*+?^${}()|\[\]\\]")


def wildcard_to_regexp(pattern: str) -> re.Pattern[str]:
    """Mirrors ``wildcardToRegExp(pattern)``.

    ``*`` 作为通配符展开为 ``.*``，其余正则元字符转义后整体锚定为 ``^...$``。
    """
    escaped = ".*".join(
        _REGEXP_SPECIAL_CHARS.sub(lambda match: "\\" + match.group(0), part)
        for part in pattern.split("*")
    )
    return re.compile(f"^{escaped}$")


def _domain_rule_subject(url: str) -> Optional[str]:
    """Mirrors ``domainRuleSubject(url)``.

    # PORT: TS ``new URL(url.trim())`` 要求绝对 URL，非法输入抛错被 catch 吞掉。
    # Python ``urlsplit`` 更宽容（裸域名不抛错而是 hostname 为空），故显式用
    # ``hostname is None`` 判定，保持与 JS 一致的「非绝对 URL 即不成立」。
    """
    try:
        parsed = urlsplit(url.strip())
        hostname = parsed.hostname
    except ValueError:
        return None
    if hostname is None:
        return None
    hostname = hostname.lower()
    # TS ``.replace(/\.$/, "")`` 只去掉一个结尾点。
    if hostname.endswith("."):
        hostname = hostname[:-1]
    return f"domain:{hostname}" if len(hostname) > 0 else None


def web_fetch_rule_subjects(url: str) -> list[str]:
    """Mirrors ``webFetchRuleSubjects(url)``."""
    domain = _domain_rule_subject(url)
    return [domain] if domain is not None else []
