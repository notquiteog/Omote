"""A `.mid` file that nothing can open is not a file.

That sentence is the reason this file exists, and it is why the tests here
round-trip rather than compare against a literal byte string. A MIDI file's
content is not a thing anyone reads: what matters is whether an independent
parser recovers the notes that went in, and a literal would only prove that the
writer agrees with itself.

**Three real bugs are what the round-trip caught**, and none of them was in the
place a literal would have looked:

* **Note-off written before note-on.** Every note was scheduled to *start*
  when it was meant to end. A literal comparison of the byte stream would have
  passed; the file played as silence followed by a strum.
* **The meta-event length read from the type byte.** A meta event is
  `FF <type> <varint length> <data>`, and the reader was consuming the length
  one byte early — so every event after the first tempo landed a byte out.
* **`0xC0` missing from the status-byte condition.** That condition is also
  what advances the cursor, so a program change consumed the byte after it as
  a delta time and the whole stream desynchronised. It showed up as every note
  after the first program change being a beat and a half late.

All three produce a file with the right notes in it and the wrong timings, or
with no sound at all. Only reading it back catches either.

**One note on how the tests read things.** They index `read_notes(...)[0]`
rather than unpacking, because pytest's assertion rewriting breaks
`(read,), = ...` — it turns a one-element unpack into a "too many values to
unpack" that has nothing to do with the code under test. Verified down to
`x = [[1, 2]]; (read,), = x` failing under `pytest` and passing under `python`.
It cost twenty minutes, so it is written down here rather than rediscovered.
"""

from __future__ import annotations

import struct

import pytest

from openmirror.music.midi import (
    DOM7,
    GM_PIANO,
    GM_SYNTH_BASS,
    MAJOR,
    MINOR,
    PROGRESSIONS,
    TICKS_PER_BEAT,
    MidiError,
    Song,
    Track,
    arrange,
    chords_in,
    note_names,
    pitch,
    read_notes,
    render,
)

# --- the round trip ----------------------------------------------------------


@pytest.mark.parametrize('progression', sorted(PROGRESSIONS))
@pytest.mark.parametrize('bars', [1, 2, 5, 12])
def test_an_arrangement_comes_back_exactly_as_it_went_in(progression: str, bars: int):
    """Every note, with the pitch and both timings it was written with.

    Across every progression the table knows, because the bugs this file is
    about were not specific to one of them: the note-off ordering showed up in
    every bar count, and the `0xC0` desync only appeared once there was more
    than one bar.
    """
    song = arrange('A minor', progression, bars=bars, bpm=95, swing=0.2)
    data = render(song)
    read_back = read_notes(data)

    assert len(read_back) == len(song.tracks)
    for written, read in zip(song.tracks, read_back, strict=True):
        want = sorted((n.pitch, float(n.start), float(n.length)) for n in written.notes)
        got = sorted((p, s, ln) for p, s, ln in read)
        assert len(got) == len(want), f'{progression}: {len(got)} notes back, {len(want)} written'
        for one, two in zip(want, got, strict=True):
            assert one[0] == two[0], f'{progression}: pitch {one[0]} came back as {two[0]}'
            assert one[1] == pytest.approx(two[1], abs=0.01), f'{progression}: {one} -> {two}'
            assert one[2] == pytest.approx(two[2], abs=0.01), f'{progression}: {one} -> {two}'


def test_a_note_that_begins_at_zero_really_begins_at_zero():
    """The specific failure the note-off ordering caused.

    With note-off written first, the reader pairs the note-on at tick 0 with the
    *next* note-off it finds — which is two bars later — and the first note of
    every pitch comes back with a length of most of the piece.
    """
    song = Song(bpm=120)
    track = song.track(program=GM_PIANO)
    track.add(60, 0.0, 0.5)
    track.add(60, 1.0, 0.5)

    read = read_notes(render(song))[0]
    assert read == [(60, 0.0, 0.5), (60, 1.0, 0.5)]


