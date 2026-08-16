from __future__ import annotations

import gzip
import os
import shutil
import subprocess
from pathlib import Path

import pytest


STATIC = Path(__file__).parents[3] / "plugins" / "study_companion" / "static"


def test_static_document_analysis_uses_cancellable_long_job_contract() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    main = (STATIC / "main.js").read_text(encoding="utf-8")
    surface_panels = (STATIC / "surface-panels.js").read_text(encoding="utf-8")
    controller = (STATIC / "document-controller.js").read_text(encoding="utf-8")

    assert 'id="studyDocumentProgress"' in html
    assert 'id="studyDocumentProgressBar"' in html
    assert 'data-i18n="ui.document.cancel_analysis"' in html
    assert "study_start_document_analysis" in controller
    assert "study_document_analysis_status" in controller
    assert "study_cancel_document_analysis" in controller
    assert "let delay = 1000" in controller
    assert "delay = 2000" in controller
    assert "let consecutiveFailures = 0" in controller
    assert "consecutiveFailures < 3" in controller
    assert "Math.min(consecutiveFailures - 2, 4)" in controller
    assert "setReply(message)" in controller
    assert "cancelRequested" in controller
    assert "if (!jobId) return" in controller
    assert "navigator.sendBeacon('/runs'" not in controller
    assert "window.StudyDocumentJobs" not in controller
    assert "StudyDocumentJobs" not in surface_panels
    assert "study_start_document_analysis" not in surface_panels
    assert "StudyDocumentController.create" in main
    assert "documentController.bind()" in main
    assert "study_analyze_document', {" not in main
    assert "StudyDocumentJobs" not in main
    assert "studyDocument" not in main

    script_tags = [
        html.index("document-controller.js"),
        html.index("main.js"),
    ]
    assert script_tags == sorted(script_tags)


def test_static_document_busy_isolated_and_budget_hints_match_backend_modes() -> None:
    controller = (STATIC / "document-controller.js").read_text(encoding="utf-8")
    style = (STATIC / "style.css").read_text(encoding="utf-8")

    busy = controller[
        controller.index("function setDocumentBusy") : controller.index(
            "function documentErrorMessage"
        )
    ]
    assert "setPanelBusy" not in busy
    assert "studyDocumentCard.dataset.busy" in busy
    assert "tokens > 160000 ? 'document_too_long'" in controller
    assert "tokens > 48000" in controller
    assert "ui.document.chunked_mode_hint" in controller
    assert "ui.document.direct_mode_hint" in controller
    assert "function estimateDocumentTokens" in controller
    assert "function decodeDocumentBuffer" in controller
    assert "function documentTextProblem" in controller
    assert '.study-document-card[data-busy="true"]' in style
    assert '.main-view[data-busy="true"] .study-document-card' not in style


def test_static_document_import_supports_pdf_docx_and_truncation_notice() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    controller = (STATIC / "document-controller.js").read_text(encoding="utf-8")

    assert ".pdf,.docx" in html
    assert 'id="studyDocumentTruncated"' in html
    assert "fetch('/api/documents/parse'" in controller
    assert "formData.append('file', file, file.name)" in controller
    assert "16 * 1024 * 1024" in controller
    assert "document_parse_timeout" in controller
    assert "payload.truncated === true" in controller
    assert "studyDocumentTruncated.hidden = !importedDocument.truncated" in controller


