"""Compact prompt — the summarization prompt and post-compact continuation message.

Ported from: packages/core/src/compact/prompt.ts
Original package: @zcode/core (compact)

# PORT: 本文件的三段提示词正文（``_NO_TOOLS_PREAMBLE`` / ``_BASE_COMPACT_PROMPT`` /
# ``_NO_TOOLS_TRAILER``）逐字保留——提示词正文是压缩质量的关键。TS 模板字面量里有
# 两处 ``${" "}`` 插值（manual 之外的纯空格），Python 侧写成 ``\\x20``，渲染结果同为
# 单个空格，且不会在编辑器/格式化器去尾随空格时丢失。
"""

from __future__ import annotations

import re
from typing import Optional, TypedDict

__all__ = [
    "build_compact_prompt",
    "format_compact_summary",
    "build_compact_summary_message",
]


# PORT: TS ``const NO_TOOLS_PREAMBLE``（模块私有，非 export）。
_NO_TOOLS_PREAMBLE = """CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.

- Do NOT use Read, Bash, Grep, Glob, Edit, Write, or ANY other tool.
- You already have all the context you need in the conversation above.
- Tool calls will be REJECTED and will waste your only turn — you will fail the task.
- Your entire response must be plain text: an <analysis> block followed by a <summary> block.

"""

# PORT: TS ``const NO_TOOLS_TRAILER``（模块私有，非 export）。
_NO_TOOLS_TRAILER = (
    "\n\nREMINDER: Do NOT call any tools. Respond with plain text only — "
    + "an <analysis> block followed by a <summary> block. Tool calls will be rejected and you will fail the task."
)

# PORT: TS ``const BASE_COMPACT_PROMPT``（模块私有，非 export）。
_BASE_COMPACT_PROMPT = """Your task is to create a detailed summary of the conversation so far, paying close attention to the user's explicit requests and your previous actions.
This summary should be thorough in capturing technical details, code patterns, and architectural decisions that would be essential for continuing development work without losing context.

Before providing your final summary, wrap your analysis in <analysis> tags to organize your thoughts and ensure you've covered all necessary points. In your analysis process:

1. Chronologically analyze each message and section of the conversation. For each section thoroughly identify:
   - The user's explicit requests and intents
   - Your approach to addressing the user's requests
   - Key decisions, technical concepts and code patterns
   - Specific details like:
     - file names
     - full code snippets
     - function signatures
     - file edits
   - Errors that you ran into and how you fixed them
   - Pay special attention to specific user feedback that you received, especially if the user told you to do something differently.
   - Note any security-relevant instructions or constraints the user stated (e.g., sensitive files or data to avoid, operations that must not be performed, credential or secret handling rules). These MUST be preserved verbatim in the summary so they continue to apply after compaction.
2. Double-check for technical accuracy and completeness, addressing each required element thoroughly.

Your summary should include the following sections:

1. Primary Request and Intent: Capture all of the user's explicit requests and intents in detail
2. Key Technical Concepts: List all important technical concepts, technologies, and frameworks discussed.
3. Files and Code Sections: Enumerate specific files and code sections examined, modified, or created. Pay special attention to the most recent messages and include full code snippets where applicable and include a summary of why this file read or edit is important.
4. Errors and fixes: List all errors that you ran into, and how you fixed them. Pay special attention to specific user feedback that you received, especially if the user told you to do something differently.
5. Problem Solving: Document problems solved and any ongoing troubleshooting efforts.
6. All user messages: List ALL user messages that are not tool results. These are critical for understanding the users' feedback and changing intent. Preserve any security-relevant instructions or constraints verbatim so they remain in effect after compaction.
7. Pending Tasks: Outline any pending tasks that you have explicitly been asked to work on.
8. Current Work: Describe in detail precisely what was being worked on immediately before this summary request, paying special attention to the most recent messages from both user and assistant. Include file names and code snippets where applicable.
9. Optional Next Step: List the next step that you will take that is related to the most recent work you were doing. IMPORTANT: ensure that this step is DIRECTLY in line with the user's most recent explicit requests, and the task you were working on immediately before this summary request. If your last task was concluded, then only list next steps if they are explicitly in line with the users request. Do not start on tangential requests or really old requests that were already completed without confirming with the user first.
                       If there is a next step, include direct quotes from the most recent conversation showing exactly what task you were working on and where you left off. This should be verbatim to ensure there's no drift in task interpretation.

Here's an example of how your output should be structured:

<example>
<analysis>
[Your thought process, ensuring all points are covered thoroughly and accurately]
</analysis>

<summary>
1. Primary Request and Intent:
   [Detailed description]

2. Key Technical Concepts:
   - [Concept 1]
   - [Concept 2]
   - [...]

3. Files and Code Sections:
   - [File Name 1]
      - [Summary of why this file is important]
      - [Summary of the changes made to this file, if any]
      - [Important Code Snippet]
   - [File Name 2]
      - [Important Code Snippet]
   - [...]

4. Errors and fixes:
    - [Detailed description of error 1]:
      - [How you fixed the error]
      - [User feedback on the error if any]
    - [...]

5. Problem Solving:
   [Description of solved problems and ongoing troubleshooting]

6. All user messages:\x20
    - [Detailed non tool use user message]
    - [...]

7. Pending Tasks:
   - [Task 1]
   - [Task 2]
   - [...]

8. Current Work:
   [Precise description of current work]

9. Optional Next Step:
   [Optional Next step to take]

</summary>
</example>

Please provide your summary based on the conversation so far, following this structure and ensuring precision and thoroughness in your response.\x20

There may be additional summarization instructions provided in the included context. If so, remember to follow these instructions when creating the above summary. Examples of instructions include:
<example>
## Compact Instructions
When summarizing the conversation focus on typescript code changes and also remember the mistakes you made and how you fixed them.
</example>

<example>
# Summary instructions
When you are using compact - please focus on test output and code changes. Include file reads verbatim.
</example>"""

