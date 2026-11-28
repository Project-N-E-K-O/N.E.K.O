/* Optional models install separately; selection is always an explicit user action. */
(function (root) {
    'use strict';
    const states = {idle:'未下载', downloading:'下载中', verifying:'校验中', installing:'安装中', cancelling:'正在取消', cancelled:'已取消', failed:'下载失败，可重试', complete:'已安装'};
    root.createPNGTuberDownloadPanel = function ({document, request, onInstalled, setTimer, clearTimer}) {
        const panel = document.getElementById('pngtuber-optional-models');
        if (!panel) return null;
        let timer = null, closed = false, busy = false;
        const notified = new Set();
        async function refresh() {
            if (closed || busy) return;
            busy = true;
            try {
                const result = await request('/api/model/pngtuber/optional');
                if (closed) return;
                panel.replaceChildren();
                let active = false;
                for (const model of result.models || []) {
                    const row = document.createElement('div');
                    row.style.cssText='display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0';
                    const label = document.createElement('span');
                    const working = ['downloading','verifying','installing','cancelling'].includes(model.state);
                    active ||= working;
                    const size = (model.download_size / 1048576).toFixed(1);
                    let status = model.update_available ? '可更新' : model.installed ? '已安装' : states[model.state] || model.state;
                    if (!model.published && !model.installed) status = '资源待发布';
                    if (working && model.total) status += ` ${Math.min(100, Math.floor(model.received / model.total * 100))}%`;
                    label.textContent = `${model.name} · ${size} MiB · ${status}`;
                    row.appendChild(label);
                    const button = document.createElement('button');button.type='button';button.className='btn';
                    button.textContent = working ? '取消下载' : model.update_available ? '更新模型' : model.installed ? '已安装' : model.state === 'failed' ? '重试下载' : '下载模型';
                    button.disabled = (model.installed && !model.update_available) || !model.published || ['installing','cancelling'].includes(model.state);
                    button.addEventListener('click', async () => {
                        button.disabled = true;
                        try {
                            const data = await request(`/api/model/pngtuber/optional/${encodeURIComponent(model.id)}/${working?'cancel':'download'}`, {method:'POST'});
                            if (data.success === false) throw Error(data.error || '操作失败');
                        } catch (error) { label.textContent = `${model.name} · ${error.message}`; }
                        finally { button.disabled = false; schedule(); }
                    });
                    row.appendChild(button);
                    if (model.error) { const error=document.createElement('p');error.textContent=model.error;row.appendChild(error); }
                    panel.appendChild(row);
                    if (model.state === 'complete' && model.installed && !notified.has(model.id)) {
                        notified.add(model.id);onInstalled();
                    }
                }
                if (active) schedule();
            } catch (error) { panel.textContent = `可选模型暂时无法加载：${error.message}`; }
            finally { busy = false; }
        }
        function schedule() { if (timer !== null) clearTimer(timer); if (!closed) timer=setTimer(()=>{timer=null;void refresh();},500); }
        void refresh();
        return {refresh, destroy(){closed=true;if(timer!==null)clearTimer(timer);}};
    };
    if (root.document) root.addEventListener('DOMContentLoaded', () => {
        const panel = root.document.getElementById('pngtuber-optional-models');
        const select = root.document.getElementById('model-type-select');
        if (!panel) return;
        const updateVisibility=()=>{panel.hidden=select?.value!=='pngtuber';};
        select?.addEventListener('change',updateVisibility);updateVisibility();
        const mount=()=>root.createPNGTuberDownloadPanel({document:root.document,
            request:(...args)=>root.requestOptionalPNGTuber(...args),
            onInstalled:()=>root.dispatchEvent(new CustomEvent('pngtuber-optional-installed')),
            setTimer:root.setTimeout.bind(root),clearTimer:root.clearTimeout.bind(root)});
        let controller=mount();
        root.addEventListener('pagehide',()=>controller?.destroy());
        root.addEventListener('pageshow',event=>{if(event.persisted){updateVisibility();controller=mount();}});
    });
})(typeof window !== 'undefined' ? window : globalThis);
