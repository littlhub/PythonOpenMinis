"""Host-port / sandbox behaviour smoke test.

与既有冒烟的分工：

* ``smoke_zcode_*.py`` —— ZCode **内核**侧的移植正确性；
* **本脚本** —— **宿主端口**这一层：路径世界观的换算（沙箱写法 ↔ 真实目录），
  ``zcode`` 循环里「写文件必须真落到工作区」的端到端，工具事件有没有转发给宿主，
  以及 ``PersistentShell`` 的失败姿态（超时、超长行、没有换行结尾的输出）。

这三类问题只在真机上才现形 —— 单元测试里既没有 Git Bash，也没有真实的超时
窗口，所以必须由真实子进程兜住。历史上它们分别表现为：

* ``/var/minis/workspace/...`` 被当成 Linux 绝对路径交给 ``os.makedirs``，
  在 Windows 上落到 ``\\var\\minis\\...``（无权限）→ 写文件失败、模型反复重试；
  更隐蔽的一层：内核 ``path_policy`` 会先 ``normpath``，到端口的其实是
  ``C:\\var\\minis\\...``（反斜杠 + 盘符），只按正斜杠匹配等于没修；
* ``zcode`` 循环只把 ``Text`` 转给宿主 → 前端永远看不到「工具调用」那一步
  （宿主靠 ``ToolCallComplete`` 发 ``toolStart`` 帧画工具卡）；
* 一条卡住不返回的命令留在持久 shell 的输入队列里 → **该会话之后每条 Bash
  都超时**，日志里只剩启动那一行（「卡了」）；
* 单行输出超过 ``StreamReader`` 的 64 KiB 上限 → ``readline()`` 清空缓冲，
  这一行连同紧随的完成标记一起消失 → 命令成功但输出为空。

用法（脚本位置无关，靠 ``_root.py`` 找工程根）：
    python smoke_host_ports.py          # 全通过则打印每项 ✓ 并 exit 0
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from _root import ensure_src_on_path  # noqa: E402

ensure_src_on_path()

from openminis.core.context import (  # noqa: E402
    AppContext,
    app_context,
    reset_app_context,
    set_app_context,
)
from openminis.agent.zcode.integration.host_ports import (  # noqa: E402
    AppExecutionPort,
    LocalFileSystemPort,
    _host_path,
)

PASSED = 0
FAILED = 0


def check(label: str, condition: bool, detail: object = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  \u2713 {label}")
    else:
        FAILED += 1
        print(f"  \u2717 {label}  -> {detail}")


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 路径世界观：沙箱写法 ↔ 真实目录
# ---------------------------------------------------------------------------

def test_sandbox_paths(tmp: Path) -> None:
    print("\n[sandbox paths] 入站还原")
    ctx = AppContext(data_dir=tmp / "data")
    set_app_context(ctx)
    root = ctx.external_files_dir

    got = _host_path("/var/minis/workspace/a/b.txt")
    check("工作区沙箱路径 → 宿主工作区", Path(got) == root / "a" / "b.txt", got)

    got = _host_path("/var/minis/data/x.json")
    check("数据沙箱路径 → 宿主数据目录", Path(got) == ctx.data_dir / "x.json", got)

    got = _host_path("/var/minis")
    check("裸 /var/minis 就是工作区根", Path(got) == root, got)

    got = _host_path("relative/x.txt")
    check("相对路径原样透传", got == "relative/x.txt", got)

    got = _host_path("C:/tmp/x.txt")
    check("真实路径不被动", got == "C:/tmp/x.txt", got)

    # ── 内核 normpath 之后的形态（真实到达端口的就是这个）──────────────────
    # ``path_policy.resolve_workspace_path`` 会先 ``normpath`` 一遍：Windows 上
    # ``/var/minis/workspace/x`` 到这里已经是 ``\var\minis\workspace\x``，而且
    # **常带盘符**（``C:\var\...`` / ``G:\var\...``，盘符取进程当前盘）。
    # 只按正斜杠找 ``/var/minis`` 会漏掉 → 前缀换不回去 → 写文件落到盘根 →
    # ``os.makedirs`` 在盘根被 Windows 拒 → ``permission_denied``（实测过）。
    norm_win = os.path.normpath("/var/minis/workspace/a/b.txt")
    got = _host_path(norm_win)
    check("normpath 后的反斜杠形态也要还原", Path(got) == root / "a" / "b.txt", got)

    for drive in ("C:", "G:"):
        got = _host_path(f"{drive}\\var\\minis\\workspace\\a\\b.txt")
        check(f"带盘符 {drive} 的内核形态也要还原", Path(got) == root / "a" / "b.txt", got)

    got = _host_path("G:\\var\\minis\\data\\x.json")
    check("盘符 + data 也要还原", Path(got) == ctx.data_dir / "x.json", got)

    got = _host_path(norm_win.replace("/", "\\").replace("\\", "\\"))
    check("反斜杠形态可复用", Path(got) == root / "a" / "b.txt", got)

    # 普通路径里偶然出现的 ``var\minis`` 段不该被当成沙箱前缀（要求出现在开头）
    keep = "C:\\Users\\loo\\notes\\var\\minis\\x.txt"
    check("真路径里的 var\\minis 段不被动", _host_path(keep) == keep, _host_path(keep))

    # ── 容器幻觉根（``/home/user`` 等）────────────────────────────────────
    # 用户实际报的第二种形态：``{"filePath": "/home/user/calculator.html"}``。
    # 模型没被告知工作目录，就按 Linux 容器的惯例自己编了一个；内核 ``isabs`` 认它
    # 是绝对路径、``normpath`` 之后落到盘根（用户机器上真的建出了 ``G:\home\user\``）。
    print("\n[sandbox paths] 容器幻觉根")
    for raw in (
        "/home/user/calculator.html",
        "\\home\\user\\calculator.html",
        "C:/home/user/calculator.html",
        "G:\\home\\user\\calculator.html",
        "/home/someone-else/x.txt",
        "\\root\\x.txt",
    ):
        got = _host_path(raw)
        check(f"容器家目录 {raw!r} → 工作区", Path(got) == root / "calculator.html"
              if "calculator" in raw else Path(got) == root / "x.txt", got)

    # ``/app`` 只在无盘符形态下认 —— ``C:\app`` 在 Windows 上可能是真实目录。
    check("无盘符 /app 认", Path(_host_path("/app/main.py")) == root / "main.py",
          _host_path("/app/main.py"))
    check(r"带盘符 C:\app 不动", _host_path("C:/app/main.py") == "C:/app/main.py",
          _host_path("C:/app/main.py"))
    check(
        "中段出现的 home\\user 不动",
        _host_path(r"C:\Users\loo\notes\home\user\x.txt") == r"C:\Users\loo\notes\home\user\x.txt",
    )

    print("\n[sandbox paths] 文件系统端口")
    port = LocalFileSystemPort()
    sandbox = "/var/minis/workspace/workspaces/\u7f16\u7a0b/s-1/matrix_calc.c"
    body = "int main(void) { return 0; }\n"
    _run(port.write_text_file({"path": sandbox, "content": body, "createParents": True}))

    landed = root / "workspaces" / "\u7f16\u7a0b" / "s-1" / "matrix_calc.c"
    check("沙箱路径的写入落在真实目录", landed.is_file(), str(landed))
    check(
        "内容一致",
        landed.is_file() and landed.read_text(encoding="utf-8") == body,
        landed.read_text(encoding="utf-8") if landed.is_file() else "<missing>",
    )

    # 目录不存在时不该在盘根造出 \var\minis\...
    stray = Path(str(root.anchor) + "var")
    check("没有在盘根造出 /var", not stray.exists(), str(stray))

    out = _run(port.read_text_file({"path": sandbox}))
    text = getattr(out, "text", None)
    if text is None:
        text = getattr(out, "content", None)
    check("同一沙箱路径可读回", text == body, repr(text)[:80])

    stat = _run(port.stat({"path": sandbox}))
    check("stat 认这个沙箱路径", getattr(stat, "kind", "") == "file", str(stat)[:80])

    print("\n[sandbox paths] Bash 命令文本")
    cmd = AppExecutionPort._command_text(
        {"command": "cat /var/minis/workspace/a.txt > /var/minis/data/out.txt"}
    )
    check("命令行里的沙箱路径被还原", "/var/minis" not in cmd, cmd)
    check("两处都还原", cmd.count(str(root).replace("\\", "/")) == 1, cmd)
    check(
        "数据目录也还原",
        str(ctx.data_dir).replace("\\", "/") in cmd,
        cmd,
    )
    check(
        "没有沙箱写法的命令不动",
        AppExecutionPort._command_text({"command": "echo hello"}) == "echo hello",
    )


# ---------------------------------------------------------------------------
# 持久 shell：超时、超长行、无换行结尾
# ---------------------------------------------------------------------------

def test_persistent_shell(tmp: Path) -> None:
    from openminis.sandbox.persistent_shell import PersistentShell, detect_shell_spec

    spec = detect_shell_spec()
    print(f"\n[persistent shell] shell = {spec.name} ({spec.executable})")
    if spec.name != "bash":
        print("  - 跳过：本机没找到 bash，cmd.exe 下不能跑 POSIX 断言")
        return

    ws = tmp / "shell-ws"
    ws.mkdir(parents=True, exist_ok=True)

    async def scenario() -> None:
        # --- 正常路径 ---
        sh = PersistentShell("smoke-normal", spec=spec, cwd=ws)
        out, code = await sh.execute_command("echo hi", timeout=20)
        check("普通命令", code == 0 and out.strip() == "hi", f"{code} {out!r}")
        check("shell 报活", sh.is_alive)
        await sh.stop()

        # --- 超长单行 + 无换行结尾（标记会跟在同一行）---
        sh = PersistentShell("smoke-big", spec=spec, cwd=ws)
        out, code = await sh.execute_command(
            "printf 'x%.0s' $(seq 1 200000)", timeout=40
        )
        check("20 万字节单行不丢数据", code == 0 and len(out) == 200000,
              f"code={code} len={len(out)}")
        out, code = await sh.execute_command(
            "seq 1 50000; printf 'y%.0s' $(seq 1 100000)", timeout=40
        )
        check("多行 + 无换行尾巴都在", code == 0 and len(out) > 380000,
              f"code={code} len={len(out)}")
        await sh.stop()

        # --- 超时：必须退役 shell，后面的命令不能排队等挂死的进程 ---
        sh = PersistentShell("smoke-hang", spec=spec, cwd=ws)
        out, code = await sh.execute_command("sleep 300", timeout=2)
        check("超时返回 124", code == 124, f"{code} {out!r}")
        check("超时文本说明 shell 已重建", "shell restarted" in out, out)

        t0 = time.monotonic()
        out2, code2 = await sh.execute_command("cd / && pwd", timeout=20)
        dt = time.monotonic() - t0
        check("超时后同一对象还能执行", code2 == 0 and out2.strip() == "/",
              f"{code2} {out2!r}")
        check("超时后不排队等挂死命令", dt < 12, f"等了 {dt:.1f}s")
        check("重建后的 shell 报活", sh.is_alive)
        await sh.stop()

        # --- 退役竞态：连续「超时 → 立刻执行」，旧 reader 不能误伤新命令 ---
        # 退役时 ``_retire_sync`` 把 ``_process`` 摘掉并 cancel reader，但取消不是
        # 立刻生效的：reader 若先读到 EOF 而自然收尾，会在 finally 里继续跑一段，
        # 而此时新命令已经挂上**新的** pending —— 旧 reader 拿 ``self._pending``
        # 去 ``set_result(-1)`` 就把新命令判成失败（曾偶发：3 次里红 1 次，
        # 新命令凭空拿到 ``-1 ''``）。单轮不容易命中，所以连做几轮。
        sh_race = PersistentShell("smoke-retire-race", spec=spec, cwd=ws)
        for round_no in (1, 2, 3):
            await sh_race.execute_command("sleep 300", timeout=2)
            out_r, code_r = await sh_race.execute_command("echo ok", timeout=15)
            check(
                f"退役后立刻执行不被旧 reader 误伤（第 {round_no} 轮）",
                code_r == 0 and out_r.strip() == "ok",
                f"{code_r} {out_r!r}",
            )
        await sh_race.stop()

        # --- 停止后再用：应重建而不是报 [Shell not running] ---
        out3, code3 = await sh.execute_command("echo revived", timeout=20)
        check("stop 之后自动重建", code3 == 0 and out3.strip() == "revived",
              f"{code3} {out3!r}")
        await sh.stop()

    _run(scenario())


# ---------------------------------------------------------------------------
# 端到端：zcode 循环里用沙箱路径写文件，必须真落到工作区
# ---------------------------------------------------------------------------

class _OneShotWrite:
    """一轮调 ``zcode_write``、下一轮收口的假模型。"""

    def __init__(self, target: str, body: str) -> None:
        self.target = target
        self.body = body
        self.rounds = 0

    async def stream_message(self, messages, system_prompt, max_tokens, temperature=None,
                             image_parts=None, tools=None, thinking_level=None):
        from openminis.data.model import LLMStreamChunk

        self.rounds += 1
        if self.rounds == 1:
            yield LLMStreamChunk.ToolUseStart(id="c1", name="zcode_write")
            yield LLMStreamChunk.ToolCallComplete(
                id="c1", name="zcode_write",
                args={"file_path": self.target, "content": self.body},
            )
            yield LLMStreamChunk.Finished(stop_reason="tool_use")
            return
        yield LLMStreamChunk.Text(text="写好了。")
        yield LLMStreamChunk.Finished(stop_reason="end_turn")

    def aclose(self) -> None:  # pragma: no cover
        return None


def test_zcode_write_lands(tmp: Path) -> None:
    """**用户报的原始故障**：zcode 模式下写文件「没落盘工作区」。"""
    import asyncio as _asyncio

    from openminis.agent.zcode.integration.agent import ZcodeRunOptions, run_zcode_agent

    print("\n[zcode e2e] 沙箱路径写文件要真落盘")
    ctx = AppContext(data_dir=tmp / "e2e-data")
    set_app_context(ctx)
    session = "s-e2e"
    workdir = ctx.external_files_dir / session
    workdir.mkdir(parents=True, exist_ok=True)

    body = "int main(void) { return 0; }\n"
    chunks: list[str] = []

    async def sink(chunk) -> None:
        chunks.append(type(chunk).__name__)

    # 模型照主智能体的世界观给的是**沙箱写法**（这正是出站脱敏后的形态）。
    sandbox = "/var/minis/workspace/workspaces/\u7f16\u7a0b/s-e2e/matrix_calc.c"
    result = _asyncio.run(
        run_zcode_agent(
            _OneShotWrite(sandbox, body),
            "写个 matrix_calc.c",
            session_id=session,
            options=ZcodeRunOptions(
                working_directory=str(workdir),
                workspace_root=str(workdir),
                tool_ids=["zcode_write"],
                chunk_sink=sink,
                borrow_host_tools=False,
                borrow_host_skills=False,
            ),
        )
    )

    landed = ctx.external_files_dir / "workspaces" / "\u7f16\u7a0b" / "s-e2e" / "matrix_calc.c"
    check("沙箱路径的写入没报错", not result.error and all(c["ok"] for c in result.tool_calls),
          f"error={result.error} calls={[(c['name'], c['ok']) for c in result.tool_calls]}")
    check("文件真的落到工作区", landed.is_file(), str(landed))
    check(
        "内容一致",
        landed.is_file() and landed.read_text(encoding="utf-8") == body,
        landed.read_text(encoding="utf-8") if landed.is_file() else "<missing>",
    )
    # 没有落到盘根（``<cwd 盘>:\var\minis\...``）——这就是「没落盘」的旧表现。
    stray = Path(str(ctx.external_files_dir.anchor) + "var")
    check("没有落到盘根 \\var", not stray.exists(), str(stray))

    # 工具卡：宿主靠 ``ToolCallComplete`` 发 ``toolStart`` 帧。只转 Text 的话
    # 前端永远看不到「工具调用」这一步（用户报的第二个现象）。
    check(
        "ToolCallComplete 已转发给宿主（工具卡靠它）",
        any("ToolCallComplete" in name for name in chunks),
        str(chunks),
    )
    check("ToolResult 已转发", any("ToolResult" in name for name in chunks), str(chunks))


def test_zcode_system_prompt_sections(tmp: Path) -> None:
    """系统提示必须**真的**带上内核各段 —— 防止 section 静默降级成空串。

    历史故障：``_section_text`` 原用 ``getattr(context.sections, name)`` 取 builder，
    而那个包的 ``__init__`` 刻意保持空 barrel（子模块互相依赖，包级再导出会成导入环）
    → ``getattr`` 永远 ``AttributeError`` → 被 ``except: return ""`` 吞掉 → **每一段
    都变成空串**。系统提示里只剩工具链清单：模型不知道工作目录是什么，就按训练数据
    里最常见的 Linux 容器惯例自己编了个 ``/home/user``，写文件落到盘根
    （用户机器上确有其事：``G:\\home\\user\\calculator.html``）。

    这一条是四道静态体检和既有冒烟**都没覆盖**的：它们检查导入健康、字段序、语法、
    行为，但没人检查**提示正文的内容**。所以这里必须断言「哪几段在、写的是什么」。
    """
    from openminis.agent.zcode.integration.agent import build_zcode_system_prompt

    print("\n[zcode prompt] 内核各段必须真的进提示")
    ctx = AppContext(data_dir=tmp / "prompt-data")
    set_app_context(ctx)
    workdir = ctx.external_files_dir / "s-prompt"

    prompt = build_zcode_system_prompt(
        working_directory=str(workdir),
        tool_names=["Read"],
        borrow_host_skills=False,
    )

    check("CLI 前缀段在（你是谁）", "You are ZCode" in prompt, prompt[:70])
    check("身份/守则段在（Harness）", "# Harness" in prompt, "")
    check("环境段在（工作目录）", "Primary working directory" in prompt, "")
    check(
        "环境段写的是沙箱写法",
        "/var/minis/workspace/s-prompt" in prompt,
        [ln for ln in prompt.splitlines() if "working directory" in ln],
    )
    check(
        "提示里不泄露机器路径",
        "AppData" not in prompt and "C:\\Users" not in prompt,
        [ln for ln in prompt.splitlines() if "AppData" in ln or "C:\\Users" in ln],
    )
    check("当前日期段在", "currentDate" in prompt, "")
    check("宿主路径约定在（反 /home/user 幻觉）", "Path conventions" in prompt, "")
    check("工具链段仍在", "ZCode toolchain" in prompt, "")

    # 段落是**拼**出来的，不是一条巨型硬编码串：各段之间必须是空行分隔的独立块。
    check("多段拼接（>= 5 块）", len(prompt.split("\n\n# ")) >= 5,
          f"blocks={len(prompt.split(chr(10) + chr(10) + '# '))}")


def test_zcode_container_root_lands(tmp: Path) -> None:
    """端到端：模型给容器幻觉根 ``/home/user/...`` 时也要落到工作区。

    这是用户报的第二种现场 —— ``{"filePath": "\\home\\user\\calculator.html"}``。
    修复前它会在盘根建出 ``<cwd 盘>:\\home\\user\\``（用户机器上真出现了
    ``G:\\home\\user\\calculator.html``）。
    """
    import asyncio as _asyncio

    from openminis.agent.zcode.integration.agent import ZcodeRunOptions, run_zcode_agent

    print("\n[zcode e2e] 容器幻觉根写文件要落工作区")
    ctx = AppContext(data_dir=tmp / "container-data")
    set_app_context(ctx)
    session = "s-container"
    workdir = ctx.external_files_dir / session
    workdir.mkdir(parents=True, exist_ok=True)

    body = "<html><body>1+1=2</body></html>\n"
    result = _asyncio.run(
        run_zcode_agent(
            _OneShotWrite("/home/user/calculator.html", body),
            "写个计算器页面",
            session_id=session,
            options=ZcodeRunOptions(
                working_directory=str(workdir),
                workspace_root=str(workdir),
                tool_ids=["zcode_write"],
                borrow_host_tools=False,
                borrow_host_skills=False,
            ),
        )
    )

    landed = ctx.external_files_dir / "calculator.html"
    check(
        "没报错",
        not result.error and all(c["ok"] for c in result.tool_calls),
        f"error={result.error} calls={[(c['name'], c['ok']) for c in result.tool_calls]}",
    )
    check("文件落到工作区根", landed.is_file(), str(landed))
    check(
        "内容一致",
        landed.is_file() and landed.read_text(encoding="utf-8") == body,
        landed.read_text(encoding="utf-8") if landed.is_file() else "<missing>",
    )
    stray = Path(str(ctx.external_files_dir.anchor) + "home")
    check("没有在盘根建出 \\home", not stray.exists(), str(stray))


def test_workspace_guard(tmp: Path) -> None:
    """落盘护栏：ZCode 的读写只能落在工作区内（文件端口 + shell 两道）。

    内核的 ``path_policy`` 刻意不做 containment（原版注释写明），上游靠权限状态机
    管 —— 而适配层的 ``_run_turns`` 是自实现循环、直接调 handler，那道门没接。
    于是实测 ZCode 能写 ``C:\\Users\\loo\\…`` 主目录、能用 ``../../`` 跳出工作区，
    而主智能体的同类操作会被 ``resolve_workspace_path`` / ``check_shell_command``
    拦下。同一台机器上「换个循环就能绕开沙盒」不能接受，这两道门就是补它。
    """
    from openminis.agent.zcode.integration.host_ports import AppExecutionPort
    from openminis.sandbox.guard import check_shell_command

    print("\n[guard] 文件端口")
    ctx = AppContext(data_dir=tmp / "guard-data")
    set_app_context(ctx)
    root = ctx.external_files_dir
    session = "s-guard"
    workdir = root / session
    workdir.mkdir(parents=True, exist_ok=True)
    port = LocalFileSystemPort(workspace_root=str(workdir))

    outside = tmp / "OUTSIDE" / "x.txt"
    home_probe = Path.home() / "zcode-guard-probe.txt"

    def _denied(coro) -> bool:
        """越界时端口抛 ``FileSystemPortError``，``code`` 是 ``permission_denied``。"""
        try:
            _run(coro)
        except Exception as exc:
            return getattr(exc, "code", "") == "permission_denied"
        return False

    for target, label in (
        (str(outside), "工作区外绝对路径"),
        (str(home_probe), "用户主目录"),
        ("../../OUTSIDE/up.txt", "相对路径上跳"),
    ):
        check(
            f"越界写被拒：{label}",
            _denied(port.write_text_file({"path": target, "content": "x"})),
            target,
        )

    check(
        "越界删被拒",
        _denied(port.remove_file({"path": str(outside)})),
        str(outside),
    )
    check(
        "越界建目录被拒",
        _denied(port.create_directory({"path": str(tmp / "OUTSIDE" / "d")})),
    )
    check(
        "越界读被拒",
        _denied(port.read_text_file({"path": str(home_probe)})),
    )

    # 工作区内照常（含沙箱写法 → 工作区根）
    _run(port.write_text_file({"path": str(workdir / "ok.txt"), "content": "hi"}))
    check("工作区内写入正常", (workdir / "ok.txt").is_file(), str(workdir / "ok.txt"))
    _run(
        port.write_text_file(
            {"path": "/var/minis/workspace/shared.txt", "content": "hi"}
        )
    )
    check(
        "沙箱写法写入正常（落工作区根）",
        (root / "shared.txt").is_file(),
        str(root / "shared.txt"),
    )

    check("越界文件真的没被创建", not outside.exists(), str(outside))
    check("主目录没被写", not home_probe.exists(), str(home_probe))
    if home_probe.exists():  # pragma: no cover - 护栏失效时的自保
        home_probe.unlink()

    print("\n[guard] shell（与主智能体同一道门）")
    for cmd, label, should_block in (
        (f"echo x > {outside}", "重定向写出到工作区外", True),
        ("cat C:/Users/loo/.ssh/id_rsa", "读工作区外的文件", True),
        (f"rm -rf {workdir}", "破坏性删除", True),
        ("cd C:/Windows && dir", "cd 出工作区", True),
        ("echo hello", "普通命令", False),
        (f"echo hi > {workdir / 'ok2.txt'}", "工作区内重定向", False),
    ):
        blocked = check_shell_command(cmd, session_id=session, cwd=str(workdir))
        check(
            f"{label}（期望{'拦' if should_block else '放'}）",
            (blocked is not None) == should_block,
            str(blocked)[:70],
        )

    # 端到端：guard 必须真的接在 ``AppExecutionPort.run`` 上，而不只是纯函数可用。
    from openminis.sandbox.persistent_shell import detect_shell_spec

    if detect_shell_spec().name != "bash":
        print("  - 跳过 shell 端到端：本机没找到 bash")
        return

    e2e_target = tmp / "OUTSIDE" / "e2e.txt"
    exec_port = AppExecutionPort("smoke-guard")
    try:
        result = _run(exec_port.run({"command": f"echo x > {e2e_target}"}))
    finally:
        _run(exec_port.close())
    check("被拦的命令没被执行（文件没生成）", not e2e_target.exists(), str(e2e_target))
    check(
        "被拦时返回失败的执行结果",
        str(getattr(result, "status", "")) == "failed",
        str(getattr(result, "status", "")),
    )


# ---------------------------------------------------------------------------
# 进程树回收：停掉 shell 必须连它跑着的孙进程一起收
# ---------------------------------------------------------------------------

#: 死循环 + 不 sleep —— 真烧一个核，正是用户那边看到的现象
_CPU_HOG_BODY = (
    "import os,pathlib\n"
    "pathlib.Path(r'{pf}').write_text(str(os.getpid()))\n"
    "while True: pass\n"
)


def _pid_alive(pid: int) -> bool:
    """用 ``tasklist`` 探活（``wmic`` 在本机被安全策略硬拦）。"""
    if not pid:
        return False
    try:
        cp = subprocess.run(  # noqa: S603 - 固定参数
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, timeout=15,
        )
    except Exception:
        return False
    # 中文 Windows 的 tasklist 输出是 GBK，``text=True`` 会直接把 stdout 变成 None
    out = (cp.stdout or b"").decode("gbk", "replace")
    return str(pid) in out


def _kill_tree(pid: int) -> None:
    subprocess.run(  # noqa: S603 - 探针清理，固定参数
        ["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True
    )


def test_shell_tree_reap(tmp: Path) -> None:
    """shell 停/退役时，**子孙进程**必须一并回收。

    为什么这条必须是真机断言：``Process.terminate()`` 在 Windows 上只结束 bash
    **自己**，它跑着的 ``node``/``python``（``&`` 后台、忘了退出的 ``tail -f``、
    死循环脚本）会被系统重新挂到一个不存在的父上继续跑 —— 用户报的「ZCode 干完活
    还留着一堆 pid 在烧 CPU」就是这个。而且修复的**顺序**也是被测对象：
    ``taskkill /T`` 按当前父子快照找人，先 ``terminate`` 掉 bash 就再也找不到孙子
    （实测：fire-and-forget 版本三个场景全红）。
    """
    from openminis.sandbox.execution_coordinator import ExecutionCoordinator
    from openminis.sandbox.persistent_shell import PersistentShell, detect_shell_spec

    spec = detect_shell_spec()
    print(f"\n[进程树回收] shell = {spec.name} ({spec.executable})")
    if spec.name != "bash":
        print("  - 跳过：本机没找到 bash")
        return

    ws = tmp / "reap-ws"
    ws.mkdir(parents=True, exist_ok=True)

    def child_cmd(pid_file: Path, *, background: bool) -> str:
        body = _CPU_HOG_BODY.format(pf=str(pid_file).replace("\\", "/"))
        return 'python -c "' + body + '"' + (" &" if background else "")

    async def spawn(session: str, pid_file: Path, *, background: bool):
        sh = PersistentShell(session, spec=spec, cwd=ws)
        await sh.ensure_started()
        if pid_file.exists():
            pid_file.unlink()
        # 前台那条会挂住直到超时；后台那条立刻返回（`&`）
        await sh.execute_command(
            child_cmd(pid_file, background=background), timeout=20 if background else 2
        )
        for _ in range(50):
            if pid_file.exists():
                break
            await asyncio.sleep(0.1)
        child = int(pid_file.read_text().strip()) if pid_file.exists() else 0
        return sh, child

    async def scenario() -> None:
        # 1) 显式 stop()
        pf = tmp / "reap-a.pid"
        sh, child = await spawn("smoke-reap-a", pf, background=True)
        check("stop 前孙进程在烧 CPU", _pid_alive(child), f"pid={child}")
        await sh.stop()
        await asyncio.sleep(1.0)
        check("stop() 连带回收孙进程", not _pid_alive(child), f"pid={child} 仍存活")
        if _pid_alive(child):
            _kill_tree(child)

        # 2) stop_sync()（取消传播 / atexit 路径）
        pf = tmp / "reap-b.pid"
        sh, child = await spawn("smoke-reap-b", pf, background=True)
        sh.stop_sync()
        await asyncio.sleep(1.2)
        check("stop_sync() 连带回收孙进程", not _pid_alive(child), f"pid={child} 仍存活")
        if _pid_alive(child):
            _kill_tree(child)

        # 3) 命令超时 → 自动退役（用户在 ZCode 里最常撞上的那条）
        pf = tmp / "reap-c.pid"
        sh, child = await spawn("smoke-reap-c", pf, background=False)
        await asyncio.sleep(0.8)
        check("超时退役后孙进程被回收", not _pid_alive(child), f"pid={child} 仍存活")
        if _pid_alive(child):
            _kill_tree(child)
        await sh.stop()

        # 4) coordinator.shutdown_sync()（atexit 走的就是它）
        pf = tmp / "reap-d.pid"
        coord = ExecutionCoordinator()
        sh = await coord._get_or_create_shell("smoke-reap-d")  # noqa: SLF001
        if pf.exists():
            pf.unlink()
        await sh.execute_command(child_cmd(pf, background=True), timeout=20)
        for _ in range(50):
            if pf.exists():
                break
            await asyncio.sleep(0.1)
        child = int(pf.read_text().strip()) if pf.exists() else 0
        coord.shutdown_sync()
        await asyncio.sleep(1.2)
        check("shutdown_sync() 连带回收孙进程", not _pid_alive(child), f"pid={child} 仍存活")
        if _pid_alive(child):
            _kill_tree(child)

        # 5) 「一轮干完」收残留后台作业：shell 还活着，但 ``&`` 起的进程不该留着
        pf = tmp / "reap-e.pid"
        coord = ExecutionCoordinator()
        sh = await coord._get_or_create_shell("smoke-sess:zcode")  # noqa: SLF001
        # ZCode 的 shell 键带 ``:``（``<宿主 id>:zcode``），而 Windows 目录名不允许
        # 冒号 —— 修前这里直接 ``NotADirectoryError: [WinError 267]``。
        check("会话 id 带冒号也能建 shell（目录名已折换）",
              ":" not in sh.cwd.name and sh.cwd.name == "smoke-sess_zcode",
              f"cwd={sh.cwd}")
        check("会话身份不被折换（MINIS_SESSION_ID 仍是原值）",
              sh._env.get("MINIS_SESSION_ID") == "smoke-sess:zcode",  # noqa: SLF001
              f"{sh._env.get('MINIS_SESSION_ID')!r}")  # noqa: SLF001
        if pf.exists():
            pf.unlink()
        await sh.execute_command(child_cmd(pf, background=True), timeout=20)
        for _ in range(50):
            if pf.exists():
                break
            await asyncio.sleep(0.1)
        child = int(pf.read_text().strip()) if pf.exists() else 0
        check("一轮结束前后台作业还在跑", _pid_alive(child), f"pid={child}")
        reaped = await coord.reap_background_jobs("smoke-sess")
        await asyncio.sleep(1.0)
        check("reap 报告了被收掉的作业", bool(reaped), f"reaped={reaped}")
        check("后台作业已被收掉（shell 仍可用）", not _pid_alive(child), f"pid={child} 仍存活")
        if _pid_alive(child):
            _kill_tree(child)
        out_r, code_r = await sh.execute_command("echo still-alive", timeout=15)
        check("收完残留后 shell 照常工作", code_r == 0 and out_r.strip() == "still-alive",
              f"{code_r} {out_r!r}")
        await coord.shutdown()

    _run(scenario())


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="smoke-host-ports-"))
    try:
        test_sandbox_paths(tmp)
        test_zcode_write_lands(tmp)
        test_zcode_container_root_lands(tmp)
        test_zcode_system_prompt_sections(tmp)
        test_workspace_guard(tmp)
        test_persistent_shell(tmp)
        test_shell_tree_reap(tmp)
    finally:
        reset_app_context()
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nPASSED = {PASSED}  FAILED = {FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
