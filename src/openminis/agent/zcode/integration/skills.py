"""ZCode 技能面：独立技能根发现 + ``SkillPort`` 实现。

ZCode 的 skill 与主智能体**不是同一套**：上游 ``adapters/skills/roots.ts`` 扫描的是
``<dir>/.zcode/skills`` 与 ``<dir>/.agents/skills``（用户级 + 项目级，项目级从 cwd
逐级向上直到 git 根）。本模块把这条发现规则落成本机实现，并把结果同时供给：

- 内核 ``Skill`` 工具（``SkillPort.load_skill``）
- 应用技能页 / 子代理技能勾选（``discover_zcode_skills()``）

与主智能体技能库（``openminis.skills.store`` 的单根目录）**并存**：两边的技能名可能
相交，这里一律**取并集**并在元数据里标明 ``root_path``/``source``，不覆盖、不去重合并。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ..context.types import SkillDiagnostic, SkillLoadOutcome, SkillMetadata, SkillPolicy

__all__ = [
    "ZCODE_SKILL_DIR",
    "AGENTS_SKILL_DIR",
    "SKILL_FILE_NAME",
    "ZcodeSkillRoot",
    "expand_skill_root",
    "roots_from_patterns",
    "host_skill_roots",
    "generated_host_skill_names",
    "resolve_zcode_skill_roots",
    "discover_zcode_skills",
    "discover_skills_at_roots",
    "ZcodeSkillPort",
]

SKILL_FILE_NAME = "SKILL.md"
#: ZCode 自己的技能目录（项目级 + 用户级）。
ZCODE_SKILL_DIR = ".zcode"
#: 跨工具约定的技能目录，ZCode 也认。
AGENTS_SKILL_DIR = ".agents"
_SKILLS_DIR = "skills"
_MAX_SKILL_BYTES = 100_000
_MAX_DEPTH_LEVELS = 24


@dataclass(slots=True)
class ZcodeSkillRoot:
    """一个 ZCode 技能根（对应上游 ``SkillRoot``）。"""

    path: str
    scope: str  # "project" | "user"
    source: str  # "zcode" | "agents"
    priority: int


def _frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """拆 ``---`` YAML frontmatter；不做完整 YAML 解析（只需顶层标量）。

    # PORT: 上游用完整 YAML 解析器。这里只取 ``key: value`` 顶层标量 —— 技能
    # frontmatter 实际只用到 ``name`` / ``description`` / ``when_to_use`` 这几个
    # 字符串字段，引入 YAML 依赖不划算。
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    head = text[3:end]
    body = text[end + 4 :]
    meta: dict[str, Any] = {}
    for line in head.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # 跳过嵌套表（``policy:`` 之类）—— 顶层标量之外的交给下游默认值。
        if not value:
            continue
        meta[key] = value
    return meta, body


def _git_root(start: Path) -> Optional[Path]:
    current = start
    for _ in range(_MAX_DEPTH_LEVELS):
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent
    return None


def resolve_zcode_skill_roots(
    working_directory: str,
    *,
    home_directory: Optional[str] = None,
) -> list[ZcodeSkillRoot]:
    """按上游优先级解析 ZCode 技能根（先项目级、后用户级，逐级外扩）。

    优先级必须**单调递增**且项目级在前：同名技能由内核按 priority 取先出现的那个，
    项目级覆盖用户级是用户预期。
    """
    resolved = Path(os.path.abspath(working_directory or os.getcwd()))
    home = Path(home_directory or os.path.expanduser("~"))
    roots: list[ZcodeSkillRoot] = []
    priority = 0

    def next_priority() -> int:
        nonlocal priority
        priority += 10
        return priority

    # 项目级：从 cwd 逐级向上到 git 根（找不到 git 根就只认 cwd 本身）。
    git_root = _git_root(resolved)
    levels: list[Path] = []
    current = resolved
    while True:
        levels.append(current)
        if git_root is None or current == git_root or current.parent == current:
            break
        current = current.parent
    if git_root is not None and levels:
        levels = list(reversed(levels))  # 外层先入，内层（更靠近 cwd）优先级更高

    for level in levels:
        for source, base in (("zcode", ZCODE_SKILL_DIR), ("agents", AGENTS_SKILL_DIR)):
            roots.append(
                ZcodeSkillRoot(
                    path=str(level / base / _SKILLS_DIR),
                    scope="project",
                    source=source,
                    priority=next_priority(),
                )
            )

    # 用户级
    for source, base in (("zcode", ZCODE_SKILL_DIR), ("agents", AGENTS_SKILL_DIR)):
        roots.append(
            ZcodeSkillRoot(
                path=str(home / base / _SKILLS_DIR),
                scope="user",
                source=source,
                priority=next_priority(),
            )
        )
    return roots


