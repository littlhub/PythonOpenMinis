"""Local offline speech-to-text core (sherpa-onnx streaming zipformer).

Ported from: G:\Ekko\语音识别\stt\stt_core.py
Original: sherpa-onnx (Apache-2.0) by k2-fsa
Adapted to OpenMinis app context for model path resolution.

Provides:
- Model discovery (follows the same search order as the standalone program)
- WAV parsing (supports most PCM formats and WAVE_EXTENSIBLE)
- Resampling (audioop.ratecv with linear interpolation fallback)
- Single-file transcription + streaming session for microphone
"""

from __future__ import annotations

import os
import struct
import subprocess
import tarfile
import threading
import time
import urllib.error
import urllib.request
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from ..core.context import app_context
from ..core.logging import get_logger

logger = get_logger(__name__)

# The engine deps are an optional extra (``pip install "openminis[speech]"``) —
# they pull ~200 MB of ONNX runtime, and most installs never record anything.
# Importing must therefore stay non-fatal: the router is mounted unconditionally
# so the UI can ask ``/api/speech/status`` and show a "install the model" hint.
try:  # pragma: no cover - depends on the install
    import numpy as np
    import sherpa_onnx
except ImportError as _import_error:  # pragma: no cover
    np = None  # type: ignore[assignment]
    sherpa_onnx = None  # type: ignore[assignment]
    _ENGINE_IMPORT_ERROR: Optional[ImportError] = _import_error
else:
    _ENGINE_IMPORT_ERROR = None


def require_engine() -> None:
    """Raise a readable :class:`SttError` when the optional extra is missing."""
    if _ENGINE_IMPORT_ERROR is not None:
        raise SttError(
            '本地语音识别依赖未安装。装一下：pip install "openminis[speech]"\n'
            f"（缺少：{_ENGINE_IMPORT_ERROR}）"
        )

# ---------------------------------------------------------------- Constants

MODEL_ID = "sherpa-onnx-streaming-zipformer-bilingual-zh-en-int8-2023-02-20"

#: 官方发布用的名字：GitHub release 的压缩包、HF 仓库、包内根目录都**不带** ``-int8-``。
#: 包里 encoder / joiner 同时给了 ``.onnx`` 和 ``.int8.onnx`` 两套，我们要的是 int8 那套。
#: 用 MODEL_ID 拼下载地址会 404，所以下载路径一律用这个常量。
MODEL_ARCHIVE_ID = "sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20"

#: Required model files -> expected byte count (for integrity checking)
MODEL_FILES = {
    "encoder-epoch-99-avg-1.int8.onnx": 181_895_032,
    "decoder-epoch-99-avg-1.onnx": 13_876_452,
    "joiner-epoch-99-avg-1.int8.onnx": 3_228_404,
    "tokens.txt": 56_317,
}

SAMPLE_RATE = 16_000
FEATURE_DIM = 80
FINAL_FLUSH_SILENCE_SECONDS = 0.8


class SttError(RuntimeError):
    """Base class for STT-related errors."""


class ModelNotFoundError(SttError):
    """No usable STT model directory found."""


class ModelDownloadError(SttError):
    """下载 / 解压 / 校验模型失败（消息已经是给人看的中文）。"""


def managed_model_dir() -> Path:
    """设置页「一键下载」装到这里：``<data_dir>/models/<MODEL_ID>``。"""
    return app_context().data_dir / "models" / MODEL_ID


def bundled_model_dirs() -> List[Path]:
    """打包后的 exe 旁边放了模型吗（``<exe>/models/<MODEL_ID>``）？"""
    import sys

    out: List[Path] = []
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
        out.append(root / "models" / MODEL_ID)
        out.append(root / "models")
    return out


class AudioDecodeError(SttError):
    """Failed to decode the audio file."""


# ---------------------------------------------------------------- Model path resolution

