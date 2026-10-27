/** 喵宇宙串门：只显示当前社区账号的真实互关。 */
(function () {
  'use strict';
  if (window.nekoSocialHub) return;

  var standalone = window.__NEKO_SOCIAL_HUB_STANDALONE__ === true;
  var state = {
    open: standalone, loading: true, loggedIn: false,
    toast: '', toastError: false, incoming: null, friends: [], accountId: '', account: null,
    presenceMenuOpen: false, presenceSaving: false,
    invitations: new Map(), restoredAccountId: ''
  };
  var layer = null;
  var panel = null;
  var previousFocus = null;
  var incomingDeliveryKey = '';
  var incomingDeliveryTimer = 0;

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }
  function image(src, alt, className) {
    var img = el('img', className || '');
    img.src = src || '/static/icons/avatar/master-avatar.png';
    img.alt = alt || '';
    img.draggable = false;
    img.addEventListener('error', function () {
      if (!img.src.endsWith('/static/icons/avatar/master-avatar.png')) {
        img.src = '/static/icons/avatar/master-avatar.png';
      }
    }, { once: true });
    return img;
  }
  function hashHue(seed) {
    var hash = 2166136261 >>> 0;
    Array.from(String(seed || '')).forEach(function (character) {
      hash ^= character.codePointAt(0);
      hash = Math.imul(hash, 16777619) >>> 0;
    });
    hash ^= hash >>> 16;
    hash = Math.imul(hash, 2246822507) >>> 0;
    hash ^= hash >>> 13;
    hash = Math.imul(hash, 3266489909) >>> 0;
    hash ^= hash >>> 16;
    return (hash >>> 0) % 360;
  }
  function avatarVisual(value, displayName, seed) {
    var avatarValue = String(value || '');
    var hueMatch = /^hue:(\d{1,3})$/.exec(avatarValue);
    var hue = hueMatch ? Number(hueMatch[1]) : hashHue(seed);
    if (avatarValue && !hueMatch) {
      return image(avatarValue, displayName, 'neko-social-hub-avatar');
    }
    if (hue < 0 || hue > 360) hue = hashHue(seed);
    var fallback = el('span', 'neko-social-hub-avatar is-hue');
    fallback.style.setProperty('--neko-avatar-hue', String(hue));
    fallback.setAttribute('role', 'img');
    fallback.setAttribute('aria-label', displayName || '喵友');
    fallback.textContent = Array.from(String(displayName || '喵').trim())[0] || '喵';
    return fallback;
  }
  function button(label, className, handler) {
    var node = el('button', className, label);
    node.type = 'button';
    node.addEventListener('click', handler);
    return node;
  }
  function host() { return window.nekoSocialHubHost || null; }
  function social() { return window.nekoSocial || null; }
  async function json(response) { try { return await response.json(); } catch (_) { return {}; } }

  function avatarManagers() {
    return [window.live2dManager, window.vrmManager, window.mmdManager, window.pngtuberManager]
      .filter(function (manager, index, managers) {
        return manager && managers.indexOf(manager) === index;
      });
  }

  function closeAvatarPopups() {
    avatarManagers().forEach(function (manager) {
      if (typeof manager.closeAllPopups === 'function') manager.closeAllPopups();
    });
  }

  function notifyOverlayVisibilityChanged() {
    try {
      window.dispatchEvent(new CustomEvent('neko-avatar-overlay-visibility-changed', {
        detail: { prefix: 'social' }
      }));
    } catch (_) {}
  }

  function detailText(body, fallback) {
    if (body && body.detail === 'community_login_required') return '请先在 Neko 设置里登录喵宇宙';
    if (body && body.detail === 'mutual_follow_required') return '你们已不再互相关注，无法发送邀请';
    if (body && body.detail === 'target_offline') return '对方当前离线，暂时不能邀请';
    if (body && body.detail === 'community_unavailable') return '暂时连不上喵宇宙，请稍后重试';
    return fallback;
  }
  function normalizeFriend(item) {
    var presenceStatus = String(item && item.presence_status || '');
    if (['online', 'away', 'offline'].indexOf(presenceStatus) === -1) {
      presenceStatus = item && item.online === true ? 'online' : 'offline';
    }
    return {
      id: String(item && item.id || ''),
      displayName: String(item && (item.display_name || item.username) || '喵友'),
      avatarUrl: String(item && item.avatar_url || ''),
      online: item && item.online === true,
      presenceStatus: presenceStatus,
      lastSeenAt: String(item && item.last_seen_at || '')
    };
  }
  function normalizeAccount(item) {
    if (!item || !item.id) return null;
    var presenceStatus = String(item.presence_status || 'online');
    if (['online', 'away', 'invisible'].indexOf(presenceStatus) === -1) presenceStatus = 'online';
    return {
      id: String(item.id),
      displayName: String(item.display_name || item.username || '我'),
      username: String(item.username || ''),
      avatarUrl: String(item.avatar_url || ''),
      presenceStatus: presenceStatus
    };
  }
  function normalizeIncoming(item) {
    if (!item || (!item.visit_id && !item.invite_code)) return null;
    return {
      id: String(item.id || ''),
      actorId: String(item.actor_id || ''),
      displayName: String(item.actor_display_name || '喵友'),
      avatarUrl: String(item.actor_avatar_url || ''),
      visitId: String(item.visit_id || ''),
      inviteCode: String(item.invite_code || ''),
      expiresAt: Number(item.expires_at) || 0
    };
  }

  var INVITATION_STORAGE_KEY = 'nekoSocialHubInvitationsV1';
  var VISIT_PHASES = ['sending', 'pending', 'invite_ready', 'joining', 'awaiting_accept', 'active', 'wrap_up', 'ending'];

  function normalizePhase(value) {
    var phase = String(value || '');
    if (phase === 'started' || phase === 'departed') return 'active';
    return VISIT_PHASES.indexOf(phase) !== -1 ? phase : '';
  }

  function invitationExpired(invitation) {
    return invitation && invitation.expiresAt > 0 && invitation.expiresAt <= Date.now() / 1000;
  }

  function saveInvitations() {
    if (!state.accountId) return;
    try {
      sessionStorage.setItem(INVITATION_STORAGE_KEY, JSON.stringify({
        accountId: state.accountId,
        items: Array.from(state.invitations.entries()).map(function (entry) {
          return Object.assign({ friendId: entry[0] }, entry[1]);
        })
      }));
    } catch (_) {}
  }

  function restoreInvitations() {
    if (!state.accountId || state.restoredAccountId === state.accountId) return;
    state.restoredAccountId = state.accountId;
    state.invitations.clear();
    try {
      var stored = JSON.parse(sessionStorage.getItem(INVITATION_STORAGE_KEY) || '{}');
      if (stored.accountId !== state.accountId || !Array.isArray(stored.items)) return;
      stored.items.forEach(function (item) {
        var friendId = String(item && item.friendId || '');
        var phase = normalizePhase(item && item.phase);
        if (!friendId || !phase || invitationExpired(item)) return;
        state.invitations.set(friendId, {
          visitId: String(item.visitId || ''), phase: phase,
          expiresAt: Number(item.expiresAt) || 0
        });
      });
    } catch (_) {}
  }

  function setInvitation(friendId, invitation) {
    friendId = String(friendId || '');
    if (!friendId) return;
    if (!invitation) state.invitations.delete(friendId);
    else state.invitations.set(friendId, invitation);
    saveInvitations();
  }

  function activeInvitation(exceptFriendId) {
    var found = null;
    state.invitations.forEach(function (invitation, friendId) {
      if (!found && friendId !== exceptFriendId && !invitationExpired(invitation)) found = invitation;
    });
    return found;
  }

  function invitationView(invitation) {
    var phase = invitation && invitation.phase;
    if (phase === 'sending' || phase === 'pending') return { status: '正在准备邀请', action: '发送中', tone: 'waiting' };
    if (phase === 'invite_ready') return { status: '等待回应', action: '已邀请', tone: 'waiting' };
    if (phase === 'joining') return { status: '正在连接', action: '连接中', tone: 'connecting' };
    if (phase === 'awaiting_accept') return { status: '等待接待', action: '门口等候', tone: 'connecting' };
    if (phase === 'active') return { status: '正在串门', action: '串门中', tone: 'active' };
    if (phase === 'wrap_up') return { status: '正在道别', action: '串门中', tone: 'ending' };
    if (phase === 'ending') return { status: '正在收尾', action: '即将结束', tone: 'ending' };
    return null;
  }

  function applyVisitStatus(detail) {
    detail = detail || {};
    var visitId = String(detail.visitId || detail.visit_id || '');
    var phase = normalizePhase(detail.phase || detail.action);
    var changed = false;
    state.invitations.forEach(function (invitation, friendId) {
      if (!invitation || !visitId || invitation.visitId !== visitId) return;
      if (!phase || detail.action === 'ended' || detail.phase === 'ended') {
        state.invitations.delete(friendId);
      } else if (invitation.phase !== phase) {
        invitation.phase = phase;
      }
      changed = true;
    });
    if (changed) {
      saveInvitations();
      render();
    }
  }

  function reconcileCurrentVisit() {
    var visit = window.nekoVisitChat;
    if (!visit || typeof visit.getState !== 'function') return;
    var current = visit.getState() || {};
    var currentId = String(current.visitId || current.visit_id || '');
    var changed = false;
    state.invitations.forEach(function (invitation, friendId) {
      if (invitationExpired(invitation) || (invitation.visitId && invitation.visitId !== currentId)) {
        state.invitations.delete(friendId);
        changed = true;
      }
    });
    if (currentId) applyVisitStatus(current);
    else if (changed) { saveInvitations(); render(); }
  }
  function applyInviteResponses(items) {
    (Array.isArray(items) ? items : []).forEach(function (item) {
      var notificationId = String(item && item.id || '');
      var visitId = String(item && item.visit_id || '');
      if (!notificationId || !visitId) return;
      var accepted = item.accepted === true;
      if (!accepted) {
        state.invitations.forEach(function (invitation, friendId) {
          if (invitation && invitation.visitId === visitId) state.invitations.delete(friendId);
        });
        state.toast = String(item.actor_display_name || '对方') + '这次不能来串门';
        state.toastError = false;
      }
      try {
        window.dispatchEvent(new CustomEvent('neko-social-invite-response', {
          detail: { visitId: visitId, accepted: accepted }
        }));
      } catch (_) {}
      fetch('/api/community/visit-invitations/' + encodeURIComponent(notificationId) + '/read', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' }, body: '{}'
      }).catch(function () {});
    });
    saveInvitations();
  }
  function showToast(message, isError) {
    state.toast = message || '';
    state.toastError = !!isError;
    render();
  }

  function deliverIncomingToChat(attempt) {
    if (standalone || !state.incoming || !state.incoming.visitId) return;
    var incoming = state.incoming;
    var key = incoming.id + ':' + incoming.visitId;
    if (incomingDeliveryKey === key) return;
    var visit = window.nekoVisitChat;
    if (!visit || typeof visit.presentDirectInvitation !== 'function') {
      if ((attempt || 0) < 20) {
        clearTimeout(incomingDeliveryTimer);
        incomingDeliveryTimer = setTimeout(function () {
          deliverIncomingToChat((attempt || 0) + 1);
        }, 300);
      }
      return;
    }
    incomingDeliveryKey = key;
    Promise.resolve(visit.presentDirectInvitation({
      id: incoming.id,
      actor_id: incoming.actorId,
      display_name: incoming.displayName,
      visit_id: incoming.visitId,
      expires_at: incoming.expiresAt
    })).then(function (delivered) {
      if (delivered) return;
      incomingDeliveryKey = '';
      if ((attempt || 0) < 20) {
        clearTimeout(incomingDeliveryTimer);
        incomingDeliveryTimer = setTimeout(function () {
          deliverIncomingToChat((attempt || 0) + 1);
        }, 300);
      }
    }).catch(function () {
      incomingDeliveryKey = '';
    });
  }

  async function refresh(silent) {
    silent = silent === true;
    if (!silent) {
      state.loading = true;
      render();
    }
    try {
      var response = await fetch('/api/community/visit-hub', { credentials: 'same-origin' });
      var body = await json(response);
      if (!response.ok) throw new Error(detailText(body, '读取互关好友失败'));
      state.loggedIn = body.logged_in === true;
      state.accountId = String(body.account && body.account.id || '');
      state.account = normalizeAccount(body.account);
      state.friends = Array.isArray(body.friends)
        ? body.friends.map(normalizeFriend).filter(function (friend) { return friend.id; }) : [];
      state.incoming = normalizeIncoming(body.incoming_invite);
      restoreInvitations();
      applyInviteResponses(body.invite_responses);
      reconcileCurrentVisit();
      if (!silent) {
        state.toast = '';
        state.toastError = false;
      }
    } catch (error) {
      if (!silent) {
        state.loggedIn = false;
        state.account = null;
        state.friends = [];
        state.incoming = null;
        state.toast = error && error.message || '读取互关好友失败';
        state.toastError = true;
      }
    } finally {
      if (!silent) state.loading = false;
      render();
      deliverIncomingToChat(0);
    }
  }

  function openCommunity() {
    var bridge = host();
    if (bridge && typeof bridge.openCommunity === 'function') return bridge.openCommunity();
    close();
    window.__nekoOpenFullCommunityOnce = true;
    window.dispatchEvent(new CustomEvent('live2d-social-click'));
  }
  function avatar(person, statusTone) {
    var wrap = el('span', 'neko-social-hub-avatar-wrap');
    wrap.appendChild(avatarVisual(
      person.avatarUrl,
      person.displayName,
      person.id || person.actorId
    ));
    if (statusTone) wrap.appendChild(el('span', 'neko-social-hub-status-dot ' + statusTone));
    return wrap;
  }
  function presenceLabel(status) {
    return { online: '在线', away: '离开', invisible: '隐身' }[status] || '在线';
  }
  async function setOwnPresence(selected) {
    if (!state.account || state.presenceSaving) return;
    if (['online', 'away', 'invisible'].indexOf(selected) === -1) return;
    state.presenceMenuOpen = false;
    state.presenceSaving = true;
    render();
    try {
      var response = await fetch('/api/community/presence', {
        method: 'PUT', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: selected })
      });
      var body = await json(response);
      if (!response.ok) throw new Error(detailText(body, '状态设置失败，请稍后重试'));
      state.account.presenceStatus = String(body.status || selected);
      state.toast = '';
      state.toastError = false;
    } catch (error) {
      state.toast = error && error.message || '状态设置失败，请稍后重试';
      state.toastError = true;
    } finally {
      state.presenceSaving = false;
      render();
    }
  }
  function renderOwnAccount(target) {
    if (!state.account) return;
    var account = state.account;
    var section = el('section', 'neko-social-hub-self');
    section.appendChild(avatar(account, account.presenceStatus));
    var copy = el('div', 'neko-social-hub-self-copy');
    var picker = el('div', 'neko-social-hub-presence-picker' + (state.presenceMenuOpen ? ' is-open' : ''));
    var titleRow = el('div', 'neko-social-hub-self-title-row');
    titleRow.appendChild(el('strong', 'neko-social-hub-self-name', account.displayName));
    var toggle = button('', 'neko-social-hub-presence-toggle', function () {
      if (state.presenceSaving) return;
      state.presenceMenuOpen = !state.presenceMenuOpen;
      render();
    });
    toggle.disabled = state.presenceSaving;
    toggle.setAttribute('aria-label', '设置在线状态');
    toggle.setAttribute('aria-haspopup', 'menu');
    toggle.setAttribute('aria-expanded', state.presenceMenuOpen ? 'true' : 'false');
    var arrow = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    arrow.setAttribute('class', 'neko-social-hub-presence-arrow');
    arrow.setAttribute('viewBox', '0 0 16 16');
    arrow.setAttribute('focusable', 'false');
    arrow.setAttribute('aria-hidden', 'true');
    var arrowPath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    arrowPath.setAttribute('d', 'M3 6 L8 11 L13 6');
    arrow.appendChild(arrowPath);
    toggle.appendChild(arrow);
    titleRow.appendChild(toggle);
    picker.appendChild(titleRow);
    picker.appendChild(el('span', 'neko-social-hub-presence-label',
      state.presenceSaving ? '设置中…' : presenceLabel(account.presenceStatus)));
    if (state.presenceMenuOpen) {
      var menu = el('div', 'neko-social-hub-presence-menu');
      menu.setAttribute('role', 'menu');
      [
        ['online', '在线', '好友能看到你在线'],
        ['away', '离开', '保留在线，但显示暂时离开'],
        ['invisible', '隐身', '好友会看到你处于离线状态']
      ].forEach(function (option) {
        var item = button('', 'neko-social-hub-presence-option' +
          (account.presenceStatus === option[0] ? ' is-selected' : ''), function () {
          setOwnPresence(option[0]);
        });
        item.setAttribute('role', 'menuitemradio');
        item.setAttribute('aria-checked', account.presenceStatus === option[0] ? 'true' : 'false');
        item.appendChild(el('span', 'neko-social-hub-presence-dot ' + option[0]));
        var words = el('span', 'neko-social-hub-presence-option-copy');
        words.appendChild(el('strong', '', option[1]));
        words.appendChild(el('small', '', option[2]));
        item.appendChild(words);
        menu.appendChild(item);
      });
      picker.appendChild(menu);
    }
    copy.appendChild(picker);
    section.appendChild(copy);
    (target || panel).appendChild(section);
  }
  async function joinWithCode(rawCode) {
    var code = String(rawCode || '').trim().toUpperCase();
    if (!code) return showToast('请输入邀请码', true);
    var bridge = host();
    var visit = window.nekoVisitChat;
    var join = bridge && typeof bridge.joinWithCode === 'function'
      ? function () { return bridge.joinWithCode(code); }
      : visit && typeof visit.joinWithCode === 'function'
        ? function () { return visit.joinWithCode(code); } : null;
    if (!join) return showToast('串门窗口还没有准备好，请稍后再试', true);
    showToast('正在查看邀请…', false);
    try {
      var result = await join();
      if (result === null || result === false || (result && result.status && result.status !== 202)) return false;
      if (standalone) close();
      return result || true;
    } catch (error) {
      showToast(error && error.message || '邀请码无法使用', true);
      return false;
    }
  }
  async function inviteFriend(friendId) {
    var friend = state.friends.find(function (item) { return item.id === friendId; });
    if (!friend || state.invitations.has(friendId)) return;
    if (activeInvitation(friendId)) return showToast('请先结束当前的邀请或串门，再邀请其他喵友', true);
    var bridge = host();
    var visit = window.nekoVisitChat;
    var createInvite = bridge && typeof bridge.createDirectInvite === 'function'
      ? function () { return bridge.createDirectInvite(friend.id); }
      : visit && typeof visit.createDirectInvitation === 'function'
        ? function () { return visit.createDirectInvitation(friend.id); } : null;
    if (!createInvite) {
      return showToast('请在桌面版 Neko 中发送定向邀请', true);
    }
    setInvitation(friendId, { visitId: '', phase: 'sending', expiresAt: 0 });
    showToast('正在为' + friend.displayName + '准备房间…', false);
    try {
      var invite = await createInvite();
      var visitId = String(invite && (invite.visit_id || invite.visitId) || '');
      if (!visitId) throw new Error('没有拿到定向串门房间');
      var expiresAt = Number(invite.expires_at || invite.expiresAt) || 0;
      var response = await fetch('/api/community/visit-invitations', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          target_user_id: friend.id,
          visit_id: visitId,
          expires_at: expiresAt || undefined
        })
      });
      var body = await json(response);
      if (!response.ok) throw new Error(detailText(body, '邀请没有发出去，请稍后重试'));
      setInvitation(friendId, { visitId: visitId, phase: 'invite_ready', expiresAt: expiresAt });
      state.toast = '已向' + friend.displayName + '发出串门邀请';
      state.toastError = false;
    } catch (error) {
      setInvitation(friendId, null);
      state.toast = error && error.message || '邀请没有发出去，请稍后重试';
      state.toastError = true;
    }
    render();
  }

  function friendRow(friend) {
    var invitation = state.invitations.get(friend.id) || null;
    var view = invitationView(invitation);
    var busyElsewhere = !view && !!activeInvitation(friend.id);
    var tone = view ? view.tone : (friend.presenceStatus === 'away'
      ? 'away' : (friend.online ? 'online' : 'offline'));
    var row = el('li', 'neko-social-hub-friend' + (tone ? ' is-' + tone : ''));
    row.appendChild(avatar(friend, tone));
    var copy = el('div', 'neko-social-hub-friend-copy');
    copy.appendChild(el('strong', 'neko-social-hub-name', friend.displayName));
    copy.appendChild(el('span', 'neko-social-hub-status', view ? view.status : friendStatus(friend)));
    row.appendChild(copy);
    // 离线好友只展示身份与最近在线信息。已有邀请状态仍可显示状态按钮，
    // 但普通离线行不能从 DOM 中触发新的串门邀请。
    if (view || friend.online) {
      var action = button(view ? view.action : (busyElsewhere ? '稍后再邀' : '邀请来玩'),
        'neko-social-hub-button' + (!view && !busyElsewhere ? ' primary' : ''), function () {
        inviteFriend(friend.id);
      });
      action.disabled = !!view || busyElsewhere;
      row.appendChild(action);
    }
    return row;
  }
  function relativeLastSeen(value) {
    var timestamp = Date.parse(String(value || ''));
    if (!Number.isFinite(timestamp)) return '';
    var elapsed = Math.max(0, Date.now() - timestamp);
    var minutes = Math.floor(elapsed / 60000);
    if (minutes < 1) return '刚刚';
    if (minutes < 60) return minutes + ' 分钟前';
    var hours = Math.floor(minutes / 60);
    if (hours < 24) return hours + ' 小时前';
    var days = Math.floor(hours / 24);
    if (days < 30) return days + ' 天前';
    var months = Math.floor(days / 30);
    if (months < 12) return months + ' 个月前';
    return Math.floor(months / 12) + ' 年前';
  }
  function friendStatus(friend) {
    if (friend.presenceStatus === 'away') return '离开';
    if (friend.online) return '在线';
    var lastSeen = relativeLastSeen(friend.lastSeenAt);
    return lastSeen ? '上次在线 ' + lastSeen : '离线';
  }
  function renderFriendGroup(title, friends, target) {
    if (!friends.length) return;
    target.appendChild(el('h3', 'neko-social-hub-heading', title + ' (' + friends.length + ')'));
    var list = el('ul', 'neko-social-hub-friends');
    friends.forEach(function (friend) { list.appendChild(friendRow(friend)); });
    target.appendChild(list);
  }
  function renderEmpty(target) {
    var empty = el('section', 'neko-social-hub-empty');
    if (state.loading) empty.appendChild(el('strong', '', '正在读取当前账号的互关好友…'));
    else if (!state.loggedIn) {
      empty.appendChild(el('strong', '', '还没有登录喵宇宙'));
      empty.appendChild(el('span', '', '请先在 Neko 设置中登录社区账号'));
    } else {
      empty.appendChild(el('strong', '', '暂时没有互关喵友'));
      empty.appendChild(el('span', '', '双方互相关注后，就会出现在这里'));
    }
    target.appendChild(empty);
  }
  function render() {
    if (!panel) return;
    panel.replaceChildren();
    var header = el('header', 'neko-social-hub-header');
    header.appendChild(image('/static/icons/neko_community_on.png', '', 'neko-social-hub-logo'));
    header.appendChild(el('h2', 'neko-social-hub-title', '喵宇宙'));
    header.appendChild(button('打开完整社区', 'neko-social-hub-community-link', openCommunity));
    panel.appendChild(header);
    if (state.loggedIn && state.account) renderOwnAccount(panel);

    // 账号区固定在顶部，好友分组独立滚动，避免滚动好友时丢失自己的头像和状态入口。
    var friendScroller = el('div', 'neko-social-hub-friend-scroller');
    panel.appendChild(friendScroller);
    if (state.loading || !state.loggedIn || !state.friends.length) renderEmpty(friendScroller);
    else {
      var onlineFriends = state.friends.filter(function (friend) { return friend.online; });
      var offlineFriends = state.friends.filter(function (friend) { return !friend.online; });
      renderFriendGroup('在线好友', onlineFriends, friendScroller);
      renderFriendGroup('离线好友', offlineFriends, friendScroller);
    }
    if (state.toast) panel.appendChild(el('div', 'neko-social-hub-toast' + (state.toastError ? ' is-error' : ''), state.toast));
    if (state.open && !standalone) requestAnimationFrame(positionPanel);
  }

  function panelViewport() {
    var visual = window.visualViewport;
    var left = visual ? Number(visual.offsetLeft) || 0 : 0;
    var top = visual ? Number(visual.offsetTop) || 0 : 0;
    var width = visual && visual.width > 0 ? visual.width : innerWidth;
    var height = visual && visual.height > 0 ? visual.height : innerHeight;
    var right = left + width;
    var bottom = top + height;

    // Electron 的透明桌面窗可能覆盖 Dock / 任务栏，innerHeight 因而比实际可用区大。
    // 把 visual viewport 与 screen.avail* 的工作区取交集，沿用其他头像悬浮框的边界思路。
    var screenRef = window.screen || {};
    var windowLeft = Number(window.screenX);
    var windowTop = Number(window.screenY);
    var availableLeft = Number(screenRef.availLeft);
    var availableTop = Number(screenRef.availTop);
    var availableWidth = Number(screenRef.availWidth);
    var availableHeight = Number(screenRef.availHeight);
    if (Number.isFinite(windowLeft) && Number.isFinite(windowTop)
        && Number.isFinite(availableLeft) && Number.isFinite(availableTop)
        && availableWidth > 0 && availableHeight > 0) {
      left = Math.max(left, availableLeft - windowLeft);
      top = Math.max(top, availableTop - windowTop);
      right = Math.min(right, availableLeft + availableWidth - windowLeft);
      bottom = Math.min(bottom, availableTop + availableHeight - windowTop);
    }
    return { left: left, top: top, right: Math.max(left + 1, right), bottom: Math.max(top + 1, bottom) };
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(value, Math.max(min, max)));
  }

  function positionPanel() {
    if (standalone || !panel || !state.open) return;
    var margin = 12;
    var gap = 14;
    var anchor = document.querySelector('[id$="-btn-social"]');
    var viewport = panelViewport();
    var leftEdge = viewport.left + margin;
    var rightEdge = viewport.right - margin;
    var topEdge = viewport.top + margin;
    var bottomEdge = viewport.bottom - margin;
    var availableWidth = Math.max(1, rightEdge - leftEdge);
    var availableHeight = Math.max(1, bottomEdge - topEdge);

    panel.style.maxHeight = '';
    panel.style.maxWidth = Math.floor(availableWidth) + 'px';
    var cssMaxHeight = Number.parseFloat(window.getComputedStyle(panel).maxHeight);
    var maxHeight = Math.min(
      Number.isFinite(cssMaxHeight) ? cssMaxHeight : availableHeight,
      availableHeight
    );
    panel.style.maxHeight = Math.max(1, Math.floor(maxHeight)) + 'px';

    var width = Math.min(panel.offsetWidth || 392, availableWidth);
    var height = Math.min(panel.scrollHeight, maxHeight);
    var left = clamp(rightEdge - width, leftEdge, rightEdge - width);
    var top = clamp((topEdge + bottomEdge - height) / 2, topEdge, bottomEdge - height);
    if (anchor) {
      var rect = anchor.getBoundingClientRect();
      var rightCandidate = rect.right + gap;
      var leftCandidate = rect.left - width - gap;
      if (rightCandidate + width <= rightEdge) left = rightCandidate;
      else if (leftCandidate >= leftEdge) left = leftCandidate;
      else left = clamp(rightCandidate, leftEdge, rightEdge - width);
      panel.style.transformOrigin = left >= rect.right ? 'left top' : 'right top';
      // 顶部仍优先和按钮对齐；一旦底部会越过工作区（例如被 macOS Dock 遮住），
      // 就整体向上夹紧。内容超过可用高度时由面板自身滚动。
      top = clamp(rect.top, topEdge, bottomEdge - height);
    } else {
      panel.style.transformOrigin = 'center center';
    }
    panel.style.left = Math.round(left) + 'px';
    panel.style.top = Math.round(top) + 'px';
  }
  function mount() {
    if (panel) return;
    panel = el('section', 'neko-social-hub-panel');
    panel.tabIndex = -1;
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', '喵宇宙串门');
    if (standalone) (document.getElementById('neko-social-hub-root') || document.body).appendChild(panel);
    else {
      layer = el('div', 'neko-social-hub-layer');
      layer.hidden = true;
      layer.appendChild(panel);
      document.body.appendChild(layer);
    }
  }
  function open() {
    mount();
    closeAvatarPopups();
    previousFocus = document.activeElement;
    state.open = true;
    if (layer) layer.hidden = false;
    notifyOverlayVisibilityChanged();
    render();
    requestAnimationFrame(function () { positionPanel(); panel.focus({ preventScroll: true }); });
    refresh();
    return true;
  }
  function close() {
    if (standalone) {
      var bridge = host();
      return bridge && typeof bridge.close === 'function' ? bridge.close() : window.close();
    }
    if (!layer) return;
    state.open = false;
    layer.hidden = true;
    notifyOverlayVisibilityChanged();
    if (previousFocus && typeof previousFocus.focus === 'function') previousFocus.focus();
  }
  function toggle() { return state.open ? (close(), true) : open(); }

  document.addEventListener('keydown', function (event) {
    if (state.open && event.key === 'Escape') { event.preventDefault(); close(); }
  });
  document.addEventListener('pointerdown', function (event) {
    if (standalone || !state.open || !panel) return;
    if (panel.contains(event.target)) return;
    var trigger = event.target && event.target.closest
      ? event.target.closest('[id$="-btn-social"]') : null;
    if (!trigger) close();
  }, true);
  window.addEventListener('neko-avatar-popup-opening', function () {
    if (state.open && !standalone) close();
  });
  window.addEventListener('neko-visit-status-change', function (event) {
    applyVisitStatus(event && event.detail);
  });
  window.addEventListener('neko-social-incoming-resolved', function (event) {
    var detail = event && event.detail || {};
    if (!state.incoming || String(detail.id || '') !== state.incoming.id) return;
    var incoming = state.incoming;
    state.incoming = null;
    incomingDeliveryKey = '';
    if (detail.accepted === true && incoming.actorId) {
      setInvitation(incoming.actorId, {
        visitId: String(detail.visitId || incoming.visitId || ''),
        phase: 'joining',
        expiresAt: Number(detail.expiresAt || incoming.expiresAt) || 0
      });
    }
    render();
  });
  window.addEventListener('resize', positionPanel);
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', positionPanel);
    window.visualViewport.addEventListener('scroll', positionPanel);
  }
  setInterval(function () {
    var changed = false;
    state.invitations.forEach(function (invitation, friendId) {
      if (invitationExpired(invitation)) {
        state.invitations.delete(friendId);
        changed = true;
      }
    });
    if (changed) {
      saveInvitations();
      render();
    }
    reconcileCurrentVisit();
  }, 5000);
  // 面板关着时也维持社区 presence；静默刷新不会清空现有好友或制造 loading 闪烁。
  setInterval(function () { refresh(true); }, 60000);
  var socialApi = social();
  if (socialApi && typeof socialApi.onNotifyIncoming === 'function') {
    socialApi.onNotifyIncoming(function (notification) {
      var payload = notification && notification.payload;
      var kind = String(notification && notification.kind || payload && payload.type || '');
      if (kind === 'visit_invite' || kind === 'visit_invite_response') refresh();
    });
  }

  window.nekoSocialHub = {
    open: open, close: close, toggle: toggle, handlePrimaryClick: toggle,
    openFullCommunity: openCommunity, refresh: refresh, _state: state,
    _applyVisitStatus: applyVisitStatus
  };
  if (standalone) requestAnimationFrame(open);
  else requestAnimationFrame(refresh);
})();
