# ZCode 编程 agent 内核 → Python 移植规范

本包是 ZCode（`ZCode-main/apps/zcode-cli/packages/core`，TypeScript）**编程 agent 逻辑内核**
的 Python 移植。目标：**逐文件 1:1 翻译**，保留原语义、注释与结构，使两侧可模块级对照。

- 原始 TS：`ZCode-main/apps/zcode-cli/packages/core/src/**`
- 契约层 TS：`ZCode-main/apps/zcode-cli/packages/contracts/src/**`
- Python 镜像：`src/openminis/agent/zcode/<module>/`
- 包映射：`@zcode/core` → `openminis.agent.zcode`，`@zcode/contracts` → `openminis.agent.zcode.contracts`

> 与同项目 `PORTING.md`（Kotlin→Python，OpenMinis Android 移植）规则一致，
> 本文件只补充 TS→Python 的差异部分。

---

## 0.0 移植边界（用户已确认）

**只移植编程部分，不移植办公部分。** 落点是 OpenMinis 的「子编程 agent 包 / 内置助手」。

### 在范围内（编程 agent 内核）

- `agent/` — turn 状态机、消息历史
- `tool/scheduler|registry|types|edit-matchers|bash-timeout-policy|path-policy|read-file-state` — 工具基础设施
- `tool/executor/` — 工具执行编排（call-runner / approval / permission-flow / hook-flow / timeout / result-serialization）
- `tool/handlers/` 里的**编程工具**：`bash*`、`read`、`write`、`edit`、`glob`、`grep`、`todo`、`plan-mode`、`agent`(Task)、`webfetch`、`websearch`、`ask-user-question`、`skill`、`task-output`、`task-stop`
- `permission/`、`compact/`、`context/`、`memory/`
- `runtime/` — AgentRuntime 与 turn 主循环编排

### 明确排除（办公部分 / 非编程能力）

| 排除项 | 原始位置 | 理由 |
|---|---|---|
| PDF 读取 | `tool/handlers/read-pdf.ts`、`contracts/tools/read-pdf.ts` | 办公文档 |
| 文档/PPT/表格类 skill 与 handler | 办公类 plugin/skill 资产 | 办公 |
| 定时任务 / 闲时任务 | `contracts/tools/automation.ts`、`off-peak.ts` | 办公自动化 |
| 动态工作流（存档/创建/修订/评估） | `tool/handlers/{create,amend,save,resolve,eval}-workflow*.ts`、`contracts/tools/{workflow,create-workflow,amend-workflow,saved-workflow,list-saved-workflows,list-workflow-runs,get-workflow-run,resume-workflow-run,get-workflow-run-roster,eval-workflow-snippet,resolve-workflow-question}.ts`、`core/src/workflow/` | 办公编排 |
| 浏览器自动化 | `core/src/browser-client/`、`tool/handlers/browser*`、`browser-use-plugin` | 非编程内核 |
| Node REPL 宿主 | `core/src/repl/`、`tool/handlers/node-repl.ts` | 平台专有 |
| 图像/视频产出类工具 | `read-image`、媒体预算/图片处理相关 handler | 非编程内核 |

> 排除项**不生成 Python 文件**，但在 `ZCORE_PORTING_MAP.md` 登记为 `n-a (scope: non-coding)`，
> 以便后续如需扩展时有据可查。

---

## 0. 黄金法则

1. **不删减逻辑。** TS 里每一个分支、`catch`、边界判断、`??` 兜底都要有对应。宁可笨，不要"优化"。
2. **保留注释。** 原注释（含中文设计说明）原样搬到对应位置；Python 侧补充用 `# PORT:` 前缀标记。
3. **每个文件头写来源**，格式见 §7。
4. **不假设、不脑补。** 遇到 Node/Electron/TS 专有 API，按语义翻译成 Python 等价物并
   `# PORT:` 标注，**不要**直接删掉。找不到等价物就保留为「端口（Protocol）占位 + 抛 NotImplementedError」，
   并在文件头标 `# PORT-STATUS: partial`。
5. **一个 TS 文件 → 一个 Python 文件**，文件名 `kebab-case.ts` → `snake_case.py`。
6. **类型也要翻译。** `interface` → `@dataclass` 或 `Protocol`/`TypedDict`，不要因为 Python 没有类型
   擦除就省略；类型是这份内核的语义载体。

---

## 1. 类型与声明映射

