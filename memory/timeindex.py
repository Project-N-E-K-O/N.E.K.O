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

from utils.llm_client import SQLChatMessageHistory, SystemMessage
from sqlalchemy import create_engine, text
from config import TIME_ORIGINAL_TABLE_NAME, TIME_COMPRESSED_TABLE_NAME
from memory.stop_names import collect_stop_names, strip_stop_names
from utils.cloudsave_runtime import MaintenanceModeError, assert_cloudsave_writable
from utils.config_manager import get_config_manager
from utils.logger_config import get_module_logger
from collections.abc import AsyncIterator, Callable, Generator, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import asyncio
import os
import unicodedata

logger = get_module_logger(__name__, "Memory")

# ``token_overlap`` 上的两条线（消费点：memory/facts.py 的 Stage-2）。
#
# 0.25 = 值得请 LLM 看一眼的下限，**不是**「判定为重复」的线。实测这个量本身
# 分不开语义：「养了一只猫」vs「养了一只狗」0.87（必须各留一条），而
# 「用户是一名程序员」vs「用户的职业是程序员」只有 0.29（该合并）。所以它按
# 召回定：宁可多送几对无关的进仲裁（每条新 fact 至多送一对，裁掉即止），也
# 不要把该合的挡在闸外——真正的裁决权在 fact_dedup 的 LLM 那里。
FACT_NEAR_DUP_ARBITRATE_OVERLAP = 0.25
# 投递候选时最多等 resolver 的锁多久。它会被 aresolve 攥着跑完整个 LLM 调用
# （那边超时 60s），而投递发生在**已经提交完** fact 之后的请求路径上——为一
# 次无关的后台仲裁把请求拖住一分钟不值得。等不到就放掉：这是尽力而为的旁路。
FACT_NEAR_DUP_ENQUEUE_TIMEOUT_SECONDS = 5.0


class CharacterEngineAdmissionError(RuntimeError):
    """The character identity is fenced for delete/rename publication."""


def _next_readonly_batch(
    stream: Generator[list[tuple[object, object, object]], None, None],
) -> tuple[bool, list[tuple[object, object, object]] | None]:
    """Return a non-StopIteration marker suitable for asyncio futures."""
    try:
        return False, next(stream)
    except StopIteration:
        return True, None


def _alnum_runs(tokens: list[str]) -> list[str]:
    """Cut tokens at the boundaries unicode61 would cut them at.

    A token has to survive SQLite unchanged, because the Dice score is
    computed over these tokens while the *retrieval* runs over whatever
    unicode61 made of them. ``_SPLIT_RE`` keeps characters unicode61
    treats as separators (``/``, ``_``, ``'``), so ``foo/bar`` is stored
    as one token here and indexed as ``foo`` + ``bar`` there: a query for
    ``foo bar`` retrieves the row and then scores 0 against it.

    CJK n-grams are unaffected — every character in them is alphanumeric.
    Tokens made only of punctuation disappear, which also keeps an empty
    quoted term out of the FTS query.
    """
    out: list[str] = []
    for token in tokens:
        run = ""
        for ch in token:
            if ch.isalnum():
                run += ch
            elif run:
                out.append(run)
                run = ""
        if run:
            out.append(run)
    return out


def _strip_marks(text: str) -> str:
    """Drop combining marks, keeping everything else composed.

    NFD decomposes Hangul syllables into jamo, so the result is
    re-composed with NFC — otherwise Korean text would tokenize into
    jamo sequences instead of syllables.
    """
    decomposed = unicodedata.normalize("NFD", text)
    return unicodedata.normalize("NFC", "".join(
        ch for ch in decomposed if not unicodedata.combining(ch)
    ))


def fts_tokens(content: str, stop_names: list[str] | None = None) -> list[str]:
    """Tokens actually stored in / queried against the facts FTS index.

    Both sides of the near-duplicate check run through here, so index
    and query can never disagree about what a token is.

    Three things happen, in order:

    * Traditional characters fold to Simplified. Two renderings of the
      same sentence otherwise share almost no characters at all, which
      makes every character-level overlap score read them as unrelated.
    * Stop-names (master / catgirl and their nicknames) are stripped.
      They appear in nearly every fact; leaving them in lets two facts
      about entirely different things score as similar.
    * The text is split with the *same* rules as
      ``memory.hybrid_recall._tokenize`` — CJK runs become 2/3-grams,
      Latin runs stay whole. Sharing the rules keeps the dedup side and
      the recall side talking about the same units.

    Latin is lower-cased because unicode61 matches it case-insensitively:
    without this an ``USER LIKES CATS`` row is *retrieved* for a query of
    ``user likes cats`` and then scores 0, so a pair differing only in
    case looks less alike than two unrelated facts.

    Double quotes are dropped up front: a token carrying one would break
    the quoting of the FTS5 query built from it, and dropping it on both
    sides keeps the two in step.

    Combining marks are stripped for the same reason as the case fold:
    unicode61 matches Latin diacritic-insensitively, so ``José`` is
    retrieved for ``Jose`` and would then score 0 against it.

    Raises if the shared tokenizer cannot be imported. There is
    deliberately no fallback splitter: whatever this returns gets
    *persisted*, and a fallback that tokenizes differently would write
    rows the normal tokenizer can never match again — with the backfill
    marker claiming the index is complete.

    Note what raising costs on the write path: ``index_fact`` calls this
    outside its own ``try``, so the error reaches
    ``_apersist_new_facts_locked`` and rolls back the whole batch — the
    facts are not written at all, not merely left unindexed. That is the
    intended trade (the store's existing rule is that an index failure
    must roll back, or a retry hits dedup and reports a false success),
    but it is a heavier failure than "dedup degrades for a while".
    """
    from memory.script_fold import fold_script

    # 懒 import：hybrid_recall 会拉起 persona，import-time 硬依赖在
    # memory-only 的 entrypoint 上不成立（同 hybrid_recall 自己的做法）。
    #
    # persona 单独 import 一次不是多余：_tokenize **自己**对 persona 的
    # import 失败留了一条空白切分兜底，那条兜底同样会把整句中文当一个
    # token 写进索引。在这里先把它拉起来，失败就直接抛——去掉 fts_tokens
    # 自己的兜底而放任下游那条，等于什么都没防住。
    from memory.hybrid_recall import _tokenize
    from memory.persona import _SPLIT_RE  # noqa: F401

    raw = _strip_marks(fold_script(str(content or "")).replace('"', ' ').lower())
    # stop-name 要走**完全相同**的一串归一（折叠 + 小写 + 剥组合符）：
    # strip_stop_names 是逐字面替换 / 词边界匹配，两侧形态差一点就永远撞不
    # 上——配置里写 `José`、正文已经归一成 `jose`，这个名字就再也剥不掉了。
    folded_stop_names = [
        _strip_marks(fold_script(n).lower()) for n in (stop_names or [])
    ] or None
    tokens = _alnum_runs(_tokenize(raw, folded_stop_names))
    if tokens:
        return tokens
    # _tokenize 的 CJK 段从 2-gram 起步、拉丁段要求长度 >= 2，所以归一后只
    # 剩一个字的事实（`猫`，或去掉停用名后只剩一个字）会得到空 token 列表
    # ——存进去是一行空 content，查询侧也会提前返回，这条 fact 就整个绕过了
    # Stage-2。两侧同样回落到单字，至少让它参与检索。
    #
    # ⚠️ 回落也必须先剥停用名：直接拿 raw 切的话，`兰兰猫` 会原样存成一个
    # token，停用名白剥了，还会跟别的含这个名字的事实互相命中。
    residue_source = (
        strip_stop_names(raw, folded_stop_names)
        if folded_stop_names else raw
    )
    residue = _alnum_runs(residue_source.split())
    return residue[:1] if residue else []


