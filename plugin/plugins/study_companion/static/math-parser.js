(function () {
  const CURRENCY_START_PATTERN = /^\$(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?(?:[A-Z]{2,4}|%)?(?=$|[\s)\],.;!?+\-])/;
  const CURRENCY_AMOUNT_PATTERN = /^\$(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?(?:[A-Z]{2,4}|%)?/;
  const BARE_LATEX_COMMAND_PATTERN = /\\(?:vec|overrightarrow|overline|bar|hat|frac|sqrt|sum|prod|int|lim|cdot|times|angle|sin|cos|tan|log|ln|infty|to|left|right|mathbf|mathbb|mathrm)(?![A-Za-z])/;
  const DOUBLE_ESCAPED_LATEX_COMMAND_PATTERN = /\\\\(?=(?:vec|overrightarrow|overline|bar|hat|frac|sqrt|sum|prod|int|lim|cdot|times|angle|sin|cos|tan|log|ln|infty|to|left|right|mathbf|mathbb|mathrm)(?![A-Za-z]))/g;
  const BARE_LATEX_SPAN_PATTERN = /\$[^\n。；;!?！？]*\\(?:vec|overrightarrow|overline|bar|hat|frac|sqrt|sum|prod|int|lim|cdot|times|angle|sin|cos|tan|log|ln|infty|to|left|right|mathbf|mathbb|mathrm)(?![A-Za-z])[^\n。；;!?！？]*|\|?\\(?:vec|overrightarrow|overline|bar|hat|frac|sqrt|sum|prod|int|lim|cdot|times|angle|sin|cos|tan|log|ln|infty|to|left|right|mathbf|mathbb|mathrm)(?![A-Za-z])(?:\[[^\]\n]{1,40}\]|\{[^{}\n]{1,120}\}|[A-Za-z0-9\\()+\-*/=.,:_|^\s]){0,180}/g;

  const CJK_TEXT_PATTERN = /[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]/;

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
    if (raw.startsWith('$') && isCurrencyProseBeforeBareLatex(value)) {
      return null;
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

  function isCurrencyProseBeforeBareLatex(value) {
    const amount = String(value || '').match(/^(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?(?:[A-Z]{2,4}|%)?/);
    if (!amount) {
      return false;
    }
    const command = String(value || '').search(BARE_LATEX_COMMAND_PATTERN);
    if (command === -1) {
      return false;
    }
    const between = String(value || '').slice(amount[0].length, command);
    return /[A-Za-z]{2,}/.test(between);
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
        const recovered = recoverCurrencyProseBareLatexMatch(raw);
        if (recovered) {
          const textValue = `${start > last ? source.slice(last, start) : ''}${recovered.textValue}`;
          if (textValue) {
            parts.push({ type: 'text', value: textValue });
          }
          parts.push({ type: 'math', value: recovered.mathValue, display: false });
          if (recovered.trailingText) {
            parts.push({ type: 'text', value: recovered.trailingText });
          }
          last = start + recovered.consumeLength;
        }
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

  function recoverCurrencyProseBareLatexMatch(raw) {
    if (!String(raw || '').startsWith('$')) {
      return null;
    }
    const value = String(raw || '').slice(1);
    if (!isCurrencyProseBeforeBareLatex(value)) {
      return null;
    }
    const command = value.search(BARE_LATEX_COMMAND_PATTERN);
    if (command <= 0) {
      return null;
    }
    const normalized = normalizeBareLatexMatch(value.slice(command));
    if (!normalized) {
      return null;
    }
    const trailingPunctuation = normalized.value.match(/[.,;:!?]+$/);
    const trailingText = trailingPunctuation ? trailingPunctuation[0] : '';
    const mathValue = trailingText ? normalized.value.slice(0, -trailingText.length) : normalized.value;
    if (!mathValue) {
      return null;
    }
    return {
      textValue: `$${value.slice(0, command)}`,
      mathValue,
      trailingText,
      consumeLength: 1 + command + normalized.consumeLength,
    };
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

  window.__studyCompanionMathParser = {
    CURRENCY_START_PATTERN,
    hasEscapedDelimiter,
    isLikelyCurrencyStart,
    findMathDelimiter,
    findBackslashMathDelimiter,
    hasMathSyntax,
    splitByMath,
    isLikelyInlineMathValue,
    normalizeLatexForKatex,
  };
})();
