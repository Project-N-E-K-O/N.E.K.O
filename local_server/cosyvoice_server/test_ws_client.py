"""
Unit Test Client for CosyVoice OpenAI Server WebSocket Bistream Mode

Tests the /v1/audio/speech/stream WebSocket endpoint.

Usage:
    python test_ws_client.py [--url WS_URL] [--voice VOICE] [--speed SPEED] [--language LANGUAGE]
    
Example:
    python test_ws_client.py --url ws://localhost:50000/v1/audio/speech/stream
"""

import asyncio
import json
import argparse
import wave
import time
from typing import Optional

try:
    import websockets
except ImportError:
    print("Please install websockets: pip install websockets")
    exit(1)


class BiStreamTestClient:
    """WebSocket client for testing CosyVoice bistream TTS."""
    
    def __init__(
        self,
        url: str = "ws://localhost:50000/v1/audio/speech/stream",
        voice: str = "中文女",
        speed: float = 1.0,
        language: Optional[str] = None,
        sample_rate: int = 22050,
    ):
        self.url = url
        self.voice = voice
        self.speed = speed
        self.language = language
        self.sample_rate = sample_rate
        self.audio_buffer = bytearray()
        self.first_text_sent_time: Optional[float] = None
        self.first_audio_received_time: Optional[float] = None
    
    async def test_basic_stream(self, text_chunks: list[str], output_file: Optional[str] = None) -> dict:
        """
        Test basic bistream functionality.
        
        Args:
            text_chunks: List of text chunks to send
            output_file: Optional path to save audio as WAV
            
        Returns:
            dict with test results
        """
        result = {
            "success": False,
            "error": None,
            "audio_bytes_received": 0,
            "audio_duration_seconds": 0.0,
            "chunks_sent": len(text_chunks),
            "ttft_seconds": None,  # Time To First Token
        }
        
        try:
            async with websockets.connect(self.url) as ws:
                print(f"[✓] Connected to {self.url}")
                
                # 1. Send config
                config = {
                    "voice": self.voice,
                    "speed": self.speed,
                }
                if self.language:
                    config["language"] = self.language
                    
                await ws.send(json.dumps(config))
                print(f"[✓] Sent config: {config}")
                
                # 2. Create tasks for sending and receiving
                send_task = asyncio.create_task(self._send_text(ws, text_chunks))
                recv_task = asyncio.create_task(self._receive_audio(ws))
                
                # Wait for both to complete
                await asyncio.gather(send_task, recv_task)
                
                result["success"] = True
                result["audio_bytes_received"] = len(self.audio_buffer)
                result["audio_duration_seconds"] = len(self.audio_buffer) / (self.sample_rate * 2)  # 16-bit mono
                
                # Calculate TTFT
                if self.first_text_sent_time and self.first_audio_received_time:
                    result["ttft_seconds"] = self.first_audio_received_time - self.first_text_sent_time
                    print(f"[⏱] TTFT: {result['ttft_seconds']*1000:.2f}ms")
                
                # Save audio if requested
                if output_file and self.audio_buffer:
                    self._save_wav(output_file)
                    print(f"[✓] Saved audio to {output_file}")
                    
        except websockets.exceptions.ConnectionClosed as e:
            result["error"] = f"Connection closed: {e}"
            print(f"[!] {result['error']}")
        except Exception as e:
            result["error"] = str(e)
            print(f"[✗] Error: {e}")
            
        return result
    
    async def _send_text(self, ws, text_chunks: list[str]):
        """Send text chunks to the server."""
        for i, chunk in enumerate(text_chunks):
            msg = {"text": chunk}
            await ws.send(json.dumps(msg))
            # Record time of first text sent
            if i == 0:
                self.first_text_sent_time = time.perf_counter()
            print(f"[→] Sent text chunk {i+1}/{len(text_chunks)}: '{chunk[:30]}...' " if len(chunk) > 30 else f"[→] Sent text chunk {i+1}/{len(text_chunks)}: '{chunk}'")
            await asyncio.sleep(0.1)  # Small delay between chunks
        
        # Send end signal
        await ws.send(json.dumps({"event": "end"}))
        print("[→] Sent end signal")
    
    async def _receive_audio(self, ws):
        """Receive audio data from the server."""
        chunk_count = 0
        try:
            while True:
                msg = await ws.recv()
                if isinstance(msg, bytes):
                    # Record time of first audio received
                    if chunk_count == 0:
                        self.first_audio_received_time = time.perf_counter()
                    self.audio_buffer.extend(msg)
                    chunk_count += 1
                    # Print progress every 10 chunks
                    if chunk_count % 10 == 0:
                        print(f"[←] Received {chunk_count} audio chunks ({len(self.audio_buffer)} bytes)")
        except websockets.exceptions.ConnectionClosed:
            print(f"[✓] Connection closed. Total audio chunks received: {chunk_count}")
    
    def _save_wav(self, filepath: str):
        """Save audio buffer as WAV file."""
        with wave.open(filepath, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(self.sample_rate)
            wf.writeframes(bytes(self.audio_buffer))


async def run_test_suite(url: str, voice: str, speed: float, language: Optional[str], sample_rate: int):
    """Run a suite of tests."""
    print("=" * 60)
    print("CosyVoice WebSocket Bistream Test Suite")
    print("=" * 60)
    
    # Combined audio buffer for all tests
    combined_audio = bytearray()
    
    # Test 1: Single short text
    print("\n--- Test 1: Single Short Text ---")
    client1 = BiStreamTestClient(url=url, voice=voice, speed=speed, language=language, sample_rate=sample_rate)
    result1 = await client1.test_basic_stream(
        text_chunks=["你好，这是一个测试。"],
    )
    print(f"Result: {result1}")
    if result1["success"]:
        combined_audio.extend(client1.audio_buffer)
    
    # Test 2: Multiple text chunks (bistream)
    print("\n--- Test 2: Multiple Text Chunks (Bistream) ---")
    client2 = BiStreamTestClient(url=url, voice=voice, speed=speed, language=language, sample_rate=sample_rate)
    result2 = await client2.test_basic_stream(
        text_chunks=[
            "这是第一个句子。",
            "这是第二个句子。",
            "这是最后一个句子。",
        ],
    )
    print(f"Result: {result2}")
    if result2["success"]:
        combined_audio.extend(client2.audio_buffer)
    
    # Test 3: Long text
    print("\n--- Test 3: Long Text ---")
    client3 = BiStreamTestClient(url=url, voice=voice, speed=speed, language=language, sample_rate=sample_rate)
    long_text = "这是一段较长的文本，用于测试服务器处理长文本的能力。我们希望服务器能够正确地将这段文本转换为语音，并通过WebSocket流式传输回来。"
    result3 = await client3.test_basic_stream(
        text_chunks=[long_text],
    )
    print(f"Result: {result3}")
    if result3["success"]:
        combined_audio.extend(client3.audio_buffer)
    
    # Save combined audio
    if combined_audio:
        output_file = "test_output_combined.wav"
        with wave.open(output_file, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(sample_rate)
            wf.writeframes(bytes(combined_audio))
        total_duration = len(combined_audio) / (sample_rate * 2)
        print(f"\n[✓] Saved combined audio to {output_file} ({len(combined_audio)} bytes, {total_duration:.2f}s)")
    
    # Summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    tests = [("Single Short Text", result1), ("Multiple Chunks", result2), ("Long Text", result3)]
    for name, result in tests:
        status = "✓ PASSED" if result["success"] else "✗ FAILED"
        print(f"  {name}: {status}")
        if result["success"]:
            print(f"    - Audio: {result['audio_bytes_received']} bytes ({result['audio_duration_seconds']:.2f}s)")
            if result.get("ttft_seconds"):
                print(f"    - TTFT: {result['ttft_seconds']*1000:.2f}ms")
        else:
            print(f"    - Error: {result['error']}")


async def run_interactive(url: str, voice: str, speed: float, language: Optional[str]):
    """Run interactive mode where user can type text."""
    print("=" * 60)
    print("CosyVoice WebSocket Bistream Interactive Mode")
    print("=" * 60)
    print("Type text and press Enter to send. Type 'quit' to exit.")
    print("Type 'end' to send end signal and receive audio.\n")
    
    client = BiStreamTestClient(url=url, voice=voice, speed=speed, language=language)
    
    try:
        async with websockets.connect(client.url) as ws:
            print(f"[✓] Connected to {client.url}")
            
            # Send config
            config = {
                "voice": client.voice,
                "speed": client.speed,
            }
            if client.language:
                config["language"] = client.language
            await ws.send(json.dumps(config))
            print(f"[✓] Sent config: {config}")
            
            # Start receiver task
            recv_task = asyncio.create_task(client._receive_audio(ws))
            
            # Interactive input loop
            while True:
                user_input = await asyncio.get_event_loop().run_in_executor(None, input, "> ")
                
                if user_input.lower() == 'quit':
                    await ws.send(json.dumps({"event": "end"}))
                    break
                elif user_input.lower() == 'end':
                    await ws.send(json.dumps({"event": "end"}))
                    print("[→] Sent end signal. Waiting for audio...")
                    await recv_task
                    break
                else:
                    await ws.send(json.dumps({"text": user_input}))
                    print(f"[→] Sent: '{user_input}'")
            
            # Save audio
            if client.audio_buffer:
                client._save_wav("interactive_output.wav")
                print(f"[✓] Saved audio to interactive_output.wav ({len(client.audio_buffer)} bytes)")
                
    except Exception as e:
        print(f"[✗] Error: {e}")


def main():
    parser = argparse.ArgumentParser(description="Test CosyVoice WebSocket Bistream Endpoint")
    parser.add_argument("--url", type=str, default="ws://localhost:50000/v1/audio/speech/stream",
                        help="WebSocket URL")
    parser.add_argument("--voice", type=str, default="中文女",
                        help="Voice/speaker ID")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="Speech speed")
    parser.add_argument("--language", type=str, default=None,
                        help="Language tag (e.g., 'zh', 'en')")
    parser.add_argument("--mode", type=str, choices=["test", "interactive"], default="test",
                        help="Mode: 'test' for automated tests, 'interactive' for manual input")
    parser.add_argument("--sample-rate", type=int, default=22050,
                        help="Sample rate for saving WAV files")
    
    args = parser.parse_args()
    
    if args.mode == "test":
        asyncio.run(run_test_suite(args.url, args.voice, args.speed, args.language, args.sample_rate))
    else:
        asyncio.run(run_interactive(args.url, args.voice, args.speed, args.language))


if __name__ == "__main__":
    main()
