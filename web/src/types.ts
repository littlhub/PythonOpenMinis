/** Types mirroring the FastAPI payloads in openminis/server/main.py. */

export interface ConfigFieldInfo {
  path: string
  displayName: string
  description: string
  schema: string
  access: 'HIDDEN' | 'READONLY' | 'READWRITE'
  risk: 'NORMAL' | 'SENSITIVE' | 'DESTRUCTIVE'
  scope: string
  unavailableReason: string | null
  value: unknown
}

export interface ConfigUpdateResult {
  path: string
  oldValue: unknown
  newValue: unknown
}

export interface HealthInfo {
  status: string
  data_dir: string
  workspace: string
}

/** WebSocket frames — keep in sync with server/main.py. */
/** 群聊成员的身份标签（与 ChatView 的 GroupKind 一致；只是显示分组，不新增可执行成员）。 */
export type GroupMemberKind = 'agent' | 'bot' | 'human'

/** 随 chat 帧上报的群成员：``agent``/``bot`` 是子代理，``human`` 是真人席位。 */
export interface ChatParticipant {
  id: string
  kind: GroupMemberKind
  name?: string
}

export type ClientFrame =
  | { type: 'ping' }
  | {
      type: 'chat'
      text: string
      sessionId?: string
      /** 本会话的群成员（后端据此把子代理写进系统提示、把真人席位标成「人」）。 */
      participants?: ChatParticipant[]
      /** 「分配项目」：成员 id → 工作空间 id。 */
      memberProjects?: Record<string, string>
    }
  | { type: 'shell'; command: string }

export type ServerFrame =
  | { type: 'pong' }
  | { type: 'delta'; text: string }
  | { type: 'done'; exitCode?: number; sessionId?: string; stopped?: boolean }
  | { type: 'chatSession'; sessionId: string; title?: string }
  | {
      type: 'toolStart'
      id: string
      name: string
      input: Record<string, unknown>
    }
  | {
      type: 'toolEnd'
      id: string
      name: string
      ok: boolean
      output: string
      /** 耗时（毫秒）。工具卡上显示「跑了多久」，长任务一眼看出慢在哪。 */
      ms?: number
      /** 本次调用新生成、可直接预览的图片（本地绝对路径）—— 生图自动预览用。 */
      images?: string[]
    }
  // ---------------------------------------------------------------------
  // 群聊可视化：子代理跑起来时后端把它的过程单独推过来（`id` = 外层那次
  // `subagent_delegate` 调用的 id，前端据此把子代理气泡归到对应的工具卡）。
  // ---------------------------------------------------------------------
  | {
      type: 'subagentStart'
      id: string
      subagentId: string
      name: string
      emoji: string
      model?: string
      task: string
      /** 这个成员被分配到的项目目录（绝对路径）；没分配时为空。 */
      project?: string
    }
  | { type: 'subagentDelta'; id: string; subagentId: string; text: string }
  | {
      type: 'subagentToolStart'
      id: string
      subagentId: string
      callId: string
      name: string
      input: Record<string, unknown>
    }
  | {
      type: 'subagentToolEnd'
      id: string
      subagentId: string
      callId: string
      name: string
      ok: boolean
      output: string
      /** 子代理本次调用新生成的图片（绝对路径）—— 与主代理同样自动预览。 */
      images?: string[]
    }
  | {
      type: 'subagentEnd'
      id: string
      subagentId: string
      ok: boolean
      text: string
      stopReason?: string
    }
  | {
      type: 'usage'
      inputTokens: number
      outputTokens: number
      cacheCreation?: number | null
      cacheRead?: number | null
    }
  | { type: 'error'; error: string; /** 控制台被密码锁住时说一声 */ locked?: boolean }
  | {
      /** 主模型限流/超时/5xx，已自动切到兜底模型 —— 界面挂一条提示。 */
      type: 'fallback'
      fromModel: string
      toModel: string
      reason: string
    }

/**
 * 多会话并行：所有流式帧都带上「它属于哪个会话」。
 *
 * 后端本来就是按会话 id 隔离运行的（`_RUNNING_CHATS`），但帧过去不带会话信息，
 * 前端只能一股脑 append 到「当前打开的那个会话」—— 于是 A 会话在跑时切到 B，
 * A 的流式内容就串进了 B。带上 sessionId 后，前端才能把每段输出路由回各自的
 * 消息列表，多个会话也就真正能同时跑了。
 */
