import React, { useEffect, useState } from 'react';

type StudyStatus = {
  status?: string;
  active_mode?: string;
  last_reply?: string;
  last_ocr_text?: string;
};

async function callPlugin(entryId: string, args: Record<string, unknown> = {}) {
  const createResp = await fetch('/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ plugin_id: 'study_companion', entry_id: entryId, args }),
  });
  if (!createResp.ok) {
    throw new Error(`Run create failed: HTTP ${createResp.status}`);
  }
  const created = await createResp.json();
  const runId = created.run_id || created.id;
  for (let i = 0; i < 40; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 300));
    const runResp = await fetch(`/runs/${runId}`);
    if (!runResp.ok) {
      continue;
    }
    const run = await runResp.json();
    if (run.status === 'succeeded') {
      const exportResp = await fetch(`/runs/${runId}/export`);
      const exported = exportResp.ok ? await exportResp.json() : {};
      const item = (exported.items || []).find((candidate: any) => candidate.type === 'json' && candidate.json);
      return item?.json?.data || {};
    }
    if (['failed', 'canceled', 'timeout'].includes(run.status)) {
      throw new Error(run.error?.message || run.message || run.status);
    }
  }
  throw new Error('Plugin call timed out');
}

export default function StudyPanel() {
  const [status, setStatus] = useState<StudyStatus>({});
  const [text, setText] = useState('');
  const [reply, setReply] = useState('');
  const [busy, setBusy] = useState(false);

  async function refresh() {
    const data = await callPlugin('study_status') as StudyStatus;
    setStatus(data);
    setReply(data.last_reply || '');
    if (!text.trim() && data.last_ocr_text) {
      setText(data.last_ocr_text);
    }
  }

  async function explain() {
    setBusy(true);
    try {
      const data = await callPlugin('study_explain_text', { text }) as { reply?: string; summary?: string };
      setReply(data.reply || data.summary || '');
      await refresh();
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    refresh().catch((error) => setReply(error instanceof Error ? error.message : String(error)));
  }, []);

  return (
    <div className="study-panel">
      <header>
        <h1>Study Companion</h1>
        <span>{status.status || 'unknown'} / {status.active_mode || 'concept_explain'}</span>
      </header>
      <textarea value={text} onChange={(event) => setText(event.target.value)} />
      <button type="button" disabled={busy} onClick={explain}>Explain</button>
      <pre>{reply}</pre>
    </div>
  );
}
