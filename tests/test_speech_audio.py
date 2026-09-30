"""What a speech endpoint is allowed to answer with, and what happens if it does not.

This file is about the failure that made voice output "blown out and full of
static even at lowest volume", and it is worth being precise about why that
symptom is diagnostic rather than merely unpleasant.

The player takes bytes as 16-bit PCM and divides by 32768. So does every other
consumer of raw PCM. Hand those bytes a *compressed* body — mp3, because a
gateway ignored `response_format`, or a JSON envelope holding base64 — and
every "sample" is a fragment of a compressed bitstream. Reinterpreted at 24 kHz
that is broadband noise at full scale: loud, and completely unaffected by the
system volume, because the system volume scales a signal that is not one.

That is also why the fix here is a refusal rather than a filter. A filter
cannot tell hiss from speech, and the endpoint's `Content-Type` can. So the
content type is read, and anything compressed is refused *by name* — the
message says which format and which model, because that is something a person
can act on and "voice is broken" is not.

Alongside it: the rate, and the level.

* **Rate.** Three different numbers used to be in play — the rate the pipeline
  asked for (the *microphone's* 16 kHz, which was the wrong question), the rate
  the provider declared on its class (a default nothing overrode), and the rate
  the endpoint used. The browser baked the declared one into every buffer it
  built, so a mismatch was a pitch shift plus aliasing: the other half of the
  hiss.
* **Level.** A synthesiser with a DC bias puts every sample on one side of
  zero, which is a square wave, which is harmonically rich and sounds exactly
  like a failed speaker. And a sample that has already been clipped carries its
  distortion with it, so lowering the volume only makes quieter distortion.
  Both are fixed at the source, before anything can be scaled down.
"""

from __future__ import annotations

import base64
import json
import math
import struct

import pytest

from openmirror.providers.speech_audio import (
    PEAK_CEILING,
    SpeechFormatError,
    channels_of,
    codec,
    declared_rate,
    guard,
    json_audio,
    resample,
    to_pcm16,
    wav_layout,
)


def tone(rate: int, seconds: float = 1.0, hz: float = 440.0, amplitude: int = 12_000) -> bytes:
    """16-bit mono PCM of a sine."""
    count = int(rate * seconds)
    return struct.pack(f'<{count}h', *[
        int(amplitude * math.sin(2 * math.pi * hz * i / rate)) for i in range(count)
    ])


def wav(pcm: bytes, rate: int, channels: int = 1, bits: int = 16, before: bytes = b'') -> bytes:
    """A WAV with `before` wedged between the format and the data chunk.

    The leading `16` in the format string is the chunk's *size*, which is how
    `openmirror.voice.pipeline.pcm_to_wav` writes it: seven values into
    `<IHHIIHH` are chunk size, audio format, channels, rate, byte rate, block
    align and bit depth, and the size doubles as the first field. Reading that
    as a format code with a leading `16` is how a test helper ends up
    asserting a header is broken when it is not.
    """
    block = channels * bits // 8
    fmt = struct.pack('<IHHIIHH', 16, 1, channels, rate, rate * block, block, bits)
    return (
        b'RIFF'
        + struct.pack('<I', 4 + len(fmt) + len(before) + 8 + len(pcm))
        + b'WAVE'
        + b'fmt '
        + fmt
        + before
        + b'data'
        + struct.pack('<I', len(pcm))
        + pcm
    )


# --- what the body actually is -----------------------------------------------


def test_compressed_audio_is_refused_by_name():
    """The whole point. A forwarded mp3 is what static sounds like."""
    for mime, said in (
        ('audio/mpeg', 'mp3'),
        ('audio/mpeg; rate=24000', 'mp3'),
        ('audio/ogg; codecs=opus', 'ogg'),
        ('audio/flac', 'flac'),
        ('audio/webm', 'webm'),
    ):
        with pytest.raises(SpeechFormatError) as caught:
            codec(mime, model='hexgrad/kokoro-82m', provider='openrouter')
        said_out = str(caught.value)
        assert said in said_out, said_out
        # The message has to be actionable, not merely accurate.
        assert 'openrouter' in said_out and 'kokoro' in said_out, said_out


