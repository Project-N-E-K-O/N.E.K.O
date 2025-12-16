# XML记忆存储和围栏测试指南

## 测试准备

### 1. 启动服务器
使用之前创建的 `start_servers.bat` 启动服务器，或手动运行：
```bash
# 在项目目录下
uv run python memory_server.py
uv run python main_server.py
```

### 2. 访问Web界面
打开浏览器访问：`http://localhost:48911`

---

## 测试1：XML记忆存储格式

### 测试步骤

1. **检查记忆文件格式**
   - 在浏览器中与AI进行一些对话
   - 对话后，检查记忆文件是否已转换为XML格式
   - 文件位置：`我的文档/N.E.K.O/memory/recent_{角色名}.xml`

2. **验证XML格式**
   - 打开记忆文件，应该看到类似这样的XML格式：
   ```xml
   <?xml version="1.0" encoding="UTF-8"?>
   <conversation_history version="1.0" message_count="2">
     <message type="human">
       <content>你好</content>
     </message>
     <message type="ai">
       <content>你好！有什么可以帮助你的吗？</content>
     </message>
   </conversation_history>
   ```

3. **测试向后兼容**
   - 如果有旧的 `.json` 文件，系统应该自动转换为 `.xml`
   - 检查日志中是否有转换提示

### 验证方法

**方法1：直接查看文件**
```powershell
# 在PowerShell中
cd $env:USERPROFILE\Documents\N.E.K.O\memory
Get-Content recent_*.xml | Select-Object -First 20
```

**方法2：通过Web界面**
- 访问 `http://localhost:48911/memory_browser`
- 查看记忆浏览器，应该能正常显示对话历史

**方法3：检查日志**
- 查看控制台输出，应该看到类似：
  ```
  [RecentHistory] 已将 {角色名} 的历史记录从JSON转换为XML
  ```

---

## 测试2：XML围栏机制

### 测试步骤

#### 测试2.1：文本模式围栏（OmniOfflineClient）

1. **配置使用文本模式**
   - 确保使用非实时API（如普通的文本API）
   - 在对话中，让AI生成包含 `<stop>` 标签的回复

2. **测试场景**
   - 向AI发送："请简短回答，并在回答末尾添加 `<stop>` 标签"
   - 观察AI回复是否在 `<stop>` 处被截断

3. **预期结果**
   - AI回复应该在 `<stop>` 标签处停止
   - 控制台应该显示：`OmniOfflineClient: 围栏触发 - 检测到XML停止标签 <stop>，截断输出`
   - 前端不应该显示 `<stop>` 标签本身

#### 测试2.2：主动对话围栏（system_router）

1. **触发主动对话**
   - 等待AI主动搭话，或通过API触发
   - 在AI回复中包含 `<stop>` 标签

2. **验证截断**
   - 检查AI回复是否被正确截断
   - 查看日志：`[{角色名}] AI回复包含XML停止标签，将截断内容并继续`

### 测试用例

#### 用例1：基本围栏测试
```
用户输入：请说"测试123"然后添加<stop>标签
预期输出：测试123
（不应该包含<stop>标签）
```

#### 用例2：围栏在中间
```
用户输入：请说"前半部分<stop>后半部分"
预期输出：前半部分
（后半部分被截断）
```

#### 用例3：多个stop标签
```
用户输入：请说"第一段<stop>第二段<stop>第三段"
预期输出：第一段
（在第一个<stop>处截断）
```

#### 用例4：大小写不敏感
```
用户输入：请说"测试<STOP>继续"
预期输出：测试
（大写STOP也应该被检测）
```

---

## 测试3：手动测试脚本

### Python测试脚本

创建一个测试文件 `test_xml_fence.py`：

