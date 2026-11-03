const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const source = fs.readFileSync(path.resolve(__dirname, '../../static/app/app-interpage/bootstrap-resources-and-model-reload.js'), 'utf8');
const start = source.indexOf('    I.handleModelReload = async function');
const actualReload = source.slice(start, source.indexOf('\n    }', start) + 7).trim();
assert.ok(start > 0 && actualReload.endsWith('}'));

const commitStart = source.indexOf('await _waitForLive2DManagerIdle(30000);', source.indexOf('// Ensure PIXI app is initialised'));
const commitEnd = source.indexOf('preparedLive2DModel = null;', commitStart) + 'preparedLive2DModel = null;'.length;
const actualCommit = source.slice(commitStart, commitEnd);
assert.ok(commitStart > start && actualCommit.includes('loadUserPreferences'));
const rollbackStart = source.indexOf('            // 回滚提前写入的 config');
const actualRollback = source.slice(rollbackStart, source.indexOf('            if (!suppressToast) {', rollbackStart));
assert.ok(rollbackStart > commitEnd && actualRollback.includes('oldModelType'));

for (const oldType of ['vrm', 'pngtuber']) for (const phase of ['idle', 'preferences', 'preferences-error', 'switching', 'unchanged', 'load-error']) {
    test(`cloud candidate rechecks its ${oldType} role after ${phase}`, async () => {
        const current = {}, next = {}, candidate = {};
        let loads = 0;
        const context = {
            console: { log() {}, warn() {}, error() {} },
            reloadOptions: { preservePreviousUntilLoaded: true }, currentLanlanName: 'Synthetic',
            newModelPath: '/candidate.model3.json', preparedLive2DModel: candidate,
            typeChanged: true, oldModelType: oldType, oldLive3dSubType: 'mmd',
            pngRestores: 0, loadPNGTuberAvatar: async () => { context.pngRestores++; },
            skipIdleRestore: false, skipPersistentExpressions: false, innerWidth: 1280,
            lanlan_config: { lanlan_name: 'Synthetic', model_type: 'live2d', live3d_sub_type: '', pngtuber: {} },
            document: { getElementById() { return null; } },
        };
        function switchRole() {
            if (phase === 'switching') context.appState = { isSwitchingCatgirl: true };
            else context.lanlan_config.lanlan_name = 'Other';
            context.live2dManager.currentModel = next;
        }
        context._waitForLive2DManagerIdle = async () => { if (phase === 'idle') switchRole(); };
        context.live2dManager = {
            currentModel: current,
            loadUserPreferences: async () => {
                if (phase.startsWith('preferences') || phase === 'switching') switchRole();
                if (phase === 'preferences-error') throw new Error('preferences unavailable');
                return [];
            },
            loadModel: async () => {
                loads++;
                if (phase === 'load-error') throw new Error('load_failed');
                context.live2dManager.currentModel = candidate;
            },
        };
        context.window = context;
        vm.createContext(context);
        vm.runInContext('async function commitCandidate(){try{' + actualCommit + '}catch(error){' + actualRollback + 'throw error;}}', context);
        if (phase === 'unchanged') {
            await context.commitCandidate();
            assert.equal(loads, 1);
            assert.equal(context.live2dManager.currentModel, candidate);
        } else if (phase === 'load-error') {
            await assert.rejects(context.commitCandidate(), /load_failed/);
            assert.equal(loads, 1);
            assert.equal(context.live2dManager.currentModel, current);
            assert.equal(context.lanlan_config.model_type, oldType);
            assert.equal(context.lanlan_config.live3d_sub_type, 'mmd');
            assert.equal(context.pngRestores, oldType === 'pngtuber' ? 1 : 0);
        } else {
            await assert.rejects(context.commitCandidate(), /model_role_changed/);
            assert.equal(loads, 0);
            assert.equal(context.live2dManager.currentModel, next);
            assert.equal(context.lanlan_config.model_type, 'live2d');
            assert.equal(context.lanlan_config.live3d_sub_type, '');
            assert.equal(context.pngRestores, 0);
        }
    });
}

for (const type of ['live2d', 'vrm']) for (const roleChanges of [false, true]) {
    test(`cloud candidate ${roleChanges ? 'role takeover' : 'load failure'} retains the existing ${type}`, async () => {
        const old = {};
        let cleanups = 0, disposals = 0;
        const context = {
            I: { isMainUIHiddenByModelManager: () => false,
                cleanupLive2DOverlayUI() { cleanups++; }, cleanupVRMOverlayUI() { cleanups++; } },
            console: { log() {}, warn() {}, error() {} }, Date, JSON, Promise, setTimeout, clearTimeout,
            _isModelHostPage: () => true, activeMMDReloadCanvasSessionId: '', deferredMainUIShowAfterModelReload: null,
            lanlan_config: { lanlan_name: 'Synthetic', model_type: type }, live2dManager: { currentModel: old },
            Live2DModel: { from: async () => {
                if (!roleChanges) throw new Error('invalid_model');
                context.lanlan_config.lanlan_name = 'Other';
                return { destroy() { disposals++; } };
            } },
        };
        context.window = context;
        vm.createContext(context);
        vm.runInContext(actualReload, context);
        await assert.rejects(context.I.handleModelReload('Synthetic', {
            temporaryConfig: { model_type: 'live2d', model_path: '/user_live2d/candidate/model.model3.json' },
            suppressToast: true, throwOnError: true, preservePreviousUntilLoaded: true,
        }), roleChanges ? /model_role_changed/ : /invalid_model/);
        assert.equal(context.live2dManager.currentModel, old);
        assert.equal(context.lanlan_config.model_type, type);
        assert.equal(context._modelReloadInFlight, false);
        assert.equal(cleanups, 0);
        assert.equal(disposals, roleChanges ? 1 : 0);
    });
}

