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

function namedStoredZip(rawName, utf8 = false) {
    const placeholder = 'x'.repeat(rawName.length - 5) + '.json';
    const data = fflate.zipSync({ [placeholder]: encoder.encode('{"asset":true}') }, { level: 0 });
    const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
    for (let offset = 0; offset + 4 <= data.length; offset += 1) {
        const signature = view.getUint32(offset, true);
        if (signature !== 0x04034b50 && signature !== 0x02014b50) continue;
        const central = signature === 0x02014b50;
        const flagAt = offset + (central ? 8 : 6);
        view.setUint16(flagAt, utf8 ? 0x800 : 0, true);
        const start = offset + (central ? 46 : 30);
        data.set(rawName, start);
    }
    return data;
}

function zip64Fixture() {
    const data=fflate.zipSync({'one.json':encoder.encode('{"asset":1}'),'two.json':encoder.encode('{"asset":2}')},{level:0});
    const end=data.length-22;
    const before=new DataView(data.buffer,data.byteOffset,data.byteLength);
    const output=new Uint8Array(data.length+76);
    output.set(data.subarray(0,end));
    output.set(data.subarray(end),end+76);
    const view=new DataView(output.buffer);
    view.setUint32(end,0x06064b50,true);
    view.setUint32(end+4,44,true);
    view.setUint16(end+12,45,true);
    view.setUint16(end+14,45,true);
    view.setUint32(end+24,before.getUint16(end+8,true),true);
    view.setUint32(end+32,before.getUint16(end+10,true),true);
    view.setUint32(end+40,before.getUint32(end+12,true),true);
    view.setUint32(end+48,before.getUint32(end+16,true),true);
    view.setUint32(end+56,0x07064b50,true);
    view.setUint32(end+64,end,true);
    view.setUint32(end+72,1,true);
    view.setUint16(end+76+8,0xffff,true);
    view.setUint16(end+76+10,0xffff,true);
    view.setUint32(end+76+12,0xffffffff,true);
    view.setUint32(end+76+16,0xffffffff,true);
    return {output,view,record:end,locator:end+56,end:end+76};
}

test('keeps valid single-disk ZIP64 archives importable',async()=>{
    const {output}=zip64Fixture();
    const store=await ClientZipAssetStore.fromBytes(output);
    assert.deepEqual(store.list(),['one.json','two.json']);
    assert.equal(await store.open('two.json',{as:'text'}),'{"asset":2}');
    store.release();
});

for(const [name,mutate] of [
    ['EOCD counts',(v,r,l,e)=>v.setUint16(e+10,1,true)],
    ['EOCD disk',(v,r,l,e)=>v.setUint16(e+4,1,true)],
    ['EOCD directory disk',(v,r,l,e)=>v.setUint16(e+6,1,true)],
    ['locator disk',(v,r,l)=>v.setUint32(l+4,1,true)],
    ['locator total disks',(v,r,l)=>v.setUint32(l+16,2,true)],
    ['ZIP64 disk',(v,r)=>v.setUint32(r+16,1,true)],
    ['ZIP64 counts',(v,r)=>v.setUint32(r+24,1,true)],
    ['second central record',(v,r)=>{
        const first=v.getUint32(r+48,true);
        const second=first+46+v.getUint16(first+28,true)+v.getUint16(first+30,true)+v.getUint16(first+32,true);
        v.setUint32(second+20,0xffffffff,true);
        v.setUint16(second+28,0xffff,true);
    }],
]){
    test('rejects inconsistent '+name+' before invoking the vendor decoder',async()=>{
        const {output,view,record,locator,end}=zip64Fixture();
        mutate(view,record,locator,end);
        let calls=0;
        const original=fflate.unzipSync;
        fflate.unzipSync=()=>{calls++;throw new Error('decoder must not run');};
        try{
            await assert.rejects(()=>ClientZipAssetStore.fromBytes(output),{code:'decode_failed'});
            assert.equal(calls,0);
        }finally{fflate.unzipSync=original;}
    });
}

test('decodes GBK resource names exactly like the server', async () => {
    const raw = new Uint8Array([0xbd, 0xc7, 0xc9, 0xab, ...encoder.encode('.json')]);
    const store = await ClientZipAssetStore.fromBytes(namedStoredZip(raw));
    assert.deepEqual(store.list(), ['角色.json']);
    assert.equal(await store.open('角色.json', { as: 'text' }), '{"asset":true}');
    store.release();
});

test('accepts unflagged UTF-8 before trying GB18030', async () => {
    const store = await ClientZipAssetStore.fromBytes(namedStoredZip(encoder.encode('角色.json')));
    assert.deepEqual(store.list(), ['角色.json']);
    store.release();
});

test('does not reinterpret explicitly UTF-8 filenames as legacy bytes', async () => {
    const name = '½Ç.json';
    const store = await ClientZipAssetStore.fromBytes(namedStoredZip(encoder.encode(name), true));
    assert.deepEqual(store.list(), [name]);
    store.release();
});

test('falls back to CP437 when legacy bytes are invalid UTF-8 and GB18030', async () => {
    const raw = new Uint8Array([0x82, ...encoder.encode('.json')]);
    const store = await ClientZipAssetStore.fromBytes(namedStoredZip(raw));
    assert.deepEqual(store.list(), ['é.json']);
    store.release();
});

test('rejects missing ZIP64 extras before the vendor parser can scan beyond the record', async () => {
    const archive = namedStoredZip(encoder.encode('entry.json'));
    const view = new DataView(archive.buffer, archive.byteOffset, archive.byteLength);
    for (let offset = 0; offset + 46 <= archive.length; offset += 1) {
        if (view.getUint32(offset, true) === 0x02014b50) {
            view.setUint32(offset + 24, 0xffffffff, true);
            break;
        }
    }
    await assert.rejects(() => ClientZipAssetStore.fromBytes(archive), { code: 'decode_failed' });
});

test('hands off retained entries and releases the original store', async () => {
    const store = await ClientZipAssetStore.fromBytes(fixtureZip());
    const entries = new Map(store.takeEntries());
    assert.deepEqual([...entries.get('pkg/models/Test/model.skel')], [1, 2, 3]);
    assert.throws(() => store.get('pkg/models/Test/model.skel'), { code: 'released' });
});

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
