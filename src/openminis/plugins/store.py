"""[T-channels-plugins] 通道插件的仓库：安装 / 导入 / 卸载 / 配置读写。

目录约定（都在数据目录下，不动源码）：

    <data_dir>/plugins/<插件 id>/
        plugin.json     清单（随包发布或用户导入）
        config.json     用户配置 + _enabled 状态
        其他附带文件…

内置插件（引擎自带、开箱可用）住在包内 ``channels/builtin/<id>/``；「安装」
就是把内置那份复制进数据目录 —— 之后用户可以改配置、也可以卸载。

导入第三方插件包支持 zip 与目录：只接受带合法 ``plugin.json`` 的包，且
``driver`` 必须是引擎已注册的驱动（见 ``drivers/__init__.py``）。**插件包不
执行任意代码** —— 它只是配置与元信息，所以导入别人的包不会直接跑飞。
"""

from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Iterable

from ..core import context
from .manifest import (
    CATEGORY_CHANNEL,
    CONFIG_NAME,
    ENABLED_KEY,
    MANIFEST_NAME,
    RUNTIME_MANUAL,
    RUNTIME_PROCESS,
    ChannelManifest,
    ManifestError,
)

#: 插件 id 允许的字符（与 manifest 校验一致，这里再做一次防御）。
_ID_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_. ")


def plugins_dir() -> Path:
    """用户插件目录（不存在时创建）。"""
    path = context.app_context().data_dir / "plugins"
    path.mkdir(parents=True, exist_ok=True)
    return path


def builtin_dir() -> Path:
    """引擎内置插件目录（随包发布，只读）。"""
    return Path(__file__).resolve().parent / "builtin"


def _safe_id(plugin_id: str) -> str:
    pid = str(plugin_id or "").strip()
    if not pid or any(c not in _ID_OK for c in pid) or ".." in pid:
        raise ManifestError(f"插件 id 不合法：{plugin_id!r}")
    return pid


def _load_dir(path: Path, *, builtin: bool) -> ChannelManifest | None:
    try:
        return ChannelManifest.load(path, builtin=builtin)
    except ManifestError:
        return None


def list_builtin() -> list[ChannelManifest]:
    root = builtin_dir()
    if not root.is_dir():
        return []
    out: list[ChannelManifest] = []
    for child in sorted(root.iterdir()):
        if child.is_dir():
            manifest = _load_dir(child, builtin=True)
            if manifest is not None:
                out.append(manifest)
    return out


def list_installed() -> list[ChannelManifest]:
    root = plugins_dir()
    out: list[ChannelManifest] = []
    for child in sorted(root.iterdir()):
        if child.is_dir():
            manifest = _load_dir(child, builtin=False)
            if manifest is not None:
                out.append(manifest)
    return out


def list_all() -> list[ChannelManifest]:
    """已安装的 + 还没装的内置插件。"""
    installed = {m.id: m for m in list_installed()}
    out = list(installed.values())
    for manifest in list_builtin():
        if manifest.id not in installed:
            out.append(manifest)
    return out


def get(plugin_id: str) -> ChannelManifest | None:
    pid = str(plugin_id or "").strip()
    path = plugins_dir() / pid
    if path.is_dir():
        manifest = _load_dir(path, builtin=False)
        if manifest is not None:
            return manifest
    # 没装过的话看看是不是内置的（未安装状态也要能读清单渲染配置表单）
    builtin = builtin_dir() / pid
    if builtin.is_dir():
        manifest = _load_dir(builtin, builtin=True)
        if manifest is not None:
            return manifest
    return None


def is_installed(plugin_id: str) -> bool:
    return (plugins_dir() / str(plugin_id or "").strip()).is_dir()


# -- 安装 / 导入 / 卸载 -------------------------------------------------------
def _copy_builtin(source: Path, target: Path) -> None:
    """把内置插件的文件复制进去，但**绝不碰已有的 config.json**。

    配置是用户填的（AppID / 密钥 / 白名单），不该因为「更新了内置插件」就消失；
    包内那份 config.json（如果有）只当作没装过时的初始值。
    """
    target.mkdir(parents=True, exist_ok=True)
    for item in sorted(source.rglob("*")):
        rel = item.relative_to(source)
        dest = target / rel
        if item.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            continue
        if rel.name == CONFIG_NAME and dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, dest)


