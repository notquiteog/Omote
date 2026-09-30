"""The duplex conversation loop.

Both directions are live at once, so the loop is not a sequence of steps but
a small state machine with two things happening at the same time: audio
arriving from the person, and audio being generated for them. Everything
interesting comes from the overlap.

**Barge-in.** While synthesis is playing, the microphone is still being fed
to the detector. When it opens, the current utterance is cancelled: the
synthesis task is killed, any queued audio is dropped, and the client is told
to discard what it has buffered. Without that last part the assistant keeps
talking for as long as the client had audio queued, which is exactly the
behaviour that makes voice assistants feel deaf.

**Speaking before the answer is finished.** Text is split into sentences as
it streams and each is synthesised as it completes, so the reply starts
around the first sentence rather than after the last. On a local model this
is most of the perceived latency.

**What was already said is what it remembers.** When an utterance is cut off
partway, the history records the part that was actually spoken aloud, not the
full text the model generated. Otherwise the assistant believes it told you
something you never heard, and the rest of the conversation is built on that.
"""

from __future__ import annotations

import asyncio
import logging
import re
import struct
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from openmirror.protocol.agent import Risk
from openmirror.protocol.voice import (
    AssistantTextDelta,
    SpeechStarted,
    SpeechStopped,
    SynthesisCancelled,
    SynthesisStarted,
    SynthesisStopped,
    TranscriptFinal,
    VoiceAnswered,
    VoiceError,
    VoiceWaiting,
)
from openmirror.providers.base import ChatRequest, Message, StreamText, TextBlock
from openmirror.voice.answers import Answer, how_to_answer, interpret
from openmirror.voice.vad import Ringbuffer, Vad, VadConfig, VadEvent

log = logging.getLogger(__name__)

# Sends one JSON event to the client.
EmitEvent = Callable[[Any], Awaitable[None]]
# Sends one chunk of PCM audio, tagged with the utterance it belongs to.
EmitAudio = Callable[[str, bytes], Awaitable[None]]

# A final transcript this short is almost always the detector opening on a
# cough or a chair. Reported to the client, never sent to the model.
MIN_UTTERANCE_CHARS = 2
FILLERS = {'uh', 'um', 'mm', 'hm', 'hmm', 'ah', 'er', 'eh', 'mhm', 'uh huh', 'you'}

_SENTENCE_END = re.compile(r'(?<=[.!?…])\s+|\n{2,}')

# A trailing token whose full stop does not end a sentence, split into the two
# kinds because they need different treatment.
#
# **Titles and connectives are never a sentence end**, whatever follows. A name
# after `Dr.` is capitalised, so any test based on what comes next gets it
# wrong — that is how "See Dr. Smith about it." came to be two utterances and
# the first thing heard was "See Dr." followed by a pause.
_NEVER_SPLIT = re.compile(r'\b(?:Mr|Mrs|Ms|Dr|Prof|St|No|Fig|dept|est|al|vs|etc)\.$', re.I)

# **Latin and clock abbreviations depend on what follows.** "e.g. the reason"
# is one thought; "e.g. It was Tuesday" is two. The rule is simply whether the
# next word starts a new one, which for English means whether it is capitalised
# — and where that is wrong (a proper noun after "e.g.") the cost is one extra
# clause in one utterance rather than a wrong answer.
_MAYBE_SPLIT = re.compile(r'\b(?:e\.g|i\.e|c\.f|a\.k|a\.m|p\.m)\.$', re.I)

# Initials, which are a single capital letter and a stop and nothing else.
# Two of them at the end of the candidate, so "He said J." — a quoted letter,
# one initial — is not mistaken for one, and an initial that starts a run is
# caught by looking at what follows rather than what precedes.
_INITIAL_RUN = re.compile(r'(?:\b[A-Z]\.[\s ]*){2,}$')
_NEXT_IS_INITIAL = re.compile(r'^\s*[A-Z]\.')

