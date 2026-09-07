"""Install, list and read skill bundles.

The store owns ``<data_dir>/skills``: one sub-directory per skill, each with a
``SKILL.md`` carrying YAML frontmatter (``name`` / ``description``) followed by
the Markdown body the agent reads when it picks the skill up.

Two kinds of bundles live side by side:

* **builtin skills** — shipped inside the package (``skills/builtin/``) and
  copied out on first run. Existing directories are never overwritten, so a
  user's edits survive upgrades.
* **the builtin-tool manifest** — a generated bundle named
  ``builtin-tools`` that documents every tool the agent loop can call. It is
  regenerated on every install pass because the tool set depends on config
  (memory toggles, vision groups…).

Installing from a directory or a ``.zip`` is supported for third-party skills.
"""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import yaml

from ..core.context import app_context
from ..core.logging import get_logger
from .models import SkillEntry, ToolEntry

logger = get_logger(__name__)

__all__ = ["SkillStore", "SkillError", "SKILL_FILE", "BUILTIN_TOOLS_SKILL"]

SKILL_FILE = "SKILL.md"
BUILTIN_TOOLS_SKILL = "builtin-tools"
_SCRIPTS_DIR = "scripts"


class SkillError(Exception):
    """Raised when a skill cannot be installed or removed."""


def _bundled_dir() -> Path:
    return Path(__file__).resolve().parent / "builtin"


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return ``(frontmatter, body)`` for a ``SKILL.md`` payload.

    Malformed frontmatter is ignored rather than fatal — a skill with a broken
    header is still readable, it just gets a fallback name/description.
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    raw = text[3:end].strip()
    body = text[end + 4 :].lstrip("\n")
    try:
        meta = yaml.safe_load(raw)
    except yaml.YAMLError:
        logger.debug("unparseable skill frontmatter, ignoring: %.80s", raw)
        return {}, body
    return (meta if isinstance(meta, dict) else {}), body


def _skill_name(meta: dict[str, Any], fallback: str) -> str:
    name = str(meta.get("name") or "").strip()
    return name or fallback


