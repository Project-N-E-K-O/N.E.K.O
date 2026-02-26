"use strict";

class Rule extends React.Component {
  constructor(props) {
    super(props);
    this.handleChange = this.handleChange.bind(this);
    this.handleSwitch = this.handleSwitch.bind(this);
  }

  handleChange(event) {
    const target = event.target;
    const value = target.type === 'checkbox' ? target.checked : target.value;
    const name = target.name;

    this.props.onChange(name, value);
  }

  handleSwitch(checked, event) {
    this.props.onChange("enabled", checked);
  }

  render() {
    let button, disabled;
    if (this.props.type == 'built_in') {
      disabled = true;
    } else {
      disabled = false;
      button = React.createElement(
        antd.Button,
        {
          type: "danger",
          size: "small",
          onClick: this.props.onRemove
        },
        "Remove"
      );
    }

    return React.createElement(
      "div",
      { "class": "row" },
      React.createElement(antd.Input, {
        name: "pattern",
        type: "text",
        placeholder: "wildcard pattern (example: *.google.com)",
        value: this.props.pattern,
        onChange: this.handleChange,
        disabled: disabled
      }),
      button,
      React.createElement(antd.Switch, {
        checkedChildren: "Backgrond",
        unCheckedChildren: "Default",
        defaultChecked: this.props.enabled,
        onChange: this.handleSwitch,
        disabled: disabled
      })
    );
  }
}

class RuleList extends React.Component {
  constructor(props) {
    super(props);

    this.state = { enabled: true, rules: [], msg: '' };

    this.loadOptions();
  }

  loadOptions() {
    let that = this;

    chrome.storage.sync.get(['enabled', 'rules'], result => {
      if (result.enabled === undefined) {
        console.error("Enable status undefined");
        return;
      }

      if (result.rules === undefined) {
        console.error("Rules not found");
        return;
      }

      that.setState({
        enabled: result.enabled,
        rules: result.rules
      });
    });
  }

  saveOptions() {
    let enabled = this.state.enabled;
    let rlist = this.state.rules.slice();

    chrome.storage.sync.set({ 'enabled': enabled, 'rules': rlist }, () => {
      console.log("Rules saved", { enabled: enabled, rules: rlist });
      this.setState({ msg: 'Options saved!' });
      setTimeout(() => {
        this.setState({ msg: '' });
      }, 2000);
    });
  }

  handleRuleAdd() {
    let rlist = this.state.rules.slice();
    rlist.push({
      pattern: '',
      enabled: true,
      type: 'user_defined'
    });
    this.setState({ rules: rlist, msg: 'unsaved changes' });
  }

  handleRuleRemove(i) {
    let rlist = this.state.rules.slice();
    rlist.splice(i, 1);
    this.setState({ rules: rlist, msg: 'unsaved changes' });
  }

  handleRuleChange(i, name, value) {
    let rlist = this.state.rules.slice();
    rlist[i][name] = value;
    this.setState({ rules: rlist, msg: 'unsaved changes' });
  }

  handleSwitch(checked, event) {
    this.setState({ enabled: checked, msg: 'unsaved changes' });
  }

  render() {
    const rules = this.state.rules;

    let rows = [];
    for (let i in rules) {
      let rule = rules[i];
      rows[i] = React.createElement(Rule, {
        pattern: rule.pattern,
        enabled: rule.enabled,
        type: rule.type,
        onRemove: () => this.handleRuleRemove(i),
        onChange: (name, value) => this.handleRuleChange(i, name, value)
      });
    }

    let msg = React.createElement(
      "span",
      {
        "class": "tip"
      },
      this.state.msg
    );

    return React.createElement(
      "div",
      { "class": "panel" },
      React.createElement(
        "h3",
        null,
        "Global"
      ),
      React.createElement(
        "div",
        { "class": "block" },
        React.createElement(
          "div",
          { "class": "row" },
          React.createElement(
            "span",
            null,
            "Default behavior"
          ),
          React.createElement(antd.Switch, {
            checkedChildren: "Backgrond",
            unCheckedChildren: "Default",
            checked: this.state.enabled,
            onChange: (checked, e) => this.handleSwitch(checked, e)
          })
        )
      ),
      React.createElement(
        "h3",
        null,
        "Rule"
      ),
      React.createElement(
        "div",
        { "class": "block" },
        rows,
        React.createElement(
          "div",
          { "class": "row" },
          React.createElement(
            antd.Button,
            {
              type: "primary",
              onClick: () => this.handleRuleAdd()
            },
            "Add"
          )
        )
      ),
      React.createElement(
        "div",
        { "class": "block" },
        React.createElement(
          "div",
          { "class": "row" },
          React.createElement(
            antd.Button,
            {
              type: "primary",
              onClick: () => this.saveOptions()
            },
            "Save"
          ),
          msg
        )
      )
    );
  }
}

ReactDOM.render(React.createElement(RuleList, null), document.getElementById('container'));