def test_raw_pcm_is_taken_at_face_value():
    for mime in ('audio/pcm', 'audio/L16', 'audio/x-pcm', 'audio/raw', 'audio/pcm;rate=24000'):
        assert codec(mime) == 'pcm', mime


def test_an_absent_content_type_is_treated_as_pcm():
    """Not a formality: most speech endpoints send nothing useful at all.

    Refusing an unknown type would break every install that works today, so
    unknown means PCM and a warning in the log.
    """
    assert codec('') == 'pcm'
    assert codec('application/octet-stream') == 'pcm'
    assert codec('application/json') == 'json'
    assert codec('audio/wav') == 'wav'


def test_the_rate_is_read_from_the_content_type():
    assert declared_rate('audio/pcm;rate=24000') == 24_000
    assert declared_rate('audio/pcm; rate=44100; channels=1') == 44_100
    assert declared_rate('audio/wav; sample-rate=16000') == 16_000
    assert declared_rate('audio/x-wav; sample_rate="8000"') == 8000
    assert declared_rate('audio/pcm') is None
    assert declared_rate('') is None


def test_channel_count_is_read_from_the_content_type():
    assert channels_of('audio/pcm;channels=1') == 1
    assert channels_of('audio/pcm;channels=2') == 2
    assert channels_of('audio/pcm') == 1


# --- unwrapping --------------------------------------------------------------


def test_a_wav_container_is_unwrapped():
    """A WAV body is PCM, and unwrapping it is cheaper than refusing it —
    several endpoints answer with one whatever `response_format` asked for."""
    pcm = tone(24_000, 0.05)
    got, rate = to_pcm16(wav(pcm, 24_000), 'audio/wav')
    assert got == pcm
    assert rate == 24_000


def test_a_wav_with_a_chunk_before_its_data_is_unwrapped():
    """The header is not always 44 bytes.

    A `LIST`/`INFO` chunk before the data is common, and reading a fixed offset
    plays the header as audio: a click, then a signal shifted by 44 samples. So
    the chunk list is walked — and the walk has to *finish*, because returning
    the format chunk's payload offset is the same bug in a different place, and
    it was a bug here for exactly one commit.
    """
    pcm = tone(24_000, 0.05)
    info = b'LIST' + struct.pack('<I', 4) + b'INFO'
    blob = wav(pcm, 24_000, before=info)

    offset, rate, channels = wav_layout(blob)
    assert offset == len(blob) - len(pcm), 'the data offset is where the samples start'
    assert rate == 24_000 and channels == 1

    got, found = to_pcm16(blob, 'audio/wav')
    assert got == pcm
    assert found == 24_000


def test_a_data_chunk_with_no_format_is_refused_rather_than_guessed():
    pcm = tone(24_000, 0.05)
    headerless = b'RIFF' + struct.pack('<I', 4 + 8 + len(pcm)) + b'WAVE' + b'data' + struct.pack('<I', len(pcm)) + pcm
    assert wav_layout(headerless) is None
    with pytest.raises(SpeechFormatError, match='not one'):
        to_pcm16(headerless, 'audio/wav')


def test_something_claiming_to_be_a_wav_and_not_being_one_is_refused():
    with pytest.raises(SpeechFormatError, match='not one'):
        to_pcm16(b'this is not audio at all', 'audio/wav', model='m', provider='p')


def test_a_json_envelope_is_unwrapped():
    """The Google and Gemini shape, which several gateways proxy unchanged."""
    pcm = tone(24_000, 0.05)
    body = json.dumps({
        'audioContent': {'data': base64.b64encode(pcm).decode(), 'mimeType': 'audio/pcm;rate=24000'}
    }).encode()
    got, rate = to_pcm16(body, 'application/json')
    assert got == pcm
    assert rate == 24_000


