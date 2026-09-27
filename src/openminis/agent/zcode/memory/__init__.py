"""Memory — project memory extraction, manifest and index formatting.

Ported from: packages/core/src/memory/*
Original package: @zcode/core (memory)
"""

from __future__ import annotations

from .directory import ensure_memory_directory_exists
from .extraction import (
    MemoryExtractionDecision,
    MemoryExtractionExecutionInput,
    MemoryExtractionExecutionStatus,
    MemoryExtractionScheduler,
    MemoryExtractionSnapshot,
    build_memory_extraction_prompt,
    create_memory_extraction_scheduler,
)
from .index_content import format_memory_index_content, format_project_memory_index_content
from .memory_agent_loop import run_memory_agent_loop
from .memory_file_path import (
    memory_file_relative_path,
    resolve_contained_memory_file_path,
    resolve_safe_memory_file_path,
)
from .origin_session import stamp_memory_origin_session_id
from .project_root import ProjectMemoryRootInput, resolve_project_memory_root
from .recall import (
    MEMORY_RECALL_TYPES,
    MemoryManifestEntry,
    MemoryRecallType,
    format_memory_manifest,
    scan_memory_manifest,
)

__all__ = [
    "ensure_memory_directory_exists",
    "build_memory_extraction_prompt",
    "create_memory_extraction_scheduler",
    "MemoryExtractionScheduler",
    "MemoryExtractionSnapshot",
    "MemoryExtractionExecutionInput",
    "MemoryExtractionExecutionStatus",
    "MemoryExtractionDecision",
    "format_project_memory_index_content",
    "format_memory_index_content",
    "run_memory_agent_loop",
    "resolve_contained_memory_file_path",
    "resolve_safe_memory_file_path",
    "memory_file_relative_path",
    "stamp_memory_origin_session_id",
    "ProjectMemoryRootInput",
    "resolve_project_memory_root",
    "format_memory_manifest",
    "scan_memory_manifest",
    "MemoryManifestEntry",
    "MemoryRecallType",
    "MEMORY_RECALL_TYPES",
]
