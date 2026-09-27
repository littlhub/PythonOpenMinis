# 外部 agent 协议（External Agent Protocol）

**一句话**：往 `~/openminis/agents/` 放一份 YAML 或 JSON，就接进来一个新的外部
agent（Codex、Claude Code、自研 CLI、远端服务…）——不用改应用代码。它会自动出现在
子代理列表里，被委派时按这份文件调用，回传的信息回到对话里。

用户描述符目录：默认 `~/openminis/agents/`，可用环境变量 `OPENMINIS_AGENTS_DIR`
覆盖（部署与测试用）。**同名 id 以用户目录为准**（覆盖内置项）。

## 0. 槽位一览

随包分发的 `descriptors/` 里就是全部槽位：

| 文件 | id | 状态 | transport | 说明 |
| --- | --- | --- | --- | --- |
| `zcode.yaml` | `zcode` | ✅ 启用 | `internal` | ZCode 移植内核，`builtin: true`（不可删除、可改配置） |
| `codex.yaml` | `codex` | ⏸ 预留 | `stdio` | Codex CLI 示例；装好 `codex` 后把 `enabled` 改 true |
| `deepseek-harness.yaml` | `deepseek-harness` | ⏸ 预留 | `stdio` | DeepSeek Harness；填好 `stdio.command` 与 `output` 后启用 |
| `workbuddy.yaml` | `workbuddy` | ⏸ 预留 | `stdio` | WorkBuddy 侧执行能力；同上（另附 http / internal 两种备选） |
| `doubao.yaml` | `doubao` | ⏸ 预留 | `stdio` | 豆包（Doubao）；同上（附火山方舟 HTTP 备选） |
| `custom.yaml` | `custom-agent` | 📄 模板 | `stdio` | **自定义槽位**空白模板；照抄填完即用 |

预留槽位默认 `enabled: false`：它们**不会**出现在子代理列表里、**不可**被委派，
但可以在输出里看到（含描述符路径），文件本身带填法注释。
启用只需三步：改命令 → 改 `output` 取值路径 → `enabled: true`。

不想动随包文件就复制一份到 `~/openminis/agents/` 再改 —— 同名 id 以用户目录为准。

---

## 1. 最小可用示例

### 1.1 `stdio` —— 起一个 CLI 子进程

```yaml
version: 1

id: codex                     # 必填。小写字母/数字开头，可含 a-z0-9_-，≤40 字符
name: Codex                   # 显示名
emoji: 🌀
description: 把编程任务交给 Codex 的独立执行环境
persona: 你是 Codex 编程代理。  # 追加在它自己系统提示之后的补充说明
builtin: false                # true = 随应用分发，不可删除（可改配置）
enabled: true                 # false = 不进子代理列表、不可委派

transport: stdio              # internal | stdio | http

stdio:
  command: ["codex", "exec", "--json", "--skip-git-repo-check"]
  input:
    mode: argv-template       # argv-template | stdin-text | stdin-json | none
    args: ["{{task}}"]
  env:
    CODEX_QUIET: "1"
  cwd: workspace              # workspace（默认，= 会话工作区）| 具体路径
  timeoutSeconds: 900

output:
  format: json-lines          # text | json | json-lines
  textPath: $.msg.content     # 从回传里取「结论」的路径
  errorPath: $.error          # 从回传里取「错误」的路径（取到非空即判失败）

tools:                        # 它自己声明的能力面（只用于展示与配置）
  - id: codex_exec
    name: Exec
    description: 在 Codex 沙箱里执行一段编程任务

skills:
  roots: ["~/.codex/skills"]  # 它自己的技能根

borrow:                       # 与主智能体互借（见第 4 节）
  tools: false
  skills: false
```

### 1.2 `internal` —— 进程内 Python 实现

```yaml
version: 1
id: my-plugin
name: 自研 agent
transport: internal
entry: "my_package.my_agent:handle"   # <module>:<callable>
```

入口函数的统一契约（返回 `str` 或 `ExternalAgentResult`）：

```python
async def handle(task: str, *, session_id: str, workspace: str,
                 config: dict, provider=None) -> str:
    return f"收到 {task}"
```

### 1.3 `http` —— 远端服务

```yaml
version: 1
id: remote-agent
name: 远端 agent
transport: http
http:
  endpoint: "https://example.com/agent/run"
  method: POST
  headers:
    authorization: "Bearer {{token}}"
output:
  format: json
  textPath: $.data.answer
```

