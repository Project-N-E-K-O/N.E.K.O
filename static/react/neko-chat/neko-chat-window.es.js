var vu = typeof globalThis < "u" && globalThis.process ? globalThis.process : { env: { NODE_ENV: "production" } };
function TD(h) {
  return h && h.__esModule && Object.prototype.hasOwnProperty.call(h, "default") ? h.default : h;
}
var fC = { exports: {} }, bv = {}, dC = { exports: {} }, wt = {};
/**
 * @license React
 * react.production.min.js
 *
 * Copyright (c) Facebook, Inc. and its affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */
var uT;
function RD() {
  if (uT) return wt;
  uT = 1;
  var h = Symbol.for("react.element"), c = Symbol.for("react.portal"), p = Symbol.for("react.fragment"), S = Symbol.for("react.strict_mode"), _ = Symbol.for("react.profiler"), T = Symbol.for("react.provider"), E = Symbol.for("react.context"), A = Symbol.for("react.forward_ref"), I = Symbol.for("react.suspense"), $ = Symbol.for("react.memo"), fe = Symbol.for("react.lazy"), re = Symbol.iterator;
  function be(L) {
    return L === null || typeof L != "object" ? null : (L = re && L[re] || L["@@iterator"], typeof L == "function" ? L : null);
  }
  var de = { isMounted: function() {
    return !1;
  }, enqueueForceUpdate: function() {
  }, enqueueReplaceState: function() {
  }, enqueueSetState: function() {
  } }, nt = Object.assign, bt = {};
  function xt(L, Z, Ze) {
    this.props = L, this.context = Z, this.refs = bt, this.updater = Ze || de;
  }
  xt.prototype.isReactComponent = {}, xt.prototype.setState = function(L, Z) {
    if (typeof L != "object" && typeof L != "function" && L != null) throw Error("setState(...): takes an object of state variables to update or a function which returns an object of state variables.");
    this.updater.enqueueSetState(this, L, Z, "setState");
  }, xt.prototype.forceUpdate = function(L) {
    this.updater.enqueueForceUpdate(this, L, "forceUpdate");
  };
  function En() {
  }
  En.prototype = xt.prototype;
  function _t(L, Z, Ze) {
    this.props = L, this.context = Z, this.refs = bt, this.updater = Ze || de;
  }
  var rt = _t.prototype = new En();
  rt.constructor = _t, nt(rt, xt.prototype), rt.isPureReactComponent = !0;
  var Tt = Array.isArray, ze = Object.prototype.hasOwnProperty, St = { current: null }, Qe = { key: !0, ref: !0, __self: !0, __source: !0 };
  function vn(L, Z, Ze) {
    var Ye, vt = {}, ct = null, ot = null;
    if (Z != null) for (Ye in Z.ref !== void 0 && (ot = Z.ref), Z.key !== void 0 && (ct = "" + Z.key), Z) ze.call(Z, Ye) && !Qe.hasOwnProperty(Ye) && (vt[Ye] = Z[Ye]);
    var ft = arguments.length - 2;
    if (ft === 1) vt.children = Ze;
    else if (1 < ft) {
      for (var ht = Array(ft), Xt = 0; Xt < ft; Xt++) ht[Xt] = arguments[Xt + 2];
      vt.children = ht;
    }
    if (L && L.defaultProps) for (Ye in ft = L.defaultProps, ft) vt[Ye] === void 0 && (vt[Ye] = ft[Ye]);
    return { $$typeof: h, type: L, key: ct, ref: ot, props: vt, _owner: St.current };
  }
  function Zt(L, Z) {
    return { $$typeof: h, type: L.type, key: Z, ref: L.ref, props: L.props, _owner: L._owner };
  }
  function on(L) {
    return typeof L == "object" && L !== null && L.$$typeof === h;
  }
  function hn(L) {
    var Z = { "=": "=0", ":": "=2" };
    return "$" + L.replace(/[=:]/g, function(Ze) {
      return Z[Ze];
    });
  }
  var zt = /\/+/g;
  function He(L, Z) {
    return typeof L == "object" && L !== null && L.key != null ? hn("" + L.key) : Z.toString(36);
  }
  function Yt(L, Z, Ze, Ye, vt) {
    var ct = typeof L;
    (ct === "undefined" || ct === "boolean") && (L = null);
    var ot = !1;
    if (L === null) ot = !0;
    else switch (ct) {
      case "string":
      case "number":
        ot = !0;
        break;
      case "object":
        switch (L.$$typeof) {
          case h:
          case c:
            ot = !0;
        }
    }
    if (ot) return ot = L, vt = vt(ot), L = Ye === "" ? "." + He(ot, 0) : Ye, Tt(vt) ? (Ze = "", L != null && (Ze = L.replace(zt, "$&/") + "/"), Yt(vt, Z, Ze, "", function(Xt) {
      return Xt;
    })) : vt != null && (on(vt) && (vt = Zt(vt, Ze + (!vt.key || ot && ot.key === vt.key ? "" : ("" + vt.key).replace(zt, "$&/") + "/") + L)), Z.push(vt)), 1;
    if (ot = 0, Ye = Ye === "" ? "." : Ye + ":", Tt(L)) for (var ft = 0; ft < L.length; ft++) {
      ct = L[ft];
      var ht = Ye + He(ct, ft);
      ot += Yt(ct, Z, Ze, ht, vt);
    }
    else if (ht = be(L), typeof ht == "function") for (L = ht.call(L), ft = 0; !(ct = L.next()).done; ) ct = ct.value, ht = Ye + He(ct, ft++), ot += Yt(ct, Z, Ze, ht, vt);
    else if (ct === "object") throw Z = String(L), Error("Objects are not valid as a React child (found: " + (Z === "[object Object]" ? "object with keys {" + Object.keys(L).join(", ") + "}" : Z) + "). If you meant to render a collection of children, use an array instead.");
    return ot;
  }
  function Ut(L, Z, Ze) {
    if (L == null) return L;
    var Ye = [], vt = 0;
    return Yt(L, Ye, "", "", function(ct) {
      return Z.call(Ze, ct, vt++);
    }), Ye;
  }
  function Ft(L) {
    if (L._status === -1) {
      var Z = L._result;
      Z = Z(), Z.then(function(Ze) {
        (L._status === 0 || L._status === -1) && (L._status = 1, L._result = Ze);
      }, function(Ze) {
        (L._status === 0 || L._status === -1) && (L._status = 2, L._result = Ze);
      }), L._status === -1 && (L._status = 0, L._result = Z);
    }
    if (L._status === 1) return L._result.default;
    throw L._result;
  }
  var De = { current: null }, le = { transition: null }, Oe = { ReactCurrentDispatcher: De, ReactCurrentBatchConfig: le, ReactCurrentOwner: St };
  function se() {
    throw Error("act(...) is not supported in production builds of React.");
  }
  return wt.Children = { map: Ut, forEach: function(L, Z, Ze) {
    Ut(L, function() {
      Z.apply(this, arguments);
    }, Ze);
  }, count: function(L) {
    var Z = 0;
    return Ut(L, function() {
      Z++;
    }), Z;
  }, toArray: function(L) {
    return Ut(L, function(Z) {
      return Z;
    }) || [];
  }, only: function(L) {
    if (!on(L)) throw Error("React.Children.only expected to receive a single React element child.");
    return L;
  } }, wt.Component = xt, wt.Fragment = p, wt.Profiler = _, wt.PureComponent = _t, wt.StrictMode = S, wt.Suspense = I, wt.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED = Oe, wt.act = se, wt.cloneElement = function(L, Z, Ze) {
    if (L == null) throw Error("React.cloneElement(...): The argument must be a React element, but you passed " + L + ".");
    var Ye = nt({}, L.props), vt = L.key, ct = L.ref, ot = L._owner;
    if (Z != null) {
      if (Z.ref !== void 0 && (ct = Z.ref, ot = St.current), Z.key !== void 0 && (vt = "" + Z.key), L.type && L.type.defaultProps) var ft = L.type.defaultProps;
      for (ht in Z) ze.call(Z, ht) && !Qe.hasOwnProperty(ht) && (Ye[ht] = Z[ht] === void 0 && ft !== void 0 ? ft[ht] : Z[ht]);
    }
    var ht = arguments.length - 2;
    if (ht === 1) Ye.children = Ze;
    else if (1 < ht) {
      ft = Array(ht);
      for (var Xt = 0; Xt < ht; Xt++) ft[Xt] = arguments[Xt + 2];
      Ye.children = ft;
    }
    return { $$typeof: h, type: L.type, key: vt, ref: ct, props: Ye, _owner: ot };
  }, wt.createContext = function(L) {
    return L = { $$typeof: E, _currentValue: L, _currentValue2: L, _threadCount: 0, Provider: null, Consumer: null, _defaultValue: null, _globalName: null }, L.Provider = { $$typeof: T, _context: L }, L.Consumer = L;
  }, wt.createElement = vn, wt.createFactory = function(L) {
    var Z = vn.bind(null, L);
    return Z.type = L, Z;
  }, wt.createRef = function() {
    return { current: null };
  }, wt.forwardRef = function(L) {
    return { $$typeof: A, render: L };
  }, wt.isValidElement = on, wt.lazy = function(L) {
    return { $$typeof: fe, _payload: { _status: -1, _result: L }, _init: Ft };
  }, wt.memo = function(L, Z) {
    return { $$typeof: $, type: L, compare: Z === void 0 ? null : Z };
  }, wt.startTransition = function(L) {
    var Z = le.transition;
    le.transition = {};
    try {
      L();
    } finally {
      le.transition = Z;
    }
  }, wt.unstable_act = se, wt.useCallback = function(L, Z) {
    return De.current.useCallback(L, Z);
  }, wt.useContext = function(L) {
    return De.current.useContext(L);
  }, wt.useDebugValue = function() {
  }, wt.useDeferredValue = function(L) {
    return De.current.useDeferredValue(L);
  }, wt.useEffect = function(L, Z) {
    return De.current.useEffect(L, Z);
  }, wt.useId = function() {
    return De.current.useId();
  }, wt.useImperativeHandle = function(L, Z, Ze) {
    return De.current.useImperativeHandle(L, Z, Ze);
  }, wt.useInsertionEffect = function(L, Z) {
    return De.current.useInsertionEffect(L, Z);
  }, wt.useLayoutEffect = function(L, Z) {
    return De.current.useLayoutEffect(L, Z);
  }, wt.useMemo = function(L, Z) {
    return De.current.useMemo(L, Z);
  }, wt.useReducer = function(L, Z, Ze) {
    return De.current.useReducer(L, Z, Ze);
  }, wt.useRef = function(L) {
    return De.current.useRef(L);
  }, wt.useState = function(L) {
    return De.current.useState(L);
  }, wt.useSyncExternalStore = function(L, Z, Ze) {
    return De.current.useSyncExternalStore(L, Z, Ze);
  }, wt.useTransition = function() {
    return De.current.useTransition();
  }, wt.version = "18.3.1", wt;
}
var Ov = { exports: {} };
/**
 * @license React
 * react.development.js
 *
 * Copyright (c) Facebook, Inc. and its affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */
Ov.exports;
var oT;
function wD() {
  return oT || (oT = 1, function(h, c) {
    vu.env.NODE_ENV !== "production" && function() {
      typeof __REACT_DEVTOOLS_GLOBAL_HOOK__ < "u" && typeof __REACT_DEVTOOLS_GLOBAL_HOOK__.registerInternalModuleStart == "function" && __REACT_DEVTOOLS_GLOBAL_HOOK__.registerInternalModuleStart(new Error());
      var p = "18.3.1", S = Symbol.for("react.element"), _ = Symbol.for("react.portal"), T = Symbol.for("react.fragment"), E = Symbol.for("react.strict_mode"), A = Symbol.for("react.profiler"), I = Symbol.for("react.provider"), $ = Symbol.for("react.context"), fe = Symbol.for("react.forward_ref"), re = Symbol.for("react.suspense"), be = Symbol.for("react.suspense_list"), de = Symbol.for("react.memo"), nt = Symbol.for("react.lazy"), bt = Symbol.for("react.offscreen"), xt = Symbol.iterator, En = "@@iterator";
      function _t(g) {
        if (g === null || typeof g != "object")
          return null;
        var b = xt && g[xt] || g[En];
        return typeof b == "function" ? b : null;
      }
      var rt = {
        /**
         * @internal
         * @type {ReactComponent}
         */
        current: null
      }, Tt = {
        transition: null
      }, ze = {
        current: null,
        // Used to reproduce behavior of `batchedUpdates` in legacy mode.
        isBatchingLegacy: !1,
        didScheduleLegacyUpdate: !1
      }, St = {
        /**
         * @internal
         * @type {ReactComponent}
         */
        current: null
      }, Qe = {}, vn = null;
      function Zt(g) {
        vn = g;
      }
      Qe.setExtraStackFrame = function(g) {
        vn = g;
      }, Qe.getCurrentStack = null, Qe.getStackAddendum = function() {
        var g = "";
        vn && (g += vn);
        var b = Qe.getCurrentStack;
        return b && (g += b() || ""), g;
      };
      var on = !1, hn = !1, zt = !1, He = !1, Yt = !1, Ut = {
        ReactCurrentDispatcher: rt,
        ReactCurrentBatchConfig: Tt,
        ReactCurrentOwner: St
      };
      Ut.ReactDebugCurrentFrame = Qe, Ut.ReactCurrentActQueue = ze;
      function Ft(g) {
        {
          for (var b = arguments.length, V = new Array(b > 1 ? b - 1 : 0), Y = 1; Y < b; Y++)
            V[Y - 1] = arguments[Y];
          le("warn", g, V);
        }
      }
      function De(g) {
        {
          for (var b = arguments.length, V = new Array(b > 1 ? b - 1 : 0), Y = 1; Y < b; Y++)
            V[Y - 1] = arguments[Y];
          le("error", g, V);
        }
      }
      function le(g, b, V) {
        {
          var Y = Ut.ReactDebugCurrentFrame, ie = Y.getStackAddendum();
          ie !== "" && (b += "%s", V = V.concat([ie]));
          var Ve = V.map(function(ce) {
            return String(ce);
          });
          Ve.unshift("Warning: " + b), Function.prototype.apply.call(console[g], console, Ve);
        }
      }
      var Oe = {};
      function se(g, b) {
        {
          var V = g.constructor, Y = V && (V.displayName || V.name) || "ReactClass", ie = Y + "." + b;
          if (Oe[ie])
            return;
          De("Can't call %s on a component that is not yet mounted. This is a no-op, but it might indicate a bug in your application. Instead, assign to `this.state` directly or define a `state = {};` class property with the desired state in the %s component.", b, Y), Oe[ie] = !0;
        }
      }
      var L = {
        /**
         * Checks whether or not this composite component is mounted.
         * @param {ReactClass} publicInstance The instance we want to test.
         * @return {boolean} True if mounted, false otherwise.
         * @protected
         * @final
         */
        isMounted: function(g) {
          return !1;
        },
        /**
         * Forces an update. This should only be invoked when it is known with
         * certainty that we are **not** in a DOM transaction.
         *
         * You may want to call this when you know that some deeper aspect of the
         * component's state has changed but `setState` was not called.
         *
         * This will not invoke `shouldComponentUpdate`, but it will invoke
         * `componentWillUpdate` and `componentDidUpdate`.
         *
         * @param {ReactClass} publicInstance The instance that should rerender.
         * @param {?function} callback Called after component is updated.
         * @param {?string} callerName name of the calling function in the public API.
         * @internal
         */
        enqueueForceUpdate: function(g, b, V) {
          se(g, "forceUpdate");
        },
        /**
         * Replaces all of the state. Always use this or `setState` to mutate state.
         * You should treat `this.state` as immutable.
         *
         * There is no guarantee that `this.state` will be immediately updated, so
         * accessing `this.state` after calling this method may return the old value.
         *
         * @param {ReactClass} publicInstance The instance that should rerender.
         * @param {object} completeState Next state.
         * @param {?function} callback Called after component is updated.
         * @param {?string} callerName name of the calling function in the public API.
         * @internal
         */
        enqueueReplaceState: function(g, b, V, Y) {
          se(g, "replaceState");
        },
        /**
         * Sets a subset of the state. This only exists because _pendingState is
         * internal. This provides a merging strategy that is not available to deep
         * properties which is confusing. TODO: Expose pendingState or don't use it
         * during the merge.
         *
         * @param {ReactClass} publicInstance The instance that should rerender.
         * @param {object} partialState Next partial state to be merged with state.
         * @param {?function} callback Called after component is updated.
         * @param {?string} Name of the calling function in the public API.
         * @internal
         */
        enqueueSetState: function(g, b, V, Y) {
          se(g, "setState");
        }
      }, Z = Object.assign, Ze = {};
      Object.freeze(Ze);
      function Ye(g, b, V) {
        this.props = g, this.context = b, this.refs = Ze, this.updater = V || L;
      }
      Ye.prototype.isReactComponent = {}, Ye.prototype.setState = function(g, b) {
        if (typeof g != "object" && typeof g != "function" && g != null)
          throw new Error("setState(...): takes an object of state variables to update or a function which returns an object of state variables.");
        this.updater.enqueueSetState(this, g, b, "setState");
      }, Ye.prototype.forceUpdate = function(g) {
        this.updater.enqueueForceUpdate(this, g, "forceUpdate");
      };
      {
        var vt = {
          isMounted: ["isMounted", "Instead, make sure to clean up subscriptions and pending requests in componentWillUnmount to prevent memory leaks."],
          replaceState: ["replaceState", "Refactor your code to use setState instead (see https://github.com/facebook/react/issues/3236)."]
        }, ct = function(g, b) {
          Object.defineProperty(Ye.prototype, g, {
            get: function() {
              Ft("%s(...) is deprecated in plain JavaScript React classes. %s", b[0], b[1]);
            }
          });
        };
        for (var ot in vt)
          vt.hasOwnProperty(ot) && ct(ot, vt[ot]);
      }
      function ft() {
      }
      ft.prototype = Ye.prototype;
      function ht(g, b, V) {
        this.props = g, this.context = b, this.refs = Ze, this.updater = V || L;
      }
      var Xt = ht.prototype = new ft();
      Xt.constructor = ht, Z(Xt, Ye.prototype), Xt.isPureReactComponent = !0;
      function Hn() {
        var g = {
          current: null
        };
        return Object.seal(g), g;
      }
      var Ur = Array.isArray;
      function On(g) {
        return Ur(g);
      }
      function pr(g) {
        {
          var b = typeof Symbol == "function" && Symbol.toStringTag, V = b && g[Symbol.toStringTag] || g.constructor.name || "Object";
          return V;
        }
      }
      function qn(g) {
        try {
          return Xn(g), !1;
        } catch {
          return !0;
        }
      }
      function Xn(g) {
        return "" + g;
      }
      function ta(g) {
        if (qn(g))
          return De("The provided key is an unsupported type %s. This value must be coerced to a string before before using it here.", pr(g)), Xn(g);
      }
      function _i(g, b, V) {
        var Y = g.displayName;
        if (Y)
          return Y;
        var ie = b.displayName || b.name || "";
        return ie !== "" ? V + "(" + ie + ")" : V;
      }
      function Sa(g) {
        return g.displayName || "Context";
      }
      function ir(g) {
        if (g == null)
          return null;
        if (typeof g.tag == "number" && De("Received an unexpected object in getComponentNameFromType(). This is likely a bug in React. Please file an issue."), typeof g == "function")
          return g.displayName || g.name || null;
        if (typeof g == "string")
          return g;
        switch (g) {
          case T:
            return "Fragment";
          case _:
            return "Portal";
          case A:
            return "Profiler";
          case E:
            return "StrictMode";
          case re:
            return "Suspense";
          case be:
            return "SuspenseList";
        }
        if (typeof g == "object")
          switch (g.$$typeof) {
            case $:
              var b = g;
              return Sa(b) + ".Consumer";
            case I:
              var V = g;
              return Sa(V._context) + ".Provider";
            case fe:
              return _i(g, g.render, "ForwardRef");
            case de:
              var Y = g.displayName || null;
              return Y !== null ? Y : ir(g.type) || "Memo";
            case nt: {
              var ie = g, Ve = ie._payload, ce = ie._init;
              try {
                return ir(ce(Ve));
              } catch {
                return null;
              }
            }
          }
        return null;
      }
      var Nn = Object.prototype.hasOwnProperty, Kn = {
        key: !0,
        ref: !0,
        __self: !0,
        __source: !0
      }, Dr, ri, Vn;
      Vn = {};
      function Or(g) {
        if (Nn.call(g, "ref")) {
          var b = Object.getOwnPropertyDescriptor(g, "ref").get;
          if (b && b.isReactWarning)
            return !1;
        }
        return g.ref !== void 0;
      }
      function Ea(g) {
        if (Nn.call(g, "key")) {
          var b = Object.getOwnPropertyDescriptor(g, "key").get;
          if (b && b.isReactWarning)
            return !1;
        }
        return g.key !== void 0;
      }
      function ai(g, b) {
        var V = function() {
          Dr || (Dr = !0, De("%s: `key` is not a prop. Trying to access it will result in `undefined` being returned. If you need to access the same value within the child component, you should pass it as a different prop. (https://reactjs.org/link/special-props)", b));
        };
        V.isReactWarning = !0, Object.defineProperty(g, "key", {
          get: V,
          configurable: !0
        });
      }
      function xi(g, b) {
        var V = function() {
          ri || (ri = !0, De("%s: `ref` is not a prop. Trying to access it will result in `undefined` being returned. If you need to access the same value within the child component, you should pass it as a different prop. (https://reactjs.org/link/special-props)", b));
        };
        V.isReactWarning = !0, Object.defineProperty(g, "ref", {
          get: V,
          configurable: !0
        });
      }
      function ue(g) {
        if (typeof g.ref == "string" && St.current && g.__self && St.current.stateNode !== g.__self) {
          var b = ir(St.current.type);
          Vn[b] || (De('Component "%s" contains the string ref "%s". Support for string refs will be removed in a future major release. This case cannot be automatically converted to an arrow function. We ask you to manually fix this case by using useRef() or createRef() instead. Learn more about using refs safely here: https://reactjs.org/link/strict-mode-string-ref', b, g.ref), Vn[b] = !0);
        }
      }
      var Ne = function(g, b, V, Y, ie, Ve, ce) {
        var Ie = {
          // This tag allows us to uniquely identify this as a React Element
          $$typeof: S,
          // Built-in properties that belong on the element
          type: g,
          key: b,
          ref: V,
          props: ce,
          // Record the component responsible for creating this element.
          _owner: Ve
        };
        return Ie._store = {}, Object.defineProperty(Ie._store, "validated", {
          configurable: !1,
          enumerable: !1,
          writable: !0,
          value: !1
        }), Object.defineProperty(Ie, "_self", {
          configurable: !1,
          enumerable: !1,
          writable: !1,
          value: Y
        }), Object.defineProperty(Ie, "_source", {
          configurable: !1,
          enumerable: !1,
          writable: !1,
          value: ie
        }), Object.freeze && (Object.freeze(Ie.props), Object.freeze(Ie)), Ie;
      };
      function dt(g, b, V) {
        var Y, ie = {}, Ve = null, ce = null, Ie = null, Ct = null;
        if (b != null) {
          Or(b) && (ce = b.ref, ue(b)), Ea(b) && (ta(b.key), Ve = "" + b.key), Ie = b.__self === void 0 ? null : b.__self, Ct = b.__source === void 0 ? null : b.__source;
          for (Y in b)
            Nn.call(b, Y) && !Kn.hasOwnProperty(Y) && (ie[Y] = b[Y]);
        }
        var At = arguments.length - 2;
        if (At === 1)
          ie.children = V;
        else if (At > 1) {
          for (var dn = Array(At), tn = 0; tn < At; tn++)
            dn[tn] = arguments[tn + 2];
          Object.freeze && Object.freeze(dn), ie.children = dn;
        }
        if (g && g.defaultProps) {
          var pt = g.defaultProps;
          for (Y in pt)
            ie[Y] === void 0 && (ie[Y] = pt[Y]);
        }
        if (Ve || ce) {
          var nn = typeof g == "function" ? g.displayName || g.name || "Unknown" : g;
          Ve && ai(ie, nn), ce && xi(ie, nn);
        }
        return Ne(g, Ve, ce, Ie, Ct, St.current, ie);
      }
      function Wt(g, b) {
        var V = Ne(g.type, b, g.ref, g._self, g._source, g._owner, g.props);
        return V;
      }
      function sn(g, b, V) {
        if (g == null)
          throw new Error("React.cloneElement(...): The argument must be a React element, but you passed " + g + ".");
        var Y, ie = Z({}, g.props), Ve = g.key, ce = g.ref, Ie = g._self, Ct = g._source, At = g._owner;
        if (b != null) {
          Or(b) && (ce = b.ref, At = St.current), Ea(b) && (ta(b.key), Ve = "" + b.key);
          var dn;
          g.type && g.type.defaultProps && (dn = g.type.defaultProps);
          for (Y in b)
            Nn.call(b, Y) && !Kn.hasOwnProperty(Y) && (b[Y] === void 0 && dn !== void 0 ? ie[Y] = dn[Y] : ie[Y] = b[Y]);
        }
        var tn = arguments.length - 2;
        if (tn === 1)
          ie.children = V;
        else if (tn > 1) {
          for (var pt = Array(tn), nn = 0; nn < tn; nn++)
            pt[nn] = arguments[nn + 2];
          ie.children = pt;
        }
        return Ne(g.type, Ve, ce, Ie, Ct, At, ie);
      }
      function xn(g) {
        return typeof g == "object" && g !== null && g.$$typeof === S;
      }
      var mn = ".", lr = ":";
      function cn(g) {
        var b = /[=:]/g, V = {
          "=": "=0",
          ":": "=2"
        }, Y = g.replace(b, function(ie) {
          return V[ie];
        });
        return "$" + Y;
      }
      var Kt = !1, Jt = /\/+/g;
      function Ca(g) {
        return g.replace(Jt, "$&/");
      }
      function Nr(g, b) {
        return typeof g == "object" && g !== null && g.key != null ? (ta(g.key), cn("" + g.key)) : b.toString(36);
      }
      function za(g, b, V, Y, ie) {
        var Ve = typeof g;
        (Ve === "undefined" || Ve === "boolean") && (g = null);
        var ce = !1;
        if (g === null)
          ce = !0;
        else
          switch (Ve) {
            case "string":
            case "number":
              ce = !0;
              break;
            case "object":
              switch (g.$$typeof) {
                case S:
                case _:
                  ce = !0;
              }
          }
        if (ce) {
          var Ie = g, Ct = ie(Ie), At = Y === "" ? mn + Nr(Ie, 0) : Y;
          if (On(Ct)) {
            var dn = "";
            At != null && (dn = Ca(At) + "/"), za(Ct, b, dn, "", function(Rd) {
              return Rd;
            });
          } else Ct != null && (xn(Ct) && (Ct.key && (!Ie || Ie.key !== Ct.key) && ta(Ct.key), Ct = Wt(
            Ct,
            // Keep both the (mapped) and old keys if they differ, just as
            // traverseAllChildren used to do for objects as children
            V + // $FlowFixMe Flow incorrectly thinks React.Portal doesn't have a key
            (Ct.key && (!Ie || Ie.key !== Ct.key) ? (
              // $FlowFixMe Flow incorrectly thinks existing element's key can be a number
              // eslint-disable-next-line react-internal/safe-string-coercion
              Ca("" + Ct.key) + "/"
            ) : "") + At
          )), b.push(Ct));
          return 1;
        }
        var tn, pt, nn = 0, Tn = Y === "" ? mn : Y + lr;
        if (On(g))
          for (var zl = 0; zl < g.length; zl++)
            tn = g[zl], pt = Tn + Nr(tn, zl), nn += za(tn, b, V, pt, ie);
        else {
          var gs = _t(g);
          if (typeof gs == "function") {
            var tl = g;
            gs === tl.entries && (Kt || Ft("Using Maps as children is not supported. Use an array of keyed ReactElements instead."), Kt = !0);
            for (var Ss = gs.call(tl), Tu, Td = 0; !(Tu = Ss.next()).done; )
              tn = Tu.value, pt = Tn + Nr(tn, Td++), nn += za(tn, b, V, pt, ie);
          } else if (Ve === "object") {
            var Ac = String(g);
            throw new Error("Objects are not valid as a React child (found: " + (Ac === "[object Object]" ? "object with keys {" + Object.keys(g).join(", ") + "}" : Ac) + "). If you meant to render a collection of children, use an array instead.");
          }
        }
        return nn;
      }
      function Ki(g, b, V) {
        if (g == null)
          return g;
        var Y = [], ie = 0;
        return za(g, Y, "", "", function(Ve) {
          return b.call(V, Ve, ie++);
        }), Y;
      }
      function hu(g) {
        var b = 0;
        return Ki(g, function() {
          b++;
        }), b;
      }
      function mu(g, b, V) {
        Ki(g, function() {
          b.apply(this, arguments);
        }, V);
      }
      function wl(g) {
        return Ki(g, function(b) {
          return b;
        }) || [];
      }
      function bl(g) {
        if (!xn(g))
          throw new Error("React.Children.only expected to receive a single React element child.");
        return g;
      }
      function yu(g) {
        var b = {
          $$typeof: $,
          // As a workaround to support multiple concurrent renderers, we categorize
          // some renderers as primary and others as secondary. We only expect
          // there to be two concurrent renderers at most: React Native (primary) and
          // Fabric (secondary); React DOM (primary) and React ART (secondary).
          // Secondary renderers store their context values on separate fields.
          _currentValue: g,
          _currentValue2: g,
          // Used to track how many concurrent renderers this context currently
          // supports within in a single renderer. Such as parallel server rendering.
          _threadCount: 0,
          // These are circular
          Provider: null,
          Consumer: null,
          // Add these to use same hidden class in VM as ServerContext
          _defaultValue: null,
          _globalName: null
        };
        b.Provider = {
          $$typeof: I,
          _context: b
        };
        var V = !1, Y = !1, ie = !1;
        {
          var Ve = {
            $$typeof: $,
            _context: b
          };
          Object.defineProperties(Ve, {
            Provider: {
              get: function() {
                return Y || (Y = !0, De("Rendering <Context.Consumer.Provider> is not supported and will be removed in a future major release. Did you mean to render <Context.Provider> instead?")), b.Provider;
              },
              set: function(ce) {
                b.Provider = ce;
              }
            },
            _currentValue: {
              get: function() {
                return b._currentValue;
              },
              set: function(ce) {
                b._currentValue = ce;
              }
            },
            _currentValue2: {
              get: function() {
                return b._currentValue2;
              },
              set: function(ce) {
                b._currentValue2 = ce;
              }
            },
            _threadCount: {
              get: function() {
                return b._threadCount;
              },
              set: function(ce) {
                b._threadCount = ce;
              }
            },
            Consumer: {
              get: function() {
                return V || (V = !0, De("Rendering <Context.Consumer.Consumer> is not supported and will be removed in a future major release. Did you mean to render <Context.Consumer> instead?")), b.Consumer;
              }
            },
            displayName: {
              get: function() {
                return b.displayName;
              },
              set: function(ce) {
                ie || (Ft("Setting `displayName` on Context.Consumer has no effect. You should set it directly on the context with Context.displayName = '%s'.", ce), ie = !0);
              }
            }
          }), b.Consumer = Ve;
        }
        return b._currentRenderer = null, b._currentRenderer2 = null, b;
      }
      var jr = -1, Fr = 0, vr = 1, Ti = 2;
      function ii(g) {
        if (g._status === jr) {
          var b = g._result, V = b();
          if (V.then(function(Ve) {
            if (g._status === Fr || g._status === jr) {
              var ce = g;
              ce._status = vr, ce._result = Ve;
            }
          }, function(Ve) {
            if (g._status === Fr || g._status === jr) {
              var ce = g;
              ce._status = Ti, ce._result = Ve;
            }
          }), g._status === jr) {
            var Y = g;
            Y._status = Fr, Y._result = V;
          }
        }
        if (g._status === vr) {
          var ie = g._result;
          return ie === void 0 && De(`lazy: Expected the result of a dynamic import() call. Instead received: %s

Your code should look like: 
  const MyComponent = lazy(() => import('./MyComponent'))

Did you accidentally put curly braces around the import?`, ie), "default" in ie || De(`lazy: Expected the result of a dynamic import() call. Instead received: %s

Your code should look like: 
  const MyComponent = lazy(() => import('./MyComponent'))`, ie), ie.default;
        } else
          throw g._result;
      }
      function Ri(g) {
        var b = {
          // We use these fields to store the result.
          _status: jr,
          _result: g
        }, V = {
          $$typeof: nt,
          _payload: b,
          _init: ii
        };
        {
          var Y, ie;
          Object.defineProperties(V, {
            defaultProps: {
              configurable: !0,
              get: function() {
                return Y;
              },
              set: function(Ve) {
                De("React.lazy(...): It is not supported to assign `defaultProps` to a lazy component import. Either specify them where the component is defined, or create a wrapping component around it."), Y = Ve, Object.defineProperty(V, "defaultProps", {
                  enumerable: !0
                });
              }
            },
            propTypes: {
              configurable: !0,
              get: function() {
                return ie;
              },
              set: function(Ve) {
                De("React.lazy(...): It is not supported to assign `propTypes` to a lazy component import. Either specify them where the component is defined, or create a wrapping component around it."), ie = Ve, Object.defineProperty(V, "propTypes", {
                  enumerable: !0
                });
              }
            }
          });
        }
        return V;
      }
      function wi(g) {
        g != null && g.$$typeof === de ? De("forwardRef requires a render function but received a `memo` component. Instead of forwardRef(memo(...)), use memo(forwardRef(...)).") : typeof g != "function" ? De("forwardRef requires a render function but was given %s.", g === null ? "null" : typeof g) : g.length !== 0 && g.length !== 2 && De("forwardRef render functions accept exactly two parameters: props and ref. %s", g.length === 1 ? "Did you forget to use the ref parameter?" : "Any additional parameter will be undefined."), g != null && (g.defaultProps != null || g.propTypes != null) && De("forwardRef render functions do not support propTypes or defaultProps. Did you accidentally pass a React component?");
        var b = {
          $$typeof: fe,
          render: g
        };
        {
          var V;
          Object.defineProperty(b, "displayName", {
            enumerable: !1,
            configurable: !0,
            get: function() {
              return V;
            },
            set: function(Y) {
              V = Y, !g.name && !g.displayName && (g.displayName = Y);
            }
          });
        }
        return b;
      }
      var k;
      k = Symbol.for("react.module.reference");
      function q(g) {
        return !!(typeof g == "string" || typeof g == "function" || g === T || g === A || Yt || g === E || g === re || g === be || He || g === bt || on || hn || zt || typeof g == "object" && g !== null && (g.$$typeof === nt || g.$$typeof === de || g.$$typeof === I || g.$$typeof === $ || g.$$typeof === fe || // This needs to include all possible module reference object
        // types supported by any Flight configuration anywhere since
        // we don't know which Flight build this will end up being used
        // with.
        g.$$typeof === k || g.getModuleId !== void 0));
      }
      function pe(g, b) {
        q(g) || De("memo: The first argument must be a component. Instead received: %s", g === null ? "null" : typeof g);
        var V = {
          $$typeof: de,
          type: g,
          compare: b === void 0 ? null : b
        };
        {
          var Y;
          Object.defineProperty(V, "displayName", {
            enumerable: !1,
            configurable: !0,
            get: function() {
              return Y;
            },
            set: function(ie) {
              Y = ie, !g.name && !g.displayName && (g.displayName = ie);
            }
          });
        }
        return V;
      }
      function _e() {
        var g = rt.current;
        return g === null && De(`Invalid hook call. Hooks can only be called inside of the body of a function component. This could happen for one of the following reasons:
1. You might have mismatching versions of React and the renderer (such as React DOM)
2. You might be breaking the Rules of Hooks
3. You might have more than one copy of React in the same app
See https://reactjs.org/link/invalid-hook-call for tips about how to debug and fix this problem.`), g;
      }
      function it(g) {
        var b = _e();
        if (g._context !== void 0) {
          var V = g._context;
          V.Consumer === g ? De("Calling useContext(Context.Consumer) is not supported, may cause bugs, and will be removed in a future major release. Did you mean to call useContext(Context) instead?") : V.Provider === g && De("Calling useContext(Context.Provider) is not supported. Did you mean to call useContext(Context) instead?");
        }
        return b.useContext(g);
      }
      function Ke(g) {
        var b = _e();
        return b.useState(g);
      }
      function Et(g, b, V) {
        var Y = _e();
        return Y.useReducer(g, b, V);
      }
      function mt(g) {
        var b = _e();
        return b.useRef(g);
      }
      function Mn(g, b) {
        var V = _e();
        return V.useEffect(g, b);
      }
      function fn(g, b) {
        var V = _e();
        return V.useInsertionEffect(g, b);
      }
      function yn(g, b) {
        var V = _e();
        return V.useLayoutEffect(g, b);
      }
      function hr(g, b) {
        var V = _e();
        return V.useCallback(g, b);
      }
      function li(g, b) {
        var V = _e();
        return V.useMemo(g, b);
      }
      function ui(g, b, V) {
        var Y = _e();
        return Y.useImperativeHandle(g, b, V);
      }
      function lt(g, b) {
        {
          var V = _e();
          return V.useDebugValue(g, b);
        }
      }
      function st() {
        var g = _e();
        return g.useTransition();
      }
      function oi(g) {
        var b = _e();
        return b.useDeferredValue(g);
      }
      function gu() {
        var g = _e();
        return g.useId();
      }
      function Su(g, b, V) {
        var Y = _e();
        return Y.useSyncExternalStore(g, b, V);
      }
      var kl = 0, vo, Dl, na, vs, Hr, Mc, Lc;
      function ho() {
      }
      ho.__reactDisabledLog = !0;
      function Ol() {
        {
          if (kl === 0) {
            vo = console.log, Dl = console.info, na = console.warn, vs = console.error, Hr = console.group, Mc = console.groupCollapsed, Lc = console.groupEnd;
            var g = {
              configurable: !0,
              enumerable: !0,
              value: ho,
              writable: !0
            };
            Object.defineProperties(console, {
              info: g,
              log: g,
              warn: g,
              error: g,
              group: g,
              groupCollapsed: g,
              groupEnd: g
            });
          }
          kl++;
        }
      }
      function _a() {
        {
          if (kl--, kl === 0) {
            var g = {
              configurable: !0,
              enumerable: !0,
              writable: !0
            };
            Object.defineProperties(console, {
              log: Z({}, g, {
                value: vo
              }),
              info: Z({}, g, {
                value: Dl
              }),
              warn: Z({}, g, {
                value: na
              }),
              error: Z({}, g, {
                value: vs
              }),
              group: Z({}, g, {
                value: Hr
              }),
              groupCollapsed: Z({}, g, {
                value: Mc
              }),
              groupEnd: Z({}, g, {
                value: Lc
              })
            });
          }
          kl < 0 && De("disabledDepth fell below zero. This is a bug in React. Please file an issue.");
        }
      }
      var si = Ut.ReactCurrentDispatcher, ci;
      function mo(g, b, V) {
        {
          if (ci === void 0)
            try {
              throw Error();
            } catch (ie) {
              var Y = ie.stack.trim().match(/\n( *(at )?)/);
              ci = Y && Y[1] || "";
            }
          return `
` + ci + g;
        }
      }
      var Eu = !1, Nl;
      {
        var yo = typeof WeakMap == "function" ? WeakMap : Map;
        Nl = new yo();
      }
      function go(g, b) {
        if (!g || Eu)
          return "";
        {
          var V = Nl.get(g);
          if (V !== void 0)
            return V;
        }
        var Y;
        Eu = !0;
        var ie = Error.prepareStackTrace;
        Error.prepareStackTrace = void 0;
        var Ve;
        Ve = si.current, si.current = null, Ol();
        try {
          if (b) {
            var ce = function() {
              throw Error();
            };
            if (Object.defineProperty(ce.prototype, "props", {
              set: function() {
                throw Error();
              }
            }), typeof Reflect == "object" && Reflect.construct) {
              try {
                Reflect.construct(ce, []);
              } catch (Tn) {
                Y = Tn;
              }
              Reflect.construct(g, [], ce);
            } else {
              try {
                ce.call();
              } catch (Tn) {
                Y = Tn;
              }
              g.call(ce.prototype);
            }
          } else {
            try {
              throw Error();
            } catch (Tn) {
              Y = Tn;
            }
            g();
          }
        } catch (Tn) {
          if (Tn && Y && typeof Tn.stack == "string") {
            for (var Ie = Tn.stack.split(`
`), Ct = Y.stack.split(`
`), At = Ie.length - 1, dn = Ct.length - 1; At >= 1 && dn >= 0 && Ie[At] !== Ct[dn]; )
              dn--;
            for (; At >= 1 && dn >= 0; At--, dn--)
              if (Ie[At] !== Ct[dn]) {
                if (At !== 1 || dn !== 1)
                  do
                    if (At--, dn--, dn < 0 || Ie[At] !== Ct[dn]) {
                      var tn = `
` + Ie[At].replace(" at new ", " at ");
                      return g.displayName && tn.includes("<anonymous>") && (tn = tn.replace("<anonymous>", g.displayName)), typeof g == "function" && Nl.set(g, tn), tn;
                    }
                  while (At >= 1 && dn >= 0);
                break;
              }
          }
        } finally {
          Eu = !1, si.current = Ve, _a(), Error.prepareStackTrace = ie;
        }
        var pt = g ? g.displayName || g.name : "", nn = pt ? mo(pt) : "";
        return typeof g == "function" && Nl.set(g, nn), nn;
      }
      function Ji(g, b, V) {
        return go(g, !1);
      }
      function _d(g) {
        var b = g.prototype;
        return !!(b && b.isReactComponent);
      }
      function el(g, b, V) {
        if (g == null)
          return "";
        if (typeof g == "function")
          return go(g, _d(g));
        if (typeof g == "string")
          return mo(g);
        switch (g) {
          case re:
            return mo("Suspense");
          case be:
            return mo("SuspenseList");
        }
        if (typeof g == "object")
          switch (g.$$typeof) {
            case fe:
              return Ji(g.render);
            case de:
              return el(g.type, b, V);
            case nt: {
              var Y = g, ie = Y._payload, Ve = Y._init;
              try {
                return el(Ve(ie), b, V);
              } catch {
              }
            }
          }
        return "";
      }
      var Ht = {}, So = Ut.ReactDebugCurrentFrame;
      function Lt(g) {
        if (g) {
          var b = g._owner, V = el(g.type, g._source, b ? b.type : null);
          So.setExtraStackFrame(V);
        } else
          So.setExtraStackFrame(null);
      }
      function hs(g, b, V, Y, ie) {
        {
          var Ve = Function.call.bind(Nn);
          for (var ce in g)
            if (Ve(g, ce)) {
              var Ie = void 0;
              try {
                if (typeof g[ce] != "function") {
                  var Ct = Error((Y || "React class") + ": " + V + " type `" + ce + "` is invalid; it must be a function, usually from the `prop-types` package, but received `" + typeof g[ce] + "`.This often happens because of typos such as `PropTypes.function` instead of `PropTypes.func`.");
                  throw Ct.name = "Invariant Violation", Ct;
                }
                Ie = g[ce](b, ce, Y, V, null, "SECRET_DO_NOT_PASS_THIS_OR_YOU_WILL_BE_FIRED");
              } catch (At) {
                Ie = At;
              }
              Ie && !(Ie instanceof Error) && (Lt(ie), De("%s: type specification of %s `%s` is invalid; the type checker function must return `null` or an `Error` but returned a %s. You may have forgotten to pass an argument to the type checker creator (arrayOf, instanceOf, objectOf, oneOf, oneOfType, and shape all require an argument).", Y || "React class", V, ce, typeof Ie), Lt(null)), Ie instanceof Error && !(Ie.message in Ht) && (Ht[Ie.message] = !0, Lt(ie), De("Failed %s type: %s", V, Ie.message), Lt(null));
            }
        }
      }
      function bi(g) {
        if (g) {
          var b = g._owner, V = el(g.type, g._source, b ? b.type : null);
          Zt(V);
        } else
          Zt(null);
      }
      var Xe;
      Xe = !1;
      function Eo() {
        if (St.current) {
          var g = ir(St.current.type);
          if (g)
            return `

Check the render method of \`` + g + "`.";
        }
        return "";
      }
      function mr(g) {
        if (g !== void 0) {
          var b = g.fileName.replace(/^.*[\\\/]/, ""), V = g.lineNumber;
          return `

Check your code at ` + b + ":" + V + ".";
        }
        return "";
      }
      function ki(g) {
        return g != null ? mr(g.__source) : "";
      }
      var Vr = {};
      function Di(g) {
        var b = Eo();
        if (!b) {
          var V = typeof g == "string" ? g : g.displayName || g.name;
          V && (b = `

Check the top-level render call using <` + V + ">.");
        }
        return b;
      }
      function gn(g, b) {
        if (!(!g._store || g._store.validated || g.key != null)) {
          g._store.validated = !0;
          var V = Di(b);
          if (!Vr[V]) {
            Vr[V] = !0;
            var Y = "";
            g && g._owner && g._owner !== St.current && (Y = " It was passed a child from " + ir(g._owner.type) + "."), bi(g), De('Each child in a list should have a unique "key" prop.%s%s See https://reactjs.org/link/warning-keys for more information.', V, Y), bi(null);
          }
        }
      }
      function en(g, b) {
        if (typeof g == "object") {
          if (On(g))
            for (var V = 0; V < g.length; V++) {
              var Y = g[V];
              xn(Y) && gn(Y, b);
            }
          else if (xn(g))
            g._store && (g._store.validated = !0);
          else if (g) {
            var ie = _t(g);
            if (typeof ie == "function" && ie !== g.entries)
              for (var Ve = ie.call(g), ce; !(ce = Ve.next()).done; )
                xn(ce.value) && gn(ce.value, b);
          }
        }
      }
      function Ml(g) {
        {
          var b = g.type;
          if (b == null || typeof b == "string")
            return;
          var V;
          if (typeof b == "function")
            V = b.propTypes;
          else if (typeof b == "object" && (b.$$typeof === fe || // Note: Memo only checks outer props here.
          // Inner props are checked in the reconciler.
          b.$$typeof === de))
            V = b.propTypes;
          else
            return;
          if (V) {
            var Y = ir(b);
            hs(V, g.props, "prop", Y, g);
          } else if (b.PropTypes !== void 0 && !Xe) {
            Xe = !0;
            var ie = ir(b);
            De("Component %s declared `PropTypes` instead of `propTypes`. Did you misspell the property assignment?", ie || "Unknown");
          }
          typeof b.getDefaultProps == "function" && !b.getDefaultProps.isReactClassApproved && De("getDefaultProps is only used on classic React.createClass definitions. Use a static property named `defaultProps` instead.");
        }
      }
      function Jn(g) {
        {
          for (var b = Object.keys(g.props), V = 0; V < b.length; V++) {
            var Y = b[V];
            if (Y !== "children" && Y !== "key") {
              bi(g), De("Invalid prop `%s` supplied to `React.Fragment`. React.Fragment can only have `key` and `children` props.", Y), bi(null);
              break;
            }
          }
          g.ref !== null && (bi(g), De("Invalid attribute `ref` supplied to `React.Fragment`."), bi(null));
        }
      }
      function Pr(g, b, V) {
        var Y = q(g);
        if (!Y) {
          var ie = "";
          (g === void 0 || typeof g == "object" && g !== null && Object.keys(g).length === 0) && (ie += " You likely forgot to export your component from the file it's defined in, or you might have mixed up default and named imports.");
          var Ve = ki(b);
          Ve ? ie += Ve : ie += Eo();
          var ce;
          g === null ? ce = "null" : On(g) ? ce = "array" : g !== void 0 && g.$$typeof === S ? (ce = "<" + (ir(g.type) || "Unknown") + " />", ie = " Did you accidentally export a JSX literal instead of a component?") : ce = typeof g, De("React.createElement: type is invalid -- expected a string (for built-in components) or a class/function (for composite components) but got: %s.%s", ce, ie);
        }
        var Ie = dt.apply(this, arguments);
        if (Ie == null)
          return Ie;
        if (Y)
          for (var Ct = 2; Ct < arguments.length; Ct++)
            en(arguments[Ct], g);
        return g === T ? Jn(Ie) : Ml(Ie), Ie;
      }
      var Ua = !1;
      function Cu(g) {
        var b = Pr.bind(null, g);
        return b.type = g, Ua || (Ua = !0, Ft("React.createFactory() is deprecated and will be removed in a future major release. Consider using JSX or use React.createElement() directly instead.")), Object.defineProperty(b, "type", {
          enumerable: !1,
          get: function() {
            return Ft("Factory.type is deprecated. Access the class directly before passing it to createFactory."), Object.defineProperty(this, "type", {
              value: g
            }), g;
          }
        }), b;
      }
      function ms(g, b, V) {
        for (var Y = sn.apply(this, arguments), ie = 2; ie < arguments.length; ie++)
          en(arguments[ie], Y.type);
        return Ml(Y), Y;
      }
      function ys(g, b) {
        var V = Tt.transition;
        Tt.transition = {};
        var Y = Tt.transition;
        Tt.transition._updatedFibers = /* @__PURE__ */ new Set();
        try {
          g();
        } finally {
          if (Tt.transition = V, V === null && Y._updatedFibers) {
            var ie = Y._updatedFibers.size;
            ie > 10 && Ft("Detected a large number of updates inside startTransition. If this is due to a subscription please re-write it to use React provided hooks. Otherwise concurrent mode guarantees are off the table."), Y._updatedFibers.clear();
          }
        }
      }
      var Ll = !1, _u = null;
      function xd(g) {
        if (_u === null)
          try {
            var b = ("require" + Math.random()).slice(0, 7), V = h && h[b];
            _u = V.call(h, "timers").setImmediate;
          } catch {
            _u = function(ie) {
              Ll === !1 && (Ll = !0, typeof MessageChannel > "u" && De("This browser does not have a MessageChannel implementation, so enqueuing tasks via await act(async () => ...) will fail. Please file an issue at https://github.com/facebook/react/issues if you encounter this warning."));
              var Ve = new MessageChannel();
              Ve.port1.onmessage = ie, Ve.port2.postMessage(void 0);
            };
          }
        return _u(g);
      }
      var ja = 0, fi = !1;
      function Oi(g) {
        {
          var b = ja;
          ja++, ze.current === null && (ze.current = []);
          var V = ze.isBatchingLegacy, Y;
          try {
            if (ze.isBatchingLegacy = !0, Y = g(), !V && ze.didScheduleLegacyUpdate) {
              var ie = ze.current;
              ie !== null && (ze.didScheduleLegacyUpdate = !1, Al(ie));
            }
          } catch (pt) {
            throw Fa(b), pt;
          } finally {
            ze.isBatchingLegacy = V;
          }
          if (Y !== null && typeof Y == "object" && typeof Y.then == "function") {
            var Ve = Y, ce = !1, Ie = {
              then: function(pt, nn) {
                ce = !0, Ve.then(function(Tn) {
                  Fa(b), ja === 0 ? Co(Tn, pt, nn) : pt(Tn);
                }, function(Tn) {
                  Fa(b), nn(Tn);
                });
              }
            };
            return !fi && typeof Promise < "u" && Promise.resolve().then(function() {
            }).then(function() {
              ce || (fi = !0, De("You called act(async () => ...) without await. This could lead to unexpected testing behaviour, interleaving multiple act calls and mixing their scopes. You should - await act(async () => ...);"));
            }), Ie;
          } else {
            var Ct = Y;
            if (Fa(b), ja === 0) {
              var At = ze.current;
              At !== null && (Al(At), ze.current = null);
              var dn = {
                then: function(pt, nn) {
                  ze.current === null ? (ze.current = [], Co(Ct, pt, nn)) : pt(Ct);
                }
              };
              return dn;
            } else {
              var tn = {
                then: function(pt, nn) {
                  pt(Ct);
                }
              };
              return tn;
            }
          }
        }
      }
      function Fa(g) {
        g !== ja - 1 && De("You seem to have overlapping act() calls, this is not supported. Be sure to await previous act() calls before making a new one. "), ja = g;
      }
      function Co(g, b, V) {
        {
          var Y = ze.current;
          if (Y !== null)
            try {
              Al(Y), xd(function() {
                Y.length === 0 ? (ze.current = null, b(g)) : Co(g, b, V);
              });
            } catch (ie) {
              V(ie);
            }
          else
            b(g);
        }
      }
      var _o = !1;
      function Al(g) {
        if (!_o) {
          _o = !0;
          var b = 0;
          try {
            for (; b < g.length; b++) {
              var V = g[b];
              do
                V = V(!0);
              while (V !== null);
            }
            g.length = 0;
          } catch (Y) {
            throw g = g.slice(b + 1), Y;
          } finally {
            _o = !1;
          }
        }
      }
      var xu = Pr, xo = ms, To = Cu, di = {
        map: Ki,
        forEach: mu,
        count: hu,
        toArray: wl,
        only: bl
      };
      c.Children = di, c.Component = Ye, c.Fragment = T, c.Profiler = A, c.PureComponent = ht, c.StrictMode = E, c.Suspense = re, c.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED = Ut, c.act = Oi, c.cloneElement = xo, c.createContext = yu, c.createElement = xu, c.createFactory = To, c.createRef = Hn, c.forwardRef = wi, c.isValidElement = xn, c.lazy = Ri, c.memo = pe, c.startTransition = ys, c.unstable_act = Oi, c.useCallback = hr, c.useContext = it, c.useDebugValue = lt, c.useDeferredValue = oi, c.useEffect = Mn, c.useId = gu, c.useImperativeHandle = ui, c.useInsertionEffect = fn, c.useLayoutEffect = yn, c.useMemo = li, c.useReducer = Et, c.useRef = mt, c.useState = Ke, c.useSyncExternalStore = Su, c.useTransition = st, c.version = p, typeof __REACT_DEVTOOLS_GLOBAL_HOOK__ < "u" && typeof __REACT_DEVTOOLS_GLOBAL_HOOK__.registerInternalModuleStop == "function" && __REACT_DEVTOOLS_GLOBAL_HOOK__.registerInternalModuleStop(new Error());
    }();
  }(Ov, Ov.exports)), Ov.exports;
}
vu.env.NODE_ENV === "production" ? dC.exports = RD() : dC.exports = wD();
var Fv = dC.exports;
const sT = /* @__PURE__ */ TD(Fv);
/**
 * @license React
 * react-jsx-runtime.production.min.js
 *
 * Copyright (c) Facebook, Inc. and its affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */
var cT;
function bD() {
  if (cT) return bv;
  cT = 1;
  var h = Fv, c = Symbol.for("react.element"), p = Symbol.for("react.fragment"), S = Object.prototype.hasOwnProperty, _ = h.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED.ReactCurrentOwner, T = { key: !0, ref: !0, __self: !0, __source: !0 };
  function E(A, I, $) {
    var fe, re = {}, be = null, de = null;
    $ !== void 0 && (be = "" + $), I.key !== void 0 && (be = "" + I.key), I.ref !== void 0 && (de = I.ref);
    for (fe in I) S.call(I, fe) && !T.hasOwnProperty(fe) && (re[fe] = I[fe]);
    if (A && A.defaultProps) for (fe in I = A.defaultProps, I) re[fe] === void 0 && (re[fe] = I[fe]);
    return { $$typeof: c, type: A, key: be, ref: de, props: re, _owner: _.current };
  }
  return bv.Fragment = p, bv.jsx = E, bv.jsxs = E, bv;
}
var kv = {};
/**
 * @license React
 * react-jsx-runtime.development.js
 *
 * Copyright (c) Facebook, Inc. and its affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */
var fT;
function kD() {
  return fT || (fT = 1, vu.env.NODE_ENV !== "production" && function() {
    var h = Fv, c = Symbol.for("react.element"), p = Symbol.for("react.portal"), S = Symbol.for("react.fragment"), _ = Symbol.for("react.strict_mode"), T = Symbol.for("react.profiler"), E = Symbol.for("react.provider"), A = Symbol.for("react.context"), I = Symbol.for("react.forward_ref"), $ = Symbol.for("react.suspense"), fe = Symbol.for("react.suspense_list"), re = Symbol.for("react.memo"), be = Symbol.for("react.lazy"), de = Symbol.for("react.offscreen"), nt = Symbol.iterator, bt = "@@iterator";
    function xt(k) {
      if (k === null || typeof k != "object")
        return null;
      var q = nt && k[nt] || k[bt];
      return typeof q == "function" ? q : null;
    }
    var En = h.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED;
    function _t(k) {
      {
        for (var q = arguments.length, pe = new Array(q > 1 ? q - 1 : 0), _e = 1; _e < q; _e++)
          pe[_e - 1] = arguments[_e];
        rt("error", k, pe);
      }
    }
    function rt(k, q, pe) {
      {
        var _e = En.ReactDebugCurrentFrame, it = _e.getStackAddendum();
        it !== "" && (q += "%s", pe = pe.concat([it]));
        var Ke = pe.map(function(Et) {
          return String(Et);
        });
        Ke.unshift("Warning: " + q), Function.prototype.apply.call(console[k], console, Ke);
      }
    }
    var Tt = !1, ze = !1, St = !1, Qe = !1, vn = !1, Zt;
    Zt = Symbol.for("react.module.reference");
    function on(k) {
      return !!(typeof k == "string" || typeof k == "function" || k === S || k === T || vn || k === _ || k === $ || k === fe || Qe || k === de || Tt || ze || St || typeof k == "object" && k !== null && (k.$$typeof === be || k.$$typeof === re || k.$$typeof === E || k.$$typeof === A || k.$$typeof === I || // This needs to include all possible module reference object
      // types supported by any Flight configuration anywhere since
      // we don't know which Flight build this will end up being used
      // with.
      k.$$typeof === Zt || k.getModuleId !== void 0));
    }
    function hn(k, q, pe) {
      var _e = k.displayName;
      if (_e)
        return _e;
      var it = q.displayName || q.name || "";
      return it !== "" ? pe + "(" + it + ")" : pe;
    }
    function zt(k) {
      return k.displayName || "Context";
    }
    function He(k) {
      if (k == null)
        return null;
      if (typeof k.tag == "number" && _t("Received an unexpected object in getComponentNameFromType(). This is likely a bug in React. Please file an issue."), typeof k == "function")
        return k.displayName || k.name || null;
      if (typeof k == "string")
        return k;
      switch (k) {
        case S:
          return "Fragment";
        case p:
          return "Portal";
        case T:
          return "Profiler";
        case _:
          return "StrictMode";
        case $:
          return "Suspense";
        case fe:
          return "SuspenseList";
      }
      if (typeof k == "object")
        switch (k.$$typeof) {
          case A:
            var q = k;
            return zt(q) + ".Consumer";
          case E:
            var pe = k;
            return zt(pe._context) + ".Provider";
          case I:
            return hn(k, k.render, "ForwardRef");
          case re:
            var _e = k.displayName || null;
            return _e !== null ? _e : He(k.type) || "Memo";
          case be: {
            var it = k, Ke = it._payload, Et = it._init;
            try {
              return He(Et(Ke));
            } catch {
              return null;
            }
          }
        }
      return null;
    }
    var Yt = Object.assign, Ut = 0, Ft, De, le, Oe, se, L, Z;
    function Ze() {
    }
    Ze.__reactDisabledLog = !0;
    function Ye() {
      {
        if (Ut === 0) {
          Ft = console.log, De = console.info, le = console.warn, Oe = console.error, se = console.group, L = console.groupCollapsed, Z = console.groupEnd;
          var k = {
            configurable: !0,
            enumerable: !0,
            value: Ze,
            writable: !0
          };
          Object.defineProperties(console, {
            info: k,
            log: k,
            warn: k,
            error: k,
            group: k,
            groupCollapsed: k,
            groupEnd: k
          });
        }
        Ut++;
      }
    }
    function vt() {
      {
        if (Ut--, Ut === 0) {
          var k = {
            configurable: !0,
            enumerable: !0,
            writable: !0
          };
          Object.defineProperties(console, {
            log: Yt({}, k, {
              value: Ft
            }),
            info: Yt({}, k, {
              value: De
            }),
            warn: Yt({}, k, {
              value: le
            }),
            error: Yt({}, k, {
              value: Oe
            }),
            group: Yt({}, k, {
              value: se
            }),
            groupCollapsed: Yt({}, k, {
              value: L
            }),
            groupEnd: Yt({}, k, {
              value: Z
            })
          });
        }
        Ut < 0 && _t("disabledDepth fell below zero. This is a bug in React. Please file an issue.");
      }
    }
    var ct = En.ReactCurrentDispatcher, ot;
    function ft(k, q, pe) {
      {
        if (ot === void 0)
          try {
            throw Error();
          } catch (it) {
            var _e = it.stack.trim().match(/\n( *(at )?)/);
            ot = _e && _e[1] || "";
          }
        return `
` + ot + k;
      }
    }
    var ht = !1, Xt;
    {
      var Hn = typeof WeakMap == "function" ? WeakMap : Map;
      Xt = new Hn();
    }
    function Ur(k, q) {
      if (!k || ht)
        return "";
      {
        var pe = Xt.get(k);
        if (pe !== void 0)
          return pe;
      }
      var _e;
      ht = !0;
      var it = Error.prepareStackTrace;
      Error.prepareStackTrace = void 0;
      var Ke;
      Ke = ct.current, ct.current = null, Ye();
      try {
        if (q) {
          var Et = function() {
            throw Error();
          };
          if (Object.defineProperty(Et.prototype, "props", {
            set: function() {
              throw Error();
            }
          }), typeof Reflect == "object" && Reflect.construct) {
            try {
              Reflect.construct(Et, []);
            } catch (lt) {
              _e = lt;
            }
            Reflect.construct(k, [], Et);
          } else {
            try {
              Et.call();
            } catch (lt) {
              _e = lt;
            }
            k.call(Et.prototype);
          }
        } else {
          try {
            throw Error();
          } catch (lt) {
            _e = lt;
          }
          k();
        }
      } catch (lt) {
        if (lt && _e && typeof lt.stack == "string") {
          for (var mt = lt.stack.split(`
`), Mn = _e.stack.split(`
`), fn = mt.length - 1, yn = Mn.length - 1; fn >= 1 && yn >= 0 && mt[fn] !== Mn[yn]; )
            yn--;
          for (; fn >= 1 && yn >= 0; fn--, yn--)
            if (mt[fn] !== Mn[yn]) {
              if (fn !== 1 || yn !== 1)
                do
                  if (fn--, yn--, yn < 0 || mt[fn] !== Mn[yn]) {
                    var hr = `
` + mt[fn].replace(" at new ", " at ");
                    return k.displayName && hr.includes("<anonymous>") && (hr = hr.replace("<anonymous>", k.displayName)), typeof k == "function" && Xt.set(k, hr), hr;
                  }
                while (fn >= 1 && yn >= 0);
              break;
            }
        }
      } finally {
        ht = !1, ct.current = Ke, vt(), Error.prepareStackTrace = it;
      }
      var li = k ? k.displayName || k.name : "", ui = li ? ft(li) : "";
      return typeof k == "function" && Xt.set(k, ui), ui;
    }
    function On(k, q, pe) {
      return Ur(k, !1);
    }
    function pr(k) {
      var q = k.prototype;
      return !!(q && q.isReactComponent);
    }
    function qn(k, q, pe) {
      if (k == null)
        return "";
      if (typeof k == "function")
        return Ur(k, pr(k));
      if (typeof k == "string")
        return ft(k);
      switch (k) {
        case $:
          return ft("Suspense");
        case fe:
          return ft("SuspenseList");
      }
      if (typeof k == "object")
        switch (k.$$typeof) {
          case I:
            return On(k.render);
          case re:
            return qn(k.type, q, pe);
          case be: {
            var _e = k, it = _e._payload, Ke = _e._init;
            try {
              return qn(Ke(it), q, pe);
            } catch {
            }
          }
        }
      return "";
    }
    var Xn = Object.prototype.hasOwnProperty, ta = {}, _i = En.ReactDebugCurrentFrame;
    function Sa(k) {
      if (k) {
        var q = k._owner, pe = qn(k.type, k._source, q ? q.type : null);
        _i.setExtraStackFrame(pe);
      } else
        _i.setExtraStackFrame(null);
    }
    function ir(k, q, pe, _e, it) {
      {
        var Ke = Function.call.bind(Xn);
        for (var Et in k)
          if (Ke(k, Et)) {
            var mt = void 0;
            try {
              if (typeof k[Et] != "function") {
                var Mn = Error((_e || "React class") + ": " + pe + " type `" + Et + "` is invalid; it must be a function, usually from the `prop-types` package, but received `" + typeof k[Et] + "`.This often happens because of typos such as `PropTypes.function` instead of `PropTypes.func`.");
                throw Mn.name = "Invariant Violation", Mn;
              }
              mt = k[Et](q, Et, _e, pe, null, "SECRET_DO_NOT_PASS_THIS_OR_YOU_WILL_BE_FIRED");
            } catch (fn) {
              mt = fn;
            }
            mt && !(mt instanceof Error) && (Sa(it), _t("%s: type specification of %s `%s` is invalid; the type checker function must return `null` or an `Error` but returned a %s. You may have forgotten to pass an argument to the type checker creator (arrayOf, instanceOf, objectOf, oneOf, oneOfType, and shape all require an argument).", _e || "React class", pe, Et, typeof mt), Sa(null)), mt instanceof Error && !(mt.message in ta) && (ta[mt.message] = !0, Sa(it), _t("Failed %s type: %s", pe, mt.message), Sa(null));
          }
      }
    }
    var Nn = Array.isArray;
    function Kn(k) {
      return Nn(k);
    }
    function Dr(k) {
      {
        var q = typeof Symbol == "function" && Symbol.toStringTag, pe = q && k[Symbol.toStringTag] || k.constructor.name || "Object";
        return pe;
      }
    }
    function ri(k) {
      try {
        return Vn(k), !1;
      } catch {
        return !0;
      }
    }
    function Vn(k) {
      return "" + k;
    }
    function Or(k) {
      if (ri(k))
        return _t("The provided key is an unsupported type %s. This value must be coerced to a string before before using it here.", Dr(k)), Vn(k);
    }
    var Ea = En.ReactCurrentOwner, ai = {
      key: !0,
      ref: !0,
      __self: !0,
      __source: !0
    }, xi, ue;
    function Ne(k) {
      if (Xn.call(k, "ref")) {
        var q = Object.getOwnPropertyDescriptor(k, "ref").get;
        if (q && q.isReactWarning)
          return !1;
      }
      return k.ref !== void 0;
    }
    function dt(k) {
      if (Xn.call(k, "key")) {
        var q = Object.getOwnPropertyDescriptor(k, "key").get;
        if (q && q.isReactWarning)
          return !1;
      }
      return k.key !== void 0;
    }
    function Wt(k, q) {
      typeof k.ref == "string" && Ea.current;
    }
    function sn(k, q) {
      {
        var pe = function() {
          xi || (xi = !0, _t("%s: `key` is not a prop. Trying to access it will result in `undefined` being returned. If you need to access the same value within the child component, you should pass it as a different prop. (https://reactjs.org/link/special-props)", q));
        };
        pe.isReactWarning = !0, Object.defineProperty(k, "key", {
          get: pe,
          configurable: !0
        });
      }
    }
    function xn(k, q) {
      {
        var pe = function() {
          ue || (ue = !0, _t("%s: `ref` is not a prop. Trying to access it will result in `undefined` being returned. If you need to access the same value within the child component, you should pass it as a different prop. (https://reactjs.org/link/special-props)", q));
        };
        pe.isReactWarning = !0, Object.defineProperty(k, "ref", {
          get: pe,
          configurable: !0
        });
      }
    }
    var mn = function(k, q, pe, _e, it, Ke, Et) {
      var mt = {
        // This tag allows us to uniquely identify this as a React Element
        $$typeof: c,
        // Built-in properties that belong on the element
        type: k,
        key: q,
        ref: pe,
        props: Et,
        // Record the component responsible for creating this element.
        _owner: Ke
      };
      return mt._store = {}, Object.defineProperty(mt._store, "validated", {
        configurable: !1,
        enumerable: !1,
        writable: !0,
        value: !1
      }), Object.defineProperty(mt, "_self", {
        configurable: !1,
        enumerable: !1,
        writable: !1,
        value: _e
      }), Object.defineProperty(mt, "_source", {
        configurable: !1,
        enumerable: !1,
        writable: !1,
        value: it
      }), Object.freeze && (Object.freeze(mt.props), Object.freeze(mt)), mt;
    };
    function lr(k, q, pe, _e, it) {
      {
        var Ke, Et = {}, mt = null, Mn = null;
        pe !== void 0 && (Or(pe), mt = "" + pe), dt(q) && (Or(q.key), mt = "" + q.key), Ne(q) && (Mn = q.ref, Wt(q, it));
        for (Ke in q)
          Xn.call(q, Ke) && !ai.hasOwnProperty(Ke) && (Et[Ke] = q[Ke]);
        if (k && k.defaultProps) {
          var fn = k.defaultProps;
          for (Ke in fn)
            Et[Ke] === void 0 && (Et[Ke] = fn[Ke]);
        }
        if (mt || Mn) {
          var yn = typeof k == "function" ? k.displayName || k.name || "Unknown" : k;
          mt && sn(Et, yn), Mn && xn(Et, yn);
        }
        return mn(k, mt, Mn, it, _e, Ea.current, Et);
      }
    }
    var cn = En.ReactCurrentOwner, Kt = En.ReactDebugCurrentFrame;
    function Jt(k) {
      if (k) {
        var q = k._owner, pe = qn(k.type, k._source, q ? q.type : null);
        Kt.setExtraStackFrame(pe);
      } else
        Kt.setExtraStackFrame(null);
    }
    var Ca;
    Ca = !1;
    function Nr(k) {
      return typeof k == "object" && k !== null && k.$$typeof === c;
    }
    function za() {
      {
        if (cn.current) {
          var k = He(cn.current.type);
          if (k)
            return `

Check the render method of \`` + k + "`.";
        }
        return "";
      }
    }
    function Ki(k) {
      return "";
    }
    var hu = {};
    function mu(k) {
      {
        var q = za();
        if (!q) {
          var pe = typeof k == "string" ? k : k.displayName || k.name;
          pe && (q = `

Check the top-level render call using <` + pe + ">.");
        }
        return q;
      }
    }
    function wl(k, q) {
      {
        if (!k._store || k._store.validated || k.key != null)
          return;
        k._store.validated = !0;
        var pe = mu(q);
        if (hu[pe])
          return;
        hu[pe] = !0;
        var _e = "";
        k && k._owner && k._owner !== cn.current && (_e = " It was passed a child from " + He(k._owner.type) + "."), Jt(k), _t('Each child in a list should have a unique "key" prop.%s%s See https://reactjs.org/link/warning-keys for more information.', pe, _e), Jt(null);
      }
    }
    function bl(k, q) {
      {
        if (typeof k != "object")
          return;
        if (Kn(k))
          for (var pe = 0; pe < k.length; pe++) {
            var _e = k[pe];
            Nr(_e) && wl(_e, q);
          }
        else if (Nr(k))
          k._store && (k._store.validated = !0);
        else if (k) {
          var it = xt(k);
          if (typeof it == "function" && it !== k.entries)
            for (var Ke = it.call(k), Et; !(Et = Ke.next()).done; )
              Nr(Et.value) && wl(Et.value, q);
        }
      }
    }
    function yu(k) {
      {
        var q = k.type;
        if (q == null || typeof q == "string")
          return;
        var pe;
        if (typeof q == "function")
          pe = q.propTypes;
        else if (typeof q == "object" && (q.$$typeof === I || // Note: Memo only checks outer props here.
        // Inner props are checked in the reconciler.
        q.$$typeof === re))
          pe = q.propTypes;
        else
          return;
        if (pe) {
          var _e = He(q);
          ir(pe, k.props, "prop", _e, k);
        } else if (q.PropTypes !== void 0 && !Ca) {
          Ca = !0;
          var it = He(q);
          _t("Component %s declared `PropTypes` instead of `propTypes`. Did you misspell the property assignment?", it || "Unknown");
        }
        typeof q.getDefaultProps == "function" && !q.getDefaultProps.isReactClassApproved && _t("getDefaultProps is only used on classic React.createClass definitions. Use a static property named `defaultProps` instead.");
      }
    }
    function jr(k) {
      {
        for (var q = Object.keys(k.props), pe = 0; pe < q.length; pe++) {
          var _e = q[pe];
          if (_e !== "children" && _e !== "key") {
            Jt(k), _t("Invalid prop `%s` supplied to `React.Fragment`. React.Fragment can only have `key` and `children` props.", _e), Jt(null);
            break;
          }
        }
        k.ref !== null && (Jt(k), _t("Invalid attribute `ref` supplied to `React.Fragment`."), Jt(null));
      }
    }
    var Fr = {};
    function vr(k, q, pe, _e, it, Ke) {
      {
        var Et = on(k);
        if (!Et) {
          var mt = "";
          (k === void 0 || typeof k == "object" && k !== null && Object.keys(k).length === 0) && (mt += " You likely forgot to export your component from the file it's defined in, or you might have mixed up default and named imports.");
          var Mn = Ki();
          Mn ? mt += Mn : mt += za();
          var fn;
          k === null ? fn = "null" : Kn(k) ? fn = "array" : k !== void 0 && k.$$typeof === c ? (fn = "<" + (He(k.type) || "Unknown") + " />", mt = " Did you accidentally export a JSX literal instead of a component?") : fn = typeof k, _t("React.jsx: type is invalid -- expected a string (for built-in components) or a class/function (for composite components) but got: %s.%s", fn, mt);
        }
        var yn = lr(k, q, pe, it, Ke);
        if (yn == null)
          return yn;
        if (Et) {
          var hr = q.children;
          if (hr !== void 0)
            if (_e)
              if (Kn(hr)) {
                for (var li = 0; li < hr.length; li++)
                  bl(hr[li], k);
                Object.freeze && Object.freeze(hr);
              } else
                _t("React.jsx: Static children should always be an array. You are likely explicitly calling React.jsxs or React.jsxDEV. Use the Babel transform instead.");
            else
              bl(hr, k);
        }
        if (Xn.call(q, "key")) {
          var ui = He(k), lt = Object.keys(q).filter(function(gu) {
            return gu !== "key";
          }), st = lt.length > 0 ? "{key: someKey, " + lt.join(": ..., ") + ": ...}" : "{key: someKey}";
          if (!Fr[ui + st]) {
            var oi = lt.length > 0 ? "{" + lt.join(": ..., ") + ": ...}" : "{}";
            _t(`A props object containing a "key" prop is being spread into JSX:
  let props = %s;
  <%s {...props} />
React keys must be passed directly to JSX without using spread:
  let props = %s;
  <%s key={someKey} {...props} />`, st, ui, oi, ui), Fr[ui + st] = !0;
          }
        }
        return k === S ? jr(yn) : yu(yn), yn;
      }
    }
    function Ti(k, q, pe) {
      return vr(k, q, pe, !0);
    }
    function ii(k, q, pe) {
      return vr(k, q, pe, !1);
    }
    var Ri = ii, wi = Ti;
    kv.Fragment = S, kv.jsx = Ri, kv.jsxs = wi;
  }()), kv;
}
vu.env.NODE_ENV === "production" ? fC.exports = bD() : fC.exports = kD();
var Fe = fC.exports, Nv = {}, pC = { exports: {} }, ei = {}, Ny = { exports: {} }, oC = {};
/**
 * @license React
 * scheduler.production.min.js
 *
 * Copyright (c) Facebook, Inc. and its affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */
var dT;
function DD() {
  return dT || (dT = 1, function(h) {
    function c(le, Oe) {
      var se = le.length;
      le.push(Oe);
      e: for (; 0 < se; ) {
        var L = se - 1 >>> 1, Z = le[L];
        if (0 < _(Z, Oe)) le[L] = Oe, le[se] = Z, se = L;
        else break e;
      }
    }
    function p(le) {
      return le.length === 0 ? null : le[0];
    }
    function S(le) {
      if (le.length === 0) return null;
      var Oe = le[0], se = le.pop();
      if (se !== Oe) {
        le[0] = se;
        e: for (var L = 0, Z = le.length, Ze = Z >>> 1; L < Ze; ) {
          var Ye = 2 * (L + 1) - 1, vt = le[Ye], ct = Ye + 1, ot = le[ct];
          if (0 > _(vt, se)) ct < Z && 0 > _(ot, vt) ? (le[L] = ot, le[ct] = se, L = ct) : (le[L] = vt, le[Ye] = se, L = Ye);
          else if (ct < Z && 0 > _(ot, se)) le[L] = ot, le[ct] = se, L = ct;
          else break e;
        }
      }
      return Oe;
    }
    function _(le, Oe) {
      var se = le.sortIndex - Oe.sortIndex;
      return se !== 0 ? se : le.id - Oe.id;
    }
    if (typeof performance == "object" && typeof performance.now == "function") {
      var T = performance;
      h.unstable_now = function() {
        return T.now();
      };
    } else {
      var E = Date, A = E.now();
      h.unstable_now = function() {
        return E.now() - A;
      };
    }
    var I = [], $ = [], fe = 1, re = null, be = 3, de = !1, nt = !1, bt = !1, xt = typeof setTimeout == "function" ? setTimeout : null, En = typeof clearTimeout == "function" ? clearTimeout : null, _t = typeof setImmediate < "u" ? setImmediate : null;
    typeof navigator < "u" && navigator.scheduling !== void 0 && navigator.scheduling.isInputPending !== void 0 && navigator.scheduling.isInputPending.bind(navigator.scheduling);
    function rt(le) {
      for (var Oe = p($); Oe !== null; ) {
        if (Oe.callback === null) S($);
        else if (Oe.startTime <= le) S($), Oe.sortIndex = Oe.expirationTime, c(I, Oe);
        else break;
        Oe = p($);
      }
    }
    function Tt(le) {
      if (bt = !1, rt(le), !nt) if (p(I) !== null) nt = !0, Ft(ze);
      else {
        var Oe = p($);
        Oe !== null && De(Tt, Oe.startTime - le);
      }
    }
    function ze(le, Oe) {
      nt = !1, bt && (bt = !1, En(vn), vn = -1), de = !0;
      var se = be;
      try {
        for (rt(Oe), re = p(I); re !== null && (!(re.expirationTime > Oe) || le && !hn()); ) {
          var L = re.callback;
          if (typeof L == "function") {
            re.callback = null, be = re.priorityLevel;
            var Z = L(re.expirationTime <= Oe);
            Oe = h.unstable_now(), typeof Z == "function" ? re.callback = Z : re === p(I) && S(I), rt(Oe);
          } else S(I);
          re = p(I);
        }
        if (re !== null) var Ze = !0;
        else {
          var Ye = p($);
          Ye !== null && De(Tt, Ye.startTime - Oe), Ze = !1;
        }
        return Ze;
      } finally {
        re = null, be = se, de = !1;
      }
    }
    var St = !1, Qe = null, vn = -1, Zt = 5, on = -1;
    function hn() {
      return !(h.unstable_now() - on < Zt);
    }
    function zt() {
      if (Qe !== null) {
        var le = h.unstable_now();
        on = le;
        var Oe = !0;
        try {
          Oe = Qe(!0, le);
        } finally {
          Oe ? He() : (St = !1, Qe = null);
        }
      } else St = !1;
    }
    var He;
    if (typeof _t == "function") He = function() {
      _t(zt);
    };
    else if (typeof MessageChannel < "u") {
      var Yt = new MessageChannel(), Ut = Yt.port2;
      Yt.port1.onmessage = zt, He = function() {
        Ut.postMessage(null);
      };
    } else He = function() {
      xt(zt, 0);
    };
    function Ft(le) {
      Qe = le, St || (St = !0, He());
    }
    function De(le, Oe) {
      vn = xt(function() {
        le(h.unstable_now());
      }, Oe);
    }
    h.unstable_IdlePriority = 5, h.unstable_ImmediatePriority = 1, h.unstable_LowPriority = 4, h.unstable_NormalPriority = 3, h.unstable_Profiling = null, h.unstable_UserBlockingPriority = 2, h.unstable_cancelCallback = function(le) {
      le.callback = null;
    }, h.unstable_continueExecution = function() {
      nt || de || (nt = !0, Ft(ze));
    }, h.unstable_forceFrameRate = function(le) {
      0 > le || 125 < le ? console.error("forceFrameRate takes a positive int between 0 and 125, forcing frame rates higher than 125 fps is not supported") : Zt = 0 < le ? Math.floor(1e3 / le) : 5;
    }, h.unstable_getCurrentPriorityLevel = function() {
      return be;
    }, h.unstable_getFirstCallbackNode = function() {
      return p(I);
    }, h.unstable_next = function(le) {
      switch (be) {
        case 1:
        case 2:
        case 3:
          var Oe = 3;
          break;
        default:
          Oe = be;
      }
      var se = be;
      be = Oe;
      try {
        return le();
      } finally {
        be = se;
      }
    }, h.unstable_pauseExecution = function() {
    }, h.unstable_requestPaint = function() {
    }, h.unstable_runWithPriority = function(le, Oe) {
      switch (le) {
        case 1:
        case 2:
        case 3:
        case 4:
        case 5:
          break;
        default:
          le = 3;
      }
      var se = be;
      be = le;
      try {
        return Oe();
      } finally {
        be = se;
      }
    }, h.unstable_scheduleCallback = function(le, Oe, se) {
      var L = h.unstable_now();
      switch (typeof se == "object" && se !== null ? (se = se.delay, se = typeof se == "number" && 0 < se ? L + se : L) : se = L, le) {
        case 1:
          var Z = -1;
          break;
        case 2:
          Z = 250;
          break;
        case 5:
          Z = 1073741823;
          break;
        case 4:
          Z = 1e4;
          break;
        default:
          Z = 5e3;
      }
      return Z = se + Z, le = { id: fe++, callback: Oe, priorityLevel: le, startTime: se, expirationTime: Z, sortIndex: -1 }, se > L ? (le.sortIndex = se, c($, le), p(I) === null && le === p($) && (bt ? (En(vn), vn = -1) : bt = !0, De(Tt, se - L))) : (le.sortIndex = Z, c(I, le), nt || de || (nt = !0, Ft(ze))), le;
    }, h.unstable_shouldYield = hn, h.unstable_wrapCallback = function(le) {
      var Oe = be;
      return function() {
        var se = be;
        be = Oe;
        try {
          return le.apply(this, arguments);
        } finally {
          be = se;
        }
      };
    };
  }(oC)), oC;
}
var sC = {};
/**
 * @license React
 * scheduler.development.js
 *
 * Copyright (c) Facebook, Inc. and its affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */
var pT;
function OD() {
  return pT || (pT = 1, function(h) {
    vu.env.NODE_ENV !== "production" && function() {
      typeof __REACT_DEVTOOLS_GLOBAL_HOOK__ < "u" && typeof __REACT_DEVTOOLS_GLOBAL_HOOK__.registerInternalModuleStart == "function" && __REACT_DEVTOOLS_GLOBAL_HOOK__.registerInternalModuleStart(new Error());
      var c = !1, p = 5;
      function S(ue, Ne) {
        var dt = ue.length;
        ue.push(Ne), E(ue, Ne, dt);
      }
      function _(ue) {
        return ue.length === 0 ? null : ue[0];
      }
      function T(ue) {
        if (ue.length === 0)
          return null;
        var Ne = ue[0], dt = ue.pop();
        return dt !== Ne && (ue[0] = dt, A(ue, dt, 0)), Ne;
      }
      function E(ue, Ne, dt) {
        for (var Wt = dt; Wt > 0; ) {
          var sn = Wt - 1 >>> 1, xn = ue[sn];
          if (I(xn, Ne) > 0)
            ue[sn] = Ne, ue[Wt] = xn, Wt = sn;
          else
            return;
        }
      }
      function A(ue, Ne, dt) {
        for (var Wt = dt, sn = ue.length, xn = sn >>> 1; Wt < xn; ) {
          var mn = (Wt + 1) * 2 - 1, lr = ue[mn], cn = mn + 1, Kt = ue[cn];
          if (I(lr, Ne) < 0)
            cn < sn && I(Kt, lr) < 0 ? (ue[Wt] = Kt, ue[cn] = Ne, Wt = cn) : (ue[Wt] = lr, ue[mn] = Ne, Wt = mn);
          else if (cn < sn && I(Kt, Ne) < 0)
            ue[Wt] = Kt, ue[cn] = Ne, Wt = cn;
          else
            return;
        }
      }
      function I(ue, Ne) {
        var dt = ue.sortIndex - Ne.sortIndex;
        return dt !== 0 ? dt : ue.id - Ne.id;
      }
      var $ = 1, fe = 2, re = 3, be = 4, de = 5;
      function nt(ue, Ne) {
      }
      var bt = typeof performance == "object" && typeof performance.now == "function";
      if (bt) {
        var xt = performance;
        h.unstable_now = function() {
          return xt.now();
        };
      } else {
        var En = Date, _t = En.now();
        h.unstable_now = function() {
          return En.now() - _t;
        };
      }
      var rt = 1073741823, Tt = -1, ze = 250, St = 5e3, Qe = 1e4, vn = rt, Zt = [], on = [], hn = 1, zt = null, He = re, Yt = !1, Ut = !1, Ft = !1, De = typeof setTimeout == "function" ? setTimeout : null, le = typeof clearTimeout == "function" ? clearTimeout : null, Oe = typeof setImmediate < "u" ? setImmediate : null;
      typeof navigator < "u" && navigator.scheduling !== void 0 && navigator.scheduling.isInputPending !== void 0 && navigator.scheduling.isInputPending.bind(navigator.scheduling);
      function se(ue) {
        for (var Ne = _(on); Ne !== null; ) {
          if (Ne.callback === null)
            T(on);
          else if (Ne.startTime <= ue)
            T(on), Ne.sortIndex = Ne.expirationTime, S(Zt, Ne);
          else
            return;
          Ne = _(on);
        }
      }
      function L(ue) {
        if (Ft = !1, se(ue), !Ut)
          if (_(Zt) !== null)
            Ut = !0, Vn(Z);
          else {
            var Ne = _(on);
            Ne !== null && Or(L, Ne.startTime - ue);
          }
      }
      function Z(ue, Ne) {
        Ut = !1, Ft && (Ft = !1, Ea()), Yt = !0;
        var dt = He;
        try {
          var Wt;
          if (!c) return Ze(ue, Ne);
        } finally {
          zt = null, He = dt, Yt = !1;
        }
      }
      function Ze(ue, Ne) {
        var dt = Ne;
        for (se(dt), zt = _(Zt); zt !== null && !(zt.expirationTime > dt && (!ue || _i())); ) {
          var Wt = zt.callback;
          if (typeof Wt == "function") {
            zt.callback = null, He = zt.priorityLevel;
            var sn = zt.expirationTime <= dt, xn = Wt(sn);
            dt = h.unstable_now(), typeof xn == "function" ? zt.callback = xn : zt === _(Zt) && T(Zt), se(dt);
          } else
            T(Zt);
          zt = _(Zt);
        }
        if (zt !== null)
          return !0;
        var mn = _(on);
        return mn !== null && Or(L, mn.startTime - dt), !1;
      }
      function Ye(ue, Ne) {
        switch (ue) {
          case $:
          case fe:
          case re:
          case be:
          case de:
            break;
          default:
            ue = re;
        }
        var dt = He;
        He = ue;
        try {
          return Ne();
        } finally {
          He = dt;
        }
      }
      function vt(ue) {
        var Ne;
        switch (He) {
          case $:
          case fe:
          case re:
            Ne = re;
            break;
          default:
            Ne = He;
            break;
        }
        var dt = He;
        He = Ne;
        try {
          return ue();
        } finally {
          He = dt;
        }
      }
      function ct(ue) {
        var Ne = He;
        return function() {
          var dt = He;
          He = Ne;
          try {
            return ue.apply(this, arguments);
          } finally {
            He = dt;
          }
        };
      }
      function ot(ue, Ne, dt) {
        var Wt = h.unstable_now(), sn;
        if (typeof dt == "object" && dt !== null) {
          var xn = dt.delay;
          typeof xn == "number" && xn > 0 ? sn = Wt + xn : sn = Wt;
        } else
          sn = Wt;
        var mn;
        switch (ue) {
          case $:
            mn = Tt;
            break;
          case fe:
            mn = ze;
            break;
          case de:
            mn = vn;
            break;
          case be:
            mn = Qe;
            break;
          case re:
          default:
            mn = St;
            break;
        }
        var lr = sn + mn, cn = {
          id: hn++,
          callback: Ne,
          priorityLevel: ue,
          startTime: sn,
          expirationTime: lr,
          sortIndex: -1
        };
        return sn > Wt ? (cn.sortIndex = sn, S(on, cn), _(Zt) === null && cn === _(on) && (Ft ? Ea() : Ft = !0, Or(L, sn - Wt))) : (cn.sortIndex = lr, S(Zt, cn), !Ut && !Yt && (Ut = !0, Vn(Z))), cn;
      }
      function ft() {
      }
      function ht() {
        !Ut && !Yt && (Ut = !0, Vn(Z));
      }
      function Xt() {
        return _(Zt);
      }
      function Hn(ue) {
        ue.callback = null;
      }
      function Ur() {
        return He;
      }
      var On = !1, pr = null, qn = -1, Xn = p, ta = -1;
      function _i() {
        var ue = h.unstable_now() - ta;
        return !(ue < Xn);
      }
      function Sa() {
      }
      function ir(ue) {
        if (ue < 0 || ue > 125) {
          console.error("forceFrameRate takes a positive int between 0 and 125, forcing frame rates higher than 125 fps is not supported");
          return;
        }
        ue > 0 ? Xn = Math.floor(1e3 / ue) : Xn = p;
      }
      var Nn = function() {
        if (pr !== null) {
          var ue = h.unstable_now();
          ta = ue;
          var Ne = !0, dt = !0;
          try {
            dt = pr(Ne, ue);
          } finally {
            dt ? Kn() : (On = !1, pr = null);
          }
        } else
          On = !1;
      }, Kn;
      if (typeof Oe == "function")
        Kn = function() {
          Oe(Nn);
        };
      else if (typeof MessageChannel < "u") {
        var Dr = new MessageChannel(), ri = Dr.port2;
        Dr.port1.onmessage = Nn, Kn = function() {
          ri.postMessage(null);
        };
      } else
        Kn = function() {
          De(Nn, 0);
        };
      function Vn(ue) {
        pr = ue, On || (On = !0, Kn());
      }
      function Or(ue, Ne) {
        qn = De(function() {
          ue(h.unstable_now());
        }, Ne);
      }
      function Ea() {
        le(qn), qn = -1;
      }
      var ai = Sa, xi = null;
      h.unstable_IdlePriority = de, h.unstable_ImmediatePriority = $, h.unstable_LowPriority = be, h.unstable_NormalPriority = re, h.unstable_Profiling = xi, h.unstable_UserBlockingPriority = fe, h.unstable_cancelCallback = Hn, h.unstable_continueExecution = ht, h.unstable_forceFrameRate = ir, h.unstable_getCurrentPriorityLevel = Ur, h.unstable_getFirstCallbackNode = Xt, h.unstable_next = vt, h.unstable_pauseExecution = ft, h.unstable_requestPaint = ai, h.unstable_runWithPriority = Ye, h.unstable_scheduleCallback = ot, h.unstable_shouldYield = _i, h.unstable_wrapCallback = ct, typeof __REACT_DEVTOOLS_GLOBAL_HOOK__ < "u" && typeof __REACT_DEVTOOLS_GLOBAL_HOOK__.registerInternalModuleStop == "function" && __REACT_DEVTOOLS_GLOBAL_HOOK__.registerInternalModuleStop(new Error());
    }();
  }(sC)), sC;
}
var vT;
function DT() {
  return vT || (vT = 1, vu.env.NODE_ENV === "production" ? Ny.exports = DD() : Ny.exports = OD()), Ny.exports;
}
/**
 * @license React
 * react-dom.production.min.js
 *
 * Copyright (c) Facebook, Inc. and its affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */
var hT;
function ND() {
  if (hT) return ei;
  hT = 1;
  var h = Fv, c = DT();
  function p(n) {
    for (var r = "https://reactjs.org/docs/error-decoder.html?invariant=" + n, l = 1; l < arguments.length; l++) r += "&args[]=" + encodeURIComponent(arguments[l]);
    return "Minified React error #" + n + "; visit " + r + " for the full message or use the non-minified dev environment for full errors and additional helpful warnings.";
  }
  var S = /* @__PURE__ */ new Set(), _ = {};
  function T(n, r) {
    E(n, r), E(n + "Capture", r);
  }
  function E(n, r) {
    for (_[n] = r, n = 0; n < r.length; n++) S.add(r[n]);
  }
  var A = !(typeof window > "u" || typeof window.document > "u" || typeof window.document.createElement > "u"), I = Object.prototype.hasOwnProperty, $ = /^[:A-Z_a-z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u02FF\u0370-\u037D\u037F-\u1FFF\u200C-\u200D\u2070-\u218F\u2C00-\u2FEF\u3001-\uD7FF\uF900-\uFDCF\uFDF0-\uFFFD][:A-Z_a-z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u02FF\u0370-\u037D\u037F-\u1FFF\u200C-\u200D\u2070-\u218F\u2C00-\u2FEF\u3001-\uD7FF\uF900-\uFDCF\uFDF0-\uFFFD\-.0-9\u00B7\u0300-\u036F\u203F-\u2040]*$/, fe = {}, re = {};
  function be(n) {
    return I.call(re, n) ? !0 : I.call(fe, n) ? !1 : $.test(n) ? re[n] = !0 : (fe[n] = !0, !1);
  }
  function de(n, r, l, o) {
    if (l !== null && l.type === 0) return !1;
    switch (typeof r) {
      case "function":
      case "symbol":
        return !0;
      case "boolean":
        return o ? !1 : l !== null ? !l.acceptsBooleans : (n = n.toLowerCase().slice(0, 5), n !== "data-" && n !== "aria-");
      default:
        return !1;
    }
  }
  function nt(n, r, l, o) {
    if (r === null || typeof r > "u" || de(n, r, l, o)) return !0;
    if (o) return !1;
    if (l !== null) switch (l.type) {
      case 3:
        return !r;
      case 4:
        return r === !1;
      case 5:
        return isNaN(r);
      case 6:
        return isNaN(r) || 1 > r;
    }
    return !1;
  }
  function bt(n, r, l, o, f, v, C) {
    this.acceptsBooleans = r === 2 || r === 3 || r === 4, this.attributeName = o, this.attributeNamespace = f, this.mustUseProperty = l, this.propertyName = n, this.type = r, this.sanitizeURL = v, this.removeEmptyString = C;
  }
  var xt = {};
  "children dangerouslySetInnerHTML defaultValue defaultChecked innerHTML suppressContentEditableWarning suppressHydrationWarning style".split(" ").forEach(function(n) {
    xt[n] = new bt(n, 0, !1, n, null, !1, !1);
  }), [["acceptCharset", "accept-charset"], ["className", "class"], ["htmlFor", "for"], ["httpEquiv", "http-equiv"]].forEach(function(n) {
    var r = n[0];
    xt[r] = new bt(r, 1, !1, n[1], null, !1, !1);
  }), ["contentEditable", "draggable", "spellCheck", "value"].forEach(function(n) {
    xt[n] = new bt(n, 2, !1, n.toLowerCase(), null, !1, !1);
  }), ["autoReverse", "externalResourcesRequired", "focusable", "preserveAlpha"].forEach(function(n) {
    xt[n] = new bt(n, 2, !1, n, null, !1, !1);
  }), "allowFullScreen async autoFocus autoPlay controls default defer disabled disablePictureInPicture disableRemotePlayback formNoValidate hidden loop noModule noValidate open playsInline readOnly required reversed scoped seamless itemScope".split(" ").forEach(function(n) {
    xt[n] = new bt(n, 3, !1, n.toLowerCase(), null, !1, !1);
  }), ["checked", "multiple", "muted", "selected"].forEach(function(n) {
    xt[n] = new bt(n, 3, !0, n, null, !1, !1);
  }), ["capture", "download"].forEach(function(n) {
    xt[n] = new bt(n, 4, !1, n, null, !1, !1);
  }), ["cols", "rows", "size", "span"].forEach(function(n) {
    xt[n] = new bt(n, 6, !1, n, null, !1, !1);
  }), ["rowSpan", "start"].forEach(function(n) {
    xt[n] = new bt(n, 5, !1, n.toLowerCase(), null, !1, !1);
  });
  var En = /[\-:]([a-z])/g;
  function _t(n) {
    return n[1].toUpperCase();
  }
  "accent-height alignment-baseline arabic-form baseline-shift cap-height clip-path clip-rule color-interpolation color-interpolation-filters color-profile color-rendering dominant-baseline enable-background fill-opacity fill-rule flood-color flood-opacity font-family font-size font-size-adjust font-stretch font-style font-variant font-weight glyph-name glyph-orientation-horizontal glyph-orientation-vertical horiz-adv-x horiz-origin-x image-rendering letter-spacing lighting-color marker-end marker-mid marker-start overline-position overline-thickness paint-order panose-1 pointer-events rendering-intent shape-rendering stop-color stop-opacity strikethrough-position strikethrough-thickness stroke-dasharray stroke-dashoffset stroke-linecap stroke-linejoin stroke-miterlimit stroke-opacity stroke-width text-anchor text-decoration text-rendering underline-position underline-thickness unicode-bidi unicode-range units-per-em v-alphabetic v-hanging v-ideographic v-mathematical vector-effect vert-adv-y vert-origin-x vert-origin-y word-spacing writing-mode xmlns:xlink x-height".split(" ").forEach(function(n) {
    var r = n.replace(
      En,
      _t
    );
    xt[r] = new bt(r, 1, !1, n, null, !1, !1);
  }), "xlink:actuate xlink:arcrole xlink:role xlink:show xlink:title xlink:type".split(" ").forEach(function(n) {
    var r = n.replace(En, _t);
    xt[r] = new bt(r, 1, !1, n, "http://www.w3.org/1999/xlink", !1, !1);
  }), ["xml:base", "xml:lang", "xml:space"].forEach(function(n) {
    var r = n.replace(En, _t);
    xt[r] = new bt(r, 1, !1, n, "http://www.w3.org/XML/1998/namespace", !1, !1);
  }), ["tabIndex", "crossOrigin"].forEach(function(n) {
    xt[n] = new bt(n, 1, !1, n.toLowerCase(), null, !1, !1);
  }), xt.xlinkHref = new bt("xlinkHref", 1, !1, "xlink:href", "http://www.w3.org/1999/xlink", !0, !1), ["src", "href", "action", "formAction"].forEach(function(n) {
    xt[n] = new bt(n, 1, !1, n.toLowerCase(), null, !0, !0);
  });
  function rt(n, r, l, o) {
    var f = xt.hasOwnProperty(r) ? xt[r] : null;
    (f !== null ? f.type !== 0 : o || !(2 < r.length) || r[0] !== "o" && r[0] !== "O" || r[1] !== "n" && r[1] !== "N") && (nt(r, l, f, o) && (l = null), o || f === null ? be(r) && (l === null ? n.removeAttribute(r) : n.setAttribute(r, "" + l)) : f.mustUseProperty ? n[f.propertyName] = l === null ? f.type === 3 ? !1 : "" : l : (r = f.attributeName, o = f.attributeNamespace, l === null ? n.removeAttribute(r) : (f = f.type, l = f === 3 || f === 4 && l === !0 ? "" : "" + l, o ? n.setAttributeNS(o, r, l) : n.setAttribute(r, l))));
  }
  var Tt = h.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED, ze = Symbol.for("react.element"), St = Symbol.for("react.portal"), Qe = Symbol.for("react.fragment"), vn = Symbol.for("react.strict_mode"), Zt = Symbol.for("react.profiler"), on = Symbol.for("react.provider"), hn = Symbol.for("react.context"), zt = Symbol.for("react.forward_ref"), He = Symbol.for("react.suspense"), Yt = Symbol.for("react.suspense_list"), Ut = Symbol.for("react.memo"), Ft = Symbol.for("react.lazy"), De = Symbol.for("react.offscreen"), le = Symbol.iterator;
  function Oe(n) {
    return n === null || typeof n != "object" ? null : (n = le && n[le] || n["@@iterator"], typeof n == "function" ? n : null);
  }
  var se = Object.assign, L;
  function Z(n) {
    if (L === void 0) try {
      throw Error();
    } catch (l) {
      var r = l.stack.trim().match(/\n( *(at )?)/);
      L = r && r[1] || "";
    }
    return `
` + L + n;
  }
  var Ze = !1;
  function Ye(n, r) {
    if (!n || Ze) return "";
    Ze = !0;
    var l = Error.prepareStackTrace;
    Error.prepareStackTrace = void 0;
    try {
      if (r) if (r = function() {
        throw Error();
      }, Object.defineProperty(r.prototype, "props", { set: function() {
        throw Error();
      } }), typeof Reflect == "object" && Reflect.construct) {
        try {
          Reflect.construct(r, []);
        } catch (P) {
          var o = P;
        }
        Reflect.construct(n, [], r);
      } else {
        try {
          r.call();
        } catch (P) {
          o = P;
        }
        n.call(r.prototype);
      }
      else {
        try {
          throw Error();
        } catch (P) {
          o = P;
        }
        n();
      }
    } catch (P) {
      if (P && o && typeof P.stack == "string") {
        for (var f = P.stack.split(`
`), v = o.stack.split(`
`), C = f.length - 1, w = v.length - 1; 1 <= C && 0 <= w && f[C] !== v[w]; ) w--;
        for (; 1 <= C && 0 <= w; C--, w--) if (f[C] !== v[w]) {
          if (C !== 1 || w !== 1)
            do
              if (C--, w--, 0 > w || f[C] !== v[w]) {
                var D = `
` + f[C].replace(" at new ", " at ");
                return n.displayName && D.includes("<anonymous>") && (D = D.replace("<anonymous>", n.displayName)), D;
              }
            while (1 <= C && 0 <= w);
          break;
        }
      }
    } finally {
      Ze = !1, Error.prepareStackTrace = l;
    }
    return (n = n ? n.displayName || n.name : "") ? Z(n) : "";
  }
  function vt(n) {
    switch (n.tag) {
      case 5:
        return Z(n.type);
      case 16:
        return Z("Lazy");
      case 13:
        return Z("Suspense");
      case 19:
        return Z("SuspenseList");
      case 0:
      case 2:
      case 15:
        return n = Ye(n.type, !1), n;
      case 11:
        return n = Ye(n.type.render, !1), n;
      case 1:
        return n = Ye(n.type, !0), n;
      default:
        return "";
    }
  }
  function ct(n) {
    if (n == null) return null;
    if (typeof n == "function") return n.displayName || n.name || null;
    if (typeof n == "string") return n;
    switch (n) {
      case Qe:
        return "Fragment";
      case St:
        return "Portal";
      case Zt:
        return "Profiler";
      case vn:
        return "StrictMode";
      case He:
        return "Suspense";
      case Yt:
        return "SuspenseList";
    }
    if (typeof n == "object") switch (n.$$typeof) {
      case hn:
        return (n.displayName || "Context") + ".Consumer";
      case on:
        return (n._context.displayName || "Context") + ".Provider";
      case zt:
        var r = n.render;
        return n = n.displayName, n || (n = r.displayName || r.name || "", n = n !== "" ? "ForwardRef(" + n + ")" : "ForwardRef"), n;
      case Ut:
        return r = n.displayName || null, r !== null ? r : ct(n.type) || "Memo";
      case Ft:
        r = n._payload, n = n._init;
        try {
          return ct(n(r));
        } catch {
        }
    }
    return null;
  }
  function ot(n) {
    var r = n.type;
    switch (n.tag) {
      case 24:
        return "Cache";
      case 9:
        return (r.displayName || "Context") + ".Consumer";
      case 10:
        return (r._context.displayName || "Context") + ".Provider";
      case 18:
        return "DehydratedFragment";
      case 11:
        return n = r.render, n = n.displayName || n.name || "", r.displayName || (n !== "" ? "ForwardRef(" + n + ")" : "ForwardRef");
      case 7:
        return "Fragment";
      case 5:
        return r;
      case 4:
        return "Portal";
      case 3:
        return "Root";
      case 6:
        return "Text";
      case 16:
        return ct(r);
      case 8:
        return r === vn ? "StrictMode" : "Mode";
      case 22:
        return "Offscreen";
      case 12:
        return "Profiler";
      case 21:
        return "Scope";
      case 13:
        return "Suspense";
      case 19:
        return "SuspenseList";
      case 25:
        return "TracingMarker";
      case 1:
      case 0:
      case 17:
      case 2:
      case 14:
      case 15:
        if (typeof r == "function") return r.displayName || r.name || null;
        if (typeof r == "string") return r;
    }
    return null;
  }
  function ft(n) {
    switch (typeof n) {
      case "boolean":
      case "number":
      case "string":
      case "undefined":
        return n;
      case "object":
        return n;
      default:
        return "";
    }
  }
  function ht(n) {
    var r = n.type;
    return (n = n.nodeName) && n.toLowerCase() === "input" && (r === "checkbox" || r === "radio");
  }
  function Xt(n) {
    var r = ht(n) ? "checked" : "value", l = Object.getOwnPropertyDescriptor(n.constructor.prototype, r), o = "" + n[r];
    if (!n.hasOwnProperty(r) && typeof l < "u" && typeof l.get == "function" && typeof l.set == "function") {
      var f = l.get, v = l.set;
      return Object.defineProperty(n, r, { configurable: !0, get: function() {
        return f.call(this);
      }, set: function(C) {
        o = "" + C, v.call(this, C);
      } }), Object.defineProperty(n, r, { enumerable: l.enumerable }), { getValue: function() {
        return o;
      }, setValue: function(C) {
        o = "" + C;
      }, stopTracking: function() {
        n._valueTracker = null, delete n[r];
      } };
    }
  }
  function Hn(n) {
    n._valueTracker || (n._valueTracker = Xt(n));
  }
  function Ur(n) {
    if (!n) return !1;
    var r = n._valueTracker;
    if (!r) return !0;
    var l = r.getValue(), o = "";
    return n && (o = ht(n) ? n.checked ? "true" : "false" : n.value), n = o, n !== l ? (r.setValue(n), !0) : !1;
  }
  function On(n) {
    if (n = n || (typeof document < "u" ? document : void 0), typeof n > "u") return null;
    try {
      return n.activeElement || n.body;
    } catch {
      return n.body;
    }
  }
  function pr(n, r) {
    var l = r.checked;
    return se({}, r, { defaultChecked: void 0, defaultValue: void 0, value: void 0, checked: l ?? n._wrapperState.initialChecked });
  }
  function qn(n, r) {
    var l = r.defaultValue == null ? "" : r.defaultValue, o = r.checked != null ? r.checked : r.defaultChecked;
    l = ft(r.value != null ? r.value : l), n._wrapperState = { initialChecked: o, initialValue: l, controlled: r.type === "checkbox" || r.type === "radio" ? r.checked != null : r.value != null };
  }
  function Xn(n, r) {
    r = r.checked, r != null && rt(n, "checked", r, !1);
  }
  function ta(n, r) {
    Xn(n, r);
    var l = ft(r.value), o = r.type;
    if (l != null) o === "number" ? (l === 0 && n.value === "" || n.value != l) && (n.value = "" + l) : n.value !== "" + l && (n.value = "" + l);
    else if (o === "submit" || o === "reset") {
      n.removeAttribute("value");
      return;
    }
    r.hasOwnProperty("value") ? Sa(n, r.type, l) : r.hasOwnProperty("defaultValue") && Sa(n, r.type, ft(r.defaultValue)), r.checked == null && r.defaultChecked != null && (n.defaultChecked = !!r.defaultChecked);
  }
  function _i(n, r, l) {
    if (r.hasOwnProperty("value") || r.hasOwnProperty("defaultValue")) {
      var o = r.type;
      if (!(o !== "submit" && o !== "reset" || r.value !== void 0 && r.value !== null)) return;
      r = "" + n._wrapperState.initialValue, l || r === n.value || (n.value = r), n.defaultValue = r;
    }
    l = n.name, l !== "" && (n.name = ""), n.defaultChecked = !!n._wrapperState.initialChecked, l !== "" && (n.name = l);
  }
  function Sa(n, r, l) {
    (r !== "number" || On(n.ownerDocument) !== n) && (l == null ? n.defaultValue = "" + n._wrapperState.initialValue : n.defaultValue !== "" + l && (n.defaultValue = "" + l));
  }
  var ir = Array.isArray;
  function Nn(n, r, l, o) {
    if (n = n.options, r) {
      r = {};
      for (var f = 0; f < l.length; f++) r["$" + l[f]] = !0;
      for (l = 0; l < n.length; l++) f = r.hasOwnProperty("$" + n[l].value), n[l].selected !== f && (n[l].selected = f), f && o && (n[l].defaultSelected = !0);
    } else {
      for (l = "" + ft(l), r = null, f = 0; f < n.length; f++) {
        if (n[f].value === l) {
          n[f].selected = !0, o && (n[f].defaultSelected = !0);
          return;
        }
        r !== null || n[f].disabled || (r = n[f]);
      }
      r !== null && (r.selected = !0);
    }
  }
  function Kn(n, r) {
    if (r.dangerouslySetInnerHTML != null) throw Error(p(91));
    return se({}, r, { value: void 0, defaultValue: void 0, children: "" + n._wrapperState.initialValue });
  }
  function Dr(n, r) {
    var l = r.value;
    if (l == null) {
      if (l = r.children, r = r.defaultValue, l != null) {
        if (r != null) throw Error(p(92));
        if (ir(l)) {
          if (1 < l.length) throw Error(p(93));
          l = l[0];
        }
        r = l;
      }
      r == null && (r = ""), l = r;
    }
    n._wrapperState = { initialValue: ft(l) };
  }
  function ri(n, r) {
    var l = ft(r.value), o = ft(r.defaultValue);
    l != null && (l = "" + l, l !== n.value && (n.value = l), r.defaultValue == null && n.defaultValue !== l && (n.defaultValue = l)), o != null && (n.defaultValue = "" + o);
  }
  function Vn(n) {
    var r = n.textContent;
    r === n._wrapperState.initialValue && r !== "" && r !== null && (n.value = r);
  }
  function Or(n) {
    switch (n) {
      case "svg":
        return "http://www.w3.org/2000/svg";
      case "math":
        return "http://www.w3.org/1998/Math/MathML";
      default:
        return "http://www.w3.org/1999/xhtml";
    }
  }
  function Ea(n, r) {
    return n == null || n === "http://www.w3.org/1999/xhtml" ? Or(r) : n === "http://www.w3.org/2000/svg" && r === "foreignObject" ? "http://www.w3.org/1999/xhtml" : n;
  }
  var ai, xi = function(n) {
    return typeof MSApp < "u" && MSApp.execUnsafeLocalFunction ? function(r, l, o, f) {
      MSApp.execUnsafeLocalFunction(function() {
        return n(r, l, o, f);
      });
    } : n;
  }(function(n, r) {
    if (n.namespaceURI !== "http://www.w3.org/2000/svg" || "innerHTML" in n) n.innerHTML = r;
    else {
      for (ai = ai || document.createElement("div"), ai.innerHTML = "<svg>" + r.valueOf().toString() + "</svg>", r = ai.firstChild; n.firstChild; ) n.removeChild(n.firstChild);
      for (; r.firstChild; ) n.appendChild(r.firstChild);
    }
  });
  function ue(n, r) {
    if (r) {
      var l = n.firstChild;
      if (l && l === n.lastChild && l.nodeType === 3) {
        l.nodeValue = r;
        return;
      }
    }
    n.textContent = r;
  }
  var Ne = {
    animationIterationCount: !0,
    aspectRatio: !0,
    borderImageOutset: !0,
    borderImageSlice: !0,
    borderImageWidth: !0,
    boxFlex: !0,
    boxFlexGroup: !0,
    boxOrdinalGroup: !0,
    columnCount: !0,
    columns: !0,
    flex: !0,
    flexGrow: !0,
    flexPositive: !0,
    flexShrink: !0,
    flexNegative: !0,
    flexOrder: !0,
    gridArea: !0,
    gridRow: !0,
    gridRowEnd: !0,
    gridRowSpan: !0,
    gridRowStart: !0,
    gridColumn: !0,
    gridColumnEnd: !0,
    gridColumnSpan: !0,
    gridColumnStart: !0,
    fontWeight: !0,
    lineClamp: !0,
    lineHeight: !0,
    opacity: !0,
    order: !0,
    orphans: !0,
    tabSize: !0,
    widows: !0,
    zIndex: !0,
    zoom: !0,
    fillOpacity: !0,
    floodOpacity: !0,
    stopOpacity: !0,
    strokeDasharray: !0,
    strokeDashoffset: !0,
    strokeMiterlimit: !0,
    strokeOpacity: !0,
    strokeWidth: !0
  }, dt = ["Webkit", "ms", "Moz", "O"];
  Object.keys(Ne).forEach(function(n) {
    dt.forEach(function(r) {
      r = r + n.charAt(0).toUpperCase() + n.substring(1), Ne[r] = Ne[n];
    });
  });
  function Wt(n, r, l) {
    return r == null || typeof r == "boolean" || r === "" ? "" : l || typeof r != "number" || r === 0 || Ne.hasOwnProperty(n) && Ne[n] ? ("" + r).trim() : r + "px";
  }
  function sn(n, r) {
    n = n.style;
    for (var l in r) if (r.hasOwnProperty(l)) {
      var o = l.indexOf("--") === 0, f = Wt(l, r[l], o);
      l === "float" && (l = "cssFloat"), o ? n.setProperty(l, f) : n[l] = f;
    }
  }
  var xn = se({ menuitem: !0 }, { area: !0, base: !0, br: !0, col: !0, embed: !0, hr: !0, img: !0, input: !0, keygen: !0, link: !0, meta: !0, param: !0, source: !0, track: !0, wbr: !0 });
  function mn(n, r) {
    if (r) {
      if (xn[n] && (r.children != null || r.dangerouslySetInnerHTML != null)) throw Error(p(137, n));
      if (r.dangerouslySetInnerHTML != null) {
        if (r.children != null) throw Error(p(60));
        if (typeof r.dangerouslySetInnerHTML != "object" || !("__html" in r.dangerouslySetInnerHTML)) throw Error(p(61));
      }
      if (r.style != null && typeof r.style != "object") throw Error(p(62));
    }
  }
  function lr(n, r) {
    if (n.indexOf("-") === -1) return typeof r.is == "string";
    switch (n) {
      case "annotation-xml":
      case "color-profile":
      case "font-face":
      case "font-face-src":
      case "font-face-uri":
      case "font-face-format":
      case "font-face-name":
      case "missing-glyph":
        return !1;
      default:
        return !0;
    }
  }
  var cn = null;
  function Kt(n) {
    return n = n.target || n.srcElement || window, n.correspondingUseElement && (n = n.correspondingUseElement), n.nodeType === 3 ? n.parentNode : n;
  }
  var Jt = null, Ca = null, Nr = null;
  function za(n) {
    if (n = Ue(n)) {
      if (typeof Jt != "function") throw Error(p(280));
      var r = n.stateNode;
      r && (r = Rn(r), Jt(n.stateNode, n.type, r));
    }
  }
  function Ki(n) {
    Ca ? Nr ? Nr.push(n) : Nr = [n] : Ca = n;
  }
  function hu() {
    if (Ca) {
      var n = Ca, r = Nr;
      if (Nr = Ca = null, za(n), r) for (n = 0; n < r.length; n++) za(r[n]);
    }
  }
  function mu(n, r) {
    return n(r);
  }
  function wl() {
  }
  var bl = !1;
  function yu(n, r, l) {
    if (bl) return n(r, l);
    bl = !0;
    try {
      return mu(n, r, l);
    } finally {
      bl = !1, (Ca !== null || Nr !== null) && (wl(), hu());
    }
  }
  function jr(n, r) {
    var l = n.stateNode;
    if (l === null) return null;
    var o = Rn(l);
    if (o === null) return null;
    l = o[r];
    e: switch (r) {
      case "onClick":
      case "onClickCapture":
      case "onDoubleClick":
      case "onDoubleClickCapture":
      case "onMouseDown":
      case "onMouseDownCapture":
      case "onMouseMove":
      case "onMouseMoveCapture":
      case "onMouseUp":
      case "onMouseUpCapture":
      case "onMouseEnter":
        (o = !o.disabled) || (n = n.type, o = !(n === "button" || n === "input" || n === "select" || n === "textarea")), n = !o;
        break e;
      default:
        n = !1;
    }
    if (n) return null;
    if (l && typeof l != "function") throw Error(p(231, r, typeof l));
    return l;
  }
  var Fr = !1;
  if (A) try {
    var vr = {};
    Object.defineProperty(vr, "passive", { get: function() {
      Fr = !0;
    } }), window.addEventListener("test", vr, vr), window.removeEventListener("test", vr, vr);
  } catch {
    Fr = !1;
  }
  function Ti(n, r, l, o, f, v, C, w, D) {
    var P = Array.prototype.slice.call(arguments, 3);
    try {
      r.apply(l, P);
    } catch (J) {
      this.onError(J);
    }
  }
  var ii = !1, Ri = null, wi = !1, k = null, q = { onError: function(n) {
    ii = !0, Ri = n;
  } };
  function pe(n, r, l, o, f, v, C, w, D) {
    ii = !1, Ri = null, Ti.apply(q, arguments);
  }
  function _e(n, r, l, o, f, v, C, w, D) {
    if (pe.apply(this, arguments), ii) {
      if (ii) {
        var P = Ri;
        ii = !1, Ri = null;
      } else throw Error(p(198));
      wi || (wi = !0, k = P);
    }
  }
  function it(n) {
    var r = n, l = n;
    if (n.alternate) for (; r.return; ) r = r.return;
    else {
      n = r;
      do
        r = n, r.flags & 4098 && (l = r.return), n = r.return;
      while (n);
    }
    return r.tag === 3 ? l : null;
  }
  function Ke(n) {
    if (n.tag === 13) {
      var r = n.memoizedState;
      if (r === null && (n = n.alternate, n !== null && (r = n.memoizedState)), r !== null) return r.dehydrated;
    }
    return null;
  }
  function Et(n) {
    if (it(n) !== n) throw Error(p(188));
  }
  function mt(n) {
    var r = n.alternate;
    if (!r) {
      if (r = it(n), r === null) throw Error(p(188));
      return r !== n ? null : n;
    }
    for (var l = n, o = r; ; ) {
      var f = l.return;
      if (f === null) break;
      var v = f.alternate;
      if (v === null) {
        if (o = f.return, o !== null) {
          l = o;
          continue;
        }
        break;
      }
      if (f.child === v.child) {
        for (v = f.child; v; ) {
          if (v === l) return Et(f), n;
          if (v === o) return Et(f), r;
          v = v.sibling;
        }
        throw Error(p(188));
      }
      if (l.return !== o.return) l = f, o = v;
      else {
        for (var C = !1, w = f.child; w; ) {
          if (w === l) {
            C = !0, l = f, o = v;
            break;
          }
          if (w === o) {
            C = !0, o = f, l = v;
            break;
          }
          w = w.sibling;
        }
        if (!C) {
          for (w = v.child; w; ) {
            if (w === l) {
              C = !0, l = v, o = f;
              break;
            }
            if (w === o) {
              C = !0, o = v, l = f;
              break;
            }
            w = w.sibling;
          }
          if (!C) throw Error(p(189));
        }
      }
      if (l.alternate !== o) throw Error(p(190));
    }
    if (l.tag !== 3) throw Error(p(188));
    return l.stateNode.current === l ? n : r;
  }
  function Mn(n) {
    return n = mt(n), n !== null ? fn(n) : null;
  }
  function fn(n) {
    if (n.tag === 5 || n.tag === 6) return n;
    for (n = n.child; n !== null; ) {
      var r = fn(n);
      if (r !== null) return r;
      n = n.sibling;
    }
    return null;
  }
  var yn = c.unstable_scheduleCallback, hr = c.unstable_cancelCallback, li = c.unstable_shouldYield, ui = c.unstable_requestPaint, lt = c.unstable_now, st = c.unstable_getCurrentPriorityLevel, oi = c.unstable_ImmediatePriority, gu = c.unstable_UserBlockingPriority, Su = c.unstable_NormalPriority, kl = c.unstable_LowPriority, vo = c.unstable_IdlePriority, Dl = null, na = null;
  function vs(n) {
    if (na && typeof na.onCommitFiberRoot == "function") try {
      na.onCommitFiberRoot(Dl, n, void 0, (n.current.flags & 128) === 128);
    } catch {
    }
  }
  var Hr = Math.clz32 ? Math.clz32 : ho, Mc = Math.log, Lc = Math.LN2;
  function ho(n) {
    return n >>>= 0, n === 0 ? 32 : 31 - (Mc(n) / Lc | 0) | 0;
  }
  var Ol = 64, _a = 4194304;
  function si(n) {
    switch (n & -n) {
      case 1:
        return 1;
      case 2:
        return 2;
      case 4:
        return 4;
      case 8:
        return 8;
      case 16:
        return 16;
      case 32:
        return 32;
      case 64:
      case 128:
      case 256:
      case 512:
      case 1024:
      case 2048:
      case 4096:
      case 8192:
      case 16384:
      case 32768:
      case 65536:
      case 131072:
      case 262144:
      case 524288:
      case 1048576:
      case 2097152:
        return n & 4194240;
      case 4194304:
      case 8388608:
      case 16777216:
      case 33554432:
      case 67108864:
        return n & 130023424;
      case 134217728:
        return 134217728;
      case 268435456:
        return 268435456;
      case 536870912:
        return 536870912;
      case 1073741824:
        return 1073741824;
      default:
        return n;
    }
  }
  function ci(n, r) {
    var l = n.pendingLanes;
    if (l === 0) return 0;
    var o = 0, f = n.suspendedLanes, v = n.pingedLanes, C = l & 268435455;
    if (C !== 0) {
      var w = C & ~f;
      w !== 0 ? o = si(w) : (v &= C, v !== 0 && (o = si(v)));
    } else C = l & ~f, C !== 0 ? o = si(C) : v !== 0 && (o = si(v));
    if (o === 0) return 0;
    if (r !== 0 && r !== o && !(r & f) && (f = o & -o, v = r & -r, f >= v || f === 16 && (v & 4194240) !== 0)) return r;
    if (o & 4 && (o |= l & 16), r = n.entangledLanes, r !== 0) for (n = n.entanglements, r &= o; 0 < r; ) l = 31 - Hr(r), f = 1 << l, o |= n[l], r &= ~f;
    return o;
  }
  function mo(n, r) {
    switch (n) {
      case 1:
      case 2:
      case 4:
        return r + 250;
      case 8:
      case 16:
      case 32:
      case 64:
      case 128:
      case 256:
      case 512:
      case 1024:
      case 2048:
      case 4096:
      case 8192:
      case 16384:
      case 32768:
      case 65536:
      case 131072:
      case 262144:
      case 524288:
      case 1048576:
      case 2097152:
        return r + 5e3;
      case 4194304:
      case 8388608:
      case 16777216:
      case 33554432:
      case 67108864:
        return -1;
      case 134217728:
      case 268435456:
      case 536870912:
      case 1073741824:
        return -1;
      default:
        return -1;
    }
  }
  function Eu(n, r) {
    for (var l = n.suspendedLanes, o = n.pingedLanes, f = n.expirationTimes, v = n.pendingLanes; 0 < v; ) {
      var C = 31 - Hr(v), w = 1 << C, D = f[C];
      D === -1 ? (!(w & l) || w & o) && (f[C] = mo(w, r)) : D <= r && (n.expiredLanes |= w), v &= ~w;
    }
  }
  function Nl(n) {
    return n = n.pendingLanes & -1073741825, n !== 0 ? n : n & 1073741824 ? 1073741824 : 0;
  }
  function yo() {
    var n = Ol;
    return Ol <<= 1, !(Ol & 4194240) && (Ol = 64), n;
  }
  function go(n) {
    for (var r = [], l = 0; 31 > l; l++) r.push(n);
    return r;
  }
  function Ji(n, r, l) {
    n.pendingLanes |= r, r !== 536870912 && (n.suspendedLanes = 0, n.pingedLanes = 0), n = n.eventTimes, r = 31 - Hr(r), n[r] = l;
  }
  function _d(n, r) {
    var l = n.pendingLanes & ~r;
    n.pendingLanes = r, n.suspendedLanes = 0, n.pingedLanes = 0, n.expiredLanes &= r, n.mutableReadLanes &= r, n.entangledLanes &= r, r = n.entanglements;
    var o = n.eventTimes;
    for (n = n.expirationTimes; 0 < l; ) {
      var f = 31 - Hr(l), v = 1 << f;
      r[f] = 0, o[f] = -1, n[f] = -1, l &= ~v;
    }
  }
  function el(n, r) {
    var l = n.entangledLanes |= r;
    for (n = n.entanglements; l; ) {
      var o = 31 - Hr(l), f = 1 << o;
      f & r | n[o] & r && (n[o] |= r), l &= ~f;
    }
  }
  var Ht = 0;
  function So(n) {
    return n &= -n, 1 < n ? 4 < n ? n & 268435455 ? 16 : 536870912 : 4 : 1;
  }
  var Lt, hs, bi, Xe, Eo, mr = !1, ki = [], Vr = null, Di = null, gn = null, en = /* @__PURE__ */ new Map(), Ml = /* @__PURE__ */ new Map(), Jn = [], Pr = "mousedown mouseup touchcancel touchend touchstart auxclick dblclick pointercancel pointerdown pointerup dragend dragstart drop compositionend compositionstart keydown keypress keyup input textInput copy cut paste click change contextmenu reset submit".split(" ");
  function Ua(n, r) {
    switch (n) {
      case "focusin":
      case "focusout":
        Vr = null;
        break;
      case "dragenter":
      case "dragleave":
        Di = null;
        break;
      case "mouseover":
      case "mouseout":
        gn = null;
        break;
      case "pointerover":
      case "pointerout":
        en.delete(r.pointerId);
        break;
      case "gotpointercapture":
      case "lostpointercapture":
        Ml.delete(r.pointerId);
    }
  }
  function Cu(n, r, l, o, f, v) {
    return n === null || n.nativeEvent !== v ? (n = { blockedOn: r, domEventName: l, eventSystemFlags: o, nativeEvent: v, targetContainers: [f] }, r !== null && (r = Ue(r), r !== null && hs(r)), n) : (n.eventSystemFlags |= o, r = n.targetContainers, f !== null && r.indexOf(f) === -1 && r.push(f), n);
  }
  function ms(n, r, l, o, f) {
    switch (r) {
      case "focusin":
        return Vr = Cu(Vr, n, r, l, o, f), !0;
      case "dragenter":
        return Di = Cu(Di, n, r, l, o, f), !0;
      case "mouseover":
        return gn = Cu(gn, n, r, l, o, f), !0;
      case "pointerover":
        var v = f.pointerId;
        return en.set(v, Cu(en.get(v) || null, n, r, l, o, f)), !0;
      case "gotpointercapture":
        return v = f.pointerId, Ml.set(v, Cu(Ml.get(v) || null, n, r, l, o, f)), !0;
    }
    return !1;
  }
  function ys(n) {
    var r = Ou(n.target);
    if (r !== null) {
      var l = it(r);
      if (l !== null) {
        if (r = l.tag, r === 13) {
          if (r = Ke(l), r !== null) {
            n.blockedOn = r, Eo(n.priority, function() {
              bi(l);
            });
            return;
          }
        } else if (r === 3 && l.stateNode.current.memoizedState.isDehydrated) {
          n.blockedOn = l.tag === 3 ? l.stateNode.containerInfo : null;
          return;
        }
      }
    }
    n.blockedOn = null;
  }
  function Ll(n) {
    if (n.blockedOn !== null) return !1;
    for (var r = n.targetContainers; 0 < r.length; ) {
      var l = xo(n.domEventName, n.eventSystemFlags, r[0], n.nativeEvent);
      if (l === null) {
        l = n.nativeEvent;
        var o = new l.constructor(l.type, l);
        cn = o, l.target.dispatchEvent(o), cn = null;
      } else return r = Ue(l), r !== null && hs(r), n.blockedOn = l, !1;
      r.shift();
    }
    return !0;
  }
  function _u(n, r, l) {
    Ll(n) && l.delete(r);
  }
  function xd() {
    mr = !1, Vr !== null && Ll(Vr) && (Vr = null), Di !== null && Ll(Di) && (Di = null), gn !== null && Ll(gn) && (gn = null), en.forEach(_u), Ml.forEach(_u);
  }
  function ja(n, r) {
    n.blockedOn === r && (n.blockedOn = null, mr || (mr = !0, c.unstable_scheduleCallback(c.unstable_NormalPriority, xd)));
  }
  function fi(n) {
    function r(f) {
      return ja(f, n);
    }
    if (0 < ki.length) {
      ja(ki[0], n);
      for (var l = 1; l < ki.length; l++) {
        var o = ki[l];
        o.blockedOn === n && (o.blockedOn = null);
      }
    }
    for (Vr !== null && ja(Vr, n), Di !== null && ja(Di, n), gn !== null && ja(gn, n), en.forEach(r), Ml.forEach(r), l = 0; l < Jn.length; l++) o = Jn[l], o.blockedOn === n && (o.blockedOn = null);
    for (; 0 < Jn.length && (l = Jn[0], l.blockedOn === null); ) ys(l), l.blockedOn === null && Jn.shift();
  }
  var Oi = Tt.ReactCurrentBatchConfig, Fa = !0;
  function Co(n, r, l, o) {
    var f = Ht, v = Oi.transition;
    Oi.transition = null;
    try {
      Ht = 1, Al(n, r, l, o);
    } finally {
      Ht = f, Oi.transition = v;
    }
  }
  function _o(n, r, l, o) {
    var f = Ht, v = Oi.transition;
    Oi.transition = null;
    try {
      Ht = 4, Al(n, r, l, o);
    } finally {
      Ht = f, Oi.transition = v;
    }
  }
  function Al(n, r, l, o) {
    if (Fa) {
      var f = xo(n, r, l, o);
      if (f === null) Yc(n, r, o, xu, l), Ua(n, o);
      else if (ms(f, n, r, l, o)) o.stopPropagation();
      else if (Ua(n, o), r & 4 && -1 < Pr.indexOf(n)) {
        for (; f !== null; ) {
          var v = Ue(f);
          if (v !== null && Lt(v), v = xo(n, r, l, o), v === null && Yc(n, r, o, xu, l), v === f) break;
          f = v;
        }
        f !== null && o.stopPropagation();
      } else Yc(n, r, o, null, l);
    }
  }
  var xu = null;
  function xo(n, r, l, o) {
    if (xu = null, n = Kt(o), n = Ou(n), n !== null) if (r = it(n), r === null) n = null;
    else if (l = r.tag, l === 13) {
      if (n = Ke(r), n !== null) return n;
      n = null;
    } else if (l === 3) {
      if (r.stateNode.current.memoizedState.isDehydrated) return r.tag === 3 ? r.stateNode.containerInfo : null;
      n = null;
    } else r !== n && (n = null);
    return xu = n, null;
  }
  function To(n) {
    switch (n) {
      case "cancel":
      case "click":
      case "close":
      case "contextmenu":
      case "copy":
      case "cut":
      case "auxclick":
      case "dblclick":
      case "dragend":
      case "dragstart":
      case "drop":
      case "focusin":
      case "focusout":
      case "input":
      case "invalid":
      case "keydown":
      case "keypress":
      case "keyup":
      case "mousedown":
      case "mouseup":
      case "paste":
      case "pause":
      case "play":
      case "pointercancel":
      case "pointerdown":
      case "pointerup":
      case "ratechange":
      case "reset":
      case "resize":
      case "seeked":
      case "submit":
      case "touchcancel":
      case "touchend":
      case "touchstart":
      case "volumechange":
      case "change":
      case "selectionchange":
      case "textInput":
      case "compositionstart":
      case "compositionend":
      case "compositionupdate":
      case "beforeblur":
      case "afterblur":
      case "beforeinput":
      case "blur":
      case "fullscreenchange":
      case "focus":
      case "hashchange":
      case "popstate":
      case "select":
      case "selectstart":
        return 1;
      case "drag":
      case "dragenter":
      case "dragexit":
      case "dragleave":
      case "dragover":
      case "mousemove":
      case "mouseout":
      case "mouseover":
      case "pointermove":
      case "pointerout":
      case "pointerover":
      case "scroll":
      case "toggle":
      case "touchmove":
      case "wheel":
      case "mouseenter":
      case "mouseleave":
      case "pointerenter":
      case "pointerleave":
        return 4;
      case "message":
        switch (st()) {
          case oi:
            return 1;
          case gu:
            return 4;
          case Su:
          case kl:
            return 16;
          case vo:
            return 536870912;
          default:
            return 16;
        }
      default:
        return 16;
    }
  }
  var di = null, g = null, b = null;
  function V() {
    if (b) return b;
    var n, r = g, l = r.length, o, f = "value" in di ? di.value : di.textContent, v = f.length;
    for (n = 0; n < l && r[n] === f[n]; n++) ;
    var C = l - n;
    for (o = 1; o <= C && r[l - o] === f[v - o]; o++) ;
    return b = f.slice(n, 1 < o ? 1 - o : void 0);
  }
  function Y(n) {
    var r = n.keyCode;
    return "charCode" in n ? (n = n.charCode, n === 0 && r === 13 && (n = 13)) : n = r, n === 10 && (n = 13), 32 <= n || n === 13 ? n : 0;
  }
  function ie() {
    return !0;
  }
  function Ve() {
    return !1;
  }
  function ce(n) {
    function r(l, o, f, v, C) {
      this._reactName = l, this._targetInst = f, this.type = o, this.nativeEvent = v, this.target = C, this.currentTarget = null;
      for (var w in n) n.hasOwnProperty(w) && (l = n[w], this[w] = l ? l(v) : v[w]);
      return this.isDefaultPrevented = (v.defaultPrevented != null ? v.defaultPrevented : v.returnValue === !1) ? ie : Ve, this.isPropagationStopped = Ve, this;
    }
    return se(r.prototype, { preventDefault: function() {
      this.defaultPrevented = !0;
      var l = this.nativeEvent;
      l && (l.preventDefault ? l.preventDefault() : typeof l.returnValue != "unknown" && (l.returnValue = !1), this.isDefaultPrevented = ie);
    }, stopPropagation: function() {
      var l = this.nativeEvent;
      l && (l.stopPropagation ? l.stopPropagation() : typeof l.cancelBubble != "unknown" && (l.cancelBubble = !0), this.isPropagationStopped = ie);
    }, persist: function() {
    }, isPersistent: ie }), r;
  }
  var Ie = { eventPhase: 0, bubbles: 0, cancelable: 0, timeStamp: function(n) {
    return n.timeStamp || Date.now();
  }, defaultPrevented: 0, isTrusted: 0 }, Ct = ce(Ie), At = se({}, Ie, { view: 0, detail: 0 }), dn = ce(At), tn, pt, nn, Tn = se({}, At, { screenX: 0, screenY: 0, clientX: 0, clientY: 0, pageX: 0, pageY: 0, ctrlKey: 0, shiftKey: 0, altKey: 0, metaKey: 0, getModifierState: kd, button: 0, buttons: 0, relatedTarget: function(n) {
    return n.relatedTarget === void 0 ? n.fromElement === n.srcElement ? n.toElement : n.fromElement : n.relatedTarget;
  }, movementX: function(n) {
    return "movementX" in n ? n.movementX : (n !== nn && (nn && n.type === "mousemove" ? (tn = n.screenX - nn.screenX, pt = n.screenY - nn.screenY) : pt = tn = 0, nn = n), tn);
  }, movementY: function(n) {
    return "movementY" in n ? n.movementY : pt;
  } }), zl = ce(Tn), gs = se({}, Tn, { dataTransfer: 0 }), tl = ce(gs), Ss = se({}, At, { relatedTarget: 0 }), Tu = ce(Ss), Td = se({}, Ie, { animationName: 0, elapsedTime: 0, pseudoElement: 0 }), Ac = ce(Td), Rd = se({}, Ie, { clipboardData: function(n) {
    return "clipboardData" in n ? n.clipboardData : window.clipboardData;
  } }), Vv = ce(Rd), wd = se({}, Ie, { data: 0 }), bd = ce(wd), Pv = {
    Esc: "Escape",
    Spacebar: " ",
    Left: "ArrowLeft",
    Up: "ArrowUp",
    Right: "ArrowRight",
    Down: "ArrowDown",
    Del: "Delete",
    Win: "OS",
    Menu: "ContextMenu",
    Apps: "ContextMenu",
    Scroll: "ScrollLock",
    MozPrintableKey: "Unidentified"
  }, Bv = {
    8: "Backspace",
    9: "Tab",
    12: "Clear",
    13: "Enter",
    16: "Shift",
    17: "Control",
    18: "Alt",
    19: "Pause",
    20: "CapsLock",
    27: "Escape",
    32: " ",
    33: "PageUp",
    34: "PageDown",
    35: "End",
    36: "Home",
    37: "ArrowLeft",
    38: "ArrowUp",
    39: "ArrowRight",
    40: "ArrowDown",
    45: "Insert",
    46: "Delete",
    112: "F1",
    113: "F2",
    114: "F3",
    115: "F4",
    116: "F5",
    117: "F6",
    118: "F7",
    119: "F8",
    120: "F9",
    121: "F10",
    122: "F11",
    123: "F12",
    144: "NumLock",
    145: "ScrollLock",
    224: "Meta"
  }, Wy = { Alt: "altKey", Control: "ctrlKey", Meta: "metaKey", Shift: "shiftKey" };
  function nl(n) {
    var r = this.nativeEvent;
    return r.getModifierState ? r.getModifierState(n) : (n = Wy[n]) ? !!r[n] : !1;
  }
  function kd() {
    return nl;
  }
  var Dd = se({}, At, { key: function(n) {
    if (n.key) {
      var r = Pv[n.key] || n.key;
      if (r !== "Unidentified") return r;
    }
    return n.type === "keypress" ? (n = Y(n), n === 13 ? "Enter" : String.fromCharCode(n)) : n.type === "keydown" || n.type === "keyup" ? Bv[n.keyCode] || "Unidentified" : "";
  }, code: 0, location: 0, ctrlKey: 0, shiftKey: 0, altKey: 0, metaKey: 0, repeat: 0, locale: 0, getModifierState: kd, charCode: function(n) {
    return n.type === "keypress" ? Y(n) : 0;
  }, keyCode: function(n) {
    return n.type === "keydown" || n.type === "keyup" ? n.keyCode : 0;
  }, which: function(n) {
    return n.type === "keypress" ? Y(n) : n.type === "keydown" || n.type === "keyup" ? n.keyCode : 0;
  } }), Od = ce(Dd), Nd = se({}, Tn, { pointerId: 0, width: 0, height: 0, pressure: 0, tangentialPressure: 0, tiltX: 0, tiltY: 0, twist: 0, pointerType: 0, isPrimary: 0 }), Iv = ce(Nd), zc = se({}, At, { touches: 0, targetTouches: 0, changedTouches: 0, altKey: 0, metaKey: 0, ctrlKey: 0, shiftKey: 0, getModifierState: kd }), $v = ce(zc), ra = se({}, Ie, { propertyName: 0, elapsedTime: 0, pseudoElement: 0 }), rl = ce(ra), Pn = se({}, Tn, {
    deltaX: function(n) {
      return "deltaX" in n ? n.deltaX : "wheelDeltaX" in n ? -n.wheelDeltaX : 0;
    },
    deltaY: function(n) {
      return "deltaY" in n ? n.deltaY : "wheelDeltaY" in n ? -n.wheelDeltaY : "wheelDelta" in n ? -n.wheelDelta : 0;
    },
    deltaZ: 0,
    deltaMode: 0
  }), al = ce(Pn), Md = [9, 13, 27, 32], Ro = A && "CompositionEvent" in window, Es = null;
  A && "documentMode" in document && (Es = document.documentMode);
  var Cs = A && "TextEvent" in window && !Es, Yv = A && (!Ro || Es && 8 < Es && 11 >= Es), Wv = " ", Uc = !1;
  function Qv(n, r) {
    switch (n) {
      case "keyup":
        return Md.indexOf(r.keyCode) !== -1;
      case "keydown":
        return r.keyCode !== 229;
      case "keypress":
      case "mousedown":
      case "focusout":
        return !0;
      default:
        return !1;
    }
  }
  function Zv(n) {
    return n = n.detail, typeof n == "object" && "data" in n ? n.data : null;
  }
  var wo = !1;
  function Gv(n, r) {
    switch (n) {
      case "compositionend":
        return Zv(r);
      case "keypress":
        return r.which !== 32 ? null : (Uc = !0, Wv);
      case "textInput":
        return n = r.data, n === Wv && Uc ? null : n;
      default:
        return null;
    }
  }
  function Qy(n, r) {
    if (wo) return n === "compositionend" || !Ro && Qv(n, r) ? (n = V(), b = g = di = null, wo = !1, n) : null;
    switch (n) {
      case "paste":
        return null;
      case "keypress":
        if (!(r.ctrlKey || r.altKey || r.metaKey) || r.ctrlKey && r.altKey) {
          if (r.char && 1 < r.char.length) return r.char;
          if (r.which) return String.fromCharCode(r.which);
        }
        return null;
      case "compositionend":
        return Yv && r.locale !== "ko" ? null : r.data;
      default:
        return null;
    }
  }
  var Zy = { color: !0, date: !0, datetime: !0, "datetime-local": !0, email: !0, month: !0, number: !0, password: !0, range: !0, search: !0, tel: !0, text: !0, time: !0, url: !0, week: !0 };
  function qv(n) {
    var r = n && n.nodeName && n.nodeName.toLowerCase();
    return r === "input" ? !!Zy[n.type] : r === "textarea";
  }
  function Ld(n, r, l, o) {
    Ki(o), r = bs(r, "onChange"), 0 < r.length && (l = new Ct("onChange", "change", null, l, o), n.push({ event: l, listeners: r }));
  }
  var Ni = null, Ru = null;
  function Xv(n) {
    ku(n, 0);
  }
  function _s(n) {
    var r = vi(n);
    if (Ur(r)) return n;
  }
  function Gy(n, r) {
    if (n === "change") return r;
  }
  var Kv = !1;
  if (A) {
    var Ad;
    if (A) {
      var zd = "oninput" in document;
      if (!zd) {
        var Jv = document.createElement("div");
        Jv.setAttribute("oninput", "return;"), zd = typeof Jv.oninput == "function";
      }
      Ad = zd;
    } else Ad = !1;
    Kv = Ad && (!document.documentMode || 9 < document.documentMode);
  }
  function eh() {
    Ni && (Ni.detachEvent("onpropertychange", th), Ru = Ni = null);
  }
  function th(n) {
    if (n.propertyName === "value" && _s(Ru)) {
      var r = [];
      Ld(r, Ru, n, Kt(n)), yu(Xv, r);
    }
  }
  function qy(n, r, l) {
    n === "focusin" ? (eh(), Ni = r, Ru = l, Ni.attachEvent("onpropertychange", th)) : n === "focusout" && eh();
  }
  function nh(n) {
    if (n === "selectionchange" || n === "keyup" || n === "keydown") return _s(Ru);
  }
  function Xy(n, r) {
    if (n === "click") return _s(r);
  }
  function rh(n, r) {
    if (n === "input" || n === "change") return _s(r);
  }
  function Ky(n, r) {
    return n === r && (n !== 0 || 1 / n === 1 / r) || n !== n && r !== r;
  }
  var pi = typeof Object.is == "function" ? Object.is : Ky;
  function xs(n, r) {
    if (pi(n, r)) return !0;
    if (typeof n != "object" || n === null || typeof r != "object" || r === null) return !1;
    var l = Object.keys(n), o = Object.keys(r);
    if (l.length !== o.length) return !1;
    for (o = 0; o < l.length; o++) {
      var f = l[o];
      if (!I.call(r, f) || !pi(n[f], r[f])) return !1;
    }
    return !0;
  }
  function ah(n) {
    for (; n && n.firstChild; ) n = n.firstChild;
    return n;
  }
  function jc(n, r) {
    var l = ah(n);
    n = 0;
    for (var o; l; ) {
      if (l.nodeType === 3) {
        if (o = n + l.textContent.length, n <= r && o >= r) return { node: l, offset: r - n };
        n = o;
      }
      e: {
        for (; l; ) {
          if (l.nextSibling) {
            l = l.nextSibling;
            break e;
          }
          l = l.parentNode;
        }
        l = void 0;
      }
      l = ah(l);
    }
  }
  function Ul(n, r) {
    return n && r ? n === r ? !0 : n && n.nodeType === 3 ? !1 : r && r.nodeType === 3 ? Ul(n, r.parentNode) : "contains" in n ? n.contains(r) : n.compareDocumentPosition ? !!(n.compareDocumentPosition(r) & 16) : !1 : !1;
  }
  function Ts() {
    for (var n = window, r = On(); r instanceof n.HTMLIFrameElement; ) {
      try {
        var l = typeof r.contentWindow.location.href == "string";
      } catch {
        l = !1;
      }
      if (l) n = r.contentWindow;
      else break;
      r = On(n.document);
    }
    return r;
  }
  function Fc(n) {
    var r = n && n.nodeName && n.nodeName.toLowerCase();
    return r && (r === "input" && (n.type === "text" || n.type === "search" || n.type === "tel" || n.type === "url" || n.type === "password") || r === "textarea" || n.contentEditable === "true");
  }
  function bo(n) {
    var r = Ts(), l = n.focusedElem, o = n.selectionRange;
    if (r !== l && l && l.ownerDocument && Ul(l.ownerDocument.documentElement, l)) {
      if (o !== null && Fc(l)) {
        if (r = o.start, n = o.end, n === void 0 && (n = r), "selectionStart" in l) l.selectionStart = r, l.selectionEnd = Math.min(n, l.value.length);
        else if (n = (r = l.ownerDocument || document) && r.defaultView || window, n.getSelection) {
          n = n.getSelection();
          var f = l.textContent.length, v = Math.min(o.start, f);
          o = o.end === void 0 ? v : Math.min(o.end, f), !n.extend && v > o && (f = o, o = v, v = f), f = jc(l, v);
          var C = jc(
            l,
            o
          );
          f && C && (n.rangeCount !== 1 || n.anchorNode !== f.node || n.anchorOffset !== f.offset || n.focusNode !== C.node || n.focusOffset !== C.offset) && (r = r.createRange(), r.setStart(f.node, f.offset), n.removeAllRanges(), v > o ? (n.addRange(r), n.extend(C.node, C.offset)) : (r.setEnd(C.node, C.offset), n.addRange(r)));
        }
      }
      for (r = [], n = l; n = n.parentNode; ) n.nodeType === 1 && r.push({ element: n, left: n.scrollLeft, top: n.scrollTop });
      for (typeof l.focus == "function" && l.focus(), l = 0; l < r.length; l++) n = r[l], n.element.scrollLeft = n.left, n.element.scrollTop = n.top;
    }
  }
  var Jy = A && "documentMode" in document && 11 >= document.documentMode, ko = null, Ud = null, Rs = null, jd = !1;
  function Fd(n, r, l) {
    var o = l.window === l ? l.document : l.nodeType === 9 ? l : l.ownerDocument;
    jd || ko == null || ko !== On(o) || (o = ko, "selectionStart" in o && Fc(o) ? o = { start: o.selectionStart, end: o.selectionEnd } : (o = (o.ownerDocument && o.ownerDocument.defaultView || window).getSelection(), o = { anchorNode: o.anchorNode, anchorOffset: o.anchorOffset, focusNode: o.focusNode, focusOffset: o.focusOffset }), Rs && xs(Rs, o) || (Rs = o, o = bs(Ud, "onSelect"), 0 < o.length && (r = new Ct("onSelect", "select", null, r, l), n.push({ event: r, listeners: o }), r.target = ko)));
  }
  function Hc(n, r) {
    var l = {};
    return l[n.toLowerCase()] = r.toLowerCase(), l["Webkit" + n] = "webkit" + r, l["Moz" + n] = "moz" + r, l;
  }
  var wu = { animationend: Hc("Animation", "AnimationEnd"), animationiteration: Hc("Animation", "AnimationIteration"), animationstart: Hc("Animation", "AnimationStart"), transitionend: Hc("Transition", "TransitionEnd") }, yr = {}, Hd = {};
  A && (Hd = document.createElement("div").style, "AnimationEvent" in window || (delete wu.animationend.animation, delete wu.animationiteration.animation, delete wu.animationstart.animation), "TransitionEvent" in window || delete wu.transitionend.transition);
  function Vc(n) {
    if (yr[n]) return yr[n];
    if (!wu[n]) return n;
    var r = wu[n], l;
    for (l in r) if (r.hasOwnProperty(l) && l in Hd) return yr[n] = r[l];
    return n;
  }
  var ih = Vc("animationend"), lh = Vc("animationiteration"), uh = Vc("animationstart"), oh = Vc("transitionend"), Vd = /* @__PURE__ */ new Map(), Pc = "abort auxClick cancel canPlay canPlayThrough click close contextMenu copy cut drag dragEnd dragEnter dragExit dragLeave dragOver dragStart drop durationChange emptied encrypted ended error gotPointerCapture input invalid keyDown keyPress keyUp load loadedData loadedMetadata loadStart lostPointerCapture mouseDown mouseMove mouseOut mouseOver mouseUp paste pause play playing pointerCancel pointerDown pointerMove pointerOut pointerOver pointerUp progress rateChange reset resize seeked seeking stalled submit suspend timeUpdate touchCancel touchEnd touchStart volumeChange scroll toggle touchMove waiting wheel".split(" ");
  function Ha(n, r) {
    Vd.set(n, r), T(r, [n]);
  }
  for (var Pd = 0; Pd < Pc.length; Pd++) {
    var bu = Pc[Pd], eg = bu.toLowerCase(), tg = bu[0].toUpperCase() + bu.slice(1);
    Ha(eg, "on" + tg);
  }
  Ha(ih, "onAnimationEnd"), Ha(lh, "onAnimationIteration"), Ha(uh, "onAnimationStart"), Ha("dblclick", "onDoubleClick"), Ha("focusin", "onFocus"), Ha("focusout", "onBlur"), Ha(oh, "onTransitionEnd"), E("onMouseEnter", ["mouseout", "mouseover"]), E("onMouseLeave", ["mouseout", "mouseover"]), E("onPointerEnter", ["pointerout", "pointerover"]), E("onPointerLeave", ["pointerout", "pointerover"]), T("onChange", "change click focusin focusout input keydown keyup selectionchange".split(" ")), T("onSelect", "focusout contextmenu dragend focusin keydown keyup mousedown mouseup selectionchange".split(" ")), T("onBeforeInput", ["compositionend", "keypress", "textInput", "paste"]), T("onCompositionEnd", "compositionend focusout keydown keypress keyup mousedown".split(" ")), T("onCompositionStart", "compositionstart focusout keydown keypress keyup mousedown".split(" ")), T("onCompositionUpdate", "compositionupdate focusout keydown keypress keyup mousedown".split(" "));
  var ws = "abort canplay canplaythrough durationchange emptied encrypted ended error loadeddata loadedmetadata loadstart pause play playing progress ratechange resize seeked seeking stalled suspend timeupdate volumechange waiting".split(" "), Bd = new Set("cancel close invalid load scroll toggle".split(" ").concat(ws));
  function Bc(n, r, l) {
    var o = n.type || "unknown-event";
    n.currentTarget = l, _e(o, r, void 0, n), n.currentTarget = null;
  }
  function ku(n, r) {
    r = (r & 4) !== 0;
    for (var l = 0; l < n.length; l++) {
      var o = n[l], f = o.event;
      o = o.listeners;
      e: {
        var v = void 0;
        if (r) for (var C = o.length - 1; 0 <= C; C--) {
          var w = o[C], D = w.instance, P = w.currentTarget;
          if (w = w.listener, D !== v && f.isPropagationStopped()) break e;
          Bc(f, w, P), v = D;
        }
        else for (C = 0; C < o.length; C++) {
          if (w = o[C], D = w.instance, P = w.currentTarget, w = w.listener, D !== v && f.isPropagationStopped()) break e;
          Bc(f, w, P), v = D;
        }
      }
    }
    if (wi) throw n = k, wi = !1, k = null, n;
  }
  function Gt(n, r) {
    var l = r[Os];
    l === void 0 && (l = r[Os] = /* @__PURE__ */ new Set());
    var o = n + "__bubble";
    l.has(o) || (sh(r, n, 2, !1), l.add(o));
  }
  function Ic(n, r, l) {
    var o = 0;
    r && (o |= 4), sh(l, n, o, r);
  }
  var $c = "_reactListening" + Math.random().toString(36).slice(2);
  function Do(n) {
    if (!n[$c]) {
      n[$c] = !0, S.forEach(function(l) {
        l !== "selectionchange" && (Bd.has(l) || Ic(l, !1, n), Ic(l, !0, n));
      });
      var r = n.nodeType === 9 ? n : n.ownerDocument;
      r === null || r[$c] || (r[$c] = !0, Ic("selectionchange", !1, r));
    }
  }
  function sh(n, r, l, o) {
    switch (To(r)) {
      case 1:
        var f = Co;
        break;
      case 4:
        f = _o;
        break;
      default:
        f = Al;
    }
    l = f.bind(null, r, l, n), f = void 0, !Fr || r !== "touchstart" && r !== "touchmove" && r !== "wheel" || (f = !0), o ? f !== void 0 ? n.addEventListener(r, l, { capture: !0, passive: f }) : n.addEventListener(r, l, !0) : f !== void 0 ? n.addEventListener(r, l, { passive: f }) : n.addEventListener(r, l, !1);
  }
  function Yc(n, r, l, o, f) {
    var v = o;
    if (!(r & 1) && !(r & 2) && o !== null) e: for (; ; ) {
      if (o === null) return;
      var C = o.tag;
      if (C === 3 || C === 4) {
        var w = o.stateNode.containerInfo;
        if (w === f || w.nodeType === 8 && w.parentNode === f) break;
        if (C === 4) for (C = o.return; C !== null; ) {
          var D = C.tag;
          if ((D === 3 || D === 4) && (D = C.stateNode.containerInfo, D === f || D.nodeType === 8 && D.parentNode === f)) return;
          C = C.return;
        }
        for (; w !== null; ) {
          if (C = Ou(w), C === null) return;
          if (D = C.tag, D === 5 || D === 6) {
            o = v = C;
            continue e;
          }
          w = w.parentNode;
        }
      }
      o = o.return;
    }
    yu(function() {
      var P = v, J = Kt(l), te = [];
      e: {
        var K = Vd.get(n);
        if (K !== void 0) {
          var ge = Ct, Te = n;
          switch (n) {
            case "keypress":
              if (Y(l) === 0) break e;
            case "keydown":
            case "keyup":
              ge = Od;
              break;
            case "focusin":
              Te = "focus", ge = Tu;
              break;
            case "focusout":
              Te = "blur", ge = Tu;
              break;
            case "beforeblur":
            case "afterblur":
              ge = Tu;
              break;
            case "click":
              if (l.button === 2) break e;
            case "auxclick":
            case "dblclick":
            case "mousedown":
            case "mousemove":
            case "mouseup":
            case "mouseout":
            case "mouseover":
            case "contextmenu":
              ge = zl;
              break;
            case "drag":
            case "dragend":
            case "dragenter":
            case "dragexit":
            case "dragleave":
            case "dragover":
            case "dragstart":
            case "drop":
              ge = tl;
              break;
            case "touchcancel":
            case "touchend":
            case "touchmove":
            case "touchstart":
              ge = $v;
              break;
            case ih:
            case lh:
            case uh:
              ge = Ac;
              break;
            case oh:
              ge = rl;
              break;
            case "scroll":
              ge = dn;
              break;
            case "wheel":
              ge = al;
              break;
            case "copy":
            case "cut":
            case "paste":
              ge = Vv;
              break;
            case "gotpointercapture":
            case "lostpointercapture":
            case "pointercancel":
            case "pointerdown":
            case "pointermove":
            case "pointerout":
            case "pointerover":
            case "pointerup":
              ge = Iv;
          }
          var ke = (r & 4) !== 0, jn = !ke && n === "scroll", z = ke ? K !== null ? K + "Capture" : null : K;
          ke = [];
          for (var N = P, F; N !== null; ) {
            F = N;
            var ee = F.stateNode;
            if (F.tag === 5 && ee !== null && (F = ee, z !== null && (ee = jr(N, z), ee != null && ke.push(Oo(N, ee, F)))), jn) break;
            N = N.return;
          }
          0 < ke.length && (K = new ge(K, Te, null, l, J), te.push({ event: K, listeners: ke }));
        }
      }
      if (!(r & 7)) {
        e: {
          if (K = n === "mouseover" || n === "pointerover", ge = n === "mouseout" || n === "pointerout", K && l !== cn && (Te = l.relatedTarget || l.fromElement) && (Ou(Te) || Te[il])) break e;
          if ((ge || K) && (K = J.window === J ? J : (K = J.ownerDocument) ? K.defaultView || K.parentWindow : window, ge ? (Te = l.relatedTarget || l.toElement, ge = P, Te = Te ? Ou(Te) : null, Te !== null && (jn = it(Te), Te !== jn || Te.tag !== 5 && Te.tag !== 6) && (Te = null)) : (ge = null, Te = P), ge !== Te)) {
            if (ke = zl, ee = "onMouseLeave", z = "onMouseEnter", N = "mouse", (n === "pointerout" || n === "pointerover") && (ke = Iv, ee = "onPointerLeave", z = "onPointerEnter", N = "pointer"), jn = ge == null ? K : vi(ge), F = Te == null ? K : vi(Te), K = new ke(ee, N + "leave", ge, l, J), K.target = jn, K.relatedTarget = F, ee = null, Ou(J) === P && (ke = new ke(z, N + "enter", Te, l, J), ke.target = F, ke.relatedTarget = jn, ee = ke), jn = ee, ge && Te) t: {
              for (ke = ge, z = Te, N = 0, F = ke; F; F = jl(F)) N++;
              for (F = 0, ee = z; ee; ee = jl(ee)) F++;
              for (; 0 < N - F; ) ke = jl(ke), N--;
              for (; 0 < F - N; ) z = jl(z), F--;
              for (; N--; ) {
                if (ke === z || z !== null && ke === z.alternate) break t;
                ke = jl(ke), z = jl(z);
              }
              ke = null;
            }
            else ke = null;
            ge !== null && ch(te, K, ge, ke, !1), Te !== null && jn !== null && ch(te, jn, Te, ke, !0);
          }
        }
        e: {
          if (K = P ? vi(P) : window, ge = K.nodeName && K.nodeName.toLowerCase(), ge === "select" || ge === "input" && K.type === "file") var Re = Gy;
          else if (qv(K)) if (Kv) Re = rh;
          else {
            Re = nh;
            var Be = qy;
          }
          else (ge = K.nodeName) && ge.toLowerCase() === "input" && (K.type === "checkbox" || K.type === "radio") && (Re = Xy);
          if (Re && (Re = Re(n, P))) {
            Ld(te, Re, l, J);
            break e;
          }
          Be && Be(n, K, P), n === "focusout" && (Be = K._wrapperState) && Be.controlled && K.type === "number" && Sa(K, "number", K.value);
        }
        switch (Be = P ? vi(P) : window, n) {
          case "focusin":
            (qv(Be) || Be.contentEditable === "true") && (ko = Be, Ud = P, Rs = null);
            break;
          case "focusout":
            Rs = Ud = ko = null;
            break;
          case "mousedown":
            jd = !0;
            break;
          case "contextmenu":
          case "mouseup":
          case "dragend":
            jd = !1, Fd(te, l, J);
            break;
          case "selectionchange":
            if (Jy) break;
          case "keydown":
          case "keyup":
            Fd(te, l, J);
        }
        var $e;
        if (Ro) e: {
          switch (n) {
            case "compositionstart":
              var qe = "onCompositionStart";
              break e;
            case "compositionend":
              qe = "onCompositionEnd";
              break e;
            case "compositionupdate":
              qe = "onCompositionUpdate";
              break e;
          }
          qe = void 0;
        }
        else wo ? Qv(n, l) && (qe = "onCompositionEnd") : n === "keydown" && l.keyCode === 229 && (qe = "onCompositionStart");
        qe && (Yv && l.locale !== "ko" && (wo || qe !== "onCompositionStart" ? qe === "onCompositionEnd" && wo && ($e = V()) : (di = J, g = "value" in di ? di.value : di.textContent, wo = !0)), Be = bs(P, qe), 0 < Be.length && (qe = new bd(qe, n, null, l, J), te.push({ event: qe, listeners: Be }), $e ? qe.data = $e : ($e = Zv(l), $e !== null && (qe.data = $e)))), ($e = Cs ? Gv(n, l) : Qy(n, l)) && (P = bs(P, "onBeforeInput"), 0 < P.length && (J = new bd("onBeforeInput", "beforeinput", null, l, J), te.push({ event: J, listeners: P }), J.data = $e));
      }
      ku(te, r);
    });
  }
  function Oo(n, r, l) {
    return { instance: n, listener: r, currentTarget: l };
  }
  function bs(n, r) {
    for (var l = r + "Capture", o = []; n !== null; ) {
      var f = n, v = f.stateNode;
      f.tag === 5 && v !== null && (f = v, v = jr(n, l), v != null && o.unshift(Oo(n, v, f)), v = jr(n, r), v != null && o.push(Oo(n, v, f))), n = n.return;
    }
    return o;
  }
  function jl(n) {
    if (n === null) return null;
    do
      n = n.return;
    while (n && n.tag !== 5);
    return n || null;
  }
  function ch(n, r, l, o, f) {
    for (var v = r._reactName, C = []; l !== null && l !== o; ) {
      var w = l, D = w.alternate, P = w.stateNode;
      if (D !== null && D === o) break;
      w.tag === 5 && P !== null && (w = P, f ? (D = jr(l, v), D != null && C.unshift(Oo(l, D, w))) : f || (D = jr(l, v), D != null && C.push(Oo(l, D, w)))), l = l.return;
    }
    C.length !== 0 && n.push({ event: r, listeners: C });
  }
  var fh = /\r\n?/g, ng = /\u0000|\uFFFD/g;
  function dh(n) {
    return (typeof n == "string" ? n : "" + n).replace(fh, `
`).replace(ng, "");
  }
  function Wc(n, r, l) {
    if (r = dh(r), dh(n) !== r && l) throw Error(p(425));
  }
  function Fl() {
  }
  var ks = null, Du = null;
  function Qc(n, r) {
    return n === "textarea" || n === "noscript" || typeof r.children == "string" || typeof r.children == "number" || typeof r.dangerouslySetInnerHTML == "object" && r.dangerouslySetInnerHTML !== null && r.dangerouslySetInnerHTML.__html != null;
  }
  var Zc = typeof setTimeout == "function" ? setTimeout : void 0, Id = typeof clearTimeout == "function" ? clearTimeout : void 0, ph = typeof Promise == "function" ? Promise : void 0, No = typeof queueMicrotask == "function" ? queueMicrotask : typeof ph < "u" ? function(n) {
    return ph.resolve(null).then(n).catch(Gc);
  } : Zc;
  function Gc(n) {
    setTimeout(function() {
      throw n;
    });
  }
  function Mo(n, r) {
    var l = r, o = 0;
    do {
      var f = l.nextSibling;
      if (n.removeChild(l), f && f.nodeType === 8) if (l = f.data, l === "/$") {
        if (o === 0) {
          n.removeChild(f), fi(r);
          return;
        }
        o--;
      } else l !== "$" && l !== "$?" && l !== "$!" || o++;
      l = f;
    } while (l);
    fi(r);
  }
  function Mi(n) {
    for (; n != null; n = n.nextSibling) {
      var r = n.nodeType;
      if (r === 1 || r === 3) break;
      if (r === 8) {
        if (r = n.data, r === "$" || r === "$!" || r === "$?") break;
        if (r === "/$") return null;
      }
    }
    return n;
  }
  function vh(n) {
    n = n.previousSibling;
    for (var r = 0; n; ) {
      if (n.nodeType === 8) {
        var l = n.data;
        if (l === "$" || l === "$!" || l === "$?") {
          if (r === 0) return n;
          r--;
        } else l === "/$" && r++;
      }
      n = n.previousSibling;
    }
    return null;
  }
  var Hl = Math.random().toString(36).slice(2), Li = "__reactFiber$" + Hl, Ds = "__reactProps$" + Hl, il = "__reactContainer$" + Hl, Os = "__reactEvents$" + Hl, Lo = "__reactListeners$" + Hl, rg = "__reactHandles$" + Hl;
  function Ou(n) {
    var r = n[Li];
    if (r) return r;
    for (var l = n.parentNode; l; ) {
      if (r = l[il] || l[Li]) {
        if (l = r.alternate, r.child !== null || l !== null && l.child !== null) for (n = vh(n); n !== null; ) {
          if (l = n[Li]) return l;
          n = vh(n);
        }
        return r;
      }
      n = l, l = n.parentNode;
    }
    return null;
  }
  function Ue(n) {
    return n = n[Li] || n[il], !n || n.tag !== 5 && n.tag !== 6 && n.tag !== 13 && n.tag !== 3 ? null : n;
  }
  function vi(n) {
    if (n.tag === 5 || n.tag === 6) return n.stateNode;
    throw Error(p(33));
  }
  function Rn(n) {
    return n[Ds] || null;
  }
  var kt = [], Va = -1;
  function Pa(n) {
    return { current: n };
  }
  function pn(n) {
    0 > Va || (n.current = kt[Va], kt[Va] = null, Va--);
  }
  function Le(n, r) {
    Va++, kt[Va] = n.current, n.current = r;
  }
  var Mr = {}, Dn = Pa(Mr), er = Pa(!1), aa = Mr;
  function ia(n, r) {
    var l = n.type.contextTypes;
    if (!l) return Mr;
    var o = n.stateNode;
    if (o && o.__reactInternalMemoizedUnmaskedChildContext === r) return o.__reactInternalMemoizedMaskedChildContext;
    var f = {}, v;
    for (v in l) f[v] = r[v];
    return o && (n = n.stateNode, n.__reactInternalMemoizedUnmaskedChildContext = r, n.__reactInternalMemoizedMaskedChildContext = f), f;
  }
  function Bn(n) {
    return n = n.childContextTypes, n != null;
  }
  function Ao() {
    pn(er), pn(Dn);
  }
  function hh(n, r, l) {
    if (Dn.current !== Mr) throw Error(p(168));
    Le(Dn, r), Le(er, l);
  }
  function Ns(n, r, l) {
    var o = n.stateNode;
    if (r = r.childContextTypes, typeof o.getChildContext != "function") return l;
    o = o.getChildContext();
    for (var f in o) if (!(f in r)) throw Error(p(108, ot(n) || "Unknown", f));
    return se({}, l, o);
  }
  function ur(n) {
    return n = (n = n.stateNode) && n.__reactInternalMemoizedMergedChildContext || Mr, aa = Dn.current, Le(Dn, n), Le(er, er.current), !0;
  }
  function qc(n, r, l) {
    var o = n.stateNode;
    if (!o) throw Error(p(169));
    l ? (n = Ns(n, r, aa), o.__reactInternalMemoizedMergedChildContext = n, pn(er), pn(Dn), Le(Dn, n)) : pn(er), Le(er, l);
  }
  var Ai = null, zo = !1, ll = !1;
  function Xc(n) {
    Ai === null ? Ai = [n] : Ai.push(n);
  }
  function Vl(n) {
    zo = !0, Xc(n);
  }
  function zi() {
    if (!ll && Ai !== null) {
      ll = !0;
      var n = 0, r = Ht;
      try {
        var l = Ai;
        for (Ht = 1; n < l.length; n++) {
          var o = l[n];
          do
            o = o(!0);
          while (o !== null);
        }
        Ai = null, zo = !1;
      } catch (f) {
        throw Ai !== null && (Ai = Ai.slice(n + 1)), yn(oi, zi), f;
      } finally {
        Ht = r, ll = !1;
      }
    }
    return null;
  }
  var Pl = [], Bl = 0, Il = null, ul = 0, In = [], Ba = 0, xa = null, Ui = 1, ji = "";
  function Nu(n, r) {
    Pl[Bl++] = ul, Pl[Bl++] = Il, Il = n, ul = r;
  }
  function mh(n, r, l) {
    In[Ba++] = Ui, In[Ba++] = ji, In[Ba++] = xa, xa = n;
    var o = Ui;
    n = ji;
    var f = 32 - Hr(o) - 1;
    o &= ~(1 << f), l += 1;
    var v = 32 - Hr(r) + f;
    if (30 < v) {
      var C = f - f % 5;
      v = (o & (1 << C) - 1).toString(32), o >>= C, f -= C, Ui = 1 << 32 - Hr(r) + f | l << f | o, ji = v + n;
    } else Ui = 1 << v | l << f | o, ji = n;
  }
  function Kc(n) {
    n.return !== null && (Nu(n, 1), mh(n, 1, 0));
  }
  function Jc(n) {
    for (; n === Il; ) Il = Pl[--Bl], Pl[Bl] = null, ul = Pl[--Bl], Pl[Bl] = null;
    for (; n === xa; ) xa = In[--Ba], In[Ba] = null, ji = In[--Ba], In[Ba] = null, Ui = In[--Ba], In[Ba] = null;
  }
  var la = null, ua = null, Cn = !1, Ia = null;
  function $d(n, r) {
    var l = Za(5, null, null, 0);
    l.elementType = "DELETED", l.stateNode = r, l.return = n, r = n.deletions, r === null ? (n.deletions = [l], n.flags |= 16) : r.push(l);
  }
  function yh(n, r) {
    switch (n.tag) {
      case 5:
        var l = n.type;
        return r = r.nodeType !== 1 || l.toLowerCase() !== r.nodeName.toLowerCase() ? null : r, r !== null ? (n.stateNode = r, la = n, ua = Mi(r.firstChild), !0) : !1;
      case 6:
        return r = n.pendingProps === "" || r.nodeType !== 3 ? null : r, r !== null ? (n.stateNode = r, la = n, ua = null, !0) : !1;
      case 13:
        return r = r.nodeType !== 8 ? null : r, r !== null ? (l = xa !== null ? { id: Ui, overflow: ji } : null, n.memoizedState = { dehydrated: r, treeContext: l, retryLane: 1073741824 }, l = Za(18, null, null, 0), l.stateNode = r, l.return = n, n.child = l, la = n, ua = null, !0) : !1;
      default:
        return !1;
    }
  }
  function Yd(n) {
    return (n.mode & 1) !== 0 && (n.flags & 128) === 0;
  }
  function Wd(n) {
    if (Cn) {
      var r = ua;
      if (r) {
        var l = r;
        if (!yh(n, r)) {
          if (Yd(n)) throw Error(p(418));
          r = Mi(l.nextSibling);
          var o = la;
          r && yh(n, r) ? $d(o, l) : (n.flags = n.flags & -4097 | 2, Cn = !1, la = n);
        }
      } else {
        if (Yd(n)) throw Error(p(418));
        n.flags = n.flags & -4097 | 2, Cn = !1, la = n;
      }
    }
  }
  function tr(n) {
    for (n = n.return; n !== null && n.tag !== 5 && n.tag !== 3 && n.tag !== 13; ) n = n.return;
    la = n;
  }
  function ef(n) {
    if (n !== la) return !1;
    if (!Cn) return tr(n), Cn = !0, !1;
    var r;
    if ((r = n.tag !== 3) && !(r = n.tag !== 5) && (r = n.type, r = r !== "head" && r !== "body" && !Qc(n.type, n.memoizedProps)), r && (r = ua)) {
      if (Yd(n)) throw Ms(), Error(p(418));
      for (; r; ) $d(n, r), r = Mi(r.nextSibling);
    }
    if (tr(n), n.tag === 13) {
      if (n = n.memoizedState, n = n !== null ? n.dehydrated : null, !n) throw Error(p(317));
      e: {
        for (n = n.nextSibling, r = 0; n; ) {
          if (n.nodeType === 8) {
            var l = n.data;
            if (l === "/$") {
              if (r === 0) {
                ua = Mi(n.nextSibling);
                break e;
              }
              r--;
            } else l !== "$" && l !== "$!" && l !== "$?" || r++;
          }
          n = n.nextSibling;
        }
        ua = null;
      }
    } else ua = la ? Mi(n.stateNode.nextSibling) : null;
    return !0;
  }
  function Ms() {
    for (var n = ua; n; ) n = Mi(n.nextSibling);
  }
  function $l() {
    ua = la = null, Cn = !1;
  }
  function ol(n) {
    Ia === null ? Ia = [n] : Ia.push(n);
  }
  var ag = Tt.ReactCurrentBatchConfig;
  function Mu(n, r, l) {
    if (n = l.ref, n !== null && typeof n != "function" && typeof n != "object") {
      if (l._owner) {
        if (l = l._owner, l) {
          if (l.tag !== 1) throw Error(p(309));
          var o = l.stateNode;
        }
        if (!o) throw Error(p(147, n));
        var f = o, v = "" + n;
        return r !== null && r.ref !== null && typeof r.ref == "function" && r.ref._stringRef === v ? r.ref : (r = function(C) {
          var w = f.refs;
          C === null ? delete w[v] : w[v] = C;
        }, r._stringRef = v, r);
      }
      if (typeof n != "string") throw Error(p(284));
      if (!l._owner) throw Error(p(290, n));
    }
    return n;
  }
  function tf(n, r) {
    throw n = Object.prototype.toString.call(r), Error(p(31, n === "[object Object]" ? "object with keys {" + Object.keys(r).join(", ") + "}" : n));
  }
  function gh(n) {
    var r = n._init;
    return r(n._payload);
  }
  function Lu(n) {
    function r(z, N) {
      if (n) {
        var F = z.deletions;
        F === null ? (z.deletions = [N], z.flags |= 16) : F.push(N);
      }
    }
    function l(z, N) {
      if (!n) return null;
      for (; N !== null; ) r(z, N), N = N.sibling;
      return null;
    }
    function o(z, N) {
      for (z = /* @__PURE__ */ new Map(); N !== null; ) N.key !== null ? z.set(N.key, N) : z.set(N.index, N), N = N.sibling;
      return z;
    }
    function f(z, N) {
      return z = Kl(z, N), z.index = 0, z.sibling = null, z;
    }
    function v(z, N, F) {
      return z.index = F, n ? (F = z.alternate, F !== null ? (F = F.index, F < N ? (z.flags |= 2, N) : F) : (z.flags |= 2, N)) : (z.flags |= 1048576, N);
    }
    function C(z) {
      return n && z.alternate === null && (z.flags |= 2), z;
    }
    function w(z, N, F, ee) {
      return N === null || N.tag !== 6 ? (N = xp(F, z.mode, ee), N.return = z, N) : (N = f(N, F), N.return = z, N);
    }
    function D(z, N, F, ee) {
      var Re = F.type;
      return Re === Qe ? J(z, N, F.props.children, ee, F.key) : N !== null && (N.elementType === Re || typeof Re == "object" && Re !== null && Re.$$typeof === Ft && gh(Re) === N.type) ? (ee = f(N, F.props), ee.ref = Mu(z, N, F), ee.return = z, ee) : (ee = oc(F.type, F.key, F.props, null, z.mode, ee), ee.ref = Mu(z, N, F), ee.return = z, ee);
    }
    function P(z, N, F, ee) {
      return N === null || N.tag !== 4 || N.stateNode.containerInfo !== F.containerInfo || N.stateNode.implementation !== F.implementation ? (N = zf(F, z.mode, ee), N.return = z, N) : (N = f(N, F.children || []), N.return = z, N);
    }
    function J(z, N, F, ee, Re) {
      return N === null || N.tag !== 7 ? (N = vl(F, z.mode, ee, Re), N.return = z, N) : (N = f(N, F), N.return = z, N);
    }
    function te(z, N, F) {
      if (typeof N == "string" && N !== "" || typeof N == "number") return N = xp("" + N, z.mode, F), N.return = z, N;
      if (typeof N == "object" && N !== null) {
        switch (N.$$typeof) {
          case ze:
            return F = oc(N.type, N.key, N.props, null, z.mode, F), F.ref = Mu(z, null, N), F.return = z, F;
          case St:
            return N = zf(N, z.mode, F), N.return = z, N;
          case Ft:
            var ee = N._init;
            return te(z, ee(N._payload), F);
        }
        if (ir(N) || Oe(N)) return N = vl(N, z.mode, F, null), N.return = z, N;
        tf(z, N);
      }
      return null;
    }
    function K(z, N, F, ee) {
      var Re = N !== null ? N.key : null;
      if (typeof F == "string" && F !== "" || typeof F == "number") return Re !== null ? null : w(z, N, "" + F, ee);
      if (typeof F == "object" && F !== null) {
        switch (F.$$typeof) {
          case ze:
            return F.key === Re ? D(z, N, F, ee) : null;
          case St:
            return F.key === Re ? P(z, N, F, ee) : null;
          case Ft:
            return Re = F._init, K(
              z,
              N,
              Re(F._payload),
              ee
            );
        }
        if (ir(F) || Oe(F)) return Re !== null ? null : J(z, N, F, ee, null);
        tf(z, F);
      }
      return null;
    }
    function ge(z, N, F, ee, Re) {
      if (typeof ee == "string" && ee !== "" || typeof ee == "number") return z = z.get(F) || null, w(N, z, "" + ee, Re);
      if (typeof ee == "object" && ee !== null) {
        switch (ee.$$typeof) {
          case ze:
            return z = z.get(ee.key === null ? F : ee.key) || null, D(N, z, ee, Re);
          case St:
            return z = z.get(ee.key === null ? F : ee.key) || null, P(N, z, ee, Re);
          case Ft:
            var Be = ee._init;
            return ge(z, N, F, Be(ee._payload), Re);
        }
        if (ir(ee) || Oe(ee)) return z = z.get(F) || null, J(N, z, ee, Re, null);
        tf(N, ee);
      }
      return null;
    }
    function Te(z, N, F, ee) {
      for (var Re = null, Be = null, $e = N, qe = N = 0, cr = null; $e !== null && qe < F.length; qe++) {
        $e.index > qe ? (cr = $e, $e = null) : cr = $e.sibling;
        var Bt = K(z, $e, F[qe], ee);
        if (Bt === null) {
          $e === null && ($e = cr);
          break;
        }
        n && $e && Bt.alternate === null && r(z, $e), N = v(Bt, N, qe), Be === null ? Re = Bt : Be.sibling = Bt, Be = Bt, $e = cr;
      }
      if (qe === F.length) return l(z, $e), Cn && Nu(z, qe), Re;
      if ($e === null) {
        for (; qe < F.length; qe++) $e = te(z, F[qe], ee), $e !== null && (N = v($e, N, qe), Be === null ? Re = $e : Be.sibling = $e, Be = $e);
        return Cn && Nu(z, qe), Re;
      }
      for ($e = o(z, $e); qe < F.length; qe++) cr = ge($e, z, qe, F[qe], ee), cr !== null && (n && cr.alternate !== null && $e.delete(cr.key === null ? qe : cr.key), N = v(cr, N, qe), Be === null ? Re = cr : Be.sibling = cr, Be = cr);
      return n && $e.forEach(function(tu) {
        return r(z, tu);
      }), Cn && Nu(z, qe), Re;
    }
    function ke(z, N, F, ee) {
      var Re = Oe(F);
      if (typeof Re != "function") throw Error(p(150));
      if (F = Re.call(F), F == null) throw Error(p(151));
      for (var Be = Re = null, $e = N, qe = N = 0, cr = null, Bt = F.next(); $e !== null && !Bt.done; qe++, Bt = F.next()) {
        $e.index > qe ? (cr = $e, $e = null) : cr = $e.sibling;
        var tu = K(z, $e, Bt.value, ee);
        if (tu === null) {
          $e === null && ($e = cr);
          break;
        }
        n && $e && tu.alternate === null && r(z, $e), N = v(tu, N, qe), Be === null ? Re = tu : Be.sibling = tu, Be = tu, $e = cr;
      }
      if (Bt.done) return l(
        z,
        $e
      ), Cn && Nu(z, qe), Re;
      if ($e === null) {
        for (; !Bt.done; qe++, Bt = F.next()) Bt = te(z, Bt.value, ee), Bt !== null && (N = v(Bt, N, qe), Be === null ? Re = Bt : Be.sibling = Bt, Be = Bt);
        return Cn && Nu(z, qe), Re;
      }
      for ($e = o(z, $e); !Bt.done; qe++, Bt = F.next()) Bt = ge($e, z, qe, Bt.value, ee), Bt !== null && (n && Bt.alternate !== null && $e.delete(Bt.key === null ? qe : Bt.key), N = v(Bt, N, qe), Be === null ? Re = Bt : Be.sibling = Bt, Be = Bt);
      return n && $e.forEach(function(em) {
        return r(z, em);
      }), Cn && Nu(z, qe), Re;
    }
    function jn(z, N, F, ee) {
      if (typeof F == "object" && F !== null && F.type === Qe && F.key === null && (F = F.props.children), typeof F == "object" && F !== null) {
        switch (F.$$typeof) {
          case ze:
            e: {
              for (var Re = F.key, Be = N; Be !== null; ) {
                if (Be.key === Re) {
                  if (Re = F.type, Re === Qe) {
                    if (Be.tag === 7) {
                      l(z, Be.sibling), N = f(Be, F.props.children), N.return = z, z = N;
                      break e;
                    }
                  } else if (Be.elementType === Re || typeof Re == "object" && Re !== null && Re.$$typeof === Ft && gh(Re) === Be.type) {
                    l(z, Be.sibling), N = f(Be, F.props), N.ref = Mu(z, Be, F), N.return = z, z = N;
                    break e;
                  }
                  l(z, Be);
                  break;
                } else r(z, Be);
                Be = Be.sibling;
              }
              F.type === Qe ? (N = vl(F.props.children, z.mode, ee, F.key), N.return = z, z = N) : (ee = oc(F.type, F.key, F.props, null, z.mode, ee), ee.ref = Mu(z, N, F), ee.return = z, z = ee);
            }
            return C(z);
          case St:
            e: {
              for (Be = F.key; N !== null; ) {
                if (N.key === Be) if (N.tag === 4 && N.stateNode.containerInfo === F.containerInfo && N.stateNode.implementation === F.implementation) {
                  l(z, N.sibling), N = f(N, F.children || []), N.return = z, z = N;
                  break e;
                } else {
                  l(z, N);
                  break;
                }
                else r(z, N);
                N = N.sibling;
              }
              N = zf(F, z.mode, ee), N.return = z, z = N;
            }
            return C(z);
          case Ft:
            return Be = F._init, jn(z, N, Be(F._payload), ee);
        }
        if (ir(F)) return Te(z, N, F, ee);
        if (Oe(F)) return ke(z, N, F, ee);
        tf(z, F);
      }
      return typeof F == "string" && F !== "" || typeof F == "number" ? (F = "" + F, N !== null && N.tag === 6 ? (l(z, N.sibling), N = f(N, F), N.return = z, z = N) : (l(z, N), N = xp(F, z.mode, ee), N.return = z, z = N), C(z)) : l(z, N);
    }
    return jn;
  }
  var Ln = Lu(!0), ve = Lu(!1), Ta = Pa(null), oa = null, Uo = null, Qd = null;
  function Zd() {
    Qd = Uo = oa = null;
  }
  function Gd(n) {
    var r = Ta.current;
    pn(Ta), n._currentValue = r;
  }
  function qd(n, r, l) {
    for (; n !== null; ) {
      var o = n.alternate;
      if ((n.childLanes & r) !== r ? (n.childLanes |= r, o !== null && (o.childLanes |= r)) : o !== null && (o.childLanes & r) !== r && (o.childLanes |= r), n === l) break;
      n = n.return;
    }
  }
  function wn(n, r) {
    oa = n, Qd = Uo = null, n = n.dependencies, n !== null && n.firstContext !== null && (n.lanes & r && (Yn = !0), n.firstContext = null);
  }
  function $a(n) {
    var r = n._currentValue;
    if (Qd !== n) if (n = { context: n, memoizedValue: r, next: null }, Uo === null) {
      if (oa === null) throw Error(p(308));
      Uo = n, oa.dependencies = { lanes: 0, firstContext: n };
    } else Uo = Uo.next = n;
    return r;
  }
  var Au = null;
  function Xd(n) {
    Au === null ? Au = [n] : Au.push(n);
  }
  function Kd(n, r, l, o) {
    var f = r.interleaved;
    return f === null ? (l.next = l, Xd(r)) : (l.next = f.next, f.next = l), r.interleaved = l, Ra(n, o);
  }
  function Ra(n, r) {
    n.lanes |= r;
    var l = n.alternate;
    for (l !== null && (l.lanes |= r), l = n, n = n.return; n !== null; ) n.childLanes |= r, l = n.alternate, l !== null && (l.childLanes |= r), l = n, n = n.return;
    return l.tag === 3 ? l.stateNode : null;
  }
  var wa = !1;
  function Jd(n) {
    n.updateQueue = { baseState: n.memoizedState, firstBaseUpdate: null, lastBaseUpdate: null, shared: { pending: null, interleaved: null, lanes: 0 }, effects: null };
  }
  function Sh(n, r) {
    n = n.updateQueue, r.updateQueue === n && (r.updateQueue = { baseState: n.baseState, firstBaseUpdate: n.firstBaseUpdate, lastBaseUpdate: n.lastBaseUpdate, shared: n.shared, effects: n.effects });
  }
  function sl(n, r) {
    return { eventTime: n, lane: r, tag: 0, payload: null, callback: null, next: null };
  }
  function Yl(n, r, l) {
    var o = n.updateQueue;
    if (o === null) return null;
    if (o = o.shared, Dt & 2) {
      var f = o.pending;
      return f === null ? r.next = r : (r.next = f.next, f.next = r), o.pending = r, Ra(n, l);
    }
    return f = o.interleaved, f === null ? (r.next = r, Xd(o)) : (r.next = f.next, f.next = r), o.interleaved = r, Ra(n, l);
  }
  function nf(n, r, l) {
    if (r = r.updateQueue, r !== null && (r = r.shared, (l & 4194240) !== 0)) {
      var o = r.lanes;
      o &= n.pendingLanes, l |= o, r.lanes = l, el(n, l);
    }
  }
  function Eh(n, r) {
    var l = n.updateQueue, o = n.alternate;
    if (o !== null && (o = o.updateQueue, l === o)) {
      var f = null, v = null;
      if (l = l.firstBaseUpdate, l !== null) {
        do {
          var C = { eventTime: l.eventTime, lane: l.lane, tag: l.tag, payload: l.payload, callback: l.callback, next: null };
          v === null ? f = v = C : v = v.next = C, l = l.next;
        } while (l !== null);
        v === null ? f = v = r : v = v.next = r;
      } else f = v = r;
      l = { baseState: o.baseState, firstBaseUpdate: f, lastBaseUpdate: v, shared: o.shared, effects: o.effects }, n.updateQueue = l;
      return;
    }
    n = l.lastBaseUpdate, n === null ? l.firstBaseUpdate = r : n.next = r, l.lastBaseUpdate = r;
  }
  function Ls(n, r, l, o) {
    var f = n.updateQueue;
    wa = !1;
    var v = f.firstBaseUpdate, C = f.lastBaseUpdate, w = f.shared.pending;
    if (w !== null) {
      f.shared.pending = null;
      var D = w, P = D.next;
      D.next = null, C === null ? v = P : C.next = P, C = D;
      var J = n.alternate;
      J !== null && (J = J.updateQueue, w = J.lastBaseUpdate, w !== C && (w === null ? J.firstBaseUpdate = P : w.next = P, J.lastBaseUpdate = D));
    }
    if (v !== null) {
      var te = f.baseState;
      C = 0, J = P = D = null, w = v;
      do {
        var K = w.lane, ge = w.eventTime;
        if ((o & K) === K) {
          J !== null && (J = J.next = {
            eventTime: ge,
            lane: 0,
            tag: w.tag,
            payload: w.payload,
            callback: w.callback,
            next: null
          });
          e: {
            var Te = n, ke = w;
            switch (K = r, ge = l, ke.tag) {
              case 1:
                if (Te = ke.payload, typeof Te == "function") {
                  te = Te.call(ge, te, K);
                  break e;
                }
                te = Te;
                break e;
              case 3:
                Te.flags = Te.flags & -65537 | 128;
              case 0:
                if (Te = ke.payload, K = typeof Te == "function" ? Te.call(ge, te, K) : Te, K == null) break e;
                te = se({}, te, K);
                break e;
              case 2:
                wa = !0;
            }
          }
          w.callback !== null && w.lane !== 0 && (n.flags |= 64, K = f.effects, K === null ? f.effects = [w] : K.push(w));
        } else ge = { eventTime: ge, lane: K, tag: w.tag, payload: w.payload, callback: w.callback, next: null }, J === null ? (P = J = ge, D = te) : J = J.next = ge, C |= K;
        if (w = w.next, w === null) {
          if (w = f.shared.pending, w === null) break;
          K = w, w = K.next, K.next = null, f.lastBaseUpdate = K, f.shared.pending = null;
        }
      } while (!0);
      if (J === null && (D = te), f.baseState = D, f.firstBaseUpdate = P, f.lastBaseUpdate = J, r = f.shared.interleaved, r !== null) {
        f = r;
        do
          C |= f.lane, f = f.next;
        while (f !== r);
      } else v === null && (f.shared.lanes = 0);
      Bi |= C, n.lanes = C, n.memoizedState = te;
    }
  }
  function ep(n, r, l) {
    if (n = r.effects, r.effects = null, n !== null) for (r = 0; r < n.length; r++) {
      var o = n[r], f = o.callback;
      if (f !== null) {
        if (o.callback = null, o = l, typeof f != "function") throw Error(p(191, f));
        f.call(o);
      }
    }
  }
  var As = {}, Fi = Pa(As), zs = Pa(As), Us = Pa(As);
  function zu(n) {
    if (n === As) throw Error(p(174));
    return n;
  }
  function tp(n, r) {
    switch (Le(Us, r), Le(zs, n), Le(Fi, As), n = r.nodeType, n) {
      case 9:
      case 11:
        r = (r = r.documentElement) ? r.namespaceURI : Ea(null, "");
        break;
      default:
        n = n === 8 ? r.parentNode : r, r = n.namespaceURI || null, n = n.tagName, r = Ea(r, n);
    }
    pn(Fi), Le(Fi, r);
  }
  function Uu() {
    pn(Fi), pn(zs), pn(Us);
  }
  function Ch(n) {
    zu(Us.current);
    var r = zu(Fi.current), l = Ea(r, n.type);
    r !== l && (Le(zs, n), Le(Fi, l));
  }
  function rf(n) {
    zs.current === n && (pn(Fi), pn(zs));
  }
  var bn = Pa(0);
  function af(n) {
    for (var r = n; r !== null; ) {
      if (r.tag === 13) {
        var l = r.memoizedState;
        if (l !== null && (l = l.dehydrated, l === null || l.data === "$?" || l.data === "$!")) return r;
      } else if (r.tag === 19 && r.memoizedProps.revealOrder !== void 0) {
        if (r.flags & 128) return r;
      } else if (r.child !== null) {
        r.child.return = r, r = r.child;
        continue;
      }
      if (r === n) break;
      for (; r.sibling === null; ) {
        if (r.return === null || r.return === n) return null;
        r = r.return;
      }
      r.sibling.return = r.return, r = r.sibling;
    }
    return null;
  }
  var js = [];
  function je() {
    for (var n = 0; n < js.length; n++) js[n]._workInProgressVersionPrimary = null;
    js.length = 0;
  }
  var yt = Tt.ReactCurrentDispatcher, Vt = Tt.ReactCurrentBatchConfig, rn = 0, Pt = null, $n = null, or = null, lf = !1, Fs = !1, ju = 0, X = 0;
  function jt() {
    throw Error(p(321));
  }
  function We(n, r) {
    if (r === null) return !1;
    for (var l = 0; l < r.length && l < n.length; l++) if (!pi(n[l], r[l])) return !1;
    return !0;
  }
  function Wl(n, r, l, o, f, v) {
    if (rn = v, Pt = r, r.memoizedState = null, r.updateQueue = null, r.lanes = 0, yt.current = n === null || n.memoizedState === null ? Cf : $s, n = l(o, f), Fs) {
      v = 0;
      do {
        if (Fs = !1, ju = 0, 25 <= v) throw Error(p(301));
        v += 1, or = $n = null, r.updateQueue = null, yt.current = _f, n = l(o, f);
      } while (Fs);
    }
    if (yt.current = Bu, r = $n !== null && $n.next !== null, rn = 0, or = $n = Pt = null, lf = !1, r) throw Error(p(300));
    return n;
  }
  function hi() {
    var n = ju !== 0;
    return ju = 0, n;
  }
  function Lr() {
    var n = { memoizedState: null, baseState: null, baseQueue: null, queue: null, next: null };
    return or === null ? Pt.memoizedState = or = n : or = or.next = n, or;
  }
  function An() {
    if ($n === null) {
      var n = Pt.alternate;
      n = n !== null ? n.memoizedState : null;
    } else n = $n.next;
    var r = or === null ? Pt.memoizedState : or.next;
    if (r !== null) or = r, $n = n;
    else {
      if (n === null) throw Error(p(310));
      $n = n, n = { memoizedState: $n.memoizedState, baseState: $n.baseState, baseQueue: $n.baseQueue, queue: $n.queue, next: null }, or === null ? Pt.memoizedState = or = n : or = or.next = n;
    }
    return or;
  }
  function cl(n, r) {
    return typeof r == "function" ? r(n) : r;
  }
  function Ql(n) {
    var r = An(), l = r.queue;
    if (l === null) throw Error(p(311));
    l.lastRenderedReducer = n;
    var o = $n, f = o.baseQueue, v = l.pending;
    if (v !== null) {
      if (f !== null) {
        var C = f.next;
        f.next = v.next, v.next = C;
      }
      o.baseQueue = f = v, l.pending = null;
    }
    if (f !== null) {
      v = f.next, o = o.baseState;
      var w = C = null, D = null, P = v;
      do {
        var J = P.lane;
        if ((rn & J) === J) D !== null && (D = D.next = { lane: 0, action: P.action, hasEagerState: P.hasEagerState, eagerState: P.eagerState, next: null }), o = P.hasEagerState ? P.eagerState : n(o, P.action);
        else {
          var te = {
            lane: J,
            action: P.action,
            hasEagerState: P.hasEagerState,
            eagerState: P.eagerState,
            next: null
          };
          D === null ? (w = D = te, C = o) : D = D.next = te, Pt.lanes |= J, Bi |= J;
        }
        P = P.next;
      } while (P !== null && P !== v);
      D === null ? C = o : D.next = w, pi(o, r.memoizedState) || (Yn = !0), r.memoizedState = o, r.baseState = C, r.baseQueue = D, l.lastRenderedState = o;
    }
    if (n = l.interleaved, n !== null) {
      f = n;
      do
        v = f.lane, Pt.lanes |= v, Bi |= v, f = f.next;
      while (f !== n);
    } else f === null && (l.lanes = 0);
    return [r.memoizedState, l.dispatch];
  }
  function Fu(n) {
    var r = An(), l = r.queue;
    if (l === null) throw Error(p(311));
    l.lastRenderedReducer = n;
    var o = l.dispatch, f = l.pending, v = r.memoizedState;
    if (f !== null) {
      l.pending = null;
      var C = f = f.next;
      do
        v = n(v, C.action), C = C.next;
      while (C !== f);
      pi(v, r.memoizedState) || (Yn = !0), r.memoizedState = v, r.baseQueue === null && (r.baseState = v), l.lastRenderedState = v;
    }
    return [v, o];
  }
  function uf() {
  }
  function of(n, r) {
    var l = Pt, o = An(), f = r(), v = !pi(o.memoizedState, f);
    if (v && (o.memoizedState = f, Yn = !0), o = o.queue, Hs(ff.bind(null, l, o, n), [n]), o.getSnapshot !== r || v || or !== null && or.memoizedState.tag & 1) {
      if (l.flags |= 2048, Hu(9, cf.bind(null, l, o, f, r), void 0, null), nr === null) throw Error(p(349));
      rn & 30 || sf(l, r, f);
    }
    return f;
  }
  function sf(n, r, l) {
    n.flags |= 16384, n = { getSnapshot: r, value: l }, r = Pt.updateQueue, r === null ? (r = { lastEffect: null, stores: null }, Pt.updateQueue = r, r.stores = [n]) : (l = r.stores, l === null ? r.stores = [n] : l.push(n));
  }
  function cf(n, r, l, o) {
    r.value = l, r.getSnapshot = o, df(r) && pf(n);
  }
  function ff(n, r, l) {
    return l(function() {
      df(r) && pf(n);
    });
  }
  function df(n) {
    var r = n.getSnapshot;
    n = n.value;
    try {
      var l = r();
      return !pi(n, l);
    } catch {
      return !0;
    }
  }
  function pf(n) {
    var r = Ra(n, 1);
    r !== null && Yr(r, n, 1, -1);
  }
  function vf(n) {
    var r = Lr();
    return typeof n == "function" && (n = n()), r.memoizedState = r.baseState = n, n = { pending: null, interleaved: null, lanes: 0, dispatch: null, lastRenderedReducer: cl, lastRenderedState: n }, r.queue = n, n = n.dispatch = Pu.bind(null, Pt, n), [r.memoizedState, n];
  }
  function Hu(n, r, l, o) {
    return n = { tag: n, create: r, destroy: l, deps: o, next: null }, r = Pt.updateQueue, r === null ? (r = { lastEffect: null, stores: null }, Pt.updateQueue = r, r.lastEffect = n.next = n) : (l = r.lastEffect, l === null ? r.lastEffect = n.next = n : (o = l.next, l.next = n, n.next = o, r.lastEffect = n)), n;
  }
  function hf() {
    return An().memoizedState;
  }
  function jo(n, r, l, o) {
    var f = Lr();
    Pt.flags |= n, f.memoizedState = Hu(1 | r, l, void 0, o === void 0 ? null : o);
  }
  function Fo(n, r, l, o) {
    var f = An();
    o = o === void 0 ? null : o;
    var v = void 0;
    if ($n !== null) {
      var C = $n.memoizedState;
      if (v = C.destroy, o !== null && We(o, C.deps)) {
        f.memoizedState = Hu(r, l, v, o);
        return;
      }
    }
    Pt.flags |= n, f.memoizedState = Hu(1 | r, l, v, o);
  }
  function mf(n, r) {
    return jo(8390656, 8, n, r);
  }
  function Hs(n, r) {
    return Fo(2048, 8, n, r);
  }
  function yf(n, r) {
    return Fo(4, 2, n, r);
  }
  function Vs(n, r) {
    return Fo(4, 4, n, r);
  }
  function Vu(n, r) {
    if (typeof r == "function") return n = n(), r(n), function() {
      r(null);
    };
    if (r != null) return n = n(), r.current = n, function() {
      r.current = null;
    };
  }
  function gf(n, r, l) {
    return l = l != null ? l.concat([n]) : null, Fo(4, 4, Vu.bind(null, r, n), l);
  }
  function Ps() {
  }
  function Sf(n, r) {
    var l = An();
    r = r === void 0 ? null : r;
    var o = l.memoizedState;
    return o !== null && r !== null && We(r, o[1]) ? o[0] : (l.memoizedState = [n, r], n);
  }
  function Ef(n, r) {
    var l = An();
    r = r === void 0 ? null : r;
    var o = l.memoizedState;
    return o !== null && r !== null && We(r, o[1]) ? o[0] : (n = n(), l.memoizedState = [n, r], n);
  }
  function np(n, r, l) {
    return rn & 21 ? (pi(l, r) || (l = yo(), Pt.lanes |= l, Bi |= l, n.baseState = !0), r) : (n.baseState && (n.baseState = !1, Yn = !0), n.memoizedState = l);
  }
  function Bs(n, r) {
    var l = Ht;
    Ht = l !== 0 && 4 > l ? l : 4, n(!0);
    var o = Vt.transition;
    Vt.transition = {};
    try {
      n(!1), r();
    } finally {
      Ht = l, Vt.transition = o;
    }
  }
  function rp() {
    return An().memoizedState;
  }
  function Is(n, r, l) {
    var o = Ii(n);
    if (l = { lane: o, action: l, hasEagerState: !1, eagerState: null, next: null }, sa(n)) _h(r, l);
    else if (l = Kd(n, r, l, o), l !== null) {
      var f = Zn();
      Yr(l, n, o, f), un(l, r, o);
    }
  }
  function Pu(n, r, l) {
    var o = Ii(n), f = { lane: o, action: l, hasEagerState: !1, eagerState: null, next: null };
    if (sa(n)) _h(r, f);
    else {
      var v = n.alternate;
      if (n.lanes === 0 && (v === null || v.lanes === 0) && (v = r.lastRenderedReducer, v !== null)) try {
        var C = r.lastRenderedState, w = v(C, l);
        if (f.hasEagerState = !0, f.eagerState = w, pi(w, C)) {
          var D = r.interleaved;
          D === null ? (f.next = f, Xd(r)) : (f.next = D.next, D.next = f), r.interleaved = f;
          return;
        }
      } catch {
      } finally {
      }
      l = Kd(n, r, f, o), l !== null && (f = Zn(), Yr(l, n, o, f), un(l, r, o));
    }
  }
  function sa(n) {
    var r = n.alternate;
    return n === Pt || r !== null && r === Pt;
  }
  function _h(n, r) {
    Fs = lf = !0;
    var l = n.pending;
    l === null ? r.next = r : (r.next = l.next, l.next = r), n.pending = r;
  }
  function un(n, r, l) {
    if (l & 4194240) {
      var o = r.lanes;
      o &= n.pendingLanes, l |= o, r.lanes = l, el(n, l);
    }
  }
  var Bu = { readContext: $a, useCallback: jt, useContext: jt, useEffect: jt, useImperativeHandle: jt, useInsertionEffect: jt, useLayoutEffect: jt, useMemo: jt, useReducer: jt, useRef: jt, useState: jt, useDebugValue: jt, useDeferredValue: jt, useTransition: jt, useMutableSource: jt, useSyncExternalStore: jt, useId: jt, unstable_isNewReconciler: !1 }, Cf = { readContext: $a, useCallback: function(n, r) {
    return Lr().memoizedState = [n, r === void 0 ? null : r], n;
  }, useContext: $a, useEffect: mf, useImperativeHandle: function(n, r, l) {
    return l = l != null ? l.concat([n]) : null, jo(
      4194308,
      4,
      Vu.bind(null, r, n),
      l
    );
  }, useLayoutEffect: function(n, r) {
    return jo(4194308, 4, n, r);
  }, useInsertionEffect: function(n, r) {
    return jo(4, 2, n, r);
  }, useMemo: function(n, r) {
    var l = Lr();
    return r = r === void 0 ? null : r, n = n(), l.memoizedState = [n, r], n;
  }, useReducer: function(n, r, l) {
    var o = Lr();
    return r = l !== void 0 ? l(r) : r, o.memoizedState = o.baseState = r, n = { pending: null, interleaved: null, lanes: 0, dispatch: null, lastRenderedReducer: n, lastRenderedState: r }, o.queue = n, n = n.dispatch = Is.bind(null, Pt, n), [o.memoizedState, n];
  }, useRef: function(n) {
    var r = Lr();
    return n = { current: n }, r.memoizedState = n;
  }, useState: vf, useDebugValue: Ps, useDeferredValue: function(n) {
    return Lr().memoizedState = n;
  }, useTransition: function() {
    var n = vf(!1), r = n[0];
    return n = Bs.bind(null, n[1]), Lr().memoizedState = n, [r, n];
  }, useMutableSource: function() {
  }, useSyncExternalStore: function(n, r, l) {
    var o = Pt, f = Lr();
    if (Cn) {
      if (l === void 0) throw Error(p(407));
      l = l();
    } else {
      if (l = r(), nr === null) throw Error(p(349));
      rn & 30 || sf(o, r, l);
    }
    f.memoizedState = l;
    var v = { value: l, getSnapshot: r };
    return f.queue = v, mf(ff.bind(
      null,
      o,
      v,
      n
    ), [n]), o.flags |= 2048, Hu(9, cf.bind(null, o, v, l, r), void 0, null), l;
  }, useId: function() {
    var n = Lr(), r = nr.identifierPrefix;
    if (Cn) {
      var l = ji, o = Ui;
      l = (o & ~(1 << 32 - Hr(o) - 1)).toString(32) + l, r = ":" + r + "R" + l, l = ju++, 0 < l && (r += "H" + l.toString(32)), r += ":";
    } else l = X++, r = ":" + r + "r" + l.toString(32) + ":";
    return n.memoizedState = r;
  }, unstable_isNewReconciler: !1 }, $s = {
    readContext: $a,
    useCallback: Sf,
    useContext: $a,
    useEffect: Hs,
    useImperativeHandle: gf,
    useInsertionEffect: yf,
    useLayoutEffect: Vs,
    useMemo: Ef,
    useReducer: Ql,
    useRef: hf,
    useState: function() {
      return Ql(cl);
    },
    useDebugValue: Ps,
    useDeferredValue: function(n) {
      var r = An();
      return np(r, $n.memoizedState, n);
    },
    useTransition: function() {
      var n = Ql(cl)[0], r = An().memoizedState;
      return [n, r];
    },
    useMutableSource: uf,
    useSyncExternalStore: of,
    useId: rp,
    unstable_isNewReconciler: !1
  }, _f = { readContext: $a, useCallback: Sf, useContext: $a, useEffect: Hs, useImperativeHandle: gf, useInsertionEffect: yf, useLayoutEffect: Vs, useMemo: Ef, useReducer: Fu, useRef: hf, useState: function() {
    return Fu(cl);
  }, useDebugValue: Ps, useDeferredValue: function(n) {
    var r = An();
    return $n === null ? r.memoizedState = n : np(r, $n.memoizedState, n);
  }, useTransition: function() {
    var n = Fu(cl)[0], r = An().memoizedState;
    return [n, r];
  }, useMutableSource: uf, useSyncExternalStore: of, useId: rp, unstable_isNewReconciler: !1 };
  function mi(n, r) {
    if (n && n.defaultProps) {
      r = se({}, r), n = n.defaultProps;
      for (var l in n) r[l] === void 0 && (r[l] = n[l]);
      return r;
    }
    return r;
  }
  function ap(n, r, l, o) {
    r = n.memoizedState, l = l(o, r), l = l == null ? r : se({}, r, l), n.memoizedState = l, n.lanes === 0 && (n.updateQueue.baseState = l);
  }
  var xf = { isMounted: function(n) {
    return (n = n._reactInternals) ? it(n) === n : !1;
  }, enqueueSetState: function(n, r, l) {
    n = n._reactInternals;
    var o = Zn(), f = Ii(n), v = sl(o, f);
    v.payload = r, l != null && (v.callback = l), r = Yl(n, v, f), r !== null && (Yr(r, n, f, o), nf(r, n, f));
  }, enqueueReplaceState: function(n, r, l) {
    n = n._reactInternals;
    var o = Zn(), f = Ii(n), v = sl(o, f);
    v.tag = 1, v.payload = r, l != null && (v.callback = l), r = Yl(n, v, f), r !== null && (Yr(r, n, f, o), nf(r, n, f));
  }, enqueueForceUpdate: function(n, r) {
    n = n._reactInternals;
    var l = Zn(), o = Ii(n), f = sl(l, o);
    f.tag = 2, r != null && (f.callback = r), r = Yl(n, f, o), r !== null && (Yr(r, n, o, l), nf(r, n, o));
  } };
  function xh(n, r, l, o, f, v, C) {
    return n = n.stateNode, typeof n.shouldComponentUpdate == "function" ? n.shouldComponentUpdate(o, v, C) : r.prototype && r.prototype.isPureReactComponent ? !xs(l, o) || !xs(f, v) : !0;
  }
  function Tf(n, r, l) {
    var o = !1, f = Mr, v = r.contextType;
    return typeof v == "object" && v !== null ? v = $a(v) : (f = Bn(r) ? aa : Dn.current, o = r.contextTypes, v = (o = o != null) ? ia(n, f) : Mr), r = new r(l, v), n.memoizedState = r.state !== null && r.state !== void 0 ? r.state : null, r.updater = xf, n.stateNode = r, r._reactInternals = n, o && (n = n.stateNode, n.__reactInternalMemoizedUnmaskedChildContext = f, n.__reactInternalMemoizedMaskedChildContext = v), r;
  }
  function Th(n, r, l, o) {
    n = r.state, typeof r.componentWillReceiveProps == "function" && r.componentWillReceiveProps(l, o), typeof r.UNSAFE_componentWillReceiveProps == "function" && r.UNSAFE_componentWillReceiveProps(l, o), r.state !== n && xf.enqueueReplaceState(r, r.state, null);
  }
  function Ys(n, r, l, o) {
    var f = n.stateNode;
    f.props = l, f.state = n.memoizedState, f.refs = {}, Jd(n);
    var v = r.contextType;
    typeof v == "object" && v !== null ? f.context = $a(v) : (v = Bn(r) ? aa : Dn.current, f.context = ia(n, v)), f.state = n.memoizedState, v = r.getDerivedStateFromProps, typeof v == "function" && (ap(n, r, v, l), f.state = n.memoizedState), typeof r.getDerivedStateFromProps == "function" || typeof f.getSnapshotBeforeUpdate == "function" || typeof f.UNSAFE_componentWillMount != "function" && typeof f.componentWillMount != "function" || (r = f.state, typeof f.componentWillMount == "function" && f.componentWillMount(), typeof f.UNSAFE_componentWillMount == "function" && f.UNSAFE_componentWillMount(), r !== f.state && xf.enqueueReplaceState(f, f.state, null), Ls(n, l, f, o), f.state = n.memoizedState), typeof f.componentDidMount == "function" && (n.flags |= 4194308);
  }
  function Iu(n, r) {
    try {
      var l = "", o = r;
      do
        l += vt(o), o = o.return;
      while (o);
      var f = l;
    } catch (v) {
      f = `
Error generating stack: ` + v.message + `
` + v.stack;
    }
    return { value: n, source: r, stack: f, digest: null };
  }
  function ip(n, r, l) {
    return { value: n, source: null, stack: l ?? null, digest: r ?? null };
  }
  function lp(n, r) {
    try {
      console.error(r.value);
    } catch (l) {
      setTimeout(function() {
        throw l;
      });
    }
  }
  var Rf = typeof WeakMap == "function" ? WeakMap : Map;
  function Rh(n, r, l) {
    l = sl(-1, l), l.tag = 3, l.payload = { element: null };
    var o = r.value;
    return l.callback = function() {
      $o || ($o = !0, Wu = o), lp(n, r);
    }, l;
  }
  function up(n, r, l) {
    l = sl(-1, l), l.tag = 3;
    var o = n.type.getDerivedStateFromError;
    if (typeof o == "function") {
      var f = r.value;
      l.payload = function() {
        return o(f);
      }, l.callback = function() {
        lp(n, r);
      };
    }
    var v = n.stateNode;
    return v !== null && typeof v.componentDidCatch == "function" && (l.callback = function() {
      lp(n, r), typeof o != "function" && (ql === null ? ql = /* @__PURE__ */ new Set([this]) : ql.add(this));
      var C = r.stack;
      this.componentDidCatch(r.value, { componentStack: C !== null ? C : "" });
    }), l;
  }
  function op(n, r, l) {
    var o = n.pingCache;
    if (o === null) {
      o = n.pingCache = new Rf();
      var f = /* @__PURE__ */ new Set();
      o.set(r, f);
    } else f = o.get(r), f === void 0 && (f = /* @__PURE__ */ new Set(), o.set(r, f));
    f.has(l) || (f.add(l), n = fg.bind(null, n, r, l), r.then(n, n));
  }
  function wh(n) {
    do {
      var r;
      if ((r = n.tag === 13) && (r = n.memoizedState, r = r !== null ? r.dehydrated !== null : !0), r) return n;
      n = n.return;
    } while (n !== null);
    return null;
  }
  function Zl(n, r, l, o, f) {
    return n.mode & 1 ? (n.flags |= 65536, n.lanes = f, n) : (n === r ? n.flags |= 65536 : (n.flags |= 128, l.flags |= 131072, l.flags &= -52805, l.tag === 1 && (l.alternate === null ? l.tag = 17 : (r = sl(-1, 1), r.tag = 2, Yl(l, r, 1))), l.lanes |= 1), n);
  }
  var Ws = Tt.ReactCurrentOwner, Yn = !1;
  function gr(n, r, l, o) {
    r.child = n === null ? ve(r, null, l, o) : Ln(r, n.child, l, o);
  }
  function ca(n, r, l, o, f) {
    l = l.render;
    var v = r.ref;
    return wn(r, f), o = Wl(n, r, l, o, v, f), l = hi(), n !== null && !Yn ? (r.updateQueue = n.updateQueue, r.flags &= -2053, n.lanes &= ~f, Wa(n, r, f)) : (Cn && l && Kc(r), r.flags |= 1, gr(n, r, o, f), r.child);
  }
  function $u(n, r, l, o, f) {
    if (n === null) {
      var v = l.type;
      return typeof v == "function" && !_p(v) && v.defaultProps === void 0 && l.compare === null && l.defaultProps === void 0 ? (r.tag = 15, r.type = v, ut(n, r, v, o, f)) : (n = oc(l.type, null, o, r, r.mode, f), n.ref = r.ref, n.return = r, r.child = n);
    }
    if (v = n.child, !(n.lanes & f)) {
      var C = v.memoizedProps;
      if (l = l.compare, l = l !== null ? l : xs, l(C, o) && n.ref === r.ref) return Wa(n, r, f);
    }
    return r.flags |= 1, n = Kl(v, o), n.ref = r.ref, n.return = r, r.child = n;
  }
  function ut(n, r, l, o, f) {
    if (n !== null) {
      var v = n.memoizedProps;
      if (xs(v, o) && n.ref === r.ref) if (Yn = !1, r.pendingProps = o = v, (n.lanes & f) !== 0) n.flags & 131072 && (Yn = !0);
      else return r.lanes = n.lanes, Wa(n, r, f);
    }
    return bh(n, r, l, o, f);
  }
  function Qs(n, r, l) {
    var o = r.pendingProps, f = o.children, v = n !== null ? n.memoizedState : null;
    if (o.mode === "hidden") if (!(r.mode & 1)) r.memoizedState = { baseLanes: 0, cachePool: null, transitions: null }, Le(Po, ba), ba |= l;
    else {
      if (!(l & 1073741824)) return n = v !== null ? v.baseLanes | l : l, r.lanes = r.childLanes = 1073741824, r.memoizedState = { baseLanes: n, cachePool: null, transitions: null }, r.updateQueue = null, Le(Po, ba), ba |= n, null;
      r.memoizedState = { baseLanes: 0, cachePool: null, transitions: null }, o = v !== null ? v.baseLanes : l, Le(Po, ba), ba |= o;
    }
    else v !== null ? (o = v.baseLanes | l, r.memoizedState = null) : o = l, Le(Po, ba), ba |= o;
    return gr(n, r, f, l), r.child;
  }
  function sp(n, r) {
    var l = r.ref;
    (n === null && l !== null || n !== null && n.ref !== l) && (r.flags |= 512, r.flags |= 2097152);
  }
  function bh(n, r, l, o, f) {
    var v = Bn(l) ? aa : Dn.current;
    return v = ia(r, v), wn(r, f), l = Wl(n, r, l, o, v, f), o = hi(), n !== null && !Yn ? (r.updateQueue = n.updateQueue, r.flags &= -2053, n.lanes &= ~f, Wa(n, r, f)) : (Cn && o && Kc(r), r.flags |= 1, gr(n, r, l, f), r.child);
  }
  function kh(n, r, l, o, f) {
    if (Bn(l)) {
      var v = !0;
      ur(r);
    } else v = !1;
    if (wn(r, f), r.stateNode === null) Ya(n, r), Tf(r, l, o), Ys(r, l, o, f), o = !0;
    else if (n === null) {
      var C = r.stateNode, w = r.memoizedProps;
      C.props = w;
      var D = C.context, P = l.contextType;
      typeof P == "object" && P !== null ? P = $a(P) : (P = Bn(l) ? aa : Dn.current, P = ia(r, P));
      var J = l.getDerivedStateFromProps, te = typeof J == "function" || typeof C.getSnapshotBeforeUpdate == "function";
      te || typeof C.UNSAFE_componentWillReceiveProps != "function" && typeof C.componentWillReceiveProps != "function" || (w !== o || D !== P) && Th(r, C, o, P), wa = !1;
      var K = r.memoizedState;
      C.state = K, Ls(r, o, C, f), D = r.memoizedState, w !== o || K !== D || er.current || wa ? (typeof J == "function" && (ap(r, l, J, o), D = r.memoizedState), (w = wa || xh(r, l, w, o, K, D, P)) ? (te || typeof C.UNSAFE_componentWillMount != "function" && typeof C.componentWillMount != "function" || (typeof C.componentWillMount == "function" && C.componentWillMount(), typeof C.UNSAFE_componentWillMount == "function" && C.UNSAFE_componentWillMount()), typeof C.componentDidMount == "function" && (r.flags |= 4194308)) : (typeof C.componentDidMount == "function" && (r.flags |= 4194308), r.memoizedProps = o, r.memoizedState = D), C.props = o, C.state = D, C.context = P, o = w) : (typeof C.componentDidMount == "function" && (r.flags |= 4194308), o = !1);
    } else {
      C = r.stateNode, Sh(n, r), w = r.memoizedProps, P = r.type === r.elementType ? w : mi(r.type, w), C.props = P, te = r.pendingProps, K = C.context, D = l.contextType, typeof D == "object" && D !== null ? D = $a(D) : (D = Bn(l) ? aa : Dn.current, D = ia(r, D));
      var ge = l.getDerivedStateFromProps;
      (J = typeof ge == "function" || typeof C.getSnapshotBeforeUpdate == "function") || typeof C.UNSAFE_componentWillReceiveProps != "function" && typeof C.componentWillReceiveProps != "function" || (w !== te || K !== D) && Th(r, C, o, D), wa = !1, K = r.memoizedState, C.state = K, Ls(r, o, C, f);
      var Te = r.memoizedState;
      w !== te || K !== Te || er.current || wa ? (typeof ge == "function" && (ap(r, l, ge, o), Te = r.memoizedState), (P = wa || xh(r, l, P, o, K, Te, D) || !1) ? (J || typeof C.UNSAFE_componentWillUpdate != "function" && typeof C.componentWillUpdate != "function" || (typeof C.componentWillUpdate == "function" && C.componentWillUpdate(o, Te, D), typeof C.UNSAFE_componentWillUpdate == "function" && C.UNSAFE_componentWillUpdate(o, Te, D)), typeof C.componentDidUpdate == "function" && (r.flags |= 4), typeof C.getSnapshotBeforeUpdate == "function" && (r.flags |= 1024)) : (typeof C.componentDidUpdate != "function" || w === n.memoizedProps && K === n.memoizedState || (r.flags |= 4), typeof C.getSnapshotBeforeUpdate != "function" || w === n.memoizedProps && K === n.memoizedState || (r.flags |= 1024), r.memoizedProps = o, r.memoizedState = Te), C.props = o, C.state = Te, C.context = D, o = P) : (typeof C.componentDidUpdate != "function" || w === n.memoizedProps && K === n.memoizedState || (r.flags |= 4), typeof C.getSnapshotBeforeUpdate != "function" || w === n.memoizedProps && K === n.memoizedState || (r.flags |= 1024), o = !1);
    }
    return Zs(n, r, l, o, v, f);
  }
  function Zs(n, r, l, o, f, v) {
    sp(n, r);
    var C = (r.flags & 128) !== 0;
    if (!o && !C) return f && qc(r, l, !1), Wa(n, r, v);
    o = r.stateNode, Ws.current = r;
    var w = C && typeof l.getDerivedStateFromError != "function" ? null : o.render();
    return r.flags |= 1, n !== null && C ? (r.child = Ln(r, n.child, null, v), r.child = Ln(r, null, w, v)) : gr(n, r, w, v), r.memoizedState = o.state, f && qc(r, l, !0), r.child;
  }
  function Ho(n) {
    var r = n.stateNode;
    r.pendingContext ? hh(n, r.pendingContext, r.pendingContext !== r.context) : r.context && hh(n, r.context, !1), tp(n, r.containerInfo);
  }
  function Dh(n, r, l, o, f) {
    return $l(), ol(f), r.flags |= 256, gr(n, r, l, o), r.child;
  }
  var wf = { dehydrated: null, treeContext: null, retryLane: 0 };
  function cp(n) {
    return { baseLanes: n, cachePool: null, transitions: null };
  }
  function bf(n, r, l) {
    var o = r.pendingProps, f = bn.current, v = !1, C = (r.flags & 128) !== 0, w;
    if ((w = C) || (w = n !== null && n.memoizedState === null ? !1 : (f & 2) !== 0), w ? (v = !0, r.flags &= -129) : (n === null || n.memoizedState !== null) && (f |= 1), Le(bn, f & 1), n === null)
      return Wd(r), n = r.memoizedState, n !== null && (n = n.dehydrated, n !== null) ? (r.mode & 1 ? n.data === "$!" ? r.lanes = 8 : r.lanes = 1073741824 : r.lanes = 1, null) : (C = o.children, n = o.fallback, v ? (o = r.mode, v = r.child, C = { mode: "hidden", children: C }, !(o & 1) && v !== null ? (v.childLanes = 0, v.pendingProps = C) : v = Jl(C, o, 0, null), n = vl(n, o, l, null), v.return = r, n.return = r, v.sibling = n, r.child = v, r.child.memoizedState = cp(l), r.memoizedState = wf, n) : fp(r, C));
    if (f = n.memoizedState, f !== null && (w = f.dehydrated, w !== null)) return Oh(n, r, C, o, w, f, l);
    if (v) {
      v = o.fallback, C = r.mode, f = n.child, w = f.sibling;
      var D = { mode: "hidden", children: o.children };
      return !(C & 1) && r.child !== f ? (o = r.child, o.childLanes = 0, o.pendingProps = D, r.deletions = null) : (o = Kl(f, D), o.subtreeFlags = f.subtreeFlags & 14680064), w !== null ? v = Kl(w, v) : (v = vl(v, C, l, null), v.flags |= 2), v.return = r, o.return = r, o.sibling = v, r.child = o, o = v, v = r.child, C = n.child.memoizedState, C = C === null ? cp(l) : { baseLanes: C.baseLanes | l, cachePool: null, transitions: C.transitions }, v.memoizedState = C, v.childLanes = n.childLanes & ~l, r.memoizedState = wf, o;
    }
    return v = n.child, n = v.sibling, o = Kl(v, { mode: "visible", children: o.children }), !(r.mode & 1) && (o.lanes = l), o.return = r, o.sibling = null, n !== null && (l = r.deletions, l === null ? (r.deletions = [n], r.flags |= 16) : l.push(n)), r.child = o, r.memoizedState = null, o;
  }
  function fp(n, r) {
    return r = Jl({ mode: "visible", children: r }, n.mode, 0, null), r.return = n, n.child = r;
  }
  function Gs(n, r, l, o) {
    return o !== null && ol(o), Ln(r, n.child, null, l), n = fp(r, r.pendingProps.children), n.flags |= 2, r.memoizedState = null, n;
  }
  function Oh(n, r, l, o, f, v, C) {
    if (l)
      return r.flags & 256 ? (r.flags &= -257, o = ip(Error(p(422))), Gs(n, r, C, o)) : r.memoizedState !== null ? (r.child = n.child, r.flags |= 128, null) : (v = o.fallback, f = r.mode, o = Jl({ mode: "visible", children: o.children }, f, 0, null), v = vl(v, f, C, null), v.flags |= 2, o.return = r, v.return = r, o.sibling = v, r.child = o, r.mode & 1 && Ln(r, n.child, null, C), r.child.memoizedState = cp(C), r.memoizedState = wf, v);
    if (!(r.mode & 1)) return Gs(n, r, C, null);
    if (f.data === "$!") {
      if (o = f.nextSibling && f.nextSibling.dataset, o) var w = o.dgst;
      return o = w, v = Error(p(419)), o = ip(v, o, void 0), Gs(n, r, C, o);
    }
    if (w = (C & n.childLanes) !== 0, Yn || w) {
      if (o = nr, o !== null) {
        switch (C & -C) {
          case 4:
            f = 2;
            break;
          case 16:
            f = 8;
            break;
          case 64:
          case 128:
          case 256:
          case 512:
          case 1024:
          case 2048:
          case 4096:
          case 8192:
          case 16384:
          case 32768:
          case 65536:
          case 131072:
          case 262144:
          case 524288:
          case 1048576:
          case 2097152:
          case 4194304:
          case 8388608:
          case 16777216:
          case 33554432:
          case 67108864:
            f = 32;
            break;
          case 536870912:
            f = 268435456;
            break;
          default:
            f = 0;
        }
        f = f & (o.suspendedLanes | C) ? 0 : f, f !== 0 && f !== v.retryLane && (v.retryLane = f, Ra(n, f), Yr(o, n, f, -1));
      }
      return Cp(), o = ip(Error(p(421))), Gs(n, r, C, o);
    }
    return f.data === "$?" ? (r.flags |= 128, r.child = n.child, r = dg.bind(null, n), f._reactRetry = r, null) : (n = v.treeContext, ua = Mi(f.nextSibling), la = r, Cn = !0, Ia = null, n !== null && (In[Ba++] = Ui, In[Ba++] = ji, In[Ba++] = xa, Ui = n.id, ji = n.overflow, xa = r), r = fp(r, o.children), r.flags |= 4096, r);
  }
  function dp(n, r, l) {
    n.lanes |= r;
    var o = n.alternate;
    o !== null && (o.lanes |= r), qd(n.return, r, l);
  }
  function Br(n, r, l, o, f) {
    var v = n.memoizedState;
    v === null ? n.memoizedState = { isBackwards: r, rendering: null, renderingStartTime: 0, last: o, tail: l, tailMode: f } : (v.isBackwards = r, v.rendering = null, v.renderingStartTime = 0, v.last = o, v.tail = l, v.tailMode = f);
  }
  function Hi(n, r, l) {
    var o = r.pendingProps, f = o.revealOrder, v = o.tail;
    if (gr(n, r, o.children, l), o = bn.current, o & 2) o = o & 1 | 2, r.flags |= 128;
    else {
      if (n !== null && n.flags & 128) e: for (n = r.child; n !== null; ) {
        if (n.tag === 13) n.memoizedState !== null && dp(n, l, r);
        else if (n.tag === 19) dp(n, l, r);
        else if (n.child !== null) {
          n.child.return = n, n = n.child;
          continue;
        }
        if (n === r) break e;
        for (; n.sibling === null; ) {
          if (n.return === null || n.return === r) break e;
          n = n.return;
        }
        n.sibling.return = n.return, n = n.sibling;
      }
      o &= 1;
    }
    if (Le(bn, o), !(r.mode & 1)) r.memoizedState = null;
    else switch (f) {
      case "forwards":
        for (l = r.child, f = null; l !== null; ) n = l.alternate, n !== null && af(n) === null && (f = l), l = l.sibling;
        l = f, l === null ? (f = r.child, r.child = null) : (f = l.sibling, l.sibling = null), Br(r, !1, f, l, v);
        break;
      case "backwards":
        for (l = null, f = r.child, r.child = null; f !== null; ) {
          if (n = f.alternate, n !== null && af(n) === null) {
            r.child = f;
            break;
          }
          n = f.sibling, f.sibling = l, l = f, f = n;
        }
        Br(r, !0, l, null, v);
        break;
      case "together":
        Br(r, !1, null, null, void 0);
        break;
      default:
        r.memoizedState = null;
    }
    return r.child;
  }
  function Ya(n, r) {
    !(r.mode & 1) && n !== null && (n.alternate = null, r.alternate = null, r.flags |= 2);
  }
  function Wa(n, r, l) {
    if (n !== null && (r.dependencies = n.dependencies), Bi |= r.lanes, !(l & r.childLanes)) return null;
    if (n !== null && r.child !== n.child) throw Error(p(153));
    if (r.child !== null) {
      for (n = r.child, l = Kl(n, n.pendingProps), r.child = l, l.return = r; n.sibling !== null; ) n = n.sibling, l = l.sibling = Kl(n, n.pendingProps), l.return = r;
      l.sibling = null;
    }
    return r.child;
  }
  function qs(n, r, l) {
    switch (r.tag) {
      case 3:
        Ho(r), $l();
        break;
      case 5:
        Ch(r);
        break;
      case 1:
        Bn(r.type) && ur(r);
        break;
      case 4:
        tp(r, r.stateNode.containerInfo);
        break;
      case 10:
        var o = r.type._context, f = r.memoizedProps.value;
        Le(Ta, o._currentValue), o._currentValue = f;
        break;
      case 13:
        if (o = r.memoizedState, o !== null)
          return o.dehydrated !== null ? (Le(bn, bn.current & 1), r.flags |= 128, null) : l & r.child.childLanes ? bf(n, r, l) : (Le(bn, bn.current & 1), n = Wa(n, r, l), n !== null ? n.sibling : null);
        Le(bn, bn.current & 1);
        break;
      case 19:
        if (o = (l & r.childLanes) !== 0, n.flags & 128) {
          if (o) return Hi(n, r, l);
          r.flags |= 128;
        }
        if (f = r.memoizedState, f !== null && (f.rendering = null, f.tail = null, f.lastEffect = null), Le(bn, bn.current), o) break;
        return null;
      case 22:
      case 23:
        return r.lanes = 0, Qs(n, r, l);
    }
    return Wa(n, r, l);
  }
  var Qa, Wn, Nh, Mh;
  Qa = function(n, r) {
    for (var l = r.child; l !== null; ) {
      if (l.tag === 5 || l.tag === 6) n.appendChild(l.stateNode);
      else if (l.tag !== 4 && l.child !== null) {
        l.child.return = l, l = l.child;
        continue;
      }
      if (l === r) break;
      for (; l.sibling === null; ) {
        if (l.return === null || l.return === r) return;
        l = l.return;
      }
      l.sibling.return = l.return, l = l.sibling;
    }
  }, Wn = function() {
  }, Nh = function(n, r, l, o) {
    var f = n.memoizedProps;
    if (f !== o) {
      n = r.stateNode, zu(Fi.current);
      var v = null;
      switch (l) {
        case "input":
          f = pr(n, f), o = pr(n, o), v = [];
          break;
        case "select":
          f = se({}, f, { value: void 0 }), o = se({}, o, { value: void 0 }), v = [];
          break;
        case "textarea":
          f = Kn(n, f), o = Kn(n, o), v = [];
          break;
        default:
          typeof f.onClick != "function" && typeof o.onClick == "function" && (n.onclick = Fl);
      }
      mn(l, o);
      var C;
      l = null;
      for (P in f) if (!o.hasOwnProperty(P) && f.hasOwnProperty(P) && f[P] != null) if (P === "style") {
        var w = f[P];
        for (C in w) w.hasOwnProperty(C) && (l || (l = {}), l[C] = "");
      } else P !== "dangerouslySetInnerHTML" && P !== "children" && P !== "suppressContentEditableWarning" && P !== "suppressHydrationWarning" && P !== "autoFocus" && (_.hasOwnProperty(P) ? v || (v = []) : (v = v || []).push(P, null));
      for (P in o) {
        var D = o[P];
        if (w = f != null ? f[P] : void 0, o.hasOwnProperty(P) && D !== w && (D != null || w != null)) if (P === "style") if (w) {
          for (C in w) !w.hasOwnProperty(C) || D && D.hasOwnProperty(C) || (l || (l = {}), l[C] = "");
          for (C in D) D.hasOwnProperty(C) && w[C] !== D[C] && (l || (l = {}), l[C] = D[C]);
        } else l || (v || (v = []), v.push(
          P,
          l
        )), l = D;
        else P === "dangerouslySetInnerHTML" ? (D = D ? D.__html : void 0, w = w ? w.__html : void 0, D != null && w !== D && (v = v || []).push(P, D)) : P === "children" ? typeof D != "string" && typeof D != "number" || (v = v || []).push(P, "" + D) : P !== "suppressContentEditableWarning" && P !== "suppressHydrationWarning" && (_.hasOwnProperty(P) ? (D != null && P === "onScroll" && Gt("scroll", n), v || w === D || (v = [])) : (v = v || []).push(P, D));
      }
      l && (v = v || []).push("style", l);
      var P = v;
      (r.updateQueue = P) && (r.flags |= 4);
    }
  }, Mh = function(n, r, l, o) {
    l !== o && (r.flags |= 4);
  };
  function Xs(n, r) {
    if (!Cn) switch (n.tailMode) {
      case "hidden":
        r = n.tail;
        for (var l = null; r !== null; ) r.alternate !== null && (l = r), r = r.sibling;
        l === null ? n.tail = null : l.sibling = null;
        break;
      case "collapsed":
        l = n.tail;
        for (var o = null; l !== null; ) l.alternate !== null && (o = l), l = l.sibling;
        o === null ? r || n.tail === null ? n.tail = null : n.tail.sibling = null : o.sibling = null;
    }
  }
  function sr(n) {
    var r = n.alternate !== null && n.alternate.child === n.child, l = 0, o = 0;
    if (r) for (var f = n.child; f !== null; ) l |= f.lanes | f.childLanes, o |= f.subtreeFlags & 14680064, o |= f.flags & 14680064, f.return = n, f = f.sibling;
    else for (f = n.child; f !== null; ) l |= f.lanes | f.childLanes, o |= f.subtreeFlags, o |= f.flags, f.return = n, f = f.sibling;
    return n.subtreeFlags |= o, n.childLanes = l, r;
  }
  function Lh(n, r, l) {
    var o = r.pendingProps;
    switch (Jc(r), r.tag) {
      case 2:
      case 16:
      case 15:
      case 0:
      case 11:
      case 7:
      case 8:
      case 12:
      case 9:
      case 14:
        return sr(r), null;
      case 1:
        return Bn(r.type) && Ao(), sr(r), null;
      case 3:
        return o = r.stateNode, Uu(), pn(er), pn(Dn), je(), o.pendingContext && (o.context = o.pendingContext, o.pendingContext = null), (n === null || n.child === null) && (ef(r) ? r.flags |= 4 : n === null || n.memoizedState.isDehydrated && !(r.flags & 256) || (r.flags |= 1024, Ia !== null && (Qu(Ia), Ia = null))), Wn(n, r), sr(r), null;
      case 5:
        rf(r);
        var f = zu(Us.current);
        if (l = r.type, n !== null && r.stateNode != null) Nh(n, r, l, o, f), n.ref !== r.ref && (r.flags |= 512, r.flags |= 2097152);
        else {
          if (!o) {
            if (r.stateNode === null) throw Error(p(166));
            return sr(r), null;
          }
          if (n = zu(Fi.current), ef(r)) {
            o = r.stateNode, l = r.type;
            var v = r.memoizedProps;
            switch (o[Li] = r, o[Ds] = v, n = (r.mode & 1) !== 0, l) {
              case "dialog":
                Gt("cancel", o), Gt("close", o);
                break;
              case "iframe":
              case "object":
              case "embed":
                Gt("load", o);
                break;
              case "video":
              case "audio":
                for (f = 0; f < ws.length; f++) Gt(ws[f], o);
                break;
              case "source":
                Gt("error", o);
                break;
              case "img":
              case "image":
              case "link":
                Gt(
                  "error",
                  o
                ), Gt("load", o);
                break;
              case "details":
                Gt("toggle", o);
                break;
              case "input":
                qn(o, v), Gt("invalid", o);
                break;
              case "select":
                o._wrapperState = { wasMultiple: !!v.multiple }, Gt("invalid", o);
                break;
              case "textarea":
                Dr(o, v), Gt("invalid", o);
            }
            mn(l, v), f = null;
            for (var C in v) if (v.hasOwnProperty(C)) {
              var w = v[C];
              C === "children" ? typeof w == "string" ? o.textContent !== w && (v.suppressHydrationWarning !== !0 && Wc(o.textContent, w, n), f = ["children", w]) : typeof w == "number" && o.textContent !== "" + w && (v.suppressHydrationWarning !== !0 && Wc(
                o.textContent,
                w,
                n
              ), f = ["children", "" + w]) : _.hasOwnProperty(C) && w != null && C === "onScroll" && Gt("scroll", o);
            }
            switch (l) {
              case "input":
                Hn(o), _i(o, v, !0);
                break;
              case "textarea":
                Hn(o), Vn(o);
                break;
              case "select":
              case "option":
                break;
              default:
                typeof v.onClick == "function" && (o.onclick = Fl);
            }
            o = f, r.updateQueue = o, o !== null && (r.flags |= 4);
          } else {
            C = f.nodeType === 9 ? f : f.ownerDocument, n === "http://www.w3.org/1999/xhtml" && (n = Or(l)), n === "http://www.w3.org/1999/xhtml" ? l === "script" ? (n = C.createElement("div"), n.innerHTML = "<script><\/script>", n = n.removeChild(n.firstChild)) : typeof o.is == "string" ? n = C.createElement(l, { is: o.is }) : (n = C.createElement(l), l === "select" && (C = n, o.multiple ? C.multiple = !0 : o.size && (C.size = o.size))) : n = C.createElementNS(n, l), n[Li] = r, n[Ds] = o, Qa(n, r, !1, !1), r.stateNode = n;
            e: {
              switch (C = lr(l, o), l) {
                case "dialog":
                  Gt("cancel", n), Gt("close", n), f = o;
                  break;
                case "iframe":
                case "object":
                case "embed":
                  Gt("load", n), f = o;
                  break;
                case "video":
                case "audio":
                  for (f = 0; f < ws.length; f++) Gt(ws[f], n);
                  f = o;
                  break;
                case "source":
                  Gt("error", n), f = o;
                  break;
                case "img":
                case "image":
                case "link":
                  Gt(
                    "error",
                    n
                  ), Gt("load", n), f = o;
                  break;
                case "details":
                  Gt("toggle", n), f = o;
                  break;
                case "input":
                  qn(n, o), f = pr(n, o), Gt("invalid", n);
                  break;
                case "option":
                  f = o;
                  break;
                case "select":
                  n._wrapperState = { wasMultiple: !!o.multiple }, f = se({}, o, { value: void 0 }), Gt("invalid", n);
                  break;
                case "textarea":
                  Dr(n, o), f = Kn(n, o), Gt("invalid", n);
                  break;
                default:
                  f = o;
              }
              mn(l, f), w = f;
              for (v in w) if (w.hasOwnProperty(v)) {
                var D = w[v];
                v === "style" ? sn(n, D) : v === "dangerouslySetInnerHTML" ? (D = D ? D.__html : void 0, D != null && xi(n, D)) : v === "children" ? typeof D == "string" ? (l !== "textarea" || D !== "") && ue(n, D) : typeof D == "number" && ue(n, "" + D) : v !== "suppressContentEditableWarning" && v !== "suppressHydrationWarning" && v !== "autoFocus" && (_.hasOwnProperty(v) ? D != null && v === "onScroll" && Gt("scroll", n) : D != null && rt(n, v, D, C));
              }
              switch (l) {
                case "input":
                  Hn(n), _i(n, o, !1);
                  break;
                case "textarea":
                  Hn(n), Vn(n);
                  break;
                case "option":
                  o.value != null && n.setAttribute("value", "" + ft(o.value));
                  break;
                case "select":
                  n.multiple = !!o.multiple, v = o.value, v != null ? Nn(n, !!o.multiple, v, !1) : o.defaultValue != null && Nn(
                    n,
                    !!o.multiple,
                    o.defaultValue,
                    !0
                  );
                  break;
                default:
                  typeof f.onClick == "function" && (n.onclick = Fl);
              }
              switch (l) {
                case "button":
                case "input":
                case "select":
                case "textarea":
                  o = !!o.autoFocus;
                  break e;
                case "img":
                  o = !0;
                  break e;
                default:
                  o = !1;
              }
            }
            o && (r.flags |= 4);
          }
          r.ref !== null && (r.flags |= 512, r.flags |= 2097152);
        }
        return sr(r), null;
      case 6:
        if (n && r.stateNode != null) Mh(n, r, n.memoizedProps, o);
        else {
          if (typeof o != "string" && r.stateNode === null) throw Error(p(166));
          if (l = zu(Us.current), zu(Fi.current), ef(r)) {
            if (o = r.stateNode, l = r.memoizedProps, o[Li] = r, (v = o.nodeValue !== l) && (n = la, n !== null)) switch (n.tag) {
              case 3:
                Wc(o.nodeValue, l, (n.mode & 1) !== 0);
                break;
              case 5:
                n.memoizedProps.suppressHydrationWarning !== !0 && Wc(o.nodeValue, l, (n.mode & 1) !== 0);
            }
            v && (r.flags |= 4);
          } else o = (l.nodeType === 9 ? l : l.ownerDocument).createTextNode(o), o[Li] = r, r.stateNode = o;
        }
        return sr(r), null;
      case 13:
        if (pn(bn), o = r.memoizedState, n === null || n.memoizedState !== null && n.memoizedState.dehydrated !== null) {
          if (Cn && ua !== null && r.mode & 1 && !(r.flags & 128)) Ms(), $l(), r.flags |= 98560, v = !1;
          else if (v = ef(r), o !== null && o.dehydrated !== null) {
            if (n === null) {
              if (!v) throw Error(p(318));
              if (v = r.memoizedState, v = v !== null ? v.dehydrated : null, !v) throw Error(p(317));
              v[Li] = r;
            } else $l(), !(r.flags & 128) && (r.memoizedState = null), r.flags |= 4;
            sr(r), v = !1;
          } else Ia !== null && (Qu(Ia), Ia = null), v = !0;
          if (!v) return r.flags & 65536 ? r : null;
        }
        return r.flags & 128 ? (r.lanes = l, r) : (o = o !== null, o !== (n !== null && n.memoizedState !== null) && o && (r.child.flags |= 8192, r.mode & 1 && (n === null || bn.current & 1 ? Un === 0 && (Un = 3) : Cp())), r.updateQueue !== null && (r.flags |= 4), sr(r), null);
      case 4:
        return Uu(), Wn(n, r), n === null && Do(r.stateNode.containerInfo), sr(r), null;
      case 10:
        return Gd(r.type._context), sr(r), null;
      case 17:
        return Bn(r.type) && Ao(), sr(r), null;
      case 19:
        if (pn(bn), v = r.memoizedState, v === null) return sr(r), null;
        if (o = (r.flags & 128) !== 0, C = v.rendering, C === null) if (o) Xs(v, !1);
        else {
          if (Un !== 0 || n !== null && n.flags & 128) for (n = r.child; n !== null; ) {
            if (C = af(n), C !== null) {
              for (r.flags |= 128, Xs(v, !1), o = C.updateQueue, o !== null && (r.updateQueue = o, r.flags |= 4), r.subtreeFlags = 0, o = l, l = r.child; l !== null; ) v = l, n = o, v.flags &= 14680066, C = v.alternate, C === null ? (v.childLanes = 0, v.lanes = n, v.child = null, v.subtreeFlags = 0, v.memoizedProps = null, v.memoizedState = null, v.updateQueue = null, v.dependencies = null, v.stateNode = null) : (v.childLanes = C.childLanes, v.lanes = C.lanes, v.child = C.child, v.subtreeFlags = 0, v.deletions = null, v.memoizedProps = C.memoizedProps, v.memoizedState = C.memoizedState, v.updateQueue = C.updateQueue, v.type = C.type, n = C.dependencies, v.dependencies = n === null ? null : { lanes: n.lanes, firstContext: n.firstContext }), l = l.sibling;
              return Le(bn, bn.current & 1 | 2), r.child;
            }
            n = n.sibling;
          }
          v.tail !== null && lt() > Io && (r.flags |= 128, o = !0, Xs(v, !1), r.lanes = 4194304);
        }
        else {
          if (!o) if (n = af(C), n !== null) {
            if (r.flags |= 128, o = !0, l = n.updateQueue, l !== null && (r.updateQueue = l, r.flags |= 4), Xs(v, !0), v.tail === null && v.tailMode === "hidden" && !C.alternate && !Cn) return sr(r), null;
          } else 2 * lt() - v.renderingStartTime > Io && l !== 1073741824 && (r.flags |= 128, o = !0, Xs(v, !1), r.lanes = 4194304);
          v.isBackwards ? (C.sibling = r.child, r.child = C) : (l = v.last, l !== null ? l.sibling = C : r.child = C, v.last = C);
        }
        return v.tail !== null ? (r = v.tail, v.rendering = r, v.tail = r.sibling, v.renderingStartTime = lt(), r.sibling = null, l = bn.current, Le(bn, o ? l & 1 | 2 : l & 1), r) : (sr(r), null);
      case 22:
      case 23:
        return Ep(), o = r.memoizedState !== null, n !== null && n.memoizedState !== null !== o && (r.flags |= 8192), o && r.mode & 1 ? ba & 1073741824 && (sr(r), r.subtreeFlags & 6 && (r.flags |= 8192)) : sr(r), null;
      case 24:
        return null;
      case 25:
        return null;
    }
    throw Error(p(156, r.tag));
  }
  function kf(n, r) {
    switch (Jc(r), r.tag) {
      case 1:
        return Bn(r.type) && Ao(), n = r.flags, n & 65536 ? (r.flags = n & -65537 | 128, r) : null;
      case 3:
        return Uu(), pn(er), pn(Dn), je(), n = r.flags, n & 65536 && !(n & 128) ? (r.flags = n & -65537 | 128, r) : null;
      case 5:
        return rf(r), null;
      case 13:
        if (pn(bn), n = r.memoizedState, n !== null && n.dehydrated !== null) {
          if (r.alternate === null) throw Error(p(340));
          $l();
        }
        return n = r.flags, n & 65536 ? (r.flags = n & -65537 | 128, r) : null;
      case 19:
        return pn(bn), null;
      case 4:
        return Uu(), null;
      case 10:
        return Gd(r.type._context), null;
      case 22:
      case 23:
        return Ep(), null;
      case 24:
        return null;
      default:
        return null;
    }
  }
  var Ks = !1, Ar = !1, ig = typeof WeakSet == "function" ? WeakSet : Set, Ce = null;
  function Vo(n, r) {
    var l = n.ref;
    if (l !== null) if (typeof l == "function") try {
      l(null);
    } catch (o) {
      _n(n, r, o);
    }
    else l.current = null;
  }
  function Df(n, r, l) {
    try {
      l();
    } catch (o) {
      _n(n, r, o);
    }
  }
  var Ah = !1;
  function zh(n, r) {
    if (ks = Fa, n = Ts(), Fc(n)) {
      if ("selectionStart" in n) var l = { start: n.selectionStart, end: n.selectionEnd };
      else e: {
        l = (l = n.ownerDocument) && l.defaultView || window;
        var o = l.getSelection && l.getSelection();
        if (o && o.rangeCount !== 0) {
          l = o.anchorNode;
          var f = o.anchorOffset, v = o.focusNode;
          o = o.focusOffset;
          try {
            l.nodeType, v.nodeType;
          } catch {
            l = null;
            break e;
          }
          var C = 0, w = -1, D = -1, P = 0, J = 0, te = n, K = null;
          t: for (; ; ) {
            for (var ge; te !== l || f !== 0 && te.nodeType !== 3 || (w = C + f), te !== v || o !== 0 && te.nodeType !== 3 || (D = C + o), te.nodeType === 3 && (C += te.nodeValue.length), (ge = te.firstChild) !== null; )
              K = te, te = ge;
            for (; ; ) {
              if (te === n) break t;
              if (K === l && ++P === f && (w = C), K === v && ++J === o && (D = C), (ge = te.nextSibling) !== null) break;
              te = K, K = te.parentNode;
            }
            te = ge;
          }
          l = w === -1 || D === -1 ? null : { start: w, end: D };
        } else l = null;
      }
      l = l || { start: 0, end: 0 };
    } else l = null;
    for (Du = { focusedElem: n, selectionRange: l }, Fa = !1, Ce = r; Ce !== null; ) if (r = Ce, n = r.child, (r.subtreeFlags & 1028) !== 0 && n !== null) n.return = r, Ce = n;
    else for (; Ce !== null; ) {
      r = Ce;
      try {
        var Te = r.alternate;
        if (r.flags & 1024) switch (r.tag) {
          case 0:
          case 11:
          case 15:
            break;
          case 1:
            if (Te !== null) {
              var ke = Te.memoizedProps, jn = Te.memoizedState, z = r.stateNode, N = z.getSnapshotBeforeUpdate(r.elementType === r.type ? ke : mi(r.type, ke), jn);
              z.__reactInternalSnapshotBeforeUpdate = N;
            }
            break;
          case 3:
            var F = r.stateNode.containerInfo;
            F.nodeType === 1 ? F.textContent = "" : F.nodeType === 9 && F.documentElement && F.removeChild(F.documentElement);
            break;
          case 5:
          case 6:
          case 4:
          case 17:
            break;
          default:
            throw Error(p(163));
        }
      } catch (ee) {
        _n(r, r.return, ee);
      }
      if (n = r.sibling, n !== null) {
        n.return = r.return, Ce = n;
        break;
      }
      Ce = r.return;
    }
    return Te = Ah, Ah = !1, Te;
  }
  function Js(n, r, l) {
    var o = r.updateQueue;
    if (o = o !== null ? o.lastEffect : null, o !== null) {
      var f = o = o.next;
      do {
        if ((f.tag & n) === n) {
          var v = f.destroy;
          f.destroy = void 0, v !== void 0 && Df(r, l, v);
        }
        f = f.next;
      } while (f !== o);
    }
  }
  function ec(n, r) {
    if (r = r.updateQueue, r = r !== null ? r.lastEffect : null, r !== null) {
      var l = r = r.next;
      do {
        if ((l.tag & n) === n) {
          var o = l.create;
          l.destroy = o();
        }
        l = l.next;
      } while (l !== r);
    }
  }
  function pp(n) {
    var r = n.ref;
    if (r !== null) {
      var l = n.stateNode;
      switch (n.tag) {
        case 5:
          n = l;
          break;
        default:
          n = l;
      }
      typeof r == "function" ? r(n) : r.current = n;
    }
  }
  function Of(n) {
    var r = n.alternate;
    r !== null && (n.alternate = null, Of(r)), n.child = null, n.deletions = null, n.sibling = null, n.tag === 5 && (r = n.stateNode, r !== null && (delete r[Li], delete r[Ds], delete r[Os], delete r[Lo], delete r[rg])), n.stateNode = null, n.return = null, n.dependencies = null, n.memoizedProps = null, n.memoizedState = null, n.pendingProps = null, n.stateNode = null, n.updateQueue = null;
  }
  function tc(n) {
    return n.tag === 5 || n.tag === 3 || n.tag === 4;
  }
  function fl(n) {
    e: for (; ; ) {
      for (; n.sibling === null; ) {
        if (n.return === null || tc(n.return)) return null;
        n = n.return;
      }
      for (n.sibling.return = n.return, n = n.sibling; n.tag !== 5 && n.tag !== 6 && n.tag !== 18; ) {
        if (n.flags & 2 || n.child === null || n.tag === 4) continue e;
        n.child.return = n, n = n.child;
      }
      if (!(n.flags & 2)) return n.stateNode;
    }
  }
  function Vi(n, r, l) {
    var o = n.tag;
    if (o === 5 || o === 6) n = n.stateNode, r ? l.nodeType === 8 ? l.parentNode.insertBefore(n, r) : l.insertBefore(n, r) : (l.nodeType === 8 ? (r = l.parentNode, r.insertBefore(n, l)) : (r = l, r.appendChild(n)), l = l._reactRootContainer, l != null || r.onclick !== null || (r.onclick = Fl));
    else if (o !== 4 && (n = n.child, n !== null)) for (Vi(n, r, l), n = n.sibling; n !== null; ) Vi(n, r, l), n = n.sibling;
  }
  function Pi(n, r, l) {
    var o = n.tag;
    if (o === 5 || o === 6) n = n.stateNode, r ? l.insertBefore(n, r) : l.appendChild(n);
    else if (o !== 4 && (n = n.child, n !== null)) for (Pi(n, r, l), n = n.sibling; n !== null; ) Pi(n, r, l), n = n.sibling;
  }
  var zn = null, Ir = !1;
  function $r(n, r, l) {
    for (l = l.child; l !== null; ) Uh(n, r, l), l = l.sibling;
  }
  function Uh(n, r, l) {
    if (na && typeof na.onCommitFiberUnmount == "function") try {
      na.onCommitFiberUnmount(Dl, l);
    } catch {
    }
    switch (l.tag) {
      case 5:
        Ar || Vo(l, r);
      case 6:
        var o = zn, f = Ir;
        zn = null, $r(n, r, l), zn = o, Ir = f, zn !== null && (Ir ? (n = zn, l = l.stateNode, n.nodeType === 8 ? n.parentNode.removeChild(l) : n.removeChild(l)) : zn.removeChild(l.stateNode));
        break;
      case 18:
        zn !== null && (Ir ? (n = zn, l = l.stateNode, n.nodeType === 8 ? Mo(n.parentNode, l) : n.nodeType === 1 && Mo(n, l), fi(n)) : Mo(zn, l.stateNode));
        break;
      case 4:
        o = zn, f = Ir, zn = l.stateNode.containerInfo, Ir = !0, $r(n, r, l), zn = o, Ir = f;
        break;
      case 0:
      case 11:
      case 14:
      case 15:
        if (!Ar && (o = l.updateQueue, o !== null && (o = o.lastEffect, o !== null))) {
          f = o = o.next;
          do {
            var v = f, C = v.destroy;
            v = v.tag, C !== void 0 && (v & 2 || v & 4) && Df(l, r, C), f = f.next;
          } while (f !== o);
        }
        $r(n, r, l);
        break;
      case 1:
        if (!Ar && (Vo(l, r), o = l.stateNode, typeof o.componentWillUnmount == "function")) try {
          o.props = l.memoizedProps, o.state = l.memoizedState, o.componentWillUnmount();
        } catch (w) {
          _n(l, r, w);
        }
        $r(n, r, l);
        break;
      case 21:
        $r(n, r, l);
        break;
      case 22:
        l.mode & 1 ? (Ar = (o = Ar) || l.memoizedState !== null, $r(n, r, l), Ar = o) : $r(n, r, l);
        break;
      default:
        $r(n, r, l);
    }
  }
  function jh(n) {
    var r = n.updateQueue;
    if (r !== null) {
      n.updateQueue = null;
      var l = n.stateNode;
      l === null && (l = n.stateNode = new ig()), r.forEach(function(o) {
        var f = Wh.bind(null, n, o);
        l.has(o) || (l.add(o), o.then(f, f));
      });
    }
  }
  function yi(n, r) {
    var l = r.deletions;
    if (l !== null) for (var o = 0; o < l.length; o++) {
      var f = l[o];
      try {
        var v = n, C = r, w = C;
        e: for (; w !== null; ) {
          switch (w.tag) {
            case 5:
              zn = w.stateNode, Ir = !1;
              break e;
            case 3:
              zn = w.stateNode.containerInfo, Ir = !0;
              break e;
            case 4:
              zn = w.stateNode.containerInfo, Ir = !0;
              break e;
          }
          w = w.return;
        }
        if (zn === null) throw Error(p(160));
        Uh(v, C, f), zn = null, Ir = !1;
        var D = f.alternate;
        D !== null && (D.return = null), f.return = null;
      } catch (P) {
        _n(f, r, P);
      }
    }
    if (r.subtreeFlags & 12854) for (r = r.child; r !== null; ) vp(r, n), r = r.sibling;
  }
  function vp(n, r) {
    var l = n.alternate, o = n.flags;
    switch (n.tag) {
      case 0:
      case 11:
      case 14:
      case 15:
        if (yi(r, n), fa(n), o & 4) {
          try {
            Js(3, n, n.return), ec(3, n);
          } catch (ke) {
            _n(n, n.return, ke);
          }
          try {
            Js(5, n, n.return);
          } catch (ke) {
            _n(n, n.return, ke);
          }
        }
        break;
      case 1:
        yi(r, n), fa(n), o & 512 && l !== null && Vo(l, l.return);
        break;
      case 5:
        if (yi(r, n), fa(n), o & 512 && l !== null && Vo(l, l.return), n.flags & 32) {
          var f = n.stateNode;
          try {
            ue(f, "");
          } catch (ke) {
            _n(n, n.return, ke);
          }
        }
        if (o & 4 && (f = n.stateNode, f != null)) {
          var v = n.memoizedProps, C = l !== null ? l.memoizedProps : v, w = n.type, D = n.updateQueue;
          if (n.updateQueue = null, D !== null) try {
            w === "input" && v.type === "radio" && v.name != null && Xn(f, v), lr(w, C);
            var P = lr(w, v);
            for (C = 0; C < D.length; C += 2) {
              var J = D[C], te = D[C + 1];
              J === "style" ? sn(f, te) : J === "dangerouslySetInnerHTML" ? xi(f, te) : J === "children" ? ue(f, te) : rt(f, J, te, P);
            }
            switch (w) {
              case "input":
                ta(f, v);
                break;
              case "textarea":
                ri(f, v);
                break;
              case "select":
                var K = f._wrapperState.wasMultiple;
                f._wrapperState.wasMultiple = !!v.multiple;
                var ge = v.value;
                ge != null ? Nn(f, !!v.multiple, ge, !1) : K !== !!v.multiple && (v.defaultValue != null ? Nn(
                  f,
                  !!v.multiple,
                  v.defaultValue,
                  !0
                ) : Nn(f, !!v.multiple, v.multiple ? [] : "", !1));
            }
            f[Ds] = v;
          } catch (ke) {
            _n(n, n.return, ke);
          }
        }
        break;
      case 6:
        if (yi(r, n), fa(n), o & 4) {
          if (n.stateNode === null) throw Error(p(162));
          f = n.stateNode, v = n.memoizedProps;
          try {
            f.nodeValue = v;
          } catch (ke) {
            _n(n, n.return, ke);
          }
        }
        break;
      case 3:
        if (yi(r, n), fa(n), o & 4 && l !== null && l.memoizedState.isDehydrated) try {
          fi(r.containerInfo);
        } catch (ke) {
          _n(n, n.return, ke);
        }
        break;
      case 4:
        yi(r, n), fa(n);
        break;
      case 13:
        yi(r, n), fa(n), f = n.child, f.flags & 8192 && (v = f.memoizedState !== null, f.stateNode.isHidden = v, !v || f.alternate !== null && f.alternate.memoizedState !== null || (yp = lt())), o & 4 && jh(n);
        break;
      case 22:
        if (J = l !== null && l.memoizedState !== null, n.mode & 1 ? (Ar = (P = Ar) || J, yi(r, n), Ar = P) : yi(r, n), fa(n), o & 8192) {
          if (P = n.memoizedState !== null, (n.stateNode.isHidden = P) && !J && n.mode & 1) for (Ce = n, J = n.child; J !== null; ) {
            for (te = Ce = J; Ce !== null; ) {
              switch (K = Ce, ge = K.child, K.tag) {
                case 0:
                case 11:
                case 14:
                case 15:
                  Js(4, K, K.return);
                  break;
                case 1:
                  Vo(K, K.return);
                  var Te = K.stateNode;
                  if (typeof Te.componentWillUnmount == "function") {
                    o = K, l = K.return;
                    try {
                      r = o, Te.props = r.memoizedProps, Te.state = r.memoizedState, Te.componentWillUnmount();
                    } catch (ke) {
                      _n(o, l, ke);
                    }
                  }
                  break;
                case 5:
                  Vo(K, K.return);
                  break;
                case 22:
                  if (K.memoizedState !== null) {
                    nc(te);
                    continue;
                  }
              }
              ge !== null ? (ge.return = K, Ce = ge) : nc(te);
            }
            J = J.sibling;
          }
          e: for (J = null, te = n; ; ) {
            if (te.tag === 5) {
              if (J === null) {
                J = te;
                try {
                  f = te.stateNode, P ? (v = f.style, typeof v.setProperty == "function" ? v.setProperty("display", "none", "important") : v.display = "none") : (w = te.stateNode, D = te.memoizedProps.style, C = D != null && D.hasOwnProperty("display") ? D.display : null, w.style.display = Wt("display", C));
                } catch (ke) {
                  _n(n, n.return, ke);
                }
              }
            } else if (te.tag === 6) {
              if (J === null) try {
                te.stateNode.nodeValue = P ? "" : te.memoizedProps;
              } catch (ke) {
                _n(n, n.return, ke);
              }
            } else if ((te.tag !== 22 && te.tag !== 23 || te.memoizedState === null || te === n) && te.child !== null) {
              te.child.return = te, te = te.child;
              continue;
            }
            if (te === n) break e;
            for (; te.sibling === null; ) {
              if (te.return === null || te.return === n) break e;
              J === te && (J = null), te = te.return;
            }
            J === te && (J = null), te.sibling.return = te.return, te = te.sibling;
          }
        }
        break;
      case 19:
        yi(r, n), fa(n), o & 4 && jh(n);
        break;
      case 21:
        break;
      default:
        yi(
          r,
          n
        ), fa(n);
    }
  }
  function fa(n) {
    var r = n.flags;
    if (r & 2) {
      try {
        e: {
          for (var l = n.return; l !== null; ) {
            if (tc(l)) {
              var o = l;
              break e;
            }
            l = l.return;
          }
          throw Error(p(160));
        }
        switch (o.tag) {
          case 5:
            var f = o.stateNode;
            o.flags & 32 && (ue(f, ""), o.flags &= -33);
            var v = fl(n);
            Pi(n, v, f);
            break;
          case 3:
          case 4:
            var C = o.stateNode.containerInfo, w = fl(n);
            Vi(n, w, C);
            break;
          default:
            throw Error(p(161));
        }
      } catch (D) {
        _n(n, n.return, D);
      }
      n.flags &= -3;
    }
    r & 4096 && (n.flags &= -4097);
  }
  function lg(n, r, l) {
    Ce = n, hp(n);
  }
  function hp(n, r, l) {
    for (var o = (n.mode & 1) !== 0; Ce !== null; ) {
      var f = Ce, v = f.child;
      if (f.tag === 22 && o) {
        var C = f.memoizedState !== null || Ks;
        if (!C) {
          var w = f.alternate, D = w !== null && w.memoizedState !== null || Ar;
          w = Ks;
          var P = Ar;
          if (Ks = C, (Ar = D) && !P) for (Ce = f; Ce !== null; ) C = Ce, D = C.child, C.tag === 22 && C.memoizedState !== null ? mp(f) : D !== null ? (D.return = C, Ce = D) : mp(f);
          for (; v !== null; ) Ce = v, hp(v), v = v.sibling;
          Ce = f, Ks = w, Ar = P;
        }
        Fh(n);
      } else f.subtreeFlags & 8772 && v !== null ? (v.return = f, Ce = v) : Fh(n);
    }
  }
  function Fh(n) {
    for (; Ce !== null; ) {
      var r = Ce;
      if (r.flags & 8772) {
        var l = r.alternate;
        try {
          if (r.flags & 8772) switch (r.tag) {
            case 0:
            case 11:
            case 15:
              Ar || ec(5, r);
              break;
            case 1:
              var o = r.stateNode;
              if (r.flags & 4 && !Ar) if (l === null) o.componentDidMount();
              else {
                var f = r.elementType === r.type ? l.memoizedProps : mi(r.type, l.memoizedProps);
                o.componentDidUpdate(f, l.memoizedState, o.__reactInternalSnapshotBeforeUpdate);
              }
              var v = r.updateQueue;
              v !== null && ep(r, v, o);
              break;
            case 3:
              var C = r.updateQueue;
              if (C !== null) {
                if (l = null, r.child !== null) switch (r.child.tag) {
                  case 5:
                    l = r.child.stateNode;
                    break;
                  case 1:
                    l = r.child.stateNode;
                }
                ep(r, C, l);
              }
              break;
            case 5:
              var w = r.stateNode;
              if (l === null && r.flags & 4) {
                l = w;
                var D = r.memoizedProps;
                switch (r.type) {
                  case "button":
                  case "input":
                  case "select":
                  case "textarea":
                    D.autoFocus && l.focus();
                    break;
                  case "img":
                    D.src && (l.src = D.src);
                }
              }
              break;
            case 6:
              break;
            case 4:
              break;
            case 12:
              break;
            case 13:
              if (r.memoizedState === null) {
                var P = r.alternate;
                if (P !== null) {
                  var J = P.memoizedState;
                  if (J !== null) {
                    var te = J.dehydrated;
                    te !== null && fi(te);
                  }
                }
              }
              break;
            case 19:
            case 17:
            case 21:
            case 22:
            case 23:
            case 25:
              break;
            default:
              throw Error(p(163));
          }
          Ar || r.flags & 512 && pp(r);
        } catch (K) {
          _n(r, r.return, K);
        }
      }
      if (r === n) {
        Ce = null;
        break;
      }
      if (l = r.sibling, l !== null) {
        l.return = r.return, Ce = l;
        break;
      }
      Ce = r.return;
    }
  }
  function nc(n) {
    for (; Ce !== null; ) {
      var r = Ce;
      if (r === n) {
        Ce = null;
        break;
      }
      var l = r.sibling;
      if (l !== null) {
        l.return = r.return, Ce = l;
        break;
      }
      Ce = r.return;
    }
  }
  function mp(n) {
    for (; Ce !== null; ) {
      var r = Ce;
      try {
        switch (r.tag) {
          case 0:
          case 11:
          case 15:
            var l = r.return;
            try {
              ec(4, r);
            } catch (D) {
              _n(r, l, D);
            }
            break;
          case 1:
            var o = r.stateNode;
            if (typeof o.componentDidMount == "function") {
              var f = r.return;
              try {
                o.componentDidMount();
              } catch (D) {
                _n(r, f, D);
              }
            }
            var v = r.return;
            try {
              pp(r);
            } catch (D) {
              _n(r, v, D);
            }
            break;
          case 5:
            var C = r.return;
            try {
              pp(r);
            } catch (D) {
              _n(r, C, D);
            }
        }
      } catch (D) {
        _n(r, r.return, D);
      }
      if (r === n) {
        Ce = null;
        break;
      }
      var w = r.sibling;
      if (w !== null) {
        w.return = r.return, Ce = w;
        break;
      }
      Ce = r.return;
    }
  }
  var ug = Math.ceil, Gl = Tt.ReactCurrentDispatcher, Yu = Tt.ReactCurrentOwner, Sr = Tt.ReactCurrentBatchConfig, Dt = 0, nr = null, Qn = null, Er = 0, ba = 0, Po = Pa(0), Un = 0, rc = null, Bi = 0, Bo = 0, Nf = 0, ac = null, da = null, yp = 0, Io = 1 / 0, ka = null, $o = !1, Wu = null, ql = null, Mf = !1, dl = null, ic = 0, Xl = 0, Yo = null, lc = -1, zr = 0;
  function Zn() {
    return Dt & 6 ? lt() : lc !== -1 ? lc : lc = lt();
  }
  function Ii(n) {
    return n.mode & 1 ? Dt & 2 && Er !== 0 ? Er & -Er : ag.transition !== null ? (zr === 0 && (zr = yo()), zr) : (n = Ht, n !== 0 || (n = window.event, n = n === void 0 ? 16 : To(n.type)), n) : 1;
  }
  function Yr(n, r, l, o) {
    if (50 < Xl) throw Xl = 0, Yo = null, Error(p(185));
    Ji(n, l, o), (!(Dt & 2) || n !== nr) && (n === nr && (!(Dt & 2) && (Bo |= l), Un === 4 && gi(n, Er)), pa(n, o), l === 1 && Dt === 0 && !(r.mode & 1) && (Io = lt() + 500, zo && zi()));
  }
  function pa(n, r) {
    var l = n.callbackNode;
    Eu(n, r);
    var o = ci(n, n === nr ? Er : 0);
    if (o === 0) l !== null && hr(l), n.callbackNode = null, n.callbackPriority = 0;
    else if (r = o & -o, n.callbackPriority !== r) {
      if (l != null && hr(l), r === 1) n.tag === 0 ? Vl(gp.bind(null, n)) : Xc(gp.bind(null, n)), No(function() {
        !(Dt & 6) && zi();
      }), l = null;
      else {
        switch (So(o)) {
          case 1:
            l = oi;
            break;
          case 4:
            l = gu;
            break;
          case 16:
            l = Su;
            break;
          case 536870912:
            l = vo;
            break;
          default:
            l = Su;
        }
        l = Zh(l, Lf.bind(null, n));
      }
      n.callbackPriority = r, n.callbackNode = l;
    }
  }
  function Lf(n, r) {
    if (lc = -1, zr = 0, Dt & 6) throw Error(p(327));
    var l = n.callbackNode;
    if (Wo() && n.callbackNode !== l) return null;
    var o = ci(n, n === nr ? Er : 0);
    if (o === 0) return null;
    if (o & 30 || o & n.expiredLanes || r) r = Af(n, o);
    else {
      r = o;
      var f = Dt;
      Dt |= 2;
      var v = Vh();
      (nr !== n || Er !== r) && (ka = null, Io = lt() + 500, pl(n, r));
      do
        try {
          Ph();
          break;
        } catch (w) {
          Hh(n, w);
        }
      while (!0);
      Zd(), Gl.current = v, Dt = f, Qn !== null ? r = 0 : (nr = null, Er = 0, r = Un);
    }
    if (r !== 0) {
      if (r === 2 && (f = Nl(n), f !== 0 && (o = f, r = uc(n, f))), r === 1) throw l = rc, pl(n, 0), gi(n, o), pa(n, lt()), l;
      if (r === 6) gi(n, o);
      else {
        if (f = n.current.alternate, !(o & 30) && !og(f) && (r = Af(n, o), r === 2 && (v = Nl(n), v !== 0 && (o = v, r = uc(n, v))), r === 1)) throw l = rc, pl(n, 0), gi(n, o), pa(n, lt()), l;
        switch (n.finishedWork = f, n.finishedLanes = o, r) {
          case 0:
          case 1:
            throw Error(p(345));
          case 2:
            Gu(n, da, ka);
            break;
          case 3:
            if (gi(n, o), (o & 130023424) === o && (r = yp + 500 - lt(), 10 < r)) {
              if (ci(n, 0) !== 0) break;
              if (f = n.suspendedLanes, (f & o) !== o) {
                Zn(), n.pingedLanes |= n.suspendedLanes & f;
                break;
              }
              n.timeoutHandle = Zc(Gu.bind(null, n, da, ka), r);
              break;
            }
            Gu(n, da, ka);
            break;
          case 4:
            if (gi(n, o), (o & 4194240) === o) break;
            for (r = n.eventTimes, f = -1; 0 < o; ) {
              var C = 31 - Hr(o);
              v = 1 << C, C = r[C], C > f && (f = C), o &= ~v;
            }
            if (o = f, o = lt() - o, o = (120 > o ? 120 : 480 > o ? 480 : 1080 > o ? 1080 : 1920 > o ? 1920 : 3e3 > o ? 3e3 : 4320 > o ? 4320 : 1960 * ug(o / 1960)) - o, 10 < o) {
              n.timeoutHandle = Zc(Gu.bind(null, n, da, ka), o);
              break;
            }
            Gu(n, da, ka);
            break;
          case 5:
            Gu(n, da, ka);
            break;
          default:
            throw Error(p(329));
        }
      }
    }
    return pa(n, lt()), n.callbackNode === l ? Lf.bind(null, n) : null;
  }
  function uc(n, r) {
    var l = ac;
    return n.current.memoizedState.isDehydrated && (pl(n, r).flags |= 256), n = Af(n, r), n !== 2 && (r = da, da = l, r !== null && Qu(r)), n;
  }
  function Qu(n) {
    da === null ? da = n : da.push.apply(da, n);
  }
  function og(n) {
    for (var r = n; ; ) {
      if (r.flags & 16384) {
        var l = r.updateQueue;
        if (l !== null && (l = l.stores, l !== null)) for (var o = 0; o < l.length; o++) {
          var f = l[o], v = f.getSnapshot;
          f = f.value;
          try {
            if (!pi(v(), f)) return !1;
          } catch {
            return !1;
          }
        }
      }
      if (l = r.child, r.subtreeFlags & 16384 && l !== null) l.return = r, r = l;
      else {
        if (r === n) break;
        for (; r.sibling === null; ) {
          if (r.return === null || r.return === n) return !0;
          r = r.return;
        }
        r.sibling.return = r.return, r = r.sibling;
      }
    }
    return !0;
  }
  function gi(n, r) {
    for (r &= ~Nf, r &= ~Bo, n.suspendedLanes |= r, n.pingedLanes &= ~r, n = n.expirationTimes; 0 < r; ) {
      var l = 31 - Hr(r), o = 1 << l;
      n[l] = -1, r &= ~o;
    }
  }
  function gp(n) {
    if (Dt & 6) throw Error(p(327));
    Wo();
    var r = ci(n, 0);
    if (!(r & 1)) return pa(n, lt()), null;
    var l = Af(n, r);
    if (n.tag !== 0 && l === 2) {
      var o = Nl(n);
      o !== 0 && (r = o, l = uc(n, o));
    }
    if (l === 1) throw l = rc, pl(n, 0), gi(n, r), pa(n, lt()), l;
    if (l === 6) throw Error(p(345));
    return n.finishedWork = n.current.alternate, n.finishedLanes = r, Gu(n, da, ka), pa(n, lt()), null;
  }
  function Sp(n, r) {
    var l = Dt;
    Dt |= 1;
    try {
      return n(r);
    } finally {
      Dt = l, Dt === 0 && (Io = lt() + 500, zo && zi());
    }
  }
  function Zu(n) {
    dl !== null && dl.tag === 0 && !(Dt & 6) && Wo();
    var r = Dt;
    Dt |= 1;
    var l = Sr.transition, o = Ht;
    try {
      if (Sr.transition = null, Ht = 1, n) return n();
    } finally {
      Ht = o, Sr.transition = l, Dt = r, !(Dt & 6) && zi();
    }
  }
  function Ep() {
    ba = Po.current, pn(Po);
  }
  function pl(n, r) {
    n.finishedWork = null, n.finishedLanes = 0;
    var l = n.timeoutHandle;
    if (l !== -1 && (n.timeoutHandle = -1, Id(l)), Qn !== null) for (l = Qn.return; l !== null; ) {
      var o = l;
      switch (Jc(o), o.tag) {
        case 1:
          o = o.type.childContextTypes, o != null && Ao();
          break;
        case 3:
          Uu(), pn(er), pn(Dn), je();
          break;
        case 5:
          rf(o);
          break;
        case 4:
          Uu();
          break;
        case 13:
          pn(bn);
          break;
        case 19:
          pn(bn);
          break;
        case 10:
          Gd(o.type._context);
          break;
        case 22:
        case 23:
          Ep();
      }
      l = l.return;
    }
    if (nr = n, Qn = n = Kl(n.current, null), Er = ba = r, Un = 0, rc = null, Nf = Bo = Bi = 0, da = ac = null, Au !== null) {
      for (r = 0; r < Au.length; r++) if (l = Au[r], o = l.interleaved, o !== null) {
        l.interleaved = null;
        var f = o.next, v = l.pending;
        if (v !== null) {
          var C = v.next;
          v.next = f, o.next = C;
        }
        l.pending = o;
      }
      Au = null;
    }
    return n;
  }
  function Hh(n, r) {
    do {
      var l = Qn;
      try {
        if (Zd(), yt.current = Bu, lf) {
          for (var o = Pt.memoizedState; o !== null; ) {
            var f = o.queue;
            f !== null && (f.pending = null), o = o.next;
          }
          lf = !1;
        }
        if (rn = 0, or = $n = Pt = null, Fs = !1, ju = 0, Yu.current = null, l === null || l.return === null) {
          Un = 1, rc = r, Qn = null;
          break;
        }
        e: {
          var v = n, C = l.return, w = l, D = r;
          if (r = Er, w.flags |= 32768, D !== null && typeof D == "object" && typeof D.then == "function") {
            var P = D, J = w, te = J.tag;
            if (!(J.mode & 1) && (te === 0 || te === 11 || te === 15)) {
              var K = J.alternate;
              K ? (J.updateQueue = K.updateQueue, J.memoizedState = K.memoizedState, J.lanes = K.lanes) : (J.updateQueue = null, J.memoizedState = null);
            }
            var ge = wh(C);
            if (ge !== null) {
              ge.flags &= -257, Zl(ge, C, w, v, r), ge.mode & 1 && op(v, P, r), r = ge, D = P;
              var Te = r.updateQueue;
              if (Te === null) {
                var ke = /* @__PURE__ */ new Set();
                ke.add(D), r.updateQueue = ke;
              } else Te.add(D);
              break e;
            } else {
              if (!(r & 1)) {
                op(v, P, r), Cp();
                break e;
              }
              D = Error(p(426));
            }
          } else if (Cn && w.mode & 1) {
            var jn = wh(C);
            if (jn !== null) {
              !(jn.flags & 65536) && (jn.flags |= 256), Zl(jn, C, w, v, r), ol(Iu(D, w));
              break e;
            }
          }
          v = D = Iu(D, w), Un !== 4 && (Un = 2), ac === null ? ac = [v] : ac.push(v), v = C;
          do {
            switch (v.tag) {
              case 3:
                v.flags |= 65536, r &= -r, v.lanes |= r;
                var z = Rh(v, D, r);
                Eh(v, z);
                break e;
              case 1:
                w = D;
                var N = v.type, F = v.stateNode;
                if (!(v.flags & 128) && (typeof N.getDerivedStateFromError == "function" || F !== null && typeof F.componentDidCatch == "function" && (ql === null || !ql.has(F)))) {
                  v.flags |= 65536, r &= -r, v.lanes |= r;
                  var ee = up(v, w, r);
                  Eh(v, ee);
                  break e;
                }
            }
            v = v.return;
          } while (v !== null);
        }
        Ih(l);
      } catch (Re) {
        r = Re, Qn === l && l !== null && (Qn = l = l.return);
        continue;
      }
      break;
    } while (!0);
  }
  function Vh() {
    var n = Gl.current;
    return Gl.current = Bu, n === null ? Bu : n;
  }
  function Cp() {
    (Un === 0 || Un === 3 || Un === 2) && (Un = 4), nr === null || !(Bi & 268435455) && !(Bo & 268435455) || gi(nr, Er);
  }
  function Af(n, r) {
    var l = Dt;
    Dt |= 2;
    var o = Vh();
    (nr !== n || Er !== r) && (ka = null, pl(n, r));
    do
      try {
        sg();
        break;
      } catch (f) {
        Hh(n, f);
      }
    while (!0);
    if (Zd(), Dt = l, Gl.current = o, Qn !== null) throw Error(p(261));
    return nr = null, Er = 0, Un;
  }
  function sg() {
    for (; Qn !== null; ) Bh(Qn);
  }
  function Ph() {
    for (; Qn !== null && !li(); ) Bh(Qn);
  }
  function Bh(n) {
    var r = Qh(n.alternate, n, ba);
    n.memoizedProps = n.pendingProps, r === null ? Ih(n) : Qn = r, Yu.current = null;
  }
  function Ih(n) {
    var r = n;
    do {
      var l = r.alternate;
      if (n = r.return, r.flags & 32768) {
        if (l = kf(l, r), l !== null) {
          l.flags &= 32767, Qn = l;
          return;
        }
        if (n !== null) n.flags |= 32768, n.subtreeFlags = 0, n.deletions = null;
        else {
          Un = 6, Qn = null;
          return;
        }
      } else if (l = Lh(l, r, ba), l !== null) {
        Qn = l;
        return;
      }
      if (r = r.sibling, r !== null) {
        Qn = r;
        return;
      }
      Qn = r = n;
    } while (r !== null);
    Un === 0 && (Un = 5);
  }
  function Gu(n, r, l) {
    var o = Ht, f = Sr.transition;
    try {
      Sr.transition = null, Ht = 1, cg(n, r, l, o);
    } finally {
      Sr.transition = f, Ht = o;
    }
    return null;
  }
  function cg(n, r, l, o) {
    do
      Wo();
    while (dl !== null);
    if (Dt & 6) throw Error(p(327));
    l = n.finishedWork;
    var f = n.finishedLanes;
    if (l === null) return null;
    if (n.finishedWork = null, n.finishedLanes = 0, l === n.current) throw Error(p(177));
    n.callbackNode = null, n.callbackPriority = 0;
    var v = l.lanes | l.childLanes;
    if (_d(n, v), n === nr && (Qn = nr = null, Er = 0), !(l.subtreeFlags & 2064) && !(l.flags & 2064) || Mf || (Mf = !0, Zh(Su, function() {
      return Wo(), null;
    })), v = (l.flags & 15990) !== 0, l.subtreeFlags & 15990 || v) {
      v = Sr.transition, Sr.transition = null;
      var C = Ht;
      Ht = 1;
      var w = Dt;
      Dt |= 4, Yu.current = null, zh(n, l), vp(l, n), bo(Du), Fa = !!ks, Du = ks = null, n.current = l, lg(l), ui(), Dt = w, Ht = C, Sr.transition = v;
    } else n.current = l;
    if (Mf && (Mf = !1, dl = n, ic = f), v = n.pendingLanes, v === 0 && (ql = null), vs(l.stateNode), pa(n, lt()), r !== null) for (o = n.onRecoverableError, l = 0; l < r.length; l++) f = r[l], o(f.value, { componentStack: f.stack, digest: f.digest });
    if ($o) throw $o = !1, n = Wu, Wu = null, n;
    return ic & 1 && n.tag !== 0 && Wo(), v = n.pendingLanes, v & 1 ? n === Yo ? Xl++ : (Xl = 0, Yo = n) : Xl = 0, zi(), null;
  }
  function Wo() {
    if (dl !== null) {
      var n = So(ic), r = Sr.transition, l = Ht;
      try {
        if (Sr.transition = null, Ht = 16 > n ? 16 : n, dl === null) var o = !1;
        else {
          if (n = dl, dl = null, ic = 0, Dt & 6) throw Error(p(331));
          var f = Dt;
          for (Dt |= 4, Ce = n.current; Ce !== null; ) {
            var v = Ce, C = v.child;
            if (Ce.flags & 16) {
              var w = v.deletions;
              if (w !== null) {
                for (var D = 0; D < w.length; D++) {
                  var P = w[D];
                  for (Ce = P; Ce !== null; ) {
                    var J = Ce;
                    switch (J.tag) {
                      case 0:
                      case 11:
                      case 15:
                        Js(8, J, v);
                    }
                    var te = J.child;
                    if (te !== null) te.return = J, Ce = te;
                    else for (; Ce !== null; ) {
                      J = Ce;
                      var K = J.sibling, ge = J.return;
                      if (Of(J), J === P) {
                        Ce = null;
                        break;
                      }
                      if (K !== null) {
                        K.return = ge, Ce = K;
                        break;
                      }
                      Ce = ge;
                    }
                  }
                }
                var Te = v.alternate;
                if (Te !== null) {
                  var ke = Te.child;
                  if (ke !== null) {
                    Te.child = null;
                    do {
                      var jn = ke.sibling;
                      ke.sibling = null, ke = jn;
                    } while (ke !== null);
                  }
                }
                Ce = v;
              }
            }
            if (v.subtreeFlags & 2064 && C !== null) C.return = v, Ce = C;
            else e: for (; Ce !== null; ) {
              if (v = Ce, v.flags & 2048) switch (v.tag) {
                case 0:
                case 11:
                case 15:
                  Js(9, v, v.return);
              }
              var z = v.sibling;
              if (z !== null) {
                z.return = v.return, Ce = z;
                break e;
              }
              Ce = v.return;
            }
          }
          var N = n.current;
          for (Ce = N; Ce !== null; ) {
            C = Ce;
            var F = C.child;
            if (C.subtreeFlags & 2064 && F !== null) F.return = C, Ce = F;
            else e: for (C = N; Ce !== null; ) {
              if (w = Ce, w.flags & 2048) try {
                switch (w.tag) {
                  case 0:
                  case 11:
                  case 15:
                    ec(9, w);
                }
              } catch (Re) {
                _n(w, w.return, Re);
              }
              if (w === C) {
                Ce = null;
                break e;
              }
              var ee = w.sibling;
              if (ee !== null) {
                ee.return = w.return, Ce = ee;
                break e;
              }
              Ce = w.return;
            }
          }
          if (Dt = f, zi(), na && typeof na.onPostCommitFiberRoot == "function") try {
            na.onPostCommitFiberRoot(Dl, n);
          } catch {
          }
          o = !0;
        }
        return o;
      } finally {
        Ht = l, Sr.transition = r;
      }
    }
    return !1;
  }
  function $h(n, r, l) {
    r = Iu(l, r), r = Rh(n, r, 1), n = Yl(n, r, 1), r = Zn(), n !== null && (Ji(n, 1, r), pa(n, r));
  }
  function _n(n, r, l) {
    if (n.tag === 3) $h(n, n, l);
    else for (; r !== null; ) {
      if (r.tag === 3) {
        $h(r, n, l);
        break;
      } else if (r.tag === 1) {
        var o = r.stateNode;
        if (typeof r.type.getDerivedStateFromError == "function" || typeof o.componentDidCatch == "function" && (ql === null || !ql.has(o))) {
          n = Iu(l, n), n = up(r, n, 1), r = Yl(r, n, 1), n = Zn(), r !== null && (Ji(r, 1, n), pa(r, n));
          break;
        }
      }
      r = r.return;
    }
  }
  function fg(n, r, l) {
    var o = n.pingCache;
    o !== null && o.delete(r), r = Zn(), n.pingedLanes |= n.suspendedLanes & l, nr === n && (Er & l) === l && (Un === 4 || Un === 3 && (Er & 130023424) === Er && 500 > lt() - yp ? pl(n, 0) : Nf |= l), pa(n, r);
  }
  function Yh(n, r) {
    r === 0 && (n.mode & 1 ? (r = _a, _a <<= 1, !(_a & 130023424) && (_a = 4194304)) : r = 1);
    var l = Zn();
    n = Ra(n, r), n !== null && (Ji(n, r, l), pa(n, l));
  }
  function dg(n) {
    var r = n.memoizedState, l = 0;
    r !== null && (l = r.retryLane), Yh(n, l);
  }
  function Wh(n, r) {
    var l = 0;
    switch (n.tag) {
      case 13:
        var o = n.stateNode, f = n.memoizedState;
        f !== null && (l = f.retryLane);
        break;
      case 19:
        o = n.stateNode;
        break;
      default:
        throw Error(p(314));
    }
    o !== null && o.delete(r), Yh(n, l);
  }
  var Qh;
  Qh = function(n, r, l) {
    if (n !== null) if (n.memoizedProps !== r.pendingProps || er.current) Yn = !0;
    else {
      if (!(n.lanes & l) && !(r.flags & 128)) return Yn = !1, qs(n, r, l);
      Yn = !!(n.flags & 131072);
    }
    else Yn = !1, Cn && r.flags & 1048576 && mh(r, ul, r.index);
    switch (r.lanes = 0, r.tag) {
      case 2:
        var o = r.type;
        Ya(n, r), n = r.pendingProps;
        var f = ia(r, Dn.current);
        wn(r, l), f = Wl(null, r, o, n, f, l);
        var v = hi();
        return r.flags |= 1, typeof f == "object" && f !== null && typeof f.render == "function" && f.$$typeof === void 0 ? (r.tag = 1, r.memoizedState = null, r.updateQueue = null, Bn(o) ? (v = !0, ur(r)) : v = !1, r.memoizedState = f.state !== null && f.state !== void 0 ? f.state : null, Jd(r), f.updater = xf, r.stateNode = f, f._reactInternals = r, Ys(r, o, n, l), r = Zs(null, r, o, !0, v, l)) : (r.tag = 0, Cn && v && Kc(r), gr(null, r, f, l), r = r.child), r;
      case 16:
        o = r.elementType;
        e: {
          switch (Ya(n, r), n = r.pendingProps, f = o._init, o = f(o._payload), r.type = o, f = r.tag = vg(o), n = mi(o, n), f) {
            case 0:
              r = bh(null, r, o, n, l);
              break e;
            case 1:
              r = kh(null, r, o, n, l);
              break e;
            case 11:
              r = ca(null, r, o, n, l);
              break e;
            case 14:
              r = $u(null, r, o, mi(o.type, n), l);
              break e;
          }
          throw Error(p(
            306,
            o,
            ""
          ));
        }
        return r;
      case 0:
        return o = r.type, f = r.pendingProps, f = r.elementType === o ? f : mi(o, f), bh(n, r, o, f, l);
      case 1:
        return o = r.type, f = r.pendingProps, f = r.elementType === o ? f : mi(o, f), kh(n, r, o, f, l);
      case 3:
        e: {
          if (Ho(r), n === null) throw Error(p(387));
          o = r.pendingProps, v = r.memoizedState, f = v.element, Sh(n, r), Ls(r, o, null, l);
          var C = r.memoizedState;
          if (o = C.element, v.isDehydrated) if (v = { element: o, isDehydrated: !1, cache: C.cache, pendingSuspenseBoundaries: C.pendingSuspenseBoundaries, transitions: C.transitions }, r.updateQueue.baseState = v, r.memoizedState = v, r.flags & 256) {
            f = Iu(Error(p(423)), r), r = Dh(n, r, o, l, f);
            break e;
          } else if (o !== f) {
            f = Iu(Error(p(424)), r), r = Dh(n, r, o, l, f);
            break e;
          } else for (ua = Mi(r.stateNode.containerInfo.firstChild), la = r, Cn = !0, Ia = null, l = ve(r, null, o, l), r.child = l; l; ) l.flags = l.flags & -3 | 4096, l = l.sibling;
          else {
            if ($l(), o === f) {
              r = Wa(n, r, l);
              break e;
            }
            gr(n, r, o, l);
          }
          r = r.child;
        }
        return r;
      case 5:
        return Ch(r), n === null && Wd(r), o = r.type, f = r.pendingProps, v = n !== null ? n.memoizedProps : null, C = f.children, Qc(o, f) ? C = null : v !== null && Qc(o, v) && (r.flags |= 32), sp(n, r), gr(n, r, C, l), r.child;
      case 6:
        return n === null && Wd(r), null;
      case 13:
        return bf(n, r, l);
      case 4:
        return tp(r, r.stateNode.containerInfo), o = r.pendingProps, n === null ? r.child = Ln(r, null, o, l) : gr(n, r, o, l), r.child;
      case 11:
        return o = r.type, f = r.pendingProps, f = r.elementType === o ? f : mi(o, f), ca(n, r, o, f, l);
      case 7:
        return gr(n, r, r.pendingProps, l), r.child;
      case 8:
        return gr(n, r, r.pendingProps.children, l), r.child;
      case 12:
        return gr(n, r, r.pendingProps.children, l), r.child;
      case 10:
        e: {
          if (o = r.type._context, f = r.pendingProps, v = r.memoizedProps, C = f.value, Le(Ta, o._currentValue), o._currentValue = C, v !== null) if (pi(v.value, C)) {
            if (v.children === f.children && !er.current) {
              r = Wa(n, r, l);
              break e;
            }
          } else for (v = r.child, v !== null && (v.return = r); v !== null; ) {
            var w = v.dependencies;
            if (w !== null) {
              C = v.child;
              for (var D = w.firstContext; D !== null; ) {
                if (D.context === o) {
                  if (v.tag === 1) {
                    D = sl(-1, l & -l), D.tag = 2;
                    var P = v.updateQueue;
                    if (P !== null) {
                      P = P.shared;
                      var J = P.pending;
                      J === null ? D.next = D : (D.next = J.next, J.next = D), P.pending = D;
                    }
                  }
                  v.lanes |= l, D = v.alternate, D !== null && (D.lanes |= l), qd(
                    v.return,
                    l,
                    r
                  ), w.lanes |= l;
                  break;
                }
                D = D.next;
              }
            } else if (v.tag === 10) C = v.type === r.type ? null : v.child;
            else if (v.tag === 18) {
              if (C = v.return, C === null) throw Error(p(341));
              C.lanes |= l, w = C.alternate, w !== null && (w.lanes |= l), qd(C, l, r), C = v.sibling;
            } else C = v.child;
            if (C !== null) C.return = v;
            else for (C = v; C !== null; ) {
              if (C === r) {
                C = null;
                break;
              }
              if (v = C.sibling, v !== null) {
                v.return = C.return, C = v;
                break;
              }
              C = C.return;
            }
            v = C;
          }
          gr(n, r, f.children, l), r = r.child;
        }
        return r;
      case 9:
        return f = r.type, o = r.pendingProps.children, wn(r, l), f = $a(f), o = o(f), r.flags |= 1, gr(n, r, o, l), r.child;
      case 14:
        return o = r.type, f = mi(o, r.pendingProps), f = mi(o.type, f), $u(n, r, o, f, l);
      case 15:
        return ut(n, r, r.type, r.pendingProps, l);
      case 17:
        return o = r.type, f = r.pendingProps, f = r.elementType === o ? f : mi(o, f), Ya(n, r), r.tag = 1, Bn(o) ? (n = !0, ur(r)) : n = !1, wn(r, l), Tf(r, o, f), Ys(r, o, f, l), Zs(null, r, o, !0, n, l);
      case 19:
        return Hi(n, r, l);
      case 22:
        return Qs(n, r, l);
    }
    throw Error(p(156, r.tag));
  };
  function Zh(n, r) {
    return yn(n, r);
  }
  function pg(n, r, l, o) {
    this.tag = n, this.key = l, this.sibling = this.child = this.return = this.stateNode = this.type = this.elementType = null, this.index = 0, this.ref = null, this.pendingProps = r, this.dependencies = this.memoizedState = this.updateQueue = this.memoizedProps = null, this.mode = o, this.subtreeFlags = this.flags = 0, this.deletions = null, this.childLanes = this.lanes = 0, this.alternate = null;
  }
  function Za(n, r, l, o) {
    return new pg(n, r, l, o);
  }
  function _p(n) {
    return n = n.prototype, !(!n || !n.isReactComponent);
  }
  function vg(n) {
    if (typeof n == "function") return _p(n) ? 1 : 0;
    if (n != null) {
      if (n = n.$$typeof, n === zt) return 11;
      if (n === Ut) return 14;
    }
    return 2;
  }
  function Kl(n, r) {
    var l = n.alternate;
    return l === null ? (l = Za(n.tag, r, n.key, n.mode), l.elementType = n.elementType, l.type = n.type, l.stateNode = n.stateNode, l.alternate = n, n.alternate = l) : (l.pendingProps = r, l.type = n.type, l.flags = 0, l.subtreeFlags = 0, l.deletions = null), l.flags = n.flags & 14680064, l.childLanes = n.childLanes, l.lanes = n.lanes, l.child = n.child, l.memoizedProps = n.memoizedProps, l.memoizedState = n.memoizedState, l.updateQueue = n.updateQueue, r = n.dependencies, l.dependencies = r === null ? null : { lanes: r.lanes, firstContext: r.firstContext }, l.sibling = n.sibling, l.index = n.index, l.ref = n.ref, l;
  }
  function oc(n, r, l, o, f, v) {
    var C = 2;
    if (o = n, typeof n == "function") _p(n) && (C = 1);
    else if (typeof n == "string") C = 5;
    else e: switch (n) {
      case Qe:
        return vl(l.children, f, v, r);
      case vn:
        C = 8, f |= 8;
        break;
      case Zt:
        return n = Za(12, l, r, f | 2), n.elementType = Zt, n.lanes = v, n;
      case He:
        return n = Za(13, l, r, f), n.elementType = He, n.lanes = v, n;
      case Yt:
        return n = Za(19, l, r, f), n.elementType = Yt, n.lanes = v, n;
      case De:
        return Jl(l, f, v, r);
      default:
        if (typeof n == "object" && n !== null) switch (n.$$typeof) {
          case on:
            C = 10;
            break e;
          case hn:
            C = 9;
            break e;
          case zt:
            C = 11;
            break e;
          case Ut:
            C = 14;
            break e;
          case Ft:
            C = 16, o = null;
            break e;
        }
        throw Error(p(130, n == null ? n : typeof n, ""));
    }
    return r = Za(C, l, r, f), r.elementType = n, r.type = o, r.lanes = v, r;
  }
  function vl(n, r, l, o) {
    return n = Za(7, n, o, r), n.lanes = l, n;
  }
  function Jl(n, r, l, o) {
    return n = Za(22, n, o, r), n.elementType = De, n.lanes = l, n.stateNode = { isHidden: !1 }, n;
  }
  function xp(n, r, l) {
    return n = Za(6, n, null, r), n.lanes = l, n;
  }
  function zf(n, r, l) {
    return r = Za(4, n.children !== null ? n.children : [], n.key, r), r.lanes = l, r.stateNode = { containerInfo: n.containerInfo, pendingChildren: null, implementation: n.implementation }, r;
  }
  function Gh(n, r, l, o, f) {
    this.tag = r, this.containerInfo = n, this.finishedWork = this.pingCache = this.current = this.pendingChildren = null, this.timeoutHandle = -1, this.callbackNode = this.pendingContext = this.context = null, this.callbackPriority = 0, this.eventTimes = go(0), this.expirationTimes = go(-1), this.entangledLanes = this.finishedLanes = this.mutableReadLanes = this.expiredLanes = this.pingedLanes = this.suspendedLanes = this.pendingLanes = 0, this.entanglements = go(0), this.identifierPrefix = o, this.onRecoverableError = f, this.mutableSourceEagerHydrationData = null;
  }
  function Uf(n, r, l, o, f, v, C, w, D) {
    return n = new Gh(n, r, l, w, D), r === 1 ? (r = 1, v === !0 && (r |= 8)) : r = 0, v = Za(3, null, null, r), n.current = v, v.stateNode = n, v.memoizedState = { element: o, isDehydrated: l, cache: null, transitions: null, pendingSuspenseBoundaries: null }, Jd(v), n;
  }
  function hg(n, r, l) {
    var o = 3 < arguments.length && arguments[3] !== void 0 ? arguments[3] : null;
    return { $$typeof: St, key: o == null ? null : "" + o, children: n, containerInfo: r, implementation: l };
  }
  function Tp(n) {
    if (!n) return Mr;
    n = n._reactInternals;
    e: {
      if (it(n) !== n || n.tag !== 1) throw Error(p(170));
      var r = n;
      do {
        switch (r.tag) {
          case 3:
            r = r.stateNode.context;
            break e;
          case 1:
            if (Bn(r.type)) {
              r = r.stateNode.__reactInternalMemoizedMergedChildContext;
              break e;
            }
        }
        r = r.return;
      } while (r !== null);
      throw Error(p(171));
    }
    if (n.tag === 1) {
      var l = n.type;
      if (Bn(l)) return Ns(n, l, r);
    }
    return r;
  }
  function qh(n, r, l, o, f, v, C, w, D) {
    return n = Uf(l, o, !0, n, f, v, C, w, D), n.context = Tp(null), l = n.current, o = Zn(), f = Ii(l), v = sl(o, f), v.callback = r ?? null, Yl(l, v, f), n.current.lanes = f, Ji(n, f, o), pa(n, o), n;
  }
  function jf(n, r, l, o) {
    var f = r.current, v = Zn(), C = Ii(f);
    return l = Tp(l), r.context === null ? r.context = l : r.pendingContext = l, r = sl(v, C), r.payload = { element: n }, o = o === void 0 ? null : o, o !== null && (r.callback = o), n = Yl(f, r, C), n !== null && (Yr(n, f, C, v), nf(n, f, C)), C;
  }
  function Ff(n) {
    if (n = n.current, !n.child) return null;
    switch (n.child.tag) {
      case 5:
        return n.child.stateNode;
      default:
        return n.child.stateNode;
    }
  }
  function Rp(n, r) {
    if (n = n.memoizedState, n !== null && n.dehydrated !== null) {
      var l = n.retryLane;
      n.retryLane = l !== 0 && l < r ? l : r;
    }
  }
  function Hf(n, r) {
    Rp(n, r), (n = n.alternate) && Rp(n, r);
  }
  function Xh() {
    return null;
  }
  var qu = typeof reportError == "function" ? reportError : function(n) {
    console.error(n);
  };
  function wp(n) {
    this._internalRoot = n;
  }
  Vf.prototype.render = wp.prototype.render = function(n) {
    var r = this._internalRoot;
    if (r === null) throw Error(p(409));
    jf(n, r, null, null);
  }, Vf.prototype.unmount = wp.prototype.unmount = function() {
    var n = this._internalRoot;
    if (n !== null) {
      this._internalRoot = null;
      var r = n.containerInfo;
      Zu(function() {
        jf(null, n, null, null);
      }), r[il] = null;
    }
  };
  function Vf(n) {
    this._internalRoot = n;
  }
  Vf.prototype.unstable_scheduleHydration = function(n) {
    if (n) {
      var r = Xe();
      n = { blockedOn: null, target: n, priority: r };
      for (var l = 0; l < Jn.length && r !== 0 && r < Jn[l].priority; l++) ;
      Jn.splice(l, 0, n), l === 0 && ys(n);
    }
  };
  function bp(n) {
    return !(!n || n.nodeType !== 1 && n.nodeType !== 9 && n.nodeType !== 11);
  }
  function Pf(n) {
    return !(!n || n.nodeType !== 1 && n.nodeType !== 9 && n.nodeType !== 11 && (n.nodeType !== 8 || n.nodeValue !== " react-mount-point-unstable "));
  }
  function Kh() {
  }
  function mg(n, r, l, o, f) {
    if (f) {
      if (typeof o == "function") {
        var v = o;
        o = function() {
          var P = Ff(C);
          v.call(P);
        };
      }
      var C = qh(r, o, n, 0, null, !1, !1, "", Kh);
      return n._reactRootContainer = C, n[il] = C.current, Do(n.nodeType === 8 ? n.parentNode : n), Zu(), C;
    }
    for (; f = n.lastChild; ) n.removeChild(f);
    if (typeof o == "function") {
      var w = o;
      o = function() {
        var P = Ff(D);
        w.call(P);
      };
    }
    var D = Uf(n, 0, !1, null, null, !1, !1, "", Kh);
    return n._reactRootContainer = D, n[il] = D.current, Do(n.nodeType === 8 ? n.parentNode : n), Zu(function() {
      jf(r, D, l, o);
    }), D;
  }
  function sc(n, r, l, o, f) {
    var v = l._reactRootContainer;
    if (v) {
      var C = v;
      if (typeof f == "function") {
        var w = f;
        f = function() {
          var D = Ff(C);
          w.call(D);
        };
      }
      jf(r, C, n, f);
    } else C = mg(l, r, n, f, o);
    return Ff(C);
  }
  Lt = function(n) {
    switch (n.tag) {
      case 3:
        var r = n.stateNode;
        if (r.current.memoizedState.isDehydrated) {
          var l = si(r.pendingLanes);
          l !== 0 && (el(r, l | 1), pa(r, lt()), !(Dt & 6) && (Io = lt() + 500, zi()));
        }
        break;
      case 13:
        Zu(function() {
          var o = Ra(n, 1);
          if (o !== null) {
            var f = Zn();
            Yr(o, n, 1, f);
          }
        }), Hf(n, 1);
    }
  }, hs = function(n) {
    if (n.tag === 13) {
      var r = Ra(n, 134217728);
      if (r !== null) {
        var l = Zn();
        Yr(r, n, 134217728, l);
      }
      Hf(n, 134217728);
    }
  }, bi = function(n) {
    if (n.tag === 13) {
      var r = Ii(n), l = Ra(n, r);
      if (l !== null) {
        var o = Zn();
        Yr(l, n, r, o);
      }
      Hf(n, r);
    }
  }, Xe = function() {
    return Ht;
  }, Eo = function(n, r) {
    var l = Ht;
    try {
      return Ht = n, r();
    } finally {
      Ht = l;
    }
  }, Jt = function(n, r, l) {
    switch (r) {
      case "input":
        if (ta(n, l), r = l.name, l.type === "radio" && r != null) {
          for (l = n; l.parentNode; ) l = l.parentNode;
          for (l = l.querySelectorAll("input[name=" + JSON.stringify("" + r) + '][type="radio"]'), r = 0; r < l.length; r++) {
            var o = l[r];
            if (o !== n && o.form === n.form) {
              var f = Rn(o);
              if (!f) throw Error(p(90));
              Ur(o), ta(o, f);
            }
          }
        }
        break;
      case "textarea":
        ri(n, l);
        break;
      case "select":
        r = l.value, r != null && Nn(n, !!l.multiple, r, !1);
    }
  }, mu = Sp, wl = Zu;
  var yg = { usingClientEntryPoint: !1, Events: [Ue, vi, Rn, Ki, hu, Sp] }, cc = { findFiberByHostInstance: Ou, bundleType: 0, version: "18.3.1", rendererPackageName: "react-dom" }, Jh = { bundleType: cc.bundleType, version: cc.version, rendererPackageName: cc.rendererPackageName, rendererConfig: cc.rendererConfig, overrideHookState: null, overrideHookStateDeletePath: null, overrideHookStateRenamePath: null, overrideProps: null, overridePropsDeletePath: null, overridePropsRenamePath: null, setErrorHandler: null, setSuspenseHandler: null, scheduleUpdate: null, currentDispatcherRef: Tt.ReactCurrentDispatcher, findHostInstanceByFiber: function(n) {
    return n = Mn(n), n === null ? null : n.stateNode;
  }, findFiberByHostInstance: cc.findFiberByHostInstance || Xh, findHostInstancesForRefresh: null, scheduleRefresh: null, scheduleRoot: null, setRefreshHandler: null, getCurrentFiber: null, reconcilerVersion: "18.3.1-next-f1338f8080-20240426" };
  if (typeof __REACT_DEVTOOLS_GLOBAL_HOOK__ < "u") {
    var eu = __REACT_DEVTOOLS_GLOBAL_HOOK__;
    if (!eu.isDisabled && eu.supportsFiber) try {
      Dl = eu.inject(Jh), na = eu;
    } catch {
    }
  }
  return ei.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED = yg, ei.createPortal = function(n, r) {
    var l = 2 < arguments.length && arguments[2] !== void 0 ? arguments[2] : null;
    if (!bp(r)) throw Error(p(200));
    return hg(n, r, null, l);
  }, ei.createRoot = function(n, r) {
    if (!bp(n)) throw Error(p(299));
    var l = !1, o = "", f = qu;
    return r != null && (r.unstable_strictMode === !0 && (l = !0), r.identifierPrefix !== void 0 && (o = r.identifierPrefix), r.onRecoverableError !== void 0 && (f = r.onRecoverableError)), r = Uf(n, 1, !1, null, null, l, !1, o, f), n[il] = r.current, Do(n.nodeType === 8 ? n.parentNode : n), new wp(r);
  }, ei.findDOMNode = function(n) {
    if (n == null) return null;
    if (n.nodeType === 1) return n;
    var r = n._reactInternals;
    if (r === void 0)
      throw typeof n.render == "function" ? Error(p(188)) : (n = Object.keys(n).join(","), Error(p(268, n)));
    return n = Mn(r), n = n === null ? null : n.stateNode, n;
  }, ei.flushSync = function(n) {
    return Zu(n);
  }, ei.hydrate = function(n, r, l) {
    if (!Pf(r)) throw Error(p(200));
    return sc(null, n, r, !0, l);
  }, ei.hydrateRoot = function(n, r, l) {
    if (!bp(n)) throw Error(p(405));
    var o = l != null && l.hydratedSources || null, f = !1, v = "", C = qu;
    if (l != null && (l.unstable_strictMode === !0 && (f = !0), l.identifierPrefix !== void 0 && (v = l.identifierPrefix), l.onRecoverableError !== void 0 && (C = l.onRecoverableError)), r = qh(r, null, n, 1, l ?? null, f, !1, v, C), n[il] = r.current, Do(n), o) for (n = 0; n < o.length; n++) l = o[n], f = l._getVersion, f = f(l._source), r.mutableSourceEagerHydrationData == null ? r.mutableSourceEagerHydrationData = [l, f] : r.mutableSourceEagerHydrationData.push(
      l,
      f
    );
    return new Vf(r);
  }, ei.render = function(n, r, l) {
    if (!Pf(r)) throw Error(p(200));
    return sc(null, n, r, !1, l);
  }, ei.unmountComponentAtNode = function(n) {
    if (!Pf(n)) throw Error(p(40));
    return n._reactRootContainer ? (Zu(function() {
      sc(null, null, n, !1, function() {
        n._reactRootContainer = null, n[il] = null;
      });
    }), !0) : !1;
  }, ei.unstable_batchedUpdates = Sp, ei.unstable_renderSubtreeIntoContainer = function(n, r, l, o) {
    if (!Pf(l)) throw Error(p(200));
    if (n == null || n._reactInternals === void 0) throw Error(p(38));
    return sc(n, r, l, !1, o);
  }, ei.version = "18.3.1-next-f1338f8080-20240426", ei;
}
var ti = {};
/**
 * @license React
 * react-dom.development.js
 *
 * Copyright (c) Facebook, Inc. and its affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */
var mT;
function MD() {
  return mT || (mT = 1, vu.env.NODE_ENV !== "production" && function() {
    typeof __REACT_DEVTOOLS_GLOBAL_HOOK__ < "u" && typeof __REACT_DEVTOOLS_GLOBAL_HOOK__.registerInternalModuleStart == "function" && __REACT_DEVTOOLS_GLOBAL_HOOK__.registerInternalModuleStart(new Error());
    var h = Fv, c = DT(), p = h.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED, S = !1;
    function _(e) {
      S = e;
    }
    function T(e) {
      if (!S) {
        for (var t = arguments.length, a = new Array(t > 1 ? t - 1 : 0), i = 1; i < t; i++)
          a[i - 1] = arguments[i];
        A("warn", e, a);
      }
    }
    function E(e) {
      if (!S) {
        for (var t = arguments.length, a = new Array(t > 1 ? t - 1 : 0), i = 1; i < t; i++)
          a[i - 1] = arguments[i];
        A("error", e, a);
      }
    }
    function A(e, t, a) {
      {
        var i = p.ReactDebugCurrentFrame, u = i.getStackAddendum();
        u !== "" && (t += "%s", a = a.concat([u]));
        var s = a.map(function(d) {
          return String(d);
        });
        s.unshift("Warning: " + t), Function.prototype.apply.call(console[e], console, s);
      }
    }
    var I = 0, $ = 1, fe = 2, re = 3, be = 4, de = 5, nt = 6, bt = 7, xt = 8, En = 9, _t = 10, rt = 11, Tt = 12, ze = 13, St = 14, Qe = 15, vn = 16, Zt = 17, on = 18, hn = 19, zt = 21, He = 22, Yt = 23, Ut = 24, Ft = 25, De = !0, le = !1, Oe = !1, se = !1, L = !1, Z = !0, Ze = !0, Ye = !0, vt = !0, ct = /* @__PURE__ */ new Set(), ot = {}, ft = {};
    function ht(e, t) {
      Xt(e, t), Xt(e + "Capture", t);
    }
    function Xt(e, t) {
      ot[e] && E("EventRegistry: More than one plugin attempted to publish the same registration name, `%s`.", e), ot[e] = t;
      {
        var a = e.toLowerCase();
        ft[a] = e, e === "onDoubleClick" && (ft.ondblclick = e);
      }
      for (var i = 0; i < t.length; i++)
        ct.add(t[i]);
    }
    var Hn = typeof window < "u" && typeof window.document < "u" && typeof window.document.createElement < "u", Ur = Object.prototype.hasOwnProperty;
    function On(e) {
      {
        var t = typeof Symbol == "function" && Symbol.toStringTag, a = t && e[Symbol.toStringTag] || e.constructor.name || "Object";
        return a;
      }
    }
    function pr(e) {
      try {
        return qn(e), !1;
      } catch {
        return !0;
      }
    }
    function qn(e) {
      return "" + e;
    }
    function Xn(e, t) {
      if (pr(e))
        return E("The provided `%s` attribute is an unsupported type %s. This value must be coerced to a string before before using it here.", t, On(e)), qn(e);
    }
    function ta(e) {
      if (pr(e))
        return E("The provided key is an unsupported type %s. This value must be coerced to a string before before using it here.", On(e)), qn(e);
    }
    function _i(e, t) {
      if (pr(e))
        return E("The provided `%s` prop is an unsupported type %s. This value must be coerced to a string before before using it here.", t, On(e)), qn(e);
    }
    function Sa(e, t) {
      if (pr(e))
        return E("The provided `%s` CSS property is an unsupported type %s. This value must be coerced to a string before before using it here.", t, On(e)), qn(e);
    }
    function ir(e) {
      if (pr(e))
        return E("The provided HTML markup uses a value of unsupported type %s. This value must be coerced to a string before before using it here.", On(e)), qn(e);
    }
    function Nn(e) {
      if (pr(e))
        return E("Form field values (value, checked, defaultValue, or defaultChecked props) must be strings, not %s. This value must be coerced to a string before before using it here.", On(e)), qn(e);
    }
    var Kn = 0, Dr = 1, ri = 2, Vn = 3, Or = 4, Ea = 5, ai = 6, xi = ":A-Z_a-z\\u00C0-\\u00D6\\u00D8-\\u00F6\\u00F8-\\u02FF\\u0370-\\u037D\\u037F-\\u1FFF\\u200C-\\u200D\\u2070-\\u218F\\u2C00-\\u2FEF\\u3001-\\uD7FF\\uF900-\\uFDCF\\uFDF0-\\uFFFD", ue = xi + "\\-.0-9\\u00B7\\u0300-\\u036F\\u203F-\\u2040", Ne = new RegExp("^[" + xi + "][" + ue + "]*$"), dt = {}, Wt = {};
    function sn(e) {
      return Ur.call(Wt, e) ? !0 : Ur.call(dt, e) ? !1 : Ne.test(e) ? (Wt[e] = !0, !0) : (dt[e] = !0, E("Invalid attribute name: `%s`", e), !1);
    }
    function xn(e, t, a) {
      return t !== null ? t.type === Kn : a ? !1 : e.length > 2 && (e[0] === "o" || e[0] === "O") && (e[1] === "n" || e[1] === "N");
    }
    function mn(e, t, a, i) {
      if (a !== null && a.type === Kn)
        return !1;
      switch (typeof t) {
        case "function":
        case "symbol":
          return !0;
        case "boolean": {
          if (i)
            return !1;
          if (a !== null)
            return !a.acceptsBooleans;
          var u = e.toLowerCase().slice(0, 5);
          return u !== "data-" && u !== "aria-";
        }
        default:
          return !1;
      }
    }
    function lr(e, t, a, i) {
      if (t === null || typeof t > "u" || mn(e, t, a, i))
        return !0;
      if (i)
        return !1;
      if (a !== null)
        switch (a.type) {
          case Vn:
            return !t;
          case Or:
            return t === !1;
          case Ea:
            return isNaN(t);
          case ai:
            return isNaN(t) || t < 1;
        }
      return !1;
    }
    function cn(e) {
      return Jt.hasOwnProperty(e) ? Jt[e] : null;
    }
    function Kt(e, t, a, i, u, s, d) {
      this.acceptsBooleans = t === ri || t === Vn || t === Or, this.attributeName = i, this.attributeNamespace = u, this.mustUseProperty = a, this.propertyName = e, this.type = t, this.sanitizeURL = s, this.removeEmptyString = d;
    }
    var Jt = {}, Ca = [
      "children",
      "dangerouslySetInnerHTML",
      // TODO: This prevents the assignment of defaultValue to regular
      // elements (not just inputs). Now that ReactDOMInput assigns to the
      // defaultValue property -- do we need this?
      "defaultValue",
      "defaultChecked",
      "innerHTML",
      "suppressContentEditableWarning",
      "suppressHydrationWarning",
      "style"
    ];
    Ca.forEach(function(e) {
      Jt[e] = new Kt(
        e,
        Kn,
        !1,
        // mustUseProperty
        e,
        // attributeName
        null,
        // attributeNamespace
        !1,
        // sanitizeURL
        !1
      );
    }), [["acceptCharset", "accept-charset"], ["className", "class"], ["htmlFor", "for"], ["httpEquiv", "http-equiv"]].forEach(function(e) {
      var t = e[0], a = e[1];
      Jt[t] = new Kt(
        t,
        Dr,
        !1,
        // mustUseProperty
        a,
        // attributeName
        null,
        // attributeNamespace
        !1,
        // sanitizeURL
        !1
      );
    }), ["contentEditable", "draggable", "spellCheck", "value"].forEach(function(e) {
      Jt[e] = new Kt(
        e,
        ri,
        !1,
        // mustUseProperty
        e.toLowerCase(),
        // attributeName
        null,
        // attributeNamespace
        !1,
        // sanitizeURL
        !1
      );
    }), ["autoReverse", "externalResourcesRequired", "focusable", "preserveAlpha"].forEach(function(e) {
      Jt[e] = new Kt(
        e,
        ri,
        !1,
        // mustUseProperty
        e,
        // attributeName
        null,
        // attributeNamespace
        !1,
        // sanitizeURL
        !1
      );
    }), [
      "allowFullScreen",
      "async",
      // Note: there is a special case that prevents it from being written to the DOM
      // on the client side because the browsers are inconsistent. Instead we call focus().
      "autoFocus",
      "autoPlay",
      "controls",
      "default",
      "defer",
      "disabled",
      "disablePictureInPicture",
      "disableRemotePlayback",
      "formNoValidate",
      "hidden",
      "loop",
      "noModule",
      "noValidate",
      "open",
      "playsInline",
      "readOnly",
      "required",
      "reversed",
      "scoped",
      "seamless",
      // Microdata
      "itemScope"
    ].forEach(function(e) {
      Jt[e] = new Kt(
        e,
        Vn,
        !1,
        // mustUseProperty
        e.toLowerCase(),
        // attributeName
        null,
        // attributeNamespace
        !1,
        // sanitizeURL
        !1
      );
    }), [
      "checked",
      // Note: `option.selected` is not updated if `select.multiple` is
      // disabled with `removeAttribute`. We have special logic for handling this.
      "multiple",
      "muted",
      "selected"
      // NOTE: if you add a camelCased prop to this list,
      // you'll need to set attributeName to name.toLowerCase()
      // instead in the assignment below.
    ].forEach(function(e) {
      Jt[e] = new Kt(
        e,
        Vn,
        !0,
        // mustUseProperty
        e,
        // attributeName
        null,
        // attributeNamespace
        !1,
        // sanitizeURL
        !1
      );
    }), [
      "capture",
      "download"
      // NOTE: if you add a camelCased prop to this list,
      // you'll need to set attributeName to name.toLowerCase()
      // instead in the assignment below.
    ].forEach(function(e) {
      Jt[e] = new Kt(
        e,
        Or,
        !1,
        // mustUseProperty
        e,
        // attributeName
        null,
        // attributeNamespace
        !1,
        // sanitizeURL
        !1
      );
    }), [
      "cols",
      "rows",
      "size",
      "span"
      // NOTE: if you add a camelCased prop to this list,
      // you'll need to set attributeName to name.toLowerCase()
      // instead in the assignment below.
    ].forEach(function(e) {
      Jt[e] = new Kt(
        e,
        ai,
        !1,
        // mustUseProperty
        e,
        // attributeName
        null,
        // attributeNamespace
        !1,
        // sanitizeURL
        !1
      );
    }), ["rowSpan", "start"].forEach(function(e) {
      Jt[e] = new Kt(
        e,
        Ea,
        !1,
        // mustUseProperty
        e.toLowerCase(),
        // attributeName
        null,
        // attributeNamespace
        !1,
        // sanitizeURL
        !1
      );
    });
    var Nr = /[\-\:]([a-z])/g, za = function(e) {
      return e[1].toUpperCase();
    };
    [
      "accent-height",
      "alignment-baseline",
      "arabic-form",
      "baseline-shift",
      "cap-height",
      "clip-path",
      "clip-rule",
      "color-interpolation",
      "color-interpolation-filters",
      "color-profile",
      "color-rendering",
      "dominant-baseline",
      "enable-background",
      "fill-opacity",
      "fill-rule",
      "flood-color",
      "flood-opacity",
      "font-family",
      "font-size",
      "font-size-adjust",
      "font-stretch",
      "font-style",
      "font-variant",
      "font-weight",
      "glyph-name",
      "glyph-orientation-horizontal",
      "glyph-orientation-vertical",
      "horiz-adv-x",
      "horiz-origin-x",
      "image-rendering",
      "letter-spacing",
      "lighting-color",
      "marker-end",
      "marker-mid",
      "marker-start",
      "overline-position",
      "overline-thickness",
      "paint-order",
      "panose-1",
      "pointer-events",
      "rendering-intent",
      "shape-rendering",
      "stop-color",
      "stop-opacity",
      "strikethrough-position",
      "strikethrough-thickness",
      "stroke-dasharray",
      "stroke-dashoffset",
      "stroke-linecap",
      "stroke-linejoin",
      "stroke-miterlimit",
      "stroke-opacity",
      "stroke-width",
      "text-anchor",
      "text-decoration",
      "text-rendering",
      "underline-position",
      "underline-thickness",
      "unicode-bidi",
      "unicode-range",
      "units-per-em",
      "v-alphabetic",
      "v-hanging",
      "v-ideographic",
      "v-mathematical",
      "vector-effect",
      "vert-adv-y",
      "vert-origin-x",
      "vert-origin-y",
      "word-spacing",
      "writing-mode",
      "xmlns:xlink",
      "x-height"
      // NOTE: if you add a camelCased prop to this list,
      // you'll need to set attributeName to name.toLowerCase()
      // instead in the assignment below.
    ].forEach(function(e) {
      var t = e.replace(Nr, za);
      Jt[t] = new Kt(
        t,
        Dr,
        !1,
        // mustUseProperty
        e,
        null,
        // attributeNamespace
        !1,
        // sanitizeURL
        !1
      );
    }), [
      "xlink:actuate",
      "xlink:arcrole",
      "xlink:role",
      "xlink:show",
      "xlink:title",
      "xlink:type"
      // NOTE: if you add a camelCased prop to this list,
      // you'll need to set attributeName to name.toLowerCase()
      // instead in the assignment below.
    ].forEach(function(e) {
      var t = e.replace(Nr, za);
      Jt[t] = new Kt(
        t,
        Dr,
        !1,
        // mustUseProperty
        e,
        "http://www.w3.org/1999/xlink",
        !1,
        // sanitizeURL
        !1
      );
    }), [
      "xml:base",
      "xml:lang",
      "xml:space"
      // NOTE: if you add a camelCased prop to this list,
      // you'll need to set attributeName to name.toLowerCase()
      // instead in the assignment below.
    ].forEach(function(e) {
      var t = e.replace(Nr, za);
      Jt[t] = new Kt(
        t,
        Dr,
        !1,
        // mustUseProperty
        e,
        "http://www.w3.org/XML/1998/namespace",
        !1,
        // sanitizeURL
        !1
      );
    }), ["tabIndex", "crossOrigin"].forEach(function(e) {
      Jt[e] = new Kt(
        e,
        Dr,
        !1,
        // mustUseProperty
        e.toLowerCase(),
        // attributeName
        null,
        // attributeNamespace
        !1,
        // sanitizeURL
        !1
      );
    });
    var Ki = "xlinkHref";
    Jt[Ki] = new Kt(
      "xlinkHref",
      Dr,
      !1,
      // mustUseProperty
      "xlink:href",
      "http://www.w3.org/1999/xlink",
      !0,
      // sanitizeURL
      !1
    ), ["src", "href", "action", "formAction"].forEach(function(e) {
      Jt[e] = new Kt(
        e,
        Dr,
        !1,
        // mustUseProperty
        e.toLowerCase(),
        // attributeName
        null,
        // attributeNamespace
        !0,
        // sanitizeURL
        !0
      );
    });
    var hu = /^[\u0000-\u001F ]*j[\r\n\t]*a[\r\n\t]*v[\r\n\t]*a[\r\n\t]*s[\r\n\t]*c[\r\n\t]*r[\r\n\t]*i[\r\n\t]*p[\r\n\t]*t[\r\n\t]*\:/i, mu = !1;
    function wl(e) {
      !mu && hu.test(e) && (mu = !0, E("A future version of React will block javascript: URLs as a security precaution. Use event handlers instead if you can. If you need to generate unsafe HTML try using dangerouslySetInnerHTML instead. React was passed %s.", JSON.stringify(e)));
    }
    function bl(e, t, a, i) {
      if (i.mustUseProperty) {
        var u = i.propertyName;
        return e[u];
      } else {
        Xn(a, t), i.sanitizeURL && wl("" + a);
        var s = i.attributeName, d = null;
        if (i.type === Or) {
          if (e.hasAttribute(s)) {
            var m = e.getAttribute(s);
            return m === "" ? !0 : lr(t, a, i, !1) ? m : m === "" + a ? a : m;
          }
        } else if (e.hasAttribute(s)) {
          if (lr(t, a, i, !1))
            return e.getAttribute(s);
          if (i.type === Vn)
            return a;
          d = e.getAttribute(s);
        }
        return lr(t, a, i, !1) ? d === null ? a : d : d === "" + a ? a : d;
      }
    }
    function yu(e, t, a, i) {
      {
        if (!sn(t))
          return;
        if (!e.hasAttribute(t))
          return a === void 0 ? void 0 : null;
        var u = e.getAttribute(t);
        return Xn(a, t), u === "" + a ? a : u;
      }
    }
    function jr(e, t, a, i) {
      var u = cn(t);
      if (!xn(t, u, i)) {
        if (lr(t, a, u, i) && (a = null), i || u === null) {
          if (sn(t)) {
            var s = t;
            a === null ? e.removeAttribute(s) : (Xn(a, t), e.setAttribute(s, "" + a));
          }
          return;
        }
        var d = u.mustUseProperty;
        if (d) {
          var m = u.propertyName;
          if (a === null) {
            var y = u.type;
            e[m] = y === Vn ? !1 : "";
          } else
            e[m] = a;
          return;
        }
        var x = u.attributeName, R = u.attributeNamespace;
        if (a === null)
          e.removeAttribute(x);
        else {
          var M = u.type, O;
          M === Vn || M === Or && a === !0 ? O = "" : (Xn(a, x), O = "" + a, u.sanitizeURL && wl(O.toString())), R ? e.setAttributeNS(R, x, O) : e.setAttribute(x, O);
        }
      }
    }
    var Fr = Symbol.for("react.element"), vr = Symbol.for("react.portal"), Ti = Symbol.for("react.fragment"), ii = Symbol.for("react.strict_mode"), Ri = Symbol.for("react.profiler"), wi = Symbol.for("react.provider"), k = Symbol.for("react.context"), q = Symbol.for("react.forward_ref"), pe = Symbol.for("react.suspense"), _e = Symbol.for("react.suspense_list"), it = Symbol.for("react.memo"), Ke = Symbol.for("react.lazy"), Et = Symbol.for("react.scope"), mt = Symbol.for("react.debug_trace_mode"), Mn = Symbol.for("react.offscreen"), fn = Symbol.for("react.legacy_hidden"), yn = Symbol.for("react.cache"), hr = Symbol.for("react.tracing_marker"), li = Symbol.iterator, ui = "@@iterator";
    function lt(e) {
      if (e === null || typeof e != "object")
        return null;
      var t = li && e[li] || e[ui];
      return typeof t == "function" ? t : null;
    }
    var st = Object.assign, oi = 0, gu, Su, kl, vo, Dl, na, vs;
    function Hr() {
    }
    Hr.__reactDisabledLog = !0;
    function Mc() {
      {
        if (oi === 0) {
          gu = console.log, Su = console.info, kl = console.warn, vo = console.error, Dl = console.group, na = console.groupCollapsed, vs = console.groupEnd;
          var e = {
            configurable: !0,
            enumerable: !0,
            value: Hr,
            writable: !0
          };
          Object.defineProperties(console, {
            info: e,
            log: e,
            warn: e,
            error: e,
            group: e,
            groupCollapsed: e,
            groupEnd: e
          });
        }
        oi++;
      }
    }
    function Lc() {
      {
        if (oi--, oi === 0) {
          var e = {
            configurable: !0,
            enumerable: !0,
            writable: !0
          };
          Object.defineProperties(console, {
            log: st({}, e, {
              value: gu
            }),
            info: st({}, e, {
              value: Su
            }),
            warn: st({}, e, {
              value: kl
            }),
            error: st({}, e, {
              value: vo
            }),
            group: st({}, e, {
              value: Dl
            }),
            groupCollapsed: st({}, e, {
              value: na
            }),
            groupEnd: st({}, e, {
              value: vs
            })
          });
        }
        oi < 0 && E("disabledDepth fell below zero. This is a bug in React. Please file an issue.");
      }
    }
    var ho = p.ReactCurrentDispatcher, Ol;
    function _a(e, t, a) {
      {
        if (Ol === void 0)
          try {
            throw Error();
          } catch (u) {
            var i = u.stack.trim().match(/\n( *(at )?)/);
            Ol = i && i[1] || "";
          }
        return `
` + Ol + e;
      }
    }
    var si = !1, ci;
    {
      var mo = typeof WeakMap == "function" ? WeakMap : Map;
      ci = new mo();
    }
    function Eu(e, t) {
      if (!e || si)
        return "";
      {
        var a = ci.get(e);
        if (a !== void 0)
          return a;
      }
      var i;
      si = !0;
      var u = Error.prepareStackTrace;
      Error.prepareStackTrace = void 0;
      var s;
      s = ho.current, ho.current = null, Mc();
      try {
        if (t) {
          var d = function() {
            throw Error();
          };
          if (Object.defineProperty(d.prototype, "props", {
            set: function() {
              throw Error();
            }
          }), typeof Reflect == "object" && Reflect.construct) {
            try {
              Reflect.construct(d, []);
            } catch (B) {
              i = B;
            }
            Reflect.construct(e, [], d);
          } else {
            try {
              d.call();
            } catch (B) {
              i = B;
            }
            e.call(d.prototype);
          }
        } else {
          try {
            throw Error();
          } catch (B) {
            i = B;
          }
          e();
        }
      } catch (B) {
        if (B && i && typeof B.stack == "string") {
          for (var m = B.stack.split(`
`), y = i.stack.split(`
`), x = m.length - 1, R = y.length - 1; x >= 1 && R >= 0 && m[x] !== y[R]; )
            R--;
          for (; x >= 1 && R >= 0; x--, R--)
            if (m[x] !== y[R]) {
              if (x !== 1 || R !== 1)
                do
                  if (x--, R--, R < 0 || m[x] !== y[R]) {
                    var M = `
` + m[x].replace(" at new ", " at ");
                    return e.displayName && M.includes("<anonymous>") && (M = M.replace("<anonymous>", e.displayName)), typeof e == "function" && ci.set(e, M), M;
                  }
                while (x >= 1 && R >= 0);
              break;
            }
        }
      } finally {
        si = !1, ho.current = s, Lc(), Error.prepareStackTrace = u;
      }
      var O = e ? e.displayName || e.name : "", H = O ? _a(O) : "";
      return typeof e == "function" && ci.set(e, H), H;
    }
    function Nl(e, t, a) {
      return Eu(e, !0);
    }
    function yo(e, t, a) {
      return Eu(e, !1);
    }
    function go(e) {
      var t = e.prototype;
      return !!(t && t.isReactComponent);
    }
    function Ji(e, t, a) {
      if (e == null)
        return "";
      if (typeof e == "function")
        return Eu(e, go(e));
      if (typeof e == "string")
        return _a(e);
      switch (e) {
        case pe:
          return _a("Suspense");
        case _e:
          return _a("SuspenseList");
      }
      if (typeof e == "object")
        switch (e.$$typeof) {
          case q:
            return yo(e.render);
          case it:
            return Ji(e.type, t, a);
          case Ke: {
            var i = e, u = i._payload, s = i._init;
            try {
              return Ji(s(u), t, a);
            } catch {
            }
          }
        }
      return "";
    }
    function _d(e) {
      switch (e._debugOwner && e._debugOwner.type, e._debugSource, e.tag) {
        case de:
          return _a(e.type);
        case vn:
          return _a("Lazy");
        case ze:
          return _a("Suspense");
        case hn:
          return _a("SuspenseList");
        case I:
        case fe:
        case Qe:
          return yo(e.type);
        case rt:
          return yo(e.type.render);
        case $:
          return Nl(e.type);
        default:
          return "";
      }
    }
    function el(e) {
      try {
        var t = "", a = e;
        do
          t += _d(a), a = a.return;
        while (a);
        return t;
      } catch (i) {
        return `
Error generating stack: ` + i.message + `
` + i.stack;
      }
    }
    function Ht(e, t, a) {
      var i = e.displayName;
      if (i)
        return i;
      var u = t.displayName || t.name || "";
      return u !== "" ? a + "(" + u + ")" : a;
    }
    function So(e) {
      return e.displayName || "Context";
    }
    function Lt(e) {
      if (e == null)
        return null;
      if (typeof e.tag == "number" && E("Received an unexpected object in getComponentNameFromType(). This is likely a bug in React. Please file an issue."), typeof e == "function")
        return e.displayName || e.name || null;
      if (typeof e == "string")
        return e;
      switch (e) {
        case Ti:
          return "Fragment";
        case vr:
          return "Portal";
        case Ri:
          return "Profiler";
        case ii:
          return "StrictMode";
        case pe:
          return "Suspense";
        case _e:
          return "SuspenseList";
      }
      if (typeof e == "object")
        switch (e.$$typeof) {
          case k:
            var t = e;
            return So(t) + ".Consumer";
          case wi:
            var a = e;
            return So(a._context) + ".Provider";
          case q:
            return Ht(e, e.render, "ForwardRef");
          case it:
            var i = e.displayName || null;
            return i !== null ? i : Lt(e.type) || "Memo";
          case Ke: {
            var u = e, s = u._payload, d = u._init;
            try {
              return Lt(d(s));
            } catch {
              return null;
            }
          }
        }
      return null;
    }
    function hs(e, t, a) {
      var i = t.displayName || t.name || "";
      return e.displayName || (i !== "" ? a + "(" + i + ")" : a);
    }
    function bi(e) {
      return e.displayName || "Context";
    }
    function Xe(e) {
      var t = e.tag, a = e.type;
      switch (t) {
        case Ut:
          return "Cache";
        case En:
          var i = a;
          return bi(i) + ".Consumer";
        case _t:
          var u = a;
          return bi(u._context) + ".Provider";
        case on:
          return "DehydratedFragment";
        case rt:
          return hs(a, a.render, "ForwardRef");
        case bt:
          return "Fragment";
        case de:
          return a;
        case be:
          return "Portal";
        case re:
          return "Root";
        case nt:
          return "Text";
        case vn:
          return Lt(a);
        case xt:
          return a === ii ? "StrictMode" : "Mode";
        case He:
          return "Offscreen";
        case Tt:
          return "Profiler";
        case zt:
          return "Scope";
        case ze:
          return "Suspense";
        case hn:
          return "SuspenseList";
        case Ft:
          return "TracingMarker";
        case $:
        case I:
        case Zt:
        case fe:
        case St:
        case Qe:
          if (typeof a == "function")
            return a.displayName || a.name || null;
          if (typeof a == "string")
            return a;
          break;
      }
      return null;
    }
    var Eo = p.ReactDebugCurrentFrame, mr = null, ki = !1;
    function Vr() {
      {
        if (mr === null)
          return null;
        var e = mr._debugOwner;
        if (e !== null && typeof e < "u")
          return Xe(e);
      }
      return null;
    }
    function Di() {
      return mr === null ? "" : el(mr);
    }
    function gn() {
      Eo.getCurrentStack = null, mr = null, ki = !1;
    }
    function en(e) {
      Eo.getCurrentStack = e === null ? null : Di, mr = e, ki = !1;
    }
    function Ml() {
      return mr;
    }
    function Jn(e) {
      ki = e;
    }
    function Pr(e) {
      return "" + e;
    }
    function Ua(e) {
      switch (typeof e) {
        case "boolean":
        case "number":
        case "string":
        case "undefined":
          return e;
        case "object":
          return Nn(e), e;
        default:
          return "";
      }
    }
    var Cu = {
      button: !0,
      checkbox: !0,
      image: !0,
      hidden: !0,
      radio: !0,
      reset: !0,
      submit: !0
    };
    function ms(e, t) {
      Cu[t.type] || t.onChange || t.onInput || t.readOnly || t.disabled || t.value == null || E("You provided a `value` prop to a form field without an `onChange` handler. This will render a read-only field. If the field should be mutable use `defaultValue`. Otherwise, set either `onChange` or `readOnly`."), t.onChange || t.readOnly || t.disabled || t.checked == null || E("You provided a `checked` prop to a form field without an `onChange` handler. This will render a read-only field. If the field should be mutable use `defaultChecked`. Otherwise, set either `onChange` or `readOnly`.");
    }
    function ys(e) {
      var t = e.type, a = e.nodeName;
      return a && a.toLowerCase() === "input" && (t === "checkbox" || t === "radio");
    }
    function Ll(e) {
      return e._valueTracker;
    }
    function _u(e) {
      e._valueTracker = null;
    }
    function xd(e) {
      var t = "";
      return e && (ys(e) ? t = e.checked ? "true" : "false" : t = e.value), t;
    }
    function ja(e) {
      var t = ys(e) ? "checked" : "value", a = Object.getOwnPropertyDescriptor(e.constructor.prototype, t);
      Nn(e[t]);
      var i = "" + e[t];
      if (!(e.hasOwnProperty(t) || typeof a > "u" || typeof a.get != "function" || typeof a.set != "function")) {
        var u = a.get, s = a.set;
        Object.defineProperty(e, t, {
          configurable: !0,
          get: function() {
            return u.call(this);
          },
          set: function(m) {
            Nn(m), i = "" + m, s.call(this, m);
          }
        }), Object.defineProperty(e, t, {
          enumerable: a.enumerable
        });
        var d = {
          getValue: function() {
            return i;
          },
          setValue: function(m) {
            Nn(m), i = "" + m;
          },
          stopTracking: function() {
            _u(e), delete e[t];
          }
        };
        return d;
      }
    }
    function fi(e) {
      Ll(e) || (e._valueTracker = ja(e));
    }
    function Oi(e) {
      if (!e)
        return !1;
      var t = Ll(e);
      if (!t)
        return !0;
      var a = t.getValue(), i = xd(e);
      return i !== a ? (t.setValue(i), !0) : !1;
    }
    function Fa(e) {
      if (e = e || (typeof document < "u" ? document : void 0), typeof e > "u")
        return null;
      try {
        return e.activeElement || e.body;
      } catch {
        return e.body;
      }
    }
    var Co = !1, _o = !1, Al = !1, xu = !1;
    function xo(e) {
      var t = e.type === "checkbox" || e.type === "radio";
      return t ? e.checked != null : e.value != null;
    }
    function To(e, t) {
      var a = e, i = t.checked, u = st({}, t, {
        defaultChecked: void 0,
        defaultValue: void 0,
        value: void 0,
        checked: i ?? a._wrapperState.initialChecked
      });
      return u;
    }
    function di(e, t) {
      ms("input", t), t.checked !== void 0 && t.defaultChecked !== void 0 && !_o && (E("%s contains an input of type %s with both checked and defaultChecked props. Input elements must be either controlled or uncontrolled (specify either the checked prop, or the defaultChecked prop, but not both). Decide between using a controlled or uncontrolled input element and remove one of these props. More info: https://reactjs.org/link/controlled-components", Vr() || "A component", t.type), _o = !0), t.value !== void 0 && t.defaultValue !== void 0 && !Co && (E("%s contains an input of type %s with both value and defaultValue props. Input elements must be either controlled or uncontrolled (specify either the value prop, or the defaultValue prop, but not both). Decide between using a controlled or uncontrolled input element and remove one of these props. More info: https://reactjs.org/link/controlled-components", Vr() || "A component", t.type), Co = !0);
      var a = e, i = t.defaultValue == null ? "" : t.defaultValue;
      a._wrapperState = {
        initialChecked: t.checked != null ? t.checked : t.defaultChecked,
        initialValue: Ua(t.value != null ? t.value : i),
        controlled: xo(t)
      };
    }
    function g(e, t) {
      var a = e, i = t.checked;
      i != null && jr(a, "checked", i, !1);
    }
    function b(e, t) {
      var a = e;
      {
        var i = xo(t);
        !a._wrapperState.controlled && i && !xu && (E("A component is changing an uncontrolled input to be controlled. This is likely caused by the value changing from undefined to a defined value, which should not happen. Decide between using a controlled or uncontrolled input element for the lifetime of the component. More info: https://reactjs.org/link/controlled-components"), xu = !0), a._wrapperState.controlled && !i && !Al && (E("A component is changing a controlled input to be uncontrolled. This is likely caused by the value changing from a defined to undefined, which should not happen. Decide between using a controlled or uncontrolled input element for the lifetime of the component. More info: https://reactjs.org/link/controlled-components"), Al = !0);
      }
      g(e, t);
      var u = Ua(t.value), s = t.type;
      if (u != null)
        s === "number" ? (u === 0 && a.value === "" || // We explicitly want to coerce to number here if possible.
        // eslint-disable-next-line
        a.value != u) && (a.value = Pr(u)) : a.value !== Pr(u) && (a.value = Pr(u));
      else if (s === "submit" || s === "reset") {
        a.removeAttribute("value");
        return;
      }
      t.hasOwnProperty("value") ? Ve(a, t.type, u) : t.hasOwnProperty("defaultValue") && Ve(a, t.type, Ua(t.defaultValue)), t.checked == null && t.defaultChecked != null && (a.defaultChecked = !!t.defaultChecked);
    }
    function V(e, t, a) {
      var i = e;
      if (t.hasOwnProperty("value") || t.hasOwnProperty("defaultValue")) {
        var u = t.type, s = u === "submit" || u === "reset";
        if (s && (t.value === void 0 || t.value === null))
          return;
        var d = Pr(i._wrapperState.initialValue);
        a || d !== i.value && (i.value = d), i.defaultValue = d;
      }
      var m = i.name;
      m !== "" && (i.name = ""), i.defaultChecked = !i.defaultChecked, i.defaultChecked = !!i._wrapperState.initialChecked, m !== "" && (i.name = m);
    }
    function Y(e, t) {
      var a = e;
      b(a, t), ie(a, t);
    }
    function ie(e, t) {
      var a = t.name;
      if (t.type === "radio" && a != null) {
        for (var i = e; i.parentNode; )
          i = i.parentNode;
        Xn(a, "name");
        for (var u = i.querySelectorAll("input[name=" + JSON.stringify("" + a) + '][type="radio"]'), s = 0; s < u.length; s++) {
          var d = u[s];
          if (!(d === e || d.form !== e.form)) {
            var m = ym(d);
            if (!m)
              throw new Error("ReactDOMInput: Mixing React and non-React radio inputs with the same `name` is not supported.");
            Oi(d), b(d, m);
          }
        }
      }
    }
    function Ve(e, t, a) {
      // Focused number inputs synchronize on blur. See ChangeEventPlugin.js
      (t !== "number" || Fa(e.ownerDocument) !== e) && (a == null ? e.defaultValue = Pr(e._wrapperState.initialValue) : e.defaultValue !== Pr(a) && (e.defaultValue = Pr(a)));
    }
    var ce = !1, Ie = !1, Ct = !1;
    function At(e, t) {
      t.value == null && (typeof t.children == "object" && t.children !== null ? h.Children.forEach(t.children, function(a) {
        a != null && (typeof a == "string" || typeof a == "number" || Ie || (Ie = !0, E("Cannot infer the option value of complex children. Pass a `value` prop or use a plain string as children to <option>.")));
      }) : t.dangerouslySetInnerHTML != null && (Ct || (Ct = !0, E("Pass a `value` prop if you set dangerouslyInnerHTML so React knows which value should be selected.")))), t.selected != null && !ce && (E("Use the `defaultValue` or `value` props on <select> instead of setting `selected` on <option>."), ce = !0);
    }
    function dn(e, t) {
      t.value != null && e.setAttribute("value", Pr(Ua(t.value)));
    }
    var tn = Array.isArray;
    function pt(e) {
      return tn(e);
    }
    var nn;
    nn = !1;
    function Tn() {
      var e = Vr();
      return e ? `

Check the render method of \`` + e + "`." : "";
    }
    var zl = ["value", "defaultValue"];
    function gs(e) {
      {
        ms("select", e);
        for (var t = 0; t < zl.length; t++) {
          var a = zl[t];
          if (e[a] != null) {
            var i = pt(e[a]);
            e.multiple && !i ? E("The `%s` prop supplied to <select> must be an array if `multiple` is true.%s", a, Tn()) : !e.multiple && i && E("The `%s` prop supplied to <select> must be a scalar value if `multiple` is false.%s", a, Tn());
          }
        }
      }
    }
    function tl(e, t, a, i) {
      var u = e.options;
      if (t) {
        for (var s = a, d = {}, m = 0; m < s.length; m++)
          d["$" + s[m]] = !0;
        for (var y = 0; y < u.length; y++) {
          var x = d.hasOwnProperty("$" + u[y].value);
          u[y].selected !== x && (u[y].selected = x), x && i && (u[y].defaultSelected = !0);
        }
      } else {
        for (var R = Pr(Ua(a)), M = null, O = 0; O < u.length; O++) {
          if (u[O].value === R) {
            u[O].selected = !0, i && (u[O].defaultSelected = !0);
            return;
          }
          M === null && !u[O].disabled && (M = u[O]);
        }
        M !== null && (M.selected = !0);
      }
    }
    function Ss(e, t) {
      return st({}, t, {
        value: void 0
      });
    }
    function Tu(e, t) {
      var a = e;
      gs(t), a._wrapperState = {
        wasMultiple: !!t.multiple
      }, t.value !== void 0 && t.defaultValue !== void 0 && !nn && (E("Select elements must be either controlled or uncontrolled (specify either the value prop, or the defaultValue prop, but not both). Decide between using a controlled or uncontrolled select element and remove one of these props. More info: https://reactjs.org/link/controlled-components"), nn = !0);
    }
    function Td(e, t) {
      var a = e;
      a.multiple = !!t.multiple;
      var i = t.value;
      i != null ? tl(a, !!t.multiple, i, !1) : t.defaultValue != null && tl(a, !!t.multiple, t.defaultValue, !0);
    }
    function Ac(e, t) {
      var a = e, i = a._wrapperState.wasMultiple;
      a._wrapperState.wasMultiple = !!t.multiple;
      var u = t.value;
      u != null ? tl(a, !!t.multiple, u, !1) : i !== !!t.multiple && (t.defaultValue != null ? tl(a, !!t.multiple, t.defaultValue, !0) : tl(a, !!t.multiple, t.multiple ? [] : "", !1));
    }
    function Rd(e, t) {
      var a = e, i = t.value;
      i != null && tl(a, !!t.multiple, i, !1);
    }
    var Vv = !1;
    function wd(e, t) {
      var a = e;
      if (t.dangerouslySetInnerHTML != null)
        throw new Error("`dangerouslySetInnerHTML` does not make sense on <textarea>.");
      var i = st({}, t, {
        value: void 0,
        defaultValue: void 0,
        children: Pr(a._wrapperState.initialValue)
      });
      return i;
    }
    function bd(e, t) {
      var a = e;
      ms("textarea", t), t.value !== void 0 && t.defaultValue !== void 0 && !Vv && (E("%s contains a textarea with both value and defaultValue props. Textarea elements must be either controlled or uncontrolled (specify either the value prop, or the defaultValue prop, but not both). Decide between using a controlled or uncontrolled textarea and remove one of these props. More info: https://reactjs.org/link/controlled-components", Vr() || "A component"), Vv = !0);
      var i = t.value;
      if (i == null) {
        var u = t.children, s = t.defaultValue;
        if (u != null) {
          E("Use the `defaultValue` or `value` props instead of setting children on <textarea>.");
          {
            if (s != null)
              throw new Error("If you supply `defaultValue` on a <textarea>, do not pass children.");
            if (pt(u)) {
              if (u.length > 1)
                throw new Error("<textarea> can only have at most one child.");
              u = u[0];
            }
            s = u;
          }
        }
        s == null && (s = ""), i = s;
      }
      a._wrapperState = {
        initialValue: Ua(i)
      };
    }
    function Pv(e, t) {
      var a = e, i = Ua(t.value), u = Ua(t.defaultValue);
      if (i != null) {
        var s = Pr(i);
        s !== a.value && (a.value = s), t.defaultValue == null && a.defaultValue !== s && (a.defaultValue = s);
      }
      u != null && (a.defaultValue = Pr(u));
    }
    function Bv(e, t) {
      var a = e, i = a.textContent;
      i === a._wrapperState.initialValue && i !== "" && i !== null && (a.value = i);
    }
    function Wy(e, t) {
      Pv(e, t);
    }
    var nl = "http://www.w3.org/1999/xhtml", kd = "http://www.w3.org/1998/Math/MathML", Dd = "http://www.w3.org/2000/svg";
    function Od(e) {
      switch (e) {
        case "svg":
          return Dd;
        case "math":
          return kd;
        default:
          return nl;
      }
    }
    function Nd(e, t) {
      return e == null || e === nl ? Od(t) : e === Dd && t === "foreignObject" ? nl : e;
    }
    var Iv = function(e) {
      return typeof MSApp < "u" && MSApp.execUnsafeLocalFunction ? function(t, a, i, u) {
        MSApp.execUnsafeLocalFunction(function() {
          return e(t, a, i, u);
        });
      } : e;
    }, zc, $v = Iv(function(e, t) {
      if (e.namespaceURI === Dd && !("innerHTML" in e)) {
        zc = zc || document.createElement("div"), zc.innerHTML = "<svg>" + t.valueOf().toString() + "</svg>";
        for (var a = zc.firstChild; e.firstChild; )
          e.removeChild(e.firstChild);
        for (; a.firstChild; )
          e.appendChild(a.firstChild);
        return;
      }
      e.innerHTML = t;
    }), ra = 1, rl = 3, Pn = 8, al = 9, Md = 11, Ro = function(e, t) {
      if (t) {
        var a = e.firstChild;
        if (a && a === e.lastChild && a.nodeType === rl) {
          a.nodeValue = t;
          return;
        }
      }
      e.textContent = t;
    }, Es = {
      animation: ["animationDelay", "animationDirection", "animationDuration", "animationFillMode", "animationIterationCount", "animationName", "animationPlayState", "animationTimingFunction"],
      background: ["backgroundAttachment", "backgroundClip", "backgroundColor", "backgroundImage", "backgroundOrigin", "backgroundPositionX", "backgroundPositionY", "backgroundRepeat", "backgroundSize"],
      backgroundPosition: ["backgroundPositionX", "backgroundPositionY"],
      border: ["borderBottomColor", "borderBottomStyle", "borderBottomWidth", "borderImageOutset", "borderImageRepeat", "borderImageSlice", "borderImageSource", "borderImageWidth", "borderLeftColor", "borderLeftStyle", "borderLeftWidth", "borderRightColor", "borderRightStyle", "borderRightWidth", "borderTopColor", "borderTopStyle", "borderTopWidth"],
      borderBlockEnd: ["borderBlockEndColor", "borderBlockEndStyle", "borderBlockEndWidth"],
      borderBlockStart: ["borderBlockStartColor", "borderBlockStartStyle", "borderBlockStartWidth"],
      borderBottom: ["borderBottomColor", "borderBottomStyle", "borderBottomWidth"],
      borderColor: ["borderBottomColor", "borderLeftColor", "borderRightColor", "borderTopColor"],
      borderImage: ["borderImageOutset", "borderImageRepeat", "borderImageSlice", "borderImageSource", "borderImageWidth"],
      borderInlineEnd: ["borderInlineEndColor", "borderInlineEndStyle", "borderInlineEndWidth"],
      borderInlineStart: ["borderInlineStartColor", "borderInlineStartStyle", "borderInlineStartWidth"],
      borderLeft: ["borderLeftColor", "borderLeftStyle", "borderLeftWidth"],
      borderRadius: ["borderBottomLeftRadius", "borderBottomRightRadius", "borderTopLeftRadius", "borderTopRightRadius"],
      borderRight: ["borderRightColor", "borderRightStyle", "borderRightWidth"],
      borderStyle: ["borderBottomStyle", "borderLeftStyle", "borderRightStyle", "borderTopStyle"],
      borderTop: ["borderTopColor", "borderTopStyle", "borderTopWidth"],
      borderWidth: ["borderBottomWidth", "borderLeftWidth", "borderRightWidth", "borderTopWidth"],
      columnRule: ["columnRuleColor", "columnRuleStyle", "columnRuleWidth"],
      columns: ["columnCount", "columnWidth"],
      flex: ["flexBasis", "flexGrow", "flexShrink"],
      flexFlow: ["flexDirection", "flexWrap"],
      font: ["fontFamily", "fontFeatureSettings", "fontKerning", "fontLanguageOverride", "fontSize", "fontSizeAdjust", "fontStretch", "fontStyle", "fontVariant", "fontVariantAlternates", "fontVariantCaps", "fontVariantEastAsian", "fontVariantLigatures", "fontVariantNumeric", "fontVariantPosition", "fontWeight", "lineHeight"],
      fontVariant: ["fontVariantAlternates", "fontVariantCaps", "fontVariantEastAsian", "fontVariantLigatures", "fontVariantNumeric", "fontVariantPosition"],
      gap: ["columnGap", "rowGap"],
      grid: ["gridAutoColumns", "gridAutoFlow", "gridAutoRows", "gridTemplateAreas", "gridTemplateColumns", "gridTemplateRows"],
      gridArea: ["gridColumnEnd", "gridColumnStart", "gridRowEnd", "gridRowStart"],
      gridColumn: ["gridColumnEnd", "gridColumnStart"],
      gridColumnGap: ["columnGap"],
      gridGap: ["columnGap", "rowGap"],
      gridRow: ["gridRowEnd", "gridRowStart"],
      gridRowGap: ["rowGap"],
      gridTemplate: ["gridTemplateAreas", "gridTemplateColumns", "gridTemplateRows"],
      listStyle: ["listStyleImage", "listStylePosition", "listStyleType"],
      margin: ["marginBottom", "marginLeft", "marginRight", "marginTop"],
      marker: ["markerEnd", "markerMid", "markerStart"],
      mask: ["maskClip", "maskComposite", "maskImage", "maskMode", "maskOrigin", "maskPositionX", "maskPositionY", "maskRepeat", "maskSize"],
      maskPosition: ["maskPositionX", "maskPositionY"],
      outline: ["outlineColor", "outlineStyle", "outlineWidth"],
      overflow: ["overflowX", "overflowY"],
      padding: ["paddingBottom", "paddingLeft", "paddingRight", "paddingTop"],
      placeContent: ["alignContent", "justifyContent"],
      placeItems: ["alignItems", "justifyItems"],
      placeSelf: ["alignSelf", "justifySelf"],
      textDecoration: ["textDecorationColor", "textDecorationLine", "textDecorationStyle"],
      textEmphasis: ["textEmphasisColor", "textEmphasisStyle"],
      transition: ["transitionDelay", "transitionDuration", "transitionProperty", "transitionTimingFunction"],
      wordWrap: ["overflowWrap"]
    }, Cs = {
      animationIterationCount: !0,
      aspectRatio: !0,
      borderImageOutset: !0,
      borderImageSlice: !0,
      borderImageWidth: !0,
      boxFlex: !0,
      boxFlexGroup: !0,
      boxOrdinalGroup: !0,
      columnCount: !0,
      columns: !0,
      flex: !0,
      flexGrow: !0,
      flexPositive: !0,
      flexShrink: !0,
      flexNegative: !0,
      flexOrder: !0,
      gridArea: !0,
      gridRow: !0,
      gridRowEnd: !0,
      gridRowSpan: !0,
      gridRowStart: !0,
      gridColumn: !0,
      gridColumnEnd: !0,
      gridColumnSpan: !0,
      gridColumnStart: !0,
      fontWeight: !0,
      lineClamp: !0,
      lineHeight: !0,
      opacity: !0,
      order: !0,
      orphans: !0,
      tabSize: !0,
      widows: !0,
      zIndex: !0,
      zoom: !0,
      // SVG-related properties
      fillOpacity: !0,
      floodOpacity: !0,
      stopOpacity: !0,
      strokeDasharray: !0,
      strokeDashoffset: !0,
      strokeMiterlimit: !0,
      strokeOpacity: !0,
      strokeWidth: !0
    };
    function Yv(e, t) {
      return e + t.charAt(0).toUpperCase() + t.substring(1);
    }
    var Wv = ["Webkit", "ms", "Moz", "O"];
    Object.keys(Cs).forEach(function(e) {
      Wv.forEach(function(t) {
        Cs[Yv(t, e)] = Cs[e];
      });
    });
    function Uc(e, t, a) {
      var i = t == null || typeof t == "boolean" || t === "";
      return i ? "" : !a && typeof t == "number" && t !== 0 && !(Cs.hasOwnProperty(e) && Cs[e]) ? t + "px" : (Sa(t, e), ("" + t).trim());
    }
    var Qv = /([A-Z])/g, Zv = /^ms-/;
    function wo(e) {
      return e.replace(Qv, "-$1").toLowerCase().replace(Zv, "-ms-");
    }
    var Gv = function() {
    };
    {
      var Qy = /^(?:webkit|moz|o)[A-Z]/, Zy = /^-ms-/, qv = /-(.)/g, Ld = /;\s*$/, Ni = {}, Ru = {}, Xv = !1, _s = !1, Gy = function(e) {
        return e.replace(qv, function(t, a) {
          return a.toUpperCase();
        });
      }, Kv = function(e) {
        Ni.hasOwnProperty(e) && Ni[e] || (Ni[e] = !0, E(
          "Unsupported style property %s. Did you mean %s?",
          e,
          // As Andi Smith suggests
          // (http://www.andismith.com/blog/2012/02/modernizr-prefixed/), an `-ms` prefix
          // is converted to lowercase `ms`.
          Gy(e.replace(Zy, "ms-"))
        ));
      }, Ad = function(e) {
        Ni.hasOwnProperty(e) && Ni[e] || (Ni[e] = !0, E("Unsupported vendor-prefixed style property %s. Did you mean %s?", e, e.charAt(0).toUpperCase() + e.slice(1)));
      }, zd = function(e, t) {
        Ru.hasOwnProperty(t) && Ru[t] || (Ru[t] = !0, E(`Style property values shouldn't contain a semicolon. Try "%s: %s" instead.`, e, t.replace(Ld, "")));
      }, Jv = function(e, t) {
        Xv || (Xv = !0, E("`NaN` is an invalid value for the `%s` css style property.", e));
      }, eh = function(e, t) {
        _s || (_s = !0, E("`Infinity` is an invalid value for the `%s` css style property.", e));
      };
      Gv = function(e, t) {
        e.indexOf("-") > -1 ? Kv(e) : Qy.test(e) ? Ad(e) : Ld.test(t) && zd(e, t), typeof t == "number" && (isNaN(t) ? Jv(e, t) : isFinite(t) || eh(e, t));
      };
    }
    var th = Gv;
    function qy(e) {
      {
        var t = "", a = "";
        for (var i in e)
          if (e.hasOwnProperty(i)) {
            var u = e[i];
            if (u != null) {
              var s = i.indexOf("--") === 0;
              t += a + (s ? i : wo(i)) + ":", t += Uc(i, u, s), a = ";";
            }
          }
        return t || null;
      }
    }
    function nh(e, t) {
      var a = e.style;
      for (var i in t)
        if (t.hasOwnProperty(i)) {
          var u = i.indexOf("--") === 0;
          u || th(i, t[i]);
          var s = Uc(i, t[i], u);
          i === "float" && (i = "cssFloat"), u ? a.setProperty(i, s) : a[i] = s;
        }
    }
    function Xy(e) {
      return e == null || typeof e == "boolean" || e === "";
    }
    function rh(e) {
      var t = {};
      for (var a in e)
        for (var i = Es[a] || [a], u = 0; u < i.length; u++)
          t[i[u]] = a;
      return t;
    }
    function Ky(e, t) {
      {
        if (!t)
          return;
        var a = rh(e), i = rh(t), u = {};
        for (var s in a) {
          var d = a[s], m = i[s];
          if (m && d !== m) {
            var y = d + "," + m;
            if (u[y])
              continue;
            u[y] = !0, E("%s a style property during rerender (%s) when a conflicting property is set (%s) can lead to styling bugs. To avoid this, don't mix shorthand and non-shorthand properties for the same value; instead, replace the shorthand with separate values.", Xy(e[d]) ? "Removing" : "Updating", d, m);
          }
        }
      }
    }
    var pi = {
      area: !0,
      base: !0,
      br: !0,
      col: !0,
      embed: !0,
      hr: !0,
      img: !0,
      input: !0,
      keygen: !0,
      link: !0,
      meta: !0,
      param: !0,
      source: !0,
      track: !0,
      wbr: !0
      // NOTE: menuitem's close tag should be omitted, but that causes problems.
    }, xs = st({
      menuitem: !0
    }, pi), ah = "__html";
    function jc(e, t) {
      if (t) {
        if (xs[e] && (t.children != null || t.dangerouslySetInnerHTML != null))
          throw new Error(e + " is a void element tag and must neither have `children` nor use `dangerouslySetInnerHTML`.");
        if (t.dangerouslySetInnerHTML != null) {
          if (t.children != null)
            throw new Error("Can only set one of `children` or `props.dangerouslySetInnerHTML`.");
          if (typeof t.dangerouslySetInnerHTML != "object" || !(ah in t.dangerouslySetInnerHTML))
            throw new Error("`props.dangerouslySetInnerHTML` must be in the form `{__html: ...}`. Please visit https://reactjs.org/link/dangerously-set-inner-html for more information.");
        }
        if (!t.suppressContentEditableWarning && t.contentEditable && t.children != null && E("A component is `contentEditable` and contains `children` managed by React. It is now your responsibility to guarantee that none of those nodes are unexpectedly modified or duplicated. This is probably not intentional."), t.style != null && typeof t.style != "object")
          throw new Error("The `style` prop expects a mapping from style properties to values, not a string. For example, style={{marginRight: spacing + 'em'}} when using JSX.");
      }
    }
    function Ul(e, t) {
      if (e.indexOf("-") === -1)
        return typeof t.is == "string";
      switch (e) {
        case "annotation-xml":
        case "color-profile":
        case "font-face":
        case "font-face-src":
        case "font-face-uri":
        case "font-face-format":
        case "font-face-name":
        case "missing-glyph":
          return !1;
        default:
          return !0;
      }
    }
    var Ts = {
      // HTML
      accept: "accept",
      acceptcharset: "acceptCharset",
      "accept-charset": "acceptCharset",
      accesskey: "accessKey",
      action: "action",
      allowfullscreen: "allowFullScreen",
      alt: "alt",
      as: "as",
      async: "async",
      autocapitalize: "autoCapitalize",
      autocomplete: "autoComplete",
      autocorrect: "autoCorrect",
      autofocus: "autoFocus",
      autoplay: "autoPlay",
      autosave: "autoSave",
      capture: "capture",
      cellpadding: "cellPadding",
      cellspacing: "cellSpacing",
      challenge: "challenge",
      charset: "charSet",
      checked: "checked",
      children: "children",
      cite: "cite",
      class: "className",
      classid: "classID",
      classname: "className",
      cols: "cols",
      colspan: "colSpan",
      content: "content",
      contenteditable: "contentEditable",
      contextmenu: "contextMenu",
      controls: "controls",
      controlslist: "controlsList",
      coords: "coords",
      crossorigin: "crossOrigin",
      dangerouslysetinnerhtml: "dangerouslySetInnerHTML",
      data: "data",
      datetime: "dateTime",
      default: "default",
      defaultchecked: "defaultChecked",
      defaultvalue: "defaultValue",
      defer: "defer",
      dir: "dir",
      disabled: "disabled",
      disablepictureinpicture: "disablePictureInPicture",
      disableremoteplayback: "disableRemotePlayback",
      download: "download",
      draggable: "draggable",
      enctype: "encType",
      enterkeyhint: "enterKeyHint",
      for: "htmlFor",
      form: "form",
      formmethod: "formMethod",
      formaction: "formAction",
      formenctype: "formEncType",
      formnovalidate: "formNoValidate",
      formtarget: "formTarget",
      frameborder: "frameBorder",
      headers: "headers",
      height: "height",
      hidden: "hidden",
      high: "high",
      href: "href",
      hreflang: "hrefLang",
      htmlfor: "htmlFor",
      httpequiv: "httpEquiv",
      "http-equiv": "httpEquiv",
      icon: "icon",
      id: "id",
      imagesizes: "imageSizes",
      imagesrcset: "imageSrcSet",
      innerhtml: "innerHTML",
      inputmode: "inputMode",
      integrity: "integrity",
      is: "is",
      itemid: "itemID",
      itemprop: "itemProp",
      itemref: "itemRef",
      itemscope: "itemScope",
      itemtype: "itemType",
      keyparams: "keyParams",
      keytype: "keyType",
      kind: "kind",
      label: "label",
      lang: "lang",
      list: "list",
      loop: "loop",
      low: "low",
      manifest: "manifest",
      marginwidth: "marginWidth",
      marginheight: "marginHeight",
      max: "max",
      maxlength: "maxLength",
      media: "media",
      mediagroup: "mediaGroup",
      method: "method",
      min: "min",
      minlength: "minLength",
      multiple: "multiple",
      muted: "muted",
      name: "name",
      nomodule: "noModule",
      nonce: "nonce",
      novalidate: "noValidate",
      open: "open",
      optimum: "optimum",
      pattern: "pattern",
      placeholder: "placeholder",
      playsinline: "playsInline",
      poster: "poster",
      preload: "preload",
      profile: "profile",
      radiogroup: "radioGroup",
      readonly: "readOnly",
      referrerpolicy: "referrerPolicy",
      rel: "rel",
      required: "required",
      reversed: "reversed",
      role: "role",
      rows: "rows",
      rowspan: "rowSpan",
      sandbox: "sandbox",
      scope: "scope",
      scoped: "scoped",
      scrolling: "scrolling",
      seamless: "seamless",
      selected: "selected",
      shape: "shape",
      size: "size",
      sizes: "sizes",
      span: "span",
      spellcheck: "spellCheck",
      src: "src",
      srcdoc: "srcDoc",
      srclang: "srcLang",
      srcset: "srcSet",
      start: "start",
      step: "step",
      style: "style",
      summary: "summary",
      tabindex: "tabIndex",
      target: "target",
      title: "title",
      type: "type",
      usemap: "useMap",
      value: "value",
      width: "width",
      wmode: "wmode",
      wrap: "wrap",
      // SVG
      about: "about",
      accentheight: "accentHeight",
      "accent-height": "accentHeight",
      accumulate: "accumulate",
      additive: "additive",
      alignmentbaseline: "alignmentBaseline",
      "alignment-baseline": "alignmentBaseline",
      allowreorder: "allowReorder",
      alphabetic: "alphabetic",
      amplitude: "amplitude",
      arabicform: "arabicForm",
      "arabic-form": "arabicForm",
      ascent: "ascent",
      attributename: "attributeName",
      attributetype: "attributeType",
      autoreverse: "autoReverse",
      azimuth: "azimuth",
      basefrequency: "baseFrequency",
      baselineshift: "baselineShift",
      "baseline-shift": "baselineShift",
      baseprofile: "baseProfile",
      bbox: "bbox",
      begin: "begin",
      bias: "bias",
      by: "by",
      calcmode: "calcMode",
      capheight: "capHeight",
      "cap-height": "capHeight",
      clip: "clip",
      clippath: "clipPath",
      "clip-path": "clipPath",
      clippathunits: "clipPathUnits",
      cliprule: "clipRule",
      "clip-rule": "clipRule",
      color: "color",
      colorinterpolation: "colorInterpolation",
      "color-interpolation": "colorInterpolation",
      colorinterpolationfilters: "colorInterpolationFilters",
      "color-interpolation-filters": "colorInterpolationFilters",
      colorprofile: "colorProfile",
      "color-profile": "colorProfile",
      colorrendering: "colorRendering",
      "color-rendering": "colorRendering",
      contentscripttype: "contentScriptType",
      contentstyletype: "contentStyleType",
      cursor: "cursor",
      cx: "cx",
      cy: "cy",
      d: "d",
      datatype: "datatype",
      decelerate: "decelerate",
      descent: "descent",
      diffuseconstant: "diffuseConstant",
      direction: "direction",
      display: "display",
      divisor: "divisor",
      dominantbaseline: "dominantBaseline",
      "dominant-baseline": "dominantBaseline",
      dur: "dur",
      dx: "dx",
      dy: "dy",
      edgemode: "edgeMode",
      elevation: "elevation",
      enablebackground: "enableBackground",
      "enable-background": "enableBackground",
      end: "end",
      exponent: "exponent",
      externalresourcesrequired: "externalResourcesRequired",
      fill: "fill",
      fillopacity: "fillOpacity",
      "fill-opacity": "fillOpacity",
      fillrule: "fillRule",
      "fill-rule": "fillRule",
      filter: "filter",
      filterres: "filterRes",
      filterunits: "filterUnits",
      floodopacity: "floodOpacity",
      "flood-opacity": "floodOpacity",
      floodcolor: "floodColor",
      "flood-color": "floodColor",
      focusable: "focusable",
      fontfamily: "fontFamily",
      "font-family": "fontFamily",
      fontsize: "fontSize",
      "font-size": "fontSize",
      fontsizeadjust: "fontSizeAdjust",
      "font-size-adjust": "fontSizeAdjust",
      fontstretch: "fontStretch",
      "font-stretch": "fontStretch",
      fontstyle: "fontStyle",
      "font-style": "fontStyle",
      fontvariant: "fontVariant",
      "font-variant": "fontVariant",
      fontweight: "fontWeight",
      "font-weight": "fontWeight",
      format: "format",
      from: "from",
      fx: "fx",
      fy: "fy",
      g1: "g1",
      g2: "g2",
      glyphname: "glyphName",
      "glyph-name": "glyphName",
      glyphorientationhorizontal: "glyphOrientationHorizontal",
      "glyph-orientation-horizontal": "glyphOrientationHorizontal",
      glyphorientationvertical: "glyphOrientationVertical",
      "glyph-orientation-vertical": "glyphOrientationVertical",
      glyphref: "glyphRef",
      gradienttransform: "gradientTransform",
      gradientunits: "gradientUnits",
      hanging: "hanging",
      horizadvx: "horizAdvX",
      "horiz-adv-x": "horizAdvX",
      horizoriginx: "horizOriginX",
      "horiz-origin-x": "horizOriginX",
      ideographic: "ideographic",
      imagerendering: "imageRendering",
      "image-rendering": "imageRendering",
      in2: "in2",
      in: "in",
      inlist: "inlist",
      intercept: "intercept",
      k1: "k1",
      k2: "k2",
      k3: "k3",
      k4: "k4",
      k: "k",
      kernelmatrix: "kernelMatrix",
      kernelunitlength: "kernelUnitLength",
      kerning: "kerning",
      keypoints: "keyPoints",
      keysplines: "keySplines",
      keytimes: "keyTimes",
      lengthadjust: "lengthAdjust",
      letterspacing: "letterSpacing",
      "letter-spacing": "letterSpacing",
      lightingcolor: "lightingColor",
      "lighting-color": "lightingColor",
      limitingconeangle: "limitingConeAngle",
      local: "local",
      markerend: "markerEnd",
      "marker-end": "markerEnd",
      markerheight: "markerHeight",
      markermid: "markerMid",
      "marker-mid": "markerMid",
      markerstart: "markerStart",
      "marker-start": "markerStart",
      markerunits: "markerUnits",
      markerwidth: "markerWidth",
      mask: "mask",
      maskcontentunits: "maskContentUnits",
      maskunits: "maskUnits",
      mathematical: "mathematical",
      mode: "mode",
      numoctaves: "numOctaves",
      offset: "offset",
      opacity: "opacity",
      operator: "operator",
      order: "order",
      orient: "orient",
      orientation: "orientation",
      origin: "origin",
      overflow: "overflow",
      overlineposition: "overlinePosition",
      "overline-position": "overlinePosition",
      overlinethickness: "overlineThickness",
      "overline-thickness": "overlineThickness",
      paintorder: "paintOrder",
      "paint-order": "paintOrder",
      panose1: "panose1",
      "panose-1": "panose1",
      pathlength: "pathLength",
      patterncontentunits: "patternContentUnits",
      patterntransform: "patternTransform",
      patternunits: "patternUnits",
      pointerevents: "pointerEvents",
      "pointer-events": "pointerEvents",
      points: "points",
      pointsatx: "pointsAtX",
      pointsaty: "pointsAtY",
      pointsatz: "pointsAtZ",
      prefix: "prefix",
      preservealpha: "preserveAlpha",
      preserveaspectratio: "preserveAspectRatio",
      primitiveunits: "primitiveUnits",
      property: "property",
      r: "r",
      radius: "radius",
      refx: "refX",
      refy: "refY",
      renderingintent: "renderingIntent",
      "rendering-intent": "renderingIntent",
      repeatcount: "repeatCount",
      repeatdur: "repeatDur",
      requiredextensions: "requiredExtensions",
      requiredfeatures: "requiredFeatures",
      resource: "resource",
      restart: "restart",
      result: "result",
      results: "results",
      rotate: "rotate",
      rx: "rx",
      ry: "ry",
      scale: "scale",
      security: "security",
      seed: "seed",
      shaperendering: "shapeRendering",
      "shape-rendering": "shapeRendering",
      slope: "slope",
      spacing: "spacing",
      specularconstant: "specularConstant",
      specularexponent: "specularExponent",
      speed: "speed",
      spreadmethod: "spreadMethod",
      startoffset: "startOffset",
      stddeviation: "stdDeviation",
      stemh: "stemh",
      stemv: "stemv",
      stitchtiles: "stitchTiles",
      stopcolor: "stopColor",
      "stop-color": "stopColor",
      stopopacity: "stopOpacity",
      "stop-opacity": "stopOpacity",
      strikethroughposition: "strikethroughPosition",
      "strikethrough-position": "strikethroughPosition",
      strikethroughthickness: "strikethroughThickness",
      "strikethrough-thickness": "strikethroughThickness",
      string: "string",
      stroke: "stroke",
      strokedasharray: "strokeDasharray",
      "stroke-dasharray": "strokeDasharray",
      strokedashoffset: "strokeDashoffset",
      "stroke-dashoffset": "strokeDashoffset",
      strokelinecap: "strokeLinecap",
      "stroke-linecap": "strokeLinecap",
      strokelinejoin: "strokeLinejoin",
      "stroke-linejoin": "strokeLinejoin",
      strokemiterlimit: "strokeMiterlimit",
      "stroke-miterlimit": "strokeMiterlimit",
      strokewidth: "strokeWidth",
      "stroke-width": "strokeWidth",
      strokeopacity: "strokeOpacity",
      "stroke-opacity": "strokeOpacity",
      suppresscontenteditablewarning: "suppressContentEditableWarning",
      suppresshydrationwarning: "suppressHydrationWarning",
      surfacescale: "surfaceScale",
      systemlanguage: "systemLanguage",
      tablevalues: "tableValues",
      targetx: "targetX",
      targety: "targetY",
      textanchor: "textAnchor",
      "text-anchor": "textAnchor",
      textdecoration: "textDecoration",
      "text-decoration": "textDecoration",
      textlength: "textLength",
      textrendering: "textRendering",
      "text-rendering": "textRendering",
      to: "to",
      transform: "transform",
      typeof: "typeof",
      u1: "u1",
      u2: "u2",
      underlineposition: "underlinePosition",
      "underline-position": "underlinePosition",
      underlinethickness: "underlineThickness",
      "underline-thickness": "underlineThickness",
      unicode: "unicode",
      unicodebidi: "unicodeBidi",
      "unicode-bidi": "unicodeBidi",
      unicoderange: "unicodeRange",
      "unicode-range": "unicodeRange",
      unitsperem: "unitsPerEm",
      "units-per-em": "unitsPerEm",
      unselectable: "unselectable",
      valphabetic: "vAlphabetic",
      "v-alphabetic": "vAlphabetic",
      values: "values",
      vectoreffect: "vectorEffect",
      "vector-effect": "vectorEffect",
      version: "version",
      vertadvy: "vertAdvY",
      "vert-adv-y": "vertAdvY",
      vertoriginx: "vertOriginX",
      "vert-origin-x": "vertOriginX",
      vertoriginy: "vertOriginY",
      "vert-origin-y": "vertOriginY",
      vhanging: "vHanging",
      "v-hanging": "vHanging",
      videographic: "vIdeographic",
      "v-ideographic": "vIdeographic",
      viewbox: "viewBox",
      viewtarget: "viewTarget",
      visibility: "visibility",
      vmathematical: "vMathematical",
      "v-mathematical": "vMathematical",
      vocab: "vocab",
      widths: "widths",
      wordspacing: "wordSpacing",
      "word-spacing": "wordSpacing",
      writingmode: "writingMode",
      "writing-mode": "writingMode",
      x1: "x1",
      x2: "x2",
      x: "x",
      xchannelselector: "xChannelSelector",
      xheight: "xHeight",
      "x-height": "xHeight",
      xlinkactuate: "xlinkActuate",
      "xlink:actuate": "xlinkActuate",
      xlinkarcrole: "xlinkArcrole",
      "xlink:arcrole": "xlinkArcrole",
      xlinkhref: "xlinkHref",
      "xlink:href": "xlinkHref",
      xlinkrole: "xlinkRole",
      "xlink:role": "xlinkRole",
      xlinkshow: "xlinkShow",
      "xlink:show": "xlinkShow",
      xlinktitle: "xlinkTitle",
      "xlink:title": "xlinkTitle",
      xlinktype: "xlinkType",
      "xlink:type": "xlinkType",
      xmlbase: "xmlBase",
      "xml:base": "xmlBase",
      xmllang: "xmlLang",
      "xml:lang": "xmlLang",
      xmlns: "xmlns",
      "xml:space": "xmlSpace",
      xmlnsxlink: "xmlnsXlink",
      "xmlns:xlink": "xmlnsXlink",
      xmlspace: "xmlSpace",
      y1: "y1",
      y2: "y2",
      y: "y",
      ychannelselector: "yChannelSelector",
      z: "z",
      zoomandpan: "zoomAndPan"
    }, Fc = {
      "aria-current": 0,
      // state
      "aria-description": 0,
      "aria-details": 0,
      "aria-disabled": 0,
      // state
      "aria-hidden": 0,
      // state
      "aria-invalid": 0,
      // state
      "aria-keyshortcuts": 0,
      "aria-label": 0,
      "aria-roledescription": 0,
      // Widget Attributes
      "aria-autocomplete": 0,
      "aria-checked": 0,
      "aria-expanded": 0,
      "aria-haspopup": 0,
      "aria-level": 0,
      "aria-modal": 0,
      "aria-multiline": 0,
      "aria-multiselectable": 0,
      "aria-orientation": 0,
      "aria-placeholder": 0,
      "aria-pressed": 0,
      "aria-readonly": 0,
      "aria-required": 0,
      "aria-selected": 0,
      "aria-sort": 0,
      "aria-valuemax": 0,
      "aria-valuemin": 0,
      "aria-valuenow": 0,
      "aria-valuetext": 0,
      // Live Region Attributes
      "aria-atomic": 0,
      "aria-busy": 0,
      "aria-live": 0,
      "aria-relevant": 0,
      // Drag-and-Drop Attributes
      "aria-dropeffect": 0,
      "aria-grabbed": 0,
      // Relationship Attributes
      "aria-activedescendant": 0,
      "aria-colcount": 0,
      "aria-colindex": 0,
      "aria-colspan": 0,
      "aria-controls": 0,
      "aria-describedby": 0,
      "aria-errormessage": 0,
      "aria-flowto": 0,
      "aria-labelledby": 0,
      "aria-owns": 0,
      "aria-posinset": 0,
      "aria-rowcount": 0,
      "aria-rowindex": 0,
      "aria-rowspan": 0,
      "aria-setsize": 0
    }, bo = {}, Jy = new RegExp("^(aria)-[" + ue + "]*$"), ko = new RegExp("^(aria)[A-Z][" + ue + "]*$");
    function Ud(e, t) {
      {
        if (Ur.call(bo, t) && bo[t])
          return !0;
        if (ko.test(t)) {
          var a = "aria-" + t.slice(4).toLowerCase(), i = Fc.hasOwnProperty(a) ? a : null;
          if (i == null)
            return E("Invalid ARIA attribute `%s`. ARIA attributes follow the pattern aria-* and must be lowercase.", t), bo[t] = !0, !0;
          if (t !== i)
            return E("Invalid ARIA attribute `%s`. Did you mean `%s`?", t, i), bo[t] = !0, !0;
        }
        if (Jy.test(t)) {
          var u = t.toLowerCase(), s = Fc.hasOwnProperty(u) ? u : null;
          if (s == null)
            return bo[t] = !0, !1;
          if (t !== s)
            return E("Unknown ARIA attribute `%s`. Did you mean `%s`?", t, s), bo[t] = !0, !0;
        }
      }
      return !0;
    }
    function Rs(e, t) {
      {
        var a = [];
        for (var i in t) {
          var u = Ud(e, i);
          u || a.push(i);
        }
        var s = a.map(function(d) {
          return "`" + d + "`";
        }).join(", ");
        a.length === 1 ? E("Invalid aria prop %s on <%s> tag. For details, see https://reactjs.org/link/invalid-aria-props", s, e) : a.length > 1 && E("Invalid aria props %s on <%s> tag. For details, see https://reactjs.org/link/invalid-aria-props", s, e);
      }
    }
    function jd(e, t) {
      Ul(e, t) || Rs(e, t);
    }
    var Fd = !1;
    function Hc(e, t) {
      {
        if (e !== "input" && e !== "textarea" && e !== "select")
          return;
        t != null && t.value === null && !Fd && (Fd = !0, e === "select" && t.multiple ? E("`value` prop on `%s` should not be null. Consider using an empty array when `multiple` is set to `true` to clear the component or `undefined` for uncontrolled components.", e) : E("`value` prop on `%s` should not be null. Consider using an empty string to clear the component or `undefined` for uncontrolled components.", e));
      }
    }
    var wu = function() {
    };
    {
      var yr = {}, Hd = /^on./, Vc = /^on[^A-Z]/, ih = new RegExp("^(aria)-[" + ue + "]*$"), lh = new RegExp("^(aria)[A-Z][" + ue + "]*$");
      wu = function(e, t, a, i) {
        if (Ur.call(yr, t) && yr[t])
          return !0;
        var u = t.toLowerCase();
        if (u === "onfocusin" || u === "onfocusout")
          return E("React uses onFocus and onBlur instead of onFocusIn and onFocusOut. All React events are normalized to bubble, so onFocusIn and onFocusOut are not needed/supported by React."), yr[t] = !0, !0;
        if (i != null) {
          var s = i.registrationNameDependencies, d = i.possibleRegistrationNames;
          if (s.hasOwnProperty(t))
            return !0;
          var m = d.hasOwnProperty(u) ? d[u] : null;
          if (m != null)
            return E("Invalid event handler property `%s`. Did you mean `%s`?", t, m), yr[t] = !0, !0;
          if (Hd.test(t))
            return E("Unknown event handler property `%s`. It will be ignored.", t), yr[t] = !0, !0;
        } else if (Hd.test(t))
          return Vc.test(t) && E("Invalid event handler property `%s`. React events use the camelCase naming convention, for example `onClick`.", t), yr[t] = !0, !0;
        if (ih.test(t) || lh.test(t))
          return !0;
        if (u === "innerhtml")
          return E("Directly setting property `innerHTML` is not permitted. For more information, lookup documentation on `dangerouslySetInnerHTML`."), yr[t] = !0, !0;
        if (u === "aria")
          return E("The `aria` attribute is reserved for future use in React. Pass individual `aria-` attributes instead."), yr[t] = !0, !0;
        if (u === "is" && a !== null && a !== void 0 && typeof a != "string")
          return E("Received a `%s` for a string attribute `is`. If this is expected, cast the value to a string.", typeof a), yr[t] = !0, !0;
        if (typeof a == "number" && isNaN(a))
          return E("Received NaN for the `%s` attribute. If this is expected, cast the value to a string.", t), yr[t] = !0, !0;
        var y = cn(t), x = y !== null && y.type === Kn;
        if (Ts.hasOwnProperty(u)) {
          var R = Ts[u];
          if (R !== t)
            return E("Invalid DOM property `%s`. Did you mean `%s`?", t, R), yr[t] = !0, !0;
        } else if (!x && t !== u)
          return E("React does not recognize the `%s` prop on a DOM element. If you intentionally want it to appear in the DOM as a custom attribute, spell it as lowercase `%s` instead. If you accidentally passed it from a parent component, remove it from the DOM element.", t, u), yr[t] = !0, !0;
        return typeof a == "boolean" && mn(t, a, y, !1) ? (a ? E('Received `%s` for a non-boolean attribute `%s`.\n\nIf you want to write it to the DOM, pass a string instead: %s="%s" or %s={value.toString()}.', a, t, t, a, t) : E('Received `%s` for a non-boolean attribute `%s`.\n\nIf you want to write it to the DOM, pass a string instead: %s="%s" or %s={value.toString()}.\n\nIf you used to conditionally omit it with %s={condition && value}, pass %s={condition ? value : undefined} instead.', a, t, t, a, t, t, t), yr[t] = !0, !0) : x ? !0 : mn(t, a, y, !1) ? (yr[t] = !0, !1) : ((a === "false" || a === "true") && y !== null && y.type === Vn && (E("Received the string `%s` for the boolean attribute `%s`. %s Did you mean %s={%s}?", a, t, a === "false" ? "The browser will interpret it as a truthy value." : 'Although this works, it will not work as expected if you pass the string "false".', t, a), yr[t] = !0), !0);
      };
    }
    var uh = function(e, t, a) {
      {
        var i = [];
        for (var u in t) {
          var s = wu(e, u, t[u], a);
          s || i.push(u);
        }
        var d = i.map(function(m) {
          return "`" + m + "`";
        }).join(", ");
        i.length === 1 ? E("Invalid value for prop %s on <%s> tag. Either remove it from the element, or pass a string or number value to keep it in the DOM. For details, see https://reactjs.org/link/attribute-behavior ", d, e) : i.length > 1 && E("Invalid values for props %s on <%s> tag. Either remove them from the element, or pass a string or number value to keep them in the DOM. For details, see https://reactjs.org/link/attribute-behavior ", d, e);
      }
    };
    function oh(e, t, a) {
      Ul(e, t) || uh(e, t, a);
    }
    var Vd = 1, Pc = 2, Ha = 4, Pd = Vd | Pc | Ha, bu = null;
    function eg(e) {
      bu !== null && E("Expected currently replaying event to be null. This error is likely caused by a bug in React. Please file an issue."), bu = e;
    }
    function tg() {
      bu === null && E("Expected currently replaying event to not be null. This error is likely caused by a bug in React. Please file an issue."), bu = null;
    }
    function ws(e) {
      return e === bu;
    }
    function Bd(e) {
      var t = e.target || e.srcElement || window;
      return t.correspondingUseElement && (t = t.correspondingUseElement), t.nodeType === rl ? t.parentNode : t;
    }
    var Bc = null, ku = null, Gt = null;
    function Ic(e) {
      var t = Go(e);
      if (t) {
        if (typeof Bc != "function")
          throw new Error("setRestoreImplementation() needs to be called to handle a target for controlled events. This error is likely caused by a bug in React. Please file an issue.");
        var a = t.stateNode;
        if (a) {
          var i = ym(a);
          Bc(t.stateNode, t.type, i);
        }
      }
    }
    function $c(e) {
      Bc = e;
    }
    function Do(e) {
      ku ? Gt ? Gt.push(e) : Gt = [e] : ku = e;
    }
    function sh() {
      return ku !== null || Gt !== null;
    }
    function Yc() {
      if (ku) {
        var e = ku, t = Gt;
        if (ku = null, Gt = null, Ic(e), t)
          for (var a = 0; a < t.length; a++)
            Ic(t[a]);
      }
    }
    var Oo = function(e, t) {
      return e(t);
    }, bs = function() {
    }, jl = !1;
    function ch() {
      var e = sh();
      e && (bs(), Yc());
    }
    function fh(e, t, a) {
      if (jl)
        return e(t, a);
      jl = !0;
      try {
        return Oo(e, t, a);
      } finally {
        jl = !1, ch();
      }
    }
    function ng(e, t, a) {
      Oo = e, bs = a;
    }
    function dh(e) {
      return e === "button" || e === "input" || e === "select" || e === "textarea";
    }
    function Wc(e, t, a) {
      switch (e) {
        case "onClick":
        case "onClickCapture":
        case "onDoubleClick":
        case "onDoubleClickCapture":
        case "onMouseDown":
        case "onMouseDownCapture":
        case "onMouseMove":
        case "onMouseMoveCapture":
        case "onMouseUp":
        case "onMouseUpCapture":
        case "onMouseEnter":
          return !!(a.disabled && dh(t));
        default:
          return !1;
      }
    }
    function Fl(e, t) {
      var a = e.stateNode;
      if (a === null)
        return null;
      var i = ym(a);
      if (i === null)
        return null;
      var u = i[t];
      if (Wc(t, e.type, i))
        return null;
      if (u && typeof u != "function")
        throw new Error("Expected `" + t + "` listener to be a function, instead got a value of `" + typeof u + "` type.");
      return u;
    }
    var ks = !1;
    if (Hn)
      try {
        var Du = {};
        Object.defineProperty(Du, "passive", {
          get: function() {
            ks = !0;
          }
        }), window.addEventListener("test", Du, Du), window.removeEventListener("test", Du, Du);
      } catch {
        ks = !1;
      }
    function Qc(e, t, a, i, u, s, d, m, y) {
      var x = Array.prototype.slice.call(arguments, 3);
      try {
        t.apply(a, x);
      } catch (R) {
        this.onError(R);
      }
    }
    var Zc = Qc;
    if (typeof window < "u" && typeof window.dispatchEvent == "function" && typeof document < "u" && typeof document.createEvent == "function") {
      var Id = document.createElement("react");
      Zc = function(t, a, i, u, s, d, m, y, x) {
        if (typeof document > "u" || document === null)
          throw new Error("The `document` global was defined when React was initialized, but is not defined anymore. This can happen in a test environment if a component schedules an update from an asynchronous callback, but the test has already finished running. To solve this, you can either unmount the component at the end of your test (and ensure that any asynchronous operations get canceled in `componentWillUnmount`), or you can change the test itself to be asynchronous.");
        var R = document.createEvent("Event"), M = !1, O = !0, H = window.event, B = Object.getOwnPropertyDescriptor(window, "event");
        function W() {
          Id.removeEventListener(Q, Pe, !1), typeof window.event < "u" && window.hasOwnProperty("event") && (window.event = H);
        }
        var he = Array.prototype.slice.call(arguments, 3);
        function Pe() {
          M = !0, W(), a.apply(i, he), O = !1;
        }
        var Me, Nt = !1, Rt = !1;
        function U(j) {
          if (Me = j.error, Nt = !0, Me === null && j.colno === 0 && j.lineno === 0 && (Rt = !0), j.defaultPrevented && Me != null && typeof Me == "object")
            try {
              Me._suppressLogging = !0;
            } catch {
            }
        }
        var Q = "react-" + (t || "invokeguardedcallback");
        if (window.addEventListener("error", U), Id.addEventListener(Q, Pe, !1), R.initEvent(Q, !1, !1), Id.dispatchEvent(R), B && Object.defineProperty(window, "event", B), M && O && (Nt ? Rt && (Me = new Error("A cross-origin error was thrown. React doesn't have access to the actual error object in development. See https://reactjs.org/link/crossorigin-error for more information.")) : Me = new Error(`An error was thrown inside one of your components, but React doesn't know what it was. This is likely due to browser flakiness. React does its best to preserve the "Pause on exceptions" behavior of the DevTools, which requires some DEV-mode only tricks. It's possible that these don't work in your browser. Try triggering the error in production mode, or switching to a modern browser. If you suspect that this is actually an issue with React, please file an issue.`), this.onError(Me)), window.removeEventListener("error", U), !M)
          return W(), Qc.apply(this, arguments);
      };
    }
    var ph = Zc, No = !1, Gc = null, Mo = !1, Mi = null, vh = {
      onError: function(e) {
        No = !0, Gc = e;
      }
    };
    function Hl(e, t, a, i, u, s, d, m, y) {
      No = !1, Gc = null, ph.apply(vh, arguments);
    }
    function Li(e, t, a, i, u, s, d, m, y) {
      if (Hl.apply(this, arguments), No) {
        var x = Os();
        Mo || (Mo = !0, Mi = x);
      }
    }
    function Ds() {
      if (Mo) {
        var e = Mi;
        throw Mo = !1, Mi = null, e;
      }
    }
    function il() {
      return No;
    }
    function Os() {
      if (No) {
        var e = Gc;
        return No = !1, Gc = null, e;
      } else
        throw new Error("clearCaughtError was called but no error was captured. This error is likely caused by a bug in React. Please file an issue.");
    }
    function Lo(e) {
      return e._reactInternals;
    }
    function rg(e) {
      return e._reactInternals !== void 0;
    }
    function Ou(e, t) {
      e._reactInternals = t;
    }
    var Ue = (
      /*                      */
      0
    ), vi = (
      /*                */
      1
    ), Rn = (
      /*                    */
      2
    ), kt = (
      /*                       */
      4
    ), Va = (
      /*                */
      16
    ), Pa = (
      /*                 */
      32
    ), pn = (
      /*                     */
      64
    ), Le = (
      /*                   */
      128
    ), Mr = (
      /*            */
      256
    ), Dn = (
      /*                          */
      512
    ), er = (
      /*                     */
      1024
    ), aa = (
      /*                      */
      2048
    ), ia = (
      /*                    */
      4096
    ), Bn = (
      /*                   */
      8192
    ), Ao = (
      /*             */
      16384
    ), hh = (
      /*               */
      32767
    ), Ns = (
      /*                   */
      32768
    ), ur = (
      /*                */
      65536
    ), qc = (
      /* */
      131072
    ), Ai = (
      /*                       */
      1048576
    ), zo = (
      /*                    */
      2097152
    ), ll = (
      /*                 */
      4194304
    ), Xc = (
      /*                */
      8388608
    ), Vl = (
      /*               */
      16777216
    ), zi = (
      /*              */
      33554432
    ), Pl = (
      // TODO: Remove Update flag from before mutation phase by re-landing Visibility
      // flag logic (see #20043)
      kt | er | 0
    ), Bl = Rn | kt | Va | Pa | Dn | ia | Bn, Il = kt | pn | Dn | Bn, ul = aa | Va, In = ll | Xc | zo, Ba = p.ReactCurrentOwner;
    function xa(e) {
      var t = e, a = e;
      if (e.alternate)
        for (; t.return; )
          t = t.return;
      else {
        var i = t;
        do
          t = i, (t.flags & (Rn | ia)) !== Ue && (a = t.return), i = t.return;
        while (i);
      }
      return t.tag === re ? a : null;
    }
    function Ui(e) {
      if (e.tag === ze) {
        var t = e.memoizedState;
        if (t === null) {
          var a = e.alternate;
          a !== null && (t = a.memoizedState);
        }
        if (t !== null)
          return t.dehydrated;
      }
      return null;
    }
    function ji(e) {
      return e.tag === re ? e.stateNode.containerInfo : null;
    }
    function Nu(e) {
      return xa(e) === e;
    }
    function mh(e) {
      {
        var t = Ba.current;
        if (t !== null && t.tag === $) {
          var a = t, i = a.stateNode;
          i._warnedAboutRefsInRender || E("%s is accessing isMounted inside its render() function. render() should be a pure function of props and state. It should never access something that requires stale data from the previous render, such as refs. Move this logic to componentDidMount and componentDidUpdate instead.", Xe(a) || "A component"), i._warnedAboutRefsInRender = !0;
        }
      }
      var u = Lo(e);
      return u ? xa(u) === u : !1;
    }
    function Kc(e) {
      if (xa(e) !== e)
        throw new Error("Unable to find node on an unmounted component.");
    }
    function Jc(e) {
      var t = e.alternate;
      if (!t) {
        var a = xa(e);
        if (a === null)
          throw new Error("Unable to find node on an unmounted component.");
        return a !== e ? null : e;
      }
      for (var i = e, u = t; ; ) {
        var s = i.return;
        if (s === null)
          break;
        var d = s.alternate;
        if (d === null) {
          var m = s.return;
          if (m !== null) {
            i = u = m;
            continue;
          }
          break;
        }
        if (s.child === d.child) {
          for (var y = s.child; y; ) {
            if (y === i)
              return Kc(s), e;
            if (y === u)
              return Kc(s), t;
            y = y.sibling;
          }
          throw new Error("Unable to find node on an unmounted component.");
        }
        if (i.return !== u.return)
          i = s, u = d;
        else {
          for (var x = !1, R = s.child; R; ) {
            if (R === i) {
              x = !0, i = s, u = d;
              break;
            }
            if (R === u) {
              x = !0, u = s, i = d;
              break;
            }
            R = R.sibling;
          }
          if (!x) {
            for (R = d.child; R; ) {
              if (R === i) {
                x = !0, i = d, u = s;
                break;
              }
              if (R === u) {
                x = !0, u = d, i = s;
                break;
              }
              R = R.sibling;
            }
            if (!x)
              throw new Error("Child was not found in either parent set. This indicates a bug in React related to the return pointer. Please file an issue.");
          }
        }
        if (i.alternate !== u)
          throw new Error("Return fibers should always be each others' alternates. This error is likely caused by a bug in React. Please file an issue.");
      }
      if (i.tag !== re)
        throw new Error("Unable to find node on an unmounted component.");
      return i.stateNode.current === i ? e : t;
    }
    function la(e) {
      var t = Jc(e);
      return t !== null ? ua(t) : null;
    }
    function ua(e) {
      if (e.tag === de || e.tag === nt)
        return e;
      for (var t = e.child; t !== null; ) {
        var a = ua(t);
        if (a !== null)
          return a;
        t = t.sibling;
      }
      return null;
    }
    function Cn(e) {
      var t = Jc(e);
      return t !== null ? Ia(t) : null;
    }
    function Ia(e) {
      if (e.tag === de || e.tag === nt)
        return e;
      for (var t = e.child; t !== null; ) {
        if (t.tag !== be) {
          var a = Ia(t);
          if (a !== null)
            return a;
        }
        t = t.sibling;
      }
      return null;
    }
    var $d = c.unstable_scheduleCallback, yh = c.unstable_cancelCallback, Yd = c.unstable_shouldYield, Wd = c.unstable_requestPaint, tr = c.unstable_now, ef = c.unstable_getCurrentPriorityLevel, Ms = c.unstable_ImmediatePriority, $l = c.unstable_UserBlockingPriority, ol = c.unstable_NormalPriority, ag = c.unstable_LowPriority, Mu = c.unstable_IdlePriority, tf = c.unstable_yieldValue, gh = c.unstable_setDisableYieldValue, Lu = null, Ln = null, ve = null, Ta = !1, oa = typeof __REACT_DEVTOOLS_GLOBAL_HOOK__ < "u";
    function Uo(e) {
      if (typeof __REACT_DEVTOOLS_GLOBAL_HOOK__ > "u")
        return !1;
      var t = __REACT_DEVTOOLS_GLOBAL_HOOK__;
      if (t.isDisabled)
        return !0;
      if (!t.supportsFiber)
        return E("The installed version of React DevTools is too old and will not work with the current version of React. Please update React DevTools. https://reactjs.org/link/react-devtools"), !0;
      try {
        Ze && (e = st({}, e, {
          getLaneLabelMap: Au,
          injectProfilingHooks: $a
        })), Lu = t.inject(e), Ln = t;
      } catch (a) {
        E("React instrumentation encountered an error: %s.", a);
      }
      return !!t.checkDCE;
    }
    function Qd(e, t) {
      if (Ln && typeof Ln.onScheduleFiberRoot == "function")
        try {
          Ln.onScheduleFiberRoot(Lu, e, t);
        } catch (a) {
          Ta || (Ta = !0, E("React instrumentation encountered an error: %s", a));
        }
    }
    function Zd(e, t) {
      if (Ln && typeof Ln.onCommitFiberRoot == "function")
        try {
          var a = (e.current.flags & Le) === Le;
          if (Ye) {
            var i;
            switch (t) {
              case Br:
                i = Ms;
                break;
              case Hi:
                i = $l;
                break;
              case Ya:
                i = ol;
                break;
              case Wa:
                i = Mu;
                break;
              default:
                i = ol;
                break;
            }
            Ln.onCommitFiberRoot(Lu, e, i, a);
          }
        } catch (u) {
          Ta || (Ta = !0, E("React instrumentation encountered an error: %s", u));
        }
    }
    function Gd(e) {
      if (Ln && typeof Ln.onPostCommitFiberRoot == "function")
        try {
          Ln.onPostCommitFiberRoot(Lu, e);
        } catch (t) {
          Ta || (Ta = !0, E("React instrumentation encountered an error: %s", t));
        }
    }
    function qd(e) {
      if (Ln && typeof Ln.onCommitFiberUnmount == "function")
        try {
          Ln.onCommitFiberUnmount(Lu, e);
        } catch (t) {
          Ta || (Ta = !0, E("React instrumentation encountered an error: %s", t));
        }
    }
    function wn(e) {
      if (typeof tf == "function" && (gh(e), _(e)), Ln && typeof Ln.setStrictMode == "function")
        try {
          Ln.setStrictMode(Lu, e);
        } catch (t) {
          Ta || (Ta = !0, E("React instrumentation encountered an error: %s", t));
        }
    }
    function $a(e) {
      ve = e;
    }
    function Au() {
      {
        for (var e = /* @__PURE__ */ new Map(), t = 1, a = 0; a < ju; a++) {
          var i = _h(t);
          e.set(t, i), t *= 2;
        }
        return e;
      }
    }
    function Xd(e) {
      ve !== null && typeof ve.markCommitStarted == "function" && ve.markCommitStarted(e);
    }
    function Kd() {
      ve !== null && typeof ve.markCommitStopped == "function" && ve.markCommitStopped();
    }
    function Ra(e) {
      ve !== null && typeof ve.markComponentRenderStarted == "function" && ve.markComponentRenderStarted(e);
    }
    function wa() {
      ve !== null && typeof ve.markComponentRenderStopped == "function" && ve.markComponentRenderStopped();
    }
    function Jd(e) {
      ve !== null && typeof ve.markComponentPassiveEffectMountStarted == "function" && ve.markComponentPassiveEffectMountStarted(e);
    }
    function Sh() {
      ve !== null && typeof ve.markComponentPassiveEffectMountStopped == "function" && ve.markComponentPassiveEffectMountStopped();
    }
    function sl(e) {
      ve !== null && typeof ve.markComponentPassiveEffectUnmountStarted == "function" && ve.markComponentPassiveEffectUnmountStarted(e);
    }
    function Yl() {
      ve !== null && typeof ve.markComponentPassiveEffectUnmountStopped == "function" && ve.markComponentPassiveEffectUnmountStopped();
    }
    function nf(e) {
      ve !== null && typeof ve.markComponentLayoutEffectMountStarted == "function" && ve.markComponentLayoutEffectMountStarted(e);
    }
    function Eh() {
      ve !== null && typeof ve.markComponentLayoutEffectMountStopped == "function" && ve.markComponentLayoutEffectMountStopped();
    }
    function Ls(e) {
      ve !== null && typeof ve.markComponentLayoutEffectUnmountStarted == "function" && ve.markComponentLayoutEffectUnmountStarted(e);
    }
    function ep() {
      ve !== null && typeof ve.markComponentLayoutEffectUnmountStopped == "function" && ve.markComponentLayoutEffectUnmountStopped();
    }
    function As(e, t, a) {
      ve !== null && typeof ve.markComponentErrored == "function" && ve.markComponentErrored(e, t, a);
    }
    function Fi(e, t, a) {
      ve !== null && typeof ve.markComponentSuspended == "function" && ve.markComponentSuspended(e, t, a);
    }
    function zs(e) {
      ve !== null && typeof ve.markLayoutEffectsStarted == "function" && ve.markLayoutEffectsStarted(e);
    }
    function Us() {
      ve !== null && typeof ve.markLayoutEffectsStopped == "function" && ve.markLayoutEffectsStopped();
    }
    function zu(e) {
      ve !== null && typeof ve.markPassiveEffectsStarted == "function" && ve.markPassiveEffectsStarted(e);
    }
    function tp() {
      ve !== null && typeof ve.markPassiveEffectsStopped == "function" && ve.markPassiveEffectsStopped();
    }
    function Uu(e) {
      ve !== null && typeof ve.markRenderStarted == "function" && ve.markRenderStarted(e);
    }
    function Ch() {
      ve !== null && typeof ve.markRenderYielded == "function" && ve.markRenderYielded();
    }
    function rf() {
      ve !== null && typeof ve.markRenderStopped == "function" && ve.markRenderStopped();
    }
    function bn(e) {
      ve !== null && typeof ve.markRenderScheduled == "function" && ve.markRenderScheduled(e);
    }
    function af(e, t) {
      ve !== null && typeof ve.markForceUpdateScheduled == "function" && ve.markForceUpdateScheduled(e, t);
    }
    function js(e, t) {
      ve !== null && typeof ve.markStateUpdateScheduled == "function" && ve.markStateUpdateScheduled(e, t);
    }
    var je = (
      /*                         */
      0
    ), yt = (
      /*                 */
      1
    ), Vt = (
      /*                    */
      2
    ), rn = (
      /*               */
      8
    ), Pt = (
      /*              */
      16
    ), $n = Math.clz32 ? Math.clz32 : Fs, or = Math.log, lf = Math.LN2;
    function Fs(e) {
      var t = e >>> 0;
      return t === 0 ? 32 : 31 - (or(t) / lf | 0) | 0;
    }
    var ju = 31, X = (
      /*                        */
      0
    ), jt = (
      /*                          */
      0
    ), We = (
      /*                        */
      1
    ), Wl = (
      /*    */
      2
    ), hi = (
      /*             */
      4
    ), Lr = (
      /*            */
      8
    ), An = (
      /*                     */
      16
    ), cl = (
      /*                */
      32
    ), Ql = (
      /*                       */
      4194240
    ), Fu = (
      /*                        */
      64
    ), uf = (
      /*                        */
      128
    ), of = (
      /*                        */
      256
    ), sf = (
      /*                        */
      512
    ), cf = (
      /*                        */
      1024
    ), ff = (
      /*                        */
      2048
    ), df = (
      /*                        */
      4096
    ), pf = (
      /*                        */
      8192
    ), vf = (
      /*                        */
      16384
    ), Hu = (
      /*                       */
      32768
    ), hf = (
      /*                       */
      65536
    ), jo = (
      /*                       */
      131072
    ), Fo = (
      /*                       */
      262144
    ), mf = (
      /*                       */
      524288
    ), Hs = (
      /*                       */
      1048576
    ), yf = (
      /*                       */
      2097152
    ), Vs = (
      /*                            */
      130023424
    ), Vu = (
      /*                             */
      4194304
    ), gf = (
      /*                             */
      8388608
    ), Ps = (
      /*                             */
      16777216
    ), Sf = (
      /*                             */
      33554432
    ), Ef = (
      /*                             */
      67108864
    ), np = Vu, Bs = (
      /*          */
      134217728
    ), rp = (
      /*                          */
      268435455
    ), Is = (
      /*               */
      268435456
    ), Pu = (
      /*                        */
      536870912
    ), sa = (
      /*                   */
      1073741824
    );
    function _h(e) {
      {
        if (e & We)
          return "Sync";
        if (e & Wl)
          return "InputContinuousHydration";
        if (e & hi)
          return "InputContinuous";
        if (e & Lr)
          return "DefaultHydration";
        if (e & An)
          return "Default";
        if (e & cl)
          return "TransitionHydration";
        if (e & Ql)
          return "Transition";
        if (e & Vs)
          return "Retry";
        if (e & Bs)
          return "SelectiveHydration";
        if (e & Is)
          return "IdleHydration";
        if (e & Pu)
          return "Idle";
        if (e & sa)
          return "Offscreen";
      }
    }
    var un = -1, Bu = Fu, Cf = Vu;
    function $s(e) {
      switch (Zl(e)) {
        case We:
          return We;
        case Wl:
          return Wl;
        case hi:
          return hi;
        case Lr:
          return Lr;
        case An:
          return An;
        case cl:
          return cl;
        case Fu:
        case uf:
        case of:
        case sf:
        case cf:
        case ff:
        case df:
        case pf:
        case vf:
        case Hu:
        case hf:
        case jo:
        case Fo:
        case mf:
        case Hs:
        case yf:
          return e & Ql;
        case Vu:
        case gf:
        case Ps:
        case Sf:
        case Ef:
          return e & Vs;
        case Bs:
          return Bs;
        case Is:
          return Is;
        case Pu:
          return Pu;
        case sa:
          return sa;
        default:
          return E("Should have found matching lanes. This is a bug in React."), e;
      }
    }
    function _f(e, t) {
      var a = e.pendingLanes;
      if (a === X)
        return X;
      var i = X, u = e.suspendedLanes, s = e.pingedLanes, d = a & rp;
      if (d !== X) {
        var m = d & ~u;
        if (m !== X)
          i = $s(m);
        else {
          var y = d & s;
          y !== X && (i = $s(y));
        }
      } else {
        var x = a & ~u;
        x !== X ? i = $s(x) : s !== X && (i = $s(s));
      }
      if (i === X)
        return X;
      if (t !== X && t !== i && // If we already suspended with a delay, then interrupting is fine. Don't
      // bother waiting until the root is complete.
      (t & u) === X) {
        var R = Zl(i), M = Zl(t);
        if (
          // Tests whether the next lane is equal or lower priority than the wip
          // one. This works because the bits decrease in priority as you go left.
          R >= M || // Default priority updates should not interrupt transition updates. The
          // only difference between default updates and transition updates is that
          // default updates do not support refresh transitions.
          R === An && (M & Ql) !== X
        )
          return t;
      }
      (i & hi) !== X && (i |= a & An);
      var O = e.entangledLanes;
      if (O !== X)
        for (var H = e.entanglements, B = i & O; B > 0; ) {
          var W = Yn(B), he = 1 << W;
          i |= H[W], B &= ~he;
        }
      return i;
    }
    function mi(e, t) {
      for (var a = e.eventTimes, i = un; t > 0; ) {
        var u = Yn(t), s = 1 << u, d = a[u];
        d > i && (i = d), t &= ~s;
      }
      return i;
    }
    function ap(e, t) {
      switch (e) {
        case We:
        case Wl:
        case hi:
          return t + 250;
        case Lr:
        case An:
        case cl:
        case Fu:
        case uf:
        case of:
        case sf:
        case cf:
        case ff:
        case df:
        case pf:
        case vf:
        case Hu:
        case hf:
        case jo:
        case Fo:
        case mf:
        case Hs:
        case yf:
          return t + 5e3;
        case Vu:
        case gf:
        case Ps:
        case Sf:
        case Ef:
          return un;
        case Bs:
        case Is:
        case Pu:
        case sa:
          return un;
        default:
          return E("Should have found matching lanes. This is a bug in React."), un;
      }
    }
    function xf(e, t) {
      for (var a = e.pendingLanes, i = e.suspendedLanes, u = e.pingedLanes, s = e.expirationTimes, d = a; d > 0; ) {
        var m = Yn(d), y = 1 << m, x = s[m];
        x === un ? ((y & i) === X || (y & u) !== X) && (s[m] = ap(y, t)) : x <= t && (e.expiredLanes |= y), d &= ~y;
      }
    }
    function xh(e) {
      return $s(e.pendingLanes);
    }
    function Tf(e) {
      var t = e.pendingLanes & ~sa;
      return t !== X ? t : t & sa ? sa : X;
    }
    function Th(e) {
      return (e & We) !== X;
    }
    function Ys(e) {
      return (e & rp) !== X;
    }
    function Iu(e) {
      return (e & Vs) === e;
    }
    function ip(e) {
      var t = We | hi | An;
      return (e & t) === X;
    }
    function lp(e) {
      return (e & Ql) === e;
    }
    function Rf(e, t) {
      var a = Wl | hi | Lr | An;
      return (t & a) !== X;
    }
    function Rh(e, t) {
      return (t & e.expiredLanes) !== X;
    }
    function up(e) {
      return (e & Ql) !== X;
    }
    function op() {
      var e = Bu;
      return Bu <<= 1, (Bu & Ql) === X && (Bu = Fu), e;
    }
    function wh() {
      var e = Cf;
      return Cf <<= 1, (Cf & Vs) === X && (Cf = Vu), e;
    }
    function Zl(e) {
      return e & -e;
    }
    function Ws(e) {
      return Zl(e);
    }
    function Yn(e) {
      return 31 - $n(e);
    }
    function gr(e) {
      return Yn(e);
    }
    function ca(e, t) {
      return (e & t) !== X;
    }
    function $u(e, t) {
      return (e & t) === t;
    }
    function ut(e, t) {
      return e | t;
    }
    function Qs(e, t) {
      return e & ~t;
    }
    function sp(e, t) {
      return e & t;
    }
    function bh(e) {
      return e;
    }
    function kh(e, t) {
      return e !== jt && e < t ? e : t;
    }
    function Zs(e) {
      for (var t = [], a = 0; a < ju; a++)
        t.push(e);
      return t;
    }
    function Ho(e, t, a) {
      e.pendingLanes |= t, t !== Pu && (e.suspendedLanes = X, e.pingedLanes = X);
      var i = e.eventTimes, u = gr(t);
      i[u] = a;
    }
    function Dh(e, t) {
      e.suspendedLanes |= t, e.pingedLanes &= ~t;
      for (var a = e.expirationTimes, i = t; i > 0; ) {
        var u = Yn(i), s = 1 << u;
        a[u] = un, i &= ~s;
      }
    }
    function wf(e, t, a) {
      e.pingedLanes |= e.suspendedLanes & t;
    }
    function cp(e, t) {
      var a = e.pendingLanes & ~t;
      e.pendingLanes = t, e.suspendedLanes = X, e.pingedLanes = X, e.expiredLanes &= t, e.mutableReadLanes &= t, e.entangledLanes &= t;
      for (var i = e.entanglements, u = e.eventTimes, s = e.expirationTimes, d = a; d > 0; ) {
        var m = Yn(d), y = 1 << m;
        i[m] = X, u[m] = un, s[m] = un, d &= ~y;
      }
    }
    function bf(e, t) {
      for (var a = e.entangledLanes |= t, i = e.entanglements, u = a; u; ) {
        var s = Yn(u), d = 1 << s;
        // Is this one of the newly entangled lanes?
        d & t | // Is this lane transitively entangled with the newly entangled lanes?
        i[s] & t && (i[s] |= t), u &= ~d;
      }
    }
    function fp(e, t) {
      var a = Zl(t), i;
      switch (a) {
        case hi:
          i = Wl;
          break;
        case An:
          i = Lr;
          break;
        case Fu:
        case uf:
        case of:
        case sf:
        case cf:
        case ff:
        case df:
        case pf:
        case vf:
        case Hu:
        case hf:
        case jo:
        case Fo:
        case mf:
        case Hs:
        case yf:
        case Vu:
        case gf:
        case Ps:
        case Sf:
        case Ef:
          i = cl;
          break;
        case Pu:
          i = Is;
          break;
        default:
          i = jt;
          break;
      }
      return (i & (e.suspendedLanes | t)) !== jt ? jt : i;
    }
    function Gs(e, t, a) {
      if (oa)
        for (var i = e.pendingUpdatersLaneMap; a > 0; ) {
          var u = gr(a), s = 1 << u, d = i[u];
          d.add(t), a &= ~s;
        }
    }
    function Oh(e, t) {
      if (oa)
        for (var a = e.pendingUpdatersLaneMap, i = e.memoizedUpdaters; t > 0; ) {
          var u = gr(t), s = 1 << u, d = a[u];
          d.size > 0 && (d.forEach(function(m) {
            var y = m.alternate;
            (y === null || !i.has(y)) && i.add(m);
          }), d.clear()), t &= ~s;
        }
    }
    function dp(e, t) {
      return null;
    }
    var Br = We, Hi = hi, Ya = An, Wa = Pu, qs = jt;
    function Qa() {
      return qs;
    }
    function Wn(e) {
      qs = e;
    }
    function Nh(e, t) {
      var a = qs;
      try {
        return qs = e, t();
      } finally {
        qs = a;
      }
    }
    function Mh(e, t) {
      return e !== 0 && e < t ? e : t;
    }
    function Xs(e, t) {
      return e > t ? e : t;
    }
    function sr(e, t) {
      return e !== 0 && e < t;
    }
    function Lh(e) {
      var t = Zl(e);
      return sr(Br, t) ? sr(Hi, t) ? Ys(t) ? Ya : Wa : Hi : Br;
    }
    function kf(e) {
      var t = e.current.memoizedState;
      return t.isDehydrated;
    }
    var Ks;
    function Ar(e) {
      Ks = e;
    }
    function ig(e) {
      Ks(e);
    }
    var Ce;
    function Vo(e) {
      Ce = e;
    }
    var Df;
    function Ah(e) {
      Df = e;
    }
    var zh;
    function Js(e) {
      zh = e;
    }
    var ec;
    function pp(e) {
      ec = e;
    }
    var Of = !1, tc = [], fl = null, Vi = null, Pi = null, zn = /* @__PURE__ */ new Map(), Ir = /* @__PURE__ */ new Map(), $r = [], Uh = [
      "mousedown",
      "mouseup",
      "touchcancel",
      "touchend",
      "touchstart",
      "auxclick",
      "dblclick",
      "pointercancel",
      "pointerdown",
      "pointerup",
      "dragend",
      "dragstart",
      "drop",
      "compositionend",
      "compositionstart",
      "keydown",
      "keypress",
      "keyup",
      "input",
      "textInput",
      // Intentionally camelCase
      "copy",
      "cut",
      "paste",
      "click",
      "change",
      "contextmenu",
      "reset",
      "submit"
    ];
    function jh(e) {
      return Uh.indexOf(e) > -1;
    }
    function yi(e, t, a, i, u) {
      return {
        blockedOn: e,
        domEventName: t,
        eventSystemFlags: a,
        nativeEvent: u,
        targetContainers: [i]
      };
    }
    function vp(e, t) {
      switch (e) {
        case "focusin":
        case "focusout":
          fl = null;
          break;
        case "dragenter":
        case "dragleave":
          Vi = null;
          break;
        case "mouseover":
        case "mouseout":
          Pi = null;
          break;
        case "pointerover":
        case "pointerout": {
          var a = t.pointerId;
          zn.delete(a);
          break;
        }
        case "gotpointercapture":
        case "lostpointercapture": {
          var i = t.pointerId;
          Ir.delete(i);
          break;
        }
      }
    }
    function fa(e, t, a, i, u, s) {
      if (e === null || e.nativeEvent !== s) {
        var d = yi(t, a, i, u, s);
        if (t !== null) {
          var m = Go(t);
          m !== null && Ce(m);
        }
        return d;
      }
      e.eventSystemFlags |= i;
      var y = e.targetContainers;
      return u !== null && y.indexOf(u) === -1 && y.push(u), e;
    }
    function lg(e, t, a, i, u) {
      switch (t) {
        case "focusin": {
          var s = u;
          return fl = fa(fl, e, t, a, i, s), !0;
        }
        case "dragenter": {
          var d = u;
          return Vi = fa(Vi, e, t, a, i, d), !0;
        }
        case "mouseover": {
          var m = u;
          return Pi = fa(Pi, e, t, a, i, m), !0;
        }
        case "pointerover": {
          var y = u, x = y.pointerId;
          return zn.set(x, fa(zn.get(x) || null, e, t, a, i, y)), !0;
        }
        case "gotpointercapture": {
          var R = u, M = R.pointerId;
          return Ir.set(M, fa(Ir.get(M) || null, e, t, a, i, R)), !0;
        }
      }
      return !1;
    }
    function hp(e) {
      var t = pc(e.target);
      if (t !== null) {
        var a = xa(t);
        if (a !== null) {
          var i = a.tag;
          if (i === ze) {
            var u = Ui(a);
            if (u !== null) {
              e.blockedOn = u, ec(e.priority, function() {
                Df(a);
              });
              return;
            }
          } else if (i === re) {
            var s = a.stateNode;
            if (kf(s)) {
              e.blockedOn = ji(a);
              return;
            }
          }
        }
      }
      e.blockedOn = null;
    }
    function Fh(e) {
      for (var t = zh(), a = {
        blockedOn: null,
        target: e,
        priority: t
      }, i = 0; i < $r.length && sr(t, $r[i].priority); i++)
        ;
      $r.splice(i, 0, a), i === 0 && hp(a);
    }
    function nc(e) {
      if (e.blockedOn !== null)
        return !1;
      for (var t = e.targetContainers; t.length > 0; ) {
        var a = t[0], i = Bo(e.domEventName, e.eventSystemFlags, a, e.nativeEvent);
        if (i === null) {
          var u = e.nativeEvent, s = new u.constructor(u.type, u);
          eg(s), u.target.dispatchEvent(s), tg();
        } else {
          var d = Go(i);
          return d !== null && Ce(d), e.blockedOn = i, !1;
        }
        t.shift();
      }
      return !0;
    }
    function mp(e, t, a) {
      nc(e) && a.delete(t);
    }
    function ug() {
      Of = !1, fl !== null && nc(fl) && (fl = null), Vi !== null && nc(Vi) && (Vi = null), Pi !== null && nc(Pi) && (Pi = null), zn.forEach(mp), Ir.forEach(mp);
    }
    function Gl(e, t) {
      e.blockedOn === t && (e.blockedOn = null, Of || (Of = !0, c.unstable_scheduleCallback(c.unstable_NormalPriority, ug)));
    }
    function Yu(e) {
      if (tc.length > 0) {
        Gl(tc[0], e);
        for (var t = 1; t < tc.length; t++) {
          var a = tc[t];
          a.blockedOn === e && (a.blockedOn = null);
        }
      }
      fl !== null && Gl(fl, e), Vi !== null && Gl(Vi, e), Pi !== null && Gl(Pi, e);
      var i = function(m) {
        return Gl(m, e);
      };
      zn.forEach(i), Ir.forEach(i);
      for (var u = 0; u < $r.length; u++) {
        var s = $r[u];
        s.blockedOn === e && (s.blockedOn = null);
      }
      for (; $r.length > 0; ) {
        var d = $r[0];
        if (d.blockedOn !== null)
          break;
        hp(d), d.blockedOn === null && $r.shift();
      }
    }
    var Sr = p.ReactCurrentBatchConfig, Dt = !0;
    function nr(e) {
      Dt = !!e;
    }
    function Qn() {
      return Dt;
    }
    function Er(e, t, a) {
      var i = Nf(t), u;
      switch (i) {
        case Br:
          u = ba;
          break;
        case Hi:
          u = Po;
          break;
        case Ya:
        default:
          u = Un;
          break;
      }
      return u.bind(null, t, a, e);
    }
    function ba(e, t, a, i) {
      var u = Qa(), s = Sr.transition;
      Sr.transition = null;
      try {
        Wn(Br), Un(e, t, a, i);
      } finally {
        Wn(u), Sr.transition = s;
      }
    }
    function Po(e, t, a, i) {
      var u = Qa(), s = Sr.transition;
      Sr.transition = null;
      try {
        Wn(Hi), Un(e, t, a, i);
      } finally {
        Wn(u), Sr.transition = s;
      }
    }
    function Un(e, t, a, i) {
      Dt && rc(e, t, a, i);
    }
    function rc(e, t, a, i) {
      var u = Bo(e, t, a, i);
      if (u === null) {
        Tg(e, t, i, Bi, a), vp(e, i);
        return;
      }
      if (lg(u, e, t, a, i)) {
        i.stopPropagation();
        return;
      }
      if (vp(e, i), t & Ha && jh(e)) {
        for (; u !== null; ) {
          var s = Go(u);
          s !== null && ig(s);
          var d = Bo(e, t, a, i);
          if (d === null && Tg(e, t, i, Bi, a), d === u)
            break;
          u = d;
        }
        u !== null && i.stopPropagation();
        return;
      }
      Tg(e, t, i, null, a);
    }
    var Bi = null;
    function Bo(e, t, a, i) {
      Bi = null;
      var u = Bd(i), s = pc(u);
      if (s !== null) {
        var d = xa(s);
        if (d === null)
          s = null;
        else {
          var m = d.tag;
          if (m === ze) {
            var y = Ui(d);
            if (y !== null)
              return y;
            s = null;
          } else if (m === re) {
            var x = d.stateNode;
            if (kf(x))
              return ji(d);
            s = null;
          } else d !== s && (s = null);
        }
      }
      return Bi = s, null;
    }
    function Nf(e) {
      switch (e) {
        case "cancel":
        case "click":
        case "close":
        case "contextmenu":
        case "copy":
        case "cut":
        case "auxclick":
        case "dblclick":
        case "dragend":
        case "dragstart":
        case "drop":
        case "focusin":
        case "focusout":
        case "input":
        case "invalid":
        case "keydown":
        case "keypress":
        case "keyup":
        case "mousedown":
        case "mouseup":
        case "paste":
        case "pause":
        case "play":
        case "pointercancel":
        case "pointerdown":
        case "pointerup":
        case "ratechange":
        case "reset":
        case "resize":
        case "seeked":
        case "submit":
        case "touchcancel":
        case "touchend":
        case "touchstart":
        case "volumechange":
        case "change":
        case "selectionchange":
        case "textInput":
        case "compositionstart":
        case "compositionend":
        case "compositionupdate":
        case "beforeblur":
        case "afterblur":
        case "beforeinput":
        case "blur":
        case "fullscreenchange":
        case "focus":
        case "hashchange":
        case "popstate":
        case "select":
        case "selectstart":
          return Br;
        case "drag":
        case "dragenter":
        case "dragexit":
        case "dragleave":
        case "dragover":
        case "mousemove":
        case "mouseout":
        case "mouseover":
        case "pointermove":
        case "pointerout":
        case "pointerover":
        case "scroll":
        case "toggle":
        case "touchmove":
        case "wheel":
        case "mouseenter":
        case "mouseleave":
        case "pointerenter":
        case "pointerleave":
          return Hi;
        case "message": {
          var t = ef();
          switch (t) {
            case Ms:
              return Br;
            case $l:
              return Hi;
            case ol:
            case ag:
              return Ya;
            case Mu:
              return Wa;
            default:
              return Ya;
          }
        }
        default:
          return Ya;
      }
    }
    function ac(e, t, a) {
      return e.addEventListener(t, a, !1), a;
    }
    function da(e, t, a) {
      return e.addEventListener(t, a, !0), a;
    }
    function yp(e, t, a, i) {
      return e.addEventListener(t, a, {
        capture: !0,
        passive: i
      }), a;
    }
    function Io(e, t, a, i) {
      return e.addEventListener(t, a, {
        passive: i
      }), a;
    }
    var ka = null, $o = null, Wu = null;
    function ql(e) {
      return ka = e, $o = ic(), !0;
    }
    function Mf() {
      ka = null, $o = null, Wu = null;
    }
    function dl() {
      if (Wu)
        return Wu;
      var e, t = $o, a = t.length, i, u = ic(), s = u.length;
      for (e = 0; e < a && t[e] === u[e]; e++)
        ;
      var d = a - e;
      for (i = 1; i <= d && t[a - i] === u[s - i]; i++)
        ;
      var m = i > 1 ? 1 - i : void 0;
      return Wu = u.slice(e, m), Wu;
    }
    function ic() {
      return "value" in ka ? ka.value : ka.textContent;
    }
    function Xl(e) {
      var t, a = e.keyCode;
      return "charCode" in e ? (t = e.charCode, t === 0 && a === 13 && (t = 13)) : t = a, t === 10 && (t = 13), t >= 32 || t === 13 ? t : 0;
    }
    function Yo() {
      return !0;
    }
    function lc() {
      return !1;
    }
    function zr(e) {
      function t(a, i, u, s, d) {
        this._reactName = a, this._targetInst = u, this.type = i, this.nativeEvent = s, this.target = d, this.currentTarget = null;
        for (var m in e)
          if (e.hasOwnProperty(m)) {
            var y = e[m];
            y ? this[m] = y(s) : this[m] = s[m];
          }
        var x = s.defaultPrevented != null ? s.defaultPrevented : s.returnValue === !1;
        return x ? this.isDefaultPrevented = Yo : this.isDefaultPrevented = lc, this.isPropagationStopped = lc, this;
      }
      return st(t.prototype, {
        preventDefault: function() {
          this.defaultPrevented = !0;
          var a = this.nativeEvent;
          a && (a.preventDefault ? a.preventDefault() : typeof a.returnValue != "unknown" && (a.returnValue = !1), this.isDefaultPrevented = Yo);
        },
        stopPropagation: function() {
          var a = this.nativeEvent;
          a && (a.stopPropagation ? a.stopPropagation() : typeof a.cancelBubble != "unknown" && (a.cancelBubble = !0), this.isPropagationStopped = Yo);
        },
        /**
         * We release all dispatched `SyntheticEvent`s after each event loop, adding
         * them back into the pool. This allows a way to hold onto a reference that
         * won't be added back into the pool.
         */
        persist: function() {
        },
        /**
         * Checks if this event should be released back into the pool.
         *
         * @return {boolean} True if this should not be released, false otherwise.
         */
        isPersistent: Yo
      }), t;
    }
    var Zn = {
      eventPhase: 0,
      bubbles: 0,
      cancelable: 0,
      timeStamp: function(e) {
        return e.timeStamp || Date.now();
      },
      defaultPrevented: 0,
      isTrusted: 0
    }, Ii = zr(Zn), Yr = st({}, Zn, {
      view: 0,
      detail: 0
    }), pa = zr(Yr), Lf, uc, Qu;
    function og(e) {
      e !== Qu && (Qu && e.type === "mousemove" ? (Lf = e.screenX - Qu.screenX, uc = e.screenY - Qu.screenY) : (Lf = 0, uc = 0), Qu = e);
    }
    var gi = st({}, Yr, {
      screenX: 0,
      screenY: 0,
      clientX: 0,
      clientY: 0,
      pageX: 0,
      pageY: 0,
      ctrlKey: 0,
      shiftKey: 0,
      altKey: 0,
      metaKey: 0,
      getModifierState: _n,
      button: 0,
      buttons: 0,
      relatedTarget: function(e) {
        return e.relatedTarget === void 0 ? e.fromElement === e.srcElement ? e.toElement : e.fromElement : e.relatedTarget;
      },
      movementX: function(e) {
        return "movementX" in e ? e.movementX : (og(e), Lf);
      },
      movementY: function(e) {
        return "movementY" in e ? e.movementY : uc;
      }
    }), gp = zr(gi), Sp = st({}, gi, {
      dataTransfer: 0
    }), Zu = zr(Sp), Ep = st({}, Yr, {
      relatedTarget: 0
    }), pl = zr(Ep), Hh = st({}, Zn, {
      animationName: 0,
      elapsedTime: 0,
      pseudoElement: 0
    }), Vh = zr(Hh), Cp = st({}, Zn, {
      clipboardData: function(e) {
        return "clipboardData" in e ? e.clipboardData : window.clipboardData;
      }
    }), Af = zr(Cp), sg = st({}, Zn, {
      data: 0
    }), Ph = zr(sg), Bh = Ph, Ih = {
      Esc: "Escape",
      Spacebar: " ",
      Left: "ArrowLeft",
      Up: "ArrowUp",
      Right: "ArrowRight",
      Down: "ArrowDown",
      Del: "Delete",
      Win: "OS",
      Menu: "ContextMenu",
      Apps: "ContextMenu",
      Scroll: "ScrollLock",
      MozPrintableKey: "Unidentified"
    }, Gu = {
      8: "Backspace",
      9: "Tab",
      12: "Clear",
      13: "Enter",
      16: "Shift",
      17: "Control",
      18: "Alt",
      19: "Pause",
      20: "CapsLock",
      27: "Escape",
      32: " ",
      33: "PageUp",
      34: "PageDown",
      35: "End",
      36: "Home",
      37: "ArrowLeft",
      38: "ArrowUp",
      39: "ArrowRight",
      40: "ArrowDown",
      45: "Insert",
      46: "Delete",
      112: "F1",
      113: "F2",
      114: "F3",
      115: "F4",
      116: "F5",
      117: "F6",
      118: "F7",
      119: "F8",
      120: "F9",
      121: "F10",
      122: "F11",
      123: "F12",
      144: "NumLock",
      145: "ScrollLock",
      224: "Meta"
    };
    function cg(e) {
      if (e.key) {
        var t = Ih[e.key] || e.key;
        if (t !== "Unidentified")
          return t;
      }
      if (e.type === "keypress") {
        var a = Xl(e);
        return a === 13 ? "Enter" : String.fromCharCode(a);
      }
      return e.type === "keydown" || e.type === "keyup" ? Gu[e.keyCode] || "Unidentified" : "";
    }
    var Wo = {
      Alt: "altKey",
      Control: "ctrlKey",
      Meta: "metaKey",
      Shift: "shiftKey"
    };
    function $h(e) {
      var t = this, a = t.nativeEvent;
      if (a.getModifierState)
        return a.getModifierState(e);
      var i = Wo[e];
      return i ? !!a[i] : !1;
    }
    function _n(e) {
      return $h;
    }
    var fg = st({}, Yr, {
      key: cg,
      code: 0,
      location: 0,
      ctrlKey: 0,
      shiftKey: 0,
      altKey: 0,
      metaKey: 0,
      repeat: 0,
      locale: 0,
      getModifierState: _n,
      // Legacy Interface
      charCode: function(e) {
        return e.type === "keypress" ? Xl(e) : 0;
      },
      keyCode: function(e) {
        return e.type === "keydown" || e.type === "keyup" ? e.keyCode : 0;
      },
      which: function(e) {
        return e.type === "keypress" ? Xl(e) : e.type === "keydown" || e.type === "keyup" ? e.keyCode : 0;
      }
    }), Yh = zr(fg), dg = st({}, gi, {
      pointerId: 0,
      width: 0,
      height: 0,
      pressure: 0,
      tangentialPressure: 0,
      tiltX: 0,
      tiltY: 0,
      twist: 0,
      pointerType: 0,
      isPrimary: 0
    }), Wh = zr(dg), Qh = st({}, Yr, {
      touches: 0,
      targetTouches: 0,
      changedTouches: 0,
      altKey: 0,
      metaKey: 0,
      ctrlKey: 0,
      shiftKey: 0,
      getModifierState: _n
    }), Zh = zr(Qh), pg = st({}, Zn, {
      propertyName: 0,
      elapsedTime: 0,
      pseudoElement: 0
    }), Za = zr(pg), _p = st({}, gi, {
      deltaX: function(e) {
        return "deltaX" in e ? e.deltaX : (
          // Fallback to `wheelDeltaX` for Webkit and normalize (right is positive).
          "wheelDeltaX" in e ? -e.wheelDeltaX : 0
        );
      },
      deltaY: function(e) {
        return "deltaY" in e ? e.deltaY : (
          // Fallback to `wheelDeltaY` for Webkit and normalize (down is positive).
          "wheelDeltaY" in e ? -e.wheelDeltaY : (
            // Fallback to `wheelDelta` for IE<9 and normalize (down is positive).
            "wheelDelta" in e ? -e.wheelDelta : 0
          )
        );
      },
      deltaZ: 0,
      // Browsers without "deltaMode" is reporting in raw wheel delta where one
      // notch on the scroll is always +/- 120, roughly equivalent to pixels.
      // A good approximation of DOM_DELTA_LINE (1) is 5% of viewport size or
      // ~40 pixels, for DOM_DELTA_SCREEN (2) it is 87.5% of viewport size.
      deltaMode: 0
    }), vg = zr(_p), Kl = [9, 13, 27, 32], oc = 229, vl = Hn && "CompositionEvent" in window, Jl = null;
    Hn && "documentMode" in document && (Jl = document.documentMode);
    var xp = Hn && "TextEvent" in window && !Jl, zf = Hn && (!vl || Jl && Jl > 8 && Jl <= 11), Gh = 32, Uf = String.fromCharCode(Gh);
    function hg() {
      ht("onBeforeInput", ["compositionend", "keypress", "textInput", "paste"]), ht("onCompositionEnd", ["compositionend", "focusout", "keydown", "keypress", "keyup", "mousedown"]), ht("onCompositionStart", ["compositionstart", "focusout", "keydown", "keypress", "keyup", "mousedown"]), ht("onCompositionUpdate", ["compositionupdate", "focusout", "keydown", "keypress", "keyup", "mousedown"]);
    }
    var Tp = !1;
    function qh(e) {
      return (e.ctrlKey || e.altKey || e.metaKey) && // ctrlKey && altKey is equivalent to AltGr, and is not a command.
      !(e.ctrlKey && e.altKey);
    }
    function jf(e) {
      switch (e) {
        case "compositionstart":
          return "onCompositionStart";
        case "compositionend":
          return "onCompositionEnd";
        case "compositionupdate":
          return "onCompositionUpdate";
      }
    }
    function Ff(e, t) {
      return e === "keydown" && t.keyCode === oc;
    }
    function Rp(e, t) {
      switch (e) {
        case "keyup":
          return Kl.indexOf(t.keyCode) !== -1;
        case "keydown":
          return t.keyCode !== oc;
        case "keypress":
        case "mousedown":
        case "focusout":
          return !0;
        default:
          return !1;
      }
    }
    function Hf(e) {
      var t = e.detail;
      return typeof t == "object" && "data" in t ? t.data : null;
    }
    function Xh(e) {
      return e.locale === "ko";
    }
    var qu = !1;
    function wp(e, t, a, i, u) {
      var s, d;
      if (vl ? s = jf(t) : qu ? Rp(t, i) && (s = "onCompositionEnd") : Ff(t, i) && (s = "onCompositionStart"), !s)
        return null;
      zf && !Xh(i) && (!qu && s === "onCompositionStart" ? qu = ql(u) : s === "onCompositionEnd" && qu && (d = dl()));
      var m = am(a, s);
      if (m.length > 0) {
        var y = new Ph(s, t, null, i, u);
        if (e.push({
          event: y,
          listeners: m
        }), d)
          y.data = d;
        else {
          var x = Hf(i);
          x !== null && (y.data = x);
        }
      }
    }
    function Vf(e, t) {
      switch (e) {
        case "compositionend":
          return Hf(t);
        case "keypress":
          var a = t.which;
          return a !== Gh ? null : (Tp = !0, Uf);
        case "textInput":
          var i = t.data;
          return i === Uf && Tp ? null : i;
        default:
          return null;
      }
    }
    function bp(e, t) {
      if (qu) {
        if (e === "compositionend" || !vl && Rp(e, t)) {
          var a = dl();
          return Mf(), qu = !1, a;
        }
        return null;
      }
      switch (e) {
        case "paste":
          return null;
        case "keypress":
          if (!qh(t)) {
            if (t.char && t.char.length > 1)
              return t.char;
            if (t.which)
              return String.fromCharCode(t.which);
          }
          return null;
        case "compositionend":
          return zf && !Xh(t) ? null : t.data;
        default:
          return null;
      }
    }
    function Pf(e, t, a, i, u) {
      var s;
      if (xp ? s = Vf(t, i) : s = bp(t, i), !s)
        return null;
      var d = am(a, "onBeforeInput");
      if (d.length > 0) {
        var m = new Bh("onBeforeInput", "beforeinput", null, i, u);
        e.push({
          event: m,
          listeners: d
        }), m.data = s;
      }
    }
    function Kh(e, t, a, i, u, s, d) {
      wp(e, t, a, i, u), Pf(e, t, a, i, u);
    }
    var mg = {
      color: !0,
      date: !0,
      datetime: !0,
      "datetime-local": !0,
      email: !0,
      month: !0,
      number: !0,
      password: !0,
      range: !0,
      search: !0,
      tel: !0,
      text: !0,
      time: !0,
      url: !0,
      week: !0
    };
    function sc(e) {
      var t = e && e.nodeName && e.nodeName.toLowerCase();
      return t === "input" ? !!mg[e.type] : t === "textarea";
    }
    /**
     * Checks if an event is supported in the current execution environment.
     *
     * NOTE: This will not work correctly for non-generic events such as `change`,
     * `reset`, `load`, `error`, and `select`.
     *
     * Borrows from Modernizr.
     *
     * @param {string} eventNameSuffix Event name, e.g. "click".
     * @return {boolean} True if the event is supported.
     * @internal
     * @license Modernizr 3.0.0pre (Custom Build) | MIT
     */
    function yg(e) {
      if (!Hn)
        return !1;
      var t = "on" + e, a = t in document;
      if (!a) {
        var i = document.createElement("div");
        i.setAttribute(t, "return;"), a = typeof i[t] == "function";
      }
      return a;
    }
    function cc() {
      ht("onChange", ["change", "click", "focusin", "focusout", "input", "keydown", "keyup", "selectionchange"]);
    }
    function Jh(e, t, a, i) {
      Do(i);
      var u = am(t, "onChange");
      if (u.length > 0) {
        var s = new Ii("onChange", "change", null, a, i);
        e.push({
          event: s,
          listeners: u
        });
      }
    }
    var eu = null, n = null;
    function r(e) {
      var t = e.nodeName && e.nodeName.toLowerCase();
      return t === "select" || t === "input" && e.type === "file";
    }
    function l(e) {
      var t = [];
      Jh(t, n, e, Bd(e)), fh(o, t);
    }
    function o(e) {
      FC(e, 0);
    }
    function f(e) {
      var t = Qf(e);
      if (Oi(t))
        return e;
    }
    function v(e, t) {
      if (e === "change")
        return t;
    }
    var C = !1;
    Hn && (C = yg("input") && (!document.documentMode || document.documentMode > 9));
    function w(e, t) {
      eu = e, n = t, eu.attachEvent("onpropertychange", P);
    }
    function D() {
      eu && (eu.detachEvent("onpropertychange", P), eu = null, n = null);
    }
    function P(e) {
      e.propertyName === "value" && f(n) && l(e);
    }
    function J(e, t, a) {
      e === "focusin" ? (D(), w(t, a)) : e === "focusout" && D();
    }
    function te(e, t) {
      if (e === "selectionchange" || e === "keyup" || e === "keydown")
        return f(n);
    }
    function K(e) {
      var t = e.nodeName;
      return t && t.toLowerCase() === "input" && (e.type === "checkbox" || e.type === "radio");
    }
    function ge(e, t) {
      if (e === "click")
        return f(t);
    }
    function Te(e, t) {
      if (e === "input" || e === "change")
        return f(t);
    }
    function ke(e) {
      var t = e._wrapperState;
      !t || !t.controlled || e.type !== "number" || Ve(e, "number", e.value);
    }
    function jn(e, t, a, i, u, s, d) {
      var m = a ? Qf(a) : window, y, x;
      if (r(m) ? y = v : sc(m) ? C ? y = Te : (y = te, x = J) : K(m) && (y = ge), y) {
        var R = y(t, a);
        if (R) {
          Jh(e, R, i, u);
          return;
        }
      }
      x && x(t, m, a), t === "focusout" && ke(m);
    }
    function z() {
      Xt("onMouseEnter", ["mouseout", "mouseover"]), Xt("onMouseLeave", ["mouseout", "mouseover"]), Xt("onPointerEnter", ["pointerout", "pointerover"]), Xt("onPointerLeave", ["pointerout", "pointerover"]);
    }
    function N(e, t, a, i, u, s, d) {
      var m = t === "mouseover" || t === "pointerover", y = t === "mouseout" || t === "pointerout";
      if (m && !ws(i)) {
        var x = i.relatedTarget || i.fromElement;
        if (x && (pc(x) || Pp(x)))
          return;
      }
      if (!(!y && !m)) {
        var R;
        if (u.window === u)
          R = u;
        else {
          var M = u.ownerDocument;
          M ? R = M.defaultView || M.parentWindow : R = window;
        }
        var O, H;
        if (y) {
          var B = i.relatedTarget || i.toElement;
          if (O = a, H = B ? pc(B) : null, H !== null) {
            var W = xa(H);
            (H !== W || H.tag !== de && H.tag !== nt) && (H = null);
          }
        } else
          O = null, H = a;
        if (O !== H) {
          var he = gp, Pe = "onMouseLeave", Me = "onMouseEnter", Nt = "mouse";
          (t === "pointerout" || t === "pointerover") && (he = Wh, Pe = "onPointerLeave", Me = "onPointerEnter", Nt = "pointer");
          var Rt = O == null ? R : Qf(O), U = H == null ? R : Qf(H), Q = new he(Pe, Nt + "leave", O, i, u);
          Q.target = Rt, Q.relatedTarget = U;
          var j = null, ne = pc(u);
          if (ne === a) {
            var Ee = new he(Me, Nt + "enter", H, i, u);
            Ee.target = U, Ee.relatedTarget = Rt, j = Ee;
          }
          nR(e, Q, j, O, H);
        }
      }
    }
    function F(e, t) {
      return e === t && (e !== 0 || 1 / e === 1 / t) || e !== e && t !== t;
    }
    var ee = typeof Object.is == "function" ? Object.is : F;
    function Re(e, t) {
      if (ee(e, t))
        return !0;
      if (typeof e != "object" || e === null || typeof t != "object" || t === null)
        return !1;
      var a = Object.keys(e), i = Object.keys(t);
      if (a.length !== i.length)
        return !1;
      for (var u = 0; u < a.length; u++) {
        var s = a[u];
        if (!Ur.call(t, s) || !ee(e[s], t[s]))
          return !1;
      }
      return !0;
    }
    function Be(e) {
      for (; e && e.firstChild; )
        e = e.firstChild;
      return e;
    }
    function $e(e) {
      for (; e; ) {
        if (e.nextSibling)
          return e.nextSibling;
        e = e.parentNode;
      }
    }
    function qe(e, t) {
      for (var a = Be(e), i = 0, u = 0; a; ) {
        if (a.nodeType === rl) {
          if (u = i + a.textContent.length, i <= t && u >= t)
            return {
              node: a,
              offset: t - i
            };
          i = u;
        }
        a = Be($e(a));
      }
    }
    function cr(e) {
      var t = e.ownerDocument, a = t && t.defaultView || window, i = a.getSelection && a.getSelection();
      if (!i || i.rangeCount === 0)
        return null;
      var u = i.anchorNode, s = i.anchorOffset, d = i.focusNode, m = i.focusOffset;
      try {
        u.nodeType, d.nodeType;
      } catch {
        return null;
      }
      return Bt(e, u, s, d, m);
    }
    function Bt(e, t, a, i, u) {
      var s = 0, d = -1, m = -1, y = 0, x = 0, R = e, M = null;
      e: for (; ; ) {
        for (var O = null; R === t && (a === 0 || R.nodeType === rl) && (d = s + a), R === i && (u === 0 || R.nodeType === rl) && (m = s + u), R.nodeType === rl && (s += R.nodeValue.length), (O = R.firstChild) !== null; )
          M = R, R = O;
        for (; ; ) {
          if (R === e)
            break e;
          if (M === t && ++y === a && (d = s), M === i && ++x === u && (m = s), (O = R.nextSibling) !== null)
            break;
          R = M, M = R.parentNode;
        }
        R = O;
      }
      return d === -1 || m === -1 ? null : {
        start: d,
        end: m
      };
    }
    function tu(e, t) {
      var a = e.ownerDocument || document, i = a && a.defaultView || window;
      if (i.getSelection) {
        var u = i.getSelection(), s = e.textContent.length, d = Math.min(t.start, s), m = t.end === void 0 ? d : Math.min(t.end, s);
        if (!u.extend && d > m) {
          var y = m;
          m = d, d = y;
        }
        var x = qe(e, d), R = qe(e, m);
        if (x && R) {
          if (u.rangeCount === 1 && u.anchorNode === x.node && u.anchorOffset === x.offset && u.focusNode === R.node && u.focusOffset === R.offset)
            return;
          var M = a.createRange();
          M.setStart(x.node, x.offset), u.removeAllRanges(), d > m ? (u.addRange(M), u.extend(R.node, R.offset)) : (M.setEnd(R.node, R.offset), u.addRange(M));
        }
      }
    }
    function em(e) {
      return e && e.nodeType === rl;
    }
    function bC(e, t) {
      return !e || !t ? !1 : e === t ? !0 : em(e) ? !1 : em(t) ? bC(e, t.parentNode) : "contains" in e ? e.contains(t) : e.compareDocumentPosition ? !!(e.compareDocumentPosition(t) & 16) : !1;
    }
    function FT(e) {
      return e && e.ownerDocument && bC(e.ownerDocument.documentElement, e);
    }
    function HT(e) {
      try {
        return typeof e.contentWindow.location.href == "string";
      } catch {
        return !1;
      }
    }
    function kC() {
      for (var e = window, t = Fa(); t instanceof e.HTMLIFrameElement; ) {
        if (HT(t))
          e = t.contentWindow;
        else
          return t;
        t = Fa(e.document);
      }
      return t;
    }
    function gg(e) {
      var t = e && e.nodeName && e.nodeName.toLowerCase();
      return t && (t === "input" && (e.type === "text" || e.type === "search" || e.type === "tel" || e.type === "url" || e.type === "password") || t === "textarea" || e.contentEditable === "true");
    }
    function VT() {
      var e = kC();
      return {
        focusedElem: e,
        selectionRange: gg(e) ? BT(e) : null
      };
    }
    function PT(e) {
      var t = kC(), a = e.focusedElem, i = e.selectionRange;
      if (t !== a && FT(a)) {
        i !== null && gg(a) && IT(a, i);
        for (var u = [], s = a; s = s.parentNode; )
          s.nodeType === ra && u.push({
            element: s,
            left: s.scrollLeft,
            top: s.scrollTop
          });
        typeof a.focus == "function" && a.focus();
        for (var d = 0; d < u.length; d++) {
          var m = u[d];
          m.element.scrollLeft = m.left, m.element.scrollTop = m.top;
        }
      }
    }
    function BT(e) {
      var t;
      return "selectionStart" in e ? t = {
        start: e.selectionStart,
        end: e.selectionEnd
      } : t = cr(e), t || {
        start: 0,
        end: 0
      };
    }
    function IT(e, t) {
      var a = t.start, i = t.end;
      i === void 0 && (i = a), "selectionStart" in e ? (e.selectionStart = a, e.selectionEnd = Math.min(i, e.value.length)) : tu(e, t);
    }
    var $T = Hn && "documentMode" in document && document.documentMode <= 11;
    function YT() {
      ht("onSelect", ["focusout", "contextmenu", "dragend", "focusin", "keydown", "keyup", "mousedown", "mouseup", "selectionchange"]);
    }
    var Bf = null, Sg = null, kp = null, Eg = !1;
    function WT(e) {
      if ("selectionStart" in e && gg(e))
        return {
          start: e.selectionStart,
          end: e.selectionEnd
        };
      var t = e.ownerDocument && e.ownerDocument.defaultView || window, a = t.getSelection();
      return {
        anchorNode: a.anchorNode,
        anchorOffset: a.anchorOffset,
        focusNode: a.focusNode,
        focusOffset: a.focusOffset
      };
    }
    function QT(e) {
      return e.window === e ? e.document : e.nodeType === al ? e : e.ownerDocument;
    }
    function DC(e, t, a) {
      var i = QT(a);
      if (!(Eg || Bf == null || Bf !== Fa(i))) {
        var u = WT(Bf);
        if (!kp || !Re(kp, u)) {
          kp = u;
          var s = am(Sg, "onSelect");
          if (s.length > 0) {
            var d = new Ii("onSelect", "select", null, t, a);
            e.push({
              event: d,
              listeners: s
            }), d.target = Bf;
          }
        }
      }
    }
    function ZT(e, t, a, i, u, s, d) {
      var m = a ? Qf(a) : window;
      switch (t) {
        case "focusin":
          (sc(m) || m.contentEditable === "true") && (Bf = m, Sg = a, kp = null);
          break;
        case "focusout":
          Bf = null, Sg = null, kp = null;
          break;
        case "mousedown":
          Eg = !0;
          break;
        case "contextmenu":
        case "mouseup":
        case "dragend":
          Eg = !1, DC(e, i, u);
          break;
        case "selectionchange":
          if ($T)
            break;
        case "keydown":
        case "keyup":
          DC(e, i, u);
      }
    }
    function tm(e, t) {
      var a = {};
      return a[e.toLowerCase()] = t.toLowerCase(), a["Webkit" + e] = "webkit" + t, a["Moz" + e] = "moz" + t, a;
    }
    var If = {
      animationend: tm("Animation", "AnimationEnd"),
      animationiteration: tm("Animation", "AnimationIteration"),
      animationstart: tm("Animation", "AnimationStart"),
      transitionend: tm("Transition", "TransitionEnd")
    }, Cg = {}, OC = {};
    Hn && (OC = document.createElement("div").style, "AnimationEvent" in window || (delete If.animationend.animation, delete If.animationiteration.animation, delete If.animationstart.animation), "TransitionEvent" in window || delete If.transitionend.transition);
    function nm(e) {
      if (Cg[e])
        return Cg[e];
      if (!If[e])
        return e;
      var t = If[e];
      for (var a in t)
        if (t.hasOwnProperty(a) && a in OC)
          return Cg[e] = t[a];
      return e;
    }
    var NC = nm("animationend"), MC = nm("animationiteration"), LC = nm("animationstart"), AC = nm("transitionend"), zC = /* @__PURE__ */ new Map(), UC = ["abort", "auxClick", "cancel", "canPlay", "canPlayThrough", "click", "close", "contextMenu", "copy", "cut", "drag", "dragEnd", "dragEnter", "dragExit", "dragLeave", "dragOver", "dragStart", "drop", "durationChange", "emptied", "encrypted", "ended", "error", "gotPointerCapture", "input", "invalid", "keyDown", "keyPress", "keyUp", "load", "loadedData", "loadedMetadata", "loadStart", "lostPointerCapture", "mouseDown", "mouseMove", "mouseOut", "mouseOver", "mouseUp", "paste", "pause", "play", "playing", "pointerCancel", "pointerDown", "pointerMove", "pointerOut", "pointerOver", "pointerUp", "progress", "rateChange", "reset", "resize", "seeked", "seeking", "stalled", "submit", "suspend", "timeUpdate", "touchCancel", "touchEnd", "touchStart", "volumeChange", "scroll", "toggle", "touchMove", "waiting", "wheel"];
    function Qo(e, t) {
      zC.set(e, t), ht(t, [e]);
    }
    function GT() {
      for (var e = 0; e < UC.length; e++) {
        var t = UC[e], a = t.toLowerCase(), i = t[0].toUpperCase() + t.slice(1);
        Qo(a, "on" + i);
      }
      Qo(NC, "onAnimationEnd"), Qo(MC, "onAnimationIteration"), Qo(LC, "onAnimationStart"), Qo("dblclick", "onDoubleClick"), Qo("focusin", "onFocus"), Qo("focusout", "onBlur"), Qo(AC, "onTransitionEnd");
    }
    function qT(e, t, a, i, u, s, d) {
      var m = zC.get(t);
      if (m !== void 0) {
        var y = Ii, x = t;
        switch (t) {
          case "keypress":
            if (Xl(i) === 0)
              return;
          case "keydown":
          case "keyup":
            y = Yh;
            break;
          case "focusin":
            x = "focus", y = pl;
            break;
          case "focusout":
            x = "blur", y = pl;
            break;
          case "beforeblur":
          case "afterblur":
            y = pl;
            break;
          case "click":
            if (i.button === 2)
              return;
          case "auxclick":
          case "dblclick":
          case "mousedown":
          case "mousemove":
          case "mouseup":
          case "mouseout":
          case "mouseover":
          case "contextmenu":
            y = gp;
            break;
          case "drag":
          case "dragend":
          case "dragenter":
          case "dragexit":
          case "dragleave":
          case "dragover":
          case "dragstart":
          case "drop":
            y = Zu;
            break;
          case "touchcancel":
          case "touchend":
          case "touchmove":
          case "touchstart":
            y = Zh;
            break;
          case NC:
          case MC:
          case LC:
            y = Vh;
            break;
          case AC:
            y = Za;
            break;
          case "scroll":
            y = pa;
            break;
          case "wheel":
            y = vg;
            break;
          case "copy":
          case "cut":
          case "paste":
            y = Af;
            break;
          case "gotpointercapture":
          case "lostpointercapture":
          case "pointercancel":
          case "pointerdown":
          case "pointermove":
          case "pointerout":
          case "pointerover":
          case "pointerup":
            y = Wh;
            break;
        }
        var R = (s & Ha) !== 0;
        {
          var M = !R && // TODO: ideally, we'd eventually add all events from
          // nonDelegatedEvents list in DOMPluginEventSystem.
          // Then we can remove this special list.
          // This is a breaking change that can wait until React 18.
          t === "scroll", O = eR(a, m, i.type, R, M);
          if (O.length > 0) {
            var H = new y(m, x, null, i, u);
            e.push({
              event: H,
              listeners: O
            });
          }
        }
      }
    }
    GT(), z(), cc(), YT(), hg();
    function XT(e, t, a, i, u, s, d) {
      qT(e, t, a, i, u, s);
      var m = (s & Pd) === 0;
      m && (N(e, t, a, i, u), jn(e, t, a, i, u), ZT(e, t, a, i, u), Kh(e, t, a, i, u));
    }
    var Dp = ["abort", "canplay", "canplaythrough", "durationchange", "emptied", "encrypted", "ended", "error", "loadeddata", "loadedmetadata", "loadstart", "pause", "play", "playing", "progress", "ratechange", "resize", "seeked", "seeking", "stalled", "suspend", "timeupdate", "volumechange", "waiting"], _g = new Set(["cancel", "close", "invalid", "load", "scroll", "toggle"].concat(Dp));
    function jC(e, t, a) {
      var i = e.type || "unknown-event";
      e.currentTarget = a, Li(i, t, void 0, e), e.currentTarget = null;
    }
    function KT(e, t, a) {
      var i;
      if (a)
        for (var u = t.length - 1; u >= 0; u--) {
          var s = t[u], d = s.instance, m = s.currentTarget, y = s.listener;
          if (d !== i && e.isPropagationStopped())
            return;
          jC(e, y, m), i = d;
        }
      else
        for (var x = 0; x < t.length; x++) {
          var R = t[x], M = R.instance, O = R.currentTarget, H = R.listener;
          if (M !== i && e.isPropagationStopped())
            return;
          jC(e, H, O), i = M;
        }
    }
    function FC(e, t) {
      for (var a = (t & Ha) !== 0, i = 0; i < e.length; i++) {
        var u = e[i], s = u.event, d = u.listeners;
        KT(s, d, a);
      }
      Ds();
    }
    function JT(e, t, a, i, u) {
      var s = Bd(a), d = [];
      XT(d, e, i, a, s, t), FC(d, t);
    }
    function kn(e, t) {
      _g.has(e) || E('Did not expect a listenToNonDelegatedEvent() call for "%s". This is a bug in React. Please file an issue.', e);
      var a = !1, i = Dw(t), u = rR(e);
      i.has(u) || (HC(t, e, Pc, a), i.add(u));
    }
    function xg(e, t, a) {
      _g.has(e) && !t && E('Did not expect a listenToNativeEvent() call for "%s" in the bubble phase. This is a bug in React. Please file an issue.', e);
      var i = 0;
      t && (i |= Ha), HC(a, e, i, t);
    }
    var rm = "_reactListening" + Math.random().toString(36).slice(2);
    function Op(e) {
      if (!e[rm]) {
        e[rm] = !0, ct.forEach(function(a) {
          a !== "selectionchange" && (_g.has(a) || xg(a, !1, e), xg(a, !0, e));
        });
        var t = e.nodeType === al ? e : e.ownerDocument;
        t !== null && (t[rm] || (t[rm] = !0, xg("selectionchange", !1, t)));
      }
    }
    function HC(e, t, a, i, u) {
      var s = Er(e, t, a), d = void 0;
      ks && (t === "touchstart" || t === "touchmove" || t === "wheel") && (d = !0), e = e, i ? d !== void 0 ? yp(e, t, s, d) : da(e, t, s) : d !== void 0 ? Io(e, t, s, d) : ac(e, t, s);
    }
    function VC(e, t) {
      return e === t || e.nodeType === Pn && e.parentNode === t;
    }
    function Tg(e, t, a, i, u) {
      var s = i;
      if (!(t & Vd) && !(t & Pc)) {
        var d = u;
        if (i !== null) {
          var m = i;
          e: for (; ; ) {
            if (m === null)
              return;
            var y = m.tag;
            if (y === re || y === be) {
              var x = m.stateNode.containerInfo;
              if (VC(x, d))
                break;
              if (y === be)
                for (var R = m.return; R !== null; ) {
                  var M = R.tag;
                  if (M === re || M === be) {
                    var O = R.stateNode.containerInfo;
                    if (VC(O, d))
                      return;
                  }
                  R = R.return;
                }
              for (; x !== null; ) {
                var H = pc(x);
                if (H === null)
                  return;
                var B = H.tag;
                if (B === de || B === nt) {
                  m = s = H;
                  continue e;
                }
                x = x.parentNode;
              }
            }
            m = m.return;
          }
        }
      }
      fh(function() {
        return JT(e, t, a, s);
      });
    }
    function Np(e, t, a) {
      return {
        instance: e,
        listener: t,
        currentTarget: a
      };
    }
    function eR(e, t, a, i, u, s) {
      for (var d = t !== null ? t + "Capture" : null, m = i ? d : t, y = [], x = e, R = null; x !== null; ) {
        var M = x, O = M.stateNode, H = M.tag;
        if (H === de && O !== null && (R = O, m !== null)) {
          var B = Fl(x, m);
          B != null && y.push(Np(x, B, R));
        }
        if (u)
          break;
        x = x.return;
      }
      return y;
    }
    function am(e, t) {
      for (var a = t + "Capture", i = [], u = e; u !== null; ) {
        var s = u, d = s.stateNode, m = s.tag;
        if (m === de && d !== null) {
          var y = d, x = Fl(u, a);
          x != null && i.unshift(Np(u, x, y));
          var R = Fl(u, t);
          R != null && i.push(Np(u, R, y));
        }
        u = u.return;
      }
      return i;
    }
    function $f(e) {
      if (e === null)
        return null;
      do
        e = e.return;
      while (e && e.tag !== de);
      return e || null;
    }
    function tR(e, t) {
      for (var a = e, i = t, u = 0, s = a; s; s = $f(s))
        u++;
      for (var d = 0, m = i; m; m = $f(m))
        d++;
      for (; u - d > 0; )
        a = $f(a), u--;
      for (; d - u > 0; )
        i = $f(i), d--;
      for (var y = u; y--; ) {
        if (a === i || i !== null && a === i.alternate)
          return a;
        a = $f(a), i = $f(i);
      }
      return null;
    }
    function PC(e, t, a, i, u) {
      for (var s = t._reactName, d = [], m = a; m !== null && m !== i; ) {
        var y = m, x = y.alternate, R = y.stateNode, M = y.tag;
        if (x !== null && x === i)
          break;
        if (M === de && R !== null) {
          var O = R;
          if (u) {
            var H = Fl(m, s);
            H != null && d.unshift(Np(m, H, O));
          } else if (!u) {
            var B = Fl(m, s);
            B != null && d.push(Np(m, B, O));
          }
        }
        m = m.return;
      }
      d.length !== 0 && e.push({
        event: t,
        listeners: d
      });
    }
    function nR(e, t, a, i, u) {
      var s = i && u ? tR(i, u) : null;
      i !== null && PC(e, t, i, s, !1), u !== null && a !== null && PC(e, a, u, s, !0);
    }
    function rR(e, t) {
      return e + "__bubble";
    }
    var Ga = !1, Mp = "dangerouslySetInnerHTML", im = "suppressContentEditableWarning", Zo = "suppressHydrationWarning", BC = "autoFocus", fc = "children", dc = "style", lm = "__html", Rg, um, Lp, IC, om, $C, YC;
    Rg = {
      // There are working polyfills for <dialog>. Let people use it.
      dialog: !0,
      // Electron ships a custom <webview> tag to display external web content in
      // an isolated frame and process.
      // This tag is not present in non Electron environments such as JSDom which
      // is often used for testing purposes.
      // @see https://electronjs.org/docs/api/webview-tag
      webview: !0
    }, um = function(e, t) {
      jd(e, t), Hc(e, t), oh(e, t, {
        registrationNameDependencies: ot,
        possibleRegistrationNames: ft
      });
    }, $C = Hn && !document.documentMode, Lp = function(e, t, a) {
      if (!Ga) {
        var i = sm(a), u = sm(t);
        u !== i && (Ga = !0, E("Prop `%s` did not match. Server: %s Client: %s", e, JSON.stringify(u), JSON.stringify(i)));
      }
    }, IC = function(e) {
      if (!Ga) {
        Ga = !0;
        var t = [];
        e.forEach(function(a) {
          t.push(a);
        }), E("Extra attributes from the server: %s", t);
      }
    }, om = function(e, t) {
      t === !1 ? E("Expected `%s` listener to be a function, instead got `false`.\n\nIf you used to conditionally omit it with %s={condition && value}, pass %s={condition ? value : undefined} instead.", e, e, e) : E("Expected `%s` listener to be a function, instead got a value of `%s` type.", e, typeof t);
    }, YC = function(e, t) {
      var a = e.namespaceURI === nl ? e.ownerDocument.createElement(e.tagName) : e.ownerDocument.createElementNS(e.namespaceURI, e.tagName);
      return a.innerHTML = t, a.innerHTML;
    };
    var aR = /\r\n?/g, iR = /\u0000|\uFFFD/g;
    function sm(e) {
      ir(e);
      var t = typeof e == "string" ? e : "" + e;
      return t.replace(aR, `
`).replace(iR, "");
    }
    function cm(e, t, a, i) {
      var u = sm(t), s = sm(e);
      if (s !== u && (i && (Ga || (Ga = !0, E('Text content did not match. Server: "%s" Client: "%s"', s, u))), a && De))
        throw new Error("Text content does not match server-rendered HTML.");
    }
    function WC(e) {
      return e.nodeType === al ? e : e.ownerDocument;
    }
    function lR() {
    }
    function fm(e) {
      e.onclick = lR;
    }
    function uR(e, t, a, i, u) {
      for (var s in i)
        if (i.hasOwnProperty(s)) {
          var d = i[s];
          if (s === dc)
            d && Object.freeze(d), nh(t, d);
          else if (s === Mp) {
            var m = d ? d[lm] : void 0;
            m != null && $v(t, m);
          } else if (s === fc)
            if (typeof d == "string") {
              var y = e !== "textarea" || d !== "";
              y && Ro(t, d);
            } else typeof d == "number" && Ro(t, "" + d);
          else s === im || s === Zo || s === BC || (ot.hasOwnProperty(s) ? d != null && (typeof d != "function" && om(s, d), s === "onScroll" && kn("scroll", t)) : d != null && jr(t, s, d, u));
        }
    }
    function oR(e, t, a, i) {
      for (var u = 0; u < t.length; u += 2) {
        var s = t[u], d = t[u + 1];
        s === dc ? nh(e, d) : s === Mp ? $v(e, d) : s === fc ? Ro(e, d) : jr(e, s, d, i);
      }
    }
    function sR(e, t, a, i) {
      var u, s = WC(a), d, m = i;
      if (m === nl && (m = Od(e)), m === nl) {
        if (u = Ul(e, t), !u && e !== e.toLowerCase() && E("<%s /> is using incorrect casing. Use PascalCase for React components, or lowercase for HTML elements.", e), e === "script") {
          var y = s.createElement("div");
          y.innerHTML = "<script><\/script>";
          var x = y.firstChild;
          d = y.removeChild(x);
        } else if (typeof t.is == "string")
          d = s.createElement(e, {
            is: t.is
          });
        else if (d = s.createElement(e), e === "select") {
          var R = d;
          t.multiple ? R.multiple = !0 : t.size && (R.size = t.size);
        }
      } else
        d = s.createElementNS(m, e);
      return m === nl && !u && Object.prototype.toString.call(d) === "[object HTMLUnknownElement]" && !Ur.call(Rg, e) && (Rg[e] = !0, E("The tag <%s> is unrecognized in this browser. If you meant to render a React component, start its name with an uppercase letter.", e)), d;
    }
    function cR(e, t) {
      return WC(t).createTextNode(e);
    }
    function fR(e, t, a, i) {
      var u = Ul(t, a);
      um(t, a);
      var s;
      switch (t) {
        case "dialog":
          kn("cancel", e), kn("close", e), s = a;
          break;
        case "iframe":
        case "object":
        case "embed":
          kn("load", e), s = a;
          break;
        case "video":
        case "audio":
          for (var d = 0; d < Dp.length; d++)
            kn(Dp[d], e);
          s = a;
          break;
        case "source":
          kn("error", e), s = a;
          break;
        case "img":
        case "image":
        case "link":
          kn("error", e), kn("load", e), s = a;
          break;
        case "details":
          kn("toggle", e), s = a;
          break;
        case "input":
          di(e, a), s = To(e, a), kn("invalid", e);
          break;
        case "option":
          At(e, a), s = a;
          break;
        case "select":
          Tu(e, a), s = Ss(e, a), kn("invalid", e);
          break;
        case "textarea":
          bd(e, a), s = wd(e, a), kn("invalid", e);
          break;
        default:
          s = a;
      }
      switch (jc(t, s), uR(t, e, i, s, u), t) {
        case "input":
          fi(e), V(e, a, !1);
          break;
        case "textarea":
          fi(e), Bv(e);
          break;
        case "option":
          dn(e, a);
          break;
        case "select":
          Td(e, a);
          break;
        default:
          typeof s.onClick == "function" && fm(e);
          break;
      }
    }
    function dR(e, t, a, i, u) {
      um(t, i);
      var s = null, d, m;
      switch (t) {
        case "input":
          d = To(e, a), m = To(e, i), s = [];
          break;
        case "select":
          d = Ss(e, a), m = Ss(e, i), s = [];
          break;
        case "textarea":
          d = wd(e, a), m = wd(e, i), s = [];
          break;
        default:
          d = a, m = i, typeof d.onClick != "function" && typeof m.onClick == "function" && fm(e);
          break;
      }
      jc(t, m);
      var y, x, R = null;
      for (y in d)
        if (!(m.hasOwnProperty(y) || !d.hasOwnProperty(y) || d[y] == null))
          if (y === dc) {
            var M = d[y];
            for (x in M)
              M.hasOwnProperty(x) && (R || (R = {}), R[x] = "");
          } else y === Mp || y === fc || y === im || y === Zo || y === BC || (ot.hasOwnProperty(y) ? s || (s = []) : (s = s || []).push(y, null));
      for (y in m) {
        var O = m[y], H = d != null ? d[y] : void 0;
        if (!(!m.hasOwnProperty(y) || O === H || O == null && H == null))
          if (y === dc)
            if (O && Object.freeze(O), H) {
              for (x in H)
                H.hasOwnProperty(x) && (!O || !O.hasOwnProperty(x)) && (R || (R = {}), R[x] = "");
              for (x in O)
                O.hasOwnProperty(x) && H[x] !== O[x] && (R || (R = {}), R[x] = O[x]);
            } else
              R || (s || (s = []), s.push(y, R)), R = O;
          else if (y === Mp) {
            var B = O ? O[lm] : void 0, W = H ? H[lm] : void 0;
            B != null && W !== B && (s = s || []).push(y, B);
          } else y === fc ? (typeof O == "string" || typeof O == "number") && (s = s || []).push(y, "" + O) : y === im || y === Zo || (ot.hasOwnProperty(y) ? (O != null && (typeof O != "function" && om(y, O), y === "onScroll" && kn("scroll", e)), !s && H !== O && (s = [])) : (s = s || []).push(y, O));
      }
      return R && (Ky(R, m[dc]), (s = s || []).push(dc, R)), s;
    }
    function pR(e, t, a, i, u) {
      a === "input" && u.type === "radio" && u.name != null && g(e, u);
      var s = Ul(a, i), d = Ul(a, u);
      switch (oR(e, t, s, d), a) {
        case "input":
          b(e, u);
          break;
        case "textarea":
          Pv(e, u);
          break;
        case "select":
          Ac(e, u);
          break;
      }
    }
    function vR(e) {
      {
        var t = e.toLowerCase();
        return Ts.hasOwnProperty(t) && Ts[t] || null;
      }
    }
    function hR(e, t, a, i, u, s, d) {
      var m, y;
      switch (m = Ul(t, a), um(t, a), t) {
        case "dialog":
          kn("cancel", e), kn("close", e);
          break;
        case "iframe":
        case "object":
        case "embed":
          kn("load", e);
          break;
        case "video":
        case "audio":
          for (var x = 0; x < Dp.length; x++)
            kn(Dp[x], e);
          break;
        case "source":
          kn("error", e);
          break;
        case "img":
        case "image":
        case "link":
          kn("error", e), kn("load", e);
          break;
        case "details":
          kn("toggle", e);
          break;
        case "input":
          di(e, a), kn("invalid", e);
          break;
        case "option":
          At(e, a);
          break;
        case "select":
          Tu(e, a), kn("invalid", e);
          break;
        case "textarea":
          bd(e, a), kn("invalid", e);
          break;
      }
      jc(t, a);
      {
        y = /* @__PURE__ */ new Set();
        for (var R = e.attributes, M = 0; M < R.length; M++) {
          var O = R[M].name.toLowerCase();
          switch (O) {
            case "value":
              break;
            case "checked":
              break;
            case "selected":
              break;
            default:
              y.add(R[M].name);
          }
        }
      }
      var H = null;
      for (var B in a)
        if (a.hasOwnProperty(B)) {
          var W = a[B];
          if (B === fc)
            typeof W == "string" ? e.textContent !== W && (a[Zo] !== !0 && cm(e.textContent, W, s, d), H = [fc, W]) : typeof W == "number" && e.textContent !== "" + W && (a[Zo] !== !0 && cm(e.textContent, W, s, d), H = [fc, "" + W]);
          else if (ot.hasOwnProperty(B))
            W != null && (typeof W != "function" && om(B, W), B === "onScroll" && kn("scroll", e));
          else if (d && // Convince Flow we've calculated it (it's DEV-only in this method.)
          typeof m == "boolean") {
            var he = void 0, Pe = cn(B);
            if (a[Zo] !== !0) {
              if (!(B === im || B === Zo || // Controlled attributes are not validated
              // TODO: Only ignore them on controlled tags.
              B === "value" || B === "checked" || B === "selected")) {
                if (B === Mp) {
                  var Me = e.innerHTML, Nt = W ? W[lm] : void 0;
                  if (Nt != null) {
                    var Rt = YC(e, Nt);
                    Rt !== Me && Lp(B, Me, Rt);
                  }
                } else if (B === dc) {
                  if (y.delete(B), $C) {
                    var U = qy(W);
                    he = e.getAttribute("style"), U !== he && Lp(B, he, U);
                  }
                } else if (m && !L)
                  y.delete(B.toLowerCase()), he = yu(e, B, W), W !== he && Lp(B, he, W);
                else if (!xn(B, Pe, m) && !lr(B, W, Pe, m)) {
                  var Q = !1;
                  if (Pe !== null)
                    y.delete(Pe.attributeName), he = bl(e, B, W, Pe);
                  else {
                    var j = i;
                    if (j === nl && (j = Od(t)), j === nl)
                      y.delete(B.toLowerCase());
                    else {
                      var ne = vR(B);
                      ne !== null && ne !== B && (Q = !0, y.delete(ne)), y.delete(B);
                    }
                    he = yu(e, B, W);
                  }
                  var Ee = L;
                  !Ee && W !== he && !Q && Lp(B, he, W);
                }
              }
            }
          }
        }
      switch (d && // $FlowFixMe - Should be inferred as not undefined.
      y.size > 0 && a[Zo] !== !0 && IC(y), t) {
        case "input":
          fi(e), V(e, a, !0);
          break;
        case "textarea":
          fi(e), Bv(e);
          break;
        case "select":
        case "option":
          break;
        default:
          typeof a.onClick == "function" && fm(e);
          break;
      }
      return H;
    }
    function mR(e, t, a) {
      var i = e.nodeValue !== t;
      return i;
    }
    function wg(e, t) {
      {
        if (Ga)
          return;
        Ga = !0, E("Did not expect server HTML to contain a <%s> in <%s>.", t.nodeName.toLowerCase(), e.nodeName.toLowerCase());
      }
    }
    function bg(e, t) {
      {
        if (Ga)
          return;
        Ga = !0, E('Did not expect server HTML to contain the text node "%s" in <%s>.', t.nodeValue, e.nodeName.toLowerCase());
      }
    }
    function kg(e, t, a) {
      {
        if (Ga)
          return;
        Ga = !0, E("Expected server HTML to contain a matching <%s> in <%s>.", t, e.nodeName.toLowerCase());
      }
    }
    function Dg(e, t) {
      {
        if (t === "" || Ga)
          return;
        Ga = !0, E('Expected server HTML to contain a matching text node for "%s" in <%s>.', t, e.nodeName.toLowerCase());
      }
    }
    function yR(e, t, a) {
      switch (t) {
        case "input":
          Y(e, a);
          return;
        case "textarea":
          Wy(e, a);
          return;
        case "select":
          Rd(e, a);
          return;
      }
    }
    var Ap = function() {
    }, zp = function() {
    };
    {
      var gR = ["address", "applet", "area", "article", "aside", "base", "basefont", "bgsound", "blockquote", "body", "br", "button", "caption", "center", "col", "colgroup", "dd", "details", "dir", "div", "dl", "dt", "embed", "fieldset", "figcaption", "figure", "footer", "form", "frame", "frameset", "h1", "h2", "h3", "h4", "h5", "h6", "head", "header", "hgroup", "hr", "html", "iframe", "img", "input", "isindex", "li", "link", "listing", "main", "marquee", "menu", "menuitem", "meta", "nav", "noembed", "noframes", "noscript", "object", "ol", "p", "param", "plaintext", "pre", "script", "section", "select", "source", "style", "summary", "table", "tbody", "td", "template", "textarea", "tfoot", "th", "thead", "title", "tr", "track", "ul", "wbr", "xmp"], QC = [
        "applet",
        "caption",
        "html",
        "table",
        "td",
        "th",
        "marquee",
        "object",
        "template",
        // https://html.spec.whatwg.org/multipage/syntax.html#html-integration-point
        // TODO: Distinguish by namespace here -- for <title>, including it here
        // errs on the side of fewer warnings
        "foreignObject",
        "desc",
        "title"
      ], SR = QC.concat(["button"]), ER = ["dd", "dt", "li", "option", "optgroup", "p", "rp", "rt"], ZC = {
        current: null,
        formTag: null,
        aTagInScope: null,
        buttonTagInScope: null,
        nobrTagInScope: null,
        pTagInButtonScope: null,
        listItemTagAutoclosing: null,
        dlItemTagAutoclosing: null
      };
      zp = function(e, t) {
        var a = st({}, e || ZC), i = {
          tag: t
        };
        return QC.indexOf(t) !== -1 && (a.aTagInScope = null, a.buttonTagInScope = null, a.nobrTagInScope = null), SR.indexOf(t) !== -1 && (a.pTagInButtonScope = null), gR.indexOf(t) !== -1 && t !== "address" && t !== "div" && t !== "p" && (a.listItemTagAutoclosing = null, a.dlItemTagAutoclosing = null), a.current = i, t === "form" && (a.formTag = i), t === "a" && (a.aTagInScope = i), t === "button" && (a.buttonTagInScope = i), t === "nobr" && (a.nobrTagInScope = i), t === "p" && (a.pTagInButtonScope = i), t === "li" && (a.listItemTagAutoclosing = i), (t === "dd" || t === "dt") && (a.dlItemTagAutoclosing = i), a;
      };
      var CR = function(e, t) {
        switch (t) {
          case "select":
            return e === "option" || e === "optgroup" || e === "#text";
          case "optgroup":
            return e === "option" || e === "#text";
          case "option":
            return e === "#text";
          case "tr":
            return e === "th" || e === "td" || e === "style" || e === "script" || e === "template";
          case "tbody":
          case "thead":
          case "tfoot":
            return e === "tr" || e === "style" || e === "script" || e === "template";
          case "colgroup":
            return e === "col" || e === "template";
          case "table":
            return e === "caption" || e === "colgroup" || e === "tbody" || e === "tfoot" || e === "thead" || e === "style" || e === "script" || e === "template";
          case "head":
            return e === "base" || e === "basefont" || e === "bgsound" || e === "link" || e === "meta" || e === "title" || e === "noscript" || e === "noframes" || e === "style" || e === "script" || e === "template";
          case "html":
            return e === "head" || e === "body" || e === "frameset";
          case "frameset":
            return e === "frame";
          case "#document":
            return e === "html";
        }
        switch (e) {
          case "h1":
          case "h2":
          case "h3":
          case "h4":
          case "h5":
          case "h6":
            return t !== "h1" && t !== "h2" && t !== "h3" && t !== "h4" && t !== "h5" && t !== "h6";
          case "rp":
          case "rt":
            return ER.indexOf(t) === -1;
          case "body":
          case "caption":
          case "col":
          case "colgroup":
          case "frameset":
          case "frame":
          case "head":
          case "html":
          case "tbody":
          case "td":
          case "tfoot":
          case "th":
          case "thead":
          case "tr":
            return t == null;
        }
        return !0;
      }, _R = function(e, t) {
        switch (e) {
          case "address":
          case "article":
          case "aside":
          case "blockquote":
          case "center":
          case "details":
          case "dialog":
          case "dir":
          case "div":
          case "dl":
          case "fieldset":
          case "figcaption":
          case "figure":
          case "footer":
          case "header":
          case "hgroup":
          case "main":
          case "menu":
          case "nav":
          case "ol":
          case "p":
          case "section":
          case "summary":
          case "ul":
          case "pre":
          case "listing":
          case "table":
          case "hr":
          case "xmp":
          case "h1":
          case "h2":
          case "h3":
          case "h4":
          case "h5":
          case "h6":
            return t.pTagInButtonScope;
          case "form":
            return t.formTag || t.pTagInButtonScope;
          case "li":
            return t.listItemTagAutoclosing;
          case "dd":
          case "dt":
            return t.dlItemTagAutoclosing;
          case "button":
            return t.buttonTagInScope;
          case "a":
            return t.aTagInScope;
          case "nobr":
            return t.nobrTagInScope;
        }
        return null;
      }, GC = {};
      Ap = function(e, t, a) {
        a = a || ZC;
        var i = a.current, u = i && i.tag;
        t != null && (e != null && E("validateDOMNesting: when childText is passed, childTag should be null"), e = "#text");
        var s = CR(e, u) ? null : i, d = s ? null : _R(e, a), m = s || d;
        if (m) {
          var y = m.tag, x = !!s + "|" + e + "|" + y;
          if (!GC[x]) {
            GC[x] = !0;
            var R = e, M = "";
            if (e === "#text" ? /\S/.test(t) ? R = "Text nodes" : (R = "Whitespace text nodes", M = " Make sure you don't have any extra whitespace between tags on each line of your source code.") : R = "<" + e + ">", s) {
              var O = "";
              y === "table" && e === "tr" && (O += " Add a <tbody>, <thead> or <tfoot> to your code to match the DOM tree generated by the browser."), E("validateDOMNesting(...): %s cannot appear as a child of <%s>.%s%s", R, y, M, O);
            } else
              E("validateDOMNesting(...): %s cannot appear as a descendant of <%s>.", R, y);
          }
        }
      };
    }
    var dm = "suppressHydrationWarning", pm = "$", vm = "/$", Up = "$?", jp = "$!", xR = "style", Og = null, Ng = null;
    function TR(e) {
      var t, a, i = e.nodeType;
      switch (i) {
        case al:
        case Md: {
          t = i === al ? "#document" : "#fragment";
          var u = e.documentElement;
          a = u ? u.namespaceURI : Nd(null, "");
          break;
        }
        default: {
          var s = i === Pn ? e.parentNode : e, d = s.namespaceURI || null;
          t = s.tagName, a = Nd(d, t);
          break;
        }
      }
      {
        var m = t.toLowerCase(), y = zp(null, m);
        return {
          namespace: a,
          ancestorInfo: y
        };
      }
    }
    function RR(e, t, a) {
      {
        var i = e, u = Nd(i.namespace, t), s = zp(i.ancestorInfo, t);
        return {
          namespace: u,
          ancestorInfo: s
        };
      }
    }
    function bO(e) {
      return e;
    }
    function wR(e) {
      Og = Qn(), Ng = VT();
      var t = null;
      return nr(!1), t;
    }
    function bR(e) {
      PT(Ng), nr(Og), Og = null, Ng = null;
    }
    function kR(e, t, a, i, u) {
      var s;
      {
        var d = i;
        if (Ap(e, null, d.ancestorInfo), typeof t.children == "string" || typeof t.children == "number") {
          var m = "" + t.children, y = zp(d.ancestorInfo, e);
          Ap(null, m, y);
        }
        s = d.namespace;
      }
      var x = sR(e, t, a, s);
      return Vp(u, x), Hg(x, t), x;
    }
    function DR(e, t) {
      e.appendChild(t);
    }
    function OR(e, t, a, i, u) {
      switch (fR(e, t, a, i), t) {
        case "button":
        case "input":
        case "select":
        case "textarea":
          return !!a.autoFocus;
        case "img":
          return !0;
        default:
          return !1;
      }
    }
    function NR(e, t, a, i, u, s) {
      {
        var d = s;
        if (typeof i.children != typeof a.children && (typeof i.children == "string" || typeof i.children == "number")) {
          var m = "" + i.children, y = zp(d.ancestorInfo, t);
          Ap(null, m, y);
        }
      }
      return dR(e, t, a, i);
    }
    function Mg(e, t) {
      return e === "textarea" || e === "noscript" || typeof t.children == "string" || typeof t.children == "number" || typeof t.dangerouslySetInnerHTML == "object" && t.dangerouslySetInnerHTML !== null && t.dangerouslySetInnerHTML.__html != null;
    }
    function MR(e, t, a, i) {
      {
        var u = a;
        Ap(null, e, u.ancestorInfo);
      }
      var s = cR(e, t);
      return Vp(i, s), s;
    }
    function LR() {
      var e = window.event;
      return e === void 0 ? Ya : Nf(e.type);
    }
    var Lg = typeof setTimeout == "function" ? setTimeout : void 0, AR = typeof clearTimeout == "function" ? clearTimeout : void 0, Ag = -1, qC = typeof Promise == "function" ? Promise : void 0, zR = typeof queueMicrotask == "function" ? queueMicrotask : typeof qC < "u" ? function(e) {
      return qC.resolve(null).then(e).catch(UR);
    } : Lg;
    function UR(e) {
      setTimeout(function() {
        throw e;
      });
    }
    function jR(e, t, a, i) {
      switch (t) {
        case "button":
        case "input":
        case "select":
        case "textarea":
          a.autoFocus && e.focus();
          return;
        case "img": {
          a.src && (e.src = a.src);
          return;
        }
      }
    }
    function FR(e, t, a, i, u, s) {
      pR(e, t, a, i, u), Hg(e, u);
    }
    function XC(e) {
      Ro(e, "");
    }
    function HR(e, t, a) {
      e.nodeValue = a;
    }
    function VR(e, t) {
      e.appendChild(t);
    }
    function PR(e, t) {
      var a;
      e.nodeType === Pn ? (a = e.parentNode, a.insertBefore(t, e)) : (a = e, a.appendChild(t));
      var i = e._reactRootContainer;
      i == null && a.onclick === null && fm(a);
    }
    function BR(e, t, a) {
      e.insertBefore(t, a);
    }
    function IR(e, t, a) {
      e.nodeType === Pn ? e.parentNode.insertBefore(t, a) : e.insertBefore(t, a);
    }
    function $R(e, t) {
      e.removeChild(t);
    }
    function YR(e, t) {
      e.nodeType === Pn ? e.parentNode.removeChild(t) : e.removeChild(t);
    }
    function zg(e, t) {
      var a = t, i = 0;
      do {
        var u = a.nextSibling;
        if (e.removeChild(a), u && u.nodeType === Pn) {
          var s = u.data;
          if (s === vm)
            if (i === 0) {
              e.removeChild(u), Yu(t);
              return;
            } else
              i--;
          else (s === pm || s === Up || s === jp) && i++;
        }
        a = u;
      } while (a);
      Yu(t);
    }
    function WR(e, t) {
      e.nodeType === Pn ? zg(e.parentNode, t) : e.nodeType === ra && zg(e, t), Yu(e);
    }
    function QR(e) {
      e = e;
      var t = e.style;
      typeof t.setProperty == "function" ? t.setProperty("display", "none", "important") : t.display = "none";
    }
    function ZR(e) {
      e.nodeValue = "";
    }
    function GR(e, t) {
      e = e;
      var a = t[xR], i = a != null && a.hasOwnProperty("display") ? a.display : null;
      e.style.display = Uc("display", i);
    }
    function qR(e, t) {
      e.nodeValue = t;
    }
    function XR(e) {
      e.nodeType === ra ? e.textContent = "" : e.nodeType === al && e.documentElement && e.removeChild(e.documentElement);
    }
    function KR(e, t, a) {
      return e.nodeType !== ra || t.toLowerCase() !== e.nodeName.toLowerCase() ? null : e;
    }
    function JR(e, t) {
      return t === "" || e.nodeType !== rl ? null : e;
    }
    function ew(e) {
      return e.nodeType !== Pn ? null : e;
    }
    function KC(e) {
      return e.data === Up;
    }
    function Ug(e) {
      return e.data === jp;
    }
    function tw(e) {
      var t = e.nextSibling && e.nextSibling.dataset, a, i, u;
      return t && (a = t.dgst, i = t.msg, u = t.stck), {
        message: i,
        digest: a,
        stack: u
      };
    }
    function nw(e, t) {
      e._reactRetry = t;
    }
    function hm(e) {
      for (; e != null; e = e.nextSibling) {
        var t = e.nodeType;
        if (t === ra || t === rl)
          break;
        if (t === Pn) {
          var a = e.data;
          if (a === pm || a === jp || a === Up)
            break;
          if (a === vm)
            return null;
        }
      }
      return e;
    }
    function Fp(e) {
      return hm(e.nextSibling);
    }
    function rw(e) {
      return hm(e.firstChild);
    }
    function aw(e) {
      return hm(e.firstChild);
    }
    function iw(e) {
      return hm(e.nextSibling);
    }
    function lw(e, t, a, i, u, s, d) {
      Vp(s, e), Hg(e, a);
      var m;
      {
        var y = u;
        m = y.namespace;
      }
      var x = (s.mode & yt) !== je;
      return hR(e, t, a, m, i, x, d);
    }
    function uw(e, t, a, i) {
      return Vp(a, e), a.mode & yt, mR(e, t);
    }
    function ow(e, t) {
      Vp(t, e);
    }
    function sw(e) {
      for (var t = e.nextSibling, a = 0; t; ) {
        if (t.nodeType === Pn) {
          var i = t.data;
          if (i === vm) {
            if (a === 0)
              return Fp(t);
            a--;
          } else (i === pm || i === jp || i === Up) && a++;
        }
        t = t.nextSibling;
      }
      return null;
    }
    function JC(e) {
      for (var t = e.previousSibling, a = 0; t; ) {
        if (t.nodeType === Pn) {
          var i = t.data;
          if (i === pm || i === jp || i === Up) {
            if (a === 0)
              return t;
            a--;
          } else i === vm && a++;
        }
        t = t.previousSibling;
      }
      return null;
    }
    function cw(e) {
      Yu(e);
    }
    function fw(e) {
      Yu(e);
    }
    function dw(e) {
      return e !== "head" && e !== "body";
    }
    function pw(e, t, a, i) {
      var u = !0;
      cm(t.nodeValue, a, i, u);
    }
    function vw(e, t, a, i, u, s) {
      if (t[dm] !== !0) {
        var d = !0;
        cm(i.nodeValue, u, s, d);
      }
    }
    function hw(e, t) {
      t.nodeType === ra ? wg(e, t) : t.nodeType === Pn || bg(e, t);
    }
    function mw(e, t) {
      {
        var a = e.parentNode;
        a !== null && (t.nodeType === ra ? wg(a, t) : t.nodeType === Pn || bg(a, t));
      }
    }
    function yw(e, t, a, i, u) {
      (u || t[dm] !== !0) && (i.nodeType === ra ? wg(a, i) : i.nodeType === Pn || bg(a, i));
    }
    function gw(e, t, a) {
      kg(e, t);
    }
    function Sw(e, t) {
      Dg(e, t);
    }
    function Ew(e, t, a) {
      {
        var i = e.parentNode;
        i !== null && kg(i, t);
      }
    }
    function Cw(e, t) {
      {
        var a = e.parentNode;
        a !== null && Dg(a, t);
      }
    }
    function _w(e, t, a, i, u, s) {
      (s || t[dm] !== !0) && kg(a, i);
    }
    function xw(e, t, a, i, u) {
      (u || t[dm] !== !0) && Dg(a, i);
    }
    function Tw(e) {
      E("An error occurred during hydration. The server HTML was replaced with client content in <%s>.", e.nodeName.toLowerCase());
    }
    function Rw(e) {
      Op(e);
    }
    var Yf = Math.random().toString(36).slice(2), Wf = "__reactFiber$" + Yf, jg = "__reactProps$" + Yf, Hp = "__reactContainer$" + Yf, Fg = "__reactEvents$" + Yf, ww = "__reactListeners$" + Yf, bw = "__reactHandles$" + Yf;
    function kw(e) {
      delete e[Wf], delete e[jg], delete e[Fg], delete e[ww], delete e[bw];
    }
    function Vp(e, t) {
      t[Wf] = e;
    }
    function mm(e, t) {
      t[Hp] = e;
    }
    function e_(e) {
      e[Hp] = null;
    }
    function Pp(e) {
      return !!e[Hp];
    }
    function pc(e) {
      var t = e[Wf];
      if (t)
        return t;
      for (var a = e.parentNode; a; ) {
        if (t = a[Hp] || a[Wf], t) {
          var i = t.alternate;
          if (t.child !== null || i !== null && i.child !== null)
            for (var u = JC(e); u !== null; ) {
              var s = u[Wf];
              if (s)
                return s;
              u = JC(u);
            }
          return t;
        }
        e = a, a = e.parentNode;
      }
      return null;
    }
    function Go(e) {
      var t = e[Wf] || e[Hp];
      return t && (t.tag === de || t.tag === nt || t.tag === ze || t.tag === re) ? t : null;
    }
    function Qf(e) {
      if (e.tag === de || e.tag === nt)
        return e.stateNode;
      throw new Error("getNodeFromInstance: Invalid argument.");
    }
    function ym(e) {
      return e[jg] || null;
    }
    function Hg(e, t) {
      e[jg] = t;
    }
    function Dw(e) {
      var t = e[Fg];
      return t === void 0 && (t = e[Fg] = /* @__PURE__ */ new Set()), t;
    }
    var t_ = {}, n_ = p.ReactDebugCurrentFrame;
    function gm(e) {
      if (e) {
        var t = e._owner, a = Ji(e.type, e._source, t ? t.type : null);
        n_.setExtraStackFrame(a);
      } else
        n_.setExtraStackFrame(null);
    }
    function hl(e, t, a, i, u) {
      {
        var s = Function.call.bind(Ur);
        for (var d in e)
          if (s(e, d)) {
            var m = void 0;
            try {
              if (typeof e[d] != "function") {
                var y = Error((i || "React class") + ": " + a + " type `" + d + "` is invalid; it must be a function, usually from the `prop-types` package, but received `" + typeof e[d] + "`.This often happens because of typos such as `PropTypes.function` instead of `PropTypes.func`.");
                throw y.name = "Invariant Violation", y;
              }
              m = e[d](t, d, i, a, null, "SECRET_DO_NOT_PASS_THIS_OR_YOU_WILL_BE_FIRED");
            } catch (x) {
              m = x;
            }
            m && !(m instanceof Error) && (gm(u), E("%s: type specification of %s `%s` is invalid; the type checker function must return `null` or an `Error` but returned a %s. You may have forgotten to pass an argument to the type checker creator (arrayOf, instanceOf, objectOf, oneOf, oneOfType, and shape all require an argument).", i || "React class", a, d, typeof m), gm(null)), m instanceof Error && !(m.message in t_) && (t_[m.message] = !0, gm(u), E("Failed %s type: %s", a, m.message), gm(null));
          }
      }
    }
    var Vg = [], Sm;
    Sm = [];
    var Xu = -1;
    function qo(e) {
      return {
        current: e
      };
    }
    function va(e, t) {
      if (Xu < 0) {
        E("Unexpected pop.");
        return;
      }
      t !== Sm[Xu] && E("Unexpected Fiber popped."), e.current = Vg[Xu], Vg[Xu] = null, Sm[Xu] = null, Xu--;
    }
    function ha(e, t, a) {
      Xu++, Vg[Xu] = e.current, Sm[Xu] = a, e.current = t;
    }
    var Pg;
    Pg = {};
    var Si = {};
    Object.freeze(Si);
    var Ku = qo(Si), nu = qo(!1), Bg = Si;
    function Zf(e, t, a) {
      return a && ru(t) ? Bg : Ku.current;
    }
    function r_(e, t, a) {
      {
        var i = e.stateNode;
        i.__reactInternalMemoizedUnmaskedChildContext = t, i.__reactInternalMemoizedMaskedChildContext = a;
      }
    }
    function Gf(e, t) {
      {
        var a = e.type, i = a.contextTypes;
        if (!i)
          return Si;
        var u = e.stateNode;
        if (u && u.__reactInternalMemoizedUnmaskedChildContext === t)
          return u.__reactInternalMemoizedMaskedChildContext;
        var s = {};
        for (var d in i)
          s[d] = t[d];
        {
          var m = Xe(e) || "Unknown";
          hl(i, s, "context", m);
        }
        return u && r_(e, t, s), s;
      }
    }
    function Em() {
      return nu.current;
    }
    function ru(e) {
      {
        var t = e.childContextTypes;
        return t != null;
      }
    }
    function Cm(e) {
      va(nu, e), va(Ku, e);
    }
    function Ig(e) {
      va(nu, e), va(Ku, e);
    }
    function a_(e, t, a) {
      {
        if (Ku.current !== Si)
          throw new Error("Unexpected context found on stack. This error is likely caused by a bug in React. Please file an issue.");
        ha(Ku, t, e), ha(nu, a, e);
      }
    }
    function i_(e, t, a) {
      {
        var i = e.stateNode, u = t.childContextTypes;
        if (typeof i.getChildContext != "function") {
          {
            var s = Xe(e) || "Unknown";
            Pg[s] || (Pg[s] = !0, E("%s.childContextTypes is specified but there is no getChildContext() method on the instance. You can either define getChildContext() on %s or remove childContextTypes from it.", s, s));
          }
          return a;
        }
        var d = i.getChildContext();
        for (var m in d)
          if (!(m in u))
            throw new Error((Xe(e) || "Unknown") + '.getChildContext(): key "' + m + '" is not defined in childContextTypes.');
        {
          var y = Xe(e) || "Unknown";
          hl(u, d, "child context", y);
        }
        return st({}, a, d);
      }
    }
    function _m(e) {
      {
        var t = e.stateNode, a = t && t.__reactInternalMemoizedMergedChildContext || Si;
        return Bg = Ku.current, ha(Ku, a, e), ha(nu, nu.current, e), !0;
      }
    }
    function l_(e, t, a) {
      {
        var i = e.stateNode;
        if (!i)
          throw new Error("Expected to have an instance by this point. This error is likely caused by a bug in React. Please file an issue.");
        if (a) {
          var u = i_(e, t, Bg);
          i.__reactInternalMemoizedMergedChildContext = u, va(nu, e), va(Ku, e), ha(Ku, u, e), ha(nu, a, e);
        } else
          va(nu, e), ha(nu, a, e);
      }
    }
    function Ow(e) {
      {
        if (!Nu(e) || e.tag !== $)
          throw new Error("Expected subtree parent to be a mounted class component. This error is likely caused by a bug in React. Please file an issue.");
        var t = e;
        do {
          switch (t.tag) {
            case re:
              return t.stateNode.context;
            case $: {
              var a = t.type;
              if (ru(a))
                return t.stateNode.__reactInternalMemoizedMergedChildContext;
              break;
            }
          }
          t = t.return;
        } while (t !== null);
        throw new Error("Found unexpected detached subtree parent. This error is likely caused by a bug in React. Please file an issue.");
      }
    }
    var Xo = 0, xm = 1, Ju = null, $g = !1, Yg = !1;
    function u_(e) {
      Ju === null ? Ju = [e] : Ju.push(e);
    }
    function Nw(e) {
      $g = !0, u_(e);
    }
    function o_() {
      $g && Ko();
    }
    function Ko() {
      if (!Yg && Ju !== null) {
        Yg = !0;
        var e = 0, t = Qa();
        try {
          var a = !0, i = Ju;
          for (Wn(Br); e < i.length; e++) {
            var u = i[e];
            do
              u = u(a);
            while (u !== null);
          }
          Ju = null, $g = !1;
        } catch (s) {
          throw Ju !== null && (Ju = Ju.slice(e + 1)), $d(Ms, Ko), s;
        } finally {
          Wn(t), Yg = !1;
        }
      }
      return null;
    }
    var qf = [], Xf = 0, Tm = null, Rm = 0, $i = [], Yi = 0, vc = null, eo = 1, to = "";
    function Mw(e) {
      return mc(), (e.flags & Ai) !== Ue;
    }
    function Lw(e) {
      return mc(), Rm;
    }
    function Aw() {
      var e = to, t = eo, a = t & ~zw(t);
      return a.toString(32) + e;
    }
    function hc(e, t) {
      mc(), qf[Xf++] = Rm, qf[Xf++] = Tm, Tm = e, Rm = t;
    }
    function s_(e, t, a) {
      mc(), $i[Yi++] = eo, $i[Yi++] = to, $i[Yi++] = vc, vc = e;
      var i = eo, u = to, s = wm(i) - 1, d = i & ~(1 << s), m = a + 1, y = wm(t) + s;
      if (y > 30) {
        var x = s - s % 5, R = (1 << x) - 1, M = (d & R).toString(32), O = d >> x, H = s - x, B = wm(t) + H, W = m << H, he = W | O, Pe = M + u;
        eo = 1 << B | he, to = Pe;
      } else {
        var Me = m << s, Nt = Me | d, Rt = u;
        eo = 1 << y | Nt, to = Rt;
      }
    }
    function Wg(e) {
      mc();
      var t = e.return;
      if (t !== null) {
        var a = 1, i = 0;
        hc(e, a), s_(e, a, i);
      }
    }
    function wm(e) {
      return 32 - $n(e);
    }
    function zw(e) {
      return 1 << wm(e) - 1;
    }
    function Qg(e) {
      for (; e === Tm; )
        Tm = qf[--Xf], qf[Xf] = null, Rm = qf[--Xf], qf[Xf] = null;
      for (; e === vc; )
        vc = $i[--Yi], $i[Yi] = null, to = $i[--Yi], $i[Yi] = null, eo = $i[--Yi], $i[Yi] = null;
    }
    function Uw() {
      return mc(), vc !== null ? {
        id: eo,
        overflow: to
      } : null;
    }
    function jw(e, t) {
      mc(), $i[Yi++] = eo, $i[Yi++] = to, $i[Yi++] = vc, eo = t.id, to = t.overflow, vc = e;
    }
    function mc() {
      Qr() || E("Expected to be hydrating. This is a bug in React. Please file an issue.");
    }
    var Wr = null, Wi = null, ml = !1, yc = !1, Jo = null;
    function Fw() {
      ml && E("We should not be hydrating here. This is a bug in React. Please file a bug.");
    }
    function c_() {
      yc = !0;
    }
    function Hw() {
      return yc;
    }
    function Vw(e) {
      var t = e.stateNode.containerInfo;
      return Wi = aw(t), Wr = e, ml = !0, Jo = null, yc = !1, !0;
    }
    function Pw(e, t, a) {
      return Wi = iw(t), Wr = e, ml = !0, Jo = null, yc = !1, a !== null && jw(e, a), !0;
    }
    function f_(e, t) {
      switch (e.tag) {
        case re: {
          hw(e.stateNode.containerInfo, t);
          break;
        }
        case de: {
          var a = (e.mode & yt) !== je;
          yw(
            e.type,
            e.memoizedProps,
            e.stateNode,
            t,
            // TODO: Delete this argument when we remove the legacy root API.
            a
          );
          break;
        }
        case ze: {
          var i = e.memoizedState;
          i.dehydrated !== null && mw(i.dehydrated, t);
          break;
        }
      }
    }
    function d_(e, t) {
      f_(e, t);
      var a = Y1();
      a.stateNode = t, a.return = e;
      var i = e.deletions;
      i === null ? (e.deletions = [a], e.flags |= Va) : i.push(a);
    }
    function Zg(e, t) {
      {
        if (yc)
          return;
        switch (e.tag) {
          case re: {
            var a = e.stateNode.containerInfo;
            switch (t.tag) {
              case de:
                var i = t.type;
                t.pendingProps, gw(a, i);
                break;
              case nt:
                var u = t.pendingProps;
                Sw(a, u);
                break;
            }
            break;
          }
          case de: {
            var s = e.type, d = e.memoizedProps, m = e.stateNode;
            switch (t.tag) {
              case de: {
                var y = t.type, x = t.pendingProps, R = (e.mode & yt) !== je;
                _w(
                  s,
                  d,
                  m,
                  y,
                  x,
                  // TODO: Delete this argument when we remove the legacy root API.
                  R
                );
                break;
              }
              case nt: {
                var M = t.pendingProps, O = (e.mode & yt) !== je;
                xw(
                  s,
                  d,
                  m,
                  M,
                  // TODO: Delete this argument when we remove the legacy root API.
                  O
                );
                break;
              }
            }
            break;
          }
          case ze: {
            var H = e.memoizedState, B = H.dehydrated;
            if (B !== null) switch (t.tag) {
              case de:
                var W = t.type;
                t.pendingProps, Ew(B, W);
                break;
              case nt:
                var he = t.pendingProps;
                Cw(B, he);
                break;
            }
            break;
          }
          default:
            return;
        }
      }
    }
    function p_(e, t) {
      t.flags = t.flags & ~ia | Rn, Zg(e, t);
    }
    function v_(e, t) {
      switch (e.tag) {
        case de: {
          var a = e.type;
          e.pendingProps;
          var i = KR(t, a);
          return i !== null ? (e.stateNode = i, Wr = e, Wi = rw(i), !0) : !1;
        }
        case nt: {
          var u = e.pendingProps, s = JR(t, u);
          return s !== null ? (e.stateNode = s, Wr = e, Wi = null, !0) : !1;
        }
        case ze: {
          var d = ew(t);
          if (d !== null) {
            var m = {
              dehydrated: d,
              treeContext: Uw(),
              retryLane: sa
            };
            e.memoizedState = m;
            var y = W1(d);
            return y.return = e, e.child = y, Wr = e, Wi = null, !0;
          }
          return !1;
        }
        default:
          return !1;
      }
    }
    function Gg(e) {
      return (e.mode & yt) !== je && (e.flags & Le) === Ue;
    }
    function qg(e) {
      throw new Error("Hydration failed because the initial UI does not match what was rendered on the server.");
    }
    function Xg(e) {
      if (ml) {
        var t = Wi;
        if (!t) {
          Gg(e) && (Zg(Wr, e), qg()), p_(Wr, e), ml = !1, Wr = e;
          return;
        }
        var a = t;
        if (!v_(e, t)) {
          Gg(e) && (Zg(Wr, e), qg()), t = Fp(a);
          var i = Wr;
          if (!t || !v_(e, t)) {
            p_(Wr, e), ml = !1, Wr = e;
            return;
          }
          d_(i, a);
        }
      }
    }
    function Bw(e, t, a) {
      var i = e.stateNode, u = !yc, s = lw(i, e.type, e.memoizedProps, t, a, e, u);
      return e.updateQueue = s, s !== null;
    }
    function Iw(e) {
      var t = e.stateNode, a = e.memoizedProps, i = uw(t, a, e);
      if (i) {
        var u = Wr;
        if (u !== null)
          switch (u.tag) {
            case re: {
              var s = u.stateNode.containerInfo, d = (u.mode & yt) !== je;
              pw(
                s,
                t,
                a,
                // TODO: Delete this argument when we remove the legacy root API.
                d
              );
              break;
            }
            case de: {
              var m = u.type, y = u.memoizedProps, x = u.stateNode, R = (u.mode & yt) !== je;
              vw(
                m,
                y,
                x,
                t,
                a,
                // TODO: Delete this argument when we remove the legacy root API.
                R
              );
              break;
            }
          }
      }
      return i;
    }
    function $w(e) {
      var t = e.memoizedState, a = t !== null ? t.dehydrated : null;
      if (!a)
        throw new Error("Expected to have a hydrated suspense instance. This error is likely caused by a bug in React. Please file an issue.");
      ow(a, e);
    }
    function Yw(e) {
      var t = e.memoizedState, a = t !== null ? t.dehydrated : null;
      if (!a)
        throw new Error("Expected to have a hydrated suspense instance. This error is likely caused by a bug in React. Please file an issue.");
      return sw(a);
    }
    function h_(e) {
      for (var t = e.return; t !== null && t.tag !== de && t.tag !== re && t.tag !== ze; )
        t = t.return;
      Wr = t;
    }
    function bm(e) {
      if (e !== Wr)
        return !1;
      if (!ml)
        return h_(e), ml = !0, !1;
      if (e.tag !== re && (e.tag !== de || dw(e.type) && !Mg(e.type, e.memoizedProps))) {
        var t = Wi;
        if (t)
          if (Gg(e))
            m_(e), qg();
          else
            for (; t; )
              d_(e, t), t = Fp(t);
      }
      return h_(e), e.tag === ze ? Wi = Yw(e) : Wi = Wr ? Fp(e.stateNode) : null, !0;
    }
    function Ww() {
      return ml && Wi !== null;
    }
    function m_(e) {
      for (var t = Wi; t; )
        f_(e, t), t = Fp(t);
    }
    function Kf() {
      Wr = null, Wi = null, ml = !1, yc = !1;
    }
    function y_() {
      Jo !== null && (cx(Jo), Jo = null);
    }
    function Qr() {
      return ml;
    }
    function Kg(e) {
      Jo === null ? Jo = [e] : Jo.push(e);
    }
    var Qw = p.ReactCurrentBatchConfig, Zw = null;
    function Gw() {
      return Qw.transition;
    }
    var yl = {
      recordUnsafeLifecycleWarnings: function(e, t) {
      },
      flushPendingUnsafeLifecycleWarnings: function() {
      },
      recordLegacyContextWarning: function(e, t) {
      },
      flushLegacyContextWarning: function() {
      },
      discardPendingWarnings: function() {
      }
    };
    {
      var qw = function(e) {
        for (var t = null, a = e; a !== null; )
          a.mode & rn && (t = a), a = a.return;
        return t;
      }, gc = function(e) {
        var t = [];
        return e.forEach(function(a) {
          t.push(a);
        }), t.sort().join(", ");
      }, Bp = [], Ip = [], $p = [], Yp = [], Wp = [], Qp = [], Sc = /* @__PURE__ */ new Set();
      yl.recordUnsafeLifecycleWarnings = function(e, t) {
        Sc.has(e.type) || (typeof t.componentWillMount == "function" && // Don't warn about react-lifecycles-compat polyfilled components.
        t.componentWillMount.__suppressDeprecationWarning !== !0 && Bp.push(e), e.mode & rn && typeof t.UNSAFE_componentWillMount == "function" && Ip.push(e), typeof t.componentWillReceiveProps == "function" && t.componentWillReceiveProps.__suppressDeprecationWarning !== !0 && $p.push(e), e.mode & rn && typeof t.UNSAFE_componentWillReceiveProps == "function" && Yp.push(e), typeof t.componentWillUpdate == "function" && t.componentWillUpdate.__suppressDeprecationWarning !== !0 && Wp.push(e), e.mode & rn && typeof t.UNSAFE_componentWillUpdate == "function" && Qp.push(e));
      }, yl.flushPendingUnsafeLifecycleWarnings = function() {
        var e = /* @__PURE__ */ new Set();
        Bp.length > 0 && (Bp.forEach(function(O) {
          e.add(Xe(O) || "Component"), Sc.add(O.type);
        }), Bp = []);
        var t = /* @__PURE__ */ new Set();
        Ip.length > 0 && (Ip.forEach(function(O) {
          t.add(Xe(O) || "Component"), Sc.add(O.type);
        }), Ip = []);
        var a = /* @__PURE__ */ new Set();
        $p.length > 0 && ($p.forEach(function(O) {
          a.add(Xe(O) || "Component"), Sc.add(O.type);
        }), $p = []);
        var i = /* @__PURE__ */ new Set();
        Yp.length > 0 && (Yp.forEach(function(O) {
          i.add(Xe(O) || "Component"), Sc.add(O.type);
        }), Yp = []);
        var u = /* @__PURE__ */ new Set();
        Wp.length > 0 && (Wp.forEach(function(O) {
          u.add(Xe(O) || "Component"), Sc.add(O.type);
        }), Wp = []);
        var s = /* @__PURE__ */ new Set();
        if (Qp.length > 0 && (Qp.forEach(function(O) {
          s.add(Xe(O) || "Component"), Sc.add(O.type);
        }), Qp = []), t.size > 0) {
          var d = gc(t);
          E(`Using UNSAFE_componentWillMount in strict mode is not recommended and may indicate bugs in your code. See https://reactjs.org/link/unsafe-component-lifecycles for details.

* Move code with side effects to componentDidMount, and set initial state in the constructor.

Please update the following components: %s`, d);
        }
        if (i.size > 0) {
          var m = gc(i);
          E(`Using UNSAFE_componentWillReceiveProps in strict mode is not recommended and may indicate bugs in your code. See https://reactjs.org/link/unsafe-component-lifecycles for details.

* Move data fetching code or side effects to componentDidUpdate.
* If you're updating state whenever props change, refactor your code to use memoization techniques or move it to static getDerivedStateFromProps. Learn more at: https://reactjs.org/link/derived-state

Please update the following components: %s`, m);
        }
        if (s.size > 0) {
          var y = gc(s);
          E(`Using UNSAFE_componentWillUpdate in strict mode is not recommended and may indicate bugs in your code. See https://reactjs.org/link/unsafe-component-lifecycles for details.

* Move data fetching code or side effects to componentDidUpdate.

Please update the following components: %s`, y);
        }
        if (e.size > 0) {
          var x = gc(e);
          T(`componentWillMount has been renamed, and is not recommended for use. See https://reactjs.org/link/unsafe-component-lifecycles for details.

* Move code with side effects to componentDidMount, and set initial state in the constructor.
* Rename componentWillMount to UNSAFE_componentWillMount to suppress this warning in non-strict mode. In React 18.x, only the UNSAFE_ name will work. To rename all deprecated lifecycles to their new names, you can run \`npx react-codemod rename-unsafe-lifecycles\` in your project source folder.

Please update the following components: %s`, x);
        }
        if (a.size > 0) {
          var R = gc(a);
          T(`componentWillReceiveProps has been renamed, and is not recommended for use. See https://reactjs.org/link/unsafe-component-lifecycles for details.

* Move data fetching code or side effects to componentDidUpdate.
* If you're updating state whenever props change, refactor your code to use memoization techniques or move it to static getDerivedStateFromProps. Learn more at: https://reactjs.org/link/derived-state
* Rename componentWillReceiveProps to UNSAFE_componentWillReceiveProps to suppress this warning in non-strict mode. In React 18.x, only the UNSAFE_ name will work. To rename all deprecated lifecycles to their new names, you can run \`npx react-codemod rename-unsafe-lifecycles\` in your project source folder.

Please update the following components: %s`, R);
        }
        if (u.size > 0) {
          var M = gc(u);
          T(`componentWillUpdate has been renamed, and is not recommended for use. See https://reactjs.org/link/unsafe-component-lifecycles for details.

* Move data fetching code or side effects to componentDidUpdate.
* Rename componentWillUpdate to UNSAFE_componentWillUpdate to suppress this warning in non-strict mode. In React 18.x, only the UNSAFE_ name will work. To rename all deprecated lifecycles to their new names, you can run \`npx react-codemod rename-unsafe-lifecycles\` in your project source folder.

Please update the following components: %s`, M);
        }
      };
      var km = /* @__PURE__ */ new Map(), g_ = /* @__PURE__ */ new Set();
      yl.recordLegacyContextWarning = function(e, t) {
        var a = qw(e);
        if (a === null) {
          E("Expected to find a StrictMode component in a strict mode tree. This error is likely caused by a bug in React. Please file an issue.");
          return;
        }
        if (!g_.has(e.type)) {
          var i = km.get(a);
          (e.type.contextTypes != null || e.type.childContextTypes != null || t !== null && typeof t.getChildContext == "function") && (i === void 0 && (i = [], km.set(a, i)), i.push(e));
        }
      }, yl.flushLegacyContextWarning = function() {
        km.forEach(function(e, t) {
          if (e.length !== 0) {
            var a = e[0], i = /* @__PURE__ */ new Set();
            e.forEach(function(s) {
              i.add(Xe(s) || "Component"), g_.add(s.type);
            });
            var u = gc(i);
            try {
              en(a), E(`Legacy context API has been detected within a strict-mode tree.

The old API will be supported in all 16.x releases, but applications using it should migrate to the new version.

Please update the following components: %s

Learn more about this warning here: https://reactjs.org/link/legacy-context`, u);
            } finally {
              gn();
            }
          }
        });
      }, yl.discardPendingWarnings = function() {
        Bp = [], Ip = [], $p = [], Yp = [], Wp = [], Qp = [], km = /* @__PURE__ */ new Map();
      };
    }
    var Jg, eS, tS, nS, rS, S_ = function(e, t) {
    };
    Jg = !1, eS = !1, tS = {}, nS = {}, rS = {}, S_ = function(e, t) {
      if (!(e === null || typeof e != "object") && !(!e._store || e._store.validated || e.key != null)) {
        if (typeof e._store != "object")
          throw new Error("React Component in warnForMissingKey should have a _store. This error is likely caused by a bug in React. Please file an issue.");
        e._store.validated = !0;
        var a = Xe(t) || "Component";
        nS[a] || (nS[a] = !0, E('Each child in a list should have a unique "key" prop. See https://reactjs.org/link/warning-keys for more information.'));
      }
    };
    function Xw(e) {
      return e.prototype && e.prototype.isReactComponent;
    }
    function Zp(e, t, a) {
      var i = a.ref;
      if (i !== null && typeof i != "function" && typeof i != "object") {
        if ((e.mode & rn || Z) && // We warn in ReactElement.js if owner and self are equal for string refs
        // because these cannot be automatically converted to an arrow function
        // using a codemod. Therefore, we don't have to warn about string refs again.
        !(a._owner && a._self && a._owner.stateNode !== a._self) && // Will already throw with "Function components cannot have string refs"
        !(a._owner && a._owner.tag !== $) && // Will already warn with "Function components cannot be given refs"
        !(typeof a.type == "function" && !Xw(a.type)) && // Will already throw with "Element ref was specified as a string (someStringRef) but no owner was set"
        a._owner) {
          var u = Xe(e) || "Component";
          tS[u] || (E('Component "%s" contains the string ref "%s". Support for string refs will be removed in a future major release. We recommend using useRef() or createRef() instead. Learn more about using refs safely here: https://reactjs.org/link/strict-mode-string-ref', u, i), tS[u] = !0);
        }
        if (a._owner) {
          var s = a._owner, d;
          if (s) {
            var m = s;
            if (m.tag !== $)
              throw new Error("Function components cannot have string refs. We recommend using useRef() instead. Learn more about using refs safely here: https://reactjs.org/link/strict-mode-string-ref");
            d = m.stateNode;
          }
          if (!d)
            throw new Error("Missing owner for string ref " + i + ". This error is likely caused by a bug in React. Please file an issue.");
          var y = d;
          _i(i, "ref");
          var x = "" + i;
          if (t !== null && t.ref !== null && typeof t.ref == "function" && t.ref._stringRef === x)
            return t.ref;
          var R = function(M) {
            var O = y.refs;
            M === null ? delete O[x] : O[x] = M;
          };
          return R._stringRef = x, R;
        } else {
          if (typeof i != "string")
            throw new Error("Expected ref to be a function, a string, an object returned by React.createRef(), or null.");
          if (!a._owner)
            throw new Error("Element ref was specified as a string (" + i + `) but no owner was set. This could happen for one of the following reasons:
1. You may be adding a ref to a function component
2. You may be adding a ref to a component that was not created inside a component's render method
3. You have multiple copies of React loaded
See https://reactjs.org/link/refs-must-have-owner for more information.`);
        }
      }
      return i;
    }
    function Dm(e, t) {
      var a = Object.prototype.toString.call(t);
      throw new Error("Objects are not valid as a React child (found: " + (a === "[object Object]" ? "object with keys {" + Object.keys(t).join(", ") + "}" : a) + "). If you meant to render a collection of children, use an array instead.");
    }
    function Om(e) {
      {
        var t = Xe(e) || "Component";
        if (rS[t])
          return;
        rS[t] = !0, E("Functions are not valid as a React child. This may happen if you return a Component instead of <Component /> from render. Or maybe you meant to call this function rather than return it.");
      }
    }
    function E_(e) {
      var t = e._payload, a = e._init;
      return a(t);
    }
    function C_(e) {
      function t(U, Q) {
        if (e) {
          var j = U.deletions;
          j === null ? (U.deletions = [Q], U.flags |= Va) : j.push(Q);
        }
      }
      function a(U, Q) {
        if (!e)
          return null;
        for (var j = Q; j !== null; )
          t(U, j), j = j.sibling;
        return null;
      }
      function i(U, Q) {
        for (var j = /* @__PURE__ */ new Map(), ne = Q; ne !== null; )
          ne.key !== null ? j.set(ne.key, ne) : j.set(ne.index, ne), ne = ne.sibling;
        return j;
      }
      function u(U, Q) {
        var j = kc(U, Q);
        return j.index = 0, j.sibling = null, j;
      }
      function s(U, Q, j) {
        if (U.index = j, !e)
          return U.flags |= Ai, Q;
        var ne = U.alternate;
        if (ne !== null) {
          var Ee = ne.index;
          return Ee < Q ? (U.flags |= Rn, Q) : Ee;
        } else
          return U.flags |= Rn, Q;
      }
      function d(U) {
        return e && U.alternate === null && (U.flags |= Rn), U;
      }
      function m(U, Q, j, ne) {
        if (Q === null || Q.tag !== nt) {
          var Ee = KE(j, U.mode, ne);
          return Ee.return = U, Ee;
        } else {
          var me = u(Q, j);
          return me.return = U, me;
        }
      }
      function y(U, Q, j, ne) {
        var Ee = j.type;
        if (Ee === Ti)
          return R(U, Q, j.props.children, ne, j.key);
        if (Q !== null && (Q.elementType === Ee || // Keep this check inline so it only runs on the false path:
        wx(Q, j) || // Lazy types should reconcile their resolved type.
        // We need to do this after the Hot Reloading check above,
        // because hot reloading has different semantics than prod because
        // it doesn't resuspend. So we can't let the call below suspend.
        typeof Ee == "object" && Ee !== null && Ee.$$typeof === Ke && E_(Ee) === Q.type)) {
          var me = u(Q, j.props);
          return me.ref = Zp(U, Q, j), me.return = U, me._debugSource = j._source, me._debugOwner = j._owner, me;
        }
        var Ge = XE(j, U.mode, ne);
        return Ge.ref = Zp(U, Q, j), Ge.return = U, Ge;
      }
      function x(U, Q, j, ne) {
        if (Q === null || Q.tag !== be || Q.stateNode.containerInfo !== j.containerInfo || Q.stateNode.implementation !== j.implementation) {
          var Ee = JE(j, U.mode, ne);
          return Ee.return = U, Ee;
        } else {
          var me = u(Q, j.children || []);
          return me.return = U, me;
        }
      }
      function R(U, Q, j, ne, Ee) {
        if (Q === null || Q.tag !== bt) {
          var me = cs(j, U.mode, ne, Ee);
          return me.return = U, me;
        } else {
          var Ge = u(Q, j);
          return Ge.return = U, Ge;
        }
      }
      function M(U, Q, j) {
        if (typeof Q == "string" && Q !== "" || typeof Q == "number") {
          var ne = KE("" + Q, U.mode, j);
          return ne.return = U, ne;
        }
        if (typeof Q == "object" && Q !== null) {
          switch (Q.$$typeof) {
            case Fr: {
              var Ee = XE(Q, U.mode, j);
              return Ee.ref = Zp(U, null, Q), Ee.return = U, Ee;
            }
            case vr: {
              var me = JE(Q, U.mode, j);
              return me.return = U, me;
            }
            case Ke: {
              var Ge = Q._payload, at = Q._init;
              return M(U, at(Ge), j);
            }
          }
          if (pt(Q) || lt(Q)) {
            var ln = cs(Q, U.mode, j, null);
            return ln.return = U, ln;
          }
          Dm(U, Q);
        }
        return typeof Q == "function" && Om(U), null;
      }
      function O(U, Q, j, ne) {
        var Ee = Q !== null ? Q.key : null;
        if (typeof j == "string" && j !== "" || typeof j == "number")
          return Ee !== null ? null : m(U, Q, "" + j, ne);
        if (typeof j == "object" && j !== null) {
          switch (j.$$typeof) {
            case Fr:
              return j.key === Ee ? y(U, Q, j, ne) : null;
            case vr:
              return j.key === Ee ? x(U, Q, j, ne) : null;
            case Ke: {
              var me = j._payload, Ge = j._init;
              return O(U, Q, Ge(me), ne);
            }
          }
          if (pt(j) || lt(j))
            return Ee !== null ? null : R(U, Q, j, ne, null);
          Dm(U, j);
        }
        return typeof j == "function" && Om(U), null;
      }
      function H(U, Q, j, ne, Ee) {
        if (typeof ne == "string" && ne !== "" || typeof ne == "number") {
          var me = U.get(j) || null;
          return m(Q, me, "" + ne, Ee);
        }
        if (typeof ne == "object" && ne !== null) {
          switch (ne.$$typeof) {
            case Fr: {
              var Ge = U.get(ne.key === null ? j : ne.key) || null;
              return y(Q, Ge, ne, Ee);
            }
            case vr: {
              var at = U.get(ne.key === null ? j : ne.key) || null;
              return x(Q, at, ne, Ee);
            }
            case Ke:
              var ln = ne._payload, It = ne._init;
              return H(U, Q, j, It(ln), Ee);
          }
          if (pt(ne) || lt(ne)) {
            var rr = U.get(j) || null;
            return R(Q, rr, ne, Ee, null);
          }
          Dm(Q, ne);
        }
        return typeof ne == "function" && Om(Q), null;
      }
      function B(U, Q, j) {
        {
          if (typeof U != "object" || U === null)
            return Q;
          switch (U.$$typeof) {
            case Fr:
            case vr:
              S_(U, j);
              var ne = U.key;
              if (typeof ne != "string")
                break;
              if (Q === null) {
                Q = /* @__PURE__ */ new Set(), Q.add(ne);
                break;
              }
              if (!Q.has(ne)) {
                Q.add(ne);
                break;
              }
              E("Encountered two children with the same key, `%s`. Keys should be unique so that components maintain their identity across updates. Non-unique keys may cause children to be duplicated and/or omitted — the behavior is unsupported and could change in a future version.", ne);
              break;
            case Ke:
              var Ee = U._payload, me = U._init;
              B(me(Ee), Q, j);
              break;
          }
        }
        return Q;
      }
      function W(U, Q, j, ne) {
        for (var Ee = null, me = 0; me < j.length; me++) {
          var Ge = j[me];
          Ee = B(Ge, Ee, U);
        }
        for (var at = null, ln = null, It = Q, rr = 0, $t = 0, Gn = null; It !== null && $t < j.length; $t++) {
          It.index > $t ? (Gn = It, It = null) : Gn = It.sibling;
          var ya = O(U, It, j[$t], ne);
          if (ya === null) {
            It === null && (It = Gn);
            break;
          }
          e && It && ya.alternate === null && t(U, It), rr = s(ya, rr, $t), ln === null ? at = ya : ln.sibling = ya, ln = ya, It = Gn;
        }
        if ($t === j.length) {
          if (a(U, It), Qr()) {
            var ea = $t;
            hc(U, ea);
          }
          return at;
        }
        if (It === null) {
          for (; $t < j.length; $t++) {
            var Ci = M(U, j[$t], ne);
            Ci !== null && (rr = s(Ci, rr, $t), ln === null ? at = Ci : ln.sibling = Ci, ln = Ci);
          }
          if (Qr()) {
            var Ma = $t;
            hc(U, Ma);
          }
          return at;
        }
        for (var La = i(U, It); $t < j.length; $t++) {
          var ga = H(La, U, $t, j[$t], ne);
          ga !== null && (e && ga.alternate !== null && La.delete(ga.key === null ? $t : ga.key), rr = s(ga, rr, $t), ln === null ? at = ga : ln.sibling = ga, ln = ga);
        }
        if (e && La.forEach(function(yd) {
          return t(U, yd);
        }), Qr()) {
          var oo = $t;
          hc(U, oo);
        }
        return at;
      }
      function he(U, Q, j, ne) {
        var Ee = lt(j);
        if (typeof Ee != "function")
          throw new Error("An object is not an iterable. This error is likely caused by a bug in React. Please file an issue.");
        {
          typeof Symbol == "function" && // $FlowFixMe Flow doesn't know about toStringTag
          j[Symbol.toStringTag] === "Generator" && (eS || E("Using Generators as children is unsupported and will likely yield unexpected results because enumerating a generator mutates it. You may convert it to an array with `Array.from()` or the `[...spread]` operator before rendering. Keep in mind you might need to polyfill these features for older browsers."), eS = !0), j.entries === Ee && (Jg || E("Using Maps as children is not supported. Use an array of keyed ReactElements instead."), Jg = !0);
          var me = Ee.call(j);
          if (me)
            for (var Ge = null, at = me.next(); !at.done; at = me.next()) {
              var ln = at.value;
              Ge = B(ln, Ge, U);
            }
        }
        var It = Ee.call(j);
        if (It == null)
          throw new Error("An iterable object provided no iterator.");
        for (var rr = null, $t = null, Gn = Q, ya = 0, ea = 0, Ci = null, Ma = It.next(); Gn !== null && !Ma.done; ea++, Ma = It.next()) {
          Gn.index > ea ? (Ci = Gn, Gn = null) : Ci = Gn.sibling;
          var La = O(U, Gn, Ma.value, ne);
          if (La === null) {
            Gn === null && (Gn = Ci);
            break;
          }
          e && Gn && La.alternate === null && t(U, Gn), ya = s(La, ya, ea), $t === null ? rr = La : $t.sibling = La, $t = La, Gn = Ci;
        }
        if (Ma.done) {
          if (a(U, Gn), Qr()) {
            var ga = ea;
            hc(U, ga);
          }
          return rr;
        }
        if (Gn === null) {
          for (; !Ma.done; ea++, Ma = It.next()) {
            var oo = M(U, Ma.value, ne);
            oo !== null && (ya = s(oo, ya, ea), $t === null ? rr = oo : $t.sibling = oo, $t = oo);
          }
          if (Qr()) {
            var yd = ea;
            hc(U, yd);
          }
          return rr;
        }
        for (var wv = i(U, Gn); !Ma.done; ea++, Ma = It.next()) {
          var fu = H(wv, U, ea, Ma.value, ne);
          fu !== null && (e && fu.alternate !== null && wv.delete(fu.key === null ? ea : fu.key), ya = s(fu, ya, ea), $t === null ? rr = fu : $t.sibling = fu, $t = fu);
        }
        if (e && wv.forEach(function(xD) {
          return t(U, xD);
        }), Qr()) {
          var _D = ea;
          hc(U, _D);
        }
        return rr;
      }
      function Pe(U, Q, j, ne) {
        if (Q !== null && Q.tag === nt) {
          a(U, Q.sibling);
          var Ee = u(Q, j);
          return Ee.return = U, Ee;
        }
        a(U, Q);
        var me = KE(j, U.mode, ne);
        return me.return = U, me;
      }
      function Me(U, Q, j, ne) {
        for (var Ee = j.key, me = Q; me !== null; ) {
          if (me.key === Ee) {
            var Ge = j.type;
            if (Ge === Ti) {
              if (me.tag === bt) {
                a(U, me.sibling);
                var at = u(me, j.props.children);
                return at.return = U, at._debugSource = j._source, at._debugOwner = j._owner, at;
              }
            } else if (me.elementType === Ge || // Keep this check inline so it only runs on the false path:
            wx(me, j) || // Lazy types should reconcile their resolved type.
            // We need to do this after the Hot Reloading check above,
            // because hot reloading has different semantics than prod because
            // it doesn't resuspend. So we can't let the call below suspend.
            typeof Ge == "object" && Ge !== null && Ge.$$typeof === Ke && E_(Ge) === me.type) {
              a(U, me.sibling);
              var ln = u(me, j.props);
              return ln.ref = Zp(U, me, j), ln.return = U, ln._debugSource = j._source, ln._debugOwner = j._owner, ln;
            }
            a(U, me);
            break;
          } else
            t(U, me);
          me = me.sibling;
        }
        if (j.type === Ti) {
          var It = cs(j.props.children, U.mode, ne, j.key);
          return It.return = U, It;
        } else {
          var rr = XE(j, U.mode, ne);
          return rr.ref = Zp(U, Q, j), rr.return = U, rr;
        }
      }
      function Nt(U, Q, j, ne) {
        for (var Ee = j.key, me = Q; me !== null; ) {
          if (me.key === Ee)
            if (me.tag === be && me.stateNode.containerInfo === j.containerInfo && me.stateNode.implementation === j.implementation) {
              a(U, me.sibling);
              var Ge = u(me, j.children || []);
              return Ge.return = U, Ge;
            } else {
              a(U, me);
              break;
            }
          else
            t(U, me);
          me = me.sibling;
        }
        var at = JE(j, U.mode, ne);
        return at.return = U, at;
      }
      function Rt(U, Q, j, ne) {
        var Ee = typeof j == "object" && j !== null && j.type === Ti && j.key === null;
        if (Ee && (j = j.props.children), typeof j == "object" && j !== null) {
          switch (j.$$typeof) {
            case Fr:
              return d(Me(U, Q, j, ne));
            case vr:
              return d(Nt(U, Q, j, ne));
            case Ke:
              var me = j._payload, Ge = j._init;
              return Rt(U, Q, Ge(me), ne);
          }
          if (pt(j))
            return W(U, Q, j, ne);
          if (lt(j))
            return he(U, Q, j, ne);
          Dm(U, j);
        }
        return typeof j == "string" && j !== "" || typeof j == "number" ? d(Pe(U, Q, "" + j, ne)) : (typeof j == "function" && Om(U), a(U, Q));
      }
      return Rt;
    }
    var Jf = C_(!0), __ = C_(!1);
    function Kw(e, t) {
      if (e !== null && t.child !== e.child)
        throw new Error("Resuming work not yet implemented.");
      if (t.child !== null) {
        var a = t.child, i = kc(a, a.pendingProps);
        for (t.child = i, i.return = t; a.sibling !== null; )
          a = a.sibling, i = i.sibling = kc(a, a.pendingProps), i.return = t;
        i.sibling = null;
      }
    }
    function Jw(e, t) {
      for (var a = e.child; a !== null; )
        V1(a, t), a = a.sibling;
    }
    var aS = qo(null), iS;
    iS = {};
    var Nm = null, ed = null, lS = null, Mm = !1;
    function Lm() {
      Nm = null, ed = null, lS = null, Mm = !1;
    }
    function x_() {
      Mm = !0;
    }
    function T_() {
      Mm = !1;
    }
    function R_(e, t, a) {
      ha(aS, t._currentValue, e), t._currentValue = a, t._currentRenderer !== void 0 && t._currentRenderer !== null && t._currentRenderer !== iS && E("Detected multiple renderers concurrently rendering the same context provider. This is currently unsupported."), t._currentRenderer = iS;
    }
    function uS(e, t) {
      var a = aS.current;
      va(aS, t), e._currentValue = a;
    }
    function oS(e, t, a) {
      for (var i = e; i !== null; ) {
        var u = i.alternate;
        if ($u(i.childLanes, t) ? u !== null && !$u(u.childLanes, t) && (u.childLanes = ut(u.childLanes, t)) : (i.childLanes = ut(i.childLanes, t), u !== null && (u.childLanes = ut(u.childLanes, t))), i === a)
          break;
        i = i.return;
      }
      i !== a && E("Expected to find the propagation root when scheduling context work. This error is likely caused by a bug in React. Please file an issue.");
    }
    function eb(e, t, a) {
      tb(e, t, a);
    }
    function tb(e, t, a) {
      var i = e.child;
      for (i !== null && (i.return = e); i !== null; ) {
        var u = void 0, s = i.dependencies;
        if (s !== null) {
          u = i.child;
          for (var d = s.firstContext; d !== null; ) {
            if (d.context === t) {
              if (i.tag === $) {
                var m = Ws(a), y = no(un, m);
                y.tag = zm;
                var x = i.updateQueue;
                if (x !== null) {
                  var R = x.shared, M = R.pending;
                  M === null ? y.next = y : (y.next = M.next, M.next = y), R.pending = y;
                }
              }
              i.lanes = ut(i.lanes, a);
              var O = i.alternate;
              O !== null && (O.lanes = ut(O.lanes, a)), oS(i.return, a, e), s.lanes = ut(s.lanes, a);
              break;
            }
            d = d.next;
          }
        } else if (i.tag === _t)
          u = i.type === e.type ? null : i.child;
        else if (i.tag === on) {
          var H = i.return;
          if (H === null)
            throw new Error("We just came from a parent so we must have had a parent. This is a bug in React.");
          H.lanes = ut(H.lanes, a);
          var B = H.alternate;
          B !== null && (B.lanes = ut(B.lanes, a)), oS(H, a, e), u = i.sibling;
        } else
          u = i.child;
        if (u !== null)
          u.return = i;
        else
          for (u = i; u !== null; ) {
            if (u === e) {
              u = null;
              break;
            }
            var W = u.sibling;
            if (W !== null) {
              W.return = u.return, u = W;
              break;
            }
            u = u.return;
          }
        i = u;
      }
    }
    function td(e, t) {
      Nm = e, ed = null, lS = null;
      var a = e.dependencies;
      if (a !== null) {
        var i = a.firstContext;
        i !== null && (ca(a.lanes, t) && sv(), a.firstContext = null);
      }
    }
    function fr(e) {
      Mm && E("Context can only be read while React is rendering. In classes, you can read it in the render method or getDerivedStateFromProps. In function components, you can read it directly in the function body, but not inside Hooks like useReducer() or useMemo().");
      var t = e._currentValue;
      if (lS !== e) {
        var a = {
          context: e,
          memoizedValue: t,
          next: null
        };
        if (ed === null) {
          if (Nm === null)
            throw new Error("Context can only be read while React is rendering. In classes, you can read it in the render method or getDerivedStateFromProps. In function components, you can read it directly in the function body, but not inside Hooks like useReducer() or useMemo().");
          ed = a, Nm.dependencies = {
            lanes: X,
            firstContext: a
          };
        } else
          ed = ed.next = a;
      }
      return t;
    }
    var Ec = null;
    function sS(e) {
      Ec === null ? Ec = [e] : Ec.push(e);
    }
    function nb() {
      if (Ec !== null) {
        for (var e = 0; e < Ec.length; e++) {
          var t = Ec[e], a = t.interleaved;
          if (a !== null) {
            t.interleaved = null;
            var i = a.next, u = t.pending;
            if (u !== null) {
              var s = u.next;
              u.next = i, a.next = s;
            }
            t.pending = a;
          }
        }
        Ec = null;
      }
    }
    function w_(e, t, a, i) {
      var u = t.interleaved;
      return u === null ? (a.next = a, sS(t)) : (a.next = u.next, u.next = a), t.interleaved = a, Am(e, i);
    }
    function rb(e, t, a, i) {
      var u = t.interleaved;
      u === null ? (a.next = a, sS(t)) : (a.next = u.next, u.next = a), t.interleaved = a;
    }
    function ab(e, t, a, i) {
      var u = t.interleaved;
      return u === null ? (a.next = a, sS(t)) : (a.next = u.next, u.next = a), t.interleaved = a, Am(e, i);
    }
    function qa(e, t) {
      return Am(e, t);
    }
    var ib = Am;
    function Am(e, t) {
      e.lanes = ut(e.lanes, t);
      var a = e.alternate;
      a !== null && (a.lanes = ut(a.lanes, t)), a === null && (e.flags & (Rn | ia)) !== Ue && _x(e);
      for (var i = e, u = e.return; u !== null; )
        u.childLanes = ut(u.childLanes, t), a = u.alternate, a !== null ? a.childLanes = ut(a.childLanes, t) : (u.flags & (Rn | ia)) !== Ue && _x(e), i = u, u = u.return;
      if (i.tag === re) {
        var s = i.stateNode;
        return s;
      } else
        return null;
    }
    var b_ = 0, k_ = 1, zm = 2, cS = 3, Um = !1, fS, jm;
    fS = !1, jm = null;
    function dS(e) {
      var t = {
        baseState: e.memoizedState,
        firstBaseUpdate: null,
        lastBaseUpdate: null,
        shared: {
          pending: null,
          interleaved: null,
          lanes: X
        },
        effects: null
      };
      e.updateQueue = t;
    }
    function D_(e, t) {
      var a = t.updateQueue, i = e.updateQueue;
      if (a === i) {
        var u = {
          baseState: i.baseState,
          firstBaseUpdate: i.firstBaseUpdate,
          lastBaseUpdate: i.lastBaseUpdate,
          shared: i.shared,
          effects: i.effects
        };
        t.updateQueue = u;
      }
    }
    function no(e, t) {
      var a = {
        eventTime: e,
        lane: t,
        tag: b_,
        payload: null,
        callback: null,
        next: null
      };
      return a;
    }
    function es(e, t, a) {
      var i = e.updateQueue;
      if (i === null)
        return null;
      var u = i.shared;
      if (jm === u && !fS && (E("An update (setState, replaceState, or forceUpdate) was scheduled from inside an update function. Update functions should be pure, with zero side-effects. Consider using componentDidUpdate or a callback."), fS = !0), r1()) {
        var s = u.pending;
        return s === null ? t.next = t : (t.next = s.next, s.next = t), u.pending = t, ib(e, a);
      } else
        return ab(e, u, t, a);
    }
    function Fm(e, t, a) {
      var i = t.updateQueue;
      if (i !== null) {
        var u = i.shared;
        if (up(a)) {
          var s = u.lanes;
          s = sp(s, e.pendingLanes);
          var d = ut(s, a);
          u.lanes = d, bf(e, d);
        }
      }
    }
    function pS(e, t) {
      var a = e.updateQueue, i = e.alternate;
      if (i !== null) {
        var u = i.updateQueue;
        if (a === u) {
          var s = null, d = null, m = a.firstBaseUpdate;
          if (m !== null) {
            var y = m;
            do {
              var x = {
                eventTime: y.eventTime,
                lane: y.lane,
                tag: y.tag,
                payload: y.payload,
                callback: y.callback,
                next: null
              };
              d === null ? s = d = x : (d.next = x, d = x), y = y.next;
            } while (y !== null);
            d === null ? s = d = t : (d.next = t, d = t);
          } else
            s = d = t;
          a = {
            baseState: u.baseState,
            firstBaseUpdate: s,
            lastBaseUpdate: d,
            shared: u.shared,
            effects: u.effects
          }, e.updateQueue = a;
          return;
        }
      }
      var R = a.lastBaseUpdate;
      R === null ? a.firstBaseUpdate = t : R.next = t, a.lastBaseUpdate = t;
    }
    function lb(e, t, a, i, u, s) {
      switch (a.tag) {
        case k_: {
          var d = a.payload;
          if (typeof d == "function") {
            x_();
            var m = d.call(s, i, u);
            {
              if (e.mode & rn) {
                wn(!0);
                try {
                  d.call(s, i, u);
                } finally {
                  wn(!1);
                }
              }
              T_();
            }
            return m;
          }
          return d;
        }
        case cS:
          e.flags = e.flags & ~ur | Le;
        case b_: {
          var y = a.payload, x;
          if (typeof y == "function") {
            x_(), x = y.call(s, i, u);
            {
              if (e.mode & rn) {
                wn(!0);
                try {
                  y.call(s, i, u);
                } finally {
                  wn(!1);
                }
              }
              T_();
            }
          } else
            x = y;
          return x == null ? i : st({}, i, x);
        }
        case zm:
          return Um = !0, i;
      }
      return i;
    }
    function Hm(e, t, a, i) {
      var u = e.updateQueue;
      Um = !1, jm = u.shared;
      var s = u.firstBaseUpdate, d = u.lastBaseUpdate, m = u.shared.pending;
      if (m !== null) {
        u.shared.pending = null;
        var y = m, x = y.next;
        y.next = null, d === null ? s = x : d.next = x, d = y;
        var R = e.alternate;
        if (R !== null) {
          var M = R.updateQueue, O = M.lastBaseUpdate;
          O !== d && (O === null ? M.firstBaseUpdate = x : O.next = x, M.lastBaseUpdate = y);
        }
      }
      if (s !== null) {
        var H = u.baseState, B = X, W = null, he = null, Pe = null, Me = s;
        do {
          var Nt = Me.lane, Rt = Me.eventTime;
          if ($u(i, Nt)) {
            if (Pe !== null) {
              var Q = {
                eventTime: Rt,
                // This update is going to be committed so we never want uncommit
                // it. Using NoLane works because 0 is a subset of all bitmasks, so
                // this will never be skipped by the check above.
                lane: jt,
                tag: Me.tag,
                payload: Me.payload,
                callback: Me.callback,
                next: null
              };
              Pe = Pe.next = Q;
            }
            H = lb(e, u, Me, H, t, a);
            var j = Me.callback;
            if (j !== null && // If the update was already committed, we should not queue its
            // callback again.
            Me.lane !== jt) {
              e.flags |= pn;
              var ne = u.effects;
              ne === null ? u.effects = [Me] : ne.push(Me);
            }
          } else {
            var U = {
              eventTime: Rt,
              lane: Nt,
              tag: Me.tag,
              payload: Me.payload,
              callback: Me.callback,
              next: null
            };
            Pe === null ? (he = Pe = U, W = H) : Pe = Pe.next = U, B = ut(B, Nt);
          }
          if (Me = Me.next, Me === null) {
            if (m = u.shared.pending, m === null)
              break;
            var Ee = m, me = Ee.next;
            Ee.next = null, Me = me, u.lastBaseUpdate = Ee, u.shared.pending = null;
          }
        } while (!0);
        Pe === null && (W = H), u.baseState = W, u.firstBaseUpdate = he, u.lastBaseUpdate = Pe;
        var Ge = u.shared.interleaved;
        if (Ge !== null) {
          var at = Ge;
          do
            B = ut(B, at.lane), at = at.next;
          while (at !== Ge);
        } else s === null && (u.shared.lanes = X);
        Cv(B), e.lanes = B, e.memoizedState = H;
      }
      jm = null;
    }
    function ub(e, t) {
      if (typeof e != "function")
        throw new Error("Invalid argument passed as callback. Expected a function. Instead " + ("received: " + e));
      e.call(t);
    }
    function O_() {
      Um = !1;
    }
    function Vm() {
      return Um;
    }
    function N_(e, t, a) {
      var i = t.effects;
      if (t.effects = null, i !== null)
        for (var u = 0; u < i.length; u++) {
          var s = i[u], d = s.callback;
          d !== null && (s.callback = null, ub(d, a));
        }
    }
    var Gp = {}, ts = qo(Gp), qp = qo(Gp), Pm = qo(Gp);
    function Bm(e) {
      if (e === Gp)
        throw new Error("Expected host context to exist. This error is likely caused by a bug in React. Please file an issue.");
      return e;
    }
    function M_() {
      var e = Bm(Pm.current);
      return e;
    }
    function vS(e, t) {
      ha(Pm, t, e), ha(qp, e, e), ha(ts, Gp, e);
      var a = TR(t);
      va(ts, e), ha(ts, a, e);
    }
    function nd(e) {
      va(ts, e), va(qp, e), va(Pm, e);
    }
    function hS() {
      var e = Bm(ts.current);
      return e;
    }
    function L_(e) {
      Bm(Pm.current);
      var t = Bm(ts.current), a = RR(t, e.type);
      t !== a && (ha(qp, e, e), ha(ts, a, e));
    }
    function mS(e) {
      qp.current === e && (va(ts, e), va(qp, e));
    }
    var ob = 0, A_ = 1, z_ = 1, Xp = 2, gl = qo(ob);
    function yS(e, t) {
      return (e & t) !== 0;
    }
    function rd(e) {
      return e & A_;
    }
    function gS(e, t) {
      return e & A_ | t;
    }
    function sb(e, t) {
      return e | t;
    }
    function ns(e, t) {
      ha(gl, t, e);
    }
    function ad(e) {
      va(gl, e);
    }
    function cb(e, t) {
      var a = e.memoizedState;
      return a !== null ? a.dehydrated !== null : (e.memoizedProps, !0);
    }
    function Im(e) {
      for (var t = e; t !== null; ) {
        if (t.tag === ze) {
          var a = t.memoizedState;
          if (a !== null) {
            var i = a.dehydrated;
            if (i === null || KC(i) || Ug(i))
              return t;
          }
        } else if (t.tag === hn && // revealOrder undefined can't be trusted because it don't
        // keep track of whether it suspended or not.
        t.memoizedProps.revealOrder !== void 0) {
          var u = (t.flags & Le) !== Ue;
          if (u)
            return t;
        } else if (t.child !== null) {
          t.child.return = t, t = t.child;
          continue;
        }
        if (t === e)
          return null;
        for (; t.sibling === null; ) {
          if (t.return === null || t.return === e)
            return null;
          t = t.return;
        }
        t.sibling.return = t.return, t = t.sibling;
      }
      return null;
    }
    var Xa = (
      /*   */
      0
    ), Cr = (
      /* */
      1
    ), au = (
      /*  */
      2
    ), _r = (
      /*    */
      4
    ), Zr = (
      /*   */
      8
    ), SS = [];
    function ES() {
      for (var e = 0; e < SS.length; e++) {
        var t = SS[e];
        t._workInProgressVersionPrimary = null;
      }
      SS.length = 0;
    }
    function fb(e, t) {
      var a = t._getVersion, i = a(t._source);
      e.mutableSourceEagerHydrationData == null ? e.mutableSourceEagerHydrationData = [t, i] : e.mutableSourceEagerHydrationData.push(t, i);
    }
    var Se = p.ReactCurrentDispatcher, Kp = p.ReactCurrentBatchConfig, CS, id;
    CS = /* @__PURE__ */ new Set();
    var Cc = X, an = null, xr = null, Tr = null, $m = !1, Jp = !1, ev = 0, db = 0, pb = 25, G = null, Qi = null, rs = -1, _S = !1;
    function qt() {
      {
        var e = G;
        Qi === null ? Qi = [e] : Qi.push(e);
      }
    }
    function oe() {
      {
        var e = G;
        Qi !== null && (rs++, Qi[rs] !== e && vb(e));
      }
    }
    function ld(e) {
      e != null && !pt(e) && E("%s received a final argument that is not an array (instead, received `%s`). When specified, the final argument must be an array.", G, typeof e);
    }
    function vb(e) {
      {
        var t = Xe(an);
        if (!CS.has(t) && (CS.add(t), Qi !== null)) {
          for (var a = "", i = 30, u = 0; u <= rs; u++) {
            for (var s = Qi[u], d = u === rs ? e : s, m = u + 1 + ". " + s; m.length < i; )
              m += " ";
            m += d + `
`, a += m;
          }
          E(`React has detected a change in the order of Hooks called by %s. This will lead to bugs and errors if not fixed. For more information, read the Rules of Hooks: https://reactjs.org/link/rules-of-hooks

   Previous render            Next render
   ------------------------------------------------------
%s   ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
`, t, a);
        }
      }
    }
    function ma() {
      throw new Error(`Invalid hook call. Hooks can only be called inside of the body of a function component. This could happen for one of the following reasons:
1. You might have mismatching versions of React and the renderer (such as React DOM)
2. You might be breaking the Rules of Hooks
3. You might have more than one copy of React in the same app
See https://reactjs.org/link/invalid-hook-call for tips about how to debug and fix this problem.`);
    }
    function xS(e, t) {
      if (_S)
        return !1;
      if (t === null)
        return E("%s received a final argument during this render, but not during the previous render. Even though the final argument is optional, its type cannot change between renders.", G), !1;
      e.length !== t.length && E(`The final argument passed to %s changed size between renders. The order and size of this array must remain constant.

Previous: %s
Incoming: %s`, G, "[" + t.join(", ") + "]", "[" + e.join(", ") + "]");
      for (var a = 0; a < t.length && a < e.length; a++)
        if (!ee(e[a], t[a]))
          return !1;
      return !0;
    }
    function ud(e, t, a, i, u, s) {
      Cc = s, an = t, Qi = e !== null ? e._debugHookTypes : null, rs = -1, _S = e !== null && e.type !== t.type, t.memoizedState = null, t.updateQueue = null, t.lanes = X, e !== null && e.memoizedState !== null ? Se.current = r0 : Qi !== null ? Se.current = n0 : Se.current = t0;
      var d = a(i, u);
      if (Jp) {
        var m = 0;
        do {
          if (Jp = !1, ev = 0, m >= pb)
            throw new Error("Too many re-renders. React limits the number of renders to prevent an infinite loop.");
          m += 1, _S = !1, xr = null, Tr = null, t.updateQueue = null, rs = -1, Se.current = a0, d = a(i, u);
        } while (Jp);
      }
      Se.current = ry, t._debugHookTypes = Qi;
      var y = xr !== null && xr.next !== null;
      if (Cc = X, an = null, xr = null, Tr = null, G = null, Qi = null, rs = -1, e !== null && (e.flags & In) !== (t.flags & In) && // Disable this warning in legacy mode, because legacy Suspense is weird
      // and creates false positives. To make this work in legacy mode, we'd
      // need to mark fibers that commit in an incomplete state, somehow. For
      // now I'll disable the warning that most of the bugs that would trigger
      // it are either exclusive to concurrent mode or exist in both.
      (e.mode & yt) !== je && E("Internal React error: Expected static flag was missing. Please notify the React team."), $m = !1, y)
        throw new Error("Rendered fewer hooks than expected. This may be caused by an accidental early return statement.");
      return d;
    }
    function od() {
      var e = ev !== 0;
      return ev = 0, e;
    }
    function U_(e, t, a) {
      t.updateQueue = e.updateQueue, (t.mode & Pt) !== je ? t.flags &= -50333701 : t.flags &= -2053, e.lanes = Qs(e.lanes, a);
    }
    function j_() {
      if (Se.current = ry, $m) {
        for (var e = an.memoizedState; e !== null; ) {
          var t = e.queue;
          t !== null && (t.pending = null), e = e.next;
        }
        $m = !1;
      }
      Cc = X, an = null, xr = null, Tr = null, Qi = null, rs = -1, G = null, q_ = !1, Jp = !1, ev = 0;
    }
    function iu() {
      var e = {
        memoizedState: null,
        baseState: null,
        baseQueue: null,
        queue: null,
        next: null
      };
      return Tr === null ? an.memoizedState = Tr = e : Tr = Tr.next = e, Tr;
    }
    function Zi() {
      var e;
      if (xr === null) {
        var t = an.alternate;
        t !== null ? e = t.memoizedState : e = null;
      } else
        e = xr.next;
      var a;
      if (Tr === null ? a = an.memoizedState : a = Tr.next, a !== null)
        Tr = a, a = Tr.next, xr = e;
      else {
        if (e === null)
          throw new Error("Rendered more hooks than during the previous render.");
        xr = e;
        var i = {
          memoizedState: xr.memoizedState,
          baseState: xr.baseState,
          baseQueue: xr.baseQueue,
          queue: xr.queue,
          next: null
        };
        Tr === null ? an.memoizedState = Tr = i : Tr = Tr.next = i;
      }
      return Tr;
    }
    function F_() {
      return {
        lastEffect: null,
        stores: null
      };
    }
    function TS(e, t) {
      return typeof t == "function" ? t(e) : t;
    }
    function RS(e, t, a) {
      var i = iu(), u;
      a !== void 0 ? u = a(t) : u = t, i.memoizedState = i.baseState = u;
      var s = {
        pending: null,
        interleaved: null,
        lanes: X,
        dispatch: null,
        lastRenderedReducer: e,
        lastRenderedState: u
      };
      i.queue = s;
      var d = s.dispatch = gb.bind(null, an, s);
      return [i.memoizedState, d];
    }
    function wS(e, t, a) {
      var i = Zi(), u = i.queue;
      if (u === null)
        throw new Error("Should have a queue. This is likely a bug in React. Please file an issue.");
      u.lastRenderedReducer = e;
      var s = xr, d = s.baseQueue, m = u.pending;
      if (m !== null) {
        if (d !== null) {
          var y = d.next, x = m.next;
          d.next = x, m.next = y;
        }
        s.baseQueue !== d && E("Internal error: Expected work-in-progress queue to be a clone. This is a bug in React."), s.baseQueue = d = m, u.pending = null;
      }
      if (d !== null) {
        var R = d.next, M = s.baseState, O = null, H = null, B = null, W = R;
        do {
          var he = W.lane;
          if ($u(Cc, he)) {
            if (B !== null) {
              var Me = {
                // This update is going to be committed so we never want uncommit
                // it. Using NoLane works because 0 is a subset of all bitmasks, so
                // this will never be skipped by the check above.
                lane: jt,
                action: W.action,
                hasEagerState: W.hasEagerState,
                eagerState: W.eagerState,
                next: null
              };
              B = B.next = Me;
            }
            if (W.hasEagerState)
              M = W.eagerState;
            else {
              var Nt = W.action;
              M = e(M, Nt);
            }
          } else {
            var Pe = {
              lane: he,
              action: W.action,
              hasEagerState: W.hasEagerState,
              eagerState: W.eagerState,
              next: null
            };
            B === null ? (H = B = Pe, O = M) : B = B.next = Pe, an.lanes = ut(an.lanes, he), Cv(he);
          }
          W = W.next;
        } while (W !== null && W !== R);
        B === null ? O = M : B.next = H, ee(M, i.memoizedState) || sv(), i.memoizedState = M, i.baseState = O, i.baseQueue = B, u.lastRenderedState = M;
      }
      var Rt = u.interleaved;
      if (Rt !== null) {
        var U = Rt;
        do {
          var Q = U.lane;
          an.lanes = ut(an.lanes, Q), Cv(Q), U = U.next;
        } while (U !== Rt);
      } else d === null && (u.lanes = X);
      var j = u.dispatch;
      return [i.memoizedState, j];
    }
    function bS(e, t, a) {
      var i = Zi(), u = i.queue;
      if (u === null)
        throw new Error("Should have a queue. This is likely a bug in React. Please file an issue.");
      u.lastRenderedReducer = e;
      var s = u.dispatch, d = u.pending, m = i.memoizedState;
      if (d !== null) {
        u.pending = null;
        var y = d.next, x = y;
        do {
          var R = x.action;
          m = e(m, R), x = x.next;
        } while (x !== y);
        ee(m, i.memoizedState) || sv(), i.memoizedState = m, i.baseQueue === null && (i.baseState = m), u.lastRenderedState = m;
      }
      return [m, s];
    }
    function kO(e, t, a) {
    }
    function DO(e, t, a) {
    }
    function kS(e, t, a) {
      var i = an, u = iu(), s, d = Qr();
      if (d) {
        if (a === void 0)
          throw new Error("Missing getServerSnapshot, which is required for server-rendered content. Will revert to client rendering.");
        s = a(), id || s !== a() && (E("The result of getServerSnapshot should be cached to avoid an infinite loop"), id = !0);
      } else {
        if (s = t(), !id) {
          var m = t();
          ee(s, m) || (E("The result of getSnapshot should be cached to avoid an infinite loop"), id = !0);
        }
        var y = _y();
        if (y === null)
          throw new Error("Expected a work-in-progress root. This is a bug in React. Please file an issue.");
        Rf(y, Cc) || H_(i, t, s);
      }
      u.memoizedState = s;
      var x = {
        value: s,
        getSnapshot: t
      };
      return u.queue = x, Gm(P_.bind(null, i, x, e), [e]), i.flags |= aa, tv(Cr | Zr, V_.bind(null, i, x, s, t), void 0, null), s;
    }
    function Ym(e, t, a) {
      var i = an, u = Zi(), s = t();
      if (!id) {
        var d = t();
        ee(s, d) || (E("The result of getSnapshot should be cached to avoid an infinite loop"), id = !0);
      }
      var m = u.memoizedState, y = !ee(m, s);
      y && (u.memoizedState = s, sv());
      var x = u.queue;
      if (rv(P_.bind(null, i, x, e), [e]), x.getSnapshot !== t || y || // Check if the susbcribe function changed. We can save some memory by
      // checking whether we scheduled a subscription effect above.
      Tr !== null && Tr.memoizedState.tag & Cr) {
        i.flags |= aa, tv(Cr | Zr, V_.bind(null, i, x, s, t), void 0, null);
        var R = _y();
        if (R === null)
          throw new Error("Expected a work-in-progress root. This is a bug in React. Please file an issue.");
        Rf(R, Cc) || H_(i, t, s);
      }
      return s;
    }
    function H_(e, t, a) {
      e.flags |= Ao;
      var i = {
        getSnapshot: t,
        value: a
      }, u = an.updateQueue;
      if (u === null)
        u = F_(), an.updateQueue = u, u.stores = [i];
      else {
        var s = u.stores;
        s === null ? u.stores = [i] : s.push(i);
      }
    }
    function V_(e, t, a, i) {
      t.value = a, t.getSnapshot = i, B_(t) && I_(e);
    }
    function P_(e, t, a) {
      var i = function() {
        B_(t) && I_(e);
      };
      return a(i);
    }
    function B_(e) {
      var t = e.getSnapshot, a = e.value;
      try {
        var i = t();
        return !ee(a, i);
      } catch {
        return !0;
      }
    }
    function I_(e) {
      var t = qa(e, We);
      t !== null && kr(t, e, We, un);
    }
    function Wm(e) {
      var t = iu();
      typeof e == "function" && (e = e()), t.memoizedState = t.baseState = e;
      var a = {
        pending: null,
        interleaved: null,
        lanes: X,
        dispatch: null,
        lastRenderedReducer: TS,
        lastRenderedState: e
      };
      t.queue = a;
      var i = a.dispatch = Sb.bind(null, an, a);
      return [t.memoizedState, i];
    }
    function DS(e) {
      return wS(TS);
    }
    function OS(e) {
      return bS(TS);
    }
    function tv(e, t, a, i) {
      var u = {
        tag: e,
        create: t,
        destroy: a,
        deps: i,
        // Circular
        next: null
      }, s = an.updateQueue;
      if (s === null)
        s = F_(), an.updateQueue = s, s.lastEffect = u.next = u;
      else {
        var d = s.lastEffect;
        if (d === null)
          s.lastEffect = u.next = u;
        else {
          var m = d.next;
          d.next = u, u.next = m, s.lastEffect = u;
        }
      }
      return u;
    }
    function NS(e) {
      var t = iu();
      {
        var a = {
          current: e
        };
        return t.memoizedState = a, a;
      }
    }
    function Qm(e) {
      var t = Zi();
      return t.memoizedState;
    }
    function nv(e, t, a, i) {
      var u = iu(), s = i === void 0 ? null : i;
      an.flags |= e, u.memoizedState = tv(Cr | t, a, void 0, s);
    }
    function Zm(e, t, a, i) {
      var u = Zi(), s = i === void 0 ? null : i, d = void 0;
      if (xr !== null) {
        var m = xr.memoizedState;
        if (d = m.destroy, s !== null) {
          var y = m.deps;
          if (xS(s, y)) {
            u.memoizedState = tv(t, a, d, s);
            return;
          }
        }
      }
      an.flags |= e, u.memoizedState = tv(Cr | t, a, d, s);
    }
    function Gm(e, t) {
      return (an.mode & Pt) !== je ? nv(zi | aa | Xc, Zr, e, t) : nv(aa | Xc, Zr, e, t);
    }
    function rv(e, t) {
      return Zm(aa, Zr, e, t);
    }
    function MS(e, t) {
      return nv(kt, au, e, t);
    }
    function qm(e, t) {
      return Zm(kt, au, e, t);
    }
    function LS(e, t) {
      var a = kt;
      return a |= ll, (an.mode & Pt) !== je && (a |= Vl), nv(a, _r, e, t);
    }
    function Xm(e, t) {
      return Zm(kt, _r, e, t);
    }
    function $_(e, t) {
      if (typeof t == "function") {
        var a = t, i = e();
        return a(i), function() {
          a(null);
        };
      } else if (t != null) {
        var u = t;
        u.hasOwnProperty("current") || E("Expected useImperativeHandle() first argument to either be a ref callback or React.createRef() object. Instead received: %s.", "an object with keys {" + Object.keys(u).join(", ") + "}");
        var s = e();
        return u.current = s, function() {
          u.current = null;
        };
      }
    }
    function AS(e, t, a) {
      typeof t != "function" && E("Expected useImperativeHandle() second argument to be a function that creates a handle. Instead received: %s.", t !== null ? typeof t : "null");
      var i = a != null ? a.concat([e]) : null, u = kt;
      return u |= ll, (an.mode & Pt) !== je && (u |= Vl), nv(u, _r, $_.bind(null, t, e), i);
    }
    function Km(e, t, a) {
      typeof t != "function" && E("Expected useImperativeHandle() second argument to be a function that creates a handle. Instead received: %s.", t !== null ? typeof t : "null");
      var i = a != null ? a.concat([e]) : null;
      return Zm(kt, _r, $_.bind(null, t, e), i);
    }
    function hb(e, t) {
    }
    var Jm = hb;
    function zS(e, t) {
      var a = iu(), i = t === void 0 ? null : t;
      return a.memoizedState = [e, i], e;
    }
    function ey(e, t) {
      var a = Zi(), i = t === void 0 ? null : t, u = a.memoizedState;
      if (u !== null && i !== null) {
        var s = u[1];
        if (xS(i, s))
          return u[0];
      }
      return a.memoizedState = [e, i], e;
    }
    function US(e, t) {
      var a = iu(), i = t === void 0 ? null : t, u = e();
      return a.memoizedState = [u, i], u;
    }
    function ty(e, t) {
      var a = Zi(), i = t === void 0 ? null : t, u = a.memoizedState;
      if (u !== null && i !== null) {
        var s = u[1];
        if (xS(i, s))
          return u[0];
      }
      var d = e();
      return a.memoizedState = [d, i], d;
    }
    function jS(e) {
      var t = iu();
      return t.memoizedState = e, e;
    }
    function Y_(e) {
      var t = Zi(), a = xr, i = a.memoizedState;
      return Q_(t, i, e);
    }
    function W_(e) {
      var t = Zi();
      if (xr === null)
        return t.memoizedState = e, e;
      var a = xr.memoizedState;
      return Q_(t, a, e);
    }
    function Q_(e, t, a) {
      var i = !ip(Cc);
      if (i) {
        if (!ee(a, t)) {
          var u = op();
          an.lanes = ut(an.lanes, u), Cv(u), e.baseState = !0;
        }
        return t;
      } else
        return e.baseState && (e.baseState = !1, sv()), e.memoizedState = a, a;
    }
    function mb(e, t, a) {
      var i = Qa();
      Wn(Mh(i, Hi)), e(!0);
      var u = Kp.transition;
      Kp.transition = {};
      var s = Kp.transition;
      Kp.transition._updatedFibers = /* @__PURE__ */ new Set();
      try {
        e(!1), t();
      } finally {
        if (Wn(i), Kp.transition = u, u === null && s._updatedFibers) {
          var d = s._updatedFibers.size;
          d > 10 && T("Detected a large number of updates inside startTransition. If this is due to a subscription please re-write it to use React provided hooks. Otherwise concurrent mode guarantees are off the table."), s._updatedFibers.clear();
        }
      }
    }
    function FS() {
      var e = Wm(!1), t = e[0], a = e[1], i = mb.bind(null, a), u = iu();
      return u.memoizedState = i, [t, i];
    }
    function Z_() {
      var e = DS(), t = e[0], a = Zi(), i = a.memoizedState;
      return [t, i];
    }
    function G_() {
      var e = OS(), t = e[0], a = Zi(), i = a.memoizedState;
      return [t, i];
    }
    var q_ = !1;
    function yb() {
      return q_;
    }
    function HS() {
      var e = iu(), t = _y(), a = t.identifierPrefix, i;
      if (Qr()) {
        var u = Aw();
        i = ":" + a + "R" + u;
        var s = ev++;
        s > 0 && (i += "H" + s.toString(32)), i += ":";
      } else {
        var d = db++;
        i = ":" + a + "r" + d.toString(32) + ":";
      }
      return e.memoizedState = i, i;
    }
    function ny() {
      var e = Zi(), t = e.memoizedState;
      return t;
    }
    function gb(e, t, a) {
      typeof arguments[3] == "function" && E("State updates from the useState() and useReducer() Hooks don't support the second callback argument. To execute a side effect after rendering, declare it in the component body with useEffect().");
      var i = os(e), u = {
        lane: i,
        action: a,
        hasEagerState: !1,
        eagerState: null,
        next: null
      };
      if (X_(e))
        K_(t, u);
      else {
        var s = w_(e, t, u, i);
        if (s !== null) {
          var d = Na();
          kr(s, e, i, d), J_(s, t, i);
        }
      }
      e0(e, i);
    }
    function Sb(e, t, a) {
      typeof arguments[3] == "function" && E("State updates from the useState() and useReducer() Hooks don't support the second callback argument. To execute a side effect after rendering, declare it in the component body with useEffect().");
      var i = os(e), u = {
        lane: i,
        action: a,
        hasEagerState: !1,
        eagerState: null,
        next: null
      };
      if (X_(e))
        K_(t, u);
      else {
        var s = e.alternate;
        if (e.lanes === X && (s === null || s.lanes === X)) {
          var d = t.lastRenderedReducer;
          if (d !== null) {
            var m;
            m = Se.current, Se.current = Sl;
            try {
              var y = t.lastRenderedState, x = d(y, a);
              if (u.hasEagerState = !0, u.eagerState = x, ee(x, y)) {
                rb(e, t, u, i);
                return;
              }
            } catch {
            } finally {
              Se.current = m;
            }
          }
        }
        var R = w_(e, t, u, i);
        if (R !== null) {
          var M = Na();
          kr(R, e, i, M), J_(R, t, i);
        }
      }
      e0(e, i);
    }
    function X_(e) {
      var t = e.alternate;
      return e === an || t !== null && t === an;
    }
    function K_(e, t) {
      Jp = $m = !0;
      var a = e.pending;
      a === null ? t.next = t : (t.next = a.next, a.next = t), e.pending = t;
    }
    function J_(e, t, a) {
      if (up(a)) {
        var i = t.lanes;
        i = sp(i, e.pendingLanes);
        var u = ut(i, a);
        t.lanes = u, bf(e, u);
      }
    }
    function e0(e, t, a) {
      js(e, t);
    }
    var ry = {
      readContext: fr,
      useCallback: ma,
      useContext: ma,
      useEffect: ma,
      useImperativeHandle: ma,
      useInsertionEffect: ma,
      useLayoutEffect: ma,
      useMemo: ma,
      useReducer: ma,
      useRef: ma,
      useState: ma,
      useDebugValue: ma,
      useDeferredValue: ma,
      useTransition: ma,
      useMutableSource: ma,
      useSyncExternalStore: ma,
      useId: ma,
      unstable_isNewReconciler: le
    }, t0 = null, n0 = null, r0 = null, a0 = null, lu = null, Sl = null, ay = null;
    {
      var VS = function() {
        E("Context can only be read while React is rendering. In classes, you can read it in the render method or getDerivedStateFromProps. In function components, you can read it directly in the function body, but not inside Hooks like useReducer() or useMemo().");
      }, Je = function() {
        E("Do not call Hooks inside useEffect(...), useMemo(...), or other built-in Hooks. You can only call Hooks at the top level of your React function. For more information, see https://reactjs.org/link/rules-of-hooks");
      };
      t0 = {
        readContext: function(e) {
          return fr(e);
        },
        useCallback: function(e, t) {
          return G = "useCallback", qt(), ld(t), zS(e, t);
        },
        useContext: function(e) {
          return G = "useContext", qt(), fr(e);
        },
        useEffect: function(e, t) {
          return G = "useEffect", qt(), ld(t), Gm(e, t);
        },
        useImperativeHandle: function(e, t, a) {
          return G = "useImperativeHandle", qt(), ld(a), AS(e, t, a);
        },
        useInsertionEffect: function(e, t) {
          return G = "useInsertionEffect", qt(), ld(t), MS(e, t);
        },
        useLayoutEffect: function(e, t) {
          return G = "useLayoutEffect", qt(), ld(t), LS(e, t);
        },
        useMemo: function(e, t) {
          G = "useMemo", qt(), ld(t);
          var a = Se.current;
          Se.current = lu;
          try {
            return US(e, t);
          } finally {
            Se.current = a;
          }
        },
        useReducer: function(e, t, a) {
          G = "useReducer", qt();
          var i = Se.current;
          Se.current = lu;
          try {
            return RS(e, t, a);
          } finally {
            Se.current = i;
          }
        },
        useRef: function(e) {
          return G = "useRef", qt(), NS(e);
        },
        useState: function(e) {
          G = "useState", qt();
          var t = Se.current;
          Se.current = lu;
          try {
            return Wm(e);
          } finally {
            Se.current = t;
          }
        },
        useDebugValue: function(e, t) {
          return G = "useDebugValue", qt(), void 0;
        },
        useDeferredValue: function(e) {
          return G = "useDeferredValue", qt(), jS(e);
        },
        useTransition: function() {
          return G = "useTransition", qt(), FS();
        },
        useMutableSource: function(e, t, a) {
          return G = "useMutableSource", qt(), void 0;
        },
        useSyncExternalStore: function(e, t, a) {
          return G = "useSyncExternalStore", qt(), kS(e, t, a);
        },
        useId: function() {
          return G = "useId", qt(), HS();
        },
        unstable_isNewReconciler: le
      }, n0 = {
        readContext: function(e) {
          return fr(e);
        },
        useCallback: function(e, t) {
          return G = "useCallback", oe(), zS(e, t);
        },
        useContext: function(e) {
          return G = "useContext", oe(), fr(e);
        },
        useEffect: function(e, t) {
          return G = "useEffect", oe(), Gm(e, t);
        },
        useImperativeHandle: function(e, t, a) {
          return G = "useImperativeHandle", oe(), AS(e, t, a);
        },
        useInsertionEffect: function(e, t) {
          return G = "useInsertionEffect", oe(), MS(e, t);
        },
        useLayoutEffect: function(e, t) {
          return G = "useLayoutEffect", oe(), LS(e, t);
        },
        useMemo: function(e, t) {
          G = "useMemo", oe();
          var a = Se.current;
          Se.current = lu;
          try {
            return US(e, t);
          } finally {
            Se.current = a;
          }
        },
        useReducer: function(e, t, a) {
          G = "useReducer", oe();
          var i = Se.current;
          Se.current = lu;
          try {
            return RS(e, t, a);
          } finally {
            Se.current = i;
          }
        },
        useRef: function(e) {
          return G = "useRef", oe(), NS(e);
        },
        useState: function(e) {
          G = "useState", oe();
          var t = Se.current;
          Se.current = lu;
          try {
            return Wm(e);
          } finally {
            Se.current = t;
          }
        },
        useDebugValue: function(e, t) {
          return G = "useDebugValue", oe(), void 0;
        },
        useDeferredValue: function(e) {
          return G = "useDeferredValue", oe(), jS(e);
        },
        useTransition: function() {
          return G = "useTransition", oe(), FS();
        },
        useMutableSource: function(e, t, a) {
          return G = "useMutableSource", oe(), void 0;
        },
        useSyncExternalStore: function(e, t, a) {
          return G = "useSyncExternalStore", oe(), kS(e, t, a);
        },
        useId: function() {
          return G = "useId", oe(), HS();
        },
        unstable_isNewReconciler: le
      }, r0 = {
        readContext: function(e) {
          return fr(e);
        },
        useCallback: function(e, t) {
          return G = "useCallback", oe(), ey(e, t);
        },
        useContext: function(e) {
          return G = "useContext", oe(), fr(e);
        },
        useEffect: function(e, t) {
          return G = "useEffect", oe(), rv(e, t);
        },
        useImperativeHandle: function(e, t, a) {
          return G = "useImperativeHandle", oe(), Km(e, t, a);
        },
        useInsertionEffect: function(e, t) {
          return G = "useInsertionEffect", oe(), qm(e, t);
        },
        useLayoutEffect: function(e, t) {
          return G = "useLayoutEffect", oe(), Xm(e, t);
        },
        useMemo: function(e, t) {
          G = "useMemo", oe();
          var a = Se.current;
          Se.current = Sl;
          try {
            return ty(e, t);
          } finally {
            Se.current = a;
          }
        },
        useReducer: function(e, t, a) {
          G = "useReducer", oe();
          var i = Se.current;
          Se.current = Sl;
          try {
            return wS(e, t, a);
          } finally {
            Se.current = i;
          }
        },
        useRef: function(e) {
          return G = "useRef", oe(), Qm();
        },
        useState: function(e) {
          G = "useState", oe();
          var t = Se.current;
          Se.current = Sl;
          try {
            return DS(e);
          } finally {
            Se.current = t;
          }
        },
        useDebugValue: function(e, t) {
          return G = "useDebugValue", oe(), Jm();
        },
        useDeferredValue: function(e) {
          return G = "useDeferredValue", oe(), Y_(e);
        },
        useTransition: function() {
          return G = "useTransition", oe(), Z_();
        },
        useMutableSource: function(e, t, a) {
          return G = "useMutableSource", oe(), void 0;
        },
        useSyncExternalStore: function(e, t, a) {
          return G = "useSyncExternalStore", oe(), Ym(e, t);
        },
        useId: function() {
          return G = "useId", oe(), ny();
        },
        unstable_isNewReconciler: le
      }, a0 = {
        readContext: function(e) {
          return fr(e);
        },
        useCallback: function(e, t) {
          return G = "useCallback", oe(), ey(e, t);
        },
        useContext: function(e) {
          return G = "useContext", oe(), fr(e);
        },
        useEffect: function(e, t) {
          return G = "useEffect", oe(), rv(e, t);
        },
        useImperativeHandle: function(e, t, a) {
          return G = "useImperativeHandle", oe(), Km(e, t, a);
        },
        useInsertionEffect: function(e, t) {
          return G = "useInsertionEffect", oe(), qm(e, t);
        },
        useLayoutEffect: function(e, t) {
          return G = "useLayoutEffect", oe(), Xm(e, t);
        },
        useMemo: function(e, t) {
          G = "useMemo", oe();
          var a = Se.current;
          Se.current = ay;
          try {
            return ty(e, t);
          } finally {
            Se.current = a;
          }
        },
        useReducer: function(e, t, a) {
          G = "useReducer", oe();
          var i = Se.current;
          Se.current = ay;
          try {
            return bS(e, t, a);
          } finally {
            Se.current = i;
          }
        },
        useRef: function(e) {
          return G = "useRef", oe(), Qm();
        },
        useState: function(e) {
          G = "useState", oe();
          var t = Se.current;
          Se.current = ay;
          try {
            return OS(e);
          } finally {
            Se.current = t;
          }
        },
        useDebugValue: function(e, t) {
          return G = "useDebugValue", oe(), Jm();
        },
        useDeferredValue: function(e) {
          return G = "useDeferredValue", oe(), W_(e);
        },
        useTransition: function() {
          return G = "useTransition", oe(), G_();
        },
        useMutableSource: function(e, t, a) {
          return G = "useMutableSource", oe(), void 0;
        },
        useSyncExternalStore: function(e, t, a) {
          return G = "useSyncExternalStore", oe(), Ym(e, t);
        },
        useId: function() {
          return G = "useId", oe(), ny();
        },
        unstable_isNewReconciler: le
      }, lu = {
        readContext: function(e) {
          return VS(), fr(e);
        },
        useCallback: function(e, t) {
          return G = "useCallback", Je(), qt(), zS(e, t);
        },
        useContext: function(e) {
          return G = "useContext", Je(), qt(), fr(e);
        },
        useEffect: function(e, t) {
          return G = "useEffect", Je(), qt(), Gm(e, t);
        },
        useImperativeHandle: function(e, t, a) {
          return G = "useImperativeHandle", Je(), qt(), AS(e, t, a);
        },
        useInsertionEffect: function(e, t) {
          return G = "useInsertionEffect", Je(), qt(), MS(e, t);
        },
        useLayoutEffect: function(e, t) {
          return G = "useLayoutEffect", Je(), qt(), LS(e, t);
        },
        useMemo: function(e, t) {
          G = "useMemo", Je(), qt();
          var a = Se.current;
          Se.current = lu;
          try {
            return US(e, t);
          } finally {
            Se.current = a;
          }
        },
        useReducer: function(e, t, a) {
          G = "useReducer", Je(), qt();
          var i = Se.current;
          Se.current = lu;
          try {
            return RS(e, t, a);
          } finally {
            Se.current = i;
          }
        },
        useRef: function(e) {
          return G = "useRef", Je(), qt(), NS(e);
        },
        useState: function(e) {
          G = "useState", Je(), qt();
          var t = Se.current;
          Se.current = lu;
          try {
            return Wm(e);
          } finally {
            Se.current = t;
          }
        },
        useDebugValue: function(e, t) {
          return G = "useDebugValue", Je(), qt(), void 0;
        },
        useDeferredValue: function(e) {
          return G = "useDeferredValue", Je(), qt(), jS(e);
        },
        useTransition: function() {
          return G = "useTransition", Je(), qt(), FS();
        },
        useMutableSource: function(e, t, a) {
          return G = "useMutableSource", Je(), qt(), void 0;
        },
        useSyncExternalStore: function(e, t, a) {
          return G = "useSyncExternalStore", Je(), qt(), kS(e, t, a);
        },
        useId: function() {
          return G = "useId", Je(), qt(), HS();
        },
        unstable_isNewReconciler: le
      }, Sl = {
        readContext: function(e) {
          return VS(), fr(e);
        },
        useCallback: function(e, t) {
          return G = "useCallback", Je(), oe(), ey(e, t);
        },
        useContext: function(e) {
          return G = "useContext", Je(), oe(), fr(e);
        },
        useEffect: function(e, t) {
          return G = "useEffect", Je(), oe(), rv(e, t);
        },
        useImperativeHandle: function(e, t, a) {
          return G = "useImperativeHandle", Je(), oe(), Km(e, t, a);
        },
        useInsertionEffect: function(e, t) {
          return G = "useInsertionEffect", Je(), oe(), qm(e, t);
        },
        useLayoutEffect: function(e, t) {
          return G = "useLayoutEffect", Je(), oe(), Xm(e, t);
        },
        useMemo: function(e, t) {
          G = "useMemo", Je(), oe();
          var a = Se.current;
          Se.current = Sl;
          try {
            return ty(e, t);
          } finally {
            Se.current = a;
          }
        },
        useReducer: function(e, t, a) {
          G = "useReducer", Je(), oe();
          var i = Se.current;
          Se.current = Sl;
          try {
            return wS(e, t, a);
          } finally {
            Se.current = i;
          }
        },
        useRef: function(e) {
          return G = "useRef", Je(), oe(), Qm();
        },
        useState: function(e) {
          G = "useState", Je(), oe();
          var t = Se.current;
          Se.current = Sl;
          try {
            return DS(e);
          } finally {
            Se.current = t;
          }
        },
        useDebugValue: function(e, t) {
          return G = "useDebugValue", Je(), oe(), Jm();
        },
        useDeferredValue: function(e) {
          return G = "useDeferredValue", Je(), oe(), Y_(e);
        },
        useTransition: function() {
          return G = "useTransition", Je(), oe(), Z_();
        },
        useMutableSource: function(e, t, a) {
          return G = "useMutableSource", Je(), oe(), void 0;
        },
        useSyncExternalStore: function(e, t, a) {
          return G = "useSyncExternalStore", Je(), oe(), Ym(e, t);
        },
        useId: function() {
          return G = "useId", Je(), oe(), ny();
        },
        unstable_isNewReconciler: le
      }, ay = {
        readContext: function(e) {
          return VS(), fr(e);
        },
        useCallback: function(e, t) {
          return G = "useCallback", Je(), oe(), ey(e, t);
        },
        useContext: function(e) {
          return G = "useContext", Je(), oe(), fr(e);
        },
        useEffect: function(e, t) {
          return G = "useEffect", Je(), oe(), rv(e, t);
        },
        useImperativeHandle: function(e, t, a) {
          return G = "useImperativeHandle", Je(), oe(), Km(e, t, a);
        },
        useInsertionEffect: function(e, t) {
          return G = "useInsertionEffect", Je(), oe(), qm(e, t);
        },
        useLayoutEffect: function(e, t) {
          return G = "useLayoutEffect", Je(), oe(), Xm(e, t);
        },
        useMemo: function(e, t) {
          G = "useMemo", Je(), oe();
          var a = Se.current;
          Se.current = Sl;
          try {
            return ty(e, t);
          } finally {
            Se.current = a;
          }
        },
        useReducer: function(e, t, a) {
          G = "useReducer", Je(), oe();
          var i = Se.current;
          Se.current = Sl;
          try {
            return bS(e, t, a);
          } finally {
            Se.current = i;
          }
        },
        useRef: function(e) {
          return G = "useRef", Je(), oe(), Qm();
        },
        useState: function(e) {
          G = "useState", Je(), oe();
          var t = Se.current;
          Se.current = Sl;
          try {
            return OS(e);
          } finally {
            Se.current = t;
          }
        },
        useDebugValue: function(e, t) {
          return G = "useDebugValue", Je(), oe(), Jm();
        },
        useDeferredValue: function(e) {
          return G = "useDeferredValue", Je(), oe(), W_(e);
        },
        useTransition: function() {
          return G = "useTransition", Je(), oe(), G_();
        },
        useMutableSource: function(e, t, a) {
          return G = "useMutableSource", Je(), oe(), void 0;
        },
        useSyncExternalStore: function(e, t, a) {
          return G = "useSyncExternalStore", Je(), oe(), Ym(e, t);
        },
        useId: function() {
          return G = "useId", Je(), oe(), ny();
        },
        unstable_isNewReconciler: le
      };
    }
    var as = c.unstable_now, i0 = 0, iy = -1, av = -1, ly = -1, PS = !1, uy = !1;
    function l0() {
      return PS;
    }
    function Eb() {
      uy = !0;
    }
    function Cb() {
      PS = !1, uy = !1;
    }
    function _b() {
      PS = uy, uy = !1;
    }
    function u0() {
      return i0;
    }
    function o0() {
      i0 = as();
    }
    function BS(e) {
      av = as(), e.actualStartTime < 0 && (e.actualStartTime = as());
    }
    function s0(e) {
      av = -1;
    }
    function oy(e, t) {
      if (av >= 0) {
        var a = as() - av;
        e.actualDuration += a, t && (e.selfBaseDuration = a), av = -1;
      }
    }
    function uu(e) {
      if (iy >= 0) {
        var t = as() - iy;
        iy = -1;
        for (var a = e.return; a !== null; ) {
          switch (a.tag) {
            case re:
              var i = a.stateNode;
              i.effectDuration += t;
              return;
            case Tt:
              var u = a.stateNode;
              u.effectDuration += t;
              return;
          }
          a = a.return;
        }
      }
    }
    function IS(e) {
      if (ly >= 0) {
        var t = as() - ly;
        ly = -1;
        for (var a = e.return; a !== null; ) {
          switch (a.tag) {
            case re:
              var i = a.stateNode;
              i !== null && (i.passiveEffectDuration += t);
              return;
            case Tt:
              var u = a.stateNode;
              u !== null && (u.passiveEffectDuration += t);
              return;
          }
          a = a.return;
        }
      }
    }
    function ou() {
      iy = as();
    }
    function $S() {
      ly = as();
    }
    function YS(e) {
      for (var t = e.child; t; )
        e.actualDuration += t.actualDuration, t = t.sibling;
    }
    function El(e, t) {
      if (e && e.defaultProps) {
        var a = st({}, t), i = e.defaultProps;
        for (var u in i)
          a[u] === void 0 && (a[u] = i[u]);
        return a;
      }
      return t;
    }
    var WS = {}, QS, ZS, GS, qS, XS, c0, sy, KS, JS, eE, iv;
    {
      QS = /* @__PURE__ */ new Set(), ZS = /* @__PURE__ */ new Set(), GS = /* @__PURE__ */ new Set(), qS = /* @__PURE__ */ new Set(), KS = /* @__PURE__ */ new Set(), XS = /* @__PURE__ */ new Set(), JS = /* @__PURE__ */ new Set(), eE = /* @__PURE__ */ new Set(), iv = /* @__PURE__ */ new Set();
      var f0 = /* @__PURE__ */ new Set();
      sy = function(e, t) {
        if (!(e === null || typeof e == "function")) {
          var a = t + "_" + e;
          f0.has(a) || (f0.add(a), E("%s(...): Expected the last optional `callback` argument to be a function. Instead received: %s.", t, e));
        }
      }, c0 = function(e, t) {
        if (t === void 0) {
          var a = Lt(e) || "Component";
          XS.has(a) || (XS.add(a), E("%s.getDerivedStateFromProps(): A valid state object (or null) must be returned. You have returned undefined.", a));
        }
      }, Object.defineProperty(WS, "_processChildContext", {
        enumerable: !1,
        value: function() {
          throw new Error("_processChildContext is not available in React 16+. This likely means you have multiple copies of React and are attempting to nest a React 15 tree inside a React 16 tree using unstable_renderSubtreeIntoContainer, which isn't supported. Try to make sure you have only one copy of React (and ideally, switch to ReactDOM.createPortal).");
        }
      }), Object.freeze(WS);
    }
    function tE(e, t, a, i) {
      var u = e.memoizedState, s = a(i, u);
      {
        if (e.mode & rn) {
          wn(!0);
          try {
            s = a(i, u);
          } finally {
            wn(!1);
          }
        }
        c0(t, s);
      }
      var d = s == null ? u : st({}, u, s);
      if (e.memoizedState = d, e.lanes === X) {
        var m = e.updateQueue;
        m.baseState = d;
      }
    }
    var nE = {
      isMounted: mh,
      enqueueSetState: function(e, t, a) {
        var i = Lo(e), u = Na(), s = os(i), d = no(u, s);
        d.payload = t, a != null && (sy(a, "setState"), d.callback = a);
        var m = es(i, d, s);
        m !== null && (kr(m, i, s, u), Fm(m, i, s)), js(i, s);
      },
      enqueueReplaceState: function(e, t, a) {
        var i = Lo(e), u = Na(), s = os(i), d = no(u, s);
        d.tag = k_, d.payload = t, a != null && (sy(a, "replaceState"), d.callback = a);
        var m = es(i, d, s);
        m !== null && (kr(m, i, s, u), Fm(m, i, s)), js(i, s);
      },
      enqueueForceUpdate: function(e, t) {
        var a = Lo(e), i = Na(), u = os(a), s = no(i, u);
        s.tag = zm, t != null && (sy(t, "forceUpdate"), s.callback = t);
        var d = es(a, s, u);
        d !== null && (kr(d, a, u, i), Fm(d, a, u)), af(a, u);
      }
    };
    function d0(e, t, a, i, u, s, d) {
      var m = e.stateNode;
      if (typeof m.shouldComponentUpdate == "function") {
        var y = m.shouldComponentUpdate(i, s, d);
        {
          if (e.mode & rn) {
            wn(!0);
            try {
              y = m.shouldComponentUpdate(i, s, d);
            } finally {
              wn(!1);
            }
          }
          y === void 0 && E("%s.shouldComponentUpdate(): Returned undefined instead of a boolean value. Make sure to return true or false.", Lt(t) || "Component");
        }
        return y;
      }
      return t.prototype && t.prototype.isPureReactComponent ? !Re(a, i) || !Re(u, s) : !0;
    }
    function xb(e, t, a) {
      var i = e.stateNode;
      {
        var u = Lt(t) || "Component", s = i.render;
        s || (t.prototype && typeof t.prototype.render == "function" ? E("%s(...): No `render` method found on the returned component instance: did you accidentally return an object from the constructor?", u) : E("%s(...): No `render` method found on the returned component instance: you may have forgotten to define `render`.", u)), i.getInitialState && !i.getInitialState.isReactClassApproved && !i.state && E("getInitialState was defined on %s, a plain JavaScript class. This is only supported for classes created using React.createClass. Did you mean to define a state property instead?", u), i.getDefaultProps && !i.getDefaultProps.isReactClassApproved && E("getDefaultProps was defined on %s, a plain JavaScript class. This is only supported for classes created using React.createClass. Use a static property to define defaultProps instead.", u), i.propTypes && E("propTypes was defined as an instance property on %s. Use a static property to define propTypes instead.", u), i.contextType && E("contextType was defined as an instance property on %s. Use a static property to define contextType instead.", u), t.childContextTypes && !iv.has(t) && // Strict Mode has its own warning for legacy context, so we can skip
        // this one.
        (e.mode & rn) === je && (iv.add(t), E(`%s uses the legacy childContextTypes API which is no longer supported and will be removed in the next major release. Use React.createContext() instead

.Learn more about this warning here: https://reactjs.org/link/legacy-context`, u)), t.contextTypes && !iv.has(t) && // Strict Mode has its own warning for legacy context, so we can skip
        // this one.
        (e.mode & rn) === je && (iv.add(t), E(`%s uses the legacy contextTypes API which is no longer supported and will be removed in the next major release. Use React.createContext() with static contextType instead.

Learn more about this warning here: https://reactjs.org/link/legacy-context`, u)), i.contextTypes && E("contextTypes was defined as an instance property on %s. Use a static property to define contextTypes instead.", u), t.contextType && t.contextTypes && !JS.has(t) && (JS.add(t), E("%s declares both contextTypes and contextType static properties. The legacy contextTypes property will be ignored.", u)), typeof i.componentShouldUpdate == "function" && E("%s has a method called componentShouldUpdate(). Did you mean shouldComponentUpdate()? The name is phrased as a question because the function is expected to return a value.", u), t.prototype && t.prototype.isPureReactComponent && typeof i.shouldComponentUpdate < "u" && E("%s has a method called shouldComponentUpdate(). shouldComponentUpdate should not be used when extending React.PureComponent. Please extend React.Component if shouldComponentUpdate is used.", Lt(t) || "A pure component"), typeof i.componentDidUnmount == "function" && E("%s has a method called componentDidUnmount(). But there is no such lifecycle method. Did you mean componentWillUnmount()?", u), typeof i.componentDidReceiveProps == "function" && E("%s has a method called componentDidReceiveProps(). But there is no such lifecycle method. If you meant to update the state in response to changing props, use componentWillReceiveProps(). If you meant to fetch data or run side-effects or mutations after React has updated the UI, use componentDidUpdate().", u), typeof i.componentWillRecieveProps == "function" && E("%s has a method called componentWillRecieveProps(). Did you mean componentWillReceiveProps()?", u), typeof i.UNSAFE_componentWillRecieveProps == "function" && E("%s has a method called UNSAFE_componentWillRecieveProps(). Did you mean UNSAFE_componentWillReceiveProps()?", u);
        var d = i.props !== a;
        i.props !== void 0 && d && E("%s(...): When calling super() in `%s`, make sure to pass up the same props that your component's constructor was passed.", u, u), i.defaultProps && E("Setting defaultProps as an instance property on %s is not supported and will be ignored. Instead, define defaultProps as a static property on %s.", u, u), typeof i.getSnapshotBeforeUpdate == "function" && typeof i.componentDidUpdate != "function" && !GS.has(t) && (GS.add(t), E("%s: getSnapshotBeforeUpdate() should be used with componentDidUpdate(). This component defines getSnapshotBeforeUpdate() only.", Lt(t))), typeof i.getDerivedStateFromProps == "function" && E("%s: getDerivedStateFromProps() is defined as an instance method and will be ignored. Instead, declare it as a static method.", u), typeof i.getDerivedStateFromError == "function" && E("%s: getDerivedStateFromError() is defined as an instance method and will be ignored. Instead, declare it as a static method.", u), typeof t.getSnapshotBeforeUpdate == "function" && E("%s: getSnapshotBeforeUpdate() is defined as a static method and will be ignored. Instead, declare it as an instance method.", u);
        var m = i.state;
        m && (typeof m != "object" || pt(m)) && E("%s.state: must be set to an object or null", u), typeof i.getChildContext == "function" && typeof t.childContextTypes != "object" && E("%s.getChildContext(): childContextTypes must be defined in order to use getChildContext().", u);
      }
    }
    function p0(e, t) {
      t.updater = nE, e.stateNode = t, Ou(t, e), t._reactInternalInstance = WS;
    }
    function v0(e, t, a) {
      var i = !1, u = Si, s = Si, d = t.contextType;
      if ("contextType" in t) {
        var m = (
          // Allow null for conditional declaration
          d === null || d !== void 0 && d.$$typeof === k && d._context === void 0
        );
        if (!m && !eE.has(t)) {
          eE.add(t);
          var y = "";
          d === void 0 ? y = " However, it is set to undefined. This can be caused by a typo or by mixing up named and default imports. This can also happen due to a circular dependency, so try moving the createContext() call to a separate file." : typeof d != "object" ? y = " However, it is set to a " + typeof d + "." : d.$$typeof === wi ? y = " Did you accidentally pass the Context.Provider instead?" : d._context !== void 0 ? y = " Did you accidentally pass the Context.Consumer instead?" : y = " However, it is set to an object with keys {" + Object.keys(d).join(", ") + "}.", E("%s defines an invalid contextType. contextType should point to the Context object returned by React.createContext().%s", Lt(t) || "Component", y);
        }
      }
      if (typeof d == "object" && d !== null)
        s = fr(d);
      else {
        u = Zf(e, t, !0);
        var x = t.contextTypes;
        i = x != null, s = i ? Gf(e, u) : Si;
      }
      var R = new t(a, s);
      if (e.mode & rn) {
        wn(!0);
        try {
          R = new t(a, s);
        } finally {
          wn(!1);
        }
      }
      var M = e.memoizedState = R.state !== null && R.state !== void 0 ? R.state : null;
      p0(e, R);
      {
        if (typeof t.getDerivedStateFromProps == "function" && M === null) {
          var O = Lt(t) || "Component";
          ZS.has(O) || (ZS.add(O), E("`%s` uses `getDerivedStateFromProps` but its initial state is %s. This is not recommended. Instead, define the initial state by assigning an object to `this.state` in the constructor of `%s`. This ensures that `getDerivedStateFromProps` arguments have a consistent shape.", O, R.state === null ? "null" : "undefined", O));
        }
        if (typeof t.getDerivedStateFromProps == "function" || typeof R.getSnapshotBeforeUpdate == "function") {
          var H = null, B = null, W = null;
          if (typeof R.componentWillMount == "function" && R.componentWillMount.__suppressDeprecationWarning !== !0 ? H = "componentWillMount" : typeof R.UNSAFE_componentWillMount == "function" && (H = "UNSAFE_componentWillMount"), typeof R.componentWillReceiveProps == "function" && R.componentWillReceiveProps.__suppressDeprecationWarning !== !0 ? B = "componentWillReceiveProps" : typeof R.UNSAFE_componentWillReceiveProps == "function" && (B = "UNSAFE_componentWillReceiveProps"), typeof R.componentWillUpdate == "function" && R.componentWillUpdate.__suppressDeprecationWarning !== !0 ? W = "componentWillUpdate" : typeof R.UNSAFE_componentWillUpdate == "function" && (W = "UNSAFE_componentWillUpdate"), H !== null || B !== null || W !== null) {
            var he = Lt(t) || "Component", Pe = typeof t.getDerivedStateFromProps == "function" ? "getDerivedStateFromProps()" : "getSnapshotBeforeUpdate()";
            qS.has(he) || (qS.add(he), E(`Unsafe legacy lifecycles will not be called for components using new component APIs.

%s uses %s but also contains the following legacy lifecycles:%s%s%s

The above lifecycles should be removed. Learn more about this warning here:
https://reactjs.org/link/unsafe-component-lifecycles`, he, Pe, H !== null ? `
  ` + H : "", B !== null ? `
  ` + B : "", W !== null ? `
  ` + W : ""));
          }
        }
      }
      return i && r_(e, u, s), R;
    }
    function Tb(e, t) {
      var a = t.state;
      typeof t.componentWillMount == "function" && t.componentWillMount(), typeof t.UNSAFE_componentWillMount == "function" && t.UNSAFE_componentWillMount(), a !== t.state && (E("%s.componentWillMount(): Assigning directly to this.state is deprecated (except inside a component's constructor). Use setState instead.", Xe(e) || "Component"), nE.enqueueReplaceState(t, t.state, null));
    }
    function h0(e, t, a, i) {
      var u = t.state;
      if (typeof t.componentWillReceiveProps == "function" && t.componentWillReceiveProps(a, i), typeof t.UNSAFE_componentWillReceiveProps == "function" && t.UNSAFE_componentWillReceiveProps(a, i), t.state !== u) {
        {
          var s = Xe(e) || "Component";
          QS.has(s) || (QS.add(s), E("%s.componentWillReceiveProps(): Assigning directly to this.state is deprecated (except inside a component's constructor). Use setState instead.", s));
        }
        nE.enqueueReplaceState(t, t.state, null);
      }
    }
    function rE(e, t, a, i) {
      xb(e, t, a);
      var u = e.stateNode;
      u.props = a, u.state = e.memoizedState, u.refs = {}, dS(e);
      var s = t.contextType;
      if (typeof s == "object" && s !== null)
        u.context = fr(s);
      else {
        var d = Zf(e, t, !0);
        u.context = Gf(e, d);
      }
      {
        if (u.state === a) {
          var m = Lt(t) || "Component";
          KS.has(m) || (KS.add(m), E("%s: It is not recommended to assign props directly to state because updates to props won't be reflected in state. In most cases, it is better to use props directly.", m));
        }
        e.mode & rn && yl.recordLegacyContextWarning(e, u), yl.recordUnsafeLifecycleWarnings(e, u);
      }
      u.state = e.memoizedState;
      var y = t.getDerivedStateFromProps;
      if (typeof y == "function" && (tE(e, t, y, a), u.state = e.memoizedState), typeof t.getDerivedStateFromProps != "function" && typeof u.getSnapshotBeforeUpdate != "function" && (typeof u.UNSAFE_componentWillMount == "function" || typeof u.componentWillMount == "function") && (Tb(e, u), Hm(e, a, u, i), u.state = e.memoizedState), typeof u.componentDidMount == "function") {
        var x = kt;
        x |= ll, (e.mode & Pt) !== je && (x |= Vl), e.flags |= x;
      }
    }
    function Rb(e, t, a, i) {
      var u = e.stateNode, s = e.memoizedProps;
      u.props = s;
      var d = u.context, m = t.contextType, y = Si;
      if (typeof m == "object" && m !== null)
        y = fr(m);
      else {
        var x = Zf(e, t, !0);
        y = Gf(e, x);
      }
      var R = t.getDerivedStateFromProps, M = typeof R == "function" || typeof u.getSnapshotBeforeUpdate == "function";
      !M && (typeof u.UNSAFE_componentWillReceiveProps == "function" || typeof u.componentWillReceiveProps == "function") && (s !== a || d !== y) && h0(e, u, a, y), O_();
      var O = e.memoizedState, H = u.state = O;
      if (Hm(e, a, u, i), H = e.memoizedState, s === a && O === H && !Em() && !Vm()) {
        if (typeof u.componentDidMount == "function") {
          var B = kt;
          B |= ll, (e.mode & Pt) !== je && (B |= Vl), e.flags |= B;
        }
        return !1;
      }
      typeof R == "function" && (tE(e, t, R, a), H = e.memoizedState);
      var W = Vm() || d0(e, t, s, a, O, H, y);
      if (W) {
        if (!M && (typeof u.UNSAFE_componentWillMount == "function" || typeof u.componentWillMount == "function") && (typeof u.componentWillMount == "function" && u.componentWillMount(), typeof u.UNSAFE_componentWillMount == "function" && u.UNSAFE_componentWillMount()), typeof u.componentDidMount == "function") {
          var he = kt;
          he |= ll, (e.mode & Pt) !== je && (he |= Vl), e.flags |= he;
        }
      } else {
        if (typeof u.componentDidMount == "function") {
          var Pe = kt;
          Pe |= ll, (e.mode & Pt) !== je && (Pe |= Vl), e.flags |= Pe;
        }
        e.memoizedProps = a, e.memoizedState = H;
      }
      return u.props = a, u.state = H, u.context = y, W;
    }
    function wb(e, t, a, i, u) {
      var s = t.stateNode;
      D_(e, t);
      var d = t.memoizedProps, m = t.type === t.elementType ? d : El(t.type, d);
      s.props = m;
      var y = t.pendingProps, x = s.context, R = a.contextType, M = Si;
      if (typeof R == "object" && R !== null)
        M = fr(R);
      else {
        var O = Zf(t, a, !0);
        M = Gf(t, O);
      }
      var H = a.getDerivedStateFromProps, B = typeof H == "function" || typeof s.getSnapshotBeforeUpdate == "function";
      !B && (typeof s.UNSAFE_componentWillReceiveProps == "function" || typeof s.componentWillReceiveProps == "function") && (d !== y || x !== M) && h0(t, s, i, M), O_();
      var W = t.memoizedState, he = s.state = W;
      if (Hm(t, i, s, u), he = t.memoizedState, d === y && W === he && !Em() && !Vm() && !Oe)
        return typeof s.componentDidUpdate == "function" && (d !== e.memoizedProps || W !== e.memoizedState) && (t.flags |= kt), typeof s.getSnapshotBeforeUpdate == "function" && (d !== e.memoizedProps || W !== e.memoizedState) && (t.flags |= er), !1;
      typeof H == "function" && (tE(t, a, H, i), he = t.memoizedState);
      var Pe = Vm() || d0(t, a, m, i, W, he, M) || // TODO: In some cases, we'll end up checking if context has changed twice,
      // both before and after `shouldComponentUpdate` has been called. Not ideal,
      // but I'm loath to refactor this function. This only happens for memoized
      // components so it's not that common.
      Oe;
      return Pe ? (!B && (typeof s.UNSAFE_componentWillUpdate == "function" || typeof s.componentWillUpdate == "function") && (typeof s.componentWillUpdate == "function" && s.componentWillUpdate(i, he, M), typeof s.UNSAFE_componentWillUpdate == "function" && s.UNSAFE_componentWillUpdate(i, he, M)), typeof s.componentDidUpdate == "function" && (t.flags |= kt), typeof s.getSnapshotBeforeUpdate == "function" && (t.flags |= er)) : (typeof s.componentDidUpdate == "function" && (d !== e.memoizedProps || W !== e.memoizedState) && (t.flags |= kt), typeof s.getSnapshotBeforeUpdate == "function" && (d !== e.memoizedProps || W !== e.memoizedState) && (t.flags |= er), t.memoizedProps = i, t.memoizedState = he), s.props = i, s.state = he, s.context = M, Pe;
    }
    function _c(e, t) {
      return {
        value: e,
        source: t,
        stack: el(t),
        digest: null
      };
    }
    function aE(e, t, a) {
      return {
        value: e,
        source: null,
        stack: a ?? null,
        digest: t ?? null
      };
    }
    function bb(e, t) {
      return !0;
    }
    function iE(e, t) {
      try {
        var a = bb(e, t);
        if (a === !1)
          return;
        var i = t.value, u = t.source, s = t.stack, d = s !== null ? s : "";
        if (i != null && i._suppressLogging) {
          if (e.tag === $)
            return;
          console.error(i);
        }
        var m = u ? Xe(u) : null, y = m ? "The above error occurred in the <" + m + "> component:" : "The above error occurred in one of your React components:", x;
        if (e.tag === re)
          x = `Consider adding an error boundary to your tree to customize error handling behavior.
Visit https://reactjs.org/link/error-boundaries to learn more about error boundaries.`;
        else {
          var R = Xe(e) || "Anonymous";
          x = "React will try to recreate this component tree from scratch " + ("using the error boundary you provided, " + R + ".");
        }
        var M = y + `
` + d + `

` + ("" + x);
        console.error(M);
      } catch (O) {
        setTimeout(function() {
          throw O;
        });
      }
    }
    var kb = typeof WeakMap == "function" ? WeakMap : Map;
    function m0(e, t, a) {
      var i = no(un, a);
      i.tag = cS, i.payload = {
        element: null
      };
      var u = t.value;
      return i.callback = function() {
        E1(u), iE(e, t);
      }, i;
    }
    function lE(e, t, a) {
      var i = no(un, a);
      i.tag = cS;
      var u = e.type.getDerivedStateFromError;
      if (typeof u == "function") {
        var s = t.value;
        i.payload = function() {
          return u(s);
        }, i.callback = function() {
          bx(e), iE(e, t);
        };
      }
      var d = e.stateNode;
      return d !== null && typeof d.componentDidCatch == "function" && (i.callback = function() {
        bx(e), iE(e, t), typeof u != "function" && g1(this);
        var y = t.value, x = t.stack;
        this.componentDidCatch(y, {
          componentStack: x !== null ? x : ""
        }), typeof u != "function" && (ca(e.lanes, We) || E("%s: Error boundaries should implement getDerivedStateFromError(). In that method, return a state update to display an error message or fallback UI.", Xe(e) || "Unknown"));
      }), i;
    }
    function y0(e, t, a) {
      var i = e.pingCache, u;
      if (i === null ? (i = e.pingCache = new kb(), u = /* @__PURE__ */ new Set(), i.set(t, u)) : (u = i.get(t), u === void 0 && (u = /* @__PURE__ */ new Set(), i.set(t, u))), !u.has(a)) {
        u.add(a);
        var s = C1.bind(null, e, t, a);
        oa && _v(e, a), t.then(s, s);
      }
    }
    function Db(e, t, a, i) {
      var u = e.updateQueue;
      if (u === null) {
        var s = /* @__PURE__ */ new Set();
        s.add(a), e.updateQueue = s;
      } else
        u.add(a);
    }
    function Ob(e, t) {
      var a = e.tag;
      if ((e.mode & yt) === je && (a === I || a === rt || a === Qe)) {
        var i = e.alternate;
        i ? (e.updateQueue = i.updateQueue, e.memoizedState = i.memoizedState, e.lanes = i.lanes) : (e.updateQueue = null, e.memoizedState = null);
      }
    }
    function g0(e) {
      var t = e;
      do {
        if (t.tag === ze && cb(t))
          return t;
        t = t.return;
      } while (t !== null);
      return null;
    }
    function S0(e, t, a, i, u) {
      if ((e.mode & yt) === je) {
        if (e === t)
          e.flags |= ur;
        else {
          if (e.flags |= Le, a.flags |= qc, a.flags &= -52805, a.tag === $) {
            var s = a.alternate;
            if (s === null)
              a.tag = Zt;
            else {
              var d = no(un, We);
              d.tag = zm, es(a, d, We);
            }
          }
          a.lanes = ut(a.lanes, We);
        }
        return e;
      }
      return e.flags |= ur, e.lanes = u, e;
    }
    function Nb(e, t, a, i, u) {
      if (a.flags |= Ns, oa && _v(e, u), i !== null && typeof i == "object" && typeof i.then == "function") {
        var s = i;
        Ob(a), Qr() && a.mode & yt && c_();
        var d = g0(t);
        if (d !== null) {
          d.flags &= ~Mr, S0(d, t, a, e, u), d.mode & yt && y0(e, s, u), Db(d, e, s);
          return;
        } else {
          if (!Th(u)) {
            y0(e, s, u), HE();
            return;
          }
          var m = new Error("A component suspended while responding to synchronous input. This will cause the UI to be replaced with a loading indicator. To fix, updates that suspend should be wrapped with startTransition.");
          i = m;
        }
      } else if (Qr() && a.mode & yt) {
        c_();
        var y = g0(t);
        if (y !== null) {
          (y.flags & ur) === Ue && (y.flags |= Mr), S0(y, t, a, e, u), Kg(_c(i, a));
          return;
        }
      }
      i = _c(i, a), c1(i);
      var x = t;
      do {
        switch (x.tag) {
          case re: {
            var R = i;
            x.flags |= ur;
            var M = Ws(u);
            x.lanes = ut(x.lanes, M);
            var O = m0(x, R, M);
            pS(x, O);
            return;
          }
          case $:
            var H = i, B = x.type, W = x.stateNode;
            if ((x.flags & Le) === Ue && (typeof B.getDerivedStateFromError == "function" || W !== null && typeof W.componentDidCatch == "function" && !gx(W))) {
              x.flags |= ur;
              var he = Ws(u);
              x.lanes = ut(x.lanes, he);
              var Pe = lE(x, H, he);
              pS(x, Pe);
              return;
            }
            break;
        }
        x = x.return;
      } while (x !== null);
    }
    function Mb() {
      return null;
    }
    var lv = p.ReactCurrentOwner, Cl = !1, uE, uv, oE, sE, cE, xc, fE, cy, ov;
    uE = {}, uv = {}, oE = {}, sE = {}, cE = {}, xc = !1, fE = {}, cy = {}, ov = {};
    function Da(e, t, a, i) {
      e === null ? t.child = __(t, null, a, i) : t.child = Jf(t, e.child, a, i);
    }
    function Lb(e, t, a, i) {
      t.child = Jf(t, e.child, null, i), t.child = Jf(t, null, a, i);
    }
    function E0(e, t, a, i, u) {
      if (t.type !== t.elementType) {
        var s = a.propTypes;
        s && hl(
          s,
          i,
          // Resolved props
          "prop",
          Lt(a)
        );
      }
      var d = a.render, m = t.ref, y, x;
      td(t, u), Ra(t);
      {
        if (lv.current = t, Jn(!0), y = ud(e, t, d, i, m, u), x = od(), t.mode & rn) {
          wn(!0);
          try {
            y = ud(e, t, d, i, m, u), x = od();
          } finally {
            wn(!1);
          }
        }
        Jn(!1);
      }
      return wa(), e !== null && !Cl ? (U_(e, t, u), ro(e, t, u)) : (Qr() && x && Wg(t), t.flags |= vi, Da(e, t, y, u), t.child);
    }
    function C0(e, t, a, i, u) {
      if (e === null) {
        var s = a.type;
        if (F1(s) && a.compare === null && // SimpleMemoComponent codepath doesn't resolve outer props either.
        a.defaultProps === void 0) {
          var d = s;
          return d = md(s), t.tag = Qe, t.type = d, vE(t, s), _0(e, t, d, i, u);
        }
        {
          var m = s.propTypes;
          if (m && hl(
            m,
            i,
            // Resolved props
            "prop",
            Lt(s)
          ), a.defaultProps !== void 0) {
            var y = Lt(s) || "Unknown";
            ov[y] || (E("%s: Support for defaultProps will be removed from memo components in a future major release. Use JavaScript default parameters instead.", y), ov[y] = !0);
          }
        }
        var x = qE(a.type, null, i, t, t.mode, u);
        return x.ref = t.ref, x.return = t, t.child = x, x;
      }
      {
        var R = a.type, M = R.propTypes;
        M && hl(
          M,
          i,
          // Resolved props
          "prop",
          Lt(R)
        );
      }
      var O = e.child, H = EE(e, u);
      if (!H) {
        var B = O.memoizedProps, W = a.compare;
        if (W = W !== null ? W : Re, W(B, i) && e.ref === t.ref)
          return ro(e, t, u);
      }
      t.flags |= vi;
      var he = kc(O, i);
      return he.ref = t.ref, he.return = t, t.child = he, he;
    }
    function _0(e, t, a, i, u) {
      if (t.type !== t.elementType) {
        var s = t.elementType;
        if (s.$$typeof === Ke) {
          var d = s, m = d._payload, y = d._init;
          try {
            s = y(m);
          } catch {
            s = null;
          }
          var x = s && s.propTypes;
          x && hl(
            x,
            i,
            // Resolved (SimpleMemoComponent has no defaultProps)
            "prop",
            Lt(s)
          );
        }
      }
      if (e !== null) {
        var R = e.memoizedProps;
        if (Re(R, i) && e.ref === t.ref && // Prevent bailout if the implementation changed due to hot reload.
        t.type === e.type)
          if (Cl = !1, t.pendingProps = i = R, EE(e, u))
            (e.flags & qc) !== Ue && (Cl = !0);
          else return t.lanes = e.lanes, ro(e, t, u);
      }
      return dE(e, t, a, i, u);
    }
    function x0(e, t, a) {
      var i = t.pendingProps, u = i.children, s = e !== null ? e.memoizedState : null;
      if (i.mode === "hidden" || se)
        if ((t.mode & yt) === je) {
          var d = {
            baseLanes: X,
            cachePool: null,
            transitions: null
          };
          t.memoizedState = d, xy(t, a);
        } else if (ca(a, sa)) {
          var M = {
            baseLanes: X,
            cachePool: null,
            transitions: null
          };
          t.memoizedState = M;
          var O = s !== null ? s.baseLanes : a;
          xy(t, O);
        } else {
          var m = null, y;
          if (s !== null) {
            var x = s.baseLanes;
            y = ut(x, a);
          } else
            y = a;
          t.lanes = t.childLanes = sa;
          var R = {
            baseLanes: y,
            cachePool: m,
            transitions: null
          };
          return t.memoizedState = R, t.updateQueue = null, xy(t, y), null;
        }
      else {
        var H;
        s !== null ? (H = ut(s.baseLanes, a), t.memoizedState = null) : H = a, xy(t, H);
      }
      return Da(e, t, u, a), t.child;
    }
    function Ab(e, t, a) {
      var i = t.pendingProps;
      return Da(e, t, i, a), t.child;
    }
    function zb(e, t, a) {
      var i = t.pendingProps.children;
      return Da(e, t, i, a), t.child;
    }
    function Ub(e, t, a) {
      {
        t.flags |= kt;
        {
          var i = t.stateNode;
          i.effectDuration = 0, i.passiveEffectDuration = 0;
        }
      }
      var u = t.pendingProps, s = u.children;
      return Da(e, t, s, a), t.child;
    }
    function T0(e, t) {
      var a = t.ref;
      (e === null && a !== null || e !== null && e.ref !== a) && (t.flags |= Dn, t.flags |= zo);
    }
    function dE(e, t, a, i, u) {
      if (t.type !== t.elementType) {
        var s = a.propTypes;
        s && hl(
          s,
          i,
          // Resolved props
          "prop",
          Lt(a)
        );
      }
      var d;
      {
        var m = Zf(t, a, !0);
        d = Gf(t, m);
      }
      var y, x;
      td(t, u), Ra(t);
      {
        if (lv.current = t, Jn(!0), y = ud(e, t, a, i, d, u), x = od(), t.mode & rn) {
          wn(!0);
          try {
            y = ud(e, t, a, i, d, u), x = od();
          } finally {
            wn(!1);
          }
        }
        Jn(!1);
      }
      return wa(), e !== null && !Cl ? (U_(e, t, u), ro(e, t, u)) : (Qr() && x && Wg(t), t.flags |= vi, Da(e, t, y, u), t.child);
    }
    function R0(e, t, a, i, u) {
      {
        switch (J1(t)) {
          case !1: {
            var s = t.stateNode, d = t.type, m = new d(t.memoizedProps, s.context), y = m.state;
            s.updater.enqueueSetState(s, y, null);
            break;
          }
          case !0: {
            t.flags |= Le, t.flags |= ur;
            var x = new Error("Simulated error coming from DevTools"), R = Ws(u);
            t.lanes = ut(t.lanes, R);
            var M = lE(t, _c(x, t), R);
            pS(t, M);
            break;
          }
        }
        if (t.type !== t.elementType) {
          var O = a.propTypes;
          O && hl(
            O,
            i,
            // Resolved props
            "prop",
            Lt(a)
          );
        }
      }
      var H;
      ru(a) ? (H = !0, _m(t)) : H = !1, td(t, u);
      var B = t.stateNode, W;
      B === null ? (dy(e, t), v0(t, a, i), rE(t, a, i, u), W = !0) : e === null ? W = Rb(t, a, i, u) : W = wb(e, t, a, i, u);
      var he = pE(e, t, a, W, H, u);
      {
        var Pe = t.stateNode;
        W && Pe.props !== i && (xc || E("It looks like %s is reassigning its own `this.props` while rendering. This is not supported and can lead to confusing bugs.", Xe(t) || "a component"), xc = !0);
      }
      return he;
    }
    function pE(e, t, a, i, u, s) {
      T0(e, t);
      var d = (t.flags & Le) !== Ue;
      if (!i && !d)
        return u && l_(t, a, !1), ro(e, t, s);
      var m = t.stateNode;
      lv.current = t;
      var y;
      if (d && typeof a.getDerivedStateFromError != "function")
        y = null, s0();
      else {
        Ra(t);
        {
          if (Jn(!0), y = m.render(), t.mode & rn) {
            wn(!0);
            try {
              m.render();
            } finally {
              wn(!1);
            }
          }
          Jn(!1);
        }
        wa();
      }
      return t.flags |= vi, e !== null && d ? Lb(e, t, y, s) : Da(e, t, y, s), t.memoizedState = m.state, u && l_(t, a, !0), t.child;
    }
    function w0(e) {
      var t = e.stateNode;
      t.pendingContext ? a_(e, t.pendingContext, t.pendingContext !== t.context) : t.context && a_(e, t.context, !1), vS(e, t.containerInfo);
    }
    function jb(e, t, a) {
      if (w0(t), e === null)
        throw new Error("Should have a current fiber. This is a bug in React.");
      var i = t.pendingProps, u = t.memoizedState, s = u.element;
      D_(e, t), Hm(t, i, null, a);
      var d = t.memoizedState;
      t.stateNode;
      var m = d.element;
      if (u.isDehydrated) {
        var y = {
          element: m,
          isDehydrated: !1,
          cache: d.cache,
          pendingSuspenseBoundaries: d.pendingSuspenseBoundaries,
          transitions: d.transitions
        }, x = t.updateQueue;
        if (x.baseState = y, t.memoizedState = y, t.flags & Mr) {
          var R = _c(new Error("There was an error while hydrating. Because the error happened outside of a Suspense boundary, the entire root will switch to client rendering."), t);
          return b0(e, t, m, a, R);
        } else if (m !== s) {
          var M = _c(new Error("This root received an early update, before anything was able hydrate. Switched the entire root to client rendering."), t);
          return b0(e, t, m, a, M);
        } else {
          Vw(t);
          var O = __(t, null, m, a);
          t.child = O;
          for (var H = O; H; )
            H.flags = H.flags & ~Rn | ia, H = H.sibling;
        }
      } else {
        if (Kf(), m === s)
          return ro(e, t, a);
        Da(e, t, m, a);
      }
      return t.child;
    }
    function b0(e, t, a, i, u) {
      return Kf(), Kg(u), t.flags |= Mr, Da(e, t, a, i), t.child;
    }
    function Fb(e, t, a) {
      L_(t), e === null && Xg(t);
      var i = t.type, u = t.pendingProps, s = e !== null ? e.memoizedProps : null, d = u.children, m = Mg(i, u);
      return m ? d = null : s !== null && Mg(i, s) && (t.flags |= Pa), T0(e, t), Da(e, t, d, a), t.child;
    }
    function Hb(e, t) {
      return e === null && Xg(t), null;
    }
    function Vb(e, t, a, i) {
      dy(e, t);
      var u = t.pendingProps, s = a, d = s._payload, m = s._init, y = m(d);
      t.type = y;
      var x = t.tag = H1(y), R = El(y, u), M;
      switch (x) {
        case I:
          return vE(t, y), t.type = y = md(y), M = dE(null, t, y, R, i), M;
        case $:
          return t.type = y = $E(y), M = R0(null, t, y, R, i), M;
        case rt:
          return t.type = y = YE(y), M = E0(null, t, y, R, i), M;
        case St: {
          if (t.type !== t.elementType) {
            var O = y.propTypes;
            O && hl(
              O,
              R,
              // Resolved for outer only
              "prop",
              Lt(y)
            );
          }
          return M = C0(
            null,
            t,
            y,
            El(y.type, R),
            // The inner type can have defaults too
            i
          ), M;
        }
      }
      var H = "";
      throw y !== null && typeof y == "object" && y.$$typeof === Ke && (H = " Did you wrap a component in React.lazy() more than once?"), new Error("Element type is invalid. Received a promise that resolves to: " + y + ". " + ("Lazy element type must resolve to a class or function." + H));
    }
    function Pb(e, t, a, i, u) {
      dy(e, t), t.tag = $;
      var s;
      return ru(a) ? (s = !0, _m(t)) : s = !1, td(t, u), v0(t, a, i), rE(t, a, i, u), pE(null, t, a, !0, s, u);
    }
    function Bb(e, t, a, i) {
      dy(e, t);
      var u = t.pendingProps, s;
      {
        var d = Zf(t, a, !1);
        s = Gf(t, d);
      }
      td(t, i);
      var m, y;
      Ra(t);
      {
        if (a.prototype && typeof a.prototype.render == "function") {
          var x = Lt(a) || "Unknown";
          uE[x] || (E("The <%s /> component appears to have a render method, but doesn't extend React.Component. This is likely to cause errors. Change %s to extend React.Component instead.", x, x), uE[x] = !0);
        }
        t.mode & rn && yl.recordLegacyContextWarning(t, null), Jn(!0), lv.current = t, m = ud(null, t, a, u, s, i), y = od(), Jn(!1);
      }
      if (wa(), t.flags |= vi, typeof m == "object" && m !== null && typeof m.render == "function" && m.$$typeof === void 0) {
        var R = Lt(a) || "Unknown";
        uv[R] || (E("The <%s /> component appears to be a function component that returns a class instance. Change %s to a class that extends React.Component instead. If you can't use a class try assigning the prototype on the function as a workaround. `%s.prototype = React.Component.prototype`. Don't use an arrow function since it cannot be called with `new` by React.", R, R, R), uv[R] = !0);
      }
      if (
        // Run these checks in production only if the flag is off.
        // Eventually we'll delete this branch altogether.
        typeof m == "object" && m !== null && typeof m.render == "function" && m.$$typeof === void 0
      ) {
        {
          var M = Lt(a) || "Unknown";
          uv[M] || (E("The <%s /> component appears to be a function component that returns a class instance. Change %s to a class that extends React.Component instead. If you can't use a class try assigning the prototype on the function as a workaround. `%s.prototype = React.Component.prototype`. Don't use an arrow function since it cannot be called with `new` by React.", M, M, M), uv[M] = !0);
        }
        t.tag = $, t.memoizedState = null, t.updateQueue = null;
        var O = !1;
        return ru(a) ? (O = !0, _m(t)) : O = !1, t.memoizedState = m.state !== null && m.state !== void 0 ? m.state : null, dS(t), p0(t, m), rE(t, a, u, i), pE(null, t, a, !0, O, i);
      } else {
        if (t.tag = I, t.mode & rn) {
          wn(!0);
          try {
            m = ud(null, t, a, u, s, i), y = od();
          } finally {
            wn(!1);
          }
        }
        return Qr() && y && Wg(t), Da(null, t, m, i), vE(t, a), t.child;
      }
    }
    function vE(e, t) {
      {
        if (t && t.childContextTypes && E("%s(...): childContextTypes cannot be defined on a function component.", t.displayName || t.name || "Component"), e.ref !== null) {
          var a = "", i = Vr();
          i && (a += `

Check the render method of \`` + i + "`.");
          var u = i || "", s = e._debugSource;
          s && (u = s.fileName + ":" + s.lineNumber), cE[u] || (cE[u] = !0, E("Function components cannot be given refs. Attempts to access this ref will fail. Did you mean to use React.forwardRef()?%s", a));
        }
        if (t.defaultProps !== void 0) {
          var d = Lt(t) || "Unknown";
          ov[d] || (E("%s: Support for defaultProps will be removed from function components in a future major release. Use JavaScript default parameters instead.", d), ov[d] = !0);
        }
        if (typeof t.getDerivedStateFromProps == "function") {
          var m = Lt(t) || "Unknown";
          sE[m] || (E("%s: Function components do not support getDerivedStateFromProps.", m), sE[m] = !0);
        }
        if (typeof t.contextType == "object" && t.contextType !== null) {
          var y = Lt(t) || "Unknown";
          oE[y] || (E("%s: Function components do not support contextType.", y), oE[y] = !0);
        }
      }
    }
    var hE = {
      dehydrated: null,
      treeContext: null,
      retryLane: jt
    };
    function mE(e) {
      return {
        baseLanes: e,
        cachePool: Mb(),
        transitions: null
      };
    }
    function Ib(e, t) {
      var a = null;
      return {
        baseLanes: ut(e.baseLanes, t),
        cachePool: a,
        transitions: e.transitions
      };
    }
    function $b(e, t, a, i) {
      if (t !== null) {
        var u = t.memoizedState;
        if (u === null)
          return !1;
      }
      return yS(e, Xp);
    }
    function Yb(e, t) {
      return Qs(e.childLanes, t);
    }
    function k0(e, t, a) {
      var i = t.pendingProps;
      eD(t) && (t.flags |= Le);
      var u = gl.current, s = !1, d = (t.flags & Le) !== Ue;
      if (d || $b(u, e) ? (s = !0, t.flags &= ~Le) : (e === null || e.memoizedState !== null) && (u = sb(u, z_)), u = rd(u), ns(t, u), e === null) {
        Xg(t);
        var m = t.memoizedState;
        if (m !== null) {
          var y = m.dehydrated;
          if (y !== null)
            return qb(t, y);
        }
        var x = i.children, R = i.fallback;
        if (s) {
          var M = Wb(t, x, R, a), O = t.child;
          return O.memoizedState = mE(a), t.memoizedState = hE, M;
        } else
          return yE(t, x);
      } else {
        var H = e.memoizedState;
        if (H !== null) {
          var B = H.dehydrated;
          if (B !== null)
            return Xb(e, t, d, i, B, H, a);
        }
        if (s) {
          var W = i.fallback, he = i.children, Pe = Zb(e, t, he, W, a), Me = t.child, Nt = e.child.memoizedState;
          return Me.memoizedState = Nt === null ? mE(a) : Ib(Nt, a), Me.childLanes = Yb(e, a), t.memoizedState = hE, Pe;
        } else {
          var Rt = i.children, U = Qb(e, t, Rt, a);
          return t.memoizedState = null, U;
        }
      }
    }
    function yE(e, t, a) {
      var i = e.mode, u = {
        mode: "visible",
        children: t
      }, s = gE(u, i);
      return s.return = e, e.child = s, s;
    }
    function Wb(e, t, a, i) {
      var u = e.mode, s = e.child, d = {
        mode: "hidden",
        children: t
      }, m, y;
      return (u & yt) === je && s !== null ? (m = s, m.childLanes = X, m.pendingProps = d, e.mode & Vt && (m.actualDuration = 0, m.actualStartTime = -1, m.selfBaseDuration = 0, m.treeBaseDuration = 0), y = cs(a, u, i, null)) : (m = gE(d, u), y = cs(a, u, i, null)), m.return = e, y.return = e, m.sibling = y, e.child = m, y;
    }
    function gE(e, t, a) {
      return Dx(e, t, X, null);
    }
    function D0(e, t) {
      return kc(e, t);
    }
    function Qb(e, t, a, i) {
      var u = e.child, s = u.sibling, d = D0(u, {
        mode: "visible",
        children: a
      });
      if ((t.mode & yt) === je && (d.lanes = i), d.return = t, d.sibling = null, s !== null) {
        var m = t.deletions;
        m === null ? (t.deletions = [s], t.flags |= Va) : m.push(s);
      }
      return t.child = d, d;
    }
    function Zb(e, t, a, i, u) {
      var s = t.mode, d = e.child, m = d.sibling, y = {
        mode: "hidden",
        children: a
      }, x;
      if (
        // In legacy mode, we commit the primary tree as if it successfully
        // completed, even though it's in an inconsistent state.
        (s & yt) === je && // Make sure we're on the second pass, i.e. the primary child fragment was
        // already cloned. In legacy mode, the only case where this isn't true is
        // when DevTools forces us to display a fallback; we skip the first render
        // pass entirely and go straight to rendering the fallback. (In Concurrent
        // Mode, SuspenseList can also trigger this scenario, but this is a legacy-
        // only codepath.)
        t.child !== d
      ) {
        var R = t.child;
        x = R, x.childLanes = X, x.pendingProps = y, t.mode & Vt && (x.actualDuration = 0, x.actualStartTime = -1, x.selfBaseDuration = d.selfBaseDuration, x.treeBaseDuration = d.treeBaseDuration), t.deletions = null;
      } else
        x = D0(d, y), x.subtreeFlags = d.subtreeFlags & In;
      var M;
      return m !== null ? M = kc(m, i) : (M = cs(i, s, u, null), M.flags |= Rn), M.return = t, x.return = t, x.sibling = M, t.child = x, M;
    }
    function fy(e, t, a, i) {
      i !== null && Kg(i), Jf(t, e.child, null, a);
      var u = t.pendingProps, s = u.children, d = yE(t, s);
      return d.flags |= Rn, t.memoizedState = null, d;
    }
    function Gb(e, t, a, i, u) {
      var s = t.mode, d = {
        mode: "visible",
        children: a
      }, m = gE(d, s), y = cs(i, s, u, null);
      return y.flags |= Rn, m.return = t, y.return = t, m.sibling = y, t.child = m, (t.mode & yt) !== je && Jf(t, e.child, null, u), y;
    }
    function qb(e, t, a) {
      return (e.mode & yt) === je ? (E("Cannot hydrate Suspense in legacy mode. Switch from ReactDOM.hydrate(element, container) to ReactDOMClient.hydrateRoot(container, <App />).render(element) or remove the Suspense components from the server rendered components."), e.lanes = We) : Ug(t) ? e.lanes = Lr : e.lanes = sa, null;
    }
    function Xb(e, t, a, i, u, s, d) {
      if (a)
        if (t.flags & Mr) {
          t.flags &= ~Mr;
          var U = aE(new Error("There was an error while hydrating this Suspense boundary. Switched to client rendering."));
          return fy(e, t, d, U);
        } else {
          if (t.memoizedState !== null)
            return t.child = e.child, t.flags |= Le, null;
          var Q = i.children, j = i.fallback, ne = Gb(e, t, Q, j, d), Ee = t.child;
          return Ee.memoizedState = mE(d), t.memoizedState = hE, ne;
        }
      else {
        if (Fw(), (t.mode & yt) === je)
          return fy(
            e,
            t,
            d,
            // TODO: When we delete legacy mode, we should make this error argument
            // required — every concurrent mode path that causes hydration to
            // de-opt to client rendering should have an error message.
            null
          );
        if (Ug(u)) {
          var m, y, x;
          {
            var R = tw(u);
            m = R.digest, y = R.message, x = R.stack;
          }
          var M;
          y ? M = new Error(y) : M = new Error("The server could not finish this Suspense boundary, likely due to an error during server rendering. Switched to client rendering.");
          var O = aE(M, m, x);
          return fy(e, t, d, O);
        }
        var H = ca(d, e.childLanes);
        if (Cl || H) {
          var B = _y();
          if (B !== null) {
            var W = fp(B, d);
            if (W !== jt && W !== s.retryLane) {
              s.retryLane = W;
              var he = un;
              qa(e, W), kr(B, e, W, he);
            }
          }
          HE();
          var Pe = aE(new Error("This Suspense boundary received an update before it finished hydrating. This caused the boundary to switch to client rendering. The usual way to fix this is to wrap the original update in startTransition."));
          return fy(e, t, d, Pe);
        } else if (KC(u)) {
          t.flags |= Le, t.child = e.child;
          var Me = _1.bind(null, e);
          return nw(u, Me), null;
        } else {
          Pw(t, u, s.treeContext);
          var Nt = i.children, Rt = yE(t, Nt);
          return Rt.flags |= ia, Rt;
        }
      }
    }
    function O0(e, t, a) {
      e.lanes = ut(e.lanes, t);
      var i = e.alternate;
      i !== null && (i.lanes = ut(i.lanes, t)), oS(e.return, t, a);
    }
    function Kb(e, t, a) {
      for (var i = t; i !== null; ) {
        if (i.tag === ze) {
          var u = i.memoizedState;
          u !== null && O0(i, a, e);
        } else if (i.tag === hn)
          O0(i, a, e);
        else if (i.child !== null) {
          i.child.return = i, i = i.child;
          continue;
        }
        if (i === e)
          return;
        for (; i.sibling === null; ) {
          if (i.return === null || i.return === e)
            return;
          i = i.return;
        }
        i.sibling.return = i.return, i = i.sibling;
      }
    }
    function Jb(e) {
      for (var t = e, a = null; t !== null; ) {
        var i = t.alternate;
        i !== null && Im(i) === null && (a = t), t = t.sibling;
      }
      return a;
    }
    function ek(e) {
      if (e !== void 0 && e !== "forwards" && e !== "backwards" && e !== "together" && !fE[e])
        if (fE[e] = !0, typeof e == "string")
          switch (e.toLowerCase()) {
            case "together":
            case "forwards":
            case "backwards": {
              E('"%s" is not a valid value for revealOrder on <SuspenseList />. Use lowercase "%s" instead.', e, e.toLowerCase());
              break;
            }
            case "forward":
            case "backward": {
              E('"%s" is not a valid value for revealOrder on <SuspenseList />. React uses the -s suffix in the spelling. Use "%ss" instead.', e, e.toLowerCase());
              break;
            }
            default:
              E('"%s" is not a supported revealOrder on <SuspenseList />. Did you mean "together", "forwards" or "backwards"?', e);
              break;
          }
        else
          E('%s is not a supported value for revealOrder on <SuspenseList />. Did you mean "together", "forwards" or "backwards"?', e);
    }
    function tk(e, t) {
      e !== void 0 && !cy[e] && (e !== "collapsed" && e !== "hidden" ? (cy[e] = !0, E('"%s" is not a supported value for tail on <SuspenseList />. Did you mean "collapsed" or "hidden"?', e)) : t !== "forwards" && t !== "backwards" && (cy[e] = !0, E('<SuspenseList tail="%s" /> is only valid if revealOrder is "forwards" or "backwards". Did you mean to specify revealOrder="forwards"?', e)));
    }
    function N0(e, t) {
      {
        var a = pt(e), i = !a && typeof lt(e) == "function";
        if (a || i) {
          var u = a ? "array" : "iterable";
          return E("A nested %s was passed to row #%s in <SuspenseList />. Wrap it in an additional SuspenseList to configure its revealOrder: <SuspenseList revealOrder=...> ... <SuspenseList revealOrder=...>{%s}</SuspenseList> ... </SuspenseList>", u, t, u), !1;
        }
      }
      return !0;
    }
    function nk(e, t) {
      if ((t === "forwards" || t === "backwards") && e !== void 0 && e !== null && e !== !1)
        if (pt(e)) {
          for (var a = 0; a < e.length; a++)
            if (!N0(e[a], a))
              return;
        } else {
          var i = lt(e);
          if (typeof i == "function") {
            var u = i.call(e);
            if (u)
              for (var s = u.next(), d = 0; !s.done; s = u.next()) {
                if (!N0(s.value, d))
                  return;
                d++;
              }
          } else
            E('A single row was passed to a <SuspenseList revealOrder="%s" />. This is not useful since it needs multiple rows. Did you mean to pass multiple children or an array?', t);
        }
    }
    function SE(e, t, a, i, u) {
      var s = e.memoizedState;
      s === null ? e.memoizedState = {
        isBackwards: t,
        rendering: null,
        renderingStartTime: 0,
        last: i,
        tail: a,
        tailMode: u
      } : (s.isBackwards = t, s.rendering = null, s.renderingStartTime = 0, s.last = i, s.tail = a, s.tailMode = u);
    }
    function M0(e, t, a) {
      var i = t.pendingProps, u = i.revealOrder, s = i.tail, d = i.children;
      ek(u), tk(s, u), nk(d, u), Da(e, t, d, a);
      var m = gl.current, y = yS(m, Xp);
      if (y)
        m = gS(m, Xp), t.flags |= Le;
      else {
        var x = e !== null && (e.flags & Le) !== Ue;
        x && Kb(t, t.child, a), m = rd(m);
      }
      if (ns(t, m), (t.mode & yt) === je)
        t.memoizedState = null;
      else
        switch (u) {
          case "forwards": {
            var R = Jb(t.child), M;
            R === null ? (M = t.child, t.child = null) : (M = R.sibling, R.sibling = null), SE(
              t,
              !1,
              // isBackwards
              M,
              R,
              s
            );
            break;
          }
          case "backwards": {
            var O = null, H = t.child;
            for (t.child = null; H !== null; ) {
              var B = H.alternate;
              if (B !== null && Im(B) === null) {
                t.child = H;
                break;
              }
              var W = H.sibling;
              H.sibling = O, O = H, H = W;
            }
            SE(
              t,
              !0,
              // isBackwards
              O,
              null,
              // last
              s
            );
            break;
          }
          case "together": {
            SE(
              t,
              !1,
              // isBackwards
              null,
              // tail
              null,
              // last
              void 0
            );
            break;
          }
          default:
            t.memoizedState = null;
        }
      return t.child;
    }
    function rk(e, t, a) {
      vS(t, t.stateNode.containerInfo);
      var i = t.pendingProps;
      return e === null ? t.child = Jf(t, null, i, a) : Da(e, t, i, a), t.child;
    }
    var L0 = !1;
    function ak(e, t, a) {
      var i = t.type, u = i._context, s = t.pendingProps, d = t.memoizedProps, m = s.value;
      {
        "value" in s || L0 || (L0 = !0, E("The `value` prop is required for the `<Context.Provider>`. Did you misspell it or forget to pass it?"));
        var y = t.type.propTypes;
        y && hl(y, s, "prop", "Context.Provider");
      }
      if (R_(t, u, m), d !== null) {
        var x = d.value;
        if (ee(x, m)) {
          if (d.children === s.children && !Em())
            return ro(e, t, a);
        } else
          eb(t, u, a);
      }
      var R = s.children;
      return Da(e, t, R, a), t.child;
    }
    var A0 = !1;
    function ik(e, t, a) {
      var i = t.type;
      i._context === void 0 ? i !== i.Consumer && (A0 || (A0 = !0, E("Rendering <Context> directly is not supported and will be removed in a future major release. Did you mean to render <Context.Consumer> instead?"))) : i = i._context;
      var u = t.pendingProps, s = u.children;
      typeof s != "function" && E("A context consumer was rendered with multiple children, or a child that isn't a function. A context consumer expects a single child that is a function. If you did pass a function, make sure there is no trailing or leading whitespace around it."), td(t, a);
      var d = fr(i);
      Ra(t);
      var m;
      return lv.current = t, Jn(!0), m = s(d), Jn(!1), wa(), t.flags |= vi, Da(e, t, m, a), t.child;
    }
    function sv() {
      Cl = !0;
    }
    function dy(e, t) {
      (t.mode & yt) === je && e !== null && (e.alternate = null, t.alternate = null, t.flags |= Rn);
    }
    function ro(e, t, a) {
      return e !== null && (t.dependencies = e.dependencies), s0(), Cv(t.lanes), ca(a, t.childLanes) ? (Kw(e, t), t.child) : null;
    }
    function lk(e, t, a) {
      {
        var i = t.return;
        if (i === null)
          throw new Error("Cannot swap the root fiber.");
        if (e.alternate = null, t.alternate = null, a.index = t.index, a.sibling = t.sibling, a.return = t.return, a.ref = t.ref, t === i.child)
          i.child = a;
        else {
          var u = i.child;
          if (u === null)
            throw new Error("Expected parent to have a child.");
          for (; u.sibling !== t; )
            if (u = u.sibling, u === null)
              throw new Error("Expected to find the previous sibling.");
          u.sibling = a;
        }
        var s = i.deletions;
        return s === null ? (i.deletions = [e], i.flags |= Va) : s.push(e), a.flags |= Rn, a;
      }
    }
    function EE(e, t) {
      var a = e.lanes;
      return !!ca(a, t);
    }
    function uk(e, t, a) {
      switch (t.tag) {
        case re:
          w0(t), t.stateNode, Kf();
          break;
        case de:
          L_(t);
          break;
        case $: {
          var i = t.type;
          ru(i) && _m(t);
          break;
        }
        case be:
          vS(t, t.stateNode.containerInfo);
          break;
        case _t: {
          var u = t.memoizedProps.value, s = t.type._context;
          R_(t, s, u);
          break;
        }
        case Tt:
          {
            var d = ca(a, t.childLanes);
            d && (t.flags |= kt);
            {
              var m = t.stateNode;
              m.effectDuration = 0, m.passiveEffectDuration = 0;
            }
          }
          break;
        case ze: {
          var y = t.memoizedState;
          if (y !== null) {
            if (y.dehydrated !== null)
              return ns(t, rd(gl.current)), t.flags |= Le, null;
            var x = t.child, R = x.childLanes;
            if (ca(a, R))
              return k0(e, t, a);
            ns(t, rd(gl.current));
            var M = ro(e, t, a);
            return M !== null ? M.sibling : null;
          } else
            ns(t, rd(gl.current));
          break;
        }
        case hn: {
          var O = (e.flags & Le) !== Ue, H = ca(a, t.childLanes);
          if (O) {
            if (H)
              return M0(e, t, a);
            t.flags |= Le;
          }
          var B = t.memoizedState;
          if (B !== null && (B.rendering = null, B.tail = null, B.lastEffect = null), ns(t, gl.current), H)
            break;
          return null;
        }
        case He:
        case Yt:
          return t.lanes = X, x0(e, t, a);
      }
      return ro(e, t, a);
    }
    function z0(e, t, a) {
      if (t._debugNeedsRemount && e !== null)
        return lk(e, t, qE(t.type, t.key, t.pendingProps, t._debugOwner || null, t.mode, t.lanes));
      if (e !== null) {
        var i = e.memoizedProps, u = t.pendingProps;
        if (i !== u || Em() || // Force a re-render if the implementation changed due to hot reload:
        t.type !== e.type)
          Cl = !0;
        else {
          var s = EE(e, a);
          if (!s && // If this is the second pass of an error or suspense boundary, there
          // may not be work scheduled on `current`, so we check for this flag.
          (t.flags & Le) === Ue)
            return Cl = !1, uk(e, t, a);
          (e.flags & qc) !== Ue ? Cl = !0 : Cl = !1;
        }
      } else if (Cl = !1, Qr() && Mw(t)) {
        var d = t.index, m = Lw();
        s_(t, m, d);
      }
      switch (t.lanes = X, t.tag) {
        case fe:
          return Bb(e, t, t.type, a);
        case vn: {
          var y = t.elementType;
          return Vb(e, t, y, a);
        }
        case I: {
          var x = t.type, R = t.pendingProps, M = t.elementType === x ? R : El(x, R);
          return dE(e, t, x, M, a);
        }
        case $: {
          var O = t.type, H = t.pendingProps, B = t.elementType === O ? H : El(O, H);
          return R0(e, t, O, B, a);
        }
        case re:
          return jb(e, t, a);
        case de:
          return Fb(e, t, a);
        case nt:
          return Hb(e, t);
        case ze:
          return k0(e, t, a);
        case be:
          return rk(e, t, a);
        case rt: {
          var W = t.type, he = t.pendingProps, Pe = t.elementType === W ? he : El(W, he);
          return E0(e, t, W, Pe, a);
        }
        case bt:
          return Ab(e, t, a);
        case xt:
          return zb(e, t, a);
        case Tt:
          return Ub(e, t, a);
        case _t:
          return ak(e, t, a);
        case En:
          return ik(e, t, a);
        case St: {
          var Me = t.type, Nt = t.pendingProps, Rt = El(Me, Nt);
          if (t.type !== t.elementType) {
            var U = Me.propTypes;
            U && hl(
              U,
              Rt,
              // Resolved for outer only
              "prop",
              Lt(Me)
            );
          }
          return Rt = El(Me.type, Rt), C0(e, t, Me, Rt, a);
        }
        case Qe:
          return _0(e, t, t.type, t.pendingProps, a);
        case Zt: {
          var Q = t.type, j = t.pendingProps, ne = t.elementType === Q ? j : El(Q, j);
          return Pb(e, t, Q, ne, a);
        }
        case hn:
          return M0(e, t, a);
        case zt:
          break;
        case He:
          return x0(e, t, a);
      }
      throw new Error("Unknown unit of work tag (" + t.tag + "). This error is likely caused by a bug in React. Please file an issue.");
    }
    function sd(e) {
      e.flags |= kt;
    }
    function U0(e) {
      e.flags |= Dn, e.flags |= zo;
    }
    var j0, CE, F0, H0;
    j0 = function(e, t, a, i) {
      for (var u = t.child; u !== null; ) {
        if (u.tag === de || u.tag === nt)
          DR(e, u.stateNode);
        else if (u.tag !== be) {
          if (u.child !== null) {
            u.child.return = u, u = u.child;
            continue;
          }
        }
        if (u === t)
          return;
        for (; u.sibling === null; ) {
          if (u.return === null || u.return === t)
            return;
          u = u.return;
        }
        u.sibling.return = u.return, u = u.sibling;
      }
    }, CE = function(e, t) {
    }, F0 = function(e, t, a, i, u) {
      var s = e.memoizedProps;
      if (s !== i) {
        var d = t.stateNode, m = hS(), y = NR(d, a, s, i, u, m);
        t.updateQueue = y, y && sd(t);
      }
    }, H0 = function(e, t, a, i) {
      a !== i && sd(t);
    };
    function cv(e, t) {
      if (!Qr())
        switch (e.tailMode) {
          case "hidden": {
            for (var a = e.tail, i = null; a !== null; )
              a.alternate !== null && (i = a), a = a.sibling;
            i === null ? e.tail = null : i.sibling = null;
            break;
          }
          case "collapsed": {
            for (var u = e.tail, s = null; u !== null; )
              u.alternate !== null && (s = u), u = u.sibling;
            s === null ? !t && e.tail !== null ? e.tail.sibling = null : e.tail = null : s.sibling = null;
            break;
          }
        }
    }
    function Gr(e) {
      var t = e.alternate !== null && e.alternate.child === e.child, a = X, i = Ue;
      if (t) {
        if ((e.mode & Vt) !== je) {
          for (var y = e.selfBaseDuration, x = e.child; x !== null; )
            a = ut(a, ut(x.lanes, x.childLanes)), i |= x.subtreeFlags & In, i |= x.flags & In, y += x.treeBaseDuration, x = x.sibling;
          e.treeBaseDuration = y;
        } else
          for (var R = e.child; R !== null; )
            a = ut(a, ut(R.lanes, R.childLanes)), i |= R.subtreeFlags & In, i |= R.flags & In, R.return = e, R = R.sibling;
        e.subtreeFlags |= i;
      } else {
        if ((e.mode & Vt) !== je) {
          for (var u = e.actualDuration, s = e.selfBaseDuration, d = e.child; d !== null; )
            a = ut(a, ut(d.lanes, d.childLanes)), i |= d.subtreeFlags, i |= d.flags, u += d.actualDuration, s += d.treeBaseDuration, d = d.sibling;
          e.actualDuration = u, e.treeBaseDuration = s;
        } else
          for (var m = e.child; m !== null; )
            a = ut(a, ut(m.lanes, m.childLanes)), i |= m.subtreeFlags, i |= m.flags, m.return = e, m = m.sibling;
        e.subtreeFlags |= i;
      }
      return e.childLanes = a, t;
    }
    function ok(e, t, a) {
      if (Ww() && (t.mode & yt) !== je && (t.flags & Le) === Ue)
        return m_(t), Kf(), t.flags |= Mr | Ns | ur, !1;
      var i = bm(t);
      if (a !== null && a.dehydrated !== null)
        if (e === null) {
          if (!i)
            throw new Error("A dehydrated suspense component was completed without a hydrated node. This is probably a bug in React.");
          if ($w(t), Gr(t), (t.mode & Vt) !== je) {
            var u = a !== null;
            if (u) {
              var s = t.child;
              s !== null && (t.treeBaseDuration -= s.treeBaseDuration);
            }
          }
          return !1;
        } else {
          if (Kf(), (t.flags & Le) === Ue && (t.memoizedState = null), t.flags |= kt, Gr(t), (t.mode & Vt) !== je) {
            var d = a !== null;
            if (d) {
              var m = t.child;
              m !== null && (t.treeBaseDuration -= m.treeBaseDuration);
            }
          }
          return !1;
        }
      else
        return y_(), !0;
    }
    function V0(e, t, a) {
      var i = t.pendingProps;
      switch (Qg(t), t.tag) {
        case fe:
        case vn:
        case Qe:
        case I:
        case rt:
        case bt:
        case xt:
        case Tt:
        case En:
        case St:
          return Gr(t), null;
        case $: {
          var u = t.type;
          return ru(u) && Cm(t), Gr(t), null;
        }
        case re: {
          var s = t.stateNode;
          if (nd(t), Ig(t), ES(), s.pendingContext && (s.context = s.pendingContext, s.pendingContext = null), e === null || e.child === null) {
            var d = bm(t);
            if (d)
              sd(t);
            else if (e !== null) {
              var m = e.memoizedState;
              // Check if this is a client root
              (!m.isDehydrated || // Check if we reverted to client rendering (e.g. due to an error)
              (t.flags & Mr) !== Ue) && (t.flags |= er, y_());
            }
          }
          return CE(e, t), Gr(t), null;
        }
        case de: {
          mS(t);
          var y = M_(), x = t.type;
          if (e !== null && t.stateNode != null)
            F0(e, t, x, i, y), e.ref !== t.ref && U0(t);
          else {
            if (!i) {
              if (t.stateNode === null)
                throw new Error("We must have new props for new mounts. This error is likely caused by a bug in React. Please file an issue.");
              return Gr(t), null;
            }
            var R = hS(), M = bm(t);
            if (M)
              Bw(t, y, R) && sd(t);
            else {
              var O = kR(x, i, y, R, t);
              j0(O, t, !1, !1), t.stateNode = O, OR(O, x, i, y) && sd(t);
            }
            t.ref !== null && U0(t);
          }
          return Gr(t), null;
        }
        case nt: {
          var H = i;
          if (e && t.stateNode != null) {
            var B = e.memoizedProps;
            H0(e, t, B, H);
          } else {
            if (typeof H != "string" && t.stateNode === null)
              throw new Error("We must have new props for new mounts. This error is likely caused by a bug in React. Please file an issue.");
            var W = M_(), he = hS(), Pe = bm(t);
            Pe ? Iw(t) && sd(t) : t.stateNode = MR(H, W, he, t);
          }
          return Gr(t), null;
        }
        case ze: {
          ad(t);
          var Me = t.memoizedState;
          if (e === null || e.memoizedState !== null && e.memoizedState.dehydrated !== null) {
            var Nt = ok(e, t, Me);
            if (!Nt)
              return t.flags & ur ? t : null;
          }
          if ((t.flags & Le) !== Ue)
            return t.lanes = a, (t.mode & Vt) !== je && YS(t), t;
          var Rt = Me !== null, U = e !== null && e.memoizedState !== null;
          if (Rt !== U && Rt) {
            var Q = t.child;
            if (Q.flags |= Bn, (t.mode & yt) !== je) {
              var j = e === null && (t.memoizedProps.unstable_avoidThisFallback !== !0 || !0);
              j || yS(gl.current, z_) ? s1() : HE();
            }
          }
          var ne = t.updateQueue;
          if (ne !== null && (t.flags |= kt), Gr(t), (t.mode & Vt) !== je && Rt) {
            var Ee = t.child;
            Ee !== null && (t.treeBaseDuration -= Ee.treeBaseDuration);
          }
          return null;
        }
        case be:
          return nd(t), CE(e, t), e === null && Rw(t.stateNode.containerInfo), Gr(t), null;
        case _t:
          var me = t.type._context;
          return uS(me, t), Gr(t), null;
        case Zt: {
          var Ge = t.type;
          return ru(Ge) && Cm(t), Gr(t), null;
        }
        case hn: {
          ad(t);
          var at = t.memoizedState;
          if (at === null)
            return Gr(t), null;
          var ln = (t.flags & Le) !== Ue, It = at.rendering;
          if (It === null)
            if (ln)
              cv(at, !1);
            else {
              var rr = f1() && (e === null || (e.flags & Le) === Ue);
              if (!rr)
                for (var $t = t.child; $t !== null; ) {
                  var Gn = Im($t);
                  if (Gn !== null) {
                    ln = !0, t.flags |= Le, cv(at, !1);
                    var ya = Gn.updateQueue;
                    return ya !== null && (t.updateQueue = ya, t.flags |= kt), t.subtreeFlags = Ue, Jw(t, a), ns(t, gS(gl.current, Xp)), t.child;
                  }
                  $t = $t.sibling;
                }
              at.tail !== null && tr() > ux() && (t.flags |= Le, ln = !0, cv(at, !1), t.lanes = np);
            }
          else {
            if (!ln) {
              var ea = Im(It);
              if (ea !== null) {
                t.flags |= Le, ln = !0;
                var Ci = ea.updateQueue;
                if (Ci !== null && (t.updateQueue = Ci, t.flags |= kt), cv(at, !0), at.tail === null && at.tailMode === "hidden" && !It.alternate && !Qr())
                  return Gr(t), null;
              } else // The time it took to render last row is greater than the remaining
              // time we have to render. So rendering one more row would likely
              // exceed it.
              tr() * 2 - at.renderingStartTime > ux() && a !== sa && (t.flags |= Le, ln = !0, cv(at, !1), t.lanes = np);
            }
            if (at.isBackwards)
              It.sibling = t.child, t.child = It;
            else {
              var Ma = at.last;
              Ma !== null ? Ma.sibling = It : t.child = It, at.last = It;
            }
          }
          if (at.tail !== null) {
            var La = at.tail;
            at.rendering = La, at.tail = La.sibling, at.renderingStartTime = tr(), La.sibling = null;
            var ga = gl.current;
            return ln ? ga = gS(ga, Xp) : ga = rd(ga), ns(t, ga), La;
          }
          return Gr(t), null;
        }
        case zt:
          break;
        case He:
        case Yt: {
          FE(t);
          var oo = t.memoizedState, yd = oo !== null;
          if (e !== null) {
            var wv = e.memoizedState, fu = wv !== null;
            fu !== yd && // LegacyHidden doesn't do any hiding — it only pre-renders.
            !se && (t.flags |= Bn);
          }
          return !yd || (t.mode & yt) === je ? Gr(t) : ca(cu, sa) && (Gr(t), t.subtreeFlags & (Rn | kt) && (t.flags |= Bn)), null;
        }
        case Ut:
          return null;
        case Ft:
          return null;
      }
      throw new Error("Unknown unit of work tag (" + t.tag + "). This error is likely caused by a bug in React. Please file an issue.");
    }
    function sk(e, t, a) {
      switch (Qg(t), t.tag) {
        case $: {
          var i = t.type;
          ru(i) && Cm(t);
          var u = t.flags;
          return u & ur ? (t.flags = u & ~ur | Le, (t.mode & Vt) !== je && YS(t), t) : null;
        }
        case re: {
          t.stateNode, nd(t), Ig(t), ES();
          var s = t.flags;
          return (s & ur) !== Ue && (s & Le) === Ue ? (t.flags = s & ~ur | Le, t) : null;
        }
        case de:
          return mS(t), null;
        case ze: {
          ad(t);
          var d = t.memoizedState;
          if (d !== null && d.dehydrated !== null) {
            if (t.alternate === null)
              throw new Error("Threw in newly mounted dehydrated component. This is likely a bug in React. Please file an issue.");
            Kf();
          }
          var m = t.flags;
          return m & ur ? (t.flags = m & ~ur | Le, (t.mode & Vt) !== je && YS(t), t) : null;
        }
        case hn:
          return ad(t), null;
        case be:
          return nd(t), null;
        case _t:
          var y = t.type._context;
          return uS(y, t), null;
        case He:
        case Yt:
          return FE(t), null;
        case Ut:
          return null;
        default:
          return null;
      }
    }
    function P0(e, t, a) {
      switch (Qg(t), t.tag) {
        case $: {
          var i = t.type.childContextTypes;
          i != null && Cm(t);
          break;
        }
        case re: {
          t.stateNode, nd(t), Ig(t), ES();
          break;
        }
        case de: {
          mS(t);
          break;
        }
        case be:
          nd(t);
          break;
        case ze:
          ad(t);
          break;
        case hn:
          ad(t);
          break;
        case _t:
          var u = t.type._context;
          uS(u, t);
          break;
        case He:
        case Yt:
          FE(t);
          break;
      }
    }
    var B0 = null;
    B0 = /* @__PURE__ */ new Set();
    var py = !1, qr = !1, ck = typeof WeakSet == "function" ? WeakSet : Set, we = null, cd = null, fd = null;
    function fk(e) {
      Hl(null, function() {
        throw e;
      }), Os();
    }
    var dk = function(e, t) {
      if (t.props = e.memoizedProps, t.state = e.memoizedState, e.mode & Vt)
        try {
          ou(), t.componentWillUnmount();
        } finally {
          uu(e);
        }
      else
        t.componentWillUnmount();
    };
    function I0(e, t) {
      try {
        is(_r, e);
      } catch (a) {
        Sn(e, t, a);
      }
    }
    function _E(e, t, a) {
      try {
        dk(e, a);
      } catch (i) {
        Sn(e, t, i);
      }
    }
    function pk(e, t, a) {
      try {
        a.componentDidMount();
      } catch (i) {
        Sn(e, t, i);
      }
    }
    function $0(e, t) {
      try {
        W0(e);
      } catch (a) {
        Sn(e, t, a);
      }
    }
    function dd(e, t) {
      var a = e.ref;
      if (a !== null)
        if (typeof a == "function") {
          var i;
          try {
            if (Ye && vt && e.mode & Vt)
              try {
                ou(), i = a(null);
              } finally {
                uu(e);
              }
            else
              i = a(null);
          } catch (u) {
            Sn(e, t, u);
          }
          typeof i == "function" && E("Unexpected return value from a callback ref in %s. A callback ref should not return a function.", Xe(e));
        } else
          a.current = null;
    }
    function vy(e, t, a) {
      try {
        a();
      } catch (i) {
        Sn(e, t, i);
      }
    }
    var Y0 = !1;
    function vk(e, t) {
      wR(e.containerInfo), we = t, hk();
      var a = Y0;
      return Y0 = !1, a;
    }
    function hk() {
      for (; we !== null; ) {
        var e = we, t = e.child;
        (e.subtreeFlags & Pl) !== Ue && t !== null ? (t.return = e, we = t) : mk();
      }
    }
    function mk() {
      for (; we !== null; ) {
        var e = we;
        en(e);
        try {
          yk(e);
        } catch (a) {
          Sn(e, e.return, a);
        }
        gn();
        var t = e.sibling;
        if (t !== null) {
          t.return = e.return, we = t;
          return;
        }
        we = e.return;
      }
    }
    function yk(e) {
      var t = e.alternate, a = e.flags;
      if ((a & er) !== Ue) {
        switch (en(e), e.tag) {
          case I:
          case rt:
          case Qe:
            break;
          case $: {
            if (t !== null) {
              var i = t.memoizedProps, u = t.memoizedState, s = e.stateNode;
              e.type === e.elementType && !xc && (s.props !== e.memoizedProps && E("Expected %s props to match memoized props before getSnapshotBeforeUpdate. This might either be because of a bug in React, or because a component reassigns its own `this.props`. Please file an issue.", Xe(e) || "instance"), s.state !== e.memoizedState && E("Expected %s state to match memoized state before getSnapshotBeforeUpdate. This might either be because of a bug in React, or because a component reassigns its own `this.state`. Please file an issue.", Xe(e) || "instance"));
              var d = s.getSnapshotBeforeUpdate(e.elementType === e.type ? i : El(e.type, i), u);
              {
                var m = B0;
                d === void 0 && !m.has(e.type) && (m.add(e.type), E("%s.getSnapshotBeforeUpdate(): A snapshot value (or null) must be returned. You have returned undefined.", Xe(e)));
              }
              s.__reactInternalSnapshotBeforeUpdate = d;
            }
            break;
          }
          case re: {
            {
              var y = e.stateNode;
              XR(y.containerInfo);
            }
            break;
          }
          case de:
          case nt:
          case be:
          case Zt:
            break;
          default:
            throw new Error("This unit of work tag should not have side-effects. This error is likely caused by a bug in React. Please file an issue.");
        }
        gn();
      }
    }
    function _l(e, t, a) {
      var i = t.updateQueue, u = i !== null ? i.lastEffect : null;
      if (u !== null) {
        var s = u.next, d = s;
        do {
          if ((d.tag & e) === e) {
            var m = d.destroy;
            d.destroy = void 0, m !== void 0 && ((e & Zr) !== Xa ? sl(t) : (e & _r) !== Xa && Ls(t), (e & au) !== Xa && xv(!0), vy(t, a, m), (e & au) !== Xa && xv(!1), (e & Zr) !== Xa ? Yl() : (e & _r) !== Xa && ep());
          }
          d = d.next;
        } while (d !== s);
      }
    }
    function is(e, t) {
      var a = t.updateQueue, i = a !== null ? a.lastEffect : null;
      if (i !== null) {
        var u = i.next, s = u;
        do {
          if ((s.tag & e) === e) {
            (e & Zr) !== Xa ? Jd(t) : (e & _r) !== Xa && nf(t);
            var d = s.create;
            (e & au) !== Xa && xv(!0), s.destroy = d(), (e & au) !== Xa && xv(!1), (e & Zr) !== Xa ? Sh() : (e & _r) !== Xa && Eh();
            {
              var m = s.destroy;
              if (m !== void 0 && typeof m != "function") {
                var y = void 0;
                (s.tag & _r) !== Ue ? y = "useLayoutEffect" : (s.tag & au) !== Ue ? y = "useInsertionEffect" : y = "useEffect";
                var x = void 0;
                m === null ? x = " You returned null. If your effect does not require clean up, return undefined (or nothing)." : typeof m.then == "function" ? x = `

It looks like you wrote ` + y + `(async () => ...) or returned a Promise. Instead, write the async function inside your effect and call it immediately:

` + y + `(() => {
  async function fetchData() {
    // You can await here
    const response = await MyAPI.getData(someId);
    // ...
  }
  fetchData();
}, [someId]); // Or [] if effect doesn't need props or state

Learn more about data fetching with Hooks: https://reactjs.org/link/hooks-data-fetching` : x = " You returned: " + m, E("%s must not return anything besides a function, which is used for clean-up.%s", y, x);
              }
            }
          }
          s = s.next;
        } while (s !== u);
      }
    }
    function gk(e, t) {
      if ((t.flags & kt) !== Ue)
        switch (t.tag) {
          case Tt: {
            var a = t.stateNode.passiveEffectDuration, i = t.memoizedProps, u = i.id, s = i.onPostCommit, d = u0(), m = t.alternate === null ? "mount" : "update";
            l0() && (m = "nested-update"), typeof s == "function" && s(u, m, a, d);
            var y = t.return;
            e: for (; y !== null; ) {
              switch (y.tag) {
                case re:
                  var x = y.stateNode;
                  x.passiveEffectDuration += a;
                  break e;
                case Tt:
                  var R = y.stateNode;
                  R.passiveEffectDuration += a;
                  break e;
              }
              y = y.return;
            }
            break;
          }
        }
    }
    function Sk(e, t, a, i) {
      if ((a.flags & Il) !== Ue)
        switch (a.tag) {
          case I:
          case rt:
          case Qe: {
            if (!qr)
              if (a.mode & Vt)
                try {
                  ou(), is(_r | Cr, a);
                } finally {
                  uu(a);
                }
              else
                is(_r | Cr, a);
            break;
          }
          case $: {
            var u = a.stateNode;
            if (a.flags & kt && !qr)
              if (t === null)
                if (a.type === a.elementType && !xc && (u.props !== a.memoizedProps && E("Expected %s props to match memoized props before componentDidMount. This might either be because of a bug in React, or because a component reassigns its own `this.props`. Please file an issue.", Xe(a) || "instance"), u.state !== a.memoizedState && E("Expected %s state to match memoized state before componentDidMount. This might either be because of a bug in React, or because a component reassigns its own `this.state`. Please file an issue.", Xe(a) || "instance")), a.mode & Vt)
                  try {
                    ou(), u.componentDidMount();
                  } finally {
                    uu(a);
                  }
                else
                  u.componentDidMount();
              else {
                var s = a.elementType === a.type ? t.memoizedProps : El(a.type, t.memoizedProps), d = t.memoizedState;
                if (a.type === a.elementType && !xc && (u.props !== a.memoizedProps && E("Expected %s props to match memoized props before componentDidUpdate. This might either be because of a bug in React, or because a component reassigns its own `this.props`. Please file an issue.", Xe(a) || "instance"), u.state !== a.memoizedState && E("Expected %s state to match memoized state before componentDidUpdate. This might either be because of a bug in React, or because a component reassigns its own `this.state`. Please file an issue.", Xe(a) || "instance")), a.mode & Vt)
                  try {
                    ou(), u.componentDidUpdate(s, d, u.__reactInternalSnapshotBeforeUpdate);
                  } finally {
                    uu(a);
                  }
                else
                  u.componentDidUpdate(s, d, u.__reactInternalSnapshotBeforeUpdate);
              }
            var m = a.updateQueue;
            m !== null && (a.type === a.elementType && !xc && (u.props !== a.memoizedProps && E("Expected %s props to match memoized props before processing the update queue. This might either be because of a bug in React, or because a component reassigns its own `this.props`. Please file an issue.", Xe(a) || "instance"), u.state !== a.memoizedState && E("Expected %s state to match memoized state before processing the update queue. This might either be because of a bug in React, or because a component reassigns its own `this.state`. Please file an issue.", Xe(a) || "instance")), N_(a, m, u));
            break;
          }
          case re: {
            var y = a.updateQueue;
            if (y !== null) {
              var x = null;
              if (a.child !== null)
                switch (a.child.tag) {
                  case de:
                    x = a.child.stateNode;
                    break;
                  case $:
                    x = a.child.stateNode;
                    break;
                }
              N_(a, y, x);
            }
            break;
          }
          case de: {
            var R = a.stateNode;
            if (t === null && a.flags & kt) {
              var M = a.type, O = a.memoizedProps;
              jR(R, M, O);
            }
            break;
          }
          case nt:
            break;
          case be:
            break;
          case Tt: {
            {
              var H = a.memoizedProps, B = H.onCommit, W = H.onRender, he = a.stateNode.effectDuration, Pe = u0(), Me = t === null ? "mount" : "update";
              l0() && (Me = "nested-update"), typeof W == "function" && W(a.memoizedProps.id, Me, a.actualDuration, a.treeBaseDuration, a.actualStartTime, Pe);
              {
                typeof B == "function" && B(a.memoizedProps.id, Me, he, Pe), m1(a);
                var Nt = a.return;
                e: for (; Nt !== null; ) {
                  switch (Nt.tag) {
                    case re:
                      var Rt = Nt.stateNode;
                      Rt.effectDuration += he;
                      break e;
                    case Tt:
                      var U = Nt.stateNode;
                      U.effectDuration += he;
                      break e;
                  }
                  Nt = Nt.return;
                }
              }
            }
            break;
          }
          case ze: {
            bk(e, a);
            break;
          }
          case hn:
          case Zt:
          case zt:
          case He:
          case Yt:
          case Ft:
            break;
          default:
            throw new Error("This unit of work tag should not have side-effects. This error is likely caused by a bug in React. Please file an issue.");
        }
      qr || a.flags & Dn && W0(a);
    }
    function Ek(e) {
      switch (e.tag) {
        case I:
        case rt:
        case Qe: {
          if (e.mode & Vt)
            try {
              ou(), I0(e, e.return);
            } finally {
              uu(e);
            }
          else
            I0(e, e.return);
          break;
        }
        case $: {
          var t = e.stateNode;
          typeof t.componentDidMount == "function" && pk(e, e.return, t), $0(e, e.return);
          break;
        }
        case de: {
          $0(e, e.return);
          break;
        }
      }
    }
    function Ck(e, t) {
      for (var a = null, i = e; ; ) {
        if (i.tag === de) {
          if (a === null) {
            a = i;
            try {
              var u = i.stateNode;
              t ? QR(u) : GR(i.stateNode, i.memoizedProps);
            } catch (d) {
              Sn(e, e.return, d);
            }
          }
        } else if (i.tag === nt) {
          if (a === null)
            try {
              var s = i.stateNode;
              t ? ZR(s) : qR(s, i.memoizedProps);
            } catch (d) {
              Sn(e, e.return, d);
            }
        } else if (!((i.tag === He || i.tag === Yt) && i.memoizedState !== null && i !== e)) {
          if (i.child !== null) {
            i.child.return = i, i = i.child;
            continue;
          }
        }
        if (i === e)
          return;
        for (; i.sibling === null; ) {
          if (i.return === null || i.return === e)
            return;
          a === i && (a = null), i = i.return;
        }
        a === i && (a = null), i.sibling.return = i.return, i = i.sibling;
      }
    }
    function W0(e) {
      var t = e.ref;
      if (t !== null) {
        var a = e.stateNode, i;
        switch (e.tag) {
          case de:
            i = a;
            break;
          default:
            i = a;
        }
        if (typeof t == "function") {
          var u;
          if (e.mode & Vt)
            try {
              ou(), u = t(i);
            } finally {
              uu(e);
            }
          else
            u = t(i);
          typeof u == "function" && E("Unexpected return value from a callback ref in %s. A callback ref should not return a function.", Xe(e));
        } else
          t.hasOwnProperty("current") || E("Unexpected ref object provided for %s. Use either a ref-setter function or React.createRef().", Xe(e)), t.current = i;
      }
    }
    function _k(e) {
      var t = e.alternate;
      t !== null && (t.return = null), e.return = null;
    }
    function Q0(e) {
      var t = e.alternate;
      t !== null && (e.alternate = null, Q0(t));
      {
        if (e.child = null, e.deletions = null, e.sibling = null, e.tag === de) {
          var a = e.stateNode;
          a !== null && kw(a);
        }
        e.stateNode = null, e._debugOwner = null, e.return = null, e.dependencies = null, e.memoizedProps = null, e.memoizedState = null, e.pendingProps = null, e.stateNode = null, e.updateQueue = null;
      }
    }
    function xk(e) {
      for (var t = e.return; t !== null; ) {
        if (Z0(t))
          return t;
        t = t.return;
      }
      throw new Error("Expected to find a host parent. This error is likely caused by a bug in React. Please file an issue.");
    }
    function Z0(e) {
      return e.tag === de || e.tag === re || e.tag === be;
    }
    function G0(e) {
      var t = e;
      e: for (; ; ) {
        for (; t.sibling === null; ) {
          if (t.return === null || Z0(t.return))
            return null;
          t = t.return;
        }
        for (t.sibling.return = t.return, t = t.sibling; t.tag !== de && t.tag !== nt && t.tag !== on; ) {
          if (t.flags & Rn || t.child === null || t.tag === be)
            continue e;
          t.child.return = t, t = t.child;
        }
        if (!(t.flags & Rn))
          return t.stateNode;
      }
    }
    function Tk(e) {
      var t = xk(e);
      switch (t.tag) {
        case de: {
          var a = t.stateNode;
          t.flags & Pa && (XC(a), t.flags &= ~Pa);
          var i = G0(e);
          TE(e, i, a);
          break;
        }
        case re:
        case be: {
          var u = t.stateNode.containerInfo, s = G0(e);
          xE(e, s, u);
          break;
        }
        default:
          throw new Error("Invalid host parent fiber. This error is likely caused by a bug in React. Please file an issue.");
      }
    }
    function xE(e, t, a) {
      var i = e.tag, u = i === de || i === nt;
      if (u) {
        var s = e.stateNode;
        t ? IR(a, s, t) : PR(a, s);
      } else if (i !== be) {
        var d = e.child;
        if (d !== null) {
          xE(d, t, a);
          for (var m = d.sibling; m !== null; )
            xE(m, t, a), m = m.sibling;
        }
      }
    }
    function TE(e, t, a) {
      var i = e.tag, u = i === de || i === nt;
      if (u) {
        var s = e.stateNode;
        t ? BR(a, s, t) : VR(a, s);
      } else if (i !== be) {
        var d = e.child;
        if (d !== null) {
          TE(d, t, a);
          for (var m = d.sibling; m !== null; )
            TE(m, t, a), m = m.sibling;
        }
      }
    }
    var Xr = null, xl = !1;
    function Rk(e, t, a) {
      {
        var i = t;
        e: for (; i !== null; ) {
          switch (i.tag) {
            case de: {
              Xr = i.stateNode, xl = !1;
              break e;
            }
            case re: {
              Xr = i.stateNode.containerInfo, xl = !0;
              break e;
            }
            case be: {
              Xr = i.stateNode.containerInfo, xl = !0;
              break e;
            }
          }
          i = i.return;
        }
        if (Xr === null)
          throw new Error("Expected to find a host parent. This error is likely caused by a bug in React. Please file an issue.");
        q0(e, t, a), Xr = null, xl = !1;
      }
      _k(a);
    }
    function ls(e, t, a) {
      for (var i = a.child; i !== null; )
        q0(e, t, i), i = i.sibling;
    }
    function q0(e, t, a) {
      switch (qd(a), a.tag) {
        case de:
          qr || dd(a, t);
        case nt: {
          {
            var i = Xr, u = xl;
            Xr = null, ls(e, t, a), Xr = i, xl = u, Xr !== null && (xl ? YR(Xr, a.stateNode) : $R(Xr, a.stateNode));
          }
          return;
        }
        case on: {
          Xr !== null && (xl ? WR(Xr, a.stateNode) : zg(Xr, a.stateNode));
          return;
        }
        case be: {
          {
            var s = Xr, d = xl;
            Xr = a.stateNode.containerInfo, xl = !0, ls(e, t, a), Xr = s, xl = d;
          }
          return;
        }
        case I:
        case rt:
        case St:
        case Qe: {
          if (!qr) {
            var m = a.updateQueue;
            if (m !== null) {
              var y = m.lastEffect;
              if (y !== null) {
                var x = y.next, R = x;
                do {
                  var M = R, O = M.destroy, H = M.tag;
                  O !== void 0 && ((H & au) !== Xa ? vy(a, t, O) : (H & _r) !== Xa && (Ls(a), a.mode & Vt ? (ou(), vy(a, t, O), uu(a)) : vy(a, t, O), ep())), R = R.next;
                } while (R !== x);
              }
            }
          }
          ls(e, t, a);
          return;
        }
        case $: {
          if (!qr) {
            dd(a, t);
            var B = a.stateNode;
            typeof B.componentWillUnmount == "function" && _E(a, t, B);
          }
          ls(e, t, a);
          return;
        }
        case zt: {
          ls(e, t, a);
          return;
        }
        case He: {
          if (
            // TODO: Remove this dead flag
            a.mode & yt
          ) {
            var W = qr;
            qr = W || a.memoizedState !== null, ls(e, t, a), qr = W;
          } else
            ls(e, t, a);
          break;
        }
        default: {
          ls(e, t, a);
          return;
        }
      }
    }
    function wk(e) {
      e.memoizedState;
    }
    function bk(e, t) {
      var a = t.memoizedState;
      if (a === null) {
        var i = t.alternate;
        if (i !== null) {
          var u = i.memoizedState;
          if (u !== null) {
            var s = u.dehydrated;
            s !== null && fw(s);
          }
        }
      }
    }
    function X0(e) {
      var t = e.updateQueue;
      if (t !== null) {
        e.updateQueue = null;
        var a = e.stateNode;
        a === null && (a = e.stateNode = new ck()), t.forEach(function(i) {
          var u = x1.bind(null, e, i);
          if (!a.has(i)) {
            if (a.add(i), oa)
              if (cd !== null && fd !== null)
                _v(fd, cd);
              else
                throw Error("Expected finished root and lanes to be set. This is a bug in React.");
            i.then(u, u);
          }
        });
      }
    }
    function kk(e, t, a) {
      cd = a, fd = e, en(t), K0(t, e), en(t), cd = null, fd = null;
    }
    function Tl(e, t, a) {
      var i = t.deletions;
      if (i !== null)
        for (var u = 0; u < i.length; u++) {
          var s = i[u];
          try {
            Rk(e, t, s);
          } catch (y) {
            Sn(s, t, y);
          }
        }
      var d = Ml();
      if (t.subtreeFlags & Bl)
        for (var m = t.child; m !== null; )
          en(m), K0(m, e), m = m.sibling;
      en(d);
    }
    function K0(e, t, a) {
      var i = e.alternate, u = e.flags;
      switch (e.tag) {
        case I:
        case rt:
        case St:
        case Qe: {
          if (Tl(t, e), su(e), u & kt) {
            try {
              _l(au | Cr, e, e.return), is(au | Cr, e);
            } catch (Ge) {
              Sn(e, e.return, Ge);
            }
            if (e.mode & Vt) {
              try {
                ou(), _l(_r | Cr, e, e.return);
              } catch (Ge) {
                Sn(e, e.return, Ge);
              }
              uu(e);
            } else
              try {
                _l(_r | Cr, e, e.return);
              } catch (Ge) {
                Sn(e, e.return, Ge);
              }
          }
          return;
        }
        case $: {
          Tl(t, e), su(e), u & Dn && i !== null && dd(i, i.return);
          return;
        }
        case de: {
          Tl(t, e), su(e), u & Dn && i !== null && dd(i, i.return);
          {
            if (e.flags & Pa) {
              var s = e.stateNode;
              try {
                XC(s);
              } catch (Ge) {
                Sn(e, e.return, Ge);
              }
            }
            if (u & kt) {
              var d = e.stateNode;
              if (d != null) {
                var m = e.memoizedProps, y = i !== null ? i.memoizedProps : m, x = e.type, R = e.updateQueue;
                if (e.updateQueue = null, R !== null)
                  try {
                    FR(d, R, x, y, m, e);
                  } catch (Ge) {
                    Sn(e, e.return, Ge);
                  }
              }
            }
          }
          return;
        }
        case nt: {
          if (Tl(t, e), su(e), u & kt) {
            if (e.stateNode === null)
              throw new Error("This should have a text node initialized. This error is likely caused by a bug in React. Please file an issue.");
            var M = e.stateNode, O = e.memoizedProps, H = i !== null ? i.memoizedProps : O;
            try {
              HR(M, H, O);
            } catch (Ge) {
              Sn(e, e.return, Ge);
            }
          }
          return;
        }
        case re: {
          if (Tl(t, e), su(e), u & kt && i !== null) {
            var B = i.memoizedState;
            if (B.isDehydrated)
              try {
                cw(t.containerInfo);
              } catch (Ge) {
                Sn(e, e.return, Ge);
              }
          }
          return;
        }
        case be: {
          Tl(t, e), su(e);
          return;
        }
        case ze: {
          Tl(t, e), su(e);
          var W = e.child;
          if (W.flags & Bn) {
            var he = W.stateNode, Pe = W.memoizedState, Me = Pe !== null;
            if (he.isHidden = Me, Me) {
              var Nt = W.alternate !== null && W.alternate.memoizedState !== null;
              Nt || o1();
            }
          }
          if (u & kt) {
            try {
              wk(e);
            } catch (Ge) {
              Sn(e, e.return, Ge);
            }
            X0(e);
          }
          return;
        }
        case He: {
          var Rt = i !== null && i.memoizedState !== null;
          if (
            // TODO: Remove this dead flag
            e.mode & yt
          ) {
            var U = qr;
            qr = U || Rt, Tl(t, e), qr = U;
          } else
            Tl(t, e);
          if (su(e), u & Bn) {
            var Q = e.stateNode, j = e.memoizedState, ne = j !== null, Ee = e;
            if (Q.isHidden = ne, ne && !Rt && (Ee.mode & yt) !== je) {
              we = Ee;
              for (var me = Ee.child; me !== null; )
                we = me, Ok(me), me = me.sibling;
            }
            Ck(Ee, ne);
          }
          return;
        }
        case hn: {
          Tl(t, e), su(e), u & kt && X0(e);
          return;
        }
        case zt:
          return;
        default: {
          Tl(t, e), su(e);
          return;
        }
      }
    }
    function su(e) {
      var t = e.flags;
      if (t & Rn) {
        try {
          Tk(e);
        } catch (a) {
          Sn(e, e.return, a);
        }
        e.flags &= ~Rn;
      }
      t & ia && (e.flags &= ~ia);
    }
    function Dk(e, t, a) {
      cd = a, fd = t, we = e, J0(e, t, a), cd = null, fd = null;
    }
    function J0(e, t, a) {
      for (var i = (e.mode & yt) !== je; we !== null; ) {
        var u = we, s = u.child;
        if (u.tag === He && i) {
          var d = u.memoizedState !== null, m = d || py;
          if (m) {
            RE(e, t, a);
            continue;
          } else {
            var y = u.alternate, x = y !== null && y.memoizedState !== null, R = x || qr, M = py, O = qr;
            py = m, qr = R, qr && !O && (we = u, Nk(u));
            for (var H = s; H !== null; )
              we = H, J0(
                H,
                // New root; bubble back up to here and stop.
                t,
                a
              ), H = H.sibling;
            we = u, py = M, qr = O, RE(e, t, a);
            continue;
          }
        }
        (u.subtreeFlags & Il) !== Ue && s !== null ? (s.return = u, we = s) : RE(e, t, a);
      }
    }
    function RE(e, t, a) {
      for (; we !== null; ) {
        var i = we;
        if ((i.flags & Il) !== Ue) {
          var u = i.alternate;
          en(i);
          try {
            Sk(t, u, i, a);
          } catch (d) {
            Sn(i, i.return, d);
          }
          gn();
        }
        if (i === e) {
          we = null;
          return;
        }
        var s = i.sibling;
        if (s !== null) {
          s.return = i.return, we = s;
          return;
        }
        we = i.return;
      }
    }
    function Ok(e) {
      for (; we !== null; ) {
        var t = we, a = t.child;
        switch (t.tag) {
          case I:
          case rt:
          case St:
          case Qe: {
            if (t.mode & Vt)
              try {
                ou(), _l(_r, t, t.return);
              } finally {
                uu(t);
              }
            else
              _l(_r, t, t.return);
            break;
          }
          case $: {
            dd(t, t.return);
            var i = t.stateNode;
            typeof i.componentWillUnmount == "function" && _E(t, t.return, i);
            break;
          }
          case de: {
            dd(t, t.return);
            break;
          }
          case He: {
            var u = t.memoizedState !== null;
            if (u) {
              ex(e);
              continue;
            }
            break;
          }
        }
        a !== null ? (a.return = t, we = a) : ex(e);
      }
    }
    function ex(e) {
      for (; we !== null; ) {
        var t = we;
        if (t === e) {
          we = null;
          return;
        }
        var a = t.sibling;
        if (a !== null) {
          a.return = t.return, we = a;
          return;
        }
        we = t.return;
      }
    }
    function Nk(e) {
      for (; we !== null; ) {
        var t = we, a = t.child;
        if (t.tag === He) {
          var i = t.memoizedState !== null;
          if (i) {
            tx(e);
            continue;
          }
        }
        a !== null ? (a.return = t, we = a) : tx(e);
      }
    }
    function tx(e) {
      for (; we !== null; ) {
        var t = we;
        en(t);
        try {
          Ek(t);
        } catch (i) {
          Sn(t, t.return, i);
        }
        if (gn(), t === e) {
          we = null;
          return;
        }
        var a = t.sibling;
        if (a !== null) {
          a.return = t.return, we = a;
          return;
        }
        we = t.return;
      }
    }
    function Mk(e, t, a, i) {
      we = t, Lk(t, e, a, i);
    }
    function Lk(e, t, a, i) {
      for (; we !== null; ) {
        var u = we, s = u.child;
        (u.subtreeFlags & ul) !== Ue && s !== null ? (s.return = u, we = s) : Ak(e, t, a, i);
      }
    }
    function Ak(e, t, a, i) {
      for (; we !== null; ) {
        var u = we;
        if ((u.flags & aa) !== Ue) {
          en(u);
          try {
            zk(t, u, a, i);
          } catch (d) {
            Sn(u, u.return, d);
          }
          gn();
        }
        if (u === e) {
          we = null;
          return;
        }
        var s = u.sibling;
        if (s !== null) {
          s.return = u.return, we = s;
          return;
        }
        we = u.return;
      }
    }
    function zk(e, t, a, i) {
      switch (t.tag) {
        case I:
        case rt:
        case Qe: {
          if (t.mode & Vt) {
            $S();
            try {
              is(Zr | Cr, t);
            } finally {
              IS(t);
            }
          } else
            is(Zr | Cr, t);
          break;
        }
      }
    }
    function Uk(e) {
      we = e, jk();
    }
    function jk() {
      for (; we !== null; ) {
        var e = we, t = e.child;
        if ((we.flags & Va) !== Ue) {
          var a = e.deletions;
          if (a !== null) {
            for (var i = 0; i < a.length; i++) {
              var u = a[i];
              we = u, Vk(u, e);
            }
            {
              var s = e.alternate;
              if (s !== null) {
                var d = s.child;
                if (d !== null) {
                  s.child = null;
                  do {
                    var m = d.sibling;
                    d.sibling = null, d = m;
                  } while (d !== null);
                }
              }
            }
            we = e;
          }
        }
        (e.subtreeFlags & ul) !== Ue && t !== null ? (t.return = e, we = t) : Fk();
      }
    }
    function Fk() {
      for (; we !== null; ) {
        var e = we;
        (e.flags & aa) !== Ue && (en(e), Hk(e), gn());
        var t = e.sibling;
        if (t !== null) {
          t.return = e.return, we = t;
          return;
        }
        we = e.return;
      }
    }
    function Hk(e) {
      switch (e.tag) {
        case I:
        case rt:
        case Qe: {
          e.mode & Vt ? ($S(), _l(Zr | Cr, e, e.return), IS(e)) : _l(Zr | Cr, e, e.return);
          break;
        }
      }
    }
    function Vk(e, t) {
      for (; we !== null; ) {
        var a = we;
        en(a), Bk(a, t), gn();
        var i = a.child;
        i !== null ? (i.return = a, we = i) : Pk(e);
      }
    }
    function Pk(e) {
      for (; we !== null; ) {
        var t = we, a = t.sibling, i = t.return;
        if (Q0(t), t === e) {
          we = null;
          return;
        }
        if (a !== null) {
          a.return = i, we = a;
          return;
        }
        we = i;
      }
    }
    function Bk(e, t) {
      switch (e.tag) {
        case I:
        case rt:
        case Qe: {
          e.mode & Vt ? ($S(), _l(Zr, e, t), IS(e)) : _l(Zr, e, t);
          break;
        }
      }
    }
    function Ik(e) {
      switch (e.tag) {
        case I:
        case rt:
        case Qe: {
          try {
            is(_r | Cr, e);
          } catch (a) {
            Sn(e, e.return, a);
          }
          break;
        }
        case $: {
          var t = e.stateNode;
          try {
            t.componentDidMount();
          } catch (a) {
            Sn(e, e.return, a);
          }
          break;
        }
      }
    }
    function $k(e) {
      switch (e.tag) {
        case I:
        case rt:
        case Qe: {
          try {
            is(Zr | Cr, e);
          } catch (t) {
            Sn(e, e.return, t);
          }
          break;
        }
      }
    }
    function Yk(e) {
      switch (e.tag) {
        case I:
        case rt:
        case Qe: {
          try {
            _l(_r | Cr, e, e.return);
          } catch (a) {
            Sn(e, e.return, a);
          }
          break;
        }
        case $: {
          var t = e.stateNode;
          typeof t.componentWillUnmount == "function" && _E(e, e.return, t);
          break;
        }
      }
    }
    function Wk(e) {
      switch (e.tag) {
        case I:
        case rt:
        case Qe:
          try {
            _l(Zr | Cr, e, e.return);
          } catch (t) {
            Sn(e, e.return, t);
          }
      }
    }
    if (typeof Symbol == "function" && Symbol.for) {
      var fv = Symbol.for;
      fv("selector.component"), fv("selector.has_pseudo_class"), fv("selector.role"), fv("selector.test_id"), fv("selector.text");
    }
    var Qk = [];
    function Zk() {
      Qk.forEach(function(e) {
        return e();
      });
    }
    var Gk = p.ReactCurrentActQueue;
    function qk(e) {
      {
        var t = (
          // $FlowExpectedError – Flow doesn't know about IS_REACT_ACT_ENVIRONMENT global
          typeof IS_REACT_ACT_ENVIRONMENT < "u" ? IS_REACT_ACT_ENVIRONMENT : void 0
        ), a = typeof jest < "u";
        return a && t !== !1;
      }
    }
    function nx() {
      {
        var e = (
          // $FlowExpectedError – Flow doesn't know about IS_REACT_ACT_ENVIRONMENT global
          typeof IS_REACT_ACT_ENVIRONMENT < "u" ? IS_REACT_ACT_ENVIRONMENT : void 0
        );
        return !e && Gk.current !== null && E("The current testing environment is not configured to support act(...)"), e;
      }
    }
    var Xk = Math.ceil, wE = p.ReactCurrentDispatcher, bE = p.ReactCurrentOwner, Kr = p.ReactCurrentBatchConfig, Rl = p.ReactCurrentActQueue, Rr = (
      /*             */
      0
    ), rx = (
      /*               */
      1
    ), Jr = (
      /*                */
      2
    ), Gi = (
      /*                */
      4
    ), ao = 0, dv = 1, Tc = 2, hy = 3, pv = 4, ax = 5, kE = 6, Ot = Rr, Oa = null, Fn = null, wr = X, cu = X, DE = qo(X), br = ao, vv = null, my = X, hv = X, yy = X, mv = null, Ka = null, OE = 0, ix = 500, lx = 1 / 0, Kk = 500, io = null;
    function yv() {
      lx = tr() + Kk;
    }
    function ux() {
      return lx;
    }
    var gy = !1, NE = null, pd = null, Rc = !1, us = null, gv = X, ME = [], LE = null, Jk = 50, Sv = 0, AE = null, zE = !1, Sy = !1, e1 = 50, vd = 0, Ey = null, Ev = un, Cy = X, ox = !1;
    function _y() {
      return Oa;
    }
    function Na() {
      return (Ot & (Jr | Gi)) !== Rr ? tr() : (Ev !== un || (Ev = tr()), Ev);
    }
    function os(e) {
      var t = e.mode;
      if ((t & yt) === je)
        return We;
      if ((Ot & Jr) !== Rr && wr !== X)
        return Ws(wr);
      var a = Gw() !== Zw;
      if (a) {
        if (Kr.transition !== null) {
          var i = Kr.transition;
          i._updatedFibers || (i._updatedFibers = /* @__PURE__ */ new Set()), i._updatedFibers.add(e);
        }
        return Cy === jt && (Cy = op()), Cy;
      }
      var u = Qa();
      if (u !== jt)
        return u;
      var s = LR();
      return s;
    }
    function t1(e) {
      var t = e.mode;
      return (t & yt) === je ? We : wh();
    }
    function kr(e, t, a, i) {
      R1(), ox && E("useInsertionEffect must not schedule updates."), zE && (Sy = !0), Ho(e, a, i), (Ot & Jr) !== X && e === Oa ? k1(t) : (oa && Gs(e, t, a), D1(t), e === Oa && ((Ot & Jr) === Rr && (hv = ut(hv, a)), br === pv && ss(e, wr)), Ja(e, i), a === We && Ot === Rr && (t.mode & yt) === je && // Treat `act` as if it's inside `batchedUpdates`, even in legacy mode.
      !Rl.isBatchingLegacy && (yv(), o_()));
    }
    function n1(e, t, a) {
      var i = e.current;
      i.lanes = t, Ho(e, t, a), Ja(e, a);
    }
    function r1(e) {
      return (
        // TODO: Remove outdated deferRenderPhaseUpdateToNextBatch experiment. We
        // decided not to enable it.
        (Ot & Jr) !== Rr
      );
    }
    function Ja(e, t) {
      var a = e.callbackNode;
      xf(e, t);
      var i = _f(e, e === Oa ? wr : X);
      if (i === X) {
        a !== null && Tx(a), e.callbackNode = null, e.callbackPriority = jt;
        return;
      }
      var u = Zl(i), s = e.callbackPriority;
      if (s === u && // Special case related to `act`. If the currently scheduled task is a
      // Scheduler task, rather than an `act` task, cancel it and re-scheduled
      // on the `act` queue.
      !(Rl.current !== null && a !== BE)) {
        a == null && s !== We && E("Expected scheduled callback to exist. This error is likely caused by a bug in React. Please file an issue.");
        return;
      }
      a != null && Tx(a);
      var d;
      if (u === We)
        e.tag === Xo ? (Rl.isBatchingLegacy !== null && (Rl.didScheduleLegacyUpdate = !0), Nw(fx.bind(null, e))) : u_(fx.bind(null, e)), Rl.current !== null ? Rl.current.push(Ko) : zR(function() {
          (Ot & (Jr | Gi)) === Rr && Ko();
        }), d = null;
      else {
        var m;
        switch (Lh(i)) {
          case Br:
            m = Ms;
            break;
          case Hi:
            m = $l;
            break;
          case Ya:
            m = ol;
            break;
          case Wa:
            m = Mu;
            break;
          default:
            m = ol;
            break;
        }
        d = IE(m, sx.bind(null, e));
      }
      e.callbackPriority = u, e.callbackNode = d;
    }
    function sx(e, t) {
      if (Cb(), Ev = un, Cy = X, (Ot & (Jr | Gi)) !== Rr)
        throw new Error("Should not already be working.");
      var a = e.callbackNode, i = uo();
      if (i && e.callbackNode !== a)
        return null;
      var u = _f(e, e === Oa ? wr : X);
      if (u === X)
        return null;
      var s = !Rf(e, u) && !Rh(e, u) && !t, d = s ? p1(e, u) : Ty(e, u);
      if (d !== ao) {
        if (d === Tc) {
          var m = Tf(e);
          m !== X && (u = m, d = UE(e, m));
        }
        if (d === dv) {
          var y = vv;
          throw wc(e, X), ss(e, u), Ja(e, tr()), y;
        }
        if (d === kE)
          ss(e, u);
        else {
          var x = !Rf(e, u), R = e.current.alternate;
          if (x && !i1(R)) {
            if (d = Ty(e, u), d === Tc) {
              var M = Tf(e);
              M !== X && (u = M, d = UE(e, M));
            }
            if (d === dv) {
              var O = vv;
              throw wc(e, X), ss(e, u), Ja(e, tr()), O;
            }
          }
          e.finishedWork = R, e.finishedLanes = u, a1(e, d, u);
        }
      }
      return Ja(e, tr()), e.callbackNode === a ? sx.bind(null, e) : null;
    }
    function UE(e, t) {
      var a = mv;
      if (kf(e)) {
        var i = wc(e, t);
        i.flags |= Mr, Tw(e.containerInfo);
      }
      var u = Ty(e, t);
      if (u !== Tc) {
        var s = Ka;
        Ka = a, s !== null && cx(s);
      }
      return u;
    }
    function cx(e) {
      Ka === null ? Ka = e : Ka.push.apply(Ka, e);
    }
    function a1(e, t, a) {
      switch (t) {
        case ao:
        case dv:
          throw new Error("Root did not complete. This is a bug in React.");
        case Tc: {
          bc(e, Ka, io);
          break;
        }
        case hy: {
          if (ss(e, a), Iu(a) && // do not delay if we're inside an act() scope
          !Rx()) {
            var i = OE + ix - tr();
            if (i > 10) {
              var u = _f(e, X);
              if (u !== X)
                break;
              var s = e.suspendedLanes;
              if (!$u(s, a)) {
                Na(), wf(e, s);
                break;
              }
              e.timeoutHandle = Lg(bc.bind(null, e, Ka, io), i);
              break;
            }
          }
          bc(e, Ka, io);
          break;
        }
        case pv: {
          if (ss(e, a), lp(a))
            break;
          if (!Rx()) {
            var d = mi(e, a), m = d, y = tr() - m, x = T1(y) - y;
            if (x > 10) {
              e.timeoutHandle = Lg(bc.bind(null, e, Ka, io), x);
              break;
            }
          }
          bc(e, Ka, io);
          break;
        }
        case ax: {
          bc(e, Ka, io);
          break;
        }
        default:
          throw new Error("Unknown root exit status.");
      }
    }
    function i1(e) {
      for (var t = e; ; ) {
        if (t.flags & Ao) {
          var a = t.updateQueue;
          if (a !== null) {
            var i = a.stores;
            if (i !== null)
              for (var u = 0; u < i.length; u++) {
                var s = i[u], d = s.getSnapshot, m = s.value;
                try {
                  if (!ee(d(), m))
                    return !1;
                } catch {
                  return !1;
                }
              }
          }
        }
        var y = t.child;
        if (t.subtreeFlags & Ao && y !== null) {
          y.return = t, t = y;
          continue;
        }
        if (t === e)
          return !0;
        for (; t.sibling === null; ) {
          if (t.return === null || t.return === e)
            return !0;
          t = t.return;
        }
        t.sibling.return = t.return, t = t.sibling;
      }
      return !0;
    }
    function ss(e, t) {
      t = Qs(t, yy), t = Qs(t, hv), Dh(e, t);
    }
    function fx(e) {
      if (_b(), (Ot & (Jr | Gi)) !== Rr)
        throw new Error("Should not already be working.");
      uo();
      var t = _f(e, X);
      if (!ca(t, We))
        return Ja(e, tr()), null;
      var a = Ty(e, t);
      if (e.tag !== Xo && a === Tc) {
        var i = Tf(e);
        i !== X && (t = i, a = UE(e, i));
      }
      if (a === dv) {
        var u = vv;
        throw wc(e, X), ss(e, t), Ja(e, tr()), u;
      }
      if (a === kE)
        throw new Error("Root did not complete. This is a bug in React.");
      var s = e.current.alternate;
      return e.finishedWork = s, e.finishedLanes = t, bc(e, Ka, io), Ja(e, tr()), null;
    }
    function l1(e, t) {
      t !== X && (bf(e, ut(t, We)), Ja(e, tr()), (Ot & (Jr | Gi)) === Rr && (yv(), Ko()));
    }
    function jE(e, t) {
      var a = Ot;
      Ot |= rx;
      try {
        return e(t);
      } finally {
        Ot = a, Ot === Rr && // Treat `act` as if it's inside `batchedUpdates`, even in legacy mode.
        !Rl.isBatchingLegacy && (yv(), o_());
      }
    }
    function u1(e, t, a, i, u) {
      var s = Qa(), d = Kr.transition;
      try {
        return Kr.transition = null, Wn(Br), e(t, a, i, u);
      } finally {
        Wn(s), Kr.transition = d, Ot === Rr && yv();
      }
    }
    function lo(e) {
      us !== null && us.tag === Xo && (Ot & (Jr | Gi)) === Rr && uo();
      var t = Ot;
      Ot |= rx;
      var a = Kr.transition, i = Qa();
      try {
        return Kr.transition = null, Wn(Br), e ? e() : void 0;
      } finally {
        Wn(i), Kr.transition = a, Ot = t, (Ot & (Jr | Gi)) === Rr && Ko();
      }
    }
    function dx() {
      return (Ot & (Jr | Gi)) !== Rr;
    }
    function xy(e, t) {
      ha(DE, cu, e), cu = ut(cu, t);
    }
    function FE(e) {
      cu = DE.current, va(DE, e);
    }
    function wc(e, t) {
      e.finishedWork = null, e.finishedLanes = X;
      var a = e.timeoutHandle;
      if (a !== Ag && (e.timeoutHandle = Ag, AR(a)), Fn !== null)
        for (var i = Fn.return; i !== null; ) {
          var u = i.alternate;
          P0(u, i), i = i.return;
        }
      Oa = e;
      var s = kc(e.current, null);
      return Fn = s, wr = cu = t, br = ao, vv = null, my = X, hv = X, yy = X, mv = null, Ka = null, nb(), yl.discardPendingWarnings(), s;
    }
    function px(e, t) {
      do {
        var a = Fn;
        try {
          if (Lm(), j_(), gn(), bE.current = null, a === null || a.return === null) {
            br = dv, vv = t, Fn = null;
            return;
          }
          if (Ye && a.mode & Vt && oy(a, !0), Ze)
            if (wa(), t !== null && typeof t == "object" && typeof t.then == "function") {
              var i = t;
              Fi(a, i, wr);
            } else
              As(a, t, wr);
          Nb(e, a.return, a, t, wr), yx(a);
        } catch (u) {
          t = u, Fn === a && a !== null ? (a = a.return, Fn = a) : a = Fn;
          continue;
        }
        return;
      } while (!0);
    }
    function vx() {
      var e = wE.current;
      return wE.current = ry, e === null ? ry : e;
    }
    function hx(e) {
      wE.current = e;
    }
    function o1() {
      OE = tr();
    }
    function Cv(e) {
      my = ut(e, my);
    }
    function s1() {
      br === ao && (br = hy);
    }
    function HE() {
      (br === ao || br === hy || br === Tc) && (br = pv), Oa !== null && (Ys(my) || Ys(hv)) && ss(Oa, wr);
    }
    function c1(e) {
      br !== pv && (br = Tc), mv === null ? mv = [e] : mv.push(e);
    }
    function f1() {
      return br === ao;
    }
    function Ty(e, t) {
      var a = Ot;
      Ot |= Jr;
      var i = vx();
      if (Oa !== e || wr !== t) {
        if (oa) {
          var u = e.memoizedUpdaters;
          u.size > 0 && (_v(e, wr), u.clear()), Oh(e, t);
        }
        io = dp(), wc(e, t);
      }
      Uu(t);
      do
        try {
          d1();
          break;
        } catch (s) {
          px(e, s);
        }
      while (!0);
      if (Lm(), Ot = a, hx(i), Fn !== null)
        throw new Error("Cannot commit an incomplete root. This error is likely caused by a bug in React. Please file an issue.");
      return rf(), Oa = null, wr = X, br;
    }
    function d1() {
      for (; Fn !== null; )
        mx(Fn);
    }
    function p1(e, t) {
      var a = Ot;
      Ot |= Jr;
      var i = vx();
      if (Oa !== e || wr !== t) {
        if (oa) {
          var u = e.memoizedUpdaters;
          u.size > 0 && (_v(e, wr), u.clear()), Oh(e, t);
        }
        io = dp(), yv(), wc(e, t);
      }
      Uu(t);
      do
        try {
          v1();
          break;
        } catch (s) {
          px(e, s);
        }
      while (!0);
      return Lm(), hx(i), Ot = a, Fn !== null ? (Ch(), ao) : (rf(), Oa = null, wr = X, br);
    }
    function v1() {
      for (; Fn !== null && !Yd(); )
        mx(Fn);
    }
    function mx(e) {
      var t = e.alternate;
      en(e);
      var a;
      (e.mode & Vt) !== je ? (BS(e), a = VE(t, e, cu), oy(e, !0)) : a = VE(t, e, cu), gn(), e.memoizedProps = e.pendingProps, a === null ? yx(e) : Fn = a, bE.current = null;
    }
    function yx(e) {
      var t = e;
      do {
        var a = t.alternate, i = t.return;
        if ((t.flags & Ns) === Ue) {
          en(t);
          var u = void 0;
          if ((t.mode & Vt) === je ? u = V0(a, t, cu) : (BS(t), u = V0(a, t, cu), oy(t, !1)), gn(), u !== null) {
            Fn = u;
            return;
          }
        } else {
          var s = sk(a, t);
          if (s !== null) {
            s.flags &= hh, Fn = s;
            return;
          }
          if ((t.mode & Vt) !== je) {
            oy(t, !1);
            for (var d = t.actualDuration, m = t.child; m !== null; )
              d += m.actualDuration, m = m.sibling;
            t.actualDuration = d;
          }
          if (i !== null)
            i.flags |= Ns, i.subtreeFlags = Ue, i.deletions = null;
          else {
            br = kE, Fn = null;
            return;
          }
        }
        var y = t.sibling;
        if (y !== null) {
          Fn = y;
          return;
        }
        t = i, Fn = t;
      } while (t !== null);
      br === ao && (br = ax);
    }
    function bc(e, t, a) {
      var i = Qa(), u = Kr.transition;
      try {
        Kr.transition = null, Wn(Br), h1(e, t, a, i);
      } finally {
        Kr.transition = u, Wn(i);
      }
      return null;
    }
    function h1(e, t, a, i) {
      do
        uo();
      while (us !== null);
      if (w1(), (Ot & (Jr | Gi)) !== Rr)
        throw new Error("Should not already be working.");
      var u = e.finishedWork, s = e.finishedLanes;
      if (Xd(s), u === null)
        return Kd(), null;
      if (s === X && E("root.finishedLanes should not be empty during a commit. This is a bug in React."), e.finishedWork = null, e.finishedLanes = X, u === e.current)
        throw new Error("Cannot commit the same tree as before. This error is likely caused by a bug in React. Please file an issue.");
      e.callbackNode = null, e.callbackPriority = jt;
      var d = ut(u.lanes, u.childLanes);
      cp(e, d), e === Oa && (Oa = null, Fn = null, wr = X), ((u.subtreeFlags & ul) !== Ue || (u.flags & ul) !== Ue) && (Rc || (Rc = !0, LE = a, IE(ol, function() {
        return uo(), null;
      })));
      var m = (u.subtreeFlags & (Pl | Bl | Il | ul)) !== Ue, y = (u.flags & (Pl | Bl | Il | ul)) !== Ue;
      if (m || y) {
        var x = Kr.transition;
        Kr.transition = null;
        var R = Qa();
        Wn(Br);
        var M = Ot;
        Ot |= Gi, bE.current = null, vk(e, u), o0(), kk(e, u, s), bR(e.containerInfo), e.current = u, zs(s), Dk(u, e, s), Us(), Wd(), Ot = M, Wn(R), Kr.transition = x;
      } else
        e.current = u, o0();
      var O = Rc;
      if (Rc ? (Rc = !1, us = e, gv = s) : (vd = 0, Ey = null), d = e.pendingLanes, d === X && (pd = null), O || Cx(e.current, !1), Zd(u.stateNode, i), oa && e.memoizedUpdaters.clear(), Zk(), Ja(e, tr()), t !== null)
        for (var H = e.onRecoverableError, B = 0; B < t.length; B++) {
          var W = t[B], he = W.stack, Pe = W.digest;
          H(W.value, {
            componentStack: he,
            digest: Pe
          });
        }
      if (gy) {
        gy = !1;
        var Me = NE;
        throw NE = null, Me;
      }
      return ca(gv, We) && e.tag !== Xo && uo(), d = e.pendingLanes, ca(d, We) ? (Eb(), e === AE ? Sv++ : (Sv = 0, AE = e)) : Sv = 0, Ko(), Kd(), null;
    }
    function uo() {
      if (us !== null) {
        var e = Lh(gv), t = Xs(Ya, e), a = Kr.transition, i = Qa();
        try {
          return Kr.transition = null, Wn(t), y1();
        } finally {
          Wn(i), Kr.transition = a;
        }
      }
      return !1;
    }
    function m1(e) {
      ME.push(e), Rc || (Rc = !0, IE(ol, function() {
        return uo(), null;
      }));
    }
    function y1() {
      if (us === null)
        return !1;
      var e = LE;
      LE = null;
      var t = us, a = gv;
      if (us = null, gv = X, (Ot & (Jr | Gi)) !== Rr)
        throw new Error("Cannot flush passive effects while already rendering.");
      zE = !0, Sy = !1, zu(a);
      var i = Ot;
      Ot |= Gi, Uk(t.current), Mk(t, t.current, a, e);
      {
        var u = ME;
        ME = [];
        for (var s = 0; s < u.length; s++) {
          var d = u[s];
          gk(t, d);
        }
      }
      tp(), Cx(t.current, !0), Ot = i, Ko(), Sy ? t === Ey ? vd++ : (vd = 0, Ey = t) : vd = 0, zE = !1, Sy = !1, Gd(t);
      {
        var m = t.current.stateNode;
        m.effectDuration = 0, m.passiveEffectDuration = 0;
      }
      return !0;
    }
    function gx(e) {
      return pd !== null && pd.has(e);
    }
    function g1(e) {
      pd === null ? pd = /* @__PURE__ */ new Set([e]) : pd.add(e);
    }
    function S1(e) {
      gy || (gy = !0, NE = e);
    }
    var E1 = S1;
    function Sx(e, t, a) {
      var i = _c(a, t), u = m0(e, i, We), s = es(e, u, We), d = Na();
      s !== null && (Ho(s, We, d), Ja(s, d));
    }
    function Sn(e, t, a) {
      if (fk(a), xv(!1), e.tag === re) {
        Sx(e, e, a);
        return;
      }
      var i = null;
      for (i = t; i !== null; ) {
        if (i.tag === re) {
          Sx(i, e, a);
          return;
        } else if (i.tag === $) {
          var u = i.type, s = i.stateNode;
          if (typeof u.getDerivedStateFromError == "function" || typeof s.componentDidCatch == "function" && !gx(s)) {
            var d = _c(a, e), m = lE(i, d, We), y = es(i, m, We), x = Na();
            y !== null && (Ho(y, We, x), Ja(y, x));
            return;
          }
        }
        i = i.return;
      }
      E(`Internal React error: Attempted to capture a commit phase error inside a detached tree. This indicates a bug in React. Likely causes include deleting the same fiber more than once, committing an already-finished tree, or an inconsistent return pointer.

Error message:

%s`, a);
    }
    function C1(e, t, a) {
      var i = e.pingCache;
      i !== null && i.delete(t);
      var u = Na();
      wf(e, a), O1(e), Oa === e && $u(wr, a) && (br === pv || br === hy && Iu(wr) && tr() - OE < ix ? wc(e, X) : yy = ut(yy, a)), Ja(e, u);
    }
    function Ex(e, t) {
      t === jt && (t = t1(e));
      var a = Na(), i = qa(e, t);
      i !== null && (Ho(i, t, a), Ja(i, a));
    }
    function _1(e) {
      var t = e.memoizedState, a = jt;
      t !== null && (a = t.retryLane), Ex(e, a);
    }
    function x1(e, t) {
      var a = jt, i;
      switch (e.tag) {
        case ze:
          i = e.stateNode;
          var u = e.memoizedState;
          u !== null && (a = u.retryLane);
          break;
        case hn:
          i = e.stateNode;
          break;
        default:
          throw new Error("Pinged unknown suspense boundary type. This is probably a bug in React.");
      }
      i !== null && i.delete(t), Ex(e, a);
    }
    function T1(e) {
      return e < 120 ? 120 : e < 480 ? 480 : e < 1080 ? 1080 : e < 1920 ? 1920 : e < 3e3 ? 3e3 : e < 4320 ? 4320 : Xk(e / 1960) * 1960;
    }
    function R1() {
      if (Sv > Jk)
        throw Sv = 0, AE = null, new Error("Maximum update depth exceeded. This can happen when a component repeatedly calls setState inside componentWillUpdate or componentDidUpdate. React limits the number of nested updates to prevent infinite loops.");
      vd > e1 && (vd = 0, Ey = null, E("Maximum update depth exceeded. This can happen when a component calls setState inside useEffect, but useEffect either doesn't have a dependency array, or one of the dependencies changes on every render."));
    }
    function w1() {
      yl.flushLegacyContextWarning(), yl.flushPendingUnsafeLifecycleWarnings();
    }
    function Cx(e, t) {
      en(e), Ry(e, Vl, Yk), t && Ry(e, zi, Wk), Ry(e, Vl, Ik), t && Ry(e, zi, $k), gn();
    }
    function Ry(e, t, a) {
      for (var i = e, u = null; i !== null; ) {
        var s = i.subtreeFlags & t;
        i !== u && i.child !== null && s !== Ue ? i = i.child : ((i.flags & t) !== Ue && a(i), i.sibling !== null ? i = i.sibling : i = u = i.return);
      }
    }
    var wy = null;
    function _x(e) {
      {
        if ((Ot & Jr) !== Rr || !(e.mode & yt))
          return;
        var t = e.tag;
        if (t !== fe && t !== re && t !== $ && t !== I && t !== rt && t !== St && t !== Qe)
          return;
        var a = Xe(e) || "ReactComponent";
        if (wy !== null) {
          if (wy.has(a))
            return;
          wy.add(a);
        } else
          wy = /* @__PURE__ */ new Set([a]);
        var i = mr;
        try {
          en(e), E("Can't perform a React state update on a component that hasn't mounted yet. This indicates that you have a side-effect in your render function that asynchronously later calls tries to update the component. Move this work to useEffect instead.");
        } finally {
          i ? en(e) : gn();
        }
      }
    }
    var VE;
    {
      var b1 = null;
      VE = function(e, t, a) {
        var i = Ox(b1, t);
        try {
          return z0(e, t, a);
        } catch (s) {
          if (Hw() || s !== null && typeof s == "object" && typeof s.then == "function")
            throw s;
          if (Lm(), j_(), P0(e, t), Ox(t, i), t.mode & Vt && BS(t), Hl(null, z0, null, e, t, a), il()) {
            var u = Os();
            typeof u == "object" && u !== null && u._suppressLogging && typeof s == "object" && s !== null && !s._suppressLogging && (s._suppressLogging = !0);
          }
          throw s;
        }
      };
    }
    var xx = !1, PE;
    PE = /* @__PURE__ */ new Set();
    function k1(e) {
      if (ki && !yb())
        switch (e.tag) {
          case I:
          case rt:
          case Qe: {
            var t = Fn && Xe(Fn) || "Unknown", a = t;
            if (!PE.has(a)) {
              PE.add(a);
              var i = Xe(e) || "Unknown";
              E("Cannot update a component (`%s`) while rendering a different component (`%s`). To locate the bad setState() call inside `%s`, follow the stack trace as described in https://reactjs.org/link/setstate-in-render", i, t, t);
            }
            break;
          }
          case $: {
            xx || (E("Cannot update during an existing state transition (such as within `render`). Render methods should be a pure function of props and state."), xx = !0);
            break;
          }
        }
    }
    function _v(e, t) {
      if (oa) {
        var a = e.memoizedUpdaters;
        a.forEach(function(i) {
          Gs(e, i, t);
        });
      }
    }
    var BE = {};
    function IE(e, t) {
      {
        var a = Rl.current;
        return a !== null ? (a.push(t), BE) : $d(e, t);
      }
    }
    function Tx(e) {
      if (e !== BE)
        return yh(e);
    }
    function Rx() {
      return Rl.current !== null;
    }
    function D1(e) {
      {
        if (e.mode & yt) {
          if (!nx())
            return;
        } else if (!qk() || Ot !== Rr || e.tag !== I && e.tag !== rt && e.tag !== Qe)
          return;
        if (Rl.current === null) {
          var t = mr;
          try {
            en(e), E(`An update to %s inside a test was not wrapped in act(...).

When testing, code that causes React state updates should be wrapped into act(...):

act(() => {
  /* fire events that update state */
});
/* assert on the output */

This ensures that you're testing the behavior the user would see in the browser. Learn more at https://reactjs.org/link/wrap-tests-with-act`, Xe(e));
          } finally {
            t ? en(e) : gn();
          }
        }
      }
    }
    function O1(e) {
      e.tag !== Xo && nx() && Rl.current === null && E(`A suspended resource finished loading inside a test, but the event was not wrapped in act(...).

When testing, code that resolves suspended data should be wrapped into act(...):

act(() => {
  /* finish loading suspended data */
});
/* assert on the output */

This ensures that you're testing the behavior the user would see in the browser. Learn more at https://reactjs.org/link/wrap-tests-with-act`);
    }
    function xv(e) {
      ox = e;
    }
    var qi = null, hd = null, N1 = function(e) {
      qi = e;
    };
    function md(e) {
      {
        if (qi === null)
          return e;
        var t = qi(e);
        return t === void 0 ? e : t.current;
      }
    }
    function $E(e) {
      return md(e);
    }
    function YE(e) {
      {
        if (qi === null)
          return e;
        var t = qi(e);
        if (t === void 0) {
          if (e != null && typeof e.render == "function") {
            var a = md(e.render);
            if (e.render !== a) {
              var i = {
                $$typeof: q,
                render: a
              };
              return e.displayName !== void 0 && (i.displayName = e.displayName), i;
            }
          }
          return e;
        }
        return t.current;
      }
    }
    function wx(e, t) {
      {
        if (qi === null)
          return !1;
        var a = e.elementType, i = t.type, u = !1, s = typeof i == "object" && i !== null ? i.$$typeof : null;
        switch (e.tag) {
          case $: {
            typeof i == "function" && (u = !0);
            break;
          }
          case I: {
            (typeof i == "function" || s === Ke) && (u = !0);
            break;
          }
          case rt: {
            (s === q || s === Ke) && (u = !0);
            break;
          }
          case St:
          case Qe: {
            (s === it || s === Ke) && (u = !0);
            break;
          }
          default:
            return !1;
        }
        if (u) {
          var d = qi(a);
          if (d !== void 0 && d === qi(i))
            return !0;
        }
        return !1;
      }
    }
    function bx(e) {
      {
        if (qi === null || typeof WeakSet != "function")
          return;
        hd === null && (hd = /* @__PURE__ */ new WeakSet()), hd.add(e);
      }
    }
    var M1 = function(e, t) {
      {
        if (qi === null)
          return;
        var a = t.staleFamilies, i = t.updatedFamilies;
        uo(), lo(function() {
          WE(e.current, i, a);
        });
      }
    }, L1 = function(e, t) {
      {
        if (e.context !== Si)
          return;
        uo(), lo(function() {
          Tv(t, e, null, null);
        });
      }
    };
    function WE(e, t, a) {
      {
        var i = e.alternate, u = e.child, s = e.sibling, d = e.tag, m = e.type, y = null;
        switch (d) {
          case I:
          case Qe:
          case $:
            y = m;
            break;
          case rt:
            y = m.render;
            break;
        }
        if (qi === null)
          throw new Error("Expected resolveFamily to be set during hot reload.");
        var x = !1, R = !1;
        if (y !== null) {
          var M = qi(y);
          M !== void 0 && (a.has(M) ? R = !0 : t.has(M) && (d === $ ? R = !0 : x = !0));
        }
        if (hd !== null && (hd.has(e) || i !== null && hd.has(i)) && (R = !0), R && (e._debugNeedsRemount = !0), R || x) {
          var O = qa(e, We);
          O !== null && kr(O, e, We, un);
        }
        u !== null && !R && WE(u, t, a), s !== null && WE(s, t, a);
      }
    }
    var A1 = function(e, t) {
      {
        var a = /* @__PURE__ */ new Set(), i = new Set(t.map(function(u) {
          return u.current;
        }));
        return QE(e.current, i, a), a;
      }
    };
    function QE(e, t, a) {
      {
        var i = e.child, u = e.sibling, s = e.tag, d = e.type, m = null;
        switch (s) {
          case I:
          case Qe:
          case $:
            m = d;
            break;
          case rt:
            m = d.render;
            break;
        }
        var y = !1;
        m !== null && t.has(m) && (y = !0), y ? z1(e, a) : i !== null && QE(i, t, a), u !== null && QE(u, t, a);
      }
    }
    function z1(e, t) {
      {
        var a = U1(e, t);
        if (a)
          return;
        for (var i = e; ; ) {
          switch (i.tag) {
            case de:
              t.add(i.stateNode);
              return;
            case be:
              t.add(i.stateNode.containerInfo);
              return;
            case re:
              t.add(i.stateNode.containerInfo);
              return;
          }
          if (i.return === null)
            throw new Error("Expected to reach root first.");
          i = i.return;
        }
      }
    }
    function U1(e, t) {
      for (var a = e, i = !1; ; ) {
        if (a.tag === de)
          i = !0, t.add(a.stateNode);
        else if (a.child !== null) {
          a.child.return = a, a = a.child;
          continue;
        }
        if (a === e)
          return i;
        for (; a.sibling === null; ) {
          if (a.return === null || a.return === e)
            return i;
          a = a.return;
        }
        a.sibling.return = a.return, a = a.sibling;
      }
      return !1;
    }
    var ZE;
    {
      ZE = !1;
      try {
        var kx = Object.preventExtensions({});
      } catch {
        ZE = !0;
      }
    }
    function j1(e, t, a, i) {
      this.tag = e, this.key = a, this.elementType = null, this.type = null, this.stateNode = null, this.return = null, this.child = null, this.sibling = null, this.index = 0, this.ref = null, this.pendingProps = t, this.memoizedProps = null, this.updateQueue = null, this.memoizedState = null, this.dependencies = null, this.mode = i, this.flags = Ue, this.subtreeFlags = Ue, this.deletions = null, this.lanes = X, this.childLanes = X, this.alternate = null, this.actualDuration = Number.NaN, this.actualStartTime = Number.NaN, this.selfBaseDuration = Number.NaN, this.treeBaseDuration = Number.NaN, this.actualDuration = 0, this.actualStartTime = -1, this.selfBaseDuration = 0, this.treeBaseDuration = 0, this._debugSource = null, this._debugOwner = null, this._debugNeedsRemount = !1, this._debugHookTypes = null, !ZE && typeof Object.preventExtensions == "function" && Object.preventExtensions(this);
    }
    var Ei = function(e, t, a, i) {
      return new j1(e, t, a, i);
    };
    function GE(e) {
      var t = e.prototype;
      return !!(t && t.isReactComponent);
    }
    function F1(e) {
      return typeof e == "function" && !GE(e) && e.defaultProps === void 0;
    }
    function H1(e) {
      if (typeof e == "function")
        return GE(e) ? $ : I;
      if (e != null) {
        var t = e.$$typeof;
        if (t === q)
          return rt;
        if (t === it)
          return St;
      }
      return fe;
    }
    function kc(e, t) {
      var a = e.alternate;
      a === null ? (a = Ei(e.tag, t, e.key, e.mode), a.elementType = e.elementType, a.type = e.type, a.stateNode = e.stateNode, a._debugSource = e._debugSource, a._debugOwner = e._debugOwner, a._debugHookTypes = e._debugHookTypes, a.alternate = e, e.alternate = a) : (a.pendingProps = t, a.type = e.type, a.flags = Ue, a.subtreeFlags = Ue, a.deletions = null, a.actualDuration = 0, a.actualStartTime = -1), a.flags = e.flags & In, a.childLanes = e.childLanes, a.lanes = e.lanes, a.child = e.child, a.memoizedProps = e.memoizedProps, a.memoizedState = e.memoizedState, a.updateQueue = e.updateQueue;
      var i = e.dependencies;
      switch (a.dependencies = i === null ? null : {
        lanes: i.lanes,
        firstContext: i.firstContext
      }, a.sibling = e.sibling, a.index = e.index, a.ref = e.ref, a.selfBaseDuration = e.selfBaseDuration, a.treeBaseDuration = e.treeBaseDuration, a._debugNeedsRemount = e._debugNeedsRemount, a.tag) {
        case fe:
        case I:
        case Qe:
          a.type = md(e.type);
          break;
        case $:
          a.type = $E(e.type);
          break;
        case rt:
          a.type = YE(e.type);
          break;
      }
      return a;
    }
    function V1(e, t) {
      e.flags &= In | Rn;
      var a = e.alternate;
      if (a === null)
        e.childLanes = X, e.lanes = t, e.child = null, e.subtreeFlags = Ue, e.memoizedProps = null, e.memoizedState = null, e.updateQueue = null, e.dependencies = null, e.stateNode = null, e.selfBaseDuration = 0, e.treeBaseDuration = 0;
      else {
        e.childLanes = a.childLanes, e.lanes = a.lanes, e.child = a.child, e.subtreeFlags = Ue, e.deletions = null, e.memoizedProps = a.memoizedProps, e.memoizedState = a.memoizedState, e.updateQueue = a.updateQueue, e.type = a.type;
        var i = a.dependencies;
        e.dependencies = i === null ? null : {
          lanes: i.lanes,
          firstContext: i.firstContext
        }, e.selfBaseDuration = a.selfBaseDuration, e.treeBaseDuration = a.treeBaseDuration;
      }
      return e;
    }
    function P1(e, t, a) {
      var i;
      return e === xm ? (i = yt, t === !0 && (i |= rn, i |= Pt)) : i = je, oa && (i |= Vt), Ei(re, null, null, i);
    }
    function qE(e, t, a, i, u, s) {
      var d = fe, m = e;
      if (typeof e == "function")
        GE(e) ? (d = $, m = $E(m)) : m = md(m);
      else if (typeof e == "string")
        d = de;
      else
        e: switch (e) {
          case Ti:
            return cs(a.children, u, s, t);
          case ii:
            d = xt, u |= rn, (u & yt) !== je && (u |= Pt);
            break;
          case Ri:
            return B1(a, u, s, t);
          case pe:
            return I1(a, u, s, t);
          case _e:
            return $1(a, u, s, t);
          case Mn:
            return Dx(a, u, s, t);
          case fn:
          case Et:
          case yn:
          case hr:
          case mt:
          default: {
            if (typeof e == "object" && e !== null)
              switch (e.$$typeof) {
                case wi:
                  d = _t;
                  break e;
                case k:
                  d = En;
                  break e;
                case q:
                  d = rt, m = YE(m);
                  break e;
                case it:
                  d = St;
                  break e;
                case Ke:
                  d = vn, m = null;
                  break e;
              }
            var y = "";
            {
              (e === void 0 || typeof e == "object" && e !== null && Object.keys(e).length === 0) && (y += " You likely forgot to export your component from the file it's defined in, or you might have mixed up default and named imports.");
              var x = i ? Xe(i) : null;
              x && (y += `

Check the render method of \`` + x + "`.");
            }
            throw new Error("Element type is invalid: expected a string (for built-in components) or a class/function (for composite components) " + ("but got: " + (e == null ? e : typeof e) + "." + y));
          }
        }
      var R = Ei(d, a, t, u);
      return R.elementType = e, R.type = m, R.lanes = s, R._debugOwner = i, R;
    }
    function XE(e, t, a) {
      var i = null;
      i = e._owner;
      var u = e.type, s = e.key, d = e.props, m = qE(u, s, d, i, t, a);
      return m._debugSource = e._source, m._debugOwner = e._owner, m;
    }
    function cs(e, t, a, i) {
      var u = Ei(bt, e, i, t);
      return u.lanes = a, u;
    }
    function B1(e, t, a, i) {
      typeof e.id != "string" && E('Profiler must specify an "id" of type `string` as a prop. Received the type `%s` instead.', typeof e.id);
      var u = Ei(Tt, e, i, t | Vt);
      return u.elementType = Ri, u.lanes = a, u.stateNode = {
        effectDuration: 0,
        passiveEffectDuration: 0
      }, u;
    }
    function I1(e, t, a, i) {
      var u = Ei(ze, e, i, t);
      return u.elementType = pe, u.lanes = a, u;
    }
    function $1(e, t, a, i) {
      var u = Ei(hn, e, i, t);
      return u.elementType = _e, u.lanes = a, u;
    }
    function Dx(e, t, a, i) {
      var u = Ei(He, e, i, t);
      u.elementType = Mn, u.lanes = a;
      var s = {
        isHidden: !1
      };
      return u.stateNode = s, u;
    }
    function KE(e, t, a) {
      var i = Ei(nt, e, null, t);
      return i.lanes = a, i;
    }
    function Y1() {
      var e = Ei(de, null, null, je);
      return e.elementType = "DELETED", e;
    }
    function W1(e) {
      var t = Ei(on, null, null, je);
      return t.stateNode = e, t;
    }
    function JE(e, t, a) {
      var i = e.children !== null ? e.children : [], u = Ei(be, i, e.key, t);
      return u.lanes = a, u.stateNode = {
        containerInfo: e.containerInfo,
        pendingChildren: null,
        // Used by persistent updates
        implementation: e.implementation
      }, u;
    }
    function Ox(e, t) {
      return e === null && (e = Ei(fe, null, null, je)), e.tag = t.tag, e.key = t.key, e.elementType = t.elementType, e.type = t.type, e.stateNode = t.stateNode, e.return = t.return, e.child = t.child, e.sibling = t.sibling, e.index = t.index, e.ref = t.ref, e.pendingProps = t.pendingProps, e.memoizedProps = t.memoizedProps, e.updateQueue = t.updateQueue, e.memoizedState = t.memoizedState, e.dependencies = t.dependencies, e.mode = t.mode, e.flags = t.flags, e.subtreeFlags = t.subtreeFlags, e.deletions = t.deletions, e.lanes = t.lanes, e.childLanes = t.childLanes, e.alternate = t.alternate, e.actualDuration = t.actualDuration, e.actualStartTime = t.actualStartTime, e.selfBaseDuration = t.selfBaseDuration, e.treeBaseDuration = t.treeBaseDuration, e._debugSource = t._debugSource, e._debugOwner = t._debugOwner, e._debugNeedsRemount = t._debugNeedsRemount, e._debugHookTypes = t._debugHookTypes, e;
    }
    function Q1(e, t, a, i, u) {
      this.tag = t, this.containerInfo = e, this.pendingChildren = null, this.current = null, this.pingCache = null, this.finishedWork = null, this.timeoutHandle = Ag, this.context = null, this.pendingContext = null, this.callbackNode = null, this.callbackPriority = jt, this.eventTimes = Zs(X), this.expirationTimes = Zs(un), this.pendingLanes = X, this.suspendedLanes = X, this.pingedLanes = X, this.expiredLanes = X, this.mutableReadLanes = X, this.finishedLanes = X, this.entangledLanes = X, this.entanglements = Zs(X), this.identifierPrefix = i, this.onRecoverableError = u, this.mutableSourceEagerHydrationData = null, this.effectDuration = 0, this.passiveEffectDuration = 0;
      {
        this.memoizedUpdaters = /* @__PURE__ */ new Set();
        for (var s = this.pendingUpdatersLaneMap = [], d = 0; d < ju; d++)
          s.push(/* @__PURE__ */ new Set());
      }
      switch (t) {
        case xm:
          this._debugRootType = a ? "hydrateRoot()" : "createRoot()";
          break;
        case Xo:
          this._debugRootType = a ? "hydrate()" : "render()";
          break;
      }
    }
    function Nx(e, t, a, i, u, s, d, m, y, x) {
      var R = new Q1(e, t, a, m, y), M = P1(t, s);
      R.current = M, M.stateNode = R;
      {
        var O = {
          element: i,
          isDehydrated: a,
          cache: null,
          // not enabled yet
          transitions: null,
          pendingSuspenseBoundaries: null
        };
        M.memoizedState = O;
      }
      return dS(M), R;
    }
    var eC = "18.3.1";
    function Z1(e, t, a) {
      var i = arguments.length > 3 && arguments[3] !== void 0 ? arguments[3] : null;
      return ta(i), {
        // This tag allow us to uniquely identify this as a React Portal
        $$typeof: vr,
        key: i == null ? null : "" + i,
        children: e,
        containerInfo: t,
        implementation: a
      };
    }
    var tC, nC;
    tC = !1, nC = {};
    function Mx(e) {
      if (!e)
        return Si;
      var t = Lo(e), a = Ow(t);
      if (t.tag === $) {
        var i = t.type;
        if (ru(i))
          return i_(t, i, a);
      }
      return a;
    }
    function G1(e, t) {
      {
        var a = Lo(e);
        if (a === void 0) {
          if (typeof e.render == "function")
            throw new Error("Unable to find node on an unmounted component.");
          var i = Object.keys(e).join(",");
          throw new Error("Argument appears to not be a ReactComponent. Keys: " + i);
        }
        var u = la(a);
        if (u === null)
          return null;
        if (u.mode & rn) {
          var s = Xe(a) || "Component";
          if (!nC[s]) {
            nC[s] = !0;
            var d = mr;
            try {
              en(u), a.mode & rn ? E("%s is deprecated in StrictMode. %s was passed an instance of %s which is inside StrictMode. Instead, add a ref directly to the element you want to reference. Learn more about using refs safely here: https://reactjs.org/link/strict-mode-find-node", t, t, s) : E("%s is deprecated in StrictMode. %s was passed an instance of %s which renders StrictMode children. Instead, add a ref directly to the element you want to reference. Learn more about using refs safely here: https://reactjs.org/link/strict-mode-find-node", t, t, s);
            } finally {
              d ? en(d) : gn();
            }
          }
        }
        return u.stateNode;
      }
    }
    function Lx(e, t, a, i, u, s, d, m) {
      var y = !1, x = null;
      return Nx(e, t, y, x, a, i, u, s, d);
    }
    function Ax(e, t, a, i, u, s, d, m, y, x) {
      var R = !0, M = Nx(a, i, R, e, u, s, d, m, y);
      M.context = Mx(null);
      var O = M.current, H = Na(), B = os(O), W = no(H, B);
      return W.callback = t ?? null, es(O, W, B), n1(M, B, H), M;
    }
    function Tv(e, t, a, i) {
      Qd(t, e);
      var u = t.current, s = Na(), d = os(u);
      bn(d);
      var m = Mx(a);
      t.context === null ? t.context = m : t.pendingContext = m, ki && mr !== null && !tC && (tC = !0, E(`Render methods should be a pure function of props and state; triggering nested component updates from render is not allowed. If necessary, trigger nested updates in componentDidUpdate.

Check the render method of %s.`, Xe(mr) || "Unknown"));
      var y = no(s, d);
      y.payload = {
        element: e
      }, i = i === void 0 ? null : i, i !== null && (typeof i != "function" && E("render(...): Expected the last optional `callback` argument to be a function. Instead received: %s.", i), y.callback = i);
      var x = es(u, y, d);
      return x !== null && (kr(x, u, d, s), Fm(x, u, d)), d;
    }
    function by(e) {
      var t = e.current;
      if (!t.child)
        return null;
      switch (t.child.tag) {
        case de:
          return t.child.stateNode;
        default:
          return t.child.stateNode;
      }
    }
    function q1(e) {
      switch (e.tag) {
        case re: {
          var t = e.stateNode;
          if (kf(t)) {
            var a = xh(t);
            l1(t, a);
          }
          break;
        }
        case ze: {
          lo(function() {
            var u = qa(e, We);
            if (u !== null) {
              var s = Na();
              kr(u, e, We, s);
            }
          });
          var i = We;
          rC(e, i);
          break;
        }
      }
    }
    function zx(e, t) {
      var a = e.memoizedState;
      a !== null && a.dehydrated !== null && (a.retryLane = kh(a.retryLane, t));
    }
    function rC(e, t) {
      zx(e, t);
      var a = e.alternate;
      a && zx(a, t);
    }
    function X1(e) {
      if (e.tag === ze) {
        var t = Bs, a = qa(e, t);
        if (a !== null) {
          var i = Na();
          kr(a, e, t, i);
        }
        rC(e, t);
      }
    }
    function K1(e) {
      if (e.tag === ze) {
        var t = os(e), a = qa(e, t);
        if (a !== null) {
          var i = Na();
          kr(a, e, t, i);
        }
        rC(e, t);
      }
    }
    function Ux(e) {
      var t = Cn(e);
      return t === null ? null : t.stateNode;
    }
    var jx = function(e) {
      return null;
    };
    function J1(e) {
      return jx(e);
    }
    var Fx = function(e) {
      return !1;
    };
    function eD(e) {
      return Fx(e);
    }
    var Hx = null, Vx = null, Px = null, Bx = null, Ix = null, $x = null, Yx = null, Wx = null, Qx = null;
    {
      var Zx = function(e, t, a) {
        var i = t[a], u = pt(e) ? e.slice() : st({}, e);
        return a + 1 === t.length ? (pt(u) ? u.splice(i, 1) : delete u[i], u) : (u[i] = Zx(e[i], t, a + 1), u);
      }, Gx = function(e, t) {
        return Zx(e, t, 0);
      }, qx = function(e, t, a, i) {
        var u = t[i], s = pt(e) ? e.slice() : st({}, e);
        if (i + 1 === t.length) {
          var d = a[i];
          s[d] = s[u], pt(s) ? s.splice(u, 1) : delete s[u];
        } else
          s[u] = qx(
            // $FlowFixMe number or string is fine here
            e[u],
            t,
            a,
            i + 1
          );
        return s;
      }, Xx = function(e, t, a) {
        if (t.length !== a.length) {
          T("copyWithRename() expects paths of the same length");
          return;
        } else
          for (var i = 0; i < a.length - 1; i++)
            if (t[i] !== a[i]) {
              T("copyWithRename() expects paths to be the same except for the deepest key");
              return;
            }
        return qx(e, t, a, 0);
      }, Kx = function(e, t, a, i) {
        if (a >= t.length)
          return i;
        var u = t[a], s = pt(e) ? e.slice() : st({}, e);
        return s[u] = Kx(e[u], t, a + 1, i), s;
      }, Jx = function(e, t, a) {
        return Kx(e, t, 0, a);
      }, aC = function(e, t) {
        for (var a = e.memoizedState; a !== null && t > 0; )
          a = a.next, t--;
        return a;
      };
      Hx = function(e, t, a, i) {
        var u = aC(e, t);
        if (u !== null) {
          var s = Jx(u.memoizedState, a, i);
          u.memoizedState = s, u.baseState = s, e.memoizedProps = st({}, e.memoizedProps);
          var d = qa(e, We);
          d !== null && kr(d, e, We, un);
        }
      }, Vx = function(e, t, a) {
        var i = aC(e, t);
        if (i !== null) {
          var u = Gx(i.memoizedState, a);
          i.memoizedState = u, i.baseState = u, e.memoizedProps = st({}, e.memoizedProps);
          var s = qa(e, We);
          s !== null && kr(s, e, We, un);
        }
      }, Px = function(e, t, a, i) {
        var u = aC(e, t);
        if (u !== null) {
          var s = Xx(u.memoizedState, a, i);
          u.memoizedState = s, u.baseState = s, e.memoizedProps = st({}, e.memoizedProps);
          var d = qa(e, We);
          d !== null && kr(d, e, We, un);
        }
      }, Bx = function(e, t, a) {
        e.pendingProps = Jx(e.memoizedProps, t, a), e.alternate && (e.alternate.pendingProps = e.pendingProps);
        var i = qa(e, We);
        i !== null && kr(i, e, We, un);
      }, Ix = function(e, t) {
        e.pendingProps = Gx(e.memoizedProps, t), e.alternate && (e.alternate.pendingProps = e.pendingProps);
        var a = qa(e, We);
        a !== null && kr(a, e, We, un);
      }, $x = function(e, t, a) {
        e.pendingProps = Xx(e.memoizedProps, t, a), e.alternate && (e.alternate.pendingProps = e.pendingProps);
        var i = qa(e, We);
        i !== null && kr(i, e, We, un);
      }, Yx = function(e) {
        var t = qa(e, We);
        t !== null && kr(t, e, We, un);
      }, Wx = function(e) {
        jx = e;
      }, Qx = function(e) {
        Fx = e;
      };
    }
    function tD(e) {
      var t = la(e);
      return t === null ? null : t.stateNode;
    }
    function nD(e) {
      return null;
    }
    function rD() {
      return mr;
    }
    function aD(e) {
      var t = e.findFiberByHostInstance, a = p.ReactCurrentDispatcher;
      return Uo({
        bundleType: e.bundleType,
        version: e.version,
        rendererPackageName: e.rendererPackageName,
        rendererConfig: e.rendererConfig,
        overrideHookState: Hx,
        overrideHookStateDeletePath: Vx,
        overrideHookStateRenamePath: Px,
        overrideProps: Bx,
        overridePropsDeletePath: Ix,
        overridePropsRenamePath: $x,
        setErrorHandler: Wx,
        setSuspenseHandler: Qx,
        scheduleUpdate: Yx,
        currentDispatcherRef: a,
        findHostInstanceByFiber: tD,
        findFiberByHostInstance: t || nD,
        // React Refresh
        findHostInstancesForRefresh: A1,
        scheduleRefresh: M1,
        scheduleRoot: L1,
        setRefreshHandler: N1,
        // Enables DevTools to append owner stacks to error messages in DEV mode.
        getCurrentFiber: rD,
        // Enables DevTools to detect reconciler version rather than renderer version
        // which may not match for third party renderers.
        reconcilerVersion: eC
      });
    }
    var eT = typeof reportError == "function" ? (
      // In modern browsers, reportError will dispatch an error event,
      // emulating an uncaught JavaScript error.
      reportError
    ) : function(e) {
      console.error(e);
    };
    function iC(e) {
      this._internalRoot = e;
    }
    ky.prototype.render = iC.prototype.render = function(e) {
      var t = this._internalRoot;
      if (t === null)
        throw new Error("Cannot update an unmounted root.");
      {
        typeof arguments[1] == "function" ? E("render(...): does not support the second callback argument. To execute a side effect after rendering, declare it in a component body with useEffect().") : Dy(arguments[1]) ? E("You passed a container to the second argument of root.render(...). You don't need to pass it again since you already passed it to create the root.") : typeof arguments[1] < "u" && E("You passed a second argument to root.render(...) but it only accepts one argument.");
        var a = t.containerInfo;
        if (a.nodeType !== Pn) {
          var i = Ux(t.current);
          i && i.parentNode !== a && E("render(...): It looks like the React-rendered content of the root container was removed without using React. This is not supported and will cause errors. Instead, call root.unmount() to empty a root's container.");
        }
      }
      Tv(e, t, null, null);
    }, ky.prototype.unmount = iC.prototype.unmount = function() {
      typeof arguments[0] == "function" && E("unmount(...): does not support a callback argument. To execute a side effect after rendering, declare it in a component body with useEffect().");
      var e = this._internalRoot;
      if (e !== null) {
        this._internalRoot = null;
        var t = e.containerInfo;
        dx() && E("Attempted to synchronously unmount a root while React was already rendering. React cannot finish unmounting the root until the current render has completed, which may lead to a race condition."), lo(function() {
          Tv(null, e, null, null);
        }), e_(t);
      }
    };
    function iD(e, t) {
      if (!Dy(e))
        throw new Error("createRoot(...): Target container is not a DOM element.");
      tT(e);
      var a = !1, i = !1, u = "", s = eT;
      t != null && (t.hydrate ? T("hydrate through createRoot is deprecated. Use ReactDOMClient.hydrateRoot(container, <App />) instead.") : typeof t == "object" && t !== null && t.$$typeof === Fr && E(`You passed a JSX element to createRoot. You probably meant to call root.render instead. Example usage:

  let root = createRoot(domContainer);
  root.render(<App />);`), t.unstable_strictMode === !0 && (a = !0), t.identifierPrefix !== void 0 && (u = t.identifierPrefix), t.onRecoverableError !== void 0 && (s = t.onRecoverableError), t.transitionCallbacks !== void 0 && t.transitionCallbacks);
      var d = Lx(e, xm, null, a, i, u, s);
      mm(d.current, e);
      var m = e.nodeType === Pn ? e.parentNode : e;
      return Op(m), new iC(d);
    }
    function ky(e) {
      this._internalRoot = e;
    }
    function lD(e) {
      e && Fh(e);
    }
    ky.prototype.unstable_scheduleHydration = lD;
    function uD(e, t, a) {
      if (!Dy(e))
        throw new Error("hydrateRoot(...): Target container is not a DOM element.");
      tT(e), t === void 0 && E("Must provide initial children as second argument to hydrateRoot. Example usage: hydrateRoot(domContainer, <App />)");
      var i = a ?? null, u = a != null && a.hydratedSources || null, s = !1, d = !1, m = "", y = eT;
      a != null && (a.unstable_strictMode === !0 && (s = !0), a.identifierPrefix !== void 0 && (m = a.identifierPrefix), a.onRecoverableError !== void 0 && (y = a.onRecoverableError));
      var x = Ax(t, null, e, xm, i, s, d, m, y);
      if (mm(x.current, e), Op(e), u)
        for (var R = 0; R < u.length; R++) {
          var M = u[R];
          fb(x, M);
        }
      return new ky(x);
    }
    function Dy(e) {
      return !!(e && (e.nodeType === ra || e.nodeType === al || e.nodeType === Md));
    }
    function Rv(e) {
      return !!(e && (e.nodeType === ra || e.nodeType === al || e.nodeType === Md || e.nodeType === Pn && e.nodeValue === " react-mount-point-unstable "));
    }
    function tT(e) {
      e.nodeType === ra && e.tagName && e.tagName.toUpperCase() === "BODY" && E("createRoot(): Creating roots directly with document.body is discouraged, since its children are often manipulated by third-party scripts and browser extensions. This may lead to subtle reconciliation issues. Try using a container element created for your app."), Pp(e) && (e._reactRootContainer ? E("You are calling ReactDOMClient.createRoot() on a container that was previously passed to ReactDOM.render(). This is not supported.") : E("You are calling ReactDOMClient.createRoot() on a container that has already been passed to createRoot() before. Instead, call root.render() on the existing root instead if you want to update it."));
    }
    var oD = p.ReactCurrentOwner, nT;
    nT = function(e) {
      if (e._reactRootContainer && e.nodeType !== Pn) {
        var t = Ux(e._reactRootContainer.current);
        t && t.parentNode !== e && E("render(...): It looks like the React-rendered content of this container was removed without using React. This is not supported and will cause errors. Instead, call ReactDOM.unmountComponentAtNode to empty a container.");
      }
      var a = !!e._reactRootContainer, i = lC(e), u = !!(i && Go(i));
      u && !a && E("render(...): Replacing React-rendered children with a new root component. If you intended to update the children of this node, you should instead have the existing children update their state and render the new components instead of calling ReactDOM.render."), e.nodeType === ra && e.tagName && e.tagName.toUpperCase() === "BODY" && E("render(): Rendering components directly into document.body is discouraged, since its children are often manipulated by third-party scripts and browser extensions. This may lead to subtle reconciliation issues. Try rendering into a container element created for your app.");
    };
    function lC(e) {
      return e ? e.nodeType === al ? e.documentElement : e.firstChild : null;
    }
    function rT() {
    }
    function sD(e, t, a, i, u) {
      if (u) {
        if (typeof i == "function") {
          var s = i;
          i = function() {
            var O = by(d);
            s.call(O);
          };
        }
        var d = Ax(
          t,
          i,
          e,
          Xo,
          null,
          // hydrationCallbacks
          !1,
          // isStrictMode
          !1,
          // concurrentUpdatesByDefaultOverride,
          "",
          // identifierPrefix
          rT
        );
        e._reactRootContainer = d, mm(d.current, e);
        var m = e.nodeType === Pn ? e.parentNode : e;
        return Op(m), lo(), d;
      } else {
        for (var y; y = e.lastChild; )
          e.removeChild(y);
        if (typeof i == "function") {
          var x = i;
          i = function() {
            var O = by(R);
            x.call(O);
          };
        }
        var R = Lx(
          e,
          Xo,
          null,
          // hydrationCallbacks
          !1,
          // isStrictMode
          !1,
          // concurrentUpdatesByDefaultOverride,
          "",
          // identifierPrefix
          rT
        );
        e._reactRootContainer = R, mm(R.current, e);
        var M = e.nodeType === Pn ? e.parentNode : e;
        return Op(M), lo(function() {
          Tv(t, R, a, i);
        }), R;
      }
    }
    function cD(e, t) {
      e !== null && typeof e != "function" && E("%s(...): Expected the last optional `callback` argument to be a function. Instead received: %s.", t, e);
    }
    function Oy(e, t, a, i, u) {
      nT(a), cD(u === void 0 ? null : u, "render");
      var s = a._reactRootContainer, d;
      if (!s)
        d = sD(a, t, e, u, i);
      else {
        if (d = s, typeof u == "function") {
          var m = u;
          u = function() {
            var y = by(d);
            m.call(y);
          };
        }
        Tv(t, d, e, u);
      }
      return by(d);
    }
    var aT = !1;
    function fD(e) {
      {
        aT || (aT = !0, E("findDOMNode is deprecated and will be removed in the next major release. Instead, add a ref directly to the element you want to reference. Learn more about using refs safely here: https://reactjs.org/link/strict-mode-find-node"));
        var t = oD.current;
        if (t !== null && t.stateNode !== null) {
          var a = t.stateNode._warnedAboutRefsInRender;
          a || E("%s is accessing findDOMNode inside its render(). render() should be a pure function of props and state. It should never access something that requires stale data from the previous render, such as refs. Move this logic to componentDidMount and componentDidUpdate instead.", Lt(t.type) || "A component"), t.stateNode._warnedAboutRefsInRender = !0;
        }
      }
      return e == null ? null : e.nodeType === ra ? e : G1(e, "findDOMNode");
    }
    function dD(e, t, a) {
      if (E("ReactDOM.hydrate is no longer supported in React 18. Use hydrateRoot instead. Until you switch to the new API, your app will behave as if it's running React 17. Learn more: https://reactjs.org/link/switch-to-createroot"), !Rv(t))
        throw new Error("Target container is not a DOM element.");
      {
        var i = Pp(t) && t._reactRootContainer === void 0;
        i && E("You are calling ReactDOM.hydrate() on a container that was previously passed to ReactDOMClient.createRoot(). This is not supported. Did you mean to call hydrateRoot(container, element)?");
      }
      return Oy(null, e, t, !0, a);
    }
    function pD(e, t, a) {
      if (E("ReactDOM.render is no longer supported in React 18. Use createRoot instead. Until you switch to the new API, your app will behave as if it's running React 17. Learn more: https://reactjs.org/link/switch-to-createroot"), !Rv(t))
        throw new Error("Target container is not a DOM element.");
      {
        var i = Pp(t) && t._reactRootContainer === void 0;
        i && E("You are calling ReactDOM.render() on a container that was previously passed to ReactDOMClient.createRoot(). This is not supported. Did you mean to call root.render(element)?");
      }
      return Oy(null, e, t, !1, a);
    }
    function vD(e, t, a, i) {
      if (E("ReactDOM.unstable_renderSubtreeIntoContainer() is no longer supported in React 18. Consider using a portal instead. Until you switch to the createRoot API, your app will behave as if it's running React 17. Learn more: https://reactjs.org/link/switch-to-createroot"), !Rv(a))
        throw new Error("Target container is not a DOM element.");
      if (e == null || !rg(e))
        throw new Error("parentComponent must be a valid React Component");
      return Oy(e, t, a, !1, i);
    }
    var iT = !1;
    function hD(e) {
      if (iT || (iT = !0, E("unmountComponentAtNode is deprecated and will be removed in the next major release. Switch to the createRoot API. Learn more: https://reactjs.org/link/switch-to-createroot")), !Rv(e))
        throw new Error("unmountComponentAtNode(...): Target container is not a DOM element.");
      {
        var t = Pp(e) && e._reactRootContainer === void 0;
        t && E("You are calling ReactDOM.unmountComponentAtNode() on a container that was previously passed to ReactDOMClient.createRoot(). This is not supported. Did you mean to call root.unmount()?");
      }
      if (e._reactRootContainer) {
        {
          var a = lC(e), i = a && !Go(a);
          i && E("unmountComponentAtNode(): The node you're attempting to unmount was rendered by another copy of React.");
        }
        return lo(function() {
          Oy(null, null, e, !1, function() {
            e._reactRootContainer = null, e_(e);
          });
        }), !0;
      } else {
        {
          var u = lC(e), s = !!(u && Go(u)), d = e.nodeType === ra && Rv(e.parentNode) && !!e.parentNode._reactRootContainer;
          s && E("unmountComponentAtNode(): The node you're attempting to unmount was rendered by React and is not a top-level container. %s", d ? "You may have accidentally passed in a React root node instead of its container." : "Instead, have the parent component update its state and rerender in order to remove this component.");
        }
        return !1;
      }
    }
    Ar(q1), Vo(X1), Ah(K1), Js(Qa), pp(Nh), (typeof Map != "function" || // $FlowIssue Flow incorrectly thinks Map has no prototype
    Map.prototype == null || typeof Map.prototype.forEach != "function" || typeof Set != "function" || // $FlowIssue Flow incorrectly thinks Set has no prototype
    Set.prototype == null || typeof Set.prototype.clear != "function" || typeof Set.prototype.forEach != "function") && E("React depends on Map and Set built-in types. Make sure that you load a polyfill in older browsers. https://reactjs.org/link/react-polyfills"), $c(yR), ng(jE, u1, lo);
    function mD(e, t) {
      var a = arguments.length > 2 && arguments[2] !== void 0 ? arguments[2] : null;
      if (!Dy(t))
        throw new Error("Target container is not a DOM element.");
      return Z1(e, t, null, a);
    }
    function yD(e, t, a, i) {
      return vD(e, t, a, i);
    }
    var uC = {
      usingClientEntryPoint: !1,
      // Keep in sync with ReactTestUtils.js.
      // This is an array for better minification.
      Events: [Go, Qf, ym, Do, Yc, jE]
    };
    function gD(e, t) {
      return uC.usingClientEntryPoint || E('You are importing createRoot from "react-dom" which is not supported. You should instead import it from "react-dom/client".'), iD(e, t);
    }
    function SD(e, t, a) {
      return uC.usingClientEntryPoint || E('You are importing hydrateRoot from "react-dom" which is not supported. You should instead import it from "react-dom/client".'), uD(e, t, a);
    }
    function ED(e) {
      return dx() && E("flushSync was called from inside a lifecycle method. React cannot flush when React is already rendering. Consider moving this call to a scheduler task or micro task."), lo(e);
    }
    var CD = aD({
      findFiberByHostInstance: pc,
      bundleType: 1,
      version: eC,
      rendererPackageName: "react-dom"
    });
    if (!CD && Hn && window.top === window.self && (navigator.userAgent.indexOf("Chrome") > -1 && navigator.userAgent.indexOf("Edge") === -1 || navigator.userAgent.indexOf("Firefox") > -1)) {
      var lT = window.location.protocol;
      /^(https?|file):$/.test(lT) && console.info("%cDownload the React DevTools for a better development experience: https://reactjs.org/link/react-devtools" + (lT === "file:" ? `
You might need to use a local HTTP server (instead of file://): https://reactjs.org/link/react-devtools-faq` : ""), "font-weight:bold");
    }
    ti.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED = uC, ti.createPortal = mD, ti.createRoot = gD, ti.findDOMNode = fD, ti.flushSync = ED, ti.hydrate = dD, ti.hydrateRoot = SD, ti.render = pD, ti.unmountComponentAtNode = hD, ti.unstable_batchedUpdates = jE, ti.unstable_renderSubtreeIntoContainer = yD, ti.version = eC, typeof __REACT_DEVTOOLS_GLOBAL_HOOK__ < "u" && typeof __REACT_DEVTOOLS_GLOBAL_HOOK__.registerInternalModuleStop == "function" && __REACT_DEVTOOLS_GLOBAL_HOOK__.registerInternalModuleStop(new Error());
  }()), ti;
}
function OT() {
  if (!(typeof __REACT_DEVTOOLS_GLOBAL_HOOK__ > "u" || typeof __REACT_DEVTOOLS_GLOBAL_HOOK__.checkDCE != "function")) {
    if (vu.env.NODE_ENV !== "production")
      throw new Error("^_^");
    try {
      __REACT_DEVTOOLS_GLOBAL_HOOK__.checkDCE(OT);
    } catch (h) {
      console.error(h);
    }
  }
}
vu.env.NODE_ENV === "production" ? (OT(), pC.exports = ND()) : pC.exports = MD();
var LD = pC.exports, Dv = LD;
if (vu.env.NODE_ENV === "production")
  Nv.createRoot = Dv.createRoot, Nv.hydrateRoot = Dv.hydrateRoot;
else {
  var My = Dv.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED;
  Nv.createRoot = function(h, c) {
    My.usingClientEntryPoint = !0;
    try {
      return Dv.createRoot(h, c);
    } finally {
      My.usingClientEntryPoint = !1;
    }
  }, Nv.hydrateRoot = function(h, c, p) {
    My.usingClientEntryPoint = !0;
    try {
      return Dv.hydrateRoot(h, c, p);
    } finally {
      My.usingClientEntryPoint = !1;
    }
  };
}
function NT(h) {
  var c, p, S = "";
  if (typeof h == "string" || typeof h == "number") S += h;
  else if (typeof h == "object") if (Array.isArray(h)) {
    var _ = h.length;
    for (c = 0; c < _; c++) h[c] && (p = NT(h[c])) && (S && (S += " "), S += p);
  } else for (p in h) h[p] && (S && (S += " "), S += p);
  return S;
}
function xC() {
  for (var h, c, p = 0, S = "", _ = arguments.length; p < _; p++) (h = arguments[p]) && (c = NT(h)) && (S && (S += " "), S += c);
  return S;
}
function AD(h) {
  return h.avatarLabel ? h.avatarLabel : h.author.trim().slice(0, 1).toUpperCase() || "?";
}
function zD(h) {
  return xC("message-bubble", {
    "message-bubble-user": h.role === "user",
    "message-bubble-system": h.role === "system",
    "message-bubble-tool": h.role === "tool",
    "message-bubble-assistant": h.role === "assistant"
  });
}
function UD(h) {
  return xC("message-row", {
    "message-row-user": h.role === "user",
    "message-row-system": h.role === "system",
    "message-row-assistant": h.role === "assistant" || h.role === "tool"
  });
}
function yT(h) {
  return xC("avatar", {
    "avatar-user": h.role === "user",
    "avatar-tool": h.role === "tool",
    "avatar-assistant": h.role === "assistant"
  });
}
function gT({
  block: h,
  message: c,
  onAction: p
}) {
  return h.type === "text" ? /* @__PURE__ */ Fe.jsx("div", { className: "message-block message-block-text", children: h.text }) : h.type === "image" ? /* @__PURE__ */ Fe.jsx(
    "figure",
    {
      className: "message-block message-block-image",
      style: h.width && h.height ? { aspectRatio: `${h.width} / ${h.height}` } : void 0,
      children: /* @__PURE__ */ Fe.jsx("img", { src: h.url, alt: h.alt || "", loading: "lazy" })
    }
  ) : h.type === "link" ? /* @__PURE__ */ Fe.jsxs(
    "a",
    {
      className: "message-block message-block-link",
      href: h.url,
      target: "_blank",
      rel: "noreferrer",
      children: [
        h.thumbnailUrl ? /* @__PURE__ */ Fe.jsx("div", { className: "message-link-thumb", children: /* @__PURE__ */ Fe.jsx("img", { src: h.thumbnailUrl, alt: "", loading: "lazy" }) }) : null,
        /* @__PURE__ */ Fe.jsxs("div", { className: "message-link-copy", children: [
          /* @__PURE__ */ Fe.jsx("div", { className: "message-link-title", children: h.title || h.url }),
          h.description ? /* @__PURE__ */ Fe.jsx("div", { className: "message-link-description", children: h.description }) : null,
          /* @__PURE__ */ Fe.jsx("div", { className: "message-link-url", children: h.siteName || h.url })
        ] })
      ]
    }
  ) : h.type === "status" ? /* @__PURE__ */ Fe.jsx("div", { className: `message-block message-block-status tone-${h.tone || "info"}`, children: h.text }) : h.type === "buttons" ? /* @__PURE__ */ Fe.jsx("div", { className: "message-block message-block-buttons", children: h.buttons.map((S) => /* @__PURE__ */ Fe.jsx(
    "button",
    {
      className: `message-action-button variant-${S.variant || "secondary"}`,
      type: "button",
      disabled: S.disabled,
      onClick: () => p == null ? void 0 : p(c, S),
      children: S.label
    },
    S.id
  )) }) : null;
}
function jD({
  message: h,
  isGroupedWithPrevious: c = !1,
  onAction: p
}) {
  const S = zD(h), _ = UD(h), T = h.role !== "system" && !c, E = h.role !== "system";
  return h.role === "system" ? /* @__PURE__ */ Fe.jsx(
    "article",
    {
      className: _,
      "data-message-id": h.id,
      "data-message-role": h.role,
      "data-message-sort-key": h.sortKey ?? "",
      children: /* @__PURE__ */ Fe.jsxs("div", { className: "system-chip", children: [
        /* @__PURE__ */ Fe.jsx("span", { className: "system-chip-time", children: h.time }),
        /* @__PURE__ */ Fe.jsx("div", { className: "system-chip-content", children: h.blocks.map((A, I) => /* @__PURE__ */ Fe.jsx(
          gT,
          {
            block: A,
            message: h,
            onAction: p
          },
          `${h.id}-${A.type}-${I}`
        )) })
      ] })
    }
  ) : /* @__PURE__ */ Fe.jsxs(
    "article",
    {
      className: _,
      "data-message-id": h.id,
      "data-message-role": h.role,
      "data-message-status": h.status || "",
      "data-message-sort-key": h.sortKey ?? "",
      children: [
        T ? h.avatarUrl ? /* @__PURE__ */ Fe.jsx("img", { className: `${yT(h)} avatar-image`, src: h.avatarUrl, alt: h.author }) : /* @__PURE__ */ Fe.jsx("div", { className: yT(h), children: AD(h) }) : /* @__PURE__ */ Fe.jsx("div", { className: "avatar avatar-placeholder", "aria-hidden": "true" }),
        /* @__PURE__ */ Fe.jsxs("div", { className: "message-stack", children: [
          E ? /* @__PURE__ */ Fe.jsxs("div", { className: "message-meta", children: [
            /* @__PURE__ */ Fe.jsx("span", { className: "message-author", children: h.author }),
            /* @__PURE__ */ Fe.jsx("span", { className: "message-time", children: h.time }),
            h.status === "streaming" ? /* @__PURE__ */ Fe.jsx("span", { className: "message-delivery", children: "生成中" }) : null,
            h.status === "failed" ? /* @__PURE__ */ Fe.jsx("span", { className: "message-delivery message-delivery-failed", children: "发送失败" }) : null
          ] }) : null,
          /* @__PURE__ */ Fe.jsx("div", { className: S, children: h.blocks.map((A, I) => /* @__PURE__ */ Fe.jsx(
            gT,
            {
              block: A,
              message: h,
              onAction: p
            },
            `${h.id}-${A.type}-${I}`
          )) }),
          h.actions && h.actions.length > 0 ? /* @__PURE__ */ Fe.jsx("div", { className: "message-inline-actions", children: h.actions.map((A) => /* @__PURE__ */ Fe.jsx(
            "button",
            {
              className: `message-action-button variant-${A.variant || "secondary"}`,
              type: "button",
              disabled: A.disabled,
              onClick: () => p == null ? void 0 : p(h, A),
              children: A.label
            },
            A.id
          )) }) : null
        ] })
      ]
    }
  );
}
function FD(h, c) {
  return !(!c || h.role !== c.role || h.author !== c.author || h.role === "system" || typeof h.createdAt == "number" && typeof c.createdAt == "number" && Math.abs(h.createdAt - c.createdAt) > 5 * 60 * 1e3);
}
function HD({
  messages: h,
  emptyText: c = "聊天内容接入后会显示在这里。",
  onAction: p
}) {
  return h.length === 0 ? /* @__PURE__ */ Fe.jsx("div", { className: "message-list", "aria-label": "Chat messages", children: /* @__PURE__ */ Fe.jsx("div", { className: "message-empty-state", children: c }) }) : /* @__PURE__ */ Fe.jsx("div", { className: "message-list", "aria-label": "Chat messages", "data-message-list-kind": "static", children: h.map((S, _) => /* @__PURE__ */ Fe.jsx(
    jD,
    {
      message: S,
      isGroupedWithPrevious: FD(S, h[_ - 1]),
      onAction: p
    },
    S.id
  )) });
}
const VD = [];
function ST({
  title: h = "N.E.K.O Chat",
  iconSrc: c = "/static/icons/chat_icon.png",
  messages: p = VD,
  inputPlaceholder: S = "输入消息...",
  sendButtonLabel: _ = "发送",
  onMessageAction: T
}) {
  return /* @__PURE__ */ Fe.jsx("main", { className: "app-shell", children: /* @__PURE__ */ Fe.jsxs("section", { className: "chat-window", "aria-label": "Neko chat window", children: [
    /* @__PURE__ */ Fe.jsx("header", { className: "window-topbar", children: /* @__PURE__ */ Fe.jsxs("div", { className: "window-title-group", children: [
      /* @__PURE__ */ Fe.jsx("div", { className: "window-avatar window-avatar-image-shell", children: /* @__PURE__ */ Fe.jsx("img", { className: "window-avatar-image", src: c, alt: h }) }),
      /* @__PURE__ */ Fe.jsx("h1", { className: "window-title", children: h })
    ] }) }),
    /* @__PURE__ */ Fe.jsx("section", { className: "chat-body", children: /* @__PURE__ */ Fe.jsx(HD, { messages: p, onAction: T }) }),
    /* @__PURE__ */ Fe.jsxs("footer", { className: "composer-panel", children: [
      /* @__PURE__ */ Fe.jsxs("div", { className: "composer-toolbar", "aria-label": "Composer tools", children: [
        /* @__PURE__ */ Fe.jsx("button", { className: "tool-button", type: "button", "aria-label": "表情", children: "☺" }),
        /* @__PURE__ */ Fe.jsx("button", { className: "tool-button", type: "button", "aria-label": "附件", children: "＋" })
      ] }),
      /* @__PURE__ */ Fe.jsx("form", { className: "composer", onSubmit: (E) => E.preventDefault(), children: /* @__PURE__ */ Fe.jsxs("div", { className: "composer-row", children: [
        /* @__PURE__ */ Fe.jsx("label", { className: "composer-input-shell", children: /* @__PURE__ */ Fe.jsx(
          "textarea",
          {
            className: "composer-input",
            placeholder: S,
            rows: 1
          }
        ) }),
        /* @__PURE__ */ Fe.jsx("button", { className: "send-button", type: "submit", children: _ })
      ] }) })
    ] })
  ] }) });
}
var Qt;
(function(h) {
  h.assertEqual = (_) => {
  };
  function c(_) {
  }
  h.assertIs = c;
  function p(_) {
    throw new Error();
  }
  h.assertNever = p, h.arrayToEnum = (_) => {
    const T = {};
    for (const E of _)
      T[E] = E;
    return T;
  }, h.getValidEnumValues = (_) => {
    const T = h.objectKeys(_).filter((A) => typeof _[_[A]] != "number"), E = {};
    for (const A of T)
      E[A] = _[A];
    return h.objectValues(E);
  }, h.objectValues = (_) => h.objectKeys(_).map(function(T) {
    return _[T];
  }), h.objectKeys = typeof Object.keys == "function" ? (_) => Object.keys(_) : (_) => {
    const T = [];
    for (const E in _)
      Object.prototype.hasOwnProperty.call(_, E) && T.push(E);
    return T;
  }, h.find = (_, T) => {
    for (const E of _)
      if (T(E))
        return E;
  }, h.isInteger = typeof Number.isInteger == "function" ? (_) => Number.isInteger(_) : (_) => typeof _ == "number" && Number.isFinite(_) && Math.floor(_) === _;
  function S(_, T = " | ") {
    return _.map((E) => typeof E == "string" ? `'${E}'` : E).join(T);
  }
  h.joinValues = S, h.jsonStringifyReplacer = (_, T) => typeof T == "bigint" ? T.toString() : T;
})(Qt || (Qt = {}));
var ET;
(function(h) {
  h.mergeShapes = (c, p) => ({
    ...c,
    ...p
    // second overwrites first
  });
})(ET || (ET = {}));
const xe = Qt.arrayToEnum([
  "string",
  "nan",
  "number",
  "integer",
  "float",
  "boolean",
  "date",
  "bigint",
  "symbol",
  "function",
  "undefined",
  "null",
  "array",
  "object",
  "unknown",
  "promise",
  "void",
  "never",
  "map",
  "set"
]), fs = (h) => {
  switch (typeof h) {
    case "undefined":
      return xe.undefined;
    case "string":
      return xe.string;
    case "number":
      return Number.isNaN(h) ? xe.nan : xe.number;
    case "boolean":
      return xe.boolean;
    case "function":
      return xe.function;
    case "bigint":
      return xe.bigint;
    case "symbol":
      return xe.symbol;
    case "object":
      return Array.isArray(h) ? xe.array : h === null ? xe.null : h.then && typeof h.then == "function" && h.catch && typeof h.catch == "function" ? xe.promise : typeof Map < "u" && h instanceof Map ? xe.map : typeof Set < "u" && h instanceof Set ? xe.set : typeof Date < "u" && h instanceof Date ? xe.date : xe.object;
    default:
      return xe.unknown;
  }
}, ae = Qt.arrayToEnum([
  "invalid_type",
  "invalid_literal",
  "custom",
  "invalid_union",
  "invalid_union_discriminator",
  "invalid_enum_value",
  "unrecognized_keys",
  "invalid_arguments",
  "invalid_return_type",
  "invalid_date",
  "invalid_string",
  "too_small",
  "too_big",
  "invalid_intersection_types",
  "not_multiple_of",
  "not_finite"
]);
class Xi extends Error {
  get errors() {
    return this.issues;
  }
  constructor(c) {
    super(), this.issues = [], this.addIssue = (S) => {
      this.issues = [...this.issues, S];
    }, this.addIssues = (S = []) => {
      this.issues = [...this.issues, ...S];
    };
    const p = new.target.prototype;
    Object.setPrototypeOf ? Object.setPrototypeOf(this, p) : this.__proto__ = p, this.name = "ZodError", this.issues = c;
  }
  format(c) {
    const p = c || function(T) {
      return T.message;
    }, S = { _errors: [] }, _ = (T) => {
      for (const E of T.issues)
        if (E.code === "invalid_union")
          E.unionErrors.map(_);
        else if (E.code === "invalid_return_type")
          _(E.returnTypeError);
        else if (E.code === "invalid_arguments")
          _(E.argumentsError);
        else if (E.path.length === 0)
          S._errors.push(p(E));
        else {
          let A = S, I = 0;
          for (; I < E.path.length; ) {
            const $ = E.path[I];
            I === E.path.length - 1 ? (A[$] = A[$] || { _errors: [] }, A[$]._errors.push(p(E))) : A[$] = A[$] || { _errors: [] }, A = A[$], I++;
          }
        }
    };
    return _(this), S;
  }
  static assert(c) {
    if (!(c instanceof Xi))
      throw new Error(`Not a ZodError: ${c}`);
  }
  toString() {
    return this.message;
  }
  get message() {
    return JSON.stringify(this.issues, Qt.jsonStringifyReplacer, 2);
  }
  get isEmpty() {
    return this.issues.length === 0;
  }
  flatten(c = (p) => p.message) {
    const p = {}, S = [];
    for (const _ of this.issues)
      if (_.path.length > 0) {
        const T = _.path[0];
        p[T] = p[T] || [], p[T].push(c(_));
      } else
        S.push(c(_));
    return { formErrors: S, fieldErrors: p };
  }
  get formErrors() {
    return this.flatten();
  }
}
Xi.create = (h) => new Xi(h);
const Av = (h, c) => {
  let p;
  switch (h.code) {
    case ae.invalid_type:
      h.received === xe.undefined ? p = "Required" : p = `Expected ${h.expected}, received ${h.received}`;
      break;
    case ae.invalid_literal:
      p = `Invalid literal value, expected ${JSON.stringify(h.expected, Qt.jsonStringifyReplacer)}`;
      break;
    case ae.unrecognized_keys:
      p = `Unrecognized key(s) in object: ${Qt.joinValues(h.keys, ", ")}`;
      break;
    case ae.invalid_union:
      p = "Invalid input";
      break;
    case ae.invalid_union_discriminator:
      p = `Invalid discriminator value. Expected ${Qt.joinValues(h.options)}`;
      break;
    case ae.invalid_enum_value:
      p = `Invalid enum value. Expected ${Qt.joinValues(h.options)}, received '${h.received}'`;
      break;
    case ae.invalid_arguments:
      p = "Invalid function arguments";
      break;
    case ae.invalid_return_type:
      p = "Invalid function return type";
      break;
    case ae.invalid_date:
      p = "Invalid date";
      break;
    case ae.invalid_string:
      typeof h.validation == "object" ? "includes" in h.validation ? (p = `Invalid input: must include "${h.validation.includes}"`, typeof h.validation.position == "number" && (p = `${p} at one or more positions greater than or equal to ${h.validation.position}`)) : "startsWith" in h.validation ? p = `Invalid input: must start with "${h.validation.startsWith}"` : "endsWith" in h.validation ? p = `Invalid input: must end with "${h.validation.endsWith}"` : Qt.assertNever(h.validation) : h.validation !== "regex" ? p = `Invalid ${h.validation}` : p = "Invalid";
      break;
    case ae.too_small:
      h.type === "array" ? p = `Array must contain ${h.exact ? "exactly" : h.inclusive ? "at least" : "more than"} ${h.minimum} element(s)` : h.type === "string" ? p = `String must contain ${h.exact ? "exactly" : h.inclusive ? "at least" : "over"} ${h.minimum} character(s)` : h.type === "number" ? p = `Number must be ${h.exact ? "exactly equal to " : h.inclusive ? "greater than or equal to " : "greater than "}${h.minimum}` : h.type === "bigint" ? p = `Number must be ${h.exact ? "exactly equal to " : h.inclusive ? "greater than or equal to " : "greater than "}${h.minimum}` : h.type === "date" ? p = `Date must be ${h.exact ? "exactly equal to " : h.inclusive ? "greater than or equal to " : "greater than "}${new Date(Number(h.minimum))}` : p = "Invalid input";
      break;
    case ae.too_big:
      h.type === "array" ? p = `Array must contain ${h.exact ? "exactly" : h.inclusive ? "at most" : "less than"} ${h.maximum} element(s)` : h.type === "string" ? p = `String must contain ${h.exact ? "exactly" : h.inclusive ? "at most" : "under"} ${h.maximum} character(s)` : h.type === "number" ? p = `Number must be ${h.exact ? "exactly" : h.inclusive ? "less than or equal to" : "less than"} ${h.maximum}` : h.type === "bigint" ? p = `BigInt must be ${h.exact ? "exactly" : h.inclusive ? "less than or equal to" : "less than"} ${h.maximum}` : h.type === "date" ? p = `Date must be ${h.exact ? "exactly" : h.inclusive ? "smaller than or equal to" : "smaller than"} ${new Date(Number(h.maximum))}` : p = "Invalid input";
      break;
    case ae.custom:
      p = "Invalid input";
      break;
    case ae.invalid_intersection_types:
      p = "Intersection results could not be merged";
      break;
    case ae.not_multiple_of:
      p = `Number must be a multiple of ${h.multipleOf}`;
      break;
    case ae.not_finite:
      p = "Number must be finite";
      break;
    default:
      p = c.defaultError, Qt.assertNever(h);
  }
  return { message: p };
};
let PD = Av;
function vC() {
  return PD;
}
const hC = (h) => {
  const { data: c, path: p, errorMaps: S, issueData: _ } = h, T = [...p, ..._.path || []], E = {
    ..._,
    path: T
  };
  if (_.message !== void 0)
    return {
      ..._,
      path: T,
      message: _.message
    };
  let A = "";
  const I = S.filter(($) => !!$).slice().reverse();
  for (const $ of I)
    A = $(E, { data: c, defaultError: A }).message;
  return {
    ..._,
    path: T,
    message: A
  };
};
function ye(h, c) {
  const p = vC(), S = hC({
    issueData: c,
    data: h.data,
    path: h.path,
    errorMaps: [
      h.common.contextualErrorMap,
      // contextual error map is first priority
      h.schemaErrorMap,
      // then schema-bound map if available
      p,
      // then global override map
      p === Av ? void 0 : Av
      // then global default map
    ].filter((_) => !!_)
  });
  h.common.issues.push(S);
}
class Aa {
  constructor() {
    this.value = "valid";
  }
  dirty() {
    this.value === "valid" && (this.value = "dirty");
  }
  abort() {
    this.value !== "aborted" && (this.value = "aborted");
  }
  static mergeArray(c, p) {
    const S = [];
    for (const _ of p) {
      if (_.status === "aborted")
        return et;
      _.status === "dirty" && c.dirty(), S.push(_.value);
    }
    return { status: c.value, value: S };
  }
  static async mergeObjectAsync(c, p) {
    const S = [];
    for (const _ of p) {
      const T = await _.key, E = await _.value;
      S.push({
        key: T,
        value: E
      });
    }
    return Aa.mergeObjectSync(c, S);
  }
  static mergeObjectSync(c, p) {
    const S = {};
    for (const _ of p) {
      const { key: T, value: E } = _;
      if (T.status === "aborted" || E.status === "aborted")
        return et;
      T.status === "dirty" && c.dirty(), E.status === "dirty" && c.dirty(), T.value !== "__proto__" && (typeof E.value < "u" || _.alwaysSet) && (S[T.value] = E.value);
    }
    return { status: c.value, value: S };
  }
}
const et = Object.freeze({
  status: "aborted"
}), Mv = (h) => ({ status: "dirty", value: h }), ni = (h) => ({ status: "valid", value: h }), CT = (h) => h.status === "aborted", _T = (h) => h.status === "dirty", Ed = (h) => h.status === "valid", Ly = (h) => typeof Promise < "u" && h instanceof Promise;
var Ae;
(function(h) {
  h.errToObj = (c) => typeof c == "string" ? { message: c } : c || {}, h.toString = (c) => typeof c == "string" ? c : c == null ? void 0 : c.message;
})(Ae || (Ae = {}));
class pu {
  constructor(c, p, S, _) {
    this._cachedPath = [], this.parent = c, this.data = p, this._path = S, this._key = _;
  }
  get path() {
    return this._cachedPath.length || (Array.isArray(this._key) ? this._cachedPath.push(...this._path, ...this._key) : this._cachedPath.push(...this._path, this._key)), this._cachedPath;
  }
}
const xT = (h, c) => {
  if (Ed(c))
    return { success: !0, data: c.value };
  if (!h.common.issues.length)
    throw new Error("Validation failed but no issues detected.");
  return {
    success: !1,
    get error() {
      if (this._error)
        return this._error;
      const p = new Xi(h.common.issues);
      return this._error = p, this._error;
    }
  };
};
function gt(h) {
  if (!h)
    return {};
  const { errorMap: c, invalid_type_error: p, required_error: S, description: _ } = h;
  if (c && (p || S))
    throw new Error(`Can't use "invalid_type_error" or "required_error" in conjunction with custom error map.`);
  return c ? { errorMap: c, description: _ } : { errorMap: (E, A) => {
    const { message: I } = h;
    return E.code === "invalid_enum_value" ? { message: I ?? A.defaultError } : typeof A.data > "u" ? { message: I ?? S ?? A.defaultError } : E.code !== "invalid_type" ? { message: A.defaultError } : { message: I ?? p ?? A.defaultError };
  }, description: _ };
}
class Mt {
  get description() {
    return this._def.description;
  }
  _getType(c) {
    return fs(c.data);
  }
  _getOrReturnCtx(c, p) {
    return p || {
      common: c.parent.common,
      data: c.data,
      parsedType: fs(c.data),
      schemaErrorMap: this._def.errorMap,
      path: c.path,
      parent: c.parent
    };
  }
  _processInputParams(c) {
    return {
      status: new Aa(),
      ctx: {
        common: c.parent.common,
        data: c.data,
        parsedType: fs(c.data),
        schemaErrorMap: this._def.errorMap,
        path: c.path,
        parent: c.parent
      }
    };
  }
  _parseSync(c) {
    const p = this._parse(c);
    if (Ly(p))
      throw new Error("Synchronous parse encountered promise.");
    return p;
  }
  _parseAsync(c) {
    const p = this._parse(c);
    return Promise.resolve(p);
  }
  parse(c, p) {
    const S = this.safeParse(c, p);
    if (S.success)
      return S.data;
    throw S.error;
  }
  safeParse(c, p) {
    const S = {
      common: {
        issues: [],
        async: (p == null ? void 0 : p.async) ?? !1,
        contextualErrorMap: p == null ? void 0 : p.errorMap
      },
      path: (p == null ? void 0 : p.path) || [],
      schemaErrorMap: this._def.errorMap,
      parent: null,
      data: c,
      parsedType: fs(c)
    }, _ = this._parseSync({ data: c, path: S.path, parent: S });
    return xT(S, _);
  }
  "~validate"(c) {
    var S, _;
    const p = {
      common: {
        issues: [],
        async: !!this["~standard"].async
      },
      path: [],
      schemaErrorMap: this._def.errorMap,
      parent: null,
      data: c,
      parsedType: fs(c)
    };
    if (!this["~standard"].async)
      try {
        const T = this._parseSync({ data: c, path: [], parent: p });
        return Ed(T) ? {
          value: T.value
        } : {
          issues: p.common.issues
        };
      } catch (T) {
        (_ = (S = T == null ? void 0 : T.message) == null ? void 0 : S.toLowerCase()) != null && _.includes("encountered") && (this["~standard"].async = !0), p.common = {
          issues: [],
          async: !0
        };
      }
    return this._parseAsync({ data: c, path: [], parent: p }).then((T) => Ed(T) ? {
      value: T.value
    } : {
      issues: p.common.issues
    });
  }
  async parseAsync(c, p) {
    const S = await this.safeParseAsync(c, p);
    if (S.success)
      return S.data;
    throw S.error;
  }
  async safeParseAsync(c, p) {
    const S = {
      common: {
        issues: [],
        contextualErrorMap: p == null ? void 0 : p.errorMap,
        async: !0
      },
      path: (p == null ? void 0 : p.path) || [],
      schemaErrorMap: this._def.errorMap,
      parent: null,
      data: c,
      parsedType: fs(c)
    }, _ = this._parse({ data: c, path: S.path, parent: S }), T = await (Ly(_) ? _ : Promise.resolve(_));
    return xT(S, T);
  }
  refine(c, p) {
    const S = (_) => typeof p == "string" || typeof p > "u" ? { message: p } : typeof p == "function" ? p(_) : p;
    return this._refinement((_, T) => {
      const E = c(_), A = () => T.addIssue({
        code: ae.custom,
        ...S(_)
      });
      return typeof Promise < "u" && E instanceof Promise ? E.then((I) => I ? !0 : (A(), !1)) : E ? !0 : (A(), !1);
    });
  }
  refinement(c, p) {
    return this._refinement((S, _) => c(S) ? !0 : (_.addIssue(typeof p == "function" ? p(S, _) : p), !1));
  }
  _refinement(c) {
    return new Oc({
      schema: this,
      typeName: tt.ZodEffects,
      effect: { type: "refinement", refinement: c }
    });
  }
  superRefine(c) {
    return this._refinement(c);
  }
  constructor(c) {
    this.spa = this.safeParseAsync, this._def = c, this.parse = this.parse.bind(this), this.safeParse = this.safeParse.bind(this), this.parseAsync = this.parseAsync.bind(this), this.safeParseAsync = this.safeParseAsync.bind(this), this.spa = this.spa.bind(this), this.refine = this.refine.bind(this), this.refinement = this.refinement.bind(this), this.superRefine = this.superRefine.bind(this), this.optional = this.optional.bind(this), this.nullable = this.nullable.bind(this), this.nullish = this.nullish.bind(this), this.array = this.array.bind(this), this.promise = this.promise.bind(this), this.or = this.or.bind(this), this.and = this.and.bind(this), this.transform = this.transform.bind(this), this.brand = this.brand.bind(this), this.default = this.default.bind(this), this.catch = this.catch.bind(this), this.describe = this.describe.bind(this), this.pipe = this.pipe.bind(this), this.readonly = this.readonly.bind(this), this.isNullable = this.isNullable.bind(this), this.isOptional = this.isOptional.bind(this), this["~standard"] = {
      version: 1,
      vendor: "zod",
      validate: (p) => this["~validate"](p)
    };
  }
  optional() {
    return fo.create(this, this._def);
  }
  nullable() {
    return Nc.create(this, this._def);
  }
  nullish() {
    return this.nullable().optional();
  }
  array() {
    return du.create(this);
  }
  promise() {
    return jv.create(this, this._def);
  }
  or(c) {
    return zy.create([this, c], this._def);
  }
  and(c) {
    return Uy.create(this, c, this._def);
  }
  transform(c) {
    return new Oc({
      ...gt(this._def),
      schema: this,
      typeName: tt.ZodEffects,
      effect: { type: "transform", transform: c }
    });
  }
  default(c) {
    const p = typeof c == "function" ? c : () => c;
    return new Hy({
      ...gt(this._def),
      innerType: this,
      defaultValue: p,
      typeName: tt.ZodDefault
    });
  }
  brand() {
    return new zT({
      typeName: tt.ZodBranded,
      type: this,
      ...gt(this._def)
    });
  }
  catch(c) {
    const p = typeof c == "function" ? c : () => c;
    return new Vy({
      ...gt(this._def),
      innerType: this,
      catchValue: p,
      typeName: tt.ZodCatch
    });
  }
  describe(c) {
    const p = this.constructor;
    return new p({
      ...this._def,
      description: c
    });
  }
  pipe(c) {
    return RC.create(this, c);
  }
  readonly() {
    return Py.create(this);
  }
  isOptional() {
    return this.safeParse(void 0).success;
  }
  isNullable() {
    return this.safeParse(null).success;
  }
}
const BD = /^c[^\s-]{8,}$/i, ID = /^[0-9a-z]+$/, $D = /^[0-9A-HJKMNP-TV-Z]{26}$/i, YD = /^[0-9a-fA-F]{8}\b-[0-9a-fA-F]{4}\b-[0-9a-fA-F]{4}\b-[0-9a-fA-F]{4}\b-[0-9a-fA-F]{12}$/i, WD = /^[a-z0-9_-]{21}$/i, QD = /^[A-Za-z0-9-_]+\.[A-Za-z0-9-_]+\.[A-Za-z0-9-_]*$/, ZD = /^[-+]?P(?!$)(?:(?:[-+]?\d+Y)|(?:[-+]?\d+[.,]\d+Y$))?(?:(?:[-+]?\d+M)|(?:[-+]?\d+[.,]\d+M$))?(?:(?:[-+]?\d+W)|(?:[-+]?\d+[.,]\d+W$))?(?:(?:[-+]?\d+D)|(?:[-+]?\d+[.,]\d+D$))?(?:T(?=[\d+-])(?:(?:[-+]?\d+H)|(?:[-+]?\d+[.,]\d+H$))?(?:(?:[-+]?\d+M)|(?:[-+]?\d+[.,]\d+M$))?(?:[-+]?\d+(?:[.,]\d+)?S)?)??$/, GD = /^(?!\.)(?!.*\.\.)([A-Z0-9_'+\-\.]*)[A-Z0-9_+-]@([A-Z0-9][A-Z0-9\-]*\.)+[A-Z]{2,}$/i, qD = "^(\\p{Extended_Pictographic}|\\p{Emoji_Component})+$";
let cC;
const XD = /^(?:(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9][0-9]|[0-9])\.){3}(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9][0-9]|[0-9])$/, KD = /^(?:(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9][0-9]|[0-9])\.){3}(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9][0-9]|[0-9])\/(3[0-2]|[12]?[0-9])$/, JD = /^(([0-9a-fA-F]{1,4}:){7,7}[0-9a-fA-F]{1,4}|([0-9a-fA-F]{1,4}:){1,7}:|([0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}|([0-9a-fA-F]{1,4}:){1,5}(:[0-9a-fA-F]{1,4}){1,2}|([0-9a-fA-F]{1,4}:){1,4}(:[0-9a-fA-F]{1,4}){1,3}|([0-9a-fA-F]{1,4}:){1,3}(:[0-9a-fA-F]{1,4}){1,4}|([0-9a-fA-F]{1,4}:){1,2}(:[0-9a-fA-F]{1,4}){1,5}|[0-9a-fA-F]{1,4}:((:[0-9a-fA-F]{1,4}){1,6})|:((:[0-9a-fA-F]{1,4}){1,7}|:)|fe80:(:[0-9a-fA-F]{0,4}){0,4}%[0-9a-zA-Z]{1,}|::(ffff(:0{1,4}){0,1}:){0,1}((25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9])\.){3,3}(25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9])|([0-9a-fA-F]{1,4}:){1,4}:((25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9])\.){3,3}(25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9]))$/, eO = /^(([0-9a-fA-F]{1,4}:){7,7}[0-9a-fA-F]{1,4}|([0-9a-fA-F]{1,4}:){1,7}:|([0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}|([0-9a-fA-F]{1,4}:){1,5}(:[0-9a-fA-F]{1,4}){1,2}|([0-9a-fA-F]{1,4}:){1,4}(:[0-9a-fA-F]{1,4}){1,3}|([0-9a-fA-F]{1,4}:){1,3}(:[0-9a-fA-F]{1,4}){1,4}|([0-9a-fA-F]{1,4}:){1,2}(:[0-9a-fA-F]{1,4}){1,5}|[0-9a-fA-F]{1,4}:((:[0-9a-fA-F]{1,4}){1,6})|:((:[0-9a-fA-F]{1,4}){1,7}|:)|fe80:(:[0-9a-fA-F]{0,4}){0,4}%[0-9a-zA-Z]{1,}|::(ffff(:0{1,4}){0,1}:){0,1}((25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9])\.){3,3}(25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9])|([0-9a-fA-F]{1,4}:){1,4}:((25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9])\.){3,3}(25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9]))\/(12[0-8]|1[01][0-9]|[1-9]?[0-9])$/, tO = /^([0-9a-zA-Z+/]{4})*(([0-9a-zA-Z+/]{2}==)|([0-9a-zA-Z+/]{3}=))?$/, nO = /^([0-9a-zA-Z-_]{4})*(([0-9a-zA-Z-_]{2}(==)?)|([0-9a-zA-Z-_]{3}(=)?))?$/, MT = "((\\d\\d[2468][048]|\\d\\d[13579][26]|\\d\\d0[48]|[02468][048]00|[13579][26]00)-02-29|\\d{4}-((0[13578]|1[02])-(0[1-9]|[12]\\d|3[01])|(0[469]|11)-(0[1-9]|[12]\\d|30)|(02)-(0[1-9]|1\\d|2[0-8])))", rO = new RegExp(`^${MT}$`);
function LT(h) {
  let c = "[0-5]\\d";
  h.precision ? c = `${c}\\.\\d{${h.precision}}` : h.precision == null && (c = `${c}(\\.\\d+)?`);
  const p = h.precision ? "+" : "?";
  return `([01]\\d|2[0-3]):[0-5]\\d(:${c})${p}`;
}
function aO(h) {
  return new RegExp(`^${LT(h)}$`);
}
function iO(h) {
  let c = `${MT}T${LT(h)}`;
  const p = [];
  return p.push(h.local ? "Z?" : "Z"), h.offset && p.push("([+-]\\d{2}:?\\d{2})"), c = `${c}(${p.join("|")})`, new RegExp(`^${c}$`);
}
function lO(h, c) {
  return !!((c === "v4" || !c) && XD.test(h) || (c === "v6" || !c) && JD.test(h));
}
function uO(h, c) {
  if (!QD.test(h))
    return !1;
  try {
    const [p] = h.split(".");
    if (!p)
      return !1;
    const S = p.replace(/-/g, "+").replace(/_/g, "/").padEnd(p.length + (4 - p.length % 4) % 4, "="), _ = JSON.parse(atob(S));
    return !(typeof _ != "object" || _ === null || "typ" in _ && (_ == null ? void 0 : _.typ) !== "JWT" || !_.alg || c && _.alg !== c);
  } catch {
    return !1;
  }
}
function oO(h, c) {
  return !!((c === "v4" || !c) && KD.test(h) || (c === "v6" || !c) && eO.test(h));
}
class co extends Mt {
  _parse(c) {
    if (this._def.coerce && (c.data = String(c.data)), this._getType(c) !== xe.string) {
      const T = this._getOrReturnCtx(c);
      return ye(T, {
        code: ae.invalid_type,
        expected: xe.string,
        received: T.parsedType
      }), et;
    }
    const S = new Aa();
    let _;
    for (const T of this._def.checks)
      if (T.kind === "min")
        c.data.length < T.value && (_ = this._getOrReturnCtx(c, _), ye(_, {
          code: ae.too_small,
          minimum: T.value,
          type: "string",
          inclusive: !0,
          exact: !1,
          message: T.message
        }), S.dirty());
      else if (T.kind === "max")
        c.data.length > T.value && (_ = this._getOrReturnCtx(c, _), ye(_, {
          code: ae.too_big,
          maximum: T.value,
          type: "string",
          inclusive: !0,
          exact: !1,
          message: T.message
        }), S.dirty());
      else if (T.kind === "length") {
        const E = c.data.length > T.value, A = c.data.length < T.value;
        (E || A) && (_ = this._getOrReturnCtx(c, _), E ? ye(_, {
          code: ae.too_big,
          maximum: T.value,
          type: "string",
          inclusive: !0,
          exact: !0,
          message: T.message
        }) : A && ye(_, {
          code: ae.too_small,
          minimum: T.value,
          type: "string",
          inclusive: !0,
          exact: !0,
          message: T.message
        }), S.dirty());
      } else if (T.kind === "email")
        GD.test(c.data) || (_ = this._getOrReturnCtx(c, _), ye(_, {
          validation: "email",
          code: ae.invalid_string,
          message: T.message
        }), S.dirty());
      else if (T.kind === "emoji")
        cC || (cC = new RegExp(qD, "u")), cC.test(c.data) || (_ = this._getOrReturnCtx(c, _), ye(_, {
          validation: "emoji",
          code: ae.invalid_string,
          message: T.message
        }), S.dirty());
      else if (T.kind === "uuid")
        YD.test(c.data) || (_ = this._getOrReturnCtx(c, _), ye(_, {
          validation: "uuid",
          code: ae.invalid_string,
          message: T.message
        }), S.dirty());
      else if (T.kind === "nanoid")
        WD.test(c.data) || (_ = this._getOrReturnCtx(c, _), ye(_, {
          validation: "nanoid",
          code: ae.invalid_string,
          message: T.message
        }), S.dirty());
      else if (T.kind === "cuid")
        BD.test(c.data) || (_ = this._getOrReturnCtx(c, _), ye(_, {
          validation: "cuid",
          code: ae.invalid_string,
          message: T.message
        }), S.dirty());
      else if (T.kind === "cuid2")
        ID.test(c.data) || (_ = this._getOrReturnCtx(c, _), ye(_, {
          validation: "cuid2",
          code: ae.invalid_string,
          message: T.message
        }), S.dirty());
      else if (T.kind === "ulid")
        $D.test(c.data) || (_ = this._getOrReturnCtx(c, _), ye(_, {
          validation: "ulid",
          code: ae.invalid_string,
          message: T.message
        }), S.dirty());
      else if (T.kind === "url")
        try {
          new URL(c.data);
        } catch {
          _ = this._getOrReturnCtx(c, _), ye(_, {
            validation: "url",
            code: ae.invalid_string,
            message: T.message
          }), S.dirty();
        }
      else T.kind === "regex" ? (T.regex.lastIndex = 0, T.regex.test(c.data) || (_ = this._getOrReturnCtx(c, _), ye(_, {
        validation: "regex",
        code: ae.invalid_string,
        message: T.message
      }), S.dirty())) : T.kind === "trim" ? c.data = c.data.trim() : T.kind === "includes" ? c.data.includes(T.value, T.position) || (_ = this._getOrReturnCtx(c, _), ye(_, {
        code: ae.invalid_string,
        validation: { includes: T.value, position: T.position },
        message: T.message
      }), S.dirty()) : T.kind === "toLowerCase" ? c.data = c.data.toLowerCase() : T.kind === "toUpperCase" ? c.data = c.data.toUpperCase() : T.kind === "startsWith" ? c.data.startsWith(T.value) || (_ = this._getOrReturnCtx(c, _), ye(_, {
        code: ae.invalid_string,
        validation: { startsWith: T.value },
        message: T.message
      }), S.dirty()) : T.kind === "endsWith" ? c.data.endsWith(T.value) || (_ = this._getOrReturnCtx(c, _), ye(_, {
        code: ae.invalid_string,
        validation: { endsWith: T.value },
        message: T.message
      }), S.dirty()) : T.kind === "datetime" ? iO(T).test(c.data) || (_ = this._getOrReturnCtx(c, _), ye(_, {
        code: ae.invalid_string,
        validation: "datetime",
        message: T.message
      }), S.dirty()) : T.kind === "date" ? rO.test(c.data) || (_ = this._getOrReturnCtx(c, _), ye(_, {
        code: ae.invalid_string,
        validation: "date",
        message: T.message
      }), S.dirty()) : T.kind === "time" ? aO(T).test(c.data) || (_ = this._getOrReturnCtx(c, _), ye(_, {
        code: ae.invalid_string,
        validation: "time",
        message: T.message
      }), S.dirty()) : T.kind === "duration" ? ZD.test(c.data) || (_ = this._getOrReturnCtx(c, _), ye(_, {
        validation: "duration",
        code: ae.invalid_string,
        message: T.message
      }), S.dirty()) : T.kind === "ip" ? lO(c.data, T.version) || (_ = this._getOrReturnCtx(c, _), ye(_, {
        validation: "ip",
        code: ae.invalid_string,
        message: T.message
      }), S.dirty()) : T.kind === "jwt" ? uO(c.data, T.alg) || (_ = this._getOrReturnCtx(c, _), ye(_, {
        validation: "jwt",
        code: ae.invalid_string,
        message: T.message
      }), S.dirty()) : T.kind === "cidr" ? oO(c.data, T.version) || (_ = this._getOrReturnCtx(c, _), ye(_, {
        validation: "cidr",
        code: ae.invalid_string,
        message: T.message
      }), S.dirty()) : T.kind === "base64" ? tO.test(c.data) || (_ = this._getOrReturnCtx(c, _), ye(_, {
        validation: "base64",
        code: ae.invalid_string,
        message: T.message
      }), S.dirty()) : T.kind === "base64url" ? nO.test(c.data) || (_ = this._getOrReturnCtx(c, _), ye(_, {
        validation: "base64url",
        code: ae.invalid_string,
        message: T.message
      }), S.dirty()) : Qt.assertNever(T);
    return { status: S.value, value: c.data };
  }
  _regex(c, p, S) {
    return this.refinement((_) => c.test(_), {
      validation: p,
      code: ae.invalid_string,
      ...Ae.errToObj(S)
    });
  }
  _addCheck(c) {
    return new co({
      ...this._def,
      checks: [...this._def.checks, c]
    });
  }
  email(c) {
    return this._addCheck({ kind: "email", ...Ae.errToObj(c) });
  }
  url(c) {
    return this._addCheck({ kind: "url", ...Ae.errToObj(c) });
  }
  emoji(c) {
    return this._addCheck({ kind: "emoji", ...Ae.errToObj(c) });
  }
  uuid(c) {
    return this._addCheck({ kind: "uuid", ...Ae.errToObj(c) });
  }
  nanoid(c) {
    return this._addCheck({ kind: "nanoid", ...Ae.errToObj(c) });
  }
  cuid(c) {
    return this._addCheck({ kind: "cuid", ...Ae.errToObj(c) });
  }
  cuid2(c) {
    return this._addCheck({ kind: "cuid2", ...Ae.errToObj(c) });
  }
  ulid(c) {
    return this._addCheck({ kind: "ulid", ...Ae.errToObj(c) });
  }
  base64(c) {
    return this._addCheck({ kind: "base64", ...Ae.errToObj(c) });
  }
  base64url(c) {
    return this._addCheck({
      kind: "base64url",
      ...Ae.errToObj(c)
    });
  }
  jwt(c) {
    return this._addCheck({ kind: "jwt", ...Ae.errToObj(c) });
  }
  ip(c) {
    return this._addCheck({ kind: "ip", ...Ae.errToObj(c) });
  }
  cidr(c) {
    return this._addCheck({ kind: "cidr", ...Ae.errToObj(c) });
  }
  datetime(c) {
    return typeof c == "string" ? this._addCheck({
      kind: "datetime",
      precision: null,
      offset: !1,
      local: !1,
      message: c
    }) : this._addCheck({
      kind: "datetime",
      precision: typeof (c == null ? void 0 : c.precision) > "u" ? null : c == null ? void 0 : c.precision,
      offset: (c == null ? void 0 : c.offset) ?? !1,
      local: (c == null ? void 0 : c.local) ?? !1,
      ...Ae.errToObj(c == null ? void 0 : c.message)
    });
  }
  date(c) {
    return this._addCheck({ kind: "date", message: c });
  }
  time(c) {
    return typeof c == "string" ? this._addCheck({
      kind: "time",
      precision: null,
      message: c
    }) : this._addCheck({
      kind: "time",
      precision: typeof (c == null ? void 0 : c.precision) > "u" ? null : c == null ? void 0 : c.precision,
      ...Ae.errToObj(c == null ? void 0 : c.message)
    });
  }
  duration(c) {
    return this._addCheck({ kind: "duration", ...Ae.errToObj(c) });
  }
  regex(c, p) {
    return this._addCheck({
      kind: "regex",
      regex: c,
      ...Ae.errToObj(p)
    });
  }
  includes(c, p) {
    return this._addCheck({
      kind: "includes",
      value: c,
      position: p == null ? void 0 : p.position,
      ...Ae.errToObj(p == null ? void 0 : p.message)
    });
  }
  startsWith(c, p) {
    return this._addCheck({
      kind: "startsWith",
      value: c,
      ...Ae.errToObj(p)
    });
  }
  endsWith(c, p) {
    return this._addCheck({
      kind: "endsWith",
      value: c,
      ...Ae.errToObj(p)
    });
  }
  min(c, p) {
    return this._addCheck({
      kind: "min",
      value: c,
      ...Ae.errToObj(p)
    });
  }
  max(c, p) {
    return this._addCheck({
      kind: "max",
      value: c,
      ...Ae.errToObj(p)
    });
  }
  length(c, p) {
    return this._addCheck({
      kind: "length",
      value: c,
      ...Ae.errToObj(p)
    });
  }
  /**
   * Equivalent to `.min(1)`
   */
  nonempty(c) {
    return this.min(1, Ae.errToObj(c));
  }
  trim() {
    return new co({
      ...this._def,
      checks: [...this._def.checks, { kind: "trim" }]
    });
  }
  toLowerCase() {
    return new co({
      ...this._def,
      checks: [...this._def.checks, { kind: "toLowerCase" }]
    });
  }
  toUpperCase() {
    return new co({
      ...this._def,
      checks: [...this._def.checks, { kind: "toUpperCase" }]
    });
  }
  get isDatetime() {
    return !!this._def.checks.find((c) => c.kind === "datetime");
  }
  get isDate() {
    return !!this._def.checks.find((c) => c.kind === "date");
  }
  get isTime() {
    return !!this._def.checks.find((c) => c.kind === "time");
  }
  get isDuration() {
    return !!this._def.checks.find((c) => c.kind === "duration");
  }
  get isEmail() {
    return !!this._def.checks.find((c) => c.kind === "email");
  }
  get isURL() {
    return !!this._def.checks.find((c) => c.kind === "url");
  }
  get isEmoji() {
    return !!this._def.checks.find((c) => c.kind === "emoji");
  }
  get isUUID() {
    return !!this._def.checks.find((c) => c.kind === "uuid");
  }
  get isNANOID() {
    return !!this._def.checks.find((c) => c.kind === "nanoid");
  }
  get isCUID() {
    return !!this._def.checks.find((c) => c.kind === "cuid");
  }
  get isCUID2() {
    return !!this._def.checks.find((c) => c.kind === "cuid2");
  }
  get isULID() {
    return !!this._def.checks.find((c) => c.kind === "ulid");
  }
  get isIP() {
    return !!this._def.checks.find((c) => c.kind === "ip");
  }
  get isCIDR() {
    return !!this._def.checks.find((c) => c.kind === "cidr");
  }
  get isBase64() {
    return !!this._def.checks.find((c) => c.kind === "base64");
  }
  get isBase64url() {
    return !!this._def.checks.find((c) => c.kind === "base64url");
  }
  get minLength() {
    let c = null;
    for (const p of this._def.checks)
      p.kind === "min" && (c === null || p.value > c) && (c = p.value);
    return c;
  }
  get maxLength() {
    let c = null;
    for (const p of this._def.checks)
      p.kind === "max" && (c === null || p.value < c) && (c = p.value);
    return c;
  }
}
co.create = (h) => new co({
  checks: [],
  typeName: tt.ZodString,
  coerce: (h == null ? void 0 : h.coerce) ?? !1,
  ...gt(h)
});
function sO(h, c) {
  const p = (h.toString().split(".")[1] || "").length, S = (c.toString().split(".")[1] || "").length, _ = p > S ? p : S, T = Number.parseInt(h.toFixed(_).replace(".", "")), E = Number.parseInt(c.toFixed(_).replace(".", ""));
  return T % E / 10 ** _;
}
class Cd extends Mt {
  constructor() {
    super(...arguments), this.min = this.gte, this.max = this.lte, this.step = this.multipleOf;
  }
  _parse(c) {
    if (this._def.coerce && (c.data = Number(c.data)), this._getType(c) !== xe.number) {
      const T = this._getOrReturnCtx(c);
      return ye(T, {
        code: ae.invalid_type,
        expected: xe.number,
        received: T.parsedType
      }), et;
    }
    let S;
    const _ = new Aa();
    for (const T of this._def.checks)
      T.kind === "int" ? Qt.isInteger(c.data) || (S = this._getOrReturnCtx(c, S), ye(S, {
        code: ae.invalid_type,
        expected: "integer",
        received: "float",
        message: T.message
      }), _.dirty()) : T.kind === "min" ? (T.inclusive ? c.data < T.value : c.data <= T.value) && (S = this._getOrReturnCtx(c, S), ye(S, {
        code: ae.too_small,
        minimum: T.value,
        type: "number",
        inclusive: T.inclusive,
        exact: !1,
        message: T.message
      }), _.dirty()) : T.kind === "max" ? (T.inclusive ? c.data > T.value : c.data >= T.value) && (S = this._getOrReturnCtx(c, S), ye(S, {
        code: ae.too_big,
        maximum: T.value,
        type: "number",
        inclusive: T.inclusive,
        exact: !1,
        message: T.message
      }), _.dirty()) : T.kind === "multipleOf" ? sO(c.data, T.value) !== 0 && (S = this._getOrReturnCtx(c, S), ye(S, {
        code: ae.not_multiple_of,
        multipleOf: T.value,
        message: T.message
      }), _.dirty()) : T.kind === "finite" ? Number.isFinite(c.data) || (S = this._getOrReturnCtx(c, S), ye(S, {
        code: ae.not_finite,
        message: T.message
      }), _.dirty()) : Qt.assertNever(T);
    return { status: _.value, value: c.data };
  }
  gte(c, p) {
    return this.setLimit("min", c, !0, Ae.toString(p));
  }
  gt(c, p) {
    return this.setLimit("min", c, !1, Ae.toString(p));
  }
  lte(c, p) {
    return this.setLimit("max", c, !0, Ae.toString(p));
  }
  lt(c, p) {
    return this.setLimit("max", c, !1, Ae.toString(p));
  }
  setLimit(c, p, S, _) {
    return new Cd({
      ...this._def,
      checks: [
        ...this._def.checks,
        {
          kind: c,
          value: p,
          inclusive: S,
          message: Ae.toString(_)
        }
      ]
    });
  }
  _addCheck(c) {
    return new Cd({
      ...this._def,
      checks: [...this._def.checks, c]
    });
  }
  int(c) {
    return this._addCheck({
      kind: "int",
      message: Ae.toString(c)
    });
  }
  positive(c) {
    return this._addCheck({
      kind: "min",
      value: 0,
      inclusive: !1,
      message: Ae.toString(c)
    });
  }
  negative(c) {
    return this._addCheck({
      kind: "max",
      value: 0,
      inclusive: !1,
      message: Ae.toString(c)
    });
  }
  nonpositive(c) {
    return this._addCheck({
      kind: "max",
      value: 0,
      inclusive: !0,
      message: Ae.toString(c)
    });
  }
  nonnegative(c) {
    return this._addCheck({
      kind: "min",
      value: 0,
      inclusive: !0,
      message: Ae.toString(c)
    });
  }
  multipleOf(c, p) {
    return this._addCheck({
      kind: "multipleOf",
      value: c,
      message: Ae.toString(p)
    });
  }
  finite(c) {
    return this._addCheck({
      kind: "finite",
      message: Ae.toString(c)
    });
  }
  safe(c) {
    return this._addCheck({
      kind: "min",
      inclusive: !0,
      value: Number.MIN_SAFE_INTEGER,
      message: Ae.toString(c)
    })._addCheck({
      kind: "max",
      inclusive: !0,
      value: Number.MAX_SAFE_INTEGER,
      message: Ae.toString(c)
    });
  }
  get minValue() {
    let c = null;
    for (const p of this._def.checks)
      p.kind === "min" && (c === null || p.value > c) && (c = p.value);
    return c;
  }
  get maxValue() {
    let c = null;
    for (const p of this._def.checks)
      p.kind === "max" && (c === null || p.value < c) && (c = p.value);
    return c;
  }
  get isInt() {
    return !!this._def.checks.find((c) => c.kind === "int" || c.kind === "multipleOf" && Qt.isInteger(c.value));
  }
  get isFinite() {
    let c = null, p = null;
    for (const S of this._def.checks) {
      if (S.kind === "finite" || S.kind === "int" || S.kind === "multipleOf")
        return !0;
      S.kind === "min" ? (p === null || S.value > p) && (p = S.value) : S.kind === "max" && (c === null || S.value < c) && (c = S.value);
    }
    return Number.isFinite(p) && Number.isFinite(c);
  }
}
Cd.create = (h) => new Cd({
  checks: [],
  typeName: tt.ZodNumber,
  coerce: (h == null ? void 0 : h.coerce) || !1,
  ...gt(h)
});
class zv extends Mt {
  constructor() {
    super(...arguments), this.min = this.gte, this.max = this.lte;
  }
  _parse(c) {
    if (this._def.coerce)
      try {
        c.data = BigInt(c.data);
      } catch {
        return this._getInvalidInput(c);
      }
    if (this._getType(c) !== xe.bigint)
      return this._getInvalidInput(c);
    let S;
    const _ = new Aa();
    for (const T of this._def.checks)
      T.kind === "min" ? (T.inclusive ? c.data < T.value : c.data <= T.value) && (S = this._getOrReturnCtx(c, S), ye(S, {
        code: ae.too_small,
        type: "bigint",
        minimum: T.value,
        inclusive: T.inclusive,
        message: T.message
      }), _.dirty()) : T.kind === "max" ? (T.inclusive ? c.data > T.value : c.data >= T.value) && (S = this._getOrReturnCtx(c, S), ye(S, {
        code: ae.too_big,
        type: "bigint",
        maximum: T.value,
        inclusive: T.inclusive,
        message: T.message
      }), _.dirty()) : T.kind === "multipleOf" ? c.data % T.value !== BigInt(0) && (S = this._getOrReturnCtx(c, S), ye(S, {
        code: ae.not_multiple_of,
        multipleOf: T.value,
        message: T.message
      }), _.dirty()) : Qt.assertNever(T);
    return { status: _.value, value: c.data };
  }
  _getInvalidInput(c) {
    const p = this._getOrReturnCtx(c);
    return ye(p, {
      code: ae.invalid_type,
      expected: xe.bigint,
      received: p.parsedType
    }), et;
  }
  gte(c, p) {
    return this.setLimit("min", c, !0, Ae.toString(p));
  }
  gt(c, p) {
    return this.setLimit("min", c, !1, Ae.toString(p));
  }
  lte(c, p) {
    return this.setLimit("max", c, !0, Ae.toString(p));
  }
  lt(c, p) {
    return this.setLimit("max", c, !1, Ae.toString(p));
  }
  setLimit(c, p, S, _) {
    return new zv({
      ...this._def,
      checks: [
        ...this._def.checks,
        {
          kind: c,
          value: p,
          inclusive: S,
          message: Ae.toString(_)
        }
      ]
    });
  }
  _addCheck(c) {
    return new zv({
      ...this._def,
      checks: [...this._def.checks, c]
    });
  }
  positive(c) {
    return this._addCheck({
      kind: "min",
      value: BigInt(0),
      inclusive: !1,
      message: Ae.toString(c)
    });
  }
  negative(c) {
    return this._addCheck({
      kind: "max",
      value: BigInt(0),
      inclusive: !1,
      message: Ae.toString(c)
    });
  }
  nonpositive(c) {
    return this._addCheck({
      kind: "max",
      value: BigInt(0),
      inclusive: !0,
      message: Ae.toString(c)
    });
  }
  nonnegative(c) {
    return this._addCheck({
      kind: "min",
      value: BigInt(0),
      inclusive: !0,
      message: Ae.toString(c)
    });
  }
  multipleOf(c, p) {
    return this._addCheck({
      kind: "multipleOf",
      value: c,
      message: Ae.toString(p)
    });
  }
  get minValue() {
    let c = null;
    for (const p of this._def.checks)
      p.kind === "min" && (c === null || p.value > c) && (c = p.value);
    return c;
  }
  get maxValue() {
    let c = null;
    for (const p of this._def.checks)
      p.kind === "max" && (c === null || p.value < c) && (c = p.value);
    return c;
  }
}
zv.create = (h) => new zv({
  checks: [],
  typeName: tt.ZodBigInt,
  coerce: (h == null ? void 0 : h.coerce) ?? !1,
  ...gt(h)
});
class mC extends Mt {
  _parse(c) {
    if (this._def.coerce && (c.data = !!c.data), this._getType(c) !== xe.boolean) {
      const S = this._getOrReturnCtx(c);
      return ye(S, {
        code: ae.invalid_type,
        expected: xe.boolean,
        received: S.parsedType
      }), et;
    }
    return ni(c.data);
  }
}
mC.create = (h) => new mC({
  typeName: tt.ZodBoolean,
  coerce: (h == null ? void 0 : h.coerce) || !1,
  ...gt(h)
});
class Ay extends Mt {
  _parse(c) {
    if (this._def.coerce && (c.data = new Date(c.data)), this._getType(c) !== xe.date) {
      const T = this._getOrReturnCtx(c);
      return ye(T, {
        code: ae.invalid_type,
        expected: xe.date,
        received: T.parsedType
      }), et;
    }
    if (Number.isNaN(c.data.getTime())) {
      const T = this._getOrReturnCtx(c);
      return ye(T, {
        code: ae.invalid_date
      }), et;
    }
    const S = new Aa();
    let _;
    for (const T of this._def.checks)
      T.kind === "min" ? c.data.getTime() < T.value && (_ = this._getOrReturnCtx(c, _), ye(_, {
        code: ae.too_small,
        message: T.message,
        inclusive: !0,
        exact: !1,
        minimum: T.value,
        type: "date"
      }), S.dirty()) : T.kind === "max" ? c.data.getTime() > T.value && (_ = this._getOrReturnCtx(c, _), ye(_, {
        code: ae.too_big,
        message: T.message,
        inclusive: !0,
        exact: !1,
        maximum: T.value,
        type: "date"
      }), S.dirty()) : Qt.assertNever(T);
    return {
      status: S.value,
      value: new Date(c.data.getTime())
    };
  }
  _addCheck(c) {
    return new Ay({
      ...this._def,
      checks: [...this._def.checks, c]
    });
  }
  min(c, p) {
    return this._addCheck({
      kind: "min",
      value: c.getTime(),
      message: Ae.toString(p)
    });
  }
  max(c, p) {
    return this._addCheck({
      kind: "max",
      value: c.getTime(),
      message: Ae.toString(p)
    });
  }
  get minDate() {
    let c = null;
    for (const p of this._def.checks)
      p.kind === "min" && (c === null || p.value > c) && (c = p.value);
    return c != null ? new Date(c) : null;
  }
  get maxDate() {
    let c = null;
    for (const p of this._def.checks)
      p.kind === "max" && (c === null || p.value < c) && (c = p.value);
    return c != null ? new Date(c) : null;
  }
}
Ay.create = (h) => new Ay({
  checks: [],
  coerce: (h == null ? void 0 : h.coerce) || !1,
  typeName: tt.ZodDate,
  ...gt(h)
});
class TT extends Mt {
  _parse(c) {
    if (this._getType(c) !== xe.symbol) {
      const S = this._getOrReturnCtx(c);
      return ye(S, {
        code: ae.invalid_type,
        expected: xe.symbol,
        received: S.parsedType
      }), et;
    }
    return ni(c.data);
  }
}
TT.create = (h) => new TT({
  typeName: tt.ZodSymbol,
  ...gt(h)
});
class yC extends Mt {
  _parse(c) {
    if (this._getType(c) !== xe.undefined) {
      const S = this._getOrReturnCtx(c);
      return ye(S, {
        code: ae.invalid_type,
        expected: xe.undefined,
        received: S.parsedType
      }), et;
    }
    return ni(c.data);
  }
}
yC.create = (h) => new yC({
  typeName: tt.ZodUndefined,
  ...gt(h)
});
class gC extends Mt {
  _parse(c) {
    if (this._getType(c) !== xe.null) {
      const S = this._getOrReturnCtx(c);
      return ye(S, {
        code: ae.invalid_type,
        expected: xe.null,
        received: S.parsedType
      }), et;
    }
    return ni(c.data);
  }
}
gC.create = (h) => new gC({
  typeName: tt.ZodNull,
  ...gt(h)
});
class RT extends Mt {
  constructor() {
    super(...arguments), this._any = !0;
  }
  _parse(c) {
    return ni(c.data);
  }
}
RT.create = (h) => new RT({
  typeName: tt.ZodAny,
  ...gt(h)
});
class Sd extends Mt {
  constructor() {
    super(...arguments), this._unknown = !0;
  }
  _parse(c) {
    return ni(c.data);
  }
}
Sd.create = (h) => new Sd({
  typeName: tt.ZodUnknown,
  ...gt(h)
});
class ds extends Mt {
  _parse(c) {
    const p = this._getOrReturnCtx(c);
    return ye(p, {
      code: ae.invalid_type,
      expected: xe.never,
      received: p.parsedType
    }), et;
  }
}
ds.create = (h) => new ds({
  typeName: tt.ZodNever,
  ...gt(h)
});
class SC extends Mt {
  _parse(c) {
    if (this._getType(c) !== xe.undefined) {
      const S = this._getOrReturnCtx(c);
      return ye(S, {
        code: ae.invalid_type,
        expected: xe.void,
        received: S.parsedType
      }), et;
    }
    return ni(c.data);
  }
}
SC.create = (h) => new SC({
  typeName: tt.ZodVoid,
  ...gt(h)
});
class du extends Mt {
  _parse(c) {
    const { ctx: p, status: S } = this._processInputParams(c), _ = this._def;
    if (p.parsedType !== xe.array)
      return ye(p, {
        code: ae.invalid_type,
        expected: xe.array,
        received: p.parsedType
      }), et;
    if (_.exactLength !== null) {
      const E = p.data.length > _.exactLength.value, A = p.data.length < _.exactLength.value;
      (E || A) && (ye(p, {
        code: E ? ae.too_big : ae.too_small,
        minimum: A ? _.exactLength.value : void 0,
        maximum: E ? _.exactLength.value : void 0,
        type: "array",
        inclusive: !0,
        exact: !0,
        message: _.exactLength.message
      }), S.dirty());
    }
    if (_.minLength !== null && p.data.length < _.minLength.value && (ye(p, {
      code: ae.too_small,
      minimum: _.minLength.value,
      type: "array",
      inclusive: !0,
      exact: !1,
      message: _.minLength.message
    }), S.dirty()), _.maxLength !== null && p.data.length > _.maxLength.value && (ye(p, {
      code: ae.too_big,
      maximum: _.maxLength.value,
      type: "array",
      inclusive: !0,
      exact: !1,
      message: _.maxLength.message
    }), S.dirty()), p.common.async)
      return Promise.all([...p.data].map((E, A) => _.type._parseAsync(new pu(p, E, p.path, A)))).then((E) => Aa.mergeArray(S, E));
    const T = [...p.data].map((E, A) => _.type._parseSync(new pu(p, E, p.path, A)));
    return Aa.mergeArray(S, T);
  }
  get element() {
    return this._def.type;
  }
  min(c, p) {
    return new du({
      ...this._def,
      minLength: { value: c, message: Ae.toString(p) }
    });
  }
  max(c, p) {
    return new du({
      ...this._def,
      maxLength: { value: c, message: Ae.toString(p) }
    });
  }
  length(c, p) {
    return new du({
      ...this._def,
      exactLength: { value: c, message: Ae.toString(p) }
    });
  }
  nonempty(c) {
    return this.min(1, c);
  }
}
du.create = (h, c) => new du({
  type: h,
  minLength: null,
  maxLength: null,
  exactLength: null,
  typeName: tt.ZodArray,
  ...gt(c)
});
function gd(h) {
  if (h instanceof dr) {
    const c = {};
    for (const p in h.shape) {
      const S = h.shape[p];
      c[p] = fo.create(gd(S));
    }
    return new dr({
      ...h._def,
      shape: () => c
    });
  } else return h instanceof du ? new du({
    ...h._def,
    type: gd(h.element)
  }) : h instanceof fo ? fo.create(gd(h.unwrap())) : h instanceof Nc ? Nc.create(gd(h.unwrap())) : h instanceof po ? po.create(h.items.map((c) => gd(c))) : h;
}
class dr extends Mt {
  constructor() {
    super(...arguments), this._cached = null, this.nonstrict = this.passthrough, this.augment = this.extend;
  }
  _getCached() {
    if (this._cached !== null)
      return this._cached;
    const c = this._def.shape(), p = Qt.objectKeys(c);
    return this._cached = { shape: c, keys: p }, this._cached;
  }
  _parse(c) {
    if (this._getType(c) !== xe.object) {
      const $ = this._getOrReturnCtx(c);
      return ye($, {
        code: ae.invalid_type,
        expected: xe.object,
        received: $.parsedType
      }), et;
    }
    const { status: S, ctx: _ } = this._processInputParams(c), { shape: T, keys: E } = this._getCached(), A = [];
    if (!(this._def.catchall instanceof ds && this._def.unknownKeys === "strip"))
      for (const $ in _.data)
        E.includes($) || A.push($);
    const I = [];
    for (const $ of E) {
      const fe = T[$], re = _.data[$];
      I.push({
        key: { status: "valid", value: $ },
        value: fe._parse(new pu(_, re, _.path, $)),
        alwaysSet: $ in _.data
      });
    }
    if (this._def.catchall instanceof ds) {
      const $ = this._def.unknownKeys;
      if ($ === "passthrough")
        for (const fe of A)
          I.push({
            key: { status: "valid", value: fe },
            value: { status: "valid", value: _.data[fe] }
          });
      else if ($ === "strict")
        A.length > 0 && (ye(_, {
          code: ae.unrecognized_keys,
          keys: A
        }), S.dirty());
      else if ($ !== "strip") throw new Error("Internal ZodObject error: invalid unknownKeys value.");
    } else {
      const $ = this._def.catchall;
      for (const fe of A) {
        const re = _.data[fe];
        I.push({
          key: { status: "valid", value: fe },
          value: $._parse(
            new pu(_, re, _.path, fe)
            //, ctx.child(key), value, getParsedType(value)
          ),
          alwaysSet: fe in _.data
        });
      }
    }
    return _.common.async ? Promise.resolve().then(async () => {
      const $ = [];
      for (const fe of I) {
        const re = await fe.key, be = await fe.value;
        $.push({
          key: re,
          value: be,
          alwaysSet: fe.alwaysSet
        });
      }
      return $;
    }).then(($) => Aa.mergeObjectSync(S, $)) : Aa.mergeObjectSync(S, I);
  }
  get shape() {
    return this._def.shape();
  }
  strict(c) {
    return Ae.errToObj, new dr({
      ...this._def,
      unknownKeys: "strict",
      ...c !== void 0 ? {
        errorMap: (p, S) => {
          var T, E;
          const _ = ((E = (T = this._def).errorMap) == null ? void 0 : E.call(T, p, S).message) ?? S.defaultError;
          return p.code === "unrecognized_keys" ? {
            message: Ae.errToObj(c).message ?? _
          } : {
            message: _
          };
        }
      } : {}
    });
  }
  strip() {
    return new dr({
      ...this._def,
      unknownKeys: "strip"
    });
  }
  passthrough() {
    return new dr({
      ...this._def,
      unknownKeys: "passthrough"
    });
  }
  // const AugmentFactory =
  //   <Def extends ZodObjectDef>(def: Def) =>
  //   <Augmentation extends ZodRawShape>(
  //     augmentation: Augmentation
  //   ): ZodObject<
  //     extendShape<ReturnType<Def["shape"]>, Augmentation>,
  //     Def["unknownKeys"],
  //     Def["catchall"]
  //   > => {
  //     return new ZodObject({
  //       ...def,
  //       shape: () => ({
  //         ...def.shape(),
  //         ...augmentation,
  //       }),
  //     }) as any;
  //   };
  extend(c) {
    return new dr({
      ...this._def,
      shape: () => ({
        ...this._def.shape(),
        ...c
      })
    });
  }
  /**
   * Prior to zod@1.0.12 there was a bug in the
   * inferred type of merged objects. Please
   * upgrade if you are experiencing issues.
   */
  merge(c) {
    return new dr({
      unknownKeys: c._def.unknownKeys,
      catchall: c._def.catchall,
      shape: () => ({
        ...this._def.shape(),
        ...c._def.shape()
      }),
      typeName: tt.ZodObject
    });
  }
  // merge<
  //   Incoming extends AnyZodObject,
  //   Augmentation extends Incoming["shape"],
  //   NewOutput extends {
  //     [k in keyof Augmentation | keyof Output]: k extends keyof Augmentation
  //       ? Augmentation[k]["_output"]
  //       : k extends keyof Output
  //       ? Output[k]
  //       : never;
  //   },
  //   NewInput extends {
  //     [k in keyof Augmentation | keyof Input]: k extends keyof Augmentation
  //       ? Augmentation[k]["_input"]
  //       : k extends keyof Input
  //       ? Input[k]
  //       : never;
  //   }
  // >(
  //   merging: Incoming
  // ): ZodObject<
  //   extendShape<T, ReturnType<Incoming["_def"]["shape"]>>,
  //   Incoming["_def"]["unknownKeys"],
  //   Incoming["_def"]["catchall"],
  //   NewOutput,
  //   NewInput
  // > {
  //   const merged: any = new ZodObject({
  //     unknownKeys: merging._def.unknownKeys,
  //     catchall: merging._def.catchall,
  //     shape: () =>
  //       objectUtil.mergeShapes(this._def.shape(), merging._def.shape()),
  //     typeName: ZodFirstPartyTypeKind.ZodObject,
  //   }) as any;
  //   return merged;
  // }
  setKey(c, p) {
    return this.augment({ [c]: p });
  }
  // merge<Incoming extends AnyZodObject>(
  //   merging: Incoming
  // ): //ZodObject<T & Incoming["_shape"], UnknownKeys, Catchall> = (merging) => {
  // ZodObject<
  //   extendShape<T, ReturnType<Incoming["_def"]["shape"]>>,
  //   Incoming["_def"]["unknownKeys"],
  //   Incoming["_def"]["catchall"]
  // > {
  //   // const mergedShape = objectUtil.mergeShapes(
  //   //   this._def.shape(),
  //   //   merging._def.shape()
  //   // );
  //   const merged: any = new ZodObject({
  //     unknownKeys: merging._def.unknownKeys,
  //     catchall: merging._def.catchall,
  //     shape: () =>
  //       objectUtil.mergeShapes(this._def.shape(), merging._def.shape()),
  //     typeName: ZodFirstPartyTypeKind.ZodObject,
  //   }) as any;
  //   return merged;
  // }
  catchall(c) {
    return new dr({
      ...this._def,
      catchall: c
    });
  }
  pick(c) {
    const p = {};
    for (const S of Qt.objectKeys(c))
      c[S] && this.shape[S] && (p[S] = this.shape[S]);
    return new dr({
      ...this._def,
      shape: () => p
    });
  }
  omit(c) {
    const p = {};
    for (const S of Qt.objectKeys(this.shape))
      c[S] || (p[S] = this.shape[S]);
    return new dr({
      ...this._def,
      shape: () => p
    });
  }
  /**
   * @deprecated
   */
  deepPartial() {
    return gd(this);
  }
  partial(c) {
    const p = {};
    for (const S of Qt.objectKeys(this.shape)) {
      const _ = this.shape[S];
      c && !c[S] ? p[S] = _ : p[S] = _.optional();
    }
    return new dr({
      ...this._def,
      shape: () => p
    });
  }
  required(c) {
    const p = {};
    for (const S of Qt.objectKeys(this.shape))
      if (c && !c[S])
        p[S] = this.shape[S];
      else {
        let T = this.shape[S];
        for (; T instanceof fo; )
          T = T._def.innerType;
        p[S] = T;
      }
    return new dr({
      ...this._def,
      shape: () => p
    });
  }
  keyof() {
    return AT(Qt.objectKeys(this.shape));
  }
}
dr.create = (h, c) => new dr({
  shape: () => h,
  unknownKeys: "strip",
  catchall: ds.create(),
  typeName: tt.ZodObject,
  ...gt(c)
});
dr.strictCreate = (h, c) => new dr({
  shape: () => h,
  unknownKeys: "strict",
  catchall: ds.create(),
  typeName: tt.ZodObject,
  ...gt(c)
});
dr.lazycreate = (h, c) => new dr({
  shape: h,
  unknownKeys: "strip",
  catchall: ds.create(),
  typeName: tt.ZodObject,
  ...gt(c)
});
class zy extends Mt {
  _parse(c) {
    const { ctx: p } = this._processInputParams(c), S = this._def.options;
    function _(T) {
      for (const A of T)
        if (A.result.status === "valid")
          return A.result;
      for (const A of T)
        if (A.result.status === "dirty")
          return p.common.issues.push(...A.ctx.common.issues), A.result;
      const E = T.map((A) => new Xi(A.ctx.common.issues));
      return ye(p, {
        code: ae.invalid_union,
        unionErrors: E
      }), et;
    }
    if (p.common.async)
      return Promise.all(S.map(async (T) => {
        const E = {
          ...p,
          common: {
            ...p.common,
            issues: []
          },
          parent: null
        };
        return {
          result: await T._parseAsync({
            data: p.data,
            path: p.path,
            parent: E
          }),
          ctx: E
        };
      })).then(_);
    {
      let T;
      const E = [];
      for (const I of S) {
        const $ = {
          ...p,
          common: {
            ...p.common,
            issues: []
          },
          parent: null
        }, fe = I._parseSync({
          data: p.data,
          path: p.path,
          parent: $
        });
        if (fe.status === "valid")
          return fe;
        fe.status === "dirty" && !T && (T = { result: fe, ctx: $ }), $.common.issues.length && E.push($.common.issues);
      }
      if (T)
        return p.common.issues.push(...T.ctx.common.issues), T.result;
      const A = E.map((I) => new Xi(I));
      return ye(p, {
        code: ae.invalid_union,
        unionErrors: A
      }), et;
    }
  }
  get options() {
    return this._def.options;
  }
}
zy.create = (h, c) => new zy({
  options: h,
  typeName: tt.ZodUnion,
  ...gt(c)
});
const so = (h) => h instanceof CC ? so(h.schema) : h instanceof Oc ? so(h.innerType()) : h instanceof Fy ? [h.value] : h instanceof Dc ? h.options : h instanceof _C ? Qt.objectValues(h.enum) : h instanceof Hy ? so(h._def.innerType) : h instanceof yC ? [void 0] : h instanceof gC ? [null] : h instanceof fo ? [void 0, ...so(h.unwrap())] : h instanceof Nc ? [null, ...so(h.unwrap())] : h instanceof zT || h instanceof Py ? so(h.unwrap()) : h instanceof Vy ? so(h._def.innerType) : [];
class TC extends Mt {
  _parse(c) {
    const { ctx: p } = this._processInputParams(c);
    if (p.parsedType !== xe.object)
      return ye(p, {
        code: ae.invalid_type,
        expected: xe.object,
        received: p.parsedType
      }), et;
    const S = this.discriminator, _ = p.data[S], T = this.optionsMap.get(_);
    return T ? p.common.async ? T._parseAsync({
      data: p.data,
      path: p.path,
      parent: p
    }) : T._parseSync({
      data: p.data,
      path: p.path,
      parent: p
    }) : (ye(p, {
      code: ae.invalid_union_discriminator,
      options: Array.from(this.optionsMap.keys()),
      path: [S]
    }), et);
  }
  get discriminator() {
    return this._def.discriminator;
  }
  get options() {
    return this._def.options;
  }
  get optionsMap() {
    return this._def.optionsMap;
  }
  /**
   * The constructor of the discriminated union schema. Its behaviour is very similar to that of the normal z.union() constructor.
   * However, it only allows a union of objects, all of which need to share a discriminator property. This property must
   * have a different value for each object in the union.
   * @param discriminator the name of the discriminator property
   * @param types an array of object schemas
   * @param params
   */
  static create(c, p, S) {
    const _ = /* @__PURE__ */ new Map();
    for (const T of p) {
      const E = so(T.shape[c]);
      if (!E.length)
        throw new Error(`A discriminator value for key \`${c}\` could not be extracted from all schema options`);
      for (const A of E) {
        if (_.has(A))
          throw new Error(`Discriminator property ${String(c)} has duplicate value ${String(A)}`);
        _.set(A, T);
      }
    }
    return new TC({
      typeName: tt.ZodDiscriminatedUnion,
      discriminator: c,
      options: p,
      optionsMap: _,
      ...gt(S)
    });
  }
}
function EC(h, c) {
  const p = fs(h), S = fs(c);
  if (h === c)
    return { valid: !0, data: h };
  if (p === xe.object && S === xe.object) {
    const _ = Qt.objectKeys(c), T = Qt.objectKeys(h).filter((A) => _.indexOf(A) !== -1), E = { ...h, ...c };
    for (const A of T) {
      const I = EC(h[A], c[A]);
      if (!I.valid)
        return { valid: !1 };
      E[A] = I.data;
    }
    return { valid: !0, data: E };
  } else if (p === xe.array && S === xe.array) {
    if (h.length !== c.length)
      return { valid: !1 };
    const _ = [];
    for (let T = 0; T < h.length; T++) {
      const E = h[T], A = c[T], I = EC(E, A);
      if (!I.valid)
        return { valid: !1 };
      _.push(I.data);
    }
    return { valid: !0, data: _ };
  } else return p === xe.date && S === xe.date && +h == +c ? { valid: !0, data: h } : { valid: !1 };
}
class Uy extends Mt {
  _parse(c) {
    const { status: p, ctx: S } = this._processInputParams(c), _ = (T, E) => {
      if (CT(T) || CT(E))
        return et;
      const A = EC(T.value, E.value);
      return A.valid ? ((_T(T) || _T(E)) && p.dirty(), { status: p.value, value: A.data }) : (ye(S, {
        code: ae.invalid_intersection_types
      }), et);
    };
    return S.common.async ? Promise.all([
      this._def.left._parseAsync({
        data: S.data,
        path: S.path,
        parent: S
      }),
      this._def.right._parseAsync({
        data: S.data,
        path: S.path,
        parent: S
      })
    ]).then(([T, E]) => _(T, E)) : _(this._def.left._parseSync({
      data: S.data,
      path: S.path,
      parent: S
    }), this._def.right._parseSync({
      data: S.data,
      path: S.path,
      parent: S
    }));
  }
}
Uy.create = (h, c, p) => new Uy({
  left: h,
  right: c,
  typeName: tt.ZodIntersection,
  ...gt(p)
});
class po extends Mt {
  _parse(c) {
    const { status: p, ctx: S } = this._processInputParams(c);
    if (S.parsedType !== xe.array)
      return ye(S, {
        code: ae.invalid_type,
        expected: xe.array,
        received: S.parsedType
      }), et;
    if (S.data.length < this._def.items.length)
      return ye(S, {
        code: ae.too_small,
        minimum: this._def.items.length,
        inclusive: !0,
        exact: !1,
        type: "array"
      }), et;
    !this._def.rest && S.data.length > this._def.items.length && (ye(S, {
      code: ae.too_big,
      maximum: this._def.items.length,
      inclusive: !0,
      exact: !1,
      type: "array"
    }), p.dirty());
    const T = [...S.data].map((E, A) => {
      const I = this._def.items[A] || this._def.rest;
      return I ? I._parse(new pu(S, E, S.path, A)) : null;
    }).filter((E) => !!E);
    return S.common.async ? Promise.all(T).then((E) => Aa.mergeArray(p, E)) : Aa.mergeArray(p, T);
  }
  get items() {
    return this._def.items;
  }
  rest(c) {
    return new po({
      ...this._def,
      rest: c
    });
  }
}
po.create = (h, c) => {
  if (!Array.isArray(h))
    throw new Error("You must pass an array of schemas to z.tuple([ ... ])");
  return new po({
    items: h,
    typeName: tt.ZodTuple,
    rest: null,
    ...gt(c)
  });
};
class jy extends Mt {
  get keySchema() {
    return this._def.keyType;
  }
  get valueSchema() {
    return this._def.valueType;
  }
  _parse(c) {
    const { status: p, ctx: S } = this._processInputParams(c);
    if (S.parsedType !== xe.object)
      return ye(S, {
        code: ae.invalid_type,
        expected: xe.object,
        received: S.parsedType
      }), et;
    const _ = [], T = this._def.keyType, E = this._def.valueType;
    for (const A in S.data)
      _.push({
        key: T._parse(new pu(S, A, S.path, A)),
        value: E._parse(new pu(S, S.data[A], S.path, A)),
        alwaysSet: A in S.data
      });
    return S.common.async ? Aa.mergeObjectAsync(p, _) : Aa.mergeObjectSync(p, _);
  }
  get element() {
    return this._def.valueType;
  }
  static create(c, p, S) {
    return p instanceof Mt ? new jy({
      keyType: c,
      valueType: p,
      typeName: tt.ZodRecord,
      ...gt(S)
    }) : new jy({
      keyType: co.create(),
      valueType: c,
      typeName: tt.ZodRecord,
      ...gt(p)
    });
  }
}
class wT extends Mt {
  get keySchema() {
    return this._def.keyType;
  }
  get valueSchema() {
    return this._def.valueType;
  }
  _parse(c) {
    const { status: p, ctx: S } = this._processInputParams(c);
    if (S.parsedType !== xe.map)
      return ye(S, {
        code: ae.invalid_type,
        expected: xe.map,
        received: S.parsedType
      }), et;
    const _ = this._def.keyType, T = this._def.valueType, E = [...S.data.entries()].map(([A, I], $) => ({
      key: _._parse(new pu(S, A, S.path, [$, "key"])),
      value: T._parse(new pu(S, I, S.path, [$, "value"]))
    }));
    if (S.common.async) {
      const A = /* @__PURE__ */ new Map();
      return Promise.resolve().then(async () => {
        for (const I of E) {
          const $ = await I.key, fe = await I.value;
          if ($.status === "aborted" || fe.status === "aborted")
            return et;
          ($.status === "dirty" || fe.status === "dirty") && p.dirty(), A.set($.value, fe.value);
        }
        return { status: p.value, value: A };
      });
    } else {
      const A = /* @__PURE__ */ new Map();
      for (const I of E) {
        const $ = I.key, fe = I.value;
        if ($.status === "aborted" || fe.status === "aborted")
          return et;
        ($.status === "dirty" || fe.status === "dirty") && p.dirty(), A.set($.value, fe.value);
      }
      return { status: p.value, value: A };
    }
  }
}
wT.create = (h, c, p) => new wT({
  valueType: c,
  keyType: h,
  typeName: tt.ZodMap,
  ...gt(p)
});
class Uv extends Mt {
  _parse(c) {
    const { status: p, ctx: S } = this._processInputParams(c);
    if (S.parsedType !== xe.set)
      return ye(S, {
        code: ae.invalid_type,
        expected: xe.set,
        received: S.parsedType
      }), et;
    const _ = this._def;
    _.minSize !== null && S.data.size < _.minSize.value && (ye(S, {
      code: ae.too_small,
      minimum: _.minSize.value,
      type: "set",
      inclusive: !0,
      exact: !1,
      message: _.minSize.message
    }), p.dirty()), _.maxSize !== null && S.data.size > _.maxSize.value && (ye(S, {
      code: ae.too_big,
      maximum: _.maxSize.value,
      type: "set",
      inclusive: !0,
      exact: !1,
      message: _.maxSize.message
    }), p.dirty());
    const T = this._def.valueType;
    function E(I) {
      const $ = /* @__PURE__ */ new Set();
      for (const fe of I) {
        if (fe.status === "aborted")
          return et;
        fe.status === "dirty" && p.dirty(), $.add(fe.value);
      }
      return { status: p.value, value: $ };
    }
    const A = [...S.data.values()].map((I, $) => T._parse(new pu(S, I, S.path, $)));
    return S.common.async ? Promise.all(A).then((I) => E(I)) : E(A);
  }
  min(c, p) {
    return new Uv({
      ...this._def,
      minSize: { value: c, message: Ae.toString(p) }
    });
  }
  max(c, p) {
    return new Uv({
      ...this._def,
      maxSize: { value: c, message: Ae.toString(p) }
    });
  }
  size(c, p) {
    return this.min(c, p).max(c, p);
  }
  nonempty(c) {
    return this.min(1, c);
  }
}
Uv.create = (h, c) => new Uv({
  valueType: h,
  minSize: null,
  maxSize: null,
  typeName: tt.ZodSet,
  ...gt(c)
});
class Lv extends Mt {
  constructor() {
    super(...arguments), this.validate = this.implement;
  }
  _parse(c) {
    const { ctx: p } = this._processInputParams(c);
    if (p.parsedType !== xe.function)
      return ye(p, {
        code: ae.invalid_type,
        expected: xe.function,
        received: p.parsedType
      }), et;
    function S(A, I) {
      return hC({
        data: A,
        path: p.path,
        errorMaps: [p.common.contextualErrorMap, p.schemaErrorMap, vC(), Av].filter(($) => !!$),
        issueData: {
          code: ae.invalid_arguments,
          argumentsError: I
        }
      });
    }
    function _(A, I) {
      return hC({
        data: A,
        path: p.path,
        errorMaps: [p.common.contextualErrorMap, p.schemaErrorMap, vC(), Av].filter(($) => !!$),
        issueData: {
          code: ae.invalid_return_type,
          returnTypeError: I
        }
      });
    }
    const T = { errorMap: p.common.contextualErrorMap }, E = p.data;
    if (this._def.returns instanceof jv) {
      const A = this;
      return ni(async function(...I) {
        const $ = new Xi([]), fe = await A._def.args.parseAsync(I, T).catch((de) => {
          throw $.addIssue(S(I, de)), $;
        }), re = await Reflect.apply(E, this, fe);
        return await A._def.returns._def.type.parseAsync(re, T).catch((de) => {
          throw $.addIssue(_(re, de)), $;
        });
      });
    } else {
      const A = this;
      return ni(function(...I) {
        const $ = A._def.args.safeParse(I, T);
        if (!$.success)
          throw new Xi([S(I, $.error)]);
        const fe = Reflect.apply(E, this, $.data), re = A._def.returns.safeParse(fe, T);
        if (!re.success)
          throw new Xi([_(fe, re.error)]);
        return re.data;
      });
    }
  }
  parameters() {
    return this._def.args;
  }
  returnType() {
    return this._def.returns;
  }
  args(...c) {
    return new Lv({
      ...this._def,
      args: po.create(c).rest(Sd.create())
    });
  }
  returns(c) {
    return new Lv({
      ...this._def,
      returns: c
    });
  }
  implement(c) {
    return this.parse(c);
  }
  strictImplement(c) {
    return this.parse(c);
  }
  static create(c, p, S) {
    return new Lv({
      args: c || po.create([]).rest(Sd.create()),
      returns: p || Sd.create(),
      typeName: tt.ZodFunction,
      ...gt(S)
    });
  }
}
class CC extends Mt {
  get schema() {
    return this._def.getter();
  }
  _parse(c) {
    const { ctx: p } = this._processInputParams(c);
    return this._def.getter()._parse({ data: p.data, path: p.path, parent: p });
  }
}
CC.create = (h, c) => new CC({
  getter: h,
  typeName: tt.ZodLazy,
  ...gt(c)
});
class Fy extends Mt {
  _parse(c) {
    if (c.data !== this._def.value) {
      const p = this._getOrReturnCtx(c);
      return ye(p, {
        received: p.data,
        code: ae.invalid_literal,
        expected: this._def.value
      }), et;
    }
    return { status: "valid", value: c.data };
  }
  get value() {
    return this._def.value;
  }
}
Fy.create = (h, c) => new Fy({
  value: h,
  typeName: tt.ZodLiteral,
  ...gt(c)
});
function AT(h, c) {
  return new Dc({
    values: h,
    typeName: tt.ZodEnum,
    ...gt(c)
  });
}
class Dc extends Mt {
  _parse(c) {
    if (typeof c.data != "string") {
      const p = this._getOrReturnCtx(c), S = this._def.values;
      return ye(p, {
        expected: Qt.joinValues(S),
        received: p.parsedType,
        code: ae.invalid_type
      }), et;
    }
    if (this._cache || (this._cache = new Set(this._def.values)), !this._cache.has(c.data)) {
      const p = this._getOrReturnCtx(c), S = this._def.values;
      return ye(p, {
        received: p.data,
        code: ae.invalid_enum_value,
        options: S
      }), et;
    }
    return ni(c.data);
  }
  get options() {
    return this._def.values;
  }
  get enum() {
    const c = {};
    for (const p of this._def.values)
      c[p] = p;
    return c;
  }
  get Values() {
    const c = {};
    for (const p of this._def.values)
      c[p] = p;
    return c;
  }
  get Enum() {
    const c = {};
    for (const p of this._def.values)
      c[p] = p;
    return c;
  }
  extract(c, p = this._def) {
    return Dc.create(c, {
      ...this._def,
      ...p
    });
  }
  exclude(c, p = this._def) {
    return Dc.create(this.options.filter((S) => !c.includes(S)), {
      ...this._def,
      ...p
    });
  }
}
Dc.create = AT;
class _C extends Mt {
  _parse(c) {
    const p = Qt.getValidEnumValues(this._def.values), S = this._getOrReturnCtx(c);
    if (S.parsedType !== xe.string && S.parsedType !== xe.number) {
      const _ = Qt.objectValues(p);
      return ye(S, {
        expected: Qt.joinValues(_),
        received: S.parsedType,
        code: ae.invalid_type
      }), et;
    }
    if (this._cache || (this._cache = new Set(Qt.getValidEnumValues(this._def.values))), !this._cache.has(c.data)) {
      const _ = Qt.objectValues(p);
      return ye(S, {
        received: S.data,
        code: ae.invalid_enum_value,
        options: _
      }), et;
    }
    return ni(c.data);
  }
  get enum() {
    return this._def.values;
  }
}
_C.create = (h, c) => new _C({
  values: h,
  typeName: tt.ZodNativeEnum,
  ...gt(c)
});
class jv extends Mt {
  unwrap() {
    return this._def.type;
  }
  _parse(c) {
    const { ctx: p } = this._processInputParams(c);
    if (p.parsedType !== xe.promise && p.common.async === !1)
      return ye(p, {
        code: ae.invalid_type,
        expected: xe.promise,
        received: p.parsedType
      }), et;
    const S = p.parsedType === xe.promise ? p.data : Promise.resolve(p.data);
    return ni(S.then((_) => this._def.type.parseAsync(_, {
      path: p.path,
      errorMap: p.common.contextualErrorMap
    })));
  }
}
jv.create = (h, c) => new jv({
  type: h,
  typeName: tt.ZodPromise,
  ...gt(c)
});
class Oc extends Mt {
  innerType() {
    return this._def.schema;
  }
  sourceType() {
    return this._def.schema._def.typeName === tt.ZodEffects ? this._def.schema.sourceType() : this._def.schema;
  }
  _parse(c) {
    const { status: p, ctx: S } = this._processInputParams(c), _ = this._def.effect || null, T = {
      addIssue: (E) => {
        ye(S, E), E.fatal ? p.abort() : p.dirty();
      },
      get path() {
        return S.path;
      }
    };
    if (T.addIssue = T.addIssue.bind(T), _.type === "preprocess") {
      const E = _.transform(S.data, T);
      if (S.common.async)
        return Promise.resolve(E).then(async (A) => {
          if (p.value === "aborted")
            return et;
          const I = await this._def.schema._parseAsync({
            data: A,
            path: S.path,
            parent: S
          });
          return I.status === "aborted" ? et : I.status === "dirty" || p.value === "dirty" ? Mv(I.value) : I;
        });
      {
        if (p.value === "aborted")
          return et;
        const A = this._def.schema._parseSync({
          data: E,
          path: S.path,
          parent: S
        });
        return A.status === "aborted" ? et : A.status === "dirty" || p.value === "dirty" ? Mv(A.value) : A;
      }
    }
    if (_.type === "refinement") {
      const E = (A) => {
        const I = _.refinement(A, T);
        if (S.common.async)
          return Promise.resolve(I);
        if (I instanceof Promise)
          throw new Error("Async refinement encountered during synchronous parse operation. Use .parseAsync instead.");
        return A;
      };
      if (S.common.async === !1) {
        const A = this._def.schema._parseSync({
          data: S.data,
          path: S.path,
          parent: S
        });
        return A.status === "aborted" ? et : (A.status === "dirty" && p.dirty(), E(A.value), { status: p.value, value: A.value });
      } else
        return this._def.schema._parseAsync({ data: S.data, path: S.path, parent: S }).then((A) => A.status === "aborted" ? et : (A.status === "dirty" && p.dirty(), E(A.value).then(() => ({ status: p.value, value: A.value }))));
    }
    if (_.type === "transform")
      if (S.common.async === !1) {
        const E = this._def.schema._parseSync({
          data: S.data,
          path: S.path,
          parent: S
        });
        if (!Ed(E))
          return et;
        const A = _.transform(E.value, T);
        if (A instanceof Promise)
          throw new Error("Asynchronous transform encountered during synchronous parse operation. Use .parseAsync instead.");
        return { status: p.value, value: A };
      } else
        return this._def.schema._parseAsync({ data: S.data, path: S.path, parent: S }).then((E) => Ed(E) ? Promise.resolve(_.transform(E.value, T)).then((A) => ({
          status: p.value,
          value: A
        })) : et);
    Qt.assertNever(_);
  }
}
Oc.create = (h, c, p) => new Oc({
  schema: h,
  typeName: tt.ZodEffects,
  effect: c,
  ...gt(p)
});
Oc.createWithPreprocess = (h, c, p) => new Oc({
  schema: c,
  effect: { type: "preprocess", transform: h },
  typeName: tt.ZodEffects,
  ...gt(p)
});
class fo extends Mt {
  _parse(c) {
    return this._getType(c) === xe.undefined ? ni(void 0) : this._def.innerType._parse(c);
  }
  unwrap() {
    return this._def.innerType;
  }
}
fo.create = (h, c) => new fo({
  innerType: h,
  typeName: tt.ZodOptional,
  ...gt(c)
});
class Nc extends Mt {
  _parse(c) {
    return this._getType(c) === xe.null ? ni(null) : this._def.innerType._parse(c);
  }
  unwrap() {
    return this._def.innerType;
  }
}
Nc.create = (h, c) => new Nc({
  innerType: h,
  typeName: tt.ZodNullable,
  ...gt(c)
});
class Hy extends Mt {
  _parse(c) {
    const { ctx: p } = this._processInputParams(c);
    let S = p.data;
    return p.parsedType === xe.undefined && (S = this._def.defaultValue()), this._def.innerType._parse({
      data: S,
      path: p.path,
      parent: p
    });
  }
  removeDefault() {
    return this._def.innerType;
  }
}
Hy.create = (h, c) => new Hy({
  innerType: h,
  typeName: tt.ZodDefault,
  defaultValue: typeof c.default == "function" ? c.default : () => c.default,
  ...gt(c)
});
class Vy extends Mt {
  _parse(c) {
    const { ctx: p } = this._processInputParams(c), S = {
      ...p,
      common: {
        ...p.common,
        issues: []
      }
    }, _ = this._def.innerType._parse({
      data: S.data,
      path: S.path,
      parent: {
        ...S
      }
    });
    return Ly(_) ? _.then((T) => ({
      status: "valid",
      value: T.status === "valid" ? T.value : this._def.catchValue({
        get error() {
          return new Xi(S.common.issues);
        },
        input: S.data
      })
    })) : {
      status: "valid",
      value: _.status === "valid" ? _.value : this._def.catchValue({
        get error() {
          return new Xi(S.common.issues);
        },
        input: S.data
      })
    };
  }
  removeCatch() {
    return this._def.innerType;
  }
}
Vy.create = (h, c) => new Vy({
  innerType: h,
  typeName: tt.ZodCatch,
  catchValue: typeof c.catch == "function" ? c.catch : () => c.catch,
  ...gt(c)
});
class bT extends Mt {
  _parse(c) {
    if (this._getType(c) !== xe.nan) {
      const S = this._getOrReturnCtx(c);
      return ye(S, {
        code: ae.invalid_type,
        expected: xe.nan,
        received: S.parsedType
      }), et;
    }
    return { status: "valid", value: c.data };
  }
}
bT.create = (h) => new bT({
  typeName: tt.ZodNaN,
  ...gt(h)
});
class zT extends Mt {
  _parse(c) {
    const { ctx: p } = this._processInputParams(c), S = p.data;
    return this._def.type._parse({
      data: S,
      path: p.path,
      parent: p
    });
  }
  unwrap() {
    return this._def.type;
  }
}
class RC extends Mt {
  _parse(c) {
    const { status: p, ctx: S } = this._processInputParams(c);
    if (S.common.async)
      return (async () => {
        const T = await this._def.in._parseAsync({
          data: S.data,
          path: S.path,
          parent: S
        });
        return T.status === "aborted" ? et : T.status === "dirty" ? (p.dirty(), Mv(T.value)) : this._def.out._parseAsync({
          data: T.value,
          path: S.path,
          parent: S
        });
      })();
    {
      const _ = this._def.in._parseSync({
        data: S.data,
        path: S.path,
        parent: S
      });
      return _.status === "aborted" ? et : _.status === "dirty" ? (p.dirty(), {
        status: "dirty",
        value: _.value
      }) : this._def.out._parseSync({
        data: _.value,
        path: S.path,
        parent: S
      });
    }
  }
  static create(c, p) {
    return new RC({
      in: c,
      out: p,
      typeName: tt.ZodPipeline
    });
  }
}
class Py extends Mt {
  _parse(c) {
    const p = this._def.innerType._parse(c), S = (_) => (Ed(_) && (_.value = Object.freeze(_.value)), _);
    return Ly(p) ? p.then((_) => S(_)) : S(p);
  }
  unwrap() {
    return this._def.innerType;
  }
}
Py.create = (h, c) => new Py({
  innerType: h,
  typeName: tt.ZodReadonly,
  ...gt(c)
});
var tt;
(function(h) {
  h.ZodString = "ZodString", h.ZodNumber = "ZodNumber", h.ZodNaN = "ZodNaN", h.ZodBigInt = "ZodBigInt", h.ZodBoolean = "ZodBoolean", h.ZodDate = "ZodDate", h.ZodSymbol = "ZodSymbol", h.ZodUndefined = "ZodUndefined", h.ZodNull = "ZodNull", h.ZodAny = "ZodAny", h.ZodUnknown = "ZodUnknown", h.ZodNever = "ZodNever", h.ZodVoid = "ZodVoid", h.ZodArray = "ZodArray", h.ZodObject = "ZodObject", h.ZodUnion = "ZodUnion", h.ZodDiscriminatedUnion = "ZodDiscriminatedUnion", h.ZodIntersection = "ZodIntersection", h.ZodTuple = "ZodTuple", h.ZodRecord = "ZodRecord", h.ZodMap = "ZodMap", h.ZodSet = "ZodSet", h.ZodFunction = "ZodFunction", h.ZodLazy = "ZodLazy", h.ZodLiteral = "ZodLiteral", h.ZodEnum = "ZodEnum", h.ZodEffects = "ZodEffects", h.ZodNativeEnum = "ZodNativeEnum", h.ZodOptional = "ZodOptional", h.ZodNullable = "ZodNullable", h.ZodDefault = "ZodDefault", h.ZodCatch = "ZodCatch", h.ZodPromise = "ZodPromise", h.ZodBranded = "ZodBranded", h.ZodPipeline = "ZodPipeline", h.ZodReadonly = "ZodReadonly";
})(tt || (tt = {}));
const ar = co.create, By = Cd.create, cO = mC.create, fO = Sd.create;
ds.create;
const dO = SC.create, Iy = du.create, ps = dr.create;
zy.create;
const pO = TC.create;
Uy.create;
po.create;
const vO = jy.create, hO = Lv.create, Hv = Fy.create, $y = Dc.create;
jv.create;
fo.create;
Nc.create;
const wC = ps({
  id: ar().min(1),
  label: ar().min(1),
  action: ar().min(1),
  variant: $y(["primary", "secondary", "danger"]).optional(),
  disabled: cO().optional(),
  payload: vO(fO()).optional()
}), mO = ps({
  type: Hv("text"),
  text: ar()
}), yO = ps({
  type: Hv("image"),
  url: ar().min(1),
  alt: ar().optional(),
  width: By().finite().positive().optional(),
  height: By().finite().positive().optional()
}), gO = ps({
  type: Hv("link"),
  url: ar().min(1),
  title: ar().optional(),
  description: ar().optional(),
  siteName: ar().optional(),
  thumbnailUrl: ar().optional()
}), SO = ps({
  type: Hv("status"),
  tone: $y(["info", "success", "warning", "error"]).optional(),
  text: ar()
}), EO = ps({
  type: Hv("buttons"),
  buttons: Iy(wC)
}), CO = pO("type", [
  mO,
  yO,
  gO,
  SO,
  EO
]), kT = ps({
  id: ar().min(1),
  role: $y(["user", "assistant", "system", "tool"]),
  author: ar().min(1),
  time: ar(),
  createdAt: By().finite().optional(),
  avatarLabel: ar().optional(),
  avatarUrl: ar().optional(),
  blocks: Iy(CO),
  actions: Iy(wC).optional(),
  status: $y(["sending", "sent", "failed", "streaming"]).optional(),
  sortKey: By().finite().optional()
}), _O = ps({
  title: ar().optional(),
  iconSrc: ar().optional(),
  messages: Iy(kT).optional(),
  inputPlaceholder: ar().optional(),
  sendButtonLabel: ar().optional(),
  onMessageAction: hO().args(kT, wC).returns(dO()).optional()
});
function xO(h) {
  return _O.parse(h ?? {});
}
const Yy = /* @__PURE__ */ new WeakMap();
function UT(h, c = {}) {
  const p = xO(c), S = Yy.get(h);
  if (S)
    return S.render(
      /* @__PURE__ */ Fe.jsx(sT.StrictMode, { children: /* @__PURE__ */ Fe.jsx(ST, { ...p }) })
    ), S;
  const _ = Nv.createRoot(h);
  return _.render(
    /* @__PURE__ */ Fe.jsx(sT.StrictMode, { children: /* @__PURE__ */ Fe.jsx(ST, { ...p }) })
  ), Yy.set(h, _), _;
}
function jT(h) {
  const c = Yy.get(h);
  c && (c.unmount(), Yy.delete(h));
}
const TO = UT, RO = jT, wO = {
  mount: UT,
  unmount: jT,
  mountChatWindow: TO,
  unmountChatWindow: RO
};
typeof window < "u" && (window.NekoChatWindow = wO);
export {
  TO as mountChatWindow,
  RO as unmountChatWindow
};
