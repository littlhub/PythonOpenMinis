"""Task notification formatting.

Ported from: packages/core/src/runtime-task/notification.ts
Original package: @zcode/core (runtime-task)
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

__all__ = [
    "TASK_NOTIFICATION_MAX_CHARS",
    "TaskNotificationInput",
    "format_task_notification",
    "format_workflow_provider_stop_error",
    "format_workflow_stall_notification",
    "format_workflow_escalation_notification",
    "truncate_task_notification",
    "escape_xml",
    "escape_local_bash_xml",
]

TASK_NOTIFICATION_MAX_CHARS = 120_000


class TaskNotificationInput(dict[str, Any]):
    """Mirrors ``interface TaskNotificationInput`` (a loose record shape).

    # PORT: TS interface → TypedDict-style dict subclass so call sites that
    # build the payload as ``dict(...)`` keep working while attribute access
    # in the formatters is spelled out explicitly below.
    """


_XML_ESCAPE_RE = re.compile(r"[<>&'\"]")
#: 单遍替换表：re.sub 不重扫替换结果，``&`` 不会被二次转义。
_XML_ESCAPE_TABLE = {
    "<": "&lt;",
    ">": "&gt;",
    "&": "&amp;",
    "'": "&apos;",
    '"': "&quot;",
}


def escape_xml(value: str) -> str:
    return _XML_ESCAPE_RE.sub(lambda m: _XML_ESCAPE_TABLE[m.group(0)], value)


def escape_local_bash_xml(value: str) -> str:
    """``<`` ``>`` ``&`` only — bash notifications keep quotes verbatim.

    # PORT: the provider-stop ``<error>`` block relies on this: quoted
    # ``run_id="…"`` strings inside the block must stay copy-pasteable, so
    # only the three XML-structural chars are escaped.
    """
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------------------
# workflow provider-stop copy (upstream: workflow-notification-copy.ts)
# ---------------------------------------------------------------------------

def format_workflow_provider_stop_error(failure: Any, run_id: str) -> str:
    """``<error>`` block for ``stopped(provider)``.

    按 ``providerStop.kind`` 选两句（什么错了 / 做什么），后接固定的事实行与 provider 原文行。
    文案里的每个占位都有兜底（provider 显示名缺席用 providerId，再缺席用 "the provider"）——
    一条通知绝不因为少一个字段就退化成空句。GetWorkflowRun 的 ``<error>`` 块共用同一函数。
    """
    details = failure.get("providerStop") if isinstance(failure, dict) else getattr(failure, "providerStop", None)
    if details is None:
        message = failure.get("message") if isinstance(failure, dict) else str(failure)
        return message

    provider = details.get("providerLabel") or details.get("providerId") or "the provider"
    provider_ref = (
        f"{details['providerLabel']} ({details['providerId']})"
        if details.get("providerLabel") is not None and details.get("providerId") is not None
        else provider
    )
    subagent = details.get("subagentName") or details.get("subagent") or "a subagent"
    phase = "" if details.get("phase") is None else f' (phase "{details["phase"]}")'
    model = details.get("modelId") or "the current model"
    code = details.get("providerCode") or details.get("reason")
    resume = f'then call ResumeWorkflowRun with run_id="{run_id}"'

    kind = details.get("kind")
    if kind == "auth":
        sentences = (
            f"Sign-in to {provider_ref} expired while subagent {subagent}{phase} was running.",
            f"Ask the user to sign in to {provider} again, {resume}. Finished steps are kept.",
        )
    elif kind == "not_configured":
        sentences = (
            f"Provider {details.get('providerId') or provider} is not configured on this machine, "
            f"so subagent {subagent}{phase} could not send its request.",
            f"Ask the user to configure the provider or switch this session to another model, {resume}.",
        )
    elif kind == "model_unavailable":
        sentences = (
            f"Model {model} is not available on {provider} (not in the user's plan, or retired).",
            f"Ask the user to switch this session to a model the plan includes, {resume}. "
            "Subagents follow the session's model.",
        )
    elif kind == "invalid_request":
        sentences = (
            f"{provider} rejected subagent {subagent}{phase}'s request as invalid (code {code}).",
            "Switching the session to another model usually clears this; "
            f"{resume}. If it stops again with the same code, show the raw message to the user.",
        )
    elif kind == "quota":
        if details.get("resetAt") is None:
            sentences = (
                f"{provider} reports the user's quota is exhausted (code {code}).",
                f"Ask the user to top up or upgrade the plan, or switch to another provider, {resume}.",
            )
        else:
            reset_at = details["resetAt"]
            reset_iso = reset_at.isoformat() if isinstance(reset_at, datetime) else str(reset_at)
            sentences = (
                f"{provider} reports the user's usage cap is reached (code {code}); "
                f"it resets at {reset_iso}.",
                f'Tell the user; after the reset, call ResumeWorkflowRun with run_id="{run_id}". '
                "Do not rebuild the workflow.",
            )
    else:
        sentences = (
            f"{provider} refused subagent {subagent}{phase}'s request with a permanent error (code {code}).",
            "Resolve it with the user (the raw message below says what the provider wants), "
            f"{resume}.",
        )

    facts_parts = [
        f"provider={details.get('providerId') or 'unknown'}",
        f"model={details.get('modelId') or 'unknown'}",
        f"subagent={details.get('subagent') or 'unknown'}",
    ]
    if details.get("phase") is not None:
        facts_parts.append(f"phase={details['phase']}")
    facts_parts.append(f"code={code}")

    parts = [
        sentences[0],
        sentences[1],
        " ".join(facts_parts),
    ]
    if details.get("rawMessage") is not None:
        parts.append(f"raw: {details['rawMessage']}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# run-level stall / escalation notifications
# ---------------------------------------------------------------------------

def format_workflow_stall_notification(input: dict[str, Any]) -> str:
    """run 级停滞的 run 中通知。

    播报的不是终态而是一个正在发生的事实——run 还在跑、只是 20 分钟没有一次模型请求成功。
    文案必须把两件事说死：它**不需要**模型做任何事（尤其不要取消 / 重建），以及用户若在等
    就该被告知。每个 stall 段恰好一条，不催办。
    """
    since_ms = input.get("sinceMs", 0)
    minutes = max(1, round(since_ms / 60_000))
    lines = [
        "[SYSTEM NOTIFICATION - NOT USER INPUT]",
        "This is an automated workflow event, NOT a message from the user.",
        "Do NOT interpret this as user acknowledgement, confirmation, or response to any pending question.",
        "",
        "<workflow-stall>",
        f'  <run-id>{escape_xml(input["runId"])}</run-id>',
        f'  <run>{escape_xml(input["runLabel"])}</run>',
        f"  <since-ms>{max(0, int(since_ms))}</since-ms>",
    ]
    if input.get("reason") is not None:
        lines.append(f"  <dominant-reason>{escape_xml(input['reason'])}</dominant-reason>")
    if input.get("cap") is not None:
        lines.append(f"  <cap>{input['cap']}</cap>")
    reason = input.get("reason")
    reason_clause = (
        "the provider keeps failing requests"
        if reason is None
        else f"the provider keeps answering {reason}"
    )
    cap = input.get("cap")
    cap_clause = "" if cap is None else f" (current fan-out {cap})"
    lines.extend(
        [
            "</workflow-stall>",
            "",
            f"Workflow run {input['runLabel']} ({input['runId']}) has not completed a model request "
            f"in {minutes} minutes; {reason_clause} and the run is retrying with backoff{cap_clause}.",
            "It is still running and needs nothing from you. Tell the user if they are waiting on it; "
            "they can stop it from the run card. Do not cancel or rebuild it on your own.",
        ]
    )
    return truncate_task_notification("\n".join(lines))


def format_workflow_escalation_notification(input: dict[str, Any]) -> str:
    """一个 actor 从**正在跑的** run 里升级上来的阻塞问题。

    与上面几个 formatter 的关键差别：它们播报的是**终态**（活干完了，读一下结果），这条播报的
    是**一个还没被满足的义务**——有一个 actor 此刻正停在那儿等回答，而且没有超时会替它兜底。
    所以文案必须把三件事说死：问题是什么、逐字的下一步（带 qid 的工具调用）、以及 run 并没有
    因此停下（否则模型会误以为整条工作流在等它，从而放下手上一切事）。
    """
    context = input.get("context")
    lines = [
        "[SYSTEM NOTIFICATION - NOT USER INPUT]",
        "This is an automated workflow event, NOT a message from the user.",
        "Do NOT interpret this as user acknowledgement, confirmation, or response to any pending question.",
        "",
        "<workflow-escalation>",
        f'  <run-id>{escape_xml(input["runId"])}</run-id>',
        f'  <run>{escape_xml(input["runLabel"])}</run>',
        f'  <question-id>{escape_xml(input["qid"])}</question-id>',
        f'  <subagent>{escape_xml(input["actor"])}</subagent>',
        f'  <question>{escape_xml(input["question"])}</question>',
    ]
    if context is not None and len(context) > 0:
        lines.append(f"  <context>{escape_xml(context)}</context>")
    lines.extend(
        [
            "</workflow-escalation>",
            "",
            f"Subagent {input['actor']} in workflow run {input['runLabel']} ({input['runId']}) "
            "escalated a blocking question and is parked on that call waiting for your answer.",
            f'Next step: call ResolveWorkflowQuestion with question_id="{input["qid"]}" and your answer.',
            "The run is still running: only the subagent that asked is parked — every other subagent "
            "and the script's control flow keep going. So do not drop what you are doing, but do not "
            "leave it unanswered either: nothing times out on its behalf.",
            "If this notification is ever lost, GetWorkflowRun lists the questions this run still owes an answer to.",
        ]
    )
    return truncate_task_notification("\n".join(lines))


# ---------------------------------------------------------------------------
# delivery guidance
# ---------------------------------------------------------------------------

def _workflow_delivery_guidance(
    status: str,
    stop_reason: Optional[str],
    has_artifacts: bool,
    run_id: str,
    script_path: Optional[str],
) -> str:
    """终态通知末尾的呈现指引。

    告诉主代理把 run 的结果当交付物呈现，而不是转述 JSON。completed 走
    「结论 → 发现与证据 → 已验证 / 仅判断 → 未覆盖」的顺序；其余终态先呈现抢救出的 reports，再讲
    失败与下一步。顺序是对呈现的要求，不是对 result 结构的要求——脚本返回别的形态时指引仍成立。

    三终态 × stopped 的五个 reason 各一支：下一步动作在每一支里都写死。
    """
    artifacts_sentence = (
        [
            "Artifacts listed above are already in front of the user as cards; refer to them by "
            "title and do not paste their contents. The one marked primary is the deliverable: "
            "point the user to it first.",
        ]
        if has_artifacts
        else []
    )
    artifacts_short = ["Artifacts listed above are already in front of the user."] if has_artifacts else []

    if status == "completed":
        return "\n".join(
            [
                "The workflow completed. Present its outcome to the user as a deliverable, in this order: "
                "the conclusion; each finding with its evidence (path and line, or the command and output "
                "that showed it); which findings were confirmed by a deterministic check or an independent "
                "subagent and which are judged only; what the run did not cover.",
                "The reported items above are individual findings: present them individually and keep "
                'their evidence. When the preview is partial (count greater than shown), say so and read '
                "the rest with GetWorkflowRun.",
                *artifacts_sentence,
                "Do not restate the phase graph or the script.",
            ]
        )
    if stop_reason == "user":
        # 用户停的 run：这是一个决定，不是一次意外。对所有未完成态一律说「resumable as-is」会让
        # 模型把用户刚停的 run 又续上了。
        return "\n".join(
            [
                "The user stopped this workflow on purpose. Do not resume it with ResumeWorkflowRun and "
                "do not amend or rebuild it unless the user asks you to.",
                "Present what it finished before it was stopped: the reported items above are finished "
                "findings — show them individually with their evidence. Then stop and wait for the user "
                "to say what happens next.",
                *artifacts_short,
            ]
        )
    if stop_reason == "model":
        script_note = (
            ""
            if script_path is None
            else f" Its script is at {script_path}: edit that file and pass `path`."
        )
        return "\n".join(
            [
                "You stopped this workflow with TaskStop. If you stopped it to fix the script, do that "
                "now: call AmendWorkflow with this run's ID and the corrected script — everything that "
                "settled before the stop is imported as cache, and the sooner the fix runs the less it "
                f"re-pays. (Next time, amend the running run directly: AmendWorkflow stops it for you.){script_note}",
                "Otherwise present what it finished: the reported items above are finished findings — show "
                "them individually with their evidence. Resume it unchanged only if that is what the user wants.",
                *artifacts_short,
            ]
        )
    if stop_reason == "provider":
        return "\n".join(
            [
                "A provider-side error stopped this run; the <error> block above names the cause and the "
                "fix. Present what the run finished: the reported items above are finished findings — show "
                "them individually with their evidence.",
                f'Then resolve the cause with the user before calling ResumeWorkflowRun with run_id="{run_id}" '
                "— finished steps replay from the journal. Do not rebuild the workflow.",
                *artifacts_short,
            ]
        )
    if stop_reason == "interrupted":
        return "\n".join(
            [
                "The process that owned this run exited before it finished. Present what it finished: "
                "the reported items above are finished findings — show them individually with their evidence.",
                f'Then call ResumeWorkflowRun with run_id="{run_id}" — finished steps replay from the '
                "journal and only the unfinished ones run again.",
                *artifacts_short,
            ]
        )
    if stop_reason == "superseded":
        # 正常情况下到不了：superseded 的终态通知在 coordinator 处被压下。留这一支是为了老端口 /
        # stub 万一送达时不说错话——尤其不能说「resume it」。
        return "\n".join(
            [
                "This run was stopped because you amended it: a newer run supersedes it and is already "
                "running. Do not resume this run and do not amend it again; wait for the successor's "
                "notification.",
                *artifacts_short,
            ]
        )
    if status == "stopped":
        # reason 缺席（老端口 / stub）：只说「停了、可续」，不猜是谁停的。
        return "\n".join(
            [
                "This workflow was stopped before it finished. Present what it salvaged first: the "
                "reported items above are finished findings — show them individually with their evidence.",
                f'It can be continued with ResumeWorkflowRun (run_id="{run_id}"); ask the user before '
                "resuming a run you did not stop yourself.",
                *artifacts_short,
            ]
        )
    if status == "errored":
        fix_line = (
            f'Fix the script and submit it with AmendWorkflow (run_id="{run_id}") so finished work is '
            "reused. ResumeWorkflowRun will refuse this run: replaying the same script would fail the same way."
            if script_path is None
            else f"The run's script is at {script_path}. Edit that file in place, then call "
            f'AmendWorkflow (run_id="{run_id}", path="{script_path}") so finished work is reused — do '
            "not paste the script inline. ResumeWorkflowRun will refuse this run: replaying the same "
            "script would fail the same way."
        )
        return "\n".join(
            [
                "The workflow script failed. Present what it salvaged first: the reported items above are "
                "finished findings — show them individually with their evidence. Then explain the failure "
                "and what it means for the user's request.",
                *artifacts_short,
                fix_line,
            ]
        )
    # 兜底（legacy `Workflow` 工具的 failed / killed，或未知词）：沿用旧的通用指引。
    return "\n".join(
        [
            "The workflow did not complete. Present what it salvaged first: the reported items above are "
            "finished findings — show them individually with their evidence. Then explain the failure and "
            "what it means for the user's request.",
            *artifacts_short,
            "If the script itself was wrong, a corrected script submitted with AmendWorkflow re-uses the "
            "finished work; if the process died (error code Interrupted), the run is resumable as-is.",
        ]
    )


# ---------------------------------------------------------------------------
# usage blocks
# ---------------------------------------------------------------------------

def _format_local_agent_usage(usage: Optional[dict[str, Any]]) -> Optional[str]:
    if not usage:
        return None
    segments: list[str] = []
    model_usage = usage.get("modelUsage")
    total_tokens = usage.get("totalTokens")
    if total_tokens is None and isinstance(model_usage, dict):
        total_tokens = model_usage.get("totalTokens")
    if total_tokens is not None:
        segments.append(f"<subagent_tokens>{total_tokens}</subagent_tokens>")
    if usage.get("toolUseCount") is not None:
        segments.append(f"<tool_uses>{usage['toolUseCount']}</tool_uses>")
    if usage.get("durationMs") is not None:
        segments.append(f"<duration_ms>{usage['durationMs']}</duration_ms>")
    if len(segments) == 0:
        return None
    return f"<usage>{''.join(segments)}</usage>"


def _format_usage(usage: Optional[dict[str, Any]]) -> list[str]:
    if not usage:
        return []
    lines: list[str] = []
    if usage.get("totalTokens") is not None:
        lines.append(f"<total-tokens>{usage['totalTokens']}</total-tokens>")
    if usage.get("toolUseCount") is not None:
        lines.append(f"<tool-uses>{usage['toolUseCount']}</tool-uses>")
    if usage.get("durationMs") is not None:
        lines.append(f"<duration-ms>{usage['durationMs']}</duration-ms>")
    model_usage = usage.get("modelUsage")
    if isinstance(model_usage, dict):
        for key, tag in (
            ("inputTokens", "input-tokens"),
            ("outputTokens", "output-tokens"),
            ("cacheReadTokens", "cache-read-tokens"),
            ("cacheWriteTokens", "cache-write-tokens"),
            ("reasoningTokens", "reasoning-tokens"),
        ):
            if model_usage.get(key) is not None:
                lines.append(f"<{tag}>{model_usage[key]}</{tag}>")
    return lines


# ---------------------------------------------------------------------------
# per-task-type formatters
# ---------------------------------------------------------------------------

def _format_local_bash_task_notification(input: dict[str, Any]) -> str:
    lines = [
        "<task-notification>",
        f"<task-id>{escape_local_bash_xml(str(input['taskId']))}</task-id>",
    ]
    if input.get("toolUseId"):
        lines.append(f"<tool-use-id>{escape_local_bash_xml(str(input['toolUseId']))}</tool-use-id>")
    if input.get("outputFile"):
        lines.append(f"<output-file>{escape_local_bash_xml(str(input['outputFile']))}</output-file>")
    lines.append(f"<status>{escape_local_bash_xml(str(input['status']))}</status>")
    lines.append(f"<summary>{escape_local_bash_xml(str(input['summary']))}</summary>")
    lines.append("</task-notification>")
    return "\n".join(lines)


def _format_local_agent_task_notification(input: dict[str, Any]) -> str:
    lines = [
        "<task-notification>",
        f"<task-id>{escape_xml(str(input['taskId']))}</task-id>",
    ]
    if input.get("toolUseId"):
        lines.append(f"<tool-use-id>{escape_xml(str(input['toolUseId']))}</tool-use-id>")
    if input.get("outputFile"):
        lines.append(f"<output-file>{escape_xml(str(input['outputFile']))}</output-file>")
    lines.append(f"<status>{escape_xml(str(input['status']))}</status>")
    lines.append(f"<summary>{escape_xml(str(input['summary']))}</summary>")
    if input.get("result") is not None:
        lines.append(f"<result>{escape_xml(str(input['result']))}</result>")
    if input.get("error") is not None:
        lines.append(f"<error>{escape_xml(str(input['error']))}</error>")

    usage_line = _format_local_agent_usage(input.get("usage"))
    if usage_line:
        lines.append(usage_line)
    lines.append("</task-notification>")

    return truncate_task_notification("\n".join(lines))


def _format_local_workflow_task_notification(input: dict[str, Any]) -> str:
    lines = [
        "<task-notification>",
        f"<task-id>{escape_xml(str(input['taskId']))}</task-id>",
    ]
    if input.get("toolUseId"):
        lines.append(f"<tool-use-id>{escape_xml(str(input['toolUseId']))}</tool-use-id>")
    if input.get("outputFile"):
        lines.append(f"<output-file>{escape_xml(str(input['outputFile']))}</output-file>")
    # dwf 的三个终态词优先于追踪器的通用词（legacy `Workflow` 不带 runStatus，逐字节不变）。
    lines.append(f"<status>{escape_xml(str(input.get('runStatus') or input['status']))}</status>")
    if input.get("stopReason") is not None:
        lines.append(f"<stop-reason>{escape_xml(str(input['stopReason']))}</stop-reason>")
    if input.get("description"):
        lines.append(f"<description>{escape_xml(str(input['description']))}</description>")
    lines.append(f"<summary>{escape_xml(str(input['summary']))}</summary>")
    if input.get("result") is not None:
        lines.append(f"<result>{escape_xml(str(input['result']))}</result>")
    # provider 停下：`<error>` 是一整块表驱动文案（多行）；其余终态照旧一句 message。
    failure = input.get("failure")
    provider_stop_error = None
    if isinstance(failure, dict) and failure.get("providerStop") is not None:
        provider_stop_error = format_workflow_provider_stop_error(failure, str(input["taskId"]))
    if provider_stop_error is not None:
        # 块里有要让模型照抄的 `run_id="…"`：只转义 <>&，引号原样（与 bash 通知同一策略）。
        lines.append(f"<error>\n{escape_local_bash_xml(provider_stop_error)}\n</error>")
    elif input.get("error") is not None:
        lines.append(f"<error>{escape_xml(str(input['error']))}</error>")
    # 渐进产物排在 result / error **之后**：run 的收场是模型首先要读的，产物是补充材料。
    # 顺序也决定了 120k 总截断先斩谁——被斩掉的应该是这一节，而不是 run 的结果。
    reports = input.get("reports")
    if reports is not None:
        shown_attr = (
            f' shown="{reports["shown"]}"'
            if reports.get("shown", 0) < reports.get("count", 0)
            else ""
        )
        lines.extend(
            [
                f'<reports count="{reports["count"]}"{shown_attr}>',
                escape_xml(str(reports.get("preview", ""))),
                "</reports>",
            ]
        )
    # 产物排在 `<reports>` **之后**：过程产物是正文，交付物清单是索引——用户已经在屏幕上看到卡片了，
    # 模型只需要知道有哪些、叫什么。这个顺序也决定了 120k 总截断先斩谁：先斩这一节。
    artifacts = input.get("artifacts")
    if artifacts is not None:
        shown_attr = (
            f' shown="{artifacts["shown"]}"'
            if artifacts.get("shown", 0) < artifacts.get("count", 0)
            else ""
        )
        lines.extend(
            [
                f'<artifacts count="{artifacts["count"]}"{shown_attr}>',
                escape_xml(str(artifacts.get("preview", ""))),
                "</artifacts>",
            ]
        )
    lines.append("</task-notification>")
    # 呈现指引排在最后：120k 总截断先斩指引再斩产物——指引是补充材料，产物是正文。
    if input.get("deliveryGuidance"):
        lines.append("")
        lines.append(
            _workflow_delivery_guidance(
                status=input.get("runStatus") or input["status"],
                stop_reason=input.get("stopReason"),
                has_artifacts=input.get("artifacts") is not None,
                run_id=str(input["taskId"]),
                script_path=input.get("scriptPath"),
            )
        )

    return truncate_task_notification("\n".join(lines))


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def format_task_notification(input: dict[str, Any]) -> str:
    if input.get("taskType") == "local_agent":
        return _format_local_agent_task_notification(input)
    if input.get("taskType") == "local_bash":
        return _format_local_bash_task_notification(input)
    if input.get("taskType") == "local_workflow":
        return _format_local_workflow_task_notification(input)

    lines = [
        "<task-notification>",
        f"  <task-id>{escape_xml(str(input['taskId']))}</task-id>",
    ]

    if input.get("toolUseId"):
        lines.append(f"  <tool-use-id>{escape_xml(str(input['toolUseId']))}</tool-use-id>")
    lines.append(f"  <task-type>{escape_xml(str(input['taskType']))}</task-type>")
    if input.get("agentId"):
        lines.append(f"  <agent-id>{escape_xml(str(input['agentId']))}</agent-id>")
    if input.get("subagentType"):
        lines.append(f"  <subagent-type>{escape_xml(str(input['subagentType']))}</subagent-type>")
    if input.get("outputFile"):
        lines.append(f"  <output-file>{escape_xml(str(input['outputFile']))}</output-file>")
    if input.get("stdoutFile"):
        lines.append(f"  <stdout-file>{escape_xml(str(input['stdoutFile']))}</stdout-file>")
    if input.get("stderrFile"):
        lines.append(f"  <stderr-file>{escape_xml(str(input['stderrFile']))}</stderr-file>")
    lines.append(f"  <status>{escape_xml(str(input['status']))}</status>")
    if input.get("description"):
        lines.append(f"  <description>{escape_xml(str(input['description']))}</description>")
    lines.append(f"  <summary>{escape_xml(str(input['summary']))}</summary>")
    if input.get("result") is not None:
        lines.append(f"  <result>{escape_xml(str(input['result']))}</result>")
    if input.get("error") is not None:
        lines.append(f"  <error>{escape_xml(str(input['error']))}</error>")
    usage_lines = _format_usage(input.get("usage"))
    if usage_lines:
        lines.append("  <usage>")
        lines.extend(f"    {line}" for line in usage_lines)
        lines.append("  </usage>")
    lines.append("</task-notification>")

    return truncate_task_notification("\n".join(lines))


def truncate_task_notification(value: str) -> str:
    if len(value) <= TASK_NOTIFICATION_MAX_CHARS:
        return value
    return f"{value[:TASK_NOTIFICATION_MAX_CHARS]}\n[truncated]"
