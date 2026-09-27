# ZCode 编程内核移植台账

`TypeScript 源` → `Python 文件` → `状态`

状态：`done` 完成 · `partial` 部分 / 占位（文件头有 `# PORT-STATUS: partial`）

本文件由 `gen_zcore_map.py` 从各模块文件头的 `Ported from:` 自动生成，请勿手工编辑表格；改完代码重跑该脚本即可。

边界见 `src/openminis/agent/zcode/ZCORE_PORTING.md` §0.0：**只移植编程部分（agent / tool 基础设施与编程工具 / permission / compact / context / memory / runtime），不移植办公部分（PDF、Office、cron/off-peak、动态工作流、浏览器自动化、Node REPL、图像视频产出类）。**

合计已落地模块：**194**（其中 partial 31）；范围内未移植 TS 文件：**204**。

## 范围内未移植（待办）

| TS 目录 | 未移植文件 |
|---|---|
| `agent` | `agent/compact-session.ts`、`agent/file-part-hydration.ts`、`agent/message-history-usage.ts`、`agent/read-file-state-hydrator.ts`、`agent/session-history-hydrator.ts`、`agent/tool-part-order.ts` |
| `capabilities` | `capabilities/index.ts` |
| `commands` | `commands/index.ts` |
| `config` | `config/index.ts` |
| `context` | `context/sections/workflow-actor.ts` |
| `environment.ts` | `environment.ts` |
| `events` | `events/event-reducer-helpers.ts`、`events/event-reducer.ts`、`events/in-memory-session-event-store.ts`、`events/index.ts`、`events/session-event-retention.ts`、`events/stream-recovery.events.ts` |
| `hooks` | `hooks/configured-runner-callback.ts`、`hooks/configured-runner-input.ts`、`hooks/configured-runner.ts`、`hooks/display-metadata.ts`、`hooks/output.ts`、`hooks/runner-helpers.ts`、`hooks/runner.ts`、`hooks/session-mailbox.ts`、`hooks/types.ts`、`hooks/workspace-hook-policy.ts`、`hooks/workspace-hook-review-flow.ts`、`hooks/workspace-hook-runtime-admission.ts`、`hooks/workspace-hook-telemetry.ts`、`hooks/workspace-hook-trust-coordinator.ts`、`hooks/workspace-hook-trust-domain.ts`、`hooks/workspace-hook-trust-evaluation.ts`、`hooks/workspace-hook-trust-records.ts`、`hooks/workspace-hook-trust-types.ts`、`hooks/workspace-hook-trust-store.ts`、`hooks/workspace-hook-trust.ts` |
| `interfaces` | `interfaces/coordinator-response.port.ts`、`interfaces/http-client.port.ts`、`interfaces/input-history.port.ts`、`interfaces/mcp.port.ts`、`interfaces/permission-full-access.ts`、`interfaces/runtime-input-presentation.ts`、`interfaces/session-mailbox.port.ts`、`interfaces/subagent.port.ts`、`interfaces/tool-artifact-store.port.ts`、`interfaces/workflow.port.ts` |
| `logging` | `logging/logger.ts` |
| `mcp` | `mcp/index.ts`、`mcp/name.ts` |
| `memory` | `memory/recall/index.ts` |
| `model` | `model/auxiliary-model-options.ts`、`model/content-protection.ts`、`model/image-media.ts`、`model/invocation-context.ts`、`model/media-policy.ts` |
| `network` | `network/public-egress-ip.ts` |
| `output.ts` | `output.ts` |
| `path` | `path/git-bash.ts`、`path/index.ts` |
| `permission` | `permission/workflow-draft-path.ts` |
| `plugin-reference` | `plugin-reference/catalog.ts`、`plugin-reference/index.ts`、`plugin-reference/references.ts`、`plugin-reference/reminder.ts` |
| `plugins` | `plugins/index.ts` |
| `rewind` | `rewind/index.ts` |
| `runtime` | `runtime/command-queue.ts`、`runtime/execution-state.ts`、`runtime/helpers/attachment-artifacts.ts`、`runtime/helpers/attachment-data-url.ts`、`runtime/helpers/attachment-image.ts`、`runtime/helpers/attachment-media-resolver.ts`、`runtime/helpers/attachment-path-reference.ts`、`runtime/helpers/attachment-pdf.ts`、`runtime/helpers/attachment-placeholder.ts`、`runtime/helpers/attachment-video.ts`、`runtime/helpers/attachments.ts`、`runtime/helpers/child-client-ports.ts`、`runtime/helpers/commands.ts`、`runtime/helpers/compact-media.ts`、`runtime/helpers/compact-post-reminders.ts`、`runtime/helpers/compact-preservation.ts`、`runtime/helpers/compact-selection.ts`、`runtime/helpers/compact.ts`、`runtime/helpers/context-usage-breakdown.ts`、`runtime/helpers/conversation.ts`、`runtime/helpers/data.ts`、`runtime/helpers/index.ts`、`runtime/helpers/media-attachment-path.ts`、`runtime/helpers/media-budget.ts`、`runtime/helpers/media-capability.ts`、`runtime/helpers/media-observability.ts`、`runtime/helpers/model-anomaly.ts`、`runtime/helpers/model-errors.ts`、`runtime/helpers/model-tool-call-validation.ts`、`runtime/helpers/official-cua-media.ts`、`runtime/helpers/permission-broker.ts`、`runtime/helpers/permission-grant-resume.ts`、`runtime/helpers/persisted-remote-session-path-repair.ts`、`runtime/helpers/project-memory-agent.ts`、`runtime/helpers/project-memory-extraction.ts`、`runtime/helpers/project-memory.ts`、`runtime/helpers/project.ts`、`runtime/helpers/provider-business-error.ts`、`runtime/helpers/provider-entry-origins.ts`、`runtime/helpers/provider-mid-conversation-system.ts`、`runtime/helpers/provider-request-messages.ts`、`runtime/helpers/rewind.ts`、`runtime/helpers/runtime-provider-request-messages.ts`、`runtime/helpers/runtime-reminders.ts`、`runtime/helpers/runtime-tools.ts`、`runtime/helpers/steering.ts`、`runtime/helpers/streaming-tool-ledger.ts`、`runtime/helpers/subagent-interaction-broker.ts`、`runtime/helpers/subagent-selection.ts`、`runtime/helpers/tool-allowlist.ts`、`runtime/helpers/tool-result-media-persistence.ts`、`runtime/helpers/tool-result.ts`、`runtime/helpers/turn-errors.ts`、`runtime/helpers/turn-file-changes.ts`、`runtime/model-selection.ts`、`runtime/permission-full-access.ts`、`runtime/permission-grant-recovery.ts`、`runtime/session-mode-port.ts` |
| `runtime-task` | `runtime-task/index.ts`、`runtime-task/notification-policy.ts`、`runtime-task/workflow-notification-copy.ts` |
| `runtime.ts` | `runtime.ts` |
| `session-context` | `session-context/parts.ts`、`session-context/read-session-context.ts`、`session-context/references.ts`、`session-context/utils.ts` |
| `subagent` | `subagent/borrowed-mcp-port.ts`、`subagent/completion-notification.ts`、`subagent/computer-use-policy.ts`、`subagent/context-builder.ts`、`subagent/coordinator-response.ts`、`subagent/explore-tools.ts`、`subagent/explore.ts`、`subagent/general-purpose.ts`、`subagent/index.ts`、`subagent/interaction-origin.ts`、`subagent/mcp-config.ts`、`subagent/message-steering.ts`、`subagent/persistent-memory-prompt.ts`、`subagent/persistent-memory.ts`、`subagent/profile-frontmatter.ts`、`subagent/profile-model-selection.ts`、`subagent/runner.ts`、`subagent/runtime-task-registry.ts`、`subagent/system-prompt.ts`、`subagent/tool-event-mirror.ts`、`subagent/tool-policy.ts` |
| `telemetry` | `telemetry/runtime-telemetry.ts`、`telemetry/agent-execution.ts`、`telemetry/index.ts` |
| `time` | `time/local-date.ts` |
| `tool` | `tool/executor.ts`、`tool/handlers/model-reference.ts`、`tool/handlers/node-repl-model-content.ts`、`tool/handlers/read-image.ts`、`tool/handlers/read-video.ts`、`tool/handlers/saved-workflows/args.ts`、`tool/handlers/saved-workflows/frontmatter.ts`、`tool/handlers/saved-workflows/index.ts`、`tool/handlers/saved-workflows/store.ts`、`tool/handlers/webfetch-content.ts`、`tool/handlers/webfetch-egress-guard.ts`、`tool/handlers/webfetch-errors.ts`、`tool/handlers/webfetch-network.ts`、`tool/handlers/webfetch-processing.ts`、`tool/handlers/webfetch-trace.ts`、`tool/handlers/webfetch-types.ts`、`tool/handlers/webfetch-url.ts`、`tool/handlers/websearch-results.ts`、`tool/handlers/websearch-support.ts`、`tool/index.ts`、`tool/provider-visible-order.ts` |
| `tools` | `tools/apply-patch.ts`、`tools/automation.ts`、`tools/create-workflow.ts`、`tools/edit.ts`、`tools/escalate.ts`、`tools/eval-workflow-snippet.ts`、`tools/get-workflow-run-roster.ts`、`tools/get-workflow-run.ts`、`tools/index.ts`、`tools/list-saved-workflows.ts`、`tools/list-workflow-runs.ts`、`tools/node-repl.ts`、`tools/off-peak.ts`、`tools/read-pdf.ts`、`tools/read-session-context.ts`、`tools/read-state.ts`、`tools/resolve-workflow-question.ts`、`tools/resume-workflow-run.ts`、`tools/save-workflow.ts`、`tools/target.ts`、`tools/tool-result-metadata.ts`、`tools/workflow-observation-display.ts`、`tools/workflow.ts`、`tools/write.ts` |
| `tracing` | `tracing/local-turn-preparation.ts` |

