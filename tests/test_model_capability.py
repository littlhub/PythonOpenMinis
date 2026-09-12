"""模型用途标签（八类、可多选）与用途槽位解析。"""

from __future__ import annotations

import pytest

from openminis.settings.model_capability import (
    CAP_3D,
    CAP_AUDIO,
    CAP_AUDIO_GEN,
    CAP_IMAGE,
    CAP_LLM,
    CAP_VIDEO,
    CAP_VIDEO_GEN,
    CAP_VISION,
    SLOT_CAPABILITIES,
    capabilities_catalog,
    infer_capabilities,
    normalize_capabilities,
    normalize_capability,
    pick_slot_model,
    resolve_capabilities,
)

# ---------------------------------------------------------------------------
# inference
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("model_id,expected", [
    # 生图 —— 优先于其它规则（这些 id 里也有 "image"/"vl" 之类字样）
    ("dall-e-3", [CAP_IMAGE]),
    ("gpt-image-1", [CAP_IMAGE]),
    ("flux-1.1-pro", [CAP_IMAGE]),
    ("stable-diffusion-xl", [CAP_IMAGE]),
    ("sdxl-turbo", [CAP_IMAGE]),
    ("qwen-image", [CAP_IMAGE]),
    ("doubao-seedream-3-0-t2i", [CAP_IMAGE]),
    ("imagen-3.0-generate", [CAP_IMAGE]),
    # 生视频
    ("sora-2", [CAP_VIDEO_GEN]),
    ("kling-v1", [CAP_VIDEO_GEN]),
    ("veo-3", [CAP_VIDEO_GEN]),
    ("cogvideox-5b", [CAP_VIDEO_GEN]),
    ("wan2.1-t2v", [CAP_VIDEO_GEN]),
    ("seedance-1-0", [CAP_VIDEO_GEN]),
    # 生音频
    ("cosyvoice-v2", [CAP_AUDIO_GEN]),
    ("tts-1", [CAP_AUDIO_GEN]),
    ("sambert-zhichu", [CAP_AUDIO_GEN]),
    ("gpt-4o-audio-preview", [CAP_AUDIO_GEN]),
    # 3D
    ("hunyuan3d-2", [CAP_3D]),
    ("tripo3d", [CAP_3D]),
    ("meshy-4", [CAP_3D]),
    # 音频理解
    ("whisper-large-v3", [CAP_AUDIO]),
    ("paraformer-v2", [CAP_AUDIO]),
    ("sensevoice-small", [CAP_AUDIO]),
    # 视频理解
    ("video-llava-7b", [CAP_VIDEO]),
    ("qwen-vl-video", [CAP_VIDEO]),
    # 识图 / 多模态（能看图通常也能对话 -> 对话 + 识图）
    ("qwen-vl-max", [CAP_LLM, CAP_VISION]),
    ("glm-4v-plus", [CAP_LLM, CAP_VISION]),
    ("llava-1.5-7b", [CAP_LLM, CAP_VISION]),
    ("pixtral-12b", [CAP_LLM, CAP_VISION]),
    ("deepseek-vl2", [CAP_LLM, CAP_VISION]),
    ("internvl2-8b", [CAP_LLM, CAP_VISION]),
    ("gpt-4o", [CAP_LLM, CAP_VISION]),
    ("gpt-5.2", [CAP_LLM, CAP_VISION]),
    ("claude-sonnet-5", [CAP_LLM, CAP_VISION]),
    ("gemini-2.5-pro", [CAP_LLM, CAP_VISION]),
    ("grok-4.6", [CAP_LLM, CAP_VISION]),
    ("llama-4-maverick", [CAP_LLM, CAP_VISION]),
    # 扩充后的多模态家族（避免"能看图却显示成对话"）
    ("gpt-4-turbo", [CAP_LLM, CAP_VISION]),
    ("gpt-4.5-preview", [CAP_LLM, CAP_VISION]),
    ("gemma-3-27b-it", [CAP_LLM, CAP_VISION]),
    ("phi-4-multimodal-instruct", [CAP_LLM, CAP_VISION]),
    ("amazon-nova-pro", [CAP_LLM, CAP_VISION]),
    ("o1-preview", [CAP_LLM, CAP_VISION]),
    # 纯文本 LLM —— 厂商品牌名本身不算多模态（保守判定，可手动改）
    ("deepseek-chat", [CAP_LLM]),
    ("kimi-k2-thinking", [CAP_LLM]),
    ("qwen3-max", [CAP_LLM]),
    ("doubao-pro-32k", [CAP_LLM]),
    ("ernie-4.5-turbo", [CAP_LLM]),
    ("glm-4-plus", [CAP_LLM]),
    ("some-random-local-model", [CAP_LLM]),
    ("", [CAP_LLM]),
])
def test_infer_capabilities(model_id, expected):
    assert infer_capabilities(model_id) == expected


