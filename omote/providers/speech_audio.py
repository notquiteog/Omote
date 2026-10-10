"""Getting audio out of a speech endpoint, and not making a noise out of it.

This module exists because of one specific, reproducible failure: a TTS
endpoint answered with something that was not raw PCM, omote forwarded the
bytes as though it were, and the browser played compressed data as 16-bit
samples. The result was loud, full-scale, high-frequency hiss that no volume
setting could fix — because the signal was not a signal, and there was no
headroom to scale down. It is the "blown out and full of static even at lowest
volume" that this end is fixing, and it is a refusal here rather than a
heuristic in the player, because a refusal is visible and hiss is not.

**What is accepted.**

* `audio/pcm` and `audio/L16` — raw signed 16-bit little-endian mono. The rate
  comes from the `rate=` parameter when the endpoint supplies one, and from the
  provider's declared rate when it does not.
* `audio/wav` — a 44-or-more byte RIFF header followed by exactly that PCM.
  Unwrapped here rather than refused, because a WAV body is PCM and unwrapping
  it is thirty lines; several speech endpoints answer with it whatever they are
  asked for.
* `application/json` — the Google and Gemini envelope,
  `{"audioContent": {"data": "<base64>"}}`, which several gateways proxy
  through unchanged. Unwrapped and re-examined.
* Anything compressed — mp3, ogg, opus, flac, aac, WebM — is **refused, by
  name**. There is no honest way to play it as PCM without a decoder, and
  forwarding it produces the hiss described above. The message names the format
  and the model, because "your speech provider returned mp3" is a thing
  somebody can fix and "voice is broken" is not.

**The rate is reconciled, not assumed.** Three numbers used to be in play and
none of them agreed: the rate the pipeline asked for (the *microphone's* rate,
16 kHz, which was simply the wrong question), the rate the provider declared on
its class (24 kHz, a default nobody overrode), and the rate the endpoint
actually used. The browser baked the declared one into every buffer it built.
When those disagreed the audio played at the wrong speed, which is a pitch
shift plus aliasing — the other half of the hiss.

So `to_pcm16` returns the rate it found and the caller either resamples to its
declared rate or reports it, and never quietly guesses.

**The guard.** `_guard` is the last thing every buffer passes through. It is not
a limiter in the mastering sense and does not pretend to be one; it removes DC
offset, and it soft-clips the peaks that a model trained on mastered audio will
occasionally produce. That is what "blown out" is, and it is worth fixing at the
source because once a sample is clipped the distortion is in the signal and
lowering the volume only makes quieter distortion.
"""

from __future__ import annotations

import array
import base64
import binascii
import json
import logging
import math
import re
import sys
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

# A WAV header is 44 bytes; some writers put odd-sized chunks in front of the
# data, so the scan is generous and bounded.
WAV_SCAN = 4096

# Where a resampled signal is allowed to peak. Full scale is 32767 and it is
# reachable in ordinary speech, so the ceiling is below it.
PEAK_CEILING = 29_000

# The media types that are raw 16-bit PCM, possibly with a container.
_PCM_TYPES = ('audio/pcm', 'audio/l16', 'audio/x-pcm', 'audio/pcm16', 'audio/raw')
_WAV_TYPES = ('audio/wav', 'audio/x-wav', 'audio/wave', 'audio/vnd.wave')
# Refused, by name. The point of listing them is the message.
_COMPRESSED = {
    'audio/mpeg': 'mp3',
    'audio/mp3': 'mp3',
    'audio/ogg': 'ogg',
    'audio/opus': 'opus',
    'audio/flac': 'flac',
    'audio/aac': 'aac',
    'audio/mp4': 'aac',
    'audio/webm': 'webm',
    'audio/x-flac': 'flac',
    'audio/3gpp': 'amr',
}


class SpeechFormatError(RuntimeError):
    """A speech endpoint answered with audio omote cannot play.

    Named rather than raised as a bare `RuntimeError` because the caller shows
    the message to a person, and "openrouter tts hexgrad/kokoro-82m returned
    mp3, which is not raw PCM; ask the endpoint for `pcm`" is a thing they can
    act on.
    """


