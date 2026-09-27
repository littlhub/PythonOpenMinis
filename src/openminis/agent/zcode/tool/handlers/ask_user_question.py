"""AskUserQuestion tool handler.

Ported from: packages/core/src/tool/handlers/ask-user-question.ts
Original package: @zcode/core (tool/handlers)
"""

from __future__ import annotations

from typing import Any, Optional

from ...contracts.errors import CoreErrorType, create_core_error
from ..types import ToolEntry, ToolHandler
from .ports import (
    ASK_USER_QUESTION_INPUT_JSON_SCHEMA,
    ASK_USER_QUESTION_OUTPUT_JSON_SCHEMA,
    ASK_USER_QUESTION_TOOL_NAME,
)

__all__ = [
    "ask_user_question_handler",
    "ask_user_question_tool_entry",
    "format_ask_user_question_model_content",
]

MAX_ASK_USER_QUESTION_MODEL_BYTES = 100_000

ASK_USER_QUESTION_DESCRIPTION = (
    "\n".join(
        [
        "Use this tool only when you are blocked on a decision that is genuinely the user's to make: one you cannot resolve from the request, the code, or sensible defaults.",
        "",
        "Usage notes:",
        '- Users will always be able to select "Other" to provide custom text input',
        "- Use multiSelect: true to allow multiple answers to be selected for a question",
        '- If you recommend a specific option, make that the first option in the list and add "(Recommended)" at the end of the label',
        "",
        'Plan mode note: To switch into plan mode, use EnterPlanMode (not this tool). Once in plan mode, use this tool to clarify requirements or choose between approaches BEFORE finalizing your plan. Do NOT use this tool to ask "Is my plan ready?", "Should I proceed?", or otherwise reference "the plan" in questions — the user cannot see the plan until you call ExitPlanMode for approval.',
        "",
        "Reserve this for decisions where the user's answer changes what you do next — not for choices with a conventional default or facts you can verify in the codebase yourself. In those cases pick the obvious option, mention it in your response, and proceed.",
        "",
        "Preview feature:",
        "Use the optional `preview` field on options when presenting concrete artifacts that users need to visually compare:",
        "- ASCII mockups of UI layouts or components",
        "- Code snippets showing different implementations",
        "- Diagram variations",
        "- Configuration examples",
        "",
        "Preview content is rendered as markdown in a monospace box. Multi-line text with newlines is supported. When any option has a preview, the UI switches to a side-by-side layout with a vertical option list on the left and preview on the right. Do not use previews for simple preference questions where labels and descriptions suffice. Note: previews are only supported for single-select questions (not multiSelect).",
        ]
    )
    + "\n"
)


async def ask_user_question_handler(input: Any, context: Any) -> Any:
    """Mirrors ``askUserQuestionHandler: ToolHandler``."""
    # PORT: ``AskUserQuestionAnsweredInputSchema.safeParse(input)`` —— executor 已归一化，
    # 这里做等价结构校验（必须有 questions）。校验失败即回退为业务失败。
    parsed: dict[str, Any] = input
    if not isinstance(parsed, dict) or parsed.get("questions") is None:
        raise create_core_error(
            CoreErrorType.TOOL_EXECUTION_FAILED,
            "AskUserQuestion requires user answers before execution",
            {
                "context": {
                    "issues": [
                        {
                            "message": "questions is required",
                            "path": ["questions"],
                        }
                    ],
                    "tool_call_id": context.tool_call_id,
                    "tool_name": ASK_USER_QUESTION_TOOL_NAME,
                },
                "recoverable": True,
            },
        )

    result: dict[str, Any] = {
        "questions": parsed["questions"],
        "answers": parsed.get("answers") or {},
    }
    if parsed.get("annotations") is not None:
        result["annotations"] = parsed["annotations"]
    return result