## agent

| TS 源 | Python | 状态 |
|---|---|---|
| `packages/core/src/agent/index.ts` | `openminis/agent/zcode/agent/__init__.py` | partial |
| `packages/core/src/agent/message-history.ts` | `openminis/agent/zcode/agent/message_history.py` | partial |
| `packages/core/src/agent/turn-machine.ts` | `openminis/agent/zcode/agent/turn_machine.py` | done |
| `packages/core/src/agent/turn-state.ts` | `openminis/agent/zcode/agent/turn_state.py` | done |

## compact

| TS 源 | Python | 状态 |
|---|---|---|
| `packages/core/src/compact/index.ts` | `openminis/agent/zcode/compact/__init__.py` | done |
| `packages/core/src/compact/manual.ts` | `openminis/agent/zcode/compact/manual.py` | done |
| `packages/core/src/compact/microcompact.ts` | `openminis/agent/zcode/compact/microcompact.py` | done |
| `packages/core/src/compact/policy.ts` | `openminis/agent/zcode/compact/policy.py` | done |
| `packages/core/src/compact/prompt.ts` | `openminis/agent/zcode/compact/prompt.py` | done |
| `packages/core/src/compact/rounds.ts` | `openminis/agent/zcode/compact/rounds.py` | done |

## context

| TS 源 | Python | 状态 |
|---|---|---|
| `packages/core/src/context/builder.ts` | `openminis/agent/zcode/context/builder.py` | partial |
| `packages/core/src/context/dynamic-sections.ts` | `openminis/agent/zcode/context/dynamic_sections.py` | done |
| `packages/core/src/context/index.ts` | `openminis/agent/zcode/context/__init__.py` | done |
| `packages/core/src/context/sections/cli-prefix.ts` | `openminis/agent/zcode/context/sections/cli_prefix.py` | done |
| `packages/core/src/context/sections/current-date.ts` | `openminis/agent/zcode/context/sections/current_date.py` | done |
| `packages/core/src/context/sections/desktop.ts` | `openminis/agent/zcode/context/sections/desktop.py` | done |
| `packages/core/src/context/sections/env-info.ts` | `openminis/agent/zcode/context/sections/env_info.py` | done |
| `packages/core/src/context/sections/identity.ts` | `openminis/agent/zcode/context/sections/identity.py` | done |
| `packages/core/src/context/sections/memory.ts` | `openminis/agent/zcode/context/sections/memory.py` | done |
| `packages/core/src/context/sections/request-user-context.ts` | `openminis/agent/zcode/context/sections/request_user_context.py` | partial |
| `packages/core/src/context/sections/skills.ts` | `openminis/agent/zcode/context/sections/skills.py` | done |
| `packages/core/src/context/types.ts` | `openminis/agent/zcode/context/types.py` | done |
| `packages/core/src/context/utils.ts` | `openminis/agent/zcode/context/utils.py` | done |