def model_candidates(
    explicit: Optional[str] = None, extra: Optional[Sequence[Path]] = None
) -> List[Path]:
    """List possible model directories in priority order.

    Search order:
      0. ``extra`` (caller-supplied, e.g. the staging dir of a download in
         progress — so already-arrived files are not fetched twice)
      1. Explicit path (if given)
      2. Environment variables: EKKO_STT_MODEL_DIR / HERMES_LOCAL_STT_MODEL_DIR / STT_MODEL_DIR
      3. models/ next to the packaged exe (if any)
      4. app_context().data_dir / models / MODEL_ID (OpenMinis local data)
      4. G:\Ekko\语音识别\models\ (original standalone installation adjacent)
      5. ~/.hermes-web-ui/models/MODEL_ID (Ekko Studio downloaded copy)
      6. ~/.hermes/models/MODEL_ID
      7. Current working directory / models / MODEL_ID
    """
    out: List[Path] = []
    if extra:
        out.extend(Path(p) for p in extra)

    if explicit:
        out.append(Path(explicit))

    for env_name in ("EKKO_STT_MODEL_DIR", "HERMES_LOCAL_STT_MODEL_DIR", "STT_MODEL_DIR"):
        value = os.environ.get(env_name, "").strip()
        if value:
            out.append(Path(value))

    # Next to the packaged exe (build.bat copies the model there when present)
    out.extend(bundled_model_dirs())

    # OpenMinis own data directory — also where the settings page downloads to
    out.append(managed_model_dir())

    # The standalone program shipped alongside this checkout (``G:\Ekko\语音识别``).
    # Walk up instead of counting parents so the search survives repo nesting.
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "语音识别" / "models" / MODEL_ID
        if candidate.is_dir():
            out.append(candidate)
            break

    # Ekko Studio locations (common if user already downloaded it there)
    out.append(Path.home() / ".hermes-web-ui" / "models" / MODEL_ID)
    out.append(Path.home() / ".hermes" / "models" / MODEL_ID)

    # Fallback to cwd
    out.append(Path.cwd() / "models" / MODEL_ID)

    # Deduplicate while preserving order
    deduped: List[Path] = []
    seen = set()
    for path in out:
        try:
            resolved = path.expanduser().resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(resolved)
    return deduped


def _has_model_files(directory: Path) -> bool:
    return all((directory / name).is_file() for name in MODEL_FILES)


def resolve_model_dir(explicit: Optional[str] = None) -> Path:
    """Locate the model directory, raises ModelNotFoundError if none found."""
    tried: List[str] = []
    for candidate in model_candidates(explicit):
        tried.append(str(candidate))
        if _has_model_files(candidate):
            return candidate
        nested = candidate / MODEL_ID
        if _has_model_files(nested):
            return nested

    if explicit:
        raise ModelNotFoundError(
            f"指定的模型目录不可用：{explicit}\n"
            f"该目录下需要有：{', '.join(MODEL_FILES)}"
        )

    listing = "\n".join(f"  - {item}" for item in tried)
    raise ModelNotFoundError(
        "没有找到本地语音识别模型。\n"
        f"已尝试以下位置：\n{listing}\n\n"
        "解决办法（任选其一）：\n"
        "  1. 在「设置 → 语音识别」里点「下载模型」（约 195 MB）；\n"
        f"  2. 把模型目录 {MODEL_ID} 放到 {app_context().data_dir / 'models'} 下；\n"
        "  3. 设置环境变量 EKKO_STT_MODEL_DIR 指向模型目录；\n"
        "  4. 在 Ekko Studio 里下载过本地 STT 模型的话，它就在"
        f" {Path.home() / '.hermes-web-ui' / 'models' / MODEL_ID}"
    )


@dataclass(frozen=True)
class ModelPaths:
    root: Path
    encoder: Path
    decoder: Path
    joiner: Path
    tokens: Path

    @classmethod
    def load(cls, root: Path) -> "ModelPaths":
        return cls(
            root=root,
            encoder=root / "encoder-epoch-99-avg-1.int8.onnx",
            decoder=root / "decoder-epoch-99-avg-1.onnx",
            joiner=root / "joiner-epoch-99-avg-1.int8.onnx",
            tokens=root / "tokens.txt",
        )


