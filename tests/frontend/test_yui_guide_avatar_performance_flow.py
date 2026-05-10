from playwright.sync_api import Page


def test_home_yui_guide_full_flow_uses_avatar_performance_adapter(
    mock_page: Page,
    running_server: str,
):
    page = mock_page
    page.goto(f"{running_server}/", wait_until="domcontentloaded")
    page.wait_for_function(
        """
        () => window.createYuiGuideDirector
            && window.YuiGuideAvatarStage
            && window.getYuiGuideStepsRegistry
        """,
        timeout=10000,
    )

    result = page.evaluate(
        """
        async () => {
            const calls = [];
            const fakeTarget = {
                getBoundingClientRect: () => ({
                    left: 120,
                    top: 80,
                    width: 64,
                    height: 48,
                    right: 184,
                    bottom: 128
                })
            };

            window.YuiGuideAvatarStage.create = () => ({
                isAvailable: () => true,
                runWakeup: async () => {
                    calls.push({ method: 'runWakeup' });
                    return { handled: true, result: { result: 'played', reason: '' } };
                },
                enterStep: async (stepId, context) => {
                    calls.push({ method: 'enterStep', stepId, source: context && context.source });
                    return true;
                },
                applyEmotion: async (emotion, context) => {
                    calls.push({ method: 'applyEmotion', emotion, stepId: context && context.stepId });
                    return true;
                },
                onSpeechStart: async (stepId, context) => {
                    calls.push({ method: 'onSpeechStart', stepId, speechId: context && context.speechId });
                    return true;
                },
                onSpeechEnd: async (stepId, context) => {
                    calls.push({ method: 'onSpeechEnd', stepId, speechId: context && context.speechId });
                    return true;
                },
                onTimelineAction: async (stepId, action, context) => {
                    calls.push({
                        method: 'onTimelineAction',
                        stepId,
                        action,
                        hasTarget: !!(context && context.targets && context.targets.spotlight)
                    });
                    return true;
                },
                exitStep: async (stepId) => {
                    calls.push({ method: 'exitStep', stepId });
                    return true;
                },
                destroy: () => {
                    calls.push({ method: 'destroy' });
                }
            });

            const director = window.createYuiGuideDirector({
                page: 'home',
                registry: window.getYuiGuideStepsRegistry(),
                tutorialManager: {
                    _tutorialModelPrefix: 'live2d',
                    constructor: {
                        detectModelPrefix: () => 'live2d'
                    }
                },
                homeInteractionApi: {}
            });

            director.overlay = {
                hideBubble: () => {},
                hidePluginPreview: () => {},
                clearActionSpotlight: () => {},
                clearPersistentSpotlight: () => {},
                setTakingOver: () => {}
            };
            director.cursor = {
                hasPosition: () => true,
                wobble: () => {},
                hide: () => {}
            };
            director.isHomeChatExternalized = () => true;
            director.playIntroGreetingReply = async () => true;
            director.setExternalizedChatSpotlight = () => {};
            director.enableInterrupts = () => {};
            director.disableInterrupts = () => {};
            director.highlightChatWindow = () => {};
            director.appendGuideChatMessage = () => {};
            director.playRemainingIntroPreludeScenes = async () => true;
            director.waitForSceneDelay = async () => true;
            director.closeManagedPanels = async () => true;
            director.requestTermination = (reason, rawReason) => {
                calls.push({ method: 'requestTermination', reason, rawReason });
            };

            director.speakGuideLine = async function (_text, options) {
                const stepId = this.currentSceneId || 'intro_basic';
                const step = this.getStep(stepId);
                const context = this.createAvatarStageContext(
                    stepId,
                    step,
                    step && step.performance,
                    { source: 'speech-smoke' }
                );
                context.speechId = 'speech:' + stepId + ':' + (options && options.voiceKey || '');
                await this.callAvatarStage('onSpeechStart', stepId, context);
                await this.callAvatarStage('onSpeechEnd', stepId, context);
                return true;
            };

            director.runIntroVoiceControlButtonShowcase = async function (voiceKey) {
                await this.callAvatarTimelineAction('intro_basic', 'highlightVoiceControl', fakeTarget, {
                    voiceKey: voiceKey || ''
                });
                return true;
            };

            director.playScene = async function (stepId) {
                const step = this.getStep(stepId);
                const performance = step && step.performance ? step.performance : {};
                if (performance.emotion) {
                    this.applyGuideEmotion(performance.emotion);
                }
                await this.speakGuideLine(this.resolvePerformanceBubbleText(performance), {
                    voiceKey: performance.voiceKey || ''
                });
                const timeline = Array.isArray(performance.timeline) ? performance.timeline : [];
                for (const cue of timeline) {
                    await this.callAvatarTimelineAction(
                        stepId,
                        cue.action,
                        cue.action === 'returnControl' ? null : fakeTarget,
                        { voiceKey: cue.voiceKey || performance.voiceKey || '' }
                    );
                }
            };

            await director.runWakeupPrelude();
            await director.runChatIntroPrelude();

            return {
                oldWakeupGlobal: typeof window.YuiGuideWakeup,
                calls,
                timelineActions: calls
                    .filter((call) => call.method === 'onTimelineAction')
                    .map((call) => call.action),
                enteredSteps: calls
                    .filter((call) => call.method === 'enterStep')
                    .map((call) => call.stepId),
                exitedSteps: calls
                    .filter((call) => call.method === 'exitStep')
                    .map((call) => call.stepId),
                emotions: calls
                    .filter((call) => call.method === 'applyEmotion')
                    .map((call) => call.emotion)
            };
        }
        """
    )

    assert result["oldWakeupGlobal"] == "undefined"
    assert result["calls"][0]["method"] == "runWakeup"
    assert result["enteredSteps"] == [
        "intro_basic",
        "takeover_capture_cursor",
        "takeover_plugin_preview",
        "takeover_settings_peek",
        "takeover_return_control",
    ]
    assert result["exitedSteps"] == [
        "intro_basic",
        "takeover_capture_cursor",
        "takeover_plugin_preview",
        "takeover_settings_peek",
        "takeover_return_control",
    ]
    assert result["timelineActions"] == [
        "highlightVoiceControl",
        "highlightCatPaw",
        "enableAgentMaster",
        "enableKeyboardControl",
        "enableUserPlugin",
        "openManagementPanel",
        "handoffPluginDashboard",
        "openSettingsPanel",
        "showSecondLine",
        "returnControl",
    ]
    assert result["emotions"] == ["happy", "happy", "happy", "surprised", "happy"]
    assert result["calls"][-1] == {
        "method": "requestTermination",
        "reason": "complete",
        "rawReason": "complete",
    }