# The tail is spoken when the model goes quiet rather than waiting for the end
# of its turn.
#
# Everything before this was held until a sentence terminator *and* a
# whitespace character after it arrived, which meant: a reply written as a list,
# or one that ends on a dash, or one whose only full stop is inside an
# abbreviation, was not spoken at all until the stream closed. On a slow model
# that is several seconds of the person listening to nothing — and the tail is
# also where the answer to a question lives, so it is the part that most needs
# to be said early.
#
# So the unfinished text is spoken once the model has been quiet this long,
# provided there is enough of it to be a clause rather than a word. Both numbers
# are deliberately conservative: flushing a fragment makes the reply stutter,
# and a long wait puts the old latency back.
FLUSH_QUIET_MS = 550
FLUSH_MIN_CHARS = 28
# How often the tail is looked at. Well under `FLUSH_QUIET_MS` so the boundary
# is not quantised into something noticeably longer than it says.
FLUSH_POLL_S = 0.05


def pcm_to_wav(pcm: bytes, sample_rate: int = 16_000, channels: int = 1) -> bytes:
    """Wrap raw PCM16 in a WAV container.

    Every transcription endpoint in use wants a container rather than raw
    samples, and a 44-byte header is cheaper than an encoder.
    """
    return (
        b'RIFF'
        + struct.pack('<I', 36 + len(pcm))
        + b'WAVEfmt '
        + struct.pack('<IHHIIHH', 16, 1, channels, sample_rate, sample_rate * channels * 2, channels * 2, 16)
        + b'data'
        + struct.pack('<I', len(pcm))
        + pcm
    )


def split_sentences(buffer: str) -> tuple[list[str], str]:
    """Take complete sentences off the front, leaving the rest.

    Returns (complete, remainder). The remainder is whatever has not yet been
    terminated, which is held until more text arrives, the model goes quiet
    (`_Tail`), or the stream ends.

    A run of abbreviations is skipped rather than one at a time. That matters
    more than it sounds: "J. R. R. Tolkien wrote it." has two boundaries that
    are not sentence ends before the one that is, and a loop that only looked
    one boundary ahead took the first as the end and said "J. R." out loud.
    """
    out: list[str] = []
    rest = buffer
    start = 0
    at = 0

    while True:
        match = _SENTENCE_END.search(rest, at)
        if not match:
            break
        end = match.start() + 1
        after = match.end()
        if _is_abbreviation(rest[start:end], rest[after:]):
            at = after
            continue
        candidate = rest[start:end]
        if candidate.strip():
            out.append(candidate.strip())
        start = at = after

    return out, rest[start:]


def _is_abbreviation(candidate: str, after: str) -> bool:
    """Whether the full stop ending `candidate` is not a sentence end.

    Two questions, because the tokens that end in a full stop fall into two
    groups with nothing in common but the punctuation.

    The one this used to get wrong in the expensive direction is a bare capital
    letter. With `re.I` it matched *any* single capital letter before a stop, so
    the pronoun in "I think. Yes." was treated as an initial and the splitter
    looked past it — one utterance instead of two, and the first audio for it
    arriving later rather than sooner. A lone capital letter is now a sentence
    end unless it is visibly part of an initials run, in either direction: two
    initials behind it, or another initial in front.
    """
    tail = candidate.rstrip()
    if _NEVER_SPLIT.search(tail):
        return True
    if _INITIAL_RUN.search(tail):
        return True
    if _NEXT_IS_INITIAL.match(after):
        return True
    if _MAYBE_SPLIT.search(tail):
        return _continues(after)
    return False


def _continues(after: str) -> bool:
    """Whether the text after a candidate sentence end continues the same one.

    "e.g. the reason" continues. "e.g. It was" does not. A capitalised word is
    read as starting something new, which is wrong after "e.g." in front of a
    proper noun and costs one clause in one utterance rather than a wrong
    answer.
    """
    head = after.lstrip()
    if not head:
        return False
    first = head[0]
    if not first.isalpha():
        return False
    return not first.isupper()