def test_static_document_controller_lifecycle_is_frozen_and_idempotent() -> None:
    if shutil.which("node") is None:
        pytest.skip("node is not installed")

    frontend_dir = Path(__file__).resolve().parents[4] / "frontend" / "plugin-manager"
    if not (frontend_dir / "node_modules" / "happy-dom").is_dir():
        pytest.skip("frontend/plugin-manager node_modules with happy-dom is not installed")

    script = r"""
import { Window } from 'happy-dom';
import fs from 'node:fs';
import path from 'node:path';

const staticDir = process.env.STUDY_COMPANION_STATIC_DIR;
const html = fs.readFileSync(path.join(staticDir, 'index.html'), 'utf8');
const source = fs.readFileSync(path.join(staticDir, 'document-controller.js'), 'utf8');
const window = new Window({ url: 'http://testserver/plugin/study_companion/ui/?locale=en' });
const { document } = window;
document.write(html);
document.close();

const observedTarget = document.getElementById('studyInput');
let added = 0;
let removed = 0;
const originalAdd = observedTarget.addEventListener;
const originalRemove = observedTarget.removeEventListener;
observedTarget.addEventListener = function (...args) {
  added += 1;
  return originalAdd.apply(this, args);
};
observedTarget.removeEventListener = function (...args) {
  removed += 1;
  return originalRemove.apply(this, args);
};

let aborts = 0;
const NativeAbortController = window.AbortController;
window.AbortController = class extends NativeAbortController {
  abort(...args) {
    aborts += 1;
    return super.abort(...args);
  }
};

window.eval(source);
const namespace = window.StudyDocumentController;
if (!Object.isFrozen(namespace)) throw new Error('controller namespace must be frozen');
const controller = namespace.create({
  pluginId: 'study_companion',
  callPlugin: async () => ({}),
  i18n: { t: (key, fallback = key) => fallback || key, tf: (key, fallback = key) => fallback || key },
  ui: {
    setStatus: () => {},
    setReply: () => {},
    setPasteError: () => {},
    scrollReplyIntoView: () => {},
    formatPluginError: (error) => String(error?.message || error),
  },
  onAnalysisComplete: async () => {},
});
if (!Object.isFrozen(controller)) throw new Error('controller instance must be frozen');
if (Object.keys(controller).sort().join(',') !== 'bind,dispose') {
  throw new Error(`unexpected public controller API: ${Object.keys(controller)}`);
}

controller.bind();
const addedAfterFirstBind = added;
controller.bind();
if (added !== addedAfterFirstBind) throw new Error('bind registered duplicate listeners');

const slowFile = {
  name: 'notes.txt',
  type: 'text/plain',
  size: 12,
  arrayBuffer: () => new Promise(() => {}),
};
const paste = new window.Event('paste', { bubbles: true, cancelable: true });
Object.defineProperty(paste, 'clipboardData', { value: { files: [slowFile], items: [] } });
document.getElementById('studyInput').dispatchEvent(paste);
await Promise.resolve();

controller.dispose();
const removedAfterFirstDispose = removed;
const abortsAfterFirstDispose = aborts;
controller.dispose();
if (removed !== removedAfterFirstDispose) throw new Error('dispose removed listeners twice');
if (aborts !== abortsAfterFirstDispose) throw new Error('dispose aborted work twice');
if (removedAfterFirstDispose === 0) throw new Error('dispose did not remove listeners');
if (abortsAfterFirstDispose !== 2) throw new Error(`dispose abort count: ${abortsAfterFirstDispose}`);
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=frontend_dir,
        env={**os.environ, "STUDY_COMPANION_STATIC_DIR": str(STATIC)},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_static_document_controller_preserves_import_analysis_and_cancel_behavior() -> None:
    if shutil.which("node") is None:
        pytest.skip("node is not installed")

    frontend_dir = Path(__file__).resolve().parents[4] / "frontend" / "plugin-manager"
    if not (frontend_dir / "node_modules" / "happy-dom").is_dir():
        pytest.skip("frontend/plugin-manager node_modules with happy-dom is not installed")

    script = r"""
import { Window } from 'happy-dom';
import fs from 'node:fs';
import path from 'node:path';

const staticDir = process.env.STUDY_COMPANION_STATIC_DIR;
const html = fs.readFileSync(path.join(staticDir, 'index.html'), 'utf8');
const source = fs.readFileSync(path.join(staticDir, 'document-controller.js'), 'utf8');

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function waitFor(predicate, message, attempts = 200) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error(message);
}

function bytesForText(text) {
  return new TextEncoder().encode(text);
}

function fileFromBytes(bytes, name = 'notes.txt', type = 'text/plain') {
  const content = Uint8Array.from(bytes);
  return {
    name,
    type,
    size: content.byteLength,
    async arrayBuffer() {
      return content.buffer.slice(content.byteOffset, content.byteOffset + content.byteLength);
    },
  };
}

