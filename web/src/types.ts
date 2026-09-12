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
export type ClientFrame =
  | { type: 'ping' }
  | { type: 'chat'; text: string; sessionId?: string }
  | { type: 'shell'; command: string }

export type ServerFrame =
  | { type: 'pong' }
  | { type: 'delta'; text: string }
  | { type: 'done'; exitCode?: number; sessionId?: string }
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
    }
  | {
      type: 'usage'
      inputTokens: number
      outputTokens: number
      cacheCreation?: number | null
      cacheRead?: number | null
    }
  | { type: 'error'; error: string }

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
  size: number
  mtime: number
  preview: string
}

export interface MemoryList {
  dir: string
  files: MemoryFileInfo[]
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
  path: string
  /** In the main agent's skill scope (可被调用). */
  active: boolean
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

export interface KnowledgeItem {
  id: string
  kind: 'skill' | 'memory' | 'doc'
  kindLabel: string
  title: string
  source: string
  modified: number
  preview: string
}

export interface KnowledgeList {
  query: string
  count: number
  items: KnowledgeItem[]
  sources: Record<string, number>
}

export interface KnowledgeContent {
  kind: string
  name: string
  title: string
  source: string
  modified: number
  content: string
}

/** Result of a memory-organize pass (daily logs → RULES.md + wiki/*). */
export interface OrganizeResponse {
  ok: boolean
  applied: boolean
  message: string
  logsRead: number
  rules: number
  wiki: string[]
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
