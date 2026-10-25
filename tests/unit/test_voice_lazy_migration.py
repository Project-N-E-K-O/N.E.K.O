"""Round-trip invariant tests for the union-find-style lazy voice_id migration (voice-source unification §6).

Write side ``ConfigManager.voice_id_to_storage_value`` migrates a user-set legacy
voice_id into the structured object; read side ``read_legacy_voice_id`` reads both the
flat-string and structured-object forms back as the legacy string. Core invariant:
**store → read round-trips back to the original legacy string**, so whether or not an
entry has migrated is transparent to downstream dispatch / validate.
"""

from utils.config_manager import (
    get_config_manager,
    get_reserved,
    migrate_catgirl_reserved,
    set_reserved,
    validate_reserved_schema,
)
from utils.voice_config import VoiceConfig, read_legacy_voice_id


def test_clone_prefix_storage_roundtrip():
    """Clone prefixes (eleven:/gsv:) stored as an object read back == the original string (deterministic, no runtime context)."""
    cm = get_config_manager()
    for vid in ("eleven:abc", "gsv:my_voice"):
        stored = cm.voice_id_to_storage_value(vid)
        assert isinstance(stored, dict)  # User-selected voice -> migrate to a structured object.
        assert read_legacy_voice_id(stored) == vid


def test_empty_voice_stored_as_empty_string():
    cm = get_config_manager()
    assert cm.voice_id_to_storage_value("") == ""
    assert cm.voice_id_to_storage_value(None) == ""
    assert read_legacy_voice_id(cm.voice_id_to_storage_value("")) == ""


def test_object_form_in_char_config_reads_back_legacy():
    """Runtime read: when a character's voice is already an object, get_reserved +
    read_legacy_voice_id yield the legacy string dispatch consumes (i.e. _get_voice_id's behavior)."""
    cfg = {}
    set_reserved(cfg, "voice_id", {"source": "clone", "provider": "elevenlabs", "ref": "abc"})
    raw = get_reserved(cfg, "voice_id", default="", legacy_keys=("voice_id",))
    assert read_legacy_voice_id(raw) == "eleven:abc"


def test_legacy_flat_string_still_reads():
    """Untouched legacy flat strings still read fine (lazy migration never forces a full-table sweep)."""
    cfg = {}
    set_reserved(cfg, "voice_id", "gsv:legacy_voice")
    raw = get_reserved(cfg, "voice_id", default="", legacy_keys=("voice_id",))
    assert read_legacy_voice_id(raw) == "gsv:legacy_voice"


# -- Invariant: characters.json stores binding references into the voice library, never voice objects. --

def test_binding_is_a_reference_not_an_inlined_voice():
    """characters.json stores only the binding reference {source,provider,ref}, never inlining the
    voice library's body fields (audio_md5/file_url/created_at/prefix...) or endpoint config."""
    cm = get_config_manager()
    stored = cm.voice_id_to_storage_value("eleven:abc123")
    assert isinstance(stored, dict)
    assert set(stored.keys()) <= {"source", "provider", "ref"}
    # Voice objects belong only in voice_storage.json (the library), never in bindings.
    forbidden = {"audio_md5", "file_url", "dashscope_base_url", "elevenlabs_base_url",
                 "created_at", "name", "prefix", "config"}
    assert not (set(stored.keys()) & forbidden)


def test_binding_roundtrips_to_exact_library_key():
    """The binding must round-trip to the exact voice-library key (character → library voice link stays intact).

    Library key shapes: elevenlabs/gptsovits are prefixed (voice_storage keys them with the prefix);
    cosyvoice/minimax/free/native are bare ids. store→read restores the same key for each.
    """
    cm = get_config_manager()
    for library_key in ("eleven:abc123", "gsv:my_voice", "cosyclone-001", "voice-tone-X", "Puck"):
        stored = cm.voice_id_to_storage_value(library_key)
        assert read_legacy_voice_id(stored) == library_key, library_key


def test_migrate_preserves_top_level_structured_voice():
    """A character card whose **top-level** voice_id is already a structured object must be
    migrated into _reserved (not skipped), else the later top-level legacy-field cleanup
    pops it and the binding is silently lost on load (Codex/CodeRabbit P2)."""
    # The top-level legacy voice_id is already an object (e.g. an old export/manually edited card), absent from _reserved.
    cfg = {"voice_id": {"source": "clone", "provider": "elevenlabs", "ref": "abc123"}}
    migrate_catgirl_reserved(cfg)
    # Moved into _reserved, and the library key can be read back.
    assert cfg.get("_reserved", {}).get("voice_id") == {
        "source": "clone", "provider": "elevenlabs", "ref": "abc123",
    }
    assert read_legacy_voice_id(get_reserved(cfg, "voice_id", default="", legacy_keys=("voice_id",))) == "eleven:abc123"


def test_migrate_normalizes_top_level_legacy_string():
    """Top-level legacy string voice_id is migrated into _reserved as a string (unchanged behavior)."""
    cfg = {"voice_id": "gsv:legacy"}
    migrate_catgirl_reserved(cfg)
    assert get_reserved(cfg, "voice_id", default="", legacy_keys=("voice_id",)) == "gsv:legacy"


def test_schema_accepts_structured_voice_object():
    """RESERVED_FIELD_SCHEMA accepts the structured object so migrated characters are not
    flagged malformed on every load (Codex P2)."""
    errors = validate_reserved_schema(
        {"voice_id": {"source": "clone", "provider": "elevenlabs", "ref": "abc"}}
    )
    assert not [e for e in errors if "voice_id" in e], errors
    # Legacy flat strings remain valid.
    assert not [e for e in validate_reserved_schema({"voice_id": "gsv:x"}) if "voice_id" in e]


def test_schema_rejects_malformed_voice_object():
    """After widening to (str, dict), the object form still validates source/provider/ref
    are all str, rejecting malformed dicts like {"foo": 1} (CodeRabbit negative case)."""
    errors = validate_reserved_schema({"voice_id": {"foo": 1}})
    assert [e for e in errors if "voice_id" in e], "malformed voice_id dict should be flagged"
    # Also detect an incorrect provider type.
    bad = validate_reserved_schema({"voice_id": {"source": "clone", "provider": 1, "ref": "x"}})
    assert [e for e in bad if "voice_id.provider" in e]


def test_storage_guard_keeps_legacy_string_when_roundtrip_would_change_key(monkeypatch):
    """Round-trip guard (Codex P2): if the structured form would read back to a DIFFERENT key
    (provider-tagged but un-prefixed clone), keep the legacy string so the binding's library
    key is never rewritten."""
    cm = get_config_manager()
    # Simulate normalize classifying bare key 'xyz' as elevenlabs (stored bare in the library with provider metadata).
    monkeypatch.setattr(
        cm, "normalize_voice_id_to_config",
        lambda v: VoiceConfig(source="clone", provider="elevenlabs", ref="xyz"),
    )
    # to_legacy returns 'eleven:xyz', not 'xyz'; the guard retains the original string rather than migrating to an object that changes the key.
    assert cm.voice_id_to_storage_value("xyz") == "xyz"
