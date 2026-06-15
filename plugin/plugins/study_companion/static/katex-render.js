(function () {
  const CURRENCY_START_PATTERN = /^\$(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?(?:[A-Z]{2,4}|%)?(?=$|[\s)\],.;!?+\-])/;
  const CURRENCY_AMOUNT_PATTERN = /^\$(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?(?:[A-Z]{2,4}|%)?/;
  const BARE_LATEX_COMMAND_PATTERN = /\\(?:vec|overrightarrow|overline|bar|hat|frac|sqrt|sum|prod|int|lim|cdot|times|angle|sin|cos|tan|log|ln|infty|to|left|right|mathbf|mathbb|mathrm)(?![A-Za-z])/;
  const DOUBLE_ESCAPED_LATEX_COMMAND_PATTERN = /\\\\(?=(?:vec|overrightarrow|overline|bar|hat|frac|sqrt|sum|prod|int|lim|cdot|times|angle|sin|cos|tan|log|ln|infty|to|left|right|mathbf|mathbb|mathrm)(?![A-Za-z]))/g;
  const BARE_LATEX_SPAN_PATTERN = /\$[^\n。；;!?！？]*\\(?:vec|overrightarrow|overline|bar|hat|frac|sqrt|sum|prod|int|lim|cdot|times|angle|sin|cos|tan|log|ln|infty|to|left|right|mathbf|mathbb|mathrm)(?![A-Za-z])[^\n。；;!?！？]*|\|?\\(?:vec|overrightarrow|overline|bar|hat|frac|sqrt|sum|prod|int|lim|cdot|times|angle|sin|cos|tan|log|ln|infty|to|left|right|mathbf|mathbb|mathrm)(?![A-Za-z])(?:\[[^\]\n]{1,40}\]|\{[^{}\n]{1,120}\}|[A-Za-z0-9\\()+\-*/=.,:_|^\s]){0,180}/g;

  const CJK_TEXT_PATTERN = /[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]/;

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
    const tail = source.slice(index);
    return CURRENCY_START_PATTERN.test(tail) && !isCurrencyLikeLatexStart(tail);
  }

  function isCurrencyLikeLatexStart(tail) {
    const amount = tail.match(CURRENCY_AMOUNT_PATTERN);
    if (!amount) {
      return false;
    }
    const closer = findMathDelimiter(tail, 1, '$');
    if (closer === -1) {
      return false;
    }
    const expression = tail.slice(amount[0].length, closer);
    if (/^\s*\\(?:times|cdot|frac|sqrt|lt|gt|le|ge|leq|geq)(?![A-Za-z])/.test(expression)) {
      return true;
    }
    if (!/^\s*[+\-*/=^_<>≤≥]/.test(expression)) {
      return false;
    }
    if (/[A-Za-z]{2,}/.test(expression.replace(/\\[A-Za-z]+/g, ''))) {
      return false;
    }
    return (
      /[=+\-*/^_<>≤≥\\]/.test(expression)
      && /[A-Za-z0-9\\{}()[\]^_<>≤≥]/.test(expression)
    );
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
      return next;
    }
    return -1;
  }

  function isTrailingPunctuation(value) {
    return !value || /^[\s.,;:!?，。；：！？)\]}、]/.test(value);
  }

  function inlineCloseLength(source, closeIndex) {
    if (source[closeIndex + 1] === '$' && isTrailingPunctuation(source.slice(closeIndex + 2))) {
      return 2;
    }
    return 1;
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

  function hasMathSyntax(source) {
    return (
      source.includes('$')
      || source.includes('\\(')
      || source.includes('\\[')
      || BARE_LATEX_COMMAND_PATTERN.test(source)
    );
  }

  function normalizeBareLatexMatch(raw) {
    let value = String(raw || '');
    let consumeLength = value.length;
    if (value.startsWith('$')) {
      if (value.slice(1).includes('$')) {
        return null;
      }
      value = value.slice(1);
      consumeLength -= 1;
    }
    const leading = value.match(/^\s+/);
    if (leading) {
      value = value.slice(leading[0].length);
      consumeLength -= leading[0].length;
    }
    const trailing = value.match(/\s+$/);
    if (trailing) {
      value = value.slice(0, -trailing[0].length);
      consumeLength -= trailing[0].length;
    }
    const proseTail = value.match(/\s+[A-Za-z]{2,}(?:\s+[A-Za-z]{2,})*[.,;:!?]*$/);
    if (proseTail) {
      value = value.slice(0, -proseTail[0].length);
      consumeLength -= proseTail[0].length;
    }
    if (
      !value
      || value.includes('$$')
      || value.includes('**')
      || /[\n#]/.test(value)
      || !BARE_LATEX_COMMAND_PATTERN.test(value)
    ) {
      return null;
    }
    return { value, consumeLength: Math.max(1, consumeLength + (raw.startsWith('$') ? 1 : 0)) };
  }

  function isLikelyInlineMathValue(raw) {
    const value = String(raw || '').trim();
    if (
      !value
      || value.includes('$$')
      || value.includes('**')
      || /[\n#]/.test(value)
    ) {
      return false;
    }
    if (CJK_TEXT_PATTERN.test(value) && !/\\(?:text|mathrm|operatorname)(?![A-Za-z])/.test(value)) {
      return false;
    }
    return (
      BARE_LATEX_COMMAND_PATTERN.test(value)
      || /[A-Za-z0-9]/.test(value)
      || /[=+\-*/^_|()[\]{},.]/.test(value)
    );
  }

  function pushTextParts(parts, value) {
    const source = String(value || '');
    if (!source) {
      return;
    }
    let last = 0;
    for (const match of source.matchAll(BARE_LATEX_SPAN_PATTERN)) {
      const raw = match[0] || '';
      const start = match.index || 0;
      const normalized = normalizeBareLatexMatch(raw);
      if (!normalized) {
        continue;
      }
      if (start > last) {
        parts.push({ type: 'text', value: source.slice(last, start) });
      }
      parts.push({ type: 'math', value: normalized.value, display: false });
      last = start + normalized.consumeLength;
    }
    if (last < source.length) {
      parts.push({ type: 'text', value: source.slice(last) });
    }
  }

  function splitByMath(text) {
    const parts = [];
    const source = String(text || '').replace(/\\\$\\\$/g, () => '$$').replace(/\\\$\$/g, () => '$$');
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
            pushTextParts(parts, source.slice(last, index));
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
          pushTextParts(parts, source.slice(last, index));
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
        pushTextParts(parts, source.slice(last, index));
      }
      const mathValue = source.slice(index + 1, inlineCloser).trim();
      if (mathValue && isLikelyInlineMathValue(mathValue)) {
        parts.push({ type: 'math', value: mathValue, display: false });
        const closeLength = inlineCloseLength(source, inlineCloser);
        index = inlineCloser + closeLength;
      } else {
        parts.push({ type: 'text', value: source.slice(index, inlineCloser + 1) });
        index = inlineCloser + 1;
      }
      last = index;
    }
    if (last < source.length) {
      pushTextParts(parts, source.slice(last));
    }
    return parts;
  }

  function normalizeLatexForKatex(value) {
    return String(value || '')
      .replace(DOUBLE_ESCAPED_LATEX_COMMAND_PATTERN, '\\')
      .replace(/</g, '\\lt ')
      .replace(/>/g, '\\gt ');
  }

  function renderMathPart(part) {
    const wrapper = part.display ? '$$' : '$';
    if (!part.value) {
      return `<code>${wrapper}${escapeHTML(part.value)}${wrapper}</code>`;
    }
    const latex = normalizeLatexForKatex(part.value);
    try {
      const rendered = window.katex.renderToString(latex, {
        displayMode: part.display,
        throwOnError: false,
        trust: false,
      });
      if (rendered.includes('katex-error')) {
        return `<code>${wrapper}${escapeHTML(part.value)}${wrapper}</code>`;
      }
      return rendered;
    } catch (_error) {
      return `<code>${wrapper}${escapeHTML(part.value)}${wrapper}</code>`;
    }
  }

  function renderInlineMarkdown(escaped) {
    return String(escaped || '')
      .replace(/`([^`\n]{1,180})`/g, '<code>$1</code>')
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/\*\*/g, '');
  }

  function renderEscapedInlineMarkdown(value) {
    return renderInlineMarkdown(escapeHTML(value));
  }

  function renderMathAndInlineMarkdown(value) {
    const renderedMath = [];
    const html = splitByMath(value).map((part) => {
      if (part.type !== 'math') {
        return escapeHTML(part.value);
      }
      const token = `@@STUDY_MATH_${renderedMath.length}@@`;
      renderedMath.push(renderMathPart(part));
      return token;
    }).join('');
    return renderInlineMarkdown(html).replace(/@@STUDY_MATH_(\d+)@@/g, (_match, index) => (
      renderedMath[Number(index)] || ''
    ));
  }

  function studySectionMeta(value) {
    const normalized = String(value || '')
      .replace(/^#{1,4}\s+/, '')
      .replace(/^\*\*(.+?)\*\*$/, '$1')
      .replace(/[：:]\s*$/, '')
      .trim()
      .toLowerCase();
    const variants = {
      '解析': ['analysis', '解析'],
      '题目解析': ['analysis', '题目解析'],
      '題目解析': ['analysis', '題目解析'],
      'problem analysis': ['analysis', 'Problem Analysis'],
      '解题过程': ['process', '解题过程'],
      '解題過程': ['process', '解題過程'],
      'solution process': ['process', 'Solution Process'],
      '答案': ['answer', '答案'],
      'final answer': ['answer', 'Final Answer'],
      '举一反三': ['transfer', '举一反三'],
      '舉一反三': ['transfer', '舉一反三'],
      'transfer practice': ['transfer', 'Transfer Practice'],
    };
    const match = variants[normalized];
    if (!match) {
      return null;
    }
    return { variant: match[0], title: match[1] };
  }

  function renderMarkdownBlocks(text, inlineRenderer) {
    const renderInline = typeof inlineRenderer === 'function' ? inlineRenderer : renderInlineMarkdown;
    const lines = String(text || '').split(/\r?\n/);
    const blocks = [];
    let inStudySection = false;
    const closeList = () => {};
    const closeStudySection = () => {
      closeList();
      if (inStudySection) {
        blocks.push('</section>');
        inStudySection = false;
      }
    };
    for (let index = 0; index < lines.length; index += 1) {
      const line = lines[index];
      const trimmed = line.trim();
      if (!trimmed) {
        closeList();
        continue;
      }
      const displayMath = trimmed.match(/^\$\$\s*(.+?)\s*\$\$$/);
      if (displayMath) {
        closeList();
        blocks.push(renderMathPart({ type: 'math', value: displayMath[1], display: true }));
        continue;
      }
      const displayMathBlock = trimmed.match(/^\$\$\s*(.*)$/);
      if (displayMathBlock) {
        const mathLines = [];
        if (displayMathBlock[1]) {
          mathLines.push(displayMathBlock[1]);
        }
        let closer = -1;
        let trailingCloseText = '';
        for (let scan = index + 1; scan < lines.length; scan += 1) {
          const closeAt = lines[scan].indexOf('$$');
          if (closeAt !== -1) {
            const beforeClose = lines[scan].slice(0, closeAt).trim();
            if (beforeClose) {
              mathLines.push(beforeClose);
            }
            trailingCloseText = lines[scan].slice(closeAt + 2).trim();
            closer = scan;
            break;
          }
          mathLines.push(lines[scan]);
        }
        if (closer !== -1) {
          closeList();
          blocks.push(renderMathPart({ type: 'math', value: mathLines.join('\n').trim(), display: true }));
          if (trailingCloseText) {
            blocks.push(`<p>${renderInline(trailingCloseText)}</p>`);
          }
          index = closer;
          continue;
        }
      }
      const heading = trimmed.match(/^(#{1,4})\s+(.+)$/);
      if (heading) {
        const studySection = studySectionMeta(heading[2]);
        if (studySection) {
          closeStudySection();
          blocks.push(`<section class="study-reply-section study-reply-section--${studySection.variant}">`);
          blocks.push(`<h3 class="study-reply-section__title">${renderInline(studySection.title)}</h3>`);
          inStudySection = true;
          continue;
        }
        closeStudySection();
        const level = Math.min(4, heading[1].length);
        blocks.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
        continue;
      }
      const studySection = studySectionMeta(trimmed);
      if (studySection) {
        closeStudySection();
        blocks.push(`<section class="study-reply-section study-reply-section--${studySection.variant}">`);
        blocks.push(`<h3 class="study-reply-section__title">${renderInline(studySection.title)}</h3>`);
        inStudySection = true;
        continue;
      }
      const bullet = trimmed.match(/^(?:[-*+])\s+(.+)$/);
      if (bullet) {
        closeList();
        blocks.push(`<p class="study-reply-bullet">&bull; ${renderInline(bullet[1])}</p>`);
        continue;
      }
      closeList();
      blocks.push(`<p>${renderInline(trimmed)}</p>`);
    }
    closeStudySection();
    return blocks.join('\n');
  }

  function renderMathInText(text) {
    const source = String(text || '').replace(/\\\$\\\$/g, () => '$$').replace(/\\\$\$/g, () => '$$');
    if (!source) {
      return '';
    }
    if (!hasMathSyntax(source) || !window.katex || typeof window.katex.renderToString !== 'function') {
      return renderMarkdownBlocks(source, renderEscapedInlineMarkdown);
    }
    try {
      return renderMarkdownBlocks(source, renderMathAndInlineMarkdown);
    } catch (_error) {
      return renderMarkdownBlocks(source, renderEscapedInlineMarkdown);
    }
  }

  window.renderMathInText = renderMathInText;
  window.__studyCompanionMath = {
    escapeHTML,
    hasEscapedDelimiter,
    isLikelyCurrencyStart,
    findMathDelimiter,
    findBackslashMathDelimiter,
    hasMathSyntax,
    isLikelyInlineMathValue,
    normalizeLatexForKatex,
    splitByMath,
    studySectionMeta,
    renderMarkdownBlocks,
    renderMathInText,
  };
})();