def test_home_yui_guide_normal_intro_shows_after_wakeup_takeover(
    mock_page: Page,
    running_server: str,
):
    page = mock_page
    page.goto(f"{running_server}/", wait_until="domcontentloaded")
    page.wait_for_function(
        """
        () => window.universalTutorialManager
            && window.YuiGuideAvatarStage
            && window.createYuiGuideDirector
        """,
        timeout=10000,
    )

    result = page.evaluate(
        """
        async () => {
            const manager = window.universalTutorialManager;
            const calls = [];
            manager.beginTutorialAvatarOverride = () => Promise.resolve();
            manager._tutorialModelPrefix = 'live2d';

            window.YuiGuideAvatarStage.create = () => ({
                isAvailable: () => true,
                runWakeup: async () => {
                    calls.push(['runWakeup']);
                    return { handled: true, result: { result: 'played', reason: '' } };
                },
                enterStep: async (stepId) => {
                    calls.push(['enterStep', stepId]);
                    return true;
                },
                applyEmotion: async (emotion) => {
                    calls.push(['applyEmotion', emotion]);
                    return true;
                },
                onSpeechStart: async (stepId) => {
                    calls.push(['onSpeechStart', stepId]);
                    return true;
                },
                onSpeechEnd: async (stepId) => {
                    calls.push(['onSpeechEnd', stepId]);
                    return true;
                },
                onTimelineAction: async (stepId, action) => {
                    calls.push(['onTimelineAction', stepId, action]);
                    return true;
                },
                exitStep: async (stepId) => {
                    calls.push(['exitStep', stepId]);
                    return true;
                },
                destroy: () => {
                    calls.push(['destroy']);
                }
            });

            manager.startTutorial();
            await new Promise((resolve, reject) => {
                const startedAt = Date.now();
                const check = () => {
                    const director = manager.yuiGuideDirector || null;
                    const bubble = document.querySelector('.yui-guide-bubble');
                    const spotlight = Array.from(document.querySelectorAll('.yui-guide-spotlight-frame'))
                        .some((element) => !element.hidden);
                    if (
                        director
                        && director.awaitingIntroActivation === true
                        && bubble
                        && bubble.hidden === false
                        && spotlight
                    ) {
                        resolve();
                        return;
                    }
                    if (Date.now() - startedAt > 5000) {
                        reject(new Error('normal_intro_activation_not_visible'));
                        return;
                    }
                    window.setTimeout(check, 50);
                };
                check();
            });

            const director = manager.yuiGuideDirector || null;
            const bubble = document.querySelector('.yui-guide-bubble');
            return {
                calls,
                currentSceneId: director && director.currentSceneId,
                awaitingIntroActivation: director && director.awaitingIntroActivation,
                bubbleHidden: bubble ? bubble.hidden : null,
                bubbleText: document.querySelector('.yui-guide-bubble-body')
                    ? document.querySelector('.yui-guide-bubble-body').textContent
                    : '',
                visibleSpotlights: Array.from(document.querySelectorAll('.yui-guide-spotlight-frame'))
                    .filter((element) => !element.hidden).length,
                oldWakeupGlobal: typeof window.YuiGuideWakeup
            };
        }
        """
    )

    assert result["oldWakeupGlobal"] == "undefined"
    assert result["calls"][0] == ["runWakeup"]
    assert ["enterStep", "intro_basic"] in result["calls"]
    assert result["currentSceneId"] == "intro_basic"
    assert result["awaitingIntroActivation"] is True
    assert result["bubbleHidden"] is False
    assert result["bubbleText"]
    assert result["visibleSpotlights"] >= 1


