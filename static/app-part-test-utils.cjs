const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');


function jsPartPaths(directory) {
    const partPaths = fs.readdirSync(directory)
        .filter((name) => name.endsWith('.js'))
        .sort()
        .map((name) => path.join(directory, name));
    if (partPaths.length === 0) {
        throw new Error(`no JavaScript parts found under ${directory}`);
    }
    return partPaths;
}


function readJsParts(directory, options = {}) {
    const source = jsPartPaths(directory)
        .map((partPath) => fs.readFileSync(partPath, 'utf8'))
        .join('\n');
    if (options.contractView === false) return source;
    return source
        .replace(/\bI\.([A-Za-z_$][\w$]*)\s*=\s*async function\s+\1\s*\(/g, 'async function $1(')
        .replace(/\bI\.([A-Za-z_$][\w$]*)\s*=\s*function\s+\1\s*\(/g, 'function $1(')
        .replace(/^(\s*)I\.([A-Za-z_$][\w$]*)\s*=/gm, '$1var $2 =')
        .replace(/\bI\./g, '');
}


function runJsPartsInNewContext(directory, context) {
    for (const partPath of jsPartPaths(directory)) {
        vm.runInNewContext(fs.readFileSync(partPath, 'utf8'), context, { filename: partPath });
    }
}


module.exports = { jsPartPaths, readJsParts, runJsPartsInNewContext };