def model_status(explicit: Optional[str] = None) -> dict:
    """Return model status as a dict, no exceptions thrown — good for UI checks."""
    try:
        root = resolve_model_dir(explicit)
    except ModelNotFoundError as error:
        return {
            "ok": False,
            "error": str(error),
            "root": None,
            "files": {},
            "warnings": [],
            "engineReady": _ENGINE_IMPORT_ERROR is None,
        }

    paths = ModelPaths.load(root)
    files = {}
    warnings: List[str] = []
    for name, expected in MODEL_FILES.items():
        path = root / name
        actual = path.stat().st_size if path.is_file() else 0
        files[name] = actual
        if actual != expected:
            warnings.append(f"{name} 大小为 {actual}，官方为 {expected}（仍可尝试使用）")
    if _ENGINE_IMPORT_ERROR is not None:
        warnings.append(f"语音依赖未安装：{_ENGINE_IMPORT_ERROR}")

    return {
        "ok": True,
        "root": str(root),
        "files": files,
        "warnings": warnings,
        "engineReady": _ENGINE_IMPORT_ERROR is None,
        "sherpa_onnx_version": (
            getattr(sherpa_onnx, "__version__", "unknown")
            if sherpa_onnx is not None
            else "missing"
        ),
    }


# ---------------------------------------------------------------- 模型下载

#: 官方发布（GitHub releases，~487 MB 的 tar.bz2，下完只留 4 个文件）。
#: 按顺序尝试：GitHub 直连 → GitHub 加速镜像 → Hugging Face 镜像。
#: 注意压缩包名是 ``MODEL_ARCHIVE_ID``（不带 -int8），后者 404 过很多次。
MODEL_DOWNLOAD_SOURCES: Tuple[Tuple[str, str], ...] = (
    (
        "github",
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
        f"{MODEL_ARCHIVE_ID}.tar.bz2",
    ),
    (
        "ghproxy",
        "https://gh-proxy.com/https://github.com/k2-fsa/sherpa-onnx/releases/download/"
        f"asr-models/{MODEL_ARCHIVE_ID}.tar.bz2",
    ),
    (
        "ghproxy-net",
        "https://ghproxy.net/https://github.com/k2-fsa/sherpa-onnx/releases/download/"
        f"asr-models/{MODEL_ARCHIVE_ID}.tar.bz2",
    ),
    (
        # HF 镜像按文件直取（199 MB，比 487 MB 的压缩包省一半多）。仓库名同样
        # 不带 -int8；带 -int8 的仓库在镜像上 404。
        "hf-mirror",
        "https://hf-mirror.com/csukuangfj/"
        f"{MODEL_ARCHIVE_ID}/resolve/main/",
    ),
)

#: 只有这 4 个文件是引擎真正要的；压缩包里其余（非 int8 权重、test_wavs、
#: export 脚本……）解出来就删，不然白占 500 MB。
MODEL_KEEP = tuple(MODEL_FILES) + ("bpe.model",)

_DOWNLOAD_TOTAL_BYTES = sum(MODEL_FILES.values())


@dataclass
class DownloadState:
    """一次模型下载的进度，给设置页轮询用。"""

    phase: str = "idle"  # idle / downloading / extracting / done / error
    message: str = ""
    source: str = ""
    received: int = 0
    total: int = 0
    error: Optional[str] = None
    target: Optional[str] = None
    started_at: float = 0.0
    finished_at: float = 0.0
    # 速度采样的私有状态，不出现在 as_dict 里。
    speed_hint: float = 0.0
    _last_sample_at: float = 0.0
    _last_sample_received: int = 0

    def as_dict(self) -> dict:
        # 速度按「上一次采样后的增量」算，而不是总字节 / 总耗时：中间悬停
        # 一分钟再续上时，后者会把速度画得很低。调用方（后台线程）顺带刷新。
        now = time.time()
        elapsed = (self.finished_at or now) - self.started_at if self.started_at else 0.0
        if self._last_sample_at and now - self._last_sample_at >= 0.5:
            delta = self.received - self._last_sample_received
            speed = delta / (now - self._last_sample_at)
            self._last_sample_at = now
            self._last_sample_received = self.received
        elif self.speed_hint:
            speed = self.speed_hint
        else:
            speed = self.received / elapsed if elapsed > 0.5 else 0.0
        if speed > 0:
            self.speed_hint = speed
        percent = round(self.received * 100 / self.total, 1) if self.total else 0.0
        return {
            "phase": self.phase,
            "message": self.message,
            "source": self.source,
            "received": self.received,
            "total": self.total,
            "percent": min(percent, 100.0),
            "speedBytesPerSec": round(speed),
            "error": self.error,
            "target": self.target,
            "elapsedMs": round(elapsed * 1000),
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
        }


