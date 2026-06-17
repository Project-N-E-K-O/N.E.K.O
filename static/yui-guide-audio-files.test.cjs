const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const repoRoot = path.resolve(__dirname, '..');
const daysDir = path.join(repoRoot, 'static', 'tutorial', 'yui-guide', 'days');
const directorSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial', 'yui-guide', 'director.js'), 'utf8');

function collectGuideSources() {
  return fs.readdirSync(daysDir)
    .filter((fileName) => fileName.endsWith('-guide.js'))
    .map((fileName) => ({
      fileName,
      source: fs.readFileSync(path.join(daysDir, fileName), 'utf8')
    }));
}

function collectDurationKeys() {
  const match = directorSource.match(/const GUIDE_AUDIO_DURATIONS_BY_KEY = Object\.freeze\(\{([\s\S]*?)\n\s*\}\);/);
  assert.ok(match, 'expected GUIDE_AUDIO_DURATIONS_BY_KEY in director.js');
  return new Set([...match[1].matchAll(/^\s*([a-zA-Z0-9_]+):\s*Object\.freeze\(\{/gm)].map((entry) => entry[1]));
}

test('registered Yui guide audio keys have duration configs', () => {
  const durationKeys = collectDurationKeys();
  const missing = [];

  for (const { fileName, source } of collectGuideSources()) {
    const voiceKeys = [...source.matchAll(/voiceKey:\s*'([^']+)'/g)].map((entry) => entry[1]);
    const mappedKeys = [...source.matchAll(/^\s*([a-zA-Z0-9_]+):\s*audioFilesForKey\('/gm)].map((entry) => entry[1]);

    for (const key of new Set(voiceKeys.concat(mappedKeys))) {
      if (!durationKeys.has(key)) {
        missing.push(fileName + ':' + key);
      }
    }
  }

  assert.deepEqual(missing, []);
});
