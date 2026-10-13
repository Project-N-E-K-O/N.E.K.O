const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const projectRoot = path.resolve(__dirname, '..', '..');
const profileSource = fs.readFileSync(
    path.join(projectRoot, 'static/vrm/vrm-physics-profile.js'),
    'utf8'
);

function loadProfileClass(extraGlobals) {
    const sandbox = Object.assign({ window: {}, console }, extraGlobals || {});
    vm.createContext(sandbox);
    vm.runInContext(profileSource, sandbox);
    return { VrmPhysicsProfile: sandbox.window.VrmPhysicsProfile, sandbox };
}

function makeCollider(boneName, radius) {
    const bone = { name: boneName, children: [] };
    const collider = { shape: { radius, offset: { x: 0, y: 0, z: 0 } } };
    bone.children.push(collider);
    collider.parent = bone;
    return collider;
}

function makeManager(colliders, joints) {
    return {
        colliders: new Set(colliders || []),
        joints: new Set(joints || [])
    };
}

test('collider bone name comes from the parent node, not a bone property', () => {
    const { VrmPhysicsProfile } = loadProfileClass();
    const profile = new VrmPhysicsProfile();
    profile.profile = {
        colliders: [{ bone: 'J_Bip_C_Head', shape: 'sphere', radius: 0.08 }],
        chains: []
    };
    const collider = makeCollider('J_Bip_C_Head', 0.2);
    assert.equal(collider.bone, undefined);
    const manager = makeManager([collider]);
    const applied = profile.applyColliders(manager);
    assert.equal(applied, 1, 'profile radius must be applied through the parent bone name');
    assert.equal(collider.shape.radius, 0.08);
});

test('unmatched colliders fall back to the default reduction', () => {
    const { VrmPhysicsProfile } = loadProfileClass();
    const profile = new VrmPhysicsProfile();
    profile.profile = {
        colliders: [{ bone: 'J_Bip_C_Head', shape: 'sphere', radius: 0.08 }],
        chains: []
    };
    const matched = makeCollider('J_Bip_C_Head', 0.2);
    const other = makeCollider('J_Bip_L_Hand', 0.3);
    const applied = profile.applyColliders(makeManager([matched, other]));
    assert.equal(applied, 1);
    assert.equal(matched.shape.radius, 0.08);
    assert.equal(other.shape.radius, 0.15);
});

test('two colliders on one bone keep their own radii', () => {
    const { VrmPhysicsProfile } = loadProfileClass();
    const profile = new VrmPhysicsProfile();
    profile.profile = {
        colliders: [
            { bone: 'J_Bip_C_Head', shape: 'sphere', radius: 0.04 },
            { bone: 'J_Bip_C_Head', shape: 'capsule', radius: 0.11 }
        ],
        chains: []
    };
    const first = makeCollider('J_Bip_C_Head', 0.5);
    const second = makeCollider('J_Bip_C_Head', 0.5);
    const applied = profile.applyColliders(makeManager([first, second]));
    assert.equal(applied, 2);
    const radii = [first.shape.radius, second.shape.radius].sort((a, b) => a - b);
    assert.deepEqual(radii, [0.04, 0.11]);
});

test('joint settings are written onto the spring bone joint', () => {
    const { VrmPhysicsProfile } = loadProfileClass();
    const profile = new VrmPhysicsProfile();
    profile.profile = {
        colliders: [],
        chains: [{
            name: 'hair_front:hair_front',
            bones: ['hair_front_01'],
            stiffness: 0.6,
            drag_force: 0.25,
            gravity_power: 0.3,
            hit_radius: 0.02,
            gravity_dir: [0, -1, 0]
        }]
    };
    const gravityDir = { values: null, set(x, y, z) { this.values = [x, y, z]; } };
    const joint = { bone: { name: 'hair_front_01' }, settings: { stiffness: 1, gravityDir } };
    const applied = profile.applyJoints(makeManager([], [joint]));
    assert.equal(applied, 1);
    assert.equal(joint.settings.stiffness, 0.6);
    assert.equal(joint.settings.dragForce, 0.25);
    assert.equal(joint.settings.gravityPower, 0.3);
    assert.equal(joint.settings.hitRadius, 0.02);
    assert.deepEqual(gravityDir.values, [0, -1, 0]);
});

test('fields the profile omits are left untouched', () => {
    const { VrmPhysicsProfile } = loadProfileClass();
    const profile = new VrmPhysicsProfile();
    profile.profile = {
        colliders: [],
        chains: [{ name: 'x', bones: ['hair_01'], stiffness: 0.2 }]
    };
    const joint = { bone: { name: 'hair_01' }, settings: { stiffness: 1, dragForce: 0.9, hitRadius: 0.05 } };
    profile.applyJoints(makeManager([], [joint]));
    assert.equal(joint.settings.stiffness, 0.2);
    assert.equal(joint.settings.dragForce, 0.9);
    assert.equal(joint.settings.hitRadius, 0.05);
});