请求体固定为：

```json
{"task": "...", "sessionId": "...", "workspace": "...", "config": { ... }}
```

---

## 2. 字段全集

### 2.1 顶层

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `version` | int | `1` | 协议版本（当前只读，用于向后兼容判断） |
| `id` | str | **必填** | 唯一 id，同时是子代理 id |
| `name` | str | `= id` | 显示名 |
| `emoji` | str | `🔌` | 卡片图标（取前 4 字符） |
| `description` | str | `""` | 一句话职责 |
| `persona` | str | `""` | 追加在**它自己**系统提示之后的补充说明 |
| `builtin` | bool | `false` | `true` = 随应用分发，**不可删除**（可改配置） |
| `enabled` | bool | `true` | `false` = 不进列表、不可委派（但仍在注册表里可见） |
| `transport` | str | **必填** | `internal` \| `stdio` \| `http` |
| `entry` | str | `""` | `transport: internal` 必填，`<module>:<callable>` |
| `command` | list | `[]` | `transport: stdio` 必填，等价于 `stdio.command` |
| `endpoint` | str | `""` | `transport: http` 必填，等价于 `http.endpoint` |
| `tools` | list | `[]` | 能力面声明（见 2.5） |
| `skills` | map | `{}` | `{roots: [...]}`，见 2.6 |
| `prompt` | map | `{}` | `{system: "...", systemFile: "..."}`，见 2.7 |
| `borrow` | map | `{tools: true, skills: true}` | 与主智能体互借，见第 4 节 |
| `output` | map | 见 2.4 | 回传解析 |

> **多写的字段不会报错**，只是被忽略——方便你在描述符里放自己的注释性字段。

### 2.2 `stdio`

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `command` | list[str] | — | 命令 + 参数，支持模板变量 |
| `input.mode` | str | `stdin-json` | `argv-template` \| `stdin-text` \| `stdin-json` \| `none` |
| `input.args` | list[str] | `[]` | `argv-template` 时追加到命令后的参数（支持模板变量） |
| `env` | map | `{}` | 追加/覆盖的环境变量（在父进程环境之上） |
| `cwd` | str | `workspace` | `workspace` = 会话工作区；其它值按路径展开（支持 `~`） |
| `timeoutSeconds` | number | `600` | 超时后 kill 子进程，记为失败 |

`input.mode` 语义：

| 值 | 做什么 |
| --- | --- |
| `argv-template` | 把 `input.args` 逐个渲染后**追加到命令行** |
| `stdin-text` | 把任务原文写进 stdin |
| `stdin-json` | 把 `{"task", "sessionId", "workspace", "config"}` 写进 stdin |
| `none` | 不喂任何输入 |

### 2.3 `http`

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `endpoint` | str | — | 完整 URL，支持模板变量 |
| `method` | str | `POST` | HTTP 方法（自动大写） |
| `headers` | map | `{}` | 额外请求头，值支持模板变量 |

响应状态码非 2xx → 判为失败（错误里带状态码）。

### 2.4 `output`

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `format` | str | `text` | `text` \| `json` \| `json-lines` |
| `textPath` | str | — | 取「结论文本」的路径，优先于 `resultPath` |
| `resultPath` | str | — | 同上（`textPath` 的别名，取不到时回落到它） |
| `errorPath` | str | — | 取「错误」的路径；取到非空值即判失败 |

**路径语法**：`$.a.b[0].c`
——`$` 开头，`.` 取字段，`[n]` 取下标。字典按键取、对象按属性取，取不到返回空。

`format` 的解析规则：

- `text` —— 整个 stdout/响应体就是结论文本。
- `json` —— 整体 `json.loads`；不合法即判失败。
- `json-lines` —— **逐行**解析，跳过非 JSON 行（CLI 常把日志混进 stdout）；
  取不到 `textPath` 时**取最后一条记录**当结论（CLI 一般把最终结果放最后）。

没有 `textPath` 时的兜底顺序：`text` → `content` → `message` → `result` → `output`
→ `response` →（都不匹配则把整个对象按 JSON 缩进返回）。

### 2.5 `tools`

```yaml
tools:
  - id: codex_exec
    name: Exec
    description: 在 Codex 沙箱里执行一段编程任务
    input_schema:              # 可选，JSON Schema 形状
      type: object
      properties:
        task: {type: string}
      required: [task]
```

