from fractions import Fraction
from types import SimpleNamespace

import av
import numpy as np
import pytest

from main_logic.watch_together import media


def test_remux_preserves_pts_only_packets_and_skips_flush(tmp_path):
    source, target = tmp_path / 'source.mp4', tmp_path / 'copy.mp4'
    with av.open(str(source), 'w') as out:
        stream = out.add_stream('libx264', rate=2)
        stream.width, stream.height, stream.pix_fmt = 64, 48, 'yuv420p'
        stream.options = {'bf': '0'}
        for index in range(7):
            frame = av.VideoFrame.from_ndarray(np.full((48, 64, 3), index * 30, dtype=np.uint8), format='rgb24')
            frame.pts, frame.time_base = index, Fraction(1, 2)
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    with av.open(str(source)) as inp, av.open(str(target), 'w') as out:
        stream = inp.streams.video[0]
        dest = out.add_stream_from_template(stream)
        packets = list(inp.demux(stream))
        assert any(not p.size for p in packets)
        for packet in packets:
            packet.dts = None
        reader = SimpleNamespace(demux=lambda _: iter(packets))
        copied = list(media._packets(reader, stream, dest, True))
        assert len(copied) == 7
        assert [media._packet_time(p) for p in copied] == [Fraction(i, 2) for i in range(7)]
        for packet in copied:
            out.mux(packet)
    with av.open(str(target)) as inp:
        frames = list(inp.decode(video=0))
        assert len(frames) == 7
        assert [frame.time for frame in frames] == [i / 2 for i in range(7)]


def test_remux_rejects_nonempty_packet_without_timestamps():
    packet = av.Packet(b'payload')
    reader = SimpleNamespace(demux=lambda _: iter([packet]))
    with pytest.raises(ValueError, match='no usable timestamp'):
        list(media._packets(reader, None, None, True))


def test_packet_order_prefers_decode_timestamp():
    packet = av.Packet(b'payload')
    packet.pts, packet.dts, packet.time_base = 3, 1, Fraction(1, 2)
    assert media._packet_time(packet) == Fraction(1, 2)
