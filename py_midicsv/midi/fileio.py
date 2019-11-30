from .containers import *
from .events import *
from struct import unpack, pack
from .constants import *
from .util import *


class Trackiter:
    """ get next track from midifile """
    def __init__(self, reader, midifile):
        self.reader = reader
        self.file = midifile

    def __iter__(self):
        return self

    def __next__(self):
        magic, trksz = self.reader.parse_track_header(self.file)
        buf = self.reader.readexactly(self.file, trksz)
        if buf[-3:] != b"\xFF\x2F\x00":
            raise ValueError(
                "Track at {} does not end with End Track event".format(
                    self.reader.basepos))
        track = Track()
        track.type = magic
        trackdata = Byteiter(buf, pos=self.reader.basepos)
        self.reader.basepos += trksz
        return (trackdata, track)


class Byteiter:
    """ get next byte from track """

    def __init__(self, trackbuf, pos=0):
        self._buf = trackbuf
        self._it = iter(trackbuf)
        self._pos = pos

    def __iter__(self):
        return self

    def __next__(self):
        self._pos += 1
        return next(self._it)

    def pos(self):
        return self._pos

    def errmsg(self, msg, data):
        return "{} 0x{:02X} at position {}".format(msg, data, self.pos())

    def assert_data_byte(self, data):
        assert data & 0x80 == 0, self.errmsg("Unexpected status byte", data)

    def assert_status_byte(self, data):
        assert data & 0x80 != 0, self.errmsg("Unexpected data byte", data)

    def get_data_byte(self):
        byte = self.__next__()
        self.assert_data_byte(byte)
        return byte


class Eventiter:
    """ return next MIDI event from track """
    def __init__(self, reader, trackdata):
        self.reader = reader
        self.trackdata = trackdata

    def __iter__(self):
        return self

    def __next__(self):
        return self.reader.parse_midi_event(self.trackdata)


class FileReader(object):

    def read(self, midifile):
        pattern = self.parse_file_header(midifile)
        Pattern.useRunningStatus = False
        for trackdata, track in Trackiter(self, midifile):
            self.RunningStatus = None
            for event in Eventiter(self, trackdata):
                track.append(event)
            pattern.append(track)
        return pattern

    def readexactly(self, midifile, nbytes):
        buf = midifile.read(nbytes)
        if len(buf) == nbytes:
            return buf
        raise EOFError("Expected {} bytes after byte {}, found {}".format(
                                           nbytes, self.basepos, len(buf)))

    def parse_file_header(self, midifile):
        # First four bytes are MIDI header
        magic = midifile.read(4)
        if magic != b"MThd":
            raise TypeError("Bad header in MIDI file.", magic)
        # next four bytes are header size
        # next two bytes specify the format version
        # next two bytes specify the number of tracks
        # next two bytes specify the resolution/PPQ/Parts Per Quarter
        # (in other words, how many ticks per quater note)
        self.basepos = 4
        data = unpack(">LHHH", self.readexactly(midifile, 10))
        hdrsz = data[0] + 8
        format = data[1]
        ntracks = data[2]
        resolution = data[3]
        # XXX: the assumption is that any remaining bytes
        # in the header are padding
        if hdrsz > DEFAULT_MIDI_HEADER_SIZE:
            self.readexactly(midifile, hdrsz - DEFAULT_MIDI_HEADER_SIZE)
        self.basepos = hdrsz
        return Pattern(format=format, ntracks=ntracks, resolution=resolution)

    def parse_track_header(self, midifile):
        # First four bytes are Track header
        magic = midifile.read(4)
        if magic == b'':
            raise StopIteration
        # next four bytes are track size
        trksz = unpack(">L", self.readexactly(midifile, 4))[0]
        self.basepos += 8
        return (magic, trksz)

    def parse_midi_event(self, trackdata):
        # first datum is varlen representing delta-time
        tick = read_varlen(trackdata)
        # next byte is status message
        stsmsg = next(trackdata)
        # is the event a MetaEvent?
        if MetaEvent.is_event(stsmsg):
            cmd = trackdata.get_data_byte()
            if cmd not in EventRegistry.MetaEvents:
                raise Warning("Unknown Meta MIDI Event: " + repr(cmd))
            cls = EventRegistry.MetaEvents[cmd]
            datalen = read_varlen(trackdata)
            data = [next(trackdata) for x in range(datalen)]
            return cls(tick=tick, data=data)
        # is this event a Sysex Event?
        elif SysexEvent.is_event(stsmsg):
            datalen = read_varlen(trackdata)
            data = [next(trackdata) for x in range(datalen)]
            if stsmsg not in EventRegistry.Events:
                raise Warning("Unknown Sysex Event: {:02x}".format(stsmsg))
            cls = EventRegistry.Events[stsmsg]
            return cls(tick=tick, data=data)
        # not a Meta MIDI event or a Sysex event, must be a general message
        else:
            key = stsmsg & 0xF0
            if key not in EventRegistry.Events:
                if not self.RunningStatus:
                    trackdata.assert_status_byte(stsmsg)
                Pattern.useRunningStatus = True
                key = self.RunningStatus & 0xF0
                cls = EventRegistry.Events[key]
                channel = self.RunningStatus & 0x0F
                data = [stsmsg]
                data += [trackdata.get_data_byte() for _ in range(cls.length - 1)]
                return cls(tick=tick, channel=channel, data=data)
            else:
                self.RunningStatus = stsmsg
                cls = EventRegistry.Events[key]
                channel = self.RunningStatus & 0x0F
                data = [trackdata.get_data_byte() for _ in range(cls.length)]
                return cls(tick=tick, channel=channel, data=data)
        raise Warning("Unknown MIDI Event: " + repr(stsmsg))


