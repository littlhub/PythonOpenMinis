"""Dynamic sections — session guidance, dynamic behavior, output style, context management.

Ported from: packages/core/src/context/dynamic-sections.ts
Original package: @zcode/core (context)
"""

from __future__ import annotations

from typing import Optional, Sequence

from .types import ContextSection, ContextSource, OutputStylePromptConfig
from .utils import estimate_tokens

COMMUNICATION_PROMPTS = {
    "default": "Write code that reads like the surrounding code: match its comment density, naming, and idiom.",
    "additional": {
        "beforeDefault": "\n".join(
            [
                "# Communicating with the user",
                "",
                "Your text output is what the user reads; they usually can't see your thinking or the raw tool results. Write it for a teammate who stepped away and is catching up, not for a log file: they don't know the codenames or shorthand you created along the way, and they didn't watch your process unfold. Before your first tool call, say in a sentence what you're about to do; while working, give brief updates when you find something load-bearing or change direction.",
                "",
                "Text you write between tool calls may not be shown to the user. Everything the user needs from this turn — answers, summaries, findings, conclusions, deliverables — must be in the final text message of your turn, with no tool calls after it. Keep text between tool calls to brief status notes. If something important appeared only mid-turn or in your thinking, restate it in that final message.",
                "",
                'Lead with the outcome. Your first sentence after finishing should answer "what happened" or "what did you find" — the thing the user would ask for if they said "just give me the TLDR." Supporting detail and reasoning come after, for readers who want them.',
                "",
                "Being readable and being concise are different things, and readable matters more. If the user has to reread your summary or ask you to explain, any time saved by brevity is gone. The way to keep output short is to be selective about what you include (drop details that don't change what the reader would do next), not to compress the writing into fragments, abbreviations, arrow chains like `A → B → fails`, or jargon. What you do include, write in complete sentences with the technical terms spelled out. Don't make the reader cross-reference labels or numbering you invented earlier; say what you mean in place.",
                "",
                "Match the response to the question: a simple question gets a direct answer in prose, not headers and sections. Use tables only for short enumerable facts, with explanations in the surrounding prose rather than the cells. Calibrate to the user — a bit tighter for an expert, more explanatory for someone newer.",
            ]
        ),
        "afterDefault": "Only write a code comment to state a constraint the code itself can't show — never to say where it came from, what the next line does, or why your change is correct; that's you talking to the reviewer, not the next reader, and it's noise the moment the PR merges.",
    },
}

CONTEXT_MANAGEMENT_PROMPTS = {
    "default": "\n".join(
        [
            "# Context management",
            "When the conversation grows long, some or all of the current context is summarized; the summary, along with any remaining unsummarized context, is provided in the next context window so work can continue — you don't need to wrap up early or hand off mid-task.",
        ]
    ),
    "additional": "\n".join(
        [
            "When you have enough information to act, act. Do not re-derive facts already established in the conversation, re-litigate a decision the user has already made, or narrate options you will not pursue. If you are weighing a choice, give a recommendation, not an exhaustive survey",
            "",
            "You are operating autonomously. The user is not watching in real time and cannot answer questions mid-task, so asking 'Want me to…?' or 'Shall I…?' will block the work. For reversible actions that follow from the original request, proceed without asking. Stop only for destructive actions or genuine scope changes the user must decide. Offering follow-ups after the task is done is fine; asking permission before doing the work is not.",
            "",
            "Exception: when the user is describing a problem, asking a question, or thinking out loud rather than requesting a change, the deliverable is your assessment. Report your findings and stop. Don't apply a fix until they ask for one.",
            "",
            "Before ending your turn, check your last paragraph. If it is a plan, an analysis, a question, a list of next steps, or a promise about work you have not done ('I'll…', 'let me know when…'), do that work now with tool calls. That includes retrying after errors and gathering missing information yourself. Do not stop because the context or session is long. End your turn only when the task is complete or you are blocked on input only the user can provide.",
            "",
            "Before running a command that changes system state — restarts, deletes, config edits — check that the evidence actually supports that specific action. A signal that pattern-matches to a known failure may have a different cause.",
        ]
    ),
}


