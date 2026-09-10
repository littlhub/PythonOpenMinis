"""Tests for the ported ConfigValue / ConfigSchema / ConfigRegistry.

Ported behaviour checked here mirrors the Android instrumented tests under
``src/android/app/src/androidTest/java/com/openminis/app/config/``.
"""

from __future__ import annotations

import pytest

from openminis.config.config_error import ConfigError, OutOfRange, RegexMismatch, TypeMismatch
from openminis.config.config_registry import ConfigRegistry
from openminis.config.config_schema import (
    BoolSchema,
    DoubleSchema,
    IntSchema,
    OptionalSchema,
    StrEnumSchema,
    StrSchema,
)
from openminis.config.config_value import Arr, Bool, ConfigValue, Double, Int, Null, Obj, Str


class TestConfigValue:
    def test_decode_primitives(self) -> None:
        assert ConfigValue.decode("true") == Bool(True)
        assert ConfigValue.decode("42") == Int(42)
        assert ConfigValue.decode("1.5") == Double(1.5)
        assert ConfigValue.decode('"hi"') == Str("hi")
        assert ConfigValue.decode("null") == Null

    def test_decode_malformed_returns_none(self) -> None:
        assert ConfigValue.decode("{not json") is None

    def test_decode_nested(self) -> None:
        parsed = ConfigValue.decode('{"a": [1, "x"], "b": {"c": null}}')
        assert parsed is not None
        assert parsed.json_string() == '{"a": [1, "x"], "b": {"c": null}}'

    def test_display_string_truncates_long_strings(self) -> None:
        long_value = "x" * 120
        rendered = Str(long_value).display_string
        assert rendered.startswith('"')
        assert "…" in rendered
        assert len(rendered) < len(long_value)

    def test_null_display(self) -> None:
        assert Null.display_string == "null"

    def test_secret_redaction_masks_known_keys(self) -> None:
        payload = Obj({"apiKey": Str("sk-secret"), "name": Str("openai")})
        redacted = payload.redacting_secrets()
        assert isinstance(redacted, Obj)
        assert redacted.value["apiKey"] == Str("••• (hidden)")
        assert redacted.value["name"] == Str("openai")

    def test_secret_redaction_passes_through_var_references(self) -> None:
        payload = Obj({"apiKey": Str("$$MY_KEY")})
        redacted = payload.redacting_secrets()
        assert isinstance(redacted, Obj)
        assert redacted.value["apiKey"] == Str("$$MY_KEY")

    def test_redaction_recurses_into_arrays(self) -> None:
        payload = Arr([Obj({"oauthToken": Str("abc")})])
        redacted = payload.redacting_secrets()
        assert isinstance(redacted, Arr)
        inner = redacted.value[0]
        assert isinstance(inner, Obj)
        assert inner.value["oauthToken"] == Str("••• (hidden)")


class TestConfigSchema:
    def test_bool_rejects_non_bool(self) -> None:
        with pytest.raises(TypeMismatch):
            BoolSchema().validate(Str("yes"))

    def test_int_range(self) -> None:
        schema = IntSchema(min=1, max=10)
        schema.validate(Int(5))
        with pytest.raises(OutOfRange):
            schema.validate(Int(50))

    def test_double_accepts_int_widening(self) -> None:
        DoubleSchema(min=0.0, max=1.0).validate(Int(1))

    def test_str_enum(self) -> None:
        schema = StrEnumSchema(("light", "dark"))
        schema.validate(Str("dark"))
        with pytest.raises(ConfigError):
            schema.validate(Str("neon"))

    def test_str_regex_full_match(self) -> None:
        schema = StrSchema(regex=r"\d{4}")
        schema.validate(Str("2026"))
        with pytest.raises(ConfigError):
            schema.validate(Str("20"))

    def test_optional_allows_null(self) -> None:
        OptionalSchema(IntSchema()).validate(Null)

    def test_help_description(self) -> None:
        assert BoolSchema().help_description == "bool"
        # Kotlin & iOS both format the unbounded upper edge as "+∞".
        assert IntSchema(min=1).help_description == "int (1..+∞)"


