"use strict";

class TabManager {
  constructor() {
    this.dict = {};
    this.enabled = null;
    this.rules = null;
    this.pendingIds = {};
  }

  start() {
    this.registerOptionsChangeListener();
    this.loadOptions();
    this.registerTabListener();
  }

  loadOptions() {
    chrome.storage.sync.get(['enabled', 'rules'], (result) => {
      if (result.enabled === undefined) {
        console.warn("Enable status undefined, default enable");
        chrome.storage.sync.set({'enabled': true});
      } else {
        this.enabled = result.enabled;
      }

      if (result.rules === undefined) {
        console.warn("rules not found, initiate a empty rule list");
        chrome.storage.sync.set({'rules': [
          { pattern: "chrome://*", enabled: false, type: 'built_in' },
          { pattern: "chrome-search://*", enabled: false, type: 'built_in' },
          { pattern: "chrome-devtools://*", enabled: false, type: 'built_in' },
          { pattern: "chrome-extension://*", enabled: false, type: 'built_in' },
          { pattern: "about://*", enabled: false, type: 'built_in' },
          { pattern: "*sourceid=chrome-instant*", enabled: false, type: 'built_in' },
        ]});
      } else {
        this.rules = result.rules;
      }

      console.log("Options loaded", {enabled: this.enabled, rules: this.rules});
    });
  }

  registerOptionsChangeListener() {
    chrome.storage.onChanged.addListener((changes, areaName) => {
      if (changes.enabled !== undefined) {
        this.enabled = changes.enabled.newValue;
      }

      if (changes.rules !== undefined) {
        this.rules = changes.rules.newValue;
      }

      console.log("Options changed", {enabled: this.enabled, rules: this.rules});
    });
  }

  registerTabListener() {
    // Save default Tabid
    chrome.tabs.query({active: true}, (result) => {
      for (let p in result) {
        let tab = result[p];
        this.dict[tab.windowId] = tab.id;
      }
    });

    // EVENT_window_remove
    chrome.windows.onRemoved.addListener((windowId) => {
      console.debug('Window onRemoved', windowId);
      delete this.dict[windowId];
    });

    // EVENT_tab_active
    chrome.tabs.onActivated.addListener((activeInfo) => {
      console.debug('Tab onActivated', activeInfo);
      this.dict[activeInfo.windowId] = activeInfo.tabId;
    });

    // EVENT_tab_update
    chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
      console.debug('Tab onUpdated', tabId, changeInfo);

      // Fire handler when url be setted
      if (tabId in this.pendingIds && changeInfo.status == 'loading' && 'url' in changeInfo) {
        this.handleTabCreate(tab, true);
        delete this.pendingIds[tabId]
      }
    });

    // EVENT_tab_create
    chrome.tabs.onCreated.addListener((tab) => {
      console.debug('Tab onCreated', tab);

      // skip open with Ctrl or Middle Mouse Button
      // If tabs.create called with prop.selected = true, prop.active =
      // false. Argument tab.active is still false, so we have to test
      // prop.selected although it is deprecated.
      if (!tab.active && !tab.selected) {
          return;
      }

      // Pending fresh tab without url
      if (this.hasCustomRule() && tab.status == "complete" && tab.active && !('pendingUrl' in tab)) {
        this.pendingIds[tab.id] = true;
        return;
      }

      this.handleTabCreate(tab);
    });
  }

  handleTabCreate(tab, pending = false) {
    if (!this.ruleApply(tab)) {
      return;
    }

    let last_tabid = pending ? tab.openerTabId : this.dict[tab.windowId];
    if (!last_tabid || last_tabid == tab.id) {
      return;
    }

    console.info('Tab Active', last_tabid);
    chrome.tabs.update(last_tabid, {active: true});
  }

  ruleApply(tab) {
    let url = 'pendingUrl' in tab ? tab.pendingUrl : tab.url;

    for (let i in this.rules) {
      let rule = this.rules[i];
      if (this.matchRuleShort(url, rule.pattern)) {
        return rule.enabled;
      }
    }

    return this.enabled;
  }

  hasCustomRule() {
    for (let i in this.rules) {
      let rule = this.rules[i];
      if (rule.type != 'built_in') {
        return true;
      }
    }

    return false;
  }

  matchRuleShort(str, rule) {
    /* A simple wildcard function from
     * https://stackoverflow.com/questions/26246601/wildcard-string-comparison-in-javascript */
    let escapeRegex = (str) => str.replace(/([.*+?^=!:${}()|\[\]\/\\])/g, "\\$1");
    return new RegExp("^" + rule.split("*").map(escapeRegex).join(".*") + "$").test(str);
  }
}


// main
let tm = new TabManager();
tm.start();