def test_a_flat_json_envelope_is_unwrapped_too():
    """Which gateway is in front of the model is not something the caller chose."""
    pcm = tone(24_000, 0.05)
    for key in ('data', 'audio', 'audioContent', 'audio_data', 'pcm'):
        body = json.dumps({key: base64.b64encode(pcm).decode()}).encode()
        got, _ = to_pcm16(body, 'application/json')
        assert got == pcm, key


def test_json_with_no_audio_in_it_says_what_it_got():
    """Better than silence: the first 120 characters are the whole diagnosis."""
    body = json.dumps({'error': {'message': 'quota exceeded for this model'}}).encode()
    with pytest.raises(SpeechFormatError) as caught:
        to_pcm16(body, 'application/json', model='some/model', provider='somewhere')
    assert 'quota exceeded' in str(caught.value)


def test_a_compressed_payload_inside_a_json_envelope_is_still_refused():
    """Unwrapping must not become a way round the format check."""
    body = json.dumps({
        'audioContent': {'data': base64.b64encode(b'\xff\xfb\x90d' * 100).decode(), 'mimeType': 'audio/mpeg'}
    }).encode()
    with pytest.raises(SpeechFormatError, match='mp3'):
        to_pcm16(body, 'application/json', model='m', provider='p')


def test_json_audio_ignores_things_that_are_not_audio():
    assert json_audio(b'not json') is None
    assert json_audio(b'[]') is None
    assert json_audio(json.dumps({'error': 'nope'}).encode()) is None
    assert json_audio(json.dumps({'data': 'not valid base64 !!!'}).encode()) is None


# --- rate --------------------------------------------------------------------


def test_raw_pcm_passes_through_untouched():
    pcm = tone(24_000, 0.05)
    got, rate = to_pcm16(pcm, 'audio/pcm;rate=24000')
    assert got == pcm
    assert rate == 24_000


def test_an_endpoint_that_does_not_say_its_rate_says_nothing():
    """Absence is a real case, not a formality.

    Treating it as "the rate I expected" is how a 44.1 kHz voice came out played
    at 24 kHz: half speed, and aliasing on everything above the fold.
    """
    pcm = tone(44_100, 0.05)
    got, rate = to_pcm16(pcm, 'audio/pcm')
    assert got == pcm
    assert rate is None, 'None means "not stated", and the caller supplies the declared rate'


def test_an_odd_trailing_byte_is_dropped_rather_than_guessed():
    """Half a sample is not a thing, and the alternative is a click per utterance."""
    got, _ = to_pcm16(b'\x01\x02\x03', 'audio/pcm')
    assert got == b'\x01\x02'


def test_resampling_changes_the_length_by_the_right_amount():
    out = resample(tone(24_000, 1.0), 24_000, 16_000)
    assert len(out) // 2 == 16_000

    out = resample(tone(16_000, 1.0), 16_000, 24_000)
    assert len(out) // 2 == 24_000


def test_resampling_is_the_identity_when_the_rate_matches():
    pcm = tone(24_000, 0.05)
    assert resample(pcm, 24_000, 24_000) == pcm


def test_resampling_low_passes_before_it_interpolates():
    """A tone inside the new band must survive the conversion.

    The filter is there to remove what should be removed, and a filter that
    removes too much is its own kind of hiss — so the passband is asserted too,
    not only the stopband.
    """
    rate = 44_100
    hz = 6_000
    out = resample(tone(rate, 1.0, hz=hz, amplitude=20_000), rate, 24_000)
    samples = memoryview(out).cast('h')
    peak = max(abs(s) for s in samples)
    assert peak > 12_000, f'a 6 kHz tone lost its amplitude going to 24 kHz (peak {peak})'


