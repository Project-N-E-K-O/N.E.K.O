from __future__ import annotations

import asyncio
import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest

from plugin.plugins.study_companion.document_analysis import validate_document
from plugin.plugins.study_companion.document_analysis_jobs import (
    DOCUMENT_JOB_FINALIZE_RESERVED_SECONDS,
    DOCUMENT_JOB_MERGE_RESERVED_SECONDS,
    DOCUMENT_JOB_TIMEOUT_SECONDS,
    DocumentAnalysisJobManager,
)
from plugin.plugins.study_companion.document_chunking import split_document
from plugin.plugins.study_companion.tutor_llm_agent import TutorLLMAgent
from plugin.plugins.study_companion.tutor_llm_agent_document import (
    DocumentChunkAnalysisError,
    _analyze_document_chunk_result,
)


pytestmark = pytest.mark.unit


class _Logger:
    def warning(self, *_args, **_kwargs) -> None:
        return None


@pytest.mark.asyncio
async def test_job_manager_owns_one_absolute_budget_and_exposes_active_job() -> None:
    manager = DocumentAnalysisJobManager()
    observed = {}
    release = asyncio.Event()

    async def runner(_update, budget):
        observed["budget"] = budget
        await release.wait()
        return {"reply": "safe", "summary": "safe", "degraded": False}

    started = await manager.start(
        analysis_mode="chunked",
        document={"name": "book.txt", "source_retained": False},
        total_chunks=2,
        runner=runner,
    )
    for _ in range(20):
        if "budget" in observed:
            break
        await asyncio.sleep(0)

    active = await manager.active()
    budget = observed["budget"]
    assert active["job_id"] == started["job_id"]
    assert active["status"] == "running"
    assert budget.deadline_monotonic - budget.started_monotonic == pytest.approx(
        DOCUMENT_JOB_TIMEOUT_SECONDS
    )
    assert budget.merge_deadline_monotonic == pytest.approx(
        budget.deadline_monotonic - DOCUMENT_JOB_FINALIZE_RESERVED_SECONDS
    )
    assert budget.chunk_deadline_monotonic == pytest.approx(
        budget.merge_deadline_monotonic - DOCUMENT_JOB_MERGE_RESERVED_SECONDS
    )
    release.set()
    await manager.shutdown()


@pytest.mark.asyncio
async def test_cancel_and_shutdown_sources_are_distinguishable() -> None:
    manager = DocumentAnalysisJobManager()

    async def runner(_update, _budget):
        await asyncio.Future()

    started = await manager.start(
        analysis_mode="direct",
        document={"name": "notes.txt"},
        total_chunks=1,
        runner=runner,
    )
    canceled = await manager.cancel(started["job_id"], source="user")
    assert canceled["cancellation_source"] == "user"

    second = await manager.start(
        analysis_mode="direct",
        document={"name": "notes.txt"},
        total_chunks=1,
        runner=runner,
    )
    await asyncio.sleep(0)
    await manager.shutdown()
    assert second["job_id"]


