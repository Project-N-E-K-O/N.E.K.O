// The settings page parses the Bailian API Host the same way the backend does
// (utils/decision_client/workspace.py); these cases mirror the Python tests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../static/js/api_key_settings.js'), 'utf8');

function sourceBetween(startMarker, endMarker) {
    const start = source.indexOf(startMarker);
    const end = source.indexOf(endMarker, start + startMarker.length);
    assert.ok(start >= 0 && end > start, `missing source range ${startMarker}`);
    return source.slice(start, end);
}

function createContext() {
    const elements = new Map();
    const element = (id) => {
        const el = { id, value: '', style: {}, attributes: {}, textContent: '', href: '' };
        el.setAttribute = (name, value) => { el.attributes[name] = value; };
        elements.set(id, el);
        return el;
    };
    ['bailianWorkspaceRow', 'bailianWorkspaceIdInput', 'bailianWorkspaceIdError', 'bailianWorkspaceRegionNote',
        'bailianWorkspaceConsoleLink', 'bailianWorkspaceIdDocLink', 'assistApiSelect', 'enableCustomApi',
        'decisionModelProvider', 'coreApiSelect'].forEach(element);
    const context = vm.createContext({
        document: { getElementById: id => elements.get(id) || null, documentElement: { lang: 'zh-CN' } },
        window: {},
    });
    vm.runInContext([
        sourceBetween('// ==================== 百炼业务空间 API Host（决策模型原生接口） ====================', 'function isAliyunUsApiUrl(url) {'),
        'globalThis.api = { parseBailianWorkspaceHost, applyLoadedBailianWorkspaceHosts, bailianWorkspacePayload, '
            + 'getBailianWorkspaceHostForProvider, updateBailianWorkspaceControls, onBailianWorkspaceInput };',
    ].join('\n'), context);
    return { api: context.api, el: id => elements.get(id) };
}

const plain = value => JSON.parse(JSON.stringify(value));

test('IDs, API Hosts and URLs on them parse to the stored form', () => {
    const { api } = createContext();
    const cases = [
        ['ws-lp9lqfo9ycxyrv5m', 'ws-lp9lqfo9ycxyrv5m', ''],
        ['  LLM-AbC123  ', 'llm-abc123', ''],
        ['ws-abc.cn-beijing.maas.aliyuncs.com', 'ws-abc.cn-beijing.maas.aliyuncs.com', 'cn-beijing'],
        ['WS-Abc.AP-Southeast-1.MAAS.Aliyuncs.com', 'ws-abc.ap-southeast-1.maas.aliyuncs.com', 'ap-southeast-1'],
        ['https://ws-abc.cn-beijing.maas.aliyuncs.com/compatible-mode/v1', 'ws-abc.cn-beijing.maas.aliyuncs.com', 'cn-beijing'],
        ['http://ws-abc.us-east-1.maas.aliyuncs.com/?x=1#y', 'ws-abc.us-east-1.maas.aliyuncs.com', 'us-east-1'],
        ['', '', ''],
    ];
    for (const [raw, value, region] of cases) {
        assert.deepEqual(plain(api.parseBailianWorkspaceHost(raw)), { value, valid: true, region }, raw);
    }
});

test('anything that could point at another host is invalid', () => {
    const { api } = createContext();
    for (const raw of [
        'evil.com', 'llm-abc.evil.com', 'evil.com/x', 'llm-abc@evil.com', 'user:pass@evil', 'llm-abc:443',
        'llm abc', '-llm', 'llm-', 'llm_abc', 'a'.repeat(64), 'llm․evil', 'K',
        'ws-abc.cn-beijing.maas.aliyuncs.com.evil.com', 'ws-abc.cn-beijing-maas-aliyuncs-com',
        'ws-abc.cn-beijing.maas.aliyuncs.com:443',
        'ws-abc.cn-beijing.maas.aliyuncs.com.', 'evil.ws-abc.cn-beijing.maas.aliyuncs.com',
        'ws-abc.maas.aliyuncs.com', 'user@ws-abc.cn-beijing.maas.aliyuncs.com',
        'https://evil.com/ws-abc.cn-beijing.maas.aliyuncs.com', 'ftp://ws-abc.cn-beijing.maas.aliyuncs.com',
        'ws-abc.cn-beijing.maas.aliyuncs.com/' + 'x'.repeat(600),
    ]) {
        assert.equal(api.parseBailianWorkspaceHost(raw).valid, false, raw);
    }
});

test('the row edits the active provider host and flags regions without a decision model', () => {
    const { api, el } = createContext();
    el('assistApiSelect').value = 'qwen_intl';
    api.applyLoadedBailianWorkspaceHosts({
        assistWorkspaceHostQwen: 'llm-bj',
        assistWorkspaceHostQwenIntl: 'ws-sg.ap-southeast-1.maas.aliyuncs.com',
    });
    assert.equal(el('bailianWorkspaceRow').style.display, 'flex');
    assert.equal(el('bailianWorkspaceIdInput').value, 'ws-sg.ap-southeast-1.maas.aliyuncs.com');
    assert.equal(el('bailianWorkspaceConsoleLink').href,
        'https://modelstudio.console.alibabacloud.com/ap-southeast-1/settings/workspace');
    assert.equal(el('bailianWorkspaceRegionNote').style.display, 'none');

    el('bailianWorkspaceIdInput').value = 'https://WS-US.us-east-1.maas.aliyuncs.com/compatible-mode/v1';
    api.onBailianWorkspaceInput();
    assert.equal(el('bailianWorkspaceRegionNote').style.display, 'block');
    assert.match(el('bailianWorkspaceRegionNote').textContent, /us-east-1/);
    assert.deepEqual(plain(api.bailianWorkspacePayload()), {
        assistWorkspaceHostQwen: 'llm-bj',
        assistWorkspaceHostQwenIntl: 'ws-us.us-east-1.maas.aliyuncs.com',
    });
    assert.equal(api.getBailianWorkspaceHostForProvider('qwen_intl'), 'ws-us.us-east-1.maas.aliyuncs.com');

    // An invalid value in the visible field blocks saving.
    el('bailianWorkspaceIdInput').value = 'evil.com';
    api.onBailianWorkspaceInput();
    assert.equal(el('bailianWorkspaceIdError').style.display, 'block');
    assert.equal(api.bailianWorkspacePayload(), null);

    el('assistApiSelect').value = 'qwen';
    api.updateBailianWorkspaceControls();
    assert.equal(el('bailianWorkspaceIdInput').value, 'llm-bj');
    assert.equal(el('bailianWorkspaceConsoleLink').href, 'https://bailian.console.aliyun.com/cn-beijing/settings/workspace');

    el('assistApiSelect').value = 'deepseek';
    api.updateBailianWorkspaceControls();
    assert.equal(el('bailianWorkspaceRow').style.display, 'none');
});