export type SessionScopedFrame = ServerFrame & { sessionId?: string }

/**
 * 聊天页广播「哪些会话正在生成」用的窗口事件名（detail = 会话 id 数组）。
 * 侧边栏订阅它，在会话行上点一个「运行中」小圆点 —— 多会话并行时一眼看得出
 * 谁在跑、谁在闲。
 */
export const RUNNING_SESSIONS_EVENT = 'openminis:running-sessions'

// ---------------------------------------------------------------------------
// chat sessions & messages (设置无关的会话持久化)
// ---------------------------------------------------------------------------
export interface ChatSessionInfo {
  id: string
  title: string
  updatedAt: number
  lastMessage: string
  folderId?: string | null
}

export interface ChatMessageInfo {
  id: string
  role: 'user' | 'assistant'
  text: string
  createdAt: number
  /** [T-tool-cards-persist-and-fold] 这一回合调用过的工具（有序），随消息落库。
   *  界面据此把折叠工具卡画回来 —— 刷新、切会话、重启后端后依然在、能展开。
   *  它们**不进模型上下文**：喂给模型的那份只取消息正文，且更早的输出会被
   *  折成一行（设置 → 对话参数 → 工具输出进上下文）。 */
  runs?: ToolRunInfo[]
  /** [T-subagent-log-persist] 子代理的一段过程（有值才是子代理消息）。
   *  它的发言与工具调用原先只活在推流帧里，切窗口/刷新就没了；现在随消息
   *  落库，回放时重建出子代理气泡。同样**不进模型上下文**（正文不在 text 里）。 */
  sub?: SubTurnInfo | null
}

/** 落库的一段子代理过程（一次委派 = 一条）。 */
export interface SubTurnInfo {
  speaker?: {
    id?: string
    subagentId?: string
    name?: string
    emoji?: string
    project?: string
  }
  /** 主代理交给它的任务。 */
  task?: string
  /** 外层那次 `subagent_delegate` 调用的 id。 */
  roomId?: string
  text?: string
}

/** 落库的一条工具调用记录。字段与 chat 帧的 toolStart/toolEnd 对齐。 */
export interface ToolRunInfo {
  id: string
  name: string
  input?: Record<string, unknown>
  /** 成功与否。老数据可能没有。 */
  ok?: boolean
  output?: string
  /** 耗时（毫秒），落库时由后端算好。 */
  ms?: number
}

/** 群聊里的一个「发言人」（主代理 / 子代理 / 你）。 */
export interface Speaker {
  /** 唯一 id：`user` / `main` / 子代理实例 id。 */
  id: string
  name: string
  emoji: string
  /** `main` = 主代理；`sub` = 子代理；`user` = 用户。 */
  kind: 'user' | 'main' | 'sub'
  /** 该发言人被分配到的项目目录（绝对路径），没分配时不填。 */
  project?: string
}

// ---------------------------------------------------------------------------
// workspaces (工作空间 — 文件夹式会话分组)
// ---------------------------------------------------------------------------
export interface WorkspaceInfo {
  id: string
  name: string
  description: string
  updatedAt: number
  pinned: boolean
  sessionCount: number
  path?: string
}

/** Special id used to filter to ungrouped sessions ("unfiled"). */
export const UNFILED = 'unfiled'

// ---------------------------------------------------------------------------
// filesystem (右侧"项目文件"面板)
// ---------------------------------------------------------------------------
export interface FsNode {
  name: string
  path: string
  isDir: boolean
  size: number
  mtime: number
  children?: FsNode[]
  error?: string
}

export interface FsRoot {
  root: string
  name: string
  exists: boolean
}

export interface FsReadResult {
  path: string
  name: string
  content: string
  truncated: boolean
  size: number
  lines: number
}

/** Minimal entry for the right-panel "history" tab (cross-session). */
export interface HistoryEntry {
  sessionId: string
  sessionTitle: string
  messageId: string
  text: string
  createdAt: number
}

// ---------------------------------------------------------------------------
// settings (设置 → 模型服务 / 身份) — keep in sync with server/main.py
// ---------------------------------------------------------------------------
export interface ModelOption {
  id: string
  name: string
  /** 用途标签(多选,含自定义类型 id):llm=对话 / vision=识图 / image=生图 /
   *  model3d=3D / audio=音频 / video=视频 / audio_gen=生音频 / video_gen=生视频. */
  capabilities?: string[]
  /** 主标签(列表里的第一个),便于旧逻辑兼容展示. */
  capability?: string
  /** 标签的中文展示串,例如「对话+识图」. */
  capabilityLabels?: string
  /** auto=按模型名推断;user=用户在界面上手动改过. */
  capabilitySource?: 'auto' | 'user'
}

