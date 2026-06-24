from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_JS = PROJECT_ROOT / "frontend" / "plugin-manager" / "src" / "components" / "plugin" / "hosted" / "ui-kit" / "runtime.js"


def test_radio_group_generates_stable_name_across_renders():
    source = RUNTIME_JS.read_text(encoding="utf-8")
    block = source[
        source.index("function RadioGroup(props)"):
        source.index("function SegmentedControl(props)")
    ]

    assert "const generatedName = useMemo(() => 'radio-' + Math.random().toString(36).slice(2), []);" in block
    assert "const name = props.name || generatedName;" in block
    assert "const name = props.name || ('radio-' + Math.random().toString(36).slice(2));" not in block


def test_accordion_without_id_uses_instance_key_not_title_key():
    source = RUNTIME_JS.read_text(encoding="utf-8")
    block = source[
        source.index("function Accordion(props)"):
        source.index("function Markdown(props)")
    ]

    assert "const fallbackId = useMemo(() => 'instance-' + Math.random().toString(36).slice(2), []);" in block
    assert "const stateKey = `accordion:${props.id || fallbackId}`;" in block
    assert "useLocalState(stateKey, props.open !== false)" in block
    assert "accordion:${props.id || props.title || props.label || 'default'}" not in block
