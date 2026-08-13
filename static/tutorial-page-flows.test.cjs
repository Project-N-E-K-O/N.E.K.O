const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const pageFlowsSource = fs.readFileSync(
  path.join(__dirname, 'tutorial/yui-guide/director/page-flows.js'),
  'utf8'
);

function loadPageFlowMethods() {
  const methods = {};
  const directorNamespace = {
    extendDirector(nextMethods) {
      Object.assign(methods, nextMethods);
    }
  };
  const context = {
    console,
    Promise,
    setTimeout,
    clearTimeout,
    window: {
      __YuiGuideDirector: directorNamespace
    }
  };

  vm.runInNewContext(pageFlowsSource, context, {
    filename: 'static/tutorial/yui-guide/director/page-flows.js'
  });
  return methods;
}

test('openMicPanel expands the trigger selector in its local fallback', async () => {
  const methods = loadPageFlowMethods();
  let micPanelVisible = false;
  let expandedSelector = '';
  const trigger = {
    click() {
      micPanelVisible = true;
    }
  };
  const popup = {
    querySelector(selector) {
      assert.equal(selector, '[data-neko-screen-share-action="toggle"]');
      return {};
    }
  };

  const result = await methods.openMicPanel.call({
    callHomeInteractionApi(methodName, args, fallback) {
      assert.equal(methodName, 'openMicPanel');
      assert.equal(args.length, 0);
      return fallback();
    },
    getManagedPanelElement(panelId) {
      assert.equal(panelId, 'mic');
      return popup;
    },
    expandSelector(selector) {
      expandedSelector = selector;
      return selector.replace('${p}', 'mic');
    },
    resolveElement(selector) {
      assert.equal(selector, '.mic-trigger-btn');
      return trigger;
    },
    isManagedPanelVisible(panelId) {
      assert.equal(panelId, 'mic');
      return micPanelVisible;
    },
    waitForElement(resolve) {
      return Promise.resolve(resolve());
    }
  });

  assert.equal(result, true);
  assert.equal(expandedSelector, '.${p}-trigger-btn');
  assert.equal(micPanelVisible, true);
});