test('default reduction reports how many colliders it touched', () => {
    const { VrmPhysicsProfile } = loadProfileClass();
    const profile = new VrmPhysicsProfile();
    const first = makeCollider('A', 0.4);
    const second = makeCollider('B', 0.6);
    const applied = profile.applyDefaultReduction(makeManager([first, second]));
    assert.equal(applied, 2);
    assert.equal(first.shape.radius, 0.2);
    assert.equal(second.shape.radius, 0.3);
    assert.equal(profile.source, 'default');
});

test('default reduction is idempotent across repeated calls', () => {
    const { VrmPhysicsProfile } = loadProfileClass();
    const profile = new VrmPhysicsProfile();
    const collider = makeCollider('A', 0.4);
    const manager = makeManager([collider]);
    profile.applyDefaultReduction(manager);
    profile.applyDefaultReduction(manager);
    assert.equal(collider.shape.radius, 0.2);
});

test('a superseded load does not publish the older response', async () => {
    const pending = [];
    const { VrmPhysicsProfile } = loadProfileClass({
        fetch: (url) => new Promise((resolve) => {
            pending.push({ url, resolve });
        }),
        AbortSignal: undefined
    });
    const profile = new VrmPhysicsProfile();
    const slow = profile.load('model_a');
    const fast = profile.load('model_b');
    assert.equal(pending.length, 2);
    pending[1].resolve({
        ok: true,
        json: async () => ({ success: true, config: { colliders: [], chains: [], model: 'model_b' } })
    });
    await fast;
    pending[0].resolve({
        ok: true,
        json: async () => ({ success: true, config: { colliders: [], chains: [], model: 'model_a' } })
    });
    await slow;
    assert.equal(profile.hasProfile, true);
    assert.equal(profile.lastModel, 'model_b');
    assert.equal(profile.profile.model, 'model_b');
});

test('a failing fetch resolves without a profile', async () => {
    const { VrmPhysicsProfile } = loadProfileClass({
        fetch: async () => {
            throw new Error('network down');
        },
        AbortSignal: undefined
    });
    const profile = new VrmPhysicsProfile();
    const result = await profile.load('model_a');
    assert.equal(result, null);
    assert.equal(profile.hasProfile, false);
    assert.equal(profile.source, 'default');
});

test('a response carrying no config resolves without a profile', async () => {
    const { VrmPhysicsProfile } = loadProfileClass({
        fetch: async () => ({
            ok: true,
            json: async () => ({ success: true, config: null, source: 'absent' })
        }),
        AbortSignal: undefined
    });
    const profile = new VrmPhysicsProfile();
    const result = await profile.load('model_a');
    assert.equal(result, null);
    assert.equal(profile.hasProfile, false);
});

test('the request URL encodes the model name exactly once', async () => {
    const seen = [];
    const { VrmPhysicsProfile } = loadProfileClass({
        fetch: async (url) => {
            seen.push(url);
            return { ok: true, json: async () => ({ success: true, config: null }) };
        },
        AbortSignal: undefined
    });
    const profile = new VrmPhysicsProfile();
    await profile.load('小春');
    assert.equal(seen.length, 1);
    assert.equal(seen[0], '/api/model/vrm/physics_profile/' + encodeURIComponent('小春'));
    assert.equal(decodeURIComponent(seen[0].split('/').pop()), '小春');
});

test('the model name is decoded out of the quoted URL before lookup', () => {
    const source = fs.readFileSync(path.join(projectRoot, 'static/vrm/vrm-manager.js'), 'utf8');
    const start = source.indexOf('_resolvePhysicsProfileModelName(modelUrl) {');
    assert.notEqual(start, -1, 'missing _resolvePhysicsProfileModelName');
    const end = source.indexOf('\n    }\n', start) + 6;
    const method = source.slice(start, end).replace(
        '_resolvePhysicsProfileModelName(modelUrl) {',
        'function (modelUrl) {'
    );
    const resolve = eval('(' + method + ')');
    const cases = [
        ['/static/vrm/sister1.0.vrm', 'sister1.0'],
        ['/user_vrm/%E5%B0%8F%E6%98%A5.vrm', '小春'],
        ['/static/vrm/My%20Avatar.vrm', 'My Avatar'],
        ['/workshop/123/model(1).vrm', 'model(1)'],
        ['/static/vrm/a.vrm?v=123', 'a'],
        ['/static/vrm/a.vrm#frag', 'a'],
        ['', ''],
        ['/static/vrm/', ''],
        ['%E0%A4%A.vrm', ''],
        ['/static/vrm/nodot', 'nodot']
    ];
    for (const [input, expected] of cases) {
        assert.equal(resolve(input), expected, `resolve(${JSON.stringify(input)})`);
    }
});