/** 模型用途标签(多选)—— 八类。 */
export type ModelCapability =
  | 'llm'
  | 'vision'
  | 'image'
  | 'model3d'
  | 'audio'
  | 'video'
  | 'audio_gen'
  | 'video_gen'

/** 用途槽位 id —— 与八类标签一一对应。 */
export type SlotId =
  | 'chat'
  | 'vision'
  | 'image'
  | 'model3d'
  | 'audio'
  | 'video'
  | 'audio_gen'
  | 'video_gen'

export interface CapabilityInfo {
  id: ModelCapability | string
  label: string
  /** '1' 表示这是用户自定义类型(不是八类内置). */
  custom?: string
}

/** 用途槽位:每种用途各绑一个 (实例, 模型). */
export interface ModelSlotInfo {
  slot: SlotId
  label: string
  /** 该槽位接受的能力(按优先顺序)。 */
  accepts: ModelCapability[]
  acceptsLabel: string
  configured: boolean
  instanceId: string | null
  instanceLabel: string
  model: string
  /** 当前绑定模型的能力标签;未配置时为 null. */
  capabilities?: string[]
  capability: string | null
  /** 当前绑定模型的能力是否落在这个槽位接受的范围内. */
  capabilityOk: boolean
  /** 从已配模型里自动挑出的最佳候选;没有合适的就是 null. */
  suggested: { instanceId: string; model: string } | null
}

/** 一个已配置的厂商实例。同 type 可有多条(例如两个 OpenAI 兼容网关)。 */
export interface ProviderInfo {
  id: string
  type: string
  typeLabel: string
  label: string
  engine: string | null // 'anthropic' | 'openai' when the engine is ported
  note: string
  hasKey: boolean
  baseUrl: string
  model: string
  defaultModel: string
  models: ModelOption[]
  /** {modelId: [标签…]} 用户对模型用途标签的手动覆盖(可含自定义类型 id). */
  modelTypes?: Record<string, string[]>
  /** 当前所选模型的标签(覆盖优先,否则按 id 推断). */
  modelCapabilities?: string[]
  /** 当前所选模型的主标签. */
  modelCapability?: string | null
  modelCapabilityLabels?: string
  modelCapabilitySource?: 'auto' | 'user' | null
  isActive: boolean
}

/** 厂商目录(可添加的类型),与已配置实例分开。 */
export interface ProviderTypeInfo {
  type: string
  label: string
  engine: string | null
  note: string
  defaultModel: string
}

export interface IdentityInfo {
  id: string
  name: string
  emoji: string
  description: string
  persona: string
  builtin: boolean
  recommendedTools: string[]
  enabledTools: string[]
}

export interface ToolInfo {
  id: string
  name: string
  description: string
  category?: string
  /** 插件工具才有：它来自哪个插件（category === 'Plugin'）。 */
  pluginId?: string
  pluginName?: string
}

export interface SettingsInfo {
  activeProviderId: string | null
  activeIdentityId: string
  providers: ProviderInfo[]
  providerTypes: ProviderTypeInfo[]
  identities: IdentityInfo[]
  toolCatalog: ToolInfo[]
  /** Agent 对话参数(模型设置):上下文预算/记忆轮次/工具步数/深度思考. */
  agent: AgentConfig
  /** 模型用途目录(八类 + 自定义类型),供选择按钮使用. */
  capabilities?: CapabilityInfo[]
  /** 用户自定义的模型用途类型. */
  customModelTypes?: CustomModelType[]
  /** 用途分槽:八种用途当前绑定的模型. */
  modelSlots?: ModelSlotInfo[]
}

/** 用户自定义的模型用途类型 —— 自带 JSON 配置与 URL。 */
export interface CustomModelType {
  id: string
  label: string
  type: string
  url: string
  /** 自由 JSON 配置(以字符串保存,便于原样回显). */
  json: string
}

