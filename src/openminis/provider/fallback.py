"""LLM 兜底模型链 —— 主模型限流 / 超时 / 5xx 时自动切到下一个。

用户在「设置 → 对话参数」里可以配一串兜底模型（第一、第二、第三…… 想加几个加
几个）。一轮对话里主模型如果被限流（429）、超时/断网、或者网关返回 5xx，就按顺序
换下一个再来一次。

关键约束：**只有这一轮还没吐出任何内容时才能换** —— 已经流到界面上的文字撤不回
来，硬换会串内容。所以重试判定发生在流的开头；一旦拿到第一个有意义的 chunk，
后续报错就照常向上抛。

并行会话同时跑的时候这条链尤其值钱：并发一高，网关限流几乎是常态，主模型一被限
就整轮失败，用户只看到一句「Rate limited」。

Kotlin 对应实现：``ChatViewModel.kt`` 的 ``remainingFallbacks`` / ``isFallbackable``
（原版按「模型分组」组织候选，这里按用户显式配置的有序列表，语义更直白）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any, Optional

from openminis.core.logging import get_logger
from openminis.data.model import (
    AgentToolDefinition,
    LLMError,
    LLMMessage,
    LLMModel,
    LLMResponse,
    LLMStreamChunk,
    ThinkingLevel,
)
from openminis.provider.llm_provider import LLMProvider

__all__ = ["FallbackCandidate", "FallbackChainProvider", "describe_error"]

logger = get_logger(__name__)

#: 同一个候选在**换模型之前**先自己重试几次（只针对可重试错误：断网/超时/瞬时）。
#: 限流和 4xx 不在这里重试 —— 立刻换下一个更快，也更省 token。
_SAME_PROVIDER_RETRIES = 2

#: 判断一个错误值不值得换模型。
#: - ``is_fallbackable``：限流（429）/ API Key 失效 / 厂商侧错误
#: - ``is_retryable``：断网、超时（httpx 超时映射来的 NetworkError）与 5xx
#: Kotlin 侧判据是 ``isRateLimit || is5xx``（ChatViewModel），这里用错误类型
#: 自带的分类表达同一件事：重试都没好的东西，换个人试是合理的。
def _should_switch(err: LLMError) -> bool:
    return bool(err.is_fallbackable or err.is_retryable)


def _is_transient(err: LLMError) -> bool:
    """同候选内部可重试的（断网/超时/5xx），先在原地试几次再换人。"""
    return bool(err.is_retryable)


def describe_error(err: BaseException) -> str:
    """给用户看的一句话原因（挂在切模型的提示条上）。"""
    if isinstance(err, LLMError):
        return err.fallback_reason
    return type(err).__name__


class FallbackCandidate:
    """兜底链上的一个候选：厂商实例 id + 模型 id + 展示名。"""

    __slots__ = ("instance_id", "model_id", "label")

    def __init__(self, instance_id: str, model_id: str, label: str = "") -> None:
        self.instance_id = instance_id
        self.model_id = model_id
        self.label = label or f"{instance_id}/{model_id}"

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"FallbackCandidate({self.label!r})"


class FallbackChainProvider(LLMProvider):
    """把「主模型 + 有序兜底列表」包装成一个普通 provider。

    对上层（agent runtime）完全透明：接口与单个 provider 一致，出错时才在内部
    换人。``build`` 由 chat_service 注入 —— 它拿得到 settings，负责按
    ``(instance_id, model_id)`` 造/复用 provider（连带连接池缓存）。
    """

    def __init__(
        self,
        primary: LLMProvider,
        candidates: list[FallbackCandidate],
        build: Callable[[FallbackCandidate], LLMProvider],
        *,
        on_switch: Optional[Callable[[str, str, str], Any]] = None,
        same_provider_retries: int = _SAME_PROVIDER_RETRIES,
    ) -> None:
        self._primary = primary
        self._candidates = list(candidates)
        self._build = build
        #: ``(from_label, to_label, reason)`` —— 真换模型时回调（前端挂提示条）。
        self._on_switch = on_switch
        self._retries = max(0, same_provider_retries)
        self._active: LLMProvider = primary
        #: 本轮已经切到过谁（累计说明，挂在提示条上）。
        self.switch_trail: list[str] = []

    # --- 对上层暴露的"当前长什么样" --------------------------------------
    @property
    def name(self) -> str:  # type: ignore[override]
        return getattr(self._active, "name", "fallback-chain")

    @property
    def model(self) -> LLMModel:  # type: ignore[override]
        return self._active.model

    @property
    def active(self) -> LLMProvider:
        """当前实际在用的那个 provider。"""
        return self._active

    @property
    def active_label(self) -> str:
        m = getattr(self._active, "model", None)
        label = getattr(m, "display_name", None)
        return str(label or getattr(self._active, "name", "model"))

    def effective_max_output_tokens(self, model: LLMModel) -> int:
        return self._active.effective_max_output_tokens(model)

    @property
    def default_max_output_tokens(self) -> int:
        return self._active.default_max_output_tokens

    @property
    def stream_text_is_monolithic(self) -> bool:
        return bool(getattr(self._active, "stream_text_is_monolithic", False))

    # --- 候选解析 --------------------------------------------------------
    def _ordered(self) -> list[tuple[Optional[FallbackCandidate], LLMProvider]]:
        """主模型 + 兜底列表（跳过与主模型完全相同的候选，免得白切一次）。"""
        out: list[tuple[Optional[FallbackCandidate], LLMProvider]] = [
            (None, self._primary)
        ]
        seen: set[tuple[str, str]] = set()
        for c in self._candidates:
            key = (c.instance_id, c.model_id)
            if key in seen:
                continue
            seen.add(key)
            try:
                provider = self._build(c)
            except Exception:  # pragma: no cover - 配置坏了不该拖垮整轮
                logger.warning("fallback candidate %s unusable", c.label, exc_info=True)
                continue
            out.append((c, provider))
        return out

    def _note_switch(self, from_label: str, to_label: str, reason: str) -> None:
        note = f"{from_label}: {reason} → 已切到 {to_label}"
        self.switch_trail.append(note)
        logger.warning("🔀 LLM fallback: %s", note)
        if self._on_switch is None:
            return
        try:
            result = self._on_switch(from_label, to_label, reason)
            # 回调可能是协程（要往 WS 推帧）—— 不能在这里 await（本方法是同步的），
            # 交给事件循环去跑。
            if hasattr(result, "__await__"):  # pragma: no cover - 取决于回调
                import asyncio

                asyncio.get_running_loop().create_task(result)  # type: ignore[arg-type]
        except Exception:  # pragma: no cover - 提示失败不影响换模型
            logger.debug("fallback notice failed", exc_info=True)

    # --- 流式（主路径）---------------------------------------------------
    async def stream_message_clamped(
        self,
        messages: list[LLMMessage],
        system_prompt: Optional[str],
        max_tokens: int,
        temperature: Optional[float],
        image_parts: list[LLMMessage.ImagePart],
        tools: list[AgentToolDefinition],
        thinking_level: ThinkingLevel,
    ) -> AsyncIterator[LLMStreamChunk]:
        chain = self._ordered()
        last_error: BaseException | None = None
        for idx, (cand, provider) in enumerate(chain):
            has_next = idx + 1 < len(chain)
            for attempt in range(self._retries + 1):
                emitted = False
                try:
                    stream = provider.stream_message_clamped(
                        messages, system_prompt, max_tokens, temperature,
                        image_parts, tools, thinking_level,
                    )
                    async for chunk in stream:
                        emitted = True
                        yield chunk
                    self._active = provider
                    return
                except Exception as exc:  # noqa: BLE001 - 分类交给 LLMError
                    last_error = exc
                    err = exc if isinstance(exc, LLMError) else None
                    # 已经吐出内容 → 不能重来（会把两段回复接在一起），直接抛。
                    if emitted:
                        raise
                    if err is None:
                        raise
                    if _is_transient(err) and attempt < self._retries:
                        logger.info(
                            "%s transient failure, retry %d/%d",
                            cand.label if cand else "primary",
                            attempt + 1,
                            self._retries,
                        )
                        continue
                    if not _should_switch(err) or not has_next:
                        # 该换人但没人可换了（或这错误不该换）→ 交给上层报错。
                        if not has_next and _should_switch(err):
                            logger.warning(
                                "LLM fallback chain exhausted (%d candidates)",
                                len(chain),
                            )
                        raise
                    break
            if not has_next:
                break
            nxt = chain[idx + 1][0]
            reason = describe_error(last_error) if last_error else "error"
            self._note_switch(
                cand.label if cand else self.active_label,
                nxt.label if nxt else "next",
                reason,
            )
        if last_error is not None:
            raise last_error

    # --- 单次（子代理摘要等同步路径用得到）--------------------------------
    async def send_message_clamped(
        self,
        messages: list[LLMMessage],
        system_prompt: Optional[str],
        max_tokens: int,
        temperature: Optional[float],
        image_parts: list[LLMMessage.ImagePart],
        tools: list[AgentToolDefinition],
        thinking_level: ThinkingLevel,
    ) -> LLMResponse:
        chain = self._ordered()
        last_error: BaseException | None = None
        for idx, (cand, provider) in enumerate(chain):
            has_next = idx + 1 < len(chain)
            for attempt in range(self._retries + 1):
                try:
                    resp = await provider.send_message_clamped(
                        messages, system_prompt, max_tokens, temperature,
                        image_parts, tools, thinking_level,
                    )
                    self._active = provider
                    return resp
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    err = exc if isinstance(exc, LLMError) else None
                    if err is None:
                        raise
                    if _is_transient(err) and attempt < self._retries:
                        continue
                    if not _should_switch(err) or not has_next:
                        raise
                    break
            if not has_next:
                break
            nxt = chain[idx + 1][0]
            self._note_switch(
                cand.label if cand else self.active_label,
                nxt.label if nxt else "next",
                describe_error(last_error) if last_error else "error",
            )
        if last_error is not None:
            raise last_error