# ---------------------------------------------------------------------------
# 描述符驱动的技能根 —— 外部 agent 在自己的 YAML/JSON 里声明 ``skills.roots``，
# 这里把「模式」解析成真实路径并复用同一套扫描规则。这样接入一个新外部 agent
# 时**不需要为它写技能发现代码**（用户要求：写好协议文件即可调用并回传）。
# ---------------------------------------------------------------------------
_HOME_RE = re.compile(r"^~(?=[/\\]|$)")
_WORKSPACE_TOKEN = re.compile(r"<(?:workspace|root|cwd)>", re.IGNORECASE)


def expand_skill_root(
    pattern: str,
    *,
    workspace: str,
    home_directory: Optional[str] = None,
) -> Optional[str]:
    """把一条技能根模式展开成绝对路径；空模式返回 ``None``。

    支持两种占位：``<workspace>``（会话工作区）与行首 ``~``（用户主目录）。
    相对路径按 ``workspace`` 解析 —— 描述符是声明式的，相对路径的参照系只能是
    工作区，否则同一份文件在不同 cwd 下会指向不同地方。
    """
    text = str(pattern or "").strip()
    if not text:
        return None
    home = os.path.abspath(home_directory) if home_directory else os.path.expanduser("~")
    text = _WORKSPACE_TOKEN.sub(lambda _m: workspace or "", text)
    if _HOME_RE.match(text):
        text = home + text[1:]
    if not os.path.isabs(text):
        text = os.path.join(workspace or os.getcwd(), text)
    return os.path.abspath(text)


def _infer_scope(pattern: str, resolved: str) -> str:
    """按模式形态推断作用域（描述符是声明式的，作用域只能从字面推断）。"""
    text = str(pattern or "").strip()
    if _WORKSPACE_TOKEN.search(text):
        return "project"
    if _HOME_RE.match(text):
        return "user"
    if not os.path.isabs(text):
        return "project"
    return "system"


def _infer_source(resolved: str) -> str:
    normalized = resolved.replace("\\", "/").lower()
    if "/.zcode/" in normalized:
        return "zcode"
    if "/.agents/" in normalized:
        return "agents"
    return "bundled"


def roots_from_patterns(
    patterns: list[str],
    *,
    workspace: str,
    home_directory: Optional[str] = None,
) -> list[ZcodeSkillRoot]:
    """把描述符里的 ``skills.roots`` 模式列表变成可扫描的技能根。

    优先级按声明顺序递增（先声明者优先级低、后声明者在前），与 ``SKILL.md`` 的
    「先到先得」合并规则一致。
    """
    roots: list[ZcodeSkillRoot] = []
    priority = 0
    for pattern in patterns or []:
        resolved = expand_skill_root(pattern, workspace=workspace, home_directory=home_directory)
        if resolved is None:
            continue
        priority += 10
        roots.append(
            ZcodeSkillRoot(
                path=resolved,
                scope=_infer_scope(pattern, resolved),  # type: ignore[arg-type]
                source=_infer_source(resolved),  # type: ignore[arg-type]
                priority=priority,
            )
        )
    return roots


def discover_skills_at_roots(
    patterns: list[str],
    *,
    workspace: str,
    home_directory: Optional[str] = None,
) -> SkillLoadOutcome:
    """按描述符声明的技能根发现技能（外部 agent 的通用技能发现入口）。

    只扫描述符声明的那几个根（``borrow_host_skills=False``）：宿主技能由调用方
    另行并入，混在一起会让「这个技能是谁的」变得不可分辨。
    """
    return discover_zcode_skills(
        workspace,
        home_directory=home_directory,
        roots=roots_from_patterns(patterns, workspace=workspace, home_directory=home_directory),
        borrow_host_skills=False,
    )


def host_skill_roots() -> list[ZcodeSkillRoot]:
    """主智能体的技能根 —— 外部 agent **借用**宿主技能的那一份。

    用户要求「外部 subagent 与主 agent 的 skill 可以相互借用」：方向一（主 agent
    看得到外部 agent 的技能）由 ``subagents.build_registry`` 并入；这里是方向二，
    把宿主技能库的目录作为**最低优先级**的根交给外部 agent —— 同名时外部 agent
    自己的技能胜出（项目级覆盖用户级的同一套直觉）。

    宿主技能库读不到时返回空列表：借不到技能不该让外部 agent 起不来。
    """
    try:
        from ....skills.store import configured_skills_root

        root = configured_skills_root()
    except Exception:  # pragma: no cover - 技能库不可用时视为没有
        return []
    return [
        ZcodeSkillRoot(
            path=str(root),
            scope="user",
            # 宿主技能对本内核而言是「随宿主分发」的，用 bundled 标记来源。
            source="bundled",
            # 优先级取一个大数：外部 agent 自己的技能（10/20/…）永远先被看到。
            priority=1_000_000,
        )
    ]


