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

test('bones whose names sanitize alike match by the original glTF name', () => {
    const { VrmPhysicsProfile } = loadProfileClass();
    const profile = new VrmPhysicsProfile();
    profile.profile = {
        colliders: [
            { bone: 'hair.001', shape: 'sphere', radius: 0.01 },
            { bone: 'hair001', shape: 'sphere', radius: 0.02 }
        ],
        chains: [
            { bones: ['hair.001'], stiffness: 0.3 },
            { bones: ['hair001'], stiffness: 0.8 }
        ]
    };
    // GLTFLoader: node.name is sanitized and de-duplicated, userData.name keeps the original.
    // Runtime order is reversed on purpose so positional pairing cannot pass by accident.
    const second = makeCollider('hair001_1', 0.5);
    second.parent.userData = { name: 'hair001' };
    const first = makeCollider('hair001', 0.5);
    first.parent.userData = { name: 'hair.001' };
    [first, second].forEach((c) => { c.shape.type = 'sphere'; });
    assert.equal(profile.applyColliders(makeManager([second, first])), 2);
    assert.equal(first.shape.radius, 0.01);
    assert.equal(second.shape.radius, 0.02);

    const jointSecond = { bone: { name: 'hair001_1', userData: { name: 'hair001' } }, settings: {} };
    const jointFirst = { bone: { name: 'hair001', userData: { name: 'hair.001' } }, settings: {} };
    assert.equal(profile.applyJoints(makeManager([], [jointSecond, jointFirst])), 2);
    assert.equal(jointFirst.settings.stiffness, 0.3);
    assert.equal(jointSecond.settings.stiffness, 0.8);
});

