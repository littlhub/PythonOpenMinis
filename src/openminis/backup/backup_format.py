"""On-the-wire types for the `.minisbak` backup package.

Ported from: src/android/app/src/main/java/com/openminis/app/backup/BackupFormat.kt
Original package: com.openminis.app.backup

Spec: `docs/backup-restore-design.md` §2 / §2.1. This is the FORMAT layer,
mirroring `src/ios/Agent/Backup/BackupFormat.swift` field-for-field —
§5.4 explicitly forbids inventing a second format, and the manifest keys
are sealed by `manifest_mac`, so every name here is wire-frozen.
"""

from __future__ import annotations

import contextlib
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Annotated, Generic, Literal, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, BeforeValidator, field_serializer, field_validator

__all__ = [
    "Iso8601Millis",
    "Iso8601MillisNullable",
    "BackupFormat",
    "BackupCategory",
    "BackupManifest",
    "BackupFileIndexEntry",
    "BackupBlobIndexEntry",
    "BackupRecordEnvelope",
    "BackupThinkingRuleRecord",
    "BackupEnvVarMeta",
    "BackupSecrets",
    "BackupException",
]


# [T-android-envvar-iso8601-wire] A `createdAt`-style timestamp that WRITES an
# ISO-8601 UTC string (matching iOS `JSONEncoder.dateEncodingStrategy =
# .iso8601`) but READS either form:
#   - a JSON string -> parsed as ISO-8601 (iOS packages, and new Android ones);
#   - a JSON number -> treated as epoch milliseconds (old Android packages,
#     which wrote `createdAt` as a bare Long).
# The in-memory value stays epoch-millis (int) so callers are unchanged; only
# the wire representation moves to a string.
def _iso_fmt() -> str:
    return "%Y-%m-%dT%H:%M:%SZ"


def _parse_iso_or_millis(value: Any) -> int:
    """Reads ISO-8601 string or legacy epoch-millis number -> epoch millis."""
    # PORT: Kotlin's NullableLongOrString peek; we accept int/float/str.
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        s = value.strip()
        if s == "":
            return 0
        # numeric form
        try:
            return int(s)
        except ValueError:
            pass
        try:
            dt = datetime.strptime(s, _iso_fmt()).replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except ValueError:
            return 0
    return 0


def _format_millis(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).strftime(_iso_fmt())


# Kotlin: Iso8601MillisSerializer / Iso8601MillisNullableSerializer expressed as
# pydantic annotated types.
Iso8601Millis = Annotated[int, BeforeValidator(_parse_iso_or_millis), PlainSerializer(_format_millis, return_type=str)]
Iso8601MillisNullable = Annotated[Optional[int], BeforeValidator(lambda v: None if v is None else _parse_iso_or_millis(v)), PlainSerializer(lambda v: None if v is None else _format_millis(v), return_type=Optional[str])]


class BackupFormat:
    """Format constants + the shared tolerant JSON parser."""
    CURRENT = "minisbak/1"
    FILE_EXTENSION = "minisbak"
    MIME_TYPE = "application/x-minisbak"
    MAX_SHARD_BYTES = 64 * 1024 * 1024


class BackupCategory(str, Enum):
    """User-facing backup categories (§3)."""

    CHATS = "chats"
    SHARED_FILES = "shared_files"
    SKILLS = "skills"
    MEMORY = "memory"
    PROVIDERS = "providers"
    MCP_SERVERS = "mcp_servers"
    VOICE_CORRECTIONS = "voice_corrections"
    # Shell environment variables. Wire key must stay `environment_variables`.
    ENVIRONMENT_VARIABLES = "environment_variables"

    @property
    def carries_file_tree(self) -> bool:
        """Categories that stream file trees through the blob store."""
        return self in (BackupCategory.CHATS, BackupCategory.SHARED_FILES, BackupCategory.SKILLS)

    @classmethod
    def from_key(cls, key: str) -> Optional[BackupCategory]:
        with contextlib.suppress(ValueError):
            return cls(key)
        return None

    @classmethod
    def backupable(cls) -> list[BackupCategory]:
        """Categories a NEW backup may include (Voice Corrections excluded)."""
        return [c for c in cls if c != BackupCategory.VOICE_CORRECTIONS]


