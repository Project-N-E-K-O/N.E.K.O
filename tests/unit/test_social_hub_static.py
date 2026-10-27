from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_social_hub_assets_are_loaded_before_primary_click_handler():
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    assert "/static/css/social-hub.css" in template
    assert "/static/social-hub.js" in template
    assert template.index("/static/social-hub.js") < template.index(
        "/static/app/app-ui/surface-floating-controls.js"
    )


def test_community_button_prefers_hub_and_keeps_full_community_bypass():
    source = (
        ROOT / "static" / "app" / "app-ui" / "surface-floating-controls.js"
    ).read_text(encoding="utf-8")
    assert "window.nekoSocialHub.handlePrimaryClick" in source
    assert "window.electronShell.openSocialHub" in source
    assert source.index("window.nekoSocialHub.handlePrimaryClick") < source.index(
        "window.electronShell.openSocialHub"
    )
    assert "socialButton.getBoundingClientRect()" in source
    assert "anchorRect: rect ?" in source
    assert "window.__nekoOpenFullCommunityOnce" in source


def test_hub_has_targeted_friend_flow_without_invite_code_entry():
    source = (ROOT / "static" / "social-hub.js").read_text(encoding="utf-8")
    styles = (ROOT / "static" / "css" / "social-hub.css").read_text(encoding="utf-8")
    assert "/api/community/visit-hub" in source
    assert "/api/community/visit-invitations" in source
    assert "bridge.createDirectInvite(friend.id)" in source
    assert "visit.createDirectInvitation(friend.id)" in source
    assert "visit_id: visitId" in source
    assert "invite_code: code" not in source
    assert "在线好友" in source
    assert "离线好友" in source
    assert "friend.online" in source
    assert "上次在线 " in source
    assert "if (view || friend.online)" in source
    assert "setInterval(function () { refresh(true); }, 60000)" in source
    assert "target_offline" in source
    assert "邀请来玩" in source
    assert "使用邀请码（备用）" not in source
    assert "renderFallback" not in source
    assert "neko-social-hub-fallback" not in styles
    assert "async function joinWithCode(rawCode)" in source
    assert "visit.joinWithCode" in source
    assert "visit.presentDirectInvitation" in source
    assert "prototype-" not in source
    assert "小雨" not in source
    assert "neko-social-hub-close" not in source
    assert "panel.contains(event.target)" in source
    assert "event.target.closest('[id$=\"-btn-social\"]')" in source
    assert "manager.closeAllPopups()" in source
    assert "'neko-avatar-popup-opening'" in source
    assert "'neko-avatar-overlay-visibility-changed'" in source
    assert "function panelViewport()" in source
    assert "screenRef.availHeight" in source
    assert "window.visualViewport" in source
    assert "top = clamp(rect.top, topEdge, bottomEdge - height)" in source
    assert "panel.style.maxHeight = Math.max(1, Math.floor(maxHeight)) + 'px'" in source


def test_incoming_friend_invitation_is_asked_by_the_current_catgirl_in_chat():
    hub = (ROOT / "static" / "social-hub.js").read_text(encoding="utf-8")
    chat = (
        ROOT / "static" / "app" / "app-react-chat-window" / "visit-chat.js"
    ).read_text(encoding="utf-8")

    assert "deliverIncomingToChat" in hub
    assert "visit.presentDirectInvitation" in hub
    assert "renderIncoming" not in hub
    assert "acceptIncoming" not in hub
    assert "requestAnimationFrame(refresh)" in hub
    assert "'neko-social-incoming-resolved'" in hub
    assert "kind === 'visit_invite_response'" in hub
    assert "'neko-social-invite-response'" in hub

    assert "DIRECT_INVITE_ACTION = 'visit_direct_invite_decision'" in chat
    assert "/question?catgirl=" in chat
    assert "barAction('direct_accept', t('visit.directInvite.accept'" in chat
    assert "barAction('direct_decline', t('visit.directInvite.decline'" in chat
    assert "barAction('invite'," not in chat
    assert "barAction('go'," not in chat
    assert "barAction('cancel'," not in chat
    assert "barAction('copy'," not in chat
    assert "joinDirect(visitId, true)" in chat
    assert "presentDirectInvitation: presentDirectInvitation" in chat
    assert "pollDirectInvitations: pollDirectInvitations" in chat
    assert "action.action === DIRECT_INVITE_ACTION" in chat
    assert "/api/community/visit-invitations/" in chat
    assert "setInterval(pollDirectInvitations, 5000)" in chat
    assert "socialNotifications.onNotifyIncoming" in chat

    direct_action = chat[chat.index("async function decideDirectInvitation") : chat.index(
        "async function onDirectInviteAction"
    )]
    assert "/respond'" in chat
    assert "reportDirectInviteDecision(payload, false)" in direct_action
    assert "clearPendingDirectInvitation(payload, false)" in direct_action
    legacy_action = chat[chat.index("async function onDirectInviteAction") : chat.index(
        "async function presentDirectInvitation"
    )]
    assert "pendingDirectInvitation" in legacy_action
    present_direct = chat[chat.index("async function presentDirectInvitation") : chat.index(
        "var directInvitationPollInFlight"
    )]
    assert "refreshCompactGeometry();" in present_direct
    assert "type: 'buttons'" not in present_direct
    assert "'/deliver-question'" in present_direct
    assert "host.appendMessage({" not in present_direct
    assert "host.updateMessage(" not in present_direct
    assert "host.openWindow()" not in present_direct
    assert "messageId: messageId, payload: payload, busy: false, questionDelivered: false" in present_direct
    assert "catgirl: catgirl" in present_direct
    assert "currentCatgirl() !== catgirl" in present_direct
    assert present_direct.index("/question?catgirl=") < present_direct.rindex("/deliver-question")
    assert "directInvitationExpired(payload)" in direct_action
    assert "result.status === 404 || result.status === 410" in direct_action
    assert "try {\n            result = await joinDirect(visitId, true);" in direct_action
    assert "pending.busy = false" in direct_action
    assert "resolvedDirectInvitationIds.has(notificationId)" in present_direct
    assert "!releaseEnabled || !userSwitchOn()" in present_direct
    assert "if (inVisit())" in present_direct
    assert "if (visit.visitId === visitId)" in present_direct
    poll_direct = chat[chat.index("async function pollDirectInvitations") : chat.index(
        "async function startJoin"
    )]
    assert "result.body.notifications_ok === true" in poll_direct
    assert "incoming_invite_ids" in poll_direct
    assert "return await presentDirectInvitation(pendingPayload);" in poll_direct
    assert "markDirectInviteRead" not in poll_direct


