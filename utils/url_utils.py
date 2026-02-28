from urllib.parse import quote, unquote


def encode_url_path(path: str) -> str:
    """
    对 URL 路径段做安全编码,避免空格/特殊字符导致静态资源加载失败.
    仅编码路径段本身,保留 '/' 分隔结构.
    """
    if not path:
        return path

    parts = path.split('/')
    encoded_parts = [quote(unquote(part), safe='') for part in parts]
    return '/'.join(encoded_parts)