def generated_host_skill_names() -> set[str]:
    """宿主技能库里**自动生成**的技能名（如内置工具清单）。

    借用宿主技能时要排除它们：那是宿主自己写给宿主的说明书（工具清单之类），
    对本内核既不准确（工具链不同）又只是噪声。用户手写的技能才是真正可复用的。
    """
    try:
        from ....skills import SkillStore

        return {e.name for e in SkillStore().list() if getattr(e, "generated", False)}
    except Exception:  # pragma: no cover - 技能库读不到就不排除任何东西
        return set()


def _iter_skill_dirs(root: Path) -> list[Path]:
    """根自身含 SKILL.md 时根自身是一个技能；否则扫描一层子目录。

    与上游 ``scan.ts`` 同一规则：**只扫一层**，深层嵌套不作为技能（避免把技能包里的
    示例目录也当成技能）。
    """
    if not root.is_dir():
        return []
    out: list[Path] = []
    if (root / SKILL_FILE_NAME).is_file():
        out.append(root)
    try:
        children = sorted(root.iterdir())
    except OSError:
        return out
    for child in children:
        try:
            if child.is_dir() and not child.is_symlink() and (child / SKILL_FILE_NAME).is_file():
                out.append(child)
        except OSError:
            continue
    return out


def _load_metadata(directory: Path, root: ZcodeSkillRoot) -> Optional[SkillMetadata]:
    manifest = directory / SKILL_FILE_NAME
    try:
        raw = manifest.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    meta, _body = _frontmatter(raw)
    name = str(meta.get("name") or directory.name).strip()
    description = str(meta.get("description") or "").strip()
    if not name:
        return None
    when_to_use = str(meta.get("when_to_use") or meta.get("whenToUse") or "").strip() or None
    return SkillMetadata(
        name=name,
        description=description,
        path=str(manifest),
        directory=str(directory),
        root_path=root.path,
        scope=root.scope,  # type: ignore[arg-type]
        source=root.source,  # type: ignore[arg-type]
        safe_to_auto_load=True,
        frontmatter_keys=list(meta.keys()),
        when_to_use=when_to_use,
        policy=SkillPolicy(),
    )


def discover_zcode_skills(
    working_directory: str,
    *,
    home_directory: Optional[str] = None,
    roots: Optional[list[ZcodeSkillRoot]] = None,
    borrow_host_skills: bool = True,
    exclude_skill_names: Optional[set[str]] = None,
    host_skill_filter: Optional[set[str]] = None,
) -> SkillLoadOutcome:
    """发现 ZCode 技能。同名技能**先到先得**（优先级高的根胜出），其余记诊断。

    ``borrow_host_skills`` 打开时把宿主技能库也当成本内核的一个根（最低优先级）——
    这就是「外部 agent 借主 agent 的技能」；宿主里**自动生成**的技能会被自动排除
    （宿主自己的说明书对本内核不适用）。

    ``host_skill_filter`` 进一步把**借来的**技能收窄到这几个名字（``None`` = 整个
    宿主技能库都借）。自己技能根里的技能不受它影响 —— 别把「借什么」和「我有什么」
    混成一件事。
    """
    resolved_roots = roots if roots is not None else resolve_zcode_skill_roots(
        working_directory, home_directory=home_directory
    )
    excluded = set(exclude_skill_names or ())
    host_paths: set[str] = set()
    if borrow_host_skills:
        host_roots = host_skill_roots()
        resolved_roots = [*resolved_roots, *host_roots]
        host_paths = {os.path.abspath(root.path) for root in host_roots}
        excluded |= generated_host_skill_names()
    skills: list[SkillMetadata] = []
    diagnoses: list[SkillDiagnostic] = []
    total = 0
    seen: set[str] = set()

    for root in sorted(resolved_roots, key=lambda item: item.priority):
        base = Path(root.path)
        if not base.is_dir():
            continue
        for directory in _iter_skill_dirs(base):
            total += 1
            metadata = _load_metadata(directory, root)
            if metadata is None:
                diagnoses.append(
                    SkillDiagnostic(
                        code="skill_missing_name",
                        severity="warning",
                        message=f"Skipped skill without a usable name: {directory}",
                        path=str(directory),
                    )
                )
                continue
            key = metadata.qualified_name or metadata.name
            if metadata.name in excluded:
                continue
            # 借来的技能只留「用户勾中的那几个」；自己的根不受过滤影响。
            if (
                host_skill_filter is not None
                and os.path.abspath(root.path) in host_paths
                and metadata.name not in host_skill_filter
            ):
                continue
            if key in seen:
                diagnoses.append(
                    SkillDiagnostic(
                        code="skill_duplicate_name",
                        severity="warning",
                        message=f"Duplicate skill name shadowed by a higher-priority root: {metadata.name}",
                        path=metadata.path,
                        skill_name=metadata.name,
                    )
                )
                continue
            seen.add(key)
            skills.append(metadata)

    return SkillLoadOutcome(skills=skills, diagnostics=diagnoses, total_discovered=total)


