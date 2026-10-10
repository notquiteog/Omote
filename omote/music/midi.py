"""Writing a Standard MIDI File, which is how "something for FL Studio" is answered.

**Not a `.flp`.** FL Studio's project format is a proprietary binary and there
is no honest way to produce one from outside the application; anything that
claims to is either reverse-engineering a format that changes, or shelling out
to a FL Studio that may not be installed. So this writes `.mid`, which is a
published specification with no secrets in it and which FL Studio, Ableton,
Logic, Reaper, Cubase and a hundred other things import natively.

**Not a plugin host either.** FL Studio's scripting is Lua inside a running
FL Studio, which needs a Windows or macOS box with FL Studio open — a
different product from this one, on a different platform, in a different
process. So the honest scope is: the agent arranges the music and this module
renders it to a file the DAW opens.

That division of labour is the good part. Arranging music — a chord
progression, a bass line that follows the harmony, a melody that turns the
bars before the end — is a thing a language model is genuinely good at, and
something it is genuinely bad at doing by hand in a plugin's parameter list.
Writing the file format is a thing no model should be doing at all, and here
it is a hundred lines of standard library with a round-trip test.

**The format, briefly.** A `.mid` is a header chunk (`MThd`, a format number,
a track count, and a ticks-per-quarter-note division) followed by one `MTrk`
chunk per track. Inside a track every event is preceded by a delta time as a
variable-length integer — 7 bits per byte, high bit set on all but the last —
so the encoding is the fiddly part and it is done here rather than by hand
anywhere else. Note on is `0x90` plus a pitch and a velocity; note off is
`0x80` plus the same pitch and a velocity of zero; tempo is a meta event
`0x51` carrying three bytes of microseconds per quarter note.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any

# Resolution. 480 ticks per quarter note is the convention every DAW agrees
# on, and it divides by every note length anybody writes in 16ths, 32nds and
# triplets — which 96 does not, and a file with an awkward division gets
# quantised to something the musician did not ask for.
TICKS_PER_BEAT = 480

#: Middle C, and the A above it. Named because `60` and `69` are not obvious.
MIDDLE_C = 60
A440 = 69

# Major and minor keys as semitone offsets from C, and the number of sharps
# (positive) or flats (negative) the signature should carry.
KEYS: dict[str, tuple[int, int]] = {
    'C major': (0, 0), 'C minor': (0, -3),
    'D major': (2, 2), 'D minor': (2, -1),
    'E major': (4, 4), 'E minor': (4, 1),
    'F major': (5, -1), 'F minor': (5, -4),
    'G major': (7, 1), 'G minor': (7, -2),
    'A major': (9, 3), 'A minor': (9, 0),
    'B major': (11, 5), 'B minor': (11, 2),
}

# General MIDI programs, so a DAW that gets a bare `.mid` picks something
# rather than nothing. A piano is 0 and a synth bass is 38, which is what the
# two voices an arrangement almost always wants to start with.
GM_PIANO = 0
GM_SYNTH_BASS = 38

# Pitch names for parsing. Both spellings, because `Bb` is how a person writes
# it and `A#` is what a file gives back.
_NOTE_NAMES = {
    'c': 0, 'd': 2, 'e': 4, 'f': 5, 'g': 7, 'a': 9, 'b': 11,
}


class MidiError(ValueError):
    """Something about the arrangement could not be written."""


def pitch(name: str | int) -> int:
    """`"C4"`, `"Bb3"`, `"F#5"` or a raw number to a MIDI pitch.

    Octave 4 starts at `MIDDLE_C`, which is the convention every sequencer uses
    and the one that makes `C4` mean the same thing here as in the DAW the file
    is about to be opened in.
    """
    if isinstance(name, int):
        return name
    text = str(name).strip()
    if not text:
        raise MidiError('a pitch is required')

    letter = text[0].lower()
    if letter not in _NOTE_NAMES:
        raise MidiError(f'{name!r} is not a note name')
    value = _NOTE_NAMES[letter]
    at = 1
    if at < len(text) and text[at] in '#b':
        value += 1 if text[at] == '#' else -1
        at += 1
    rest = text[at:].strip()
    if rest and not (rest.isdigit() or (rest[0] == '-' and rest[1:].isdigit())):
        raise MidiError(f'{name!r} is not a note name')
    octave = int(rest) if rest else 4
    return (octave + 1) * 12 + value


def note_names(low: int, count: int) -> list[str]:
    """`[note_name(...)]` — the transpose by an interval, in semitones.

    Octave arithmetic is the fiddly part and it is here rather than at every
    call site: `C4 + 12` has to be `C5` and `C4 - 1` has to be `B3`, and
    getting that wrong is the difference between a bass line and a cluster of
    notes a semitone apart.
    """
    names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
    return [f'{names[(low + step) % 12]}{(low + step) // 12 - 1}' for step in range(count)]


@dataclass(slots=True)
class Note:
    """One note, in beats.

    `pitch` is a MIDI number, `start` and `length` are in beats from the
    beginning of the piece, and `velocity` is 1–127. Beats rather than ticks
    because a beat is what a musician means and `start`/`length` in beats is
    the form the model gets the arithmetic right in.
    """

    pitch: int
    start: float
    length: float
    velocity: int = 80
    channel: int = 0

    def ticks(self) -> tuple[int, int]:
        at = max(0, round(self.start * TICKS_PER_BEAT))
        for_ = max(1, round(self.length * TICKS_PER_BEAT))
        return at, for_

    def as_json(self) -> dict[str, Any]:
        return {
            'pitch': self.pitch,
            'name': note_names(self.pitch, 1)[0],
            'start': self.start,
            'length': self.length,
            'velocity': self.velocity,
            'channel': self.channel,
        }


@dataclass(slots=True)
class Track:
    """One instrument's part: a program, and the notes it plays.

    `program` is a General MIDI instrument number, which is a *suggestion* — a
    DAW with a sound library loaded will replace it, and one without will use
    its own fallback. It is here so that a bare file opened somewhere makes a
    sound rather than silence.
    """

    name: str = ''
    program: int = GM_PIANO
    channel: int = 0
    notes: list[Note] = field(default_factory=list)
    # Cubes the track's volume, as a meta event. 100 is "as written", which is
    # the only value that means anything without knowing the player.
    volume: int | None = None

    def add(self, pitch: int, start: float, length: float, velocity: int = 80) -> Note:
        note = Note(pitch=pitch, start=start, length=length, velocity=velocity, channel=self.channel)
        self.notes.append(note)
        return note

    @property
    def beats(self) -> float:
        return max((n.start + n.length for n in self.notes), default=0.0)


@dataclass(slots=True)
class Song:
    """A piece: a tempo, a key, and some tracks."""

    title: str = ''
    bpm: float = 120.0
    key: str = 'C major'
    # 4/4, written as [numerator, denominator-as-a-power-of-two].
    beats_per_bar: int = 4
    beat_unit: int = 4
    tracks: list[Track] = field(default_factory=list)

    def track(self, *, name: str = '', program: int = GM_PIANO, channel: int = 0, volume: int | None = None) -> Track:
        made = Track(name=name, program=program, channel=channel, volume=volume)
        self.tracks.append(made)
        return made

    @property
    def bars(self) -> int:
        per_bar = self.beats_per_bar * (4 / self.beat_unit)
        beats = max((t.beats for t in self.tracks), default=0.0)
        return max(1, round(beats / per_bar))


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------


def _varlint(value: int) -> bytes:
    """A delta time or length: 7 bits per byte, high bit set on all but the last.

    The one genuinely fiddly part of the format, and the part a hand-written
    encoder gets wrong for exactly the values that matter — anything over 16383
    ticks, which is about 34 seconds at 480 ticks a beat. So it is here, once,
    and the tests round-trip it rather than checking a literal.
    """
    if value < 0:
        raise MidiError('a MIDI delta time cannot be negative')
    out = bytearray([value & 0x7F])
    value >>= 7
    while value:
        out.insert(0, (value & 0x7F) | 0x80)
        value >>= 7
    return bytes(out)


def _chunk(tag: bytes, body: bytes) -> bytes:
    return tag + struct.pack('>I', len(body)) + body


def _track_chunk(track: Track, *, bpm: float, key: str, title: str = '') -> bytes:
    """One `MTrk`, with its events sorted by the time they happen.

    Sorting is not cosmetic. A DAW reading the file takes the events in the
    order they appear and a note-off that arrives before its note-on leaves a
    note hanging forever — which is a *stuck* note, not a quiet one. The notes
    are given in whatever order the arrangement produced them, and chords and
    runs come out of that in whatever order they were built.
    """
    # (tick, order-within-tick, bytes). Note-offs sort before note-ons at the
    # same tick so that a repeated pitch retriggers instead of being swallowed.
    events: list[tuple[int, int, bytes]] = []

    # 0x51 tempo, then 0x58 time signature, then 0x59 key. All at tick zero.
    micro = int(round(60_000_000 / bpm)) if bpm > 0 else 500_000
    events.append((0, 0, b'\xff\x51\x03' + micro.to_bytes(3, 'big')))
    if track.channel == 0:
        # The signature and key belong on one track, not all of them: a DAW
        # that reads them off every track applies them repeatedly, and some of
        # them reset the tempo to the default on the second pass.
        events.append((0, 1, b'\xff\x58\x04' + bytes([4, 2, 24, 8])))
        semitones, sharps = KEYS.get(key, (0, 0))
        events.append((0, 2, b'\xff\x59\x02' + bytes([semitones & 0xFF, sharps & 0xFF])))
        # The song's title, then the track's name. Both are what a DAW shows
        # in its track list, and neither is read by anything else — which is
        # exactly why the piece has to carry one of them, because a file called
        # "Untitled 3" in a folder of forty is a file nobody finds.
        for order, text in ((3, title), (4, track.name)):
            if text:
                raw = text.encode('utf-8')[:127]
                events.append((0, order, b'\xff\x03' + _varlint(len(raw)) + raw))
        if track.program is not None:
            events.append((0, 5, bytes([0xC0 | (track.channel & 0x0F), track.program & 0x7F])))
        if track.volume is not None:
            events.append((0, 6, bytes([0xB0 | (track.channel & 0x0F), 7]) + _varlint(track.volume)))

    for note in sorted(track.notes, key=lambda n: (n.start, n.pitch)):
        at, length = note.ticks()
        channel = note.channel & 0x0F
        velocity = max(1, min(127, note.velocity))
        # Note-on at the start and note-off at the end, in that order in the
        # file *and* in the tick they claim. This had them the other way round,
        # and the reader in this module — which is why it exists — reported
        # every note as starting when it ended and the bass track as empty.
        #
        # Note-off is 0x80 with velocity 0. The specification also allows 0x90
        # with velocity 0, and some very old players only understand that one
        # — but it is ambiguous with a note-on of velocity zero, so the
        # explicit form is used.
        events.append((at, 1, bytes([0x90 | channel, note.pitch & 0x7F, velocity])))
        events.append((at + length, 0, bytes([0x80 | channel, note.pitch & 0x7F, 0x00])))

    # At the same tick a note-off comes before a note-on, so a pitch that is
    # struck twice retriggers instead of the first note swallowing the second.
    events.sort(key=lambda item: (item[0], item[1]))

    body = bytearray()
    previous = 0
    for tick, _, raw in events:
        body += _varlint(tick - previous)
        body += raw
        previous = tick
    # End of track. Without it the file is technically unfinished and some
    # players wait for events that will never come rather than stopping.
    body += _varlint(0) + b'\xff\x2f\x00'
    return _chunk(b'MTrk', bytes(body))


def render(song: Song) -> bytes:
    """A `.mid` file as bytes.

    Format 1 rather than 0, always. Format 0 puts everything on one track and
    is smaller; format 1 gives each instrument its own track with its own
    program change, which is what makes the file open in a DAW with the right
    thing selected on each channel rather than with one sound and four copies
    of it. The four extra bytes are the cheapest decision in this module.
    """
    if not song.tracks:
        raise MidiError('there is nothing to write: no tracks')

    header = _chunk(b'MThd', struct.pack('>HHH', 1, len(song.tracks), TICKS_PER_BEAT))
    out = bytearray(header)
    for track in song.tracks:
        out += _track_chunk(track, bpm=song.bpm, key=song.key, title=song.title)
    return bytes(out)


# ---------------------------------------------------------------------------
# Reading it back
# ---------------------------------------------------------------------------


def read_notes(data: bytes) -> list[list[tuple[int, int, int]]]:
    """Every note in a `.mid`, as `(pitch, start_beats, length_beats)` per track.

    A reader in the same module, and the reason there is one: a MIDI file
    nothing else can open is not a file, and the only way to be sure this
    writer produces something a DAW will accept is to parse it back with
    independent code and compare. The tests do exactly that rather than
    asserting a literal byte string, which is how a format bug hides.
    """
    if not data.startswith(b'MThd'):
        raise MidiError('that is not a MIDI file: no MThd header')
    _fmt, _count, division = struct.unpack('>HHH', data[8:14])
    if not division:
        raise MidiError('that MIDI file declares no ticks per beat, so it cannot be read')
    at = 14

    tracks: list[list[tuple[int, int, int]]] = []
    while at + 8 <= len(data):
        tag = data[at : at + 4]
        size = struct.unpack('>I', data[at + 4 : at + 8])[0]
        body = data[at + 8 : at + 8 + size]
        at += 8 + size
        if tag != b'MTrk':
            continue

        tick = 0
        cursor = 0
        # (channel, pitch) -> the ticks its still-sounding notes started at, as
        # a stack rather than a single value. Two notes of the same pitch can
        # genuinely overlap — a held chord running into the same chord again —
        # and a single value silently swallows one of them.
        pending: dict[tuple[int, int], list[int]] = {}
        done: list[tuple[int, int, int]] = []
        while cursor < len(body):
            delta, cursor = _read_varlint(body, cursor)
            tick += delta
            if cursor >= len(body):
                break
            status = body[cursor]
            if status == 0xFF:
                # FF, then a one-byte event type, then a varint length, then the
                # data. Treating the *type* as the length is the bug this
                # comment is about: every meta event lands a byte out, and a
                # file that reads back with the right notes and the wrong
                # timings is worse than one that refuses.
                cursor += 1
                cursor += 1  # the event type
                length, cursor = _read_varlint(body, cursor)
                cursor += length
                continue
            if status in (0xF0, 0xF7):
                # System exclusive: FF is not part of it, so the length follows
                # the status byte directly.
                length, cursor = _read_varlint(body, cursor + 1)
                cursor += length
                continue
            high = status & 0xF0
            if high in (0x80, 0x90, 0xA0, 0xB0, 0xC0, 0xD0, 0xE0):
                channel = status & 0x0F
                # Program change and channel pressure carry one data byte;
                # everything else carries two. `0xC0` was missing from the
                # condition above once, and because the branch is also what
                # advances the cursor, the reader consumed the following byte
                # as a delta time and desynchronised — which showed up as every
                # note after the first program change being a beat and a half
                # late, with nothing in the file wrong.
                need = 1 if high in (0xC0, 0xD0) else 2
                args = body[cursor + 1 : cursor + 1 + need]
                cursor += 1 + need
                if high == 0x90 and args[1]:
                    pending.setdefault((channel, args[0]), []).append(tick)
                elif high == 0x80 or (high == 0x90 and not args[1]):
                    held = pending.get((channel, args[0]))
                    if held:
                        started = held.pop(0)
                        done.append((args[0], started, tick - started))
        tracks.append([
            (note, round(started / division, 3), round(span / division, 3))
            for note, started, span in sorted(done)
        ])
    return tracks


def _read_varlint(data: bytes, at: int) -> tuple[int, int]:
    value = 0
    while at < len(data):
        byte = data[at]
        at += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, at
    raise MidiError('a MIDI variable-length number ran off the end of the file')


# ---------------------------------------------------------------------------
# Arrangements
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Chord:
    """A chord as a root and a quality, which is what a key signature is for."""

    root: int                       # 0..11, semitones from C
    # Semitone offsets from the root. Major, minor, diminished, dominant
    # seventh, major seventh, minor seventh, half-diminished, diminished
    # seventh, sus2 and sus4 — the eleven a pop progression is made of.
    intervals: tuple[int, ...] = (0, 4, 7)

    def pitches(self, octave: int = 4) -> list[int]:
        return [(octave + 1) * 12 + (self.root + step) % 12 for step in self.intervals]


MAJOR = (0, 4, 7)
MINOR = (0, 3, 7)
DIM = (0, 3, 6)
DOM7 = (0, 4, 7, 10)
MAJ7 = (0, 4, 7, 11)
MIN7 = (0, 3, 7, 10)
HALF_DIM7 = (0, 3, 6, 10)
DIM7 = (0, 3, 6, 9)
SUS2 = (0, 2, 7)
SUS4 = (0, 5, 7)
POWER = (0, 7)

#: Progression names to the chords in them. Roman numerals would be more
#: correct and much harder to read in a table, so these are named after what
#: they sound like, which is how they are recognised.
PROGRESSIONS: dict[str, tuple[int, ...]] = {
    'I–V–vi–IV': (0, 7, 9, 5),
    'i–VI–III–VII': (0, 8, 3, 10),
    'vi–IV–I–V': (9, 5, 0, 7),
    'ii–V–I': (2, 7, 0),
    'I–vi–IV–V': (0, 9, 5, 7),
    'i–iv–v': (0, 5, 7),
    '12-bar blues': (0, 0, 3, 0, 5, 5, 0, 0, 3, 3, 5, 5),
    'andalusian': (9, 8, 7, 5),
    'just two chords': (0, 7),
    'turnaround': (0, 5, 2, 7),
}

#: Chords by how they resolve, so a progression can be written in a key rather
#: than by transposing by hand — which is the arithmetic a model gets wrong.
_ROMAN: dict[str, int] = {
    'I': 0, 'II': 2, 'III': 4, 'IV': 5, 'V': 7, 'VI': 9, 'VII': 11,
    'i': 0, 'ii': 2, 'iii': 4, 'iv': 5, 'v': 7, 'vi': 9, 'vii': 11,
}

_ROMAN_QUALITY: dict[str, tuple[int, ...]] = {
    'I': MAJOR, 'II': MAJOR, 'III': MAJOR, 'IV': MAJOR, 'V': MAJOR, 'VI': MAJOR, 'VII': MAJOR,
    'i': MINOR, 'ii': MINOR, 'iii': MINOR, 'iv': MINOR, 'v': MINOR, 'vi': MINOR, 'vii': MINOR,
    'I7': DOM7, 'ii7': MIN7, 'iii7': MIN7, 'IV7': DOM7, 'V7': DOM7, 'VI7': MIN7, 'VII7': MIN7,
    'i7': MIN7, 'Imin7': MIN7,
}

#: Borrowed-from-the-minor chords, because a progression that stays entirely
#: inside one mode is the sound of a beginner's first arrangement.
_BORROWED: dict[str, tuple[int, ...]] = {'bII': (0, 4, 8), 'bIII': MAJOR, 'bVI': MAJOR, 'bVII': MAJOR}
#: Their degrees. A flat numeral is a *degree*, not a semitone offset, so it
#: needs the same lookup the plain numerals use — and the reason `bVII` used to
#: be refused despite appearing in the sentence telling you it was available.
_BORROWED_DEGREE: dict[str, int] = {'bII': 1, 'bIII': 2, 'bVI': 8, 'bVII': 10}


def chords_in(key: str, progression: str) -> list[tuple[int, tuple[int, ...]]]:
    """A progression written out as `(root, intervals)` in a key.

    Accepts both the named progressions above and Roman numerals, because both
    are how people write these and neither is wrong. A progression the table
    does not know is a `MidiError` naming what is known, rather than a guess:
    a wrong chord is a piece of music that sounds wrong in a way nobody can
    trace back to where it came from.
    """
    semitone, _ = KEYS.get(key, (0, 0))
    if progression in PROGRESSIONS:
        roots = PROGRESSIONS[progression]
        # A named progression is diatonic to its own key, so the chords come
        # from the major or minor triad on each root depending on the mode.
        minor = progression[:3].lower() == 'i–' or progression in ('i–VI–III–VII', 'i–iv–v')
        quality_of = MINOR if minor else MAJOR
        return [((semitone + root) % 12, quality_of) for root in roots]

    found: list[tuple[int, tuple[int, ...]]] = []
    for token in progression.replace(' ', '').split('-'):
        token = token.strip()
        if not token:
            continue
        intervals = _BORROWED.get(token) or _ROMAN_QUALITY.get(token)
        degree = _BORROWED_DEGREE.get(token) or _ROMAN.get(token.rstrip('7').replace('min', ''))
        if intervals is None or degree is None:
            raise MidiError(
                f'{token!r} is not a chord I know. Try a Roman numeral (I, ii, V7, bVII) or one of: '
                + ', '.join(PROGRESSIONS)
            )
        found.append(((semitone + degree) % 12, intervals))
    if not found:
        raise MidiError('a progression needs at least one chord')
    return found


def arrange(
    key: str,
    progression: str,
    *,
    bars: int = 8,
    bpm: float = 120.0,
    title: str = '',
    swing: float = 0.0,
    octave: int = 4,
    bass_octave: int = 2,
) -> Song:
    """A chord-and-bass arrangement, which is a real piece of music.

    The four parts a chord progression needs to sound like a song rather than a
    worksheet, and each of them is a rule rather than a choice:

    * **The chord, held.** One bar each, which is what almost everything does
      and what makes a listener hear a progression rather than four unrelated
      chords.
    * **The bass, on the root.** An octave or two below, in half notes — long
      enough to hear the harmony change, short enough to move.
    * **Swing**, optionally. The second eighth of each pair lands late, which
      is the single biggest difference between something written and something
      played. Applied to the chord, not the bass, because a swung bass line
      over a straight chord reads as two different performances at once.
    * **The turnaround.** The last bar goes back to the key rather than sitting
      on the last chord, so a loop has somewhere to land.

    What it deliberately does not do is write a melody. A melody is the part a
    person has opinions about, and a generated one is a liability: it is
    easier to add notes to a track in a DAW than to argue with an arrangement
    about which of its notes to delete.
    """
    song = Song(title=title or f'{key} {progression}', bpm=bpm, key=key)
    chords = chords_in(key, progression)

    harmony = song.track(name='chords', program=GM_PIANO, channel=0, volume=100)
    bass = song.track(name='bass', program=GM_SYNTH_BASS, channel=1, volume=96)

    per_bar = 4
    for bar in range(bars):
        root, intervals = chords[bar % len(chords)]
        at = bar * per_bar
        for step in (0, 2):
            # Swing pushes the off-beat eighth late, which is a fraction of a
            # beat rather than a subdivision, and is why it is expressed as a
            # fraction of the beat at all.
            offset = step + (0.66 * swing)
            for voice in intervals:
                harmony.add((octave + 1) * 12 + (root + voice) % 12, at + offset, 2 - swing * 0.5, 76)
        bass.add((bass_octave + 1) * 12 + root, at, 2 - swing * 0.4, 92)
        bass.add((bass_octave + 1) * 12 + root, at + 2, 2 - swing * 0.4, 84)

    # A cadence into the key on the final bar, so a loop lands somewhere.
    tonic = (octave + 1) * 12 + (semitone_of(key) % 12)
    for voice in MAJOR:
        harmony.add(tonic + voice, (bars - 1) * per_bar + 2, 2, 72)

    return song


def semitone_of(key: str) -> int:
    """The tonic of a key, in semitones from C."""
    return KEYS.get(key, (0, 0))[0]


__all__ = [
    'A440', 'Chord', 'DIM7', 'DOM7', 'GM_PIANO', 'GM_SYNTH_BASS', 'HALF_DIM7', 'MAJ7', 'MAJOR',
    'MIDDLE_C', 'MIN7', 'MINOR', 'MidiError', 'Note', 'PROGRESSIONS', 'Song', 'SUS2', 'SUS4',
    'TICKS_PER_BEAT', 'Track', 'arrange', 'chords_in', 'note_names', 'pitch', 'read_notes', 'render',
]