test('the request is aborted when the fetch stalls', async () => {
    const { VrmPhysicsProfile } = loadProfileClass({
        AbortController: AbortController,
        setTimeout: setTimeout,
        clearTimeout: clearTimeout,
        fetch: (url, options) => new Promise((resolve, reject) => {
            assert.ok(options && options.signal, 'a signal must be attached to the request');
            options.signal.addEventListener('abort', () => {
                const err = new Error('aborted');
                err.name = 'AbortError';
                reject(err);
            });
        })
    });
    const profile = new VrmPhysicsProfile();
    const started = Date.now();
    const result = await profile.load('stalled_model', 60);
    const elapsed = Date.now() - started;
    assert.equal(result, null);
    assert.equal(profile.hasProfile, false);
    assert.ok(elapsed < 2000, `load must return promptly, took ${elapsed}ms`);
});

test('the request still carries a signal when AbortSignal.timeout is unavailable', async () => {
    let sawSignal = false;
    const { VrmPhysicsProfile } = loadProfileClass({
        AbortController: AbortController,
        AbortSignal: undefined,
        setTimeout: setTimeout,
        clearTimeout: clearTimeout,
        fetch: async (url, options) => {
            sawSignal = !!(options && options.signal);
            return { ok: true, json: async () => ({ success: true, config: null }) };
        }
    });
    const profile = new VrmPhysicsProfile();
    await profile.load('model_a');
    assert.equal(sawSignal, true);
});

test('a zero radius in the profile disables the collider instead of falling back', () => {
    const { VrmPhysicsProfile } = loadProfileClass();
    const profile = new VrmPhysicsProfile();
    profile.profile = {
        colliders: [{ bone: 'J_Bip_C_Head', shape: 'sphere', radius: 0 }],
        chains: []
    };
    const collider = makeCollider('J_Bip_C_Head', 0.2);
    const applied = profile.applyColliders(makeManager([collider]));
    assert.equal(applied, 1);
    assert.equal(collider.shape.radius, 0);
});

test('two colliders on one bone are matched by geometry, not by list position', () => {
    const { VrmPhysicsProfile } = loadProfileClass();
    const profile = new VrmPhysicsProfile();
    profile.profile = {
        colliders: [
            { bone: 'J_Bip_C_Head', shape: 'capsule', radius: 0.11, offset: [0, 0, 0], tail: [0, 0.2, 0] },
            { bone: 'J_Bip_C_Head', shape: 'sphere', radius: 0.04, offset: [0, 0, 0], tail: null }
        ],
        chains: []
    };
    const sphere = makeCollider('J_Bip_C_Head', 0.5);
    sphere.shape.type = 'sphere';
    sphere.shape.offset = { x: 0, y: 0, z: 0 };
    const capsule = makeCollider('J_Bip_C_Head', 0.5);
    capsule.shape.type = 'capsule';
    capsule.shape.offset = { x: 0, y: 0, z: 0 };
    capsule.shape.tail = { x: 0, y: 0.2, z: 0 };
    const applied = profile.applyColliders(makeManager([sphere, capsule]));
    assert.equal(applied, 2);
    assert.equal(sphere.shape.radius, 0.04);
    assert.equal(capsule.shape.radius, 0.11);
});

test('a profile entry missing from the runtime does not shift the other radii', () => {
    const { VrmPhysicsProfile } = loadProfileClass();
    const profile = new VrmPhysicsProfile();
    profile.profile = {
        colliders: [
            { bone: 'J_Bip_C_Head', shape: 'sphere', radius: 0.04, offset: [0, 0, 0] },
            { bone: 'J_Bip_C_Head', shape: 'sphere', radius: 0.09, offset: [0, 0.1, 0] }
        ],
        chains: []
    };
    const runtime = makeCollider('J_Bip_C_Head', 0.5);
    runtime.shape.type = 'sphere';
    runtime.shape.offset = { x: 0, y: 0.1, z: 0 };
    const applied = profile.applyColliders(makeManager([runtime]));
    assert.equal(applied, 1);
    assert.equal(runtime.shape.radius, 0.09);
});

test('spring state is reinitialized after the profile is applied', () => {
    const { VrmPhysicsProfile } = loadProfileClass();
    const profile = new VrmPhysicsProfile();
    profile.profile = { colliders: [], chains: [] };
    let resetCalls = 0;
    const manager = makeManager([], []);
    manager.reset = () => {
        resetCalls += 1;
    };
    const result = profile.apply(manager);
    assert.equal(resetCalls, 1);
    assert.equal(result.reset, true);
});

test('a manager without reset does not break the apply path', () => {
    const { VrmPhysicsProfile } = loadProfileClass();
    const profile = new VrmPhysicsProfile();
    profile.profile = { colliders: [], chains: [] };
    const result = profile.apply(makeManager([], []));
    assert.equal(result.reset, false);
});

