# Porting map

`kotlin 源文件` → `python 文件` → 状态

状态：`done` 完成 · `partial` 部分 / 占位 · `n-a` Android 专有，Python 侧无对应（保留路径，不翻译）

## Python-only foundations（无 Kotlin 对应）

| Python | 说明 |
|---|---|
| `src/openminis/core/flow.py` | `StateFlow` / `SharedFlow`，替代 kotlinx.coroutines.flow |
| `src/openminis/core/result.py` | `Result<T>` / `runCatching` |
| `src/openminis/core/prefs.py` | SharedPreferences + EncryptedSharedPreferences |
| `src/openminis/core/context.py` | `android.content.Context` |
| `src/openminis/core/logging.py` | `android.util.Log` |
| `src/openminis/cli/` | Typer CLI（原生的 minis-config / MCP CLI 合流） |
| `src/openminis/tui/` | Textual 终端 UI（对应 Compose 导航） |
| `src/openminis/server/` | FastAPI 后端 |

## config（com.openminis.app.config）

| Kotlin | Python | 状态 |
|---|---|---|
| `ConfigValue.kt` | `config/config_value.py` | done |
| `ConfigError.kt` | `config/config_error.py` | done |
| `ConfigSchema.kt` | `config/config_schema.py` | done |
| `ConfigField.kt` | `config/config_field.py` | done |
| `ConfigCollection.kt` | `config/config_collection.py` | done |
| `ConfigRegistry.kt` | `config/config_registry.py` | done |
| `ConfigBuiltins.kt` | `config/config_builtins.py` | partial（已注册 appearance/agent/sandbox/browser/server，集合型 topic 待补） |
| `ConfigBridge.kt` | `config/config_bridge.py` | partial（CLI 层已覆盖读改查） |
| `MinisConfigPermissionStore.kt` | — | partial |
| `fields/PrefsFields.kt` | `config/config_builtins.py::PrefsBackedField` | partial |
| `fields/ClosureField.kt` | — | pending |
| `audit/ConfigAuditEntry.kt` | — | pending |
| `audit/ConfigAuditLog.kt` | — | pending |
| `confirm/ConfigConfirmationGate.kt` | — | pending |
| `confirm/PendingConfigChange.kt` | — | pending |
| `collections/EnvVarsCollection.kt` | — | pending |
| `collections/GroupsCollection.kt` | — | pending |
| `collections/ModelsCollection.kt` | — | pending |
| `collections/ProvidersCollection.kt` | — | pending |
| `collections/ThinkingRulesCollection.kt` | — | pending |

## data（com.openminis.app.data）

| Kotlin | Python | 状态 |
|---|---|---|
| `db/AppDatabase.kt` | `data/db/app_database.py` | done（12 条迁移全量照搬） |
| `db/ChatSessionEntity.kt` | `data/db/chat_session_entity.py` | done |
| `db/MessageEntity.kt` | `data/db/message_entity.py` | done |
| `db/CompactMarkerEntity.kt` | `data/db/compact_marker_entity.py` | done |
| `db/FolderEntity.kt` | `data/db/folder_entity.py` | done |
| `db/WebAppShortcutEntity.kt` | `data/db/web_app_shortcut_entity.py` | done |
| `db/ChatDao.kt` | `data/db/chat_dao.py` | done（含 SessionMetaRow / MessageSearchRow / SessionTailRow 投影） |
| `db/UsageRecord.kt` | `data/db/usage_record.py` | done |
| `db/ProviderConfigDao.kt` | — | pending |
| `db/ProviderDatabase.kt` | — | pending |
| `db/ProviderConfigMapping.kt` | — | pending |
| `db/DatabaseVersionGuard.kt` | — | pending |
| `BPETokenizer.kt` | — | pending |
| `ContextPolicy.kt` / `ContextOffload.kt` | — | pending |
| `EnvVarRedactor.kt` | — | pending |
| `FileMentionIndex.kt` | — | pending |
| `MountedFoldersStore.kt` | — | pending |
| `SessionForkManager.kt` | — | pending |
| `UpdateChecker.kt` / `PendingUpdateStore.kt` | — | pending |
| `AutoCompactPrefs.kt` / `FastModePrefs.kt` | — | pending |

## provider（com.openminis.app.provider）

