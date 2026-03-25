"""加密服务

封装RC4加密、签名生成等加密相关功能。
"""

import base64
import hashlib
import json
import os
import time
from typing import Any, Dict


class CryptoService:
    """加密服务

    封装RC4加密、签名生成等加密相关功能。
    所有方法都是静态方法，不依赖外部状态，便于单元测试。
    """

    @staticmethod
    def rc4_encrypt(data: bytes, key: bytes) -> bytes:
        """RC4加密

        使用RC4流密码算法加密数据。
        注意：按照米家API的要求，需要先丢弃前1024字节的密钥流。

        Args:
            data: 待加密的字节数据
            key: 加密密钥（字节）

        Returns:
            加密后的字节数据
        """
        # 初始化S盒
        S = list(range(256))
        j = 0

        # KSA (Key Scheduling Algorithm) - 密钥调度算法
        for i in range(256):
            j = (j + S[i] + key[i % len(key)]) % 256
            S[i], S[j] = S[j], S[i]

        # PRGA (Pseudo-Random Generation Algorithm) - 伪随机生成算法
        # 先丢弃前1024字节的密钥流（米家API要求）
        i = j = 0
        for _ in range(1024):
            i = (i + 1) % 256
            j = (j + S[i]) % 256
            S[i], S[j] = S[j], S[i]

        # 加密数据
        result = []
        for byte in data:
            i = (i + 1) % 256
            j = (j + S[i]) % 256
            S[i], S[j] = S[j], S[i]
            K = S[(S[i] + S[j]) % 256]
            result.append(byte ^ K)

        return bytes(result)

    @staticmethod
    def rc4_decrypt(data: bytes, key: bytes) -> bytes:
        """RC4解密

        RC4是对称加密算法，解密过程与加密相同。

        Args:
            data: 待解密的字节数据
            key: 解密密钥（字节）

        Returns:
            解密后的字节数据
        """
        return CryptoService.rc4_encrypt(data, key)

    @staticmethod
    def generate_nonce() -> str:
        """生成随机nonce

        按照米家API的格式生成nonce：8字节随机数 + 时间戳（分钟）。

        Returns:
            Base64编码的nonce字符串
        """
        # 8字节有符号随机数
        random_bytes = (int.from_bytes(os.urandom(8), "big") - 2**63).to_bytes(
            8, "big", signed=True
        )
        
        # 时间戳（分钟）
        millis = int(time.time() * 1000)
        part2 = int(millis / 60000)
        timestamp_bytes = part2.to_bytes((part2.bit_length() + 7) // 8, "big")
        
        nonce = random_bytes + timestamp_bytes
        return base64.b64encode(nonce).decode()

    @staticmethod
    def get_signed_nonce(ssecurity: str, nonce: str) -> str:
        """生成签名nonce

        使用SHA256对ssecurity和nonce进行哈希。

        Args:
            ssecurity: Base64编码的安全密钥
            nonce: Base64编码的nonce

        Returns:
            Base64编码的签名nonce
        """
        m = hashlib.sha256()
        m.update(base64.b64decode(ssecurity))
        m.update(base64.b64decode(nonce))
        return base64.b64encode(m.digest()).decode()

    @staticmethod
    def generate_signature(
        uri: str, method: str, signed_nonce: str, params: Dict[str, str]
    ) -> str:
        """生成请求签名

        使用SHA1算法生成请求签名。

        Args:
            uri: API路径
            method: HTTP方法（GET/POST）
            signed_nonce: 签名nonce
            params: 请求参数字典

        Returns:
            Base64编码的签名字符串
        """
        # 构建签名参数列表
        signature_params = [method.upper(), uri]
        
        # 添加所有参数（按key=value格式）
        for k, v in params.items():
            signature_params.append(f"{k}={v}")
        
        # 添加签名nonce
        signature_params.append(signed_nonce)
        
        # 用&连接所有参数
        signature_string = "&".join(signature_params)
        
        # 生成SHA1签名
        return base64.b64encode(
            hashlib.sha1(signature_string.encode()).digest()
        ).decode()

    @staticmethod
    def decrypt_response(
        response_text: str, ssecurity: str, nonce: str
    ) -> str:
        """解密响应数据

        使用RC4解密响应，如果是GZIP压缩的则解压。

        Args:
            response_text: Base64编码的加密响应
            ssecurity: Base64编码的安全密钥
            nonce: Base64编码的nonce

        Returns:
            解密后的JSON字符串
        """
        import gzip
        from io import BytesIO

        # 生成签名nonce
        signed_nonce = CryptoService.get_signed_nonce(ssecurity, nonce)

        # 使用signed_nonce解密响应
        key = base64.b64decode(signed_nonce)
        encrypted_data = base64.b64decode(response_text)
        decrypted = CryptoService.rc4_decrypt(encrypted_data, key)

        # 尝试直接解码为UTF-8
        try:
            return decrypted.decode("utf-8")
        except UnicodeDecodeError:
            # 如果失败，说明是GZIP压缩的，需要解压
            compressed_file = BytesIO(decrypted)
            return gzip.GzipFile(fileobj=compressed_file, mode="rb").read().decode("utf-8")

    @staticmethod
    def encrypt_params(
        uri: str, data: Dict[str, Any], ssecurity: str
    ) -> Dict[str, str]:
        """加密请求参数

        按照米家API的要求加密请求参数。

        Args:
            uri: API路径
            data: 请求数据字典
            ssecurity: Base64编码的安全密钥

        Returns:
            加密后的请求参数字典，包含：
            - data: 加密的数据
            - signature: 请求签名
            - ssecurity: 安全密钥
            - _nonce: nonce值
        """
        # JSON序列化（紧凑格式，无空格）
        data_str = json.dumps(data, separators=(",", ":"))

        # 生成nonce和签名nonce
        nonce = CryptoService.generate_nonce()
        signed_nonce = CryptoService.get_signed_nonce(ssecurity, nonce)

        # 初始参数
        params = {"data": data_str}

        # 生成rc4_hash__签名
        rc4_hash = CryptoService.generate_signature(uri, "POST", signed_nonce, params)
        params["rc4_hash__"] = rc4_hash

        # 使用signed_nonce加密所有参数
        key = base64.b64decode(signed_nonce)
        encrypted_params = {}
        for k, v in params.items():
            encrypted = CryptoService.rc4_encrypt(v.encode(), key)
            encrypted_params[k] = base64.b64encode(encrypted).decode()

        # 生成最终签名
        signature = CryptoService.generate_signature(
            uri, "POST", signed_nonce, encrypted_params
        )

        # 添加签名和其他参数
        encrypted_params.update(
            {"signature": signature, "ssecurity": ssecurity, "_nonce": nonce}
        )

        return encrypted_params
