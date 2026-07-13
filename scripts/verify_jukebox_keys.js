const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { execFileSync } = require('child_process');

const repositoryRoot = path.resolve(__dirname, '..');
const partsDirectory = path.join(repositoryRoot, 'static', 'jukebox', 'jukebox');
const legacyFileName = ['Jukebox', 'js'].join('.');
const legacyPath = ['static', 'jukebox', legacyFileName].join('/');
const baseCommit = execFileSync(
  'git',
  ['merge-base', 'HEAD', 'origin/main'],
  { cwd: repositoryRoot, encoding: 'utf8' }
).trim();
const originalSource = execFileSync(
  'git',
  ['show', `${baseCommit}:${legacyPath}`],
  { cwd: repositoryRoot, encoding: 'utf8', maxBuffer: 2 * 1024 * 1024 }
);
const partPaths = fs.readdirSync(partsDirectory)
  .filter(fileName => fileName.endsWith('.js'))
  .sort()
  .map(fileName => path.join(partsDirectory, fileName));
const assembledSource = partPaths
  .map(partPath => fs.readFileSync(partPath, 'utf8'))
  .join('\n');

function loadJukebox(source, fileName) {
  const window = {
    addEventListener() {},
    removeEventListener() {}
  };
  const context = vm.createContext({
    console,
    window,
    Map,
    Set,
    URL,
    Promise,
    setTimeout,
    clearTimeout
  });
  vm.runInContext(source, context, { filename: fileName });
  if (!window.Jukebox) {
    throw new Error(`${fileName} did not create window.Jukebox`);
  }
  return window.Jukebox;
}

const originalKeys = Object.keys(loadJukebox(originalSource, 'jukebox-original.js'));
const assembledKeys = Object.keys(loadJukebox(assembledSource, 'Jukebox.assembled.js'));
const originalSet = new Set(originalKeys);
const assembledSet = new Set(assembledKeys);
const missing = originalKeys.filter(key => !assembledSet.has(key));
const extra = assembledKeys.filter(key => !originalSet.has(key));
const orderMatches = originalKeys.every((key, index) => assembledKeys[index] === key);

if (missing.length || extra.length || originalKeys.length !== assembledKeys.length) {
  console.error(`Missing keys: ${missing.join(', ') || '(none)'}`);
  console.error(`Extra keys: ${extra.join(', ') || '(none)'}`);
  process.exitCode = 1;
} else if (!orderMatches) {
  console.error('Top-level key sets match, but their insertion order changed.');
  process.exitCode = 1;
} else {
  console.log(`Jukebox top-level keys match exactly (${assembledKeys.length} keys, insertion order preserved).`);
  console.log(`Parts: ${partPaths.map(partPath => path.basename(partPath)).join(', ')}`);
}