## contracts

| TS 源 | Python | 状态 |
|---|---|---|
| `packages/contracts/src/**` | `openminis/agent/zcode/contracts/__init__.py` | done |
| `packages/contracts/src/errors/index.ts` | `openminis/agent/zcode/contracts/errors.py` | done |
| `packages/contracts/src/events/session.events.ts` | `openminis/agent/zcode/contracts/events.py` | partial |
| `packages/contracts/src/hooks/index.ts` | `openminis/agent/zcode/contracts/hooks.py` | done |
| `packages/contracts/src/interfaces/execution.port.ts` | `openminis/agent/zcode/contracts/execution.py` | partial |
| `packages/contracts/src/interfaces/file-system.port.ts` | `openminis/agent/zcode/contracts/file_system.py` | done |
| `packages/contracts/src/interfaces/shared.ts` | `openminis/agent/zcode/contracts/ids.py` | done |
| `packages/contracts/src/model/index.ts` | `openminis/agent/zcode/contracts/model.py` | done |
| `packages/contracts/src/tools/contract.ts` | `openminis/agent/zcode/contracts/tools.py` | done |
| `packages/contracts/src/tracing/tracer.ts` | `openminis/agent/zcode/contracts/tracing.py` | done |

## embedded_search

| TS 源 | Python | 状态 |
|---|---|---|
| `packages/core/src/embedded-search/**` | `openminis/agent/zcode/embedded_search/__init__.py` | done |
| `packages/core/src/embedded-search/capability.ts` | `openminis/agent/zcode/embedded_search/capability.py` | done |
| `packages/core/src/embedded-search/shell.ts` | `openminis/agent/zcode/embedded_search/shell.py` | done |

## errors

| TS 源 | Python | 状态 |
|---|---|---|
| `packages/core/src/errors/` | `openminis/agent/zcode/errors/__init__.py` | done |
| `packages/core/src/errors/error-payload.ts` | `openminis/agent/zcode/errors/error_payload.py` | done |

