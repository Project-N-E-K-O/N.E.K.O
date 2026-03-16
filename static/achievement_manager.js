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

        // 2. 茶歇时刻 - 5分钟
        ACH_TIME_5MIN: {
            name: 'ACH_TIME_5MIN',
            description: '茶歇时刻',
            steamStat: 'PLAY_TIME_SECONDS',
            threshold: 300  // 5分钟 = 300秒
        },

        // 3. 渐入佳境 - 1小时
        ACH_TIME_1HR: {
            name: 'ACH_TIME_1HR',
            description: '渐入佳境',
            steamStat: 'PLAY_TIME_SECONDS',
            threshold: 3600  // 1小时 = 3600秒
        },

        // 4. 朝夕相伴 - 100小时
        ACH_TIME_100HR: {
            name: 'ACH_TIME_100HR',
            description: '朝夕相伴',
            steamStat: 'PLAY_TIME_SECONDS',
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

    // 成就管理器类
    class AchievementManager {
        constructor() {
            this.counters = this.loadCounters();
            this.unlockedAchievements = this.loadUnlockedAchievements();
            this.sessionStartTime = Date.now();
            this.pendingAchievements = new Set(); // 防竞态：追踪正在解锁的成就

            // 启动时长追踪（用于 Steam 统计）
            this.startPlayTimeTracking();

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
                const response = await fetch(`/api/steam/set-achievement-status/${achievementName}`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    }
                });

                if (response.ok) {
                    console.log(`✓ 成就解锁成功: ${achievementName}`);

                    // 记录到本地
                    this.unlockedAchievements.push(achievementName);
                    this.saveUnlockedAchievements();

                    // 显示通知（如果有通知系统）
                    this.showAchievementNotification(ACHIEVEMENTS[achievementName]);

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
                window.showStatusToast(`🏆 成就解锁: ${achievement.description}`, 3000);
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
            if (!this.counters.hasOwnProperty(counterName)) {
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


        // 启动游戏时长追踪（用于 Steam 统计 PLAY_TIME_SECONDS）
        startPlayTimeTracking() {
            // 使用递归 setTimeout 避免重叠调用
            let prevTs = Date.now(); // 记录上次更新的时间戳

            const updatePlayTime = async () => {
                const now = Date.now();
                // 计算实际经过的秒数（毫秒转秒，至少1秒）
                // 限制单次最多发送3600秒（1小时），防止累积过多
                const elapsedSeconds = Math.min(3600, Math.max(1, Math.floor((now - prevTs) / 1000)));

                try {
                    // 调用后端 API 更新 Steam 统计，发送实际经过的秒数
                    const response = await fetch('/api/steam/update-playtime', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({
                            seconds: elapsedSeconds
                        })
                    });

                    if (response.ok) {
                        const data = await response.json();
                        // 只有在成功更新后才更新时间戳，避免时间丢失
                        prevTs = now;
                        // 检查时间相关成就
                        await this.checkPlayTimeAchievements(data.totalPlayTime);
                    } else if (response.status === 503) {
                        // Steam 未初始化，静默失败（不显示错误）
                        console.debug('Steam 未初始化，跳过时长更新');
                        // Steam 未初始化时也更新时间戳，避免累积过多时间
                        prevTs = now;
                    }
                    // 如果响应不是 ok 且不是 503，不更新时间戳，下次会重试
                } catch (error) {
                    // 网络错误或其他问题，不更新时间戳，下次会重试发送这段时间
                    console.debug('更新游戏时长失败:', error.message);
                } finally {
                    // 无论成功或失败，都在10秒后继续下一次更新
                    setTimeout(updatePlayTime, 10000);
                }
            };

            // 立即启动第一次更新，不等待10秒
            updatePlayTime();
        }

        // 检查游戏时长相关成就
        async checkPlayTimeAchievements(currentPlayTime) {
            if (!currentPlayTime) return;

            // 遍历所有基于 Steam 统计的成就
            for (const [key, achievement] of Object.entries(ACHIEVEMENTS)) {
                if (achievement.steamStat === 'PLAY_TIME_SECONDS' &&
                    achievement.threshold &&
                    currentPlayTime >= achievement.threshold &&
                    !this.isUnlocked(key)) {
                    await this.unlockAchievement(key);
                }
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

    // 创建全局实例
    window.achievementManager = new AchievementManager();

    // 导出便捷函数
    window.unlockAchievement = (name) => window.achievementManager.unlockAchievement(name);
    window.incrementAchievementCounter = (counter, amount) => window.achievementManager.incrementCounter(counter, amount);
    window.getAchievementStats = () => window.achievementManager.getStats();

    console.log('成就管理系统已初始化');
})();
