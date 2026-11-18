"""Project retained document versions into the unchanged schema-v1 pack format."""

from ..models import KnowledgePack, KnowledgeEntry, KnowledgeSource


def project_pack(pack_id: str, name: str, documents: list[dict]) -> KnowledgePack:
    """Keep source identity in tags and bound entries before v1 normalization."""
    entries = []
    for document in documents:
        # Small complete chapters avoid the v1 per-entry character ceiling;
        # the tokenizer can further divide embedding chunks without losing text.
        content = document["content"]
        for index, start in enumerate(range(0, len(content), 4000), 1):
            entries.append(KnowledgeEntry(
                title=f"{document['name'][:160]} · {index} · {document['doc_id']}",
                content=content[start:start + 4000], summary="",
                tags=(f"doc:{document['doc_id']}", f"ver:{document['version']}"),
            ))
    return KnowledgePack(pack_id=pack_id, material_type="knowledge",
                         source=KnowledgeSource(name=name), entries=tuple(entries))
