"""Workflow published artifacts — one-line model-facing projections.

Ported from: packages/core/src/tool/executor/workflow-published-artifacts.ts
Original package: @zcode/core (tool/executor)

workflow run 的**用户面产物**在模型面的一行投影，被三处共用：
完成通知的 ``<artifacts>`` 节（background_tasks.py）、manifest 载荷的
``artifacts[]``（同上）、以及 GetWorkflowRun 模型文本的 ``<artifacts>`` 截面。

⚠ 术语：本文件里的 artifact 是**脚本经 ``artifact.*`` 发布给用户看的产出**——
一个文件、一段 markdown、或一张由标签 ``report`` 喂养的图 / 表 / 指标 / 看板。
它与隔壁 ``workflow_artifact.py`` 的 ``serialize_workflow_artifact`` 是
**两件不同的东西**：那个 artifact 是脚本的顶层返回值（引擎内部的
``RunSettlement.artifact``），给模型看的。两者在完成通知里同时出现
（``<result>`` 与 ``<artifacts>``），所以文件也刻意分开。
"""

from __future__ import annotations

from typing import Any, Optional, Protocol

__all__ = [
    "WORKFLOW_ARTIFACTS_NOTIFICATION_MAX_LINES",
    "WORKFLOW_ARTIFACTS_INTROSPECTION_MAX_LINES",
    "PublishedArtifactSummary",
    "format_published_artifact_line",
    "build_workflow_artifacts_notification_section",
    "build_workflow_artifacts_manifest_section",
    "to_published_artifact_summaries",
]


class PublishedArtifactSummary(dict[str, Any]):
    """一件产物在模型面需要的全部事实。三处的输入形状不同，取交集后收成这一个结构。

    键（camelCase，与 TS 原样一致）：
    - ``id`` / ``kind`` / ``version`` — 恒在场
    - ``title`` / ``contentType`` — 可选
    - ``bytes`` — 内容产物（file / markdown）最新版的字节数；预置看板没有字节
    - ``itemCount`` — 预置看板收到的标签 report 条数（数据量）；内容产物恒 0
    - ``primary`` — run 的交付物：清单以它带头，行上标 ``primary``
    - ``description`` — 只有交付物的这一句进 manifest
    """


#: manifest 里交付物 ``description`` 的界（``ARTIFACT_CAPS.maxDescriptionLength``，与 shared 的 zod 同值）。
WORKFLOW_ARTIFACT_DESCRIPTION_MAX_CHARS = 500


def _primary_first(artifacts: list[Any]) -> list[Any]:
    """交付物带头，其余保持原顺序（稳定排序）。

    三处清单都先过这一步再截，所以上界砍不到交付物。
    端口的 ``artifactsOf`` 已经排过；这里再排一次是为了快照与通知那条路
    （它们读的是投影的顺序）。
    """
    return sorted(artifacts, key=lambda a: 0 if a.get("primary") is True else 1)


#: 完成通知里 ``<artifacts>`` 最多列几行。载荷上界（≤ 8）与文本行数刻意同值。
WORKFLOW_ARTIFACTS_NOTIFICATION_MAX_LINES = 8
#: ``GetWorkflowRun`` 的 ``<artifacts>`` 截面上界（有界 32 = ``ARTIFACT_CAPS`` 的每 run 上限）。
WORKFLOW_ARTIFACTS_INTROSPECTION_MAX_LINES = 32
#: manifest 载荷里 title 的界（``ARTIFACT_CAPS.maxTitleLength``，与 shared 的 zod 同值）。
WORKFLOW_ARTIFACT_TITLE_MAX_CHARS = 120
#: id 的界（``ARTIFACT_CAPS.maxIdLength``，与 shared 的 zod 同值）。
WORKFLOW_ARTIFACT_ID_MAX_CHARS = 64
#: contentType 的界（与 shared 的 zod 同值；MIME 本身没有这么长的合法形态）。
WORKFLOW_ARTIFACT_CONTENT_TYPE_MAX_CHARS = 255

#: 预置看板的四个成员：它们没有字节，数据量是标签 report 的条数。
PRESET_ARTIFACT_KINDS = frozenset({"chart", "table", "metrics", "board"})