const modelSource = fs.readFileSync(path.resolve(__dirname, '../../static/live2d/live2d-model.js'), 'utf8');
const modelStart = modelSource.indexOf('Live2DManager.prototype.loadModel = async function');
const loadModelSource = modelSource.slice(modelStart, modelSource.indexOf('\n// 检查加载令牌', modelStart));

for (const phase of ['before-load', 'during-prepare', 'during-remove', 'switch-finished', 'name-changed']) {
    test(`real candidate loader rejects a stale role: ${phase}`, async () => {
        const previous = {}, candidate = { destroy() { this.destroyed = true; } };
        let release, configured = 0, removes = 0;
        const context = {
            Live2DManager: function () {},
            Live2DModel: { from: () => new Promise(resolve => { release = () => resolve(candidate); }) },
            document: { body: { classList: { contains: () => false } } },
            location: { pathname: '/' },
            lanlan_config: { lanlan_name: 'Synthetic', model_type: 'live2d' },
            appState: { isSwitchingCatgirl: phase === 'before-load', _switchAttemptCounter: 1 },
            console: { log() {}, warn() {}, error() {} }, clearTimeout,
        };
        context.window = context;
        vm.createContext(context);
        vm.runInContext(loadModelSource, context);
        const manager = Object.assign(new context.Live2DManager(), {
            pixi_app: { view: { style: {} } }, currentModel: previous,
            _activeLoadToken: 0, _modelLoadState: 'ready', _isModelReadyForInteraction: true,
            _invalidateParameterReloadTickets() {}, _resetDerivedModelMetadata() {},
            _isLoadTokenActive(token) { return token === this._activeLoadToken; },
            removeModel() {
                removes++;
                return new Promise(resolve => { release = () => { this.currentModel = null; resolve(); }; });
            },
            async _configureLoadedModel() { configured++; },
        });
        const operation = manager.loadModel('/candidate', {
            preservePreviousUntilLoaded: true,
            ...(phase === 'during-prepare' ? {} : { preparedModel: candidate }),
        });
        if (release) {
            if (phase === 'name-changed') context.lanlan_config.lanlan_name = 'Other';
            else {
                context.appState._switchAttemptCounter++;
                context.appState.isSwitchingCatgirl = phase !== 'switch-finished';
            }
            release();
        }
        await assert.rejects(operation, error => error.name === 'LoadSuperseded');
        assert.notEqual(manager.currentModel, candidate);
        assert.equal(configured, 0);
        assert.equal(removes, ['before-load', 'during-prepare'].includes(phase) ? 0 : 1);
        if (phase !== 'before-load') assert.equal(candidate.destroyed, true);
    });
}

for (const prepared of [false, true]) for (const failConfiguration of [false, true]) {
    test(`retained model metadata survives candidate preparation; prepared=${prepared}, configureFailure=${failConfiguration}`, async () => {
        const old = { id: 'old' };
        let disposed = 0;
        const candidate = { destroy() { disposed++; } };
        const context = {
            Live2DManager: function () {}, Live2DModel: { from: async () => { throw new Error('prepare-failed'); } },
            document: { body: { classList: { contains: () => false } } },
            location: { pathname: '/' }, lanlan_config: { model_type: 'live2d' },
            console: { log() {}, warn() {}, error() {} }, clearTimeout,
        };
        context.window = context;
        vm.createContext(context);
        vm.runInContext(loadModelSource, context);
        const manager = Object.assign(new context.Live2DManager(), {
            pixi_app: { view: { style: {} } }, currentModel: old,
            _activeLoadToken: 0, _modelLoadState: 'ready', _isModelReadyForInteraction: true, metadata: 'old-metadata',
            _invalidateParameterReloadTickets() {}, _isLoadTokenActive: () => true,
            _resetDerivedModelMetadata() { this.metadata = null; },
            async removeModel() { this.currentModel = null; },
            async _configureLoadedModel() {
                this.metadata = 'candidate-metadata';
                if (failConfiguration) throw new Error('configure-failed');
                this._modelLoadState = 'ready'; this._isModelReadyForInteraction = true;
            },
        });
        const options = { preservePreviousUntilLoaded: true, ...(prepared ? { preparedModel: candidate } : {}) };
        if (!prepared || failConfiguration) {
            await assert.rejects(manager.loadModel('/candidate', options), prepared ? /configure-failed/ : /prepare-failed/);
            assert.equal(manager.currentModel, prepared ? null : old);
            assert.equal(manager.metadata, prepared ? null : 'old-metadata');
            assert.equal(manager._isModelReadyForInteraction, !prepared);
            assert.equal(disposed, prepared ? 1 : 0);
        } else {
            assert.equal(await manager.loadModel('/candidate', options), candidate);
            assert.equal(manager.metadata, 'candidate-metadata');
        }
        assert.equal(manager._isLoadingModel, false);
    });
}