function createEnvironment(
  callPlugin = async () => ({}),
  onAnalysisComplete = async () => {},
  beforeBind = () => {},
) {
  const window = new Window({ url: 'http://testserver/plugin/study_companion/ui/?locale=en' });
  const { document } = window;
  document.write(html);
  document.close();
  const pasteErrors = [];
  const statuses = [];
  const replies = [];
  window.eval(source);
  const controller = window.StudyDocumentController.create({
    pluginId: 'study_companion',
    callPlugin,
    i18n: {
      t: (key, fallback = key) => fallback || key,
      tf: (key, fallback = key, values = {}) => fallback || `${key}:${JSON.stringify(values)}`,
    },
    ui: {
      setStatus: (value) => statuses.push(value),
      setReply: (value) => replies.push(value),
      setPasteError: (_target, value) => {
        if (value) pasteErrors.push(value);
      },
      scrollReplyIntoView: () => {},
      formatPluginError: (error) => String(error?.message || error),
    },
    onAnalysisComplete,
  });
  beforeBind(window, document);
  controller.bind();
  return { window, document, controller, pasteErrors, statuses, replies };
}

function pasteFile(environment, file) {
  const event = new environment.window.Event('paste', { bubbles: true, cancelable: true });
  Object.defineProperty(event, 'clipboardData', { value: { files: [file], items: [] } });
  environment.document.getElementById('studyInput').dispatchEvent(event);
  assert(event.defaultPrevented, `paste was not handled for ${file.name}`);
}

async function importAndWait(environment, file, expectedText) {
  pasteFile(environment, file);
  const editor = environment.document.getElementById('studyDocumentText');
  const analyze = environment.document.getElementById('studyDocumentAnalyzeBtn');
  await waitFor(
    () => editor.value === expectedText && !analyze.disabled,
    `document import did not complete for ${file.name}`,
  );
}

const utf8Text = 'UTF-8 study notes';
const utf8 = createEnvironment();
await importAndWait(utf8, fileFromBytes(bytesForText(utf8Text)), utf8Text);
assert(
  utf8.document.getElementById('studyDocumentMeta').textContent.includes('UTF-8'),
  'UTF-8 encoding was not reported',
);
utf8.controller.dispose();

const utf16Text = 'UTF-16 LE notes';
const utf16Bytes = Uint8Array.from([
  0xff,
  0xfe,
  ...Buffer.from(utf16Text, 'utf16le'),
]);
const utf16 = createEnvironment();
await importAndWait(utf16, fileFromBytes(utf16Bytes), utf16Text);
assert(
  utf16.document.getElementById('studyDocumentMeta').textContent.includes('UTF-16 LE'),
  'UTF-16 LE BOM was not detected',
);
utf16.controller.dispose();

const gb18030Text = '\u4e2d\u6587\u6587\u6863';
const gb18030 = createEnvironment();
await importAndWait(
  gb18030,
  fileFromBytes([214, 208, 206, 196, 206, 196, 181, 181]),
  gb18030Text,
);
assert(
  gb18030.document.getElementById('studyDocumentMeta').textContent.includes('GB18030'),
  'GB18030 fallback was not reported',
);
gb18030.controller.dispose();

for (const invalidCase of [
  { name: 'empty', bytes: bytesForText(' \n\t'), error: 'ui.error.document_empty' },
  { name: 'binary', bytes: [0, 1, 2, 3, 4, 5], error: 'ui.error.document_binary' },
  { name: 'unsafe', bytes: bytesForText('A'.repeat(8192)), error: 'ui.error.document_unsafe_content' },
]) {
  const environment = createEnvironment();
  pasteFile(environment, fileFromBytes(invalidCase.bytes, `${invalidCase.name}.txt`));
  await waitFor(
    () => environment.pasteErrors.length === 1,
    `${invalidCase.name} document was not rejected`,
  );
  assert(
    environment.pasteErrors[0] === invalidCase.error,
    `${invalidCase.name} error was ${environment.pasteErrors[0]}`,
  );
  assert(
    environment.document.getElementById('studyDocumentCard').hidden,
    `${invalidCase.name} document remained imported`,
  );
  environment.controller.dispose();
}

