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
    """enable_cache_control tracks requires_body_flag only, orthogonal to
    requires_header. Header-only providers (qwen) must now report False."""
    print("\n" + "="*70)
    print("测试: 提供商兼容性检查")
    print("="*70)

    from config.providers import get_cache_kwargs

    for provider_id, config in PROVIDER_CACHE_CONFIG.items():
        cache_config = get_cache_kwargs(config["base_url"])

        # body 级 cache flag 由 requires_body_flag 决定，而非 requires_header。
        expected = config["requires_body_flag"]

        actual = cache_config["enable_cache_control"]
        status = "[PASS]" if (actual == expected) else "[FAIL]"
        print(f"  {status} {config['name']}: 缓存控制 = {actual}")
        assert actual == expected, f"{config['name']}: expected {expected}, got {actual}"

    # 没接 body-flag provider 时，全员都应该是 False（header 路不受影响）。
    assert all(
        not get_cache_kwargs(c["base_url"])["enable_cache_control"]
        for c in PROVIDER_CACHE_CONFIG.values()
    ), "当前没有 provider 需要 body 级 flag，enable_cache_control 应全为 False"


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
    """Session Cache Header (qwen only).

    qwen takes the header path: default_headers carries the session-cache
    header, but enable_cache_control must be False -- it needs no body-level
    cache_control marker, and stamping an Anthropic-style cache_control onto
    DashScope (an OpenAI-compatible endpoint) would be wrong.
    """
    print("\n" + "="*70)
    print("测试: Session Cache Header 配置")
    print("="*70)

    from config.providers import get_cache_kwargs

    qwen_config = get_cache_kwargs("https://dashscope.aliyuncs.com/compatible-mode/v1")

    print("\n  qwen (DashScope):")
    print(f"    enable_cache_control: {qwen_config['enable_cache_control']}")
    print(f"    default_headers: {qwen_config['default_headers']}")

    expected_header = {"x-dashscope-session-cache": "enable"}
    passed = qwen_config["enable_cache_control"] is False and qwen_config["default_headers"] == expected_header

    status = "[PASS]" if passed else "[FAIL]"
    print(f"\n  {status} Session Cache Header 正确配置")
    assert qwen_config["enable_cache_control"] is False, "header-only provider 不应打 body flag"
    assert qwen_config["default_headers"] == expected_header, f"headers mismatch: {qwen_config['default_headers']}"


def test_body_flag_drives_enable_cache_control():
    """Only a provider with requires_body_flag=True gets enable_cache_control=True,
    fully orthogonal to requires_header (both can coexist). Verified via a
    temporary synthetic provider."""
    print("\n" + "="*70)
    print("测试: requires_body_flag 驱动 enable_cache_control")
    print("="*70)

    from config.providers import CACHE_PROVIDERS, CacheProviderConfig, get_cache_kwargs

    fake = CacheProviderConfig(
        provider_id="anthropic_test",
        name="Anthropic (test)",
        base_url="https://anthropic-test.example/v1",
        base_url_pattern="anthropic-test.example",
        cache_mode="auto",
        requires_header=True,           # 故意两个都打开，验证正交
        requires_body_flag=True,
        header_name="x-test-cache",
        header_value="on",
    )
    CACHE_PROVIDERS["anthropic_test"] = fake
    try:
        kw = get_cache_kwargs("https://anthropic-test.example/v1/messages")
        print(f"  body-flag provider → {kw}")
        assert kw["enable_cache_control"] is True, "requires_body_flag 应驱动 enable_cache_control"
        assert kw["default_headers"] == {"x-test-cache": "on"}, "header 与 body flag 正交，应同时注入"
        print("  [PASS] requires_body_flag 正确驱动 enable_cache_control 且与 header 正交")
    finally:
        del CACHE_PROVIDERS["anthropic_test"]


