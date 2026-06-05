(function () {
  const CURRENCY_START_PATTERN = /^\$(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?(?:[A-Z]{2,4}|%)?(?=$|[\s)\],.;!?-])/;

  function escapeHTML(value) {
    const div = document.createElement('div');
    div.appendChild(document.createTextNode(String(value || '')));
    return div.innerHTML;
  }

  function hasEscapedDelimiter(source, index) {
    let slashes = 0;
    for (let i = index - 1; i >= 0 && source[i] === '\\'; i -= 1) {
      slashes += 1;
    }
    return slashes % 2 === 1;
  }

  function isLikelyCurrencyStart(source, index) {
    return CURRENCY_START_PATTERN.test(source.slice(index));
  }

  function findMathDelimiter(source, start, delimiter) {
    let index = start;
    while (index < source.length) {
      const next = source.indexOf(delimiter, index);
      if (next === -1) {
        return -1;
      }
      if (hasEscapedDelimiter(source, next)) {
        index = next + delimiter.length;
        continue;
      }
      if (delimiter === '$' && source[next + 1] === '$') {
        index = next + 2;
        continue;
      }
      return next;
    }
    return -1;
  }

  function findBackslashMathDelimiter(source, start, closeChar) {
    let index = start;
    while (index < source.length) {
      const next = source.indexOf(`\\${closeChar}`, index);
      if (next === -1) {
        return -1;
      }
      if (hasEscapedDelimiter(source, next)) {
        index = next + 2;
        continue;
      }
      return next;
    }
    return -1;
  }

  function splitByMath(text) {
    const parts = [];
    const source = String(text || '');
    let last = 0;
    let index = 0;
    while (index < source.length) {
      if (source[index] === '\\' && !hasEscapedDelimiter(source, index)) {
        const openChar = source[index + 1];
        const isBackslashInline = openChar === '(';
        const isBackslashDisplay = openChar === '[';
        if (isBackslashInline || isBackslashDisplay) {
          const closeChar = isBackslashInline ? ')' : ']';
          const closer = findBackslashMathDelimiter(source, index + 2, closeChar);
          if (closer === -1) {
            index += 2;
            continue;
          }
          if (index > last) {
            parts.push({ type: 'text', value: source.slice(last, index) });
          }
          const mathValue = source.slice(index + 2, closer).trim();
          if (mathValue) {
            parts.push({ type: 'math', value: mathValue, display: isBackslashDisplay });
          } else {
            parts.push({ type: 'text', value: source.slice(index, closer + 2) });
          }
          index = closer + 2;
          last = index;
          continue;
        }
      }

      if (source[index] !== '$' || hasEscapedDelimiter(source, index)) {
        index += 1;
        continue;
      }

      if (source[index + 1] === '$') {
        const displayCloser = findMathDelimiter(source, index + 2, '$$');
        if (displayCloser === -1) {
          index += 2;
          continue;
        }
        if (index > last) {
          parts.push({ type: 'text', value: source.slice(last, index) });
        }
        parts.push({
          type: 'math',
          value: source.slice(index + 2, displayCloser).trim(),
          display: true,
        });
        index = displayCloser + 2;
        last = index;
        continue;
      }

      if (isLikelyCurrencyStart(source, index)) {
        index += 1;
        continue;
      }
      const inlineCloser = findMathDelimiter(source, index + 1, '$');
      if (inlineCloser === -1) {
        index += 1;
        continue;
      }
      if (index > last) {
        parts.push({ type: 'text', value: source.slice(last, index) });
      }
      const mathValue = source.slice(index + 1, inlineCloser).trim();
      if (mathValue) {
        parts.push({ type: 'math', value: mathValue, display: false });
      } else {
        parts.push({ type: 'text', value: source.slice(index, inlineCloser + 1) });
      }
      index = inlineCloser + 1;
      last = index;
    }
    if (last < source.length) {
      parts.push({ type: 'text', value: source.slice(last) });
    }
    return parts;
  }

  function normalizeLatexForKatex(value) {
    return String(value || '').replace(/</g, '\\lt ').replace(/>/g, '\\gt ');
  }

  function renderMathPart(part) {
    const wrapper = part.display ? '$$' : '$';
    if (!part.value) {
      return `<code>${wrapper}${escapeHTML(part.value)}${wrapper}</code>`;
    }
    const latex = normalizeLatexForKatex(part.value);
    try {
      return window.katex.renderToString(latex, {
        displayMode: part.display,
        throwOnError: false,
        trust: false,
      });
    } catch (_error) {
      return `<code>${wrapper}${escapeHTML(part.value)}${wrapper}</code>`;
    }
  }

  function renderMathInText(text) {
    const source = String(text || '');
    const hasMathDelimiter = source.includes('$') || source.includes('\\(') || source.includes('\\[');
    if (!source || !hasMathDelimiter || !window.katex || typeof window.katex.renderToString !== 'function') {
      return escapeHTML(source);
    }
    try {
      return splitByMath(source).map((part) => (
        part.type === 'math' ? renderMathPart(part) : escapeHTML(part.value)
      )).join('');
    } catch (_error) {
      return escapeHTML(source);
    }
  }

  window.renderMathInText = renderMathInText;
  window.__studyCompanionMath = {
    escapeHTML,
    hasEscapedDelimiter,
    isLikelyCurrencyStart,
    findMathDelimiter,
    findBackslashMathDelimiter,
    normalizeLatexForKatex,
    splitByMath,
    renderMathInText,
  };
})();
