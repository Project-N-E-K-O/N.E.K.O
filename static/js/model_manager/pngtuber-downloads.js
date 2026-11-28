/* Optional models install separately; selection is always an explicit user action. */
(function (root) {
    'use strict';
    const states = {idle:'未下载', downloading:'下载中', verifying:'校验中', installing:'安装中', cancelling:'正在取消', cancelled:'已取消', failed:'下载失败，可重试', complete:'已安装'};
    const working = model => ['downloading','verifying','installing','cancelling'].includes(model.state);
    root.createPNGTuberDownloadPanel = function ({document, request, onInstalled, setTimer, clearTimer}) {
        const panel = document.getElementById('pngtuber-optional-models');
        if (!panel) return null;
        let timer = null, closed = false, busy = false, rerun = false, failures = 0;
        const notified = new Set(), rows = new Map();
        const statusError = document.createElement('p');statusError.hidden=true;
        panel.replaceChildren();
        function update(view, model) {
            view.model = model;
            const size = (model.download_size / 1048576).toFixed(1);
            let status = model.update_available ? '可更新' : model.installed ? '已安装' : states[model.state] || model.state;
            if (!model.published && !model.installed) status = '资源待发布';
            if (working(model) && model.total) status += ` ${Math.min(100, Math.floor(model.received / model.total * 100))}%`;
            view.label.textContent = `${model.name} · ${size} MiB · ${status}`;
            view.button.textContent = working(model) ? '取消下载' : model.update_available ? '更新模型' : model.installed ? '已安装' : model.state === 'failed' ? '重试下载' : '下载模型';
            view.button.disabled = view.pending || (model.installed && !model.update_available) || !model.published || ['installing','cancelling'].includes(model.state);
            view.error.textContent = model.error || view.operationError || '';
            view.error.hidden = !view.error.textContent;
        }
        function createRow(model) {
            const row = document.createElement('div');
            row.style.cssText='display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0';
            const label = document.createElement('span'), button = document.createElement('button'), error = document.createElement('p');
            button.type='button';button.className='btn';
            row.appendChild(label);row.appendChild(button);row.appendChild(error);panel.appendChild(row);
            const view = {row,label,button,error,model,pending:false,operationError:''};
            button.addEventListener('click', async () => {
                if (closed || view.pending || button.disabled) return;
                const action = working(view.model) ? 'cancel' : 'download';
                view.pending=true;view.operationError='';update(view,view.model);
                try {
                    const data = await request(`/api/model/pngtuber/optional/${encodeURIComponent(view.model.id)}/${action}`, {method:'POST'});
                    if (data.success === false) throw Error(data.error || '操作失败');
                } catch (error) { view.operationError=error.message; }
                finally { view.pending=false;if(!closed){update(view,view.model);schedule();} }
            });
            return view;
        }
        async function refresh() {
            if (closed) return;
            if (busy) {rerun=true;return;}
            if (timer !== null) {clearTimer(timer);timer=null;}
            busy=true;
            try {
                const result = await request('/api/model/pngtuber/optional');
                if (closed) return;
                failures=0;statusError.textContent='';statusError.hidden=true;
                const present=new Set();let active=false;
                for (const model of result.models || []) {
                    present.add(model.id);
                    let view=rows.get(model.id);
                    if(!view){view=createRow(model);rows.set(model.id,view);}
                    update(view,model);active ||= working(model);
                    if(!model.installed)notified.delete(model.id);
                    if(model.state==='complete' && model.installed && !notified.has(model.id)) {
                        notified.add(model.id);onInstalled();
                    }
                }
                for(const [id,view] of rows)if(!present.has(id)){view.row.remove();rows.delete(id);notified.delete(id);}
                if(active)schedule();
            } catch (error) {
                if(closed)return;
                statusError.hidden=false;statusError.textContent=`可选模型暂时无法加载，正在重试：${error.message}`;
                if(!statusError.parentNode)panel.appendChild(statusError);
                schedule(Math.min(8000,500*2**Math.min(failures++,4)));
            } finally {
                busy=false;
                if(rerun){rerun=false;schedule(0);}
            }
        }
        function schedule(delay=500) {
            if(timer!==null)clearTimer(timer);
            timer=null;
            if(!closed)timer=setTimer(()=>{timer=null;void refresh();},delay);
        }
        void refresh();
        return {refresh,destroy(){closed=true;rerun=false;if(timer!==null)clearTimer(timer);timer=null;}};
    };
    if(root.document)root.addEventListener('DOMContentLoaded',()=>{
        const panel=root.document.getElementById('pngtuber-optional-models'),select=root.document.getElementById('model-type-select');
        if(!panel)return;
        let controller=null;
        const updateVisibility=()=>{panel.hidden=select?.value!=='pngtuber';if(!panel.hidden)void controller?.refresh();};
        const mount=()=>root.createPNGTuberDownloadPanel({document:root.document,
            request:(...args)=>root.requestOptionalPNGTuber(...args),
            onInstalled:()=>root.dispatchEvent(new CustomEvent('pngtuber-optional-installed')),
            setTimer:root.setTimeout.bind(root),clearTimer:root.clearTimeout.bind(root)});
        select?.addEventListener('change',updateVisibility);updateVisibility();controller=mount();
        root.addEventListener('pngtuber-optional-refresh',()=>{if(!panel.hidden)void controller?.refresh();});
        root.addEventListener('pagehide',()=>controller?.destroy());
        root.addEventListener('pageshow',event=>{if(event.persisted){controller=mount();updateVisibility();}});
    });
})(typeof window !== 'undefined' ? window : globalThis);