_download_state = DownloadState()
_download_lock = threading.Lock()


def download_state() -> dict:
    with _download_lock:
        return _download_state.as_dict()


def model_download_target() -> Path:
    return managed_model_dir()


def _install_dir_ready(directory: Path) -> bool:
    return all(
        (directory / name).is_file() and (directory / name).stat().st_size == size
        for name, size in MODEL_FILES.items()
    )


def _is_model_ready() -> bool:
    try:
        resolve_model_dir()
    except ModelNotFoundError:
        return False
    except Exception:  # noqa: BLE001
        return False
    return True


def _open_partial(url: str, offset: int) -> Tuple[object, int]:
    """打开 url；offset > 0 时带 Range 续传。返回 (响应, 整包总长度或 0)。"""
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "OpenMinis-speech/1.0", "Accept": "*/*"},
    )
    if offset:
        request.add_header("Range", f"bytes={offset}-")
    # 30 秒是连接 / 单次读取的超时，不是整包超时：200 MB 在慢网上一分钟只走
    # 20 MB，设成整体超时会把好下载掐死。
    response = urllib.request.urlopen(request, timeout=30)
    length = int(response.headers.get("Content-Length") or 0)
    if offset and response.status == 206:
        length += offset
    elif offset:
        # 服务器不认 Range：只能从头再来。
        offset = 0
    return response, length


def _download_to(url: str, target: Path, state: DownloadState) -> None:
    """流式下载到 target，中途更新进度；断流就从断点续传（最多 5 次）。"""
    import http.client
    import socket

    state.received = 0
    state.message = "下载中"
    attempts = 0
    # 连接 / TLS 握手本身也可能超时（国内访问 hf-mirror 偶尔会），重试几次
    # 再判定这个源失败；已经收到数据的重试交给下面的续传逻辑。
    for attempt in range(4):
        try:
            response, length = _open_partial(url, 0)
            break
        except (urllib.error.URLError, OSError) as error:
            if attempt == 3:
                raise
            logger.warning("stt download connect failed (%d): %s", attempt + 1, error)
            time.sleep(2.0 * (attempt + 1))
    if length:
        state.total = length
    try:
        with target.open("wb") as handle:
            while True:
                try:
                    chunk = response.read(1 << 20)
                except (http.client.IncompleteRead, socket.timeout, ConnectionError) as error:
                    partial = getattr(error, "partial", b"") or b""
                    if partial:
                        handle.write(partial)
                        state.received += len(partial)
                    if state.received and (not length or state.received >= length):
                        break
                    attempts += 1
                    if attempts > 5:
                        raise
                    state.message = f"断流，续传中（第 {attempts} 次）"
                    logger.warning("stt download stalled at %d bytes: %s", state.received, error)
                    response.close()
                    time.sleep(1.5 * attempts)
                    try:
                        response, length = _open_partial(url, state.received)
                    except (urllib.error.URLError, OSError) as reopen_error:
                        # 续传的连接也没建起来：算一次尝试，继续循环重试。
                        logger.warning("stt resume failed: %s", reopen_error)
                        continue
                    if state.received == 0:
                        handle.seek(0)
                        handle.truncate()
                    if length:
                        state.total = length
                    continue
                if not chunk:
                    break
                handle.write(chunk)
                state.received += len(chunk)
    finally:
        response.close()
    if length and state.received != length:
        raise ModelDownloadError(
            f"下载不完整：收到 {state.received} / {length} 字节，换个下载源重试"
        )


