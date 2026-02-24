import pytest
import os
import logging
from unittest.mock import MagicMock

# Adjust path to import project modules
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from main_logic.omni_offline_client import OmniOfflineClient

logger = logging.getLogger(__name__)


# Providers to test: (provider_key, env_var_name)
PROVIDERS = [
    ("qwen", "ASSIST_API_KEY_QWEN"),
    ("openai", "ASSIST_API_KEY_OPENAI"),
    ("glm", "ASSIST_API_KEY_GLM"),
    ("step", "ASSIST_API_KEY_STEP"),
    ("silicon", "ASSIST_API_KEY_SILICON"),
    # Gemini requires specific setup often skipped in CI, but we test if key exists
    ("gemini", "ASSIST_API_KEY_GEMINI"),
]

@pytest.mark.unit
@pytest.mark.parametrize("provider_key, env_var", PROVIDERS)
def test_provider_connectivity(loaded_api_keys, provider_key, env_var, clean_user_data_dir):
    """
    Test that OmniOfflineClient can be instantiated and (optionally) connect to the provider.
    Note: Real connection tests might fail if keys are invalid or quotas exceeded.
    We primarily check configuration loading and client initialization here.
    """
    if env_var not in os.environ:
        pytest.skip(f"Environment variable {env_var} not set for provider {provider_key}")

    # Use ConfigManager to get provider config
    from utils.api_config_loader import get_assist_api_profiles
    assist_profiles = get_assist_api_profiles()
    
    # Check if provider exists in configuration
    assert provider_key in assist_profiles, f"Provider {provider_key} not found in assist_profiles"
    profile = assist_profiles[provider_key]
    
    logger.info(f"Testing provider: {provider_key} with model {profile.get('CORRECTION_MODEL')}")


    # Instantiate OmniOfflineClient
    try:
        api_key = profile.get('OPENROUTER_API_KEY')
        if not api_key:
            api_key = os.environ.get(env_var)
        
        if not api_key:
            pytest.skip(f"API key for {provider_key} not found in config or environment.")

        client = OmniOfflineClient(
            base_url=profile['OPENROUTER_URL'],
            api_key=api_key,
            model=profile['CORRECTION_MODEL'],
            vision_model=profile.get('VISION_MODEL', ''),
            vision_base_url=profile.get('VISION_BASE_URL', ''), 
            vision_api_key=profile.get('VISION_API_KEY', ''),
            on_text_delta=MagicMock(),
            on_response_done=MagicMock()
        )
        assert client is not None
        assert client.llm is not None or client.model is not None
        
        # Checking if we can actually create a message (without sending it yet to avoid cost/time in this loop)
        # To truly test connectivity, we would need to invoke it.
        # Uncomment below to enable real API calls (costs money!)
        # response = client.invoke_sync("Hello, simply reply 'Hi'.")
        # assert "Hi" in response or len(response) > 0
        
    except Exception as e:
        pytest.fail(f"Failed to instantiateOmniOfflineClient for {provider_key}: {e}")

if __name__ == "__main__":
    pytest.main([__file__])
