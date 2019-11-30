### System ###
import csv
import re

### Local ###
from .midi.events import *
from .midi.containers import *
from .events import csv_to_midi_map

COMMENT_DELIMITERS = ("#", ";")


def parse(file):
    """Parses a CSV file into MIDI format.

    Args:
        file: A string giving the path to a file on disk or
              an open file-like object.

    Returns:
        A Pattern() object containing the byte-representations as parsed from
        the input file.
    """

    if isinstance(file, str):
        with open(file, "r") as f:
            return parse(f)

    pattern = Pattern(tick_relative=False)
    for line in csv.reader(file, skipinitialspace=True):
        if not line:
            continue
        if line[0].startswith(COMMENT_DELIMITERS):
            continue
        tr = int(line[0])
        time = int(line[1])
        identifier = line[2].strip()
        if identifier == "Header":
            pattern.format = int(line[3])
            pattern.ntracks = int(line[4])
            pattern.resolution = int(line[5])
        elif identifier == "End_of_file":
            continue
        elif identifier == "Start_track":
            track = Track(tick_relative=False)
            track.type = "MTrk".encode()
            pattern.append(track)
        elif identifier.startswith("Start_"):
            p = re.compile("^Start_(\w{4})_track$")
            m = p.match(identifier)
            if m:
                track = Track(tick_relative=False)
                track.type = m.group(1).encode()
                pattern.append(track)
            else:
                raise ValueError("Unexpected identifier {}".format(identifier))
        else:
            event = csv_to_midi_map[identifier](tr, time, identifier, line[3:])
            track.append(event)
    pattern.make_ticks_rel()
    return pattern