/** Agent 对话运行参数 — 对应「模型设置」里的那几项。 */
export interface AgentConfig {
  maxContextTokens: number
  maxMemoryRounds: number
  maxToolSteps: number
  deepThinking: boolean
  /** 是否允许主 Agent 委派子代理助理(subagent_delegate)。 */
  subagentEnabled?: boolean
  /** 图片如何进上下文:path=只记路径(默认,省上下文) / inline=多模态直读. */
  imageContextMode?: 'path' | 'inline'
  /** 读图/送图前缩放的最大边长(px),用来压住图片的上下文开销. */
  imageMaxEdge?: number
  /** 看图走哪条路:false(默认)=read_image 走「识图」槽;true=委派识图子代理. */
  imageVisionSubagent?: boolean
  /** Agent 循环模式:react=增强版(同参重复立刻拦 + 空转自动收尾) / kt=KT 原版四策略. */
  loopMode?: 'react' | 'kt'
  /** 每多少条用户提问后把每日记忆蒸馏进长期记忆(0=关闭). */
  memoryOrganizeEvery?: number
  /** LLM 兜底模型链:主模型限流/超时/5xx 时按顺序自动切下一个. */
  fallbackModels?: { instance: string; model: string }[]
  /** 工具输出进上下文:最近 N 条保持完整,更早的折成一行(0=不折叠). */
  toolKeepRecent?: number
  /** 单条工具输出进上下文的字符上限(0=不截断). */
  toolOutputMaxChars?: number
}

export interface FetchModelsRequest {
  /** 厂商实例 id(优先);老版本传 type 也兼容。 */
  id?: string
  type: string
  baseUrl: string
  apiKey: string
}

export interface FetchModelsResponse {
  models: string[]
  source: string
}

/** User-created identity (role agent). */
export interface CustomIdentityDraft {
  id: string
  name: string
  emoji?: string
  description?: string
  persona: string
  enabledTools?: string[]
}

/** Full PUT payload — mirrors store.apply_full(). Every section is optional so
 * sub-pages (身份/人格, 模型服务, 灵魂) can save only what they changed. */
export interface SettingsPayload {
  activeProviderId?: string | null
  activeIdentityId?: string
  providers?: {
    id?: string
    type: string
    label?: string
    apiKey: string
    baseUrl: string
    model: string
    /** {modelId: [标签…]} — 用户对模型用途标签的手动覆盖(可含自定义 id). */
    modelTypes?: Record<string, string[]>
  }[]
  /** 用途槽位:{slot: {instanceId, model} | null}. 传 null 表示清空该槽. */
  modelSlots?: Partial<
    Record<SlotId, { instanceId: string; model: string } | null>
  >
  identityEdits?: { id: string; enabledTools: string[] }[]
  customIdentities?: CustomIdentityDraft[]
  /** 用户自定义的模型用途类型列表(整体替换). */
  customModelTypes?: CustomModelType[]
  /** Partial Agent 对话参数 — 只合并传入的键,其余保持默认. */
  agent?: Partial<AgentConfig>
}

// ---------------------------------------------------------------------------
// settings system helpers (设置 → 记忆/日志/存储/备份/关于)
// ---------------------------------------------------------------------------
export interface MemoryFileInfo {
  name: string
  /** 记忆分类 id：long_term / daily / rules / troubleshooting / preferences
   *  （另 soul / other 为特殊文件）。 */
  kind?: string
  kindLabel?: string
  size: number
  mtime: number
  preview: string
}

/** 五类记忆的定义（后端随列表下发，前端按它分栏排序）。 */
export interface MemoryKindInfo {
  id: string
  label: string
  path: string
  desc: string
}

export interface MemoryList {
  dir: string
  files: MemoryFileInfo[]
  kinds?: MemoryKindInfo[]
}

export interface MemoryDoc {
  name: string
  size: number
  content: string
}

export interface SystemLogs {
  path: string
  size: number
  updatedAt: number
  lines: string[]
  rotated: string[]
  note?: string
}

export interface StorageItem {
  name: string
  path: string
  bytes: number
  fileCount: number
}

// ---------------------------------------------------------------------------
// scheduled tasks (定时) — keep in sync with server/scheduled_api.py
// ---------------------------------------------------------------------------
/** 重复模式，与 Kotlin ScheduledRepeatMode 同名。 */
export type RepeatMode = 'ONCE' | 'DAILY' | 'WEEKDAYS' | 'CUSTOM'

export interface ScheduledRunInfo {
  firedAt: number
  sessionId?: string | null
  preview?: string | null
  ok: boolean
}