def is_meaningful(text: str) -> bool:
    """Whether a transcript is worth sending to the model."""
    cleaned = re.sub(r'[^\w\s]', '', text).strip().lower()
    if len(cleaned) < MIN_UTTERANCE_CHARS:
        return False
    words = cleaned.split()
    # All filler: whisper reliably hallucinates 'you' and 'thank you' on
    # silence, and answering those makes the assistant talk to itself.
    return bool(words) and not all(w in FILLERS for w in words)


@dataclass(slots=True)
class Waiting:
    """What the agent is suspended on, while it is.

    `strict` is set for anything that spends money or types a secret: the
    affirmative then has to be the word "confirm", because a bare "yes" is one
    transcription error away from a purchase and "yes" is a word people say
    while thinking.
    """

    kind: str                       # 'question' | 'approval'
    id: str
    strict: bool = False
    summary: str = ''
    options: list[str] = field(default_factory=list)


def _spoken_question(question: str, options: list[str]) -> str:
    """A question, as something to hear rather than read.

    The options are read out because on a screen they are buttons and in a
    voice call they are the only way to know what the accepted answers are.
    Capped, since a list of nine read aloud is worse than none.
    """
    said = question.strip()
    if options and len(options) <= 4:
        said += ' You can say: ' + ', or '.join(o.strip() for o in options) + '.'
    elif options:
        said += f' There are {len(options)} options on screen, or just tell me what you want.'
    return said


def _spoken_approval(summary: str, strict: bool) -> str:
    """The sentence that stops a voice call going quiet on an approval."""
    what = summary.strip() or 'do something that needs your permission'
    lead = (
        'This one spends money or enters a secret' if strict
        else 'This one needs your say-so'
    )
    return f'{lead}: {what}. {how_to_answer(strict)}'


@dataclass
class VoiceConfig:
    sample_rate: int = 16_000
    frame_ms: int = 20
    stt_model: str = 'whisper-1'
    tts_model: str = 'tts-1'
    tts_voice: str = 'alloy'
    llm_model: str = ''
    language: str | None = None
    # Passed through to the chat provider. The default turns reasoning off;
    # override it for a model where thinking is cheap or wanted.
    llm_extra: dict = field(default_factory=lambda: {})
    system_prompt: str = (
        'You are in a spoken conversation. Reply in one or two sentences unless asked for more. '
        'Write what should be said aloud: no markdown, no lists, no code blocks, no emoji. '
        'Expand numbers, symbols and abbreviations into words. If you did not understand, say so '
        'briefly and ask.'
    )
    vad: VadConfig = field(default_factory=VadConfig)


class _Speech:
    """Speaks sentences in order, on its own task, while the model keeps going.

    This is the fix for the largest thing wrong with talk mode's latency.

    Synthesis used to be awaited *inside* the loop reading the model's stream:

        async for event in self._generate():
            sentences, buffer = split_sentences(buffer)
            for sentence in sentences:
                await self._speak(utterance_id, sentence)

    `_speak` drains the whole TTS response for that sentence before returning,
    so the two were strictly serialised. Nothing was pipelined: sentence N+1's
    request was not even sent until sentence N's audio had finished arriving,
    and while all that happened the model's SSE stream sat unread in the socket
    with the generator not being polled. Time to the first word was
    *model TTFT + a whole sentence of generation + a whole TTS round trip*, and
    a five-sentence reply took the sum of five round trips rather than the max
    of one and the rest.

    A queue fixes it without changing what is heard: sentences go in in the
    order the model produced them, come out in that order, and the synthesis of
    one overlaps the generation of the next. The model gets to run ahead, which
    is the entire point — on a slow model it is usually several sentences ahead
    by the time the first one is finished being spoken.

    `finish()` waits for the backlog, because the end of a reply still has to
    mean the audio is finished. That is the one wait that stays.
    """

    __slots__ = ('_utterance', '_session', '_queue', '_task', '_idle')

    def __init__(self, session: VoiceSession, utterance_id: str) -> None:
        self._session = session
        self._utterance = utterance_id
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._idle: _Tail | None = None
        self._task = asyncio.create_task(self._run())

    def start_tail(self) -> None:
        """Watch the unfinished text and speak it when the model goes quiet."""
        self._idle = _Tail(self)

    @property
    def tail(self) -> str:
        return self._idle.text if self._idle is not None else ''

    @tail.setter
    def tail(self, value: str) -> None:
        if self._idle is not None:
            self._idle.text = value

    def put(self, sentence: str) -> None:
        """Hand over a sentence. Never blocks and never awaits."""
        self._queue.put_nowait(sentence)

    async def _run(self) -> None:
        while True:
            sentence = await self._queue.get()
            if sentence is None:
                return
            try:
                await self._session._speak(self._utterance, sentence)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                # `_speak` reports its own failures; this is the belt to that
                # braces, because a worker that dies takes every later sentence
                # with it and the reply stops half way with no reason given.
                log.exception('speech worker failed')
            finally:
                self._queue.task_done()

    async def finish(self) -> None:
        """Wait for every queued sentence to have been synthesised."""
        await self._queue.join()
        self._queue.put_nowait(None)
        await self._task
        if self._idle is not None:
            await self._idle.stop()

    async def stop(self) -> None:
        """Abandon the backlog. Used when the turn is cancelled."""
        if self._idle is not None:
            await self._idle.stop()
        self._queue.put_nowait(None)
        self._task.cancel()


