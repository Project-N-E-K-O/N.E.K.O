'use strict';

const fs = require('node:fs');
const path = require('node:path');

const directorScriptNames = Object.freeze([
    'tutorial/yui-guide/director/foundation.js',
    'tutorial/yui-guide/director/voice-queue.js',
    'tutorial/yui-guide/director/emotion-bridge.js',
    'tutorial/yui-guide/director/cursor-anchor-store.js',
    'tutorial/yui-guide/director/director-core.js',
    'tutorial/yui-guide/director/avatar-rounds.js',
    'tutorial/yui-guide/director/page-flows.js',
    'tutorial/yui-guide/director/chat-performance.js',
    'tutorial/yui-guide/director/lifecycle.js',
    'tutorial/yui-guide/director/bootstrap.js'
]);

function readDirectorSource(staticRoot) {
    const sources = new Map(directorScriptNames.map((relativePath) => [
        relativePath,
        fs.readFileSync(path.join(staticRoot, relativePath), 'utf8')
    ]));
    const classSource = (relativePath, className) => {
        const source = sources.get(relativePath);
        const start = source.indexOf(`    class ${className} {`);
        const end = source.indexOf(`\n    namespace.${className} = ${className};`, start);
        return source.slice(start, end);
    };
    const methodSource = (relativePath) => {
        const source = sources.get(relativePath);
        const startMarker = '    namespace.extendDirector({\n';
        const start = source.indexOf(startMarker) + startMarker.length;
        const end = source.lastIndexOf('\n    });');
        return source.slice(start, end).replace(/^        },$/gm, '        }');
    };
    const coreSource = classSource('tutorial/yui-guide/director/director-core.js', 'YuiGuideDirector');
    const coreWithoutClosingBrace = coreSource.slice(0, coreSource.lastIndexOf('\n    }'));

    return [
        sources.get('tutorial/yui-guide/director/foundation.js'),
        classSource('tutorial/yui-guide/director/voice-queue.js', 'YuiGuideVoiceQueue'),
        classSource('tutorial/yui-guide/director/emotion-bridge.js', 'YuiGuideEmotionBridge'),
        classSource('tutorial/yui-guide/director/cursor-anchor-store.js', 'CursorAnchorStore'),
        coreWithoutClosingBrace,
        methodSource('tutorial/yui-guide/director/avatar-rounds.js'),
        methodSource('tutorial/yui-guide/director/page-flows.js'),
        methodSource('tutorial/yui-guide/director/chat-performance.js'),
        methodSource('tutorial/yui-guide/director/lifecycle.js'),
        '    }',
        sources.get('tutorial/yui-guide/director/bootstrap.js')
    ].join('\n');
}

function findDirectorScriptIndexes(templateSource) {
    return directorScriptNames.map((relativePath) => templateSource.indexOf(`/static/${relativePath}`));
}

function hasOrderedDirectorScripts(templateSource) {
    const indexes = findDirectorScriptIndexes(templateSource);
    return indexes.every((index) => index >= 0)
        && indexes.every((index, position) => position === 0 || indexes[position - 1] < index);
}

module.exports = {
    directorScriptNames,
    findDirectorScriptIndexes,
    hasOrderedDirectorScripts,
    readDirectorSource
};
