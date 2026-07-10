/**
 * 成就管理系统
 * 统一管理所有成就的解锁逻辑
 */

(function() {
    'use strict';

    // 成就定义配置
    const ACHIEVEMENTS = {
        // 1. 初次邂逅
        ACH_FIRST_DIALOGUE: {
            name: 'ACH_FIRST_DIALOGUE',
            description: '初次邂逅',
            checkOnce: true
        },

        // 2-4. 计时成就：由 Steam Progress Stat PLAY_TIME_SECONDS 绑定阈值自动解锁
        // Steamworks 后台需将 ACH_TIME_* 绑定到该 Stat（阈值分别为 300 / 3600 / 360000 秒）
        ACH_TIME_5MIN: {
            name: 'ACH_TIME_5MIN',
            description: '茶歇时刻',
            steamProgressStat: 'PLAY_TIME_SECONDS',
            threshold: 300  // 5分钟 = 300秒
        },

        ACH_TIME_1HR: {
            name: 'ACH_TIME_1HR',
            description: '渐入佳境',
            steamProgressStat: 'PLAY_TIME_SECONDS',
            threshold: 3600  // 1小时 = 3600秒
        },

        ACH_TIME_100HR: {
            name: 'ACH_TIME_100HR',
            description: '朝夕相伴',
            steamProgressStat: 'PLAY_TIME_SECONDS',
            threshold: 360000  // 100小时 = 360000秒
        },

        // 5. 焕然一新 - 换肤
        ACH_CHANGE_SKIN: {
            name: 'ACH_CHANGE_SKIN',
            description: '焕然一新',
            checkOnce: true
        },

        // 6. 来自异世界的礼物 - 使用创意工坊
        ACH_WORKSHOP_USE: {
            name: 'ACH_WORKSHOP_USE',
            description: '来自异世界的礼物',
            checkOnce: true
        },

        // 7. 与你分享的世界 - 发送图片
        ACH_SEND_IMAGE: {
            name: 'ACH_SEND_IMAGE',
            description: '与你分享的世界',
            checkOnce: true
        },

        // 8. 喵语十级 - 喵喵100次
        ACH_MEOW_100: {
            name: 'ACH_MEOW_100',
            description: '喵语十级',
            counter: 'meowCount',
            threshold: 50
        }
    };

    // 本地存储的计数器
    const STORAGE_KEY = 'neko_achievement_counters';
    const UNLOCKED_KEY = 'neko_unlocked_achievements';

    // Pet + React Chat 都会加载本脚本；时长 Progress Stat 只能由一个窗口上报。
    function shouldOwnPlaytimeTracking() {
        if (window.__NEKO_STANDALONE_CHAT__ === true) {
            return false;
        }
        const path = String(window.location.pathname || '');
        if (window.__NEKO_MULTI_WINDOW__ === true && /^\/chat(?:_full)?(?:\/|$)/.test(path)) {
            return false;
        }
        return true;
    }

    // 成就管理器类
    class AchievementManager {
        constructor() {
            this.counters = this.loadCounters();
            this.unlockedAchievements = this.loadUnlockedAchievements();
            this.sessionStartTime = Date.now();
            this.pendingAchievements = new Set(); // 防竞态：追踪正在解锁的成就

            // 仅主窗口/Pet 上报 Progress Stat，避免 Pet+Chat 双窗口把时长翻倍
            if (shouldOwnPlaytimeTracking()) {
                this.startPlayTimeTracking();
            }

        }

        // 加载计数器
        loadCounters() {
            try {
                const data = localStorage.getItem(STORAGE_KEY);
                if (!data) return {};
                const parsed = JSON.parse(data);
                return (parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed))
                    ? parsed
                    : {};
            } catch (e) {
                console.error('加载成就计数器失败:', e);
                return {};
            }
        }

        // 保存计数器
        saveCounters() {
            try {
                localStorage.setItem(STORAGE_KEY, JSON.stringify(this.counters));
            } catch (e) {
                console.error('保存成就计数器失败:', e);
            }
        }

        // 加载已解锁成就
        loadUnlockedAchievements() {
            try {
                const data = localStorage.getItem(UNLOCKED_KEY);
                if (!data) return [];
                const parsed = JSON.parse(data);
                return Array.isArray(parsed) ? parsed : [];
            } catch (e) {
                console.error('加载已解锁成就失败:', e);
                return [];
            }
        }

        // 保存已解锁成就
        saveUnlockedAchievements() {
            try {
                localStorage.setItem(UNLOCKED_KEY, JSON.stringify(this.unlockedAchievements));
            } catch (e) {
                console.error('保存已解锁成就失败:', e);
            }
        }

        // 检查成就是否已解锁
        isUnlocked(achievementName) {
            return this.unlockedAchievements.includes(achievementName);
        }

        markUnlockedLocally(achievementName) {
            if (this.isUnlocked(achievementName)) {
                return;
            }
            this.unlockedAchievements.push(achievementName);
            this.saveUnlockedAchievements();
        }

        // 解锁成就
        async unlockAchievement(achievementName) {
            // 检查成就是否存在
            if (!ACHIEVEMENTS[achievementName]) {
                console.warn(`成就不存在: ${achievementName}`);
                return false;
            }

            // 检查是否已解锁
            if (this.isUnlocked(achievementName)) {
                console.log(`成就已解锁: ${achievementName}`);
                return true;
            }

            // 检查是否正在解锁（防竞态）
            if (this.pendingAchievements.has(achievementName)) {
                console.log(`成就正在解锁中: ${achievementName}`);
                return false;
            }

            // 标记为正在解锁
            this.pendingAchievements.add(achievementName);

            try {
                console.log(`尝试解锁成就: ${achievementName} - ${ACHIEVEMENTS[achievementName].description}`);

                // 调用Steam API
                const achHeaders = { 'Content-Type': 'application/json' };
                const sec = window.nekoLocalMutationSecurity;
                if (sec && typeof sec.getMutationHeaders === 'function') {
                    try { Object.assign(achHeaders, await sec.getMutationHeaders()); } catch (_) { }
                }
                const response = await fetch(`/api/steam/set-achievement-status/${achievementName}`, {
                    method: 'POST',
                    headers: achHeaders
                });

                if (response.ok) {
                    const payload = await response.json().catch(() => ({}));
                    const alreadyUnlocked = payload && payload.alreadyUnlocked === true;
                    const newlyUnlocked = payload && payload.newlyUnlocked === true;
                    const processed = newlyUnlocked || alreadyUnlocked || (payload && payload.success === true);

                    if (!processed) {
                        console.error(`✗ 成就解锁响应异常: ${achievementName}`, payload);
                        return false;
                    }

                    // 无论 Steam 返回"刚解锁"还是"早已解锁"，本地都要补齐缓存；
                    // 但只有刚解锁才弹提示，避免清缓存/跨窗口时重复祝贺。
                    this.markUnlockedLocally(achievementName);

                    if (newlyUnlocked) {
                        console.log(`✓ 成就解锁成功: ${achievementName}`);
                        this.showAchievementNotification(ACHIEVEMENTS[achievementName]);
                    } else if (alreadyUnlocked) {
                        console.log(`成就已在 Steam 解锁，已同步本地缓存: ${achievementName}`);
                    } else {
                        console.log(`✓ 成就处理完成: ${achievementName}`);
                    }

                    return true;
                } else {
                    console.error(`✗ 成就解锁失败: ${achievementName}`);
                    return false;
                }
            } catch (error) {
                console.error(`成就解锁错误: ${achievementName}`, error);
                return false;
            } finally {
                // 移除 pending 标记
                this.pendingAchievements.delete(achievementName);
            }
        }

        // 显示成就通知
        showAchievementNotification(achievement) {
            // 如果有 showStatusToast 函数，使用它
            if (typeof window.showStatusToast === 'function') {
                window.showStatusToast(window.t ? window.t('achievement.unlocked', { description: achievement.description }) : `🏆 成就解锁: ${achievement.description}`, 3000);
            }

            // 触发自定义事件，允许其他模块监听
            window.dispatchEvent(new CustomEvent('achievement-unlocked', {
                detail: { achievement }
            }));
        }

        // 增加计数器
        incrementCounter(counterName, amount = 1) {
            const delta = Number(amount);
            if (!Number.isFinite(delta) || delta <= 0) {
                console.warn(`无效的成就计数增量: ${counterName} = ${amount}`);
                return;
            }
            // 如果计数器不存在，自动创建
            if (!Object.prototype.hasOwnProperty.call(this.counters, counterName)) {
                this.counters[counterName] = 0;
            }

            this.counters[counterName] += delta;
            this.saveCounters();

            // 检查相关成就
            this.checkCounterAchievements(counterName);
        }

        // 检查计数器相关成就
        async checkCounterAchievements(counterName) {
            const currentValue = this.counters[counterName];

            // 遍历所有成就，检查是否达到阈值
            for (const [key, achievement] of Object.entries(ACHIEVEMENTS)) {
                if (achievement.counter === counterName &&
                    achievement.threshold &&
                    currentValue >= achievement.threshold &&
                    !this.isUnlocked(key)) {
                    await this.unlockAchievement(key);
                }
            }
        }


        // 本地累加会话时长，定期 SetStat(PLAY_TIME_SECONDS)+StoreStats；
        // 计时成就由 Steam 按 Progress Stat 绑定阈值自动解锁，前端不主动 SetAchievement。
        startPlayTimeTracking() {
            // 约每 60 秒上报一次（官方常见节奏：定期/存档点/退出时同步）
            const REPORT_INTERVAL_MS = 60000;
            let prevTs = Date.now();
            let reporting = false;
            let activeController = null;
            let forceFlushInFlight = false;

            const flushPlayTime = async ({ force = false } = {}) => {
                // visibilitychange(hidden) + pagehide often fire together; only one
                // keepalive unload flush should run for the same elapsed window.
                if (force && forceFlushInFlight) return;

                // Unload flush must not be dropped by the in-flight sentinel:
                // abort the regular request (no keepalive) and re-send with keepalive.
                if (reporting) {
                    if (!force) return;
                    // A force flush is already in flight (no AbortController) — do not
                    // start a second keepalive with the same elapsedSeconds.
                    if (!activeController) return;
                    try { activeController.abort(); } catch (_) { }
                    activeController = null;
                }
                const now = Date.now();
                const elapsedSeconds = Math.min(
                    3600,
                    Math.max(0, Math.floor((now - prevTs) / 1000))
                );
                if (!force && elapsedSeconds < 1) {
                    return;
                }
                if (elapsedSeconds < 1) {
                    prevTs = now;
                    return;
                }

                reporting = true;
                if (force) forceFlushInFlight = true;
                const controller = force ? null : new AbortController();
                if (controller) activeController = controller;
                try {
                    const playtimeHeaders = { 'Content-Type': 'application/json' };
                    const sec = window.nekoLocalMutationSecurity;
                    if (sec && typeof sec.getMutationHeaders === 'function') {
                        try { Object.assign(playtimeHeaders, await sec.getMutationHeaders()); } catch (_) { }
                    }
                    const fetchOpts = {
                        method: 'POST',
                        headers: playtimeHeaders,
                        // pagehide / visibility hidden 时页面可能正在卸载，keepalive 保证请求能发完
                        keepalive: !!force,
                        body: JSON.stringify({ seconds: elapsedSeconds })
                    };
                    if (controller) fetchOpts.signal = controller.signal;
                    const response = await fetch('/api/steam/update-playtime', fetchOpts);

                    if (response.ok) {
                        prevTs = now;
                        const data = await response.json().catch(() => ({}));
                        this.syncProgressStatAchievements(data);
                    } else if (response.status === 503) {
                        // Steam 未初始化：丢弃这段间隔，避免离线堆积后一次灌入
                        console.debug('Steam 未初始化，跳过时长进度上报');
                        prevTs = now;
                    }
                    // 其他错误保留 prevTs，下次重试这段时间
                } catch (error) {
                    if (error && error.name === 'AbortError') {
                        // Superseded by a force/keepalive flush; keep prevTs for that retry.
                        return;
                    }
                    console.debug('更新游戏时长进度失败:', error.message);
                } finally {
                    if (activeController === controller) activeController = null;
                    if (force) forceFlushInFlight = false;
                    reporting = false;
                }
            };

            const scheduleNext = () => {
                setTimeout(async () => {
                    await flushPlayTime();
                    scheduleNext();
                }, REPORT_INTERVAL_MS);
            };

            // 页面隐藏/退出时尽量冲刷一次，贴近官方“退出时同步”
            const flushOnLeave = () => {
                void flushPlayTime({ force: true });
            };
            document.addEventListener('visibilitychange', () => {
                if (document.visibilityState === 'hidden') {
                    flushOnLeave();
                }
            });
            window.addEventListener('pagehide', flushOnLeave);

            // 启动后稍等再首次上报，避免刚进页就打空请求
            setTimeout(async () => {
                await flushPlayTime();
                scheduleNext();
            }, REPORT_INTERVAL_MS);
        }

        // 同步 Steam 已通过 Progress Stat 解锁的计时成就到本地缓存（不主动 SetAchievement）
        syncProgressStatAchievements(data) {
            if (!data || data.success !== true) return;
            const unlocked = Array.isArray(data.progressUnlocked) ? data.progressUnlocked : [];
            for (const achievementName of unlocked) {
                if (!ACHIEVEMENTS[achievementName]) continue;
                if (this.isUnlocked(achievementName)) continue;
                this.markUnlockedLocally(achievementName);
                // Steam 客户端会弹官方成就通知；这里只补本地 toast/缓存
                this.showAchievementNotification(ACHIEVEMENTS[achievementName]);
            }
        }

        // 获取当前统计数据
        getStats() {
            return {
                counters: { ...this.counters },
                unlockedCount: this.unlockedAchievements.length,
                totalCount: Object.keys(ACHIEVEMENTS).length,
                unlockedAchievements: [...this.unlockedAchievements]
            };
        }
    }

    function installAchievementManager(manager) {
        window.achievementManager = manager;

        // 导出便捷函数
        window.unlockAchievement = (name) => window.achievementManager.unlockAchievement(name);
        window.incrementAchievementCounter = (counter, amount) => window.achievementManager.incrementCounter(counter, amount);
        window.getAchievementStats = () => window.achievementManager.getStats();

        console.log('成就管理系统已初始化');
    }

    async function initAchievementManagerAfterStorageBarrier() {
        if (typeof window.waitForStorageLocationStartupBarrier === 'function') {
            try {
                await window.waitForStorageLocationStartupBarrier();
            } catch (error) {
                console.warn('[Achievement] storage startup barrier failed; achievement manager init deferred', error);
                return;
            }
        } else if (window.__nekoStorageLocationStartupBarrier
            && typeof window.__nekoStorageLocationStartupBarrier.then === 'function') {
            try {
                await window.__nekoStorageLocationStartupBarrier;
            } catch (error) {
                console.warn('[Achievement] storage startup barrier failed; achievement manager init deferred', error);
                return;
            }
        }

        installAchievementManager(new AchievementManager());
    }

    initAchievementManagerAfterStorageBarrier();
})();