# PORT: ``buildCompactSummaryMessage`` 的匿名 options 形状 → ``TypedDict``。
class BuildCompactSummaryMessageOptions(TypedDict, total=False):
    recent_messages_preserved: bool
    repl_state_cleared: bool
    suppress_followup: bool
    transcript_path: str


def build_compact_prompt(custom_instructions: Optional[str]) -> str:
    """Mirrors ``buildCompactPrompt``."""
    # PORT: ``customInstructions?.trim()`` → ``None`` 时不做 trim（保持 falsy 分支语义）。
    trimmed = custom_instructions.strip() if custom_instructions is not None else None
    custom_instruction_block = (
        f"\n\nAdditional Instructions:\n{custom_instructions}" if trimmed else ""
    )

    return f"{_NO_TOOLS_PREAMBLE}{_BASE_COMPACT_PROMPT}{custom_instruction_block}{_NO_TOOLS_TRAILER}"


def format_compact_summary(text: Optional[str]) -> str:
    """Mirrors ``formatCompactSummary``."""
    # PORT: ``text?.trim() ?? ""``。
    formatted = text.strip() if text is not None else ""
    if not formatted:
        return ""

    formatted = re.sub(r"<analysis>[\s\S]*?</analysis>", "", formatted)
    summary_match = re.search(r"<summary>([\s\S]*?)</summary>", formatted)
    if summary_match:
        summary = summary_match.group(1) or ""
        # PORT: TS ``String.replace`` 的 ``$`` 特殊替换串在 Python ``re.sub`` 里由函数
        # 替换替代，避免 summary 内含 ``\g`` / 反斜杠时被当作反向引用。
        formatted = re.sub(
            r"<summary>[\s\S]*?</summary>",
            lambda _match: f"Summary:\n{summary.strip()}",
            formatted,
        )

    # PORT: ```/\n\n+/g``` → ``re.sub``（全局替换）。
    return re.sub(r"\n\n+", "\n\n", formatted).strip()


def build_compact_summary_message(
    summary: str,
    options: Optional[BuildCompactSummaryMessageOptions] = None,
) -> str:
    """Mirrors ``buildCompactSummaryMessage``.

    # PORT: TS 默认值 ``options = {}`` → ``None`` 哨兵后在函数内取空对象。
    """
    opts = options if options is not None else {}
    message = (
        "This session is being continued from a previous conversation that ran out of context. "
        "The summary below covers the earlier portion of the conversation.\n\n"
        + format_compact_summary(summary)
    )

    transcript_path = opts.get("transcript_path")
    if transcript_path:
        message += f"\n\nIf you need specific details from before compaction (like exact code snippets, error messages, or content you generated), read the full transcript at: {transcript_path}"

    if opts.get("recent_messages_preserved"):
        message += "\n\nRecent messages are preserved verbatim."

    if opts.get("repl_state_cleared"):
        message += "\n\nYour REPL VM state has been cleared as part of this compaction. Variables defined in REPL calls before this point are no longer accessible — redefine any you still need."

    if opts.get("suppress_followup"):
        message += '\nContinue the conversation from where it left off without asking the user any further questions. Resume directly — do not acknowledge the summary, do not recap what was happening, do not preface with "I\'ll continue" or similar. Pick up the last task as if the break never happened.'

    return message