def test_friend_invitation_status_follows_the_real_visit_lifecycle():
    hub = (ROOT / "static" / "social-hub.js").read_text(encoding="utf-8")
    chat = (
        ROOT / "static" / "app" / "app-react-chat-window" / "visit-chat.js"
    ).read_text(encoding="utf-8")

    assert "invitations: new Map()" in hub
    assert "'neko-visit-status-change'" in hub
    assert "detail.action === 'ended'" in hub
    assert "state.invitations.delete(friendId)" in hub
    assert "invitationExpired(invitation)" in hub
    for copy in ("正在准备邀请", "等待回应", "正在连接", "等待接待", "正在串门", "正在道别", "正在收尾"):
        assert copy in hub

    assert "visit_id: visit.visitId || null" in chat
    assert "dispatchVisitStatus(frame);" in chat
    assert "getState: publicVisitState" in chat
    assert "new CustomEvent('neko-visit-status-change'" in chat


def test_invite_code_join_is_a_public_visit_chat_capability():
    source = (
        ROOT / "static" / "app" / "app-react-chat-window" / "visit-chat.js"
    ).read_text(encoding="utf-8")
    export_start = source.index("window.nekoVisitChat = {")
    public_exports = source[export_start : export_start + 700]
    assert "joinWithCode: joinWithCode" in public_exports
    assert "createInviteCode: createInviteCode" in public_exports
    assert "joinDirect: joinDirect" in public_exports
    assert "presentDirectInvitation: presentDirectInvitation" in public_exports
    assert "createDirectInvitation: createDirectInvitation" in public_exports


def test_standalone_hub_page_uses_the_same_production_assets():
    page = (ROOT / "templates" / "social_hub.html").read_text(encoding="utf-8")
    assert "__NEKO_SOCIAL_HUB_STANDALONE__" in page
    assert "/static/css/social-hub.css" in page
    assert "/static/social-hub.js" in page


def test_hub_styles_include_dark_responsive_and_reduced_motion_states():
    source = (ROOT / "static" / "css" / "social-hub.css").read_text(encoding="utf-8")
    assert '[data-theme="dark"] .neko-social-hub-panel' in source
    assert "@media (max-width: 520px)" in source
    assert "@media (prefers-reduced-motion: reduce)" in source


def test_social_hub_uses_community_hue_avatars_without_cat_image_fallback():
    source = (ROOT / "static" / "social-hub.js").read_text(encoding="utf-8")
    styles = (ROOT / "static" / "css" / "social-hub.css").read_text(encoding="utf-8")

    assert "function hashHue(seed)" in source
    assert "function avatarVisual(value, displayName, seed)" in source
    assert "var hueMatch = /^hue:" in source
    assert "avatarUrl: String(item && item.avatar_url || '')" in source
    assert "avatarUrl: String(item.actor_avatar_url || '')" in source
    assert ".neko-social-hub-avatar.is-hue" in styles


