'use strict';
const assert = require('node:assert/strict');

// Shared actual-page contract: rebuilding the Key Book must preserve management
// fields, including masked credentials, unsaved edits and intentional clearing.
async function verifyManagementSettings({ run, waitFor, state }) {
    await waitFor("window.apiKeySettingsInitialized === true && document.getElementById('doubaoVoiceManagementSecretKey')?.dataset.maskedSecret === 'true'");
    const snapshot = () => run(`(() => {
        const access = document.getElementById('doubaoVoiceManagementAccessKey');
        const secret = document.getElementById('doubaoVoiceManagementSecretKey');
        return { payload: doubaoVoiceManagementSettingsPayload(),
            accessRealKey: access.dataset.realKey, secretRealKey: secret.dataset.realKey,
            accessDisplay: access.value, secretDisplay: secret.value };
    })()`);
    const rebuild = async () => {
        await run("window.previousManagementInput = document.getElementById('doubaoVoiceManagementAccessKey'); window.dispatchEvent(new Event('localechange')); true;");
        await waitFor("document.getElementById('doubaoVoiceManagementAccessKey') !== window.previousManagementInput");
    };
    const initial = await snapshot();
    assert.equal(initial.payload.doubaoVoiceManagementAccessKey, '__NEKO_SECRET_MASKED__');
    assert.equal(initial.payload.doubaoVoiceManagementSecretKey, '__NEKO_SECRET_MASKED__');
    assert.equal(initial.accessRealKey, '');
    assert.equal(initial.secretRealKey, '');
    await run("document.getElementById('doubaoVoiceManagementProjectName').value='Controlled Project'; true;");
    await rebuild();
    const masked = await snapshot();
    assert.deepEqual(masked, { ...initial, payload: { ...initial.payload,
        doubaoVoiceManagementProjectName: 'Controlled Project' } }, 'locale rebuild must preserve masked credentials and unsaved management fields');

    await run("document.getElementById('api-key-form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})); true;");
    await waitFor("document.getElementById('warning-modal').style.display === 'flex'");
    await run("confirmApiKeyChange(); true;");
    await waitFor("!document.getElementById('main-content').inert && document.getElementById('status').textContent.length > 0");
    assert.equal(state.settings.length, 1);
    assert.equal(state.settings[0].doubaoVoiceManagementAccessKey, '__NEKO_SECRET_MASKED__');
    assert.equal(state.settings[0].doubaoVoiceManagementSecretKey, '__NEKO_SECRET_MASKED__');
    assert.equal(state.settings[0].doubaoVoiceManagementProjectName, 'Controlled Project');

    await run(`(() => {
        for (const [id, value] of [
            ['doubaoVoiceManagementAccessKey', 'fake-edited-access'],
            ['doubaoVoiceManagementSecretKey', 'fake-edited-secret']
        ]) {
            const input = document.getElementById(id);
            input.dispatchEvent(new Event('beforeinput'));
            input.value = value;
            input.dispatchEvent(new Event('input'));
            input.dispatchEvent(new Event('blur'));
        }
        document.getElementById('doubaoVoiceManagementAppId').value = '';
        document.getElementById('doubaoVoiceManagementProjectName').value = '';
        return true;
    })()`);
    const edited = await snapshot();
    assert.equal(edited.payload.doubaoVoiceManagementAccessKey, 'fake-edited-access');
    assert.equal(edited.payload.doubaoVoiceManagementSecretKey, 'fake-edited-secret');
    assert.equal(edited.payload.doubaoVoiceManagementAppId, '');
    assert.equal(edited.payload.doubaoVoiceManagementProjectName, '');
    await rebuild();
    assert.deepEqual(await snapshot(), edited, 'locale rebuild must preserve edited secrets and cleared fields');

    // Reloading authoritative server configuration must still replace local edits.
    await run('loadCurrentApiKey()');
    assert.deepEqual(await snapshot(), initial);
    return { maskedManagementCredentialRoundTrip: true, managementFieldsSurviveRebuild: true,
        editedAndClearedManagementFieldsPreserved: true, serverReloadRemainsAuthoritative: true };
}

module.exports = { verifyManagementSettings };
