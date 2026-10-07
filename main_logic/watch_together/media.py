"""Bundled PyAV media operations, isolated so native decoders can be cancelled.

No external executables or PATH lookup. Each bounded job owns one spawn worker;
the caller reaps it before returning, including repeated asyncio cancellation.
"""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
import heapq
import io
import math
import multiprocessing
from pathlib import Path
import threading
import time
import wave

import av


def check_available():
    for name, mode in (("h264", "r"), ("aac", "r"), ("opus", "r"),
                       ("libx264", "w"), ("aac", "w"), ("pcm_s16le", "w")):
        av.codec.Codec(name, mode)


def _duration(path):
    with av.open(str(path)) as container:
        if container.duration is not None:
            value = container.duration / av.time_base
        else:
            value = max((float(s.duration * s.time_base) for s in container.streams
                         if s.duration is not None and s.time_base is not None), default=0)
        if not math.isfinite(value) or value <= 0:
            raise ValueError("Media duration unavailable")
        return value


def _probe(path, role):
    with av.open(str(path)) as container:
        return any(s.type == role and s.codec_context.name not in (None, "unknown")
                   for s in container.streams)


def _save_frame(frame, output):
    height = max(2, round(frame.height * 640 / frame.width / 2) * 2)
    frame.reformat(width=640, height=height).to_image().save(str(output), quality=85)


def _frames(path, directory, interval=5.0):
    samples = []
    previous = -1
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        origin = float((stream.start_time or 0) * stream.time_base)
        for frame in container.decode(stream):
            if frame.time is None:
                continue
            at = max(0.0, float(frame.time) - origin)
            bucket = math.floor(at / interval)
            if bucket > previous:
                previous = bucket
                output = Path(directory) / f"{len(samples) + 1:05d}.jpg"
                _save_frame(frame, output)
                samples.append((at, str(output)))
    if not samples:
        raise ValueError("Video has no decodable frames")
    return samples


def _frame(path, at, output):
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        origin = float((stream.start_time or 0) * stream.time_base)
        target = origin + at
        container.seek(int(target / stream.time_base), stream=stream, backward=True)
        for frame in container.decode(stream):
            if frame.time is not None and float(frame.time) + 1e-6 >= target:
                _save_frame(frame, output)
                return True
    return False


def _packet_time(packet):
    timestamp = packet.dts if packet.dts is not None else packet.pts
    if timestamp is None or packet.time_base is None:
        raise ValueError("Media packet has no usable timestamp")
    return timestamp * packet.time_base


def _packets(container, stream, output, copy):
    if copy:
        for packet in container.demux(stream):
            if packet.size:
                # Demux flush packets are empty; missing DTS alone is not EOF.
                _packet_time(packet)
                packet.stream = output
                yield packet
        return
    resampler = (av.AudioResampler(format="fltp", layout=output.layout.name, rate=48000)
                 if stream.type == "audio" else None)
    for frame in container.decode(stream):
        if resampler:
            for audio in resampler.resample(frame):
                yield from output.encode(audio)
        else:
            yield from output.encode(frame.reformat(width=output.width, height=output.height,
                                                   format="yuv420p"))
    if resampler:
        for frame in resampler.resample(None):
            yield from output.encode(frame)
    yield from output.encode(None)


def _mux(video, audio, target):
    with ExitStack() as stack:
        vin = stack.enter_context(av.open(str(video)))
        # Separate readers are necessary when both tracks live in one input:
        # independently advancing demux iterators on one container loses packets.
        audio_path = audio if audio is not None else video
        ain = stack.enter_context(av.open(str(audio_path)))
        out = stack.enter_context(av.open(str(target), "w", format="mp4", options={"movflags": "+faststart"}))
        video_stream = vin.streams.video[0]
        streams = [(vin, video_stream)]
        if ain.streams.audio:
            streams.append((ain, ain.streams.audio[0]))
        iterators = []
        for source, stream in streams:
            copy = stream.codec_context.name == ("h264" if stream.type == "video" else "aac")
            if copy:
                dest = out.add_stream_from_template(stream)
            elif stream.type == "video":
                dest = out.add_stream("libx264", rate=stream.average_rate or 25)
                dest.width = max(2, (stream.width + 1) // 2 * 2)
                dest.height = max(2, (stream.height + 1) // 2 * 2)
                dest.pix_fmt = "yuv420p"
                dest.options = {"preset": "veryfast", "crf": "23"}
            else:
                dest = out.add_stream("aac", rate=48000)
                dest.layout = "mono" if stream.codec_context.channels == 1 else "stereo"
                dest.bit_rate = 128000
            iterators.append(_packets(source, stream, dest, copy))
        for packet in heapq.merge(*iterators, key=_packet_time):
            out.mux(packet)


def _speech(payload, output):
    if not payload:
        raise ValueError("Empty speech audio")
    with wave.open(str(output), "wb") as wav:
        wav.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        if not payload.startswith(b"OggS"):
            if len(payload) % 2:
                raise ValueError("Invalid PCM16 speech payload")
            wav.writeframes(payload)
            return
        with av.open(io.BytesIO(payload)) as container:
            resampler = av.AudioResampler(format="s16", layout="mono", rate=48000)
            for frame in container.decode(audio=0):
                for converted in resampler.resample(frame):
                    wav.writeframes(converted.to_ndarray().tobytes())
            for converted in resampler.resample(None):
                wav.writeframes(converted.to_ndarray().tobytes())


_OPERATIONS = {"duration": _duration, "probe": _probe, "frames": _frames,
               "frame": _frame, "mux": _mux, "speech": _speech}


def _worker(connection, operation, args):
    try:
        connection.send((True, _OPERATIONS[operation](*args)))
    except Exception as exc:
        connection.send((False, f"{type(exc).__name__}: {exc}"))
    finally:
        connection.close()


def run(operation, *args, timeout=600, cancel=None):
    if operation not in _OPERATIONS:
        raise ValueError("Unknown media operation")
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(send, operation, args), daemon=True)
    deadline = time.monotonic() + timeout
    try:
        process.start()
        send.close()
        while True:
            if cancel is not None and cancel.is_set():
                raise InterruptedError("Media operation cancelled")
            if time.monotonic() >= deadline:
                raise TimeoutError("Media operation timed out")
            if receive.poll(0.05):
                try:
                    ok, result = receive.recv()
                except EOFError as exc:
                    raise RuntimeError("Media worker exited without a result") from exc
                if not ok:
                    raise RuntimeError(result)
                return result
            if not process.is_alive():
                raise RuntimeError("Media worker exited without a result")
    finally:
        send.close()
        receive.close()
        if process.pid is not None:
            if process.is_alive():
                process.kill()
            process.join()
            process.close()


async def run_async(operation, *args, timeout=600):
    cancel = threading.Event()
    job = asyncio.create_task(asyncio.to_thread(run, operation, *args, timeout=timeout, cancel=cancel))
    cancelled = False
    while not job.done():
        try:
            await asyncio.shield(job)
        except asyncio.CancelledError:
            cancelled = True
            cancel.set()
        except Exception:
            break
    if cancelled:
        # Retrieve the worker error, but cancellation wins over its result.
        if not job.cancelled():
            job.exception()
        raise asyncio.CancelledError()
    return job.result()