def install_builtin(plugin_id: str, *, refresh: bool = False) -> ChannelManifest:
    """把内置插件装进数据目录。

    已安装时默认什么都不做（幂等）；``refresh=True`` 会用包内那份覆盖清单 ——
    专门用来解决「包里升级了、数据目录那份还是旧的」：内置插件装过之后不会自动
    跟着升级，于是新加的配置项在界面上永远不出现，很难自己反应过来。配置照旧保留。
    """
    pid = _safe_id(plugin_id)
    source = builtin_dir() / pid
    if not source.is_dir():
        raise ManifestError(f"没有内置插件：{pid}")
    target = plugins_dir() / pid
    if target.is_dir() and not refresh:
        manifest = _load_dir(target, builtin=False)
        if manifest is None:
            raise ManifestError(f"内置插件 {pid} 的清单不合法")
        return manifest
    _copy_builtin(source, target)
    manifest = _load_dir(target, builtin=False)
    if manifest is None:
        raise ManifestError(f"内置插件 {pid} 的清单不合法")
    return manifest


def builtin_outdated(plugin_id: str) -> bool:
    """数据目录那份的清单和包内的不一致吗（= 内置插件升级过了）。"""
    pid = _safe_id(plugin_id)
    source = builtin_dir() / pid / MANIFEST_NAME
    target = plugins_dir() / pid / MANIFEST_NAME
    if not source.is_file() or not target.is_file():
        return False
    try:
        return source.read_bytes() != target.read_bytes()
    except OSError:  # pragma: no cover - 读不动就当没差异，别拿它挡启动
        return False


def import_plugin(source: str | Path) -> ChannelManifest:
    """导入插件包：``.zip`` 或插件目录。

    包里没有 ``plugin.json`` 也能导入 —— 会按项目类型推断一份（Node 的
    package.json / Python 的入口文件），写进插件目录供后续手改。这样
    「导入 dsh 这类现成的桥接项目」不需要对方先支持我们这套清单。

    推断不出来的包也能导入（``runtime=manual``）：能在插件页看到、能删，
    引擎不去启停它。
    """
    src = Path(source)
    if not (src.is_file() or src.is_dir()):
        raise ManifestError(f"找不到这个插件包：{src}")
    staging = _staging_dir()
    payload: Path | None = None
    target: Path | None = None
    try:
        payload = _unpack(src, staging)
        manifest = _ensure_manifest(payload)
        target = plugins_dir() / _safe_id(manifest.id)
        if target.is_dir():
            shutil.rmtree(target)
        shutil.move(str(payload), str(target))
    except Exception:
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    assert target is not None
    return _reload(target)


def _staging_dir() -> Path:
    """导入先落临时目录：装一半失败不会在插件目录里留个半成品。"""
    root = plugins_dir() / ".staging"
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="import-", dir=str(root)))


def _unpack(src: Path, staging: Path) -> Path:
    """把 zip / 目录摊到临时目录，返回「插件根」（含清单或项目文件的那层）。"""
    payload = staging / "payload"
    if src.is_dir():
        shutil.copytree(src, payload)
    else:
        if src.suffix.lower() != ".zip":
            raise ManifestError(f"插件包要是 .zip 或目录：{src}")
        payload.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(src) as zf:
                for name in zf.namelist():
                    pure = Path(name)
                    if pure.is_absolute() or ".." in pure.parts:
                        raise ManifestError(f"插件包里有非法路径：{name}")
                zf.extractall(payload)
        except zipfile.BadZipFile as exc:
            raise ManifestError(f"不是合法的 zip：{exc}") from exc
    _strip_macos(payload)
    return _descend(payload)


def _strip_macos(root: Path) -> None:
    """删掉 macOS 打包时塞进来的 __MACOSX 与 ._ 资源分叉。"""
    for child in list(root.iterdir()):
        if child.name == "__MACOSX" or child.name.startswith("._"):
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)


def _looks_like_root(path: Path) -> bool:
    markers = (MANIFEST_NAME, "package.json", "pyproject.toml", "requirements.txt")
    if any((path / m).is_file() for m in markers):
        return True
    return any((path / n).is_file() for n in ("index.js", "main.py", "app.py", "__main__.py"))


def _descend(root: Path) -> Path:
    """zip 常见套一层（dsh-bridge-main/），往里走一层再找根。"""
    current = root
    for _ in range(2):
        if _looks_like_root(current):
            return current
        children = [c for c in current.iterdir() if c.is_dir() and not c.name.startswith(".")]
        if len(children) != 1:
            return current
        current = children[0]
    return current


def _ensure_manifest(base: Path) -> ChannelManifest:
    """读清单；没有就推断一份写进去（返回带 path 的清单）。"""
    if (base / MANIFEST_NAME).is_file():
        return _with_path(ChannelManifest.load(base), base)
    raw = infer_manifest(base)
    (base / MANIFEST_NAME).write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return ChannelManifest.from_dict(raw, path=base, builtin=False)


