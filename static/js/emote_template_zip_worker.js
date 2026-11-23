/* Decode bounded ZIP resources off the editor thread, using its shared decoder. */
importScripts('../libs/fflate.umd.js', 'emote_template_client_store.js');

self.onmessage = async ({ data }) => {
    let store;
    try {
        store = await self.NekoEmoteClientZipStore.ClientZipAssetStore.fromBytes(data);
        const entries = store.takeEntries().map(([path, bytes]) => [path, bytes.buffer]);
        self.postMessage({ success: true, entries }, [...new Set(entries.map(([, buffer]) => buffer))]);
    } catch (error) {
        self.postMessage({ success: false, code: error.code || 'decode_failed', message: String(error.message || error) });
    } finally {
        store?.release();
        self.close();
    }
};
