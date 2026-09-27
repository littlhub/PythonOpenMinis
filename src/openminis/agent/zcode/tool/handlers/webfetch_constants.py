"""WebFetch constants — URL / cache / network tuning.

Ported from: packages/core/src/tool/handlers/webfetch-constants.ts
Original package: @zcode/core (tool/handlers)

# PORT: ``WEBFETCH_TOOL_NAME`` / ``DEFAULT_WEBFETCH_TIMEOUT_MS`` /
# ``MAX_WEBFETCH_MODEL_BYTES`` already live in ``.ports`` (mirrors the
# contracts landing there); this module carries the remaining
# webfetch-constants.ts values that are not shared by other handlers.
"""

from __future__ import annotations

# 与 TS ``MAX_WEBFETCH_URL_CHARS`` 一致：超长 URL 直接拒绝，避免 egress 之前就浪费一次解析。
MAX_WEBFETCH_URL_CHARS = 2_000

# 与 TS ``MAX_WEBFETCH_RESPONSE_BYTES`` 一致：单响应体上限 10 MiB，超过由 HttpClientPort 截断。
MAX_WEBFETCH_RESPONSE_BYTES = 10 * 1024 * 1024

# 与 TS ``MAX_MODEL_INPUT_CHARS`` 一致：喂给抽取模型的纯文本上限 100k 字符。
MAX_MODEL_INPUT_CHARS = 100_000

# 与 TS ``CACHE_TTL_MS`` 一致：每条 URL 缓存 15 分钟。
CACHE_TTL_MS = 15 * 60 * 1000

# 与 TS ``CACHE_MAX_BYTES`` 一致：进程内缓存总容量 50 MiB，超出按 LRU 淘汰。
CACHE_MAX_BYTES = 50 * 1024 * 1024

# 与 TS ``MAX_REDIRECTS`` 一致：跨主机重定向安全上限。
MAX_REDIRECTS = 10

# 与 TS ``WEBFETCH_USER_AGENT`` 一致。
WEBFETCH_USER_AGENT = "ZCode-WebFetch/0.1 (+https://zcode.ai; coding-agent-cli)"
