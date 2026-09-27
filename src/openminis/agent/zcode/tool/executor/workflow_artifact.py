"""Workflow artifact serialization + notification report sections.

Ported from: packages/shared/src/zcode-protocol-v4/workflow-artifact.ts
             (re-exported by packages/core/src/tool/executor/workflow-artifact.ts)
Original package: @zcode/shared / @zcode/core (tool/executor)

# PORT: the upstream core file re-exports ``serializeWorkflowArtifact`` from
# ``@zcode/shared`` and adds two report-section builders. Both halves live in
# one Python module here.
"""

from __future__ import annotations

import json
from typing import Any, Optional

__all__ = [
    "serialize_workflow_artifact",
    "build_workflow_reports_notification_section",
    "build_workflow_reports_manifest_section",
]


def serialize_workflow_artifact(value: Any) -> Optional[str]:
    """workflow run 产物的序列化规则。

    被 core 内的两处共用：完成通知的 ``<result>``（background_tasks.py）与
    runtime task 条目上的 ``resultText``（background_task_registry.py，
    TaskOutput 的唯一来源）。

    - ``None`` → 返回 ``None``（调用方据此让该字段整体缺席）
    - ``str`` → 原样返回
    - 其他值 → ``json.dumps(value, indent=2)``
    - dumps 回 ``None``（undefined/function/symbol 在 Python 侧对应
      不可序列化对象）或抛错（循环引用）→ 退化为 ``str(value)``，绝不把值整段丢掉。

    本文件**不设任何长度上限**：通知端有 120k 截断、TaskOutput 端有
    artifact 预算、投影端有 ``maxReportPreviewLength``，界属于各自的边界。
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        text = json.dumps(value, indent=2, ensure_ascii=False, default=str)
        return text if text is not None else str(value)
    except Exception:
        return str(value)


#: 完成通知里 ``<reports>`` 那一节的预算：整节最多这么多字符。
#:
#: 通知端本来就有 120k 的总截断，但那是**最后一道**闸：它先斩到的是排在后面的字段。
#: 渐进产物可以有 256 条，任由它铺开会把 ``<result>`` 与 ``<error>`` 挤出通知——
#: 而那两个才是模型首先要看的。所以这一节自己带预算。
WORKFLOW_REPORTS_PREVIEW_MAX_CHARS = 8_000
#: 单条的界。一条 32KB 的产物不该吃掉整节预算，让后面十条一条也进不来。
WORKFLOW_REPORT_ITEM_MAX_CHARS = 2_000

#: manifest 载荷里逐条产物预览的界（shared schema：≤500 字符）。
WORKFLOW_NOTIFICATION_REPORT_ITEM_MAX_CHARS = 500
#: manifest 载荷里预览最多几条（shared schema：≤8 条）。
WORKFLOW_NOTIFICATION_REPORTS_MAX_ITEMS = 8


def _clip(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def build_workflow_reports_notification_section(
    items: Optional[list[Any]],
) -> Optional[dict[str, Any]]:
    """journal 里的 report 条目 → 完成通知的 ``<reports>`` 一节（completed / failed / cancelled 通用）。

    截断与计数**两者都重要**：计数才是让主 agent 知道「预览是局部的、全量可经 run id
    取回」的那个信号。只给预览会让模型以为它看到了全部；只给计数则等于什么产物都没回。

    零条时返回 ``None``，调用方据此让整节缺席——不发一节空的 ``<reports>``。
    """
    if items is None or len(items) == 0:
        return None

    lines: list[str] = []
    used = 0
    for index, item in enumerate(items):
        text = _clip(serialize_workflow_artifact(item) or "", WORKFLOW_REPORT_ITEM_MAX_CHARS)
        line = f"[{index + 1}] {text}"
        # 至少给一条：一条超长产物也比"报过 11 条但一条都看不到"有用。
        if lines and used + len(line) + 1 > WORKFLOW_REPORTS_PREVIEW_MAX_CHARS:
            break
        lines.append(line)
        used += len(line) + 1

    return {"count": len(items), "shown": len(lines), "preview": "\n".join(lines)}


def build_workflow_reports_manifest_section(
    items: Optional[list[Any]],
) -> Optional[dict[str, Any]]:
    """manifest 载荷（``WorkflowNotificationMeta.reports``）的 ``<reports>`` 一节。

    与上面那个**通知文本**用的一节同源同逐条序列化（``serialize_workflow_artifact``），
    只是产出 **list[str]** 而非拼接好的一段文本——manifest 由 GUI 逐条渲染成编号预览，
    拼接会让它再拆一次。

    界与通知文本那一节刻意不同：这里逐条 ≤500、最多 8 条（同步于 shared 的
    ``workflowNotificationMetaSchema``——超界会让 turnHeader row 落库时拒收）。
    ``count`` 恒是**真实总条数**：``count ≠ shown`` 就是「预览是局部的、全量经 run id
    可取」的信号。

    零条时返回 ``None``，调用方据此让整字段缺席。
    """
    if items is None or len(items) == 0:
        return None

    preview: list[str] = []
    for item in items:
        if len(preview) >= WORKFLOW_NOTIFICATION_REPORTS_MAX_ITEMS:
            break
        preview.append(
            _clip(
                serialize_workflow_artifact(item) or "",
                WORKFLOW_NOTIFICATION_REPORT_ITEM_MAX_CHARS,
            )
        )
    return {"count": len(items), "shown": len(preview), "preview": preview}