def _extract_tar(source: Path, target: Path, state: DownloadState) -> None:
    state.phase = "extracting"
    state.message = "解压中"
    state.received = state.total = _DOWNLOAD_TOTAL_BYTES  # 进度条走满，下面只换文字
    target.mkdir(parents=True, exist_ok=True)
    # 压缩包根目录是 MODEL_ARCHIVE_ID（不带 -int8）；也容忍平铺（没有子目录）
    # 和以前用 MODEL_ID 解出来的目录，免得老的手工包失效。
    roots = {MODEL_ID, MODEL_ARCHIVE_ID}
    with tarfile.open(source, "r:bz2") as archive:
        members = [
            m
            for m in archive.getmembers()
            if m.isfile()
            and m.name.split("/")[-1] in MODEL_KEEP
            and (m.name.split("/")[0] in roots or "/" not in m.name)
        ]
        if not members:
            raise ModelDownloadError("压缩包里没有模型文件，可能下载源给的是错误页面")
        for index, member in enumerate(members, 1):
            state.message = f"解压中 {index}/{len(members)} {member.name.split('/')[-1]}"
            stream = archive.extractfile(member)
            if stream is None:
                continue
            destination = target / Path(member.name).name
            partial = destination.with_name(destination.name + ".part")
            with partial.open("wb") as out, stream:
                while True:
                    chunk = stream.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
            partial.replace(destination)


def _download_from_hf(target: Path, state: DownloadState) -> None:
    base = dict(MODEL_DOWNLOAD_SOURCES)["hf-mirror"]
    total = sum(MODEL_FILES.values())
    state.total = total
    for name, size in MODEL_FILES.items():
        destination = target / name
        if destination.is_file() and destination.stat().st_size == size:
            state.received += size
            continue
        state.message = f"下载中 {name}"
        _download_to(base + name, destination, state)


def _download_from_archive(url: str, label: str, target: Path, state: DownloadState) -> None:
    import tempfile

    state.source = label
    state.message = f"下载中（{label}，压缩包约 490 MB）"
    with tempfile.TemporaryDirectory(prefix="openminis-stt-") as scratch:
        archive_path = Path(scratch) / f"{MODEL_ARCHIVE_ID}.tar.bz2"
        _download_to(url, archive_path, state)
        _extract_tar(archive_path, target, state)


def _download_worker(state: DownloadState, sources: Sequence[str]) -> None:
    target = model_download_target()
    partial_root = target.with_name(target.name + ".download")
    errors: List[str] = []
    sources_by_label = dict(MODEL_DOWNLOAD_SOURCES)

    # 严格按调用方给的顺序试（默认顺序见 MODEL_DOWNLOAD_SOURCES）：压缩包源
    # 和 HF 按文件取源是同一条流水线里的两种形态，谁先谁后由列表说话。
    for label in sources:
        url = sources_by_label.get(label)
        if not url:
            logger.warning("stt model download: unknown source %r", label)
            errors.append(f"{label}: 未知下载源")
            continue
        try:
            # 不清空暂存目录：同一台机器上断了重来，已到手的文件不用再下一个
            # 490 MB 的压缩包。
            partial_root.mkdir(parents=True, exist_ok=True)
            if _install_dir_ready(partial_root):
                _finalize(partial_root, target, state)
                return
            state.phase = "downloading"
            state.source = label
            state.received = 0
            state.total = 0
            if label == "hf-mirror":
                _download_from_hf(partial_root, state)
            else:
                _download_from_archive(url, label, partial_root, state)
            _finalize(partial_root, target, state)
            return
        except (ModelDownloadError, OSError, tarfile.TarError, urllib.error.URLError) as error:
            logger.warning("stt model download via %s failed: %s", label, error)
            errors.append(f"{label}: {error}")

    # 暂存目录留着：下次点「下载」只补缺的文件。
    state.phase = "error"
    state.message = "下载失败"
    state.error = (
        "所有下载源都失败了：\n  " + "\n  ".join(errors[-3:]) + "\n"
        "可以挂代理后重试，或者手动把模型目录放进：\n  " + str(target)
    )
    state.finished_at = time.time()


def _finalize(partial_root: Path, target: Path, state: DownloadState) -> None:
    import shutil

    state.phase = "extracting"
    state.message = "校验中"
    if not _install_dir_ready(partial_root):
        sizes = {}
        for name in MODEL_FILES:
            path = partial_root / name
            if path.is_file():
                sizes[name] = f"{path.stat().st_size}/{MODEL_FILES[name]}"
        raise ModelDownloadError(
            "模型文件不完整（大小对不上）：" + (", ".join(f"{k} {v}" for k, v in sizes.items()) or "没有文件")
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = target.with_name(target.name + ".old")
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
    if target.exists():
        target.rename(backup)
    partial_root.rename(target)
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)

    state.phase = "done"
    state.message = "下载完成"
    state.received = state.total = _DOWNLOAD_TOTAL_BYTES
    state.target = str(target)
    state.finished_at = time.time()
    logger.info("stt model installed to %s", target)


