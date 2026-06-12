/**
 * Neko Claudian — i18n Runtime
 * Handles internationalization.
 */

const Nekoi18n = {
    locale: 'zh-CN',
    translations: {},

    async load(locale) {
        this.locale = locale;
        try {
            const response = await fetch(`/neko_claudian/static/i18n/${locale}.json`);
            if (response.ok) {
                this.translations = await response.json();
            }
        } catch (e) {
            console.warn(`Failed to load i18n for ${locale}`);
        }
    },

    t(key, params = {}) {
        let text = this.translations[key] || key;
        // Replace parameters
        Object.entries(params).forEach(([k, v]) => {
            text = text.replace(`{${k}}`, v);
        });
        return text;
    },

    getLocale() {
        return this.locale;
    },
};

// Export for use
if (typeof module !== 'undefined') {
    module.exports = Nekoi18n;
}
