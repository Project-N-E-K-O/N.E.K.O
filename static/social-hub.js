/** 喵宇宙串门：只显示当前社区账号的真实互关。 */
(function () {
  'use strict';
  if (window.nekoSocialHub) return;

  var standalone = window.__NEKO_SOCIAL_HUB_STANDALONE__ === true;
  var state = {
    open: standalone, loading: true, loggedIn: false, loadFailed: false, loadError: '',
    toast: '', toastError: false, incoming: null, friends: [], recommendations: [],
    recommendationBusy: new Set(), accountId: '', account: null,
    presenceMenuOpen: false, presenceSaving: false,
    invitations: new Map(), restoredAccountId: ''
  };
  var layer = null;
  var panel = null;
  var presenceMenu = null;
  var previousFocus = null;
  var openGeneration = 0;
  var dismissedRecommendationFocusKey = '';
  // 重试时按钮会先被“读取中”卡片替换；记住焦点目标，结果出来后再还给对应按钮。
  var pendingFocusKey = '';
  // 关注补位可能耗时；只有用户仍停在原关注按钮时才恢复，避免覆盖其新选中的控件。
  var pendingFocusOriginKey = '';
  var incomingDeliveryKey = '';
  var incomingDeliveryTimer = 0;
  var hubRefreshRevision = 0;
  var appliedRefreshRevision = 0;
  var appliedRecommendationRevision = 0;
  var loadingRefreshRevision = 0;
  var invitationSnapshotRevision = 0;
  var presenceRefreshInFlight = false;
  var presencePushRevision = 0;
  var presencePushRevisions = new Map();
  var dismissedRecommendationIds = new Set();

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
  function interpolate(text, params) {
    return String(text).replace(/\{\{\s*(\w+)\s*\}\}/g, function (match, name) {
      return params && params[name] !== undefined ? String(params[name]) : match;
    });
  }
  function tr(key, fallback, params) {
    if (typeof window.t !== 'function') return interpolate(fallback, params);
    var value = window.t(key, params || {});
    return !value || value === key ? interpolate(fallback, params) : value;
  }
  function svgIcon(className, paths) {
    var ns = 'http://www.w3.org/2000/svg';
    var svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('class', className);
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('focusable', 'false');
    svg.setAttribute('aria-hidden', 'true');
    paths.forEach(function (d) {
      var path = document.createElementNS(ns, 'path');
      path.setAttribute('d', d);
      svg.appendChild(path);
    });
    return svg;
  }
  function focusKey(node, key) {
    node.setAttribute('data-neko-focus-key', key);
    return node;
  }

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
    if (body && body.detail === 'community_login_required') return tr('visit.socialHub.loginRequired', '请先在 N.E.K.O 设置里登录喵宇宙');
    if (body && body.detail === 'mutual_follow_required') return tr('visit.socialHub.mutualRequired', '你们已不再互相关注，无法发送邀请');
    if (body && body.detail === 'target_offline') return tr('visit.socialHub.targetOffline', '对方当前离线，暂时不能邀请');
    if (body && body.detail === 'community_unavailable') return tr('visit.socialHub.communityUnavailable', '暂时连不上喵宇宙，稍后再试一次');
    if (body && body.detail === 'user_relationship_blocked') return tr('visit.socialHub.followBlocked', '当前关系无法关注这位用户');
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
  function normalizeRecommendation(item) {
    if (!item || !item.id) return null;
    var reason = String(item.reason || 'active_user');
    if (['active_user', 'liked_you', 'recent_follower'].indexOf(reason) === -1) reason = 'active_user';
    return {
      id: String(item.id),
      displayName: String(item.display_name || item.username || '喵友'),
      username: String(item.username || ''),
      avatarUrl: String(item.avatar_url || ''),
      reason: reason,
      followsViewer: item.follows_viewer === true
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
  var VISIT_PHASES = ['sending', 'pending', 'invite_ready', 'cancelling', 'joining', 'awaiting_accept', 'active', 'wrap_up', 'ending'];

  function normalizePhase(value) {
    var phase = String(value || '');
    if (phase === 'started' || phase === 'departed') return 'active';
    return VISIT_PHASES.indexOf(phase) !== -1 ? phase : '';
  }

  function invitationExpired(invitation) {
    return invitation && invitation.phase === 'invite_ready' &&
      invitation.expiresAt > 0 && invitation.expiresAt <= Date.now() / 1000;
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
        var restoredVisitId = String(item && item.visitId || '');
        if (!friendId || !phase || !restoredVisitId || invitationExpired(item)) return;
        state.invitations.set(friendId, {
          visitId: restoredVisitId, phase: phase,
          expiresAt: Number(item.expiresAt) || 0,
          displayName: String(item.displayName || ''),
          avatarUrl: String(item.avatarUrl || '')
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
    if (phase === 'sending' || phase === 'pending') return { status: tr('visit.socialHub.preparing', '正在准备邀请'), action: tr('visit.socialHub.sending', '发送中'), tone: 'waiting' };
    if (phase === 'invite_ready') return { status: tr('visit.socialHub.waitingResponse', '等待回应'), action: tr('visit.socialHub.cancelInvite', '取消邀请'), tone: 'waiting', canCancel: true };
    if (phase === 'cancelling') return { status: tr('visit.socialHub.cancelling', '正在取消'), action: tr('visit.socialHub.cancelling', '正在取消'), tone: 'waiting' };
    if (phase === 'joining') return { status: tr('visit.socialHub.connecting', '正在连接'), action: tr('visit.socialHub.connectingAction', '连接中'), tone: 'connecting' };
    if (phase === 'awaiting_accept') return { status: tr('visit.socialHub.waitingHost', '等待接待'), action: tr('visit.socialHub.atDoor', '门口等候'), tone: 'connecting' };
    if (phase === 'active') return { status: tr('visit.socialHub.visiting', '正在串门'), action: tr('visit.socialHub.inVisit', '串门中'), tone: 'active' };
    if (phase === 'wrap_up') return { status: tr('visit.socialHub.sayingGoodbye', '正在道别'), action: tr('visit.socialHub.inVisit', '串门中'), tone: 'ending' };
    if (phase === 'ending') return { status: tr('visit.socialHub.ending', '正在收尾'), action: tr('visit.socialHub.endingSoon', '即将结束'), tone: 'ending' };
    return null;
  }

  function applyVisitStatus(detail) {
    detail = detail || {};
    var visitId = String(detail.visitId || detail.visit_id || '');
    var phase = normalizePhase(detail.phase || detail.action);
    var changed = false;
    var targetUserId = String(detail.targetUserId || '');
    if (visitId && phase && detail.active === true && detail.side === 'host' && targetUserId) {
      var restored = state.invitations.get(targetUserId);
      if (!restored || restored.visitId !== visitId) {
        var friend = state.friends.find(function (item) { return item.id === targetUserId; });
        state.invitations.set(targetUserId, {
          visitId: visitId, phase: phase, expiresAt: Number(detail.inviteExpiresAt) || 0,
          displayName: friend ? friend.displayName : '', avatarUrl: friend ? friend.avatarUrl : ''
        });
        changed = true;
      }
    }
    state.invitations.forEach(function (invitation, friendId) {
      if (!invitation) return;
      if (!visitId && detail.ready === true && detail.active === false && invitation.visitId) {
        state.invitations.delete(friendId);
        changed = true;
        return;
      }
      if (!visitId || invitation.visitId !== visitId) return;
      if (!phase || detail.action === 'ended' || detail.phase === 'ended') {
        state.invitations.delete(friendId);
        changed = true;
      } else if (invitation.phase !== phase) {
        invitation.phase = phase;
        changed = true;
      }
      if (typeof detail.inviteExpiresAt === 'number' && detail.inviteExpiresAt > 0
          && invitation.expiresAt !== detail.inviteExpiresAt) {
        invitation.expiresAt = detail.inviteExpiresAt;
        changed = true;
      }
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
    // 刷新后的聊天窗还未拿到权威状态时，不用初始空值删掉仍有效的邀请。
    if (current.ready === false) return;
    var currentId = String(current.visitId || current.visit_id || '');
    var changed = false;
    state.invitations.forEach(function (invitation, friendId) {
      if (invitationExpired(invitation) || (invitation.visitId && invitation.visitId !== currentId)) {
        state.invitations.delete(friendId);
        changed = true;
      }
    });
    if (currentId) applyVisitStatus(current);
    if (changed) { saveInvitations(); render(); }
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
        state.toast = tr('visit.socialHub.declined', '{{name}}这次不能来串门', {
          name: String(item.actor_display_name || tr('visit.socialHub.otherParty', '对方'))
        });
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

  function applyInvitationSnapshot(body, revision) {
    if (revision < invitationSnapshotRevision) return false;
    invitationSnapshotRevision = revision;
    body = body || {};
    state.incoming = normalizeIncoming(body.incoming_invite);
    applyInviteResponses(body.invite_responses);
    reconcileCurrentVisit();
    deliverIncomingToChat(0);
    return true;
  }

  function applyRecommendationSnapshot(body, revision) {
    if (revision < appliedRecommendationRevision) return false;
    appliedRecommendationRevision = revision;
    state.recommendations = Array.isArray(body && body.recommendations)
      ? body.recommendations.slice(0, 3).map(normalizeRecommendation).filter(function (item) {
        return item && !dismissedRecommendationIds.has(item.id);
      }) : [];
    return true;
  }

  async function refresh(silent, replaceRecommendationsRequested) {
    silent = silent === true;
    replaceRecommendationsRequested = replaceRecommendationsRequested === true;
    var refreshGeneration = openGeneration;
    var refreshRevision = ++hubRefreshRevision;
    var presenceRevisionAtStart = presencePushRevision;
    if (!silent) {
      loadingRefreshRevision = refreshRevision;
      state.loading = true;
      render();
    }
    try {
      var response = await fetch('/api/community/visit-hub', { credentials: 'same-origin' });
      var body = await json(response);
      // 登录态失效按“未登录”处理，引导重新登录，而不是显示成加载失败。
      if (!response.ok && body && body.detail === 'community_login_required') body = { logged_in: false };
      else if (!response.ok) {
        var serverError = new Error(detailText(body, tr('visit.socialHub.readFailed', '读取好友失败')));
        serverError.fromServer = true;
        throw serverError;
      }
      // Missing/malformed successful JSON is a read failure, never evidence of
      // logout. A silent refresh must keep the last confirmed account view.
      if (!body || typeof body.logged_in !== 'boolean'
          || (body.logged_in && (!body.account || !body.account.id))) {
        throw new Error('community_invalid_response');
      }
      // 浮窗内容按打开代数隔离，但邀请属于聊天事件，关闭浮窗后仍需及时投递。
      if (refreshGeneration !== openGeneration) {
        applyInvitationSnapshot(body, refreshRevision);
        return;
      }
      var nextAccountId = String(body.account && body.account.id || '');
      // 只拒绝早于最近已应用资料的响应；较新的请求失败时，较早的成功响应仍可兜底。
      if (refreshRevision < appliedRefreshRevision) {
        // 推荐替换有独立版本：后发的普通静默刷新不能吞掉打开读取或关注补位。
        if ((replaceRecommendationsRequested || !silent) && nextAccountId === state.accountId) {
          applyRecommendationSnapshot(body, refreshRevision);
        }
        applyInvitationSnapshot(body, refreshRevision);
        return;
      }
      appliedRefreshRevision = refreshRevision;
      state.loadFailed = false;
      state.loadError = '';
      state.loggedIn = body.logged_in === true;
      var accountChanged = nextAccountId !== state.accountId;
      if (accountChanged) {
        state.presenceMenuOpen = false;
        dismissedRecommendationIds.clear();
        state.recommendationBusy.clear();
      }
      var shouldReplaceRecommendations = replaceRecommendationsRequested || !silent || accountChanged;
      state.accountId = nextAccountId;
      state.account = normalizeAccount(body.account);
      var nextFriends = Array.isArray(body.friends)
        ? body.friends.map(normalizeFriend).filter(function (friend) { return friend.id; }) : [];
      if (!accountChanged) {
        var previousFriends = new Map(state.friends.map(function (friend) {
          return [friend.id, friend];
        }));
        nextFriends.forEach(function (friend) {
          var previous = previousFriends.get(friend.id);
          if (!previous || (presencePushRevisions.get(friend.id) || 0) <= presenceRevisionAtStart) return;
          friend.online = previous.online;
          friend.presenceStatus = previous.presenceStatus;
          friend.lastSeenAt = previous.lastSeenAt;
        });
      }
      state.friends = nextFriends;
      if (shouldReplaceRecommendations) applyRecommendationSnapshot(body, refreshRevision);
      restoreInvitations();
      applyInvitationSnapshot(body, refreshRevision);
      if (!silent) {
        state.toast = '';
        state.toastError = false;
      }
    } catch (error) {
      if (refreshGeneration !== openGeneration || refreshRevision < appliedRefreshRevision) return;
      if (!silent) {
        state.loadFailed = true;
        state.loggedIn = false;
        state.account = null;
        state.friends = [];
        state.recommendations = [];
        state.recommendationBusy.clear();
        state.incoming = null;
        // 失败原因直接写进空状态卡片（带重试按钮），不再额外弹一条重复的红色提示。
        // 服务端给出的原因照常显示；网络层异常（浏览器的 "Failed to fetch" 等）换成本地化文案。
        state.loadError = error && error.fromServer && error.message
          ? error.message
          : tr('visit.socialHub.communityUnavailable', '暂时连不上喵宇宙，请稍后再试');
        state.toast = '';
        state.toastError = false;
      }
    } finally {
      if (refreshGeneration === openGeneration) {
        // 静默刷新不拥有 loading；重叠读取也只能由最近的非静默刷新结束它。
        if (!silent && refreshRevision === loadingRefreshRevision) state.loading = false;
        render();
      }
    }
  }

  // 独立窗口只能借宿主桥打开社区；页面内浮层则交给 live2d-social-click 处理。
  function canOpenCommunity() {
    var bridge = host();
    return !standalone || !!(bridge && typeof bridge.openCommunity === 'function');
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
    return {
      online: tr('visit.socialHub.presenceOnline', '在线'),
      away: tr('visit.socialHub.presenceAway', '离开'),
      invisible: tr('visit.socialHub.presenceInvisible', '隐身')
    }[status] || tr('visit.socialHub.presenceOnline', '在线');
  }
  function hubContains(node) {
    return !!node && ((panel && panel.contains(node)) || (presenceMenu && presenceMenu.contains(node)));
  }
  function removePresenceMenu() {
    if (presenceMenu) presenceMenu.remove();
    presenceMenu = null;
    if (panel) panel.removeAttribute('aria-owns');
  }
  function closePresenceMenu(restoreFocus) {
    state.presenceMenuOpen = false;
    removePresenceMenu();
    var picker = panel && panel.querySelector('.neko-social-hub-presence-picker');
    if (picker) picker.classList.remove('is-open');
    var toggle = panel && panel.querySelector('.neko-social-hub-presence-toggle');
    if (toggle) {
      toggle.setAttribute('aria-expanded', 'false');
      toggle.removeAttribute('aria-controls');
    }
    if (restoreFocus && toggle) toggle.focus({ preventScroll: true });
  }
  function focusPresenceOption(index) {
    if (!presenceMenu) return;
    var options = presenceMenu.querySelectorAll('[role="menuitemradio"]');
    var target = index === undefined
      ? presenceMenu.querySelector('[aria-checked="true"]') : options[(index + options.length) % options.length];
    if (target) target.focus();
  }
  async function setOwnPresence(selected) {
    if (!state.account || state.presenceSaving) return;
    if (['online', 'away', 'invisible'].indexOf(selected) === -1) return;
    var restorePresenceFocus = presenceMenu && presenceMenu.contains(document.activeElement);
    state.presenceMenuOpen = false;
    state.presenceSaving = true;
    render();
    if (restorePresenceFocus) panel.focus({ preventScroll: true });
    try {
      var response = await fetch('/api/community/presence', {
        method: 'PUT', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: selected })
      });
      var body = await json(response);
      if (!response.ok) throw new Error(detailText(body, tr('visit.socialHub.presenceFailed', '状态设置失败，请稍后重试')));
      state.account.presenceStatus = String(body.status || selected);
      state.toast = '';
      state.toastError = false;
    } catch (error) {
      state.toast = error && error.message || tr('visit.socialHub.presenceFailed', '状态设置失败，请稍后重试');
      state.toastError = true;
    } finally {
      state.presenceSaving = false;
      var returnPresenceFocus = restorePresenceFocus && document.activeElement === panel;
      render();
      var toggle = panel && panel.querySelector('.neko-social-hub-presence-toggle');
      if (returnPresenceFocus && toggle) toggle.focus({ preventScroll: true });
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
      if (state.presenceMenuOpen) focusPresenceOption();
    });
    toggle.addEventListener('keydown', function (event) {
      if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
      event.preventDefault();
      state.presenceMenuOpen = true;
      render();
      focusPresenceOption(event.key === 'ArrowUp' ? -1 : 0);
    });
    toggle.id = 'neko-social-hub-presence-toggle';
    toggle.disabled = state.presenceSaving;
    focusKey(toggle, 'presence-toggle');
    toggle.setAttribute('aria-label', tr('visit.socialHub.setPresence', '设置在线状态'));
    toggle.setAttribute('aria-haspopup', 'menu');
    toggle.setAttribute('aria-expanded', state.presenceMenuOpen ? 'true' : 'false');
    if (state.presenceMenuOpen) toggle.setAttribute('aria-controls', 'neko-social-hub-presence-menu');
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
      state.presenceSaving ? tr('visit.socialHub.setting', '设置中…') : presenceLabel(account.presenceStatus)));
    copy.appendChild(picker);
    section.appendChild(copy);
    (target || panel).appendChild(section);
  }
  function renderPresenceMenu() {
    var scrollTop = presenceMenu ? presenceMenu.scrollTop : 0;
    removePresenceMenu();
    if (!state.open || !state.loggedIn || !state.account) state.presenceMenuOpen = false;
    if (state.presenceMenuOpen) {
      var account = state.account;
      var menu = el('div', 'neko-social-hub-presence-menu');
      menu.id = 'neko-social-hub-presence-menu';
      menu.setAttribute('role', 'menu');
      menu.setAttribute('aria-labelledby', 'neko-social-hub-presence-toggle');
      menu.style.visibility = 'hidden';
      [
        ['online', tr('visit.socialHub.presenceOnline', '在线'), tr('visit.socialHub.presenceOnlineHint', '好友能看到你在线')],
        ['away', tr('visit.socialHub.presenceAway', '离开'), tr('visit.socialHub.presenceAwayHint', '保留在线，但显示暂时离开')],
        ['invisible', tr('visit.socialHub.presenceInvisible', '隐身'), tr('visit.socialHub.presenceInvisibleHint', '好友会看到你处于离线状态')]
      ].forEach(function (option) {
        var item = button('', 'neko-social-hub-presence-option' +
          (account.presenceStatus === option[0] ? ' is-selected' : ''), function () {
          setOwnPresence(option[0]);
        });
        item.setAttribute('role', 'menuitemradio');
        focusKey(item, 'presence-' + option[0]);
        item.setAttribute('aria-checked', account.presenceStatus === option[0] ? 'true' : 'false');
        item.appendChild(el('span', 'neko-social-hub-presence-dot ' + option[0]));
        var words = el('span', 'neko-social-hub-presence-option-copy');
        words.appendChild(el('strong', '', option[1]));
        words.appendChild(el('small', '', option[2]));
        item.appendChild(words);
        menu.appendChild(item);
      });
      presenceMenu = menu;
      (layer || panel.parentNode).appendChild(menu);
      panel.setAttribute('aria-owns', menu.id);
      positionPresenceMenu();
      menu.scrollTop = scrollTop;
    }
  }
  function positionPresenceMenu() {
    if (!presenceMenu || !state.open) return;
    var picker = panel.querySelector('.neko-social-hub-presence-picker');
    if (!picker) return;
    var anchor = picker.getBoundingClientRect();
    var viewport = panelViewport();
    var leftEdge = viewport.left + 12;
    var rightEdge = viewport.right - 12;
    var topEdge = viewport.top + 12;
    var bottomEdge = viewport.bottom - 12;
    presenceMenu.style.maxWidth = Math.max(1, rightEdge - leftEdge) + 'px';
    // 测量完整内容，不临时解除限高，否则会重置已聚焦选项的滚动位置。
    var naturalHeight = presenceMenu.scrollHeight + presenceMenu.offsetHeight - presenceMenu.clientHeight;
    var below = Math.max(0, bottomEdge - anchor.bottom - 6);
    var above = Math.max(0, anchor.top - topEdge - 6);
    var opensBelow = naturalHeight <= below || below >= above;
    presenceMenu.style.maxHeight = Math.max(1, Math.floor(opensBelow ? below : above)) + 'px';
    var top = opensBelow ? anchor.bottom + 6 : anchor.top - 6 - presenceMenu.offsetHeight;
    presenceMenu.style.left = Math.round(clamp(anchor.left - 8, leftEdge, rightEdge - presenceMenu.offsetWidth)) + 'px';
    presenceMenu.style.top = Math.round(clamp(top, topEdge, bottomEdge - presenceMenu.offsetHeight)) + 'px';
    presenceMenu.style.visibility = '';
  }
  async function joinWithCode(rawCode) {
    var code = String(rawCode || '').trim().toUpperCase();
    if (!code) return showToast(tr('visit.socialHub.enterCode', '请输入邀请码'), true);
    var bridge = host();
    var visit = window.nekoVisitChat;
    var join = bridge && typeof bridge.joinWithCode === 'function'
      ? function () { return bridge.joinWithCode(code); }
      : visit && typeof visit.joinWithCode === 'function'
        ? function () { return visit.joinWithCode(code); } : null;
    if (!join) return showToast(tr('visit.socialHub.windowNotReady', '串门窗口还没有准备好，请稍后再试'), true);
    showToast(tr('visit.socialHub.checkingInvite', '正在查看邀请…'), false);
    try {
      var result = await join();
      if (result === null || result === false || (result && result.status && result.status !== 202)) return false;
      if (standalone) close();
      return result || true;
    } catch (error) {
      showToast(error && error.message || tr('visit.socialHub.codeUnavailable', '邀请码无法使用'), true);
      return false;
    }
  }
  async function inviteFriend(friendId) {
    var friend = state.friends.find(function (item) { return item.id === friendId; });
    if (!friend || state.invitations.has(friendId)) return;
    if (activeInvitation(friendId)) return showToast(tr('visit.socialHub.activeInvite', '请先结束当前的邀请或串门，再邀请其他喵友'), true);
    var bridge = host();
    var visit = window.nekoVisitChat;
    var createInvite = bridge && typeof bridge.createDirectInvite === 'function'
      ? function () { return bridge.createDirectInvite(friend.id); }
      : visit && typeof visit.createDirectInvitation === 'function'
        ? function () { return visit.createDirectInvitation(friend.id); } : null;
    if (!createInvite) {
      return showToast(tr('visit.socialHub.desktopRequired', '请在桌面版 Neko 中发送定向邀请'), true);
    }
    setInvitation(friendId, {
      visitId: '', phase: 'sending', expiresAt: 0,
      displayName: friend.displayName, avatarUrl: friend.avatarUrl
    });
    showToast(tr('visit.socialHub.preparingRoom', '正在为 {{name}} 准备房间…', { name: friend.displayName }), false);
    var createdVisitId = '';
    try {
      var invite = await createInvite();
      var visitId = String(invite && (invite.visit_id || invite.visitId) || '');
      if (!visitId) throw new Error(tr('visit.socialHub.roomMissing', '没有拿到定向串门房间'));
      createdVisitId = visitId;
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
      if (!response.ok) throw new Error(detailText(body, tr('visit.socialHub.sendFailed', '邀请没有发出去，请稍后重试')));
      setInvitation(friendId, {
        visitId: visitId, phase: 'invite_ready', expiresAt: expiresAt,
        displayName: friend.displayName, avatarUrl: friend.avatarUrl
      });
      state.toast = tr('visit.socialHub.invited', '已向 {{name}} 发出串门邀请', { name: friend.displayName });
      state.toastError = false;
    } catch (error) {
      if (createdVisitId) {
        var cancelled = false;
        try { cancelled = await cancelDirectVisit(createdVisitId); } catch (_) {}
        if (!cancelled) {
          // The room is still waiting; keep the cancel button so it can be closed later.
          setInvitation(friendId, {
            visitId: createdVisitId, phase: 'invite_ready', expiresAt: Number(expiresAt) || 0,
            displayName: friend.displayName, avatarUrl: friend.avatarUrl
          });
          state.toast = tr('visit.socialHub.cancelFailed', '邀请暂时无法取消，请稍后重试');
          state.toastError = true;
          render();
          return;
        }
      }
      setInvitation(friendId, null);
      state.toast = error && error.message || tr('visit.socialHub.sendFailed', '邀请没有发出去，请稍后重试');
      state.toastError = true;
    }
    render();
  }

  async function cancelDirectVisit(visitId) {
    var bridge = host();
    var visit = window.nekoVisitChat;
    if (bridge && typeof bridge.cancelDirectInvite === 'function') {
      return bridge.cancelDirectInvite(visitId);
    }
    if (visit && typeof visit.cancelDirectInvitation === 'function') {
      return visit.cancelDirectInvitation(visitId);
    }
    return false;
  }

  async function cancelInvitation(friendId, invitation) {
    if (!invitation || invitation.phase !== 'invite_ready') return;
    setInvitation(friendId, Object.assign({}, invitation, { phase: 'cancelling' }));
    render();
    try {
      if (!await cancelDirectVisit(invitation.visitId)) throw new Error('cancel_failed');
      // 保持“正在取消”，由 ended / 空闲快照确认收尾，避免后端还占用时提前显示可再次邀请。
      state.toast = '';
      state.toastError = false;
    } catch (_) {
      setInvitation(friendId, Object.assign({}, invitation, { phase: 'invite_ready' }));
      state.toast = tr('visit.socialHub.cancelFailed', '邀请暂时无法取消，请稍后重试');
      state.toastError = true;
    }
    render();
  }

  function recommendationReason(recommendation) {
    if (recommendation.reason === 'recent_follower') {
      return tr('visit.socialHub.recommendationRecentFollower', '最近关注了你');
    }
    if (recommendation.reason === 'liked_you') {
      return tr('visit.socialHub.recommendationLikedYou', '最近赞过你的内容');
    }
    return tr('visit.socialHub.recommendationActive', '最近很活跃');
  }

  function dismissRecommendation(recommendation) {
    if (!recommendation || state.recommendationBusy.has(recommendation.id)) return;
    var index = state.recommendations.findIndex(function (item) {
      return item.id === recommendation.id;
    });
    var remaining = state.recommendations.filter(function (item) {
      return item.id !== recommendation.id;
    });
    var next = remaining[Math.min(Math.max(index, 0), remaining.length - 1)] || null;
    dismissedRecommendationFocusKey = next
      ? 'recommendation-dismiss-' + next.id
      : 'open-community';
    dismissedRecommendationIds.add(recommendation.id);
    state.recommendations = remaining;
    render();
  }

  async function followRecommendation(recommendation) {
    if (!recommendation || state.recommendationBusy.has(recommendation.id)) return;
    var followGeneration = openGeneration;
    var followAccountId = state.accountId;
    var followFocusKey = 'recommendation-' + recommendation.id;
    var activeAtFollow = document.activeElement;
    var restoreFollowFocus = !!(activeAtFollow && activeAtFollow.getAttribute &&
      activeAtFollow.getAttribute('data-neko-focus-key') === followFocusKey);
    function isCurrentFollow() {
      return followGeneration === openGeneration && followAccountId === state.accountId;
    }
    state.recommendationBusy.add(recommendation.id);
    state.toast = '';
    state.toastError = false;
    render();
    try {
      var response = await fetch('/api/community/users/' + encodeURIComponent(recommendation.id) +
        '/follow?expected_account_id=' + encodeURIComponent(followAccountId), {
        method: 'POST', credentials: 'same-origin'
      });
      var body = await json(response);
      if (!response.ok) throw new Error(detailText(body, tr('visit.socialHub.followFailed', '关注失败，请稍后重试')));
      if (!isCurrentFollow()) return;
      var index = state.recommendations.findIndex(function (item) {
        return item.id === recommendation.id;
      });
      var remaining = state.recommendations.filter(function (item) {
        return item.id !== recommendation.id;
      });
      var next = remaining[Math.min(Math.max(index, 0), remaining.length - 1)] || null;
      if (restoreFollowFocus) {
        pendingFocusOriginKey = followFocusKey;
        pendingFocusKey = next ? 'recommendation-' + next.id : 'open-community';
      }
      state.recommendations = remaining;
      await refresh(true, true);
      if (!isCurrentFollow()) return;
      state.toast = tr('visit.socialHub.followedUser', '已关注 {{name}}', { name: recommendation.displayName });
    } catch (error) {
      if (!isCurrentFollow()) return;
      state.toast = error && error.message || tr('visit.socialHub.followFailed', '关注失败，请稍后重试');
      state.toastError = true;
    } finally {
      if (isCurrentFollow()) {
        state.recommendationBusy.delete(recommendation.id);
        render();
      }
    }
  }

  function recommendationRow(recommendation) {
    var row = el('li', 'neko-social-hub-friend neko-social-hub-recommendation');
    row.appendChild(avatar(recommendation, ''));
    var copy = el('div', 'neko-social-hub-friend-copy');
    copy.appendChild(el('strong', 'neko-social-hub-name', recommendation.displayName));
    copy.appendChild(el('span', 'neko-social-hub-status', recommendationReason(recommendation)));
    row.appendChild(copy);
    var busy = state.recommendationBusy.has(recommendation.id);
    var action = button(
      busy ? tr('visit.socialHub.followingUser', '关注中…') : tr('visit.socialHub.followUser', '关注'),
      'neko-social-hub-button primary',
      function () { followRecommendation(recommendation); }
    );
    focusKey(action, 'recommendation-' + recommendation.id);
    action.disabled = busy;
    var actions = el('div', 'neko-social-hub-recommendation-actions');
    actions.appendChild(action);
    var dismissLabel = tr('visit.socialHub.dismissRecommendation', '不再推荐 {{name}}', {
      name: recommendation.displayName
    });
    var dismiss = button('×', 'neko-social-hub-recommendation-dismiss', function () {
      dismissRecommendation(recommendation);
    });
    dismiss.setAttribute('aria-label', dismissLabel);
    dismiss.title = dismissLabel;
    focusKey(dismiss, 'recommendation-dismiss-' + recommendation.id);
    dismiss.disabled = busy;
    actions.appendChild(dismiss);
    row.appendChild(actions);
    return row;
  }

  function renderRecommendations(target) {
    if (!state.recommendations.length) return;
    var section = el('section', 'neko-social-hub-recommendation-section');
    section.appendChild(el('h3', 'neko-social-hub-heading neko-social-hub-recommendation-heading',
      tr('visit.socialHub.recommendedUsers', '你可能感兴趣的人')));
    var list = el('ul', 'neko-social-hub-friends neko-social-hub-recommendations');
    state.recommendations.forEach(function (recommendation) {
      list.appendChild(recommendationRow(recommendation));
    });
    section.appendChild(list);
    target.appendChild(section);
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
      var action = button(view ? view.action : (busyElsewhere ? tr('visit.socialHub.inviteLater', '稍后再邀') : tr('visit.socialHub.invite', '邀请来玩')),
        'neko-social-hub-button' + (!view && !busyElsewhere ? ' primary' : ''), function () {
        if (view && view.canCancel) cancelInvitation(friend.id, invitation);
        else inviteFriend(friend.id);
      });
      focusKey(action, 'friend-' + friend.id);
      action.disabled = (!!view && !view.canCancel) || busyElsewhere;
      row.appendChild(action);
    }
    return row;
  }
  function orphanInvitationFriends() {
    var friendIds = new Set(state.friends.map(function (friend) { return friend.id; }));
    var orphans = [];
    state.invitations.forEach(function (invitation, friendId) {
      if (friendIds.has(friendId) || !invitation ||
          (invitation.phase !== 'invite_ready' && invitation.phase !== 'cancelling')) return;
      orphans.push({
        id: friendId,
        displayName: String(invitation.displayName || tr('visit.socialHub.otherParty', '对方')),
        avatarUrl: String(invitation.avatarUrl || ''),
        online: false,
        presenceStatus: 'offline',
        lastSeenAt: ''
      });
    });
    return orphans;
  }
  function relativeLastSeen(value) {
    var timestamp = Date.parse(String(value || ''));
    if (!Number.isFinite(timestamp)) return '';
    var elapsed = Math.max(0, Date.now() - timestamp);
    var minutes = Math.floor(elapsed / 60000);
    var locale = document.documentElement.lang || undefined;
    var formatter;
    try { formatter = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' }); } catch (_) {}
    if (minutes < 1) return tr('visit.socialHub.justNow', '刚刚');
    if (minutes < 60) return formatter ? formatter.format(-minutes, 'minute') : minutes + ' 分钟前';
    var hours = Math.floor(minutes / 60);
    if (hours < 24) return formatter ? formatter.format(-hours, 'hour') : hours + ' 小时前';
    var days = Math.floor(hours / 24);
    if (days < 30) return formatter ? formatter.format(-days, 'day') : days + ' 天前';
    var months = Math.floor(days / 30);
    if (months < 12) return formatter ? formatter.format(-months, 'month') : months + ' 个月前';
    var years = Math.floor(months / 12);
    return formatter ? formatter.format(-years, 'year') : years + ' 年前';
  }
  function friendStatus(friend) {
    if (friend.presenceStatus === 'away') return tr('visit.socialHub.presenceAway', '离开');
    if (friend.online) return tr('visit.socialHub.presenceOnline', '在线');
    var lastSeen = relativeLastSeen(friend.lastSeenAt);
    return lastSeen ? tr('visit.socialHub.lastSeen', '上次在线 {{time}}', { time: lastSeen }) : tr('visit.socialHub.offline', '离线');
  }
  function applyPresenceChanged(notification) {
    var payload = notification && notification.payload;
    var userId = String(payload && payload.user_id || '');
    var status = String(payload && payload.presence_status || '');
    if (!userId || ['online', 'away', 'offline'].indexOf(status) === -1) return false;
    var friend = state.friends.find(function (item) { return item.id === userId; });
    if (!friend) return false;
    presencePushRevisions.set(userId, ++presencePushRevision);
    friend.presenceStatus = status;
    friend.online = status === 'online' || status === 'away';
    friend.lastSeenAt = String(payload.last_seen_at || friend.lastSeenAt || '');
    if (state.open) render();
    return true;
  }
  async function refreshPresence() {
    if (!state.open || !state.loggedIn || presenceRefreshInFlight) return false;
    presenceRefreshInFlight = true;
    var requestAccountId = state.accountId;
    var requestGeneration = openGeneration;
    var requestRevision = presencePushRevision;
    var controller = typeof AbortController === 'function' ? new AbortController() : null;
    var abortTimer = controller ? setTimeout(function () { controller.abort(); }, 8000) : 0;
    try {
      var response = await fetch('/api/community/friend-presence', {
        credentials: 'same-origin', signal: controller ? controller.signal : undefined
      });
      var body = await json(response);
      if (!response.ok || !Array.isArray(body.friends)) return false;
      if (openGeneration !== requestGeneration || state.accountId !== requestAccountId) return false;
      var updates = new Map();
      body.friends.forEach(function (item) {
        var userId = String(item && item.id || '');
        var status = String(item && item.presence_status || '');
        if (userId && ['online', 'away', 'offline'].indexOf(status) !== -1) {
          updates.set(userId, item);
        }
      });
      var changed = false;
      state.friends.forEach(function (friend) {
        var item = updates.get(friend.id);
        if (!item) return;
        if ((presencePushRevisions.get(friend.id) || 0) > requestRevision) return;
        var status = String(item.presence_status);
        var online = status === 'online' || status === 'away';
        var lastSeenAt = String(item.last_seen_at || '');
        // 即使值未变化也记录这次确认，阻止更早的全量快照随后回写旧 presence。
        presencePushRevisions.set(friend.id, ++presencePushRevision);
        if (friend.presenceStatus === status && friend.online === online && friend.lastSeenAt === lastSeenAt) return;
        friend.presenceStatus = status;
        friend.online = online;
        friend.lastSeenAt = lastSeenAt;
        changed = true;
      });
      if (changed && state.open) render();
      return changed;
    } catch (_) {
      return false;
    } finally {
      if (abortTimer) clearTimeout(abortTimer);
      presenceRefreshInFlight = false;
    }
  }
  function renderFriendGroup(title, friends, target) {
    if (!friends.length) return;
    target.appendChild(el('h3', 'neko-social-hub-heading', title + ' (' + friends.length + ')'));
    var list = el('ul', 'neko-social-hub-friends');
    friends.forEach(function (friend) { list.appendChild(friendRow(friend)); });
    target.appendChild(list);
  }
  var EMPTY_ICONS = {
    login: ['M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8z', 'M4.5 20a7.5 7.5 0 0 1 15 0'],
    friends: ['M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z', 'M2.5 20a6.5 6.5 0 0 1 13 0',
      'M16 4.3a3.5 3.5 0 0 1 0 6.4', 'M18 14.2a6.5 6.5 0 0 1 3.5 5.8'],
    offline: ['M7 18h10.5a4 4 0 0 0 .6-7.96A6 6 0 0 0 6.6 9.1 4.5 4.5 0 0 0 7 18z', 'M12 9.5v4', 'M12 16.2v.1']
  };
  function renderEmpty(target) {
    var empty = el('section', 'neko-social-hub-empty');
    if (state.loading) {
      empty.classList.add('is-loading');
      empty.appendChild(el('span', 'neko-social-hub-spinner'));
      empty.appendChild(el('span', 'neko-social-hub-empty-hint', tr('visit.socialHub.loadingFriends', '正在读取互关好友…')));
      target.appendChild(empty);
      return;
    }
    var kind, title, hint, actionLabel, actionHandler, actionKey;
    if (state.loadFailed) {
      kind = 'offline';
      title = tr('visit.socialHub.loadFailedTitle', '好友列表加载失败');
      // 没有具体原因时，通用的“读取失败”会和标题重复，换成可操作的提示。
      hint = state.loadError && state.loadError !== tr('visit.socialHub.readFailed', '读取好友失败')
        ? state.loadError : tr('visit.socialHub.loadFailedHint', '检查一下网络，再试一次');
      actionLabel = tr('visit.socialHub.retry', '重试');
      actionHandler = function () {
        pendingFocusKey = 'empty-retry';
        refresh();
      };
      actionKey = 'empty-retry';
    } else if (!state.loggedIn) {
      kind = 'login';
      title = tr('visit.socialHub.notLoggedIn', '还没登录喵宇宙');
      hint = tr('visit.socialHub.loginHint', '登录后就能邀请好友来串门');
      actionLabel = tr('visit.socialHub.loginAction', '登录喵宇宙');
      actionHandler = openCommunity;
      actionKey = 'empty-login';
    } else {
      kind = 'friends';
      title = tr('visit.socialHub.noFriends', '还没有互关喵友');
      hint = tr('visit.socialHub.mutualHint', '互相关注后，就能邀请 TA 来串门');
      actionLabel = tr('visit.socialHub.findFriends', '去喵宇宙找喵友');
      actionHandler = openCommunity;
      actionKey = 'empty-find-friends';
    }
    if (kind !== 'offline' && !canOpenCommunity()) {
      // 打不开社区时不放无效按钮；未登录退回到“去 Neko 设置登录”的指引。
      if (kind === 'login') hint = tr('visit.socialHub.loginRequired', '请先在 N.E.K.O 设置里登录喵宇宙');
      actionLabel = '';
    }
    empty.classList.add('is-' + kind);
    var badge = el('span', 'neko-social-hub-empty-badge');
    badge.appendChild(svgIcon('neko-social-hub-empty-icon', EMPTY_ICONS[kind]));
    empty.appendChild(badge);
    empty.appendChild(el('strong', 'neko-social-hub-empty-title', title));
    empty.appendChild(el('span', 'neko-social-hub-empty-hint', hint));
    if (actionLabel) {
      empty.appendChild(focusKey(button(actionLabel, 'neko-social-hub-button primary neko-social-hub-empty-action', actionHandler), actionKey));
    }
    target.appendChild(empty);
  }
  function render() {
    if (!panel) return;
    var oldScroller = panel.querySelector('.neko-social-hub-friend-scroller');
    var oldScrollTop = oldScroller ? oldScroller.scrollTop : 0;
    var active = document.activeElement;
    var activeKey = hubContains(active) ? active.getAttribute('data-neko-focus-key') : '';
    if (dismissedRecommendationFocusKey) {
      activeKey = dismissedRecommendationFocusKey;
      dismissedRecommendationFocusKey = '';
    }
    // 用户已把焦点移到面板外的其他控件：放弃待恢复的焦点，不再抢回来。
    if (pendingFocusKey && active && active !== document.body && !hubContains(active)) {
      pendingFocusKey = '';
      pendingFocusOriginKey = '';
    }
    var pendingFocusFallback = !!(pendingFocusKey && !state.loading);
    if (pendingFocusFallback) {
      var focusStayedOnOrigin = !!(pendingFocusOriginKey && activeKey === pendingFocusOriginKey);
      var focusedNodeDisappeared = !active || active === document.body || active === panel;
      if (focusStayedOnOrigin || focusedNodeDisappeared) activeKey = pendingFocusKey;
      else if (hubContains(active)) {
        // 用户在补位期间主动选择了面板内的其他控件，保留其选择。
        pendingFocusKey = '';
        pendingFocusOriginKey = '';
        pendingFocusFallback = false;
      }
    }
    if (!state.loading) {
      pendingFocusKey = '';
      pendingFocusOriginKey = '';
    }
    panel.replaceChildren();
    var header = el('header', 'neko-social-hub-header');
    header.appendChild(image('/static/icons/neko_community_on.png', '', 'neko-social-hub-logo'));
    header.appendChild(el('h2', 'neko-social-hub-title', tr('buttons.social', '喵宇宙')));
    // 完整社区里实际是看卡、铸造和私信；直接写出来，比“打开完整社区”更好懂。
    var communityLink = button(tr('visit.socialHub.openCommunity', '卡牌 · 铸造 · 私信'), 'neko-social-hub-community-link', openCommunity);
    communityLink.title = tr('visit.socialHub.openCommunityHint', '打开完整社区：看卡、铸造（抽卡）、私信');
    communityLink.appendChild(svgIcon('neko-social-hub-community-arrow', ['M7 17 17 7', 'M8 7h9v9']));
    // 独立窗口没有宿主桥时打不开社区，不放这个点了没反应的入口。
    if (canOpenCommunity()) header.appendChild(focusKey(communityLink, 'open-community'));
    panel.appendChild(header);
    if (state.loggedIn && state.account) renderOwnAccount(panel);

    // 账号区和推荐区固定在顶部，只让好友分组独立滚动。
    // 这样滚动条不会穿过“你可能感兴趣的人”，也不会挡住关注按钮。
    renderRecommendations(panel);
    var friendScroller = el('div', 'neko-social-hub-friend-scroller');
    panel.appendChild(friendScroller);
    var orphanInvitations = orphanInvitationFriends();
    if (state.loading || state.loadFailed || !state.loggedIn || (!state.friends.length && !orphanInvitations.length)) renderEmpty(friendScroller);
    else {
      var onlineFriends = state.friends.filter(function (friend) { return friend.online; });
      var offlineFriends = state.friends.filter(function (friend) { return !friend.online; });
      renderFriendGroup(tr('visit.socialHub.waitingResponse', '等待回应'), orphanInvitations, friendScroller);
      renderFriendGroup(tr('visit.socialHub.onlineFriends', '在线好友'), onlineFriends, friendScroller);
      renderFriendGroup(tr('visit.socialHub.offlineFriends', '离线好友'), offlineFriends, friendScroller);
    }
    if (state.toast) panel.appendChild(el('div', 'neko-social-hub-toast' + (state.toastError ? ' is-error' : ''), state.toast));
    renderPresenceMenu();
    friendScroller.scrollTop = oldScrollTop;
    var restoredFocus = false;
    if (activeKey) {
      var focusNodes = Array.from(panel.querySelectorAll('[data-neko-focus-key]'));
      if (presenceMenu) focusNodes = focusNodes.concat(Array.from(presenceMenu.querySelectorAll('[data-neko-focus-key]')));
      restoredFocus = focusNodes.some(function (node) {
        if (node.getAttribute('data-neko-focus-key') !== activeKey) return false;
        try { node.focus({ preventScroll: true }); } catch (_) { node.focus(); }
        return true;
      });
    }
    if (!restoredFocus && pendingFocusFallback) {
      var communityEntry = panel.querySelector('[data-neko-focus-key="open-community"]');
      if (communityEntry) {
        try { communityEntry.focus({ preventScroll: true }); } catch (_) { communityEntry.focus(); }
      }
    }
    // 读取中按钮暂时不存在，先把焦点留在面板上，避免掉到 body。
    if (pendingFocusKey && state.loading && (!document.activeElement || document.activeElement === document.body)) {
      try { panel.focus({ preventScroll: true }); } catch (_) { panel.focus(); }
    }
    if (state.open) requestAnimationFrame(function () {
      if (!standalone) keepPanelInViewport();
      positionPresenceMenu();
    });
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

  function keepPanelInViewport() {
    if (standalone || !panel || !state.open) return;
    var currentTop = Number.parseFloat(panel.style.top);
    if (!Number.isFinite(currentTop)) return;
    var rect = panel.getBoundingClientRect();
    var viewport = panelViewport();
    var topEdge = viewport.top + 12;
    var bottomEdge = viewport.bottom - 12;
    var nextTop = currentTop;
    if (rect.bottom > bottomEdge) nextTop -= rect.bottom - bottomEdge;
    if (rect.top < topEdge) nextTop += topEdge - rect.top;
    if (Math.abs(nextTop - currentTop) >= 1) panel.style.top = Math.round(nextTop) + 'px';
  }

  function positionPanel() {
    if (!panel || !state.open) return;
    if (standalone) { positionPresenceMenu(); return; }
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

    var width = Math.min(panel.offsetWidth || 360, availableWidth);
    // 按面板外框的实际高度定位，内部滚动内容不参与外框位置计算。
    var height = Math.min(panel.offsetHeight || panel.scrollHeight, maxHeight);
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
    positionPresenceMenu();
  }
  function mount() {
    if (panel) return;
    panel = el('section', 'neko-social-hub-panel');
    panel.tabIndex = -1;
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', '喵宇宙串门');
    panel.addEventListener('animationend', positionPresenceMenu);
    if (standalone) (document.getElementById('neko-social-hub-root') || document.body).appendChild(panel);
    else {
      layer = el('div', 'neko-social-hub-layer');
      layer.hidden = true;
      layer.appendChild(panel);
      document.body.appendChild(layer);
    }
  }
  function revealOpenPanel(generation) {
    if (!state.open || generation !== openGeneration) return;
    if (layer) layer.hidden = false;
    notifyOverlayVisibilityChanged();
    render();
    requestAnimationFrame(function () {
      if (!state.open || generation !== openGeneration) return;
      positionPanel();
      panel.focus({ preventScroll: true });
    });
  }
  function open() {
    if (state.open && panel && !standalone) return true;
    mount();
    closeAvatarPopups();
    previousFocus = document.activeElement;
    var generation = ++openGeneration;
    state.open = true;
    dismissedRecommendationIds.clear();
    if (!layer) {
      revealOpenPanel(generation);
      refresh();
      return true;
    }
    // 快速请求一次性展示完整内容；慢请求超过短暂防闪窗口后才显示 loading。
    // 这样不会先闪出上次的推荐，也不会在上游变慢时长时间毫无反馈。
    layer.hidden = true;
    state.recommendations = [];
    state.recommendationBusy.clear();
    var loadingRevealTimer = setTimeout(function () {
      revealOpenPanel(generation);
    }, 300);
    refresh().finally(function () {
      clearTimeout(loadingRevealTimer);
      // 慢请求的 loading 已经展示并聚焦过；完成后只由 refresh() 更新内容。
      if (layer.hidden) revealOpenPanel(generation);
    });
    return true;
  }
  function close() {
    closePresenceMenu(false);
    if (standalone) {
      var bridge = host();
      return bridge && typeof bridge.close === 'function' ? bridge.close() : window.close();
    }
    if (!layer) return;
    openGeneration += 1;
    state.open = false;
    layer.hidden = true;
    notifyOverlayVisibilityChanged();
    if (previousFocus && typeof previousFocus.focus === 'function') previousFocus.focus();
  }
  function toggle() { return state.open ? (close(), true) : open(); }

  document.addEventListener('keydown', function (event) {
    if (state.open && state.presenceMenuOpen) {
      if (event.key === 'Escape' || event.key === 'Tab') {
        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); }
        closePresenceMenu(true);
        return;
      }
      if (presenceMenu && presenceMenu.contains(event.target)) {
        var options = Array.from(presenceMenu.querySelectorAll('[role="menuitemradio"]'));
        var index = options.indexOf(document.activeElement);
        var next = { ArrowDown: (index + 1) % options.length, ArrowUp: (index + options.length - 1) % options.length,
          Home: 0, End: options.length - 1 }[event.key];
        if (next !== undefined) { event.preventDefault(); focusPresenceOption(next); }
      }
    }
    if (state.open && event.key === 'Escape') { event.preventDefault(); close(); }
  });
  document.addEventListener('pointerdown', function (event) {
    if (!state.open || !panel) return;
    if (presenceMenu && presenceMenu.contains(event.target)) return;
    var picker = event.target && event.target.closest && event.target.closest('.neko-social-hub-presence-picker');
    if (state.presenceMenuOpen && !picker) closePresenceMenu(false);
    if (standalone) return;
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
        expiresAt: Number(detail.expiresAt || incoming.expiresAt) || 0,
        displayName: incoming.displayName,
        avatarUrl: incoming.avatarUrl
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
  // 推送负责即时变化；短轮询只在面板打开时兜底断线和 presence 租约自然过期。
  // 它只拉好友状态，不刷新推荐、通知或整个浮窗。
  setInterval(function () { refreshPresence(); }, 3000);
  // 面板关着时也维持社区 presence；静默刷新不会清空现有好友或制造 loading 闪烁。
  setInterval(function () { refresh(true); }, 60000);
  var socialApi = social();
  if (socialApi && typeof socialApi.onNotifyIncoming === 'function') {
    socialApi.onNotifyIncoming(function (notification) {
      var payload = notification && notification.payload;
      var kind = String(notification && notification.kind || payload && payload.type || '');
      if (kind === 'presence_changed') applyPresenceChanged(notification);
      else if (kind === 'visit_invite' || kind === 'visit_invite_response') refresh(true);
    });
  }

  window.nekoSocialHub = {
    open: open, close: close, toggle: toggle, handlePrimaryClick: toggle,
    openFullCommunity: openCommunity, refresh: refresh, _state: state,
    _applyVisitStatus: applyVisitStatus, _inviteFriend: inviteFriend,
    _orphanInvitationFriends: orphanInvitationFriends,
    _applyPresenceChanged: applyPresenceChanged,
    _refreshPresence: refreshPresence,
    _dismissRecommendation: dismissRecommendation,
    _followRecommendation: followRecommendation
  };
  if (standalone) requestAnimationFrame(open);
  else requestAnimationFrame(refresh);
})();
