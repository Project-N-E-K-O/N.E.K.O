# -*- coding: utf-8 -*-
"""
鲁棒的日志配置模块
适用于exe封装后的应用，支持：
- 自动选择合适的日志目录（用户数据目录）
- 日志轮转（按大小和时间）
- 自动清理旧日志
- 降级策略（当无法写入时的备用方案）
- 跨平台支持
"""
import os
import sys
import logging
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from pathlib import Path
from datetime import datetime, timedelta
import shutil

from config import APP_NAME


class RobustLoggerConfig:
    """鲁棒的日志配置类"""
    
    # 默认配置
    DEFAULT_LOG_LEVEL = logging.INFO
    DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10MB per log file
    DEFAULT_BACKUP_COUNT = 5  # Keep 5 backup files
    DEFAULT_LOG_RETENTION_DAYS = 30  # Keep logs for 30 days
    
    def __init__(self, app_name=None, service_name=None, log_level=None, max_bytes=None, 
                 backup_count=None, retention_days=None):
        """
        初始化日志配置
        
        Args:
            app_name: 应用名称，用于创建日志目录，默认使用配置中的 APP_NAME
            service_name: 服务名称，用于区分不同服务的日志文件（如Main、Memory、Agent）
            log_level: 日志级别
            max_bytes: 单个日志文件的最大大小
            backup_count: 保留的备份文件数量
            retention_days: 日志保留天数
        """
        self.app_name = app_name if app_name is not None else APP_NAME
        self.service_name = service_name  # 服务名称用于文件名区分
        self.log_level = log_level or self.DEFAULT_LOG_LEVEL
        self.max_bytes = max_bytes or self.DEFAULT_MAX_BYTES
        self.backup_count = backup_count or self.DEFAULT_BACKUP_COUNT
        self.retention_days = retention_days or self.DEFAULT_LOG_RETENTION_DAYS
        
        # 获取日志目录
        self.log_dir = self._get_log_directory()
        
        # 日志文件名：如果有service_name则包含，否则只用app_name
        if self.service_name:
            log_filename = f"{self.app_name}_{self.service_name}_{datetime.now().strftime('%Y%m%d')}.log"
        else:
            log_filename = f"{self.app_name}_{datetime.now().strftime('%Y%m%d')}.log"
        self.log_file = self.log_dir / log_filename
        
        # 确保日志目录存在
        self._ensure_log_directory()
        
        # 清理旧日志
        self._cleanup_old_logs()
    
    def _get_log_directory(self):
        """
        获取合适的日志目录
        优先级：
        1. 用户文档目录/{APP_NAME}/logs（我的文档，默认首选）
        2. 应用程序所在目录/logs
        3. 用户数据目录（AppData等）
        4. 用户主目录
        5. 临时目录（最后的降级选项）
        
        Returns:
            Path: 日志目录路径
        """
        # 尝试1: 使用用户文档目录（我的文档，默认首选！）
        try:
            docs_dir = self._get_documents_directory()
            # 使用配置的应用名称目录
            log_dir = docs_dir / self.app_name / "logs"
            if self._test_directory_writable(log_dir):
                return log_dir
        except Exception as e:
            print(f"Warning: Failed to use Documents directory: {e}", file=sys.stderr)
        
        # 尝试2: 使用应用程序所在目录
        try:
            # 对于exe打包的应用，使用exe所在目录
            if getattr(sys, 'frozen', False):
                # 如果是打包后的exe
                app_dir = Path(sys.executable).parent
            else:
                # 如果是脚本运行，使用项目根目录
                app_dir = Path.cwd()
            
            log_dir = app_dir / "logs"
            if self._test_directory_writable(log_dir):
                return log_dir
        except Exception as e:
            print(f"Warning: Failed to use application directory: {e}", file=sys.stderr)
        
        # 尝试3: 使用系统用户数据目录
        try:
            if sys.platform == "win32":
                # Windows: %APPDATA%\AppName\logs
                base_dir = os.getenv('APPDATA')
                if base_dir:
                    log_dir = Path(base_dir) / self.app_name / "logs"
                    if self._test_directory_writable(log_dir):
                        return log_dir
            elif sys.platform == "darwin":
                # macOS: ~/Library/Application Support/AppName/logs
                base_dir = Path.home() / "Library" / "Application Support"
                log_dir = base_dir / self.app_name / "logs"
                if self._test_directory_writable(log_dir):
                    return log_dir
            else:
                # Linux: ~/.local/share/AppName/logs
                xdg_data_home = os.getenv('XDG_DATA_HOME')
                if xdg_data_home:
                    log_dir = Path(xdg_data_home) / self.app_name / "logs"
                else:
                    log_dir = Path.home() / ".local" / "share" / self.app_name / "logs"
                if self._test_directory_writable(log_dir):
                    return log_dir
        except Exception as e:
            print(f"Warning: Failed to get system data directory: {e}", file=sys.stderr)
        
        # 尝试4: 使用用户主目录
        try:
            log_dir = Path.home() / f".{self.app_name}" / "logs"
            if self._test_directory_writable(log_dir):
                return log_dir
        except Exception as e:
            print(f"Warning: Failed to use home directory: {e}", file=sys.stderr)
        
        # 尝试5: 使用临时目录（最后的降级选项）
        try:
            import tempfile
            log_dir = Path(tempfile.gettempdir()) / self.app_name / "logs"
            if self._test_directory_writable(log_dir):
                return log_dir
        except Exception as e:
            print(f"Warning: Failed to use temp directory: {e}", file=sys.stderr)
        
        # 如果所有方法都失败，返回当前目录
        print(f"Warning: All log directory attempts failed, using current directory", file=sys.stderr)
        return Path.cwd() / "logs"
    
    def _get_documents_directory(self):
        """获取系统的用户文档目录（使用系统API）"""
        if sys.platform == "win32":
            # Windows: 使用系统API获取真正的"我的文档"路径
            try:
                import ctypes
                from ctypes import windll, wintypes
                
                # 使用SHGetFolderPath获取我的文档路径
                CSIDL_PERSONAL = 5  # My Documents
                SHGFP_TYPE_CURRENT = 0
                
                buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
                windll.shell32.SHGetFolderPathW(None, CSIDL_PERSONAL, None, SHGFP_TYPE_CURRENT, buf)
                docs_dir = Path(buf.value)
                
                if docs_dir.exists():
                    return docs_dir
            except Exception as e:
                print(f"Warning: Failed to get Documents path via API: {e}", file=sys.stderr)
            
            # 降级：尝试从注册表读取
            try:
                import winreg
                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
                )
                docs_dir = Path(winreg.QueryValueEx(key, "Personal")[0])
                winreg.CloseKey(key)
                
                # 展开环境变量
                docs_dir = Path(os.path.expandvars(str(docs_dir)))
                if docs_dir.exists():
                    return docs_dir
            except Exception as e:
                print(f"Warning: Failed to get Documents path from registry: {e}", file=sys.stderr)
            
            # 最后的降级
            docs_dir = Path.home() / "Documents"
            if not docs_dir.exists():
                docs_dir = Path.home() / "文档"
            return docs_dir
        
        elif sys.platform == "darwin":
            # macOS
            return Path.home() / "Documents"
        else:
            # Linux: 尝试使用XDG
            xdg_docs = os.getenv('XDG_DOCUMENTS_DIR')
            if xdg_docs:
                return Path(xdg_docs)
            return Path.home() / "Documents"
    
    def _test_directory_writable(self, directory):
        """
        测试目录是否可写
        
        Args:
            directory: 要测试的目录
            
        Returns:
            bool: 是否可写
        """
        try:
            # 分步创建目录，避免parents=True在打包后可能出现的问题
            # 收集所有需要创建的父目录
            dirs_to_create = []
            current = directory
            while current and not current.exists():
                dirs_to_create.append(current)
                current = current.parent
            
            # 从最顶层开始创建目录
            for dir_path in reversed(dirs_to_create):
                if not dir_path.exists():
                    dir_path.mkdir(exist_ok=True)
            
            # 尝试创建一个测试文件
            test_file = directory / ".write_test"
            test_file.write_text("test")
            test_file.unlink()
            return True
        except Exception:
            return False
    
    def _ensure_log_directory(self):
        """确保日志目录存在"""
        try:
            # 分步创建目录，避免parents=True在打包后可能出现的问题
            dirs_to_create = []
            current = self.log_dir
            while current and not current.exists():
                dirs_to_create.append(current)
                current = current.parent
            
            # 从最顶层开始创建目录
            for dir_path in reversed(dirs_to_create):
                if not dir_path.exists():
                    dir_path.mkdir(exist_ok=True)
        except Exception as e:
            print(f"Error: Failed to create log directory: {e}", file=sys.stderr)
            raise
    
    def _cleanup_old_logs(self):
        """清理超过保留期的旧日志文件"""
        try:
            cutoff_date = datetime.now() - timedelta(days=self.retention_days)
            
            for log_file in self.log_dir.glob(f"{self.app_name}_*.log*"):
                try:
                    # 获取文件的修改时间
                    file_mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
                    
                    # 如果文件太旧，删除它
                    if file_mtime < cutoff_date:
                        log_file.unlink()
                        print(f"Cleaned up old log file: {log_file.name}")
                except Exception as e:
                    print(f"Warning: Failed to clean up log file {log_file}: {e}", file=sys.stderr)
        except Exception as e:
            print(f"Warning: Failed to cleanup old logs: {e}", file=sys.stderr)
    
    def get_log_file_path(self):
        """获取日志文件路径"""
        return str(self.log_file)
    
    def get_log_directory_path(self):
        """获取日志目录路径"""
        return str(self.log_dir)
    
    def setup_logger(self, logger_name=None):
        """
        配置并返回logger实例
        
        Args:
            logger_name: logger的名称，如果为None则返回root logger
            
        Returns:
            logging.Logger: 配置好的logger实例
        """
        # 创建或获取logger
        logger = logging.getLogger(logger_name)
        logger.setLevel(self.log_level)
        
        # 避免重复添加handler
        if logger.handlers:
            return logger
        
        # 日志格式
        log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        date_format = '%Y-%m-%d %H:%M:%S'
        formatter = logging.Formatter(log_format, date_format)
        
        # 1. 控制台Handler
        try:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(self.log_level)
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)
        except Exception as e:
            print(f"Warning: Failed to add console handler: {e}", file=sys.stderr)
        
        # 2. 文件Handler（带轮转）
        try:
            # 使用RotatingFileHandler进行按大小轮转
            file_handler = RotatingFileHandler(
                self.log_file,
                maxBytes=self.max_bytes,
                backupCount=self.backup_count,
                encoding='utf-8'
            )
            file_handler.setLevel(self.log_level)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except Exception as e:
            print(f"Error: Failed to add file handler: {e}", file=sys.stderr)
            # 文件handler失败不应该阻止应用运行
        
        # 3. 错误日志Handler（单独记录ERROR及以上级别）
        try:
            if self.service_name:
                error_filename = f"{self.app_name}_{self.service_name}_error.log"
            else:
                error_filename = f"{self.app_name}_error.log"
            error_log_file = self.log_dir / error_filename
            error_handler = RotatingFileHandler(
                error_log_file,
                maxBytes=self.max_bytes,
                backupCount=self.backup_count,
                encoding='utf-8'
            )
            error_handler.setLevel(logging.ERROR)
            error_handler.setFormatter(formatter)
            logger.addHandler(error_handler)
        except Exception as e:
            print(f"Warning: Failed to add error handler: {e}", file=sys.stderr)
        
        return logger


