"""
XML记忆存储工具函数
用于将LangChain消息对象转换为XML格式，以及从XML格式恢复
"""
import xml.etree.ElementTree as ET
from xml.dom import minidom
try:
    # 使用defusedxml来安全解析外部输入的XML，防止XXE攻击
    from defusedxml.ElementTree import fromstring as safe_fromstring
except ImportError:
    # 如果defusedxml未安装，回退到标准库（不推荐，存在XXE风险）
    import warnings
    warnings.warn("defusedxml未安装，XML解析存在XXE安全风险。建议安装: pip install defusedxml", stacklevel=2)
    safe_fromstring = ET.fromstring
from langchain_core.messages import (
    BaseMessage, HumanMessage, AIMessage, SystemMessage,
    messages_to_dict, messages_from_dict
)
from typing import List, Dict, Any
import json


def escape_xml_text(text: str) -> str:
    """转义XML特殊字符"""
    if not isinstance(text, str):
        text = str(text)
    return (text
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
            .replace("'", '&apos;'))


def unescape_xml_text(text: str) -> str:
    """反转义XML特殊字符"""
    if not isinstance(text, str):
        text = str(text)
    return (text
            .replace('&lt;', '<')
            .replace('&gt;', '>')
            .replace('&quot;', '"')
            .replace('&apos;', "'")
            .replace('&amp;', '&'))


def message_to_xml_element(message: BaseMessage) -> ET.Element:
    """将单个消息转换为XML元素"""
    msg_elem = ET.Element('message')
    msg_elem.set('type', getattr(message, 'type', 'unknown'))
    
    # 处理消息内容
    content = getattr(message, 'content', '')
    if isinstance(content, str):
        content_elem = ET.SubElement(msg_elem, 'content')
        content_elem.text = content
    elif isinstance(content, list):
        content_elem = ET.SubElement(msg_elem, 'content')
        for item in content:
            if isinstance(item, dict):
                item_elem = ET.SubElement(content_elem, 'item')
                item_elem.set('type', item.get('type', 'text'))
                if 'text' in item:
                    item_elem.text = item['text']
                # 保存其他字段
                for key, value in item.items():
                    if key not in ['type', 'text']:
                        item_elem.set(key, str(value))
            else:
                item_elem = ET.SubElement(content_elem, 'item')
                item_elem.text = str(item)
    else:
        content_elem = ET.SubElement(msg_elem, 'content')
        content_elem.text = str(content)
    
    # 保存其他属性
    if hasattr(message, 'id') and message.id:
        msg_elem.set('id', str(message.id))
    if hasattr(message, 'name') and message.name:
        msg_elem.set('name', str(message.name))
    
    # 保存additional_kwargs
    if hasattr(message, 'additional_kwargs') and message.additional_kwargs:
        kwargs_elem = ET.SubElement(msg_elem, 'additional_kwargs')
        kwargs_elem.text = json.dumps(message.additional_kwargs, ensure_ascii=False)
    
    # 保存response_metadata
    if hasattr(message, 'response_metadata') and message.response_metadata:
        metadata_elem = ET.SubElement(msg_elem, 'response_metadata')
        metadata_elem.text = json.dumps(message.response_metadata, ensure_ascii=False)
    
    # 保存tool_calls（如果是AI消息）
    if hasattr(message, 'tool_calls') and message.tool_calls:
        tool_calls_elem = ET.SubElement(msg_elem, 'tool_calls')
        tool_calls_elem.text = json.dumps(message.tool_calls, ensure_ascii=False)
    
    # 保存usage_metadata
    if hasattr(message, 'usage_metadata') and message.usage_metadata:
        usage_elem = ET.SubElement(msg_elem, 'usage_metadata')
        usage_elem.text = json.dumps(message.usage_metadata, ensure_ascii=False)
    
    return msg_elem


def messages_to_xml(messages: List[BaseMessage], pretty_print: bool = True) -> str:
    """将消息列表转换为XML字符串"""
    root = ET.Element('conversation_history')
    root.set('version', '1.0')
    root.set('message_count', str(len(messages)))
    
    for msg in messages:
        msg_elem = message_to_xml_element(msg)
        root.append(msg_elem)
    
    # 转换为字符串
    xml_str = ET.tostring(root, encoding='unicode')
    
    if pretty_print:
        # 美化输出
        dom = minidom.parseString(xml_str)
        return dom.toprettyxml(indent='  ', encoding=None)
    else:
        return xml_str