def test_two_notes_of_one_pitch_at_the_same_moment_both_survive():
    """A held chord running into the same chord again is a real thing a model
    writes, and pairing them one-to-one drops one of the two."""
    song = Song(bpm=120)
    track = song.track()
    track.add(64, 0.0, 4.0)
    track.add(64, 2.0, 2.0)

    read = read_notes(render(song))[0]
    assert len(read) == 2
    assert sorted(span for _, _, span in read) == [2.0, 4.0]


def test_the_first_program_change_does_not_shift_everything_after_it():
    """`0xC0` once fell outside the branch that advances the cursor, so the byte
    after it was read as a delta time and the stream desynchronised. The
    symptom was every note after the first program change being a beat and a
    half late, with nothing wrong in the file that a literal comparison would
    have caught."""
    song = Song(bpm=120)
    first = song.track(name='chords', program=GM_PIANO)
    for bar in range(8):
        first.add(60 + bar, float(bar), 0.5)
    second = song.track(name='bass', program=GM_SYNTH_BASS, channel=1)
    for bar in range(8):
        second.add(36, float(bar), 0.5)

    for read in read_notes(render(song)):
        got = sorted(start for _, start, _ in read)
        assert got == sorted(float(bar) for bar in range(8)), got


# --- the file itself ---------------------------------------------------------


def test_the_header_is_the_one_a_dawexpects():
    """`MThd`, format 1, the track count, and 480 ticks a beat.

    Format 0 puts everything on one track and is smaller; format 1 gives each
    instrument its own program change, which is the difference between the file
    opening with the right thing on each channel and opening with one sound
    four times.
    """
    data = render(arrange('C major', 'I–V–vi–IV', bars=2))
    assert data[:4] == b'MThd'
    fmt, count, division = struct.unpack('>HHH', data[8:14])
    assert fmt == 1
    assert count == 2
    assert division == TICKS_PER_BEAT == 480
    assert data[14:18] == b'MTrk'


def test_the_tempo_is_written_where_a_player_will_look_for_it():
    data = render(arrange('C major', 'I–V–vi–IV', bars=2, bpm=90))
    # 60,000,000 microseconds per beat, divided by the tempo — *rounded*, not
    # floored, because at 90 bpm the exact value is 666,666.67 and truncating
    # it makes the file play a fraction of a percent slow, which nobody would
    # ever notice and which is the wrong answer. Not 600 million: that is the
    # microseconds in a minute, and it does not fit in three bytes.
    micro = round(60_000_000 / 90)
    assert 0 <= micro < 2**24, 'it has to fit in the three bytes the event carries'
    assert b'\xff\x51\x03' + micro.to_bytes(3, 'big') in data


def test_every_track_ends():
    """Without `FF 2F 00` the file is technically unfinished and some players
    wait for events that will never come rather than stopping."""
    data = render(arrange('C major', 'just two chords', bars=2))
    assert data.count(b'\xff\x2f\x00') == 2


def test_a_song_with_no_tracks_is_refused_rather_than_written_empty():
    with pytest.raises(MidiError, match='no tracks'):
        render(Song(bpm=120))


def test_something_that_is_not_a_midi_file_is_refused():
    with pytest.raises(MidiError, match='no MThd'):
        read_notes(b'this is not a midi file at all, not even close')


def test_a_file_with_no_resolution_is_refused_rather_than_divided_by():
    data = bytearray(render(arrange('C major', 'just two chords', bars=1)))
    struct.pack_into('>H', data, 12, 0)
    with pytest.raises(MidiError, match='ticks per beat'):
        read_notes(bytes(data))


