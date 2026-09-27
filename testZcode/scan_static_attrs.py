"""Static attribute / kwarg checker for openminis.agent.zcode (debug only, not shipped).

Catches runtime-only mistakes that an import check cannot see, because they sit
inside function bodies:

1. ``SomeEnum.camelCaseMember``      -> AttributeError at call time.
2. ``some_module.missing_symbol``    -> AttributeError at call time.
3. ``SomeDataclass(unknown_kwarg=v)``-> TypeError at call time.
4. ``SomeDataclass(init_false_field=v)`` -> TypeError at call time.
5. ``SomeDataclass(a, b, c)`` with too many positional args.

Usage:  python scan_static_attrs.py
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

findings: list[tuple[str, int, str, str]] = []  # (mod, lineno, kind, detail)


def _camel_to_snake(name: str) -> str:
    out = []
    for i, ch in enumerate(name):
        if ch.isupper():
            if i and (not name[i - 1].isupper()):
                out.append("_")
            out.append(ch)
        else:
            out.append(ch.upper())
    return "".join(out)


def _enum_suggestions(cls: type, attr: str) -> str:
    """Best-effort suggestion for a mistyped enum member."""
    want = _camel_to_snake(attr)
    members = [m.name for m in cls]  # type: ignore[attr-defined]
    if want in members:
        return want
    # fuzzy: same letters ignoring underscores
    flat = {m.replace("_", "").lower(): m for m in members}
    return flat.get(attr.replace("_", "").lower(), "")


def _check_module(mod_name: str, path: Path) -> None:
    try:
        module = importlib.import_module(mod_name)
    except Exception as exc:  # noqa: BLE001
        findings.append((mod_name, 0, "IMPORT_FAIL", f"{type(exc).__name__}: {exc}"))
        return

    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        findings.append((mod_name, exc.lineno or 0, "SYNTAX", str(exc)))
        return

    g = vars(module)

    def resolve(node: ast.AST) -> tuple[bool, object]:
        """Resolve ``a``, ``a.b``, ``a.b.c`` chains to an object. Returns (ok, obj)."""
        if isinstance(node, ast.Name):
            if node.id in g:
                return True, g[node.id]
            return False, None
        if isinstance(node, ast.Attribute):
            ok, base = resolve(node.value)
            if not ok:
                return False, None
            try:
                return True, getattr(base, node.attr)
            except Exception:  # noqa: BLE001
                return False, None
        return False, None

    for node in ast.walk(tree):
        # ---------------------------------------------------------------- attrs
        if isinstance(node, ast.Attribute):
            ok, base = resolve(node.value)
            if not ok or base is None:
                continue
            attr = node.attr
            try:
                getattr(base, attr)
                continue
            except Exception:  # noqa: BLE001
                pass

            field_names: set[str] = set()
            if isinstance(base, type) and is_dataclass(base):
                field_names = set(base.__dataclass_fields__.keys())
            if attr in field_names:
                continue

            if isinstance(base, EnumMeta):
                hint = _enum_suggestions(base, attr)
                detail = f"{ast.unparse(node.value)}.{attr}"
                if hint:
                    detail += f"  ->  did you mean {ast.unparse(node.value)}.{hint}?"
                findings.append((mod_name, node.lineno, "ENUM_MEMBER", detail))
            elif isinstance(base, type):
                findings.append(
                    (mod_name, node.lineno, "CLASS_ATTR", f"{ast.unparse(node.value)}.{attr}")
                )
            elif isinstance(base, type(sys)):
                findings.append(
                    (mod_name, node.lineno, "MODULE_ATTR", f"{ast.unparse(node.value)}.{attr}")
                )

        # ---------------------------------------------------------------- calls
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            cls = g.get(node.func.id)
            if cls is None or not isinstance(cls, type) or not is_dataclass(cls):
                continue
            fields = cls.__dataclass_fields__
            for kw in node.keywords:
                if kw.arg is None:
                    continue
                if kw.arg not in fields:
                    findings.append(
                        (
                            mod_name,
                            node.lineno,
                            "UNKNOWN_KWARG",
                            f"{node.func.id}({kw.arg}=...)  (valid: {sorted(fields)[:6]}...)",
                        )
                    )
                elif not fields[kw.arg].init:
                    findings.append(
                        (
                            mod_name,
                            node.lineno,
                            "INIT_FALSE_KWARG",
                            f"{node.func.id}({kw.arg}=...)  (field is init=False)",
                        )
                    )
            positional = [a for a in node.args if not isinstance(a, ast.Starred)]
            init_count = sum(1 for f in fields.values() if f.init)
            if len(positional) > init_count:
                findings.append(
                    (
                        mod_name,
                        node.lineno,
                        "TOO_MANY_POS",
                        f"{node.func.id}() got {len(positional)} positional, max {init_count}",
                    )
                )


def main() -> int:
    total = 0
    scanned = 0
    for py in sorted(ZC.rglob("*.py")):
        rel = py.relative_to(ZC).with_suffix("")
        parts = list(rel.parts)
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        mod = ".".join([TOP, *parts]) if parts else TOP
        before = len(findings)
        _check_module(mod, py)
        scanned += 1
        total += len(findings) - before

    by_kind: dict[str, list] = {}
    for mod, line, kind, detail in findings:
        by_kind.setdefault(kind, []).append((mod, line, detail))

    for kind in sorted(by_kind):
        rows = by_kind[kind]
        print(f"\n=== {kind}  ({len(rows)}) ===")
        for mod, line, detail in rows:
            short = mod.replace(TOP + ".", "")
            print(f"  {short}:{line}  {detail}")

    print(f"\nTOTAL FINDINGS = {total}")
    if scanned == 0:
        # 一个模块都没扫到 = 扫描根算错了（脚本搬家/到处调用），不是「干净」。
        print("=== NO MODULES SCANNED：扫描根没算对，本次结果无意义 ===")
        print(f"    ZC = {ZC}")
        return 1
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