@pytest.mark.parametrize("model_id", [
    "glm-4.5v", "qwen3-vl-plus", "doubao-1.5-vision-pro",
    "ernie-4.5-turbo-vl", "hunyuan-vision", "kimi-vl",
])
def test_chinese_vision_variants(model_id):
    assert infer_capabilities(model_id) == [CAP_LLM, CAP_VISION]


def test_infer_capabilities_never_throws_on_garbage():
    assert infer_capabilities(None) == [CAP_LLM]  # type: ignore[arg-type]


def test_underscore_and_dash_normalised():
    assert infer_capabilities("gpt_image_1") == [CAP_IMAGE]


# ---------------------------------------------------------------------------
# overrides win over inference
# ---------------------------------------------------------------------------
def test_user_override_wins():
    # 推断是「对话+识图」，但用户说它其实只能文本 -> 以用户为准
    caps, src = resolve_capabilities("gpt-4o", {"gpt-4o": [CAP_LLM]})
    assert (caps, src) == ([CAP_LLM], "user")
    # 覆盖成多个标签
    caps2, src2 = resolve_capabilities("gpt-4o", {"gpt-4o": [CAP_LLM, CAP_IMAGE]})
    assert (caps2, src2) == ([CAP_LLM, CAP_IMAGE], "user")
    # 没覆盖的走推断
    caps3, src3 = resolve_capabilities("gpt-4o", {"other": [CAP_LLM]})
    assert (caps3, src3) == ([CAP_LLM, CAP_VISION], "auto")


def test_bogus_override_falls_back_to_inference():
    caps, src = resolve_capabilities("gpt-4o", {"gpt-4o": ["not-a-capability"]})
    assert (caps, src) == ([CAP_LLM, CAP_VISION], "auto")


# ---------------------------------------------------------------------------
# normalisation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("llm", [CAP_LLM]),
    ("IMAGE", [CAP_IMAGE]),
    ("image_input", [CAP_IMAGE]),
    ("text_output", [CAP_LLM]),
    ("vlm", [CAP_VISION]),
    ("image_gen", [CAP_IMAGE]),
    ("tts", [CAP_AUDIO_GEN]),
    ("multimodal", [CAP_LLM, CAP_VISION]),   # 旧标签展开
    (["vision", "llm"], [CAP_LLM, CAP_VISION]),  # 规整成展示顺序
    (["llm", "llm"], [CAP_LLM]),
    (["bogus"], []),
    ([], []),
    ("bogus", None),
    (None, None),
])
def test_normalize_capabilities(raw, expected):
    assert normalize_capabilities(raw) == expected


def test_normalize_capability_single():
    assert normalize_capability("llm") == CAP_LLM
    assert normalize_capability("vlm") == CAP_VISION
    assert normalize_capability("bogus") is None


def test_capabilities_catalog_shape():
    cat = capabilities_catalog()
    assert [c["id"] for c in cat] == [
        CAP_LLM, CAP_VISION, CAP_IMAGE, CAP_3D,
        CAP_AUDIO, CAP_VIDEO, CAP_AUDIO_GEN, CAP_VIDEO_GEN,
    ]
    assert all(c["label"] for c in cat)


# ---------------------------------------------------------------------------
# slot resolution
# ---------------------------------------------------------------------------
def test_slot_accepts():
    assert SLOT_CAPABILITIES["chat"] == (CAP_LLM,)
    assert SLOT_CAPABILITIES["vision"] == (CAP_VISION,)
    assert SLOT_CAPABILITIES["image"] == (CAP_IMAGE,)
    assert SLOT_CAPABILITIES["model3d"] == (CAP_3D,)
    assert SLOT_CAPABILITIES["audio"] == (CAP_AUDIO,)
    assert SLOT_CAPABILITIES["video"] == (CAP_VIDEO,)
    assert SLOT_CAPABILITIES["audio_gen"] == (CAP_AUDIO_GEN,)
    assert SLOT_CAPABILITIES["video_gen"] == (CAP_VIDEO_GEN,)


def test_pick_slot_model_prefers_best_rank():
    cands = [
        ("gw", "gpt-4o", [CAP_LLM, CAP_VISION]),
        ("gw", "qwen-vl-max", [CAP_LLM, CAP_VISION]),
        ("gen", "dall-e-3", [CAP_IMAGE]),
    ]
    assert pick_slot_model(cands, "vision") == ("gw", "gpt-4o")
    assert pick_slot_model(cands, "image") == ("gen", "dall-e-3")
    assert pick_slot_model(cands, "chat") == ("gw", "gpt-4o")


def test_pick_slot_model_none_when_nothing_fits():
    assert pick_slot_model([("gw", "gpt-4o", [CAP_LLM, CAP_VISION])], "image") is None
    assert pick_slot_model([], "vision") is None
    assert pick_slot_model([("gw", "x", [CAP_LLM])], "audio_gen") is None


def test_pick_slot_model_accepts_bare_string_caps():
    # 兼容旧的单标签字符串
    assert pick_slot_model([("gen", "dall-e-3", CAP_IMAGE)], "image") == ("gen", "dall-e-3")
