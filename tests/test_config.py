"""Tests for the ported ConfigValue / ConfigSchema / ConfigRegistry.

Ported behaviour checked here mirrors the Android instrumented tests under
``src/android/app/src/androidTest/java/com/openminis/app/config/``.
"""

from __future__ import annotations

import pytest

from openminis.config.config_error import ConfigError, OutOfRange, TypeMismatch
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
