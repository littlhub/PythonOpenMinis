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
}

/** Agent 对话运行参数 — 对应「模型设置」里的那几项。 */
export interface AgentConfig {
  maxContextTokens: number
  maxMemoryRounds: number
  maxToolSteps: number
  deepThinking: boolean
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
  }[]
  identityEdits?: { id: string; enabledTools: string[] }[]
  customIdentities?: CustomIdentityDraft[]
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