def format_published_artifact_line(artifact: Any) -> str:
    """一件产物 → 一行：``- {id} ({kind}, v{version}, {contentType}, {bytes} bytes): {title}``。

    缺席的部件整段省略而不是留一个空位—— ``(file, v2, , : )`` 读起来像数据坏了。
    预置看板把字节换成 ``{n} items``：一张图的「大小」是它有多少个点，
    字节数对它没有意义（它根本没有字节）。
    ``title`` 缺席时连冒号一起省略（facade 的默认 title 就是 id，重复一遍没有信息）。

    刻意**不给 ``uri``**：模型读不了 tool-artifact store。要内容就走
    ``GetWorkflowRun``，或者让子代理去读工作区里的原路径。
    """
    parts: list[str] = [
        artifact["kind"], "primary"
    ] if artifact.get("primary") is True else [artifact["kind"]]
    parts.append(f"v{artifact['version']}")
    content_type = artifact.get("contentType")
    if content_type is not None and len(content_type) > 0:
        parts.append(content_type)
    if artifact["kind"] in PRESET_ARTIFACT_KINDS:
        if artifact.get("itemCount") is not None:
            n = artifact["itemCount"]
            parts.append(f"{n} item" + ("" if n == 1 else "s"))
    elif artifact.get("bytes") is not None:
        parts.append(f"{artifact['bytes']} bytes")
    head = f"- {artifact['id']} ({', '.join(parts)})"
    title = artifact.get("title")
    title = title.strip() if isinstance(title, str) else None
    if title is None or len(title) == 0:
        return head
    return f"{head}: {title}"


def build_workflow_artifacts_notification_section(
    artifacts: Optional[list[Any]],
    max_lines: int,
) -> Optional[dict[str, Any]]:
    """产物清单 → 完成通知 / GetWorkflowRun 的 ``<artifacts>`` 一节。

    与 ``<reports>`` 同规：``count`` 恒是真实总数，``count ≠ shown`` 就是
    「清单是局部的、全量经 run id 可取」的那个信号。
    零件时返回 ``None``，调用方据此让整节缺席——不发一节空的 ``<artifacts>``。
    """
    if artifacts is None or len(artifacts) == 0:
        return None
    lines = [format_published_artifact_line(a) for a in _primary_first(artifacts)[:max_lines]]
    return {"count": len(artifacts), "shown": len(lines), "preview": "\n".join(lines)}


_MANIFEST_ARTIFACT_KINDS: tuple[str, ...] = (
    "file",
    "markdown",
    "chart",
    "table",
    "metrics",
    "board",
)


def _manifest_artifact_kind(kind: Any) -> Optional[str]:
    for known in _MANIFEST_ARTIFACT_KINDS:
        if known == kind:
            return known
    return None


