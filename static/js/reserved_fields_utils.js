/**
 * 角色保留字段加载工具（供 chara_manager.js / steam_workshop_manager.js 共用）。
 *
 * 依赖：无外部依赖。需在使用方之前通过 <script> 引入。
 *
 * 使用方式:
 *   // 声明页面级变量
 *   let characterReservedFieldsConfig = ReservedFieldsUtils.emptyConfig();
 *
 *   // 加载
 *   characterReservedFieldsConfig = await ReservedFieldsUtils.load();
 */

// eslint-disable-next-line no-unused-vars
const ReservedFieldsUtils = (() => {
    function _safeArray(value) {
        return Array.isArray(value) ? value : [];
    }

    const SYSTEM_RESERVED_FIELDS_FALLBACK = Object.freeze([
        'live2d', 'voice_id', 'system_prompt', 'model_type', 'vrm', 'vrm_animation',
        'lighting', 'vrm_rotation', 'live2d_item_id', '_reserved', 'item_id', 'idleAnimation'
    ]);

    const WORKSHOP_RESERVED_FIELDS_FALLBACK = Object.freeze([
        '原始数据', '文件路径', '创意工坊物品ID',
        'description', 'tags', 'name',
        '描述', '标签', '关键词',
        '_reserved', 'item_id', 'idleAnimation'
    ]);

    const ALL_RESERVED_FIELDS_FALLBACK = Object.freeze(
        [...new Set([...SYSTEM_RESERVED_FIELDS_FALLBACK, ...WORKSHOP_RESERVED_FIELDS_FALLBACK])]
    );

    function emptyConfig() {
        return {
            system_reserved_fields: [],
            workshop_reserved_fields: [],
            all_reserved_fields: []
        };
    }

    /**
     * Fetch reserved-field config from the backend with timeout & fallback.
     *
     * @param {Object}  [opts]
     * @param {string}  [opts.url='/api/config/character_reserved_fields']
     * @param {number}  [opts.timeout=3000]  Abort timeout in ms
     * @param {string}  [opts.label='角色保留字段配置']  Label for console errors
     * @returns {Promise<{system_reserved_fields:string[], workshop_reserved_fields:string[], all_reserved_fields:string[]}>}
     */
    async function load(opts = {}) {
        const {
            url = '/api/config/character_reserved_fields',
            timeout = 3000,
            label = '角色保留字段配置',
        } = opts;

        const safeDefaults = emptyConfig();
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), timeout);
        try {
            const resp = await fetch(url, { signal: controller.signal });
            if (!resp.ok) {
                console.error(`加载${label}失败: HTTP ${resp.status}`);
                return safeDefaults;
            }
            const data = await resp.json();
            if (data && data.success) {
                return {
                    system_reserved_fields: _safeArray(data.system_reserved_fields),
                    workshop_reserved_fields: _safeArray(data.workshop_reserved_fields),
                    all_reserved_fields: _safeArray(data.all_reserved_fields),
                };
            }
            console.error(
                `加载${label}失败: success 标志无效`,
                (data && (data.error || data.message || data.status)) || data
            );
            return safeDefaults;
        } catch (e) {
            console.error(`加载${label}失败，使用安全默认值:`, e);
            return safeDefaults;
        } finally {
            clearTimeout(timeoutId);
        }
    }

    return {
        emptyConfig, load, _safeArray,
        SYSTEM_RESERVED_FIELDS_FALLBACK,
        WORKSHOP_RESERVED_FIELDS_FALLBACK,
        ALL_RESERVED_FIELDS_FALLBACK,
    };
})();