def build_session_guidance_section(
    tool_names: Sequence[str], has_skills: bool = False
) -> Optional[ContextSection]:
    tools = set(tool_names)
    lines = ["# Session-specific guidance"]

    # 当前不输出 Agent 指导段。
    # if (tools.has("Agent")) {
    #   lines.push("- Use the Agent tool with specialized agents when the task at hand matches the agent's description. Subagents are valuable for parallelizing independent queries or for protecting the main context window from excessive results, but they should not be used excessively when not needed. Importantly, avoid duplicating work that subagents are already doing - if you delegate research to a subagent, do not also perform the same searches yourself.");

    #   let exploreGuide = "- For broad codebase exploration or research that'll take more than 3 queries, spawn Agent with subagent_type=Explore.";
    #   const fallbackSearch = getDirectSearchGuidance(tools);
    #   if (fallbackSearch) {
    #     exploreGuide += ` Otherwise use ${fallbackSearch} directly.`;
    #   }
    #   lines.push(exploreGuide);
    # }

    if "Skill" in tools and has_skills:
        lines.append(
            "- When the user types `/<skill-name>`, invoke it via Skill. Only use skills listed in the user-invocable skills section — don't guess."
        )

    # if (tools.has("AskUserQuestion")) {
    #   lines.push("- Use AskUserQuestion when you need a bounded clarification before proceeding.");
    # }

    if len(lines) <= 1:
        return None

    # 只有存在实际 session guidance 时才输出本段，避免向 simple branch 注入空标题。
    return _create_dynamic_section(
        "Session-specific guidance", "session_guidance", "\n".join(lines)
    )


def build_dynamic_behavior_section() -> ContextSection:
    return _create_dynamic_section(
        "Dynamic Behavior",
        "dynamic_behavior",
        "\n".join(
            [
                COMMUNICATION_PROMPTS["additional"]["beforeDefault"],
                "",
                COMMUNICATION_PROMPTS["default"],
                COMMUNICATION_PROMPTS["additional"]["afterDefault"],
                "",
                "For actions that are hard to reverse or outward-facing, confirm first unless durably authorized or explicitly told to proceed without asking; approval in one context doesn't extend to the next. Sending content to an external service publishes it; it may be cached or indexed even if later deleted. Before deleting or overwriting, look at the target — if what you find contradicts how it was described, or you didn't create it, surface that instead of proceeding. Report outcomes faithfully: if tests fail, say so with the output; if a step was skipped, say that; when something is done and verified, state it plainly without hedging.",
            ]
        ),
    )


def build_output_style_section(
    style: Optional[OutputStylePromptConfig] = None,
) -> Optional[ContextSection]:
    """PORT: 原 TS 写成 ``style: ContextBuilderConfig["outputStyle"]``，即
    ``OutputStylePromptConfig | undefined``；Python 侧用同一个可选类型表达。
    """
    if style is None or len(style.prompt.strip()) == 0:
        return None
    return _create_dynamic_section(
        "Output Style",
        "output_style",
        "\n".join([f"# Output Style: {style.name}", style.prompt.strip()]),
    )


def build_context_management_section() -> ContextSection:
    return _create_dynamic_section(
        "Context Management",
        "context_management",
        "\n".join(
            [
                CONTEXT_MANAGEMENT_PROMPTS["default"],
                "",
                CONTEXT_MANAGEMENT_PROMPTS["additional"],
            ]
        ),
    )


def _create_dynamic_section(
    name: str,
    source: ContextSource,
    content: str,
) -> ContextSection:
    return ContextSection(
        name=name,
        source=source,
        injection_target="system",
        cache_hint="dynamic",
        chars=len(content),
        tokens=estimate_tokens(content),
        content=content,
        preview=content[:100],
    )