def build_workflow_artifacts_manifest_section(
    artifacts: Optional[list[Any]],
) -> Optional[dict[str, Any]]:
    """manifest 载荷的 ``artifacts`` 与 ``artifactsTruncated``。

    与上面那一节**同源不同形**：GUI 把每条渲染成一枚 chip（图标 + 截短的 title），
    所以载荷带的是结构化字段而不是拼好的一行；``bytes`` / ``itemCount`` 刻意不进载荷——
    chip 上放不下，点开侧板即可看到全部。截断在**构造前**完成：载荷随 turnHeader row
    走协议，超界会让整行落库时拒收。

    ``kind`` 不在六个字面量里的条目整条丢弃（不猜、不归一）：一个未知种类的 chip
    在 GUI 上没有图标可画，而放行它会让整个载荷被拒掉，连带其余 chip 一起消失。

    零件时返回 ``None``，调用方据此让两个字段一起缺席。
    """
    if artifacts is None or len(artifacts) == 0:
        return None
    entries: list[dict[str, Any]] = []
    # 交付物先进清单：8 的上界永远砍不到它。
    for artifact in _primary_first(artifacts):
        if len(entries) >= WORKFLOW_ARTIFACTS_NOTIFICATION_MAX_LINES:
            break
        kind = _manifest_artifact_kind(artifact.get("kind"))
        # id 是 chip 的身份键，截短就是编造一个不存在的产物——超界只能整条丢，与未知 kind 同规。
        # 两者都是「一条坏行不该把其余 chip 一起带走」：放行会让整份载荷被拒收。
        artifact_id = artifact.get("id")
        if kind is None or artifact_id is None or len(artifact_id) == 0:
            continue
        if len(artifact_id) > WORKFLOW_ARTIFACT_ID_MAX_CHARS:
            continue
        # 版本号是 chip 的定位键之一，shared 的 zod 要求正整数；非正整数同样整条丢。
        version = artifact.get("version")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            continue
        raw_title = artifact.get("title")
        title = raw_title.strip() if isinstance(raw_title, str) else None
        raw_content_type = artifact.get("contentType")
        content_type = (
            raw_content_type
            if isinstance(raw_content_type, str)
            and len(raw_content_type) <= WORKFLOW_ARTIFACT_CONTENT_TYPE_MAX_CHARS
            else None
        )
        raw_description = artifact.get("description")
        description = (
            raw_description.strip()
            if artifact.get("primary") is True and isinstance(raw_description, str)
            else None
        )
        entry: dict[str, Any] = {"id": artifact_id, "kind": kind, "version": version}
        if title is not None and len(title) > 0:
            entry["title"] = title[:WORKFLOW_ARTIFACT_TITLE_MAX_CHARS]
        if content_type is not None:
            entry["contentType"] = content_type
        if artifact.get("primary") is True:
            entry["primary"] = True
        if description is not None and len(description) > 0:
            entry["description"] = description[:WORKFLOW_ARTIFACT_DESCRIPTION_MAX_CHARS]
        entries.append(entry)
    if len(entries) == 0:
        return None
    # 截断诚实：被上界砍掉的、以及被种类过滤掉的，都算「还有更多没画出来」——
    # 判据因此是「画出来的比总数少」，而不是「命中了上界」。
    result: dict[str, Any] = {"artifacts": entries}
    if len(entries) < len(artifacts):
        result["artifactsTruncated"] = True
    return result


def to_published_artifact_summaries(value: Any) -> Optional[list[Any]]:
    """快照 / 端口详情上的产物 → 本模块的输入形状。

    ``bytes`` 只在版本项上（端口的顶层刻意不带它），所以从**最新版**取——
    那正是清单要描述的那一版。

    形状照样防御性检查：输入跨包而来（端口实现、或一条冷恢复回来的行），
    一个坏条目不该让整节消失。不合形的整条跳过。
    """
    if not isinstance(value, list) or len(value) == 0:
        return None
    summaries: list[dict[str, Any]] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        record: dict[str, Any] = entry
        artifact_id = record.get("id") if isinstance(record.get("id"), str) else None
        kind = record.get("kind") if isinstance(record.get("kind"), str) else None
        version = record.get("version") if isinstance(record.get("version"), int) and not isinstance(record.get("version"), bool) else None
        if artifact_id is None or kind is None or version is None:
            continue
        versions = record.get("versions") if isinstance(record.get("versions"), list) else None
        latest = versions[-1] if versions else None
        bytes_val: Optional[float] = None
        if isinstance(latest, dict) and isinstance(latest.get("bytes"), (int, float)) and not isinstance(latest.get("bytes"), bool):
            bytes_val = latest["bytes"]
        summary: dict[str, Any] = {"id": artifact_id, "kind": kind, "version": version}
        if isinstance(record.get("title"), str):
            summary["title"] = record["title"]
        if isinstance(record.get("contentType"), str):
            summary["contentType"] = record["contentType"]
        if bytes_val is not None:
            summary["bytes"] = bytes_val
        if isinstance(record.get("itemCount"), (int, float)) and not isinstance(record.get("itemCount"), bool):
            summary["itemCount"] = record["itemCount"]
        if record.get("primary") is True:
            summary["primary"] = True
        if isinstance(record.get("description"), str):
            summary["description"] = record["description"]
        summaries.append(summary)
    if len(summaries) == 0:
        return None
    return summaries
