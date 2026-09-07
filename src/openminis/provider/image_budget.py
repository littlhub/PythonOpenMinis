"""Per-message inline-image byte budget (mirrors iOS kPerImageMaxBytes / kMessageImageMaxBytes).

Ported from: src/android/app/src/main/java/com/openminis/app/provider/ImageBudget.kt
Original package: com.openminis.app.provider

Android Bitmap decode/resize → Pillow (PIL.Image). The Pillow import is
lazy so the module imports even where Pillow is absent (logic-only callers).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from openminis.core.logging import get_logger

__all__ = ["ImageBudget"]

logger = get_logger(__name__)

# PORT: Android `AppLogger` → core logger.


class ImageBudget:
    """Object ImageBudget (ImageBudget.kt)."""

    # Single image bytes ceiling before re-encode kicks in.
    MAX_PER_IMAGE_BYTES = 5 * 1024 * 1024
    # Cumulative inline-image bytes per user message.
    MAX_TOTAL_BYTES = 25 * 1024 * 1024
    # Cumulative inline-image bytes across ALL messages in a single request.
    MAX_REQUEST_BYTES = 25 * 1024 * 1024
    # Default re-encode target longest edge in pixels.
    MAX_EDGE_PX = 2000
    # Default re-encode JPEG quality (0-100).
    JPEG_QUALITY = 80

    _LADDER = [
        (2000, 80),
        (1600, 75),
        (1280, 70),
        (1024, 65),
        (896, 55),
        (768, 50),
        (640, 45),
    ]

    @staticmethod
    def compress_bytes(input: bytes, max_edge: int = MAX_EDGE_PX, q: int = JPEG_QUALITY) -> bytes:
        """Re-encode input to a JPEG with max longest edge maxEdge at JPEG quality q.
        Returns the original bytes on decode/encode failure."""
        if not input:
            return input
        try:
            from io import BytesIO

            from PIL import Image

            decoded = Image.open(BytesIO(input))
            w0, h0 = decoded.size
            if w0 <= 0 or h0 <= 0:
                return input
            sample = 1
            while w0 / sample > max_edge * 2 or h0 / sample > max_edge * 2:
                sample *= 2
            if sample > 1:
                decoded = decoded.resize((max(1, w0 // sample), max(1, h0 // sample)))
            scale = min(max_edge / decoded.width, max_edge / decoded.height, 1.0)
            out = decoded
            if scale < 1.0:
                out = decoded.resize(
                    (max(1, int(decoded.width * scale)), max(1, int(decoded.height * scale)))
                )
            buf = BytesIO()
            out.convert("RGB").save(buf, format="JPEG", quality=max(1, min(100, q)))
            return buf.getvalue()
        except Exception as t:
            logger.warning("compressBytes failed (%dB → keeping original): %s", len(input), t)
            return input

    @staticmethod
    def compress_under_budget(input: bytes, target_max_bytes: int = MAX_PER_IMAGE_BYTES) -> bytes:
        """Try increasingly aggressive (maxEdge, quality) candidates until the
        re-encoded JPEG fits under targetMaxBytes. Returns the smallest encoding
        produced when no candidate fits — never returns the original silently."""
        if len(input) <= target_max_bytes:
            return input
        best = input
        best_size = len(input)
        for edge, q in ImageBudget._LADDER:
            candidate = ImageBudget.compress_bytes(input, edge, q)
            if len(candidate) < best_size:
                best = candidate
                best_size = len(candidate)
            if len(candidate) <= target_max_bytes:
                logger.info("compressUnderBudget hit: %dB → %dB (edge=%d q=%d)", len(input), len(candidate), edge, q)
                return candidate
        logger.warning("compressUnderBudget exhausted ladder: %dB → %dB (target=%dB)", len(input), len(best), target_max_bytes)
        return best

    @dataclass(slots=True)
    class BudgetResult:
        kept_bytes: list[bytes]
        compressed_count: int
        dropped_count: int
        total_bytes: int

        @property
        def mutated(self) -> bool:
            return self.compressed_count > 0 or self.dropped_count > 0

    @staticmethod
    def apply_message_budget(bytes_in: list[bytes]) -> "ImageBudget.BudgetResult":
        """Walk bytesIn and produce a budgeted output: each oversize part is run
        through compressUnderBudget first, then cumulative bytes are summed; once
        the running total would exceed MAX_TOTAL_BYTES the remaining tail is dropped."""
        if not bytes_in:
            return ImageBudget.BudgetResult([], 0, 0, 0)
        kept: list[bytes] = []
        compressed = 0
        dropped = 0
        running = 0
        for part in bytes_in:
            if len(part) > ImageBudget.MAX_PER_IMAGE_BYTES:
                c = ImageBudget.compress_under_budget(part)
                if len(c) != len(part):
                    compressed += 1
                sized = c
            else:
                sized = part
            if running + len(sized) > ImageBudget.MAX_TOTAL_BYTES:
                dropped += 1
                continue
            kept.append(sized)
            running += len(sized)
        if dropped > 0 or compressed > 0:
            logger.info(
                "applyMessageBudget: in=%d kept=%d compressed=%d dropped=%d total=%dB",
                len(bytes_in), len(kept), compressed, dropped, running,
            )
        return ImageBudget.BudgetResult(kept, compressed, dropped, running)

    @dataclass(slots=True)
    class ImagePartId:
        identity_hash: int

        @staticmethod
        def of(data: bytes) -> "ImageBudget.ImagePartId":
            return ImageBudget.ImagePartId(id(data))

    @dataclass(slots=True)
    class BudgetImage:
        data: bytes
        linux_path: Optional[str]
        mime_type: str

    @dataclass(slots=True)
    class RequestBudgetPlan:
        dropped_ids: set["ImageBudget.ImagePartId"]
        dropped_paths: dict["ImageBudget.ImagePartId", Optional[str]]
        kept_bytes: int
        elided_bytes: int
        dropped_count: int
        total_count: int

        @property
        def mutated(self) -> bool:
            return self.dropped_count > 0

    @staticmethod
    def plan_request_budget(
        images: list["ImageBudget.BudgetImage"],
        max_bytes: int = MAX_REQUEST_BYTES,
    ) -> "ImageBudget.RequestBudgetPlan":
        """Walk all candidate images latest → eldest (most recent wins the budget)
        and decide which can fit under maxBytes."""
        if not images:
            return ImageBudget.RequestBudgetPlan(set(), {}, 0, 0, 0, 0)
        dropped: set[ImageBudget.ImagePartId] = set()
        dropped_paths: dict[ImageBudget.ImagePartId, Optional[str]] = {}
        kept = 0
        elided = 0
        for img in reversed(images):
            pid = ImageBudget.ImagePartId.of(img.data)
            effective_size = min(len(img.data), ImageBudget.MAX_PER_IMAGE_BYTES)
            if kept + effective_size <= max_bytes:
                kept += effective_size
            else:
                dropped.add(pid)
                dropped_paths[pid] = img.linux_path
                elided += effective_size
        if dropped:
            logger.info(
                "planRequestBudget: in=%d kept=%d dropped=%d keptBytes=%dB elidedBytes=%dB cap=%dB",
                len(images), len(images) - len(dropped), len(dropped), kept, elided, max_bytes,
            )
        return ImageBudget.RequestBudgetPlan(
            dropped_ids=dropped,
            dropped_paths=dropped_paths,
            kept_bytes=kept,
            elided_bytes=elided,
            dropped_count=len(dropped),
            total_count=len(images),
        )

    @staticmethod
    def elided_image_placeholder(linux_path: Optional[str]) -> str:
        """Build the text placeholder a provider emits in place of an elided image."""
        if linux_path is not None:
            return f"[image elided to fit 25MB request budget. Original at {linux_path} — re-fetch with `read_image {linux_path}` if you need to see it.]"
        return "[image elided to fit 25MB request budget. Original bytes no longer addressable; ask the user to re-attach if needed.]"

    @staticmethod
    def ensure_spillover(
        session_attachments_dir: Path,
        data: bytes,
        mime_type: str,
    ) -> Optional[str]:
        """Lazily persist data to a session-scoped spillover dir under
        attachments/spillover/<sha1>.<ext>. Returns the linux path, or null on failure."""
        if not data:
            return None
        ext = {
            "image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png",
            "image/gif": "gif", "image/webp": "webp", "image/heic": "heic",
            "image/heif": "heif",
        }.get(mime_type.lower(), "bin")
        try:
            sha = hashlib.sha1(data).hexdigest()
        except Exception:
            sha = str(id(data))
        spillover_dir = session_attachments_dir / "spillover"
        try:
            spillover_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning("ensureSpillover mkdirs failed: %s", e)
            return None
        file = spillover_dir / f"{sha}.{ext}"
        if not file.exists():
            try:
                file.write_bytes(data)
            except Exception as e:
                logger.warning("ensureSpillover write failed: %s", e)
                return None
        return f"/var/minis/attachments/spillover/{sha}.{ext}"