class FileWriter(object):
    RunningStatus = None

    def __init__(self, file):
        self.file = file

    def write(self, pattern):
        self.write_file_header(pattern)
        for track in pattern:
            self.write_track(track)

    def write_file_header(self, pattern):
        # First four bytes are MIDI header
        packdata = pack(">LHHH", 6,
                        pattern.format,
                        pattern.ntracks,
                        pattern.resolution)
        self.file.write(b"MThd" + packdata)

    def write_track(self, track):
        hlen = len(self.encode_track_header(0))
        buf = bytearray(b"0" * hlen)
        for event in track:
            buf.extend(self.encode_midi_event(event))
        buf[:hlen] = self.encode_track_header(len(buf) - hlen, track.type)
        self.file.write(buf)

    def write_track_header(self, track=None):
        if track is None:
            trklen = 1
        elif isinstance(track, int):
            trklen = track
        else:
            trklen = len(track)
        self.file.write(self.encode_track_header(trklen, track.type))

    def encode_track_header(self, trklen, ttype=b"MTrk"):
        return ttype + pack(">L", trklen)

    def write_midi_event(self, event):
        # be sure to write the track and pattern headers first
        # can stream to timidity or fluidsynth this way
        self.file.write(self.encode_midi_event(event))

    def encode_midi_event(self, event):
        ret = bytearray()
        assert isinstance(event.tick, int), event.tick
        ret.extend(write_varlen(event.tick))
        # is the event a MetaEvent?
        if isinstance(event, MetaEvent):
            self.RunningStatus = None
            ret.append(event.statusmsg)
            ret.append(event.metacommand)
            ret.extend(write_varlen(len(event.data)))
            ret.extend(event.data)
        # is this event a Sysex Event?
        elif isinstance(event, SysexEvent):
            self.RunningStatus = None
            ret.append(event.statusmsg)
            ret.extend(write_varlen(len(event.data)))
            ret.extend(event.data)
        # not a Meta MIDI event or a Sysex event, must be a general message
        elif isinstance(event, Event):
            status = event.statusmsg | event.channel
            if status != self.RunningStatus or not Pattern.useRunningStatus:
                self.RunningStatus = status
                ret.append(status)
            ret.extend(event.data)
        else:
            raise ValueError("Unknown MIDI Event: " + str(event))
        return ret


def write_midifile(midifile, pattern):
    if type(midifile) in (str, str):
        with open(midifile, "wb") as out:
            return write_midifile(out, pattern)
    writer = FileWriter(midifile)
    return writer.write(pattern)


def read_midifile(midifile):
    if type(midifile) in (str, bytes):
        with open(midifile, "rb") as inp:
            return read_midifile(inp)
    reader = FileReader()
    return reader.read(midifile)
