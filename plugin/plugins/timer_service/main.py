"""
定时器服务插件主模块

提供定时器管理功能，其他插件可以通过调用此插件来实现定时任务。
使用自定义事件实现定时触发。
"""
import threading
import time
from typing import Dict, Any, Optional
from plugin.sdk.base import NekoPluginBase
from plugin.sdk import ok, fail, ErrorCode
from plugin.sdk.decorators import (
    neko_plugin,
    lifecycle,
    plugin_entry,
    custom_event,
)


@neko_plugin
class TimerServicePlugin(NekoPluginBase):
    """定时器服务插件"""
    
    def __init__(self, ctx):
        super().__init__(ctx)
        # 启用文件日志
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        
        # 定时器存储：{timer_id: {info, thread, stop_event}}
        self._timers: Dict[str, Dict[str, Any]] = {}
        self._timer_lock = threading.Lock()
        
        self.logger.info("TimerServicePlugin 初始化完成")
    
    @lifecycle(id="startup")
    def startup(self, **_):
        """插件启动"""
        self.logger.info("TimerServicePlugin 已启动")
        self.report_status({
            "status": "running",
            "timer_count": len(self._timers)
        })
        return ok(data={"status": "ready"})
    
    @lifecycle(id="shutdown")
    def shutdown(self, **_):
        """插件关闭时停止所有定时器"""
        self.logger.info("TimerServicePlugin 正在关闭，停止所有定时器...")
        with self._timer_lock:
            timer_ids = list(self._timers.keys())
        
        for timer_id in timer_ids:
            self._stop_timer_internal(timer_id)
        
        self.logger.info(f"已停止 {len(timer_ids)} 个定时器")
        return ok(data={"status": "stopped"})
    
    @custom_event(
        event_type="timer_tick",
        id="handle_timer_tick",
        name="定时器触发事件",
        description="处理定时器触发事件",
        input_schema={
            "type": "object",
            "properties": {
                "timer_id": {"type": "string"},
                "callback_plugin_id": {"type": "string"},
                "callback_entry_id": {"type": "string"},
                "callback_args": {"type": "object"}
            },
            "required": ["timer_id"]
        },
        trigger_method="auto",
        auto_start=False
    )
    def _handle_timer_tick(
        self,
        timer_id: str,
        callback_plugin_id: Optional[str] = None,
        callback_entry_id: Optional[str] = None,
        callback_args: Optional[Dict[str, Any]] = None
    ):
        """处理定时器触发事件"""
        try:
            self.logger.debug(f"[TimerService] 定时器 '{timer_id}' 触发")
            
            # 更新定时器统计
            with self._timer_lock:
                if timer_id in self._timers:
                    self._timers[timer_id]["tick_count"] = self._timers[timer_id].get("tick_count", 0) + 1
                    self._timers[timer_id]["last_tick_time"] = time.time()
                    
                    # 准备回调参数，更新 current_count
                    final_callback_args = {}
                    if callback_args:
                        # 复制原始回调参数
                        final_callback_args = callback_args.copy()
                        # 更新 current_count 为当前的 tick_count
                        final_callback_args["current_count"] = self._timers[timer_id]["tick_count"]
                    else:
                        # 如果没有提供 callback_args，至少包含 tick_count
                        final_callback_args["current_count"] = self._timers[timer_id]["tick_count"]
            
            # 检查是否达到最大次数（在锁外检查，避免阻塞）
            max_count = None
            if callback_args and "max_count" in callback_args:
                max_count = callback_args.get("max_count", 0)
            
            # 如果有回调插件，触发回调（在锁外执行，避免阻塞）
            if callback_plugin_id and callback_entry_id:
                self._trigger_callback(
                    callback_plugin_id,
                    callback_entry_id,
                    final_callback_args
                )
            
            # 如果达到最大次数，自动停止定时器（在回调之后，避免死锁）
            if max_count and max_count > 0:
                should_stop = False
                with self._timer_lock:
                    if timer_id in self._timers:
                        tick_count = self._timers[timer_id].get("tick_count", 0)
                        if tick_count >= max_count:
                            self.logger.info(
                                f"[TimerService] 定时器 '{timer_id}' 已达到最大次数 {max_count}，自动停止"
                            )
                            should_stop = True
                
                # 在锁外停止定时器，避免死锁
                if should_stop:
                    stop_result = self._stop_timer_internal(timer_id)
                    if stop_result.get("success"):
                        self.logger.info(
                            f"[TimerService] 定时器 '{timer_id}' 已自动停止"
                        )
        except Exception as e:
            self.logger.exception(f"[TimerService] 处理定时器 '{timer_id}' 触发时出错: {e}")
    
    def _trigger_callback(self, plugin_id: str, entry_id: str, args: Dict[str, Any]):
        """触发回调插件的入口点（使用 Queue 机制，单线程处理）"""
        try:
            # 使用 Queue 机制调用其他插件（和 plugin_entry 一样，在单线程中处理）
            result = self.plugins.call_entry(
                f"{plugin_id}:{entry_id}",
                args=args,
                timeout=10.0,
            )
            self.logger.debug(
                f"[TimerService] 成功触发回调: {plugin_id}.{entry_id}, result={result}"
            )
        except Exception as e:
            self.logger.exception(
                f"[TimerService] 触发回调失败: {plugin_id}.{entry_id}, 错误: {e}"
            )
    
    def _run_timer_loop(
        self,
        timer_id: str,
        interval: float,
        callback_plugin_id: Optional[str],
        callback_entry_id: Optional[str],
        callback_args: Optional[Dict[str, Any]],
        stop_event: threading.Event,
        immediate: bool
    ):
        """定时器循环线程"""
        try:
            if immediate:
                # 立即执行一次
                self._handle_timer_tick(
                    timer_id=timer_id,
                    callback_plugin_id=callback_plugin_id,
                    callback_entry_id=callback_entry_id,
                    callback_args=callback_args
                )
            
            # 定时循环
            while not stop_event.is_set():
                if stop_event.wait(interval):
                    break
                
                self._handle_timer_tick(
                    timer_id=timer_id,
                    callback_plugin_id=callback_plugin_id,
                    callback_entry_id=callback_entry_id,
                    callback_args=callback_args
                )
        except (KeyboardInterrupt, SystemExit):
            self.logger.info(f"[TimerService] 定时器 '{timer_id}' 被中断")
        except Exception as e:
            self.logger.exception(f"[TimerService] 定时器 '{timer_id}' 循环出错: {e}")
    
    @plugin_entry(
        id="start_timer",
        name="启动定时器",
        description="启动一个定时器，可以设置回调插件和入口点",
        input_schema={
            "type": "object",
            "properties": {
                "timer_id": {
                    "type": "string",
                    "description": "定时器ID（唯一标识）"
                },
                "interval": {
                    "type": "number",
                    "description": "定时器间隔（秒）",
                    "minimum": 0.1
                },
                "immediate": {
                    "type": "boolean",
                    "description": "是否立即执行一次",
                    "default": False
                },
                "callback_plugin_id": {
                    "type": "string",
                    "description": "回调插件ID（可选）"
                },
                "callback_entry_id": {
                    "type": "string",
                    "description": "回调入口点ID（可选，需要与 callback_plugin_id 一起使用）"
                },
                "callback_args": {
                    "type": "object",
                    "description": "回调参数（可选）"
                }
            },
            "required": ["timer_id", "interval"]
        }
    )
    def start_timer(
        self,
        timer_id: Optional[str] = None,
        interval: Optional[float] = None,
        interval_seconds: Optional[float] = None,  # 别名参数
        immediate: bool = False,
        callback_plugin_id: Optional[str] = None,
        callback_plugin: Optional[str] = None,  # 别名参数
        callback_entry_id: Optional[str] = None,
        callback_entry: Optional[str] = None,  # 别名参数
        callback_args: Optional[Dict[str, Any]] = None,
        **kwargs
    ):
        """启动定时器"""
        # 参数名称兼容性处理
        if timer_id is None:
            timer_id = kwargs.get("timer_id")
        if timer_id is None:
            return fail(ErrorCode.VALIDATION_ERROR, "Missing required parameter: timer_id")
        
        # interval 参数处理（支持 interval 和 interval_seconds 别名）
        if interval is None:
            interval = interval_seconds or kwargs.get("interval") or kwargs.get("interval_seconds")
        if interval is None:
            return fail(ErrorCode.VALIDATION_ERROR, "Missing required parameter: interval")
        
        # callback_plugin_id 参数处理（支持 callback_plugin 别名）
        if callback_plugin_id is None:
            callback_plugin_id = callback_plugin or kwargs.get("callback_plugin_id") or kwargs.get("callback_plugin")
        
        # callback_entry_id 参数处理（支持 callback_entry 别名）
        if callback_entry_id is None:
            callback_entry_id = callback_entry or kwargs.get("callback_entry_id") or kwargs.get("callback_entry")
        
        # callback_args 参数处理
        if callback_args is None:
            callback_args = kwargs.get("callback_args")
        
        # immediate 参数处理
        if "immediate" in kwargs:
            immediate = kwargs.get("immediate", immediate)
        
        with self._timer_lock:
            if timer_id in self._timers:
                return fail(ErrorCode.VALIDATION_ERROR, f"Timer '{timer_id}' already exists")
            
            # 创建停止事件
            stop_event = threading.Event()
            
            # 保存定时器信息
            timer_info = {
                "timer_id": timer_id,
                "interval": interval,
                "started_at": time.time(),
                "tick_count": 0,
                "last_tick_time": None,
                "callback_plugin_id": callback_plugin_id,
                "callback_entry_id": callback_entry_id,
                "callback_args": callback_args or {},
                "stop_event": stop_event,
                "thread": None
            }
            
            # 创建定时器线程
            thread = threading.Thread(
                target=self._run_timer_loop,
                args=(
                    timer_id,
                    interval,
                    callback_plugin_id,
                    callback_entry_id,
                    callback_args,
                    stop_event,
                    immediate
                ),
                name=f"Timer-{timer_id}",
                daemon=True
            )
            thread.start()
            timer_info["thread"] = thread
            self._timers[timer_id] = timer_info
        
        self.logger.info(
            f"[TimerService] 启动定时器 '{timer_id}': 间隔={interval}s, "
            f"立即执行={immediate}, 回调={callback_plugin_id}.{callback_entry_id if callback_entry_id else 'None'}"
        )
        
        self.report_status({
            "status": "running",
            "timer_count": len(self._timers),
            "timers": list(self._timers.keys())
        })
        
        return ok(data={"timer_id": timer_id, "interval": interval, "immediate": immediate})
    
    @plugin_entry(
        id="stop_timer",
        name="停止定时器",
        description="停止指定的定时器",
        input_schema={
            "type": "object",
            "properties": {
                "timer_id": {
                    "type": "string",
                    "description": "定时器ID"
                }
            },
            "required": ["timer_id"]
        }
    )
    def stop_timer(self, timer_id: str, **_):
        """停止定时器"""
        return self._stop_timer_internal(timer_id)
    
    def _stop_timer_internal(self, timer_id: str) -> Dict[str, Any]:
        """内部方法：停止定时器"""
        # 在锁内获取定时器信息和统计信息，然后立即释放锁
        with self._timer_lock:
            if timer_id not in self._timers:
                return fail(ErrorCode.NOT_FOUND, f"Timer '{timer_id}' not found")
            
            timer_info = self._timers[timer_id]
            stop_event = timer_info["stop_event"]
            thread = timer_info["thread"]
            
            # 获取统计信息（在删除前）
            tick_count = timer_info.get("tick_count", 0)
            started_at = timer_info.get("started_at", time.time())
            
            # 删除定时器（在锁内删除，防止其他线程访问）
            del self._timers[timer_id]
        
        # 在锁外设置停止事件和等待线程（避免死锁）
        # 注意：此时定时器已从 _timers 中删除，但线程可能还在运行
        # _handle_timer_tick 会检查 timer_id 是否存在，所以是安全的
        stop_event.set()
        
        # 等待线程结束（在锁外，避免死锁）
        if thread and thread.is_alive():
            # 如果在定时器线程内调用停止（例如达到最大次数的回调里），不能 join 当前线程
            if thread is threading.current_thread():
                self.logger.debug("[TimerService] Skip joining current timer thread for '%s'", timer_id)
            else:
                thread.join(timeout=2.0)
        
        # 计算运行时间（在锁外）
        elapsed = time.time() - started_at
        
        self.logger.info(
            f"[TimerService] 停止定时器 '{timer_id}': "
            f"执行次数={tick_count}, 运行时间={elapsed:.1f}s"
        )
        
        # 更新状态（需要重新获取锁来读取当前定时器数量）
        with self._timer_lock:
            timer_count = len(self._timers)
            timer_ids = list(self._timers.keys())
        
        self.report_status({
            "status": "running",
            "timer_count": timer_count,
            "timers": timer_ids
        })
        
        return ok(data={"timer_id": timer_id, "tick_count": tick_count, "elapsed": elapsed})
    
    @plugin_entry(
        id="stop_all_timers",
        name="停止所有定时器",
        description="停止所有正在运行的定时器"
    )
    def stop_all_timers(self, **_):
        """停止所有定时器"""
        with self._timer_lock:
            timer_ids = list(self._timers.keys())
        
        results = []
        for timer_id in timer_ids:
            result = self._stop_timer_internal(timer_id)
            results.append(result)
        
        self.logger.info(f"[TimerService] 已停止 {len(results)} 个定时器")
        
        return ok(data={"stopped_count": len(results), "results": results})
    
    @plugin_entry(
        id="get_timer_info",
        name="获取定时器信息",
        description="获取指定定时器的信息",
        input_schema={
            "type": "object",
            "properties": {
                "timer_id": {
                    "type": "string",
                    "description": "定时器ID"
                }
            },
            "required": ["timer_id"]
        }
    )
    def get_timer_info(self, timer_id: str, **_):
        """获取定时器信息"""
        with self._timer_lock:
            if timer_id not in self._timers:
                return fail(ErrorCode.NOT_FOUND, f"Timer '{timer_id}' not found")
            
            timer_info = self._timers[timer_id]
            thread = timer_info.get("thread")
            
            return ok(
                data={
                    "timer_id": timer_id,
                    "interval": timer_info["interval"],
                    "started_at": timer_info["started_at"],
                    "tick_count": timer_info.get("tick_count", 0),
                    "last_tick_time": timer_info.get("last_tick_time"),
                    "running": thread.is_alive() if thread else False,
                    "callback_plugin_id": timer_info.get("callback_plugin_id"),
                    "callback_entry_id": timer_info.get("callback_entry_id"),
                }
            )
    
    @plugin_entry(
        id="list_timers",
        name="列出所有定时器",
        description="获取所有定时器的列表和信息"
    )
    def list_timers(self, **_):
        """列出所有定时器"""
        with self._timer_lock:
            timers = []
            for timer_id, timer_info in self._timers.items():
                thread = timer_info.get("thread")
                timers.append({
                    "timer_id": timer_id,
                    "interval": timer_info["interval"],
                    "started_at": timer_info["started_at"],
                    "tick_count": timer_info.get("tick_count", 0),
                    "last_tick_time": timer_info.get("last_tick_time"),
                    "running": thread.is_alive() if thread else False,
                    "callback_plugin_id": timer_info.get("callback_plugin_id"),
                    "callback_entry_id": timer_info.get("callback_entry_id")
                })
        
        return ok(data={"timer_count": len(timers), "timers": timers})