class SkillStore:
    """Filesystem-backed registry of installed skills."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else app_context().data_dir / "skills"

    # -- install ---------------------------------------------------------
    def ensure_installed(self) -> list[str]:
        """Install bundled skills + refresh the tool manifest. Idempotent.

        Returns the names of the skills that were newly installed (empty on a
        warm start) so callers can log once instead of on every request.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        installed: list[str] = []
        bundled = _bundled_dir()
        if bundled.is_dir():
            for src in sorted(p for p in bundled.iterdir() if p.is_dir()):
                # A `.skip` marker (e.g. third-party skills dropped in by
                # other tools) keeps a bundle out of the install set.
                if (src / ".skip").exists():
                    continue
                dest = self.root / src.name
                # Never clobber: a user-edited skill stays user-edited.
                if not (dest / SKILL_FILE).exists():
                    shutil.copytree(src, dest)
                    installed.append(src.name)
        self.write_builtin_tools()
        return installed

    def install(self, source: Path | str, *, force: bool = False) -> SkillEntry:
        """Install a skill from a directory or a ``.zip`` archive."""
        src = Path(str(source)).expanduser()
        if not src.exists():
            raise SkillError(f"路径不存在: {src}")
        if src.is_dir():
            return self._install_dir(src, force=force)
        if src.suffix.lower() != ".zip":
            raise SkillError(f"只支持目录或 .zip 技能包: {src}")
        tmp = Path(tempfile.mkdtemp(prefix="minis-skill-"))
        try:
            self._extract_zip(src, tmp)
            return self._install_dir(tmp, force=force)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _install_dir(self, staged: Path, *, force: bool) -> SkillEntry:
        manifest = self._locate_manifest(staged)
        meta, _ = _split_frontmatter(
            manifest.read_text(encoding="utf-8", errors="replace")
        )
        name = _skill_name(meta, manifest.parent.name)
        dest = self.root / self._safe_name(name)
        if dest.exists() and any(dest.iterdir()):
            if not force:
                raise SkillError(f"技能已存在: {name}（如需覆盖请传 force=true）")
            shutil.rmtree(dest)
        shutil.copytree(manifest.parent, dest)

        entry = self._read_entry(dest)
        if entry is None:  # pragma: no cover - copytree just wrote the file
            raise SkillError(f"技能安装后无法读取: {name}")
        return entry

    @staticmethod
    def _extract_zip(src: Path, dest: Path) -> None:
        with zipfile.ZipFile(src) as zf:
            root = dest.resolve()
            for member in zf.namelist():
                # zip-slip guard: every entry must land inside ``dest``
                target = (dest / member).resolve()
                if root not in target.parents and target != root:
                    raise SkillError(f"技能包含有非法路径: {member}")
            zf.extractall(dest)

    def uninstall(self, name: str) -> bool:
        """Remove an installed skill. Generated bundles are protected."""
        entry = self.get(name)
        if entry is None:
            return False
        if entry.generated:
            raise SkillError(f"{name} 是自动生成的,不能卸载")
        shutil.rmtree(Path(entry.path))
        return True

    # -- builtin tool manifest ------------------------------------------
    @staticmethod
    def builtin_tools() -> list[ToolEntry]:
        """Every tool the agent loop exposes, straight from the registry."""
        from ..tools.agent_tools import AgentTools

        out: list[ToolEntry] = []
        for d in AgentTools.make_agent_tools():
            params = {
                pname: getattr(p, "description", "") or ""
                for pname, p in (d.parameters or {}).items()
            }
            out.append(
                ToolEntry(
                    name=d.name,
                    description=d.description or "",
                    parameters=params,
                    required=tuple(d.required or ()),
                )
            )
        return out

    def write_builtin_tools(self) -> SkillEntry | None:
        """(Re)generate the ``builtin-tools`` bundle. Returns its entry."""
        try:
            tools = self.builtin_tools()
        except Exception:  # pragma: no cover - never block startup over docs
            logger.debug("builtin tool manifest skipped", exc_info=True)
            return None

        lines = [
            "---",
            f"name: {BUILTIN_TOOLS_SKILL}",
            "builtin: true",
            'description: "OpenMinis 内置工具清单。需要判断该用哪个工具、或需要组合多个工具完成任务时使用。"',
            "---",
            "",
            "# OpenMinis 内置工具",
            "",
            "下面是当前会话可直接调用的工具。工具随配置变化（例如关闭记忆后",
            "memory_write / memory_get 会消失），本文件每次启动都会重新生成。",
            "",
        ]
        for t in tools:
            lines.append(f"## {t.name}")
            lines.append("")
            lines.append(t.description.strip() or "（无描述）")
            if t.parameters:
                lines.append("")
                lines.append("参数:")
                for pname, pdesc in t.parameters.items():
                    required = "必填" if pname in t.required else "可选"
                    lines.append(f"- `{pname}`（{required}）: {pdesc}")
            lines.append("")

        dest = self.root / BUILTIN_TOOLS_SKILL
        dest.mkdir(parents=True, exist_ok=True)
        (dest / SKILL_FILE).write_text("\n".join(lines), encoding="utf-8")
        return self._read_entry(dest)

    # -- read ------------------------------------------------------------
    def list(self) -> list[SkillEntry]:
        if not self.root.is_dir():
            return []
        out: list[SkillEntry] = []
        for child in sorted(self.root.iterdir()):
            if not child.is_dir():
                continue
            entry = self._read_entry(child)
            if entry is not None:
                out.append(entry)
        return out

    def get(self, name: str) -> SkillEntry | None:
        """Look up by bundle directory name *or* frontmatter name."""
        target = self._safe_name(name)
        direct = self.root / target
        if (direct / SKILL_FILE).is_file():
            return self._read_entry(direct)
        for entry in self.list():
            if entry.name == name:
                return entry
        return None

    def read(self, name: str) -> str:
        """Body of a skill's ``SKILL.md`` (frontmatter stripped)."""
        entry = self.get(name)
        if entry is None:
            raise SkillError(f"技能不存在: {name}")
        return entry.body

    # -- internals -------------------------------------------------------
    def _read_entry(self, directory: Path) -> SkillEntry | None:
        manifest = directory / SKILL_FILE
        if not manifest.is_file():
            return None
        try:
            meta, body = _split_frontmatter(
                manifest.read_text(encoding="utf-8", errors="replace")
            )
        except OSError:  # pragma: no cover - unreadable file
            return None
        scripts_dir = directory / _SCRIPTS_DIR
        scripts: tuple[str, ...] = ()
        if scripts_dir.is_dir():
            scripts = tuple(sorted(p.name for p in scripts_dir.iterdir()))
        return SkillEntry(
            name=_skill_name(meta, directory.name),
            description=str(meta.get("description") or "").strip(),
            path=str(directory),
            source="builtin" if meta.get("builtin") else "user",
            generated=directory.name == BUILTIN_TOOLS_SKILL,
            scripts=scripts,
            body=body,
        )

    @staticmethod
    def _safe_name(name: str) -> str:
        """Keep skill names usable as a single directory name."""
        cleaned = name.strip().replace("\\", "/").split("/")[-1]
        cleaned = cleaned.replace("..", "").strip()
        if cleaned in {"", ".", ".."}:
            raise SkillError(f"非法的技能名: {name!r}")
        return cleaned

    def _locate_manifest(self, staged: Path) -> Path:
        """Accept a bundle root, or an archive that wraps it in one folder."""
        direct = staged / SKILL_FILE
        if direct.is_file():
            return direct
        for child in sorted(staged.iterdir()):
            if child.is_dir() and (child / SKILL_FILE).is_file():
                return child / SKILL_FILE
        raise SkillError(f"未找到 {SKILL_FILE}: {staged}")
