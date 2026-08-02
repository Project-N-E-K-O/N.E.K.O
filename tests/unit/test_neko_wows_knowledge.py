"""Tactical document import, indexing and retrieval."""

from __future__ import annotations

import pytest

from plugin.plugins.neko_wows.domain.contracts import (
    NullTacticsRepository,
    TacticQuery,
    WowsConfig,
)
from plugin.plugins.neko_wows.knowledge.importer import (
    MAX_FRONT_MATTER_CHARS,
    MAX_TAGS_PER_DOCUMENT,
    DocumentImporter,
    DocumentRejected,
    chunk_body,
    parse_document,
    parse_front_matter,
    split_front_matter,
)
from plugin.plugins.neko_wows.knowledge.retrieval import WowsTacticsRepository
from plugin.plugins.neko_wows.knowledge.store import KnowledgeStore
from plugin.plugins.neko_wows.knowledge.tokenize import (
    index_terms,
    normalize,
    rank_terms,
    trigrams,
)


@pytest.fixture
def store(tmp_path):
    instance = KnowledgeStore(tmp_path / "tactical.db")
    instance.open()
    yield instance
    instance.close()


def cfg(**overrides):
    data = {"tactics_chunk_chars": 800, "tactics_chunk_overlap": 100}
    data.update(overrides)
    return WowsConfig.from_mapping(data)


def importer(store, **overrides):
    return DocumentImporter(store, cfg(**overrides))


# --- tokenizer -----------------------------------------------------------

def test_normalize_folds_full_width_and_case():
    assert normalize("ＡＰ弹") == "ap弹"


def test_chinese_text_becomes_two_grams():
    terms = index_terms("巡洋舰")
    assert "巡洋" in terms and "洋舰" in terms


def test_two_gram_index_recalls_a_longer_phrase():
    """The whole reason FTS5's unicode61 tokenizer is not usable here."""
    document = set(index_terms("重巡洋舰应该保持距离"))
    query = set(index_terms("巡洋舰"))
    assert query & document


def test_ranking_adds_three_grams():
    ranked = set(rank_terms("巡洋舰"))
    assert "巡洋舰" in ranked
    assert "巡洋舰" not in set(index_terms("巡洋舰"))


def test_latin_runs_stay_whole_tokens():
    assert "reload" in index_terms("reload timer")
    assert "re" not in index_terms("reload timer")


def test_mixed_script_segments_keep_both_halves():
    """"AP弹" must be findable as both `ap` and the CJK n-gram."""
    terms = index_terms("AP弹药")
    assert "ap" in terms
    assert "弹药" in terms


def test_single_characters_are_not_indexed():
    assert index_terms("a b c") == []


def test_trigrams_only_cover_cjk():
    assert trigrams("reload timer") == []
    assert "巡洋舰" in trigrams("巡洋舰")


def test_term_frequency_is_preserved():
    terms = index_terms("距离 距离")
    assert terms.count("距离") == 2


# --- front matter --------------------------------------------------------

def test_front_matter_is_split_from_the_body():
    block, body = split_front_matter("---\nmaps: New Dawn\n---\n正文\n")
    assert "maps" in block
    assert body.strip() == "正文"


def test_a_horizontal_rule_is_not_front_matter():
    block, body = split_front_matter("---\n没有结束围栏\n")
    assert block == ""
    assert "没有结束围栏" in body


def test_inline_and_block_lists_both_parse():
    tags = parse_front_matter(
        "maps: New Dawn, Ocean\nships: [Yamato, Zao]\nclasses:\n  - Battleship\n")
    assert tags["maps"] == ("New Dawn", "Ocean")
    assert tags["ships"] == ("Yamato", "Zao")
    assert tags["classes"] == ("Battleship",)


def test_an_unknown_key_rejects_the_whole_document():
    """Silently dropping it would let the user believe a tag is active."""
    with pytest.raises(DocumentRejected) as excinfo:
        parse_front_matter("maps: New Dawn\nscript: rm -rf /\n")
    assert "script" in str(excinfo.value)


def test_nested_structures_are_rejected():
    with pytest.raises(DocumentRejected):
        parse_front_matter("maps:\n  nested:\n    deeper: 1\n")


