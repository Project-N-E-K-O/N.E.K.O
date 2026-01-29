"""
批量更新导入路径脚本

将旧的导入路径更新为新的目录结构。
"""
import re
from pathlib import Path

# 导入路径映射
IMPORT_MAPPINGS = {
    'from plugin.server.executor import': 'from plugin.server.infrastructure.executor import',
    'from plugin.server.auth import': 'from plugin.server.infrastructure.auth import',
    'from plugin.server.utils import': 'from plugin.server.infrastructure.utils import',
    'from plugin.server.exceptions import': 'from plugin.server.infrastructure.exceptions import',
    'from plugin.server.error_handler import': 'from plugin.server.infrastructure.error_handler import',
    'from plugin.server.metrics_service import': 'from plugin.server.monitoring.metrics import',
    'from plugin.server.message_plane_runner import': 'from plugin.server.messaging.plane_runner import',
    'from plugin.server.message_plane_bridge import': 'from plugin.server.messaging.plane_bridge import',
    'from plugin.server.bus_subscriptions import': 'from plugin.server.messaging.bus_subscriptions import',
    'from plugin.server.ws_admin import': 'from plugin.server.websocket.admin import',
    'from plugin.server.ws_run import': 'from plugin.server.runs.websocket import',
    'from plugin.server.runs import': 'from plugin.server.runs.manager import',
    'from plugin.server.blob_store import': 'from plugin.server.runs.storage import',
}

def update_file(file_path: Path) -> bool:
    """更新单个文件的导入路径"""
    try:
        content = file_path.read_text(encoding='utf-8')
        original_content = content
        
        for old_import, new_import in IMPORT_MAPPINGS.items():
            content = content.replace(old_import, new_import)
        
        if content != original_content:
            file_path.write_text(content, encoding='utf-8')
            print(f"✓ Updated: {file_path}")
            return True
        return False
    except Exception as e:
        print(f"✗ Error updating {file_path}: {e}")
        return False

def main():
    plugin_dir = Path(__file__).parent
    
    # 需要更新的文件模式
    patterns = [
        'server/**/*.py',
        'core/**/*.py',
        'runtime/**/*.py',
        'user_plugin_server.py',
    ]
    
    updated_count = 0
    for pattern in patterns:
        for file_path in plugin_dir.glob(pattern):
            if file_path.name == 'update_imports.py':
                continue
            if update_file(file_path):
                updated_count += 1
    
    print(f"\n总计更新了 {updated_count} 个文件")

if __name__ == '__main__':
    main()
