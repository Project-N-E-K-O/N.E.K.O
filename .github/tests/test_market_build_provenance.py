from pathlib import Path
import re

import yaml

ROOT = Path(__file__).resolve().parents[2]


def workflow():
    return yaml.safe_load((ROOT / '.github/workflows/plugin-market-release.yml').read_text())


def test_only_the_isolated_signing_job_can_request_an_identity():
    jobs = workflow()['jobs']
    build, sign = jobs['release'], jobs['attest']
    assert build['permissions'] == {'contents': 'read'}
    assert sign['permissions'] == {'contents': 'write', 'id-token': 'write', 'attestations': 'write'}
    assert sign['needs'] == 'release'
    assert all('run' not in step for step in sign['steps'])
    assert not any('checkout' in step.get('uses', '') for step in sign['steps'])


def test_signer_consumes_the_exact_immutable_build_artifact():
    jobs = workflow()['jobs']
    assert jobs['release']['outputs']['artifact-id'] == '${{ steps.build-output.outputs.artifact-id }}'
    steps = jobs['attest']['steps']
    assert steps[0]['with']['artifact-ids'] == '${{ needs.release.outputs.artifact-id }}'
    assert 'name' not in steps[0]['with']
    attestation = next(step for step in steps if 'attest-build-provenance@' in step.get('uses', ''))
    assert 'signed-output/market-evidence.json' in attestation['with']['subject-path']
    assert 'signed-output/*.neko-plugin' in attestation['with']['subject-path']
    assert all(re.fullmatch(r'[a-f0-9]{40}', step['uses'].split('@')[1]) for step in steps)


def test_generated_callers_allow_attestation_permissions():
    text = (ROOT / 'plugin/neko_plugin_cli/templates/generator.py').read_text()
    release = text.split('def render_release_workflow', 1)[1]
    assert '  id-token: write\n  attestations: write' in release
    caller = (ROOT / 'plugin/plugins/lifekit/.github/workflows/release.yml').read_text()
    assert '  id-token: write\n  attestations: write' in caller
