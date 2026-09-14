"""``image_gen`` tool — 用「生图槽」绑定的模型生成图片。

生图是第四个模型能力（``image``）。模型从 设置 → 模型服务 → 用途分槽
的「生图」槽读取，与对话/识图互不干扰。

传输协议：**OpenAI 兼容的** ``POST {base}/images/generations``（``dall-e-3``、
``gpt-image-1``，以及绝大多数国内 OpenAI 兼容网关如即梦/通义万相的兼容层
都是这个形状）。厂商没有这个端点、或引擎未移植时，工具**明确说明未接入**，
而不是假装调用或抛异常 —— 这样 agent 能如实告诉用户去配什么。

生成的图片落盘到 ``<workspace>/generated/``，返回值里同时带 image_data
（多模态主模型能在下一轮直接看到图）。
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from pathlib import Path

from ..core.context import app_context
from ..core.logging import get_logger
from ..data.model.agent_tool_definition import AgentToolDefinition, AgentToolParam
from .tool_execution_result import ToolExecutionResult

logger = get_logger(__name__)

__all__ = ["ImageGenTool"]

TIMEOUT_SECONDS = 120.0
DEFAULT_SIZE = "1024x1024"
#: 单次调用最多生成的张数（与 ``repeat_guard.max_images_per_turn`` 对齐）。
MAX_COUNT = 8

#: 该引擎是否支持 OpenAI 兼容的生图端点。
_IMAGE_ENGINES = {"openai"}


def _generated_dir() -> Path:
    p = app_context().external_files_dir / "generated"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _not_configured_text() -> str:
    return (
        "生图未启用：还没有绑定「生图」模型的槽位。请打开 设置 → 模型服务 → "
        "用途分槽，把「生图」槽指向一个生图模型（如 dall-e-3 / gpt-image-1 / "
        "flux / 即梦），然后重试。"
    )


class ImageGenTool:
    """Kotlin counterpart: not ported — 生图在 Android 走独立的 image endpoint。"""

    NAME = "image_gen"

    @staticmethod
    def definition() -> AgentToolDefinition:
        return AgentToolDefinition(
            name=ImageGenTool.NAME,
            description=(
                "Generate an image from a text prompt using the model bound to "
                "the 生图 (image) slot. Returns the saved file path plus the "
                "image itself. The model must be configured in 设置 → 模型服务 → "
                "用途分槽 → 生图; when it is not, the tool says so instead of "
                "pretending to draw. To make several images, set `count` "
                "according to what the user asked for (说几张就填几), instead "
                "of calling the tool again — it runs the generation `count` "
                "times in this one call."
            ),
            parameters={
                "tool_title": AgentToolParam(
                    "string",
                    "A concise 5-10 word summary of what this tool call does, "
                    "shown to the user (e.g. 'Draw a sunset over mountains'). "
                    "Use the same language as the user.",
                ),
                "prompt": AgentToolParam(
                    "string",
                    "What to draw. Be specific about subject, style, lighting "
                    "and composition. Use the user's language unless the image "
                    "model clearly expects English.",
                ),
                "size": AgentToolParam(
                    "string",
                    f"Image size like '1024x1024' (default: {DEFAULT_SIZE}).",
                ),
                "count": AgentToolParam(
                    "integer",
                    "How many images to generate in THIS single call, decided "
                    "from the user's wording: 说一张就填 1，说两张就填 2 "
                    f"(default 1, max {MAX_COUNT}). Do NOT call this tool "
                    "repeatedly to make several images — set `count` once and "
                    "the tool will run the generation that many times.",
                ),
            },
            required=["tool_title", "prompt"],
            property_ordering=["tool_title", "prompt", "size", "count"],
        )

    @staticmethod
    async def execute(args_json: str, session_id: str) -> ToolExecutionResult:
        try:
            args = json.loads(args_json)
        except (ValueError, TypeError):
            return ToolExecutionResult("Error: invalid JSON args", False,
                                       tool_title=ImageGenTool.NAME)
        tool_title = str(args.get("tool_title", ImageGenTool.NAME))
        prompt = str(args.get("prompt", "")).strip()
        size = str(args.get("size", "") or DEFAULT_SIZE).strip()
        if not prompt:
            return ToolExecutionResult("Error: 'prompt' is required", False,
                                       tool_title=tool_title)
        # 张数由模型按语义决定（说一张填 1、说两张填 2）；这里只做范围收敛。
        try:
            count = int(args.get("count") or 1)
        except (TypeError, ValueError):
            count = 1
        count = max(1, min(count, MAX_COUNT))

        # 读取生图槽 —— 槽位/存储是运行时的单一事实来源
        try:
            from ..settings.store import SettingsStore

            store = SettingsStore.get()
            binding = store.slot_binding("image")
        except Exception as exc:  # pragma: no cover - settings unreadable
            return ToolExecutionResult(
                f"生图配置读取失败：{type(exc).__name__}: {exc}", False,
                tool_title=tool_title,
            )
        if binding is None:
            return ToolExecutionResult(_not_configured_text(), False,
                                       tool_title=tool_title)
        conf, model_id = binding

        from ..settings.catalog import engine_for

        engine = engine_for(str(conf.get("type") or ""))
        if engine not in _IMAGE_ENGINES:
            return ToolExecutionResult(
                f"生图未接入：厂商「{conf.get('type')}」的引擎不支持生图端点"
                f"（当前仅支持 OpenAI 兼容的 /images/generations）。"
                "可改用 OpenAI 兼容网关，或把生图槽指向另一家实例。",
                False, tool_title=tool_title,
            )

        api_key = str(conf.get("apiKey") or "").strip()
        if not api_key:
            return ToolExecutionResult(
                "生图失败：该厂商实例还没有填 API Key（设置 → 模型服务）。",
                False, tool_title=tool_title,
            )
        base_url = str(conf.get("baseUrl") or "").strip() or "https://api.openai.com/v1"
        url = f"{base_url.rstrip('/')}/images/generations"

        try:
            import httpx
        except ImportError:  # pragma: no cover
            return ToolExecutionResult("Error: httpx 未安装，无法生图", False,
                                       tool_title=tool_title)

        payload: dict = {"model": model_id, "prompt": prompt, "n": 1}
        if size:
            payload["size"] = size

        # 一次调用里串行跑 count 次 —— 「说一张跑一次、说两张跑两次」。
        images: list[tuple[bytes, Path]] = []
        failures: list[str] = []
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                for _ in range(count):
                    raw, err = await _generate_once(
                        client, url, api_key, payload, model_id
                    )
                    if raw is None:
                        failures.append(err or "未知错误")
                        continue
                    out_path = _generated_dir() / (
                        f"img_{int(time.time())}_{uuid.uuid4().hex[:6]}.png"
                    )
                    try:
                        out_path.write_bytes(raw)
                    except OSError as exc:  # pragma: no cover - disk issues
                        failures.append(f"生图成功但保存失败：{exc}")
                        continue
                    images.append((raw, out_path))
        except Exception as exc:  # pragma: no cover - client construction
            logger.debug("image_gen client failed: %s", exc)
            return ToolExecutionResult(
                f"生图请求失败（{model_id}）: {type(exc).__name__}: {exc}", False,
                tool_title=tool_title,
            )

        if not images:
            detail = failures[0] if failures else "响应里没有图片数据"
            return ToolExecutionResult(f"生图失败（{model_id}）：{detail}", False,
                                       tool_title=tool_title)

        lines = [f"[image_gen · {model_id} · {size} · {len(images)}/{count} 张]"]
        for _, path in images:
            lines.append(f"已生成图片并保存到工作区: generated/{path.name}")
        if failures:
            lines.append(f"（另有 {len(failures)} 张失败：{failures[0]}）")

        first_raw, first_path = images[0]
        return ToolExecutionResult(
            output="\n".join(lines),
            success=True,
            image_data=first_raw,
            image_mime_type="image/png",
            image_file_path=str(first_path),
            tool_title=tool_title,
        )


async def _generate_once(
    client: "httpx.AsyncClient",
    url: str,
    api_key: str,
    payload: dict,
    model_id: str,
) -> tuple[bytes | None, str | None]:
    """One generation run. Returns ``(image_bytes, None)`` or ``(None, error)``."""
    try:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json=payload,
        )
    except Exception as exc:
        logger.debug("image_gen request failed: %s", exc)
        return None, f"请求失败 {type(exc).__name__}: {exc}"

    if resp.status_code >= 400:
        return None, f"HTTP {resp.status_code}: {resp.text[:400]}"

    try:
        body = resp.json()
        item = (body.get("data") or [{}])[0]
    except (ValueError, IndexError, AttributeError):
        return None, "响应不是预期的 JSON 形状"

    raw: bytes | None = None
    if item.get("b64_json"):
        try:
            raw = base64.b64decode(item["b64_json"])
        except (ValueError, TypeError):
            raw = None
    elif item.get("url"):
        raw = await _download(item["url"])
    if not raw:
        return None, "响应里没有图片数据"
    return raw, None


async def _download(url: str) -> bytes | None:
    """Fetch a generated image that came back as a URL instead of base64."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS,
                                     follow_redirects=True) as client:
            r = await client.get(url)
            if r.status_code < 400:
                return r.content
        logger.debug("image_gen download HTTP %s", r.status_code)
    except Exception as exc:  # pragma: no cover - network
        logger.debug("image_gen download failed: %s", exc)
    return None
