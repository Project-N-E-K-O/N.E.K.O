"""A narrow compatibility view with one provider's credentials frozen."""


class VoiceRuntimeSnapshot:
    def __init__(self, manager, runtime):
        self.manager = manager
        self.runtime = runtime

    def __getattr__(self, name):
        return getattr(self.manager, name)

    def get_tts_api_key(self, provider):
        if provider == self.runtime.provider:
            return self.runtime.api_key
        return self.manager.get_tts_api_key(provider)

    def get_cosyvoice_clone_runtime(self, provider):
        if provider == self.runtime.provider:
            return {
                "provider": provider, "api_key": self.runtime.api_key,
                "base_url": self.runtime.base_url, "model": self.runtime.model,
            }
        return self.manager.get_cosyvoice_clone_runtime(provider)