class _Tail:
    """The text the model has not finished, and the task that gives up on it.

    Holding an unterminated tail until the stream closes is the difference
    between hearing a list as it is read out and hearing it all at the end. The
    text is spoken once it has stopped changing for `FLUSH_QUIET_MS` and is
    long enough to be a clause.

    The read-and-clear below happens with no `await` in it, and so does the
    write in the reply loop, so the two cannot interleave even though they are
    on the same object.
    """

    __slots__ = ('_speech', 'text', '_last', '_quiet_for', '_task', '_done')

    def __init__(self, speech: _Speech) -> None:
        self._speech = speech
        self.text = ''
        self._last = ''
        self._quiet_for = 0.0
        self._done = False
        self._task = asyncio.create_task(self._watch())

    async def _watch(self) -> None:
        try:
            while not self._done:
                await asyncio.sleep(FLUSH_POLL_S)
                if self.text == self._last:
                    self._quiet_for += FLUSH_POLL_S
                else:
                    self._last = self.text
                    self._quiet_for = 0.0
                    continue
                if self._quiet_for * 1000 < FLUSH_QUIET_MS:
                    continue
                if len(self.text.strip()) < FLUSH_MIN_CHARS:
                    continue
                self._quiet_for = 0.0
                self._last = ''
                self._speech.put(self.text.strip())
                self.text = ''
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception('the tail watcher failed')

    async def stop(self) -> None:
        self._done = True
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