def token_overlap(left: list[str], right: list[str]) -> float:
    """Dice coefficient over two token sets — 0.0 (disjoint) .. 1.0 (same set).

    Deliberately not BM25: the caller needs a number it can put a fixed
    threshold on, and BM25 magnitudes move with corpus size and term
    rarity, so a threshold calibrated on one character's history means
    something else on another's. BM25 still does what it is good at —
    ranking the candidate window — it just doesn't get to decide.

    Set semantics, not multiset: a repeated n-gram says the phrase
    recurs inside one fact, which is not evidence about the other fact.
    """
    left_set, right_set = set(left), set(right)
    if not left_set or not right_set:
        return 0.0
    return 2 * len(left_set & right_set) / (len(left_set) + len(right_set))


class TimeIndexedMemory:
    def __init__(
        self,
        recent_history_manager,
        *,
        engine_admission_check: Callable[[str], bool] | None = None,
    ):
        self.engines = {}  # 存储 {lanlan_name: engine}
        self.db_paths = {} # 存储 {lanlan_name: db_path}
        self._engine_readonly_flags = {}  # 存储 {lanlan_name: bool}
        self._writable_bootstrapped = set()  # 存储已完成可写初始化的角色
        # {lanlan_name: {connection_string}}：dispose 失败、仍扣着文件句柄的 pool。
        # 按连接串记账而不是靠 db_path 现推——路径漂移重建会覆盖 db_path，
        # 那之后就再也推不出失败 pool 的键了。
        self._undisposed_pools: dict[str, set[str]] = {}
        self.recent_history_manager = recent_history_manager
        self._engine_admission_check = engine_admission_check
        # 懒加载：不在构造器里同步初始化每角色 engine，首次访问时按需创建
        # （MaintenanceModeError 在 _ensure_engine_exists 内部按需处理）

    def _assert_timeindex_writable(self, lanlan_name: str) -> None:
        assert_cloudsave_writable(
            get_config_manager(),
            operation="save",
            target=f"memory/{lanlan_name}/time_indexed.db",
        )

    def _build_sqlite_connection_string(self, db_path: str, *, readonly: bool) -> tuple[str, str]:
        normalized_db_path = os.path.abspath(db_path)
        uri_path = normalized_db_path.replace("\\", "/")
        if readonly:
            sqlite_file_uri = f"file:{uri_path}"
            if os.name == "nt" and not uri_path.startswith("/"):
                sqlite_file_uri = f"file:/{uri_path}"
            return normalized_db_path, f"sqlite:///{sqlite_file_uri}?mode=ro&uri=true"
        if not readonly:
            db_dir = os.path.dirname(normalized_db_path)
            os.makedirs(db_dir, exist_ok=True)
        return normalized_db_path, f"sqlite:///{uri_path}"

    def _resolve_expected_db_path(self, lanlan_name: str, *, readonly: bool) -> str | None:
        """Compute the target path of this character's db under the current memory_dir.

        time_store takes precedence (allowing a character to register its db
        explicitly outside memory_dir), otherwise fall back to
        ``memory_dir/{name}/time_indexed.db``. ``config_manager.memory_dir`` is
        re-read on every call so the path-drift self-check in
        ``_ensure_engine_exists`` can notice in-process memory_dir drift.
        """
        try:
            _, _, _, _, _, _, time_store, _, _ = get_config_manager().get_character_data()
        except Exception as exc:
            logger.warning("[TimeIndexedMemory] get_character_data 失败，回退默认 db_path: %s", exc)
            time_store = {}
        if lanlan_name in time_store:
            return time_store[lanlan_name]
        config_mgr = get_config_manager()
        if readonly:
            return os.path.join(str(config_mgr.memory_dir), lanlan_name, "time_indexed.db")
        from memory import ensure_character_dir
        return os.path.join(ensure_character_dir(config_mgr.memory_dir, lanlan_name), 'time_indexed.db')

    @staticmethod
    def _db_paths_equivalent(left: str, right: str) -> bool:
        try:
            return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))
        except Exception:
            return left == right

    def _ensure_engine_exists(
        self,
        lanlan_name: str,
        db_path: str | None = None,
        readonly: bool = False,
    ) -> bool:
        """Ensure the given character's database engine is initialized, meow~"""
        if (
            self._engine_admission_check is not None
            and not self._engine_admission_check(lanlan_name)
        ):
            logger.debug(
                "[TimeIndexedMemory] 角色 %s 正在删除或改名，拒绝数据库引擎初始化",
                lanlan_name,
            )
            raise CharacterEngineAdmissionError(
                f"character engine admission is fenced: {lanlan_name}"
            )
        if not readonly:
            self._assert_timeindex_writable(lanlan_name)
        if lanlan_name in self.engines and lanlan_name in self.db_paths:
            cached_engine = self.engines[lanlan_name]
            cached_db_path = str(self.db_paths[lanlan_name])
            cached_readonly = bool(self._engine_readonly_flags.get(lanlan_name, False))

            # Path-drift defense: 罕见但可能——/reload 期间 storage_policy
            # 重写 selected_root，新实例已经 reload 过但旧实例还在被某条
            # async path 持有；或测试场景里 monkeypatch 了 memory_dir。
            # 一旦 cached db_path 与当前 memory_dir 推导出的目标不一致，
            # 老 SQLAlchemy engine 会继续往旧文件写，前端表象就是 db 永远
            # 不更新（/process 的 except Exception 又把 SQL 错误吞掉）。
            # 嗅探到漂移就 dispose 让下面的新建分支用 expected 重建。
            expected_db_path = db_path
            if expected_db_path is None:
                try:
                    expected_db_path = self._resolve_expected_db_path(lanlan_name, readonly=readonly)
                except Exception as exc:
                    logger.debug("[TimeIndexedMemory] 解析 expected db_path 失败，跳过 drift 检查: %s", exc)
                    expected_db_path = None
            if expected_db_path and not self._db_paths_equivalent(expected_db_path, cached_db_path):
                logger.warning(
                    "[TimeIndexedMemory] 角色 %s 的 db_path 漂移，dispose 重建：cached=%s expected=%s",
                    lanlan_name, cached_db_path, expected_db_path,
                )
                self.dispose_engine(lanlan_name)
                db_path = expected_db_path
                # 落到下面"新建 engine"分支
            elif not readonly and cached_readonly and lanlan_name not in self._writable_bootstrapped:
                logger.info("[TimeIndexedMemory] 角色 %s 当前为只读引擎，切换为可写引擎后再执行迁移", lanlan_name)
                self.dispose_engine(lanlan_name)
                if not db_path:
                    db_path = cached_db_path
            else:
                if readonly or lanlan_name in self._writable_bootstrapped:
                    return True
                try:
                    normalized_db_path, connection_string = self._build_sqlite_connection_string(
                        str(self.db_paths[lanlan_name]),
                        readonly=False,
                    )
                    self._ensure_tables_exist_with(cached_engine, connection_string, lanlan_name)
                    self._check_and_migrate_schema(cached_engine, lanlan_name)
                    self.db_paths[lanlan_name] = normalized_db_path
                    self._writable_bootstrapped.add(lanlan_name)
                    self._engine_readonly_flags[lanlan_name] = False
                    return True
                except Exception:
                    logger.exception(f"补跑角色数据库可写初始化失败: {lanlan_name}")
                    return False

        engine = None
        connection_string = None
        try:
            if not db_path:
                db_path = self._resolve_expected_db_path(lanlan_name, readonly=readonly)
                if not db_path:
                    logger.error(f"[TimeIndexedMemory] 角色 '{lanlan_name}' 无法解析 db_path")
                    return False

            normalized_db_path, connection_string = self._build_sqlite_connection_string(
                db_path,
                readonly=readonly,
            )
            if readonly and not os.path.isfile(normalized_db_path):
                return False
            engine = create_engine(connection_string)
            if not readonly:
                # 先完成所有初始化/迁移，再注册到 self.engines，
                # 避免失败后引擎被标记为"已初始化"而跳过后续修复
                self._ensure_tables_exist_with(engine, connection_string, lanlan_name)
                self._check_and_migrate_schema(engine, lanlan_name)
                self._writable_bootstrapped.add(lanlan_name)
            else:
                self._writable_bootstrapped.discard(lanlan_name)
            self.db_paths[lanlan_name] = normalized_db_path
            self.engines[lanlan_name] = engine
            self._engine_readonly_flags[lanlan_name] = readonly
            return True
        except Exception:
            try:
                if engine is not None:
                    engine.dispose()
            except Exception as cleanup_exc:
                logger.debug(
                    "[TimeIndexedMemory] 初始化失败后的 engine.dispose 清理失败: %s",
                    cleanup_exc,
                )
            try:
                existing_engine = self.engines.get(lanlan_name)
                if existing_engine is engine:
                    self.engines.pop(lanlan_name, None)
                    self.db_paths.pop(lanlan_name, None)
                    self._engine_readonly_flags.pop(lanlan_name, None)
                    self._writable_bootstrapped.discard(lanlan_name)
            except Exception as cleanup_exc:
                logger.debug(
                    "[TimeIndexedMemory] 初始化失败后的缓存回收清理失败(%s): %s",
                    lanlan_name,
                    cleanup_exc,
                )
            if connection_string:
                cached_engine = SQLChatMessageHistory._engine_cache.pop(connection_string, None)
                if cached_engine is not None and cached_engine is not engine:
                    try:
                        cached_engine.dispose()
                    except Exception as cleanup_exc:
                        logger.debug(
                            "[TimeIndexedMemory] 初始化失败后的 SQLChatMessageHistory 引擎清理失败(%s): %s",
                            lanlan_name,
                            cleanup_exc,
                        )
            logger.exception(f"初始化角色数据库引擎失败: {lanlan_name}")
            return False

    async def _aensure_engine_exists(self, lanlan_name: str, db_path: str | None = None) -> bool:
        """Async version: offload the blocking engine creation to the thread pool.

        There used to be an early short-circuit here — ``if lanlan_name in self.engines and lanlan_name in self.db_paths:
        return True`` — keeping the cache-hit check outside the sync
        implementation. That path bypassed the path-drift self-check newly added
        to ``_ensure_engine_exists`` (dispose & rebuild when the cached db_path
        mismatches the expected one derived from the current memory_dir). No
        async caller currently uses this entry, but to keep a future addition
        from silently disabling the drift detection, the short-circuit was
        removed and everything delegates to the sync implementation.
        """
        return await asyncio.to_thread(self._ensure_engine_exists, lanlan_name, db_path)

    def dispose_engine(
        self, lanlan_name: str, *, retain_on_failure: bool = False,
    ) -> bool:
        """Dispose one character's cached engines and report whether any were known.

        ``retain_on_failure`` keeps the bookkeeping when a disposal raised, so a
        caller that retries can still reach this generation instead of losing the
        pool that holds the file. It is for the character-release path only.

        The default clears the bookkeeping either way, which is what the in-place
        repair branches of ``_ensure_engine_exists`` (db_path drift, readonly →
        writable switch) rely on: they dispose and then fall through to the
        rebuild branch, so a transient disposal failure must not pin this
        character to the same failing branch forever.
        """
        db_path = self.db_paths.get(lanlan_name)
        engine = self.engines.get(lanlan_name)
        released = engine is not None
        errors: list[Exception] = []
        engine_disposed = engine is None
        # 本次要处理的连接串 = 当前 db_path 推出来的两个 + 以前失败留账的那些。
        connection_strings: set[str] = set(
            self._undisposed_pools.get(lanlan_name, ())
        )
        if db_path:
            normalized_db_path, readonly_connection_string = self._build_sqlite_connection_string(
                str(db_path),
                readonly=True,
            )
            uri_path = normalized_db_path.replace("\\", "/")
            connection_strings.add(readonly_connection_string)
            connection_strings.add(f"sqlite:///{uri_path}")

        def _remember_undisposed(keys: set[str]) -> None:
            if keys:
                self._undisposed_pools.setdefault(lanlan_name, set()).update(keys)

        if engine:
            try:
                engine.dispose()
                engine_disposed = True
                logger.info(f"[TimeIndexedMemory] 已释放角色 {lanlan_name} 的数据库引擎")
            except Exception as exc:
                # 继续走下面的 _engine_cache 清理：真正扣着文件句柄的往往是
                # 缓存里的那个 pool，不能因为这一步失败就整段跳过。
                errors.append(exc)
                _remember_undisposed(connection_strings)
        for connection_string in sorted(connection_strings):
            # 只在确认释放成功之后才摘缓存条目：先摘后放的话，dispose 一抛
            # 这个 pool 就再也找不回来，重试摸不到它，句柄会一直扣到进程退出。
            cached_engine = SQLChatMessageHistory._engine_cache.get(connection_string)
            if cached_engine is None:
                self._undisposed_pools.get(lanlan_name, set()).discard(connection_string)
                continue
            released = True
            if cached_engine is engine:
                if not engine_disposed:
                    continue
            else:
                try:
                    cached_engine.dispose()
                except Exception as exc:
                    errors.append(exc)
                    _remember_undisposed({connection_string})
                    continue
            SQLChatMessageHistory._engine_cache.pop(connection_string, None)
            self._undisposed_pools.get(lanlan_name, set()).discard(connection_string)
        if not self._undisposed_pools.get(lanlan_name):
            self._undisposed_pools.pop(lanlan_name, None)
        if not (errors and retain_on_failure):
            self.db_paths.pop(lanlan_name, None)
            self.engines.pop(lanlan_name, None)
            self._engine_readonly_flags.pop(lanlan_name, None)
            self._writable_bootstrapped.discard(lanlan_name)
        if errors:
            raise errors[0]
        return released

    def cleanup(self):
        """Clean up all engine resources, meow~"""
        for name in list(self.engines.keys()):
            self.dispose_engine(name)

    def _ensure_tables_exist_with(self, engine, connection_string: str, lanlan_name: str) -> None:
        """
        Ensure the raw and compressed tables exist, meow~
        Note: this method relies on a side effect of the SQLChatMessageHistory
        constructor (automatic table creation). If the LangChain implementation
        changes in the future, this logic may need adjusting.
        """
        _ = SQLChatMessageHistory(
            connection_string=connection_string,
            session_id="",
            table_name=TIME_ORIGINAL_TABLE_NAME,
        )
        _ = SQLChatMessageHistory(
            connection_string=connection_string,
            session_id="",
            table_name=TIME_COMPRESSED_TABLE_NAME,
        )

        # 验证表是否真的被创建了喵~
        with engine.connect() as conn:
            for table in [TIME_ORIGINAL_TABLE_NAME, TIME_COMPRESSED_TABLE_NAME]:
                result = conn.execute(text(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'"))
                if not result.fetchone():
                    logger.error(f"[TimeIndexedMemory] 表 {table} 未能成功创建喵！")

    def _check_and_migrate_schema(self, engine, lanlan_name: str) -> None:
        """Backfill timestamp columns and their read indexes table by table."""
        migration_errors = []
        for table_name in [TIME_ORIGINAL_TABLE_NAME, TIME_COMPRESSED_TABLE_NAME]:
            table = self._validate_table_name(table_name)
            try:
                with engine.connect() as conn:
                    result = conn.execute(text(f"PRAGMA table_info({table})"))
                    columns = [row[1] for row in result.fetchall()]
                    if 'timestamp' not in columns:
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN timestamp DATETIME"))
                        logger.info(f"[TimeIndexedMemory] 已为 {lanlan_name} 的表 {table} 补齐 timestamp 列")
                    # SQLite secondary indexes carry rowid as their implicit
                    # tie-breaker, matching ORDER BY timestamp, rowid in the
                    # paginated reader without requiring a redundant column.
                    index_name = f"idx_{table}_timestamp"
                    conn.execute(text(
                        f"CREATE INDEX IF NOT EXISTS {index_name} "
                        f"ON {table}(timestamp)"
                    ))
                    conn.commit()
            except Exception as exc:
                logger.exception(f"[TimeIndexedMemory] 迁移 {lanlan_name} 表 {table} 失败")
                migration_errors.append(f"{table}: {exc}")
        if migration_errors:
            raise RuntimeError(
                f"[TimeIndexedMemory] 角色 {lanlan_name} schema 迁移失败: {'; '.join(migration_errors)}"
            )

    def store_conversation(self, event_id, messages, lanlan_name, timestamp=None):
        self._assert_timeindex_writable(lanlan_name)
        # 确保数据库引擎和路径存在
        if not self._ensure_engine_exists(lanlan_name):
            logger.error(f"严重错误：无法为角色 {lanlan_name} 创建任何数据库连接")
            return

        if timestamp is None:
            timestamp = datetime.now()

        db_path = self.db_paths[lanlan_name]
        uri_path = db_path.replace("\\", "/")
        connection_string = f"sqlite:///{uri_path}"

        original_table = self._validate_table_name(TIME_ORIGINAL_TABLE_NAME)

        origin_history = SQLChatMessageHistory(
            connection_string=connection_string,
            session_id=event_id,
            table_name=original_table,
        )

        origin_history.add_messages(messages)
        # NOTE: compressed table 写入已废弃，fact/reflection 层已取代其功能

        with self.engines[lanlan_name].connect() as conn:
            conn.execute(
                text(f"UPDATE {original_table} SET timestamp = :timestamp WHERE session_id = :session_id"),
                {"timestamp": timestamp, "session_id": event_id}
            )
            conn.commit()

    async def astore_conversation(self, event_id, messages, lanlan_name, timestamp=None):
        await asyncio.to_thread(
            self.store_conversation, event_id, messages, lanlan_name, timestamp
        )

    def _validate_table_name(self, table_name: str) -> str:
        """Validate that a table name is legal, guarding against SQL injection, meow~"""
        allowed_tables = {TIME_ORIGINAL_TABLE_NAME, TIME_COMPRESSED_TABLE_NAME}
        if table_name not in allowed_tables:
            raise ValueError(f"不合法的表名: {table_name}")
        return table_name

    def get_last_conversation_time(self, lanlan_name: str) -> datetime | None:
        """Query the timestamp of the given character's last conversation. Returns None when there are no records."""
        try:
            if not self._ensure_engine_exists(lanlan_name, readonly=True):
                return None
        except MaintenanceModeError as exc:
            logger.debug(f"[TimeIndexedMemory] 维护态跳过初始化 {lanlan_name} 的 time_indexed.db: {exc}")
            return None
        table_name = self._validate_table_name(TIME_ORIGINAL_TABLE_NAME)
        try:
            with self.engines[lanlan_name].connect() as conn:
                result = conn.execute(
                    text(f"SELECT MAX(timestamp) FROM {table_name}")
                )
                row = result.fetchone()
                if row and row[0]:
                    ts = row[0]
                    if isinstance(ts, str):
                        try:
                            return datetime.fromisoformat(ts)
                        except ValueError:
                            return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S.%f")
                    if isinstance(ts, datetime):
                        return ts
        except Exception as e:
            logger.warning(f"[TimeIndexedMemory] 查询最后对话时间失败: {e}")
        return None

    async def aget_last_conversation_time(self, lanlan_name: str) -> datetime | None:
        return await asyncio.to_thread(self.get_last_conversation_time, lanlan_name)

    def retrieve_summary_by_timeframe(self, lanlan_name, start_time, end_time):
        """[Deprecated] The compressed table is no longer written; fact/reflection replaced it."""
        return []

    async def aretrieve_summary_by_timeframe(self, lanlan_name, start_time, end_time):
        return []

    def retrieve_original_by_timeframe(self, lanlan_name, start_time, end_time, limit_rows: int | None = None):
        """Read raw conversation rows within the [start_time, end_time] window.

        Returns ``[(timestamp, session_id, message), ...]`` sorted by timestamp
        ASC — guaranteeing the caller can advance its cursor for drainage based
        on the last row's ts.

        When ``limit_rows`` is not None, a LIMIT is added at the SQL level,
        keeping an overlong fallback window from pulling the whole table into
        memory.

        Lazy loading: first access (e.g. reading right after a restart) needs
        engine registration, otherwise the rebuttal loop silently skips until
        store_conversation triggers table creation. The read path is readonly;
        reads are allowed even in maintenance mode.
        """
        try:
            if not self._ensure_engine_exists(lanlan_name, readonly=True):
                return []
        except MaintenanceModeError as exc:
            logger.debug(f"[TimeIndexedMemory] 维护态跳过读取 {lanlan_name} 的历史对话: {exc}")
            return []
        table_name = self._validate_table_name(TIME_ORIGINAL_TABLE_NAME)
        try:
            sql = (
                f"SELECT timestamp, session_id, message FROM {table_name} "
                f"WHERE timestamp BETWEEN :start_time AND :end_time "
                f"ORDER BY timestamp ASC"
            )
            params: dict = {"start_time": start_time, "end_time": end_time}
            if limit_rows is not None and limit_rows > 0:
                sql += " LIMIT :limit_rows"
                params["limit_rows"] = int(limit_rows)
            with self.engines[lanlan_name].connect() as conn:
                result = conn.execute(text(sql), params)
                return result.fetchall()
        except Exception as e:
            logger.warning(f"[TimeIndexedMemory] 按时间范围读取原始对话失败: {e}")
            return []

    async def aretrieve_original_by_timeframe(self, lanlan_name, start_time, end_time, limit_rows: int | None = None):
        return await asyncio.to_thread(
            self.retrieve_original_by_timeframe, lanlan_name, start_time, end_time, limit_rows
        )

    def _fetch_original_timeframe_page(
        self,
        lanlan_name: str,
        start_time,
        end_time,
        *,
        page_size: int,
        cursor: tuple[object, int] | None,
    ) -> tuple[list[tuple[object, object, object]], tuple[object, int] | None]:
        """Fetch one stable keyset page and close its connection before returning."""
        table_name = self._validate_table_name(TIME_ORIGINAL_TABLE_NAME)
        sql = (
            f"SELECT timestamp, rowid, session_id, message FROM {table_name} "
            f"WHERE timestamp BETWEEN :start_time AND :end_time"
        )
        params: dict = {
            "start_time": start_time,
            "end_time": end_time,
            "page_size": page_size,
        }
        if cursor is not None:
            sql += (
                " AND (timestamp > :cursor_timestamp "
                "OR (timestamp = :cursor_timestamp AND rowid > :cursor_rowid))"
            )
            params["cursor_timestamp"], params["cursor_rowid"] = cursor
        sql += " ORDER BY timestamp ASC, rowid ASC LIMIT :page_size"

        # The connection and Result stay entirely inside this call. In
        # particular, async callers run this whole method in ``to_thread`` and
        # only receive the materialized, bounded page after ``__exit__``.
        with self.engines[lanlan_name].connect() as conn:
            result = conn.execute(text(sql), params)
            raw_rows = result.fetchmany(page_size)

        if not raw_rows:
            return [], None
        rows = [(row[0], row[2], row[3]) for row in raw_rows]
        next_cursor = (raw_rows[-1][0], int(raw_rows[-1][1]))
        return rows, next_cursor

    def _has_indexed_timeframe_order(
        self,
        lanlan_name: str,
        start_time,
        end_time,
    ) -> bool:
        """Return whether SQLite can satisfy the keyset order without sorting."""
        table_name = self._validate_table_name(TIME_ORIGINAL_TABLE_NAME)
        sql = (
            f"EXPLAIN QUERY PLAN SELECT timestamp, rowid FROM {table_name} "
            "WHERE timestamp BETWEEN :start_time AND :end_time "
            "ORDER BY timestamp ASC, rowid ASC LIMIT 1"
        )
        try:
            with self.engines[lanlan_name].connect() as conn:
                plan = conn.execute(
                    text(sql),
                    {"start_time": start_time, "end_time": end_time},
                ).fetchall()
        except Exception as exc:
            logger.debug(
                "[TimeIndexedMemory] 无法检查时间索引，改用单查询流式读取: %s",
                exc,
            )
            return False
        return not any(
            "USE TEMP B-TREE FOR ORDER BY" in str(row[-1]).upper() for row in plan
        )

    def _iter_readonly_timeframe_stream(
        self,
        lanlan_name: str,
        start_time,
        end_time,
        *,
        batch_size: int,
        limit_rows: int | None,
    ) -> Generator[list[tuple[object, object, object]], None, None]:
        """Fetch bounded batches from one ordered read-only query."""
        table_name = self._validate_table_name(TIME_ORIGINAL_TABLE_NAME)
        sql = (
            f"SELECT timestamp, session_id, message FROM {table_name} "
            "WHERE timestamp BETWEEN :start_time AND :end_time "
            "ORDER BY timestamp ASC, rowid ASC"
        )
        params: dict = {"start_time": start_time, "end_time": end_time}
        if limit_rows is not None and limit_rows > 0:
            sql += " LIMIT :limit_rows"
            params["limit_rows"] = int(limit_rows)
        with self.engines[lanlan_name].connect() as conn:
            result = conn.execute(text(sql), params)
            while True:
                raw_rows = result.fetchmany(batch_size)
                if not raw_rows:
                    return
                yield [(row[0], row[1], row[2]) for row in raw_rows]

    def iter_original_by_timeframe_batches(
        self,
        lanlan_name: str,
        start_time,
        end_time,
        *,
        batch_size: int = 256,
        limit_rows: int | None = None,
    ) -> Iterator[list[tuple[object, object, object]]]:
        """Yield bounded raw-conversation batches in stable chronological order.

        Indexed databases close their connection before each keyset page is
        yielded. A read-only legacy database without that index uses one
        streaming cursor, avoiding a full scan and sort per page. The async
        path confines that cursor to one worker and only returns materialized
        batches across the thread boundary.
        ``limit_rows`` is a total cap across all batches; non-positive values
        retain the list API's historical "no LIMIT" meaning.
        """
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than zero")
        try:
            if not self._ensure_engine_exists(lanlan_name, readonly=True):
                return
        except MaintenanceModeError as exc:
            logger.debug(f"[TimeIndexedMemory] 维护态跳过分批读取 {lanlan_name} 的历史对话: {exc}")
            return

        if not self._has_indexed_timeframe_order(lanlan_name, start_time, end_time):
            stream = self._iter_readonly_timeframe_stream(
                lanlan_name,
                start_time,
                end_time,
                batch_size=batch_size,
                limit_rows=limit_rows,
            )
            try:
                yield from stream
            except Exception as exc:
                logger.warning(f"[TimeIndexedMemory] 单查询流式读取原始对话失败: {exc}")
                raise
            finally:
                stream.close()
            return

        remaining = int(limit_rows) if limit_rows is not None and limit_rows > 0 else None
        cursor: tuple[object, int] | None = None
        while remaining is None or remaining > 0:
            page_size = batch_size if remaining is None else min(batch_size, remaining)
            try:
                rows, cursor = self._fetch_original_timeframe_page(
                    lanlan_name,
                    start_time,
                    end_time,
                    page_size=page_size,
                    cursor=cursor,
                )
            except Exception as exc:
                logger.warning(f"[TimeIndexedMemory] 分批读取原始对话失败: {exc}")
                raise
            if not rows:
                return
            if remaining is not None:
                remaining -= len(rows)
            yield rows

    async def aiter_original_by_timeframe_batches(
        self,
        lanlan_name: str,
        start_time,
        end_time,
        *,
        batch_size: int = 256,
        limit_rows: int | None = None,
    ) -> AsyncIterator[list[tuple[object, object, object]]]:
        """Async batch iterator whose SQLite work is fully contained in workers."""
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than zero")
        try:
            ready = await asyncio.to_thread(
                self._ensure_engine_exists,
                lanlan_name,
                None,
                True,
            )
            if not ready:
                return
        except MaintenanceModeError as exc:
            logger.debug(f"[TimeIndexedMemory] 维护态跳过异步分批读取 {lanlan_name} 的历史对话: {exc}")
            return

        indexed = await asyncio.to_thread(
            self._has_indexed_timeframe_order,
            lanlan_name,
            start_time,
            end_time,
        )
        if not indexed:
            stream = self._iter_readonly_timeframe_stream(
                lanlan_name,
                start_time,
                end_time,
                batch_size=batch_size,
                limit_rows=limit_rows,
            )
            executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="time-index-read",
            )
            loop = asyncio.get_running_loop()
            try:
                while True:
                    done, rows = await loop.run_in_executor(
                        executor,
                        _next_readonly_batch,
                        stream,
                    )
                    if done:
                        return
                    if rows is not None:
                        yield rows
            except Exception as exc:
                logger.warning(f"[TimeIndexedMemory] 异步单查询流式读取原始对话失败: {exc}")
                raise
            finally:
                try:
                    await loop.run_in_executor(executor, stream.close)
                finally:
                    executor.shutdown(wait=True)
            return

        remaining = int(limit_rows) if limit_rows is not None and limit_rows > 0 else None
        cursor: tuple[object, int] | None = None
        while remaining is None or remaining > 0:
            page_size = batch_size if remaining is None else min(batch_size, remaining)
            try:
                rows, cursor = await asyncio.to_thread(
                    self._fetch_original_timeframe_page,
                    lanlan_name,
                    start_time,
                    end_time,
                    page_size=page_size,
                    cursor=cursor,
                )
            except Exception as exc:
                logger.warning(f"[TimeIndexedMemory] 异步分批读取原始对话失败: {exc}")
                raise
            if not rows:
                return
            if remaining is not None:
                remaining -= len(rows)
            yield rows

    # ── FTS5 事实索引 ─────────────────────────────────────────────

    FACTS_FTS_TABLE = "facts_fts_v2"
    FACTS_FTS_META_TABLE = "facts_fts_meta"
    # v1（``facts_fts``）存的是原文 + unicode61，对中文整段只产出一个 token，
    # 近重复检索永远打不中（#2703）。v2 换成「Python 端折叠 + n-gram」后两侧
    # 口径都变了，旧表的行一条也不能复用——建 v2 时直接 DROP，不是省空间，是
    # 隐私擦除必须只有一份索引：留着 v1 就等于 delete_fact_from_index 之后
    # 原文还躺在另一张表里。
    _LEGACY_FACTS_FTS_TABLES = ("facts_fts",)

    def _ensure_fts_table(self, lanlan_name: str, readonly: bool = False) -> bool:
        """Ensure the FTS5 virtual table exists.

        The table stores **pre-tokenized** content (see ``fts_tokens``),
        not raw fact text: unicode61 treats a whole run of CJK as one
        token, so raw Chinese is only ever retrievable by an exact
        full-string match. Feeding it space-separated n-grams puts the
        tokenization under our control and keeps the dependency at zero.
        """
        if not self._ensure_engine_exists(lanlan_name, readonly=readonly):
            return False
        if readonly:
            try:
                with self.engines[lanlan_name].connect() as conn:
                    result = conn.execute(
                        text(
                            "SELECT name FROM sqlite_master "
                            "WHERE type='table' AND name = :table_name"
                        ),
                        {"table_name": self.FACTS_FTS_TABLE},
                    )
                    return result.fetchone() is not None
            except Exception as e:
                logger.debug(f"[TimeIndexedMemory] 只读检查 FTS5 表失败: {e}")
                return False
        self._assert_timeindex_writable(lanlan_name)
        try:
            with self.engines[lanlan_name].connect() as conn:
                # fact_id 必须 UNINDEXED：它进 FTS 词表的话，OR 查询里的拉丁
                # token（"fact"）会命中每一行的 id 列，候选集直接退化成全表。
                conn.execute(text(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS {self.FACTS_FTS_TABLE} "
                    f"USING fts5(fact_id UNINDEXED, content, tokenize='unicode61')"
                ))
                conn.execute(text(
                    f"CREATE TABLE IF NOT EXISTS {self.FACTS_FTS_META_TABLE} "
                    f"(key TEXT PRIMARY KEY, value TEXT)"
                ))
                for legacy in self._LEGACY_FACTS_FTS_TABLES:
                    conn.execute(text(f"DROP TABLE IF EXISTS {legacy}"))
                conn.commit()
            return True
        except Exception as e:
            logger.warning(f"[TimeIndexedMemory] 创建 FTS5 表失败: {e}")
            return False

    async def a_ensure_fts_table(self, lanlan_name: str) -> None:
        await asyncio.to_thread(self._ensure_fts_table, lanlan_name)

    def index_fact(self, lanlan_name: str, fact_id: str, content: str) -> None:
        """Insert a fact into the FTS5 index.

        What lands in the ``content`` column is the token list from
        ``fts_tokens``, space-joined — never the raw fact text. Nothing
        reads this column back as prose (search returns ids), so the
        stored form is free to be whatever makes matching work.
        """
        self._assert_timeindex_writable(lanlan_name)
        if not self._ensure_engine_exists(lanlan_name):
            return
        if not self._ensure_fts_table(lanlan_name):
            return
        stop_names = collect_stop_names(get_config_manager(), lanlan_name)
        indexed_content = " ".join(fts_tokens(content, stop_names))
        try:
            with self.engines[lanlan_name].connect() as conn:
                # 先检查是否已存在
                result = conn.execute(
                    text(f"SELECT fact_id FROM {self.FACTS_FTS_TABLE} WHERE fact_id = :fid"),
                    {"fid": fact_id}
                )
                if result.fetchone():
                    return  # 已索引
                conn.execute(
                    text(f"INSERT INTO {self.FACTS_FTS_TABLE}(fact_id, content) VALUES(:fid, :content)"),
                    {"fid": fact_id, "content": indexed_content}
                )
                conn.commit()
        except Exception as e:
            logger.warning(f"[TimeIndexedMemory] 索引事实失败: {e}")

    async def aindex_fact(self, lanlan_name: str, fact_id: str, content: str) -> None:
        await asyncio.to_thread(self.index_fact, lanlan_name, fact_id, content)

    # ── v1 → v2 索引回填 ──────────────────────────────────────────

    _FTS_BACKFILL_META_KEY = "backfilled_at"

    def fts_index_needs_backfill(self, lanlan_name: str) -> bool:
        """True while the v2 index has never been populated from facts.json.

        The v2 table is created empty (v1's rows are unusable — different
        tokenization on both sides), so without a backfill every fact
        written before the upgrade is invisible to the near-duplicate
        check. An empty table is *not* the signal: a character with no
        facts yet is also empty, and re-scanning it on every write would
        never stop. The marker row is.
        """
        try:
            if not self._ensure_engine_exists(lanlan_name, readonly=True):
                # 连只读都打不开（db 文件还不存在，或这一刻不可读）——当作
                # 「要回填」：写路径会顺手建库、建表、盖标记，一次就收敛。
                # 反过来当作「不用回填」的话，一个还没有 db 的角色会永久
                # 错过回填，而这条路径正是全新角色的常态。
                return True
            with self.engines[lanlan_name].connect() as conn:
                present = {
                    row[0] for row in conn.execute(
                        text(
                            "SELECT name FROM sqlite_master "
                            "WHERE type='table' AND name IN "
                            "(:meta_table, :fts_table)"
                        ),
                        {
                            "meta_table": self.FACTS_FTS_META_TABLE,
                            "fts_table": self.FACTS_FTS_TABLE,
                        },
                    ).fetchall()
                }
                # 标记表在、索引表不在（局部修库 / 手工恢复），说明标记在说
                # 谎：认它的话历史行永远补不回来，下一次 index_fact 只会建
                # 一张空表再塞进当前这一条。
                if (
                    self.FACTS_FTS_META_TABLE not in present
                    or self.FACTS_FTS_TABLE not in present
                ):
                    return True
                row = conn.execute(
                    text(
                        f"SELECT value FROM {self.FACTS_FTS_META_TABLE} "
                        f"WHERE key = :key"
                    ),
                    {"key": self._FTS_BACKFILL_META_KEY},
                ).fetchone()
                return row is None
        except MaintenanceModeError:
            raise
        except Exception as e:
            # 读不出标记时报「要回填」，与上面打不开只读引擎同一个道理：
            # 报「不用回填」会让调用方把本进程记成已完成，历史 fact 在这个
            # 进程剩下的时间里都不在索引里，而 Stage-2 看上去在工作。报要
            # 回填最多让写路径白跑一次（它自己失败会返回 None，同样不落
            # 标记），下次写入再试。
            logger.debug(f"[TimeIndexedMemory] 检查 FTS 回填标记失败: {e}")
            return True

    def backfill_fact_index(
        self, lanlan_name: str, rows: list[tuple[object, str]],
    ) -> int | None:
        """Bulk-index ``(fact_id, content)`` pairs, then mark the backfill done.

        Returns the number of rows indexed, or ``None`` if the backfill
        could not run — the caller must not record "done" on a ``None``,
        or a single failed attempt would leave the whole history out of
        the index until the next process start.

        Rows already present are skipped, so a partial previous run (or
        a crash between the inserts and the marker) costs a rescan, not
        duplicate index entries.
        """
        self._assert_timeindex_writable(lanlan_name)
        if not self._ensure_engine_exists(lanlan_name):
            return None
        if not self._ensure_fts_table(lanlan_name):
            return None
        stop_names = collect_stop_names(get_config_manager(), lanlan_name)
        try:
            with self.engines[lanlan_name].connect() as conn:
                indexed = {
                    r[0] for r in conn.execute(
                        text(f"SELECT fact_id FROM {self.FACTS_FTS_TABLE}")
                    ).fetchall()
                }
                # 输入按 fact_id 去重（活跃行优先——它排在 rows 前半段）：
                # 归档提交被打断时同一个 id 可以同时躺在 facts.json 和
                # facts_archive.json 里，而 FTS 表没有唯一约束，两份都插进去
                # 会各占一个候选名额，把真正的近重复挤出窗口。
                # 去重键带上类型：id 1 和 "1" 是本仓库刻意区分的两行
                # （_speaker_trust_fact_id 同样按类型标注），合并掉等于把
                # 其中一行永久排除在近重复检索之外。
                seen: set[tuple[str, object]] = set()
                payload = []
                for fact_id, content in rows:
                    try:
                        key = (type(fact_id).__name__, fact_id)
                        skip = fact_id is None or fact_id in indexed or key in seen
                    except TypeError:
                        # 不可哈希的 id（list / dict）：调用方按 _readable_fact_id
                        # 已经滤过一道，这里是第二道——一行畸形数据不该让整轮
                        # 回填抛异常、把标记永远拦在门外。
                        continue
                    if skip:
                        continue
                    seen.add(key)
                    payload.append({
                        "fid": fact_id,
                        "content": " ".join(fts_tokens(content, stop_names)),
                    })
                if payload:
                    conn.execute(
                        text(
                            f"INSERT INTO {self.FACTS_FTS_TABLE}(fact_id, content) "
                            f"VALUES(:fid, :content)"
                        ),
                        payload,
                    )
                conn.execute(
                    text(
                        f"INSERT OR REPLACE INTO {self.FACTS_FTS_META_TABLE}"
                        f"(key, value) VALUES(:key, :value)"
                    ),
                    {
                        "key": self._FTS_BACKFILL_META_KEY,
                        "value": datetime.now().isoformat(),
                    },
                )
                conn.commit()
                return len(payload)
        except Exception as e:
            logger.warning(f"[TimeIndexedMemory] 回填 FTS 索引失败: {e}")
            return None

    async def abackfill_fact_index(
        self, lanlan_name: str, rows: list[tuple[object, str]],
    ) -> int | None:
        return await asyncio.to_thread(
            self.backfill_fact_index, lanlan_name, rows,
        )

    def search_similar_facts(
        self, lanlan_name: str, query: str, limit: int = 10,
    ) -> list[tuple[str, float]]:
        """Find textually near-duplicate facts. Returns [(fact_id, overlap), ...].

        ``overlap`` is the ``token_overlap`` Dice score in 0.0..1.0,
        sorted descending — the *higher*, the more alike. (The predecessor
        ``search_facts`` returned raw bm25(), which runs the other way and
        has no fixed scale; the rename is there so no caller can keep the
        old comparison and silently invert its meaning.)

        FTS5 does the retrieval: an OR over the query's tokens, ranked by
        bm25 and cut at ``limit``, so a large history doesn't have to be
        scored row by row in Python. The returned score is then computed
        from the stored token lists, which is what the caller thresholds.
        """
        try:
            if not self._ensure_engine_exists(lanlan_name, readonly=True):
                return []
            if not self._ensure_fts_table(lanlan_name, readonly=True):
                return []
        except MaintenanceModeError as exc:
            logger.debug(f"[TimeIndexedMemory] 维护态跳过搜索 {lanlan_name} 的 FTS 索引初始化: {exc}")
            return []
        stop_names = collect_stop_names(get_config_manager(), lanlan_name)
        query_tokens = fts_tokens(query, stop_names)
        if not query_tokens:
            # 折叠 + stripping 后什么都没剩——多半是纯名字查询，不让 FTS5
            # 在空 query 上抛 syntax error。
            return []
        try:
            # 每个 token 单独加引号：FTS5 的布尔关键字（AND / OR / NOT）在
            # 裸 term 位置会被当运算符，而拉丁 token 恰好可能就是这几个词。
            # token 里不可能再出现 `"`（fts_tokens 已经先剔掉），所以引号
            # 不会被自身内容截断；OR 连接是 issue #2703 的正题——AND 语义
            # 要求改写后的句子含有原句**全部** token，差一个词就归零，
            # 「近重复」这层闸因此对任何改写都不成立。
            fts_query = ' OR '.join(
                f'"{t}"' for t in dict.fromkeys(query_tokens)
            )
            with self.engines[lanlan_name].connect() as conn:
                result = conn.execute(
                    text(
                        f"SELECT fact_id, content, bm25({self.FACTS_FTS_TABLE}) as score "
                        f"FROM {self.FACTS_FTS_TABLE} "
                        f'WHERE {self.FACTS_FTS_TABLE} MATCH :query '
                        f"ORDER BY score LIMIT :limit"
                    ),
                    {"query": fts_query, "limit": limit}
                )
                scored = [
                    (row[0], token_overlap(query_tokens, str(row[1] or '').split()))
                    for row in result.fetchall()
                ]
            scored.sort(key=lambda item: item[1], reverse=True)
            return scored
        except Exception as e:
            logger.debug(f"[TimeIndexedMemory] FTS5 搜索失败（可能是查询为空或语法）: {e}")
            return []

    async def asearch_similar_facts(
        self, lanlan_name: str, query: str, limit: int = 10,
    ) -> list[tuple[str, float]]:
        return await asyncio.to_thread(
            self.search_similar_facts, lanlan_name, query, limit,
        )

    def delete_fact_from_index(
        self, lanlan_name: str, fact_id: str, *, strict: bool = False,
    ) -> None:
        """Remove a fact from the FTS5 index.

        ``strict`` is used by privacy erasure: any inability to confirm the
        deletion must abort before the authoritative JSON rows are removed.
        """
        self._assert_timeindex_writable(lanlan_name)
        if not self._ensure_engine_exists(lanlan_name):
            if strict:
                raise RuntimeError(
                    f"Unable to initialize time index for {lanlan_name}"
                )
            return
        if not self._ensure_fts_table(lanlan_name):
            if strict:
                raise RuntimeError(
                    f"Unable to initialize facts FTS index for {lanlan_name}"
                )
            return
        try:
            with self.engines[lanlan_name].connect() as conn:
                conn.execute(
                    text(f"DELETE FROM {self.FACTS_FTS_TABLE} WHERE fact_id = :fid"),
                    {"fid": fact_id}
                )
                conn.commit()
        except Exception as e:
            if strict:
                raise RuntimeError(
                    f"Unable to delete fact {fact_id} from FTS index"
                ) from e
            logger.warning(f"[TimeIndexedMemory] 删除 FTS5 索引失败: {e}")

    async def adelete_fact_from_index(
        self, lanlan_name: str, fact_id: str, *, strict: bool = False,
    ) -> None:
        await asyncio.to_thread(
            self.delete_fact_from_index,
            lanlan_name,
            fact_id,
            strict=strict,
        )
