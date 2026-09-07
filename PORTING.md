# Kotlin → Python 移植规范

本目录是 `src/android/app/src/main/java/com/openminis/app/` 下 Kotlin 代码的 Python 移植。
目标：**全量 1:1 翻译**，保留原始语义、注释与结构，让 Python 版与原版逐模块可对照。

- 原始 Kotlin：`../src/android/...`（只读，不要修改）
- Python 镜像：`src/openminis/<module>/`
- 包映射：`com.openminis.app.foo.bar.Baz` → `openminis/foo/bar.py::Baz`

---

## 0. 黄金法则

1. **不删减逻辑。** 原文件里每一个分支、每一个 `catch`、每一个边界判断都要有对应。
   宁可笨一点，也不要"优化"掉原作者刻意写的处理。
2. **保留英文注释。** 原注释（含 `[T-xxx]` ticket 标记）原样搬到对应位置。
   翻译时只加必要的 Python 侧说明，用 `# PORT:` 前缀标记，例如
   `# PORT: Kotlin 的 when 在这里用 match 表达`。
3. **每个文件头必须写来源**，格式见 §7。
4. **不要假设，不要脑补。** 遇到看不懂的 Android API（如 `Context`、`Bundle`、
   `AccessibilityNodeInfo`），照语义翻译成 Python 等价物并在 `# PORT:` 注释里
   注明"Android 专有，Python 侧为等价实现"，**不要**直接删掉。
5. **一个 Kotlin 文件 → 一个 Python 文件**，文件名 `CamelCase.kt` → `snake_case.py`。
   例外：文件内只有一个极小的 `object`/`enum`，可并入同目录 `__init__.py` 导出。

---

## 1. 类型与声明映射

| Kotlin | Python |
|---|---|
| `data class X(val a: Int, val b: String? = null)` | `@dataclass(frozen=True, slots=True)` 同名类 |
| `data class` 需要校验/序列化 | `pydantic.BaseModel`（用于 API 边界、JSON 载荷） |
| `class X(val a: Int)` 可变 | `@dataclass` （不加 frozen） |
| `sealed class`/`sealed interface` | `abc.ABC` 基类 + 子类；判定用 `match type(x):` 或 `isinstance` |
| `object X {}` | `enum.Enum`（若是常量集合）或模块级单例实例 |
| `companion object` | 类级常量（ClassVar）+ `@classmethod` / `@staticmethod` |
| `enum class X { A, B }` | `enum.Enum`（值用 `str`/`int` 保持一致） |
| `typealias A = B` | `A = B` 或直接 `TypeAlias` |
| `interface X { fun f() }` | `abc.ABC` + `@abstractmethod` |
| `val x: T get() = ...` | `@property` |
| `lateinit var x: T` | `x: T`（在 `__init__` 赋值）或 `x: T \| None = None` |
| `T?` | `T \| None` |
| `List<T>` / `MutableList<T>` | `list[T]` |
| `Map<K,V>` / `MutableMap<K,V>` | `dict[K, V]` |
| `Set<T>` | `set[T]` |
| `Long`（毫秒时间戳） | `int`，字段名保留 `*_ms` 后缀语义，注释注明 milliseconds |
| `ByteArray` | `bytes` |
| `Result<T>` | 见 §4 |
| `Unit` | `None` |

### 命名
- 类：`CamelCase`（保持原名）
- 函数 / 方法 / 变量：`snake_case`
- 常量：`UPPER_SNAKE_CASE`
- 私有：`_leading_underscore`（Kotlin 的 `private` 一律加下划线）
- 布尔：`is_*` / `has_*`（对应 Kotlin 的 `isXxx`/`hasXxx`）
- Kotlin 的 `internal` → 不加下划线，但在模块注释里标注

---

## 2. 控制流映射