class EnhancedLogger:
    """增强的Logger包装器，自动处理traceback"""
    
    def __init__(self, logger):
        self._logger = logger
    
    def __getattr__(self, name):
        """代理所有其他方法到原始logger"""
        return getattr(self._logger, name)
    
    def error(self, msg, *args, exc_info=None, **kwargs):
        """
        增强的error方法，自动包含traceback
        
        Args:
            msg: 错误消息
            exc_info: 是否包含异常信息，默认True（自动检测）
            *args, **kwargs: 传递给原始logger.error的其他参数
        """
        # 如果在异常上下文中且未明确指定exc_info，自动设置为True
        if exc_info is None:
            import sys
            exc_info = sys.exc_info()[0] is not None
        
        self._logger.error(msg, *args, exc_info=exc_info, **kwargs)
    
    def exception(self, msg, *args, **kwargs):
        """异常记录方法（始终包含traceback）"""
        self._logger.exception(msg, *args, **kwargs)


def setup_logging(app_name=None, service_name=None, log_level=None):
    """
    便捷函数：设置日志配置
    
    Args:
        app_name: 应用名称，默认使用配置中的 APP_NAME（用于确定日志目录）
        service_name: 服务名称，用于区分不同服务的日志文件（如Main、Memory、Agent）
        log_level: 日志级别
        
    Returns:
        tuple: (增强的logger实例, 日志配置对象)
        
    注意：
        返回的logger会自动在error()调用时包含traceback（如果在异常上下文中）
        也可以使用logger.exception()来明确记录异常信息
    """
    config = RobustLoggerConfig(app_name=app_name, service_name=service_name, log_level=log_level)
    base_logger = config.setup_logger()
    
    # 包装为增强logger
    logger = EnhancedLogger(base_logger)
    
    # 记录日志配置信息
    service_info = f"{service_name}" if service_name else config.app_name
    logger.info(f"=== {service_info} 日志系统已初始化 ===")
    logger.info(f"日志目录: {config.get_log_directory_path()}")
    logger.info(f"日志级别: {logging.getLevelName(config.log_level)}")
    logger.info("=" * 50)
    
    return logger, config


# 导出主要接口
__all__ = ['RobustLoggerConfig', 'EnhancedLogger', 'setup_logging']


if __name__ == "__main__":
    # 测试代码
    logger, config = setup_logging("TestApp")
    
    logger.debug("这是一条debug消息")
    logger.info("这是一条info消息")
    logger.warning("这是一条warning消息")
    logger.error("这是一条error消息")
    
    print(f"\n日志已保存到: {config.get_log_file_path()}")