export interface ScheduledTaskInfo {
  id: string
  label: string
  hour: number
  minute: number
  repeatMode: RepeatMode
  /** Calendar.DAY_OF_WEEK: 1=周日 … 7=周六 */
  customDays: string
  prompt: string
  targetMode: string
  enabled: boolean
  createdAt: number
  startDateMs?: number | null
  endDateMs?: number | null
  lastFiredAt?: number | null
  lastResultPreview?: string | null
  lastResultSessionId?: string | null
  /** 服务端计算的“下次触发时间”（epoch ms）；禁用/无有效档期为 null。 */
  nextTriggerMs: number | null
  runCount: number
  /** 任务钉住的模型；null/缺省 = 跟随「当前模型」。 */
  modelId?: string | null
  /** 提供该模型的 provider 实例 id（同一模型可能挂在两个网关上）。 */
  modelBinding?: string | null
}

// ---------------------------------------------------------------------------
// Token 用量 (UsageStats)
// ---------------------------------------------------------------------------

/**
 * 归因可信度。四态必须互相区分——把它们合并成一个，正是当初那个
 * “整段历史被悄悄改记到当前模型名下”的 bug 能长期藏住的原因。
 */
export type Attribution =
  | 'MEASURED' // 逐条快照，就是实际服务的那个模型
  | 'ESTIMATED' // 快照列出现之前写入的旧行，按会话当前模型猜的
  | 'UNKNOWN_SESSION' // 孤儿行：既无快照、也无会话可猜
  | 'MEASURED_REMOVED' // 有快照，但该模型已从配置里删掉

export interface UsageModelStats {
  modelId: string
  displayName: string
  provider: string
  attribution: Attribution
  inputTokens: number
  outputTokens: number
  cacheCreationTokens: number
  cacheReadTokens: number
  /** input + cacheRead + cacheCreation，与 Android 的 ModelStats.totalInput 一致 */
  totalInput: number
  formattedInput: string
  formattedOutput: string
  sessions: number
  activeDays: number
}

export interface UsageProviderGroup {
  name: string
  models: UsageModelStats[]
}

export interface UsageGrandTotal {
  totalInput: number
  outputTokens: number
  cacheReadTokens: number
  cacheCreationTokens: number
  /** 无缓存读取时为 null，避免显示误导性的 0.0% */
  cacheHitRate: number | null
  formattedInput: string
  formattedOutput: string
  formattedCacheRead: string
  formattedCacheCreation: string
}

export interface UsageStats {
  grandTotal: UsageGrandTotal
  groups: UsageProviderGroup[]
  /** 一个 (模型, 归因态) 组合算一个 bucket */
  bucketCount: number
  error?: string
}

export interface ScheduledTaskDraft {
  id?: string
  label: string
  hour: number
  minute: number
  repeatMode: RepeatMode
  customDays?: number[]
  prompt: string
  targetMode?: string
  enabled?: boolean
  /** 钉住的模型 + 其 provider 实例；都为空 = 跟随当前模型。 */
  modelId?: string | null
  modelBinding?: string | null
}

export interface StorageInfo {
  root: string
  totalBytes: number
  items: StorageItem[]
}

export interface AppInfo {
  name: string
  version: string
  versionCode: number
  packageName: string
  python: string
  platform: string
  dataDir: string
  workspaceDir: string
  memoryDir: string
  cacheDir: string
  databasesDir: string
  logPath: string
  settingsFile: string
}

/** An installed skill bundle (a directory holding SKILL.md). */
export interface SkillInfo {
  name: string
  description: string
  /** 'builtin' = shipped with the app, 'user' = installed later. */
  source: string
  /** Generated bundles (builtin-tool manifest) can't be uninstalled. */
  generated: boolean
  scripts: string[]
  /** SKILL.md 里声明的环境变量名（`metadata.requires.env` 等）。 */
  env?: string[]
  path: string
  /** In the main agent's skill scope (可被调用). */
  active: boolean
}

/** 技能声明的环境变量 —— 哪个技能要它、现在配了没有。 */
export interface SkillEnvVar {
  name: string
  skills: string[]
  set: boolean
}

/** `/api/skills/env` 的返回：声明清单 + 当前值（与设置页的 envExtra 同一份）。 */
export interface SkillEnvInfo {
  required: SkillEnvVar[]
  values: Record<string, string>
  count: number
}

/** A builtin tool exposed to the agent loop. */
export interface SkillToolInfo {
  name: string
  description: string
  parameters: Record<string, string>
  required: string[]
}