def test_social_hub_renders_own_community_identity_and_presence_picker():
    source = (ROOT / "static" / "social-hub.js").read_text(encoding="utf-8")
    styles = (ROOT / "static" / "css" / "social-hub.css").read_text(encoding="utf-8")

    assert "state.account = normalizeAccount(body.account)" in source
    assert "function renderOwnAccount(target)" in source
    assert "function setOwnPresence(selected)" in source
    assert "fetch('/api/community/presence'" in source
    assert "['online', 'away', 'invisible']" in source
    assert "visit.socialHub.presenceOnlineHint" in source
    assert "visit.socialHub.presenceAwayHint" in source
    assert "visit.socialHub.presenceInvisibleHint" in source
    assert ".neko-social-hub-self" in styles
    assert ".neko-social-hub-presence-menu" in styles
    assert "neko-social-hub-self-title-row" in source
    assert "titleRow.appendChild(toggle)" in source
    assert "document.createElementNS('http://www.w3.org/2000/svg', 'svg')" in source
    assert "arrow.setAttribute('aria-hidden', 'true')" in source
    assert "arrowPath.setAttribute('d', 'M3 6 L8 11 L13 6')" in source
    assert ".neko-social-hub-self-title-row" in styles
    assert ".neko-social-hub-presence-arrow path" in styles
    assert ".neko-social-hub-presence-toggle" in styles
    assert "transform: translateY(-4px)" in styles
    assert ".neko-social-hub-presence-picker.is-open .neko-social-hub-presence-arrow" not in styles
    assert ".neko-social-hub-status-dot.away" in styles
    assert ".neko-social-hub-status-dot.invisible" in styles


def test_only_friend_area_scrolls_while_current_account_stays_fixed():
    source = (ROOT / "static" / "social-hub.js").read_text(encoding="utf-8")
    styles = (ROOT / "static" / "css" / "social-hub.css").read_text(encoding="utf-8")

    assert "neko-social-hub-friend-scroller" in source
    assert "renderOwnAccount(panel)" in source
    assert "renderFriendGroup(tr('visit.socialHub.onlineFriends'" in source
    assert "renderFriendGroup(tr('visit.socialHub.offlineFriends'" in source
    assert ".neko-social-hub-panel" in styles
    assert "overflow: hidden" in styles
    assert ".neko-social-hub-friend-scroller" in styles
    assert "overflow-y: auto" in styles


def test_hub_preserves_friend_scroll_and_focus_across_renders():
    source = (ROOT / "static" / "social-hub.js").read_text(encoding="utf-8")

    assert "var oldScrollTop = oldScroller ? oldScroller.scrollTop : 0" in source
    assert "friendScroller.scrollTop = oldScrollTop" in source
    assert "data-neko-focus-key" in source
    assert "node.focus({ preventScroll: true })" in source
    assert "if (invitation.phase !== phase)" in source


def test_waiting_direct_invitation_can_be_cancelled():
    hub = (ROOT / "static" / "social-hub.js").read_text(encoding="utf-8")
    chat = (
        ROOT / "static" / "app" / "app-react-chat-window" / "visit-chat.js"
    ).read_text(encoding="utf-8")

    assert "canCancel: true" in hub
    assert "async function cancelInvitation(friendId, invitation)" in hub
    assert "bridge.cancelDirectInvite(visitId)" in hub
    assert "visit.cancelDirectInvitation(visitId)" in hub
    assert "async function cancelDirectInvitation(visitId)" in chat
    assert "cancelDirectInvitation: cancelDirectInvitation" in chat
    assert "await cancelDirectVisit(createdVisitId)" in hub
    assert "!restoredVisitId" in hub
    assert "function orphanInvitationFriends()" in hub
    assert "renderFriendGroup(tr('visit.socialHub.waitingResponse'" in hub


def test_friend_area_has_no_group_or_row_dividers():
    styles = (ROOT / "static" / "css" / "social-hub.css").read_text(encoding="utf-8")

    assert ".neko-social-hub-heading::after" not in styles
    assert ".neko-social-hub-friend:last-child" not in styles


def test_open_hub_keeps_every_avatar_toolbar_visible():
    source = (ROOT / "static" / "avatar" / "avatar-popup-common.js").read_text(
        encoding="utf-8"
    )
    assert "function hasVisibleSocialHub()" in source
    assert ".neko-social-hub-layer:not([hidden])" in source
    assert "hasVisibleSidePanel(ownerPrefix) || hasVisibleSocialHub()" in source
    assert "hasVisibleSocialHub," in source


def test_local_visual_preview_uses_the_production_hub_assets():
    preview = (ROOT / "static" / "social-hub-preview.html").read_text(encoding="utf-8")
    assert "/static/css/social-hub.css" in preview
    assert "/static/avatar/avatar-popup-common.js" in preview
    assert "/static/social-hub.js" in preview
    assert 'id="preview-btn-social"' in preview
    assert "window.nekoSocialHub.toggle()" in preview