## mcp

| TS 源 | Python | 状态 |
|---|---|---|
| `packages/core/src/mcp/**` | `openminis/agent/zcode/mcp/__init__.py` | partial |
| `packages/core/src/mcp/image-normalization.ts` | `openminis/agent/zcode/mcp/image_normalization.py` | partial |

## memory

| TS 源 | Python | 状态 |
|---|---|---|
| `packages/core/src/memory/*` | `openminis/agent/zcode/memory/__init__.py` | done |
| `packages/core/src/memory/directory.ts` | `openminis/agent/zcode/memory/directory.py` | done |
| `packages/core/src/memory/extraction.ts` | `openminis/agent/zcode/memory/extraction.py` | done |
| `packages/core/src/memory/index-content.ts` | `openminis/agent/zcode/memory/index_content.py` | done |
| `packages/core/src/memory/memory-file-path.ts` | `openminis/agent/zcode/memory/memory_file_path.py` | done |
| `packages/core/src/memory/origin-session.ts` | `openminis/agent/zcode/memory/origin_session.py` | done |
| `packages/core/src/memory/project-root.ts` | `openminis/agent/zcode/memory/project_root.py` | done |
| `packages/core/src/memory/recall/*` | `openminis/agent/zcode/memory/recall/__init__.py` | done |
| `packages/core/src/memory/recall/manifest.ts` | `openminis/agent/zcode/memory/recall/manifest.py` | done |
| `packages/core/src/memory/recall/types.ts` | `openminis/agent/zcode/memory/recall/types.py` | done |

## permission

| TS 源 | Python | 状态 |
|---|---|---|
| `packages/contracts/src/interfaces/permission.port.ts` | `openminis/agent/zcode/permission/ports.py` | done |
| `packages/core/src/permission/broker.ts` | `openminis/agent/zcode/permission/broker.py` | done |
| `packages/core/src/permission/index.ts` | `openminis/agent/zcode/permission/__init__.py` | done |
| `packages/core/src/permission/plan-mode-policy.ts` | `openminis/agent/zcode/permission/plan_mode_policy.py` | done |
| `packages/core/src/permission/rule-matching.ts` | `openminis/agent/zcode/permission/rule_matching.py` | done |
| `packages/core/src/permission/service.ts` | `openminis/agent/zcode/permission/service.py` | partial |

## runtime

| TS 源 | Python | 状态 |
|---|---|---|
| `packages/core/src/runtime/deps.ts` | `openminis/agent/zcode/runtime/deps.py` | done |
| `packages/core/src/runtime/internal-methods.ts` | `openminis/agent/zcode/runtime/methods/internal_methods.py` | done |
| `packages/core/src/runtime/internal.ts` | `openminis/agent/zcode/runtime/internal.py` | done |
| `packages/core/src/runtime/methods/config.ts` | `openminis/agent/zcode/runtime/methods/config.py` | done |
| `packages/core/src/runtime/methods/events.ts` | `openminis/agent/zcode/runtime/methods/events.py` | done |
| `packages/core/src/runtime/methods/tools.ts` | `openminis/agent/zcode/runtime/methods/tools.py` | done |
| `packages/core/src/runtime/types.ts` | `openminis/agent/zcode/runtime/types.py` | done |

## runtime_task

| TS 源 | Python | 状态 |
|---|---|---|
| `packages/core/src/runtime-task/notification.ts` | `openminis/agent/zcode/runtime_task/notification.py` | done |
| `packages/core/src/runtime-task/registry.ts` | `openminis/agent/zcode/runtime_task/__init__.py` | done |
| `packages/core/src/runtime-task/registry.ts` | `openminis/agent/zcode/runtime_task/registry.py` | done |

## system_reminder

| TS 源 | Python | 状态 |
|---|---|---|
| `packages/core/src/system-reminder/incoming-message.ts` | `openminis/agent/zcode/system_reminder/incoming_message.py` | done |
| `packages/core/src/system-reminder/prompt-attachment.ts` | `openminis/agent/zcode/system_reminder/prompt_attachment.py` | partial |
| `packages/core/src/system-reminder/source.ts` | `openminis/agent/zcode/system_reminder/source.py` | done |

## tool

