# trace — 「整理知识 → 反复 memory_get 空转」运行轨迹

日期：2026-09-08（上午，OpenMinis Python 移植版，Web UI 会话）

## 现象

主 agent 收到"整理知识 / 生成 wiki"一类指令后，进入**纯检索空转**：

- 反复调用 `memory_get`，且每次换关键词 / 换 scope（`中文` → `uv` → `项目约定` …），
- 全程没有落到任何一次 `memory_write(scope=wiki)` 的**完成动作**，
- 任务无法自行收尾，直到顶穿 `max_turns` 才被通用提示截断。

## 触发时配置快照

- 身份：assistant / coder（启用工具含 `memory_get`、`memory_write`、file/shell 等）
- 相关技能：`wiki-distill`（整理知识入口），旧版指引只写"检索现状 → 蒸馏 → 写入"，未限检索次数、未给完成约束
- 引擎没有把 `POST /api/system/memory/organize`（整理器）暴露成 agent 工具
- 服务日志不记录工具调用，事后无法还原逐字轨迹

## 复现轨迹（典型序列，非逐字日志）

```
用户: 整理一下我的知识
1. memory_get(scope=all, keywords='项目')     → 少量命中
2. memory_get(scope=all, keywords='python')   → …
3. memory_get(scope=daily)                    → …
4. memory_get(scope=all, keywords='约定')     → …
5. memory_get(scope=all, keywords='')         → 全量（终于）
6..N memory_get(不同关键词/scope 继续换)       → 依旧没有 memory_write
… 顶穿 max_turns → "[stopped: reached the maximum number of tool rounds]"
```

关键点：**每次调用参数都不同 → 原有 loop-detector 的"相同参数重复"规则永远不触发。**

## 根因分析

| # | 根因 | 说明 |
|---|------|------|
| 1 | 检测器盲区 | `ToolLoopDetector` 只对相同 `args_hash`（同工具+同参）计数；"整理"场景每次换关键词，哈希全部不同，rules 2-4 全部失效 |
| 2 | 技能指引无纪律 | `wiki-distill` 没有规定检索次数上限；`memory_get` 本可一次 `keywords=''` 全量取回，旧指引却教模型"搜关键词"，诱导逐词检索 |
| 3 | 无完成约束 | 任务正确收尾 = 至少一次 `memory_write(scope=wiki)`；但没有任何机制约束或提醒模型"该写了" |
| 4 | 无运行轨迹日志 | 服务端不记录工具调用 → 此类问题只能靠症状猜测，无法复现分析 |

## 修复（commit 待落）

1. **技能层（对症，立即生效）**：`wiki-distill/SKILL.md` 新增「检索纪律」——
   - 每个整理任务 `memory_get` **最多 2 次**：第 1 次 `scope=all` 且 **keywords 留空**（全量），第 2 次才允许带关键词定位；
   - 素材不足就停止检索：直接基于已有内容写 wiki，或向用户说明缺素材；
   - 完成标志 = **至少一次成功的 `memory_write(scope=wiki)`**，写完即收尾总结。
   - 已同步更新仓库内置包与 `C:\Users\loo\openminis\skills\wiki-distill` 已安装副本。
2. **检测器层（兜底）**：`ToolLoopDetector` 新增 `query_tool_runaway` 规则——
   - query 类工具（`memory_get` / `web_search` / `web_fetch`）**连续调用**（中间无其它工具）≥ 5 次告警、≥ 10 次 CRITICAL 阻断，**不要求参数相同**；
   - 中间穿插其它工具即重置计数；只作用于 query 白名单，不影响 coder 连续读文件的正常流程。
3. **可观测性**：`agent_runtime` 每次工具执行后记一行 `tool_call session=… name=… ok=… chars=…`（info 级）→ 之后可在 `tmp/server.log` 还原真实运行轨迹。

## 验证

- 新增 `tests/test_loop_query_guard.py` 4 例：换关键词连打 10 次→CRITICAL；≥5 次→WARNING；穿插 memory_write 重置；shell_execute 不受影响。全绿。
- 全量 pytest 通过。
- 8765 后端已重启（代码层改动生效；技能内容从磁盘读取，即时生效）。

## 后续可选（本次未做）

- 把整理器暴露为 agent 工具 `memory_organize`（复用 `POST /api/system/memory/organize`）需要把当前 LLM provider 注入 tool executor，改动面较大，暂缓；
- 会话级"连续同工具无进展"的通用检测可继续收紧（当前仅 query 白名单，避免误伤）。

---

# trace 2 — 「新闻抓完不总结，跑满 max_turns」运行轨迹

日期：2026-09-12（OpenMinis Python 移植版，Web UI 会话）

## 现象

主 agent 执行"抓取新闻并总结"一类任务时，新闻素材已抓够，但循环**不退出**：

- 40 轮全是 `web_fetch` / `browser_use` 交替调用（`tool_call … ok=True`），
- 全程**没有任何一次向用户输出文本**，
- 直到顶穿 `max_turns=40` 才被 `[stopped: reached the maximum number of tool rounds]` 截断，**没有总结**。

## 根因

| # | 根因 | 说明 |
|---|------|------|
| 1 | `browser_use` 不在检索白名单 | `ToolLoopConfig.query_tools` 只有 `memory_get/web_search/web_fetch`，`browser_use` 漏网，runaway 规则根本不接管它 |
| 2 | 连续计数按"严格同名" | `_consecutive_same_tool_streak` 要求 tool_name 完全相等。新闻场景 `web_fetch` 抓列表 → `browser_use` 开文章 → `web_fetch` 再抓，**互相打断**，单工具最长连续仅 4 次，永远够不到 `query_critical_threshold=10` |
| 3 | 循环无"空转收尾"出口 | ReAct 循环唯一的自然出口是"模型不再调用工具"。模型陷入"只调工具、从不输出文本"的惯性时出口永远到不了 → 只有 max_turns 硬顶 |

## 修复

1. **同类合并（检测器层）**：`query_tools` 纳入 `browser_use`；`_consecutive_same_tool_streak`
   改为**按检索/浏览家族合并计数**——`web_fetch`/`browser_use`/`web_search`/`memory_get`
   互相接力不再重置 streak；非检索工具（shell/file 等）仍重置。交替抓取同样累计到 CRITICAL → 触发既有 blocked→wrap-up 硬停。
2. **空转收尾护栏（运行时层，核心）**：`AgentRuntime` 新增 `wrap_up_rounds` 选项与
   `stalled_rounds` 计数——连续 N 轮"只有工具、无文本产出"后，注入 `[auto wrap-up]` 指令并
   发起一次 **tools=None** 的收尾调用，把模型文本作为最终答案，`stop_reason="auto_wrap_up"`。
   默认阈值 `max(6, min(12, max_turns))`（40 轮预算 → 12 轮即收尾）。**任务完成却空转时主动跳出循环并总结**，不再顶穿 max_turns。

## 验证

- 新增 `tests/test_loop_query_guard.py` 3 例：browser_use 独立连打 10 次 → CRITICAL；web_fetch/browser_use 交替 10 次 → CRITICAL；非检索工具穿插 → streak 重置。
- 新增 `tests/test_agent.py::test_runtime_auto_wrap_up_when_never_answering`：纯工具无文本连打 → `stop=="auto_wrap_up"` 且提前收尾。
- 全量 **306 passed**。