`tools` 是**能力面声明**，用于界面展示与子代理配置。真正能勾选的工具是
「它自带的工具 ∩ 本应用的工具目录」——声明了一个本应用不认识的 id（CLI agent 的
工具通常都在它自己进程里）不会变成可勾选项，只会出现在 `selfManagedTools` 里做展示。

它还有**目录形态**：像技能根一样，把工具清单放进一个目录就等于接入工具，不用把每个
工具手写进描述符（与「绘世导入模型」一个直觉）。

```yaml
tools:
  roots:
    - "<workspace>/.zcode/tools"    # 目录里放 *.json / *.yaml 清单即自动导入
    - "~/.my-agent/tools"
  declared:                          # 与目录导入并存：显式声明的写这里
    - id: my_exec
      name: Exec
      description: 执行一段任务
```

目录扫描规则（`external_agents/tool_roots.py`）：

- 根下的 `*.json` / `*.yaml` / `*.yml` —— 单文件即一个工具（`{id, name, description,
  input_schema}`），或一个索引文件（`tools: [ {id: ...}, ... ]` 顶层列表）；
- 根下的**子目录**里若有同名清单文件（`tool.json` / `index.yaml` / …），也算一个工具
  ——「一个工具一个目录」放附件时好用；
- 同 id 以**描述符显式声明**为准（目录里的同名项被忽略，不报错）；
- 读不懂的文件只记一条诊断（`external_agent_tool_root_errors()`），不影响其它文件。

`toolRoots` / `tool_roots` 顶层键是 `tools.roots` 的别名。

### 2.6 `skills`

```yaml
skills:
  roots:
    - "<workspace>/.zcode/skills"   # <workspace>/<root>/<cwd> 占位符
    - "~/.zcode/skills"             # 行首 ~ 展开为主目录
    - "relative/skills"             # 相对路径按 workspace 解析
```

每个根目录下：根自身含 `SKILL.md` 则根自身是一个技能，否则**只扫一层**子目录
（深度嵌套不作为技能）。同名技能先到先得（声明顺序靠后者优先）。

作用域按字面推断并如实标注：含 `<workspace>` 或相对路径 → `project`；
行首 `~` → `user`；其它绝对路径 → `system`。

### 2.7 `prompt`

```yaml
prompt:
  system: "直接内联的系统提示"        # 优先
  systemFile: "PROMPT.md"            # 相对描述符文件所在目录解析
```

`prompt.system` 只对 `transport: internal` 有意义（CLI/HTTP 的系统提示在对端）。
没写时 `persona` 就是全部附加说明。

---

## 3. 模板变量

`command` / `input.args` / `env.*` / `http.endpoint` / `http.headers.*` 里的
`{{var}}` 会被替换；**未知变量替换成空串**（不报错）。

| 变量 | 值 |
| --- | --- |
| `task` | 本次委派的任务原文 |
| `session_id` / `sessionId` | 会话 id |
| `workspace` / `cwd` | 会话工作区绝对路径 |

子代理配置里的**标量**字段（`model`、`maxRounds`…）也能当变量用；数组/对象会被
序列化成 JSON 字符串。

---

## 4. 与主智能体互借（`borrow`）

外部 agent 的 tool / mcp / skill / loop / prompt 都是**它自己那一套**，与主智能体
不同；同时两边可以**相互借用**：

```yaml
borrow:
  tools: true      # 默认 true
  skills: true     # 默认 true
```

| 方向 | 机制 |
| --- | --- |
| 主智能体 → 借外部 agent 的工具 | 外部 agent 的工具以 `zcode_*` 之类的 id 并入应用工具目录，主智能体/其它子代理可直接勾选使用 |
| 外部 agent → 借主智能体的工具 | `borrow.tools: true` 时，子代理配置里勾中的宿主工具（如 `file_read`）会被桥接进外部 agent 的循环：**沿用宿主原名**，执行落回宿主工具注册表（不复制实现） |
| 主智能体 → 看外部 agent 的技能 | `subagents.build_registry()` 并入外部 agent 的技能（`source: "external"`，带 `agentId`），同名**不合并**（两边可能是不同的正文） |
| 外部 agent → 借主智能体的技能 | `borrow.skills: true` 时把宿主技能库当作**最低优先级**的根，同名时外部 agent 自己的技能胜出；配置里勾了具体几个名字时**只借那几个** |