const analysisCalls = [];
let analysisRefreshes = 0;
const analysis = createEnvironment(async (entryId, args, signal) => {
  analysisCalls.push({ entryId, args, signal });
  return { status: 'completed', reply: 'analysis complete' };
}, async () => {
  analysisRefreshes += 1;
});
await importAndWait(
  analysis,
  fileFromBytes(bytesForText('original document text')),
  'original document text',
);
const analysisEditor = analysis.document.getElementById('studyDocumentText');
analysisEditor.value = 'edited document text';
analysisEditor.dispatchEvent(new analysis.window.Event('input', { bubbles: true }));
analysis.document.getElementById('studyDocumentAnalyzeBtn').click();
await waitFor(() => analysisRefreshes === 1, 'edited document analysis did not complete');
assert(analysisCalls.length === 1, `analysis call count was ${analysisCalls.length}`);
assert(
  analysisCalls[0].entryId === 'study_start_document_analysis',
  `analysis entry was ${analysisCalls[0].entryId}`,
);
assert(
  analysisCalls[0].args.document_text === 'edited document text',
  `analysis used stale text: ${analysisCalls[0].args.document_text}`,
);
assert(analysis.replies.at(-1) === 'analysis complete', 'analysis reply was not rendered');
analysis.controller.dispose();

const transientCalls = [];
const transientPollDelays = [];
let transientRefreshes = 0;
let resolveRecoveredPoll;
const transient = createEnvironment(async (entryId, args, signal) => {
  transientCalls.push({ entryId, args, signal });
  if (entryId === 'study_start_document_analysis') {
    return { job_id: 'job-transient', status: 'queued' };
  }
  if (entryId === 'study_document_analysis_status') {
    const attempt = transientCalls.filter(
      (call) => call.entryId === 'study_document_analysis_status',
    ).length;
    if (attempt <= 3) throw new Error(`transient poll ${attempt}`);
    if (attempt === 4) {
      return new Promise((resolve) => {
        resolveRecoveredPoll = resolve;
      });
    }
    return { job_id: 'job-transient', status: 'completed', reply: 'recovered analysis' };
  }
  throw new Error(`unexpected transient entry: ${entryId}`);
}, async () => {
  transientRefreshes += 1;
});
transient.window.setTimeout = (callback, delay) => {
  transientPollDelays.push(delay);
  queueMicrotask(callback);
  return transientPollDelays.length;
};
await importAndWait(
  transient,
  fileFromBytes(bytesForText('transient polling document')),
  'transient polling document',
);
transient.document.getElementById('studyDocumentAnalyzeBtn').click();
await waitFor(
  () => typeof resolveRecoveredPoll === 'function',
  'polling did not continue after three transient failures',
);
assert(
  transient.document.getElementById('studyDocumentAnalyzeBtn').disabled,
  'transient polling failure cleared the busy state',
);
assert(
  transient.window.sessionStorage.getItem('study_companion.document_analysis_job_id') === 'job-transient',
  'transient polling failure discarded the recoverable job id',
);
assert(
  transient.replies.includes('transient poll 3'),
  'third transient polling failure was not surfaced to the user',
);
resolveRecoveredPoll({ job_id: 'job-transient', status: 'running', stage: 'analyzing' });
await waitFor(() => transientRefreshes === 1, 'analysis did not recover after polling resumed');
assert(
  transientCalls.filter((call) => call.entryId === 'study_start_document_analysis').length === 1,
  'poll recovery started a duplicate document job',
);
assert(
  transientCalls.filter((call) => call.entryId === 'study_document_analysis_status').length === 5,
  'poll recovery did not preserve the original job',
);
assert(
  transientPollDelays.slice(0, 5).join(',') === '1000,2000,2000,4000,2000',
  `unexpected transient polling backoff: ${transientPollDelays.join(',')}`,
);
assert(transient.replies.at(-1) === 'recovered analysis', 'recovered result was not rendered');
transient.controller.dispose();