def test_a_tone_above_the_new_nyquist_is_not_folded_back_in():
    """The aliasing, measured.

    Decimating 44.1 kHz to 24 kHz without a low-pass folds 15 kHz down to
    9 kHz, where it sits in the middle of the voice's range and is heard as a
    whistle under the speech. This is the property — not "the filter is called"
    — that separates a converter from an interpolator.
    """
    source, target = 44_100, 24_000
    above = 15_000          # above target/2, so it must not survive
    # Where it lands without a filter: fold it into [0, target/2] by subtracting
    # whole multiples of the *target* rate.
    aliased_to = abs(above - round(above / target) * target)   # 15000 - 24000 -> 9000
    assert aliased_to == 9_000

    pcm = tone(source, 0.5, hz=above, amplitude=20_000)
    samples = memoryview(resample(pcm, source, target)).cast('h')

    # Measure only the aliased frequency, by correlating against a reference
    # sine. A naive `max(abs(s))` would pass, because the resampler always
    # contains *something*.
    window = 4_096
    reference = [math.sin(2 * math.pi * aliased_to * i / target) for i in range(window)]
    got = [float(s) for s in samples[:window]]
    amplitude = 2 * abs(sum(g * r for g, r in zip(got, reference, strict=True))) / window

    assert amplitude < 1_500, (
        f'a {above} Hz tone folded into {aliased_to} Hz at amplitude {amplitude:.0f}; '
        f'the low-pass is not doing its job'
    )


def test_resampling_does_not_overshoot():
    """The filter is applied in floating point and comes back as int16.

    Anything that rounded past full scale would wrap to the opposite sign,
    which is a very loud click in the middle of a word.
    """
    out = resample(tone(44_100, 0.2, amplitude=30_000), 44_100, 24_000)
    assert all(-32_768 <= s <= 32_767 for s in memoryview(out).cast('h'))
    # And no negative wrap-around spike: a wrapped sample is a large value of
    # the wrong sign against its neighbours.
    samples = list(memoryview(out).cast('h'))
    jumps = sum(1 for a, b in zip(samples, samples[1:], strict=False) if abs(a - b) > 20_000)
    assert jumps == 0, f'{jumps} discontinuities in the resampled signal'


def test_resampling_keeps_the_signal_within_range():
    out = resample(tone(44_100, 0.2, amplitude=30_000), 44_100, 24_000)
    assert all(-32_768 <= s <= 32_767 for s in memoryview(out).cast('h'))


# --- level -------------------------------------------------------------------


def test_a_clean_signal_is_left_alone():
    """The guard must not be able to make a good buffer worse."""
    pcm = tone(24_000, 0.05, amplitude=10_000)
    assert guard(pcm) == pcm


def test_a_dc_offset_is_removed():
    """A constant bias makes every sample one-signed, which is a square wave.

    It is harmonically rich, loud, and sounds exactly like a broken speaker —
    and it is the reason a quiet voice can sound like a loud one.
    """
    offset = 9_000
    biased = struct.pack('<400h', *[offset + (600 if i % 2 else -600) for i in range(400)])
    cleaned = memoryview(guard(biased)).cast('h')
    peak = max(abs(s) for s in cleaned)
    assert peak < 2_000, f'the offset survived: peak {peak}'
    assert guard(biased) != biased, 'a signal with a large DC bias must be changed'


def test_hot_peaks_are_pulled_under_the_ceiling_rather_than_clipped():
    """Already-clipped audio carries its distortion; this is not about undoing
    that, it is about not making it worse on the way out."""
    hot = struct.pack('<400h', *[32_000 if i % 7 == 0 else 8_000 for i in range(400)])
    cleaned = memoryview(guard(hot)).cast('h')
    assert max(cleaned) <= PEAK_CEILING
    assert max(abs(s) for s in cleaned) <= PEAK_CEILING


def test_silence_is_silence_and_not_a_normalised_signal():
    """A guard that raised the level would make one very quiet sentence very
    loud, which is worse than the problem it solves."""
    assert guard(b'\x00\x00' * 100) == b''
    assert guard(b'') == b''


def test_a_quiet_signal_is_not_turned_up():
    quiet = tone(24_000, 0.05, amplitude=200)
    assert max(abs(s) for s in memoryview(guard(quiet)).cast('h')) < 400
