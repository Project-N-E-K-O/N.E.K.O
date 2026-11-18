import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';

const require = createRequire(import.meta.url);
const fflate = require('../../static/libs/fflate.umd.js');
const {
    ClientZipAssetStore,
    ClientZipAssetStoreError,
} = require('../../static/js/emote_template_client_store.js');

const encoder = new TextEncoder();

function fixtureZip() {
    return fflate.zipSync({
        'pkg/models/Test/model.skel': new Uint8Array([1, 2, 3]),
        'pkg/models/Test/model.atlas': encoder.encode('page.png\nsize: 2, 2\nscale: 1\n'),
        'pkg/models/Test/page.png': new Uint8Array([137, 80, 78, 71]),
        'pkg/history/old.png': new Uint8Array([4]),
        'pkg/ignored.js': new Uint8Array([5]),
    });
}

test('client ZIP store keeps active resources in memory and releases them', async () => {
    const store = await ClientZipAssetStore.fromBytes(fixtureZip());
    assert.equal(store.has('pkg/models/Test/model.skel'), true);
    assert.equal(store.has('乱码包根/models/Test/model.skel'), true);
    assert.equal(store.has('pkg/history/old.png'), true, 'directory names have no vendor-specific meaning');
    assert.deepEqual(
        [...new Uint8Array(await store.open('pkg/models/Test/model.skel'))],
        [1, 2, 3],
    );
    const objectUrl = store.url('pkg/models/Test/page.png');
    assert.match(objectUrl, /^blob:/);
    assert.equal(store.stats().objectUrls, 1);
    store.release();
    assert.equal(store.stats().released, true);
    assert.throws(() => store.get('pkg/models/Test/page.png'), /已释放/);
});

test('client ZIP store rejects traversal paths before exposing assets', async () => {
    const archive = fflate.zipSync({ '../outside.png': new Uint8Array([1]) });
    await assert.rejects(
        () => ClientZipAssetStore.fromBytes(archive),
        (error) => error instanceof ClientZipAssetStoreError && error.code === 'unsafe_path',
    );
});

test('real Spine ZIP smoke test when SPINE_TEST_ZIP is provided', async (t) => {
    const filename = process.env.SPINE_TEST_ZIP;
    if (!filename) {
        t.skip('set SPINE_TEST_ZIP to run against a local Spine archive');
        return;
    }
    const store = await ClientZipAssetStore.fromBytes(fs.readFileSync(filename));
    assert.ok(store.list().some((path) => /\.(skel(?:\.bytes)?|json)$/.test(path)));
    assert.ok(store.list().some((path) => /\.atlas(?:\.txt)?$/.test(path)));
    store.release();
});