def format_ask_user_question_model_content(output: Any) -> str:
    """Mirrors ``formatAskUserQuestionModelContent(output)``."""
    result = output
    answers = result.get("answers") or {}
    if len(answers) == 0:
        return "The user did not provide answers to these questions. Continue using your best judgment; do not treat this as a rejection or invent a user preference."

    answers_text = ", ".join(
        [
            _format_answer_entry(question_text, answer, result.get("annotations"))
            for question_text, answer in answers.items()
        ]
    )

    unanswered_count = sum(
        1 for question in result.get("questions", []) if question.get("question") not in answers
    )
    if unanswered_count > 0:
        return (
            f"The user answered some questions and skipped {unanswered_count}. "
            f"Provided answers: {answers_text}. Continue with the provided answers and use your "
            "best judgment for the unanswered questions; do not invent user preferences."
        )

    return f"User has answered your questions: {answers_text}. You can now continue with the user's answers in mind."


def _format_answer_entry(question_text: str, answer: Any, annotations: Optional[dict[str, Any]]) -> str:
    """Mirrors the inline ``[questionText, answer]`` mapping body in formatAskUserQuestionModelContent."""
    annotation = (annotations or {}).get(question_text)
    parts = [f'"{question_text}"="{answer}"']
    if annotation and annotation.get("preview"):
        parts.append(f"selected preview:\n{annotation['preview']}")
    if annotation and annotation.get("notes"):
        parts.append(f"user notes: {annotation['notes']}")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# ToolEntry 构造
# ---------------------------------------------------------------------------

def _ask_user_question_permission() -> Any:
    from ...contracts.tools import ToolPermissionSpec

    return ToolPermissionSpec(
        permission="userInteraction.askQuestion",
        reason="AskUserQuestion pauses execution to collect answers from the user",
        risk_level="low",
        side_effect_scope="userInteraction",
        needs_approval=True,
        pattern_sources=["tool_name", "input"],
        always_allow_pattern_sources=["tool_name"],
        deny_priority="beforeAsk",
    )


def _ask_user_question_result_budget() -> Any:
    from ...contracts.tools import ToolResultBudget, ToolResultBudgetPreview

    return ToolResultBudget(
        max_inline_bytes=MAX_ASK_USER_QUESTION_MODEL_BYTES,
        max_model_bytes=MAX_ASK_USER_QUESTION_MODEL_BYTES,
        strategy="truncate",
        preview=ToolResultBudgetPreview(max_bytes=MAX_ASK_USER_QUESTION_MODEL_BYTES, direction="head"),
    )


def _ask_user_question_timeout() -> Any:
    from ...contracts.tools import TimedToolTimeoutPolicy

    return TimedToolTimeoutPolicy(default_ms=30000, max_ms=30000, allow_call_override=False)


def _ask_user_question_trace() -> Any:
    from ...contracts.tools import ToolTracePolicy

    return ToolTracePolicy(
        propagate_to_adapters=False,
        record_input="summary",
        record_output="summary",
    )


def _build_ask_user_question_tool_entry() -> ToolEntry:
    from ...contracts.tools import ToolCancellationPolicy

    return ToolEntry(
        capability="Ask the user multiple-choice clarification questions and continue with their answers",
        requires_user_interaction=True,
        metadata={
            "name": ASK_USER_QUESTION_TOOL_NAME,
            "description": ASK_USER_QUESTION_DESCRIPTION,
            "read_only": True,
            "destructive": False,
            "concurrent_safe": True,
            "requires_user_interaction": True,
            "timeout_ms": 30000,
            "max_output_bytes": MAX_ASK_USER_QUESTION_MODEL_BYTES,
            "side_effect_scope": "userInteraction",
            "risk_level": "low",
            "needs_approval": True,
        },
        handler=ask_user_question_handler,
        format_model_content=format_ask_user_question_model_content,
        input_schema=ASK_USER_QUESTION_INPUT_JSON_SCHEMA,
        output_schema=ASK_USER_QUESTION_OUTPUT_JSON_SCHEMA,
        runtime_input_schema=ASK_USER_QUESTION_INPUT_JSON_SCHEMA,
        runtime_output_schema=ASK_USER_QUESTION_OUTPUT_JSON_SCHEMA,
        permission=_ask_user_question_permission(),
        result_budget=_ask_user_question_result_budget(),
        timeout=_ask_user_question_timeout(),
        cancellation=ToolCancellationPolicy(
            supported=True,
            cleanup="none",
            user_visible_message="AskUserQuestion was cancelled before answers were returned",
        ),
        trace=_ask_user_question_trace(),
    )


ask_user_question_tool_entry: ToolEntry = _build_ask_user_question_tool_entry()