| TS 源 | Python | 状态 |
|---|---|---|
| `packages/contracts/src/tools/*.ts` | `openminis/agent/zcode/tool/handlers/ports.py` | done |
| `packages/core/src/tool/**` | `openminis/agent/zcode/tool/__init__.py` | done |
| `packages/core/src/tool/bash-timeout-policy.ts` | `openminis/agent/zcode/tool/bash_timeout_policy.py` | done |
| `packages/core/src/tool/compat.ts` | `openminis/agent/zcode/tool/compat.py` | done |
| `packages/core/src/tool/diff.ts` | `openminis/agent/zcode/tool/diff.py` | done |
| `packages/core/src/tool/diff.ts` | `openminis/agent/zcode/tool/structured_patch.py` | done |
| `packages/core/src/tool/edit-matchers.ts` | `openminis/agent/zcode/tool/edit_matchers.py` | done |
| `packages/core/src/tool/executor/approval-gate.ts` | `openminis/agent/zcode/tool/executor/approval_gate.py` | done |
| `packages/core/src/tool/executor/background-task-output.ts` | `openminis/agent/zcode/tool/executor/background_task_output.py` | done |
| `packages/core/src/tool/executor/background-task-registry.ts` | `openminis/agent/zcode/tool/executor/background_task_registry.py` | done |
| `packages/core/src/tool/executor/background-tasks.ts` | `openminis/agent/zcode/tool/executor/background_tasks.py` | done |
| `packages/core/src/tool/executor/bash-result-display.ts` | `openminis/agent/zcode/tool/executor/bash_result_display.py` | done |
| `packages/core/src/tool/executor/batch-runner.ts` | `openminis/agent/zcode/tool/executor/batch_runner.py` | done |
| `packages/core/src/tool/executor/call-runner.ts` | `openminis/agent/zcode/tool/executor/call_runner.py` | done |
| `packages/core/src/tool/executor/create-workflow-display.ts` | `openminis/agent/zcode/tool/executor/create_workflow_display.py` | partial |
| `packages/core/src/tool/executor/display-text.ts` | `openminis/agent/zcode/tool/executor/display_text.py` | done |
| `packages/core/src/tool/executor/errors.ts` | `openminis/agent/zcode/tool/executor/errors.py` | partial |
| `packages/core/src/tool/executor/events.ts` | `openminis/agent/zcode/tool/executor/events.py` | done |
| `packages/core/src/tool/executor/hook-flow.ts` | `openminis/agent/zcode/tool/executor/hook_flow.py` | done |
| `packages/core/src/tool/executor/impl.ts` | `openminis/agent/zcode/tool/executor/impl.py` | done |
| `packages/core/src/tool/executor/memory-file-permission.ts` | `openminis/agent/zcode/tool/executor/memory_file_permission.py` | done |
| `packages/core/src/tool/executor/model-status-sink.ts` | `openminis/agent/zcode/tool/executor/model_status_sink.py` | done |
| `packages/core/src/tool/executor/permission-capability.ts` | `openminis/agent/zcode/tool/executor/permission_capability.py` | done |
| `packages/core/src/tool/executor/permission-flow.ts` | `openminis/agent/zcode/tool/executor/permission_flow.py` | done |
| `packages/core/src/tool/executor/permission-input-recheck.ts` | `openminis/agent/zcode/tool/executor/permission_input_recheck.py` | done |
| `packages/core/src/tool/executor/permission-responder-race.ts` | `openminis/agent/zcode/tool/executor/permission_responder_race.py` | partial |
| `packages/core/src/tool/executor/permission-rules-persistence.ts` | `openminis/agent/zcode/tool/executor/permission_rules_persistence.py` | done |
| `packages/core/src/tool/executor/permission-rules.ts` | `openminis/agent/zcode/tool/executor/permission_rules.py` | done |
| `packages/core/src/tool/executor/permission-suggestions.ts` | `openminis/agent/zcode/tool/executor/permission_suggestions.py` | done |
| `packages/core/src/tool/executor/result-content-projection.ts` | `openminis/agent/zcode/tool/executor/result_content_projection.py` | partial |
| `packages/core/src/tool/executor/result-display.ts` | `openminis/agent/zcode/tool/executor/result_display.py` | partial |
| `packages/core/src/tool/executor/result-serialization.ts` | `openminis/agent/zcode/tool/executor/result_serialization.py` | partial |
| `packages/core/src/tool/executor/telemetry.ts` | `openminis/agent/zcode/tool/executor/telemetry.py` | done |
| `packages/core/src/tool/executor/timeout.ts` | `openminis/agent/zcode/tool/executor/timeout.py` | done |
| `packages/core/src/tool/executor/turn-control.ts` | `openminis/agent/zcode/tool/executor/turn_control.py` | done |
| `packages/core/src/tool/executor/types.ts` | `openminis/agent/zcode/tool/executor/types.py` | done |
| `packages/core/src/tool/executor/utils.ts` | `openminis/agent/zcode/tool/executor/utils.py` | done |
| `packages/core/src/tool/executor/validation.ts` | `openminis/agent/zcode/tool/executor/validation.py` | partial |
| `packages/core/src/tool/executor/workflow-observation-display.ts` | `openminis/agent/zcode/tool/executor/workflow_observation_display.py` | partial |
| `packages/core/src/tool/executor/workflow-published-artifacts.ts` | `openminis/agent/zcode/tool/executor/workflow_published_artifacts.py` | done |
| `packages/core/src/tool/handlers/agent.ts` | `openminis/agent/zcode/tool/handlers/agent.py` | partial |
| `packages/core/src/tool/handlers/ask-user-question.ts` | `openminis/agent/zcode/tool/handlers/ask_user_question.py` | done |
| `packages/core/src/tool/handlers/bash-background-lifecycle.ts` | `openminis/agent/zcode/tool/handlers/bash_background_lifecycle.py` | done |
| `packages/core/src/tool/handlers/bash-background-policy.ts` | `openminis/agent/zcode/tool/handlers/bash_background_policy.py` | done |
| `packages/core/src/tool/handlers/bash-command-parser.ts` | `openminis/agent/zcode/tool/handlers/bash_command_parser.py` | partial |
| `packages/core/src/tool/handlers/bash-command-permission-policy.ts` | `openminis/agent/zcode/tool/handlers/bash_command_permission_policy.py` | done |
| `packages/core/src/tool/handlers/bash-command-rule-evaluator.ts` | `openminis/agent/zcode/tool/handlers/bash_command_rule_evaluator.py` | done |
| `packages/core/src/tool/handlers/bash-cwd-policy.ts` | `openminis/agent/zcode/tool/handlers/bash_cwd_policy.py` | done |
| `packages/core/src/tool/handlers/bash-gh-rate-limit.ts` | `openminis/agent/zcode/tool/handlers/bash_gh_rate_limit.py` | done |
| `packages/core/src/tool/handlers/bash-git-runtime-safety.ts` | `openminis/agent/zcode/tool/handlers/bash_git_runtime_safety.py` | done |
| `packages/core/src/tool/handlers/bash-metadata.ts` | `openminis/agent/zcode/tool/handlers/bash_metadata.py` | done |
| `packages/core/src/tool/handlers/bash-model-content.ts` | `openminis/agent/zcode/tool/handlers/bash_model_content.py` | partial |
| `packages/core/src/tool/handlers/bash-output.ts` | `openminis/agent/zcode/tool/handlers/bash_output.py` | partial |
| `packages/core/src/tool/handlers/bash-prompt.ts` | `openminis/agent/zcode/tool/handlers/bash_prompt.py` | done |
| `packages/core/src/tool/handlers/bash-read-file-sources.ts` | `openminis/agent/zcode/tool/handlers/bash_read_file_sources.py` | done |
| `packages/core/src/tool/handlers/bash-read-file-state.ts` | `openminis/agent/zcode/tool/handlers/bash_read_file_state.py` | done |
| `packages/core/src/tool/handlers/bash-readonly-policy-argv-direct.ts` | `openminis/agent/zcode/tool/handlers/bash_readonly_policy_argv_direct.py` | done |
| `packages/core/src/tool/handlers/bash-readonly-policy-argv-flags.ts` | `openminis/agent/zcode/tool/handlers/bash_readonly_policy_argv_flags.py` | done |
| `packages/core/src/tool/handlers/bash-readonly-policy-argv-git.ts` | `openminis/agent/zcode/tool/handlers/bash_readonly_policy_argv_git.py` | done |
| `packages/core/src/tool/handlers/bash-readonly-policy-argv-io.ts` | `openminis/agent/zcode/tool/handlers/bash_readonly_policy_argv_io.py` | done |
| `packages/core/src/tool/handlers/bash-readonly-policy-argv.ts` | `openminis/agent/zcode/tool/handlers/bash_readonly_policy_argv.py` | done |
| `packages/core/src/tool/handlers/bash-readonly-policy-callbacks.ts` | `openminis/agent/zcode/tool/handlers/bash_readonly_policy_callbacks.py` | done |
| `packages/core/src/tool/handlers/bash-readonly-policy-commands.ts` | `openminis/agent/zcode/tool/handlers/bash_readonly_policy_commands.py` | done |
| `packages/core/src/tool/handlers/bash-readonly-policy-flags-file.ts` | `openminis/agent/zcode/tool/handlers/bash_readonly_policy_flags_file.py` | done |
| `packages/core/src/tool/handlers/bash-readonly-policy-flags-git.ts` | `openminis/agent/zcode/tool/handlers/bash_readonly_policy_flags_git.py` | done |
| `packages/core/src/tool/handlers/bash-readonly-policy-flags-process.ts` | `openminis/agent/zcode/tool/handlers/bash_readonly_policy_flags_process.py` | done |
| `packages/core/src/tool/handlers/bash-readonly-policy-flags-text.ts` | `openminis/agent/zcode/tool/handlers/bash_readonly_policy_flags_text.py` | done |
| `packages/core/src/tool/handlers/bash-readonly-policy-flags.ts` | `openminis/agent/zcode/tool/handlers/bash_readonly_policy_flags.py` | done |
| `packages/core/src/tool/handlers/bash-readonly-policy-git-callbacks.ts` | `openminis/agent/zcode/tool/handlers/bash_readonly_policy_git_callbacks.py` | done |
| `packages/core/src/tool/handlers/bash-readonly-policy-git-subcommands-core.ts` | `openminis/agent/zcode/tool/handlers/bash_readonly_policy_git_subcommands_core.py` | done |
| `packages/core/src/tool/handlers/bash-readonly-policy-git-subcommands-history.ts` | `openminis/agent/zcode/tool/handlers/bash_readonly_policy_git_subcommands_history.py` | done |
| `packages/core/src/tool/handlers/bash-readonly-policy-multiword-core.ts` | `openminis/agent/zcode/tool/handlers/bash_readonly_policy_multiword_core.py` | done |
| `packages/core/src/tool/handlers/bash-readonly-policy-multiword-gh.ts` | `openminis/agent/zcode/tool/handlers/bash_readonly_policy_multiword_gh.py` | done |
| `packages/core/src/tool/handlers/bash-readonly-policy-simple-commands.ts` | `openminis/agent/zcode/tool/handlers/bash_readonly_policy_simple_commands.py` | done |
| `packages/core/src/tool/handlers/bash-readonly-policy-types.ts` | `openminis/agent/zcode/tool/handlers/bash_readonly_policy_types.py` | done |
| `packages/core/src/tool/handlers/bash-readonly-policy.ts` | `openminis/agent/zcode/tool/handlers/bash_readonly_policy.py` | done |
| `packages/core/src/tool/handlers/bash-semantics.ts` | `openminis/agent/zcode/tool/handlers/bash_semantics.py` | done |
| `packages/core/src/tool/handlers/bash.ts` | `openminis/agent/zcode/tool/handlers/bash.py` | done |
| `packages/core/src/tool/handlers/edit.ts` | `openminis/agent/zcode/tool/handlers/edit.py` | done |
| `packages/core/src/tool/handlers/generated/bash-command-registry.ts` | `openminis/agent/zcode/tool/handlers/generated/__init__.py` | partial |
| `packages/core/src/tool/handlers/generated/bash-command-registry.ts` | `openminis/agent/zcode/tool/handlers/generated/bash_command_registry.py` | partial |
| `packages/core/src/tool/handlers/glob.ts` | `openminis/agent/zcode/tool/handlers/glob.py` | done |
| `packages/core/src/tool/handlers/grep.ts` | `openminis/agent/zcode/tool/handlers/grep.py` | done |
| `packages/core/src/tool/handlers/index.ts` | `openminis/agent/zcode/tool/handlers/__init__.py` | partial |
| `packages/core/src/tool/handlers/plan-mode-prompts.ts` | `openminis/agent/zcode/tool/handlers/plan_mode_prompts.py` | done |
| `packages/core/src/tool/handlers/plan-mode.ts` | `openminis/agent/zcode/tool/handlers/plan_mode.py` | done |
| `packages/core/src/tool/handlers/read-text.ts` | `openminis/agent/zcode/tool/handlers/read_text.py` | done |
| `packages/core/src/tool/handlers/read.ts` | `openminis/agent/zcode/tool/handlers/read.py` | partial |
| `packages/core/src/tool/handlers/skill.ts` | `openminis/agent/zcode/tool/handlers/skill.py` | done |
| `packages/core/src/tool/handlers/task-output-bash.ts` | `openminis/agent/zcode/tool/handlers/task_output_bash.py` | done |
| `packages/core/src/tool/handlers/task-output-projection.ts` | `openminis/agent/zcode/tool/handlers/task_output_projection.py` | done |
| `packages/core/src/tool/handlers/task-output.ts` | `openminis/agent/zcode/tool/handlers/task_output.py` | done |
| `packages/core/src/tool/handlers/task-stop.ts` | `openminis/agent/zcode/tool/handlers/task_stop.py` | done |
| `packages/core/src/tool/handlers/todo.ts` | `openminis/agent/zcode/tool/handlers/todo.py` | done |
| `packages/core/src/tool/handlers/tool-perf.ts` | `openminis/agent/zcode/tool/handlers/tool_perf.py` | done |
| `packages/core/src/tool/handlers/webfetch-cache.ts` | `openminis/agent/zcode/tool/handlers/webfetch_cache.py` | done |
| `packages/core/src/tool/handlers/webfetch-constants.ts` | `openminis/agent/zcode/tool/handlers/webfetch_constants.py` | done |
| `packages/core/src/tool/handlers/webfetch.ts` | `openminis/agent/zcode/tool/handlers/webfetch.py` | partial |
| `packages/core/src/tool/handlers/websearch.ts` | `openminis/agent/zcode/tool/handlers/websearch.py` | partial |
| `packages/core/src/tool/handlers/workflow-script-path.ts` | `openminis/agent/zcode/tool/handlers/workflow_script_path.py` | done |
| `packages/core/src/tool/handlers/write.ts` | `openminis/agent/zcode/tool/handlers/write.py` | done |
| `packages/core/src/tool/input-normalization.ts` | `openminis/agent/zcode/tool/input_normalization.py` | done |
| `packages/core/src/tool/input-validation-model-content.ts` | `openminis/agent/zcode/tool/input_validation_model_content.py` | done |
| `packages/core/src/tool/json-schema.ts` | `openminis/agent/zcode/tool/json_schema.py` | done |
| `packages/core/src/tool/model-contract.ts` | `openminis/agent/zcode/tool/model_contract.py` | partial |
| `packages/core/src/tool/path-normalization.ts` | `openminis/agent/zcode/tool/path_normalization.py` | done |
| `packages/core/src/tool/path-policy.ts` | `openminis/agent/zcode/tool/path_policy.py` | done |
| `packages/core/src/tool/read-file-state-metadata.ts` | `openminis/agent/zcode/tool/read_file_state_metadata.py` | done |
| `packages/core/src/tool/read-file-state.ts` | `openminis/agent/zcode/tool/read_file_state.py` | done |
| `packages/core/src/tool/registry.ts` | `openminis/agent/zcode/tool/registry.py` | done |
| `packages/core/src/tool/result-persistence-format.ts` | `openminis/agent/zcode/tool/result_persistence_format.py` | done |
| `packages/core/src/tool/scheduler.ts` | `openminis/agent/zcode/tool/scheduler.py` | done |
| `packages/core/src/tool/tool-input-validation-issues.ts` | `openminis/agent/zcode/tool/tool_input_validation_issues.py` | done |
| `packages/core/src/tool/tool-visibility.ts` | `openminis/agent/zcode/tool/tool_visibility.py` | done |
| `packages/core/src/tool/types.ts` | `openminis/agent/zcode/tool/types.py` | done |
| `packages/core/src/tool/webfetch-preapproved.ts` | `openminis/agent/zcode/tool/executor/webfetch_preapproved.py` | done |
| `packages/core/src/tool/webfetch-preapproved.ts` | `openminis/agent/zcode/tool/webfetch_preapproved.py` | done |
| `packages/shared/src/zcode-protocol-v4/workflow-artifact.ts` | `openminis/agent/zcode/tool/executor/workflow_artifact.py` | done |