def _with_path(manifest: ChannelManifest, base: Path) -> ChannelManifest:
    return ChannelManifest.from_dict(
        {
            "id": manifest.id,
            "name": manifest.name,
            "icon": manifest.icon,
            "version": manifest.version,
            "driver": manifest.driver,
            "description": manifest.description,
            "author": manifest.author,
            "category": manifest.category,
            "runtime": manifest.runtime,
            "process": manifest.process,
            "fields": [f.to_dict() for f in manifest.fields],
            "tools": [t.to_dict() for t in manifest.tools],
        },
        path=base,
        builtin=False,
    )


#: 名字里带这些词的多半是通道/桥接类插件 —— 推断时给个更合适的图标与分组。
_CHANNEL_HINTS = ("bridge", "bot", "qq", "wechat", "weixin", "feishu", "lark", "telegram", "im")


def infer_manifest(base: Path) -> dict[str, Any]:
    """没有清单时，按项目类型推断一份。

    只做**保守**的推断：能看出启动入口就填进 ``process``；看不出来就
    ``runtime=manual``（仅登记，不启停）。
    """
    pkg_file = base / "package.json"
    if pkg_file.is_file():
        try:
            pkg = json.loads(pkg_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pkg = {}
        if isinstance(pkg, dict):
            raw = _from_package_json(base, pkg)
            if raw is not None:
                return raw

    entry = _python_entry(base)
    if entry is not None:
        return _from_python_entry(base, entry)

    return {
        "id": _slug(base.name),
        "name": base.name,
        "icon": "📦",
        "version": "0.0.0",
        "category": "other",
        "runtime": RUNTIME_MANUAL,
        "description": "没能识别出启动入口，仅登记。可以手动补一份 plugin.json 让它可启停。",
        "fields": [],
    }


def _from_package_json(base: Path, pkg: dict[str, Any]) -> dict[str, Any] | None:
    name = str(pkg.get("name") or base.name)
    short = name.split("/")[-1]
    entry = _node_entry(base, pkg)
    desc = str(pkg.get("description") or "")
    category = (
        CATEGORY_CHANNEL
        if any(hint in f"{short} {desc}".lower() for hint in _CHANNEL_HINTS)
        else "other"
    )
    raw: dict[str, Any] = {
        "id": _slug(short),
        "name": short,
        "icon": "🧩",
        "version": str(pkg.get("version") or "0.0.0"),
        "category": category,
        "description": desc or f"从 package.json 推断（{short}）",
        "fields": [],
    }
    if entry is None:
        raw["runtime"] = RUNTIME_MANUAL
        raw["description"] = f"{desc}（没找到可执行入口，仅登记）".strip()
        return raw
    raw["runtime"] = RUNTIME_PROCESS
    raw["process"] = {
        "command": "node",
        "args": [entry],
        "cwd": ".",
        "autoRestart": True,
    }
    return raw


def _node_entry(base: Path, pkg: dict[str, Any]) -> str | None:
    """找启动脚本：package.json 的 main / bin，其次常见的入口文件名。"""
    main = pkg.get("main")
    if isinstance(main, str) and (base / main).is_file():
        return main
    bin_field = pkg.get("bin")
    if isinstance(bin_field, str) and (base / bin_field).is_file():
        return bin_field
    if isinstance(bin_field, dict):
        for value in bin_field.values():
            if isinstance(value, str) and (base / value).is_file():
                return value
    for candidate in ("index.js", "index.mjs", "index.cjs", "main.js", "server.js"):
        if (base / candidate).is_file():
            return candidate
    return None


def _python_entry(base: Path) -> Path | None:
    for candidate in ("main.py", "app.py", "server.py", "__main__.py", "run.py"):
        if (base / candidate).is_file():
            return base / candidate
    return None


def _from_python_entry(base: Path, entry: Path) -> dict[str, Any]:
    short = base.name
    desc = f"从 {entry.name} 推断"
    return {
        "id": _slug(short),
        "name": short,
        "icon": "🐍",
        "version": "0.0.0",
        "category": "other",
        "description": desc,
        "runtime": RUNTIME_PROCESS,
        "process": {
            "command": "python",
            "args": [entry.name],
            "cwd": ".",
            "autoRestart": True,
        },
        "fields": [],
    }


def _slug(name: str) -> str:
    out = []
    for ch in str(name or "").strip():
        if ch.isalnum() or ch in "-_.":
            out.append(ch)
        elif ch in " \t":
            out.append("-")
    slug = "".join(out).strip("-.").lower()
    return slug or "plugin"


def _reload(target: Path) -> ChannelManifest:
    manifest = _load_dir(target, builtin=False)
    if manifest is None:
        raise ManifestError(f"插件清单不合法：{target / MANIFEST_NAME}")
    return manifest


def remove(plugin_id: str) -> bool:
    """卸载（连配置一起删）。路径必须落在插件根目录里。"""
    pid = _safe_id(plugin_id)
    target = plugins_dir() / pid
    root = plugins_dir().resolve()
    try:
        resolved = target.resolve()
    except OSError:
        return False
    if root not in resolved.parents or not target.is_dir():
        return False
    shutil.rmtree(target)
    return True


# -- 配置 ---------------------------------------------------------------------
def config_path(plugin_id: str) -> Path:
    return plugins_dir() / _safe_id(plugin_id) / CONFIG_NAME


def read_config(plugin_id: str) -> dict[str, Any]:
    path = config_path(plugin_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def write_config(plugin_id: str, values: dict[str, Any]) -> dict[str, Any]:
    """合并写入配置。密钥字段传空字符串 = 沿用旧值（前端不回传密钥）。"""
    pid = _safe_id(plugin_id)
    manifest = get(pid)
    if manifest is None:
        raise ManifestError(f"没有这个插件：{pid}")
    merged = read_config(pid)
    for spec in manifest.fields:
        if spec.key not in values:
            continue
        value = values[spec.key]
        if spec.secret and isinstance(value, str) and not value.strip():
            continue  # 空 = 不改
        merged[spec.key] = _coerce(spec, value)
    path = config_path(pid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return merged


def _coerce(spec: Any, value: Any) -> Any:
    """按清单里的类型收口 —— 配置写进去之前必须是引擎能直接用的形状。"""
    if spec.type in ("number",):
        try:
            num = int(value)
        except (TypeError, ValueError):
            num = int(spec.default or 0)
        if spec.minimum is not None:
            num = max(num, spec.minimum)
        if spec.maximum is not None:
            num = min(num, spec.maximum)
        return num
    if spec.type == "switch":
        return bool(value)
    if spec.type == "list":
        if isinstance(value, list):
            items = [str(v).strip() for v in value]
        else:
            items = [line.strip() for line in str(value or "").splitlines()]
        return [v for v in items if v]
    if spec.type == "csv":
        if isinstance(value, list):
            items = [str(v).strip() for v in value]
        else:
            items = [chunk.strip() for chunk in str(value or "").split(",")]
        return [v for v in items if v]
    return value if value is None else str(value)


def public_config(plugin_id: str) -> dict[str, Any]:
    """给界面看的配置：密钥只回「是否已填」，不回明文。"""
    manifest = get(plugin_id)
    raw = read_config(plugin_id)
    if manifest is None:
        return {}
    secret_keys = set(manifest.secrets)
    out: dict[str, Any] = {}
    for spec in manifest.fields:
        value = raw.get(spec.key, spec.default)
        if spec.key in secret_keys:
            out[spec.key] = ""
            out[f"{spec.key}__set"] = bool(str(raw.get(spec.key) or "").strip())
            continue
        if value is None:
            value = [] if spec.type in ("list", "csv") else ""
        out[spec.key] = value
    return out


def is_enabled(plugin_id: str) -> bool:
    return bool(read_config(plugin_id).get(ENABLED_KEY))


def set_enabled(plugin_id: str, enabled: bool) -> None:
    pid = _safe_id(plugin_id)
    cfg = read_config(pid)
    cfg[ENABLED_KEY] = bool(enabled)
    path = config_path(pid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def missing_required(plugin_id: str) -> list[str]:
    """必填但还没填的字段（启动前拦一道，省得连不上还找不到原因）。"""
    manifest = get(plugin_id)
    if manifest is None:
        return []
    raw = read_config(plugin_id)
    lack: list[str] = []
    for spec in manifest.fields:
        if not spec.required:
            continue
        value = raw.get(spec.key)
        if value is None or (isinstance(value, str) and not value.strip()):
            lack.append(spec.label or spec.key)
    return lack


__all__ = [
    "plugins_dir",
    "builtin_dir",
    "list_all",
    "list_builtin",
    "list_installed",
    "get",
    "is_installed",
    "install_builtin",
    "builtin_outdated",
    "import_plugin",
    "remove",
    "read_config",
    "write_config",
    "public_config",
    "is_enabled",
    "set_enabled",
    "missing_required",
    "config_path",
]