class VoiceSession:
    def __init__(
        self,
        *,
        stt: Any,
        tts: Any,
        llm: Any,
        config: VoiceConfig,
        emit_event: EmitEvent,
        emit_audio: EmitAudio,
        agent: Any = None,
    ) -> None:
        self.stt = stt
        self.tts = tts
        self.llm = llm
        self.config = config
        self.emit_event = emit_event
        self.emit_audio = emit_audio
        # When set, transcripts drive an agent session instead of a bare chat
        # model, so a voice call can act on the machine.
        self.agent = agent

        self.vad = Vad(config.vad)
        self.preroll = Ringbuffer(config.sample_rate, ms=400)
        self.history: list[Message] = []

        self._utterance = bytearray()
        self._capturing = False
        self._muted = False
        self._partial = bytearray()

        self._reply: asyncio.Task[None] | None = None
        self._utterance_id: str | None = None
        # Captured utterances waiting to be transcribed, and the one task that
        # transcribes them.
        #
        # Transcription used to be awaited straight from `_frame`, which the
        # websocket read loop calls. So for the whole duration of a
        # `POST /audio/transcriptions` — 300 ms to a second and a half through
        # OpenRouter — the server read *no* microphone frames, ran *no*
        # voice-activity detection, and handled *no* `voice.interrupt`,
        # `voice.mute` or `voice.stop`.
        #
        # The compounding is the part that made it feel far worse than a
        # one-off: the detector does not see the silence you produce after your
        # sentence while it is busy, so its 500 ms hangover does not even start
        # counting until the buffered frames are drained. Every turn made the
        # next one later.
        #
        # So the read loop hands the audio over and goes straight back to
        # listening. The queue keeps transcripts in the order they were spoken,
        # which is the order the history needs.
        self._audio: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._transcriber: asyncio.Task[None] | None = None
        # What the agent is suspended on, when it is: a question it asked, or
        # a tool waiting to be approved. While this is set the next thing the
        # person says is an *answer* rather than a new instruction — otherwise
        # they reply to a question and it becomes a fresh turn while the agent
        # sits waiting for something nobody is going to send.
        self._awaiting: Waiting | None = None
        # What has actually been played to the person for the current
        # utterance, so an interruption can be recorded truthfully.
        self._spoken: list[str] = []

    @property
    def frame_bytes(self) -> int:
        return self.vad.frame_bytes

    @property
    def is_speaking(self) -> bool:
        return self._reply is not None and not self._reply.done()

    # -- audio in -----------------------------------------------------------

    async def feed(self, audio: bytes) -> None:
        """Take arbitrary-sized audio from the client and process whole frames.

        Browsers deliver whatever their audio worklet produces, which is not
        the detector's frame size, so a remainder is carried between calls.
        """
        if self._muted:
            return

        self._partial.extend(audio)
        size = self.frame_bytes

        while len(self._partial) >= size:
            frame = bytes(self._partial[:size])
            del self._partial[:size]
            await self._frame(frame)

    async def _frame(self, frame: bytes) -> None:
        if self._capturing:
            self._utterance.extend(frame)
        else:
            self.preroll.push(frame)

        event = self.vad.push(frame)

        if event is VadEvent.SPEECH_START:
            await self.emit_event(SpeechStarted())

            # The decisive moment: they started talking while it was talking.
            if self.is_speaking:
                if self._awaiting is not None:
                    # Answering a question it is still reading out. Stop the
                    # audio, keep the turn: cancelling here would throw away
                    # the very work the answer is meant to unblock.
                    await self._silence('barge_in')
                else:
                    await self._cancel_reply('barge_in')

            self._capturing = True
            # Pre-roll first, so the utterance starts before the detector did.
            self._utterance = bytearray(self.preroll.take())
            self._utterance.extend(frame)

        elif event is VadEvent.SPEECH_END and self._capturing:
            self._capturing = False
            captured = bytes(self._utterance)
            self._utterance = bytearray()
            self.preroll.clear()
            ms = int(len(captured) / 2 / self.config.sample_rate * 1000)
            await self.emit_event(SpeechStopped(duration_ms=ms))
            # Handed over rather than awaited — see `_audio` above. The read
            # loop's next job is to keep listening.
            self._transcribe(captured)

        elif self._capturing and not self.vad.speaking:
            # The detector closed the utterance without reporting one: too
            # short to have been speech. Nothing to transcribe, but the
            # capture still has to stop — otherwise every later frame is
            # appended to an utterance that will never be sent, and none of
            # them reach the pre-roll, so the next real sentence arrives with
            # its first word already gone.
            self._capturing = False
            self._utterance = bytearray()

    def _transcribe(self, pcm: bytes) -> None:
        if not pcm:
            return
        if self._transcriber is None or self._transcriber.done():
            self._transcriber = asyncio.create_task(self._transcribe_all())
        self._audio.put_nowait(pcm)

    async def _transcribe_all(self) -> None:
        while True:
            pcm = await self._audio.get()
            if pcm is None:
                return
            try:
                await self._handle_utterance(pcm)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                # `_handle_utterance` reports its own failures to the client. A
                # worker that died on one would take every later utterance with
                # it and the call would simply stop hearing anything.
                log.exception('transcription worker failed')
            finally:
                self._audio.task_done()

    async def settle(self, *, limit: float = 10.0) -> bool:
        """Wait until everything captured has been transcribed and spoken.

        Part of the session's interface because moving transcription off the
        read loop made it necessary rather than merely convenient: a caller
        that has finished feeding audio has not finished being answered, and
        code that assumed otherwise — the router shutting down, a test
        asserting on a transcript — would look at a half-finished conversation.

        The reply is awaited under `shield` so that a caller giving up on this
        does not take the reply down with it. Returns whether it settled; a
        False is a slow provider, not a failure.
        """
        async def everything() -> None:
            if self._transcriber is not None:
                await self._audio.join()
            # The transcription just waited for may itself have started one.
            reply = self._reply
            if reply is not None:
                await asyncio.shield(reply)

        try:
            async with asyncio.timeout(limit):
                await everything()
        except TimeoutError:
            return False
        return True

    async def mute(self, muted: bool) -> None:
        self._muted = muted
        if muted:
            # Drop a half-formed utterance rather than resuming it later with
            # a hole in the middle.
            self._capturing = False
            self._utterance = bytearray()
            self._partial = bytearray()
            self.vad.reset()
            self.preroll.clear()

    # -- transcription and reply -------------------------------------------

    async def _handle_utterance(self, pcm: bytes) -> None:
        if not pcm:
            return
        try:
            transcript = await self.stt.transcribe(
                pcm_to_wav(pcm, self.config.sample_rate),
                model=self.config.stt_model,
                language=self.config.language,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception('transcription failed')
            await self.emit_event(VoiceError(message=f'transcription failed: {exc}'))
            return

        text = (transcript.text or '').strip()
        if not is_meaningful(text):
            # Reported anyway: a client showing 'heard nothing' is far less
            # confusing than one that shows nothing at all.
            await self.emit_event(TranscriptFinal(text=text, submitted=False))
            return

        if self._awaiting is not None:
            await self.emit_event(TranscriptFinal(text=text, submitted=True))
            await self._answer(text)
            return

        await self.emit_event(TranscriptFinal(text=text, submitted=True))
        await self.say(text)

    async def _answer(self, said: str) -> None:
        """Give a spoken reply to whatever the agent is suspended on.

        Deliberately does not touch the reply task. The generator driving the
        agent is still running and still consuming its events — cancelling it
        here would abandon a turn that is one answer away from finishing.
        """
        waiting = self._awaiting
        if waiting is None or self.agent is None:
            return

        if waiting.kind == 'question':
            self._awaiting = None
            await self.emit_event(
                VoiceAnswered(kind='question', heard=said, understood='answer')
            )
            self.agent.answer(waiting.id, said)
            return

        verdict = interpret(said, strict=waiting.strict)
        await self.emit_event(
            VoiceAnswered(kind='approval', heard=said, understood=verdict.value)
        )

        if verdict is Answer.UNCLEAR:
            # Never guessed. "Go" and "no" are one phoneme apart, and the
            # likelier reading is not a good enough reason to spend money.
            # The question stays open and is asked again.
            await self.say_aloud(
                f'Sorry, I did not catch that. {how_to_answer(waiting.strict)}'
            )
            return

        self._awaiting = None
        if verdict is Answer.YES:
            self.agent.approve(waiting.id, remember=False)
        else:
            self.agent.deny(waiting.id, 'declined out loud')

    async def say_aloud(self, text: str) -> None:
        """Speak one sentence without starting a turn.

        For the things this end says on its own behalf — "I did not catch
        that" — which must not become a message to the model or cancel a turn
        that is suspended waiting for an answer.
        """
        utterance_id = uuid.uuid4().hex[:12]
        await self.emit_event(SynthesisStarted(utterance_id=utterance_id))
        previous, self._utterance_id = self._utterance_id, utterance_id
        try:
            await self._speak(utterance_id, text)
        finally:
            self._utterance_id = previous
        await self.emit_event(SynthesisStopped(utterance_id=utterance_id))

    async def say(self, text: str) -> None:
        """Take a turn from text, as if it had been spoken."""
        if self.is_speaking:
            await self._cancel_reply('client')
        self.history.append(Message(role='user', content=[TextBlock(text=text)]))
        self._reply = asyncio.create_task(self._reply_turn())

    async def _reply_turn(self) -> None:
        utterance_id = uuid.uuid4().hex[:12]
        self._utterance_id = utterance_id
        self._spoken = []
        buffer = ''
        full: list[str] = []
        speech = _Speech(self, utterance_id)
        # Starting the tail watcher before the first event: the model can emit a
        # long first clause and then think for a while, and that is precisely
        # the case where the quiet-flush is worth having.
        speech.start_tail()

        await self.emit_event(SynthesisStarted(utterance_id=utterance_id))

        try:
            async for event in self._generate():
                if not isinstance(event, StreamText):
                    continue
                await self.emit_event(AssistantTextDelta(text=event.text))
                buffer += event.text
                full.append(event.text)

                sentences, buffer = split_sentences(buffer)
                for sentence in sentences:
                    # Handed over, not awaited. See `_Speech`.
                    speech.put(sentence)
                speech.tail = buffer

                # A suspended turn has no end for the tail to be flushed at:
                # the generator is still open, waiting on a person. Without
                # this the last sentence stays in the buffer — and the last
                # sentence is the one saying how to answer, so they would hear
                # "this one spends money: place the order" and then silence.
                if self._awaiting is not None and buffer.strip():
                    speech.put(buffer.strip())
                    buffer = ''
                    speech.tail = ''

            # Whatever is left when the model stops, terminated or not.
            if buffer.strip():
                speech.put(buffer.strip())
            speech.tail = ''

            # The one wait that stays: the reply is not over until it has been
            # heard. Everything before it is now overlapped rather than serial.
            await speech.finish()

            self.history.append(Message(role='assistant', content=[TextBlock(text=''.join(full))]))
            await self.emit_event(SynthesisStopped(utterance_id=utterance_id))

        except asyncio.CancelledError:
            await speech.stop()
            # Record only what was actually heard. The rest never happened as
            # far as the person is concerned, and the model must not act later
            # as though it had been said.
            spoken = ' '.join(self._spoken).strip()
            self.history.append(
                Message(
                    role='assistant',
                    content=[TextBlock(text=(spoken + ' —' if spoken else '—') + ' [interrupted]')],
                )
            )
            raise
        except Exception as exc:  # noqa: BLE001
            await speech.stop()
            log.exception('reply failed')
            await self.emit_event(VoiceError(message=str(exc)))
            await self.emit_event(SynthesisStopped(utterance_id=utterance_id))

    async def _generate(self):
        """Stream the reply, from the agent if one is attached or the model if not."""
        if self.agent is not None:
            async for event in self._generate_via_agent():
                yield event
            return

        request = ChatRequest(
            model=self.config.llm_model,
            messages=self.history,
            system=self.config.system_prompt,
            # No ceiling. A spoken reply that stops mid-sentence is worse
            # here than anywhere else in openmirror: there is no scrollback and no
            # stop reason to notice, just a voice that trails off. Length is
            # the prompt's job, and the prompt already asks for short answers.

            # Reasoning is silence. A model that thinks for eight seconds
            # before its first word of content cannot be rescued by
            # synthesising a sentence at a time, because there is no sentence
            # yet — the person just hears nothing and assumes it broke.
            # Measured on gemma4:12b: 1534 characters of thinking before 276
            # of answer. Servers that do not know the option ignore it.
            extra={'think': False, **self.config.llm_extra},
        )
        async for event in self.llm.stream(request):
            yield event

    async def _generate_via_agent(self):
        """Drive an agent session and speak its prose — and its silences.

        Reasoning and tool output are still not spoken: reading a build log
        aloud is unbearable. What *is* spoken now is the two things that
        suspend a turn, because this used to be the mode's worst failure. The
        agent would ask a question or stop for an approval, only `TextDelta`
        was being spoken, and the call simply went quiet — leaving somebody
        who is not looking at a screen listening to nothing, with no way to
        know the machine was waiting on them.

        The generator does not stop when the turn suspends. It keeps consuming
        events, so the agent stays alive on its future and the rest of the
        turn arrives once an answer does.
        """
        from openmirror.protocol.agent import (
            QuestionAsked,
            TextDelta,
            ToolDenied,
            ToolProposed,
            TurnCompleted,
        )

        last = self.history[-1]
        text = ' '.join(b.text for b in last.content if isinstance(b, TextBlock))
        self.agent.submit(text)

        async for event in self.agent.events():
            if isinstance(event, TextDelta):
                yield StreamText(text=event.text)

            elif isinstance(event, QuestionAsked):
                self._awaiting = Waiting(
                    kind='question', id=event.question_id, options=list(event.options)
                )
                await self.emit_event(
                    VoiceWaiting(kind='question', prompt=event.question, options=event.options)
                )
                yield StreamText(text=' ' + _spoken_question(event.question, event.options))

            elif isinstance(event, ToolProposed) and event.needs_approval:
                strict = event.call.risk in (Risk.PURCHASE, Risk.CREDENTIAL)
                self._awaiting = Waiting(
                    kind='approval', id=event.call.id, strict=strict, summary=event.call.summary
                )
                await self.emit_event(
                    VoiceWaiting(
                        kind='approval', prompt=event.call.summary, strict=strict,
                        risk=event.call.risk.value,
                    )
                )
                yield StreamText(text=' ' + _spoken_approval(event.call.summary, strict))

            elif isinstance(event, ToolDenied):
                # Said aloud so a "no" is acknowledged. Without it the only
                # evidence the refusal landed is the absence of the thing.
                if self._awaiting is None:
                    yield StreamText(text=" Right, I won't.")

            elif isinstance(event, TurnCompleted):
                break

    async def _speak(self, utterance_id: str, sentence: str) -> None:
        """Synthesise one sentence and stream it to the client."""
        try:
            async for chunk in self.tts.synthesize(
                sentence,
                model=self.config.tts_model,
                voice=self.config.tts_voice,
                fmt='pcm16',
                sample_rate=self.config.sample_rate,
            ):
                # Checked per chunk: cancellation can land between the model
                # producing a sentence and the client hearing it, and sending
                # audio for a cancelled utterance is what a barge-in is meant
                # to prevent.
                if self._utterance_id != utterance_id:
                    return
                await self.emit_audio(utterance_id, chunk)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception('synthesis failed')
            await self.emit_event(VoiceError(message=f'speech synthesis failed: {exc}'))
            return

        self._spoken.append(sentence)

    # -- interruption -------------------------------------------------------

    async def _silence(self, reason: str) -> None:
        """Stop what is being said without stopping what is being done.

        Bumping the utterance id is what makes an in-flight synthesis drop its
        remaining chunks — see `_speak`, which checks it per chunk — so this
        is the whole of "be quiet" separated from "give up".
        """
        utterance_id = self._utterance_id
        self._utterance_id = None
        if utterance_id:
            await self.emit_event(SynthesisCancelled(utterance_id=utterance_id, reason=reason))

    async def _cancel_reply(self, reason: str) -> None:
        task, utterance_id = self._reply, self._utterance_id
        # Cleared before awaiting, so any synthesis still in flight sees the
        # id has moved on and stops sending.
        self._reply = None
        self._utterance_id = None

        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

        if utterance_id:
            await self.emit_event(SynthesisCancelled(utterance_id=utterance_id, reason=reason))

        # A turn that has been thrown away is not waiting for an answer, and
        # leaving this set would route the person's next sentence into a
        # question that no longer exists.
        self._awaiting = None

    async def interrupt(self) -> None:
        if self.is_speaking:
            await self._cancel_reply('client')

    async def close(self) -> None:
        await self._cancel_reply('client')
        self._audio.put_nowait(None)
        if self._transcriber is not None and not self._transcriber.done():
            self._transcriber.cancel()
            try:
                await self._transcriber
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if self.agent is not None:
            await self.agent.close()
