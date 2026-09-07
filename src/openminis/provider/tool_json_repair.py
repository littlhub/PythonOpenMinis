"""JSON repair for malformed / incomplete tool calls (T-tool-json-repair b2c4f8a6).

Ported from: src/android/app/src/main/java/com/openminis/app/provider/ToolJsonRepair.kt
Original package: com.openminis.app.provider

Mirrors the iOS implementation in AIChatViewModel.swift (repairToolArgs /
preflight pre-pass). Operates on the already-parsed dict that the streaming
provider surfaced, optionally consulting a raw stream "tail" snapshot from the
tool-input chunk ring when the dict is empty (truncation case).
"""

from __future__ import annotations

import json
from typing import Optional

from openminis.data.model import AgentToolDefinition

__all__ = ["ToolJsonRepair"]


class ToolJsonRepair:
    """Object ToolJsonRepair (ToolJsonRepair.kt)."""

    @staticmethod
    def repair(
        tool_name: str,
        args: dict,
        raw_tail: Optional[str],
        tools: list[AgentToolDefinition],
    ) -> list[str]:
        """Mutates [args] in-place and returns the list of repair strategy tags
        that fired (empty when nothing changed)."""
        tool_def = next((t for t in tools if t.name == tool_name), None)
        if tool_def is None:
            return []

        repairs: list[str] = []

        # Strategy 1: truncation repair. Only fires when the dict is empty but
        # the raw stream tail looks like a JSON object that just got cut.
        if len(args) == 0 and raw_tail and raw_tail.strip():
            tail = raw_tail.strip()
            suffixes = ["", "\"", "\"}", "\"]}", "}", "}}", "]}", "]}}", "]", "]]"]
            for suffix in suffixes:
                candidate = tail + suffix
                parsed = ToolJsonRepair._try_parse_object(candidate)
                if parsed is None:
                    continue
                for k in list(parsed.keys()):
                    args[k] = parsed[k]
                repairs.append("truncation+" + (suffix if suffix else "noop"))
                break

        # Strategy 2: type coercion on required fields.
        for field_name in tool_def.required:
            if field_name not in args:
                continue
            raw = args.get(field_name)
            if raw is None:
                continue
            if isinstance(raw, str):
                continue
            if raw is None or isinstance(raw, (type(None),)):  # JSONObject.NULL
                continue
            coerced = str(raw)
            if coerced.strip():
                args[field_name] = coerced
                repairs.append(f"type-coerce:{field_name}")

        # Strategy 3: fuzzy field-name match for missing required fields.
        schema_fields = set(tool_def.parameters.keys())
        for field_name in tool_def.required:
            if field_name in args:
                continue
            candidate_key = next(
                (key for key in list(args.keys())
                 if key not in schema_fields and ToolJsonRepair._levenshtein_at_most_one(key, field_name)),
                None,
            )
            if candidate_key is None:
                continue
            args[field_name] = args[candidate_key]
            del args[candidate_key]
            repairs.append(f"fuzzy:{candidate_key}->{field_name}")

        return repairs

    @staticmethod
    def _try_parse_object(s: str) -> Optional[dict]:
        try:
            obj = json.loads(s)
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None

    @staticmethod
    def _levenshtein_at_most_one(a: str, b: str) -> bool:
        """True iff Levenshtein edit distance between a and b is exactly 1
        (case-insensitive)."""
        al = a.lower()
        bl = b.lower()
        if al == bl:
            return False  # distance 0 = same key, not a repair candidate
        diff = len(al) - len(bl)
        if diff > 1 or diff < -1:
            return False
        if len(al) == len(bl):
            mismatches = 0
            for i in range(len(al)):
                if al[i] != bl[i]:
                    mismatches += 1
                    if mismatches > 1:
                        return False
            return mismatches == 1
        longer = al if len(al) > len(bl) else bl
        shorter = bl if len(al) > len(bl) else al
        i = 0
        j = 0
        skipped = False
        while i < len(longer) and j < len(shorter):
            if longer[i] == shorter[j]:
                i += 1
                j += 1
            elif not skipped:
                i += 1
                skipped = True
            else:
                return False
        return True
