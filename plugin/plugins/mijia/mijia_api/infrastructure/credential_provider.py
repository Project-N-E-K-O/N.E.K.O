"""凭据提供者

负责获取和刷新用户凭据。
"""

import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict
from typing import Optional, Tuple

import asyncio
import httpx
from qrcode.main import QRCode

from ..core.config import ConfigManager
from ..core.logging import get_logger
from ..domain.exceptions import LoginFailedError, TokenExpiredError
from ..domain.models import Credential

logger = get_logger(__name__)


class CredentialProvider:
    """凭据提供者

    负责从米家服务器获取和刷新凭据，独立于业务逻辑。
    """

    def __init__(self, config: ConfigManager):
        """初始化凭据提供者

        Args:
            config: 配置管理器
        """
        self._config = config
        self._client = httpx.Client(timeout=config.get("DEFAULT_TIMEOUT", 30))

    def login_by_qrcode(self) -> Credential:
        """通过二维码登录获取凭据

        Returns:
            Credential: 包含用户认证信息的凭据对象

        Raises:
            LoginFailedError: 登录失败
        """
        logger.info("开始二维码登录流程")

        try:
            # Step 1: 从 serviceLogin 获取登录链接参数
            location_data = self._get_location()
            
            # 如果已经有有效的token，直接返回
            if location_data.get("code") == 0 and location_data.get("message") == "刷新Token成功":
                logger.info("Token仍然有效，无需重新登录")
                # 从现有的auth_data构建凭据
                # 注意：这里需要确保有完整的凭据信息
                raise LoginFailedError("请使用已保存的凭据，无需重新登录")

            # Step 2: 获取二维码URL和轮询URL
            qr_data = self._get_qrcode_data(location_data)
            self._display_qrcode(qr_data["loginUrl"])
            print(f"也可以访问链接查看二维码图片: {qr_data['qr']}")

            # Step 3: 长轮询等待扫码
            login_result = self._long_poll_for_scan(qr_data["lp"])

            # Step 4: 访问callback获取cookies
            callback_url = login_result["location"]
            response = self._client.get(callback_url)
            
            # 从cookies中提取serviceToken
            service_token = response.cookies.get("serviceToken")
            if not service_token:
                raise LoginFailedError("未能从callback获取serviceToken")

            # Step 5: 构建凭据对象
            credential = Credential(
                user_id=str(login_result["userId"]),
                service_token=service_token,
                ssecurity=login_result["ssecurity"],
                pass_token=login_result.get("passToken", ""),  # 添加passToken
                c_user_id=str(login_result.get("cUserId", login_result["userId"])),  # cUserId
                device_id=self._generate_device_id(),
                user_agent=self._generate_user_agent(),
                expires_at=self._calculate_expires_at({}),
            )

            logger.info(f"登录成功，用户ID: {credential.user_id}")
            return credential

        except LoginFailedError:
            raise
        except Exception as e:
            logger.error(f"二维码登录失败: {e}")
            raise LoginFailedError(f"二维码登录失败: {e}") from e

    def refresh(self, credential: Credential) -> Credential:
        """刷新凭据

        Args:
            credential: 需要刷新的旧凭据对象

        Returns:
            Credential: 刷新后的新凭据对象

        Raises:
            TokenExpiredError: 凭据刷新失败
        """
        logger.info(f"刷新凭据，用户ID: {credential.user_id}")

        # 检查是否有passToken
        if not credential.pass_token:
            raise TokenExpiredError(
                "凭据缺少passToken，无法刷新。请重新登录以获取包含passToken的新凭据。"
            )

        try:
            # 使用现有的凭据信息刷新
            new_token_data = self._refresh_service_token(credential)

            # 创建新的凭据对象，保留passToken
            new_credential = Credential(
                user_id=credential.user_id,
                service_token=new_token_data["serviceToken"],
                ssecurity=new_token_data["ssecurity"],
                pass_token=credential.pass_token,  # 保留原有的passToken
                c_user_id=credential.c_user_id,
                device_id=credential.device_id,
                user_agent=credential.user_agent,
                expires_at=self._calculate_expires_at(new_token_data),
            )

            logger.info(f"凭据刷新成功，用户ID: {credential.user_id}")
            return new_credential

        except TokenExpiredError:
            raise  # 已经是最具体的异常，直接透传
        except Exception as e:
            logger.error(f"凭据刷新失败: {e}")
            # 仅当确定是鉴权/Token 失效时才抛 TokenExpiredError
            # 网络超时、解析错误等应透传原异常
            error_str = str(e).lower()
            if any(kw in error_str for kw in ["401", "403", "token", "expired", "unauthorized", "invalid"]):
                raise TokenExpiredError(f"凭据刷新失败: {e}") from e
            raise  # 非鉴权错误直接透传

    def revoke(self, credential: Credential) -> bool:
        """撤销凭据

        Args:
            credential: 要撤销的凭据对象

        Returns:
            bool: 撤销是否成功
        """
        logger.info(f"撤销凭据，用户ID: {credential.user_id}")

        try:
            # 调用API撤销token
            url = f"{self._config.get('LOGIN_URL')}/pass/logout"
            response = self._client.post(
                url,
                headers={"User-Agent": credential.user_agent},
                json={"serviceToken": credential.service_token},
            )

            if response.status_code == 200:
                logger.info(f"凭据撤销成功，用户ID: {credential.user_id}")
                return True
            else:
                logger.warning(f"凭据撤销失败，状态码: {response.status_code}")
                return False

        except Exception as e:
            logger.error(f"撤销凭据时发生错误: {e}")
            return False

    def _get_location(self) -> Dict[str, Any]:
        """获取登录location参数

        Returns:
            Dict[str, Any]: location参数字典

        Raises:
            LoginFailedError: 获取失败
        """
        try:
            # 使用米家的sid
            url = f"{self._config.get('SERVICE_LOGIN_URL')}?_json=true&sid=mijia&_locale=zh_CN"
            response = self._client.get(url)
            response.raise_for_status()

            # 解析响应
            data = response.text.replace("&&&START&&&", "")
            import json

            result = json.loads(data)

            # 如果code=0，说明已经有有效的token
            if result.get("code") == 0:
                return {"code": 0, "message": "刷新Token成功"}

            # 否则返回location参数
            location = result.get("location", "")
            if not location:
                raise LoginFailedError(f"获取location失败: {result.get('desc')}")

            # 解析location中的参数
            from urllib import parse
            location_data = parse.parse_qs(parse.urlparse(location).query)
            return {k: v[0] for k, v in location_data.items()}

        except Exception as e:
            logger.error(f"获取location失败: {e}")
            raise LoginFailedError(f"获取location失败: {e}") from e

    def _get_qrcode_data(self, location_data: Dict[str, Any]) -> Dict[str, Any]:
        """获取二维码数据

        Args:
            location_data: location参数

        Returns:
            Dict[str, Any]: 包含二维码URL和轮询URL的字典

        Raises:
            LoginFailedError: 获取失败
        """
        try:
            from urllib import parse
            
            # 添加额外参数
            location_data.update({
                "theme": "",
                "bizDeviceType": "",
                "_hasLogo": "false",
                "_qrsize": "240",
                "_dc": str(int(time.time() * 1000)),
            })
            
            # 构建URL
            login_url = self._config.get("LOGIN_URL")
            url = f"{login_url}/longPolling/loginUrl?" + parse.urlencode(location_data)
            
            response = self._client.get(url)
            response.raise_for_status()

            # 解析响应
            data = response.text.replace("&&&START&&&", "")
            import json

            result = json.loads(data)

            if result.get("code") != 0:
                raise LoginFailedError(f"获取二维码失败: {result.get('desc')}")

            logger.info("二维码数据获取成功")
            return result

        except Exception as e:
            logger.error(f"获取二维码数据失败: {e}")
            raise LoginFailedError(f"获取二维码数据失败: {e}") from e

    def _long_poll_for_scan(self, poll_url: str) -> Dict[str, Any]:
        """长轮询等待扫码

        Args:
            poll_url: 轮询URL

        Returns:
            Dict[str, Any]: 登录结果数据

        Raises:
            LoginFailedError: 扫码超时或失败
        """
        try:
            logger.info("等待扫码...")
            # 使用长轮询，超时时间120秒
            response = self._client.get(poll_url, timeout=120)
            response.raise_for_status()

            # 解析响应
            data = response.text.replace("&&&START&&&", "")
            import json

            result = json.loads(data)

            if result.get("code") != 0:
                raise LoginFailedError(f"扫码失败: {result.get('desc')}")

            logger.info("扫码成功")
            return result

        except httpx.TimeoutException:
            raise LoginFailedError("扫码超时，请重试")
        except Exception as e:
            logger.error(f"等待扫码时发生错误: {e}")
            raise LoginFailedError(f"等待扫码失败: {e}") from e

    def _get_qrcode_url(self) -> str:
        """获取二维码URL（旧方法，已弃用）

        Returns:
            str: 二维码URL

        Raises:
            LoginFailedError: 获取二维码URL失败
        """
        try:
            url = f"{self._config.get('SERVICE_LOGIN_URL')}?sid=xiaomiio&_json=true"
            response = self._client.get(url)
            response.raise_for_status()

            # 解析响应（去掉前缀&&&START&&&）
            data = response.text.replace("&&&START&&&", "")
            import json

            result = json.loads(data)

            if result.get("code") != 0:
                raise LoginFailedError(f"获取二维码URL失败: {result.get('desc')}")

            qr_url: str = result["qr"]
            logger.info("二维码URL获取成功")
            return qr_url

        except Exception as e:
            logger.error(f"获取二维码URL失败: {e}")
            raise LoginFailedError(f"获取二维码URL失败: {e}") from e

    def _display_qrcode(self, url: str) -> None:
        """在终端显示二维码

        Args:
            url: 二维码URL
        """
        print("\n请使用米家APP扫描以下二维码登录：\n")
        qr = QRCode()
        qr.add_data(url)
        qr.make()
        qr.print_ascii()
        print("\n等待扫码...\n")

    def _wait_for_scan(self, qr_url: str, timeout: int = 300) -> Dict[str, Any]:
        """轮询等待扫码

        Args:
            qr_url: 二维码URL
            timeout: 超时时间（秒），默认5分钟

        Returns:
            Dict[str, Any]: 登录结果数据

        Raises:
            LoginFailedError: 扫码超时或失败
        """
        start_time = time.time()
        poll_interval = 2  # 每2秒轮询一次

        while time.time() - start_time < timeout:
            try:
                # 轮询登录状态
                check_url = f"{self._config.get('SERVICE_LOGIN_URL')}/check?sid=xiaomiio&_json=true"
                response = self._client.get(check_url)

                # 解析响应
                data = response.text.replace("&&&START&&&", "")
                import json

                result: Dict[str, Any] = json.loads(data)

                # 检查登录状态
                if result.get("code") == 0:
                    logger.info("扫码成功")
                    return result
                elif result.get("code") == 87001:
                    # 等待扫码
                    time.sleep(poll_interval)
                    continue
                else:
                    raise LoginFailedError(f"扫码失败: {result.get('desc')}")

            except LoginFailedError:
                raise
            except Exception as e:
                logger.warning(f"轮询扫码状态时发生错误: {e}")
                time.sleep(poll_interval)

        raise LoginFailedError("扫码超时，请重试")

    def _get_service_token(self, login_result: Dict[str, Any]) -> Dict[str, Any]:
        """获取service token

        Args:
            login_result: 登录结果数据

        Returns:
            Dict[str, Any]: 包含serviceToken和ssecurity的字典

        Raises:
            LoginFailedError: 获取service token失败
        """
        try:
            # 从登录结果中提取location
            location = login_result.get("location")
            if not location:
                raise LoginFailedError("登录结果中缺少location字段")

            # 访问location获取service token
            response = self._client.get(location)
            response.raise_for_status()

            # 从cookies中提取serviceToken
            service_token = response.cookies.get("serviceToken")
            if not service_token:
                raise LoginFailedError("未能获取serviceToken")

            # 获取ssecurity
            ssecurity_url = f"{self._config.get('LOGIN_URL')}/pass/serviceLoginAuth2"
            response = self._client.get(
                ssecurity_url,
                params={"sid": "xiaomiio", "serviceToken": service_token},
            )
            response.raise_for_status()

            # 解析响应
            import json

            result = json.loads(response.text.replace("&&&START&&&", ""))

            if result.get("code") != 0:
                raise LoginFailedError(f"获取ssecurity失败: {result.get('desc')}")

            ssecurity = result.get("ssecurity")
            if not ssecurity:
                raise LoginFailedError("未能获取ssecurity")

            logger.info("Service token获取成功")
            return {"serviceToken": service_token, "ssecurity": ssecurity}

        except Exception as e:
            logger.error(f"获取service token失败: {e}")
            raise LoginFailedError(f"获取service token失败: {e}") from e

    def _refresh_service_token(self, credential: Credential) -> Dict[str, Any]:
        """刷新service token
        
        通过重新访问serviceLogin接口并携带现有的passToken来刷新凭据。
        这是小米账号系统支持的正确刷新方式。

        Args:
            credential: 当前的凭据对象，包含passToken等信息

        Returns:
            Dict[str, Any]: 包含新的serviceToken和ssecurity的字典

        Raises:
            TokenExpiredError: 刷新失败
        """
        try:
            # 构建serviceLogin URL，使用sid=mijia（与米家APP一致）
            service_login_url = f"{self._config.get('LOGIN_URL')}/pass/serviceLogin?_json=true&sid=mijia&_locale=zh_CN"
            
            # 构建请求头，携带现有的认证信息
            headers = {
                "User-Agent": credential.user_agent,
                "Connection": "keep-alive",
                "Accept-Encoding": "gzip",
                "Content-Type": "application/x-www-form-urlencoded",
                "Cookie": f"deviceId={credential.device_id};"
                          f"passToken={credential.pass_token};"
                          f"userId={credential.user_id};"
                          f"cUserId={credential.c_user_id};"
            }
            
            # 请求serviceLogin接口
            response = self._client.get(service_login_url, headers=headers)
            response.raise_for_status()
            
            # 解析响应
            import json
            result = json.loads(response.text.replace("&&&START&&&", ""))
            
            logger.debug(f"刷新响应: code={result.get('code')}, desc={result.get('desc')}")
            
            # 检查响应状态
            if result.get("code") == 0:
                # code=0表示token仍然有效，可以直接刷新
                location = result.get("location")
                if not location:
                    raise TokenExpiredError("刷新响应中缺少location字段")
                
                # 访问location URL完成刷新
                location_response = self._client.get(location, headers={"User-Agent": credential.user_agent})
                
                if location_response.status_code == 200:
                    # 从响应中提取新的serviceToken
                    new_service_token = location_response.cookies.get("serviceToken")
                    if not new_service_token:
                        # 如果没有新token，使用旧token
                        new_service_token = credential.service_token
                    
                    # 提取新的ssecurity
                    new_ssecurity = result.get("ssecurity")
                    if not new_ssecurity:
                        raise TokenExpiredError("刷新响应中缺少ssecurity")
                    
                    logger.info("Service token刷新成功")
                    return {
                        "serviceToken": new_service_token,
                        "ssecurity": new_ssecurity
                    }
                else:
                    raise TokenExpiredError(f"访问location失败: HTTP {location_response.status_code}")
            
            # 如果code不为0，说明需要重新登录
            raise TokenExpiredError(f"凭据已失效，需要重新登录: {result.get('desc', '未知错误')}")

        except Exception as e:
            logger.error(f"刷新service token失败: {e}")
            raise TokenExpiredError(f"刷新service token失败: {e}") from e

    def _generate_device_id(self) -> str:
        """生成设备ID

        Returns:
            str: UUID格式的设备ID
        """
        return str(uuid.uuid4())

    def _generate_user_agent(self) -> str:
        """生成随机的移动端User-Agent

        模拟米家APP在不同移动设备上的User-Agent。
        支持iOS和Android两种平台，随机选择（80% iOS, 20% Android）。

        Returns:
            str: 随机生成的User-Agent字符串
        """
        import random

        # 随机选择平台（80% iOS, 20% Android）
        platform = random.choices(["iOS", "Android"], weights=[0.8, 0.2])[0]

        if platform == "iOS":
            return self._generate_ios_user_agent()
        else:
            return self._generate_android_user_agent()

    def _generate_ios_user_agent(self) -> str:
        """生成iOS平台的User-Agent

        Returns:
            str: iOS User-Agent字符串
        """
        import random

        # iOS版本列表（常见版本）
        ios_versions = [
            "14.0", "14.1", "14.2", "14.3", "14.4", "14.5", "14.6", "14.7", "14.8",
            "15.0", "15.1", "15.2", "15.3", "15.4", "15.5", "15.6", "15.7",
            "16.0", "16.1", "16.2", "16.3", "16.4", "16.5", "16.6",
            "17.0", "17.1", "17.2", "17.3", "17.4", "17.5", "17.6",
            "18.0", "18.1"
        ]

        # 米家APP版本列表
        app_versions = [
            "6.0.100", "6.0.101", "6.0.102", "6.0.103", "6.0.104", "6.0.105",
            "7.0.100", "7.0.101", "7.0.102", "7.0.103", "7.0.104",
            "8.0.100", "8.0.101", "8.0.102", "8.0.103"
        ]

        # iPhone设备型号列表
        iphone_models = [
            "iPhone12,1",  # iPhone 11
            "iPhone12,3",  # iPhone 11 Pro
            "iPhone12,5",  # iPhone 11 Pro Max
            "iPhone13,1",  # iPhone 12 mini
            "iPhone13,2",  # iPhone 12
            "iPhone13,3",  # iPhone 12 Pro
            "iPhone13,4",  # iPhone 12 Pro Max
            "iPhone14,2",  # iPhone 13 Pro
            "iPhone14,3",  # iPhone 13 Pro Max
            "iPhone14,4",  # iPhone 13 mini
            "iPhone14,5",  # iPhone 13
            "iPhone14,7",  # iPhone 14
            "iPhone14,8",  # iPhone 14 Plus
            "iPhone15,2",  # iPhone 14 Pro
            "iPhone15,3",  # iPhone 14 Pro Max
            "iPhone15,4",  # iPhone 15
            "iPhone15,5",  # iPhone 15 Plus
            "iPhone16,1",  # iPhone 15 Pro
            "iPhone16,2",  # iPhone 15 Pro Max
            "iPhone17,1",  # iPhone 16 Pro
            "iPhone17,2",  # iPhone 16 Pro Max
            "iPhone17,3",  # iPhone 16
            "iPhone17,4",  # iPhone 16 Plus
        ]

        # 随机选择
        ios_version = random.choice(ios_versions)
        app_version = random.choice(app_versions)
        device_model = random.choice(iphone_models)

        user_agent = f"iOS-{ios_version}-{app_version}-{device_model}"
        logger.debug(f"生成iOS User-Agent: {user_agent}")

        return user_agent

    def _generate_android_user_agent(self) -> str:
        """生成Android平台的User-Agent

        Returns:
            str: Android User-Agent字符串
        """
        import random

        # Android版本列表
        android_versions = [
            "11", "12", "13", "14", "15"
        ]

        # 米家APP版本列表
        app_versions = [
            "6.0.701", "6.0.702", "6.0.703", "6.0.704",
            "7.0.701", "7.0.702", "7.0.703", "7.0.704",
            "8.0.701", "8.0.702", "8.0.703"
        ]

        # 小米设备型号列表
        xiaomi_models = [
            "23046RP50C",  # Xiaomi 13
            "2211133C",    # Xiaomi 12S
            "2304FPN6DC",  # Xiaomi 14
            "23127PN0CC",  # Xiaomi 13 Ultra
            "2211133G",    # Xiaomi 12S Ultra
            "22041216C",   # Xiaomi 12 Pro
            "2201123C",    # Xiaomi 12
            "21091116C",   # Xiaomi 11
            "M2102J2SC",   # Xiaomi 11 Pro
            "2107113SG",   # Xiaomi 11 Ultra
            "24031PN0DC",  # Xiaomi 14 Pro
            "24053PY09C",  # Xiaomi 14 Ultra
        ]

        # 随机生成ID
        def random_hex(length: int) -> str:
            return "".join(random.choices("0123456789ABCDEF", k=length))

        android_version = random.choice(android_versions)
        app_version = random.choice(app_versions)
        device_model = random.choice(xiaomi_models)

        # 生成随机ID
        ua_id1 = random_hex(40)
        ua_id2 = random_hex(32)
        ua_id3 = random_hex(32)
        ua_id4 = random_hex(40)
        pass_o = random_hex(16)

        # 构建Android User-Agent（参考旧版本格式）
        user_agent = (
            f"Android-{android_version}-{app_version}-Xiaomi-{device_model}-"
            f"OS2.0.212.0.VMYCNXM-{ua_id1}-CN-{ua_id3}-{ua_id2}-"
            f"SmartHome-MI_APP_STORE-{ua_id1}|{ua_id4}|{pass_o}-64"
        )

        logger.debug(f"生成Android User-Agent: {user_agent[:100]}...")

        return user_agent

    def _calculate_expires_at(self, token_data: Dict[str, Any], default_days: int = 7) -> datetime:
        """计算过期时间

        Args:
            token_data: token数据，可能包含expires_in字段
            default_days: 默认过期天数，默认7天

        Returns:
            datetime: 过期时间
        """
        # 如果token_data中包含expires_in字段，使用它
        expires_in = token_data.get("expires_in")
        if expires_in:
            return datetime.now() + timedelta(seconds=expires_in)

        # 否则使用默认过期时间
        return datetime.now() + timedelta(days=default_days)

    def __del__(self) -> None:
        """析构函数，关闭HTTP客户端"""
        try:
            self._client.close()
        except Exception:
            pass
        
    async def get_qrcode_async(self) -> tuple[str, str]:
        """
        异步获取二维码登录信息，返回 (qr_url, login_url)
        """
        try:
            # Step 1: 获取 location 参数（同步版本复用）
            # 注意：_get_location 是同步的，但我们可以在线程中运行
            location_data = await asyncio.to_thread(self._get_location)
            if location_data.get("code") == 0:
                raise Exception("已有有效Token，无需登录")

            # Step 2: 获取二维码数据（同步方法包装）
            qr_data = await asyncio.to_thread(self._get_qrcode_data, location_data)
            qr_url = qr_data["qr"]
            login_url = qr_data["lp"]
            return qr_url, login_url
        except Exception as e:
            raise LoginFailedError(f"获取二维码失败: {e}")

    async def poll_login_result_async(self, login_url: str, timeout: int = 120) -> Optional[Credential]:
        """
        异步轮询扫码结果，返回凭据或 None

        注意：复用 self._client（同步 httpx.Client）的 cookie jar，
        以保持与 get_qrcode_async() 同一会话状态。
        """
        try:
            # 长轮询请求（复用 self._client 的 cookie jar）
            response = await asyncio.to_thread(
                self._client.get, login_url, timeout=timeout
            )
            response.raise_for_status()
            data = response.text.replace("&&&START&&&", "")
            import json
            result = json.loads(data)
            if result.get("code") != 0:
                raise LoginFailedError(f"扫码失败: {result.get('desc')}")

            # 提取关键信息
            callback_url = result["location"]
            # 访问 callback 获取 cookies（复用同一 session）
            callback_resp = await asyncio.to_thread(
                self._client.get, callback_url, timeout=timeout
            )
            service_token = callback_resp.cookies.get("serviceToken")
            if not service_token:
                raise LoginFailedError("未能获取 serviceToken")

            # 构建凭据（部分字段需从 result 中获取）
            credential = Credential(
                user_id=str(result["userId"]),
                service_token=service_token,
                ssecurity=result["ssecurity"],
                pass_token=result.get("passToken", ""),
                c_user_id=str(result.get("cUserId", result["userId"])),
                device_id=self._generate_device_id(),
                user_agent=self._generate_user_agent(),
                expires_at=self._calculate_expires_at({}),
            )
            return credential
        except httpx.TimeoutException:
            return None  # 超时未扫码
        except Exception as e:
            raise LoginFailedError(f"轮询失败: {e}") from e
        
    async def refresh_async(self, credential: Credential) -> Credential:
        """异步刷新凭据"""
        import asyncio
        return await asyncio.to_thread(self.refresh, credential)