def xml_element_to_message(elem: ET.Element) -> BaseMessage:
    """将XML元素转换为消息对象"""
    msg_type = elem.get('type', 'human')
    
    # 读取内容
    content_elem = elem.find('content')
    if content_elem is not None:
        # 检查是否有子元素
        items = content_elem.findall('item')
        if items:
            content = []
            for item_elem in items:
                item_type = item_elem.get('type', 'text')
                item_text = item_elem.text or ''
                item_dict = {'type': item_type, 'text': item_text}
                # 读取其他属性
                for key, value in item_elem.attrib.items():
                    if key != 'type':
                        item_dict[key] = value
                content.append(item_dict)
        else:
            content = content_elem.text or ''
    else:
        content = ''
    
    # 创建消息对象
    if msg_type == 'human':
        message = HumanMessage(content=content)
    elif msg_type == 'ai':
        message = AIMessage(content=content)
    elif msg_type == 'system':
        message = SystemMessage(content=content)
    else:
        # 默认使用HumanMessage
        message = HumanMessage(content=content)
    
    # 恢复其他属性
    if elem.get('id'):
        message.id = elem.get('id')
    if elem.get('name'):
        message.name = elem.get('name')
    
    # 恢复additional_kwargs
    kwargs_elem = elem.find('additional_kwargs')
    if kwargs_elem is not None and kwargs_elem.text:
        try:
            message.additional_kwargs = json.loads(kwargs_elem.text)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    
    # 恢复response_metadata
    metadata_elem = elem.find('response_metadata')
    if metadata_elem is not None and metadata_elem.text:
        try:
            message.response_metadata = json.loads(metadata_elem.text)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    
    # 恢复tool_calls
    tool_calls_elem = elem.find('tool_calls')
    if tool_calls_elem is not None and tool_calls_elem.text:
        try:
            message.tool_calls = json.loads(tool_calls_elem.text)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    
    # 恢复usage_metadata
    usage_elem = elem.find('usage_metadata')
    if usage_elem is not None and usage_elem.text:
        try:
            message.usage_metadata = json.loads(usage_elem.text)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    
    return message


def messages_from_xml(xml_str: str) -> List[BaseMessage]:
    """从XML字符串恢复消息列表（使用安全解析，防止XXE攻击）"""
    try:
        root = safe_fromstring(xml_str)
        messages = []
        for msg_elem in root.findall('message'):
            message = xml_element_to_message(msg_elem)
            messages.append(message)
        return messages
    except ET.ParseError as e:
        raise ValueError(f"XML解析错误: {e}")


def dict_to_xml(data: Dict[str, Any], root_name: str = 'data') -> str:
    """将字典转换为XML字符串"""
    root = ET.Element(root_name)
    
    def dict_to_elem(parent, d):
        for key, value in d.items():
            if isinstance(value, dict):
                child = ET.SubElement(parent, str(key))
                dict_to_elem(child, value)
            elif isinstance(value, list):
                child = ET.SubElement(parent, str(key))
                for item in value:
                    if isinstance(item, dict):
                        item_elem = ET.SubElement(child, 'item')
                        dict_to_elem(item_elem, item)
                    else:
                        item_elem = ET.SubElement(child, 'item')
                        item_elem.text = str(item)
            else:
                child = ET.SubElement(parent, str(key))
                child.text = str(value) if value is not None else ''
    
    dict_to_elem(root, data)
    
    dom = minidom.parseString(ET.tostring(root, encoding='unicode'))
    return dom.toprettyxml(indent='  ', encoding=None)


def xml_to_dict(xml_str: str) -> Dict[str, Any]:
    """从XML字符串恢复字典（使用安全解析，防止XXE攻击）"""
    root = safe_fromstring(xml_str)
    
    def elem_to_dict(elem):
        result = {}
        for child in elem:
            if len(child) == 0:
                # 叶子节点：处理重复键，转换为列表
                if child.tag in result:
                    if not isinstance(result[child.tag], list):
                        result[child.tag] = [result[child.tag]]
                    result[child.tag].append(child.text or '')
                else:
                    result[child.tag] = child.text or ''
            else:
                # 检查子元素是否都是 item（列表结构）
                items = child.findall('item')
                if items:
                    result[child.tag] = [elem_to_dict(item) for item in items]
                else:
                    # 处理重复键，转换为列表
                    if child.tag in result:
                        if not isinstance(result[child.tag], list):
                            result[child.tag] = [result[child.tag]]
                        result[child.tag].append(elem_to_dict(child))
                    else:
                        result[child.tag] = elem_to_dict(child)
        return result
    
    return elem_to_dict(root)