class TestConfigRegistry:
    def test_topics_and_paths(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ConfigRegistry.reset_for_tests()
        registry = ConfigRegistry.init()
        assert "appearance" in registry.topics()
        assert "appearance.theme" in registry.all_visible_field_paths()
        ConfigRegistry.reset_for_tests()

    def test_resolve_and_roundtrip(self) -> None:
        ConfigRegistry.reset_for_tests()
        registry = ConfigRegistry.init()
        field = registry.resolve_field("appearance.theme")
        assert field is not None
        field.write(Str("light"))
        assert field.read() == Str("light")
        ConfigRegistry.reset_for_tests()

    def test_unknown_path_returns_none(self) -> None:
        ConfigRegistry.reset_for_tests()
        registry = ConfigRegistry.init()
        assert registry.resolve_field("nope.nope") is None
        ConfigRegistry.reset_for_tests()


class TestAppearanceBackground:
    """``appearance.background`` — Web 端专有的背景规格。

    校验必须留在后端:写盘只有 ``PUT /api/config/<path>`` 一条路径,只在前端
    限制的话,一个手写请求就能把任意字符串塞进样式表。格式见
    ``web/src/theme.ts`` 的 ``resolveBackground``,两边必须保持一致。
    """

    @staticmethod
    def _field():
        ConfigRegistry.reset_for_tests()
        registry = ConfigRegistry.init()
        field = registry.resolve_field("appearance.background")
        assert field is not None, "appearance.background 未注册"
        return field

    def test_registered_and_roundtrips(self) -> None:
        # 不断言「当前值」:这些字段落在真实的 prefs 文件里,上一次会话改过的
        # 值会留到下一次 —— 断言默认值等于让测试依赖用户机器状态。
        field = self._field()
        assert field._default == Str("default")
        assert isinstance(field.read(), Str)
        ConfigRegistry.reset_for_tests()

    @pytest.mark.parametrize(
        "value",
        [
            "default",
            "",
            "preset:ink",
            "preset:sky-blue",
            "#1a2b3c",
            "#FFFFFF",
            "url:https://example.test/bg.jpg",
            "file:background.png",
            "file:background.jpeg",
        ],
    )
    def test_accepts_legal_specs(self, value: str) -> None:
        schema = self._field().value_schema
        schema.validate(Str(value))  # 不抛异常即为通过
        ConfigRegistry.reset_for_tests()

    @pytest.mark.parametrize(
        "value",
        [
            "preset:",  # 空预设名
            "#xyz",  # 非十六进制
            "#12345",  # 位数不足
            "url:",  # 缺 URL
            "url:javascript:alert(1)",  # 非 http(s),会变成样式注入
            "random",  # 既不是预设也不是颜色
            "preset:Ink!",  # 预设名带非法字符
            "file:../settings.json",  # 路径穿越
            "file:background",  # 缺扩展名
            "file:",  # 缺文件名
        ],
    )
    def test_rejects_malformed_specs(self, value: str) -> None:
        schema = self._field().value_schema
        with pytest.raises(RegexMismatch):
            schema.validate(Str(value))
        ConfigRegistry.reset_for_tests()


class TestConfigListRoute:
    """``GET /api/config`` 与 ``/api/config/`` 必须都返回字段列表。

    尾斜杠形式曾被 ``/api/config/{path:path}`` 以空 path 吃掉,返回
    ``404 unknown_path: `` —— 前端 configList 恰好用的就是这个带斜杠的地址,
    于是「外观」页静默读不到任何字段。这类路由遮蔽不会报错、只在页面上表现为
    「空」,所以值得钉住。
    """

    @pytest.fixture()
    def client(self):
        from fastapi.testclient import TestClient

        from openminis.server.main import app

        ConfigRegistry.reset_for_tests()
        with TestClient(app) as c:
            yield c
        ConfigRegistry.reset_for_tests()

    def test_both_spellings_return_the_topic_fields(self, client) -> None:
        for url in ("/api/config?topic=appearance", "/api/config/?topic=appearance"):
            r = client.get(url)
            assert r.status_code == 200, f"{url} -> {r.status_code} {r.text}"
            paths = {f["path"] for f in r.json()}
            assert "appearance.background" in paths, url

    def test_catch_all_still_serves_a_single_path(self, client) -> None:
        r = client.get("/api/config/appearance.background")
        assert r.status_code == 200
        # Bare JSON value (not a {value} wrapper — that shape belongs to PUT).
        assert isinstance(r.json(), str)

    def test_unknown_path_still_404s(self, client) -> None:
        assert client.get("/api/config/nope.nope").status_code == 404