const exhaustedCalls = [];
const exhausted = createEnvironment(async (entryId) => {
  exhaustedCalls.push(entryId);
  if (entryId === 'study_start_document_analysis') throw new Error('start offline');
  if (entryId === 'study_active_document_analysis') throw new Error('recovery offline');
  throw new Error(`unexpected exhausted entry: ${entryId}`);
}, async () => {}, (window) => {
  window.setTimeout = (callback) => {
    queueMicrotask(callback);
    return 1;
  };
});
await importAndWait(
  exhausted,
  fileFromBytes(bytesForText('exhausted recovery document')),
  'exhausted recovery document',
);
exhausted.document.getElementById('studyDocumentAnalyzeBtn').click();
await waitFor(
  () => exhaustedCalls.filter((entryId) => entryId === 'study_active_document_analysis').length === 5
    && !exhausted.document.getElementById('studyDocumentAnalyzeBtn').disabled,
  'exhausted recovery did not release the busy UI',
);
assert(
  exhausted.replies.filter((reply) => reply === 'recovery offline').length === 1,
  'recovery failure repeatedly overwrote the reply',
);
assert(exhausted.replies.at(-1) === 'start offline', 'original start error was not restored');
assert(
  exhausted.window.sessionStorage.getItem('study_companion.document_analysis_job_id') === '__pending__',
  'exhausted recovery discarded the pending marker',
);
exhausted.controller.dispose();

const ambiguousCalls = [];
let resolveAmbiguousActive;
let ambiguousRefreshes = 0;
const ambiguous = createEnvironment(async (entryId, args, signal) => {
  ambiguousCalls.push({ entryId, args, signal });
  if (entryId === 'study_start_document_analysis') {
    throw new Error('start response lost');
  }
  if (entryId === 'study_active_document_analysis') {
    const attempt = ambiguousCalls.filter(
      (call) => call.entryId === 'study_active_document_analysis',
    ).length;
    if (attempt === 1) throw new Error('active lookup unavailable');
    return new Promise((resolve) => {
      resolveAmbiguousActive = resolve;
    });
  }
  if (entryId === 'study_document_analysis_status') {
    return { job_id: 'job-ambiguous', status: 'completed', reply: 'reconciled analysis' };
  }
  throw new Error(`unexpected ambiguous entry: ${entryId}`);
}, async () => {
  ambiguousRefreshes += 1;
}, (window) => {
  window.setTimeout = (callback) => {
    queueMicrotask(callback);
    return 1;
  };
});
await importAndWait(
  ambiguous,
  fileFromBytes(bytesForText('ambiguous start document')),
  'ambiguous start document',
);
ambiguous.document.getElementById('studyDocumentAnalyzeBtn').click();
await waitFor(
  () => typeof resolveAmbiguousActive === 'function',
  'ambiguous start did not retry active-job reconciliation',
);
assert(
  ambiguous.window.sessionStorage.getItem('study_companion.document_analysis_job_id') === '__pending__',
  'ambiguous start discarded the pending recovery marker',
);
assert(
  ambiguous.document.getElementById('studyDocumentAnalyzeBtn').disabled,
  'ambiguous start returned the document UI to idle before reconciliation',
);
resolveAmbiguousActive({ job_id: 'job-ambiguous', status: 'running', stage: 'analyzing' });
await waitFor(() => ambiguousRefreshes === 1, 'ambiguous start did not recover its result');
assert(
  ambiguousCalls.filter((call) => call.entryId === 'study_start_document_analysis').length === 1,
  'ambiguous start created a duplicate job',
);
assert(ambiguous.replies.at(-1) === 'reconciled analysis', 'reconciled result was not rendered');
ambiguous.controller.dispose();

