"""Soul — persistent personality / identity file (Kotlin SoulStore.kt port).

Ported from: src/android/app/src/main/java/com/openminis/app/agent/SoulStore.kt
Original package: com.openminis.app.agent

SOUL.md lives at ``<data_dir>/memory/SOUL.md`` (the same folder GLOBAL.md
and the daily memory logs use). Format: YAML frontmatter delimited by ``---``
followed by a Markdown body.

Why a separate file rather than reusing identity.persona: the Android code
keeps SOUL.md as a user-authored Markdown file the agent can reference and
the chat bubble header can preview, while the system prompt builder stitches
it in via a fixed template — so the user never sees (or accidentally
deletes) the internal "You are <name>, a capable AI assistant ..." sentence.

Components:
  - SoulMetadata / SoulFile — frontmatter + body pair
  - SoulMDParser            — frontmatter parse + serialize (lossless-ish)
  - SoulStore               — file I/O, default content, restore-default,
                              language-aware length cap, atomic write
  - SystemPromptBuilder     — identitySection() used by chat_service to
                              prepend to the per-identity persona

Length caps mirror iOS / Android:
  - CJK ratio > 30 %  → 1600 chars
  - otherwise         → 1000 words

INJECTION_PATTERNS scrubs known prompt-injection lines at prompt-build time
and rejects them at write time so a misconfigured file surfaces an error
instead of a silent edit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..core.context import app_context
from ..core.logging import get_logger

logger = get_logger(__name__)


FILE_NAME = "SOUL.md"
MEMORY_SUBDIR = "memory"

#: CJK code-point ratio threshold above which a body is counted in chars
#: (otherwise it's counted in whitespace-delimited words).
CJK_RATIO_THRESHOLD: float = 0.3
#: Hard character cap for CJK-leaning bodies.
CHINESE_CHAR_LIMIT: int = 1600
#: Hard word cap for Latin-leaning bodies.
ENGLISH_WORD_LIMIT: int = 1000


#: Emoji displayed in every UI surface — locked regardless of what's
#: written in SOUL.md. Mirrors iOS/Android rollback of the custom-emoji
#: field.
DISPLAY_EMOJI = "\u2728"


#: Patterns recognised by :func:`scrub_injections` / :func:`contains_injection_pattern`.
#: A user-authored personality body has no business issuing instructions
#: to the model; these are dropped at prompt-build time and rejected at
#: write time.
INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore.{0,30}previous.{0,30}instructions?", re.IGNORECASE),
    re.compile(
        r"disregard.{0,30}(previous|prior).{0,30}instructions?", re.IGNORECASE
    ),
    re.compile(
        r"forget.{0,30}(previous|prior).{0,30}instructions?", re.IGNORECASE
    ),
)


@dataclass(frozen=True, slots=True)
class SoulMetadata:
    """YAML frontmatter for SOUL.md.

    ``emoji`` is preserved verbatim from disk so a user-authored file from
    an older build round-trips cleanly; UI always renders
    :data:`DISPLAY_EMOJI`. ``icon`` is the user-settable identity icon
    (emoji literal or ``data:image/png;base64,...`` URI; empty → sparkle).
    """

    name: str = "Minis"
    emoji: str = ""
    icon: str = ""
    style: str = ""
    lang: str = "auto"

    @property
    def display_emoji(self) -> str:
        return DISPLAY_EMOJI


DEFAULT_METADATA = SoulMetadata()
"""Default frontmatter — matches Kotlin ``SoulMetadata.DEFAULT``."""


@dataclass(frozen=True, slots=True)
class SoulFile:
    metadata: SoulMetadata = field(default_factory=lambda: DEFAULT_METADATA)
    body: str = ""


# ---------------------------------------------------------------------------
# Limit check
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class SoulBodyLimitCheck:
    """Result of :func:`SoulStore.check_body_limit`."""

    ok: bool
    unit: str = ""  # "chars" | "words" | ""
    used: int = 0
    cap: int = 0


# ---------------------------------------------------------------------------
# Parser — handles the four keys SOUL.md actually uses.
# ---------------------------------------------------------------------------
class SoulMDParser:
    """Lossless-ish frontmatter parser/serializer for SOUL.md.

    Only the keys :class:`SoulMetadata` knows about are recognised; any
    extra line in the frontmatter is silently ignored. ``emoji`` is kept
    in memory for round-trip safety even though :meth:`serialize` no
    longer writes the line — that way an old user-authored file keeps its
    emoji until the next save naturally drops it.
    """

    @staticmethod
    def parse(source: str) -> SoulFile:
        # Drop a leading newline run so a file with `\n---\n...` parses the
        # same way as one starting with `---`.
        trimmed_lead = source.lstrip("\n\r")
        if not trimmed_lead.startswith("---"):
            return SoulFile(DEFAULT_METADATA, source.rstrip("\n\r"))
        lines = trimmed_lead.split("\n")
        if lines[0].strip() != "---":
            return SoulFile(DEFAULT_METADATA, source.rstrip("\n\r"))
        close_rel = next(
            (i for i, ln in enumerate(lines[1:], start=1) if ln.strip() == "---"),
            -1,
        )
        if close_rel < 0:
            return SoulFile(DEFAULT_METADATA, source)

        front = lines[1:close_rel]
        body_lines = lines[close_rel + 1 :]
        body = "\n".join(body_lines).lstrip("\n\r")

        name = DEFAULT_METADATA.name
        emoji = DEFAULT_METADATA.emoji
        icon = DEFAULT_METADATA.icon
        style = DEFAULT_METADATA.style
        lang = DEFAULT_METADATA.lang

        for raw in front:
            line = raw.strip()
            if not line:
                continue
            colon = line.find(":")
            if colon <= 0:
                continue
            # FIRST colon only — `icon` may be a data URI containing colons.
            key = line[:colon].strip().lower()
            value = line[colon + 1 :].strip()
            if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            if key == "name" and value:
                name = value
            elif key == "emoji" and value:
                emoji = value
            elif key == "icon":
                icon = value
            elif key == "style":
                style = value
            elif key == "lang" and value:
                lang = value
            # else: unknown key, ignore (parser is intentionally narrow).

        return SoulFile(
            SoulMetadata(name=name, emoji=emoji, icon=icon, style=style, lang=lang),
            body,
        )

    @staticmethod
    def serialize(file: SoulFile) -> str:
        md = file.metadata
        sb = ["---\n"]
        sb.append(f'name: "{_escape(md.name)}"\n')
        if md.icon:
            sb.append(f'icon: "{_escape(md.icon)}"\n')
        sb.append(f'style: "{_escape(md.style)}"\n')
        sb.append(f'lang: "{_escape(md.lang)}"\n')
        sb.append("---\n\n")
        sb.append(file.body)
        if not sb[-1].endswith("\n"):
            sb[-1] += "\n"
        return "".join(sb)


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------
def _is_cjk(cp: int) -> bool:
    """True if ``cp`` belongs to a CJK / Hiragana / Katakana / Hangul block.

    Wide enough to count CJK Ext A / B / C / D / E / F / G / H, Hiragana /
    Katakana + their phonetic extensions, and Hangul Syllables / Jamo.
    """
    if 0x4E00 <= cp <= 0x9FFF:
        return True
    if 0x3400 <= cp <= 0x4DBF:
        return True
    if 0x20000 <= cp <= 0x2A6DF:
        return True
    if 0x2A700 <= cp <= 0x2EBEF:
        return True
    if 0x30000 <= cp <= 0x323AF:
        return True
    if 0x3040 <= cp <= 0x309F:
        return True
    if 0x30A0 <= cp <= 0x30FF:
        return True
    if 0x31F0 <= cp <= 0x31FF:
        return True
    if 0xAC00 <= cp <= 0xD7AF:
        return True
    if 0x1100 <= cp <= 0x11FF:
        return True
    if 0x3130 <= cp <= 0x318F:
        return True
    return False


def _cjk_ratio(text: str) -> tuple[int, int]:
    """Return ``(cjk_count, total_codepoints)`` for non-empty ``text``.

    Empty input returns ``(0, 0)`` so the ratio falls back to 0 → word
    count path, which trivially satisfies the limit.
    """
    if not text:
        return 0, 0
    cjk = 0
    total = 0
    i = 0
    n = len(text)
    while i < n:
        cp = ord(text[i])
        # Hot loop — only do the surrogate-pair dance when we have to.
        if 0xD800 <= cp <= 0xDBFF and i + 1 < n:
            cp = ((cp - 0xD800) << 10) + (ord(text[i + 1]) - 0xDC00) + 0x10000
            total += 1
            if _is_cjk(cp):
                cjk += 1
            i += 2
            continue
        total += 1
        if _is_cjk(cp):
            cjk += 1
        i += 1
    return cjk, total


class SoulStore:
    """File-system + cache helpers for SOUL.md."""

    @staticmethod
    def memory_dir() -> Path:
        return app_context().data_dir / MEMORY_SUBDIR

    @staticmethod
    def file_path() -> Path:
        return SoulStore.memory_dir() / FILE_NAME

    # -- body length rules (language-aware) -----------------------------
    @staticmethod
    def check_body_limit(body: str) -> SoulBodyLimitCheck:
        """Classify ``body`` under the language-aware length cap.

        Empty / whitespace-only bodies always return ok=True with 0 used.
        """
        stripped = body.strip()
        if not stripped:
            return SoulBodyLimitCheck(ok=True)
        cjk, total = _cjk_ratio(stripped)
        ratio = (cjk / total) if total else 0.0
        if ratio > CJK_RATIO_THRESHOLD:
            chars = len(stripped)  # Python str counts code points natively
            if chars > CHINESE_CHAR_LIMIT:
                return SoulBodyLimitCheck(
                    ok=False, unit="chars", used=chars, cap=CHINESE_CHAR_LIMIT
                )
            return SoulBodyLimitCheck(
                ok=True, unit="chars", used=chars, cap=CHINESE_CHAR_LIMIT
            )
        words = [w for w in stripped.split() if w]
        n = len(words)
        if n > ENGLISH_WORD_LIMIT:
            return SoulBodyLimitCheck(
                ok=False, unit="words", used=n, cap=ENGLISH_WORD_LIMIT
            )
        return SoulBodyLimitCheck(ok=True, unit="words", used=n, cap=ENGLISH_WORD_LIMIT)

    @staticmethod
    def body_count_text(body: str) -> str:
        """Human-readable ``"N / cap unit"`` counter for the Settings UI."""
        stripped = body.strip()
        if not stripped:
            return "0"
        cjk, total = _cjk_ratio(stripped)
        ratio = (cjk / total) if total else 0.0
        if ratio > CJK_RATIO_THRESHOLD:
            return f"{len(stripped)} / {CHINESE_CHAR_LIMIT} chars"
        n = len([w for w in stripped.split() if w])
        return f"{n} / {ENGLISH_WORD_LIMIT} words"

    # -- file I/O --------------------------------------------------------
    #: Default SOUL.md body used both for first-run seeding and the
    #: "Restore Default" button in Settings. Personality / character /
    #: voice guidance only — the "You are <name>, ..." identity sentence
    #: is owned by :class:`SystemPromptBuilder` and stitched in at
    #: prompt-build time.
    DEFAULT_CONTENT: str = (
        "---\n"
        'name: "Minis"\n'
        'style: ""\n'
        'lang: "auto"\n'
        "---\n\n"
        "**Don't perform — help.** Skip the \"Sure!\" and \"Happy to assist!\" — just do the work.\n\n"
        "**Have a stance.** It's fine to disagree, prefer one thing over another, find some things interesting and others dull.\n\n"
        "**Act first, ask second.** If you can look it up, look it up. Come back with answers, not questions.\n"
    )

    @staticmethod
    def ensure_exists() -> None:
        """Seed SOUL.md with :data:`DEFAULT_CONTENT` if it doesn't exist yet.

        Safe to call on every launch — never overwrites user edits.
        """
        path = SoulStore.file_path()
        if path.exists():
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(SoulStore.DEFAULT_CONTENT, encoding="utf-8")
            logger.info("seeded SOUL.md at %s", path)
        except OSError as e:
            logger.warning("SOUL.md seed failed: %s", e)

    @staticmethod
    def load() -> Optional[SoulFile]:
        """Read + parse SOUL.md. ``None`` when the file is missing or
        unreadable; an empty body parses to default metadata with empty
        body."""
        path = SoulStore.file_path()
        if not path.exists():
            return None
        try:
            return SoulMDParser.parse(path.read_text(encoding="utf-8"))
        except OSError as e:
            logger.warning("SOUL.md load failed: %s", e)
            return None

    @staticmethod
    def save(file: SoulFile) -> None:
        """Atomic write via ``.tmp`` sibling + rename."""
        target = SoulStore.file_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        text = SoulMDParser.serialize(file)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        try:
            tmp.replace(target)
        except OSError:
            # Fallback for filesystems that reject cross-inode rename.
            target.write_text(text, encoding="utf-8")
            try:
                tmp.unlink()
            except OSError:
                pass

    @staticmethod
    def delete() -> bool:
        """Remove SOUL.md if present. Returns True if a file was deleted."""
        path = SoulStore.file_path()
        if not path.exists():
            return False
        try:
            path.unlink()
        except OSError as e:
            logger.warning("SOUL.md delete failed: %s", e)
            return False
        return True

    @staticmethod
    def restore_default() -> SoulFile:
        """Overwrite SOUL.md with :data:`DEFAULT_CONTENT` and return the
        resulting parsed file. Used by the Settings UI "Restore Default"
        button."""
        parsed = SoulMDParser.parse(SoulStore.DEFAULT_CONTENT)
        SoulStore.save(parsed)
        return parsed


# ---------------------------------------------------------------------------
# System prompt integration
# ---------------------------------------------------------------------------
def contains_injection_pattern(s: str) -> bool:
    return any(p.search(s) for p in INJECTION_PATTERNS)


def scrub_injections(s: str) -> str:
    """Drop lines that look like prompt-injection attempts."""
    return "\n".join(
        line
        for line in s.split("\n")
        if not any(p.search(line) for p in INJECTION_PATTERNS)
    )


#: Identity sentence template. ``{name}`` is substituted from the SOUL
#: metadata; the wording matches the pre-SOUL literal — keep it in sync
#: if the runtime statement ever needs a tweak.
IDENTITY_TEMPLATE = (
    "You are {name}, a capable AI assistant running on a desktop server "
    "with a fully functional Linux sandbox. "
)


SOUL_EDIT_HINT = (
    "---\n"
    "SOUL.md fields (name / icon / style / lang / body) can be edited two ways:\n"
    "1. Tool: call `minis-config` to propose changes (user must approve).\n"
    "2. UI: ask the user to go to Settings → Soul to edit directly.\n"
    "Pick whichever the user finds easier in context. Do not say you cannot change your personality."
)


class SystemPromptBuilder:
    """Composes Layer-1 of the agent system prompt from SOUL.md."""

    @staticmethod
    def identity_section() -> str:
        """Render the identity sentence + (optional) style block +
        (optional) personality body + SOUL edit hint.

        When SOUL.md is missing or the body is empty, the rendered prompt
        is byte-identical to the no-SOUL fallback (only ``{name}`` is
        substituted into the template).
        """
        file = SoulStore.load()
        meta = file.metadata if file is not None else DEFAULT_METADATA
        name = (meta.name or DEFAULT_METADATA.name).strip() or "Minis"
        style = (meta.style or "").strip()

        identity = IDENTITY_TEMPLATE.replace("{name}", name).rstrip()

        style_block = (
            "\n\nResponse style (from SOUL.md `style` — apply to every reply "
            "unless the user explicitly asks otherwise; if it prescribes a "
            "reply language, it overrides the default match-the-user's-language "
            f"rule):\n{style}"
            if style
            else ""
        )

        body = (file.body if file is not None else "") or ""
        stripped = body.strip()

        if not stripped:
            return identity + style_block + "\n\n" + SOUL_EDIT_HINT + "\n\n"

        # Over-limit body → fall back to identity-only prompt. The user
        # notices the personality isn't taking effect and the Save button
        # already rejects the same file with a red counter, so this is the
        # safer signal than silent truncation.
        check = SoulStore.check_body_limit(stripped)
        if not check.ok:
            logger.warning(
                "SOUL.md body over the %s cap (%d/%d) — falling back to identity-only prompt",
                check.unit,
                check.used,
                check.cap,
            )
            return identity + style_block + "\n\n" + SOUL_EDIT_HINT + "\n\n"

        personality = scrub_injections(stripped)
        return (
            identity
            + "\n\nPersonality (from SOUL.md — your character and voice; defer to the user's latest message when it conflicts with anything here):\n"
            + personality
            + style_block
            + "\n\n"
            + SOUL_EDIT_HINT
            + "\n\n"
        )