class ZcodeSkillPort:
    """``SkillPort`` 的 ZCode 实现：从 ZCode 自己的技能根加载正文。"""

    def __init__(
        self,
        working_directory: str,
        *,
        home_directory: Optional[str] = None,
        disabled_paths: Optional[set[str]] = None,
        borrow_host_skills: bool = True,
        exclude_skill_names: Optional[set[str]] = None,
        host_skill_filter: Optional[set[str]] = None,
    ) -> None:
        self._working_directory = working_directory
        self._home = home_directory
        self._borrow_host_skills = bool(borrow_host_skills)
        self._exclude_names = set(exclude_skill_names or ())
        #: 借来的宿主技能只暴露这几个名字（``None`` = 整个宿主技能库）。
        self._host_filter = None if host_skill_filter is None else set(host_skill_filter)
        # 被禁用的 SKILL.md 绝对路径（对齐上游 ``collectDisabledPaths``）。
        self._disabled = {os.path.abspath(p) for p in (disabled_paths or set())}

    def _roots(self) -> list[ZcodeSkillRoot]:
        roots = resolve_zcode_skill_roots(self._working_directory, home_directory=self._home)
        if self._borrow_host_skills:
            roots = [*roots, *host_skill_roots()]
        return roots

    async def discover_skills(self, request: Any = None, options: Any = None) -> SkillLoadOutcome:
        working_directory = _req(request, "working_directory", "workingDirectory") or self._working_directory
        outcome = discover_zcode_skills(
            working_directory,
            home_directory=self._home,
            borrow_host_skills=self._borrow_host_skills,
            exclude_skill_names=self._exclude_names,
            host_skill_filter=self._host_filter,
        )
        if not self._disabled:
            return outcome
        kept = [s for s in outcome.skills if os.path.abspath(s.path) not in self._disabled]
        removed = len(outcome.skills) - len(kept)
        return SkillLoadOutcome(
            skills=kept,
            diagnostics=outcome.diagnostics,
            total_discovered=max(0, outcome.total_discovered - removed),
        )

    async def load_skill(self, request: Any, options: Any = None) -> Any:
        name = str(_req(request, "name") or "").strip()
        working_directory = _req(request, "working_directory", "workingDirectory") or self._working_directory
        max_bytes = int(_req(request, "max_bytes", "maxBytes") or _MAX_SKILL_BYTES)
        outcome = await self.discover_skills({"workingDirectory": working_directory})
        match = next(
            (s for s in outcome.skills if s.name == name or s.qualified_name == name), None
        )
        if match is None:
            # 只回一句「没找到」对模型没有校正作用：它会当成瞬时故障反复试，
            # 或者照着名字家族继续编 —— 实测模型看到清单里的 ``web-fetch`` /
            # ``web-summarizer`` 后，编出了并不存在的 ``web-dev``，还自造了个
            # ``zcode:`` 前缀（因为工具说明里写了 ``plugin:skill`` 这种限定名
            # 格式，而清单里一个带前缀的都没有）。
            # 把**当前真实可用的技能名**一并回给模型，它下一轮就能自我纠正。
            # 前缀 ``Skill not found: {name}`` 与上游保持一致（语义不变）。
            available = ", ".join(sorted({s.name for s in outcome.skills})) or "(none)"
            raise FileNotFoundError(f"Skill not found: {name} (available: {available})")
        manifest = Path(match.path)
        raw = manifest.read_text(encoding="utf-8", errors="replace")
        _meta_mapping, body = _frontmatter(raw)
        encoded = body.encode("utf-8")
        truncated = len(encoded) > max_bytes
        text = encoded[:max_bytes].decode("utf-8", "ignore") if truncated else body
        return _LoadedSkill(
            metadata=match,
            content=text,
            base_directory=match.directory,
            bytes_read=len(text.encode("utf-8")),
            size_bytes=len(encoded),
            truncated=truncated,
        )


@dataclass(slots=True)
class _LoadedSkill:
    """``SkillContent`` 的等价载体（内核按属性访问，不强制具体类）。"""

    metadata: SkillMetadata
    content: str
    base_directory: str
    bytes_read: int
    size_bytes: int
    truncated: bool


def _req(source: Any, snake_name: str, camel_name: Optional[str] = None) -> Any:
    if source is None:
        return None
    if isinstance(source, dict):
        if snake_name in source:
            return source[snake_name]
        return source.get(camel_name) if camel_name else None
    if hasattr(source, snake_name):
        return getattr(source, snake_name)
    return getattr(source, camel_name, None) if camel_name else None
