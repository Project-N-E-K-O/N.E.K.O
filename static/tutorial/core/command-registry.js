(function (root, factory) {
    'use strict';

    const api = factory();
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }
    if (root) {
        root.TutorialCommandRegistry = api;
    }
})(typeof window !== 'undefined' ? window : globalThis, function () {
    'use strict';

    class CommandRegistry {
        constructor(options) {
            const normalizedOptions = options || {};
            this.handlers = Object.create(null);
            if (normalizedOptions.handlers && typeof normalizedOptions.handlers === 'object') {
                Object.keys(normalizedOptions.handlers).forEach((command) => {
                    this.register(command, normalizedOptions.handlers[command]);
                });
            }
        }

        register(command, handler) {
            const normalizedCommand = typeof command === 'string' ? command.trim() : '';
            if (!normalizedCommand || typeof handler !== 'function') {
                return false;
            }
            this.handlers[normalizedCommand] = handler;
            return true;
        }

        unregister(command) {
            const normalizedCommand = typeof command === 'string' ? command.trim() : '';
            if (!normalizedCommand || !this.handlers[normalizedCommand]) {
                return false;
            }
            delete this.handlers[normalizedCommand];
            return true;
        }

        has(command) {
            const normalizedCommand = typeof command === 'string' ? command.trim() : '';
            return !!(normalizedCommand && this.handlers[normalizedCommand]);
        }

        getRegisteredCommands() {
            return Object.keys(this.handlers).sort();
        }

        dispatch(event, context) {
            const normalizedEvent = event && typeof event === 'object' ? event : {};
            const command = typeof normalizedEvent.command === 'string'
                ? normalizedEvent.command.trim()
                : '';
            if (!command) {
                return Promise.resolve(null);
            }
            const handler = this.handlers[command];
            if (typeof handler !== 'function') {
                return Promise.resolve(null);
            }
            try {
                return Promise.resolve(handler(normalizedEvent, context || {}));
            } catch (error) {
                return Promise.reject(error);
            }
        }
    }

    function createTutorialCommandRegistry(options) {
        return new CommandRegistry(options);
    }

    return {
        CommandRegistry,
        createTutorialCommandRegistry
    };
});