def test_oversized_front_matter_is_rejected():
    block = "---\n" + ("maps: x\n" * (MAX_FRONT_MATTER_CHARS // 4)) + "---\n正文"
    with pytest.raises(DocumentRejected):
        split_front_matter(block)


def test_too_many_tags_are_rejected():
    values = ",".join(f"m{i}" for i in range(MAX_TAGS_PER_DOCUMENT + 5))
    with pytest.raises(DocumentRejected):
        parse_front_matter(f"maps: {values}\n")


def test_empty_front_matter_is_fine():
    assert parse_front_matter("") == {}


# --- chunking ------------------------------------------------------------

def test_headings_become_a_breadcrumb():
    pairs = chunk_body("# 甲\n\n段落一\n\n## 乙\n\n段落二\n", size=800, overlap=100)
    headings = [heading for heading, _text in pairs]
    assert "甲" in headings
    assert "甲 / 乙" in headings


def test_chunks_respect_the_size_limit():
    body = "\n\n".join("句子。" * 40 for _ in range(20))
    pairs = chunk_body(body, size=300, overlap=50)
    assert pairs
    assert all(len(text) <= 300 for _heading, text in pairs)


def test_consecutive_chunks_overlap():
    body = "甲" * 900
    pairs = chunk_body(body, size=300, overlap=60)
    assert len(pairs) >= 2
    first, second = pairs[0][1], pairs[1][1]
    assert first[-30:] in second or second[:30] in first


def test_headings_inside_code_fences_are_text():
    body = "# 真标题\n\n```\n# 假标题\n```\n"
    headings = {heading for heading, _text in chunk_body(body, size=800, overlap=0)}
    assert headings == {"真标题"}


def test_body_is_nfkc_normalized():
    document = parse_document("d.md", "全角ＡＰ弹", size=800, overlap=100)
    assert "AP" in document.chunks[0].text


def test_the_title_prefers_the_first_heading():
    document = parse_document("filename.md", "# 巡洋舰站位\n\n正文", size=800, overlap=0)
    assert document.title == "巡洋舰站位"


def test_the_title_falls_back_to_the_stem_without_the_directory():
    """A private folder path must never reach the database."""
    document = parse_document(
        r"D:/private/notes/guide.md", "正文没有标题", size=800, overlap=0)
    assert document.title == "guide"
    assert "private" not in document.title


def test_an_empty_document_is_rejected():
    with pytest.raises(DocumentRejected):
        parse_document("d.md", "   \n\n  ", size=800, overlap=0)


# --- quotas and dedup ----------------------------------------------------

def test_identical_content_is_deduplicated(store):
    tool = importer(store)
    first = tool.import_text("a.md", "# 甲\n\n巡洋舰应该保持距离")
    second = tool.import_text("b.md", "# 甲\n\n巡洋舰应该保持距离")
    assert first["status"] == "imported"
    assert second["status"] == "duplicate"
    assert second["doc_id"] == first["doc_id"]
    assert store.stats()["documents"] == 1


def test_an_oversized_file_is_refused(store):
    tool = importer(store, tactics_max_file_bytes=4096)
    with pytest.raises(DocumentRejected) as excinfo:
        tool.import_text("big.md", "甲" * 5000)
    assert "单个文件" in str(excinfo.value)


def test_the_document_count_quota_is_enforced(store):
    tool = importer(store, tactics_max_documents=2)
    tool.import_text("a.md", "内容甲")
    tool.import_text("b.md", "内容乙")
    with pytest.raises(DocumentRejected) as excinfo:
        tool.import_text("c.md", "内容丙")
    assert "数量已达上限" in str(excinfo.value)


def test_the_total_size_quota_is_enforced(store):
    tool = importer(store, tactics_max_total_bytes=4096)
    tool.import_text("a.md", "甲" * 1000)
    with pytest.raises(DocumentRejected) as excinfo:
        tool.import_text("b.md", "乙" * 1000)
    assert "总量" in str(excinfo.value)


def test_unsupported_suffixes_are_rejected(store, tmp_path):
    path = tmp_path / "notes.pdf"
    path.write_text("内容", encoding="utf-8")
    summary = importer(store).import_paths([str(path)])
    assert summary["counts"]["rejected"] == 1


def test_an_oversized_file_is_refused_before_it_is_read(store, tmp_path, monkeypatch):
    path = tmp_path / "big.md"
    path.write_text("甲" * 5000, encoding="utf-8")

    def explode(*_args, **_kwargs):
        pytest.fail("the size check must come before the read")

    monkeypatch.setattr(type(path), "read_text", explode)
    summary = importer(store, tactics_max_file_bytes=4096).import_paths([str(path)])
    assert summary["counts"]["rejected"] == 1
    assert "单个文件" in summary["results"][0]["error"]


def test_non_utf8_files_are_rejected(store, tmp_path):
    path = tmp_path / "notes.md"
    path.write_bytes("巡洋舰".encode("gbk"))
    summary = importer(store).import_paths([str(path)])
    assert summary["results"][0]["error"] == "文件不是 UTF-8 编码"


def test_import_from_paths_reports_per_file_results(store, tmp_path):
    good = tmp_path / "good.md"
    good.write_text("# 甲\n\n巡洋舰应该保持距离", encoding="utf-8")
    bad = tmp_path / "bad.md"
    bad.write_text("---\nscript: nope\n---\n正文", encoding="utf-8")
    summary = importer(store).import_paths([str(good), str(bad)])
    assert summary["counts"]["imported"] == 1
    assert summary["counts"]["rejected"] == 1


# --- index cap -----------------------------------------------------------

def test_chunks_beyond_the_index_cap_are_stored_but_not_indexed(store):
    tool = importer(store, tactics_index_chunk_cap=2, tactics_chunk_chars=200,
                    tactics_chunk_overlap=0)
    body = "\n\n".join(f"段落{i}" + "甲" * 190 for i in range(6))
    result = tool.import_text("big.md", body)
    assert result["chunks"] > 2
    assert result["indexed_chunks"] == 2
    assert result["index_truncated"] is True

    stats = store.stats()
    assert stats["chunks"] == result["chunks"]
    assert stats["indexed_chunks"] == 2


def test_an_exhausted_index_still_stores_the_document(store):
    tool = importer(store, tactics_index_chunk_cap=0)
    result = tool.import_text("a.md", "巡洋舰应该保持距离")
    assert result["indexed_chunks"] == 0
    assert store.stats()["documents"] == 1


# --- retrieval -----------------------------------------------------------

def repository(store, **overrides):
    return WowsTacticsRepository(store, cfg(**overrides))


def test_chinese_query_recalls_a_longer_phrase(store):
    importer(store).import_text(
        "cruiser.md", "# 巡洋舰\n\n重巡洋舰应该保持距离，不要贴脸。")
    hits = repository(store).search(
        TacticQuery(summary="巡洋舰距离", topics=("巡洋舰距离",)), limit=3)
    assert hits
    assert "巡洋舰" in hits[0].text


def test_no_tag_and_too_few_term_hits_injects_nothing(store):
    importer(store).import_text("a.md", "# 甲\n\n完全无关的内容，讲的是园艺。")
    repo = repository(store)
    hits = repo.search(TacticQuery(summary="鱼雷"), limit=3)
    assert hits == ()
    assert repo.diagnostics.gated is True
    assert "无标签命中" in repo.diagnostics.gate_reason


def test_a_single_term_hit_is_not_enough(store):
    importer(store).import_text("a.md", "# 甲\n\n距离很重要。")
    repo = repository(store, tactics_min_term_hits=2)
    hits = repo.search(TacticQuery(summary="距"), limit=3)
    assert hits == ()


def test_an_empty_corpus_returns_nothing(store):
    repo = repository(store)
    assert repo.search(TacticQuery(summary="巡洋舰距离"), limit=3) == ()


def test_a_tag_match_injects_even_without_term_hits(store):
    importer(store).import_text(
        "map.md", "---\nmaps: New Dawn\n---\n\n这张图北边有岛可以卡视野。")
    hits = repository(store).search(
        TacticQuery(summary="开局", map_name="New Dawn"), limit=3)
    assert hits
    assert "maps:New Dawn" in hits[0].tags


def test_tag_weighting_outranks_a_plain_term_match(store):
    tool = importer(store)
    tool.import_text("plain.md", "# 甲\n\n巡洋舰应该保持距离，这段没有标签。")
    tool.import_text(
        "tagged.md",
        "---\nclasses: Cruiser\n---\n\n巡洋舰应该保持距离，这段带标签。")
    hits = repository(store, tactics_tag_weight=10.0).search(
        TacticQuery(summary="巡洋舰距离", ship_class="Cruiser"), limit=2)
    assert hits
    assert "带标签" in hits[0].text


def test_diagnostics_explain_a_successful_search(store):
    importer(store).import_text(
        "a.md", "---\nmodes: Domination\n---\n\n占领区附近要注意鱼雷。")
    repo = repository(store)
    repo.search(TacticQuery(summary="占领", game_mode="Domination"), limit=3)
    diagnostics = repo.diagnostics.as_dict()
    assert diagnostics["gated"] is False
    assert diagnostics["tags_used"] == ["modes:Domination"]
    assert diagnostics["hits"]


def test_results_are_reproducible_for_the_same_corpus(store):
    tool = importer(store)
    for index in range(4):
        tool.import_text(f"d{index}.md", f"# 甲{index}\n\n巡洋舰应该保持距离 {index}")
    repo = repository(store)
    query = TacticQuery(summary="巡洋舰距离")
    first = [hit.text for hit in repo.search(query, limit=3)]
    second = [hit.text for hit in repo.search(query, limit=3)]
    assert first == second


def test_the_limit_is_respected(store):
    tool = importer(store)
    for index in range(6):
        tool.import_text(f"d{index}.md", f"# 甲{index}\n\n巡洋舰应该保持距离 {index}")
    assert len(repository(store).search(
        TacticQuery(summary="巡洋舰距离"), limit=2)) == 2


def test_unindexed_chunks_are_still_reachable_by_tag(store):
    """The documented degradation past the index cap."""
    tool = importer(store, tactics_index_chunk_cap=0)
    tool.import_text(
        "map.md", "---\nmaps: Ocean\n---\n\n这张图开阔，别单走。")
    hits = repository(store, tactics_index_chunk_cap=0).search(
        TacticQuery(summary="开局", map_name="Ocean"), limit=3)
    assert hits
    assert "别单走" in hits[0].text


def test_a_deleted_document_disappears_from_results(store):
    tool = importer(store)
    result = tool.import_text("a.md", "# 甲\n\n巡洋舰应该保持距离")
    repo = repository(store)
    assert repo.search(TacticQuery(summary="巡洋舰距离"), limit=3)

    store.delete_document(result["doc_id"])
    assert repo.search(TacticQuery(summary="巡洋舰距离"), limit=3) == ()
    assert store.stats()["postings"] == 0


def test_clearing_removes_everything(store):
    tool = importer(store)
    tool.import_text("a.md", "内容甲")
    tool.import_text("b.md", "内容乙")
    assert store.clear_documents() == 2
    stats = store.stats()
    assert stats == {
        "documents": 0, "total_bytes": 0, "chunks": 0,
        "indexed_chunks": 0, "postings": 0, "total_tokens": 0,
    }


# --- query shape ---------------------------------------------------------

def test_tag_candidates_map_battle_context_onto_kinds():
    query = TacticQuery(
        summary="低血量", map_name="New Dawn", ship_class="Cruiser",
        game_mode="Domination", topics=("撤退",))
    assert query.tag_candidates() == {
        "maps": ("New Dawn",),
        "classes": ("Cruiser",),
        "modes": ("Domination",),
        "topics": ("撤退",),
    }


def test_query_text_includes_context_for_term_matching():
    query = TacticQuery(summary="低血量", map_name="New Dawn")
    assert "低血量" in query.text()
    assert "New Dawn" in query.text()


def test_the_null_repository_matches_the_protocol():
    assert NullTacticsRepository().search(
        TacticQuery(summary="anything"), limit=3, budget=0) == ()


# --- safety: documents are not a data source -----------------------------

def test_a_document_cannot_substitute_for_a_missing_capability(store):
    """The whole point of the fence: reference text is not telemetry.

    A guide that talks at length about hull health must not let a health-based
    detector run when the `self` domain is unavailable.
    """
    from plugin.plugins.neko_wows.detectors._base import DetectorRegistry
    from plugin.plugins.neko_wows.detectors.survival import build_survival_detectors
    from plugin.plugins.neko_wows.domain.facts import FactBuilder
    from plugin.plugins.neko_wows.domain.snapshot import (
        AVAIL_UNKNOWN,
        AVAIL_UNSUPPORTED,
        CORE_DOMAINS,
        FUTURE_DOMAINS,
        STATUS_LIVE,
        WowsSnapshot,
    )

    importer(store).import_text(
        "health.md",
        "# 血量\n\n血量低于三成就该脱离，巡洋舰尤其不能硬顶战列舰的齐射。")

    settings = cfg()
    builder = FactBuilder(settings)
    registry = DetectorRegistry(build_survival_detectors(settings))
    availability = {domain: AVAIL_UNKNOWN for domain in CORE_DOMAINS}
    availability.update({domain: AVAIL_UNSUPPORTED for domain in FUTURE_DOMAINS})

    previous = None
    results = []
    for seq in (1, 2):
        snapshot = WowsSnapshot(
            instance_id="inst", seq=seq, battle_id="b-1", status=STATUS_LIVE,
            capabilities={domain: True for domain in CORE_DOMAINS},
            availability=dict(availability),
            active=True, self_ship=None, received_at=100.0 + seq,
        )
        current = (snapshot, builder.build(snapshot))
        results.append(registry.feed(previous, current, cfg=settings))
        previous = current

    blocked = {entry.detector for result in results for entry in result.blocked}
    assert "low_health" in blocked
    assert not [event for result in results for event in result.events]


def test_retrieved_text_is_fenced_as_untrusted_end_to_end(store):
    """A real excerpt, all the way through the router the P1 tests exercised."""
    from plugin.plugins.neko_wows.detectors._base import GameEvent
    from plugin.plugins.neko_wows.domain.catalog import LOW_HEALTH
    from plugin.plugins.neko_wows.domain.contracts import CHANNEL_DUAL
    from plugin.plugins.neko_wows.domain.facts import WowsFacts
    from plugin.plugins.neko_wows.policy.tactic_policy import WowsTacticPolicy
    from plugin.plugins.neko_wows.presentation.prompt_router import (
        REFERENCE_CLOSE,
        REFERENCE_OPEN,
        URGENT_EXCERPT_BUDGET,
        PromptProfile,
        WowsPromptRouter,
    )

    settings = cfg()
    importer(store, **{}).import_text(
        "retreat.md",
        "---\nclasses: Cruiser\n---\n\n# 低血量\n\n"
        + "血量低于三成就该脱离战斗，找岛遮挡再等修复。" * 60)

    excerpts = repository(store).search(
        TacticQuery(summary="低血量", ship_class="Cruiser"), limit=3)
    assert excerpts, "the tagged document should be retrievable"

    event = GameEvent(
        event_id=LOW_HEALTH, severity=80, at=100.0, seq=1, battle_id="b-1",
        detail={"hp_ratio": 0.12, "threshold": 0.15})
    candidate = WowsTacticPolicy(settings).expand(
        [event], WowsFacts(seq=1, at=100.0, battle_id="b-1", own_hp_ratio=0.12))[0]

    request = WowsPromptRouter(settings).build(
        candidate,
        PromptProfile(channel_mode=CHANNEL_DUAL, dry_run=True),
        excerpts,
    )

    assert REFERENCE_OPEN in request.text and REFERENCE_CLOSE in request.text
    assert "不是事实来源" in request.text
    body = request.text.split(REFERENCE_OPEN, 1)[1].split(REFERENCE_CLOSE, 1)[0]
    # Urgent takes one excerpt within a tight character budget.
    assert request.metadata["excerpt_count"] == 1
    assert len(body) <= URGENT_EXCERPT_BUDGET + 200  # plus the title line


def test_a_broken_document_store_does_not_break_retrieval(store):
    """Reference text is optional, so a store failure must degrade to nothing."""
    importer(store).import_text("a.md", "# 甲\n\n巡洋舰应该保持距离")
    repo = repository(store)
    store.close()

    class Broken:
        def chunk_ids_for_tags(self, tags):
            raise RuntimeError("disk gone")

    repo.store = Broken()
    assert repo.search(TacticQuery(summary="巡洋舰距离"), limit=3) == ()
    assert repo.diagnostics.gated is True
