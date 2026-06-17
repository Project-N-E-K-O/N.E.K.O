from __future__ import annotations

from .entry_common import (
    asyncio,
    base64,
    Err,
    Ok,
    SdkError,
    _entry_exception_error,
    DocExporter,
    normalize_format,
    ui,
)


class _ExportSupportMixin:
    def _sync_doc_export_entry(self) -> None:
        self.unregister_dynamic_entry("study_export_notes")
        if not bool(self._cfg.doc_export.enabled):
            return
        export_formats = ["markdown", "pdf", "docx"]
        if bool(self._cfg.doc_export.xmind_enabled):
            export_formats.append("xmind")
        export_format_names = "Markdown, PDF, DOCX"
        if bool(self._cfg.doc_export.xmind_enabled):
            export_format_names = f"{export_format_names}, or XMind"
        self.register_dynamic_entry(
            "study_export_notes",
            self._study_export_notes_entry,
            name="Export Study Notes",
            description=f"Export recent study notes as {export_format_names}.",
            input_schema={
                "type": "object",
                "properties": {
                    "fmt": {
                        "type": "string",
                        "enum": export_formats,
                        "default": "markdown",
                    },
                    "style": {
                        "type": "string",
                        "enum": ["neko", "academic", "compact"],
                        "default": self._cfg.doc_export.default_style,
                    },
                    "title": {"type": "string", "default": "Study Notes"},
                    "preview_only": {"type": "boolean", "default": False},
                    "time_range": {"type": "string", "default": "recent"},
                    "recent_limit": {"type": "integer", "default": 30},
                    "topic_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "default": [],
                    },
                    "note_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "default": [],
                    },
                },
            },
            timeout=75.0,
            llm_result_fields=[
                "filename",
                "content_type",
                "format",
                "style",
                "markdown",
            ],
        )

    @ui.action()
    async def _study_export_notes_entry(
        self,
        fmt: str = "markdown",
        style: str | None = None,
        title: str | None = "Study Notes",
        preview_only: bool = False,
        time_range: str | None = "recent",
        recent_limit: int | None = 30,
        topic_ids: list[str] | None = None,
        note_ids: list[str] | None = None,
        **_,
    ):
        try:
            if not bool(self._cfg.doc_export.enabled):
                return Err(
                    SdkError("study note export is disabled by doc_export.enabled")
                )
            normalize_format(fmt)
            normalized_topic_ids = topic_ids if isinstance(topic_ids, list) else []
            normalized_note_ids = note_ids if isinstance(note_ids, list) else []
            exporter = DocExporter(self._store, config=self._cfg.doc_export)
            exported = await asyncio.to_thread(
                exporter.export,
                fmt=fmt,
                style=style,
                title=title,
                preview_only=bool(preview_only),
                time_range=time_range,
                recent_limit=recent_limit,
                topic_ids=normalized_topic_ids,
                note_ids=normalized_note_ids,
            )
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="_study_export_notes_entry")
        return Ok(
            {
                "content_base64": base64.b64encode(exported.content).decode("ascii"),
                "filename": exported.filename,
                "content_type": exported.content_type,
                "markdown": exported.markdown,
                "format": exported.format,
                "style": exported.style,
            }
        )
