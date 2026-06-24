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


def test_form_controls_preserve_falsy_values():
    source = RUNTIME_JS.read_text(encoding="utf-8")
    input_block = source[
        source.index("function Input(props)"):
        source.index("function PasswordInput(props)")
    ]
    textarea_block = source[
        source.index("function Textarea(props)"):
        source.index("function Select(props)")
    ]
    select_block = source[
        source.index("function Select(props)"):
        source.index("function RadioGroup(props)")
    ]

    assert "value: props.value ?? ''" in input_block
    assert "value: props.value ?? ''" in textarea_block
    assert "value: props.value ?? ''" in select_block
    assert "value: props.value || ''" not in input_block
    assert "value: props.value || ''" not in textarea_block
    assert "value: props.value || ''" not in select_block


def test_file_download_open_external_checks_safe_url_and_target_origin():
    source = RUNTIME_JS.read_text(encoding="utf-8")
    hosted_url_block = source[
        source.index("function hostedAbsoluteUrl(href)"):
        source.index("function safeInsert(parentDom, node, anchor)")
    ]
    download_block = source[
        source.index("function FileDownload(props)"):
        source.index("function Form(props)")
    ]

    assert "if (!isSafeUrl(text)) return '';" in hosted_url_block
    assert "return isSafeUrl(absolute) ? absolute : '';" in hosted_url_block
    assert "function hostedTargetOrigin()" in hosted_url_block
    assert "if (!isSafeUrl(href)) return;" in download_block
    assert "if (!url || !isSafeUrl(url)) return;" in download_block
    assert "parent.postMessage({ type: 'neko-hosted-surface-open-external', payload: { url } }, hostedTargetOrigin());" in download_block
    assert "payload: { url } }, '*')" not in download_block
