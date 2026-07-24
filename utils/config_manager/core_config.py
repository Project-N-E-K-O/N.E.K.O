# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Core config mixin.

get_core_config / get_model_api_config snapshot assembly, geo (mainland vs
non-mainland) region resolution, free-route URL adjustment and the agent/voice
free-tier predicates.

The geo caches themselves are class attributes on the assembled
``ConfigManager`` (single owner); methods below resolve them late through
the package facade.

Region-resolution invariants
----------------------------
These six constrain each other, and a change that satisfies one can quietly
break another — during the work that introduced them, two were broken exactly
that way (a "kick a due probe" fix bypassed #2; an ``is_alive()`` guard
disabled #4). Check a change against all six, not just the one it targets.

Checking the list is necessary but not sufficient: the mechanism added to
satisfy #4 (replacing a wedged probe) was itself correct against every rule
above and still introduced two fresh defects, because it quietly made the
cache multi-writer. #6 is what that cost, and it is the rule to reach for
whenever a fix adds a concurrent actor rather than a condition.

1. The probe never does network IO on the caller's thread. ``get_core_config``
   fans out to ~40 sync callers living inside ``async def``, so a blocking
   probe freezes the shared event loop and stalls every WebSocket handshake in
   the process. Waiting is allowed only at startup and only offloaded.
2. The probe is started only for the free ``lanlan.tech`` route. Users on their
   own API keys never have their public IP handed to a third-party geolocation
   service. Reading the config is the natural gate (URL rewriting consults the
   region only for that host) — go through it rather than re-deriving
   eligibility at each call site, or the checks will drift apart.
3. ``_region_cache`` is written only from the IP verdict. Steam is a fallback
   vote that must never latch, at any failure count: it counts probes started,
   not finished, so any threshold can fire while one is still in flight.
4. The probe never gives up permanently — including when a thread wedges in
   ``getaddrinfo`` (not covered by the socket timeout). Liveness alone must not
   gate new probes, or one wedged thread cancels the retry schedule forever.
5. Every path that freezes a base URL into a session settles the region first
   (main session, hot-swap prepare, game session pool). Tests assert this
   structurally, because the real risk is a *new* path added later.
6. At most one probe owns the verdict, and outstanding probes are bounded.
   Replacing a wedged probe does not stop it: it stays runnable and can surface
   long afterwards with an answer taken before the network exit changed, so
   ownership is carried by ``_ip_probe_generation`` and a superseded probe
   discards its own result. Wedged threads are unjoinable, so replacements are
   also capped (``_IP_PROBE_MAX_WEDGED``) — otherwise a permanently blocked
   resolver leaks one thread per backoff cycle for the life of the process.
"""
import asyncio
import json
import math
import sys
import threading
import time
from copy import deepcopy
from urllib.parse import urlparse, urlunparse

from config import DEFAULT_CONFIG_DATA, GEOIP_FORCE_NON_MAINLAND
from utils.gptsovits_config import normalize_gsv_api_url
from utils.steam_state import get_steamworks

from ._shared import _as_bool, logger


class CoreConfigMixin:
    """Core config snapshot, geo checks and model API resolution."""

    # HTTP 地理探测的失败退避：开机自启动时本程序常常跑在网络栈就绪之前，第一次
    # 探测必定超时。Steam 缺席的机器上 IP 是唯一判据（见 _check_non_mainland），
    # 永久放弃会把整台机器锁死在国内线路，而网络可能几十分钟后才好（用户开机后
    # 才连 WiFi），所以不设次数上限，只按失败次数指数退避到 _IP_CHECK_RETRY_MAX_S。
    _IP_CHECK_RETRY_BASE_S = 30.0
    _IP_CHECK_RETRY_MAX_S = 600.0

    # 指数先于乘法封顶：探测不设次数上限，一台长期离线的机器攒够失败次数后
    # float * (2 ** 巨大整数) 会直接抛 OverflowError，而不是溢出成 inf。
    # 2**_IP_CHECK_MAX_EXPONENT 乘上 base 已远超 _IP_CHECK_RETRY_MAX_S，封在这里无损。
    _IP_CHECK_MAX_EXPONENT = 32

    # 探测线程超过这个时长仍未结束就当作卡死（getaddrinfo 不受 socket 超时约束），
    # 允许另起一个顶替，否则一个卡死的线程会永久挡住退避重试。取值须明显大于
    # 探测自身的 3s socket 超时，避免把只是慢的正常探测误判成卡死。
    _IP_PROBE_STALE_AFTER_S = 30.0
    # 同时允许存在的卡死探测线程上限。DNS 永久阻塞时它们 join 不掉、只增不减，
    # 不封顶就会随进程寿命线性泄漏（封顶退避 10 分钟一档 ≈ 每小时 6 个）。
    _IP_PROBE_MAX_WEDGED = 3
    # 到达上限后的「兜底探测」节奏。目的不是继续正常重试，而是保证网络恢复不会
    # 被永久错过——线程增速降到每小时 1 个，同时最迟一小时内发现恢复。
    _IP_PROBE_DESPERATE_INTERVAL_S = 3600.0

    @classmethod
    def _ip_check_backoff_s(cls, failures: int) -> float:
        """Seconds to wait before the next probe after `failures` consecutive failures."""
        if failures <= 0:
            return 0.0
        exponent = min(failures - 1, cls._IP_CHECK_MAX_EXPONENT)
        return min(cls._IP_CHECK_RETRY_BASE_S * (2 ** exponent), cls._IP_CHECK_RETRY_MAX_S)

    async def aget_core_config(self):
        """Async wrapper for get_core_config: internally open()+json.load() reads core_config.json;
        async endpoints must offload it to avoid blocking the event loop."""
        return await asyncio.to_thread(self.get_core_config)

    # --- Core config helpers ---

    @staticmethod
    def _run_ip_probe(generation):
        """Body of the IP geolocation probe. Always runs on a dedicated thread.

        ``generation`` is the probe's ticket. A probe wedged in DNS gets replaced
        while it is still running, and can surface much later with an answer taken
        before the network exit changed (WiFi came back, VPN toggled). Writing that
        unconditionally would clobber the replacement's newer, correct verdict — so
        a probe only publishes while its ticket is still the current one.
        """
        from utils.config_manager import ConfigManager

        try:
            import urllib.request
            req = urllib.request.Request(
                "http://ip-api.com/json/?fields=countryCode",
                headers={"User-Agent": "Mozilla/5.0"},
            )
            # 显式禁用代理，避免探测到代理服务器所在国家而非用户真实 IP 所在地。
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=3) as resp:
                data = json.loads(resp.read().decode())
            country = (data.get("countryCode") or "").upper()
            if country:
                result = country != "CN"
                with ConfigManager._geo_probe_lock:
                    if generation != ConfigManager._ip_probe_generation:
                        print(
                            f"[GeoIP] discarding superseded probe result: country={country}",
                            file=sys.stderr,
                        )
                        return
                    ConfigManager._ip_check_cache = result
                print(f"[GeoIP] HTTP IP check: country={country}, non_mainland={result}", file=sys.stderr)
                return
        except Exception as e:
            print(f"[GeoIP] HTTP IP check failed: {e}", file=sys.stderr)
        # 刻意不写永久放弃标记：网络可能在几十分钟后才就绪，退避到 10 分钟一次的
        # 探测成本可以忽略，而永久放弃会把 Steam 缺席的海外用户锁死在国内线路。
        print(
            f"[GeoIP] HTTP IP check will retry in "
            f"{ConfigManager._ip_check_backoff_s(ConfigManager._ip_check_attempts):.0f}s",
            file=sys.stderr,
        )

    @staticmethod
    def join_ip_probe(timeout: float = 5.0) -> bool:
        """Block until the in-flight GeoIP probe finishes. Returns whether a verdict landed.

        Only for startup and session setup, and only from a worker thread (see
        ``awarmup_region_check``): request paths must never wait on the probe.
        Waiting before a session starts is what keeps it off the transient mainland
        fallback — the route is frozen into each session at start_session time.

        Skipped entirely once Steam has answered. The wait exists to avoid routing on
        *no* information; Steam's answer is information, and it is already enough to
        pick a route. IP still outranks it — the Steam verdict is never latched, so
        the probe takes over for later sessions once it lands. Waiting anyway would
        tax exactly the users who already have an answer in hand.
        """
        from utils.config_manager import ConfigManager

        if ConfigManager._steam_check_cache is not None:
            return True
        thread = ConfigManager._ip_probe_thread
        if thread is not None:
            thread.join(timeout)
        return ConfigManager._ip_check_cache is not None

    async def aensure_region_resolved(self, timeout: float = 1.5) -> bool:
        """Last chance for an in-flight probe before a session freezes its route.

        A session pins its base URL at start_session and never revisits it, so a
        verdict that lands one second too late costs that whole session. Startup
        already waits, but its join can expire while the probe sits in DNS
        resolution (the 3s socket timeout does not cover ``getaddrinfo``).

        Costs nothing on the normal path: returns immediately unless the region is
        still unknown *and* a probe is actually in flight. The wait itself is
        offloaded, so the event loop keeps running.
        """
        from utils.config_manager import ConfigManager

        if ConfigManager._region_cache is not None or ConfigManager._ip_check_cache is not None:
            return True
        # Steam 已经给出结论：足够选线路了，不必再为 IP 付等待。IP 落地后照样接管
        # （Steam 票不落定），所以这里省下的是延迟、不是正确性。
        if self._check_steam_non_mainland() is not None:
            return True

        # 读一次配置来补发到期的探测：上一次失败、退避又已到期时这里不发，就只能
        # 用兜底线路开一整场，而随后 get_core_config 发起的那次赶不上本场线路定死。
        #
        # 刻意走 aget_core_config 而不是直接戳 _check_ip_non_mainland_http：URL 改写
        # 内部只对 lanlan.tech 免费路由做区域判定，这就是「该不该探测」的天然门。
        # 直接戳会让自配 API / livestream 用户也把 IP 发给 ip-api.com——他们的线路
        # 根本不经过区域改写，那次请求纯属白白暴露。
        await self.aget_core_config()
        if ConfigManager._ip_check_cache is not None:
            return True
        thread = ConfigManager._ip_probe_thread
        if thread is None or not thread.is_alive():
            return False
        resolved = await asyncio.to_thread(self.join_ip_probe, timeout)
        if not resolved:
            # 等满仍无结论：这一场会话会用大陆兜底线路，且中途不会改。无限等不是
            # 选项（会话会挂死），所以这里只把失败态记下来，让现场可诊断——否则
            # 「海外用户偶尔一整场很慢」在日志里没有任何痕迹。
            logger.warning(
                "[GeoIP] 区域判定在会话开始前仍未落地（等待 %.1fs），本场会话按大陆线路启动",
                timeout,
            )
        return resolved

    async def awarmup_region_check(self, timeout: float = 5.0) -> bool:
        """Resolve the region before the server starts accepting sessions.

        Reads the config (which kicks the probe only on the free ``lanlan.tech``
        route, so users on their own API keys never hand their IP to a third-party
        geolocation service), then waits for that probe off the event loop.
        """
        await self.aget_core_config()
        return await asyncio.to_thread(self.join_ip_probe, timeout)

    @staticmethod
    def _check_ip_non_mainland_http():
        """Non-blocking read of the IP geolocation verdict (ip-api.com over HTTP).

        Never performs network IO on the caller's thread. When a probe is due this
        kicks one off in the background and reports "no verdict yet"; the answer is
        picked up by whichever call comes after it lands.

        Blocking here is not an option: ``get_core_config`` fans out to ~40 sync
        callers that sit inside ``async def`` (``get_model_api_config`` in
        ``_start_session_prepare_runtime`` among them), so a 3s connect timeout
        freezes the shared event loop — every WebSocket handshake and heartbeat
        across all three subsystems stalls with it, which reads to users as
        "cannot connect".
        """
        # Late-bound: class-level shared state (single owner) lives on the
        # assembled ConfigManager; resolve it through the package facade.
        from utils.config_manager import ConfigManager

        cache = ConfigManager._ip_check_cache
        if cache is not None:
            return cache

        # 退避账本的 check-and-set 必须原子：并发调用同时穿过门会在一次网络未就绪的
        # 爆发里把退避烧光，也会同时起多个探测线程。
        now = time.monotonic()
        with ConfigManager._geo_probe_lock:
            if ConfigManager._ip_check_cache is not None:
                return ConfigManager._ip_check_cache
            in_flight = ConfigManager._ip_probe_thread
            if in_flight is not None and in_flight.is_alive():
                # 用「是否还活着」当唯一判据会留一个死锁：getaddrinfo 不受 socket
                # 超时约束，DNS 卡住的线程可以活得任意久，而它活着就永远挡住新探测
                # ——指数退避再也不会跑，网络恢复了也回不来。超龄的当作已失效，另起
                # 一个顶替（旧的靠 generation 失去写权，见 _run_ip_probe）。
                started = ConfigManager._ip_probe_started_monotonic
                if started is None or (now - started) < ConfigManager._IP_PROBE_STALE_AFTER_S:
                    return None
                # 顶替不能无限做：DNS 永久阻塞时每个退避周期都换一个，卡死的
                # daemon 线程只增不减（封顶 10 分钟一档 ≈ 每小时 6 个，进程活多久
                # 漏多久）。累积到上限就停手——此时网络已经坏得没有再探的意义。
                ConfigManager._wedged_probes = [
                    t for t in ConfigManager._wedged_probes if t.is_alive()
                ]
                if len(ConfigManager._wedged_probes) >= ConfigManager._IP_PROBE_MAX_WEDGED:
                    # 到顶不能变成永久停手：那等于用「线程不泄漏」换回了本 PR 一开始
                    # 删掉的那个死局——网络恢复后再也没人去探，海外用户锁死国内线路
                    # （不变量 #4）。改为把节奏降到 _IP_PROBE_DESPERATE_INTERVAL_S
                    # 一次：pathological 情况下线程增速从每小时 6 个降到 1 个，同时
                    # 保证网络一旦恢复最迟一个周期内被发现。
                    last_desperate = ConfigManager._ip_probe_last_desperate_monotonic
                    if (last_desperate is not None
                            and (now - last_desperate) < ConfigManager._IP_PROBE_DESPERATE_INTERVAL_S):
                        return None
                    ConfigManager._ip_probe_last_desperate_monotonic = now
                    print(
                        f"[GeoIP] {len(ConfigManager._wedged_probes)} probes wedged; "
                        "starting a slow-rate replacement anyway so recovery is not missed",
                        file=sys.stderr,
                    )
                print(
                    f"[GeoIP] previous probe stuck for {now - started:.0f}s, starting a replacement",
                    file=sys.stderr,
                )
            last = ConfigManager._ip_check_last_attempt_monotonic
            failures = ConfigManager._ip_check_attempts
            if last is not None and (now - last) < ConfigManager._ip_check_backoff_s(failures):
                return None
            if in_flight is not None and in_flight.is_alive():
                # 记账放在「确定要另起一个」之后，且按身份去重：早记会在下面的退避
                # 闸把本轮拦掉时留下一笔，同一个线程于是被反复计数、虚高到上限。
                if not any(t is in_flight for t in ConfigManager._wedged_probes):
                    ConfigManager._wedged_probes.append(in_flight)
            ConfigManager._ip_check_last_attempt_monotonic = now
            ConfigManager._ip_check_attempts = failures + 1
            ConfigManager._ip_probe_generation += 1
            # daemon：探测永远不该拖住进程退出（最坏挂在 3s 连接超时上）。
            thread = threading.Thread(
                target=ConfigManager._run_ip_probe,
                args=(ConfigManager._ip_probe_generation,),
                name="geoip-probe",
                daemon=True,
            )
            ConfigManager._ip_probe_thread = thread
            ConfigManager._ip_probe_started_monotonic = now
            thread.start()

        return None

    @staticmethod
    def _check_steam_non_mainland():
        """Steam-based IP country check via Steamworks SDK.

        Fallback source only — see _check_non_mainland for why the HTTP probe outranks it.
        """
        # Late-bound: class-level shared state (single owner) lives on the
        # assembled ConfigManager; resolve it through the package facade.
        from utils.config_manager import ConfigManager

        if ConfigManager._steam_check_cache is not None:
            return ConfigManager._steam_check_cache
        try:
            steamworks = get_steamworks()
            if steamworks is None:
                return None
            ip_country = steamworks.Utils.GetIPCountry()
            if isinstance(ip_country, bytes):
                ip_country = ip_country.decode('utf-8')
            if ip_country:
                result = ip_country.upper() != "CN"
                ConfigManager._steam_check_cache = result
                print(f"[GeoIP] Steam IP check: country={ip_country}, non_mainland={result}", file=sys.stderr)
                return result
        except ImportError:
            pass
        except Exception as e:
            print(f"[GeoIP] Steam IP check failed: {e}", file=sys.stderr)
        return None

    def _check_non_mainland(self) -> bool:
        """Region check: HTTP IP geo first, Steam geo only as a fallback.

        This used to require both to say non-mainland. Steamworks stays silent
        forever on non-Steam builds and on Steam builds started without the Steam
        client running — most users only auto-start N.E.K.O., not Steam — so that
        second yes vote never arrived and pinned those overseas users to the
        mainland route.

        IP now decides whenever it has an answer, because it is the more accurate
        of the two: the probe disables proxies explicitly, so a user behind a plain
        system HTTP proxy still geolocates to their real country, while
        ``Utils.GetIPCountry()`` reports whatever exit IP Steam's servers saw — the
        proxy's. Steam only breaks the tie when the probe has no answer at all.
        """
        # Late-bound: class-level shared state (single owner) lives on the
        # assembled ConfigManager; resolve it through the package facade.
        from utils.config_manager import ConfigManager

        # 调试开关：config.GEOIP_FORCE_NON_MAINLAND 非 None 时直接返回它，绕过真实检测。
        # 生产保持 None（走下方判定）。改 config/__init__.py 那个常量即可，不动这里。
        if GEOIP_FORCE_NON_MAINLAND is not None:
            print(
                f"[GeoIP] override active: forcing non-mainland={GEOIP_FORCE_NON_MAINLAND} "
                "(config.GEOIP_FORCE_NON_MAINLAND)",
                file=sys.stderr,
            )
            return GEOIP_FORCE_NON_MAINLAND

        if ConfigManager._region_cache is not None:
            return ConfigManager._region_cache

        ip_result = self._check_ip_non_mainland_http()
        steam_result = self._check_steam_non_mainland()
        if ip_result is None:
            # 探测在后台跑，可能恰好在上面两行之间落地。不复查就会拿 Steam 的
            # 兜底票压过刚到手的权威结论——挂代理时两者方向相反，一个 URL 用 IP、
            # 下一个用 Steam，同一份 core_config 快照内部就自相矛盾了。
            ip_result = ConfigManager._ip_check_cache

        if ip_result is not None:
            ConfigManager._region_cache = ip_result
            ConfigManager._geo_indeterminate_logged = False
            print(f"[GeoIP] IP decides: non_mainland={ip_result} (Steam={steam_result})", file=sys.stderr)
            return ip_result

        if steam_result is not None:
            # IP 探测无结论时才轮到 Steam。它反映的是 Steam 服务端看到的出口 IP，
            # 挂代理时同样会跟着代理走，所以只当兜底票，不当权威票。
            #
            # 刻意**永不写** _region_cache，无论 IP 失败多少次：
            #  - 冷启动首探超时那一刻 Steam 往往已经有票，落定它等于把这一票变成
            #    永久裁决，IP 的退避重试再也没机会接管；
            #  - 「失败够多次就落定」也不行：_ip_check_attempts 在网络 IO 之前递增，
            #    某次探测尚在飞行中时并发调用就会看到阈值达标并落定 Steam，随后那次
            #    探测的成功结论写进 _ip_check_cache 也永远不会被采纳；
            #  - 且探测长期失败不代表 Steam 就对——Steam 走海外代理而直连 GeoIP 暂时
            #    不可用时两者会分歧，网络恢复后必须让 IP 接管。
            # 代价是探测持续失败时每个封顶退避周期再付一次超时，这是有意的取舍。
            if not ConfigManager._geo_steam_fallback_logged:
                ConfigManager._geo_steam_fallback_logged = True
                print(
                    f"[GeoIP] Steam fallback: non_mainland={steam_result} "
                    "(IP has no verdict yet, still retrying)",
                    file=sys.stderr,
                )
            return steam_result

        # No verdict from either source (ip-api.com unreachable AND Steam not yet
        # initialised).  Do NOT write to _region_cache: either may become definitive
        # shortly after this call, and caching False here would permanently suppress
        # re-evaluation.
        # Callers that iterate get_core_config() will simply retry the geo check on the
        # next invocation until at least one source becomes definitive.
        if not ConfigManager._geo_indeterminate_logged:
            ConfigManager._geo_indeterminate_logged = True
            print("[GeoIP] Both sources indeterminate, transient mainland default", file=sys.stderr)
        return False

    # Livestream 派生只接管 free 路这三个已知端点，避免劫持其他 lanlan.tech 路径
    # （例如未来新增 /docs /metrics 之类的非数据端点）
    _LIVESTREAM_DERIVE_PATHS = frozenset({'/core', '/text/v1', '/tts'})

    def _adjust_free_api_url(self, url: str, is_free: bool, non_mainland=None) -> str:
        """Internal URL adjustment for free API users.

        Priority: livestream prefix derivation > overseas lanlan.tech→lanlan.app switch > return as-is.
        When livestream is enabled it only takes over whitelisted free-path endpoints under
        the lanlan.tech domain (/core /text/v1 /tts); other paths go through the original region switch.

        ``non_mainland`` lets a caller rewriting several URLs pass one region verdict
        for all of them. Resolving per URL is not safe: the verdict is not cached
        while it is still provisional, so Steam initialising midway through the loop
        would leave earlier URLs on lanlan.tech and later ones on lanlan.app — one
        snapshot pointing at two regions.
        """
        # Late-bound through the package facade so existing
        # patch("utils.config_manager.<helper>") dotted-path monkeypatches
        # keep intercepting these call sites.
        from utils.config_manager import get_livestream_config, is_livestream_active

        if not url or 'lanlan.tech' not in url:
            return url

        try:
            if is_livestream_active():
                orig_path = urlparse(url).path or ''
                if orig_path in self._LIVESTREAM_DERIVE_PATHS:
                    derived = self._derive_livestream_url(
                        url, get_livestream_config()['server_prefix']
                    )
                    if derived:
                        return derived
        except Exception as e:
            logger.warning(f"Livestream URL 派生失败，回退到原始路径: {e}")

        try:
            if non_mainland if non_mainland is not None else self._check_non_mainland():
                # 海外免费统一走 www.lanlan.app（含 /tts）：该节点透传客户端
                # voice 字段到 Gemini，支持 Gemini 全量 + yui。早期把 /tts 降级到
                # 裸 lanlan.app（硬覆盖 Leda 的旧端点）的 .replace 已移除。
                return url.replace('lanlan.tech', 'lanlan.app')
        except Exception:
            pass

        return url

    def _normalize_agent_url(self, url: str) -> str:
        """Temporarily do not rewrite the Agent URL.

        free-agent-model must use the CN ``lanlan.tech`` text entry from the config; keep
        AGENT_MODEL_URL as-is here to avoid normalizing it to ``lanlan.app``.
        """
        return url

    @staticmethod
    def _derive_livestream_url(original_url: str, prefix: str) -> str:
        """Derive the equivalent address of a lanlan.tech URL from the livestream server_prefix.

        - keeps the original URL's path (``/core`` / ``/tts`` / ``/text/v1``) appended after the prefix path
        - scheme family is unchanged (ws/wss in → ws/wss out; http/https in → http/https out)
        - encryption (the ``s`` suffix) follows the prefix's scheme (https/wss prefix → encrypted output)

        Examples:
        - ``wss://www.lanlan.tech/core`` + ``http://host:port/tok`` → ``ws://host:port/tok/core``
        - ``https://www.lanlan.tech/text/v1`` + ``http://host:port/tok`` → ``http://host:port/tok/text/v1``
        - ``wss://www.lanlan.tech/tts`` + ``https://host/tok`` → ``wss://host/tok/tts``
        """
        if not original_url or not prefix:
            return ''
        try:
            orig = urlparse(original_url)
            pref = urlparse(prefix)
        except Exception:
            return ''
        if not pref.scheme or not pref.netloc:
            return ''

        is_ws_family = orig.scheme in ('ws', 'wss')
        is_secure = pref.scheme in ('https', 'wss')
        if is_ws_family:
            out_scheme = 'wss' if is_secure else 'ws'
        else:
            out_scheme = 'https' if is_secure else 'http'

        base_path = pref.path.rstrip('/')
        return f"{out_scheme}://{pref.netloc}{base_path}{orig.path}"

    @staticmethod
    def _provider_url_candidates(profile: dict, url_key: str, list_key: str) -> list[str]:
        """Read the provider's primary URL and candidate URLs, deduped and order-preserving."""
        raw_candidates = [profile.get(url_key)]
        configured_candidates = profile.get(list_key)
        if isinstance(configured_candidates, list):
            raw_candidates.extend(configured_candidates)
        elif isinstance(configured_candidates, str):
            raw_candidates.append(configured_candidates)

        result = []
        seen = set()
        for raw_url in raw_candidates:
            url = str(raw_url or '').strip()
            if not url or url in seen:
                continue
            seen.add(url)
            result.append(url)
        return result

    def _get_saved_provider_url(
        self,
        core_cfg: dict,
        scope: str,
        provider_key: str,
        profile: dict,
        url_key: str,
        list_key: str,
    ) -> str:
        """Return the URL saved by the connectivity test that still belongs to the current provider candidate set."""
        resolved_urls = core_cfg.get('resolvedProviderUrls')
        if not isinstance(resolved_urls, dict):
            return ''
        saved_url = str(resolved_urls.get(f'{scope}:{provider_key}') or '').strip()
        if not saved_url:
            return ''
        candidates = set(self._provider_url_candidates(profile, url_key, list_key))
        return saved_url if saved_url in candidates else ''

    def get_core_config(self):
        """Read core config dynamically"""
        # Late-bound through the package facade so existing
        # patch("utils.config_manager.<helper>") dotted-path monkeypatches
        # keep intercepting these call sites.
        from utils.config_manager import (
            get_assist_api_key_fields,
            get_assist_api_profiles,
            get_core_api_profiles,
        )

        # 从 config 模块导入所有默认配置值
        from config import (
            DEFAULT_CORE_API_KEY,
            DEFAULT_AUDIO_API_KEY,
            DEFAULT_OPENROUTER_API_KEY,
            DEFAULT_MCP_ROUTER_API_KEY,
            DEFAULT_CORE_URL,
            DEFAULT_CORE_MODEL,
            DEFAULT_OPENROUTER_URL,
            DEFAULT_CONVERSATION_MODEL,
            DEFAULT_SUMMARY_MODEL,
            DEFAULT_CORRECTION_MODEL,
            DEFAULT_EMOTION_MODEL,
            DEFAULT_VISION_MODEL,
            DEFAULT_REALTIME_MODEL,
            DEFAULT_TTS_MODEL,
            DEFAULT_AGENT_MODEL,
            DEFAULT_CONVERSATION_MODEL_URL,
            DEFAULT_CONVERSATION_MODEL_API_KEY,
            DEFAULT_SUMMARY_MODEL_URL,
            DEFAULT_SUMMARY_MODEL_API_KEY,
            DEFAULT_CORRECTION_MODEL_URL,
            DEFAULT_CORRECTION_MODEL_API_KEY,
            DEFAULT_EMOTION_MODEL_URL,
            DEFAULT_EMOTION_MODEL_API_KEY,
            DEFAULT_VISION_MODEL_URL,
            DEFAULT_VISION_MODEL_API_KEY,
            DEFAULT_AGENT_MODEL_URL,
            DEFAULT_AGENT_MODEL_API_KEY,
            DEFAULT_REALTIME_MODEL_URL,
            DEFAULT_REALTIME_MODEL_API_KEY,
            DEFAULT_TTS_MODEL_URL,
            DEFAULT_TTS_MODEL_API_KEY,
        )

        config = {
            'CORE_API_KEY': DEFAULT_CORE_API_KEY,
            'AUDIO_API_KEY': DEFAULT_AUDIO_API_KEY,
            'OPENROUTER_API_KEY': DEFAULT_OPENROUTER_API_KEY,
            'MCP_ROUTER_API_KEY': DEFAULT_MCP_ROUTER_API_KEY,
            'CORE_URL': DEFAULT_CORE_URL,
            'CORE_MODEL': DEFAULT_CORE_MODEL,
            'CORE_API_TYPE': 'qwen',
            'OPENROUTER_URL': DEFAULT_OPENROUTER_URL,
            'CONVERSATION_MODEL': DEFAULT_CONVERSATION_MODEL,
            'SUMMARY_MODEL': DEFAULT_SUMMARY_MODEL,
            'GAME_MAIN_MODEL': DEFAULT_CONVERSATION_MODEL,
            'GAME_SUMMARY_MODEL': DEFAULT_SUMMARY_MODEL,
            'CORRECTION_MODEL': DEFAULT_CORRECTION_MODEL,
            'EMOTION_MODEL': DEFAULT_EMOTION_MODEL,
            'ASSIST_API_KEY_QWEN': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_OPENAI': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_GLM': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_STEP': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_SILICON': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_GEMINI': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_KIMI': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_KIMI_CODE': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_DEEPSEEK': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_DOUBAO': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_DOUBAO_TTS': '',
            'ASSIST_API_KEY_QWEN_INTL': '',
            'ASSIST_API_KEY_MINIMAX': '',
            'ASSIST_API_KEY_MINIMAX_INTL': '',
            'ASSIST_API_KEY_MIMO': '',
            'ASSIST_API_KEY_MIMO_TOKEN_PLAN': '',
            'ASSIST_API_KEY_GROK': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_OPENROUTER': DEFAULT_CORE_API_KEY,
            'VISION_MODEL': DEFAULT_VISION_MODEL,
            'AGENT_MODEL': DEFAULT_AGENT_MODEL,
            'REALTIME_MODEL': DEFAULT_REALTIME_MODEL,
            'TTS_MODEL': DEFAULT_TTS_MODEL,
            'CONVERSATION_MODEL_URL': DEFAULT_CONVERSATION_MODEL_URL,
            'CONVERSATION_MODEL_API_KEY': DEFAULT_CONVERSATION_MODEL_API_KEY,
            'SUMMARY_MODEL_URL': DEFAULT_SUMMARY_MODEL_URL,
            'SUMMARY_MODEL_API_KEY': DEFAULT_SUMMARY_MODEL_API_KEY,
            'GAME_MAIN_MODEL_URL': DEFAULT_CONVERSATION_MODEL_URL,
            'GAME_MAIN_MODEL_API_KEY': DEFAULT_CONVERSATION_MODEL_API_KEY,
            'GAME_SUMMARY_MODEL_URL': DEFAULT_SUMMARY_MODEL_URL,
            'GAME_SUMMARY_MODEL_API_KEY': DEFAULT_SUMMARY_MODEL_API_KEY,
            'CORRECTION_MODEL_URL': DEFAULT_CORRECTION_MODEL_URL,
            'CORRECTION_MODEL_API_KEY': DEFAULT_CORRECTION_MODEL_API_KEY,
            'EMOTION_MODEL_URL': DEFAULT_EMOTION_MODEL_URL,
            'EMOTION_MODEL_API_KEY': DEFAULT_EMOTION_MODEL_API_KEY,
            'VISION_MODEL_URL': DEFAULT_VISION_MODEL_URL,
            'VISION_MODEL_API_KEY': DEFAULT_VISION_MODEL_API_KEY,
            'AGENT_MODEL_URL': DEFAULT_AGENT_MODEL_URL,
            'AGENT_MODEL_API_KEY': DEFAULT_AGENT_MODEL_API_KEY,
            'REALTIME_MODEL_URL': DEFAULT_REALTIME_MODEL_URL,
            'REALTIME_MODEL_API_KEY': DEFAULT_REALTIME_MODEL_API_KEY,
            'TTS_MODEL_URL': DEFAULT_TTS_MODEL_URL,
            'TTS_MODEL_API_KEY': DEFAULT_TTS_MODEL_API_KEY,
            'OPENCLAW_URL': "http://127.0.0.1:8088",
            'OPENCLAW_TIMEOUT': 300.0,
            'OPENCLAW_DEFAULT_SENDER_ID': "neko_user",
        }

        core_cfg = deepcopy(DEFAULT_CONFIG_DATA['core_config.json'])

        try:
            with open(str(self.get_config_path('core_config.json')), 'r', encoding='utf-8') as f:
                file_data = json.load(f)
            if isinstance(file_data, dict):
                core_cfg.update(file_data)
                # 模板默认 assistApi='qwen' 会把文件里从未保存过的 assistApi 填
                # 上，导致下方「core=free 时 assist 默认 free」的跟随逻辑收不到
                # 缺失信号。仅当文件显式选了 core=free 且从未保存 assistApi 时
                # 恢复跟随语义；其余缺失场景维持模板默认。
                if 'assistApi' not in file_data and file_data.get('coreApi') == 'free':
                    core_cfg['assistApi'] = 'free'
            else:
                logger.warning("core_config.json 格式异常，使用默认配置。")

        except FileNotFoundError:
            logger.info("未找到 core_config.json，使用默认配置。")
        except Exception as e:
            logger.error("Error parsing Core API Key: %s", e)
        finally:
            if not isinstance(core_cfg, dict):
                core_cfg = deepcopy(DEFAULT_CONFIG_DATA['core_config.json'])
        config['RESOLVED_PROVIDER_URLS'] = (
            dict(core_cfg.get('resolvedProviderUrls'))
            if isinstance(core_cfg.get('resolvedProviderUrls'), dict)
            else {}
        )

        # API Keys — 仅对与 coreApi/assistApi 匹配的服务商回退到 CORE_API_KEY
        if core_cfg.get('coreApiKey'):
            config['CORE_API_KEY'] = core_cfg['coreApiKey']

        _core_api_provider = core_cfg.get('coreApi') or config['CORE_API_TYPE']
        _assist_api_provider = core_cfg.get('assistApi')
        if not _assist_api_provider:
            _assist_api_provider = 'free' if _core_api_provider == 'free' else 'qwen'
        _fallback_providers = {_core_api_provider, _assist_api_provider}
        _core_key_fallback = config['CORE_API_KEY'] if config['CORE_API_KEY'] != 'free-access' else ''

        def _fb(provider: str) -> str:
            return _core_key_fallback if provider in _fallback_providers else ''

        config['ASSIST_API_KEY_QWEN'] = core_cfg.get('assistApiKeyQwen', '') or _fb('qwen')
        config['ASSIST_API_KEY_QWEN_INTL'] = core_cfg.get('assistApiKeyQwenIntl', '') or _fb('qwen_intl')
        config['ASSIST_API_KEY_OPENAI'] = core_cfg.get('assistApiKeyOpenai', '') or _fb('openai')
        config['ASSIST_API_KEY_GLM'] = core_cfg.get('assistApiKeyGlm', '') or _fb('glm')
        config['ASSIST_API_KEY_STEP'] = core_cfg.get('assistApiKeyStep', '') or _fb('step')
        config['ASSIST_API_KEY_SILICON'] = core_cfg.get('assistApiKeySilicon', '') or _fb('silicon')
        config['ASSIST_API_KEY_GEMINI'] = core_cfg.get('assistApiKeyGemini', '') or _fb('gemini')
        config['ASSIST_API_KEY_KIMI'] = core_cfg.get('assistApiKeyKimi', '') or _fb('kimi')
        config['ASSIST_API_KEY_KIMI_CODE'] = core_cfg.get('assistApiKeyKimiCode', '') or _fb('kimi_code')
        config['ASSIST_API_KEY_DEEPSEEK'] = core_cfg.get('assistApiKeyDeepseek', '') or _fb('deepseek')
        config['ASSIST_API_KEY_DOUBAO'] = core_cfg.get('assistApiKeyDoubao', '') or _fb('doubao')
        config['ASSIST_API_KEY_DOUBAO_TTS'] = core_cfg.get('assistApiKeyDoubaoTts', '')
        # MiniMax / MiMo 是 assist-only TTS provider，coreApiKey 不保证兼容；
        # 不 fallback，以免把无效 key 塞进 TTS 凭证槽位导致 401，
        # 掩盖"未配置 TTS provider key"的真实提示。
        config['ASSIST_API_KEY_MINIMAX'] = core_cfg.get('assistApiKeyMinimax', '')
        config['ASSIST_API_KEY_MINIMAX_INTL'] = core_cfg.get('assistApiKeyMinimaxIntl', '')
        config['ASSIST_API_KEY_MIMO'] = core_cfg.get('assistApiKeyMimo', '')
        config['ASSIST_API_KEY_MIMO_TOKEN_PLAN'] = core_cfg.get('assistApiKeyMimoTokenPlan', '')
        config['useMimoTokenPlan'] = _as_bool(core_cfg.get('useMimoTokenPlan', False))
        config['ASSIST_API_KEY_ELEVENLABS'] = core_cfg.get('assistApiKeyElevenlabs', '')
        config['ASSIST_API_KEY_GROK'] = core_cfg.get('assistApiKeyGrok', '') or _fb('grok')
        config['ASSIST_API_KEY_CLAUDE'] = core_cfg.get('assistApiKeyClaude', '') or _fb('claude')
        config['ASSIST_API_KEY_OPENROUTER'] = core_cfg.get('assistApiKeyOpenrouter', '') or _fb('openrouter')

        if core_cfg.get('mcpToken'):
            config['MCP_ROUTER_API_KEY'] = core_cfg['mcpToken']

        openclaw_url = core_cfg.get('openclawUrl')
        if isinstance(openclaw_url, str) and openclaw_url.strip():
            normalized_openclaw_url = openclaw_url.strip().rstrip('/')
            try:
                parsed_openclaw_url = urlparse(normalized_openclaw_url)
            except Exception:
                parsed_openclaw_url = None
            if parsed_openclaw_url and parsed_openclaw_url.netloc:
                try:
                    if parsed_openclaw_url.port == 8089:
                        host = parsed_openclaw_url.hostname or ""
                        if ":" in host and not host.startswith("["):
                            host = f"[{host}]"
                        userinfo = ""
                        if parsed_openclaw_url.username:
                            userinfo = parsed_openclaw_url.username
                            if parsed_openclaw_url.password:
                                userinfo += f":{parsed_openclaw_url.password}"
                            userinfo += "@"
                        migrated_openclaw_url = urlunparse(
                            parsed_openclaw_url._replace(netloc=f"{userinfo}{host}:8088")
                        )
                        core_cfg['openclawUrl'] = migrated_openclaw_url
                        openclaw_url = migrated_openclaw_url
                        try:
                            self.save_json_config('core_config.json', core_cfg)
                            logger.info("已自动将 openclawUrl 从 8089 迁移到 8088: %s", migrated_openclaw_url)
                        except Exception as exc:
                            logger.warning("自动迁移 openclawUrl 到 8088 失败: %s", exc)
                except ValueError:
                    pass
        if isinstance(openclaw_url, str) and openclaw_url.strip():
            config['OPENCLAW_URL'] = openclaw_url.strip()
        try:
            openclaw_timeout = core_cfg.get('openclawTimeout', config['OPENCLAW_TIMEOUT'])
            openclaw_timeout = float(openclaw_timeout)
            if not math.isfinite(openclaw_timeout) or openclaw_timeout <= 0:
                raise ValueError("openclawTimeout must be a positive finite number")
            config['OPENCLAW_TIMEOUT'] = openclaw_timeout
        except (TypeError, ValueError):
            config['OPENCLAW_TIMEOUT'] = 300.0
        openclaw_sender = core_cfg.get('openclawDefaultSenderId')
        if isinstance(openclaw_sender, str) and openclaw_sender.strip():
            config['OPENCLAW_DEFAULT_SENDER_ID'] = openclaw_sender.strip()

        core_api_profiles = get_core_api_profiles()
        assist_api_profiles = get_assist_api_profiles()
        assist_api_key_fields = get_assist_api_key_fields()

        # Core API profile
        core_api_value = core_cfg.get('coreApi') or config['CORE_API_TYPE']
        config['CORE_API_TYPE'] = core_api_value
        core_profile = core_api_profiles.get(core_api_value)
        if core_profile:
            config.update(core_profile)
            resolved_core_url = self._get_saved_provider_url(
                core_cfg, 'core', core_api_value, core_profile, 'CORE_URL', 'CORE_URLS'
            )
            if resolved_core_url:
                config['CORE_URL'] = resolved_core_url

        # Assist API profile
        # 显式选择的 assistApi 一律被尊重，即使 coreApi=free。这样用户可以组合
        # 「免费实时（core=free）+ 付费文本/Agent（assist=qwen 等）」——免费 realtime
        # 端点和付费 assist 端点是独立的两条链路，没有理由把后者绑死在 free 上。
        # 仅当用户没有显式选 assist 时，沿用 coreApi 的偏好做默认：core=free 默认 free，
        # 其他默认 qwen。
        assist_api_value = core_cfg.get('assistApi')
        if not assist_api_value:
            assist_api_value = 'free' if core_api_value == 'free' else 'qwen'

        config['assistApi'] = assist_api_value

        assist_profile = assist_api_profiles.get(assist_api_value)
        if not assist_profile and assist_api_value != 'qwen':
            logger.warning("未知的 assistApi '%s'，回退到 qwen。", assist_api_value)
            assist_api_value = 'qwen'
            config['assistApi'] = assist_api_value
            assist_profile = assist_api_profiles.get(assist_api_value)

        if assist_profile:
            config.update(assist_profile)
            resolved_assist_url = self._get_saved_provider_url(
                core_cfg, 'assist', assist_api_value, assist_profile, 'OPENROUTER_URL', 'OPENROUTER_URLS'
            )
            if resolved_assist_url:
                config['OPENROUTER_URL'] = resolved_assist_url
        use_mimo_token_plan = (
            assist_api_value == 'mimo'
            and _as_bool(core_cfg.get('useMimoTokenPlan', False))
        )
        if use_mimo_token_plan:
            token_plan_urls = config.get('MIMO_TOKEN_PLAN_OPENROUTER_URLS')
            if not isinstance(token_plan_urls, list):
                token_plan_urls = []
            token_plan_profile = {
                'OPENROUTER_URL': config.get(
                    'MIMO_TOKEN_PLAN_OPENROUTER_URL',
                    'https://token-plan-cn.xiaomimimo.com/v1',
                ),
                'OPENROUTER_URLS': token_plan_urls or [
                    'https://token-plan-cn.xiaomimimo.com/v1',
                    'https://token-plan-sgp.xiaomimimo.com/v1',
                    'https://token-plan-ams.xiaomimimo.com/v1',
                ],
            }
            token_plan_url = self._get_saved_provider_url(
                core_cfg,
                'assist',
                'mimo_token_plan',
                token_plan_profile,
                'OPENROUTER_URL',
                'OPENROUTER_URLS',
            )
            config['OPENROUTER_URL'] = token_plan_url or token_plan_profile['OPENROUTER_URL']
        # agent api 默认跟随辅助 API 的 agent_model，缺失时回退到 VISION_MODEL
        config['AGENT_MODEL'] = config.get('AGENT_MODEL') or config.get('VISION_MODEL', '')
        config['AGENT_MODEL_URL'] = config.get('AGENT_MODEL_URL') or config.get('VISION_MODEL_URL', '') or config.get('OPENROUTER_URL', '')
        config['AGENT_MODEL_URL'] = self._normalize_agent_url(config['AGENT_MODEL_URL'])

        key_field = (
            'ASSIST_API_KEY_MIMO_TOKEN_PLAN'
            if use_mimo_token_plan
            else assist_api_key_fields.get(assist_api_value)
        )
        derived_key = ''
        if key_field:
            derived_key = config.get(key_field, '')
            if derived_key:
                config['AUDIO_API_KEY'] = derived_key
                config['OPENROUTER_API_KEY'] = derived_key

        if not config['AUDIO_API_KEY']:
            config['AUDIO_API_KEY'] = _core_key_fallback
        if not config['OPENROUTER_API_KEY']:
            config['OPENROUTER_API_KEY'] = _core_key_fallback

        # Agent API Key 回退：未显式配置时跟随辅助 API Key
        if not config.get('AGENT_MODEL_API_KEY'):
            config['AGENT_MODEL_API_KEY'] = config.get('OPENROUTER_API_KEY', '')

        # 自定义API配置映射（使用大写下划线形式的内部键，且在未提供时保留已有默认值）
        enable_custom_api = core_cfg.get('enableCustomApi', False)
        config['ENABLE_CUSTOM_API'] = enable_custom_api

        # GPT-SoVITS「是否启用」收口到 ttsModelProvider 下拉这单一真相（见
        # docs/design/tts-voice-source-unification.md §3/§4）。choke-point 在此派生，
        # 13 个下游读点（core.py 路由 / 本文件 URL 解析与 TTS_VOICE_ID override /
        # get_model_api_config 的 is_custom 自愈 / 各 router）以及 worker 的
        # _gptsovits_is_selected 全部沿用 GPTSOVITS_ENABLED 不变。
        # 派生语义：ttsModelProvider 一旦「显式选了某个 TTS provider」即唯一真相——选中
        # gptsovits 才启用、选别家（vllm_omni/mimo/custom…）就关，旧 gptsovitsEnabled 不参与。
        # 仅当未显式选择时（provider 缺失/空串，或 follow_assist/follow_core 这两个「跟随
        # assist/core」哨兵——它们是各 model provider 下拉的默认值，等同未选）才回落旧开关，
        # 兜住 pre-#1830 存量（用 checkbox 开 GSV、下拉仍停在默认 follow_* 的用户）。
        # ⚠️ 不能把 follow_* 当显式 provider，否则存量 GSV 用户（gptsovitsEnabled=true +
        # ttsModelProvider='follow_assist'）会被误判为关 → 升级后 GSV 失效（Codex PR#1850 P1）。
        # 刻意不用 `旧flag OR 下拉` 的纯 OR：前端已退役 gptsovitsEnabled 写路径，旧 flag 在
        # 增量合并下会粘住；显式切走由「下拉为准」关掉，follow_* 切走由 config_router 的
        # save choke point 惰性落 False 清掉（见该处注释）。
        _tts_model_provider = str(core_cfg.get('ttsModelProvider', '') or '').strip()
        if _tts_model_provider in ('', 'follow_assist', 'follow_core'):
            config['GPTSOVITS_ENABLED'] = _as_bool(core_cfg.get('gptsovitsEnabled', False))
        else:
            config['GPTSOVITS_ENABLED'] = (_tts_model_provider == 'gptsovits')

        config['ELEVENLABS_API_KEY'] = core_cfg.get('assistApiKeyElevenlabs', '')
        config['TTS_PROVIDER'] = core_cfg.get('ttsProvider', '')

        # 将 vLLM-Omni TTS 的前端原始字段放进 core_config snapshot，供
        # core.py 判断是否启用外部 TTS，并生成与实际 worker 参数一致的复用 key。
        # 凭证字段 ttsModelApiKey 不放入 snapshot；它仍由 tts_client.py 从持久化
        # 配置读取，避免扩大通用配置快照中的敏感字段范围。
        for _model_provider_prefix in (
            'conversation', 'summary', 'gameMain', 'gameSummary', 'correction',
            'emotion', 'vision', 'agent', 'omni', 'tts',
        ):
            config[f'{_model_provider_prefix}ModelProvider'] = str(
                core_cfg.get(f'{_model_provider_prefix}ModelProvider', '') or ''
            )
        config['ttsModelUrl'] = str(core_cfg.get('ttsModelUrl', '') or '')
        config['ttsModelId'] = str(core_cfg.get('ttsModelId', '') or '')
        config['ttsVoiceId'] = str(core_cfg.get('ttsVoiceId', '') or '')

        # 禁用TTS
        _raw_disable_tts = core_cfg.get('disableTts', False)
        if isinstance(_raw_disable_tts, bool):
            config['DISABLE_TTS'] = _raw_disable_tts
        elif isinstance(_raw_disable_tts, str):
            config['DISABLE_TTS'] = _raw_disable_tts.lower() in ('true', '1', 'yes', 'on')
        else:
            config['DISABLE_TTS'] = False

        # 文本模式回复长度守卫上限（tiktoken o200k_base tokens，超限触发 reroll；
        # reroll 耗尽后回退到最后一个句末标点截断后落定）
        try:
            config['TEXT_GUARD_MAX_LENGTH'] = int(core_cfg.get('textGuardMaxLength', 300))
            if config['TEXT_GUARD_MAX_LENGTH'] <= 0:
                config['TEXT_GUARD_MAX_LENGTH'] = 300
        except (TypeError, ValueError):
            config['TEXT_GUARD_MAX_LENGTH'] = 300
        
        # GPT-SoVITS 是本地 TTS 运行时，不依赖 enableCustomApi 总开关。用户
        # 保存的 ttsModelUrl 是 GSV server URL，不能被 follow_core/follow_assist
        # 的 LLM URL 覆盖；空值只在运行时默认到 127.0.0.1，不写回配置文件。
        if config['GPTSOVITS_ENABLED']:
            config['TTS_MODEL_URL'] = normalize_gsv_api_url(core_cfg.get('ttsModelUrl'))

        # 只有在启用自定义API时才允许覆盖各模型相关字段
        if enable_custom_api:
            # URL / Model ID 字段：空值回退到已有配置。
            # API Key 字段：根据用户选择的 provider 决定是否覆盖：
            #   - follow_core / follow_assist / ''（老配置无此字段）→ 保留上方派生的值
            #   - 具体服务商或 'custom' → 允许覆盖（空串合法，本地服务商可能不需要 key）
            def _resolve_follow_model_url(prefix: str, provider: str) -> str:
                """Recompute follow_* URLs for the current provider, avoiding stale regions saved historically."""
                if provider == 'follow_assist':
                    return config.get('OPENROUTER_URL', '')
                if provider == 'follow_conversation':
                    return config.get('CONVERSATION_MODEL_URL', '') or config.get('OPENROUTER_URL', '')
                if provider == 'follow_summary':
                    return config.get('SUMMARY_MODEL_URL', '') or config.get('OPENROUTER_URL', '')
                if provider != 'follow_core':
                    return ''

                if prefix == 'omni':
                    return config.get('CORE_URL', '')

                follow_core_profile = assist_api_profiles.get(core_api_value)
                if isinstance(follow_core_profile, dict):
                    resolved_url = self._get_saved_provider_url(
                        core_cfg,
                        'assist',
                        core_api_value,
                        follow_core_profile,
                        'OPENROUTER_URL',
                        'OPENROUTER_URLS',
                    )
                    return resolved_url or follow_core_profile.get('OPENROUTER_URL', '')

                if isinstance(core_profile, dict):
                    resolved_url = self._get_saved_provider_url(
                        core_cfg,
                        'core',
                        core_api_value,
                        core_profile,
                        'CORE_URL',
                        'CORE_URLS',
                    )
                    return resolved_url or core_profile.get('CORE_URL', '')
                return ''

            def _resolve_game_follow_model_id(prefix: str, provider: str) -> str:
                if prefix not in ('gameMain', 'gameSummary'):
                    return ''
                if provider == 'follow_core':
                    follow_core_profile = assist_api_profiles.get(core_api_value)
                    if isinstance(follow_core_profile, dict):
                        if prefix == 'gameSummary':
                            return follow_core_profile.get('SUMMARY_MODEL', '') or config.get('SUMMARY_MODEL', '')
                        return follow_core_profile.get('CONVERSATION_MODEL', '') or config.get('CONVERSATION_MODEL', '')
                    return config.get('CORE_MODEL', '')
                if provider != 'follow_assist':
                    return ''
                if prefix == 'gameSummary':
                    return config.get('SUMMARY_MODEL', '')
                return config.get('CONVERSATION_MODEL', '')

            _custom_api_fields = [
                # (前端字段前缀, 模型config键, URL config键, API Key config键)
                ('conversation', 'CONVERSATION_MODEL', 'CONVERSATION_MODEL_URL', 'CONVERSATION_MODEL_API_KEY'),
                ('summary',      'SUMMARY_MODEL',      'SUMMARY_MODEL_URL',      'SUMMARY_MODEL_API_KEY'),
                ('gameMain',     'GAME_MAIN_MODEL',    'GAME_MAIN_MODEL_URL',    'GAME_MAIN_MODEL_API_KEY'),
                ('gameSummary',  'GAME_SUMMARY_MODEL', 'GAME_SUMMARY_MODEL_URL', 'GAME_SUMMARY_MODEL_API_KEY'),
                ('correction',   'CORRECTION_MODEL',    'CORRECTION_MODEL_URL',   'CORRECTION_MODEL_API_KEY'),
                ('emotion',      'EMOTION_MODEL',       'EMOTION_MODEL_URL',      'EMOTION_MODEL_API_KEY'),
                ('vision',       'VISION_MODEL',        'VISION_MODEL_URL',       'VISION_MODEL_API_KEY'),
                ('agent',        'AGENT_MODEL',         'AGENT_MODEL_URL',        'AGENT_MODEL_API_KEY'),
                ('omni',         'REALTIME_MODEL',      'REALTIME_MODEL_URL',     'REALTIME_MODEL_API_KEY'),
                ('tts',          'TTS_MODEL',           'TTS_MODEL_URL',          'TTS_MODEL_API_KEY'),
            ]
            for prefix, model_key, url_key, apikey_key in _custom_api_fields:
                provider = core_cfg.get(f'{prefix}ModelProvider', '')
                # follow_core / follow_assist 的 URL 是前端联动 readonly 自填的提示值
                # （static/js/api_key_settings.js: onCustomModelProviderChange），不代表
                # 用户选择"自定义部署"。但只在 omni/tts 才会出问题：
                #   - omni: get_model_api_config 看见 REALTIME_MODEL+_URL 都非空 →
                #     强行 api_type='local'（TODO 未实现）→ core_api_type='local' →
                #     TTS 调度落 dummy_tts_worker → 静音
                #   - tts:  TTS_MODEL_URL 被联动值污染让 tts_custom 走错 provider
                # 其他 model type（conversation/summary/correction/emotion/vision/agent）
                # 走 chat completion REST，没有 'local' 分支；跳 URL 反而会改变它们的
                # follow_* 路由（详见 PR #1084 review thread），故仅对 omni/tts 跳。
                # 注：follow_* 下 omni/tts 仍不能依赖 get_model_api_config fallback
                # 读取用户填的 modelId（fallback 用 CORE_MODEL，不是 REALTIME_MODEL/TTS_MODEL），
                # 因此这些特殊前缀继续走既有 follow 解析。
                is_follow = provider in ('follow_core', 'follow_assist', 'follow_conversation', 'follow_summary')
                # GSV 启用时 ttsModelUrl 是 GPT-SoVITS server URL，不是 follow_*
                # 联动出来的 LLM URL。即便 ttsModelProvider 仍是默认 follow_assist，
                # 也必须优先保留 GSV URL，否则对话 TTS 会连到辅助 LLM endpoint。
                gsv_enabled_for_url = config['GPTSOVITS_ENABLED']
                gsv_tts_url_override = prefix == 'tts' and gsv_enabled_for_url
                skip_url_for_follow = (
                    is_follow
                    and prefix in ('omni', 'tts')
                    and not gsv_tts_url_override
                )

                # URL: 空值回退到已有配置；omni/tts follow_* 时跳过
                cfg_url = core_cfg.get(f'{prefix}ModelUrl')
                if gsv_tts_url_override:
                    config[url_key] = normalize_gsv_api_url(cfg_url or config.get(url_key))
                elif not skip_url_for_follow:
                    if is_follow:
                        followed_url = _resolve_follow_model_url(prefix, provider)
                        if followed_url:
                            config[url_key] = followed_url
                    else:
                        if cfg_url is not None:
                            config[url_key] = cfg_url or config.get(url_key, '')

                # Model ID: 空值回退到已有配置
                cfg_model = core_cfg.get(f'{prefix}ModelId')
                if provider == 'follow_conversation':
                    config[model_key] = config.get('CONVERSATION_MODEL', '')
                elif provider == 'follow_summary':
                    config[model_key] = config.get('SUMMARY_MODEL', '')
                elif provider in ('follow_core', 'follow_assist'):
                    uses_fixed_free_assist_model = provider == 'follow_assist' and assist_api_value == 'free'
                    if (
                        not uses_fixed_free_assist_model
                        and
                        prefix not in ('gameMain', 'gameSummary', 'omni', 'tts')
                        and isinstance(cfg_model, str)
                        and cfg_model.strip()
                    ):
                        config[model_key] = cfg_model.strip()
                    else:
                        followed_model = _resolve_game_follow_model_id(prefix, provider)
                        if followed_model:
                            config[model_key] = followed_model
                elif cfg_model is not None:
                    config[model_key] = cfg_model or config.get(model_key, '')

                # API Key 处理：
                #   follow_core   → 从核心 API Key 派生
                #   follow_assist → 从辅助 API Key 派生（OPENROUTER_API_KEY 已含 assist→core 回退）
                #   具体服务商/custom/''(老配置) → 使用存储值（空串合法，本地服务商不需要 key）
                #
                # GSV 启用 + prefix='tts' + ttsModelProvider 默认 'follow_*' 时跳过派生：
                # 派生会把 TTS_MODEL_API_KEY 写成 OPENROUTER_API_KEY / CORE_API_KEY（这俩是
                # LLM key，可能是 Gemini / DeepSeek 等），随后 get_model_api_config('tts_custom')
                # 的 is_gsv_url 分支会原样返回这个无关 key；get_tts_api_key('cosyvoice') 因此
                # 拿到错的 key，CosyVoice clone 鉴权失败。跳过后 TTS_MODEL_API_KEY 保留其持久化
                # 值（用户开 GSV 一般不会同时填这个字段，留空即可），让下游 is_gsv_url 分支的
                # ASSIST_API_KEY_QWEN fallback 接手。
                skip_key_for_follow_gsv = (
                    is_follow
                    and prefix == 'tts'
                    and gsv_enabled_for_url
                )
                if provider == 'follow_core':
                    if not skip_key_for_follow_gsv:
                        config[apikey_key] = config.get('CORE_API_KEY', '')
                elif provider == 'follow_assist':
                    if not skip_key_for_follow_gsv:
                        config[apikey_key] = config.get('OPENROUTER_API_KEY', '')
                elif provider == 'follow_conversation':
                    config[apikey_key] = config.get('CONVERSATION_MODEL_API_KEY', '')
                elif provider == 'follow_summary':
                    config[apikey_key] = config.get('SUMMARY_MODEL_API_KEY', '')
                else:
                    cfg_key = core_cfg.get(f'{prefix}ModelApiKey')
                    if cfg_key is not None:
                        config[apikey_key] = cfg_key

            # TTS Voice ID 作为角色 voice_id 的回退
            if core_cfg.get('ttsVoiceId') is not None:
                config['TTS_VOICE_ID'] = core_cfg.get('ttsVoiceId', '')

        if config['GPTSOVITS_ENABLED'] and core_cfg.get('ttsVoiceId') is not None:
            config['TTS_VOICE_ID'] = core_cfg.get('ttsVoiceId', '')

        # 整份快照共用一次区域判定：判定尚未落定时它每次都会重算，Steam 若在循环
        # 中途初始化完成，前面的 URL 会停在 lanlan.tech、后面的却变成 lanlan.app，
        # 同一份 core_config 指向两个区域。
        #
        # 但提到循环外之前必须先确认「确实有 URL 需要它」：区域判定会发起 GeoIP
        # 探测，而免费路由门原本就长在 _adjust_free_api_url 的首行早退里
        # （不变量 #2）。无条件判定 = 自配 API 用户也把 IP 发给第三方。
        needs_region = any(
            key.endswith('_URL') and isinstance(value, str) and 'lanlan.tech' in value
            for key, value in config.items()
        )
        snapshot_non_mainland = False
        if needs_region:
            try:
                snapshot_non_mainland = self._check_non_mainland()
            except Exception:
                snapshot_non_mainland = False
        for key, value in config.items():
            if key.endswith('_URL') and isinstance(value, str):
                config[key] = self._adjust_free_api_url(
                    value, True, non_mainland=snapshot_non_mainland,
                )

        # Agent model always uses international API regardless of region
        if isinstance(config.get('AGENT_MODEL_URL'), str):
            config['AGENT_MODEL_URL'] = self._normalize_agent_url(config['AGENT_MODEL_URL'])

        return config

    def get_model_api_config(self, model_type: str) -> dict:
        """
        Get the API config for the given model type (automatically handling custom API priority)
        
        Args:
            model_type: model type, one of:
                - 'summary': summary model (falls back to assist API)
                - 'correction': correction model (falls back to assist API)
                - 'emotion': emotion analysis model (falls back to assist API)
                - 'vision': vision model (falls back to assist API)
                - 'realtime': realtime speech model (falls back to core API)
                - 'tts_default': default TTS (falls back to core API, used by OmniOfflineClient)
                - 'tts_custom': custom TTS (falls back to assist API, used for voice_id scenarios)
                
        Returns:
            dict: config containing:
                - 'model': model name
                - 'api_key': API key
                - 'base_url': API endpoint URL
                - 'is_custom': whether a custom API config is used
        """
        # Late-bound through the package facade so existing
        # patch("utils.config_manager.<helper>") dotted-path monkeypatches
        # keep intercepting these call sites (incl. the nested provider-type
        # resolvers and the tts_custom Qwen-profile fallback below).
        from utils.config_manager import get_assist_api_profiles, get_core_api_profiles

        core_config = self.get_core_config()
        enable_custom_api = core_config.get('ENABLE_CUSTOM_API', False)

        # GPT-SoVITS 启用时，tts_custom slot 视为自定义 API：UI 上勾 GSV 在产品语义上
        # 就是 "启用一个自定义 TTS"，但前端 (api_key_settings.js) 并不会顺手把
        # ENABLE_CUSTOM_API 也勾上。后端这里自愈，避免 "勾了 GSV 但没勾 ENABLE_CUSTOM_API"
        # 这条用户极易踩中的路径让 is_custom=False、整条 GSV 链路（dispatcher /
        # check_custom_tts_voice_allowed / /custom_tts_voices）全部失效。
        # 仅扩到 tts_custom，不影响其他 slot 的开关行为。
        gsv_enabled_for_tts = (
            model_type == 'tts_custom'
            and core_config.get('GPTSOVITS_ENABLED', False)
        )
        treat_as_custom = enable_custom_api or gsv_enabled_for_tts

        # 模型类型到配置字段的映射
        # fallback_type: 'assist' = 辅助API, 'core' = 核心API
        model_type_mapping = {
            'conversation': {
                'custom_model': 'CONVERSATION_MODEL',
                'custom_url': 'CONVERSATION_MODEL_URL',
                'custom_key': 'CONVERSATION_MODEL_API_KEY',
                'default_model': 'CONVERSATION_MODEL',
                'fallback_type': 'assist',
            },
            'summary': {
                'custom_model': 'SUMMARY_MODEL',
                'custom_url': 'SUMMARY_MODEL_URL',
                'custom_key': 'SUMMARY_MODEL_API_KEY',
                'default_model': 'SUMMARY_MODEL',
                'fallback_type': 'assist',
            },
            'game_main': {
                'custom_model': 'GAME_MAIN_MODEL',
                'custom_url': 'GAME_MAIN_MODEL_URL',
                'custom_key': 'GAME_MAIN_MODEL_API_KEY',
                'default_model': 'GAME_MAIN_MODEL',
                'fallback_type': 'conversation',
            },
            'game_summary': {
                'custom_model': 'GAME_SUMMARY_MODEL',
                'custom_url': 'GAME_SUMMARY_MODEL_URL',
                'custom_key': 'GAME_SUMMARY_MODEL_API_KEY',
                'default_model': 'GAME_SUMMARY_MODEL',
                'fallback_type': 'summary',
            },
            'correction': {
                'custom_model': 'CORRECTION_MODEL',
                'custom_url': 'CORRECTION_MODEL_URL',
                'custom_key': 'CORRECTION_MODEL_API_KEY',
                'default_model': 'CORRECTION_MODEL',
                'fallback_type': 'assist',
            },
            'emotion': {
                'custom_model': 'EMOTION_MODEL',
                'custom_url': 'EMOTION_MODEL_URL',
                'custom_key': 'EMOTION_MODEL_API_KEY',
                'default_model': 'EMOTION_MODEL',
                'fallback_type': 'assist',
            },
            'vision': {
                'custom_model': 'VISION_MODEL',
                'custom_url': 'VISION_MODEL_URL',
                'custom_key': 'VISION_MODEL_API_KEY',
                'default_model': 'VISION_MODEL',
                'fallback_type': 'assist',
            },
            'agent': {
                'custom_model': 'AGENT_MODEL',
                'custom_url': 'AGENT_MODEL_URL',
                'custom_key': 'AGENT_MODEL_API_KEY',
                'default_model': 'AGENT_MODEL',
                'fallback_type': 'assist',
            },
            'realtime': {
                'custom_model': 'REALTIME_MODEL',
                'custom_url': 'REALTIME_MODEL_URL',
                'custom_key': 'REALTIME_MODEL_API_KEY',
                'default_model': 'CORE_MODEL',
                'fallback_type': 'core',  # 实时模型回退到核心API
            },
            'tts_default': {
                'custom_model': 'TTS_MODEL',
                'custom_url': 'TTS_MODEL_URL',
                'custom_key': 'TTS_MODEL_API_KEY',
                'default_model': 'CORE_MODEL',
                'fallback_type': 'core',  # 默认TTS回退到核心API
            },
            'tts_custom': {
                'custom_model': 'TTS_MODEL',
                'custom_url': 'TTS_MODEL_URL',
                'custom_key': 'TTS_MODEL_API_KEY',
                'default_model': 'CORE_MODEL',
                'fallback_type': 'assist',  # 自定义TTS回退到辅助API
            },
        }
        
        if model_type not in model_type_mapping:
            raise ValueError(f"Unknown model_type: {model_type}. Valid types: {list(model_type_mapping.keys())}")
        
        mapping = model_type_mapping[model_type]

        def _normalize_provider_type_value(value: object) -> str:
            provider_type = str(value or 'openai_compatible').strip().lower()
            if provider_type not in ('openai_compatible', 'anthropic', 'websocket'):
                return 'openai_compatible'
            return provider_type

        def _provider_type_from_assist_key(provider_key: str) -> str:
            profile = get_assist_api_profiles().get(str(provider_key or '').strip(), {})
            if isinstance(profile, dict):
                return _normalize_provider_type_value(profile.get('PROVIDER_TYPE'))
            return 'openai_compatible'

        def _provider_type_from_core_key(provider_key: str) -> str:
            profile = get_core_api_profiles().get(str(provider_key or '').strip(), {})
            if isinstance(profile, dict):
                return _normalize_provider_type_value(profile.get('PROVIDER_TYPE'))
            return _provider_type_from_assist_key(provider_key)

        def _resolved_provider_type_for_model(target_model_type: str, _seen: frozenset[str] = frozenset()) -> str:
            if target_model_type in _seen:
                return _normalize_provider_type_value(core_config.get('PROVIDER_TYPE'))
            seen = _seen | frozenset((target_model_type,))
            prefix_by_type = {
                'conversation': 'conversation',
                'summary': 'summary',
                'game_main': 'gameMain',
                'game_summary': 'gameSummary',
                'correction': 'correction',
                'emotion': 'emotion',
                'vision': 'vision',
                'agent': 'agent',
                'realtime': 'omni',
                'tts_default': 'tts',
                'tts_custom': 'tts',
            }
            prefix = prefix_by_type.get(target_model_type, target_model_type)
            provider = str(core_config.get(f'{prefix}ModelProvider') or '').strip()
            if provider == 'custom':
                return 'openai_compatible'
            if provider == 'follow_conversation':
                if target_model_type == 'conversation':
                    return _normalize_provider_type_value(core_config.get('PROVIDER_TYPE'))
                return _resolved_provider_type_for_model('conversation', seen)
            if provider == 'follow_summary':
                if target_model_type == 'summary':
                    return _normalize_provider_type_value(core_config.get('PROVIDER_TYPE'))
                return _resolved_provider_type_for_model('summary', seen)
            if provider == 'follow_core':
                return _provider_type_from_core_key(core_config.get('CORE_API_TYPE', ''))
            if provider == 'follow_assist':
                return _normalize_provider_type_value(core_config.get('PROVIDER_TYPE'))
            if not provider:
                fallback_type = model_type_mapping.get(target_model_type, {}).get('fallback_type')
                if fallback_type == 'core':
                    return _provider_type_from_core_key(core_config.get('CORE_API_TYPE', ''))
                if fallback_type == 'conversation':
                    return _resolved_provider_type_for_model('conversation', seen)
                if fallback_type == 'summary':
                    return _resolved_provider_type_for_model('summary', seen)
                return _normalize_provider_type_value(core_config.get('PROVIDER_TYPE'))
            return _provider_type_from_assist_key(provider)

        if model_type == 'game_main':
            provider = str(core_config.get('gameMainModelProvider') or 'follow_conversation').strip()
            if not treat_as_custom or provider == 'follow_conversation':
                return self.get_model_api_config('conversation')
        elif model_type == 'game_summary':
            provider = str(core_config.get('gameSummaryModelProvider') or 'follow_summary').strip()
            if not treat_as_custom or provider == 'follow_summary':
                return self.get_model_api_config('summary')
        
        # agent 始终走专用字段（AGENT_MODEL_URL 有 lanlan.app 归一化），
        # 但 is_custom 仅在 enableCustomApi 开启时为 True。
        if treat_as_custom or model_type == 'agent':
            custom_model = core_config.get(mapping['custom_model'], '')
            custom_url = core_config.get(mapping['custom_url'], '')
            custom_key = core_config.get(mapping['custom_key'], '')

            # GSV 模式下 voice_id 即定位（无 model 概念），URL 即可视为已配置；
            # 不放宽到全部 tts_custom 场景，避免改变 cosyvoice 用户原有的 fallthrough 行为。
            is_gsv_url = (
                gsv_enabled_for_tts
                and custom_url.startswith(('http://', 'https://'))
            )

            # 自定义配置完整时使用自定义配置
            if (custom_model and custom_url) or is_gsv_url:
                resolved_api_key = custom_key
                # 仅勾选 GSV、未填 TTS_MODEL_API_KEY 时，tts_custom slot 仍会被
                # CosyVoice clone 路径复用 (register_voice → get_tts_api_key('cosyvoice')
                # → 这里取 api_key)。直接返回空 key 会让 CosyVoice 报
                # TTS_AUDIO_API_KEY_MISSING，回退到 ASSIST_API_KEY_QWEN 才能保住用户
                # 在 GSV 开启前就在用的 CosyVoice 克隆能力。
                if is_gsv_url and not resolved_api_key and model_type == 'tts_custom':
                    resolved_api_key = (core_config.get('ASSIST_API_KEY_QWEN') or '').strip()
                return {
                    'model': custom_model,
                    'api_key': resolved_api_key,
                    'base_url': custom_url,
                    'is_custom': treat_as_custom,
                    'provider_type': _resolved_provider_type_for_model(model_type),
                    # 对于 realtime 模型，自定义配置时 api_type 设为 'local'
                    # TODO: 后续完善 'local' 类型的具体实现（如本地推理服务等）
                    'api_type': 'local' if model_type == 'realtime' else None,
                }
        
        # 自定义音色(CosyVoice)的特殊回退逻辑：优先尝试用户保存的 Qwen Cosyvoice API，
        # 只有在缺少 Qwen Cosyvoice API 时才再回退到辅助 API（CosyVoice 目前是唯一支持 voice clone 的）
        if model_type == 'tts_custom':
            active_assist = str(core_config.get('assistApi') or '').strip()
            qwen_candidates = []
            if active_assist in ('qwen', 'qwen_intl'):
                qwen_candidates.append(active_assist)
            qwen_candidates.extend(['qwen', 'qwen_intl'])

            seen_qwen = set()
            for qwen_provider in qwen_candidates:
                if qwen_provider in seen_qwen:
                    continue
                seen_qwen.add(qwen_provider)
                key_field = 'ASSIST_API_KEY_QWEN_INTL' if qwen_provider == 'qwen_intl' else 'ASSIST_API_KEY_QWEN'
                qwen_api_key = (core_config.get(key_field) or '').strip()
                if not qwen_api_key:
                    continue
                if qwen_provider == active_assist:
                    base_url = core_config.get('OPENROUTER_URL', '')
                else:
                    qwen_profile = get_assist_api_profiles().get(qwen_provider, {})
                    resolved_urls = core_config.get('RESOLVED_PROVIDER_URLS')
                    resolved_core_cfg = {
                        'resolvedProviderUrls': resolved_urls if isinstance(resolved_urls, dict) else {},
                    }
                    base_url = (
                        self._get_saved_provider_url(
                            resolved_core_cfg,
                            'assist',
                            qwen_provider,
                            qwen_profile,
                            'OPENROUTER_URL',
                            'OPENROUTER_URLS',
                        )
                        or qwen_profile.get('OPENROUTER_URL', core_config.get('OPENROUTER_URL', ''))
                    )
                return {
                    'model': core_config.get(mapping['default_model'], ''), # 占位值，下游会覆盖成实际模型
                    'api_key': qwen_api_key,
                    'base_url': base_url,
                    'is_custom': False,
                    'provider_type': _provider_type_from_assist_key(qwen_provider),
                }

        # 根据 fallback_type 回退到不同的 API
        if mapping['fallback_type'] == 'core':
            # 回退到核心 API 配置
            return {
                'model': core_config.get(mapping['default_model'], ''),
                'api_key': core_config.get('CORE_API_KEY', ''),
                'base_url': core_config.get('CORE_URL', ''),
                'is_custom': False,
                'provider_type': _resolved_provider_type_for_model(model_type),
                # 对于 realtime 模型，回退到核心API时使用配置的 CORE_API_TYPE
                'api_type': core_config.get('CORE_API_TYPE', '') if model_type == 'realtime' else None,
            }
        elif mapping['fallback_type'] == 'conversation':
            return self.get_model_api_config('conversation')
        elif mapping['fallback_type'] == 'summary':
            return self.get_model_api_config('summary')
        else:
            # 回退到辅助 API 配置
            return {
                'model': core_config.get(mapping['default_model'], ''),
                'api_key': core_config.get('OPENROUTER_API_KEY', ''),
                'base_url': core_config.get('OPENROUTER_URL', ''),
                'is_custom': False,
                'provider_type': _resolved_provider_type_for_model(model_type),
            }

    def is_agent_api_ready(self) -> tuple[bool, list[str]]:
        """
        Agent mode readiness check:
        - a usable AGENT_MODEL (model/url/api_key) is required
        - whether it is free (quota counting / frontend hints) is decided separately by
          is_agent_free() and is unrelated to this check: readiness only cares whether the
          model/url/key trio is filled in and a request can be made. free-access is a valid
          placeholder token for the truly free agent and should pass the gate; dirty configs
          (placeholder key against a self-paid endpoint) are caught downstream by 401, not here.
        """
        reasons = []
        agent_api = self.get_model_api_config('agent')
        if not (agent_api.get('model') or '').strip():
            reasons.append("Agent 模型未配置")
        if not (agent_api.get('base_url') or '').strip():
            reasons.append("Agent API URL 未配置")
        api_key = (agent_api.get('api_key') or '').strip()
        if not api_key:
            reasons.append("Agent API Key 未配置或不可用")
        return len(reasons) == 0, reasons

    def is_agent_free(self) -> bool:
        """Whether the Agent actually in use is the built-in free Agent model (free-agent-model).

        The single source of truth for "is the agent free" — quota counting and the frontend
        "free model may be congested" hint both read it. Dual of is_free_voice() (the
        voice/core dimension): even when using the free voice (core=free), if the agent is
        switched to a self-paid/custom model, this returns False.
        """
        agent_model = (self.get_model_api_config('agent').get('model') or '').strip()
        return agent_model == self._free_agent_model_name

    def is_free_voice(self) -> bool:
        """Whether the built-in free voice is in use (core=free). The single source of truth for
        "is voice free" — free preset voices, hidden main cloud voices and the default YUI
        fallback all read it. Dual of is_agent_free().

        Realtime and text TTS share the same voice and follow core, independent of assist:
        hide_cloud_main hides the main CosyVoice/Qwen cloud bucket in free mode. Provider-keyed
        clone buckets are handled separately by get_voices_for_current_api(), and remain visible
        when the corresponding CosyVoice clone API key is configured.
        """
        return (self.get_core_config().get('CORE_API_TYPE') or '') == 'free'