class BackupManifest(BaseModel):
    """`manifest.json` — ALWAYS plaintext, even in an encrypted package (§2.1)."""

    model_config = ConfigDict(extra="ignore")

    format: str = BackupFormat.CURRENT
    created_at: str = ""
    snapshot_at: Optional[str] = None
    app: "BackupManifest.AppInfo" = Field(default_factory=lambda: BackupManifest.AppInfo())
    device_name: str = "Unknown device"
    backup_id: str = ""
    categories: dict[str, "BackupManifest.CategoryStat"] = Field(default_factory=dict)
    limits: "BackupManifest.Limits" = Field(default_factory=lambda: BackupManifest.Limits())
    encryption: Optional["BackupManifest.Encryption"] = None
    integrity: dict[str, str] = Field(default_factory=dict)
    manifest_mac: Optional[str] = None

    class AppInfo(BaseModel):
        model_config = ConfigDict(extra="ignore")
        platform: str = "unknown"
        version: str = "?"
        build: str = "?"

    class CategoryStat(BaseModel):
        model_config = ConfigDict(extra="ignore")
        entries: int = 0
        bytes: int = 0
        encrypted: bool = False
        messages: Optional[int] = None
        files: Optional[int] = None
        includes_credentials: Optional[bool] = Field(default=None, alias="includesCredentials")
        thinking_rules: Optional[int] = Field(default=None, alias="thinkingRules")

    class Limits(BaseModel):
        model_config = ConfigDict(extra="ignore")
        max_file_bytes: Optional[int] = Field(default=None, alias="maxFileBytes")
        skipped_files: int = Field(default=0, alias="skippedFiles")
        skipped_bytes: int = Field(default=0, alias="skippedBytes")

    class Encryption(BaseModel):
        model_config = ConfigDict(extra="ignore")
        scheme: str = ""
        kdf: "BackupManifest.Encryption.KDF"
        verifier: str = ""

        class KDF(BaseModel):
            model_config = ConfigDict(extra="ignore")
            # `alg` and `salt` have no safe default — required.
            alg: str
            salt: str
            m_kib: Optional[int] = Field(default=None, alias="mKib")
            t: Optional[int] = None
            p: Optional[int] = None
            iterations: Optional[int] = None


class BackupFileIndexEntry(BaseModel):
    """One line of `files.index.jsonl` — the directory-tree index (§2)."""

    model_config = ConfigDict(extra="ignore")
    path: str
    size: int = 0
    sha256: Optional[str] = None
    category: str = ""
    skipped: Optional[str] = None
    is_directory: Optional[bool] = Field(default=None, alias="isDirectory")

    @field_validator("is_directory", mode="before")
    @classmethod
    def _coerce_dir(cls, v: Any) -> Any:
        # PORT: tolerate missing / null like Kotlin default.
        return v


class BackupBlobIndexEntry(BaseModel):
    """One line of `blobs.index.jsonl` — content-addressed payload map (§2)."""

    model_config = ConfigDict(extra="ignore")
    sha256: str
    size: int
    path: str
    session_id: Optional[str] = Field(default=None, alias="sessionId")
    mime: Optional[str] = None


T = TypeVar("T")


class BackupRecordEnvelope(BaseModel, Generic[T]):
    """Generic JSONL record envelope; `t` is a type tag, `d` is the payload."""

    model_config = ConfigDict(extra="ignore")
    t: str
    d: T


class BackupThinkingRuleRecord(BaseModel):
    """One user-authored thinking rule on the wire (§ providers).

    Field names ARE the JSON keys (camelCase) — no snake_case remapping.
    """
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    id: str
    instanceId: str
    sortOrder: int = 0
    scopeKind: str = "allModels"
    scopePattern: Optional[str] = None
    wireFormatJson: str = "{}"
    echoField: Optional[str] = None
    echoTiming: Optional[str] = None
    label: str = ""
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None


class BackupEnvVarMeta(BaseModel):
    """Env-var metadata line of `data/env_vars.json` (an array)."""

    model_config = ConfigDict(extra="ignore")
    id: str
    key: str
    note: str = ""
    # [T-android-envvar-iso8601-wire] Epoch millis in memory, written as ISO string.
    createdAt: Iso8601Millis = 0

    @field_serializer("createdAt")
    def _ser_created(self, v: int) -> str:
        return _format_millis(v)


class BackupSecrets(BaseModel):
    """`secrets.json` — the ONE credential-carrying member."""

    model_config = ConfigDict(extra="ignore")
    v: int = 1
    providers: list["BackupSecrets.ProviderSecret"] = Field(default_factory=list)
    envVars: list["BackupSecrets.EnvVarSecret"] = Field(default_factory=list)
    mcpOAuth: list["BackupSecrets.MCPOAuthSecret"] = Field(default_factory=list)

    class ProviderSecret(BaseModel):
        model_config = ConfigDict(extra="ignore")
        instanceId: str
        label: Optional[str] = None
        providerType: Optional[str] = None
        apiKey: Optional[str] = None
        manualOAuthToken: Optional[str] = None
        oauthToken: Optional[str] = None
        oauthEmail: Optional[str] = None
        oauthGcpProject: Optional[str] = None

        @property
        def is_empty(self) -> bool:
            return (
                self.apiKey is None
                and self.manualOAuthToken is None
                and self.oauthToken is None
                and self.oauthEmail is None
                and self.oauthGcpProject is None
            )

    class EnvVarSecret(BaseModel):
        model_config = ConfigDict(extra="ignore")
        name: str
        value: str

    class MCPOAuthSecret(BaseModel):
        model_config = ConfigDict(extra="ignore")
        serverId: str
        token: str
        clientSecret: Optional[str] = None


class BackupException(Exception):
    def __init__(self, message: str, cause: BaseException | None = None) -> None:
        super().__init__(message)
        self.__cause__ = cause