export interface SkillsList {
  skills: SkillInfo[]
  tools: SkillToolInfo[]
  dir: string
  /** Skill names currently in the main agent's scope. */
  active: string[]
}

export interface SkillDetail extends SkillInfo {
  content: string
}

/** A curated marketplace source card (skill pack site or MCP directory). */
export interface MarketplaceSource {
  id: string
  /** 'skill' = 技能包市场，'mcp' = MCP 服务器目录 */
  kind: string
  name: string
  description: string
  url: string
}

export interface MarketplaceInstallResult {
  ok: boolean
  skill: { name: string; description: string }
  bytes: number
}

// -- 插件（通道 / 工具 / 桥接，含导入的第三方包）------------------------
/** `select` 字段的一个可选项（由后端按 optionsFrom 解析出来）。 */
export interface PluginOptionInfo {
  value: string
  label: string
  description?: string
  /** 分组标题：默认 / 主 agent 身份 / 子代理（助理） */
  group?: string
  kind?: string
}

/** 插件清单里的一个配置项 —— 界面按 type 渲染成对应的输入控件。 */
export interface PluginFieldInfo {
  key: string
  label: string
  /** text | password | number | switch | list | csv | select */
  type: string
  required: boolean
  secret: boolean
  default?: unknown
  help: string
  min?: number
  max?: number
  placeholder?: string
  /** select 专用：选项来源（如 agents）。界面只用来判断要不要渲染下拉。 */
  optionsFrom?: string
  /** select 专用：真实可选值。 */
  options?: PluginOptionInfo[]
}

/** 外部程序插件的启动段（env 已脱敏成 envKeys，避免密钥回传浏览器）。 */
export interface PluginProcessInfo {
  command?: string
  args?: string[]
  cwd?: string
  healthUrl?: string
  envKeys?: string[]
}

/** 插件给 agent 加的一个工具（声明式：调用时才起插件目录里的进程）。 */
export interface PluginToolInfo {
  id: string
  name: string
  description: string
  /** JSON Schema 的 properties：{参数名: {type, description, enum?}} */
  parameters: Record<string, { type?: string; description?: string; enum?: string[] }>
  required: string[]
  command: string[]
  cwd: string
  timeoutSec: number
  requiresConfig?: string[]
}

/** 一个插件的清单 + 运行状态（`/api/plugins` 的元素）。 */
export interface PluginStatus {
  id: string
  name: string
  icon: string
  version: string
  description: string
  category: string
  /** engine（引擎内驱动）/ process（外部程序）/ manual（无连接，可声明工具） */
  runtime: string
  driver: string
  builtin: boolean
  installed: boolean
  /** 内置插件的包内清单比数据目录那份新 —— 可以点「更新清单」刷新。 */
  outdated?: boolean
  /** 上次是不是启用状态 —— 引擎重启会自动把它拉起来。 */
  enabled: boolean
  running: boolean
  /** idle | starting | connected | reconnecting | error | stopped */
  state: string
  detail: string
  error: string
  account: string
  /** 外部程序插件的进程号 / 已运行秒数。 */
  pid: number
  uptimeSec: number
  since: number
  /** 当前配置（密钥字段回传空串，另带 `<key>__set` 标记是否已存过）。 */
  config: Record<string, unknown>
  /** 必填但还没填的字段 label。 */
  missing: string[]
  fields: PluginFieldInfo[]
  /** 这个插件给 agent 加的工具（启用后可在身份/子代理里勾选）。 */
  tools: PluginToolInfo[]
  toolCount: number
  process?: PluginProcessInfo
  maxMessageChars?: number
}

export interface PluginsList {
  plugins: PluginStatus[]
  dir: string
  /** 引擎内置的驱动（新建清单时 driver 只能从这些里选）。 */
  drivers: { id: string; label: string }[]
}

export interface PluginLogEntry {
  ts: number
  level: string
  text: string
}

export interface PluginLogs {
  id: string
  logs: PluginLogEntry[]
  now: number
}

export interface KnowledgeItem {
  id: string
  kind: 'skill' | 'memory' | 'knowledge' | 'doc'
  kindLabel: string
  title: string
  source: string
  modified: number
  preview: string
  /** 知识文档的五分类 id（仅 kind=knowledge） */
  category?: KnowledgeCategoryId
  categoryLabel?: string
}

