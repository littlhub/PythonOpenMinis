"""技能广场（marketplace）— 市场源清单 + 从 URL 安装技能包.

内置市场源只是"链接卡片"，真实浏览/下载在市场站点完成；用户把拿到的
``.zip`` 直链粘回来即可一键装进本地技能库。GitHub 仓库的 codeload
zipball 链接同样支持（自动跟随重定向）。
"""

from __future__ import annotations

import tempfile
import urllib.request
from pathlib import Path
from typing import Any

from ..skills.store import SkillError, SkillStore

MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024  # 200 MB 上限，防止误下超大包
DOWNLOAD_TIMEOUT = 90  # 秒

#: 市场源清单（kind: skill=技能包市场, mcp=MCP 服务器目录）。
SOURCES: list[dict[str, str]] = [
    {
        "id": "cowagent-skills",
        "kind": "skill",
        "name": "skills.cowagent.ai",
        "description": "社区技能广场，浏览后复制技能包 .zip 直链即可安装",
        "url": "https://skills.cowagent.ai",
    },
    {
        "id": "modelscope-skills",
        "kind": "skill",
        "name": "ModelScope 技能",
        "description": "魔搭社区 Agent 技能仓库，仓库页可下载 zip",
        "url": "https://modelscope.cn/skills",
    },
    {
        "id": "modelscope-mcp",
        "kind": "mcp",
        "name": "ModelScope MCP 广场",
        "description": "MCP 服务器目录，拿到的配置将用于通道/MCP 接入",
        "url": "https://modelscope.cn/mcp",
    },
]


def list_sources() -> list[dict[str, str]]:
    """Return the curated marketplace sources (fresh copies)."""
    return [dict(s) for s in SOURCES]


def install_from_url(store: SkillStore, url: str, *, force: bool = False) -> dict[str, Any]:
    """Download a ``.zip`` skill bundle from *url* and install it.

    Returns ``{"skill": <entry dict>, "bytes": n}``.  Raises ``SkillError``
    on network or validation problems.
    """
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise SkillError(f"只支持 http(s) 链接: {url}")

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "openminis-skill-market/1.0", "Accept": "*/*"},
    )
    tmp_dir = Path(tempfile.mkdtemp(prefix="minis-market-"))
    tmp_zip = tmp_dir / "bundle.zip"
    try:
        total = 0
        with urllib.request.urlopen(  # noqa: S310 - scheme whitelist above
            req, timeout=DOWNLOAD_TIMEOUT
        ) as resp, open(tmp_zip, "wb") as fh:
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise SkillError(f"下载超过 {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB 上限，已中止")
                fh.write(chunk)
        if total == 0:
            raise SkillError("下载内容为空")
        entry = store.install(str(tmp_zip), force=force)
        return {"skill": {"name": entry.name, "description": entry.description}, "bytes": total}
    except SkillError:
        raise
    except Exception as exc:  # network / HTTP errors
        raise SkillError(f"下载失败: {exc}") from exc
    finally:
        try:
            tmp_zip.unlink(missing_ok=True)
            tmp_dir.rmdir()
        except OSError:
            pass
