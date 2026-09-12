const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { spawnSync } = require('node:child_process');

// An isolated GC-enabled process checks actual retention, without production
// debug hooks or changing the real reader's cancellation behavior.
if (typeof global.gc !== 'function') {
  const child = spawnSync(process.execPath, ['--expose-gc', __filename], {
    encoding: 'utf8', timeout: 30000,
  });
  assert.equal(child.status, 0, child.error?.message || child.stderr || child.stdout);
  process.stdout.write(child.stdout);
} else {
  const watchdog = setTimeout(() => {
    process.stderr.write('response memory probe did not finish\n');
    process.exit(1);
  }, 20000);
  main().catch(error => { process.stderr.write(`${error.stack}\n`); process.exitCode = 1; })
    .finally(() => clearTimeout(watchdog));
}

async function main() {
  const source = fs.readFileSync(path.resolve(__dirname,
    '../../static/game/sdk/neko-minigame-same-origin-host.js'), 'utf8');
  const start = source.indexOf('    async _bufferResponse(');
  const end = source.indexOf('    async _request(', start);
  assert(start >= 0 && end > start, 'response reader extraction anchors changed');
  const constant = source.match(/const DEFAULT_RESPONSE_BYTE_LIMIT = ([^;]+);/);
  const turn = () => new Promise(resolve => setImmediate(resolve));
  for (const maxBytes of [undefined, 2 * 1024 * 1024]) {
    const allocations = [];
    class ObservedBytes extends Uint8Array {
      constructor(...args) {
        super(...args);
        if (this.byteLength >= 64 * 1024) allocations.push(new WeakRef(this));
      }
    }
    const readerMethod = vm.runInNewContext(
      `${constant ? constant[0] : ''}\n({${source.slice(start, end)}})._bufferResponse`,
      { Uint8Array: ObservedBytes, Response },
    );
    let finishRead;
    let readCount = 0;
    let cancelled = 0;
    let released = 0;
    const response = { status: 200, headers: new Headers(), arrayBuffer() {},
      body: { getReader() { return {
        read() {
          if (++readCount === 1) return Promise.resolve({ done: false, value: new Uint8Array(1024 * 1024) });
          return new Promise(resolve => { finishRead = resolve; });
        },
        cancel() { cancelled++; return new Promise(() => {}); },
        releaseLock() { released++; },
      }; } },
    };
    const owner = { _window: { Response }, _hostError(code) { return Object.assign(new Error(code), { code }); } };
    const abort = new AbortController();
    let settled = false;
    const work = readerMethod.call(owner, response, maxBytes, abort.signal)
      .then(() => 'success', error => error.code).finally(() => { settled = true; });
    try {
      await turn();
      assert.equal(readCount, 2);
      assert(allocations.some(ref => ref.deref()), 'probe did not observe the accumulated bytes');
      abort.abort();
      for (let attempt = 0; attempt < 8; attempt++) { await turn(); global.gc(); }
      assert.equal(cancelled, 1);
      assert.equal(settled, false, 'probe must keep raw read unresolved');
      assert.equal(released, 0, 'probe must not reach the reader finally yet');
      assert(allocations.every(ref => !ref.deref()),
        `cancelled read retained its accumulated buffer (maxBytes=${maxBytes})`);
    } finally {
      finishRead?.({ done: false, value: new Uint8Array(16) });
      assert.equal(await work, 'cancelled');
      assert.equal(readCount, 2, 'late result resumed reading');
      assert.equal(released, 1);
    }
  }
  process.stdout.write('mini-game response memory runtime test passed\n');
}
