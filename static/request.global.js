function Kn(e, n) {
  return function() {
    return e.apply(n, arguments);
  };
}
const { toString: _s } = Object.prototype, { getPrototypeOf: jt } = Object, { iterator: ft, toStringTag: Xn } = Symbol, dt = /* @__PURE__ */ ((e) => (n) => {
  const s = _s.call(n);
  return e[s] || (e[s] = s.slice(8, -1).toLowerCase());
})(/* @__PURE__ */ Object.create(null)), oe = (e) => (e = e.toLowerCase(), (n) => dt(n) === e), ht = (e) => (n) => typeof n === e, { isArray: Ne } = Array, Pe = ht("undefined");
function qe(e) {
  return e !== null && !Pe(e) && e.constructor !== null && !Pe(e.constructor) && Z(e.constructor.isBuffer) && e.constructor.isBuffer(e);
}
const Qn = oe("ArrayBuffer");
function Fs(e) {
  let n;
  return typeof ArrayBuffer < "u" && ArrayBuffer.isView ? n = ArrayBuffer.isView(e) : n = e && e.buffer && Qn(e.buffer), n;
}
const Us = ht("string"), Z = ht("function"), Gn = ht("number"), He = (e) => e !== null && typeof e == "object", ks = (e) => e === !0 || e === !1, it = (e) => {
  if (dt(e) !== "object")
    return !1;
  const n = jt(e);
  return (n === null || n === Object.prototype || Object.getPrototypeOf(n) === null) && !(Xn in e) && !(ft in e);
}, Ls = (e) => {
  if (!He(e) || qe(e))
    return !1;
  try {
    return Object.keys(e).length === 0 && Object.getPrototypeOf(e) === Object.prototype;
  } catch {
    return !1;
  }
}, Bs = oe("Date"), Ds = oe("File"), Is = oe("Blob"), js = oe("FileList"), qs = (e) => He(e) && Z(e.pipe), Hs = (e) => {
  let n;
  return e && (typeof FormData == "function" && e instanceof FormData || Z(e.append) && ((n = dt(e)) === "formdata" || // detect form-data instance
  n === "object" && Z(e.toString) && e.toString() === "[object FormData]"));
}, vs = oe("URLSearchParams"), [$s, Ms, zs, Ws] = ["ReadableStream", "Request", "Response", "Headers"].map(oe), Js = (e) => e.trim ? e.trim() : e.replace(/^[\s\uFEFF\xA0]+|[\s\uFEFF\xA0]+$/g, "");
function ve(e, n, { allOwnKeys: s = !1 } = {}) {
  if (e === null || typeof e > "u")
    return;
  let i, c;
  if (typeof e != "object" && (e = [e]), Ne(e))
    for (i = 0, c = e.length; i < c; i++)
      n.call(null, e[i], i, e);
  else {
    if (qe(e))
      return;
    const f = s ? Object.getOwnPropertyNames(e) : Object.keys(e), l = f.length;
    let y;
    for (i = 0; i < l; i++)
      y = f[i], n.call(null, e[y], y, e);
  }
}
function Zn(e, n) {
  if (qe(e))
    return null;
  n = n.toLowerCase();
  const s = Object.keys(e);
  let i = s.length, c;
  for (; i-- > 0; )
    if (c = s[i], n === c.toLowerCase())
      return c;
  return null;
}
const Re = typeof globalThis < "u" ? globalThis : typeof self < "u" ? self : typeof window < "u" ? window : global, Yn = (e) => !Pe(e) && e !== Re;
function Lt() {
  const { caseless: e, skipUndefined: n } = Yn(this) && this || {}, s = {}, i = (c, f) => {
    const l = e && Zn(s, f) || f;
    it(s[l]) && it(c) ? s[l] = Lt(s[l], c) : it(c) ? s[l] = Lt({}, c) : Ne(c) ? s[l] = c.slice() : (!n || !Pe(c)) && (s[l] = c);
  };
  for (let c = 0, f = arguments.length; c < f; c++)
    arguments[c] && ve(arguments[c], i);
  return s;
}
const Vs = (e, n, s, { allOwnKeys: i } = {}) => (ve(n, (c, f) => {
  s && Z(c) ? e[f] = Kn(c, s) : e[f] = c;
}, { allOwnKeys: i }), e), Ks = (e) => (e.charCodeAt(0) === 65279 && (e = e.slice(1)), e), Xs = (e, n, s, i) => {
  e.prototype = Object.create(n.prototype, i), e.prototype.constructor = e, Object.defineProperty(e, "super", {
    value: n.prototype
  }), s && Object.assign(e.prototype, s);
}, Qs = (e, n, s, i) => {
  let c, f, l;
  const y = {};
  if (n = n || {}, e == null) return n;
  do {
    for (c = Object.getOwnPropertyNames(e), f = c.length; f-- > 0; )
      l = c[f], (!i || i(l, e, n)) && !y[l] && (n[l] = e[l], y[l] = !0);
    e = s !== !1 && jt(e);
  } while (e && (!s || s(e, n)) && e !== Object.prototype);
  return n;
}, Gs = (e, n, s) => {
  e = String(e), (s === void 0 || s > e.length) && (s = e.length), s -= n.length;
  const i = e.indexOf(n, s);
  return i !== -1 && i === s;
}, Zs = (e) => {
  if (!e) return null;
  if (Ne(e)) return e;
  let n = e.length;
  if (!Gn(n)) return null;
  const s = new Array(n);
  for (; n-- > 0; )
    s[n] = e[n];
  return s;
}, Ys = /* @__PURE__ */ ((e) => (n) => e && n instanceof e)(typeof Uint8Array < "u" && jt(Uint8Array)), eo = (e, n) => {
  const i = (e && e[ft]).call(e);
  let c;
  for (; (c = i.next()) && !c.done; ) {
    const f = c.value;
    n.call(e, f[0], f[1]);
  }
}, to = (e, n) => {
  let s;
  const i = [];
  for (; (s = e.exec(n)) !== null; )
    i.push(s);
  return i;
}, no = oe("HTMLFormElement"), ro = (e) => e.toLowerCase().replace(
  /[-_\s]([a-z\d])(\w*)/g,
  function(s, i, c) {
    return i.toUpperCase() + c;
  }
), Nn = (({ hasOwnProperty: e }) => (n, s) => e.call(n, s))(Object.prototype), so = oe("RegExp"), er = (e, n) => {
  const s = Object.getOwnPropertyDescriptors(e), i = {};
  ve(s, (c, f) => {
    let l;
    (l = n(c, f, e)) !== !1 && (i[f] = l || c);
  }), Object.defineProperties(e, i);
}, oo = (e) => {
  er(e, (n, s) => {
    if (Z(e) && ["arguments", "caller", "callee"].indexOf(s) !== -1)
      return !1;
    const i = e[s];
    if (Z(i)) {
      if (n.enumerable = !1, "writable" in n) {
        n.writable = !1;
        return;
      }
      n.set || (n.set = () => {
        throw Error("Can not rewrite read-only method '" + s + "'");
      });
    }
  });
}, io = (e, n) => {
  const s = {}, i = (c) => {
    c.forEach((f) => {
      s[f] = !0;
    });
  };
  return Ne(e) ? i(e) : i(String(e).split(n)), s;
}, ao = () => {
}, co = (e, n) => e != null && Number.isFinite(e = +e) ? e : n;
function uo(e) {
  return !!(e && Z(e.append) && e[Xn] === "FormData" && e[ft]);
}
const lo = (e) => {
  const n = new Array(10), s = (i, c) => {
    if (He(i)) {
      if (n.indexOf(i) >= 0)
        return;
      if (qe(i))
        return i;
      if (!("toJSON" in i)) {
        n[c] = i;
        const f = Ne(i) ? [] : {};
        return ve(i, (l, y) => {
          const S = s(l, c + 1);
          !Pe(S) && (f[y] = S);
        }), n[c] = void 0, f;
      }
    }
    return i;
  };
  return s(e, 0);
}, fo = oe("AsyncFunction"), ho = (e) => e && (He(e) || Z(e)) && Z(e.then) && Z(e.catch), tr = ((e, n) => e ? setImmediate : n ? ((s, i) => (Re.addEventListener("message", ({ source: c, data: f }) => {
  c === Re && f === s && i.length && i.shift()();
}, !1), (c) => {
  i.push(c), Re.postMessage(s, "*");
}))(`axios@${Math.random()}`, []) : (s) => setTimeout(s))(
  typeof setImmediate == "function",
  Z(Re.postMessage)
), po = typeof queueMicrotask < "u" ? queueMicrotask.bind(Re) : typeof process < "u" && process.nextTick || tr, mo = (e) => e != null && Z(e[ft]), m = {
  isArray: Ne,
  isArrayBuffer: Qn,
  isBuffer: qe,
  isFormData: Hs,
  isArrayBufferView: Fs,
  isString: Us,
  isNumber: Gn,
  isBoolean: ks,
  isObject: He,
  isPlainObject: it,
  isEmptyObject: Ls,
  isReadableStream: $s,
  isRequest: Ms,
  isResponse: zs,
  isHeaders: Ws,
  isUndefined: Pe,
  isDate: Bs,
  isFile: Ds,
  isBlob: Is,
  isRegExp: so,
  isFunction: Z,
  isStream: qs,
  isURLSearchParams: vs,
  isTypedArray: Ys,
  isFileList: js,
  forEach: ve,
  merge: Lt,
  extend: Vs,
  trim: Js,
  stripBOM: Ks,
  inherits: Xs,
  toFlatObject: Qs,
  kindOf: dt,
  kindOfTest: oe,
  endsWith: Gs,
  toArray: Zs,
  forEachEntry: eo,
  matchAll: to,
  isHTMLForm: no,
  hasOwnProperty: Nn,
  hasOwnProp: Nn,
  // an alias to avoid ESLint no-prototype-builtins detection
  reduceDescriptors: er,
  freezeMethods: oo,
  toObjectSet: io,
  toCamelCase: ro,
  noop: ao,
  toFiniteNumber: co,
  findKey: Zn,
  global: Re,
  isContextDefined: Yn,
  isSpecCompliantForm: uo,
  toJSONObject: lo,
  isAsyncFn: fo,
  isThenable: ho,
  setImmediate: tr,
  asap: po,
  isIterable: mo
};
function B(e, n, s, i, c) {
  Error.call(this), Error.captureStackTrace ? Error.captureStackTrace(this, this.constructor) : this.stack = new Error().stack, this.message = e, this.name = "AxiosError", n && (this.code = n), s && (this.config = s), i && (this.request = i), c && (this.response = c, this.status = c.status ? c.status : null);
}
m.inherits(B, Error, {
  toJSON: function() {
    return {
      // Standard
      message: this.message,
      name: this.name,
      // Microsoft
      description: this.description,
      number: this.number,
      // Mozilla
      fileName: this.fileName,
      lineNumber: this.lineNumber,
      columnNumber: this.columnNumber,
      stack: this.stack,
      // Axios
      config: m.toJSONObject(this.config),
      code: this.code,
      status: this.status
    };
  }
});
const nr = B.prototype, rr = {};
[
  "ERR_BAD_OPTION_VALUE",
  "ERR_BAD_OPTION",
  "ECONNABORTED",
  "ETIMEDOUT",
  "ERR_NETWORK",
  "ERR_FR_TOO_MANY_REDIRECTS",
  "ERR_DEPRECATED",
  "ERR_BAD_RESPONSE",
  "ERR_BAD_REQUEST",
  "ERR_CANCELED",
  "ERR_NOT_SUPPORT",
  "ERR_INVALID_URL"
  // eslint-disable-next-line func-names
].forEach((e) => {
  rr[e] = { value: e };
});
Object.defineProperties(B, rr);
Object.defineProperty(nr, "isAxiosError", { value: !0 });
B.from = (e, n, s, i, c, f) => {
  const l = Object.create(nr);
  m.toFlatObject(e, l, function(g) {
    return g !== Error.prototype;
  }, (R) => R !== "isAxiosError");
  const y = e && e.message ? e.message : "Error", S = n == null && e ? e.code : n;
  return B.call(l, y, S, s, i, c), e && l.cause == null && Object.defineProperty(l, "cause", { value: e, configurable: !0 }), l.name = e && e.name || "Error", f && Object.assign(l, f), l;
};
const yo = null;
function Bt(e) {
  return m.isPlainObject(e) || m.isArray(e);
}
function sr(e) {
  return m.endsWith(e, "[]") ? e.slice(0, -2) : e;
}
function _n(e, n, s) {
  return e ? e.concat(n).map(function(c, f) {
    return c = sr(c), !s && f ? "[" + c + "]" : c;
  }).join(s ? "." : "") : n;
}
function wo(e) {
  return m.isArray(e) && !e.some(Bt);
}
const bo = m.toFlatObject(m, {}, null, function(n) {
  return /^is[A-Z]/.test(n);
});
function pt(e, n, s) {
  if (!m.isObject(e))
    throw new TypeError("target must be an object");
  n = n || new FormData(), s = m.toFlatObject(s, {
    metaTokens: !0,
    dots: !1,
    indexes: !1
  }, !1, function(_, x) {
    return !m.isUndefined(x[_]);
  });
  const i = s.metaTokens, c = s.visitor || g, f = s.dots, l = s.indexes, S = (s.Blob || typeof Blob < "u" && Blob) && m.isSpecCompliantForm(n);
  if (!m.isFunction(c))
    throw new TypeError("visitor must be a function");
  function R(E) {
    if (E === null) return "";
    if (m.isDate(E))
      return E.toISOString();
    if (m.isBoolean(E))
      return E.toString();
    if (!S && m.isBlob(E))
      throw new B("Blob is not supported. Use a Buffer instead.");
    return m.isArrayBuffer(E) || m.isTypedArray(E) ? S && typeof Blob == "function" ? new Blob([E]) : Buffer.from(E) : E;
  }
  function g(E, _, x) {
    let I = E;
    if (E && !x && typeof E == "object") {
      if (m.endsWith(_, "{}"))
        _ = i ? _ : _.slice(0, -2), E = JSON.stringify(E);
      else if (m.isArray(E) && wo(E) || (m.isFileList(E) || m.endsWith(_, "[]")) && (I = m.toArray(E)))
        return _ = sr(_), I.forEach(function(q, z) {
          !(m.isUndefined(q) || q === null) && n.append(
            // eslint-disable-next-line no-nested-ternary
            l === !0 ? _n([_], z, f) : l === null ? _ : _ + "[]",
            R(q)
          );
        }), !1;
    }
    return Bt(E) ? !0 : (n.append(_n(x, _, f), R(E)), !1);
  }
  const b = [], C = Object.assign(bo, {
    defaultVisitor: g,
    convertValue: R,
    isVisitable: Bt
  });
  function k(E, _) {
    if (!m.isUndefined(E)) {
      if (b.indexOf(E) !== -1)
        throw Error("Circular reference detected in " + _.join("."));
      b.push(E), m.forEach(E, function(I, M) {
        (!(m.isUndefined(I) || I === null) && c.call(
          n,
          I,
          m.isString(M) ? M.trim() : M,
          _,
          C
        )) === !0 && k(I, _ ? _.concat(M) : [M]);
      }), b.pop();
    }
  }
  if (!m.isObject(e))
    throw new TypeError("data must be an object");
  return k(e), n;
}
function Fn(e) {
  const n = {
    "!": "%21",
    "'": "%27",
    "(": "%28",
    ")": "%29",
    "~": "%7E",
    "%20": "+",
    "%00": "\0"
  };
  return encodeURIComponent(e).replace(/[!'()~]|%20|%00/g, function(i) {
    return n[i];
  });
}
function qt(e, n) {
  this._pairs = [], e && pt(e, this, n);
}
const or = qt.prototype;
or.append = function(n, s) {
  this._pairs.push([n, s]);
};
or.toString = function(n) {
  const s = n ? function(i) {
    return n.call(this, i, Fn);
  } : Fn;
  return this._pairs.map(function(c) {
    return s(c[0]) + "=" + s(c[1]);
  }, "").join("&");
};
function Ro(e) {
  return encodeURIComponent(e).replace(/%3A/gi, ":").replace(/%24/g, "$").replace(/%2C/gi, ",").replace(/%20/g, "+");
}
function ir(e, n, s) {
  if (!n)
    return e;
  const i = s && s.encode || Ro;
  m.isFunction(s) && (s = {
    serialize: s
  });
  const c = s && s.serialize;
  let f;
  if (c ? f = c(n, s) : f = m.isURLSearchParams(n) ? n.toString() : new qt(n, s).toString(i), f) {
    const l = e.indexOf("#");
    l !== -1 && (e = e.slice(0, l)), e += (e.indexOf("?") === -1 ? "?" : "&") + f;
  }
  return e;
}
class Un {
  constructor() {
    this.handlers = [];
  }
  /**
   * Add a new interceptor to the stack
   *
   * @param {Function} fulfilled The function to handle `then` for a `Promise`
   * @param {Function} rejected The function to handle `reject` for a `Promise`
   *
   * @return {Number} An ID used to remove interceptor later
   */
  use(n, s, i) {
    return this.handlers.push({
      fulfilled: n,
      rejected: s,
      synchronous: i ? i.synchronous : !1,
      runWhen: i ? i.runWhen : null
    }), this.handlers.length - 1;
  }
  /**
   * Remove an interceptor from the stack
   *
   * @param {Number} id The ID that was returned by `use`
   *
   * @returns {void}
   */
  eject(n) {
    this.handlers[n] && (this.handlers[n] = null);
  }
  /**
   * Clear all interceptors from the stack
   *
   * @returns {void}
   */
  clear() {
    this.handlers && (this.handlers = []);
  }
  /**
   * Iterate over all the registered interceptors
   *
   * This method is particularly useful for skipping over any
   * interceptors that may have become `null` calling `eject`.
   *
   * @param {Function} fn The function to call for each interceptor
   *
   * @returns {void}
   */
  forEach(n) {
    m.forEach(this.handlers, function(i) {
      i !== null && n(i);
    });
  }
}
const ar = {
  silentJSONParsing: !0,
  forcedJSONParsing: !0,
  clarifyTimeoutError: !1
}, go = typeof URLSearchParams < "u" ? URLSearchParams : qt, Eo = typeof FormData < "u" ? FormData : null, So = typeof Blob < "u" ? Blob : null, Oo = {
  isBrowser: !0,
  classes: {
    URLSearchParams: go,
    FormData: Eo,
    Blob: So
  },
  protocols: ["http", "https", "file", "blob", "url", "data"]
}, Ht = typeof window < "u" && typeof document < "u", Dt = typeof navigator == "object" && navigator || void 0, To = Ht && (!Dt || ["ReactNative", "NativeScript", "NS"].indexOf(Dt.product) < 0), Ao = typeof WorkerGlobalScope < "u" && // eslint-disable-next-line no-undef
self instanceof WorkerGlobalScope && typeof self.importScripts == "function", Co = Ht && window.location.href || "http://localhost", xo = /* @__PURE__ */ Object.freeze(/* @__PURE__ */ Object.defineProperty({
  __proto__: null,
  hasBrowserEnv: Ht,
  hasStandardBrowserEnv: To,
  hasStandardBrowserWebWorkerEnv: Ao,
  navigator: Dt,
  origin: Co
}, Symbol.toStringTag, { value: "Module" })), X = {
  ...xo,
  ...Oo
};
function Po(e, n) {
  return pt(e, new X.classes.URLSearchParams(), {
    visitor: function(s, i, c, f) {
      return X.isNode && m.isBuffer(s) ? (this.append(i, s.toString("base64")), !1) : f.defaultVisitor.apply(this, arguments);
    },
    ...n
  });
}
function No(e) {
  return m.matchAll(/\w+|\[(\w*)]/g, e).map((n) => n[0] === "[]" ? "" : n[1] || n[0]);
}
function _o(e) {
  const n = {}, s = Object.keys(e);
  let i;
  const c = s.length;
  let f;
  for (i = 0; i < c; i++)
    f = s[i], n[f] = e[f];
  return n;
}
function cr(e) {
  function n(s, i, c, f) {
    let l = s[f++];
    if (l === "__proto__") return !0;
    const y = Number.isFinite(+l), S = f >= s.length;
    return l = !l && m.isArray(c) ? c.length : l, S ? (m.hasOwnProp(c, l) ? c[l] = [c[l], i] : c[l] = i, !y) : ((!c[l] || !m.isObject(c[l])) && (c[l] = []), n(s, i, c[l], f) && m.isArray(c[l]) && (c[l] = _o(c[l])), !y);
  }
  if (m.isFormData(e) && m.isFunction(e.entries)) {
    const s = {};
    return m.forEachEntry(e, (i, c) => {
      n(No(i), c, s, 0);
    }), s;
  }
  return null;
}
function Fo(e, n, s) {
  if (m.isString(e))
    try {
      return (n || JSON.parse)(e), m.trim(e);
    } catch (i) {
      if (i.name !== "SyntaxError")
        throw i;
    }
  return (s || JSON.stringify)(e);
}
const $e = {
  transitional: ar,
  adapter: ["xhr", "http", "fetch"],
  transformRequest: [function(n, s) {
    const i = s.getContentType() || "", c = i.indexOf("application/json") > -1, f = m.isObject(n);
    if (f && m.isHTMLForm(n) && (n = new FormData(n)), m.isFormData(n))
      return c ? JSON.stringify(cr(n)) : n;
    if (m.isArrayBuffer(n) || m.isBuffer(n) || m.isStream(n) || m.isFile(n) || m.isBlob(n) || m.isReadableStream(n))
      return n;
    if (m.isArrayBufferView(n))
      return n.buffer;
    if (m.isURLSearchParams(n))
      return s.setContentType("application/x-www-form-urlencoded;charset=utf-8", !1), n.toString();
    let y;
    if (f) {
      if (i.indexOf("application/x-www-form-urlencoded") > -1)
        return Po(n, this.formSerializer).toString();
      if ((y = m.isFileList(n)) || i.indexOf("multipart/form-data") > -1) {
        const S = this.env && this.env.FormData;
        return pt(
          y ? { "files[]": n } : n,
          S && new S(),
          this.formSerializer
        );
      }
    }
    return f || c ? (s.setContentType("application/json", !1), Fo(n)) : n;
  }],
  transformResponse: [function(n) {
    const s = this.transitional || $e.transitional, i = s && s.forcedJSONParsing, c = this.responseType === "json";
    if (m.isResponse(n) || m.isReadableStream(n))
      return n;
    if (n && m.isString(n) && (i && !this.responseType || c)) {
      const l = !(s && s.silentJSONParsing) && c;
      try {
        return JSON.parse(n, this.parseReviver);
      } catch (y) {
        if (l)
          throw y.name === "SyntaxError" ? B.from(y, B.ERR_BAD_RESPONSE, this, null, this.response) : y;
      }
    }
    return n;
  }],
  /**
   * A timeout in milliseconds to abort a request. If set to 0 (default) a
   * timeout is not created.
   */
  timeout: 0,
  xsrfCookieName: "XSRF-TOKEN",
  xsrfHeaderName: "X-XSRF-TOKEN",
  maxContentLength: -1,
  maxBodyLength: -1,
  env: {
    FormData: X.classes.FormData,
    Blob: X.classes.Blob
  },
  validateStatus: function(n) {
    return n >= 200 && n < 300;
  },
  headers: {
    common: {
      Accept: "application/json, text/plain, */*",
      "Content-Type": void 0
    }
  }
};
m.forEach(["delete", "get", "head", "post", "put", "patch"], (e) => {
  $e.headers[e] = {};
});
const Uo = m.toObjectSet([
  "age",
  "authorization",
  "content-length",
  "content-type",
  "etag",
  "expires",
  "from",
  "host",
  "if-modified-since",
  "if-unmodified-since",
  "last-modified",
  "location",
  "max-forwards",
  "proxy-authorization",
  "referer",
  "retry-after",
  "user-agent"
]), ko = (e) => {
  const n = {};
  let s, i, c;
  return e && e.split(`
`).forEach(function(l) {
    c = l.indexOf(":"), s = l.substring(0, c).trim().toLowerCase(), i = l.substring(c + 1).trim(), !(!s || n[s] && Uo[s]) && (s === "set-cookie" ? n[s] ? n[s].push(i) : n[s] = [i] : n[s] = n[s] ? n[s] + ", " + i : i);
  }), n;
}, kn = Symbol("internals");
function je(e) {
  return e && String(e).trim().toLowerCase();
}
function at(e) {
  return e === !1 || e == null ? e : m.isArray(e) ? e.map(at) : String(e);
}
function Lo(e) {
  const n = /* @__PURE__ */ Object.create(null), s = /([^\s,;=]+)\s*(?:=\s*([^,;]+))?/g;
  let i;
  for (; i = s.exec(e); )
    n[i[1]] = i[2];
  return n;
}
const Bo = (e) => /^[-_a-zA-Z0-9^`|~,!#$%&'*+.]+$/.test(e.trim());
function _t(e, n, s, i, c) {
  if (m.isFunction(i))
    return i.call(this, n, s);
  if (c && (n = s), !!m.isString(n)) {
    if (m.isString(i))
      return n.indexOf(i) !== -1;
    if (m.isRegExp(i))
      return i.test(n);
  }
}
function Do(e) {
  return e.trim().toLowerCase().replace(/([a-z\d])(\w*)/g, (n, s, i) => s.toUpperCase() + i);
}
function Io(e, n) {
  const s = m.toCamelCase(" " + n);
  ["get", "set", "has"].forEach((i) => {
    Object.defineProperty(e, i + s, {
      value: function(c, f, l) {
        return this[i].call(this, n, c, f, l);
      },
      configurable: !0
    });
  });
}
let Y = class {
  constructor(n) {
    n && this.set(n);
  }
  set(n, s, i) {
    const c = this;
    function f(y, S, R) {
      const g = je(S);
      if (!g)
        throw new Error("header name must be a non-empty string");
      const b = m.findKey(c, g);
      (!b || c[b] === void 0 || R === !0 || R === void 0 && c[b] !== !1) && (c[b || S] = at(y));
    }
    const l = (y, S) => m.forEach(y, (R, g) => f(R, g, S));
    if (m.isPlainObject(n) || n instanceof this.constructor)
      l(n, s);
    else if (m.isString(n) && (n = n.trim()) && !Bo(n))
      l(ko(n), s);
    else if (m.isObject(n) && m.isIterable(n)) {
      let y = {}, S, R;
      for (const g of n) {
        if (!m.isArray(g))
          throw TypeError("Object iterator must return a key-value pair");
        y[R = g[0]] = (S = y[R]) ? m.isArray(S) ? [...S, g[1]] : [S, g[1]] : g[1];
      }
      l(y, s);
    } else
      n != null && f(s, n, i);
    return this;
  }
  get(n, s) {
    if (n = je(n), n) {
      const i = m.findKey(this, n);
      if (i) {
        const c = this[i];
        if (!s)
          return c;
        if (s === !0)
          return Lo(c);
        if (m.isFunction(s))
          return s.call(this, c, i);
        if (m.isRegExp(s))
          return s.exec(c);
        throw new TypeError("parser must be boolean|regexp|function");
      }
    }
  }
  has(n, s) {
    if (n = je(n), n) {
      const i = m.findKey(this, n);
      return !!(i && this[i] !== void 0 && (!s || _t(this, this[i], i, s)));
    }
    return !1;
  }
  delete(n, s) {
    const i = this;
    let c = !1;
    function f(l) {
      if (l = je(l), l) {
        const y = m.findKey(i, l);
        y && (!s || _t(i, i[y], y, s)) && (delete i[y], c = !0);
      }
    }
    return m.isArray(n) ? n.forEach(f) : f(n), c;
  }
  clear(n) {
    const s = Object.keys(this);
    let i = s.length, c = !1;
    for (; i--; ) {
      const f = s[i];
      (!n || _t(this, this[f], f, n, !0)) && (delete this[f], c = !0);
    }
    return c;
  }
  normalize(n) {
    const s = this, i = {};
    return m.forEach(this, (c, f) => {
      const l = m.findKey(i, f);
      if (l) {
        s[l] = at(c), delete s[f];
        return;
      }
      const y = n ? Do(f) : String(f).trim();
      y !== f && delete s[f], s[y] = at(c), i[y] = !0;
    }), this;
  }
  concat(...n) {
    return this.constructor.concat(this, ...n);
  }
  toJSON(n) {
    const s = /* @__PURE__ */ Object.create(null);
    return m.forEach(this, (i, c) => {
      i != null && i !== !1 && (s[c] = n && m.isArray(i) ? i.join(", ") : i);
    }), s;
  }
  [Symbol.iterator]() {
    return Object.entries(this.toJSON())[Symbol.iterator]();
  }
  toString() {
    return Object.entries(this.toJSON()).map(([n, s]) => n + ": " + s).join(`
`);
  }
  getSetCookie() {
    return this.get("set-cookie") || [];
  }
  get [Symbol.toStringTag]() {
    return "AxiosHeaders";
  }
  static from(n) {
    return n instanceof this ? n : new this(n);
  }
  static concat(n, ...s) {
    const i = new this(n);
    return s.forEach((c) => i.set(c)), i;
  }
  static accessor(n) {
    const i = (this[kn] = this[kn] = {
      accessors: {}
    }).accessors, c = this.prototype;
    function f(l) {
      const y = je(l);
      i[y] || (Io(c, l), i[y] = !0);
    }
    return m.isArray(n) ? n.forEach(f) : f(n), this;
  }
};
Y.accessor(["Content-Type", "Content-Length", "Accept", "Accept-Encoding", "User-Agent", "Authorization"]);
m.reduceDescriptors(Y.prototype, ({ value: e }, n) => {
  let s = n[0].toUpperCase() + n.slice(1);
  return {
    get: () => e,
    set(i) {
      this[s] = i;
    }
  };
});
m.freezeMethods(Y);
function Ft(e, n) {
  const s = this || $e, i = n || s, c = Y.from(i.headers);
  let f = i.data;
  return m.forEach(e, function(y) {
    f = y.call(s, f, c.normalize(), n ? n.status : void 0);
  }), c.normalize(), f;
}
function ur(e) {
  return !!(e && e.__CANCEL__);
}
function _e(e, n, s) {
  B.call(this, e ?? "canceled", B.ERR_CANCELED, n, s), this.name = "CanceledError";
}
m.inherits(_e, B, {
  __CANCEL__: !0
});
function lr(e, n, s) {
  const i = s.config.validateStatus;
  !s.status || !i || i(s.status) ? e(s) : n(new B(
    "Request failed with status code " + s.status,
    [B.ERR_BAD_REQUEST, B.ERR_BAD_RESPONSE][Math.floor(s.status / 100) - 4],
    s.config,
    s.request,
    s
  ));
}
function jo(e) {
  const n = /^([-+\w]{1,25})(:?\/\/|:)/.exec(e);
  return n && n[1] || "";
}
function qo(e, n) {
  e = e || 10;
  const s = new Array(e), i = new Array(e);
  let c = 0, f = 0, l;
  return n = n !== void 0 ? n : 1e3, function(S) {
    const R = Date.now(), g = i[f];
    l || (l = R), s[c] = S, i[c] = R;
    let b = f, C = 0;
    for (; b !== c; )
      C += s[b++], b = b % e;
    if (c = (c + 1) % e, c === f && (f = (f + 1) % e), R - l < n)
      return;
    const k = g && R - g;
    return k ? Math.round(C * 1e3 / k) : void 0;
  };
}
function Ho(e, n) {
  let s = 0, i = 1e3 / n, c, f;
  const l = (R, g = Date.now()) => {
    s = g, c = null, f && (clearTimeout(f), f = null), e(...R);
  };
  return [(...R) => {
    const g = Date.now(), b = g - s;
    b >= i ? l(R, g) : (c = R, f || (f = setTimeout(() => {
      f = null, l(c);
    }, i - b)));
  }, () => c && l(c)];
}
const lt = (e, n, s = 3) => {
  let i = 0;
  const c = qo(50, 250);
  return Ho((f) => {
    const l = f.loaded, y = f.lengthComputable ? f.total : void 0, S = l - i, R = c(S), g = l <= y;
    i = l;
    const b = {
      loaded: l,
      total: y,
      progress: y ? l / y : void 0,
      bytes: S,
      rate: R || void 0,
      estimated: R && y && g ? (y - l) / R : void 0,
      event: f,
      lengthComputable: y != null,
      [n ? "download" : "upload"]: !0
    };
    e(b);
  }, s);
}, Ln = (e, n) => {
  const s = e != null;
  return [(i) => n[0]({
    lengthComputable: s,
    total: e,
    loaded: i
  }), n[1]];
}, Bn = (e) => (...n) => m.asap(() => e(...n)), vo = X.hasStandardBrowserEnv ? /* @__PURE__ */ ((e, n) => (s) => (s = new URL(s, X.origin), e.protocol === s.protocol && e.host === s.host && (n || e.port === s.port)))(
  new URL(X.origin),
  X.navigator && /(msie|trident)/i.test(X.navigator.userAgent)
) : () => !0, $o = X.hasStandardBrowserEnv ? (
  // Standard browser envs support document.cookie
  {
    write(e, n, s, i, c, f, l) {
      if (typeof document > "u") return;
      const y = [`${e}=${encodeURIComponent(n)}`];
      m.isNumber(s) && y.push(`expires=${new Date(s).toUTCString()}`), m.isString(i) && y.push(`path=${i}`), m.isString(c) && y.push(`domain=${c}`), f === !0 && y.push("secure"), m.isString(l) && y.push(`SameSite=${l}`), document.cookie = y.join("; ");
    },
    read(e) {
      if (typeof document > "u") return null;
      const n = document.cookie.match(new RegExp("(?:^|; )" + e + "=([^;]*)"));
      return n ? decodeURIComponent(n[1]) : null;
    },
    remove(e) {
      this.write(e, "", Date.now() - 864e5, "/");
    }
  }
) : (
  // Non-standard browser env (web workers, react-native) lack needed support.
  {
    write() {
    },
    read() {
      return null;
    },
    remove() {
    }
  }
);
function Mo(e) {
  return /^([a-z][a-z\d+\-.]*:)?\/\//i.test(e);
}
function zo(e, n) {
  return n ? e.replace(/\/?\/$/, "") + "/" + n.replace(/^\/+/, "") : e;
}
function fr(e, n, s) {
  let i = !Mo(n);
  return e && (i || s == !1) ? zo(e, n) : n;
}
const Dn = (e) => e instanceof Y ? { ...e } : e;
function Ee(e, n) {
  n = n || {};
  const s = {};
  function i(R, g, b, C) {
    return m.isPlainObject(R) && m.isPlainObject(g) ? m.merge.call({ caseless: C }, R, g) : m.isPlainObject(g) ? m.merge({}, g) : m.isArray(g) ? g.slice() : g;
  }
  function c(R, g, b, C) {
    if (m.isUndefined(g)) {
      if (!m.isUndefined(R))
        return i(void 0, R, b, C);
    } else return i(R, g, b, C);
  }
  function f(R, g) {
    if (!m.isUndefined(g))
      return i(void 0, g);
  }
  function l(R, g) {
    if (m.isUndefined(g)) {
      if (!m.isUndefined(R))
        return i(void 0, R);
    } else return i(void 0, g);
  }
  function y(R, g, b) {
    if (b in n)
      return i(R, g);
    if (b in e)
      return i(void 0, R);
  }
  const S = {
    url: f,
    method: f,
    data: f,
    baseURL: l,
    transformRequest: l,
    transformResponse: l,
    paramsSerializer: l,
    timeout: l,
    timeoutMessage: l,
    withCredentials: l,
    withXSRFToken: l,
    adapter: l,
    responseType: l,
    xsrfCookieName: l,
    xsrfHeaderName: l,
    onUploadProgress: l,
    onDownloadProgress: l,
    decompress: l,
    maxContentLength: l,
    maxBodyLength: l,
    beforeRedirect: l,
    transport: l,
    httpAgent: l,
    httpsAgent: l,
    cancelToken: l,
    socketPath: l,
    responseEncoding: l,
    validateStatus: y,
    headers: (R, g, b) => c(Dn(R), Dn(g), b, !0)
  };
  return m.forEach(Object.keys({ ...e, ...n }), function(g) {
    const b = S[g] || c, C = b(e[g], n[g], g);
    m.isUndefined(C) && b !== y || (s[g] = C);
  }), s;
}
const dr = (e) => {
  const n = Ee({}, e);
  let { data: s, withXSRFToken: i, xsrfHeaderName: c, xsrfCookieName: f, headers: l, auth: y } = n;
  if (n.headers = l = Y.from(l), n.url = ir(fr(n.baseURL, n.url, n.allowAbsoluteUrls), e.params, e.paramsSerializer), y && l.set(
    "Authorization",
    "Basic " + btoa((y.username || "") + ":" + (y.password ? unescape(encodeURIComponent(y.password)) : ""))
  ), m.isFormData(s)) {
    if (X.hasStandardBrowserEnv || X.hasStandardBrowserWebWorkerEnv)
      l.setContentType(void 0);
    else if (m.isFunction(s.getHeaders)) {
      const S = s.getHeaders(), R = ["content-type", "content-length"];
      Object.entries(S).forEach(([g, b]) => {
        R.includes(g.toLowerCase()) && l.set(g, b);
      });
    }
  }
  if (X.hasStandardBrowserEnv && (i && m.isFunction(i) && (i = i(n)), i || i !== !1 && vo(n.url))) {
    const S = c && f && $o.read(f);
    S && l.set(c, S);
  }
  return n;
}, Wo = typeof XMLHttpRequest < "u", Jo = Wo && function(e) {
  return new Promise(function(s, i) {
    const c = dr(e);
    let f = c.data;
    const l = Y.from(c.headers).normalize();
    let { responseType: y, onUploadProgress: S, onDownloadProgress: R } = c, g, b, C, k, E;
    function _() {
      k && k(), E && E(), c.cancelToken && c.cancelToken.unsubscribe(g), c.signal && c.signal.removeEventListener("abort", g);
    }
    let x = new XMLHttpRequest();
    x.open(c.method.toUpperCase(), c.url, !0), x.timeout = c.timeout;
    function I() {
      if (!x)
        return;
      const q = Y.from(
        "getAllResponseHeaders" in x && x.getAllResponseHeaders()
      ), ee = {
        data: !y || y === "text" || y === "json" ? x.responseText : x.response,
        status: x.status,
        statusText: x.statusText,
        headers: q,
        config: e,
        request: x
      };
      lr(function(Q) {
        s(Q), _();
      }, function(Q) {
        i(Q), _();
      }, ee), x = null;
    }
    "onloadend" in x ? x.onloadend = I : x.onreadystatechange = function() {
      !x || x.readyState !== 4 || x.status === 0 && !(x.responseURL && x.responseURL.indexOf("file:") === 0) || setTimeout(I);
    }, x.onabort = function() {
      x && (i(new B("Request aborted", B.ECONNABORTED, e, x)), x = null);
    }, x.onerror = function(z) {
      const ee = z && z.message ? z.message : "Network Error", le = new B(ee, B.ERR_NETWORK, e, x);
      le.event = z || null, i(le), x = null;
    }, x.ontimeout = function() {
      let z = c.timeout ? "timeout of " + c.timeout + "ms exceeded" : "timeout exceeded";
      const ee = c.transitional || ar;
      c.timeoutErrorMessage && (z = c.timeoutErrorMessage), i(new B(
        z,
        ee.clarifyTimeoutError ? B.ETIMEDOUT : B.ECONNABORTED,
        e,
        x
      )), x = null;
    }, f === void 0 && l.setContentType(null), "setRequestHeader" in x && m.forEach(l.toJSON(), function(z, ee) {
      x.setRequestHeader(ee, z);
    }), m.isUndefined(c.withCredentials) || (x.withCredentials = !!c.withCredentials), y && y !== "json" && (x.responseType = c.responseType), R && ([C, E] = lt(R, !0), x.addEventListener("progress", C)), S && x.upload && ([b, k] = lt(S), x.upload.addEventListener("progress", b), x.upload.addEventListener("loadend", k)), (c.cancelToken || c.signal) && (g = (q) => {
      x && (i(!q || q.type ? new _e(null, e, x) : q), x.abort(), x = null);
    }, c.cancelToken && c.cancelToken.subscribe(g), c.signal && (c.signal.aborted ? g() : c.signal.addEventListener("abort", g)));
    const M = jo(c.url);
    if (M && X.protocols.indexOf(M) === -1) {
      i(new B("Unsupported protocol " + M + ":", B.ERR_BAD_REQUEST, e));
      return;
    }
    x.send(f || null);
  });
}, Vo = (e, n) => {
  const { length: s } = e = e ? e.filter(Boolean) : [];
  if (n || s) {
    let i = new AbortController(), c;
    const f = function(R) {
      if (!c) {
        c = !0, y();
        const g = R instanceof Error ? R : this.reason;
        i.abort(g instanceof B ? g : new _e(g instanceof Error ? g.message : g));
      }
    };
    let l = n && setTimeout(() => {
      l = null, f(new B(`timeout ${n} of ms exceeded`, B.ETIMEDOUT));
    }, n);
    const y = () => {
      e && (l && clearTimeout(l), l = null, e.forEach((R) => {
        R.unsubscribe ? R.unsubscribe(f) : R.removeEventListener("abort", f);
      }), e = null);
    };
    e.forEach((R) => R.addEventListener("abort", f));
    const { signal: S } = i;
    return S.unsubscribe = () => m.asap(y), S;
  }
}, Ko = function* (e, n) {
  let s = e.byteLength;
  if (s < n) {
    yield e;
    return;
  }
  let i = 0, c;
  for (; i < s; )
    c = i + n, yield e.slice(i, c), i = c;
}, Xo = async function* (e, n) {
  for await (const s of Qo(e))
    yield* Ko(s, n);
}, Qo = async function* (e) {
  if (e[Symbol.asyncIterator]) {
    yield* e;
    return;
  }
  const n = e.getReader();
  try {
    for (; ; ) {
      const { done: s, value: i } = await n.read();
      if (s)
        break;
      yield i;
    }
  } finally {
    await n.cancel();
  }
}, In = (e, n, s, i) => {
  const c = Xo(e, n);
  let f = 0, l, y = (S) => {
    l || (l = !0, i && i(S));
  };
  return new ReadableStream({
    async pull(S) {
      try {
        const { done: R, value: g } = await c.next();
        if (R) {
          y(), S.close();
          return;
        }
        let b = g.byteLength;
        if (s) {
          let C = f += b;
          s(C);
        }
        S.enqueue(new Uint8Array(g));
      } catch (R) {
        throw y(R), R;
      }
    },
    cancel(S) {
      return y(S), c.return();
    }
  }, {
    highWaterMark: 2
  });
}, jn = 64 * 1024, { isFunction: ot } = m, Go = (({ Request: e, Response: n }) => ({
  Request: e,
  Response: n
}))(m.global), {
  ReadableStream: qn,
  TextEncoder: Hn
} = m.global, vn = (e, ...n) => {
  try {
    return !!e(...n);
  } catch {
    return !1;
  }
}, Zo = (e) => {
  e = m.merge.call({
    skipUndefined: !0
  }, Go, e);
  const { fetch: n, Request: s, Response: i } = e, c = n ? ot(n) : typeof fetch == "function", f = ot(s), l = ot(i);
  if (!c)
    return !1;
  const y = c && ot(qn), S = c && (typeof Hn == "function" ? /* @__PURE__ */ ((E) => (_) => E.encode(_))(new Hn()) : async (E) => new Uint8Array(await new s(E).arrayBuffer())), R = f && y && vn(() => {
    let E = !1;
    const _ = new s(X.origin, {
      body: new qn(),
      method: "POST",
      get duplex() {
        return E = !0, "half";
      }
    }).headers.has("Content-Type");
    return E && !_;
  }), g = l && y && vn(() => m.isReadableStream(new i("").body)), b = {
    stream: g && ((E) => E.body)
  };
  c && ["text", "arrayBuffer", "blob", "formData", "stream"].forEach((E) => {
    !b[E] && (b[E] = (_, x) => {
      let I = _ && _[E];
      if (I)
        return I.call(_);
      throw new B(`Response type '${E}' is not supported`, B.ERR_NOT_SUPPORT, x);
    });
  });
  const C = async (E) => {
    if (E == null)
      return 0;
    if (m.isBlob(E))
      return E.size;
    if (m.isSpecCompliantForm(E))
      return (await new s(X.origin, {
        method: "POST",
        body: E
      }).arrayBuffer()).byteLength;
    if (m.isArrayBufferView(E) || m.isArrayBuffer(E))
      return E.byteLength;
    if (m.isURLSearchParams(E) && (E = E + ""), m.isString(E))
      return (await S(E)).byteLength;
  }, k = async (E, _) => {
    const x = m.toFiniteNumber(E.getContentLength());
    return x ?? C(_);
  };
  return async (E) => {
    let {
      url: _,
      method: x,
      data: I,
      signal: M,
      cancelToken: q,
      timeout: z,
      onDownloadProgress: ee,
      onUploadProgress: le,
      responseType: Q,
      headers: Fe,
      withCredentials: Se = "same-origin",
      fetchOptions: Me
    } = dr(E), ze = n || fetch;
    Q = Q ? (Q + "").toLowerCase() : "text";
    let Oe = Vo([M, q && q.toAbortSignal()], z), me = null;
    const fe = Oe && Oe.unsubscribe && (() => {
      Oe.unsubscribe();
    });
    let We;
    try {
      if (le && R && x !== "get" && x !== "head" && (We = await k(Fe, I)) !== 0) {
        let ie = new s(_, {
          method: "POST",
          body: I,
          duplex: "half"
        }), de;
        if (m.isFormData(I) && (de = ie.headers.get("content-type")) && Fe.setContentType(de), ie.body) {
          const [Be, Te] = Ln(
            We,
            lt(Bn(le))
          );
          I = In(ie.body, jn, Be, Te);
        }
      }
      m.isString(Se) || (Se = Se ? "include" : "omit");
      const W = f && "credentials" in s.prototype, Ue = {
        ...Me,
        signal: Oe,
        method: x.toUpperCase(),
        headers: Fe.normalize().toJSON(),
        body: I,
        duplex: "half",
        credentials: W ? Se : void 0
      };
      me = f && new s(_, Ue);
      let J = await (f ? ze(me, Me) : ze(_, Ue));
      const ke = g && (Q === "stream" || Q === "response");
      if (g && (ee || ke && fe)) {
        const ie = {};
        ["status", "statusText", "headers"].forEach((Je) => {
          ie[Je] = J[Je];
        });
        const de = m.toFiniteNumber(J.headers.get("content-length")), [Be, Te] = ee && Ln(
          de,
          lt(Bn(ee), !0)
        ) || [];
        J = new i(
          In(J.body, jn, Be, () => {
            Te && Te(), fe && fe();
          }),
          ie
        );
      }
      Q = Q || "text";
      let Le = await b[m.findKey(b, Q) || "text"](J, E);
      return !ke && fe && fe(), await new Promise((ie, de) => {
        lr(ie, de, {
          data: Le,
          headers: Y.from(J.headers),
          status: J.status,
          statusText: J.statusText,
          config: E,
          request: me
        });
      });
    } catch (W) {
      throw fe && fe(), W && W.name === "TypeError" && /Load failed|fetch/i.test(W.message) ? Object.assign(
        new B("Network Error", B.ERR_NETWORK, E, me),
        {
          cause: W.cause || W
        }
      ) : B.from(W, W && W.code, E, me);
    }
  };
}, Yo = /* @__PURE__ */ new Map(), hr = (e) => {
  let n = e && e.env || {};
  const { fetch: s, Request: i, Response: c } = n, f = [
    i,
    c,
    s
  ];
  let l = f.length, y = l, S, R, g = Yo;
  for (; y--; )
    S = f[y], R = g.get(S), R === void 0 && g.set(S, R = y ? /* @__PURE__ */ new Map() : Zo(n)), g = R;
  return R;
};
hr();
const vt = {
  http: yo,
  xhr: Jo,
  fetch: {
    get: hr
  }
};
m.forEach(vt, (e, n) => {
  if (e) {
    try {
      Object.defineProperty(e, "name", { value: n });
    } catch {
    }
    Object.defineProperty(e, "adapterName", { value: n });
  }
});
const $n = (e) => `- ${e}`, ei = (e) => m.isFunction(e) || e === null || e === !1;
function ti(e, n) {
  e = m.isArray(e) ? e : [e];
  const { length: s } = e;
  let i, c;
  const f = {};
  for (let l = 0; l < s; l++) {
    i = e[l];
    let y;
    if (c = i, !ei(i) && (c = vt[(y = String(i)).toLowerCase()], c === void 0))
      throw new B(`Unknown adapter '${y}'`);
    if (c && (m.isFunction(c) || (c = c.get(n))))
      break;
    f[y || "#" + l] = c;
  }
  if (!c) {
    const l = Object.entries(f).map(
      ([S, R]) => `adapter ${S} ` + (R === !1 ? "is not supported by the environment" : "is not available in the build")
    );
    let y = s ? l.length > 1 ? `since :
` + l.map($n).join(`
`) : " " + $n(l[0]) : "as no adapter specified";
    throw new B(
      "There is no suitable adapter to dispatch the request " + y,
      "ERR_NOT_SUPPORT"
    );
  }
  return c;
}
const pr = {
  /**
   * Resolve an adapter from a list of adapter names or functions.
   * @type {Function}
   */
  getAdapter: ti,
  /**
   * Exposes all known adapters
   * @type {Object<string, Function|Object>}
   */
  adapters: vt
};
function Ut(e) {
  if (e.cancelToken && e.cancelToken.throwIfRequested(), e.signal && e.signal.aborted)
    throw new _e(null, e);
}
function Mn(e) {
  return Ut(e), e.headers = Y.from(e.headers), e.data = Ft.call(
    e,
    e.transformRequest
  ), ["post", "put", "patch"].indexOf(e.method) !== -1 && e.headers.setContentType("application/x-www-form-urlencoded", !1), pr.getAdapter(e.adapter || $e.adapter, e)(e).then(function(i) {
    return Ut(e), i.data = Ft.call(
      e,
      e.transformResponse,
      i
    ), i.headers = Y.from(i.headers), i;
  }, function(i) {
    return ur(i) || (Ut(e), i && i.response && (i.response.data = Ft.call(
      e,
      e.transformResponse,
      i.response
    ), i.response.headers = Y.from(i.response.headers))), Promise.reject(i);
  });
}
const mr = "1.13.2", mt = {};
["object", "boolean", "number", "function", "string", "symbol"].forEach((e, n) => {
  mt[e] = function(i) {
    return typeof i === e || "a" + (n < 1 ? "n " : " ") + e;
  };
});
const zn = {};
mt.transitional = function(n, s, i) {
  function c(f, l) {
    return "[Axios v" + mr + "] Transitional option '" + f + "'" + l + (i ? ". " + i : "");
  }
  return (f, l, y) => {
    if (n === !1)
      throw new B(
        c(l, " has been removed" + (s ? " in " + s : "")),
        B.ERR_DEPRECATED
      );
    return s && !zn[l] && (zn[l] = !0, console.warn(
      c(
        l,
        " has been deprecated since v" + s + " and will be removed in the near future"
      )
    )), n ? n(f, l, y) : !0;
  };
};
mt.spelling = function(n) {
  return (s, i) => (console.warn(`${i} is likely a misspelling of ${n}`), !0);
};
function ni(e, n, s) {
  if (typeof e != "object")
    throw new B("options must be an object", B.ERR_BAD_OPTION_VALUE);
  const i = Object.keys(e);
  let c = i.length;
  for (; c-- > 0; ) {
    const f = i[c], l = n[f];
    if (l) {
      const y = e[f], S = y === void 0 || l(y, f, e);
      if (S !== !0)
        throw new B("option " + f + " must be " + S, B.ERR_BAD_OPTION_VALUE);
      continue;
    }
    if (s !== !0)
      throw new B("Unknown option " + f, B.ERR_BAD_OPTION);
  }
}
const ct = {
  assertOptions: ni,
  validators: mt
}, ue = ct.validators;
let ge = class {
  constructor(n) {
    this.defaults = n || {}, this.interceptors = {
      request: new Un(),
      response: new Un()
    };
  }
  /**
   * Dispatch a request
   *
   * @param {String|Object} configOrUrl The config specific for this request (merged with this.defaults)
   * @param {?Object} config
   *
   * @returns {Promise} The Promise to be fulfilled
   */
  async request(n, s) {
    try {
      return await this._request(n, s);
    } catch (i) {
      if (i instanceof Error) {
        let c = {};
        Error.captureStackTrace ? Error.captureStackTrace(c) : c = new Error();
        const f = c.stack ? c.stack.replace(/^.+\n/, "") : "";
        try {
          i.stack ? f && !String(i.stack).endsWith(f.replace(/^.+\n.+\n/, "")) && (i.stack += `
` + f) : i.stack = f;
        } catch {
        }
      }
      throw i;
    }
  }
  _request(n, s) {
    typeof n == "string" ? (s = s || {}, s.url = n) : s = n || {}, s = Ee(this.defaults, s);
    const { transitional: i, paramsSerializer: c, headers: f } = s;
    i !== void 0 && ct.assertOptions(i, {
      silentJSONParsing: ue.transitional(ue.boolean),
      forcedJSONParsing: ue.transitional(ue.boolean),
      clarifyTimeoutError: ue.transitional(ue.boolean)
    }, !1), c != null && (m.isFunction(c) ? s.paramsSerializer = {
      serialize: c
    } : ct.assertOptions(c, {
      encode: ue.function,
      serialize: ue.function
    }, !0)), s.allowAbsoluteUrls !== void 0 || (this.defaults.allowAbsoluteUrls !== void 0 ? s.allowAbsoluteUrls = this.defaults.allowAbsoluteUrls : s.allowAbsoluteUrls = !0), ct.assertOptions(s, {
      baseUrl: ue.spelling("baseURL"),
      withXsrfToken: ue.spelling("withXSRFToken")
    }, !0), s.method = (s.method || this.defaults.method || "get").toLowerCase();
    let l = f && m.merge(
      f.common,
      f[s.method]
    );
    f && m.forEach(
      ["delete", "get", "head", "post", "put", "patch", "common"],
      (E) => {
        delete f[E];
      }
    ), s.headers = Y.concat(l, f);
    const y = [];
    let S = !0;
    this.interceptors.request.forEach(function(_) {
      typeof _.runWhen == "function" && _.runWhen(s) === !1 || (S = S && _.synchronous, y.unshift(_.fulfilled, _.rejected));
    });
    const R = [];
    this.interceptors.response.forEach(function(_) {
      R.push(_.fulfilled, _.rejected);
    });
    let g, b = 0, C;
    if (!S) {
      const E = [Mn.bind(this), void 0];
      for (E.unshift(...y), E.push(...R), C = E.length, g = Promise.resolve(s); b < C; )
        g = g.then(E[b++], E[b++]);
      return g;
    }
    C = y.length;
    let k = s;
    for (; b < C; ) {
      const E = y[b++], _ = y[b++];
      try {
        k = E(k);
      } catch (x) {
        _.call(this, x);
        break;
      }
    }
    try {
      g = Mn.call(this, k);
    } catch (E) {
      return Promise.reject(E);
    }
    for (b = 0, C = R.length; b < C; )
      g = g.then(R[b++], R[b++]);
    return g;
  }
  getUri(n) {
    n = Ee(this.defaults, n);
    const s = fr(n.baseURL, n.url, n.allowAbsoluteUrls);
    return ir(s, n.params, n.paramsSerializer);
  }
};
m.forEach(["delete", "get", "head", "options"], function(n) {
  ge.prototype[n] = function(s, i) {
    return this.request(Ee(i || {}, {
      method: n,
      url: s,
      data: (i || {}).data
    }));
  };
});
m.forEach(["post", "put", "patch"], function(n) {
  function s(i) {
    return function(f, l, y) {
      return this.request(Ee(y || {}, {
        method: n,
        headers: i ? {
          "Content-Type": "multipart/form-data"
        } : {},
        url: f,
        data: l
      }));
    };
  }
  ge.prototype[n] = s(), ge.prototype[n + "Form"] = s(!0);
});
let ri = class yr {
  constructor(n) {
    if (typeof n != "function")
      throw new TypeError("executor must be a function.");
    let s;
    this.promise = new Promise(function(f) {
      s = f;
    });
    const i = this;
    this.promise.then((c) => {
      if (!i._listeners) return;
      let f = i._listeners.length;
      for (; f-- > 0; )
        i._listeners[f](c);
      i._listeners = null;
    }), this.promise.then = (c) => {
      let f;
      const l = new Promise((y) => {
        i.subscribe(y), f = y;
      }).then(c);
      return l.cancel = function() {
        i.unsubscribe(f);
      }, l;
    }, n(function(f, l, y) {
      i.reason || (i.reason = new _e(f, l, y), s(i.reason));
    });
  }
  /**
   * Throws a `CanceledError` if cancellation has been requested.
   */
  throwIfRequested() {
    if (this.reason)
      throw this.reason;
  }
  /**
   * Subscribe to the cancel signal
   */
  subscribe(n) {
    if (this.reason) {
      n(this.reason);
      return;
    }
    this._listeners ? this._listeners.push(n) : this._listeners = [n];
  }
  /**
   * Unsubscribe from the cancel signal
   */
  unsubscribe(n) {
    if (!this._listeners)
      return;
    const s = this._listeners.indexOf(n);
    s !== -1 && this._listeners.splice(s, 1);
  }
  toAbortSignal() {
    const n = new AbortController(), s = (i) => {
      n.abort(i);
    };
    return this.subscribe(s), n.signal.unsubscribe = () => this.unsubscribe(s), n.signal;
  }
  /**
   * Returns an object that contains a new `CancelToken` and a function that, when called,
   * cancels the `CancelToken`.
   */
  static source() {
    let n;
    return {
      token: new yr(function(c) {
        n = c;
      }),
      cancel: n
    };
  }
};
function si(e) {
  return function(s) {
    return e.apply(null, s);
  };
}
function oi(e) {
  return m.isObject(e) && e.isAxiosError === !0;
}
const It = {
  Continue: 100,
  SwitchingProtocols: 101,
  Processing: 102,
  EarlyHints: 103,
  Ok: 200,
  Created: 201,
  Accepted: 202,
  NonAuthoritativeInformation: 203,
  NoContent: 204,
  ResetContent: 205,
  PartialContent: 206,
  MultiStatus: 207,
  AlreadyReported: 208,
  ImUsed: 226,
  MultipleChoices: 300,
  MovedPermanently: 301,
  Found: 302,
  SeeOther: 303,
  NotModified: 304,
  UseProxy: 305,
  Unused: 306,
  TemporaryRedirect: 307,
  PermanentRedirect: 308,
  BadRequest: 400,
  Unauthorized: 401,
  PaymentRequired: 402,
  Forbidden: 403,
  NotFound: 404,
  MethodNotAllowed: 405,
  NotAcceptable: 406,
  ProxyAuthenticationRequired: 407,
  RequestTimeout: 408,
  Conflict: 409,
  Gone: 410,
  LengthRequired: 411,
  PreconditionFailed: 412,
  PayloadTooLarge: 413,
  UriTooLong: 414,
  UnsupportedMediaType: 415,
  RangeNotSatisfiable: 416,
  ExpectationFailed: 417,
  ImATeapot: 418,
  MisdirectedRequest: 421,
  UnprocessableEntity: 422,
  Locked: 423,
  FailedDependency: 424,
  TooEarly: 425,
  UpgradeRequired: 426,
  PreconditionRequired: 428,
  TooManyRequests: 429,
  RequestHeaderFieldsTooLarge: 431,
  UnavailableForLegalReasons: 451,
  InternalServerError: 500,
  NotImplemented: 501,
  BadGateway: 502,
  ServiceUnavailable: 503,
  GatewayTimeout: 504,
  HttpVersionNotSupported: 505,
  VariantAlsoNegotiates: 506,
  InsufficientStorage: 507,
  LoopDetected: 508,
  NotExtended: 510,
  NetworkAuthenticationRequired: 511,
  WebServerIsDown: 521,
  ConnectionTimedOut: 522,
  OriginIsUnreachable: 523,
  TimeoutOccurred: 524,
  SslHandshakeFailed: 525,
  InvalidSslCertificate: 526
};
Object.entries(It).forEach(([e, n]) => {
  It[n] = e;
});
function wr(e) {
  const n = new ge(e), s = Kn(ge.prototype.request, n);
  return m.extend(s, ge.prototype, n, { allOwnKeys: !0 }), m.extend(s, n, null, { allOwnKeys: !0 }), s.create = function(c) {
    return wr(Ee(e, c));
  }, s;
}
const j = wr($e);
j.Axios = ge;
j.CanceledError = _e;
j.CancelToken = ri;
j.isCancel = ur;
j.VERSION = mr;
j.toFormData = pt;
j.AxiosError = B;
j.Cancel = j.CanceledError;
j.all = function(n) {
  return Promise.all(n);
};
j.spread = si;
j.isAxiosError = oi;
j.mergeConfig = Ee;
j.AxiosHeaders = Y;
j.formToJSON = (e) => cr(m.isHTMLForm(e) ? new FormData(e) : e);
j.getAdapter = pr.getAdapter;
j.HttpStatusCode = It;
j.default = j;
const {
  Axios: Si,
  AxiosError: Oi,
  CanceledError: Ti,
  isCancel: Ai,
  CancelToken: Ci,
  VERSION: xi,
  all: Pi,
  Cancel: Ni,
  isAxiosError: _i,
  spread: Fi,
  toFormData: Ui,
  AxiosHeaders: ki,
  HttpStatusCode: Li,
  formToJSON: Bi,
  getAdapter: Di,
  mergeConfig: Ii
} = j;
var ii = typeof globalThis < "u" ? globalThis : typeof window < "u" ? window : typeof global < "u" ? global : typeof self < "u" ? self : {};
function ai(e) {
  return e && e.__esModule && Object.prototype.hasOwnProperty.call(e, "default") ? e.default : e;
}
var ut = { exports: {} };
var kt, Wn;
function ci() {
  if (Wn) return kt;
  Wn = 1;
  function e(t, r) {
    return function() {
      return t.apply(r, arguments);
    };
  }
  const { toString: n } = Object.prototype, { getPrototypeOf: s } = Object, { iterator: i, toStringTag: c } = Symbol, f = /* @__PURE__ */ ((t) => (r) => {
    const o = n.call(r);
    return t[o] || (t[o] = o.slice(8, -1).toLowerCase());
  })(/* @__PURE__ */ Object.create(null)), l = (t) => (t = t.toLowerCase(), (r) => f(r) === t), y = (t) => (r) => typeof r === t, { isArray: S } = Array, R = y("undefined");
  function g(t) {
    return t !== null && !R(t) && t.constructor !== null && !R(t.constructor) && E(t.constructor.isBuffer) && t.constructor.isBuffer(t);
  }
  const b = l("ArrayBuffer");
  function C(t) {
    let r;
    return typeof ArrayBuffer < "u" && ArrayBuffer.isView ? r = ArrayBuffer.isView(t) : r = t && t.buffer && b(t.buffer), r;
  }
  const k = y("string"), E = y("function"), _ = y("number"), x = (t) => t !== null && typeof t == "object", I = (t) => t === !0 || t === !1, M = (t) => {
    if (f(t) !== "object")
      return !1;
    const r = s(t);
    return (r === null || r === Object.prototype || Object.getPrototypeOf(r) === null) && !(c in t) && !(i in t);
  }, q = (t) => {
    if (!x(t) || g(t))
      return !1;
    try {
      return Object.keys(t).length === 0 && Object.getPrototypeOf(t) === Object.prototype;
    } catch {
      return !1;
    }
  }, z = l("Date"), ee = l("File"), le = l("Blob"), Q = l("FileList"), Fe = (t) => x(t) && E(t.pipe), Se = (t) => {
    let r;
    return t && (typeof FormData == "function" && t instanceof FormData || E(t.append) && ((r = f(t)) === "formdata" || // detect form-data instance
    r === "object" && E(t.toString) && t.toString() === "[object FormData]"));
  }, Me = l("URLSearchParams"), [ze, Oe, me, fe] = ["ReadableStream", "Request", "Response", "Headers"].map(l), We = (t) => t.trim ? t.trim() : t.replace(/^[\s\uFEFF\xA0]+|[\s\uFEFF\xA0]+$/g, "");
  function W(t, r, { allOwnKeys: o = !1 } = {}) {
    if (t === null || typeof t > "u")
      return;
    let a, u;
    if (typeof t != "object" && (t = [t]), S(t))
      for (a = 0, u = t.length; a < u; a++)
        r.call(null, t[a], a, t);
    else {
      if (g(t))
        return;
      const h = o ? Object.getOwnPropertyNames(t) : Object.keys(t), d = h.length;
      let w;
      for (a = 0; a < d; a++)
        w = h[a], r.call(null, t[w], w, t);
    }
  }
  function Ue(t, r) {
    if (g(t))
      return null;
    r = r.toLowerCase();
    const o = Object.keys(t);
    let a = o.length, u;
    for (; a-- > 0; )
      if (u = o[a], r === u.toLowerCase())
        return u;
    return null;
  }
  const J = typeof globalThis < "u" ? globalThis : typeof self < "u" ? self : typeof window < "u" ? window : ii, ke = (t) => !R(t) && t !== J;
  function Le() {
    const { caseless: t, skipUndefined: r } = ke(this) && this || {}, o = {}, a = (u, h) => {
      const d = t && Ue(o, h) || h;
      M(o[d]) && M(u) ? o[d] = Le(o[d], u) : M(u) ? o[d] = Le({}, u) : S(u) ? o[d] = u.slice() : (!r || !R(u)) && (o[d] = u);
    };
    for (let u = 0, h = arguments.length; u < h; u++)
      arguments[u] && W(arguments[u], a);
    return o;
  }
  const ie = (t, r, o, { allOwnKeys: a } = {}) => (W(r, (u, h) => {
    o && E(u) ? t[h] = e(u, o) : t[h] = u;
  }, { allOwnKeys: a }), t), de = (t) => (t.charCodeAt(0) === 65279 && (t = t.slice(1)), t), Be = (t, r, o, a) => {
    t.prototype = Object.create(r.prototype, a), t.prototype.constructor = t, Object.defineProperty(t, "super", {
      value: r.prototype
    }), o && Object.assign(t.prototype, o);
  }, Te = (t, r, o, a) => {
    let u, h, d;
    const w = {};
    if (r = r || {}, t == null) return r;
    do {
      for (u = Object.getOwnPropertyNames(t), h = u.length; h-- > 0; )
        d = u[h], (!a || a(d, t, r)) && !w[d] && (r[d] = t[d], w[d] = !0);
      t = o !== !1 && s(t);
    } while (t && (!o || o(t, r)) && t !== Object.prototype);
    return r;
  }, Je = (t, r, o) => {
    t = String(t), (o === void 0 || o > t.length) && (o = t.length), o -= r.length;
    const a = t.indexOf(r, o);
    return a !== -1 && a === o;
  }, Sr = (t) => {
    if (!t) return null;
    if (S(t)) return t;
    let r = t.length;
    if (!_(r)) return null;
    const o = new Array(r);
    for (; r-- > 0; )
      o[r] = t[r];
    return o;
  }, Or = /* @__PURE__ */ ((t) => (r) => t && r instanceof t)(typeof Uint8Array < "u" && s(Uint8Array)), Tr = (t, r) => {
    const a = (t && t[i]).call(t);
    let u;
    for (; (u = a.next()) && !u.done; ) {
      const h = u.value;
      r.call(t, h[0], h[1]);
    }
  }, Ar = (t, r) => {
    let o;
    const a = [];
    for (; (o = t.exec(r)) !== null; )
      a.push(o);
    return a;
  }, Cr = l("HTMLFormElement"), xr = (t) => t.toLowerCase().replace(
    /[-_\s]([a-z\d])(\w*)/g,
    function(o, a, u) {
      return a.toUpperCase() + u;
    }
  ), Mt = (({ hasOwnProperty: t }) => (r, o) => t.call(r, o))(Object.prototype), Pr = l("RegExp"), zt = (t, r) => {
    const o = Object.getOwnPropertyDescriptors(t), a = {};
    W(o, (u, h) => {
      let d;
      (d = r(u, h, t)) !== !1 && (a[h] = d || u);
    }), Object.defineProperties(t, a);
  }, Nr = (t) => {
    zt(t, (r, o) => {
      if (E(t) && ["arguments", "caller", "callee"].indexOf(o) !== -1)
        return !1;
      const a = t[o];
      if (E(a)) {
        if (r.enumerable = !1, "writable" in r) {
          r.writable = !1;
          return;
        }
        r.set || (r.set = () => {
          throw Error("Can not rewrite read-only method '" + o + "'");
        });
      }
    });
  }, _r = (t, r) => {
    const o = {}, a = (u) => {
      u.forEach((h) => {
        o[h] = !0;
      });
    };
    return S(t) ? a(t) : a(String(t).split(r)), o;
  }, Fr = () => {
  }, Ur = (t, r) => t != null && Number.isFinite(t = +t) ? t : r;
  function kr(t) {
    return !!(t && E(t.append) && t[c] === "FormData" && t[i]);
  }
  const Lr = (t) => {
    const r = new Array(10), o = (a, u) => {
      if (x(a)) {
        if (r.indexOf(a) >= 0)
          return;
        if (g(a))
          return a;
        if (!("toJSON" in a)) {
          r[u] = a;
          const h = S(a) ? [] : {};
          return W(a, (d, w) => {
            const P = o(d, u + 1);
            !R(P) && (h[w] = P);
          }), r[u] = void 0, h;
        }
      }
      return a;
    };
    return o(t, 0);
  }, Br = l("AsyncFunction"), Dr = (t) => t && (x(t) || E(t)) && E(t.then) && E(t.catch), Wt = ((t, r) => t ? setImmediate : r ? ((o, a) => (J.addEventListener("message", ({ source: u, data: h }) => {
    u === J && h === o && a.length && a.shift()();
  }, !1), (u) => {
    a.push(u), J.postMessage(o, "*");
  }))(`axios@${Math.random()}`, []) : (o) => setTimeout(o))(
    typeof setImmediate == "function",
    E(J.postMessage)
  ), Ir = typeof queueMicrotask < "u" ? queueMicrotask.bind(J) : typeof process < "u" && process.nextTick || Wt;
  var p = {
    isArray: S,
    isArrayBuffer: b,
    isBuffer: g,
    isFormData: Se,
    isArrayBufferView: C,
    isString: k,
    isNumber: _,
    isBoolean: I,
    isObject: x,
    isPlainObject: M,
    isEmptyObject: q,
    isReadableStream: ze,
    isRequest: Oe,
    isResponse: me,
    isHeaders: fe,
    isUndefined: R,
    isDate: z,
    isFile: ee,
    isBlob: le,
    isRegExp: Pr,
    isFunction: E,
    isStream: Fe,
    isURLSearchParams: Me,
    isTypedArray: Or,
    isFileList: Q,
    forEach: W,
    merge: Le,
    extend: ie,
    trim: We,
    stripBOM: de,
    inherits: Be,
    toFlatObject: Te,
    kindOf: f,
    kindOfTest: l,
    endsWith: Je,
    toArray: Sr,
    forEachEntry: Tr,
    matchAll: Ar,
    isHTMLForm: Cr,
    hasOwnProperty: Mt,
    hasOwnProp: Mt,
    // an alias to avoid ESLint no-prototype-builtins detection
    reduceDescriptors: zt,
    freezeMethods: Nr,
    toObjectSet: _r,
    toCamelCase: xr,
    noop: Fr,
    toFiniteNumber: Ur,
    findKey: Ue,
    global: J,
    isContextDefined: ke,
    isSpecCompliantForm: kr,
    toJSONObject: Lr,
    isAsyncFn: Br,
    isThenable: Dr,
    setImmediate: Wt,
    asap: Ir,
    isIterable: (t) => t != null && E(t[i])
  };
  function L(t, r, o, a, u) {
    Error.call(this), Error.captureStackTrace ? Error.captureStackTrace(this, this.constructor) : this.stack = new Error().stack, this.message = t, this.name = "AxiosError", r && (this.code = r), o && (this.config = o), a && (this.request = a), u && (this.response = u, this.status = u.status ? u.status : null);
  }
  p.inherits(L, Error, {
    toJSON: function() {
      return {
        // Standard
        message: this.message,
        name: this.name,
        // Microsoft
        description: this.description,
        number: this.number,
        // Mozilla
        fileName: this.fileName,
        lineNumber: this.lineNumber,
        columnNumber: this.columnNumber,
        stack: this.stack,
        // Axios
        config: p.toJSONObject(this.config),
        code: this.code,
        status: this.status
      };
    }
  });
  const Jt = L.prototype, Vt = {};
  [
    "ERR_BAD_OPTION_VALUE",
    "ERR_BAD_OPTION",
    "ECONNABORTED",
    "ETIMEDOUT",
    "ERR_NETWORK",
    "ERR_FR_TOO_MANY_REDIRECTS",
    "ERR_DEPRECATED",
    "ERR_BAD_RESPONSE",
    "ERR_BAD_REQUEST",
    "ERR_CANCELED",
    "ERR_NOT_SUPPORT",
    "ERR_INVALID_URL"
    // eslint-disable-next-line func-names
  ].forEach((t) => {
    Vt[t] = { value: t };
  }), Object.defineProperties(L, Vt), Object.defineProperty(Jt, "isAxiosError", { value: !0 }), L.from = (t, r, o, a, u, h) => {
    const d = Object.create(Jt);
    p.toFlatObject(t, d, function(O) {
      return O !== Error.prototype;
    }, (A) => A !== "isAxiosError");
    const w = t && t.message ? t.message : "Error", P = r == null && t ? t.code : r;
    return L.call(d, w, P, o, a, u), t && d.cause == null && Object.defineProperty(d, "cause", { value: t, configurable: !0 }), d.name = t && t.name || "Error", h && Object.assign(d, h), d;
  };
  var jr = null;
  function yt(t) {
    return p.isPlainObject(t) || p.isArray(t);
  }
  function Kt(t) {
    return p.endsWith(t, "[]") ? t.slice(0, -2) : t;
  }
  function Xt(t, r, o) {
    return t ? t.concat(r).map(function(u, h) {
      return u = Kt(u), !o && h ? "[" + u + "]" : u;
    }).join(o ? "." : "") : r;
  }
  function qr(t) {
    return p.isArray(t) && !t.some(yt);
  }
  const Hr = p.toFlatObject(p, {}, null, function(r) {
    return /^is[A-Z]/.test(r);
  });
  function Ve(t, r, o) {
    if (!p.isObject(t))
      throw new TypeError("target must be an object");
    r = r || new FormData(), o = p.toFlatObject(o, {
      metaTokens: !0,
      dots: !1,
      indexes: !1
    }, !1, function(U, N) {
      return !p.isUndefined(N[U]);
    });
    const a = o.metaTokens, u = o.visitor || O, h = o.dots, d = o.indexes, P = (o.Blob || typeof Blob < "u" && Blob) && p.isSpecCompliantForm(r);
    if (!p.isFunction(u))
      throw new TypeError("visitor must be a function");
    function A(T) {
      if (T === null) return "";
      if (p.isDate(T))
        return T.toISOString();
      if (p.isBoolean(T))
        return T.toString();
      if (!P && p.isBlob(T))
        throw new L("Blob is not supported. Use a Buffer instead.");
      return p.isArrayBuffer(T) || p.isTypedArray(T) ? P && typeof Blob == "function" ? new Blob([T]) : Buffer.from(T) : T;
    }
    function O(T, U, N) {
      let v = T;
      if (T && !N && typeof T == "object") {
        if (p.endsWith(U, "{}"))
          U = a ? U : U.slice(0, -2), T = JSON.stringify(T);
        else if (p.isArray(T) && qr(T) || (p.isFileList(T) || p.endsWith(U, "[]")) && (v = p.toArray(T)))
          return U = Kt(U), v.forEach(function($, G) {
            !(p.isUndefined($) || $ === null) && r.append(
              // eslint-disable-next-line no-nested-ternary
              d === !0 ? Xt([U], G, h) : d === null ? U : U + "[]",
              A($)
            );
          }), !1;
      }
      return yt(T) ? !0 : (r.append(Xt(N, U, h), A(T)), !1);
    }
    const F = [], D = Object.assign(Hr, {
      defaultVisitor: O,
      convertValue: A,
      isVisitable: yt
    });
    function K(T, U) {
      if (!p.isUndefined(T)) {
        if (F.indexOf(T) !== -1)
          throw Error("Circular reference detected in " + U.join("."));
        F.push(T), p.forEach(T, function(v, te) {
          (!(p.isUndefined(v) || v === null) && u.call(
            r,
            v,
            p.isString(te) ? te.trim() : te,
            U,
            D
          )) === !0 && K(v, U ? U.concat(te) : [te]);
        }), F.pop();
      }
    }
    if (!p.isObject(t))
      throw new TypeError("data must be an object");
    return K(t), r;
  }
  function Qt(t) {
    const r = {
      "!": "%21",
      "'": "%27",
      "(": "%28",
      ")": "%29",
      "~": "%7E",
      "%20": "+",
      "%00": "\0"
    };
    return encodeURIComponent(t).replace(/[!'()~]|%20|%00/g, function(a) {
      return r[a];
    });
  }
  function wt(t, r) {
    this._pairs = [], t && Ve(t, this, r);
  }
  const Gt = wt.prototype;
  Gt.append = function(r, o) {
    this._pairs.push([r, o]);
  }, Gt.toString = function(r) {
    const o = r ? function(a) {
      return r.call(this, a, Qt);
    } : Qt;
    return this._pairs.map(function(u) {
      return o(u[0]) + "=" + o(u[1]);
    }, "").join("&");
  };
  function vr(t) {
    return encodeURIComponent(t).replace(/%3A/gi, ":").replace(/%24/g, "$").replace(/%2C/gi, ",").replace(/%20/g, "+");
  }
  function Zt(t, r, o) {
    if (!r)
      return t;
    const a = o && o.encode || vr;
    p.isFunction(o) && (o = {
      serialize: o
    });
    const u = o && o.serialize;
    let h;
    if (u ? h = u(r, o) : h = p.isURLSearchParams(r) ? r.toString() : new wt(r, o).toString(a), h) {
      const d = t.indexOf("#");
      d !== -1 && (t = t.slice(0, d)), t += (t.indexOf("?") === -1 ? "?" : "&") + h;
    }
    return t;
  }
  class $r {
    constructor() {
      this.handlers = [];
    }
    /**
     * Add a new interceptor to the stack
     *
     * @param {Function} fulfilled The function to handle `then` for a `Promise`
     * @param {Function} rejected The function to handle `reject` for a `Promise`
     *
     * @return {Number} An ID used to remove interceptor later
     */
    use(r, o, a) {
      return this.handlers.push({
        fulfilled: r,
        rejected: o,
        synchronous: a ? a.synchronous : !1,
        runWhen: a ? a.runWhen : null
      }), this.handlers.length - 1;
    }
    /**
     * Remove an interceptor from the stack
     *
     * @param {Number} id The ID that was returned by `use`
     *
     * @returns {void}
     */
    eject(r) {
      this.handlers[r] && (this.handlers[r] = null);
    }
    /**
     * Clear all interceptors from the stack
     *
     * @returns {void}
     */
    clear() {
      this.handlers && (this.handlers = []);
    }
    /**
     * Iterate over all the registered interceptors
     *
     * This method is particularly useful for skipping over any
     * interceptors that may have become `null` calling `eject`.
     *
     * @param {Function} fn The function to call for each interceptor
     *
     * @returns {void}
     */
    forEach(r) {
      p.forEach(this.handlers, function(a) {
        a !== null && r(a);
      });
    }
  }
  var Yt = $r, en = {
    silentJSONParsing: !0,
    forcedJSONParsing: !0,
    clarifyTimeoutError: !1
  }, Mr = typeof URLSearchParams < "u" ? URLSearchParams : wt, zr = typeof FormData < "u" ? FormData : null, Wr = typeof Blob < "u" ? Blob : null, Jr = {
    isBrowser: !0,
    classes: {
      URLSearchParams: Mr,
      FormData: zr,
      Blob: Wr
    },
    protocols: ["http", "https", "file", "blob", "url", "data"]
  };
  const bt = typeof window < "u" && typeof document < "u", Rt = typeof navigator == "object" && navigator || void 0, Vr = bt && (!Rt || ["ReactNative", "NativeScript", "NS"].indexOf(Rt.product) < 0), Kr = typeof WorkerGlobalScope < "u" && // eslint-disable-next-line no-undef
  self instanceof WorkerGlobalScope && typeof self.importScripts == "function", Xr = bt && window.location.href || "http://localhost";
  var Qr = /* @__PURE__ */ Object.freeze({
    __proto__: null,
    hasBrowserEnv: bt,
    hasStandardBrowserWebWorkerEnv: Kr,
    hasStandardBrowserEnv: Vr,
    navigator: Rt,
    origin: Xr
  }), V = {
    ...Qr,
    ...Jr
  };
  function Gr(t, r) {
    return Ve(t, new V.classes.URLSearchParams(), {
      visitor: function(o, a, u, h) {
        return V.isNode && p.isBuffer(o) ? (this.append(a, o.toString("base64")), !1) : h.defaultVisitor.apply(this, arguments);
      },
      ...r
    });
  }
  function Zr(t) {
    return p.matchAll(/\w+|\[(\w*)]/g, t).map((r) => r[0] === "[]" ? "" : r[1] || r[0]);
  }
  function Yr(t) {
    const r = {}, o = Object.keys(t);
    let a;
    const u = o.length;
    let h;
    for (a = 0; a < u; a++)
      h = o[a], r[h] = t[h];
    return r;
  }
  function tn(t) {
    function r(o, a, u, h) {
      let d = o[h++];
      if (d === "__proto__") return !0;
      const w = Number.isFinite(+d), P = h >= o.length;
      return d = !d && p.isArray(u) ? u.length : d, P ? (p.hasOwnProp(u, d) ? u[d] = [u[d], a] : u[d] = a, !w) : ((!u[d] || !p.isObject(u[d])) && (u[d] = []), r(o, a, u[d], h) && p.isArray(u[d]) && (u[d] = Yr(u[d])), !w);
    }
    if (p.isFormData(t) && p.isFunction(t.entries)) {
      const o = {};
      return p.forEachEntry(t, (a, u) => {
        r(Zr(a), u, o, 0);
      }), o;
    }
    return null;
  }
  function es(t, r, o) {
    if (p.isString(t))
      try {
        return (r || JSON.parse)(t), p.trim(t);
      } catch (a) {
        if (a.name !== "SyntaxError")
          throw a;
      }
    return (o || JSON.stringify)(t);
  }
  const gt = {
    transitional: en,
    adapter: ["xhr", "http", "fetch"],
    transformRequest: [function(r, o) {
      const a = o.getContentType() || "", u = a.indexOf("application/json") > -1, h = p.isObject(r);
      if (h && p.isHTMLForm(r) && (r = new FormData(r)), p.isFormData(r))
        return u ? JSON.stringify(tn(r)) : r;
      if (p.isArrayBuffer(r) || p.isBuffer(r) || p.isStream(r) || p.isFile(r) || p.isBlob(r) || p.isReadableStream(r))
        return r;
      if (p.isArrayBufferView(r))
        return r.buffer;
      if (p.isURLSearchParams(r))
        return o.setContentType("application/x-www-form-urlencoded;charset=utf-8", !1), r.toString();
      let w;
      if (h) {
        if (a.indexOf("application/x-www-form-urlencoded") > -1)
          return Gr(r, this.formSerializer).toString();
        if ((w = p.isFileList(r)) || a.indexOf("multipart/form-data") > -1) {
          const P = this.env && this.env.FormData;
          return Ve(
            w ? { "files[]": r } : r,
            P && new P(),
            this.formSerializer
          );
        }
      }
      return h || u ? (o.setContentType("application/json", !1), es(r)) : r;
    }],
    transformResponse: [function(r) {
      const o = this.transitional || gt.transitional, a = o && o.forcedJSONParsing, u = this.responseType === "json";
      if (p.isResponse(r) || p.isReadableStream(r))
        return r;
      if (r && p.isString(r) && (a && !this.responseType || u)) {
        const d = !(o && o.silentJSONParsing) && u;
        try {
          return JSON.parse(r, this.parseReviver);
        } catch (w) {
          if (d)
            throw w.name === "SyntaxError" ? L.from(w, L.ERR_BAD_RESPONSE, this, null, this.response) : w;
        }
      }
      return r;
    }],
    /**
     * A timeout in milliseconds to abort a request. If set to 0 (default) a
     * timeout is not created.
     */
    timeout: 0,
    xsrfCookieName: "XSRF-TOKEN",
    xsrfHeaderName: "X-XSRF-TOKEN",
    maxContentLength: -1,
    maxBodyLength: -1,
    env: {
      FormData: V.classes.FormData,
      Blob: V.classes.Blob
    },
    validateStatus: function(r) {
      return r >= 200 && r < 300;
    },
    headers: {
      common: {
        Accept: "application/json, text/plain, */*",
        "Content-Type": void 0
      }
    }
  };
  p.forEach(["delete", "get", "head", "post", "put", "patch"], (t) => {
    gt.headers[t] = {};
  });
  var Et = gt;
  const ts = p.toObjectSet([
    "age",
    "authorization",
    "content-length",
    "content-type",
    "etag",
    "expires",
    "from",
    "host",
    "if-modified-since",
    "if-unmodified-since",
    "last-modified",
    "location",
    "max-forwards",
    "proxy-authorization",
    "referer",
    "retry-after",
    "user-agent"
  ]);
  var ns = (t) => {
    const r = {};
    let o, a, u;
    return t && t.split(`
`).forEach(function(d) {
      u = d.indexOf(":"), o = d.substring(0, u).trim().toLowerCase(), a = d.substring(u + 1).trim(), !(!o || r[o] && ts[o]) && (o === "set-cookie" ? r[o] ? r[o].push(a) : r[o] = [a] : r[o] = r[o] ? r[o] + ", " + a : a);
    }), r;
  };
  const nn = Symbol("internals");
  function De(t) {
    return t && String(t).trim().toLowerCase();
  }
  function Ke(t) {
    return t === !1 || t == null ? t : p.isArray(t) ? t.map(Ke) : String(t);
  }
  function rs(t) {
    const r = /* @__PURE__ */ Object.create(null), o = /([^\s,;=]+)\s*(?:=\s*([^,;]+))?/g;
    let a;
    for (; a = o.exec(t); )
      r[a[1]] = a[2];
    return r;
  }
  const ss = (t) => /^[-_a-zA-Z0-9^`|~,!#$%&'*+.]+$/.test(t.trim());
  function St(t, r, o, a, u) {
    if (p.isFunction(a))
      return a.call(this, r, o);
    if (u && (r = o), !!p.isString(r)) {
      if (p.isString(a))
        return r.indexOf(a) !== -1;
      if (p.isRegExp(a))
        return a.test(r);
    }
  }
  function os(t) {
    return t.trim().toLowerCase().replace(/([a-z\d])(\w*)/g, (r, o, a) => o.toUpperCase() + a);
  }
  function is(t, r) {
    const o = p.toCamelCase(" " + r);
    ["get", "set", "has"].forEach((a) => {
      Object.defineProperty(t, a + o, {
        value: function(u, h, d) {
          return this[a].call(this, r, u, h, d);
        },
        configurable: !0
      });
    });
  }
  class Xe {
    constructor(r) {
      r && this.set(r);
    }
    set(r, o, a) {
      const u = this;
      function h(w, P, A) {
        const O = De(P);
        if (!O)
          throw new Error("header name must be a non-empty string");
        const F = p.findKey(u, O);
        (!F || u[F] === void 0 || A === !0 || A === void 0 && u[F] !== !1) && (u[F || P] = Ke(w));
      }
      const d = (w, P) => p.forEach(w, (A, O) => h(A, O, P));
      if (p.isPlainObject(r) || r instanceof this.constructor)
        d(r, o);
      else if (p.isString(r) && (r = r.trim()) && !ss(r))
        d(ns(r), o);
      else if (p.isObject(r) && p.isIterable(r)) {
        let w = {}, P, A;
        for (const O of r) {
          if (!p.isArray(O))
            throw TypeError("Object iterator must return a key-value pair");
          w[A = O[0]] = (P = w[A]) ? p.isArray(P) ? [...P, O[1]] : [P, O[1]] : O[1];
        }
        d(w, o);
      } else
        r != null && h(o, r, a);
      return this;
    }
    get(r, o) {
      if (r = De(r), r) {
        const a = p.findKey(this, r);
        if (a) {
          const u = this[a];
          if (!o)
            return u;
          if (o === !0)
            return rs(u);
          if (p.isFunction(o))
            return o.call(this, u, a);
          if (p.isRegExp(o))
            return o.exec(u);
          throw new TypeError("parser must be boolean|regexp|function");
        }
      }
    }
    has(r, o) {
      if (r = De(r), r) {
        const a = p.findKey(this, r);
        return !!(a && this[a] !== void 0 && (!o || St(this, this[a], a, o)));
      }
      return !1;
    }
    delete(r, o) {
      const a = this;
      let u = !1;
      function h(d) {
        if (d = De(d), d) {
          const w = p.findKey(a, d);
          w && (!o || St(a, a[w], w, o)) && (delete a[w], u = !0);
        }
      }
      return p.isArray(r) ? r.forEach(h) : h(r), u;
    }
    clear(r) {
      const o = Object.keys(this);
      let a = o.length, u = !1;
      for (; a--; ) {
        const h = o[a];
        (!r || St(this, this[h], h, r, !0)) && (delete this[h], u = !0);
      }
      return u;
    }
    normalize(r) {
      const o = this, a = {};
      return p.forEach(this, (u, h) => {
        const d = p.findKey(a, h);
        if (d) {
          o[d] = Ke(u), delete o[h];
          return;
        }
        const w = r ? os(h) : String(h).trim();
        w !== h && delete o[h], o[w] = Ke(u), a[w] = !0;
      }), this;
    }
    concat(...r) {
      return this.constructor.concat(this, ...r);
    }
    toJSON(r) {
      const o = /* @__PURE__ */ Object.create(null);
      return p.forEach(this, (a, u) => {
        a != null && a !== !1 && (o[u] = r && p.isArray(a) ? a.join(", ") : a);
      }), o;
    }
    [Symbol.iterator]() {
      return Object.entries(this.toJSON())[Symbol.iterator]();
    }
    toString() {
      return Object.entries(this.toJSON()).map(([r, o]) => r + ": " + o).join(`
`);
    }
    getSetCookie() {
      return this.get("set-cookie") || [];
    }
    get [Symbol.toStringTag]() {
      return "AxiosHeaders";
    }
    static from(r) {
      return r instanceof this ? r : new this(r);
    }
    static concat(r, ...o) {
      const a = new this(r);
      return o.forEach((u) => a.set(u)), a;
    }
    static accessor(r) {
      const a = (this[nn] = this[nn] = {
        accessors: {}
      }).accessors, u = this.prototype;
      function h(d) {
        const w = De(d);
        a[w] || (is(u, d), a[w] = !0);
      }
      return p.isArray(r) ? r.forEach(h) : h(r), this;
    }
  }
  Xe.accessor(["Content-Type", "Content-Length", "Accept", "Accept-Encoding", "User-Agent", "Authorization"]), p.reduceDescriptors(Xe.prototype, ({ value: t }, r) => {
    let o = r[0].toUpperCase() + r.slice(1);
    return {
      get: () => t,
      set(a) {
        this[o] = a;
      }
    };
  }), p.freezeMethods(Xe);
  var re = Xe;
  function Ot(t, r) {
    const o = this || Et, a = r || o, u = re.from(a.headers);
    let h = a.data;
    return p.forEach(t, function(w) {
      h = w.call(o, h, u.normalize(), r ? r.status : void 0);
    }), u.normalize(), h;
  }
  function rn(t) {
    return !!(t && t.__CANCEL__);
  }
  function Ae(t, r, o) {
    L.call(this, t ?? "canceled", L.ERR_CANCELED, r, o), this.name = "CanceledError";
  }
  p.inherits(Ae, L, {
    __CANCEL__: !0
  });
  function sn(t, r, o) {
    const a = o.config.validateStatus;
    !o.status || !a || a(o.status) ? t(o) : r(new L(
      "Request failed with status code " + o.status,
      [L.ERR_BAD_REQUEST, L.ERR_BAD_RESPONSE][Math.floor(o.status / 100) - 4],
      o.config,
      o.request,
      o
    ));
  }
  function as(t) {
    const r = /^([-+\w]{1,25})(:?\/\/|:)/.exec(t);
    return r && r[1] || "";
  }
  function cs(t, r) {
    t = t || 10;
    const o = new Array(t), a = new Array(t);
    let u = 0, h = 0, d;
    return r = r !== void 0 ? r : 1e3, function(P) {
      const A = Date.now(), O = a[h];
      d || (d = A), o[u] = P, a[u] = A;
      let F = h, D = 0;
      for (; F !== u; )
        D += o[F++], F = F % t;
      if (u = (u + 1) % t, u === h && (h = (h + 1) % t), A - d < r)
        return;
      const K = O && A - O;
      return K ? Math.round(D * 1e3 / K) : void 0;
    };
  }
  function us(t, r) {
    let o = 0, a = 1e3 / r, u, h;
    const d = (A, O = Date.now()) => {
      o = O, u = null, h && (clearTimeout(h), h = null), t(...A);
    };
    return [(...A) => {
      const O = Date.now(), F = O - o;
      F >= a ? d(A, O) : (u = A, h || (h = setTimeout(() => {
        h = null, d(u);
      }, a - F)));
    }, () => u && d(u)];
  }
  const Qe = (t, r, o = 3) => {
    let a = 0;
    const u = cs(50, 250);
    return us((h) => {
      const d = h.loaded, w = h.lengthComputable ? h.total : void 0, P = d - a, A = u(P), O = d <= w;
      a = d;
      const F = {
        loaded: d,
        total: w,
        progress: w ? d / w : void 0,
        bytes: P,
        rate: A || void 0,
        estimated: A && w && O ? (w - d) / A : void 0,
        event: h,
        lengthComputable: w != null,
        [r ? "download" : "upload"]: !0
      };
      t(F);
    }, o);
  }, on = (t, r) => {
    const o = t != null;
    return [(a) => r[0]({
      lengthComputable: o,
      total: t,
      loaded: a
    }), r[1]];
  }, an = (t) => (...r) => p.asap(() => t(...r));
  var ls = V.hasStandardBrowserEnv ? /* @__PURE__ */ ((t, r) => (o) => (o = new URL(o, V.origin), t.protocol === o.protocol && t.host === o.host && (r || t.port === o.port)))(
    new URL(V.origin),
    V.navigator && /(msie|trident)/i.test(V.navigator.userAgent)
  ) : () => !0, fs = V.hasStandardBrowserEnv ? (
    // Standard browser envs support document.cookie
    {
      write(t, r, o, a, u, h, d) {
        if (typeof document > "u") return;
        const w = [`${t}=${encodeURIComponent(r)}`];
        p.isNumber(o) && w.push(`expires=${new Date(o).toUTCString()}`), p.isString(a) && w.push(`path=${a}`), p.isString(u) && w.push(`domain=${u}`), h === !0 && w.push("secure"), p.isString(d) && w.push(`SameSite=${d}`), document.cookie = w.join("; ");
      },
      read(t) {
        if (typeof document > "u") return null;
        const r = document.cookie.match(new RegExp("(?:^|; )" + t + "=([^;]*)"));
        return r ? decodeURIComponent(r[1]) : null;
      },
      remove(t) {
        this.write(t, "", Date.now() - 864e5, "/");
      }
    }
  ) : (
    // Non-standard browser env (web workers, react-native) lack needed support.
    {
      write() {
      },
      read() {
        return null;
      },
      remove() {
      }
    }
  );
  function ds(t) {
    return /^([a-z][a-z\d+\-.]*:)?\/\//i.test(t);
  }
  function hs(t, r) {
    return r ? t.replace(/\/?\/$/, "") + "/" + r.replace(/^\/+/, "") : t;
  }
  function cn(t, r, o) {
    let a = !ds(r);
    return t && (a || o == !1) ? hs(t, r) : r;
  }
  const un = (t) => t instanceof re ? { ...t } : t;
  function ye(t, r) {
    r = r || {};
    const o = {};
    function a(A, O, F, D) {
      return p.isPlainObject(A) && p.isPlainObject(O) ? p.merge.call({ caseless: D }, A, O) : p.isPlainObject(O) ? p.merge({}, O) : p.isArray(O) ? O.slice() : O;
    }
    function u(A, O, F, D) {
      if (p.isUndefined(O)) {
        if (!p.isUndefined(A))
          return a(void 0, A, F, D);
      } else return a(A, O, F, D);
    }
    function h(A, O) {
      if (!p.isUndefined(O))
        return a(void 0, O);
    }
    function d(A, O) {
      if (p.isUndefined(O)) {
        if (!p.isUndefined(A))
          return a(void 0, A);
      } else return a(void 0, O);
    }
    function w(A, O, F) {
      if (F in r)
        return a(A, O);
      if (F in t)
        return a(void 0, A);
    }
    const P = {
      url: h,
      method: h,
      data: h,
      baseURL: d,
      transformRequest: d,
      transformResponse: d,
      paramsSerializer: d,
      timeout: d,
      timeoutMessage: d,
      withCredentials: d,
      withXSRFToken: d,
      adapter: d,
      responseType: d,
      xsrfCookieName: d,
      xsrfHeaderName: d,
      onUploadProgress: d,
      onDownloadProgress: d,
      decompress: d,
      maxContentLength: d,
      maxBodyLength: d,
      beforeRedirect: d,
      transport: d,
      httpAgent: d,
      httpsAgent: d,
      cancelToken: d,
      socketPath: d,
      responseEncoding: d,
      validateStatus: w,
      headers: (A, O, F) => u(un(A), un(O), F, !0)
    };
    return p.forEach(Object.keys({ ...t, ...r }), function(O) {
      const F = P[O] || u, D = F(t[O], r[O], O);
      p.isUndefined(D) && F !== w || (o[O] = D);
    }), o;
  }
  var ln = (t) => {
    const r = ye({}, t);
    let { data: o, withXSRFToken: a, xsrfHeaderName: u, xsrfCookieName: h, headers: d, auth: w } = r;
    if (r.headers = d = re.from(d), r.url = Zt(cn(r.baseURL, r.url, r.allowAbsoluteUrls), t.params, t.paramsSerializer), w && d.set(
      "Authorization",
      "Basic " + btoa((w.username || "") + ":" + (w.password ? unescape(encodeURIComponent(w.password)) : ""))
    ), p.isFormData(o)) {
      if (V.hasStandardBrowserEnv || V.hasStandardBrowserWebWorkerEnv)
        d.setContentType(void 0);
      else if (p.isFunction(o.getHeaders)) {
        const P = o.getHeaders(), A = ["content-type", "content-length"];
        Object.entries(P).forEach(([O, F]) => {
          A.includes(O.toLowerCase()) && d.set(O, F);
        });
      }
    }
    if (V.hasStandardBrowserEnv && (a && p.isFunction(a) && (a = a(r)), a || a !== !1 && ls(r.url))) {
      const P = u && h && fs.read(h);
      P && d.set(u, P);
    }
    return r;
  }, ps = typeof XMLHttpRequest < "u" && function(t) {
    return new Promise(function(o, a) {
      const u = ln(t);
      let h = u.data;
      const d = re.from(u.headers).normalize();
      let { responseType: w, onUploadProgress: P, onDownloadProgress: A } = u, O, F, D, K, T;
      function U() {
        K && K(), T && T(), u.cancelToken && u.cancelToken.unsubscribe(O), u.signal && u.signal.removeEventListener("abort", O);
      }
      let N = new XMLHttpRequest();
      N.open(u.method.toUpperCase(), u.url, !0), N.timeout = u.timeout;
      function v() {
        if (!N)
          return;
        const $ = re.from(
          "getAllResponseHeaders" in N && N.getAllResponseHeaders()
        ), se = {
          data: !w || w === "text" || w === "json" ? N.responseText : N.response,
          status: N.status,
          statusText: N.statusText,
          headers: $,
          config: t,
          request: N
        };
        sn(function(ne) {
          o(ne), U();
        }, function(ne) {
          a(ne), U();
        }, se), N = null;
      }
      "onloadend" in N ? N.onloadend = v : N.onreadystatechange = function() {
        !N || N.readyState !== 4 || N.status === 0 && !(N.responseURL && N.responseURL.indexOf("file:") === 0) || setTimeout(v);
      }, N.onabort = function() {
        N && (a(new L("Request aborted", L.ECONNABORTED, t, N)), N = null);
      }, N.onerror = function(G) {
        const se = G && G.message ? G.message : "Network Error", we = new L(se, L.ERR_NETWORK, t, N);
        we.event = G || null, a(we), N = null;
      }, N.ontimeout = function() {
        let G = u.timeout ? "timeout of " + u.timeout + "ms exceeded" : "timeout exceeded";
        const se = u.transitional || en;
        u.timeoutErrorMessage && (G = u.timeoutErrorMessage), a(new L(
          G,
          se.clarifyTimeoutError ? L.ETIMEDOUT : L.ECONNABORTED,
          t,
          N
        )), N = null;
      }, h === void 0 && d.setContentType(null), "setRequestHeader" in N && p.forEach(d.toJSON(), function(G, se) {
        N.setRequestHeader(se, G);
      }), p.isUndefined(u.withCredentials) || (N.withCredentials = !!u.withCredentials), w && w !== "json" && (N.responseType = u.responseType), A && ([D, T] = Qe(A, !0), N.addEventListener("progress", D)), P && N.upload && ([F, K] = Qe(P), N.upload.addEventListener("progress", F), N.upload.addEventListener("loadend", K)), (u.cancelToken || u.signal) && (O = ($) => {
        N && (a(!$ || $.type ? new Ae(null, t, N) : $), N.abort(), N = null);
      }, u.cancelToken && u.cancelToken.subscribe(O), u.signal && (u.signal.aborted ? O() : u.signal.addEventListener("abort", O)));
      const te = as(u.url);
      if (te && V.protocols.indexOf(te) === -1) {
        a(new L("Unsupported protocol " + te + ":", L.ERR_BAD_REQUEST, t));
        return;
      }
      N.send(h || null);
    });
  }, ms = (t, r) => {
    const { length: o } = t = t ? t.filter(Boolean) : [];
    if (r || o) {
      let a = new AbortController(), u;
      const h = function(A) {
        if (!u) {
          u = !0, w();
          const O = A instanceof Error ? A : this.reason;
          a.abort(O instanceof L ? O : new Ae(O instanceof Error ? O.message : O));
        }
      };
      let d = r && setTimeout(() => {
        d = null, h(new L(`timeout ${r} of ms exceeded`, L.ETIMEDOUT));
      }, r);
      const w = () => {
        t && (d && clearTimeout(d), d = null, t.forEach((A) => {
          A.unsubscribe ? A.unsubscribe(h) : A.removeEventListener("abort", h);
        }), t = null);
      };
      t.forEach((A) => A.addEventListener("abort", h));
      const { signal: P } = a;
      return P.unsubscribe = () => p.asap(w), P;
    }
  };
  const ys = function* (t, r) {
    let o = t.byteLength;
    if (o < r) {
      yield t;
      return;
    }
    let a = 0, u;
    for (; a < o; )
      u = a + r, yield t.slice(a, u), a = u;
  }, ws = async function* (t, r) {
    for await (const o of bs(t))
      yield* ys(o, r);
  }, bs = async function* (t) {
    if (t[Symbol.asyncIterator]) {
      yield* t;
      return;
    }
    const r = t.getReader();
    try {
      for (; ; ) {
        const { done: o, value: a } = await r.read();
        if (o)
          break;
        yield a;
      }
    } finally {
      await r.cancel();
    }
  }, fn = (t, r, o, a) => {
    const u = ws(t, r);
    let h = 0, d, w = (P) => {
      d || (d = !0, a && a(P));
    };
    return new ReadableStream({
      async pull(P) {
        try {
          const { done: A, value: O } = await u.next();
          if (A) {
            w(), P.close();
            return;
          }
          let F = O.byteLength;
          if (o) {
            let D = h += F;
            o(D);
          }
          P.enqueue(new Uint8Array(O));
        } catch (A) {
          throw w(A), A;
        }
      },
      cancel(P) {
        return w(P), u.return();
      }
    }, {
      highWaterMark: 2
    });
  }, dn = 64 * 1024, { isFunction: Ge } = p, Rs = (({ Request: t, Response: r }) => ({
    Request: t,
    Response: r
  }))(p.global), {
    ReadableStream: hn,
    TextEncoder: pn
  } = p.global, mn = (t, ...r) => {
    try {
      return !!t(...r);
    } catch {
      return !1;
    }
  }, gs = (t) => {
    t = p.merge.call({
      skipUndefined: !0
    }, Rs, t);
    const { fetch: r, Request: o, Response: a } = t, u = r ? Ge(r) : typeof fetch == "function", h = Ge(o), d = Ge(a);
    if (!u)
      return !1;
    const w = u && Ge(hn), P = u && (typeof pn == "function" ? /* @__PURE__ */ ((T) => (U) => T.encode(U))(new pn()) : async (T) => new Uint8Array(await new o(T).arrayBuffer())), A = h && w && mn(() => {
      let T = !1;
      const U = new o(V.origin, {
        body: new hn(),
        method: "POST",
        get duplex() {
          return T = !0, "half";
        }
      }).headers.has("Content-Type");
      return T && !U;
    }), O = d && w && mn(() => p.isReadableStream(new a("").body)), F = {
      stream: O && ((T) => T.body)
    };
    u && ["text", "arrayBuffer", "blob", "formData", "stream"].forEach((T) => {
      !F[T] && (F[T] = (U, N) => {
        let v = U && U[T];
        if (v)
          return v.call(U);
        throw new L(`Response type '${T}' is not supported`, L.ERR_NOT_SUPPORT, N);
      });
    });
    const D = async (T) => {
      if (T == null)
        return 0;
      if (p.isBlob(T))
        return T.size;
      if (p.isSpecCompliantForm(T))
        return (await new o(V.origin, {
          method: "POST",
          body: T
        }).arrayBuffer()).byteLength;
      if (p.isArrayBufferView(T) || p.isArrayBuffer(T))
        return T.byteLength;
      if (p.isURLSearchParams(T) && (T = T + ""), p.isString(T))
        return (await P(T)).byteLength;
    }, K = async (T, U) => {
      const N = p.toFiniteNumber(T.getContentLength());
      return N ?? D(U);
    };
    return async (T) => {
      let {
        url: U,
        method: N,
        data: v,
        signal: te,
        cancelToken: $,
        timeout: G,
        onDownloadProgress: se,
        onUploadProgress: we,
        responseType: ne,
        headers: Pt,
        withCredentials: nt = "same-origin",
        fetchOptions: On
      } = ln(T), Tn = r || fetch;
      ne = ne ? (ne + "").toLowerCase() : "text";
      let rt = ms([te, $ && $.toAbortSignal()], G), Ie = null;
      const be = rt && rt.unsubscribe && (() => {
        rt.unsubscribe();
      });
      let An;
      try {
        if (we && A && N !== "get" && N !== "head" && (An = await K(Pt, v)) !== 0) {
          let pe = new o(U, {
            method: "POST",
            body: v,
            duplex: "half"
          }), Ce;
          if (p.isFormData(v) && (Ce = pe.headers.get("content-type")) && Pt.setContentType(Ce), pe.body) {
            const [Nt, st] = on(
              An,
              Qe(an(we))
            );
            v = fn(pe.body, dn, Nt, st);
          }
        }
        p.isString(nt) || (nt = nt ? "include" : "omit");
        const ce = h && "credentials" in o.prototype, Cn = {
          ...On,
          signal: rt,
          method: N.toUpperCase(),
          headers: Pt.normalize().toJSON(),
          body: v,
          duplex: "half",
          credentials: ce ? nt : void 0
        };
        Ie = h && new o(U, Cn);
        let he = await (h ? Tn(Ie, On) : Tn(U, Cn));
        const xn = O && (ne === "stream" || ne === "response");
        if (O && (se || xn && be)) {
          const pe = {};
          ["status", "statusText", "headers"].forEach((Pn) => {
            pe[Pn] = he[Pn];
          });
          const Ce = p.toFiniteNumber(he.headers.get("content-length")), [Nt, st] = se && on(
            Ce,
            Qe(an(se), !0)
          ) || [];
          he = new a(
            fn(he.body, dn, Nt, () => {
              st && st(), be && be();
            }),
            pe
          );
        }
        ne = ne || "text";
        let Ns = await F[p.findKey(F, ne) || "text"](he, T);
        return !xn && be && be(), await new Promise((pe, Ce) => {
          sn(pe, Ce, {
            data: Ns,
            headers: re.from(he.headers),
            status: he.status,
            statusText: he.statusText,
            config: T,
            request: Ie
          });
        });
      } catch (ce) {
        throw be && be(), ce && ce.name === "TypeError" && /Load failed|fetch/i.test(ce.message) ? Object.assign(
          new L("Network Error", L.ERR_NETWORK, T, Ie),
          {
            cause: ce.cause || ce
          }
        ) : L.from(ce, ce && ce.code, T, Ie);
      }
    };
  }, Es = /* @__PURE__ */ new Map(), yn = (t) => {
    let r = t && t.env || {};
    const { fetch: o, Request: a, Response: u } = r, h = [
      a,
      u,
      o
    ];
    let d = h.length, w = d, P, A, O = Es;
    for (; w--; )
      P = h[w], A = O.get(P), A === void 0 && O.set(P, A = w ? /* @__PURE__ */ new Map() : gs(r)), O = A;
    return A;
  };
  yn();
  const Tt = {
    http: jr,
    xhr: ps,
    fetch: {
      get: yn
    }
  };
  p.forEach(Tt, (t, r) => {
    if (t) {
      try {
        Object.defineProperty(t, "name", { value: r });
      } catch {
      }
      Object.defineProperty(t, "adapterName", { value: r });
    }
  });
  const wn = (t) => `- ${t}`, Ss = (t) => p.isFunction(t) || t === null || t === !1;
  function Os(t, r) {
    t = p.isArray(t) ? t : [t];
    const { length: o } = t;
    let a, u;
    const h = {};
    for (let d = 0; d < o; d++) {
      a = t[d];
      let w;
      if (u = a, !Ss(a) && (u = Tt[(w = String(a)).toLowerCase()], u === void 0))
        throw new L(`Unknown adapter '${w}'`);
      if (u && (p.isFunction(u) || (u = u.get(r))))
        break;
      h[w || "#" + d] = u;
    }
    if (!u) {
      const d = Object.entries(h).map(
        ([P, A]) => `adapter ${P} ` + (A === !1 ? "is not supported by the environment" : "is not available in the build")
      );
      let w = o ? d.length > 1 ? `since :
` + d.map(wn).join(`
`) : " " + wn(d[0]) : "as no adapter specified";
      throw new L(
        "There is no suitable adapter to dispatch the request " + w,
        "ERR_NOT_SUPPORT"
      );
    }
    return u;
  }
  var bn = {
    /**
     * Resolve an adapter from a list of adapter names or functions.
     * @type {Function}
     */
    getAdapter: Os,
    /**
     * Exposes all known adapters
     * @type {Object<string, Function|Object>}
     */
    adapters: Tt
  };
  function At(t) {
    if (t.cancelToken && t.cancelToken.throwIfRequested(), t.signal && t.signal.aborted)
      throw new Ae(null, t);
  }
  function Rn(t) {
    return At(t), t.headers = re.from(t.headers), t.data = Ot.call(
      t,
      t.transformRequest
    ), ["post", "put", "patch"].indexOf(t.method) !== -1 && t.headers.setContentType("application/x-www-form-urlencoded", !1), bn.getAdapter(t.adapter || Et.adapter, t)(t).then(function(a) {
      return At(t), a.data = Ot.call(
        t,
        t.transformResponse,
        a
      ), a.headers = re.from(a.headers), a;
    }, function(a) {
      return rn(a) || (At(t), a && a.response && (a.response.data = Ot.call(
        t,
        t.transformResponse,
        a.response
      ), a.response.headers = re.from(a.response.headers))), Promise.reject(a);
    });
  }
  const gn = "1.13.2", Ze = {};
  ["object", "boolean", "number", "function", "string", "symbol"].forEach((t, r) => {
    Ze[t] = function(a) {
      return typeof a === t || "a" + (r < 1 ? "n " : " ") + t;
    };
  });
  const En = {};
  Ze.transitional = function(r, o, a) {
    function u(h, d) {
      return "[Axios v" + gn + "] Transitional option '" + h + "'" + d + (a ? ". " + a : "");
    }
    return (h, d, w) => {
      if (r === !1)
        throw new L(
          u(d, " has been removed" + (o ? " in " + o : "")),
          L.ERR_DEPRECATED
        );
      return o && !En[d] && (En[d] = !0, console.warn(
        u(
          d,
          " has been deprecated since v" + o + " and will be removed in the near future"
        )
      )), r ? r(h, d, w) : !0;
    };
  }, Ze.spelling = function(r) {
    return (o, a) => (console.warn(`${a} is likely a misspelling of ${r}`), !0);
  };
  function Ts(t, r, o) {
    if (typeof t != "object")
      throw new L("options must be an object", L.ERR_BAD_OPTION_VALUE);
    const a = Object.keys(t);
    let u = a.length;
    for (; u-- > 0; ) {
      const h = a[u], d = r[h];
      if (d) {
        const w = t[h], P = w === void 0 || d(w, h, t);
        if (P !== !0)
          throw new L("option " + h + " must be " + P, L.ERR_BAD_OPTION_VALUE);
        continue;
      }
      if (o !== !0)
        throw new L("Unknown option " + h, L.ERR_BAD_OPTION);
    }
  }
  var Ye = {
    assertOptions: Ts,
    validators: Ze
  };
  const ae = Ye.validators;
  class et {
    constructor(r) {
      this.defaults = r || {}, this.interceptors = {
        request: new Yt(),
        response: new Yt()
      };
    }
    /**
     * Dispatch a request
     *
     * @param {String|Object} configOrUrl The config specific for this request (merged with this.defaults)
     * @param {?Object} config
     *
     * @returns {Promise} The Promise to be fulfilled
     */
    async request(r, o) {
      try {
        return await this._request(r, o);
      } catch (a) {
        if (a instanceof Error) {
          let u = {};
          Error.captureStackTrace ? Error.captureStackTrace(u) : u = new Error();
          const h = u.stack ? u.stack.replace(/^.+\n/, "") : "";
          try {
            a.stack ? h && !String(a.stack).endsWith(h.replace(/^.+\n.+\n/, "")) && (a.stack += `
` + h) : a.stack = h;
          } catch {
          }
        }
        throw a;
      }
    }
    _request(r, o) {
      typeof r == "string" ? (o = o || {}, o.url = r) : o = r || {}, o = ye(this.defaults, o);
      const { transitional: a, paramsSerializer: u, headers: h } = o;
      a !== void 0 && Ye.assertOptions(a, {
        silentJSONParsing: ae.transitional(ae.boolean),
        forcedJSONParsing: ae.transitional(ae.boolean),
        clarifyTimeoutError: ae.transitional(ae.boolean)
      }, !1), u != null && (p.isFunction(u) ? o.paramsSerializer = {
        serialize: u
      } : Ye.assertOptions(u, {
        encode: ae.function,
        serialize: ae.function
      }, !0)), o.allowAbsoluteUrls !== void 0 || (this.defaults.allowAbsoluteUrls !== void 0 ? o.allowAbsoluteUrls = this.defaults.allowAbsoluteUrls : o.allowAbsoluteUrls = !0), Ye.assertOptions(o, {
        baseUrl: ae.spelling("baseURL"),
        withXsrfToken: ae.spelling("withXSRFToken")
      }, !0), o.method = (o.method || this.defaults.method || "get").toLowerCase();
      let d = h && p.merge(
        h.common,
        h[o.method]
      );
      h && p.forEach(
        ["delete", "get", "head", "post", "put", "patch", "common"],
        (T) => {
          delete h[T];
        }
      ), o.headers = re.concat(d, h);
      const w = [];
      let P = !0;
      this.interceptors.request.forEach(function(U) {
        typeof U.runWhen == "function" && U.runWhen(o) === !1 || (P = P && U.synchronous, w.unshift(U.fulfilled, U.rejected));
      });
      const A = [];
      this.interceptors.response.forEach(function(U) {
        A.push(U.fulfilled, U.rejected);
      });
      let O, F = 0, D;
      if (!P) {
        const T = [Rn.bind(this), void 0];
        for (T.unshift(...w), T.push(...A), D = T.length, O = Promise.resolve(o); F < D; )
          O = O.then(T[F++], T[F++]);
        return O;
      }
      D = w.length;
      let K = o;
      for (; F < D; ) {
        const T = w[F++], U = w[F++];
        try {
          K = T(K);
        } catch (N) {
          U.call(this, N);
          break;
        }
      }
      try {
        O = Rn.call(this, K);
      } catch (T) {
        return Promise.reject(T);
      }
      for (F = 0, D = A.length; F < D; )
        O = O.then(A[F++], A[F++]);
      return O;
    }
    getUri(r) {
      r = ye(this.defaults, r);
      const o = cn(r.baseURL, r.url, r.allowAbsoluteUrls);
      return Zt(o, r.params, r.paramsSerializer);
    }
  }
  p.forEach(["delete", "get", "head", "options"], function(r) {
    et.prototype[r] = function(o, a) {
      return this.request(ye(a || {}, {
        method: r,
        url: o,
        data: (a || {}).data
      }));
    };
  }), p.forEach(["post", "put", "patch"], function(r) {
    function o(a) {
      return function(h, d, w) {
        return this.request(ye(w || {}, {
          method: r,
          headers: a ? {
            "Content-Type": "multipart/form-data"
          } : {},
          url: h,
          data: d
        }));
      };
    }
    et.prototype[r] = o(), et.prototype[r + "Form"] = o(!0);
  });
  var tt = et;
  class Ct {
    constructor(r) {
      if (typeof r != "function")
        throw new TypeError("executor must be a function.");
      let o;
      this.promise = new Promise(function(h) {
        o = h;
      });
      const a = this;
      this.promise.then((u) => {
        if (!a._listeners) return;
        let h = a._listeners.length;
        for (; h-- > 0; )
          a._listeners[h](u);
        a._listeners = null;
      }), this.promise.then = (u) => {
        let h;
        const d = new Promise((w) => {
          a.subscribe(w), h = w;
        }).then(u);
        return d.cancel = function() {
          a.unsubscribe(h);
        }, d;
      }, r(function(h, d, w) {
        a.reason || (a.reason = new Ae(h, d, w), o(a.reason));
      });
    }
    /**
     * Throws a `CanceledError` if cancellation has been requested.
     */
    throwIfRequested() {
      if (this.reason)
        throw this.reason;
    }
    /**
     * Subscribe to the cancel signal
     */
    subscribe(r) {
      if (this.reason) {
        r(this.reason);
        return;
      }
      this._listeners ? this._listeners.push(r) : this._listeners = [r];
    }
    /**
     * Unsubscribe from the cancel signal
     */
    unsubscribe(r) {
      if (!this._listeners)
        return;
      const o = this._listeners.indexOf(r);
      o !== -1 && this._listeners.splice(o, 1);
    }
    toAbortSignal() {
      const r = new AbortController(), o = (a) => {
        r.abort(a);
      };
      return this.subscribe(o), r.signal.unsubscribe = () => this.unsubscribe(o), r.signal;
    }
    /**
     * Returns an object that contains a new `CancelToken` and a function that, when called,
     * cancels the `CancelToken`.
     */
    static source() {
      let r;
      return {
        token: new Ct(function(u) {
          r = u;
        }),
        cancel: r
      };
    }
  }
  var As = Ct;
  function Cs(t) {
    return function(o) {
      return t.apply(null, o);
    };
  }
  function xs(t) {
    return p.isObject(t) && t.isAxiosError === !0;
  }
  const xt = {
    Continue: 100,
    SwitchingProtocols: 101,
    Processing: 102,
    EarlyHints: 103,
    Ok: 200,
    Created: 201,
    Accepted: 202,
    NonAuthoritativeInformation: 203,
    NoContent: 204,
    ResetContent: 205,
    PartialContent: 206,
    MultiStatus: 207,
    AlreadyReported: 208,
    ImUsed: 226,
    MultipleChoices: 300,
    MovedPermanently: 301,
    Found: 302,
    SeeOther: 303,
    NotModified: 304,
    UseProxy: 305,
    Unused: 306,
    TemporaryRedirect: 307,
    PermanentRedirect: 308,
    BadRequest: 400,
    Unauthorized: 401,
    PaymentRequired: 402,
    Forbidden: 403,
    NotFound: 404,
    MethodNotAllowed: 405,
    NotAcceptable: 406,
    ProxyAuthenticationRequired: 407,
    RequestTimeout: 408,
    Conflict: 409,
    Gone: 410,
    LengthRequired: 411,
    PreconditionFailed: 412,
    PayloadTooLarge: 413,
    UriTooLong: 414,
    UnsupportedMediaType: 415,
    RangeNotSatisfiable: 416,
    ExpectationFailed: 417,
    ImATeapot: 418,
    MisdirectedRequest: 421,
    UnprocessableEntity: 422,
    Locked: 423,
    FailedDependency: 424,
    TooEarly: 425,
    UpgradeRequired: 426,
    PreconditionRequired: 428,
    TooManyRequests: 429,
    RequestHeaderFieldsTooLarge: 431,
    UnavailableForLegalReasons: 451,
    InternalServerError: 500,
    NotImplemented: 501,
    BadGateway: 502,
    ServiceUnavailable: 503,
    GatewayTimeout: 504,
    HttpVersionNotSupported: 505,
    VariantAlsoNegotiates: 506,
    InsufficientStorage: 507,
    LoopDetected: 508,
    NotExtended: 510,
    NetworkAuthenticationRequired: 511,
    WebServerIsDown: 521,
    ConnectionTimedOut: 522,
    OriginIsUnreachable: 523,
    TimeoutOccurred: 524,
    SslHandshakeFailed: 525,
    InvalidSslCertificate: 526
  };
  Object.entries(xt).forEach(([t, r]) => {
    xt[r] = t;
  });
  var Ps = xt;
  function Sn(t) {
    const r = new tt(t), o = e(tt.prototype.request, r);
    return p.extend(o, tt.prototype, r, { allOwnKeys: !0 }), p.extend(o, r, null, { allOwnKeys: !0 }), o.create = function(u) {
      return Sn(ye(t, u));
    }, o;
  }
  const H = Sn(Et);
  return H.Axios = tt, H.CanceledError = Ae, H.CancelToken = As, H.isCancel = rn, H.VERSION = gn, H.toFormData = Ve, H.AxiosError = L, H.Cancel = H.CanceledError, H.all = function(r) {
    return Promise.all(r);
  }, H.spread = Cs, H.isAxiosError = xs, H.mergeConfig = ye, H.AxiosHeaders = re, H.formToJSON = (t) => tn(p.isHTMLForm(t) ? new FormData(t) : t), H.getAdapter = bn.getAdapter, H.HttpStatusCode = Ps, H.default = H, kt = H, kt;
}
var ui = ut.exports, Jn;
function li() {
  return Jn || (Jn = 1, (function(e, n) {
    (function(s, i) {
      e.exports = i(/* @__PURE__ */ ci());
    })(ui, (function(s) {
      return (function() {
        var i = { 593: function(y, S, R) {
          Object.defineProperty(S, "__esModule", { value: !0 }), S.resendFailedRequest = S.getRetryInstance = S.unsetCache = S.createRequestQueueInterceptor = S.createRefreshCall = S.shouldInterceptError = S.mergeOptions = S.defaultOptions = void 0;
          const g = R(300);
          S.defaultOptions = { statusCodes: [401], pauseInstanceWhileRefreshing: !1 }, S.mergeOptions = function(b, C) {
            return Object.assign(Object.assign(Object.assign({}, b), { pauseInstanceWhileRefreshing: C.skipWhileRefreshing }), C);
          }, S.shouldInterceptError = function(b, C, k, E) {
            var _, x;
            return !!b && !(!((_ = b.config) === null || _ === void 0) && _.skipAuthRefresh) && !!(C.interceptNetworkError && !b.response && b.request.status === 0 || b.response && (C?.shouldRefresh ? C.shouldRefresh(b) : !((x = C.statusCodes) === null || x === void 0) && x.includes(parseInt(b.response.status)))) && (b.response || (b.response = { config: b.config }), !C.pauseInstanceWhileRefreshing || !E.skipInstances.includes(k));
          }, S.createRefreshCall = function(b, C, k) {
            return k.refreshCall || (k.refreshCall = C(b), typeof k.refreshCall.then == "function") ? k.refreshCall : (console.warn("axios-auth-refresh requires `refreshTokenCall` to return a promise."), Promise.reject());
          }, S.createRequestQueueInterceptor = function(b, C, k) {
            return C.requestQueueInterceptorId === void 0 && (C.requestQueueInterceptorId = b.interceptors.request.use(((E) => C.refreshCall.catch((() => {
              throw new g.default.Cancel("Request call failed");
            })).then((() => k.onRetry ? k.onRetry(E) : E))))), C.requestQueueInterceptorId;
          }, S.unsetCache = function(b, C) {
            b.interceptors.request.eject(C.requestQueueInterceptorId), C.requestQueueInterceptorId = void 0, C.refreshCall = void 0, C.skipInstances = C.skipInstances.filter(((k) => k !== b));
          }, S.getRetryInstance = function(b, C) {
            return C.retryInstance || b;
          }, S.resendFailedRequest = function(b, C) {
            return b.config.skipAuthRefresh = !0, C(b.response.config);
          };
        }, 300: function(y) {
          y.exports = s;
        } }, c = {};
        function f(y) {
          var S = c[y];
          if (S !== void 0) return S.exports;
          var R = c[y] = { exports: {} };
          return i[y](R, R.exports, f), R.exports;
        }
        var l = {};
        return (function() {
          var y = l;
          Object.defineProperty(y, "__esModule", { value: !0 });
          const S = f(593);
          y.default = function(R, g, b = {}) {
            if (typeof g != "function") throw new Error("axios-auth-refresh requires `refreshAuthCall` to be a function that returns a promise.");
            const C = { skipInstances: [], refreshCall: void 0, requestQueueInterceptorId: void 0 };
            return R.interceptors.response.use(((k) => k), ((k) => {
              if (b = (0, S.mergeOptions)(S.defaultOptions, b), !(0, S.shouldInterceptError)(k, b, R, C)) return Promise.reject(k);
              b.pauseInstanceWhileRefreshing && C.skipInstances.push(R);
              const E = (0, S.createRefreshCall)(k, g, C);
              return (0, S.createRequestQueueInterceptor)(R, C, b), E.catch(((_) => Promise.reject(_))).then((() => (0, S.resendFailedRequest)(k, (0, S.getRetryInstance)(R, b)))).finally((() => (0, S.unsetCache)(R, C)));
            }));
          };
        })(), l;
      })();
    }));
  })(ut)), ut.exports;
}
var fi = li();
const di = /* @__PURE__ */ ai(fi);
class hi {
  constructor() {
    this.queue = [], this.isRefreshing = !1, this.refreshPromise = null;
  }
  /**
   * 添加请求到队列
   */
  enqueue(n) {
    this.queue.push(n);
  }
  /**
   * 处理队列中的所有请求
   */
  async processQueue(n = null) {
    const s = [...this.queue];
    this.queue = [], s.forEach(({ resolve: i, reject: c, config: f }) => {
      n ? c(n) : i(f);
    });
  }
  /**
   * 开始刷新 token
   */
  startRefresh() {
    return this.isRefreshing && this.refreshPromise ? this.refreshPromise : (this.isRefreshing = !0, this.refreshPromise = new Promise((n, s) => {
      this.resolveRefresh = n, this.rejectRefresh = s;
    }), this.refreshPromise);
  }
  /**
   * 完成刷新（成功）
   */
  async finishRefresh() {
    this.isRefreshing = !1, this.refreshPromise = null, this.resolveRefresh && (this.resolveRefresh(), this.resolveRefresh = void 0), await this.processQueue();
  }
  /**
   * 完成刷新（失败）
   */
  async finishRefreshWithError(n) {
    this.isRefreshing = !1, this.refreshPromise = null, this.rejectRefresh && (this.rejectRefresh(n), this.rejectRefresh = void 0), await this.processQueue(n);
  }
  /**
   * 检查是否正在刷新
   */
  getIsRefreshing() {
    return this.isRefreshing;
  }
  /**
   * 清空队列
   */
  clear() {
    this.queue = [], this.isRefreshing = !1, this.refreshPromise = null;
  }
}
function br(e) {
  const {
    baseURL: n,
    storage: s,
    refreshApi: i,
    timeout: c = 15e3,
    requestInterceptor: f,
    responseInterceptor: l,
    returnDataOnly: y = !0,
    errorHandler: S
  } = e, R = j.create({
    baseURL: n,
    timeout: c,
    headers: {
      "Content-Type": "application/json"
    }
  }), g = new hi();
  return R.interceptors.request.use(
    async (b) => {
      if (g.getIsRefreshing())
        return new Promise((k, E) => {
          g.enqueue({ resolve: k, reject: E, config: b });
        });
      const C = await s.getAccessToken();
      return C && b.headers && (b.headers.Authorization = `Bearer ${C}`), f ? await f(b) : b;
    },
    (b) => Promise.reject(b)
  ), di(
    R,
    async (b) => {
      if (g.getIsRefreshing())
        try {
          await g.startRefresh();
          const C = await s.getAccessToken();
          return C && b.config?.headers && (b.config.headers.Authorization = `Bearer ${C}`), Promise.resolve();
        } catch (C) {
          return Promise.reject(C);
        }
      await g.startRefresh();
      try {
        const C = await s.getRefreshToken();
        if (!C)
          throw new Error("No refresh token available");
        const k = await i(C);
        return await s.setAccessToken(k.accessToken), await s.setRefreshToken(k.refreshToken), b.config?.headers && (b.config.headers.Authorization = `Bearer ${k.accessToken}`), await g.finishRefresh(), Promise.resolve();
      } catch (C) {
        return await s.clearTokens(), await g.finishRefreshWithError(C), Promise.reject(C);
      }
    },
    {
      statusCodes: [401],
      // 只在 401 时触发刷新
      skipWhileRefreshing: !1
      // 允许在刷新期间处理其他请求
    }
  ), R.interceptors.response.use(
    (b) => l?.onFulfilled ? l.onFulfilled(b) : y ? b.data : b,
    async (b) => {
      if (l?.onRejected)
        return l.onRejected(b);
      S && await S(b);
      const C = {
        message: b.message || "Request failed",
        status: b.response?.status,
        data: b.response?.data,
        config: b.config
      };
      return Promise.reject(C);
    }
  ), R;
}
const pi = {
  getItem(e) {
    return Promise.resolve(localStorage.getItem(e));
  },
  setItem(e, n) {
    return Promise.resolve(localStorage.setItem(e, n));
  },
  removeItem(e) {
    return Promise.resolve(localStorage.removeItem(e));
  }
}, xe = {
  ACCESS_TOKEN: "access_token",
  REFRESH_TOKEN: "refresh_token"
};
class Rr {
  constructor() {
    this.storage = pi;
  }
  async getAccessToken() {
    return await this.storage.getItem(xe.ACCESS_TOKEN);
  }
  async setAccessToken(n) {
    await this.storage.setItem(xe.ACCESS_TOKEN, n);
  }
  async getRefreshToken() {
    return await this.storage.getItem(xe.REFRESH_TOKEN);
  }
  async setRefreshToken(n) {
    await this.storage.setItem(xe.REFRESH_TOKEN, n);
  }
  async clearTokens() {
    await Promise.all([
      this.storage.removeItem(xe.ACCESS_TOKEN),
      this.storage.removeItem(xe.REFRESH_TOKEN)
    ]);
  }
}
br({
  baseURL: "/api",
  storage: new Rr(),
  refreshApi: async (e) => {
    const n = await fetch("/api/auth/refresh", {
      method: "POST",
      body: JSON.stringify({ refreshToken: e }),
      headers: { "Content-Type": "application/json" }
    }).then((s) => s.json());
    return {
      accessToken: n.access_token,
      refreshToken: n.refresh_token
    };
  }
});
function $t() {
  return typeof window < "u" && window.API_BASE_URL ? window.API_BASE_URL : "http://localhost:48911";
}
function gr() {
  return typeof window < "u" && window.STATIC_SERVER_URL ? window.STATIC_SERVER_URL : $t();
}
function Er(e) {
  if (e.startsWith("http://") || e.startsWith("https://"))
    return e;
  const s = $t().replace(/\/$/, ""), i = e.startsWith("/") ? e : `/${e}`;
  return `${s}${i}`;
}
function mi(e) {
  if (e.startsWith("http://") || e.startsWith("https://"))
    return e;
  const s = gr().replace(/\/$/, ""), i = e.startsWith("/") ? e : `/${e}`;
  return `${s}${i}`;
}
function yi(e) {
  return Er(e).replace(/^http:\/\//, "ws://").replace(/^https:\/\//, "wss://");
}
function Vn() {
  if (typeof window > "u") {
    console.warn("[Request] 非浏览器环境，跳过初始化");
    return;
  }
  const e = $t(), n = br({
    baseURL: e,
    storage: new Rr(),
    refreshApi: async (i) => {
      const c = `${e.replace(/\/$/, "")}/api/auth/refresh`, f = await fetch(c, {
        method: "POST",
        body: JSON.stringify({ refreshToken: i }),
        headers: { "Content-Type": "application/json" }
      }).then((l) => l.json());
      return {
        accessToken: f.access_token,
        refreshToken: f.refresh_token
      };
    },
    timeout: 15e3,
    returnDataOnly: !0,
    errorHandler: async (i) => {
      i.response?.status === 403 && console.warn("[Request] Access forbidden, redirecting to login...");
    }
  }), s = window;
  s.request = n, s.buildApiUrl = Er, s.buildStaticUrl = mi, s.buildWebSocketUrl = yi, s.API_BASE_URL = e, s.STATIC_SERVER_URL = gr(), console.log("[Request] request 实例和工具函数已暴露到全局");
}
typeof window < "u" && (document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", Vn) : Vn());
export {
  Er as buildApiUrl,
  mi as buildStaticUrl,
  yi as buildWebSocketUrl,
  Vn as initRequest
};
