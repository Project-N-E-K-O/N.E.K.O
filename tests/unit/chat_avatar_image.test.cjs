const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const limits = { source_max_bytes: 10485760, source_max_pixels: 20000000 };

function png(width, height, animated = false) {
    const bytes = Buffer.alloc(animated ? 45 : 33);
    Buffer.from('\x89PNG\r\n\x1a\n', 'binary').copy(bytes);
    bytes.writeUInt32BE(13, 8); bytes.write('IHDR', 12);
    bytes.writeUInt32BE(width, 16); bytes.writeUInt32BE(height, 20);
    if (animated) { bytes.writeUInt32BE(0, 33); bytes.write('acTL', 37); }
    return new Blob([bytes]);
}

function fixture() {
    const images = []; const revoked = []; const created = [];
    class Image {
        constructor() { images.push(this); }
        set src(value) { this.url = value; }
        load(width, height) { this.naturalWidth = width; this.naturalHeight = height; this.onload(); }
    }
    const window = {};
    const sourcePath = path.join(__dirname, '../../static/app/app-chat-avatar-image.js');
    vm.runInNewContext(fs.readFileSync(sourcePath, 'utf8'), {
        window, Image, Blob, DataView, Uint8Array, atob,
        URL: {
            createObjectURL(blob) { created.push(blob); return 'blob:' + created.length; },
            revokeObjectURL(url) { revoked.push(url); }
        }
    }, { filename: sourcePath });
    async function tick() { for (let i = 0; i < 8; ++i) await Promise.resolve(); }
    return { api: window.appChatAvatarImage, images, revoked, created, tick };
}

test('image limits must be loaded and original file byte ceiling precedes reads', async () => {
    const h = fixture(); await assert.rejects(h.api.decodeFile(png(1, 1), null), { code: 'chat_avatar_unavailable' });
    await assert.rejects(h.api.decodeFile({ size: limits.source_max_bytes + 1 }, limits), { code: 'chat_avatar_too_large' });
    assert.equal(h.created.length, 0);
});

test('forged or truncated image header cannot bypass actual decoding', async () => {
    const h = fixture(); const result = h.api.decodeFile(png(320, 320), limits); await h.tick();
    assert.equal(h.images.length, 1); h.images[0].onerror();
    await assert.rejects(result, { code: 'chat_avatar_invalid_image' });
    assert.deepEqual(h.revoked, ['blob:1']);
});

test('source pixel maximum is checked before allocating a decoded image', async () => {
    const h = fixture(); await assert.rejects(h.api.decodeFile(png(20000, 20000), limits), { code: 'chat_avatar_too_many_pixels' });
    assert.equal(h.images.length, 0); assert.equal(h.created.length, 0);
});

test('browser decoded EXIF orientation and dimensions are authoritative; URL lives until release', async () => {
    const h = fixture(); const result = h.api.decodeFile(png(40, 30), limits); await h.tick();
    h.images[0].load(30, 40); const source = await result;
    assert.equal(source.width, 30); assert.equal(source.height, 40); assert.equal(h.revoked.length, 0);
    source.release(); source.release(); assert.deepEqual(h.revoked, ['blob:1']);
});

test('actual decoded dimensions cannot exceed maximum hidden behind a smaller header', async () => {
    const h = fixture(); const result = h.api.decodeFile(png(1, 1), limits); await h.tick();
    h.images[0].load(20000, 20000);
    await assert.rejects(result, { code: 'chat_avatar_too_many_pixels' }); assert.deepEqual(h.revoked, ['blob:1']);
});

test('animated PNG, animated WebP and unsupported container are rejected', async () => {
    const h = fixture(); await assert.rejects(h.api.decodeFile(png(1, 1, true), limits), { code: 'chat_avatar_invalid_image' });
    const webp = Buffer.alloc(30); webp.write('RIFF'); webp.write('WEBP', 8); webp.write('VP8X', 12); webp[20] = 2;
    await assert.rejects(h.api.decodeFile(new Blob([webp]), limits), { code: 'chat_avatar_invalid_image' });
    await assert.rejects(h.api.decodeFile(new Blob(['GIF89a']), limits), { code: 'chat_avatar_invalid_image' });
    assert.equal(h.created.length, 0);
});

test('JPEG SOF dimensions are recognized independently of MIME and extension', async () => {
    const h = fixture(); const jpeg = Buffer.from([0xff, 0xd8, 0xff, 0xc0, 0, 8, 8, 0, 10, 0, 20, 1]);
    const result = h.api.decodeFile(new Blob([jpeg], { type: 'text/plain' }), limits); await h.tick();
    h.images[0].load(20, 10); const source = await result; assert.equal(source.width, 20); source.release();
});

test('WebP VP8X, VP8 and VP8L dimensions support very small sources', async () => {
    for (const kind of ['VP8X', 'VP8 ', 'VP8L']) {
        const h = fixture(); const bytes = Buffer.alloc(kind === 'VP8L' ? 28 : 30);
        bytes.write('RIFF'); bytes.write('WEBP', 8); bytes.write(kind, 12);
        if (kind === 'VP8 ') { bytes[23] = 0x9d; bytes[24] = 1; bytes[25] = 0x2a; bytes.writeUInt16LE(1, 26); bytes.writeUInt16LE(1, 28); }
        if (kind === 'VP8L') bytes[20] = 0x2f;
        const result = h.api.decodeFile(new Blob([bytes]), limits); await h.tick();
        h.images[0].load(1, 1); const source = await result; assert.equal(source.width, 1); source.release();
    }
});

test('normalization blob enforces PNG and encoded byte ceiling', async () => {
    const h = fixture(); const data = 'data:image/png;base64,' + btoa('abcd');
    const blob = h.api.pngBlob(data, 4); assert.equal(blob.type, 'image/png'); assert.equal(blob.size, 4);
    assert.throws(() => h.api.pngBlob(data, 3), { code: 'chat_avatar_too_large' });
    assert.throws(() => h.api.pngBlob('data:image/jpeg;base64,YQ==', 4), { code: 'chat_avatar_invalid_image' });
});