## zcode

| TS 源 | Python | 状态 |
|---|---|---|
| `apps/zcode-cli/packages/core + packages/contracts` | `openminis/agent/zcode/__init__.py` | done |
| `packages/core/src/hooks/index.ts` | `openminis/agent/zcode/hooks.py` | partial |
| `packages/core/src/runtime/agent-runtime.ts` | `openminis/agent/zcode/agent_entry.py` | done |
| `packages/core/src/shared/index.ts` | `openminis/agent/zcode/shared.py` | partial |

## zcode_cua

| TS 源 | Python | 状态 |
|---|---|---|
| `packages/zcode-cua/` | `openminis/agent/zcode/zcode_cua/__init__.py` | done |
| `packages/zcode-cua/frame-contract.js` | `openminis/agent/zcode/zcode_cua/frame_contract.py` | done |
| `packages/zcode-cua/request-access-contract.js` | `openminis/agent/zcode/zcode_cua/request_access_contract.py` | done |

## Python 专有 / 端口占位（无 ``Ported from:`` 头）

| Python | 说明 |
|---|---|
| `openminis/agent/zcode/context/sections/__init__.py` | 文件头无 ``Ported from:`` —— 端口占位 / Python 专有 |
| `openminis/agent/zcode/core/__init__.py` | 文件头无 ``Ported from:`` —— 端口占位 / Python 专有 |
| `openminis/agent/zcode/core/abort.py` | 文件头无 ``Ported from:`` —— 端口占位 / Python 专有 |
| `openminis/agent/zcode/integration/__init__.py` | 文件头无 ``Ported from:`` —— 端口占位 / Python 专有 |
| `openminis/agent/zcode/integration/agent.py` | 文件头无 ``Ported from:`` —— 端口占位 / Python 专有 |
| `openminis/agent/zcode/integration/host_ports.py` | 文件头无 ``Ported from:`` —— 端口占位 / Python 专有 |
| `openminis/agent/zcode/integration/skills.py` | 文件头无 ``Ported from:`` —— 端口占位 / Python 专有 |
| `openminis/agent/zcode/integration/toolchain.py` | 文件头无 ``Ported from:`` —— 端口占位 / Python 专有 |
| `openminis/agent/zcode/memory/memory_agent_loop.py` | 文件头无 ``Ported from:`` —— 端口占位 / Python 专有 |
| `openminis/agent/zcode/runtime/__init__.py` | 文件头无 ``Ported from:`` —— 端口占位 / Python 专有 |
| `openminis/agent/zcode/runtime/helpers/__init__.py` | 文件头无 ``Ported from:`` —— 端口占位 / Python 专有 |
| `openminis/agent/zcode/runtime/methods/__init__.py` | 文件头无 ``Ported from:`` —— 端口占位 / Python 专有 |
| `openminis/agent/zcode/system_reminder/__init__.py` | 文件头无 ``Ported from:`` —— 端口占位 / Python 专有 |
| `openminis/agent/zcode/tool/executor/__init__.py` | 文件头无 ``Ported from:`` —— 端口占位 / Python 专有 |
