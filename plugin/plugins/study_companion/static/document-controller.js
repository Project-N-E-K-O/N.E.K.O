(() => {
  'use strict';

  function create(dependencies) {
    const {
      pluginId,
      callPlugin,
      i18n: { t, tf },
      ui: {
        setStatus,
        setReply,
        setPasteError,
        scrollReplyIntoView,
        formatPluginError,
      },
      onAnalysisComplete,
    } = dependencies;

    const studyInput = document.getElementById('studyInput');
    const studyInputPasteError = document.getElementById('studyInputPasteError');
    const studyDocumentDropZone = document.getElementById('studyDocumentDropZone');
    const studyDocumentInput = document.getElementById('studyDocumentInput');
    const studyDocumentImportBtn = document.getElementById('studyDocumentImportBtn');
    const studyDocumentCard = document.getElementById('studyDocumentCard');
    const studyDocumentName = document.getElementById('studyDocumentName');
    const studyDocumentMeta = document.getElementById('studyDocumentMeta');
    const studyDocumentState = document.getElementById('studyDocumentState');
    const studyDocumentTruncated = document.getElementById('studyDocumentTruncated');
    const studyDocumentCancelBtn = document.getElementById('studyDocumentCancelBtn');
    const studyDocumentKindSelect = document.getElementById('studyDocumentKind');
    const studyDocumentInstruction = document.getElementById('studyDocumentInstruction');
    const studyDocumentEditor = document.getElementById('studyDocumentEditor');
    const studyDocumentText = document.getElementById('studyDocumentText');
    const studyDocumentAnalyzeBtn = document.getElementById('studyDocumentAnalyzeBtn');
    const studyDocumentRemoveBtn = document.getElementById('studyDocumentRemoveBtn');
    const studyDocumentProgress = document.getElementById('studyDocumentProgress');
    const studyDocumentProgressBar = document.getElementById('studyDocumentProgressBar');
    const studyDocumentProgressText = document.getElementById('studyDocumentProgressText');

    let bound = false;
    let disposed = false;
    let documentBusy = false;
    let documentReadGeneration = 0;
    let importedDocument = null;
    let documentSource = '';
    let documentKind = 'auto';
    let documentRequestController = null;
    const listeners = [];
    const documentJobStorageKey = 'study_companion.document_analysis_job_id';
    const pendingDocumentJobId = '__pending__';

    function listen(target, type, listener, options) {
      if (!target) return;
      target.addEventListener(type, listener, options);
      listeners.push({ target, type, listener, options });
    }

    function estimateDocumentTokens(text) {
      return Math.ceil(new TextEncoder().encode(text).byteLength / 3) || 1;
    }

    function decodeDocumentBuffer(buffer) {
      const bytes = new Uint8Array(buffer);
      let candidates;
      if (bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) candidates = [['UTF-8', 'utf-8', bytes.subarray(3)]];
      else if (bytes[0] === 0xff && bytes[1] === 0xfe) candidates = [['UTF-16 LE', 'utf-16le', bytes.subarray(2)]];
      else if (bytes[0] === 0xfe && bytes[1] === 0xff) candidates = [['UTF-16 BE', 'utf-16be', bytes.subarray(2)]];
      else candidates = [['UTF-8', 'utf-8', bytes], ['GB18030', 'gb18030', bytes]];
      for (const [label, encoding, content] of candidates) {
        try {
          return { text: new TextDecoder(encoding, { fatal: true }).decode(content), encoding: label };
        } catch {}
      }
      throw new Error('document_encoding');
    }

    function documentTextProblem(text) {
      if (!text.trim()) return 'document_empty';
      let controls = 0;
      let replacements = 0;
      for (const character of text) {
        const code = character.codePointAt(0);
        if (code === 0xfffd) replacements += 1;
        if ((code < 32 && ![9, 10, 13].includes(code)) || code === 127) controls += 1;
      }
      if (text.includes('\0') || controls / text.length > 0.01) return 'document_binary';
      if (replacements / text.length > 0.001) return 'document_encoding';
      if (/data:[^\s,;]+(?:;[^\s,;=]+)*;base64,[A-Za-z0-9+/=]{4096,}/i.test(text)) return 'document_unsafe_content';
      if (text.split(/\r?\n/).some((line) => line.length > 32768 || /^[A-Za-z0-9+/=]{8192,}$/.test(line.trim()))) return 'document_unsafe_content';
      return '';
    }

    function formatDocumentDiagnostic(diagnostic) {
      const code = (diagnostic || '').trim();
      const validation = {
        empty_document: 'empty',
        binary_document: 'binary',
        invalid_document_encoding: 'encoding',
        unsupported_document_type: 'type',
        unsafe_document_content: 'unsafe_content',
        analysis_instruction_too_long: 'instruction_too_long',
        unsupported_document_kind: 'invalid_kind',
        invalid_document_name: 'invalid_name',
        document_canceled: 'canceled',
        unsupported_document: 'type',
        document_too_large: 'parse_too_large',
        invalid_pdf: 'invalid_pdf',
        invalid_ooxml: 'invalid_ooxml',
        encrypted_pdf_unsupported: 'encrypted_pdf_unsupported',
        legacy_office_unsupported: 'legacy_office_unsupported',
        macro_document_unsupported: 'macro_document_unsupported',
        no_readable_text: 'no_readable_text',
        garbled_text: 'garbled_text',
        document_parse_failed: 'parse_failed',
        document_parse_timeout: 'parse_timeout',
        document_parse_permission_denied: 'parse_permission_denied',
      };
      const analysisErrors = 'timeout rate_limited authentication_failed model_not_supported provider_unavailable invalid_endpoint invalid_request unsafe_model_output llm_call_failed';
      return t(`ui.error.document_${analysisErrors.includes(code) ? `analysis_${code}` : validation[code] || code.replace(/^document_/, '') || 'analysis_failed'}`);
    }

    const documentJobs = {
      currentId: '',
      cancelRequested: false,
      remember(jobId) {
        this.currentId = String(jobId || '');
        try {
          if (this.currentId) window.sessionStorage.setItem(documentJobStorageKey, this.currentId);
          else window.sessionStorage.removeItem(documentJobStorageKey);
        } catch (_error) {
          // Storage may be unavailable in a restricted webview.
        }
      },
      savedId() {
        try {
          return String(window.sessionStorage.getItem(documentJobStorageKey) || '');
        } catch (_error) {
          return '';
        }
      },
      markPending() {
        try {
          window.sessionStorage.setItem(documentJobStorageKey, pendingDocumentJobId);
        } catch (_error) {
          // Storage may be unavailable in a restricted webview.
        }
      },
      render(data = {}) {
        this.currentId = data.job_id || this.currentId;
        const completed = Number(data.completed_chunks) || 0;
        const total = Number(data.total_chunks) || Number(data.chunks) || 0;
        const progress = Math.max(0, Math.min(1, Number(data.progress) || 0));
        studyDocumentProgressBar.value = progress;
        studyDocumentProgressText.textContent = total
          ? tf('ui.document.progress_chunks', '', { completed, total })
          : `${Math.round(progress * 100)}%`;
        const stage = data.stage || 'validating';
        studyDocumentState.textContent = t(`ui.document.stage.${stage}`, stage);
      },
      async run(args, signal, update) {
        this.cancelRequested = false;
        this.markPending();
        let data;
        try {
          data = await callPlugin('study_start_document_analysis', args, signal);
        } catch (error) {
          if (!signal.aborted) this.remember('');
          throw error;
        }
        this.remember(data.job_id || '');
        if (this.cancelRequested && this.currentId) {
          try {
            data = await callPlugin('study_cancel_document_analysis', {
              job_id: this.currentId,
              cancellation_source: 'user',
            }, signal);
            this.remember('');
            return data;
          } catch (error) {
            this.cancelRequested = false;
            studyDocumentCancelBtn.disabled = false;
            studyDocumentState.textContent = formatPluginError(error);
          }
        }
        this.render(data);
        update(data);
        const jobId = data.job_id;
        if (!jobId || ['completed', 'failed', 'canceled'].includes(data.status)) {
          this.remember('');
          return data;
        }
        let delay = 1000;
        while (!signal.aborted) {
          await new Promise((resolve) => setTimeout(resolve, delay));
          if (signal.aborted) break;
          data = await callPlugin('study_document_analysis_status', { job_id: jobId }, signal);
          this.render(data);
          update(data);
          if (['completed', 'failed', 'canceled'].includes(data.status)) {
            this.remember('');
            return data;
          }
          delay = 2000;
        }
        throw new DOMException('Aborted', 'AbortError');
      },
      async cancel() {
        this.cancelRequested = true;
        studyDocumentCancelBtn.disabled = true;
        studyDocumentState.textContent = t('ui.document.stage.canceling', 'Canceling analysis...');
        const jobId = this.currentId;
        if (!jobId) return;
        try {
          const data = await callPlugin('study_cancel_document_analysis', {
            job_id: jobId,
            cancellation_source: 'user',
          });
          documentRequestController?.abort();
          this.remember('');
          studyDocumentState.textContent = formatDocumentDiagnostic(data.diagnostic || 'document_canceled');
        } catch (error) {
          this.cancelRequested = false;
          studyDocumentCancelBtn.disabled = false;
          studyDocumentState.textContent = formatPluginError(error);
        }
      },
      async resume(signal, update) {
        const savedId = this.savedId();
        if (!savedId) return null;
        let data = null;
        if (savedId !== pendingDocumentJobId) {
          try {
            data = await callPlugin(
              'study_document_analysis_status',
              { job_id: savedId },
              signal,
            );
          } catch (_error) {
            data = null;
          }
        }
        if (data?.diagnostic === 'document_job_not_found') data = null;
        if (!data) {
          data = await callPlugin('study_active_document_analysis', {}, signal);
        }
        const jobId = String(data?.job_id || '');
        if (!jobId || data.status === 'idle') {
          this.remember('');
          return null;
        }
        this.remember(jobId);
        this.render(data);
        update(data);
        if (['completed', 'failed', 'canceled'].includes(data.status)) {
          this.remember('');
          return data;
        }
        let delay = 1000;
        while (!signal.aborted) {
          await new Promise((resolve) => setTimeout(resolve, delay));
          if (signal.aborted) break;
          data = await callPlugin(
            'study_document_analysis_status',
            { job_id: jobId },
            signal,
          );
          this.render(data);
          update(data);
          if (['completed', 'failed', 'canceled'].includes(data.status)) {
            this.remember('');
            return data;
          }
          delay = 2000;
        }
        throw new DOMException('Aborted', 'AbortError');
      },
    };

    function setDocumentBusy(busy) {
      documentBusy = Boolean(busy);
      if (studyDocumentCard) studyDocumentCard.dataset.busy = documentBusy ? 'true' : 'false';
      studyDocumentAnalyzeBtn.disabled = documentBusy || !importedDocument;
      studyDocumentRemoveBtn.disabled = studyDocumentImportBtn.disabled = studyDocumentInput.disabled = studyDocumentKindSelect.disabled = studyDocumentInstruction.disabled = documentBusy;
      studyDocumentText.readOnly = documentBusy;
      if (documentBusy) studyDocumentEditor.open = false;
      studyDocumentCancelBtn.hidden = !documentBusy;
      if (!documentBusy) studyDocumentCancelBtn.disabled = false;
      studyDocumentProgress.hidden = !documentBusy;
    }

    function documentErrorMessage(code) {
      return t(`ui.error.${code || 'document_read'}`);
    }

    function updateDocumentCard(options = {}) {
      if (!importedDocument) {
        if (studyDocumentCard) studyDocumentCard.hidden = true;
        if (studyDocumentTruncated) studyDocumentTruncated.hidden = true;
        return;
      }
      const bytes = new TextEncoder().encode(documentSource).byteLength;
      const tokens = estimateDocumentTokens(documentSource);
      const problem = documentTextProblem(documentSource) || (bytes > 524288 ? 'document_too_large' : tokens > 160000 ? 'document_too_long' : '');
      importedDocument = { ...importedDocument, bytes, tokens, problem, modified: importedDocument.modified || Boolean(options.modified) };
      if (studyDocumentCard) studyDocumentCard.hidden = false;
      studyDocumentName.textContent = importedDocument.name;
      studyDocumentMeta.textContent = tf('ui.document.meta', '', {
        size: `${(importedDocument.originalSize / 1024).toFixed(1)} KB`,
        encoding: importedDocument.encoding,
        chars: documentSource.length.toLocaleString(),
        tokens: tokens.toLocaleString(),
      });
      if (studyDocumentTruncated) studyDocumentTruncated.hidden = !importedDocument.truncated;
      if (studyDocumentState && !documentBusy) {
        studyDocumentState.textContent = problem
          ? documentErrorMessage(problem)
          : (tokens > 48000
            ? tf('ui.document.chunked_mode_hint', '', { chunks: Math.ceil(tokens / 10000) })
            : t('ui.document.direct_mode_hint'));
      }
      studyDocumentAnalyzeBtn.disabled = documentBusy || Boolean(problem);
    }

    function removeImportedDocument() {
      documentReadGeneration += 1;
      documentRequestController?.abort();
      documentRequestController = null;
      importedDocument = null;
      documentSource = '';
      documentKind = 'auto';
      studyDocumentInput.value = studyDocumentText.value = studyDocumentInstruction.value = '';
      studyDocumentEditor.open = false;
      studyDocumentKindSelect.value = 'auto';
      studyDocumentDropZone.dataset.dragging = 'false';
      updateDocumentCard();
      setDocumentBusy(false);
      setPasteError(studyInputPasteError, '');
    }

    async function importDocumentFile(file) {
      const generation = ++documentReadGeneration;
      documentRequestController?.abort();
      const controller = new AbortController();
      documentRequestController = controller;
      setPasteError(studyInputPasteError, '');
      if (!file) throw new Error('document_read');
      if (/\.doc$/i.test(file.name)) throw new Error('document_legacy_office_unsupported');
      if (/\.docm$/i.test(file.name)) throw new Error('document_macro_document_unsupported');
      if (!/\.(txt|md|markdown|pdf|docx)$/i.test(file.name)) throw new Error('document_type');
      const declaredType = String(file.type || '').toLowerCase();
      const parsedDocument = /\.(pdf|docx)$/i.test(file.name);
      const expectedParsedType = /\.pdf$/i.test(file.name)
        ? 'application/pdf'
        : 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
      const allowedTypes = parsedDocument
        ? [expectedParsedType, 'application/octet-stream']
        : ['text/plain', 'text/markdown', 'application/octet-stream'];
      if (declaredType && !allowedTypes.includes(declaredType)) throw new Error('document_type');
      if (file.size > (parsedDocument ? 16 * 1024 * 1024 : 524288)) {
        throw new Error(parsedDocument ? 'document_parse_too_large' : 'document_too_large');
      }
      const lowerName = file.name.toLowerCase();
      const sourceType = lowerName.endsWith('.pdf') ? 'pdf'
        : lowerName.endsWith('.docx') ? 'docx'
          : lowerName.endsWith('.txt') ? 'txt' : 'markdown';
      const analysisType = sourceType === 'pdf' ? 'application/pdf'
        : sourceType === 'docx' ? 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
          : sourceType === 'txt' ? 'text/plain' : 'text/markdown';
      importedDocument = {
        name: (file.name.split(/[\\/]/).pop().replace(/[\0-\x1f\x7f]/g, '').trim() || 'document.txt').slice(0, 255),
        sourceType,
        type: analysisType,
        originalSize: file.size,
        encoding: '',
        truncated: false,
        meta: {},
        modified: false,
      };
      studyDocumentCard.hidden = false;
      studyDocumentName.textContent = importedDocument.name;
      studyDocumentMeta.textContent = `${(file.size / 1024).toFixed(1)} KB`;
      setDocumentBusy(true);
      studyDocumentState.textContent = t('ui.document.reading');
      try {
        let decoded;
        if (parsedDocument) {
          const formData = new FormData();
          formData.append('file', file, file.name);
          let response;
          let parseTimedOut = false;
          const parseTimeout = setTimeout(() => {
            parseTimedOut = true;
            controller.abort();
          }, 45000);
          try {
            response = await fetch('/api/documents/parse', {
              method: 'POST',
              body: formData,
              signal: controller.signal,
            });
          } catch (error) {
            if (parseTimedOut) throw new Error('document_parse_timeout');
            if (controller.signal.aborted) throw error;
            throw new Error(error instanceof DOMException && error.name === 'TimeoutError'
              ? 'document_parse_timeout'
              : 'document_parse_failed');
          } finally {
            clearTimeout(parseTimeout);
          }
          const body = await response.json().catch(() => ({}));
          if (!response.ok) {
            throw new Error(body?.detail?.code || body?.code || 'document_parse_failed');
          }
          const payload = body?.document || body?.item || body;
          const returnedType = String(payload?.sourceType || payload?.documentType || sourceType).toLowerCase();
          if (returnedType !== sourceType || typeof payload?.content !== 'string') {
            throw new Error('document_parse_failed');
          }
          decoded = { text: payload.content, encoding: String(payload.encoding || 'document-parser') };
          importedDocument = {
            ...importedDocument,
            name: String(payload.name || importedDocument.name).slice(0, 255),
            originalSize: Number(payload.originalSize ?? payload.size ?? file.size) || file.size,
            truncated: payload.truncated === true,
            meta: payload.meta && typeof payload.meta === 'object' ? payload.meta : {},
          };
        } else {
          decoded = decodeDocumentBuffer(await file.arrayBuffer());
        }
        if (controller.signal.aborted || generation !== documentReadGeneration) return;
        const problem = documentTextProblem(decoded.text);
        if (problem) throw new Error(problem);
        importedDocument = { ...importedDocument, encoding: decoded.encoding };
        documentSource = decoded.text;
        documentKind = 'auto';
        studyDocumentText.value = decoded.text;
        studyDocumentKindSelect.value = documentKind;
        updateDocumentCard();
      } catch (error) {
        if (generation === documentReadGeneration) {
          importedDocument = null;
          updateDocumentCard();
        }
        throw error;
      } finally {
        if (documentRequestController === controller) documentRequestController = null;
        if (generation === documentReadGeneration) setDocumentBusy(false);
      }
    }

    function acceptDocumentFiles(files) {
      if (documentBusy) return undefined;
      const list = Array.from(files || []);
      if (list.length !== 1) throw new Error(list.length > 1 ? 'document_multiple' : 'document_read');
      return importDocumentFile(list[0]);
    }

    function reportDocumentImportError(error) {
      studyDocumentInput.value = '';
      setPasteError(studyInputPasteError, documentErrorMessage(error instanceof Error ? error.message : 'document_read'));
    }

    function handleDocumentPaste(event) {
      const directFiles = Array.from(event.clipboardData?.files || []);
      const itemFiles = Array.from(event.clipboardData?.items || [])
        .filter((item) => item.kind === 'file')
        .map((item) => item.getAsFile())
        .filter(Boolean);
      const files = directFiles.length ? directFiles : itemFiles;
      if (!files.length || files.every((file) => String(file.type || '').startsWith('image/'))) return;
      event.preventDefault();
      Promise.resolve(acceptDocumentFiles(files)).catch(reportDocumentImportError);
    }

    function handleDocumentDrop(event) {
      event.preventDefault();
      studyDocumentDropZone.dataset.dragging = 'false';
      Promise.resolve(acceptDocumentFiles(event.dataTransfer?.files)).catch(reportDocumentImportError);
    }

    async function analyzeDocument() {
      if (!importedDocument || documentBusy) return;
      updateDocumentCard();
      if (importedDocument.problem) throw new Error(importedDocument.problem);
      const controller = new AbortController();
      documentRequestController?.abort();
      documentRequestController = controller;
      setDocumentBusy(true);
      setStatus(t('ui.status.analyzing_document'));
      setReply(t('ui.status.analyzing_document'));
      scrollReplyIntoView();
      try {
        const data = await documentJobs.run({
          document_name: importedDocument.name,
          document_type: importedDocument.type,
          document_text: documentSource,
          analysis_kind: documentKind,
          analysis_instruction: studyDocumentInstruction?.value.trim() || '',
          locale: window.I18n?.lang?.() || document.documentElement.lang || '',
        }, controller.signal, () => {});
        if (controller.signal.aborted) return;
        const failed = data.status !== 'completed' || data.degraded;
        setStatus(failed ? t('ui.status.error', 'Error') : t('ui.status.document_complete'));
        setReply(failed ? formatDocumentDiagnostic(data.diagnostic) : (data.reply || data.summary || ''));
        studyDocumentState.textContent = failed ? formatDocumentDiagnostic(data.diagnostic) : t('ui.status.document_complete');
        await onAnalysisComplete({ updateReply: false });
      } catch (error) {
        if (controller.signal.aborted) return;
        setStatus(t('ui.status.error', 'Error'));
        setReply(/timed out|timeout/i.test(error.message) ? t('ui.error.document_analysis_timeout') : formatPluginError(error));
      } finally {
        if (documentRequestController === controller) documentRequestController = null;
        setDocumentBusy(false);
      }
    }

    async function resumeDocumentAnalysis() {
      if (documentBusy || !studyDocumentAnalyzeBtn) return;
      const controller = new AbortController();
      documentRequestController?.abort();
      documentRequestController = controller;
      try {
        const data = await documentJobs.resume(
          controller.signal,
          () => setDocumentBusy(true),
        );
        if (!data || controller.signal.aborted) return;
        const failed = data.status !== 'completed' || data.degraded;
        setStatus(failed ? t('ui.status.error', 'Error') : t('ui.status.document_complete'));
        setReply(failed ? formatDocumentDiagnostic(data.diagnostic) : (data.reply || data.summary || ''));
        studyDocumentState.textContent = failed ? formatDocumentDiagnostic(data.diagnostic) : t('ui.status.document_complete');
        if (!failed) await onAnalysisComplete({ updateReply: false });
      } catch (error) {
        if (!controller.signal.aborted) setReply(formatPluginError(error));
      } finally {
        if (documentRequestController === controller) documentRequestController = null;
        setDocumentBusy(false);
      }
    }

    function handlePageHide() {
      dispose();
    }

    function bind() {
      if (bound || disposed) return;
      bound = true;
      listen(studyDocumentAnalyzeBtn, 'click', analyzeDocument);
      listen(studyInput, 'paste', handleDocumentPaste);
      listen(studyDocumentImportBtn, 'click', () => studyDocumentInput.click());
      listen(studyDocumentInput, 'change', () => {
        Promise.resolve(acceptDocumentFiles(studyDocumentInput.files)).catch(reportDocumentImportError);
      });
      listen(studyDocumentRemoveBtn, 'click', removeImportedDocument);
      listen(studyDocumentCancelBtn, 'click', () => documentJobs.cancel());
      listen(studyDocumentKindSelect, 'change', () => {
        documentKind = studyDocumentKindSelect.value || 'auto';
      });
      listen(studyDocumentEditor, 'toggle', () => {
        if (documentBusy && studyDocumentEditor.open) studyDocumentEditor.open = false;
      });
      listen(studyDocumentText, 'input', () => {
        if (documentBusy || !importedDocument) return;
        documentSource = studyDocumentText.value;
        updateDocumentCard({ modified: true });
      });
      listen(window, 'pagehide', handlePageHide, { once: true });
      listen(studyDocumentDropZone, 'dragenter', (event) => {
        event.preventDefault();
        studyDocumentDropZone.dataset.dragging = 'true';
      });
      listen(studyDocumentDropZone, 'dragover', (event) => {
        event.preventDefault();
        if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
      });
      listen(studyDocumentDropZone, 'dragleave', (event) => {
        if (!studyDocumentDropZone.contains(event.relatedTarget)) studyDocumentDropZone.dataset.dragging = 'false';
      });
      listen(studyDocumentDropZone, 'drop', handleDocumentDrop);
      void resumeDocumentAnalysis();
    }

    function dispose() {
      if (disposed) return;
      disposed = true;
      documentRequestController?.abort();
      documentRequestController = null;
      for (const { target, type, listener, options } of listeners.splice(0)) {
        target.removeEventListener(type, listener, options);
      }
      bound = false;
    }

    return Object.freeze({ bind, dispose });
  }

  window.StudyDocumentController = Object.freeze({ create });
})();
