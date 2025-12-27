# -*- coding: utf-8 -*-
"""
N.E.K.O. 统一启动器
启动所有服务器，等待它们准备就绪后启动主程序，并监控主程序状态
"""
import sys
import os
import io
import signal

# 强制 UTF-8 编码
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    
# 处理 PyInstaller 和 Nuitka 打包后的路径
if getattr(sys, 'frozen', False):
    # 运行在打包后的环境
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller
        bundle_dir = sys._MEIPASS
    else:
        # Nuitka 或其他
        bundle_dir = os.path.dirname(os.path.abspath(__file__))
    
    app_dir = os.path.dirname(sys.executable)
else:
    # 运行在正常 Python 环境
    bundle_dir = os.path.dirname(os.path.abspath(__file__))
    app_dir = bundle_dir

sys.path.insert(0, bundle_dir)
os.chdir(bundle_dir)

import subprocess
import socket
import time
import threading
import itertools
from typing import List, Dict
from multiprocessing import Process, freeze_support, Event
from config import MAIN_SERVER_PORT, MEMORY_SERVER_PORT, TOOL_SERVER_PORT

# 服务器配置
SERVERS = [
    {
        'name': 'Memory Server',
        'module': 'memory_server',
        'port': MEMORY_SERVER_PORT,
        'process': None,
        'ready_event': None,
    },
    {
        'name': 'Agent Server', 
        'module': 'agent_server',
        'port': TOOL_SERVER_PORT,
        'process': None,
        'ready_event': None,
    },
    {
        'name': 'Main Server',
        'module': 'main_server',
        'port': MAIN_SERVER_PORT,
        'process': None,
        'ready_event': None,
    },
]

# 不再启动主程序，用户自己启动 lanlan_frd.exe

def run_memory_server(ready_event: Event):
    """运行 Memory Server"""
    try:
        # 确保工作目录正确
        if getattr(sys, 'frozen', False):
            if hasattr(sys, '_MEIPASS'):
                # PyInstaller
                os.chdir(sys._MEIPASS)
            else:
                # Nuitka
                os.chdir(os.path.dirname(os.path.abspath(__file__)))
            # 禁用 typeguard（子进程需要重新禁用）
            try:
                import typeguard
                def dummy_typechecked(func=None, **kwargs):
                    return func if func else (lambda f: f)
                typeguard.typechecked = dummy_typechecked
                if hasattr(typeguard, '_decorators'):
                    typeguard._decorators.typechecked = dummy_typechecked
            except: # noqa
                pass
        
        import memory_server
        import uvicorn
        
        print(f"[Memory Server] Starting on port {MEMORY_SERVER_PORT}")
        
        # 使用 Server 对象，在启动后通知父进程
        config = uvicorn.Config(
            app=memory_server.app,
            host="127.0.0.1",
            port=MEMORY_SERVER_PORT,
            log_level="error"
        )
        server = uvicorn.Server(config)
        
        # 在后台线程中运行服务器
        import asyncio
        
        async def run_with_notify():
            # 启动服务器
            await server.serve()
        
        # 启动线程来运行服务器，并在启动后通知
        def run_server():
            # 创建事件循环
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # 添加启动完成的回调
            async def startup():
                print(f"[Memory Server] Running on port {MEMORY_SERVER_PORT}")
                ready_event.set()
            
            # 将 startup 添加到服务器的启动事件
            server.config.app.add_event_handler("startup", startup)
            
            # 运行服务器
            loop.run_until_complete(server.serve())
        
        run_server()
        
    except Exception as e:
        print(f"Memory Server error: {e}")
        import traceback
        traceback.print_exc()

def run_agent_server(ready_event: Event):
    """运行 Agent Server (不需要等待初始化)"""
    try:
        # 确保工作目录正确
        if getattr(sys, 'frozen', False):
            if hasattr(sys, '_MEIPASS'):
                # PyInstaller
                os.chdir(sys._MEIPASS)
            else:
                # Nuitka
                os.chdir(os.path.dirname(os.path.abspath(__file__)))
            # 禁用 typeguard（子进程需要重新禁用）
            try:
                import typeguard
                def dummy_typechecked(func=None, **kwargs):
                    return func if func else (lambda f: f)
                typeguard.typechecked = dummy_typechecked
                if hasattr(typeguard, '_decorators'):
                    typeguard._decorators.typechecked = dummy_typechecked
            except: # noqa
                pass
        
        import agent_server
        import uvicorn
        
        print(f"[Agent Server] Starting on port {TOOL_SERVER_PORT}")
        
        # Agent Server 不需要等待，立即通知就绪
        ready_event.set()
        
        uvicorn.run(agent_server.app, host="127.0.0.1", port=TOOL_SERVER_PORT, log_level="error", log_config=None)
    except Exception as e:
        print(f"Agent Server error: {e}")
        import traceback
        traceback.print_exc()