| TypeScript | Python |
|---|---|
| `interface X { a: string; b?: number }` 纯数据 | `@dataclass(slots=True)` |
| `interface X` 不可变值对象 | `@dataclass(frozen=True, slots=True)` |
| `interface X { f(): void }` 行为契约 | `typing.Protocol` |
| `interface X` 仅作宽松载荷 | `TypedDict` |
| `class X { constructor(...) }` | `@dataclass` 或普通 `class` + `__init__` |
| `class X extends Error` | `Exception` 子类 |
| `type A = B \| C` | `A = B \| C`（`typing.Union` / `X | Y`） |
| `type S = string & { __brand }` | `NewType("SessionId", str)` 或 `str` 别名 + 注释 |
| `enum` / `as const` 对象 | `enum.Enum`（`str` 值），或模块级常量 + `Literal` |
| `Record<string, T>` | `dict[str, T]` |
| `readonly T[]` | `Sequence[T]`（入参）/ `list[T]`（出参） |
| `T[]` / `Array<T>` | `list[T]` |
| `Set<T>` | `set[T]` |
| `Map<K, V>` | `dict[K, V]` |
| `Promise<T>` | `Awaitable[T]` / `async def -> T` |
| `AsyncIterable<T>` | `AsyncIterator[T]` |
| `AbortSignal` | `asyncio.Event` 包装的 `AbortSignal`（见 §5） |
| `Date` | `datetime`（UTC aware）；毫秒时间戳用 `int` 且字段名保留 `*_ms` |
| `unknown` | `Any`（局部）；跨模块边界优先具体类型 |
| `void` / `undefined` | `None` |
| `X \| undefined` | `X \| None = None` |
| `crypto.randomUUID()` | `uuid.uuid4()` |
| `JSON.parse/stringify` | `json.loads/dumps` |
| `structuredClone` | `copy.deepcopy` |
| `Object.assign(a, b)` | `a.update(b)` 或逐字段赋值 |

### 命名
- 类 / interface：`CamelCase`（**保持原名**，便于对照）
- 函数 / 方法 / 变量 / 字段：`snake_case`
- 常量 / enum 成员：`UPPER_SNAKE_CASE`
- 私有：`_leading_underscore`（TS 的 `private` 一律加下划线）
- 布尔：`is_*` / `has_*`
- enum 值保持**原字符串**（协议兼容），如 `TurnPhase.STREAMING = "streaming"`

---

## 2. 控制流映射

| TypeScript | Python |
|---|---|
| `switch (x) { case A: ...; default: ... }` | `match x:` / `case A():` … `case _:` |
| `x?.f()` 可选链 | `if x is not None: x.f()` |
| `a ?? b` | `a if a is not None else b` |
| `a || b`（真值兜底） | `a or b` |
| `a && b` | `a and b` |
| `cond ? a : b` | `a if cond else b` |
| `for (const x of arr)` | `for x in arr:` |
| `for (const [k, v] of map)` | `for k, v in d.items():` |
| `arr.map(f)` / `.filter(f)` | 列表推导 / `filter` |
| `Object.entries(o)` | `o.items()` |
| `try { } catch (e) { }` | `try/except`（`except Exception as e:`，窄化时用具体异常） |
| `throw createCoreError(...)` | `raise CoreError(...)`（见 §4） |
| `Array.from(set)` | `list(s)` |
| `new Set(arr)` 去重保序 | `list(dict.fromkeys(arr))` |
| `arr.flat()` | `[y for xs in arr for y in xs]` |
| `a.sort((x,y)=>x-y)` | `sorted(a)` / `a.sort(key=...)` |

---

## 3. 并发与流

原项目是 Node 单线程事件循环 + `AsyncGenerator` 流。

| TypeScript | Python |
|---|---|
| `async function f()` | `async def f()` |
| `await p` | `await p` |
| `Promise.all([...])` | `asyncio.gather(...)` |
| `Promise.allSettled` | `asyncio.gather(..., return_exceptions=True)` |
| 串行 `for await (const x of gen)` | `async for x in gen:` |
| `setTimeout(fn, ms)` | `await asyncio.sleep(ms / 1000)` |
| `new AbortController()` | `AbortSignal`（`src/openminis/agent/zcode/core/abort.py`） |
| `signal.aborted` | `signal.aborted` |
| `signal.throwIfAborted()` | `signal.throw_if_aborted()` |
| 并发上限 | `asyncio.Semaphore(n)` |
| `EventEmitter` 同步扇出 | `list[Callable]` + 顺序 `await` 调用 |

**规则**：TS 里 `async` 的函数，Python 侧必须 `async def`，调用处必须 `await`。不要为了省事改同步。
`AbortSignal` 一律用本包自己的 `AbortSignal`，不要用 `asyncio.CancelledError` 代替。