| Kotlin | Python | 状态 |
|---|---|---|
| `data/model/*.kt`（provider 依赖子集） | `data/model/__init__.py` | partial（873 行：LLMMessage / LLMModel / LLMStreamChunk / AgentToolDefinition / LLMError / LLMUsage / ProviderConfig） |
| `LLMProvider.kt` | `provider/llm_provider.py` | partial |
| `ImageBudget.kt` | `provider/image_budget.py` | partial |
| `ToolJsonRepair.kt` | `provider/tool_json_repair.py` | partial |
| 其余（anthropic/openai/gemini/… 子包） | — | pending |

## sandbox（com.openminis.app.sandbox）

| Kotlin | Python | 状态 |
|---|---|---|
| `PtyBridge.kt` | `sandbox/pty_bridge.py` | partial |
| `NativeOffload.kt` | `sandbox/native_offload.py` | partial |
| `ShellTimeoutPolicy.kt` | `sandbox/shell_timeout_policy.py` | partial |
| `TerminalSanitizer.kt` | `sandbox/terminal_sanitizer.py` | done |
| `PersistentShell.kt` | `sandbox/persistent_shell.py` | **done**（真实子进程 shell：cd/export 持久、超时杀进程、marker 协议、死亡重启） |
| `ExecutionCoordinator.kt` | `sandbox/execution_coordinator.py` | **done**（会话级 shell 缓存 + 清洗 + 退出码标注） |
| `TerminalSession.kt` / `SeccompFallbackPolicy.kt` / `MountedFolderCoordinator.kt` / `PRootKernel` / `RootfsManager` 等 | — | pending |

## backup / config 子包

| Kotlin | Python | 状态 |
|---|---|---|
| `backup/BackupFormat.kt` | `backup/backup_format.py` | partial |
| `backup/BackupCrypto.kt` | `backup/backup_crypto.py` | partial |
| `config/audit/ConfigAuditLog.kt` | `config/audit/config_audit_log.py` | partial |
| `config/fields/PrefsFields.kt` | `config/fields/prefs_fields.py` | partial |

## tools（com.openminis.app.tools）

| Kotlin | Python | 状态 |
|---|---|---|
| `ToolExecutionResult.kt` | `tools/tool_execution_result.py` | done |
| `FileReadTool.kt` | `tools/file_read_tool.py` | done |
| `FileWriteTool.kt` / `FileEditTool.kt` / `ReadImageTool.kt` | — | pending |
| `AgentTools.kt` / `MemoryTools.kt` / `BrowserUseTool.kt` | — | pending |
| `VisionGroupResolver.kt` | — | pending |

## 其余模块（按目录，均未开始）

| 模块 | Kotlin 行数 | 状态 |
|---|---|---|
| `ui/` | 93,961 | pending（拆为 Python ViewModel + React 组件，见 PORTING.md §6） |
| `sandbox/` | 15,580 | pending |
| `provider/` | 10,870 | pending |
| `speech/` | 7,880 | pending |
| `debug/` | 5,780 | pending |
| `backup/` | 5,671 | pending |
| `browser/` | 4,193 | pending |
| `service/` | 3,159 | pending |
| `auth/` | 2,991 | pending |
| `offload/` | 2,342 | pending |
| `agent/` | 1,848 | **partial** — `ToolLoopDetector.kt` → `agent/tool_loop_detector.py`（done）；ChatViewModel 的 `runAgentLoop`/`executeTool` 内核 → `agent/agent_runtime.py`（done，UI 部分剥离）；`agent/shell/`（BashismDetector 等）待翻 |
| `tools/` | 1,250 | **partial** — `ToolExecutionResult.kt`/`FileReadTool.kt` done；`AgentTools.kt` 的 `shell_execute` → `tools/shell_execute_tool.py` done；FileWrite/FileEdit/ReadImage/Memory/BrowserUseTool 待翻 |
| `webapp/` | 1,292 | pending |
| `tools/` | 1,250 | pending |
| `diagnostics/` | 1,222 | pending |
| `share/` | 1,174 | pending |
| `crash/` | 1,038 | pending |
| `scheduled/` | 965 | pending |
| `accessibility/` | 897 | pending（Android 专有，多半 `n-a`） |
| `logging/` | 531 | pending |
| `mcp/` | 469 | pending |
| `shared/` | 376 | pending |
| `notification/` | 321 | pending |
| `deeplink/` | 314 | pending |
| `providers/` | 224 | pending |
| `network/` | 202 | pending |
| `power/` | 193 | pending |
| `terminal/` | 126 | pending |
| `i18n/` | 115 | pending |
| `util/` | 87 | pending |

合计 Kotlin 20.8 万行 / 617 文件。