const resumeCalls = [];
let resumeRefreshes = 0;
const resumed = createEnvironment(async (entryId, args, signal) => {
  resumeCalls.push({ entryId, args, signal });
  if (entryId === 'study_document_analysis_status') {
    const attempt = resumeCalls.filter(
      (call) => call.entryId === 'study_document_analysis_status',
    ).length;
    if (attempt === 1) throw new Error('saved status transport failed');
    return { job_id: 'job-saved', status: 'completed', reply: 'saved completed result' };
  }
  throw new Error(`unexpected resume entry: ${entryId}`);
}, async () => {
  resumeRefreshes += 1;
}, (window) => {
  window.sessionStorage.setItem('study_companion.document_analysis_job_id', 'job-saved');
  window.setTimeout = (callback) => {
    queueMicrotask(callback);
    return 1;
  };
});
await waitFor(() => resumeRefreshes === 1, 'saved job did not recover after transport failure');
assert(
  resumeCalls.filter((call) => call.entryId === 'study_document_analysis_status').length === 2,
  'saved job status was not retried directly',
);
assert(
  resumeCalls.every((call) => call.entryId !== 'study_active_document_analysis'),
  'saved status transport failure incorrectly fell back to the active endpoint',
);
assert(resumed.replies.at(-1) === 'saved completed result', 'saved result was not rendered');
resumed.controller.dispose();

const terminalCancelCalls = [];
let terminalCancelRefreshes = 0;
let resolveTerminalCancelPoll;
const terminalCancel = createEnvironment(async (entryId, args, signal) => {
  terminalCancelCalls.push({ entryId, args, signal });
  if (entryId === 'study_start_document_analysis') {
    return { job_id: 'job-terminal-cancel', status: 'queued' };
  }
  if (entryId === 'study_document_analysis_status') {
    return new Promise((resolve) => {
      resolveTerminalCancelPoll = resolve;
    });
  }
  if (entryId === 'study_cancel_document_analysis') {
    const completed = {
      job_id: 'job-terminal-cancel',
      status: 'completed',
      reply: 'completed before cancel',
    };
    resolveTerminalCancelPoll?.(completed);
    return completed;
  }
  throw new Error(`unexpected terminal cancel entry: ${entryId}`);
}, async () => {
  terminalCancelRefreshes += 1;
}, (window) => {
  window.setTimeout = (callback) => {
    queueMicrotask(callback);
    return 1;
  };
});
await importAndWait(
  terminalCancel,
  fileFromBytes(bytesForText('terminal cancel document')),
  'terminal cancel document',
);
terminalCancel.document.getElementById('studyDocumentAnalyzeBtn').click();
await waitFor(
  () => terminalCancelCalls.some((call) => call.entryId === 'study_document_analysis_status'),
  'terminal cancel analysis never entered polling',
);
terminalCancel.document.getElementById('studyDocumentCancelBtn').click();
await waitFor(() => terminalCancelRefreshes === 1, 'terminal cancel result was discarded');
assert(
  terminalCancel.replies.at(-1) === 'completed before cancel',
  'terminal cancel response was mislabeled as canceled',
);
const terminalCancelStart = terminalCancelCalls.find(
  (call) => call.entryId === 'study_start_document_analysis',
);
assert(!terminalCancelStart.signal.aborted, 'terminal cancel response aborted result processing');
terminalCancel.controller.dispose();

const beforeIdCalls = [];
let resolveStart;
const beforeId = createEnvironment(async (entryId, args, signal) => {
  beforeIdCalls.push({ entryId, args, signal });
  if (entryId === 'study_start_document_analysis') {
    return new Promise((resolve) => {
      resolveStart = resolve;
    });
  }
  if (entryId === 'study_cancel_document_analysis') {
    return { status: 'canceled', diagnostic: 'document_canceled' };
  }
  throw new Error(`unexpected entry before job id: ${entryId}`);
});
await importAndWait(beforeId, fileFromBytes(bytesForText('cancel before id')), 'cancel before id');
beforeId.document.getElementById('studyDocumentAnalyzeBtn').click();
await waitFor(() => beforeIdCalls.length === 1, 'analysis did not start before early cancel');
beforeId.document.getElementById('studyDocumentCancelBtn').click();
await Promise.resolve();
assert(
  beforeIdCalls.every((call) => call.entryId !== 'study_cancel_document_analysis'),
  'cancel was sent without a job id',
);
resolveStart({ job_id: 'job-before-id', status: 'queued' });
await waitFor(
  () => beforeIdCalls.some((call) => call.entryId === 'study_cancel_document_analysis'),
  'deferred cancel was not sent after receiving the job id',
);
const deferredCancel = beforeIdCalls.find(
  (call) => call.entryId === 'study_cancel_document_analysis',
);
assert(deferredCancel.args.job_id === 'job-before-id', 'deferred cancel used the wrong job id');
beforeId.controller.dispose();