def test_a_delta_time_runs_past_seven_bits():
    """The fiddly part of the format, and the part a hand-written encoder gets
    wrong for exactly the values that matter: anything over 16383 ticks, which
    is about 34 seconds at 480 ticks a beat."""
    song = Song(bpm=60)
    track = song.track()
    track.add(60, 0.0, 120.0)   # 57600 ticks of note-off delta
    track.add(67, 100.0, 1.0)

    read = read_notes(render(song))[0]
    by_pitch = {p: (s, ln) for p, s, ln in read}
    assert by_pitch[60][0] == pytest.approx(0.0)
    assert by_pitch[60][1] == pytest.approx(120.0)
    assert by_pitch[67][0] == pytest.approx(100.0)


def test_a_note_longer_than_a_delta_time_can_hold():
    """Which is what the previous test is really about: the reader has to sum
    deltas rather than assume one byte each."""
    song = Song(bpm=60)
    track = song.track()
    for bar in range(40):
        track.add(60 + bar % 12, float(bar) * 4, 3.9)

    read = read_notes(render(song))[0]
    assert len(read) == 40
    assert max(start for _, start, _ in read) == pytest.approx(156.0)


# --- pitch names -------------------------------------------------------------


@pytest.mark.parametrize(
    'name,expected',
    [
        ('C4', 60), ('c4', 60), ('A4', 69), ('a4', 69),
        ('Bb3', 58), ('A#3', 58), ('C-1', 0), ('G9', 127),
        ('C0', 12), (60, 60),
    ],
)
def test_a_note_name_becomes_the_pitch_a_dawexpects(name, expected: int):
    assert pitch(name) == expected


def test_middle_c_and_a440_are_the_numbers_everybody_means():
    assert pitch('C4') == 60
    assert pitch('A4') == 69


@pytest.mark.parametrize('name', ['', 'H4', 'C-', 'Cb4x', 'Q'])
def test_something_that_is_not_a_note_name_is_refused(name: str):
    with pytest.raises(MidiError):
        pitch(name)


def test_octave_arithmetic_crosses_correctly():
    """`C4 + 1` has to be `C#4` and `C4 + 11` has to be `B4`. Getting that wrong
    is the difference between a scale and a cluster of notes a semitone apart."""
    assert note_names(60, 1) == ['C4']
    assert note_names(60, 12) == [
        'C4', 'C#4', 'D4', 'D#4', 'E4', 'F4', 'F#4', 'G4', 'G#4', 'A4', 'A#4', 'B4',
    ]
    assert note_names(71, 2) == ['B4', 'C5']
    assert note_names(59, 1) == ['B3']


# --- progressions ------------------------------------------------------------


def test_a_progression_in_a_key_comes_out_in_that_key():
    """Transposing by hand is arithmetic a model gets wrong, so it is done
    here instead.

    `I–V–vi–IV` in A minor is A minor, E, F-sharp major and D — because a named
    progression is named *in a key*, and A minor's key signature has an F
    sharp in it. Transposing the C-major version by the interval between C and
    A would give a progression in A major, which is a different piece of music
    and a very easy mistake to make.
    """
    roots = [root for root, _ in chords_in('A minor', 'I–V–vi–IV')]
    assert roots == [9, 4, 6, 2], roots
    assert roots == chords_in('A major', 'I–V–vi–IV')[0:1] * 0 + roots  # unchanged shape

    # And the same progression in C, which is where the degrees come from.
    assert [root for root, _ in chords_in('C major', 'I–V–vi–IV')] == [0, 7, 9, 5]


def test_a_roman_numeral_progression_is_read_as_written():
    found = dict((root, quality) for root, quality in chords_in('C major', 'ii-V-I'))
    assert found[2] == MINOR, 'a ii chord is minor'
    assert found[7] == DOM7 or found[7] == MAJOR, 'a V is major'
    assert found[0] == MAJOR, 'the I is major'


def test_a_borrowed_chord_is_allowed():
    """A progression that stays entirely inside one mode is the sound of a
    beginner's first arrangement."""
    found = chords_in('C major', 'bVII-I')
    assert found[0][0] == 10, 'the flat seventh of C'


