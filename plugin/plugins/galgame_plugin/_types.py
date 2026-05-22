from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Any, Protocol


MEMORY_READER_DEFAULT_ENGINE = "unknown"


@dataclass(slots=True)
class DetectedGameProcess:
    pid: int
    name: str
    create_time: float
    engine: str
    exe_path: str = ""
    detection_reason: str = ""

    @property
    def process_key(self) -> str:
        path_digest = hashlib.sha1(self.exe_path.encode("utf-8")).hexdigest()[:12] if self.exe_path else "noexe"
        name_digest = hashlib.sha1(self.name.lower().encode("utf-8")).hexdigest()[:8] if self.name else "noname"
        return f"memproc:{int(self.pid)}:{name_digest}:{path_digest}"

    def to_dict(
        self,
        *,
        is_attached: bool = False,
        is_manual_target: bool = False,
    ) -> dict[str, Any]:
        return {
            "process_key": self.process_key,
            "pid": self.pid,
            "process_name": self.name,
            "exe_path": self.exe_path,
            "detected_engine": self.engine or MEMORY_READER_DEFAULT_ENGINE,
            "engine": self.engine or MEMORY_READER_DEFAULT_ENGINE,
            "detection_reason": self.detection_reason,
            "create_time": self.create_time,
            "is_attached": is_attached,
            "is_manual_target": is_manual_target,
        }


@dataclass(slots=True)
class ParsedTextractorLine:
    pid: int
    hook_addr: str
    ctx: str
    sub_ctx: str
    text: str

    @property
    def hook_id(self) -> str:
        return f"{self.pid}:{self.hook_addr}:{self.ctx}:{self.sub_ctx}"


@dataclass(slots=True)
class MemoryReaderRuntime:
    enabled: bool = False
    status: str = "disabled"
    detail: str = ""
    target_selection_mode: str = "auto"
    target_selection_detail: str = ""
    process_name: str = ""
    pid: int = 0
    exe_path: str = ""
    engine: str = ""
    detection_reason: str = ""
    hook_code_count: int = 0
    hook_code_detail: str = ""
    game_id: str = ""
    session_id: str = ""
    last_seq: int = 0
    last_event_ts: str = ""
    last_text_seq: int = 0
    last_text_ts: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "status": self.status,
            "detail": self.detail,
            "target_selection_mode": self.target_selection_mode,
            "target_selection_detail": self.target_selection_detail,
            "process_name": self.process_name,
            "pid": self.pid,
            "exe_path": self.exe_path,
            "engine": self.engine,
            "detection_reason": self.detection_reason,
            "hook_code_count": self.hook_code_count,
            "hook_code_detail": self.hook_code_detail,
            "game_id": self.game_id,
            "session_id": self.session_id,
            "last_seq": self.last_seq,
            "last_event_ts": self.last_event_ts,
            "last_text_seq": self.last_text_seq,
            "last_text_ts": self.last_text_ts,
        }


@dataclass(slots=True)
class MemoryReaderTickResult:
    warnings: list[str] = field(default_factory=list)
    should_rescan: bool = False
    runtime: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MemoryReaderProcessTarget:
    mode: str = "auto"
    process_key: str = ""
    process_name: str = ""
    exe_path: str = ""
    pid: int = 0
    engine: str = ""
    detection_reason: str = ""
    create_time: float = 0.0
    selected_at: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> MemoryReaderProcessTarget:
        raw = value if isinstance(value, dict) else {}
        mode = str(raw.get("mode") or "auto").strip().lower()
        if mode not in {"auto", "manual"}:
            mode = "auto"
        try:
            pid = int(raw.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        try:
            create_time = float(raw.get("create_time") or 0.0)
        except (TypeError, ValueError):
            create_time = 0.0
        return cls(
            mode=mode,
            process_key=str(raw.get("process_key") or "").strip(),
            process_name=str(raw.get("process_name") or raw.get("name") or "").strip(),
            exe_path=str(raw.get("exe_path") or "").strip(),
            pid=max(0, pid),
            engine=str(raw.get("engine") or raw.get("detected_engine") or "").strip().lower(),
            detection_reason=str(raw.get("detection_reason") or "").strip(),
            create_time=max(0.0, create_time),
            selected_at=str(raw.get("selected_at") or "").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "process_key": self.process_key,
            "process_name": self.process_name,
            "exe_path": self.exe_path,
            "pid": self.pid,
            "engine": self.engine,
            "detected_engine": self.engine,
            "detection_reason": self.detection_reason,
            "create_time": self.create_time,
            "selected_at": self.selected_at,
        }

    def is_manual(self) -> bool:
        return self.mode == "manual"

    def matches_exact(self, candidate: DetectedGameProcess) -> bool:
        if self.process_key and self.process_key == candidate.process_key:
            return True
        return bool(self.pid) and self.pid == candidate.pid

    def matches_signature(self, candidate: DetectedGameProcess) -> bool:
        process_name = self.process_name.strip().lower()
        candidate_name = candidate.name.strip().lower()
        exe_path = os.path.normcase(self.exe_path.strip())
        candidate_exe = os.path.normcase(candidate.exe_path.strip())
        if exe_path and candidate_exe and exe_path != candidate_exe:
            return False
        if process_name and process_name != candidate_name:
            return False
        if not process_name and not exe_path and self.pid > 0:
            return candidate.pid == self.pid
        return bool(process_name or exe_path or self.pid)

    def resolved_for(self, candidate: DetectedGameProcess) -> MemoryReaderProcessTarget:
        return MemoryReaderProcessTarget(
            mode="manual",
            process_key=candidate.process_key,
            process_name=candidate.name,
            exe_path=candidate.exe_path,
            pid=candidate.pid,
            engine=candidate.engine,
            detection_reason=candidate.detection_reason,
            create_time=candidate.create_time,
            selected_at=self.selected_at,
        )


class TextractorProcessHandle(Protocol):
    async def write(self, payload: str) -> None: ...

    async def readline(self, timeout: float) -> str | None: ...

    def poll(self) -> int | None: ...

    async def terminate(self) -> None: ...

    async def wait(self, timeout: float) -> int | None: ...
