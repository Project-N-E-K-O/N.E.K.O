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