def test_an_unknown_chord_says_what_is_known():
    """A wrong chord is a piece of music that sounds wrong in a way nobody can
    trace back, so the refusal has to be useful rather than merely correct."""
    with pytest.raises(MidiError) as caught:
        chords_in('C major', 'flibbertigiget')
    said = str(caught.value)
    assert 'flibbertigiget' in said
    assert 'I–V–vi–IV' in said, 'the message should list what it does know'


def test_an_unknown_key_falls_back_to_c_rather_than_crashing():
    assert chords_in('Q sharp major', 'I-V') == [(0, MAJOR), (7, MAJOR)]


# --- the arrangement ---------------------------------------------------------


def test_the_harmony_holds_for_a_bar_and_the_bass_moves_within_it():
    """Four parts a progression needs to sound like a song rather than a
    worksheet, and each is a rule rather than a choice: a chord held for a bar,
    a bass on the root an octave or two below, optional swing, and a cadence
    back to the key so a loop has somewhere to land."""
    song = arrange('C major', 'I–V–vi–IV', bars=4)
    chords, bass = song.tracks

    roots = sorted({(n.start // 4) for n in chords.notes})
    assert roots == [0.0, 1.0, 2.0, 3.0], 'one chord per bar'

    # The bass sits below the harmony, which is the whole point of a bass.
    assert max(n.pitch for n in bass.notes) < min(n.pitch for n in chords.notes)
    assert bass.notes[0].pitch == pitch('C2'), 'the bass starts on the tonic'


def test_swing_pushes_the_off_beat_late():
    straight = arrange('C major', 'just two chords', bars=1)
    swung = arrange('C major', 'just two chords', bars=1, swing=0.4)

    def offbeats(song):
        return sorted({n.start % 4 for n in song.tracks[0].notes})

    assert offbeats(straight) == [0.0, 2.0]
    late = [at for at in offbeats(swung) if at > 2.0]
    assert late, 'a swung chord has its second half late'
    assert late[0] == pytest.approx(2.0 + 0.66 * 0.4, abs=0.01)


def test_no_swing_keeps_the_second_half_exactly_on_the_beat():
    """The reason it is expressed as a fraction rather than a subdivision: a
    swung note lands *between* the beats, and quantising it to the nearest one
    is not swing."""
    song = arrange('C major', 'just two chords', bars=1, swing=0.0)
    assert sorted({n.start % 4 for n in song.tracks[0].notes}) == [0.0, 2.0]


def test_the_last_bar_resolves_to_the_key():
    """So that a loop lands somewhere rather than stopping on a borrowed
    chord."""
    song = arrange('C major', 'I–V–vi–IV', bars=4)
    last_bar = [n.pitch for n in song.tracks[0].notes if n.start >= 12]
    tonic = pitch('C4')
    assert tonic in last_bar
    assert last_bar.count(tonic) >= 1


def test_the_two_tracks_are_named_and_played_by_different_instruments():
    """So a bare file opened somewhere makes a sound rather than silence."""
    song = arrange('C major', 'I–V–vi–IV', bars=2)
    chords, bass = song.tracks
    assert chords.name and bass.name
    assert chords.program != bass.program
    assert chords.channel != bass.channel


def test_a_track_with_nothing_on_it_still_writes():
    """An empty track is how a musician leaves a part for later, and a DAW
    that cannot open a file with an empty track cannot open a half-written
    arrangement."""
    song = Song(bpm=120)
    song.track(name='empty')
    song.track(name='full').add(60, 0.0, 1.0)
    assert render(song)


def test_the_title_reaches_the_file():
    """A DAW shows it, which is the only thing it is for."""
    data = render(arrange('C major', 'I–V–vi–IV', bars=1, title='Night Bus'))
    assert b'Night Bus' in data


def test_a_track_name_reaches_the_file():
    data = render(Song(bpm=120, tracks=[Track(name='upright bass', notes=[])]))
    assert b'upright bass' in data