/** 知识库五分类：概念 / 实体 / 来源 / 分析 / 模板（+ other 未归类）。 */
export type KnowledgeCategoryId =
  | 'concepts'
  | 'entities'
  | 'sources'
  | 'analysis'
  | 'templates'
  | 'other'

export interface KnowledgeKindInfo {
  id: KnowledgeCategoryId
  label: string
  dir: string
  desc: string
}

export interface KnowledgeList {
  query: string
  count: number
  items: KnowledgeItem[]
  sources: Record<string, number>
  /** 各知识分类的文档数 */
  categories?: Record<string, number>
  knowledgeKinds?: KnowledgeKindInfo[]
}

export interface KnowledgeContent {
  kind: string
  name: string
  title: string
  source: string
  modified: number
  content: string
  category?: KnowledgeCategoryId
  categoryLabel?: string
}

/** 知识图谱：节点 = 文档（按分类着色），连线 = 文档间 [[双链]]。 */export interface KnowledgeGraphNode {
  id: string
  label: string
  path: string
  kind: string
  category: KnowledgeCategoryId
  categoryLabel: string
}

export interface KnowledgeGraphLink {
  source: string
  target: string
}

export interface KnowledgeGraph {
  nodes: KnowledgeGraphNode[]
  links: KnowledgeGraphLink[]
  categories: KnowledgeKindInfo[]
}

/** Result of a memory-organize pass（每日记忆 → 四类长期记忆）。 */
export interface OrganizeResponse {
  ok: boolean
  applied: boolean
  message: string
  logsRead: number
  /** 各记忆分类写入的条数：{long_term, rules, troubleshooting, preferences} */
  kinds: Record<string, number>
}

// ---------------------------------------------------------------------------
// subagents (助理 — 主 agent 可委派的子代理)
// ---------------------------------------------------------------------------
export interface SubagentInfo {
  id: string
  name: string
  emoji: string
  description: string
  persona: string
  /** 厂商实例 id（模型服务里的一个实例）。 */
  providerId: string
  /** 协议类型；旧配置可能只有它，保存时会被解析成 providerId。 */
  providerType?: string
  model: string
  tools: string[]
  skills: string[]
  mcpServers: string[]
  maxRounds: number
}

/** A provider as seen by the subagent registry: may lack a key or engine. */
export interface SubagentProviderOption {
  id: string
  type: string
  label: string
  engine: string | null
  hasKey: boolean
  ready: boolean
  usable: boolean
  isActive: boolean
  model: string
}

export interface SubagentModelOption {
  providerType: string
  providerLabel: string
  id: string
  display: string
}

/** What a subagent is allowed to be built from (live snapshot). */
export interface SubagentRegistry {
  providers: SubagentProviderOption[]
  models: SubagentModelOption[]
  tools: ToolInfo[]
  skills: { name: string; description: string }[]
  mcpServers: string[]
  mcpNote: string
}

export interface SubagentsList {
  subagents: SubagentInfo[]
  registry: SubagentRegistry
}

/** Result of POST /subagents/plan (LLM-designed candidate). */
export interface SubagentPlanResult {
  saved: boolean
  subagent: SubagentInfo
}

// ---------------------------------------------------------------------------
// 沙箱守卫（异常删除 / 敏感信息拦截 + 控制台密码）
// ---------------------------------------------------------------------------

export type GuardFamily = 'escape' | 'delete' | 'secret'

export interface GuardEvent {
  id: string
  time: number
  family: GuardFamily
  familyLabel: string
  tool: string
  session_id: string
  /** 调用目录 —— 这条操作是在哪儿发起的 */
  cwd: string
  targets: string[]
  reasons: string[]
  command: string
  /** 异常输出：原始命令 / 报错 / 命中片段 */
  output: string
  status: 'blocked' | 'allowed'
  scope: string | null
}

export interface GuardEventList {
  events: GuardEvent[]
  counts: Record<string, number>
  blocked: number
  scopes: string[]
}

/** 放行范围：只放一次 / 本会话该目录 / 永久白名单。 */
export type GuardScope = 'once' | 'session' | 'always'

export interface ConsoleStatus {
  hasPassword: boolean
  locked: boolean
  expiresAt: number | null
  ttlSeconds: number
}

/** 实时检测流水：每条被扫描的命令一行（放行/拦截都显示，内存态）。 */
export interface GuardLiveItem {
  time: number
  command: string
  cwd: string
  family: '' | 'escape' | 'delete' | 'secret'
  reasons: string[]
  blocked: boolean
}
