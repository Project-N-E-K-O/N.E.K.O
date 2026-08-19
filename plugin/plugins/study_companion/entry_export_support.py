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
    plugin_entry,
    ui,
)


class _ExportSupportMixin:
    def _sync_doc_export_entry(self) -> None:
        # The entry metadata is fixed so the parent Hosted UI registry can
        # publish it before the child process starts. Keep this cleanup for
        # upgrades from versions that registered the same id dynamically.
        self.unregister_dynamic_entry("study_export_notes")

    @ui.action()
    @plugin_entry(
        id="study_export_notes",
        name="Export Study Notes",
        description="Export recent study notes as Markdown, PDF, DOCX, or XMind.",
        input_schema={
            "type": "object",
            "properties": {
                "fmt": {
                    "type": "string",
                    "enum": ["markdown", "pdf", "docx", "xmind"],
                    "default": "markdown",
                },
                "style": {
                    "type": "string",
                    "enum": ["neko", "academic", "compact"],
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
    async def study_export_notes(self, **kwargs):
        return await self._study_export_notes_entry(**kwargs)

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
