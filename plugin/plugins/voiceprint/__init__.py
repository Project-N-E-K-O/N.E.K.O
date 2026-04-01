"""
N.E.K.O. 声纹比对插件（轻量版）
基于 MFCC 特征提取的简单声纹识别
无需下载大型模型，直接使用！
"""

from plugin.sdk.plugin import (
    NekoPluginBase, neko_plugin, plugin_entry, lifecycle,
    Ok, Err,
)
from typing import Any, Optional
import os
import json
import base64
import numpy as np
from scipy.io import wavfile
from scipy.fft import fft
from scipy.signal import get_window


@neko_plugin
class VoiceprintPlugin(NekoPluginBase):
    """声纹比对插件（轻量版）- 基于MFCC特征，无需大型模型"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)

        # 配置参数
        self.voiceprint_threshold = 0.5  # 相似度阈值
        self.sample_rate = 16000         # 采样率
        self.n_mfcc = 13                 # MFCC系数数量
        self.n_fft = 512                 # FFT窗口大小
        self.hop_length = 256            # 帧移

        # 缓存
        self._embedding1: Optional[np.ndarray] = None
        self._audio1: Optional[np.ndarray] = None

        # 缓存路径
        self.cache_path = "data/cache/voiceprint_cache.wav"
        self.reference_audio_path = "data/cache/voiceprint_reference.wav"

    def _load_config(self):
        """加载配置文件"""
        try:
            config_path = "data/db/config.json"
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                threshold = config.get("声纹识别阈值", 0.5)
                if threshold is not None:
                    self.voiceprint_threshold = float(threshold)
                    self.logger.info(f"Loaded threshold from config: {self.voiceprint_threshold}")
        except Exception as e:
            self.logger.warning(f"Failed to load config: {e}")

    def _preprocess_audio(self, audio: np.ndarray, source_sr: int = 16000) -> np.ndarray:
        """音频预处理：重采样到目标采样率"""
        if len(audio) == 0:
            return audio

        # 已达目标采样率，直接返回
        if source_sr == self.sample_rate:
            return audio

        # 拒绝不支持的采样率，防止特征整体算偏
        if source_sr not in (8000, 11025, 16000, 22050, 44100, 48000):
            raise ValueError(f"不支持的采样率: {source_sr}，请先转换为支持的采样率")

        # 使用 scipy 信号重采样（分数倍采样，精度高）
        from scipy.signal import resample_poly
        # gcd 计算最小公倍数，确保整数倍下采样
        gcd = self._gcd(source_sr, self.sample_rate)
        up = self.sample_rate // gcd
        down = source_sr // gcd
        return resample_poly(audio, up, down)

    @staticmethod
    def _gcd(a: int, b: int) -> int:
        """计算最大公约数（欧几里得算法）"""
        while b:
            a, b = b, a % b
        return a

    def _frame_audio(self, audio: np.ndarray) -> np.ndarray:
        """分帧处理"""
        # 兜底：音频过短时直接返回零帧，避免 np.zeros(0, ...) 异常
        if len(audio) < self.n_fft:
            return np.zeros((0, self.n_fft))

        n_frames = 1 + (len(audio) - self.n_fft) // self.hop_length
        frames = np.zeros((n_frames, self.n_fft))

        for i in range(n_frames):
            start = i * self.hop_length
            end = start + self.n_fft
            if end <= len(audio):
                frames[i] = audio[start:end]

        return frames

    def _apply_window(self, frames: np.ndarray) -> np.ndarray:
        """加汉宁窗"""
        window = get_window('hann', self.n_fft)
        return frames * window

    def _compute_mel_filterbank(self, n_mels: int = 26) -> np.ndarray:
        """计算梅尔滤波器组"""
        # 梅尔刻度转换
        def hz_to_mel(hz):
            return 2595 * np.log10(1 + hz / 700)

        def mel_to_hz(mel):
            return 700 * (10 ** (mel / 2595) - 1)

        # 频率范围
        low_freq_mel = hz_to_mel(0)
        high_freq_mel = hz_to_mel(self.sample_rate / 2)

        # 梅尔频率点
        mel_points = np.linspace(low_freq_mel, high_freq_mel, n_mels + 2)
        hz_points = mel_to_hz(mel_points)

        # 转换为FFT频率点
        fft_bins = np.floor((self.n_fft + 1) * hz_points / self.sample_rate).astype(int)

        # 构建滤波器
        filters = np.zeros((n_mels, self.n_fft // 2 + 1))
        for m in range(1, n_mels + 1):
            f_m_minus = fft_bins[m - 1]
            f_m = fft_bins[m]
            f_m_plus = fft_bins[m + 1]

            for k in range(f_m_minus, f_m):
                filters[m - 1, k] = (k - f_m_minus) / (f_m - f_m_minus)
            for k in range(f_m, f_m_plus):
                filters[m - 1, k] = (f_m_plus - k) / (f_m_plus - f_m)

        return filters

    def _compute_mfcc(self, audio: np.ndarray) -> np.ndarray:
        """计算MFCC特征"""
        # 分帧
        frames = self._frame_audio(audio)

        # 加窗
        windowed = self._apply_window(frames)

        # FFT
        spec = np.abs(fft(windowed, axis=1))[:, :self.n_fft // 2 + 1]

        # 功率谱
        power_spec = spec ** 2

        # 梅尔滤波器组
        mel_filters = self._compute_mel_filterbank()

        # 梅尔频谱
        mel_spec = np.dot(power_spec, mel_filters.T)
        mel_spec = np.where(mel_spec > 0, np.log(mel_spec), 0)

        # DCT得到MFCC
        mfcc = self._dct(mel_spec, self.n_mfcc)

        return mfcc

    def _dct(self, mel_spec: np.ndarray, n_mfcc: int) -> np.ndarray:
        """离散余弦变换"""
        n_mels = mel_spec.shape[1]
        dct_matrix = np.zeros((n_mfcc, n_mels))
        for i in range(n_mfcc):
            dct_matrix[i] = np.cos(np.pi * i * (np.arange(n_mels) + 0.5) / n_mels)

        mfcc = np.dot(mel_spec, dct_matrix.T)
        return mfcc

    def _extract_embedding(self, audio: np.ndarray) -> np.ndarray:
        """提取声纹特征向量"""
        # 计算MFCC
        mfcc = self._compute_mfcc(audio)

        # 对每帧MFCC取均值和标准差作为特征
        mean_mfcc = np.mean(mfcc, axis=0)
        std_mfcc = np.std(mfcc, axis=0)

        # 合并均值和标准差
        embedding = np.concatenate([mean_mfcc, std_mfcc])

        # L2归一化
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm

        return embedding

    def _cosine_similarity(self, vec1: np.ndarray, vec2: np.ndarray) -> float:
        """计算余弦相似度"""
        dot_product = np.dot(vec1, vec2)
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2)

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return float(dot_product / (norm1 * norm2))

    def _decode_audio(self, audio_base64: str, save_path: str) -> bool:
        """解码音频并保存"""
        try:
            audio_bytes = base64.b64decode(audio_base64)
            with open(save_path, 'wb') as f:
                f.write(audio_bytes)
            return True
        except Exception as e:
            self.logger.error(f"解码音频失败: {e}")
            return False

    def _load_wav(self, filename: str) -> tuple:
        """加载WAV音频"""
        sr, audio = wavfile.read(filename)

        # 转换为单通道float
        if len(audio.shape) > 1:
            audio = audio[:, 0]

        # 归一化到[-1, 1]，按绝对峰值归一化（避免不对称波形越界）
        audio = audio.astype(np.float32)
        abs_max = np.abs(audio).max()
        if abs_max != 0:
            audio = audio / abs_max

        return audio, sr

    @lifecycle(id="startup")
    def on_startup(self, **_):
        """插件启动"""
        self.logger.info("VoiceprintPlugin(轻量版) 启动中...")

        # 确保缓存目录存在
        os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)

        # 加载配置
        self._load_config()

        return Ok({
            "status": "ready",
            "model": "MFCC_Lightweight",
            "threshold": self.voiceprint_threshold,
            "model_status": "已就绪",
            "no_model_required": True,
            "usage": "先调用 enroll 注册声纹，再调用 verify 验证；或使用 compare 直接比对两段音频"
        })

    @plugin_entry(
        id="enroll",
        name="注册声纹",
        description="将一段音频注册为声纹样本（只需注册一次）",
        input_schema={
            "type": "object",
            "properties": {
                "audio": {
                    "type": "string",
                    "description": "音频数据（Base64编码的WAV格式）"
                }
            },
            "required": ["audio"]
        }
    )
    async def enroll_voiceprint(self, audio: str, **_):
        """注册声纹样本"""
        try:
            self.logger.info("开始注册声纹...")

            # 解码并保存音频
            if not self._decode_audio(audio, self.reference_audio_path):
                return Err({"error": "音频解码失败"})

            # 加载音频
            audio_data, sample_rate = self._load_wav(self.reference_audio_path)

            # 预处理
            audio_data = self._preprocess_audio(audio_data, sample_rate)

            # 提取声纹特征
            self._audio1 = audio_data
            self._embedding1 = self._extract_embedding(audio_data)

            self.logger.info(f"声纹注册成功！特征维度: {len(self._embedding1)}, 阈值: {self.voiceprint_threshold}")

            return Ok({
                "success": True,
                "message": "声纹注册成功",
                "sample_rate": sample_rate,
                "audio_duration": len(audio_data) / sample_rate,
                "audio_samples": len(audio_data),
                "threshold": self.voiceprint_threshold
            })

        except ImportError as e:
            return Err({
                "error": "缺少依赖库",
                "hint": "请安装: pip install scipy numpy soundfile"
            })
        except Exception as e:
            self.logger.error(f"声纹注册失败: {e}")
            return Err({"error": f"声纹注册失败: {str(e)}"})

    @plugin_entry(
        id="verify",
        name="验证声纹",
        description="将待验证的音频与已注册的声纹进行比对，判断是否为同一人",
        input_schema={
            "type": "object",
            "properties": {
                "audio": {
                    "type": "string",
                    "description": "待验证的音频（Base64编码的WAV格式）"
                }
            },
            "required": ["audio"]
        }
    )
    async def verify_voiceprint(self, audio: str, **_):
        """验证声纹"""
        try:
            # 检查是否已注册声纹
            if self._embedding1 is None:
                return Err({
                    "error": "尚未注册声纹",
                    "hint": "请先调用 enroll 注册声纹样本"
                })

            self.logger.info("开始声纹验证...")

            # 解码并保存音频
            if not self._decode_audio(audio, self.cache_path):
                return Err({"error": "音频解码失败"})

            # 加载音频
            audio_data, sample_rate = self._load_wav(self.cache_path)

            # 预处理
            audio_data = self._preprocess_audio(audio_data, sample_rate)

            # 提取声纹特征
            embedding2 = self._extract_embedding(audio_data)

            # 计算相似度
            similarity = self._cosine_similarity(self._embedding1, embedding2)

            # 判断结果
            is_same_person = similarity >= self.voiceprint_threshold

            self.logger.info(
                f"声纹验证结果: {'是同一人' if is_same_person else '不是同一人'} "
                f"(相似度 {similarity:.4f} {'>=' if is_same_person else '<'} 阈值 {self.voiceprint_threshold})"
            )

            return Ok({
                "result": "是" if is_same_person else "不是",
                "similarity": round(similarity, 4),
                "threshold": self.voiceprint_threshold,
                "confidence": "高" if similarity > 0.7 else "中" if similarity >= self.voiceprint_threshold else "低"
            })

        except ImportError as e:
            return Err({
                "error": "缺少依赖库",
                "hint": "请安装: pip install scipy numpy soundfile"
            })
        except Exception as e:
            self.logger.error(f"声纹验证失败: {e}")
            return Err({"error": f"声纹验证失败: {str(e)}"})

    @plugin_entry(
        id="compare",
        name="声纹比对",
        description="比对两段音频，判断是否为同一人（需要同时提供两段音频）",
        input_schema={
            "type": "object",
            "properties": {
                "audio1": {
                    "type": "string",
                    "description": "第一段音频（Base64编码的WAV格式）"
                },
                "audio2": {
                    "type": "string",
                    "description": "第二段音频（Base64编码的WAV格式）"
                },
                "threshold": {
                    "type": "number",
                    "description": "相似度阈值",
                    "default": 0.5
                }
            },
            "required": ["audio1", "audio2"]
        }
    )
    async def compare_voice(self, audio1: str, audio2: str, threshold: float = 0.5, **_):
        """直接比对两段音频"""
        try:
            self.logger.info("开始声纹比对...")

            # 解码并保存音频1
            if not self._decode_audio(audio1, self.reference_audio_path):
                return Err({"error": "音频1解码失败"})

            # 解码并保存音频2
            if not self._decode_audio(audio2, self.cache_path):
                return Err({"error": "音频2解码失败"})

            # 加载音频1
            audio_data1, sample_rate1 = self._load_wav(self.reference_audio_path)

            # 加载音频2
            audio_data2, sample_rate2 = self._load_wav(self.cache_path)

            # 预处理
            audio_data1 = self._preprocess_audio(audio_data1, sample_rate1)
            audio_data2 = self._preprocess_audio(audio_data2, sample_rate2)

            # 提取声纹特征
            embedding1 = self._extract_embedding(audio_data1)
            embedding2 = self._extract_embedding(audio_data2)

            # 计算相似度
            similarity = self._cosine_similarity(embedding1, embedding2)

            # 判断结果
            is_same_person = similarity >= threshold

            self.logger.info(
                f"声纹比对结果: {'是同一人' if is_same_person else '不是同一人'} "
                f"(相似度 {similarity:.4f} {'>=' if is_same_person else '<'} 阈值 {threshold})"
            )

            return Ok({
                "result": "是" if is_same_person else "不是",
                "similarity": round(similarity, 4),
                "threshold": threshold,
                "confidence": "高" if similarity > 0.7 else "中" if similarity >= threshold else "低"
            })

        except ImportError as e:
            return Err({
                "error": "缺少依赖库",
                "hint": "请安装: pip install scipy numpy soundfile"
            })
        except Exception as e:
            self.logger.error(f"声纹比对失败: {e}")
            return Err({"error": f"声纹比对失败: {str(e)}"})

    @plugin_entry(
        id="set_threshold",
        name="设置阈值",
        description="设置声纹识别的相似度阈值",
        input_schema={
            "type": "object",
            "properties": {
                "threshold": {
                    "type": "number",
                    "description": "阈值 (0.0-1.0)，越低越严格",
                    "default": 0.5
                }
            }
        }
    )
    async def set_threshold(self, threshold: float = 0.5, **_):
        """设置阈值"""
        if threshold < 0 or threshold > 1:
            return Err({"error": "阈值必须在 0.0 到 1.0 之间"})

        self.voiceprint_threshold = threshold
        self.logger.info(f"声纹识别阈值已设置为: {threshold}")

        return Ok({
            "success": True,
            "threshold": threshold,
            "message": f"阈值已设置为 {threshold}"
        })

    @plugin_entry(
        id="get_status",
        name="状态查询",
        description="查询插件状态",
        input_schema={
            "type": "object",
            "properties": {}
        }
    )
    async def get_status(self, **_):
        """获取状态"""
        return Ok({
            "model": "MFCC_Lightweight",
            "no_model_required": True,
            "voice_enrolled": self._embedding1 is not None,
            "threshold": self.voiceprint_threshold,
            "sample_rate": self.sample_rate,
            "n_mfcc": self.n_mfcc
        })