| Kotlin | Python |
|---|---|
| `when (x) { is A -> ...; else -> ... }` | `match x:` / `case A():` … `case _:` |
| `when { cond -> ... }`（无主语） | `if / elif / else` |
| `x?.let { ... }` | `if x is not None: ...` |
| `x.also { ... }` | 顺序语句 |
| `x.apply { ... }` | 构造后赋值 |
| `run { ... }` / `with(x) { ... }` | 直接内联 |
| `require(x)` / `check(x)` | `if not x: raise ValueError(...)` / `RuntimeError(...)` |
| `error("msg")` | `raise RuntimeError("msg")` |
| `TODO("msg")` | `raise NotImplementedError("msg")` 并保留原字符串 |
| `repeat(n) {}` | `for _ in range(n):` |
| `for (i in 0 until n)` | `for i in range(n):` |
| `for (i in n downTo 0)` | `for i in range(n, -1, -1):` |

---

## 3. 并发映射

原项目是 Kotlin 协程，Python 侧统一用 **anyio + asyncio**。

| Kotlin | Python |
|---|---|
| `suspend fun f()` | `async def f()` |
| `fun f()`（阻塞） | `def f()` |
| `withContext(Dispatchers.IO) {}` | `await anyio.to_thread.run_sync(...)` |
| `withContext(Dispatchers.Main) {}` | 直接执行（TUI/UI 事件循环） |
| `coroutineScope { launch {} }` | `async with anyio.create_task_group() as tg: tg.start_soon(...)` |
| `async {}` / `Deferred` | `anyio.create_task_group` 或 `asyncio.create_task` |
| `runBlocking {}` | `anyio.run(...)` / `asyncio.run(...)`（仅在 CLI 入口） |
| `delay(ms)` | `await anyio.sleep(ms / 1000)` |
| `Mutex` | `anyio.Lock` |
| `Semaphore(n)` | `anyio.Semaphore(n)` |
| `Channel<T>` | `anyio.create_memory_object_stream` |
| `Flow<T>` | `AsyncIterator[T]`（async generator） |
| `StateFlow<T>` / `MutableStateFlow<T>` | `openminis.core.flow.StateFlow`（见 `core/flow.py`） |
| `SharedFlow` | `openminis.core.flow.SharedFlow` |
| `Job` / `.cancel()` | `anyio.CancelScope` |
| `@Volatile` | 无直接对应，用 `anyio.Lock` 或注释标注 |

**规则**：只要原函数是 `suspend`，Python 侧必须 `async def`，调用处必须 `await`。
不要为了省事把 async 函数改成同步。

---

## 4. 错误处理

Kotlin 大量使用 `Result<T>` 和 `runCatching`。

```python
# Kotlin: runCatching { f() }.getOrElse { default }
from openminis.core.result import Result, Ok, Err

result: Result[T] = ...
if result.is_ok:
    value = result.unwrap()
else:
    err = result.error
```

- `runCatching {}` → `Result.of(lambda: ...)` 或 `try/except`
- `.getOrNull()` → `result.ok_or_none()`
- `.getOrThrow()` → `result.unwrap()`（失败时抛出）
- `.onSuccess {}` / `.onFailure {}` → 对应方法链
- `sealed class Error` → `Exception` 子类层次，保留原名

**不要吞异常。** 原代码 `catch (e: XxxException)` 里做了什么的，Python 侧照做。

---

## 5. 平台 API 映射

