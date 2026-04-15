"""
CCO (Context Cache Optimization) 完整测试

测试所有 API 提供商的 Context Cache：
1. 阿里云 DashScope (qwen)
2. OpenAI
3. 智谱 GLM
4. 阶跃星辰 Step
5. 硅基流动 Silicon
6. Google Gemini
7. Moonshot Kimi

Reference: https://help.aliyun.com/zh/model-studio/user-guide/context-cache
"""

import sys
sys.path.insert(0, '.')

from config.providers import CACHE_PROVIDERS as PROVIDER_CACHE_CONFIG


def test_all_providers_config():
    """测试所有提供商的缓存配置"""
    print("\n" + "="*70)
    print("测试: 所有 API 提供商缓存配置")
    print("="*70)

    print(f"\n{'提供商':<20} {'缓存模式':<12} {'Header':<25} {'最小Token':<10}")
    print("-" * 70)

    for config in PROVIDER_CACHE_CONFIG.values():
        header = f"{config['header_name']}: {config['header_value']}" if config['requires_header'] else "N/A"
        print(f"{config['name']:<20} {config['cache_mode']:<12} {header:<25} {config['min_cache_tokens']:<10}")
        assert config.get("name") and config.get("cache_mode") and config.get("min_cache_tokens") is not None


def test_token_extraction_all_providers():
    """测试所有提供商的 Token 提取"""
    print("\n" + "="*70)
    print("测试: Token Tracker 缓存字段提取")
    print("="*70)

    test_cases = {
        "qwen/openai": {
            "prompt_tokens": 10000,
            "completion_tokens": 100,
            "prompt_tokens_details": {"cached_tokens": 9000}
        },
        "glm/step": {
            "prompt_tokens": 10000,
            "completion_tokens": 100,
            "cached_tokens": 9000
        },
        "silicon/kimi": {
            "prompt_tokens": 10000,
            "completion_tokens": 100,
            "prompt_cache_hit_tokens": 9000
        },
        "gemini": {
            "prompt_tokens": 10000,
            "completion_tokens": 100,
            "cached_content_token_count": 9000
        },
    }

    from utils.token_tracker import _extract_cached_tokens

    for provider_name, usage_dict in test_cases.items():
        cached = _extract_cached_tokens(usage_dict)
        expected = 9000
        status = "[PASS]" if cached == expected else "[FAIL]"
        print(f"  {status} {provider_name}: 提取到 {cached} tokens (预期: {expected})")
        assert cached == expected, f"{provider_name}: got {cached}, expected {expected}"


def test_cost_calculation_all_providers():
    """测试所有提供商的费用计算"""
    print("\n" + "="*70)
    print("测试: 费用计算 (90% 缓存命中率)")
    print("="*70)

    prompt_tokens = 10000
    cached_tokens = 9000
    input_price = 0.001

    print(f"\n场景: {prompt_tokens} tokens 输入, {cached_tokens} tokens 命中缓存 (90%)")
    print(f"{'提供商':<25} {'费用':<12} {'无缓存':<12} {'节省':<10}")
    print("-" * 60)

    miss_tokens = prompt_tokens - cached_tokens
    for config in PROVIDER_CACHE_CONFIG.values():
        cache_price = config["cache_price"]
        creation_price = config["creation_price"]

        if config["cache_mode"] == "session":
            cached_cost = (cached_tokens / 1000) * input_price * cache_price
            creation_cost = (cached_tokens / 1000) * input_price * (creation_price - cache_price)
            miss_cost = (miss_tokens / 1000) * input_price
            total_cost = cached_cost + creation_cost + miss_cost
        elif config["cache_mode"] == "upstream":
            total_cost = (cached_tokens / 1000) * input_price * 0.10 + (miss_tokens / 1000) * input_price
        else:
            total_cost = (cached_tokens / 1000) * input_price * cache_price + (miss_tokens / 1000) * input_price

        no_cache_cost = (prompt_tokens / 1000) * input_price
        savings = no_cache_cost - total_cost
        savings_pct = (savings / no_cache_cost) * 100

        print(f"{config['name']:<25} {total_cost:.6f}    {no_cache_cost:.6f}    {savings_pct:.1f}%")
        assert savings >= 0, f"{config['name']}: negative savings {savings}"
        assert 0 <= savings_pct <= 100, f"{config['name']}: savings_pct {savings_pct}% out of range"