def codec(content_type: str, *, model: str = '', provider: str = '') -> str:
    """What the response actually is: `pcm`, `wav`, `json`, or a refused name.

    Deliberately not a total function over the whole of IANA: an unknown type
    is treated as PCM with a warning rather than refused, because the speech
    endpoints in use overwhelmingly send no useful `Content-Type` at all and
    refusing those would break every install that works today.
    """
    mime = (content_type or '').split(';', 1)[0].strip().lower()
    if mime in _PCM_TYPES:
        return 'pcm'
    if mime in _WAV_TYPES:
        return 'wav'
    if mime in ('application/json', 'text/json'):
        return 'json'
    if mime in _COMPRESSED:
        raise SpeechFormatError(
            f'{label(provider, model)} returned {_COMPRESSED[mime]}, which is compressed audio. '
            f'omote plays raw PCM and will not forward something it cannot decode, because '
            f'the bytes would be heard as static. Ask that endpoint for `pcm` instead.'
        )
    if mime:
        log.warning('speech: unknown content type %r; treating the body as raw PCM', mime)
    return 'pcm'


def label(provider: str, model: str) -> str:
    return ' '.join(part for part in (provider, model) if part) or 'the speech endpoint'


def declared_rate(content_type: str) -> int | None:
    """The rate an endpoint said it used, from `rate=` in its content type.

    `rate=24000` and `sample-rate=24000` are both in the wild, and so is a bare
    `sample_rate` in a JSON envelope, so all three spellings are read.
    """
    match = re.search(r'\b(?:rate|sample[-_]?rate)\s*=\s*"?(\d+)', content_type or '', re.I)
    return int(match.group(1)) if match else None


def channels_of(content_type: str) -> int:
    match = re.search(r'\bchannels\s*=\s*"?(\d+)', content_type or '', re.I)
    return int(match.group(1)) if match else 1


def wav_layout(blob: bytes) -> tuple[int, int, int] | None:
    """`(data offset, sample rate, channels)` from a RIFF/WAVE header, or None.

    Walks the chunk list rather than assuming 44 bytes, because a WAV carrying
    a `LIST` or `fact` chunk before its data is common and a fixed offset would
    play the header as audio — a click, and then a signal shifted by 44 samples.
    It also has to *finish* the walk: returning the `fmt` chunk's payload offset
    is the same bug wearing a different hat, and it is a bug this function had
    for one commit.
    """
    if len(blob) < 12 or blob[:4] not in (b'RIFF', b'RIFX') or blob[8:12] != b'WAVE':
        return None
    at = 12
    limit = min(len(blob), WAV_SCAN)
    rate = 0
    channels = 0
    saw_fmt = False
    while at + 8 <= limit:
        name = blob[at : at + 4]
        size = int.from_bytes(blob[at + 4 : at + 8], 'little')
        if name == b'fmt ' and at + 24 <= len(blob):
            channels = int.from_bytes(blob[at + 10 : at + 12], 'little') or 1
            rate = int.from_bytes(blob[at + 12 : at + 16], 'little')
            bits = int.from_bytes(blob[at + 22 : at + 24], 'little') or 16
            saw_fmt = rate and bits in (16, 8)
        elif name == b'data':
            if saw_fmt:
                return (at + 8, rate, channels)
            # A data chunk before its format description. Nothing to play it
            # with, so the caller is told rather than given a guess.
            return None
        at += 8 + size + (size & 1)
    return None


def json_audio(body: bytes) -> tuple[bytes, dict[str, Any]] | None:
    """The audio out of a JSON envelope, if there is one.

    Google and Gemini's shape is `{"audioContent": {"data": "<base64>"}}`.
    Others use `{"audio": ...}` or a bare `{"data": ...}`. All three are read,
    because which gateway is in front of the model is not something the caller
    chose.
    """
    if len(body) > 4 * 1024 * 1024:
        return None
    try:
        parsed = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    candidate = parsed.get('audioContent')
    if isinstance(candidate, dict):
        parsed = candidate
    for key in ('data', 'audio', 'audioContent', 'audio_data', 'pcm'):
        value = parsed.get(key)
        if isinstance(value, str) and value:
            try:
                return base64.b64decode(value, validate=False), parsed
            except (binascii.Error, ValueError):
                continue
    return None


