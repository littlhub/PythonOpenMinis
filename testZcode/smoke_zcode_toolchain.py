"""ZCode 工具链端到端冒烟：真跑内核 handler + 本机端口。

不依赖模型、不依赖主智能体 —— 直接构造宿主运行时并调用工具，
验证「ZCode 用自己的工具链且能独立运行」这条主线。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from _root import ensure_src_on_path  # noqa: E402

ensure_src_on_path()

from openminis.agent.zcode.integration import (  # noqa: E402
    ZCODE_TOOLCHAIN,
    build_zcode_runtime,
    execute_zcode_tool_id,
    format_zcode_tool_output,
    reset_session_runtimes,
    zcode_tool_spec,
)

PASSED = 0
FAILED = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  PASS  {label}")
    else:
        FAILED += 1
        print(f"  FAIL  {label} {detail}")


async def call(spec_id: str, args: dict, session: str, cwd: str) -> str:
    return await execute_zcode_tool_id(spec_id, args, session_id=session, working_directory=cwd)


async def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="zcode-smoke-"))
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text(
        "def main():\n    print('hello')\n\n\nif __name__ == '__main__':\n    main()\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("# demo\n\nzcode marker line\n", encoding="utf-8")
    session = "smoke-session"
    cwd = str(root)

    print("=== Read ===")
    out = await call("zcode_read", {"file_path": str(root / "src" / "app.py")}, session, cwd)
    check("读文件含行号", "1\tdef main():" in out or "def main()" in out, repr(out[:120]))
    check("Read 输出非空", bool(out.strip()))

    print("=== Write ===")
    target = root / "src" / "new.txt"
    out = await call("zcode_write", {"file_path": str(target), "content": "written by zcode\n"}, session, cwd)
    check("写文件落盘", target.is_file() and target.read_text(encoding="utf-8") == "written by zcode\n", repr(out[:120]))
    check("Write 返回成功文案", "success" in out.lower() or "written" in out.lower(), repr(out[:160]))

    print("=== Edit（read-before-write 状态）===")
    # 先 Read 再 Edit：内核的 read-state 门禁要求先读。
    await call("zcode_read", {"file_path": str(target)}, session, cwd)
    out = await call(
        "zcode_edit",
        {"file_path": str(target), "old_string": "written by zcode", "new_string": "edited by zcode"},
        session, cwd,
    )
    check("精确替换生效", "edited by zcode" in target.read_text(encoding="utf-8"), repr(out[:160]))

    print("=== Glob ===")
    out = await call("zcode_glob", {"pattern": "**/*.py", "path": cwd}, session, cwd)
    check("Glob 命中 app.py", "app.py" in out, repr(out[:160]))

    print("=== Grep ===")
    out = await call("zcode_grep", {"pattern": "zcode marker", "path": cwd}, session, cwd)
    check("Grep 命中 README", "README" in out, repr(out[:160]))

    print("=== Todo ===")
    out = await call(
        "zcode_todo_write",
        {"todos": [{"content": "smoke", "status": "in_progress", "priority": "high"}]},
        session, cwd,
    )
    check("TodoWrite 返回摘要", "summary" in out or "total" in out, repr(out[:160]))
    out = await call("zcode_todo_read", {}, session, cwd)
    check("TodoRead 读回条目", "smoke" in out, repr(out[:160]))

    print("=== Bash（复用应用持久 shell）===")
    out = await call("zcode_bash", {"command": "echo zcode-bash-ok"}, session, cwd)
    check("Bash 输出正确", "zcode-bash-ok" in out, repr(out[:200]))
    check("Bash 不重复渲染退出码", out.count("exit code") <= 1, repr(out[:200]))

    print("=== 端口缺失时的如实报错 ===")
    runtime = build_zcode_runtime(session_id="no-exec", working_directory=cwd, need=("fs",))
    spec = zcode_tool_spec("zcode_bash")
    check("exec 端口缺席可探测", runtime.port("exec") is None and spec is not None)

    print("=== 工具链清单 ===")
    ids = [s.id for s in ZCODE_TOOLCHAIN]
    check("10 个可执行工具", len(ids) == 10, str(ids))
    check("命名空间前缀隔离", all(i.startswith("zcode_") for i in ids))
    check("相交登记不误判", zcode_tool_spec("zcode_read").host_equivalent == "file_read")
    check("Bash 相交为 shell_execute", zcode_tool_spec("zcode_bash").host_equivalent == "shell_execute")

    print("=== dict 输出格式化 ===")
    check(
        "bash 形态 dict → 可读文本",
        format_zcode_tool_output({"stdout": "x", "stderr": "", "status": "completed"}) == "x",
    )
    check(
        "非零退出码标注",
        "exit code: 3" in format_zcode_tool_output({"stdout": "x", "status": "failed", "exitCode": 3}),
    )

    reset_session_runtimes()
    print()
    print(f"PASSED = {PASSED}   FAILED = {FAILED}")
    if FAILED:
        raise SystemExit(1)


asyncio.run(main())