def test_inject_cache_control_helper():
    """_inject_cache_control / _attach_cache_control: breakpoint selection,
    string promotion, idempotency, and defensive no-mutation behavior."""
    print("\n" + "="*70)
    print("测试: body 级 cache_control 注入逻辑")
    print("="*70)

    from utils.llm_client import _inject_cache_control

    EPH = {"type": "ephemeral"}

    # 1) 有 system → 标在最后一条 system 上；字符串内容提升为 text part。
    msgs = [
        {"role": "system", "content": "you are a cat"},
        {"role": "user", "content": "hi"},
    ]
    out = _inject_cache_control(msgs)
    assert out[0]["content"] == [{"type": "text", "text": "you are a cat", "cache_control": EPH}]
    assert out[1] == {"role": "user", "content": "hi"}, "非断点消息不应被改"
    # 原列表与原 dict 不被 mutate
    assert msgs[0]["content"] == "you are a cat", "原始消息被 mutate 了"
    print("  [PASS] system 断点：字符串提升为带 cache_control 的 text part，原对象不变")

    # 2) 没有 system → 标在最后一条消息。
    msgs2 = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
    ]
    out2 = _inject_cache_control(msgs2)
    assert out2[0] == {"role": "user", "content": "a"}
    assert out2[1]["content"] == [{"type": "text", "text": "b", "cache_control": EPH}]
    print("  [PASS] 无 system：回退到最后一条消息")

    # 3) content 已是 parts list → 标在最后一个 text part 上。
    msgs3 = [{
        "role": "system",
        "content": [
            {"type": "text", "text": "p1"},
            {"type": "image_url", "image_url": {"url": "x"}},
            {"type": "text", "text": "p2"},
        ],
    }]
    out3 = _inject_cache_control(msgs3)
    parts = out3[0]["content"]
    assert parts[2] == {"type": "text", "text": "p2", "cache_control": EPH}
    assert parts[0] == {"type": "text", "text": "p1"}, "非最后 text part 不应带标记"
    assert parts[1] == {"type": "image_url", "image_url": {"url": "x"}}, "图片 part 不应带标记"
    print("  [PASS] parts list：只标最后一个 text part，图片/前置 part 不动")

    # 4) 断点选 leading 连续 system 段的末尾，trailing system（状态/归档提示）
    #    不许偷走断点 —— 否则只缓存一句废话，长上下文白缓存。
    msgs4 = [
        {"role": "system", "content": "BIG STABLE SYSTEM PROMPT"},
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a"},
        {"role": "system", "content": "你已断开连接"},  # trailing 噪声 system
    ]
    out4 = _inject_cache_control(msgs4)
    assert out4[0]["content"] == [{"type": "text", "text": "BIG STABLE SYSTEM PROMPT", "cache_control": EPH}]
    assert out4[3] == {"role": "system", "content": "你已断开连接"}, "trailing system 不应被标记"
    print("  [PASS] leading system 段末尾被选中，trailing system 噪声不抢断点")

    # 5) 幂等：对已打标的输出再跑一次，不二次打标、不 clobber 既有 marker。
    once = _inject_cache_control([{"role": "system", "content": "s"}])
    twice = _inject_cache_control(once)
    assert twice is once, "已打标 → 原样返回，幂等"
    rich = [{"role": "system", "content": [{"type": "text", "text": "s", "cache_control": {"type": "ephemeral", "ttl": "1h"}}]}]
    assert _inject_cache_control(rich) is rich, "已有更丰富的 marker → 不覆盖"
    print("  [PASS] 幂等：重复调用不二次打标，既有 marker 不被 clobber")

    # 6) 防御分支：非 dict / None content / 纯图片 system / 空列表 / 空字符串。
    assert _inject_cache_control([]) == []
    img_only = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}]
    assert _inject_cache_control(img_only) is img_only, "无可标记 text → 原样返回"
    empty_str = [{"role": "system", "content": ""}]
    assert _inject_cache_control(empty_str) is empty_str, "空字符串 → 不造空 part"
    none_content = [{"role": "system", "content": None}, {"role": "user", "content": "u"}]
    # leading system 的 content=None 不可标记 → 整体 no-op（不回退到 user）。
    assert _inject_cache_control(none_content) is none_content, "system content=None → no-op"
    sys_img_only = [{"role": "system", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}]
    assert _inject_cache_control(sys_img_only) is sys_img_only, "纯图片 system → idx 为 None → no-op"
    with_nondict = [{"role": "system", "content": "s"}, None, {"role": "user", "content": "u"}]
    out6 = _inject_cache_control(with_nondict)
    assert out6[0]["content"] == [{"type": "text", "text": "s", "cache_control": EPH}]
    assert out6[1] is None, "非 dict 成员原样保留，不崩"
    print("  [PASS] 防御分支：非 dict / None content / 纯图片 system 均安全 no-op 或跳过")


def test_params_consumes_enable_cache_control():
    """End-to-end: ChatOpenAI._params actually reads self.enable_cache_control
    and stamps the marker, and changes nothing when off -- proving the field is
    no longer dead."""
    print("\n" + "="*70)
    print("测试: _params 消费 enable_cache_control")
    print("="*70)

    from utils.llm_client import ChatOpenAI

    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
    ]

    on = ChatOpenAI(model="claude-x", base_url="https://x/v1", api_key="k", enable_cache_control=True)
    p_on = on._params(msgs)
    assert p_on["messages"][0]["content"] == [
        {"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}
    ], "enable_cache_control=True 时应打标"
    print("  [PASS] enable_cache_control=True：system 消息被打上 cache_control")

    off = ChatOpenAI(model="qwen-x", base_url="https://y/v1", api_key="k", enable_cache_control=False)
    p_off = off._params(msgs)
    assert p_off["messages"][0]["content"] == "sys", "关闭时不应改动消息"
    print("  [PASS] enable_cache_control=False：消息原样下发")


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
        ("body-flag 驱动 enable_cache_control", test_body_flag_drives_enable_cache_control),
        ("cache_control 注入逻辑", test_inject_cache_control_helper),
        ("_params 消费 enable_cache_control", test_params_consumes_enable_cache_control),
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