| Android / Kotlin | Python |
|---|---|
| `android.util.Log.d/w/e/i` | `logging.getLogger(__name__).debug/warning/error/info` |
| `SharedPreferences` | `openminis.core.prefs.Prefs`（JSON 文件持久化） |
| `EncryptedSharedPreferences` | `openminis.core.prefs.SecretStore`（keyring + 加密文件） |
| Room `@Entity` | SQLAlchemy `DeclarativeBase` 子类 |
| Room `@Dao` | `openminis.data.db.base` 下的 Repository 类 |
| Room `@Database` | `openminis.data.db.database.Database`（SQLAlchemy engine/session） |
| Room `@Query("SELECT ...")` | SQLAlchemy `select()`，`# PORT:` 注释里保留原 SQL |
| `Context` / `ContextCompat` | `openminis.core.context.AppContext`（应用级资源与路径） |
| `Handler(Looper.getMainLooper())` | `anyio` 事件循环调度 |
| `JSONObject` / `JSONArray` | `dict` / `list`，用 `json` 模块 |
| `OkHttp` / `Request` | `httpx.AsyncClient` |
| `WebView` | 前端 WebView 组件 / TUI 侧无对应 → 标注 |
| `AccessibilityService` | `openminis.accessibility.*`（Python 侧为接口占位 + 平台适配层） |
| `NotificationManager` | `openminis.notification.*`（TUI 用 rich 提示，Web 用 toast 事件） |
| `WorkManager` | `openminis.scheduled.*`（APScheduler 风格调度器） |
| `Intent` / `Bundle` | dataclass + `openminis.core.intent` |
| `Uri` | `str` + `urllib.parse` |
| `File(path)` | `pathlib.Path` |
| `Base64.encodeToString` | `base64.b64encode(...).decode()` |
| `MessageDigest`（SHA-256） | `hashlib.sha256` |
| `javax.crypto` / AES-GCM | `cryptography.hazmat.primitives.ciphers.aead.AESGCM` |
| `Locale` / `getString()` | `openminis.i18n`（gettext 风格 + JSON 资源） |
| `Bitmap` / `Canvas` | Pillow（`PIL.Image`），或标注为前端职责 |

---

## 6. UI 层映射（重要）

`ui/` 目录 9.4 万行，是最大的一块。Compose 的 `@Composable` 没有 Python 对应物，
按下面三层拆分，**不要机械翻译成死代码**：

### 6.1 ViewModel / 状态逻辑 → Python（内核层）
`ui/<area>/XxxViewModel.kt` → `src/openminis/ui/<area>/xxx_viewmodel.py`

- 保留全部状态字段与业务方法
- `MutableStateFlow<UiState>` → `StateFlow`
- `viewModelScope.launch` → 内部 `anyio` task group
- 这一层被 **TUI 和 FastAPI 共用**，是真正的可运行代码

### 6.2 纯展示 Composable → Web 前端（不生成 Python）
`ui/<area>/XxxScreen.kt` 里只负责排版的部分 → `web/src/...` 的 React 组件

- 在 Python 侧对应位置留一个 `# PORT: 展示层见 web/src/components/...` 注释
- 同时在 `PORTING_MAP.md` 里登记映射关系

### 6.3 TUI → Textual
每个主界面在 `src/openminis/tui/screens/` 下有一个 Textual `Screen`，
消费 6.1 的 ViewModel。

### 判定口诀
> 有状态、有逻辑、有网络/数据库调用 → Python
> 只有 `Column {}` / `Text()` / `Modifier.padding()` → 前端

---

## 7. 文件头模板

每个 Python 文件必须以这个头开始：

```python
"""Config value union.

Ported from: src/android/app/src/main/java/com/openminis/app/config/ConfigValue.kt
Original package: com.openminis.app.config
"""

from __future__ import annotations
```

- 第二行写**相对 `src/android` 的原始路径**，方便对照。
- 类/函数上的 docstring 优先复用原 Kotlin 的 KDoc。

---

## 8. 禁止事项

- ❌ 不要用 `# type: ignore` 铺路（个别第三方缺桩除外，需 `# PORT:` 说明）
- ❌ 不要引入原项目没有的新抽象层
- ❌ 不要把 `async` 函数改同步
- ❌ 不要删除 `[T-xxx]` 注释
- ❌ 不要在翻译时"顺手重构"
- ❌ 不要写 `pass` 占位了事——没翻译完就在文件头标 `# PORT-STATUS: partial`
- ✅ 允许：为 Python 语义清晰而做的等价改写（并在 `# PORT:` 里说明）

---

## 9. 完成标准

每个模块翻译完必须：
1. `python -c "import openminis.<module>"` 通过
2. 语法与导入零错误
3. 在 `PORTING_MAP.md` 登记：原始路径 → Python 路径 → 状态（done / partial / n-a）