test('a bone with an original name absent from the profile does not borrow a sanitized match', () => {
    const { VrmPhysicsProfile } = loadProfileClass();
    const profile = new VrmPhysicsProfile();
    profile.profile = {
        colliders: [{ bone: 'hair001', shape: 'sphere', radius: 0.02 }],
        chains: [{ bones: ['hair001'], stiffness: 0.8 }]
    };
    const stranger = makeCollider('hair001', 0.5);
    stranger.parent.userData = { name: 'hair.001' };
    stranger.shape.type = 'sphere';
    assert.equal(profile.applyColliders(makeManager([stranger])), 0);
    assert.equal(stranger.shape.radius, 0.25);

    const joint = { bone: { name: 'hair001', userData: { name: 'hair.001' } }, settings: { stiffness: 1 } };
    assert.equal(profile.applyJoints(makeManager([], [joint])), 0);
    assert.equal(joint.settings.stiffness, 1);
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

test('the configured hit radius follows the scene scale too', () => {
    const { VrmPhysicsProfile } = loadProfileClass();
    const profile = new VrmPhysicsProfile();
    profile.profile = {
        colliders: [],
        chains: [{ bones: ['Head'], stiffness: 0.4, hit_radius: 0.02 }]
    };
    const joint = { bone: { name: 'Head' }, settings: {} };
    profile.applyJoints(makeManager([], [joint]), 0.5);
    assert.equal(joint.settings.hitRadius, 0.01);
    assert.equal(joint.settings.stiffness, 0.4);
});

test('two chains on one bone each keep their own settings', () => {
    const { VrmPhysicsProfile } = loadProfileClass();
    const profile = new VrmPhysicsProfile();
    profile.profile = {
        colliders: [],
        chains: [
            { bones: ['Tail'], stiffness: 0.2 },
            { bones: ['Tail'], stiffness: 0.9 }
        ]
    };
    const first = { bone: { name: 'Tail' }, settings: {} };
    const second = { bone: { name: 'Tail' }, settings: {} };
    const applied = profile.applyJoints(makeManager([], [first, second]));
    assert.equal(applied, 2);
    assert.equal(first.settings.stiffness, 0.2);
    assert.equal(second.settings.stiffness, 0.9);
});

// ---- Real-model layout and manager integration ----

function readVrmJson(relPath) {
    const fd = fs.openSync(path.join(projectRoot, relPath), 'r');
    try {
        const header = Buffer.alloc(20);
        fs.readSync(fd, header, 0, 20, 0);
        const jsonLength = header.readUInt32LE(12);
        const chunk = Buffer.alloc(jsonLength);
        fs.readSync(fd, chunk, 0, jsonLength, 20);
        return JSON.parse(chunk.toString('utf8'));
    } finally {
        fs.closeSync(fd);
    }
}

// GLTFLoader keeps the glTF node name in userData.name (sister1.0's names need no sanitizing).
function runtimeNode(node) {
    return { name: node.name, userData: { name: node.name } };
}

// Builds the runtime collider/joint layout three-vrm creates for a VRM1 model:
// one sphere/capsule collider per entry (child of its node), one joint per spring segment.
function runtimeFromVrm1(gltf) {
    const nodes = gltf.nodes;
    const spring = gltf.extensions.VRMC_springBone;
    const colliders = spring.colliders.map((entry) => {
        const type = entry.shape.sphere ? 'sphere' : 'capsule';
        const data = entry.shape[type];
        const bone = runtimeNode(nodes[entry.node]);
        const offset = data.offset || [0, 0, 0];
        return {
            parent: bone,
            shape: { type, radius: data.radius, offset: { x: offset[0], y: offset[1], z: offset[2] } }
        };
    });
    const joints = [];
    for (const s of spring.springs) {
        for (let i = 0; i < s.joints.length - 1; i++) {
            const j = s.joints[i];
            joints.push({
                bone: runtimeNode(nodes[j.node]),
                settings: {
                    hitRadius: j.hitRadius || 0,
                    stiffness: j.stiffness,
                    dragForce: j.dragForce,
                    gravityPower: j.gravityPower || 0
                }
            });
        }
    }
    return { colliders, joints };
}

const sisterGltf = readVrmJson('static/vrm/sister1.0.vrm');

// The six colliders vrm-physics-tuner's PhysicsConfigBuilder writes for sister1.0: one per
// humanoid candidate bone, capsule when the bone has a child, offset always [0,0,0].
const TUNER_COLLIDERS_SISTER = [
    { bone: 'J_Bip_C_Head', shape: 'capsule', radius: 0.141539, offset: [0, 0, 0] },
    { bone: 'J_Bip_C_Chest', shape: 'capsule', radius: 0.046839, offset: [0, 0, 0] },
    { bone: 'J_Bip_C_Spine', shape: 'capsule', radius: 0.046219, offset: [0, 0, 0] },
    { bone: 'J_Bip_C_Hips', shape: 'capsule', radius: 0.037673, offset: [0, 0, 0] },
    { bone: 'J_Bip_L_UpperArm', shape: 'capsule', radius: 0.095722, offset: [0, 0, 0] },
    { bone: 'J_Bip_R_UpperArm', shape: 'capsule', radius: 0.095722, offset: [0, 0, 0] }
];

test('sister1.0: tuner capsules never land on the model sphere colliders', () => {
    const { VrmPhysicsProfile } = loadProfileClass();
    const profile = new VrmPhysicsProfile();
    profile.profile = { colliders: TUNER_COLLIDERS_SISTER, chains: [] };
    const { colliders } = runtimeFromVrm1(sisterGltf);
    assert.equal(colliders.length, 28);
    const raw = colliders.map((c) => c.shape.radius);
    const applied = profile.applyColliders(makeManager(colliders), 0.5);
    assert.equal(applied, 0, 'a capsule radius must not be written onto a sphere');
    colliders.forEach((c, i) => {
        assert.equal(c.shape.radius, raw[i] * 0.5, 'unmatched colliders keep the no-profile radius');
    });
});

test('sister1.0: a profile copying the model colliders matches every one of them', () => {
    const { VrmPhysicsProfile } = loadProfileClass();
    const profile = new VrmPhysicsProfile();
    const nodes = sisterGltf.nodes;
    profile.profile = {
        colliders: sisterGltf.extensions.VRMC_springBone.colliders.map((c, i) => ({
            bone: nodes[c.node].name,
            shape: 'sphere',
            radius: 0.001 * (i + 1),
            offset: c.shape.sphere.offset
        })).reverse(),
        chains: []
    };
    const { colliders } = runtimeFromVrm1(sisterGltf);
    const applied = profile.applyColliders(makeManager(colliders));
    assert.equal(applied, 28);
    colliders.forEach((c, i) => {
        assert.ok(Math.abs(c.shape.radius - 0.001 * (i + 1)) < 1e-12, `collider ${i} got its own radius`);
    });
});

test('sister1.0: zero-offset sphere entries pair with the bone spheres in order', () => {
    const { VrmPhysicsProfile } = loadProfileClass();
    const profile = new VrmPhysicsProfile();
    profile.profile = {
        colliders: [
            { bone: 'J_Bip_C_UpperChest', shape: 'sphere', radius: 0.03, offset: [0, 0, 0] },
            { bone: 'J_Bip_C_UpperChest', shape: 'sphere', radius: 0.02, offset: [0, 0, 0] },
            { bone: 'J_Bip_C_UpperChest', shape: 'sphere', radius: 0.021, offset: [0, 0, 0] }
        ],
        chains: []
    };
    const { colliders } = runtimeFromVrm1(sisterGltf);
    const chest = colliders.filter((c) => c.parent.name === 'J_Bip_C_UpperChest');
    assert.equal(chest.length, 3);
    assert.equal(profile.applyColliders(makeManager(colliders)), 3);
    assert.deepEqual(chest.map((c) => c.shape.radius), [0.03, 0.02, 0.021]);
});

test('a shape-only entry does not land on a collider of another shape', () => {
    const { VrmPhysicsProfile } = loadProfileClass();
    const profile = new VrmPhysicsProfile();
    profile.profile = { colliders: [{ bone: 'Head', shape: 'sphere', radius: 0.07 }], chains: [] };
    const capsule = makeCollider('Head', 0.4);
    capsule.shape.type = 'capsule';
    assert.equal(profile.applyColliders(makeManager([capsule])), 0);
    assert.equal(capsule.shape.radius, 0.2);
});

test('sister1.0: tuner chains reach every runtime spring joint', () => {
    const { VrmPhysicsProfile } = loadProfileClass();
    const profile = new VrmPhysicsProfile();
    const nodes = sisterGltf.nodes;
    // The tuner walks each spring root down its children; model springs list the same bones.
    profile.profile = {
        colliders: [],
        chains: sisterGltf.extensions.VRMC_springBone.springs.map((s) => ({
            bones: s.joints.map((j) => nodes[j.node].name),
            stiffness: 0.7,
            drag_force: 0.5,
            gravity_power: 0.15,
            hit_radius: 0.0175
        }))
    };
    const { joints } = runtimeFromVrm1(sisterGltf);
    assert.equal(joints.length, 71);
    assert.equal(profile.applyJoints(makeManager([], joints), 1), 71);
    joints.forEach((j) => {
        assert.equal(j.settings.stiffness, 0.7);
        assert.equal(j._originalHitRadius, 0.0175);
    });
});

// Extracts a VRMManager method body from the real source.
function extractManagerMethod(signature) {
    const source = fs.readFileSync(path.join(projectRoot, 'static/vrm/vrm-manager.js'), 'utf8');
    const start = source.indexOf('    ' + signature + ' {');
    assert.ok(start > 0, signature + ' must exist');
    const bodyStart = source.indexOf('{', start);
    let depth = 0;
    let end = bodyStart;
    for (; end < source.length; end++) {
        if (source[end] === '{') depth++;
        else if (source[end] === '}') {
            depth--;
            if (depth === 0) break;
        }
    }
    return source.slice(bodyStart + 1, end);
}

function loadSetModelScale() {
    return new Function('x', 'y', 'z', extractManagerMethod('setModelScale(x, y, z)'));
}

function fakeModel(colliders, joints) {
    return {
        currentModel: {
            vrm: {
                scene: { scale: { x: 1, set(x) { this.x = x; } } },
                springBoneManager: makeManager(colliders, joints)
            }
        }
    };
}

test('profile hit radius is scaled once after the user rescales the model', () => {
    const { VrmPhysicsProfile } = loadProfileClass();
    const profile = new VrmPhysicsProfile();
    profile.profile = { colliders: [], chains: [{ bones: ['Hair'], hit_radius: 0.02 }] };
    const joint = { bone: { name: 'Hair' }, settings: { hitRadius: 0.004 } };
    profile.applyJoints(makeManager([], [joint]), 0.6);
    assert.ok(Math.abs(joint.settings.hitRadius - 0.012) < 1e-12);

    const setModelScale = loadSetModelScale();
    const host = fakeModel([], [joint]);
    const log = console.log;
    console.log = () => {};
    try {
        setModelScale.call(host, 0.7, 0.7, 0.7);
    } finally {
        console.log = log;
    }
    assert.ok(Math.abs(joint.settings.hitRadius - 0.014) < 1e-12, `got ${joint.settings.hitRadius}`);
});

test('without a profile, rescaling leaves joint hit radius untouched', () => {
    const joint = { bone: { name: 'Hair' }, settings: { hitRadius: 0.004 } };
    const collider = makeCollider('Head', 0.1);
    const setModelScale = loadSetModelScale();
    const host = fakeModel([collider], [joint]);
    const log = console.log;
    console.log = () => {};
    try {
        setModelScale.call(host, 0.5, 0.5, 0.5);
        setModelScale.call(host, 0.8, 0.8, 0.8);
    } finally {
        console.log = log;
    }
    assert.equal(joint.settings.hitRadius, 0.004);
    assert.equal(joint._originalHitRadius, undefined);
    assert.ok(Math.abs(collider.shape.radius - 0.08) < 1e-12, 'colliders still follow the scale');
});

test('an empty or array config is not treated as a profile', async () => {
    for (const config of [{}, [], { colliders: 'x' }]) {
        const { VrmPhysicsProfile } = loadProfileClass({
            fetch: async () => ({ ok: true, json: async () => ({ success: true, config }) }),
            AbortController,
            setTimeout,
            clearTimeout
        });
        const profile = new VrmPhysicsProfile();
        assert.equal(await profile.load('m'), null);
        assert.equal(profile.hasProfile, false);
    }
});

test('capsules sharing an offset are told apart by their tails, whatever the profile order', () => {
    const { VrmPhysicsProfile } = loadProfileClass();
    const profile = new VrmPhysicsProfile();
    profile.profile = {
        colliders: [
            { bone: 'Hips', shape: 'capsule', radius: 0.09, offset: [0, 0, 0], tail: [0, 0, 0.2] },
            { bone: 'Hips', shape: 'capsule', radius: 0.03, offset: [0, 0, 0], tail: [0.2, 0, 0] }
        ],
        chains: []
    };
    const sideways = makeCollider('Hips', 0.5);
    sideways.shape.type = 'capsule';
    sideways.shape.tail = { x: 0.2, y: 0, z: 0 };
    const forward = makeCollider('Hips', 0.5);
    forward.shape.type = 'capsule';
    forward.shape.tail = { x: 0, y: 0, z: 0.2 };
    assert.equal(profile.applyColliders(makeManager([sideways, forward])), 2);
    assert.equal(sideways.shape.radius, 0.03);
    assert.equal(forward.shape.radius, 0.09);
});

test('a capsule tail in another coordinate space still matches by offset', () => {
    const { VrmPhysicsProfile } = loadProfileClass();
    const profile = new VrmPhysicsProfile();
    profile.profile = {
        colliders: [{ bone: 'Hips', shape: 'capsule', radius: 0.04, offset: [0, 0.1, 0], tail: [-0.2, 0, 0] }],
        chains: []
    };
    const capsule = makeCollider('Hips', 0.5);
    capsule.shape.type = 'capsule';
    capsule.shape.offset = { x: 0, y: 0.1, z: 0 };
    capsule.shape.tail = { x: 0.2, y: 0, z: 0 };
    assert.equal(profile.applyColliders(makeManager([capsule])), 1);
    assert.equal(capsule.shape.radius, 0.04);
});

test('non-finite joint values in the profile leave those settings untouched', () => {
    const { VrmPhysicsProfile } = loadProfileClass();
    const profile = new VrmPhysicsProfile();
    profile.profile = {
        colliders: [],
        chains: [{
            bones: ['Hair'],
            stiffness: 'bad',
            drag_force: NaN,
            gravity_power: 0.2,
            hit_radius: Infinity,
            gravity_dir: ['bad', -1, 0]
        }]
    };
    const calls = [];
    const joint = {
        bone: { name: 'Hair' },
        settings: {
            stiffness: 0.8,
            dragForce: 0.4,
            gravityPower: 0,
            hitRadius: 0.01,
            gravityDir: { set: (...v) => calls.push(v) }
        }
    };
    profile.applyJoints(makeManager([], [joint]));
    assert.equal(joint.settings.stiffness, 0.8);
    assert.equal(joint.settings.dragForce, 0.4);
    assert.equal(joint.settings.gravityPower, 0.2);
    assert.equal(joint.settings.hitRadius, 0.01);
    assert.equal(joint._originalHitRadius, undefined);
    assert.deepEqual(calls, []);
});

test('reloading the same model fetches its profile again', async () => {
    const AsyncFunction = Object.getPrototypeOf(async () => {}).constructor;
    const loadPhysicsProfile = new AsyncFunction('modelUrl', extractManagerMethod('async loadPhysicsProfile(modelUrl)'));
    let served = { colliders: [], chains: [{ bones: ['Hair'], stiffness: 0.3 }] };
    let fetches = 0;
    const { VrmPhysicsProfile } = loadProfileClass({
        fetch: async () => {
            fetches++;
            return { ok: true, json: async () => ({ success: true, config: served }) };
        },
        AbortController,
        setTimeout,
        clearTimeout
    });
    const host = {
        _physicsProfile: null,
        _physicsProfileModel: '',
        _resolvePhysicsProfileModelName: () => 'sister1.0'
    };
    const previousWindow = globalThis.window;
    globalThis.window = { VrmPhysicsProfile };
    try {
        assert.equal(await loadPhysicsProfile.call(host, '/static/vrm/sister1.0.vrm'), true);
        assert.equal(host._physicsProfile.profile.chains[0].stiffness, 0.3);

        served = { colliders: [], chains: [{ bones: ['Hair'], stiffness: 0.9 }] };
        assert.equal(await loadPhysicsProfile.call(host, '/static/vrm/sister1.0.vrm'), true);
        assert.equal(host._physicsProfile.profile.chains[0].stiffness, 0.9, 'edited profile is picked up');

        served = null;
        assert.equal(await loadPhysicsProfile.call(host, '/static/vrm/sister1.0.vrm'), false);
        assert.equal(host._physicsProfile.hasProfile, false, 'deleted profile is dropped');
        assert.equal(host._physicsProfileModel, '');
        assert.equal(fetches, 3);
    } finally {
        globalThis.window = previousWindow;
    }
});