def run_main_server(ready_event: Event):
    """运行 Main Server"""
    try:
        # 确保工作目录正确
        if getattr(sys, 'frozen', False):
            if hasattr(sys, '_MEIPASS'):
                # PyInstaller
                os.chdir(sys._MEIPASS)
            else:
                # Nuitka
                os.chdir(os.path.dirname(os.path.abspath(__file__)))
        
        print("[Main Server] Importing main_server module...")
        import main_server
        import uvicorn
        
        print(f"[Main Server] Starting on port {MAIN_SERVER_PORT}")
        
        # 直接运行 FastAPI app，不依赖 main_server 的 __main__ 块
        config = uvicorn.Config(
            app=main_server.app,
            host="127.0.0.1",
            port=MAIN_SERVER_PORT,
            log_level="error",
            loop="asyncio",
            reload=False,
        )
        server = uvicorn.Server(config)
        
        # 添加启动完成的回调
        async def startup():
            print(f"[Main Server] Running on port {MAIN_SERVER_PORT}")
            ready_event.set()
        
        # 将 startup 添加到服务器的启动事件
        main_server.app.add_event_handler("startup", startup)
        
        # 运行服务器
        server.run()
    except Exception as e:
        print(f"Main Server error: {e}")
        import traceback
        traceback.print_exc()