def test_home_yui_guide_normal_intro_shows_after_real_pose_wakeup(
    mock_page: Page,
    running_server: str,
):
    page = mock_page
    page.goto(f"{running_server}/", wait_until="domcontentloaded")
    page.wait_for_function(
        """
        () => window.universalTutorialManager
            && window.YuiGuideAvatarStage
            && window.AvatarPerformanceStage
            && window.createYuiGuideDirector
        """,
        timeout=10000,
    )

    result = page.evaluate(
        """
        async () => {
            const paramIds = [
                'ParamEyeLOpen',
                'ParamEyeROpen',
                'ParamAngleX',
                'ParamAngleY',
                'ParamAngleZ',
                'ParamEyeBallX',
                'ParamEyeBallY',
                'ParamEyeLSmile',
                'ParamEyeRSmile',
                'ParamBodyAngleX',
                'ParamBodyAngleY',
                'ParamBodyAngleZ',
                'Param75',
                'Param90',
                'Param92',
                'Param95'
            ];
            const values = {};
            paramIds.forEach((id) => {
                values[id] = id === 'ParamEyeLOpen' || id === 'ParamEyeROpen' ? 1 : 0;
            });
            const coreModel = {
                getParameterIndex: (id) => paramIds.indexOf(id),
                getParameterValueByIndex: (index) => values[paramIds[index]] || 0,
                getParameterMinimumValueByIndex: (index) => paramIds[index].indexOf('EyeBall') >= 0 ? -1 : -30,
                getParameterMaximumValueByIndex: (index) => (
                    paramIds[index] === 'ParamEyeLOpen'
                    || paramIds[index] === 'ParamEyeROpen'
                    || paramIds[index] === 'Param75'
                    || paramIds[index] === 'Param90'
                    || paramIds[index] === 'Param92'
                    || paramIds[index] === 'Param95'
                ) ? 1 : 30,
                getParameterDefaultValueByIndex: () => 0,
                setParameterValueByIndex: (index, value) => {
                    values[paramIds[index]] = value;
                }
            };
            const model = {
                destroyed: false,
                internalModel: {
                    coreModel
                }
            };
            const fakeManager = {
                currentModel: model,
                pixi_app: {
                    ticker: {}
                },
                _suspendEyeBlinkOverride: false,
                setTemporaryPoseOverride(source, apply) {
                    this.temporaryPose = { source, apply };
                    return true;
                },
                clearTemporaryPoseOverride(source) {
                    if (!this.temporaryPose || this.temporaryPose.source === source) {
                        this.temporaryPose = null;
                    }
                },
                setEmotion: async () => true,
                playMotion: async () => true,
                playExpression: async () => true
            };
            window.live2dManager = fakeManager;

            const manager = window.universalTutorialManager;
            manager.beginTutorialAvatarOverride = () => Promise.resolve();
            manager._tutorialModelPrefix = 'live2d';

            manager.startTutorial();
            await new Promise((resolve, reject) => {
                const startedAt = Date.now();
                const check = () => {
                    const director = manager.yuiGuideDirector || null;
                    const bubble = document.querySelector('.yui-guide-bubble');
                    const spotlight = Array.from(document.querySelectorAll('.yui-guide-spotlight-frame'))
                        .some((element) => !element.hidden);
                    if (
                        director
                        && director.awaitingIntroActivation === true
                        && bubble
                        && bubble.hidden === false
                        && spotlight
                    ) {
                        resolve();
                        return;
                    }
                    if (Date.now() - startedAt > 7000) {
                        reject(new Error('real_pose_wakeup_intro_not_visible'));
                        return;
                    }
                    window.setTimeout(check, 50);
                };
                check();
            });

            const director = manager.yuiGuideDirector || null;
            const bubble = document.querySelector('.yui-guide-bubble');
            return {
                currentSceneId: director && director.currentSceneId,
                awaitingIntroActivation: director && director.awaitingIntroActivation,
                bubbleHidden: bubble ? bubble.hidden : null,
                bubbleText: document.querySelector('.yui-guide-bubble-body')
                    ? document.querySelector('.yui-guide-bubble-body').textContent
                    : '',
                visibleSpotlights: Array.from(document.querySelectorAll('.yui-guide-spotlight-frame'))
                    .filter((element) => !element.hidden).length,
                temporaryPoseActive: !!fakeManager.temporaryPose,
                suspendEyeBlink: fakeManager._suspendEyeBlinkOverride,
                oldWakeupGlobal: typeof window.YuiGuideWakeup
            };
        }
        """
    )

    assert result["oldWakeupGlobal"] == "undefined"
    assert result["currentSceneId"] == "intro_basic"
    assert result["awaitingIntroActivation"] is True
    assert result["bubbleHidden"] is False
    assert result["bubbleText"]
    assert result["visibleSpotlights"] >= 1
    assert result["temporaryPoseActive"] is False
    assert result["suspendEyeBlink"] is False
