from tennis_tracker import protocol


def test_packet_roundtrip():
    samples = [((0.0, -1.0, 15.9), (1999.0, -0.5, 123.4)), ((0.5, 0.25, -0.125), (0.0, 0.0, -2000.0))]
    data = protocol.encode_packet(7, 123456, 200, samples)
    p = protocol.decode_packet(data)
    assert (p.seq, p.t_us, p.rate_hz, len(p.samples)) == (7, 123456, 200, 2)
    for (a1, g1), (a2, g2) in zip(samples, p.samples):
        assert all(abs(x - y) < 1e-3 for x, y in zip(a1, a2))
        assert all(abs(x - y) < 0.07 for x, y in zip(g1, g2))


def test_clock_counts_lost_packets_and_unwraps_time():
    clock = protocol.SampleClock()
    s = [((0, 0, 1), (0, 0, 0))] * 2
    t1 = clock.sample_times(protocol.decode_packet(protocol.encode_packet(0, 2**32 - 10_000, 200, s)))
    t2 = clock.sample_times(protocol.decode_packet(protocol.encode_packet(3, 0, 200, s)))
    assert clock.lost_packets == 2
    assert t2[0] > t1[-1]
    assert abs(t1[1] - t1[0] - 0.005) < 1e-9


def test_commands_and_uuids():
    assert protocol.cmd_set_rate(400) == b"\x02\x90\x01"
    assert protocol.DATA_UUID == "7e510002-0c1a-4b7e-9a35-54656e6e6973"