const pollingCalls = [];
const polling = createEnvironment(async (entryId, args, signal) => {
  pollingCalls.push({ entryId, args, signal });
  if (entryId === 'study_start_document_analysis') {
    return { job_id: 'job-polling', status: 'queued' };
  }
  if (entryId === 'study_document_analysis_status') {
    return new Promise((_resolve, reject) => {
      signal.addEventListener(
        'abort',
        () => reject(new polling.window.DOMException('Aborted', 'AbortError')),
        { once: true },
      );
    });
  }
  if (entryId === 'study_cancel_document_analysis') {
    return { status: 'canceled', diagnostic: 'document_canceled' };
  }
  throw new Error(`unexpected polling entry: ${entryId}`);
});
await importAndWait(polling, fileFromBytes(bytesForText('cancel while polling')), 'cancel while polling');
polling.document.getElementById('studyDocumentAnalyzeBtn').click();
await waitFor(
  () => pollingCalls.some((call) => call.entryId === 'study_document_analysis_status'),
  'analysis never entered polling',
);
polling.document.getElementById('studyDocumentCancelBtn').click();
await waitFor(
  () => pollingCalls.some((call) => call.entryId === 'study_cancel_document_analysis'),
  'cancel was not sent while polling',
);
const pollingCancel = pollingCalls.find(
  (call) => call.entryId === 'study_cancel_document_analysis',
);
assert(pollingCancel.args.job_id === 'job-polling', 'polling cancel used the wrong job id');
await waitFor(
  () => pollingCalls.find((call) => call.entryId === 'study_start_document_analysis').signal.aborted,
  'polling request signal was not aborted after cancel',
);
polling.controller.dispose();

const pagehideCalls = [];
const beacons = [];
const pagehide = createEnvironment(async (entryId, args, signal) => {
  pagehideCalls.push({ entryId, args, signal });
  if (entryId === 'study_start_document_analysis') {
    return { job_id: 'job-pagehide', status: 'queued' };
  }
  if (entryId === 'study_document_analysis_status') {
    return new Promise(() => {});
  }
  throw new Error(`unexpected pagehide entry: ${entryId}`);
});
pagehide.window.navigator.sendBeacon = (url, body) => {
  beacons.push({ url, body });
  return true;
};
await importAndWait(pagehide, fileFromBytes(bytesForText('leave the page')), 'leave the page');
pagehide.document.getElementById('studyDocumentAnalyzeBtn').click();
await waitFor(
  () => pagehide.document.getElementById('studyDocumentState').textContent === 'validating',
  'pagehide analysis did not receive a job id',
);
const pagehideStart = pagehideCalls.find(
  (call) => call.entryId === 'study_start_document_analysis',
);
pagehide.window.dispatchEvent(new pagehide.window.Event('pagehide'));
assert(pagehideStart.signal.aborted, 'pagehide did not abort the active analysis request');
assert(beacons.length === 0, 'pagehide canceled a recoverable background job');
assert(
  !pagehideCalls.some((call) => call.entryId === 'study_cancel_document_analysis'),
  'pagehide sent an explicit cancel request',
);
pagehide.controller.dispose();
assert(beacons.length === 0, 'dispose canceled a recoverable background job');
process.exit(0);
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=frontend_dir,
        env={**os.environ, "STUDY_COMPANION_STATIC_DIR": str(STATIC)},
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_static_document_changes_stay_inside_bundle_limits() -> None:
    main = (STATIC / "main.js").read_bytes()
    style = (STATIC / "style.css").read_text(encoding="utf-8")

    assert len(main) <= 95_000
    assert len(gzip.compress(main)) <= 22_000
    assert len(style.splitlines()) <= 2_500
