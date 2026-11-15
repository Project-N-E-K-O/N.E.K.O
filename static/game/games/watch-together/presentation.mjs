// Trusted presentation hooks for scene.mjs: cards, preparation progress and the long-video dialog.
// The scene keeps all playback state; these hooks only render it and forward clicks to its controls.
import {clock} from './format.mjs';

// restore() leaves maximize/fullscreen, so actions that lead to the finder or library can show it.
export function createPresentation({companion = '', restore = async () => {}} = {}) {
  const $ = id => document.getElementById(id);
  const tr = (key, params) => window.i18n?.t?.(`watchTogether.${key}`, params) || key;
  const narrow = () => window.matchMedia?.('(max-width: 899px)').matches;
  // In the single-column layout the finder and library sit below the stage.
  const reveal = element => { if (narrow()) element?.scrollIntoView({block: 'start', behavior: 'smooth'}); };
  const setBar = (bar, value) => {
    if (Number.isFinite(value)) bar.value = Math.max(0, Math.min(100, value));
    else bar.removeAttribute('value');
  };
  const mirrorDisabled = (source, target) => {
    const sync = () => { target.disabled = source.disabled; };
    new MutationObserver(sync).observe(source, {attributes: true, attributeFilter: ['disabled']});
    sync();
  };
  let current = null, preparingTitle = '', titled = '';
  const pageTitle = () => { document.title = titled ? `${titled} · ${tr('title')}` : tr('title'); };
  $('companion-name').textContent = companion;
  pageTitle();
  window.addEventListener('localechange', pageTitle);

  // Empty stage: the same actions as the finder and the library.
  $('empty-discover').onclick = () => $('discover-button').click();
  mirrorDisabled($('discover-button'), $('empty-discover'));
  $('empty-paste').onclick = async () => { await restore(); reveal($('prepare')); $('url').focus({preventScroll: narrow()}); };
  $('empty-library').onclick = async () => { await restore(); reveal(document.querySelector('.library')); };

  // End card, manual mode only: automatic mode speaks its intermission and moves on by itself.
  const video = $('video'), endCard = $('end-card');
  video.addEventListener('ended', () => { endCard.hidden = $('automatic-enabled').checked || !$('title').textContent; });
  for (const type of ['play', 'seeking', 'emptied']) video.addEventListener(type, () => { endCard.hidden = true; });
  mirrorDisabled($('next-video'), $('end-next'));
  $('end-next').onclick = () => $('next-video').click();
  $('end-replay').onclick = () => { video.currentTime = 0; video.play().catch(() => {}); };
  $('end-pick').onclick = async () => {
    endCard.hidden = true;
    await restore();
    reveal(document.querySelector('.library'));
    (document.querySelector('.video-card:not(:disabled)') || $('topic')).focus({preventScroll: narrow()});
  };

  // Long-video confirmation. The scene runs one preparation at a time, so one dialog at most.
  const dialog = $('long-dialog');
  window.addEventListener('pagehide', () => { if (dialog.open) dialog.close('cancel'); });
  function confirmLong(info) {
    if (typeof dialog.showModal !== 'function') return window.confirm(`${info.title}\n${tr('longWarning')}\n${Math.ceil(info.duration)}s`);
    $('long-dialog-video').textContent = info.title || '';
    // Rounded up like the backend's limit: 300.04 seconds is over five minutes, shown as 5:01.
    $('long-dialog-duration').textContent = clock(Math.ceil(Number(info.duration)));
    dialog.returnValue = '';
    return new Promise(resolve => {
      // The backend expires an unanswered confirmation after five minutes.
      const timer = setTimeout(() => dialog.close('cancel'), 290000);
      dialog.addEventListener('close', () => { clearTimeout(timer); resolve(dialog.returnValue === 'confirm'); }, {once: true});
      dialog.showModal();
    });
  }

  const isCurrent = (job, version) => !!current && job === current.job && version === current.version;
  function decorateAnalysis(button, row) {
    const make = (tag, properties) => Object.assign(document.createElement(tag), properties);
    const title = button.textContent, thumb = make('span', {className: 'thumb'});
    button.className = 'video-card';
    button.dataset.job = row.job;
    button.dataset.version = row.version || '';
    if (row.cover) {
      const image = make('img', {src: row.cover, alt: '', loading: 'lazy', decoding: 'async'});
      image.onerror = () => image.remove();
      thumb.append(image);
    }
    const length = clock(row.duration);
    if (length) thumb.append(make('span', {className: 'duration', textContent: length}));
    if (row.status !== 'ready') thumb.append(make('span', {className: 'badge', textContent: tr('unavailable')}));
    const meta = Number.isInteger(row.reactions) ? tr('reactionCount', {count: row.reactions}) : '';
    button.replaceChildren(thumb, make('span', {className: 'card-title', textContent: title}), make('span', {className: 'card-meta', textContent: meta}));
    if (isCurrent(row.job, row.version)) button.setAttribute('aria-current', 'true');
  }
  function preparation(state) {
    if (!state) { $('prep-card').hidden = true; preparingTitle = ''; return; }
    preparingTitle = state.title || state.confirmation_video?.title || preparingTitle;
    $('prep-title').textContent = preparingTitle;
    $('prep-stage').textContent = state.stage_key ? tr(state.stage_key) : (state.stage || '');
    setBar($('prep-progress'), state.progress == null ? NaN : Number(state.progress));
    $('prep-card').hidden = false;
  }
  function next(state) {
    const bar = $('next-progress'), active = state.status === 'searching' || state.status === 'preparing';
    bar.hidden = !active;
    if (active) setBar(bar, state.status === 'preparing' && state.progress != null ? Number(state.progress) : NaN);
    $('next-video').title = state.row?.title || state.title || '';
  }
  function selected(row) {
    current = {job: row.id, version: row.version};
    titled = row.title || '';
    pageTitle();
    const count = Array.isArray(row.events) ? row.events.length : 0;
    $('cue-count').textContent = count ? tr('reactionCount', {count}) : '';
    endCard.hidden = true;
    for (const card of document.querySelectorAll('.video-card')) {
      if (isCurrent(card.dataset.job, card.dataset.version)) card.setAttribute('aria-current', 'true');
      else card.removeAttribute('aria-current');
    }
    if (!$('automatic-enabled').checked) reveal($('stage'));
  }
  return {confirmLong, decorateAnalysis, preparation, next, selected};
}