def test_cache_hit_rate_scenarios():
    """测试不同缓存命中率场景"""
    print("\n" + "="*70)
    print("测试: 不同缓存命中率场景 (以 qwen 为例)")
    print("="*70)

    from utils.token_tracker import calculate_cache_hit_rate

    scenarios = [
        (2911, 2888, "实际会话 99.2%"),
        (10000, 9000, "90% 命中率"),
        (10000, 5000, "50% 命中率"),
        (10000, 1000, "10% 命中率"),
        (1000, 0, "0% 命中率"),
    ]

    print(f"\n{'场景':<20} {'Prompt':<10} {'Cached':<10} {'命中率':<10} {'节省'}")
    print("-" * 60)

    for prompt, cached, desc in scenarios:
        hit_rate = calculate_cache_hit_rate(prompt, cached)
        savings = hit_rate * 90
        print(f"{desc:<20} {prompt:<10} {cached:<10} {hit_rate*100:.1f}%     {savings:.1f}%")
        assert 0.0 <= hit_rate <= 1.0, f"{desc}: hit_rate {hit_rate} out of range"


def test_provider_compatibility():
    """测试提供商兼容性"""
    print("\n" + "="*70)
    print("测试: 提供商兼容性检查")
    print("="*70)

    from config.providers import get_cache_kwargs

    results = []

    for provider_id, config in PROVIDER_CACHE_CONFIG.items():
        cache_config = get_cache_kwargs(config["base_url"])

        if provider_id in ("qwen", "qwen_intl"):
            expected = True
        else:
            expected = False

        actual = cache_config["enable_cache_control"]
        passed = actual == expected
        status = "[PASS]" if (actual == expected) else "[FAIL]"
        print(f"  {status} {config['name']}: 缓存控制 = {actual}")
        assert actual == expected, f"{config['name']}: expected {expected}, got {actual}"


def test_min_cache_tokens_all_providers():
    """测试所有提供商的最小缓存限制"""
    print("\n" + "="*70)
    print("测试: 各提供商最小缓存 Token 限制")
    print("="*70)

    print(f"\n{'提供商':<25} {'最小缓存':<12} {'<1024行为'}")
    print("-" * 50)

    for config in PROVIDER_CACHE_CONFIG.values():
        min_tokens = config["min_cache_tokens"]
        behavior = "不可缓存" if min_tokens >= 1024 else "可缓存"
        print(f"{config['name']:<25} {min_tokens:<12} {behavior}")
        assert isinstance(min_tokens, int) and min_tokens > 0, f"{config['name']}: invalid min_cache_tokens {min_tokens}"


def test_session_cache_header():
    """测试 Session Cache Header (仅 qwen)"""
    print("\n" + "="*70)
    print("测试: Session Cache Header 配置")
    print("="*70)

    from config.providers import get_cache_kwargs

    qwen_config = get_cache_kwargs("https://dashscope.aliyuncs.com/compatible-mode/v1")

    print("\n  qwen (DashScope):")
    print(f"    enable_cache_control: {qwen_config['enable_cache_control']}")
    print(f"    default_headers: {qwen_config['default_headers']}")

    expected_header = {"x-dashscope-session-cache": "enable"}
    passed = qwen_config["enable_cache_control"] is True and qwen_config["default_headers"] == expected_header

    status = "[PASS]" if passed else "[FAIL]"
    print(f"\n  {status} Session Cache Header 正确配置")
    assert qwen_config["enable_cache_control"] is True, "enable_cache_control should be True"
    assert qwen_config["default_headers"] == expected_header, f"headers mismatch: {qwen_config['default_headers']}"


def main():
    print("\n" + "="*70)
    print("CCO (Context Cache Optimization) 完整测试 - 所有 API 提供商")
    print("="*70)

    tests = [
        ("所有提供商缓存配置", test_all_providers_config),
        ("Token 字段提取", test_token_extraction_all_providers),
        ("费用计算", test_cost_calculation_all_providers),
        ("缓存命中率场景", test_cache_hit_rate_scenarios),
        ("提供商兼容性", test_provider_compatibility),
        ("最小缓存限制", test_min_cache_tokens_all_providers),
        ("Session Header", test_session_cache_header),
    ]

    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            # assert-based tests return None on success; treat None as passed
            success = True if result is None or result is True else bool(result)
            results.append((name, success))
        except Exception as e:
            print(f"\n  [ERROR] {name}: {e}")
            results.append((name, False))

    print("\n" + "="*70)
    print("测试结果汇总")
    print("="*70)

    passed = sum(1 for _, r in results if r)
    total = len(results)

    for name, result in results:
        status = "[PASS]" if result else "[FAIL]"
        print(f"  {status} {name}")

    print(f"\n总计: {passed}/{total} 通过")

    if passed == total:
        print("\n" + "="*70)
        print("所有 API 提供商 CCO 测试通过!")
        print("="*70)
        print("\n支持的 API 提供商:")
        for config in PROVIDER_CACHE_CONFIG.values():
            print(f"  - {config['name']}: {config['cache_mode']} 模式")

    return passed == total


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