---

## 4. 错误处理

`@zcode/contracts` 的 `createCoreError(type, message, {context, recoverable, retryable, cause})`
→ `openminis.agent.zcode.contracts.errors.create_core_error(...)`，返回 `CoreError(Exception)`。

- 原 `throw createCoreError(...)` → `raise create_core_error(...)`
- 原 `catch` 里做类型判断 → Python 侧用 `except <具体异常>` 或 `if isinstance(e, CoreError)`
- **不吞异常。** 原代码 catch 后做什么，Python 侧照做。
- TS 的 `instanceof` 判定：Python 用 `isinstance`；`isCoreError` 语义用 `is_core_error()`。

---

## 5. 平台与 IO

**核心原则**：内核**不直接做 IO**。原 TS 里凡是 `fs / child_process / http / electron` 的地方，
都经由 `*Port` 接口注入（`FileSystemPort`、`ExecutionPort`、`HttpClientPort`…）。
Python 侧**照搬端口（Protocol）定义**，具体实现由调用方（OpenMinis 侧适配层）提供。

| TypeScript | Python |
|---|---|
| `node:fs/promises` | `FileSystemPort`（Protocol） |
| `child_process.spawn` | `ExecutionPort`（Protocol） |
| `fetch` / `node:http` | `HttpClientPort`（Protocol） |
| `node:path` join/resolve | `pathlib.Path` |
| `process.cwd()` | `working_directory`（显式传参） |
| `process.env` | `dict[str, str]`（显式传参） |
| `crypto.createHash` | `hashlib` |
| `performance.now()` | `time.monotonic()` |
| `Date.now()` | `int(time.time() * 1000)` |
| `console.warn/log` | `logging.getLogger(__name__)` |
| `AbortSignal` | `zcode.core.abort.AbortSignal` |

---

## 6. 目录映射

| ZCode TS | 本包 Python | 状态 |
|---|---|---|
| `contracts/src/interfaces/shared.ts` | `contracts/ids.py` | - |
| `contracts/src/errors/index.ts` | `contracts/errors.py` | - |
| `contracts/src/model/index.ts` | `contracts/model.py` | - |
| `contracts/src/tools/contract.ts` | `contracts/tools.py` | - |
| `core/src/agent/turn-state.ts` | `agent/turn_state.py` | - |
| `core/src/agent/turn-machine.ts` | `agent/turn_machine.py` | - |
| `core/src/agent/message-history.ts` | `agent/message_history.py` | - |
| `core/src/tool/types.ts` | `tool/types.py` | - |
| `core/src/tool/registry.ts` | `tool/registry.py` | - |
| `core/src/tool/scheduler.ts` | `tool/scheduler.py` | - |
| `core/src/tool/edit-matchers.ts` | `tool/edit_matchers.py` | - |
| `core/src/permission/*` | `permission/*` | - |
| `core/src/compact/*` | `compact/*` | - |
| `core/src/context/*` | `context/*` | - |
| `core/src/tool/executor/*` | `tool/executor/*` | - |
| `core/src/tool/handlers/*` | `tool/handlers/*` | - |
| `core/src/runtime/*` | `runtime/*` | - |

状态标记：`done` 完成 · `partial` 部分 / 占位 · `n-a` Node 专有，无对应（保留路径不翻译）

---

## 7. 文件头模板

```python
"""Turn machine — state machine for turn lifecycle.

Ported from: packages/core/src/agent/turn-machine.ts
Original package: @zcode/core (agent)
"""

from __future__ import annotations
```

- 第二行写**相对 `apps/zcode-cli/` 的原始路径**，便于对照。
- 类 / 函数 docstring 优先复用原 TS 注释。

---

## 8. 禁止事项

- ❌ 不要用 `# type: ignore` 铺路（第三方缺桩除外，需 `# PORT:` 说明）
- ❌ 不要引入原项目没有的新抽象层
- ❌ 不要把 `async` 函数改同步
- ❌ 不要删掉原设计注释（尤其中文设计说明）
- ❌ 不要边翻译边"顺手重构"
- ❌ 不要写 `pass` 占位了事——没翻完就在文件头标 `# PORT-STATUS: partial`
- ✅ 允许：为 Python 语义清晰而做的等价改写（并在 `# PORT:` 说明）

---

## 9. 完成标准

每个模块翻译完必须：

1. `python -c "import openminis.agent.zcode.<module>"` 通过
2. 语法与导入零错误
3. 在 `ZCORE_PORTING_MAP.md` 登记：原始路径 → Python 路径 → 状态