test('profile bone names survive the GLTFLoader node sanitization', () => {
    const { VrmPhysicsProfile } = loadProfileClass();
    const profile = new VrmPhysicsProfile();
    profile.profile = {
        colliders: [{ bone: 'hair.001', shape: 'sphere', radius: 0.03, offset: [0, 0, 0] }],
        chains: [{ bones: ['upper_arm.L'], stiffness: 0.7 }]
    };
    const collider = makeCollider('hair001', 0.5);
    collider.shape.type = 'sphere';
    collider.shape.offset = { x: 0, y: 0, z: 0 };
    const applied = profile.applyColliders(makeManager([collider]));
    assert.equal(applied, 1);
    assert.equal(collider.shape.radius, 0.03);

    const joint = { bone: { name: 'upper_armL' }, settings: {} };
    const manager = makeManager([], [joint]);
    const joints = profile.applyJoints(manager);
    assert.equal(joints, 1);
    assert.equal(joint.settings.stiffness, 0.7);
});

test('a bone name with spaces and brackets is sanitized the same way', () => {
    const { VrmPhysicsProfile } = loadProfileClass();
    const profile = new VrmPhysicsProfile();
    profile.profile = {
        colliders: [{ bone: 'Hair Front [L]', shape: 'sphere', radius: 0.05, offset: [0, 0, 0] }],
        chains: []
    };
    const collider = makeCollider('Hair_Front_L', 0.5);
    collider.shape.type = 'sphere';
    collider.shape.offset = { x: 0, y: 0, z: 0 };
    const applied = profile.applyColliders(makeManager([collider]));
    assert.equal(applied, 1);
    assert.equal(collider.shape.radius, 0.05);
});

test('a geometry mismatch stays unmatched instead of stealing the next entry', () => {
    const { VrmPhysicsProfile } = loadProfileClass();
    const profile = new VrmPhysicsProfile();
    profile.profile = {
        colliders: [
            { bone: 'Head', shape: 'sphere', radius: 0.09, offset: [0, 0.1, 0] },
            { bone: 'Head', shape: 'sphere', radius: 0.04, offset: [0, 0, 0] }
        ],
        chains: []
    };
    const first = makeCollider('Head', 0.5);
    first.shape.type = 'sphere';
    first.shape.offset = { x: 0, y: 0, z: 0 };
    const second = makeCollider('Head', 0.5);
    second.shape.type = 'sphere';
    second.shape.offset = { x: 0, y: 0.1, z: 0 };
    const applied = profile.applyColliders(makeManager([first, second]));
    assert.equal(applied, 2);
    assert.equal(first.shape.radius, 0.04);
    assert.equal(second.shape.radius, 0.09);
});

test('an unmatched collider does not take an entry that belongs to another collider', () => {
    const { VrmPhysicsProfile } = loadProfileClass();
    const profile = new VrmPhysicsProfile();
    profile.profile = {
        colliders: [{ bone: 'Head', shape: 'sphere', radius: 0.07, offset: [0, 0.9, 0] }],
        chains: []
    };
    const stranger = makeCollider('Head', 0.5);
    stranger.shape.type = 'capsule';
    stranger.shape.offset = { x: 0, y: 0, z: 0 };
    const applied = profile.applyColliders(makeManager([stranger]));
    assert.equal(applied, 0, 'a collider whose geometry differs must not take the entry');
    assert.equal(stranger.shape.radius, 0.25);
});

test('an entry without geometry metadata falls back to positional pairing', () => {
    const { VrmPhysicsProfile } = loadProfileClass();
    const profile = new VrmPhysicsProfile();
    profile.profile = {
        colliders: [{ bone: 'Head', radius: 0.06 }],
        chains: []
    };
    const collider = makeCollider('Head', 0.5);
    collider.shape.type = 'sphere';
    collider.shape.offset = { x: 0, y: 0, z: 0 };
    const applied = profile.applyColliders(makeManager([collider]));
    assert.equal(applied, 1);
    assert.equal(collider.shape.radius, 0.06);
});

test('configured radii are scaled by the current scene scale', () => {
    const { VrmPhysicsProfile } = loadProfileClass();
    const profile = new VrmPhysicsProfile();
    profile.profile = {
        colliders: [{ bone: 'Head', shape: 'sphere', radius: 0.1, offset: [0, 0, 0] }],
        chains: []
    };
    const collider = makeCollider('Head', 0.5);
    collider.shape.type = 'sphere';
    collider.shape.offset = { x: 0, y: 0, z: 0 };
    profile.applyColliders(makeManager([collider]), 0.5);
    assert.equal(collider.shape.radius, 0.05);
    assert.equal(collider._originalRadius, 0.1);
});