```python
import re

def test_xml_fence_detection():
    """测试XML围栏检测逻辑"""
    
    # 测试用例
    test_cases = [
        ("正常文本", False),
        ("测试<stop>", True),
        ("测试</stop>", True),
        ("测试<stop/>", True),
        ("测试<STOP>", True),  # 大小写不敏感
        ("前半部分<stop>后半部分", True),
        ("多个<stop>标签<stop>测试", True),
    ]
    
    stop_pattern = re.compile(r'<stop\s*/?>|</stop\s*>', re.IGNORECASE)
    
    print("XML围栏检测测试：")
    print("=" * 50)
    
    for text, should_trigger in test_cases:
        match = stop_pattern.search(text)
        triggered = match is not None
        
        status = "✓" if triggered == should_trigger else "✗"
        print(f"{status} 文本: {text[:30]}")
        print(f"   预期: {'触发' if should_trigger else '不触发'}, 实际: {'触发' if triggered else '不触发'}")
        
        if triggered:
            fence_pos = match.start()
            result = text[:fence_pos]
            print(f"   截断结果: {result}")
        print()
    
    print("=" * 50)

if __name__ == "__main__":
    test_xml_fence_detection()
```

运行测试：
```bash
uv run python test_xml_fence.py
```

---

## 测试4：检查记忆文件

### 查看记忆文件内容

```python
# test_memory_files.py
import os
from pathlib import Path
from utils.config_manager import get_config_manager
from utils.xml_memory_utils import messages_from_xml

def check_memory_files():
    """检查记忆文件格式"""
    cm = get_config_manager()
    memory_dir = Path(cm.memory_dir)
    
    print("检查记忆文件：")
    print("=" * 50)
    
    # 查找所有XML文件
    xml_files = list(memory_dir.glob("recent_*.xml"))
    json_files = list(memory_dir.glob("recent_*.json"))
    
    print(f"找到 {len(xml_files)} 个XML文件")
    print(f"找到 {len(json_files)} 个JSON文件（应该被转换）")
    print()
    
    for xml_file in xml_files[:3]:  # 只检查前3个
        print(f"文件: {xml_file.name}")
        try:
            with open(xml_file, 'r', encoding='utf-8') as f:
                content = f.read()
                messages = messages_from_xml(content)
                print(f"  ✓ XML格式正确，包含 {len(messages)} 条消息")
                if messages:
                    print(f"  第一条消息类型: {messages[0].type}")
                    print(f"  内容预览: {str(messages[0].content)[:50]}...")
        except Exception as e:
            print(f"  ✗ 错误: {e}")
        print()

if __name__ == "__main__":
    check_memory_files()
```

---

## 常见问题排查

### 问题1：记忆文件仍然是JSON格式
**解决方案：**
- 检查是否有旧的JSON文件存在
- 系统会在首次读取时自动转换
- 可以手动删除JSON文件，让系统创建新的XML文件

### 问题2：围栏不工作
**检查项：**
1. 确认使用的是文本模式（非实时模式）
2. 检查日志中是否有围栏触发的消息
3. 确认AI回复中确实包含了 `<stop>` 标签

### 问题3：XML文件格式错误
**解决方案：**
- 检查文件编码是否为UTF-8
- 查看控制台错误日志
- 尝试重新生成记忆文件

---

## 快速测试命令

### PowerShell快速检查
```powershell
# 检查XML文件
Get-ChildItem "$env:USERPROFILE\Documents\N.E.K.O\memory\recent_*.xml" | Select-Object Name, Length, LastWriteTime

# 查看XML文件内容（前20行）
Get-Content "$env:USERPROFILE\Documents\N.E.K.O\memory\recent_*.xml" -TotalCount 20
```

### 检查日志
在运行服务器的控制台中，查找以下关键词：
- `XML`
- `围栏触发`
- `转换为XML`
- `XML停止标签`

---

## 测试检查清单

- [ ] 记忆文件已转换为XML格式
- [ ] XML文件可以正常读取和解析
- [ ] 向后兼容：旧的JSON文件被自动转换
- [ ] XML围栏在文本模式下正常工作
- [ ] XML围栏在主动对话中正常工作
- [ ] 大小写不敏感的围栏检测
- [ ] 跨块检测正常工作
- [ ] 日志中显示正确的围栏触发信息

---

## 需要帮助？

如果测试中遇到问题，请检查：
1. 服务器日志输出
2. 浏览器控制台（F12）
3. 记忆文件的实际内容
4. API配置是否正确