@pytest.mark.asyncio
async def test_job_timeout_records_job_timeout_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.study_companion import document_analysis_jobs as jobs_module

    monkeypatch.setattr(jobs_module, "DOCUMENT_JOB_TIMEOUT_SECONDS", 0.01)
    manager = DocumentAnalysisJobManager()

    async def runner(_update, _budget):
        await asyncio.Future()

    started = await manager.start(
        analysis_mode="direct",
        document={"name": "notes.txt"},
        total_chunks=1,
        runner=runner,
    )
    await asyncio.sleep(0.03)
    status = await manager.status(started["job_id"])
    assert status["status"] == "failed"
    assert status["diagnostic"] == "timeout"
    assert status["cancellation_source"] == "job_timeout"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_chunk_retry_does_not_start_after_chunk_window_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = validate_document(
        document_name="book.txt",
        document_type="text/plain",
        document_text="A short chapter.",
        locale="en",
    )
    chunk = split_document(document.text, document.document_type)[0]
    agent = TutorLLMAgent(
        logger=_Logger(), config=type("C", (), {"llm_call_timeout_seconds": 3600})()
    )
    calls = 0

    async def fake(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        error = RuntimeError("limited")
        error.diagnostic = "rate_limited"
        raise error

    monkeypatch.setattr(agent, "_call_model", fake)
    with pytest.raises(DocumentChunkAnalysisError) as raised:
        await _analyze_document_chunk_result(
            agent,
            document,
            chunk,
            1,
            deadline_monotonic=time.monotonic() + 0.01,
        )
    assert raised.value.diagnostic == "document_chunk_window_exhausted"
    assert calls == 1


def test_document_frontends_resume_jobs_without_canceling_on_unload() -> None:
    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    hosted = (plugin_dir / "surfaces" / "study_panel.tsx").read_text(encoding="utf-8")
    static = (plugin_dir / "static" / "document-controller.js").read_text(
        encoding="utf-8"
    )

    for source in (hosted, static):
        assert "sessionStorage" in source
        assert "study_active_document_analysis" in source
        assert "cancellation_source: 'user'" in source
    assert "navigator.sendBeacon('/runs'" not in static
    cleanup_start = hosted.index("mountedRef.current = false;")
    cleanup_end = hosted.index("}, [props.locale]);", cleanup_start)
    assert "study_cancel_document_analysis" not in hosted[cleanup_start:cleanup_end]


def test_hosted_document_job_recovery_survives_opaque_storage_and_transient_failures() -> None:
    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    hosted = (plugin_dir / "surfaces" / "study_panel.tsx").read_text(encoding="utf-8")

    resume_start = hosted.index("async function resumeDocumentJob")
    resume_end = hosted.index("async function cancelDocumentJob", resume_start)
    resume = hosted[resume_start:resume_end]
    assert "if (!savedJobId) return;" not in resume
    assert "const pendingStart = isPendingDocumentJobId(savedJobId);" in resume
    assert "savedJobId && !pendingStart" in resume
    assert "study_active_document_analysis" in resume
    assert "pendingStart ? { pending_start: true } : {}" in resume
    assert "activeArgs.start_token = startToken" in resume
    assert "pendingStartTokenOverride" in resume
    assert ".catch(() => null)" not in resume
    assert "if (lookupFailed)" in resume
    assert "await waitForDocumentPoll(retryDelayMs, signal);" in resume
    assert "continue;" in resume
    assert "savedJobNotFound = true;" in resume
    assert "async function acknowledgeDocumentJob" in hosted
    assert "{ job_id: jobId, acknowledge: true }" in hosted
    assert resume.count("await acknowledgeDocumentJob(jobId, signal);") == 2

    saved_start = hosted.index("function savedDocumentJobId")
    saved_end = hosted.index("function rememberPendingDocumentJob", saved_start)
    saved = hosted[saved_start:saved_end]
    assert "const inMemoryJobId = String(documentJobIdRef.current || '');" in saved
    assert "return inMemoryJobId" in saved
    assert "return inMemoryJobId || inMemoryPendingJobId;" in saved

    poll_start = hosted.index("async function pollDocumentJob")
    poll_end = hosted.index("async function resumeDocumentJob", poll_start)
    poll = hosted[poll_start:poll_end]
    assert "throw error;" not in poll
    assert "setReply(formatPluginError(error));" in poll
    assert "Math.min(" in poll
    assert "continue;" in poll


def test_document_finalization_drains_after_the_job_deadline_starts_canceling() -> None:
    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    source = (plugin_dir / "entry_document_analysis_jobs.py").read_text(
        encoding="utf-8"
    )
    start = source.index("finalize_remaining =")
    end = source.index('payload.pop("input_text", None)', start)
    finalization = source[start:end]

    assert "finalize_task = asyncio.create_task(" in finalization
    assert "payload = await asyncio.shield(finalize_task)" in finalization
    assert "except asyncio.CancelledError:" in finalization
    assert "payload = await finalize_task" in finalization
    assert "timeout=finalize_remaining" not in finalization


def test_hosted_document_job_recovery_is_independent_of_status_initialization() -> None:
    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    hosted = (plugin_dir / "surfaces" / "study_panel.tsx").read_text(encoding="utf-8")
    mounted_start = hosted.index("mountedRef.current = true;")
    init_start = hosted.rfind("useEffect(() => {", 0, mounted_start)
    init_end = hosted.index("}, [props.locale]);", init_start)
    initialization = hosted[init_start:init_end]

    assert "const documentController = documentPollingController();" in initialization
    assert "void resumeDocumentJob(documentController.signal).catch" in initialization
    assert ".then(() => resumeDocumentJob(controller.signal))" not in initialization
    assert initialization.index("resumeDocumentJob") < initialization.index("refresh(controller.signal)")
    status_initialization = initialization[initialization.index("refresh(controller.signal)") :]
    assert (
        "setReply((current) => current || formatPluginError(error));"
        in status_initialization
    )


def test_hosted_document_job_ambiguous_start_and_cancel_failures_keep_recovery() -> None:
    plugin_dir = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
    hosted = (plugin_dir / "surfaces" / "study_panel.tsx").read_text(encoding="utf-8")

    cancel_helper_start = hosted.index("async function cancelKnownDocumentJob")
    cancel_start = hosted.index("async function cancelDocumentJob", cancel_helper_start)
    cancel_helper = hosted[cancel_helper_start:cancel_start]
    assert "rememberDocumentJobId(jobId);" in cancel_helper
    assert "setDocumentJob(fallbackJob);" in cancel_helper
    assert "await pollDocumentJob(jobId, controller);" in cancel_helper

    cancel_end = hosted.index("async function analyzeDocument", cancel_start)
    cancel = hosted[cancel_start:cancel_end]
    assert "finally" not in cancel
    assert "await cancelKnownDocumentJob(jobId, fallbackJob);" in cancel
    assert "rememberDocumentJobId('');" not in cancel

    analyze_start = cancel_end
    analyze_end = hosted.index("async function refresh", analyze_start)
    analyze = hosted[analyze_start:analyze_end]
    analyze_catch_start = analyze.index("} catch (error) {")
    analyze_catch = analyze[analyze_catch_start:]
    assert "rememberDocumentJobId('');" not in analyze_catch
    assert "await resumeDocumentJob(controller.signal, startToken);" in analyze_catch


def test_static_document_job_storage_recovers_valid_and_clears_stale_ids() -> None:
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    repo = Path(__file__).resolve().parents[4]
    frontend = repo / "frontend" / "plugin-manager"
    if not (frontend / "node_modules" / "happy-dom").is_dir():
        pytest.skip("happy-dom is not installed")
    static = repo / "plugin" / "plugins" / "study_companion" / "static"
    script = r"""
import { Window } from 'happy-dom';
import fs from 'node:fs';
import path from 'node:path';
const staticDir = process.env.STUDY_COMPANION_STATIC_DIR;
const html = fs.readFileSync(path.join(staticDir, 'index.html'), 'utf8');
const source = fs.readFileSync(path.join(staticDir, 'document-controller.js'), 'utf8');
const key = 'study_companion.document_analysis_job_id';
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const waitFor = async (predicate) => {
  for (let index = 0; index < 100; index += 1) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error('timed out');
};
function environment(savedId, callPlugin) {
  const window = new Window({ url: 'http://testserver/' });
  window.document.write(html);
  window.document.close();
  window.sessionStorage.setItem(key, savedId);
  window.eval(source);
  const calls = [];
  const controller = window.StudyDocumentController.create({
    pluginId: 'study_companion',
    callPlugin: async (...args) => {
      calls.push({ entry: args[0], args: args[1] || {} });
      return callPlugin(...args);
    },
    i18n: { t: (key, fallback = key) => fallback, tf: (key, fallback = key) => fallback },
    ui: {
      setStatus() {}, setReply() {}, setPasteError() {}, scrollReplyIntoView() {},
      formatPluginError: (error) => String(error?.message || error),
    },
    onAnalysisComplete: async () => {},
  });
  controller.bind();
  return { window, controller, calls };
}
const valid = environment('job-valid', async (entry) => {
  if (entry === 'study_document_analysis_status') {
    return { job_id: 'job-valid', status: 'completed', stage: 'completed', reply: 'done' };
  }
  throw new Error(`unexpected ${entry}`);
});
await waitFor(() => valid.calls.length >= 2 && valid.window.sessionStorage.getItem(key) === null);
assert(valid.calls[0].entry === 'study_document_analysis_status', 'saved job was not queried');
assert(valid.calls[1].args.acknowledge === true, 'completed saved job was not acknowledged');
assert(valid.window.sessionStorage.getItem(key) === null, 'completed job id was retained');
valid.controller.dispose();

const stale = environment('job-stale', async (entry) => {
  if (entry === 'study_document_analysis_status') {
    return { status: 'failed', diagnostic: 'document_job_not_found' };
  }
  if (entry === 'study_active_document_analysis') return { status: 'idle', job_id: '' };
  throw new Error(`unexpected ${entry}`);
});
await waitFor(() => stale.calls.length === 2 && stale.window.sessionStorage.getItem(key) === null);
assert(stale.calls[1].entry === 'study_active_document_analysis', 'stale id did not use active lookup');
assert(stale.window.sessionStorage.getItem(key) === null, 'stale job id was retained');
stale.controller.dispose();
process.exit(0);
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=frontend,
        env={**os.environ, "STUDY_COMPANION_STATIC_DIR": str(static)},
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
