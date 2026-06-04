(function () {
  const mathParser = window.__studyCompanionMathParser || {};

  function escapeHTML(value) {
    const div = document.createElement('div');
    div.appendChild(document.createTextNode(String(value || '')));
    return div.innerHTML;
  }

  function normalizeLatexForKatex(value) {
    if (typeof mathParser.normalizeLatexForKatex === 'function') {
      return mathParser.normalizeLatexForKatex(value);
    }
    return String(value || '');
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
      if (typeof mathParser.splitByMath !== 'function') {
        return escapeHTML(source);
      }
      return mathParser.splitByMath(source).map((part) => (
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
    splitByMath: mathParser.splitByMath,
    renderMathInText,
  };
})();
