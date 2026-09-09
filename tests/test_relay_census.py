"""The zero-airtime census: does this fleet relay at all?

A relaying node stamps its own transport identity into the announce header
(RNS Transport.mangle_hops with transport_insert=True) and increments hops, so
a copy of Y's announce carrying transport_id X is PROOF X heard Y. Those
transmissions happen regardless, so reading them costs no airtime and leaks
nothing new — but RNS dispatches app-level announce handlers only for a BETTER
path, so the medic never sees the relayed copy. The bytes still cross the radio
port, and the splitter owns that port.

Header layout is taken from RNS.Packet.unpack, not guessed:
    flags = raw[0]; hops = raw[1]
    header_type = (flags & 0b01000000) >> 6      # 1 => transport_id present
    packet_type = (flags & 0b00000011)           # 1 => announce
    HEADER_2: transport_id = raw[2:18], destination_hash = raw[18:34]
"""

from monitor.relay_census import RelayCensus, CMD_DATA

RELAY = bytes(range(0x10, 0x20))          # 16-byte transport id
DEST = bytes(range(0x80, 0x90))           # 16-byte destination hash


def _frame(hops=2, header2=True, announce=True, cmd=CMD_DATA):
    flags = 0
    if header2:
        flags |= 0b01000000
    if announce:
        flags |= 0b00000001
    body = bytes([flags, hops]) + RELAY + DEST + b"\x00" + b"payload"
    return bytes([cmd]) + body


def test_a_relayed_announce_names_who_relayed_it():
    c = RelayCensus(path="/dev/null")
    c.observe(_frame(hops=2))
    assert c.relayed_announces == 1
    assert c.relays[RELAY.hex()] == 1
    assert c.pairs[RELAY.hex()][DEST.hex()] == 1


def test_a_directly_heard_announce_is_not_a_relay():
    """hops 0 means nobody forwarded it — it proves nothing about a third party."""
    c = RelayCensus(path="/dev/null")
    c.observe(_frame(hops=0))
    assert c.relayed_announces == 0


def test_a_packet_with_no_transport_id_teaches_nothing():
    c = RelayCensus(path="/dev/null")
    c.observe(_frame(header2=False))
    assert c.relayed_announces == 0 and c.header2 == 0


def test_non_announce_packets_are_not_counted():
    """Only an announce names a destination we can attribute this way."""
    c = RelayCensus(path="/dev/null")
    c.observe(_frame(announce=False))
    assert c.relayed_announces == 0


def test_non_data_frames_are_ignored():
    c = RelayCensus(path="/dev/null")
    c.observe(_frame(cmd=0x21))          # a stats frame, not CMD_DATA
    assert c.frames == 0


def test_counting_never_raises_on_junk():
    """This runs in the medic's live radio path. It must not throw, ever."""
    c = RelayCensus(path="/dev/null")
    for junk in (b"", b"\x00", b"\x00\x40", bytes([CMD_DATA]) + b"\x40" * 3,
                 bytes([CMD_DATA]) + bytes(range(20))):
        c.observe(junk)                  # must not raise
    assert c.relayed_announces == 0


def test_the_splitter_never_lets_the_census_break_the_stream():
    """A fault in an observer must cost a count, never a packet."""
    from monitor.serial_splitter import KissGpsSplitter
    from monitor.rnode_gps import FEND

    class _Exploding:
        def observe(self, frame):
            raise RuntimeError("boom")

    sp = KissGpsSplitter()
    sp.census = _Exploding()
    payload = _frame()
    out = sp.feed(bytes([FEND]) + payload + bytes([FEND]))
    assert payload in out, "the frame must still reach rnsd"