def check_port(port: int, timeout: float = 0.5) -> bool:
    """检查端口是否已开放"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex(('127.0.0.1', port))
        sock.close()
        return result == 0
    except: # noqa
        return False

def show_spinner(stop_event: threading.Event, message: str = "正在启动服务器"):
    """显示转圈圈动画"""
    spinner = itertools.cycle(['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'])
    while not stop_event.is_set():
        sys.stdout.write(f'\r{message}... {next(spinner)} ')
        sys.stdout.flush()
        time.sleep(0.1)
    sys.stdout.write('\r' + ' ' * 60 + '\r')  # 清除动画行
    sys.stdout.write('\n')  # 换行，确保后续输出在新行
    sys.stdout.flush()

def start_server(server: Dict) -> bool:
    """启动单个服务器"""
    try:
        # 根据模块名选择启动函数
        if server['module'] == 'memory_server':
            target_func = run_memory_server
        elif server['module'] == 'agent_server':
            target_func = run_agent_server
        elif server['module'] == 'main_server':
            target_func = run_main_server
        else:
            print(f"✗ {server['name']} 未知模块", flush=True)
            return False
        
        # 创建进程间同步事件
        server['ready_event'] = Event()
        
        # 使用 multiprocessing 启动服务器
        # 注意：不能设置 daemon=True，因为 main_server 自己会创建子进程
        server['process'] = Process(target=target_func, args=(server['ready_event'],), daemon=False)
        server['process'].start()
        
        print(f"✓ {server['name']} 已启动 (PID: {server['process'].pid})", flush=True)
        return True
    except Exception as e:
        print(f"✗ {server['name']} 启动失败: {e}", flush=True)
        return False

def wait_for_servers(timeout: int = 60) -> bool:
    """等待所有服务器启动完成"""
    print("\n等待服务器准备就绪...", flush=True)
    
    # 启动动画线程
    stop_spinner = threading.Event()
    spinner_thread = threading.Thread(target=show_spinner, args=(stop_spinner, "检查服务器状态"))
    spinner_thread.daemon = True
    spinner_thread.start()
    
    start_time = time.time()
    all_ready = False
    
    # 第一步：等待所有端口就绪
    while time.time() - start_time < timeout:
        ready_count = 0
        for server in SERVERS:
            if check_port(server['port']) or server['port']==TOOL_SERVER_PORT:
                ready_count += 1
        
        if ready_count == len(SERVERS):
            break
        
        time.sleep(0.5)
    
    # 第二步：等待所有服务器的 ready_event（同步初始化完成）
    if ready_count == len(SERVERS):
        for server in SERVERS:
            remaining_time = timeout - (time.time() - start_time)
            if remaining_time > 0:
                if server['ready_event'].wait(timeout=remaining_time):
                    continue
                else:
                    # 超时
                    break
        else:
            # 所有服务器都就绪了
            all_ready = True
    
    # 停止动画
    stop_spinner.set()
    spinner_thread.join()
    
    if all_ready:
        print("\n", flush=True)
        print("=" * 60, flush=True)
        print("✓✓✓  所有服务器已准备就绪！  ✓✓✓", flush=True)
        print("=" * 60, flush=True)
        print("\n", flush=True)
        return True
    else:
        print("\n", flush=True)
        print("=" * 60, flush=True)
        print("✗ 服务器启动超时，请检查日志文件", flush=True)
        print("=" * 60, flush=True)
        print("\n", flush=True)
        # 显示未就绪的服务器
        for server in SERVERS:
            if not server['ready_event'].is_set():
                print(f"  - {server['name']} 初始化未完成", flush=True)
            elif not check_port(server['port']):
                print(f"  - {server['name']} 端口 {server['port']} 未就绪", flush=True)
        return False


def cleanup_servers():
    """清理所有服务器进程"""
    print("\n正在关闭服务器...", flush=True)
    for server in SERVERS:
        if server['process'] and server['process'].is_alive():
            try:
                # 先尝试温和地终止
                server['process'].terminate()
                server['process'].join(timeout=3)
                if not server['process'].is_alive():
                    print(f"✓ {server['name']} 已关闭", flush=True)
                else:
                    # 如果还活着，强制杀死
                    server['process'].kill()
                    server['process'].join(timeout=2)
                    print(f"✓ {server['name']} 已强制关闭", flush=True)
            except Exception as e:
                print(f"✗ {server['name']} 关闭失败: {e}", flush=True)

def main():
    """主函数"""
    # 支持 multiprocessing 在 Windows 上的打包
    freeze_support()
    
    print("=" * 60, flush=True)
    print("N.E.K.O. 服务器启动器", flush=True)
    print("=" * 60, flush=True)
    
    try:
        # 1. 启动所有服务器
        print("\n正在启动服务器...\n", flush=True)
        all_started = True
        for server in SERVERS:
            if not start_server(server):
                all_started = False
                break
        
        if not all_started:
            print("\n启动失败，正在清理...", flush=True)
            cleanup_servers()
            return 1
        
        # 2. 等待服务器准备就绪
        if not wait_for_servers():
            print("\n启动失败，正在清理...", flush=True)
            cleanup_servers()
            return 1
        
        # 3. 服务器已启动，等待用户操作
        print("", flush=True)
        print("=" * 60, flush=True)
        print("  🎉 所有服务器已启动完成！", flush=True)
        print("\n  现在你可以：", flush=True)
        print("  1. 启动 lanlan_frd.exe 使用系统", flush=True)
        print("  2. 在浏览器访问 http://localhost:48911", flush=True)
        print("\n  按 Ctrl+C 关闭所有服务器", flush=True)
        print("=" * 60, flush=True)
        print("", flush=True)
        
        # 持续运行，监控服务器状态
        while True:
            time.sleep(1)
            # 检查服务器是否还活着
            all_alive = all(
                server['process'] and server['process'].is_alive() 
                for server in SERVERS
            )
            if not all_alive:
                print("\n检测到服务器异常退出！", flush=True)
                break
        
    except KeyboardInterrupt:
        print("\n\n收到中断信号，等待子进程退出...", flush=True)
        # 子进程已经收到了 SIGINT，给它们一点时间自己退出
        start_wait = time.time()
        while time.time() - start_wait < 3:
            all_dead = True
            for server in SERVERS:
                if server.get('process') and server['process'].is_alive():
                    all_dead = False
                    break
            if all_dead:
                break
            time.sleep(0.1)
            
    except Exception as e:
        print(f"\n发生错误: {e}", flush=True)
    finally:
        print("\n正在关闭所有进程...", flush=True)
        
        # 尝试优雅关闭
        cleanup_servers()
        
        # 等待一段时间，确认进程是否真的无法终止
        print("\n等待进程清理完成...", flush=True)
        time.sleep(2)
        
        # 检查是否还有存活的进程
        has_alive = any(
            server.get('process') and server['process'].is_alive()
            for server in SERVERS
        )
        
        if has_alive:
            print("\n检测到进程未能正常退出，尝试强制终止...", flush=True)
            
            try:
                if hasattr(os, 'killpg'):
                    pgid = os.getpgrp()
                    # 先尝试 SIGTERM 允许清理
                    os.killpg(pgid, signal.SIGTERM)
                    time.sleep(1)
                    # 如果还活着，再使用 SIGKILL
                    os.killpg(pgid, signal.SIGKILL)
                else:
                    # Windows: 使用 taskkill 强制杀死进程树
                    import subprocess
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(os.getpid())], 
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                # 强制终止失败，忽略错误（进程可能已经退出）
                print(f"强制终止进程组时出错（可能进程已退出）: {e}", flush=True)
        
        print("\n清理完成", flush=True)
        # 如果还有残留进程，使用非零退出码
        # 注意：如果 has_alive 为 True，sys.exit(1) 会终止程序，不会执行 return 0
        # 如果 has_alive 为 False，所有进程正常退出，继续执行 return 0
        if has_alive:
            sys.exit(1)
    
    # 所有进程正常退出，返回成功退出码
    return 0

if __name__ == "__main__":
    sys.exit(main())

