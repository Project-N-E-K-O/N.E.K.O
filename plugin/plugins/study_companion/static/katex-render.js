(function () {
  const MATH_PATTERN = /\$\$([\s\S]+?)\$\$|\$(.+?)\$/g;

  function escapeHTML(value) {
    const div = document.createElement('div');
    div.appendChild(document.createTextNode(String(value || '')));
    return div.innerHTML;
  }

  function splitByMath(text) {
    const parts = [];
    const source = String(text || '');
    let last = 0;
    let match;
    while ((match = MATH_PATTERN.exec(source)) !== null) {
      if (match.index > last) {
        parts.push({ type: 'text', value: source.slice(last, match.index) });
      }
      if (match[1] !== undefined) {
        parts.push({ type: 'math', value: match[1].trim(), display: true });
      } else {
        parts.push({ type: 'math', value: match[2].trim(), display: false });
      }
      last = MATH_PATTERN.lastIndex;
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
    if (!source || !source.includes('$') || !window.katex || typeof window.katex.renderToString !== 'function') {
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
    normalizeLatexForKatex,
    splitByMath,
    renderMathInText,
  };
})();
