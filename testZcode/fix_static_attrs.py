"""Fix ENUM_MEMBER findings reported by scan_static_attrs.py (debug only, not shipped).

Only rewrites the exact attribute expression at its recorded column span, so it
never touches unrelated text.
"""
from __future__ import annotations

import ast
import importlib
import sys
from dataclasses import is_dataclass
from enum import EnumMeta
from pathlib import Path

from _root import PROJECT_ROOT as ROOT, SRC_ROOT as PKG  # noqa: E402

sys.path.insert(0, str(PKG))

TOP = "openminis.agent.zcode"
ZC = ROOT / "src" / "openminis" / "agent" / "zcode"


def _camel_to_snake(name: str) -> str:
    out = []
    for i, ch in enumerate(name):
        if ch.isupper():
            if i and not name[i - 1].isupper():
                out.append("_")
            out.append(ch)
        else:
            out.append(ch.upper())
    return "".join(out)


def _suggest(cls: type, attr: str) -> str:
    want = _camel_to_snake(attr)
    members = [m.name for m in cls]  # type: ignore[attr-defined]
    if want in members:
        return want
    flat = {m.replace("_", "").lower(): m for m in members}
    return flat.get(attr.replace("_", "").lower(), "")


def fix_file(mod_name: str, path: Path) -> int:
    try:
        module = importlib.import_module(mod_name)
    except Exception:  # noqa: BLE001
        return 0
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return 0

    g = vars(module)

    def resolve(node: ast.AST):
        if isinstance(node, ast.Name):
            return g.get(node.id) if node.id in g else None
        if isinstance(node, ast.Attribute):
            base = resolve(node.value)
            if base is None:
                return None
            try:
                return getattr(base, node.attr)
            except Exception:  # noqa: BLE001
                return None
        return None

    edits: list[tuple[int, int, int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if node.lineno != node.end_lineno:
            continue
        base = resolve(node.value)
        if base is None:
            continue
        try:
            getattr(base, node.attr)
            continue
        except Exception:  # noqa: BLE001
            pass
        if not isinstance(base, EnumMeta):
            continue
        hint = _suggest(base, node.attr)
        if not hint:
            continue
        edits.append(
            (
                node.lineno,
                node.col_offset,
                node.end_col_offset,
                f"{ast.unparse(node.value)}.{hint}",
            )
        )

    if not edits:
        return 0

    lines = src.splitlines(keepends=True)
    for lineno, cs, ce, text in sorted(edits, reverse=True):
        line = lines[lineno - 1]
        lines[lineno - 1] = line[:cs] + text + line[ce:]
    path.write_text("".join(lines), encoding="utf-8")
    return len(edits)


def main() -> int:
    total = 0
    for py in sorted(ZC.rglob("*.py")):
        rel = py.relative_to(ZC).with_suffix("")
        parts = list(rel.parts)
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        mod = ".".join([TOP, *parts]) if parts else TOP
        n = fix_file(mod, py)
        if n:
            print(f"  fixed {n:3d}  {mod}")
            total += n
    print(f"\nTOTAL FIXED = {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