def to_pcm16(blob: bytes, content_type: str, *, model: str = '', provider: str = '') -> tuple[bytes, int | None]:
    """`(pcm16, rate)` out of whatever an endpoint sent.

    `rate` is None only when the endpoint genuinely did not say, in which case
    the caller's declared rate is the best available answer and guessing is the
    correct move rather than a guess.
    """
    kind = codec(content_type, model=model, provider=provider)
    rate = declared_rate(content_type)

    if kind == 'json':
        found = json_audio(blob)
        if found is None:
            raise SpeechFormatError(
                f'{label(provider, model)} answered with JSON that has no audio in it. '
                f'The first 120 characters were: {blob[:120]!r}'
            )
        blob, parsed = found
        # The envelope usually repeats the rate in the body, and it is the only
        # place some gateways put it.
        rate = rate or declared_rate(json.dumps(parsed))
        if rate is None:
            for value in parsed.values():
                if isinstance(value, int) and 4000 <= value <= 192000:
                    rate = value
                    break
        kind = codec(str(parsed.get('mimeType') or parsed.get('contentType') or ''))
        if kind != 'pcm':
            codec(str(parsed.get('mimeType') or ''), model=model, provider=provider)

    if kind == 'wav':
        layout = wav_layout(blob)
        if layout is None:
            raise SpeechFormatError(
                f'{label(provider, model)} said it was a WAV and it is not one: {blob[:16]!r}'
            )
        offset, wav_rate, wav_channels = layout
        if not wav_rate:
            raise SpeechFormatError(
                f'{label(provider, model)} said it was a WAV and it is not one: {blob[:16]!r}'
            )
        # Only mono 16-bit is playable as-is. Stereo speech is vanishingly rare
        # and folding it to mono here is wrong enough to sound like a fault, so
        # it is refused with the reason rather than quietly mangled.
        if wav_channels > 1:
            raise SpeechFormatError(
                f'{label(provider, model)} returned {wav_channels}-channel audio. '
                f'omote plays one channel; ask the endpoint for mono.'
            )
        return blob[offset:], rate or wav_rate

    if rate is not None and channels_of(content_type) > 1:
        raise SpeechFormatError(
            f'{label(provider, model)} returned {channels_of(content_type)}-channel audio. '
            f'omote plays one channel; ask the endpoint for mono.'
        )

    # Raw PCM. An odd trailing byte is dropped rather than guessed at, because
    # half a sample is not a thing and the alternative is a click per utterance.
    return blob[: len(blob) // 2 * 2], rate


# ---------------------------------------------------------------------------
# Rate conversion
# ---------------------------------------------------------------------------


def resample(pcm: bytes, source: int, target: int) -> bytes:
    """16-bit mono PCM from one rate to another, without aliasing.

    The previous version was linear interpolation and nothing else, and it was
    the other half of the static: decimating 44.1 kHz to 24 kHz folds
    everything above 12 kHz straight back into the speech band, where it is
    heard as fizz.

    So the low-pass comes first, and it is an FFT brick wall with a raised-cosine
    edge rather than a short FIR. Both reasons matter. A short FIR is wrong at
    these ratios — 44.1 kHz to 24 kHz needs a transition band of a couple of
    kilohertz, which is sixty-odd taps, and sixty taps of Python arithmetic per
    sample is a second of CPU per second of speech. And a brick wall in the
    frequency domain needs no tap count at all: one transform, one taper, one
    transform back.

    numpy is a dependency of this project rather than something being introduced
    here — memory search has used it all along — so this costs an import that was
    already paid.

    Still not a studio resampler, and it does not need to be: this is speech on
    its way to a laptop speaker.
    """
    if source <= 0 or target <= 0 or source == target:
        return pcm[: len(pcm) // 2 * 2]

    data = np.frombuffer(pcm, dtype='<i2')
    if not data.size:
        return b''
    if sys.byteorder != 'little':
        data = data.byteswap().view('<i2')

    working = data.astype(np.float64)
    if source > target:
        working = _lowpass(working, source, target / 2)

    count = int(len(working) * target / source)
    if count <= 0:
        return b''
    positions = np.arange(count, dtype=np.float64) * (source / target)
    # Clamped at the top so the last output sample interpolates against real
    # data rather than against the zero the array pads with.
    positions = np.minimum(positions, len(working) - 1.0)
    resampled = np.interp(positions, np.arange(len(working), dtype=np.float64), working)

    out = np.clip(np.rint(resampled), -32_768, 32_767).astype('<i2')
    return out.tobytes()


def _lowpass(samples: np.ndarray, rate: float, cutoff: float) -> np.ndarray:
    """Pass everything below `cutoff`, attenuate a cosine edge above it, kill the rest.

    The cosine edge rather than a step, because a step is a brick wall and a
    brick wall rings: Gibbs oscillation in the time domain is audible as a
    pre-echo click at the start of every utterance.
    """
    size = len(samples)
    if size < 8:
        return samples
    spectrum = np.fft.rfft(samples)
    bins = np.fft.rfftfreq(size, d=1.0 / rate)

    # A tenth of the source rate is a wide, gentle edge, which is right for
    # speech and costs nothing.
    edge = cutoff * 1.2
    gain = np.ones_like(bins)
    if edge >= bins[-1]:
        gain[bins > cutoff] = 0.0
    else:
        rolling = (bins > cutoff) & (bins < edge)
        gain[rolling] = 0.5 * (1.0 + np.cos(np.pi * (bins[rolling] - cutoff) / (edge - cutoff)))
        gain[bins >= edge] = 0.0

    return np.fft.irfft(spectrum * gain, n=size)


# ---------------------------------------------------------------------------
# Level
# ---------------------------------------------------------------------------


def guard(pcm: bytes, *, ceiling: int = PEAK_CEILING) -> bytes:
    """Strip DC offset and soft-clip, which is what "blown out" is.

    Two cheap things, both of which a browser's volume control cannot undo
    because they happen before it:

    * **DC offset.** A synthesiser with a small DC bias puts every sample on one
      side of zero, so quiet speech arrives as a square wave — which is
      harmonically rich, loud, and sounds exactly like a broken speaker. Removing
      the mean is the difference between "harsh" and "fine".
    * **Peaks.** A sample that reached full scale has been clipped by whoever
      clipped it, and the distortion is now in the signal. Squashing what is
      left into a little headroom is not fixing that, but it stops it being
      worse.

    The whole buffer is scanned rather than a running maximum, because the
    pipeline sends one sentence at a time and a sentence is small.
    """
    samples = array.array('h')
    samples.frombytes(pcm[: len(pcm) // 2 * 2])
    if not samples:
        return b''
    if sys.byteorder != 'little':
        samples.byteswap()

    total = 0
    peak = 0
    for value in samples:
        total += value
        magnitude = value if value >= 0 else -value
        if magnitude > peak:
            peak = magnitude
    if peak == 0:
        return b''

    offset = total // len(samples)
    # Only bother when there is something to bother about: a clean signal with
    # no DC and no hot peak is returned untouched, so this costs nothing on the
    # common case and cannot make a good buffer worse.
    hot = peak > ceiling
    dc = abs(offset) > 32
    if not hot and not dc:
        return pcm[: len(pcm) // 2 * 2]

    for index, value in enumerate(samples):
        value -= offset
        if value > ceiling or value < -ceiling:
            # tanh-shaped, cheap: linear to 60% of the ceiling and asymptotic
            # after it. A hard clip at the ceiling is what produced the problem.
            span = ceiling * 0.6
            value = int(span * math.tanh(value / span))
        samples[index] = 32767 if value > 32767 else (-32768 if value < -32768 else value)

    if sys.byteorder != 'little':
        samples.byteswap()
    return samples.tobytes()


__all__ = [
    'PEAK_CEILING',
    'SpeechFormatError',
    'channels_of',
    'codec',
    'declared_rate',
    'guard',
    'json_audio',
    'resample',
    'to_pcm16',
    'wav_layout',
]
