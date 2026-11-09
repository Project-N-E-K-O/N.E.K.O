from pathlib import Path
import re

import yaml

from plugin.neko_plugin_cli.templates.generator import PluginSpec, render_release_workflow

ROOT = Path(__file__).resolve().parents[2]
PINNED = re.compile(r'[^@\s]+@[a-f0-9]{40}')


def workflow():
    return yaml.safe_load((ROOT / '.github/workflows/plugin-market-release.yml').read_text())


def test_only_the_isolated_signing_job_can_request_an_identity():
    jobs = workflow()['jobs']
    build, evidence, sign = jobs['release'], jobs['evidence'], jobs['attest']
    assert build['permissions'] == {'contents': 'read'}
    assert evidence['permissions'] == {'contents': 'read'}
    assert sign['permissions'] == {'contents': 'write', 'id-token': 'write', 'attestations': 'write'}
    assert evidence['needs'] == 'release'
    assert sign['needs'] == 'evidence'
    assert all('run' not in step for step in sign['steps'])
    for job in (evidence, sign):
        assert not any('checkout' in step.get('uses', '') for step in job['steps'])
        assert all(PINNED.fullmatch(step['uses']) for step in job['steps'] if 'uses' in step)


def test_evidence_is_written_outside_the_job_that_runs_plugin_code():
    jobs = workflow()['jobs']
    build_steps = [step['name'] for step in jobs['release']['steps']]
    assert 'Write Market evidence' not in build_steps
    assert 'market-evidence.json' not in jobs['release']['steps'][-1]['with']['path']
    # Values handed to the evidence job are fixed before plugin code runs.
    outputs = jobs['release']['outputs']
    assert outputs['neko-commit'] == '${{ steps.neko.outputs.commit }}'
    assert outputs['plugin-directory'] == '${{ steps.mount.outputs.plugin-directory }}'
    assert outputs['package-name'] == '${{ steps.mount.outputs.package-name }}'
    first_plugin_code = build_steps.index('Sync plugin dependencies')
    assert build_steps.index('Checkout N.E.K.O') < first_plugin_code
    assert build_steps.index('Mount plugin into N.E.K.O tree') < first_plugin_code
    env = jobs['evidence']['env']
    assert env['NEKO_COMMIT'] == '${{ needs.release.outputs.neko-commit }}'
    assert env['MARKET_PACKAGE_NAME'] == '${{ needs.release.outputs.package-name }}'
    steps = jobs['evidence']['steps']
    assert steps[0]['with']['artifact-ids'] == '${{ needs.release.outputs.artifact-id }}'
    assert [step['name'] for step in steps].index('Write Market evidence') == 1


def test_signer_consumes_the_exact_immutable_evidence_artifact():
    jobs = workflow()['jobs']
    assert jobs['release']['outputs']['artifact-id'] == '${{ steps.build-output.outputs.artifact-id }}'
    assert jobs['evidence']['outputs']['artifact-id'] == '${{ steps.signing-input.outputs.artifact-id }}'
    steps = jobs['attest']['steps']
    assert steps[0]['with']['artifact-ids'] == '${{ needs.evidence.outputs.artifact-id }}'
    assert 'name' not in steps[0]['with']
    attestation = next(step for step in steps if 'attest-build-provenance@' in step.get('uses', ''))
    assert 'signed-output/market-evidence.json' in attestation['with']['subject-path']
    assert 'signed-output/*.neko-plugin' in attestation['with']['subject-path']


def test_generated_callers_allow_attestation_permissions():
    for caller in (
        render_release_workflow(PluginSpec(plugin_id='demo')),
        (ROOT / 'plugin/plugins/lifekit/.github/workflows/release.yml').read_text(),
    ):
        permissions = yaml.safe_load(caller)['permissions']
        assert permissions['id-token'] == 'write'
        assert permissions['attestations'] == 'write'


def test_retries_replace_only_the_published_evidence_artifact():
    jobs = workflow()['jobs']
    sign = jobs['attest']
    upload = next(step for step in sign['steps'] if step['name'] == 'Upload Market evidence')
    assert upload['with']['overwrite'] is True
    assert sign['timeout-minutes'] >= 30
    templates = [jobs['release']['steps'][-1]['with']['name'], jobs['evidence']['steps'][-1]['with']['name'],
                 upload['with']['name']]
    # Overwriting the build or signing input would break a later attest retry by artifact id.
    for plugin_id in ('market', 'market_build', 'market_signing_input'):
        context = {'github.run_id': '123', 'github.run_attempt': '1', 'inputs.plugin-id': plugin_id}
        assert len({resolve(template, context) for template in templates}) == 3
    assert all('overwrite' not in job['steps'][-1]['with'] for job in (jobs['release'], jobs['evidence']))


def resolve(template, context):
    return re.sub(r'\$\{\{\s*([^}\s]+)\s*\}\}', lambda match: context[match.group(1)], template)