借来的宿主工具受**宿主自己的沙箱与权限规则**约束（越权路径该被拒就被拒），失败会
如实变成 `is_error` 的工具结果，不会静默成功。

`borrow.tools: false` 时宿主工具 id 在外部 agent 侧解析不到（模型看不到、调用即报错）。

> `subagent_delegate` **不外借**：它是「子代理再委派子代理」的递归入口。

### 借用是**纯增量**：只增不减

外部 agent 在配置界面上「和原助手一样可以添加工具和技能，只不过没法删除」——
添加 = 借主智能体的，加在**它自己那套**之上。落地规则：

| 项 | 它自带的（`ownTools` / `ownSkills`） | 借来的（`borrowedTools` / `borrowedSkills`） |
| --- | --- | --- |
| 来源 | 描述符 `tools` / `tools.roots` / `skills.roots` | 子代理配置里勾中的宿主工具 / 技能 |
| 界面 | **锁定项**（虚线 chip、带 🔒，点不动） | 可勾选 chip（带「借」标记） |
| 落库 | 不落库（每次从描述符合成） | 只落**净增量**：保存时自动减掉自带的那一份 |
| 可否移除 | 不能 —— 后端结构性保证（不是靠界面不给按钮） | 取消勾选即可 |

`SubagentInfo.tools` / `.skills` 是**并集**（自带 ∪ 借来的，即当前生效的那份）；
想知道哪一半是借的，看 `borrowedTools` / `borrowedSkills`。

技能借用还有一处细节：**一个都没勾 = 借整个宿主技能库**（保持默认行为），
一旦勾了具体名字就**只借那几个**。「借来的」只影响宿主技能库里发现的技能，
它自己技能根里的技能不受过滤。

---

## 5. 配置面：哪些能改、哪些不能

外部 agent 在界面上**可以配置但不可删除**。可改字段是白名单：

```
name, emoji, description, persona, providerId, providerType, model,
tools, skills, mcpServers, maxRounds
```

- 保存的是**覆盖层**（只存你改过的字段），没改的字段仍走描述符默认值 ——
  描述符升级（换默认人设、加工具）会自动惠及你没动过的字段。
- `tools` / `skills` 是**只增不减**的（见第 4 节）：界面回传的是「自带 + 借来的」
  并集，落库前自动减掉自带的那一份，所以它自己那套删不掉 —— 想改它自带的能力面，
  去改描述符（`tools` / `tools.roots` / `skills.roots`）。
- **不能改** `loop` / `prompt`：那是外部 agent 内核的一部分。改它们只会造出一个
  四不像（主智能体的配置面去改别人的循环与系统提示）。
- 后端自己合成出来的只读字段（`selfManagedTools` / `ownTools` / `ownSkills` /
  `borrowedTools` / `borrowedSkills` / `skillRoots` / `toolRoots` / `source` /
  `systemPrompt` / `borrowTools` / `borrowSkills`）出现在 PUT 载荷里会被**静默忽略**
  —— 界面的「编辑」就是把整条记录塞回表单再回传，不能因此报错。
- `providerId` **允许留空**：表示跟随当前激活的模型服务实例。
- 想彻底移除一个**用户自建**描述符 → 删掉 `~/openminis/agents/` 里那份文件。

---

## 6. 排障

- 坏文件**只跳过它自己**，其余照常加载；原因可通过
  `openminis.agent.external_agents.descriptor_load_errors()` 取到（设置页展示）。
- 想单文件校验：`load_descriptor_file(Path("xxx.yaml"))`，不合法抛
  `ExternalAgentError`。
- 调用失败一定是**一句可读的话**（`ExternalAgentResult.as_text()`）：
  找不到可执行文件 / 超时 / 退出码 N + stderr / 回传不是合法 JSON / HTTP 状态码 /
  对端自报错误（`errorPath`）。
- YAML 优先用 pyyaml。pyyaml 缺席时用一个**受控子集**的解析器兜底（嵌套映射、
  `- ` 列表、行内 `[a, b]` / `{k: v}`、引号字符串、布尔与数字、`#` 注释）；
  **不支持**多行标量 / 锚点 / 别名 —— 需要那些时请装 pyyaml，或改用 `.json`。
- 与 pyyaml 一致：`on` / `off` / `yes` / `no` 会被解析成布尔值。所以别用 `off`
  当 `id`。
