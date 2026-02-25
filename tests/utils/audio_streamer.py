import asyncio
import wave
import logging

logger = logging.getLogger(__name__)

class AudioStreamer:
    """
    Simulates real-time audio input by yielding chunks of .wav files.
    """
    def __init__(self, chunk_size=1024, sample_rate=16000, delay=0.0):
        self.chunk_size = chunk_size
        self.sample_rate = sample_rate
        self.delay = delay # Optional delay between chunks to simulate real-time

    async def stream_wav(self, file_path: str):
        """
        Yields raw audio bytes from the wav file, chunk by chunk.
        Using wave module to strictly handle .wav format.
        """
        try:
            with wave.open(file_path, 'rb') as wf:
                # Validate sample rate if needed
                if wf.getframerate() != self.sample_rate:
                    logger.warning(f"File {file_path} sample rate ({wf.getframerate()}) differs from target ({self.sample_rate}). Resampling not implemented.")
                
                # Yield chunks
                data = wf.readframes(self.chunk_size)
                while data:
                    yield data
                    if self.delay > 0:
                        await asyncio.sleep(self.delay)
                    data = wf.readframes(self.chunk_size)
                    
        except FileNotFoundError:
            logger.error(f"Test audio file not found: {file_path}")
            return
        except Exception as e:
            logger.error(f"Error streaming audio {file_path}: {e}")
            return

    def load_wav_content(self, file_path: str) -> bytes:
        """Helper to load full content if needed (e.g. for non-streaming checks)"""
        try:
            with wave.open(file_path, 'rb') as wf:
                return wf.readframes(wf.getnframes())
        except Exception as e:
            logger.error(f"Failed to load wav content {file_path}: {e}")
            return b""
