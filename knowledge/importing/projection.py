"""Project retained document versions into the unchanged schema-v1 pack format."""

from ..models import KnowledgePack, KnowledgeEntry, KnowledgeSource


def project_pack(pack_id: str, name: str, documents: list[dict], *, legacy_windows: bool = False) -> KnowledgePack:
    """Keep source identity in tags and bound entries before v1 normalization."""
    entries = []
    for document in documents:
        # Small complete chapters avoid the v1 per-entry character ceiling;
        # the tokenizer can further divide embedding chunks without losing text.
        content = document["content"]
        start = 0
        index = 0
        while start < len(content):
            stop = min(start + 4000, len(content))
            # Prefer a source whitespace boundary so FTS does not lose a word
            # straddling two entries. Hard-cut only a single overlong token.
            if not legacy_windows and stop < len(content) and not content[stop].isspace():
                boundary = max((pos for pos in range(start, stop) if content[pos].isspace()), default=start)
                if boundary > start:
                    stop = boundary + 1
            body = content[start:stop]
            start = stop
            if not legacy_windows and not body.strip():
                continue
            index += 1
            entries.append(KnowledgeEntry(
                title=f"{document['name'][:160]} · {index} · {document['doc_id']}",
                content=body, summary="",
                tags=(f"doc:{document['doc_id']}", f"ver:{document['version']}"),
            ))
    return KnowledgePack(pack_id=pack_id, material_type="knowledge",
                         source=KnowledgeSource(name=name), entries=tuple(entries))