def start_model_download(sources: Optional[Sequence[str]] = None) -> dict:
    """后台下载模型，立刻返回进度快照。重复调用返回当前进度。"""
    with _download_lock:
        global _download_state
        if _download_state.phase in ("downloading", "extracting"):
            return _download_state.as_dict()

        chosen = list(sources or [label for label, _ in MODEL_DOWNLOAD_SOURCES])
        _download_state = DownloadState(
            phase="downloading",
            message="准备下载",
            total=0,
            started_at=time.time(),
            target=str(model_download_target()),
        )
        state = _download_state

    try:
        from .engine import reset_engine

        reset_engine()
    except Exception:  # noqa: BLE001 - engine may not be imported yet
        pass

    thread = threading.Thread(
        target=_download_worker,
        args=(state, chosen),
        name="stt-model-download",
        daemon=True,
    )
    thread.start()
    return state.as_dict()


# ---------------------------------------------------------------- Audio decoding

def _parse_riff(data: bytes) -> Tuple[np.ndarray, int]:
    """Parse RIFF/WAV bytes, return (float32 mono samples, original sample rate)."""
    require_engine()
    if len(data) < 12 or data[0:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise AudioDecodeError("不是有效的 RIFF/WAVE 文件")

    position = 12
    fmt_chunk: Optional[bytes] = None
    pcm_chunk: Optional[bytes] = None

    while position + 8 <= len(data):
        chunk_id = data[position:position + 4]
        chunk_size = struct.unpack("<I", data[position + 4:position + 8])[0]
        body = data[position + 8:position + 8 + chunk_size]
        if chunk_id == b"fmt ":
            fmt_chunk = body
        elif chunk_id == b"data":
            pcm_chunk = body
        position += 8 + chunk_size + (chunk_size & 1)  # 2-byte alignment

    if fmt_chunk is None or len(fmt_chunk) < 16:
        raise AudioDecodeError("WAV 缺少 fmt 块")
    if pcm_chunk is None:
        raise AudioDecodeError("WAV 缺少 data 块")

    audio_format, channels, sample_rate = struct.unpack("<HHI", fmt_chunk[:8])
    bits = struct.unpack("<H", fmt_chunk[14:16])[0]

    # WAVE_FORMAT_EXTENSIBLE: actual format is in the first two bytes of SubFormat
    if audio_format == 0xFFFE and len(fmt_chunk) >= 26:
        audio_format = struct.unpack("<H", fmt_chunk[24:26])[0]

    if channels < 1:
        raise AudioDecodeError("WAV 声道数无效")

    if audio_format == 1:  # PCM integer
        if bits == 8:
            samples = (np.frombuffer(pcm_chunk, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        elif bits == 16:
            samples = np.frombuffer(pcm_chunk, dtype="<i2").astype(np.float32) / 32768.0
        elif bits == 24:
            raw = np.frombuffer(pcm_chunk, dtype=np.uint8)
            usable = (raw.size // 3) * 3
            triples = raw[:usable].reshape(-1, 3).astype(np.int32)
            values = triples[:, 0] | (triples[:, 1] << 8) | (triples[:, 2] << 16)
            values = np.where(values >= (1 << 23), values - (1 << 24), values)
            samples = values.astype(np.float32) / 8388608.0
        elif bits == 32:
            samples = np.frombuffer(pcm_chunk, dtype="<i4").astype(np.float32) / 2147483648.0
        else:
            raise AudioDecodeError(f"不支持的 PCM 位深：{bits}")
    elif audio_format == 3:  # IEEE floating point
        if bits == 32:
            samples = np.frombuffer(pcm_chunk, dtype="<f4").astype(np.float32)
        elif bits == 64:
            samples = np.frombuffer(pcm_chunk, dtype="<f8").astype(np.float32)
        else:
            raise AudioDecodeError(f"不支持的浮点位深：{bits}")
    else:
        raise AudioDecodeError(f"不支持的 WAV 编码格式：{audio_format}")

    if channels > 1:
        # Downmix to mono
        usable = (samples.size // channels) * channels
        samples = samples[:usable].reshape(-1, channels).mean(axis=1)

    return np.ascontiguousarray(samples, dtype=np.float32), int(sample_rate)


def resample(samples: np.ndarray, source_rate: int, target_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Resample to target sample rate. Uses audioop.ratecv with anti-aliasing when available,
    falls back to linear interpolation otherwise."""
    require_engine()
    if source_rate == target_rate or samples.size == 0:
        return np.ascontiguousarray(samples, dtype=np.float32)

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            import audioop  # Python <= 3.12, still available in most installations

        clipped = np.clip(samples, -1.0, 1.0)
        pcm16 = (clipped * 32767.0).astype(np.int16).tobytes()
        converted, _ = audioop.ratecv(pcm16, 2, 1, int(source_rate), int(target_rate), None)
        return np.frombuffer(converted, dtype=np.int16).astype(np.float32) / 32768.0
    except Exception:
        return _linear_resample(samples, source_rate, target_rate)


def _linear_resample(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    """Simple linear interpolation resampling when audioop not available."""
    duration = samples.size / float(source_rate)
    target_length = max(1, int(round(duration * target_rate)))
    source_positions = np.linspace(0.0, duration, num=samples.size, endpoint=False)
    target_positions = np.linspace(0.0, duration, num=target_length, endpoint=False)
    out = np.interp(target_positions, source_positions, samples).astype(np.float32)
    return np.ascontiguousarray(out, dtype=np.float32)


def _decode_with_ffmpeg(path: Path, target_rate: int = SAMPLE_RATE) -> Tuple[np.ndarray, int]:
    """Use ffmpeg to decode non-WAV formats (webm/mp3/m4a etc)."""
    import shutil
    exe = shutil.which("ffmpeg")
    if not exe:
        raise AudioDecodeError(
            f"无法直接解码 {path.name}（只支持 WAV 原生解析）。\n"
            "安装 ffmpeg 并加入 PATH 就能自动转码 mp3/m4a/webm 等格式。"
        )
    command = [
        exe, "-v", "error", "-i", str(path),
        "-f", "wav", "-ar", str(target_rate), "-ac", "1", "-",
    ]
    result = subprocess.run(command, capture_output=True)
    if result.returncode != 0 or not result.stdout:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise AudioDecodeError(f"ffmpeg 转码失败：{detail}")
    return _parse_riff(result.stdout)


def load_audio(path: str | Path, target_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Load an audio file, return 16kHz mono float32 samples normalized to [-1, 1]."""
    require_engine()
    audio_path = Path(path)
    if not audio_path.is_file():
        raise AudioDecodeError(f"音频文件不存在：{audio_path}")

    data = audio_path.read_bytes()
    if len(data) >= 12 and data[0:4] == b"RIFF" and data[8:12] == b"WAVE":
        samples, rate = _parse_riff(data)
    else:
        samples, rate = _decode_with_ffmpeg(audio_path, target_rate)

    samples = resample(samples, rate, target_rate)
    if samples.size == 0:
        raise AudioDecodeError("音频是空的")
    return samples


def write_wav(path: str | Path, samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> None:
    """Write float32 samples to 16-bit PCM WAV file."""
    require_engine()
    pcm = np.clip(samples, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype(np.int16).tobytes()
    data_size = len(pcm)
    header = b"RIFF" + struct.pack("<I", 36 + data_size) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
    header += b"data" + struct.pack("<I", data_size)
    Path(path).write_bytes(header + pcm)


# ---------------------------------------------------------------- Recognizer construction

def create_recognizer(
    model_dir: Optional[str | Path] = None,
    num_threads: int = 2,
    enable_endpoint_detection: bool = False,
    decoding_method: str = "greedy_search",
    hotwords: Sequence[str] = (),
    hotwords_score: float = 1.5,
    debug: bool = False,
) -> sherpa_onnx.OnlineRecognizer:
    """Create an OnlineRecognizer for streaming speech recognition.

    Args:
        model_dir: Explicit model directory (uses search order if None)
        num_threads: Number of decoding threads (2 = good default for CPU)
        enable_endpoint_detection: Enable automatic sentence segmentation
            (useful for real-time microphone input)
        decoding_method: "greedy_search" (faster) or "modified_beam_search" (slightly more accurate)
        hotwords: List of hotwords to boost detection probability
        hotwords_score: Boost score for hotwords (1.5 = default)
        debug: Enable debug output from sherpa-onnx
    """
    require_engine()
    paths = ModelPaths.load(resolve_model_dir(str(model_dir) if model_dir else None))

    hotwords_file = ""
    if hotwords:
        import tempfile
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".txt", delete=False, encoding="utf-8"
        )
        handle.write("\n".join(hotwords))
        handle.close()
        hotwords_file = handle.name

    return sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=str(paths.tokens),
        encoder=str(paths.encoder),
        decoder=str(paths.decoder),
        joiner=str(paths.joiner),
        num_threads=num_threads,
        sample_rate=SAMPLE_RATE,
        feature_dim=FEATURE_DIM,
        decoding_method=decoding_method,
        model_type="zipformer",
        provider="cpu",
        enable_endpoint_detection=enable_endpoint_detection,
        hotwords_file=hotwords_file,
        hotwords_score=hotwords_score,
        debug=debug,
    )


def _decode_all(recognizer: sherpa_onnx.OnlineRecognizer, stream) -> None:
    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)


def transcribe_samples(
    recognizer: sherpa_onnx.OnlineRecognizer,
    samples: np.ndarray,
    sample_rate: int = SAMPLE_RATE,
    flush_silence_seconds: float = FINAL_FLUSH_SILENCE_SECONDS,
) -> str:
    """Transcribe a single full chunk of audio (offline-style)."""
    if samples.size == 0:
        return ""

    if sample_rate != SAMPLE_RATE:
        samples = resample(samples, sample_rate, SAMPLE_RATE)

    stream = recognizer.create_stream()
    stream.accept_waveform(SAMPLE_RATE, np.ascontiguousarray(samples, dtype=np.float32))

    if flush_silence_seconds > 0:
        tail = np.zeros(int(round(SAMPLE_RATE * flush_silence_seconds)), dtype=np.float32)
        stream.accept_waveform(SAMPLE_RATE, tail)

    stream.input_finished()
    _decode_all(recognizer, stream)
    return recognizer.get_result(stream).strip()


def transcribe_file(
    recognizer: sherpa_onnx.OnlineRecognizer,
    path: str | Path,
    flush_silence_seconds: float = FINAL_FLUSH_SILENCE_SECONDS,
) -> str:
    """Transcribe an entire audio file from disk."""
    return transcribe_samples(recognizer, load_audio(path), SAMPLE_RATE, flush_silence_seconds)


class StreamSession:
    """A continuous recognition session, good for real-time microphone input."""

    def __init__(self, recognizer: sherpa_onnx.OnlineRecognizer):
        self.recognizer = recognizer
        self.stream = recognizer.create_stream()
        self._sample_rate = SAMPLE_RATE

    def accept(self, samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> None:
        """Accept a chunk of audio samples."""
        if samples.size == 0:
            return
        self._sample_rate = sample_rate
        if sample_rate != SAMPLE_RATE:
            samples = resample(samples, sample_rate, SAMPLE_RATE)
        self.stream.accept_waveform(SAMPLE_RATE, np.ascontiguousarray(samples, dtype=np.float32))

    def decode(self) -> None:
        """Run decoding on the currently accepted chunks."""
        _decode_all(self.recognizer, self.stream)

    def text(self) -> str:
        """Get the current recognized text."""
        return self.recognizer.get_result(self.stream).strip()

    def is_endpoint(self) -> bool:
        """Check if we've reached an endpoint (silence gap indicating end of utterance)."""
        return self.recognizer.is_endpoint(self.stream)

    def reset(self) -> None:
        """Reset the stream after an utterance (for next sentence)."""
        self.recognizer.reset(self.stream)

    def finish(self, flush_silence_seconds: float = FINAL_FLUSH_SILENCE_SECONDS) -> str:
        """Finish the session: flush with silence and return final text."""
        if flush_silence_seconds > 0:
            tail = np.zeros(int(round(SAMPLE_RATE * flush_silence_seconds)), dtype=np.float32)
            self.stream.accept_waveform(SAMPLE_RATE, tail)
        self.stream.input_finished()
        self.decode()
        return self.text()
