const dock = document.getElementById('game-chat');
const frame = document.getElementById('game-chat-frame');
const toggle = document.getElementById('chat-toggle');
const close = document.getElementById('chat-close');

function chatUrl(preferredName = '') {
  const current = new URLSearchParams(window.location.search);
  const params = new URLSearchParams({ game_overlay:'air_basketball' });
  const name = preferredName || current.get('lanlan_name') || window.lanlan_config?.lanlan_name;
  if (name) params.set('lanlan_name',name);
  return `/chat?${params}`;
}

let bridgePrepared = false;

function prepareChatBridge(preferredName = '') {
  if (!frame || bridgePrepared) return;
  bridgePrepared = true;
  frame.src = chatUrl(preferredName);
}

function openChat() {
  prepareChatBridge();
  dock.classList.remove('is-closed');
  dock.setAttribute('aria-hidden','false');
  toggle.setAttribute('aria-expanded','true');
}

function closeChat() {
  dock.classList.add('is-closed');
  dock.setAttribute('aria-hidden','true');
  toggle.setAttribute('aria-expanded','false');
}

toggle?.addEventListener('click',() => dock.classList.contains('is-closed') ? openChat() : closeChat());
close?.addEventListener('click',closeChat);
toggle?.setAttribute('aria-expanded','false');
window.prepareAirBasketballChat = prepareChatBridge;
