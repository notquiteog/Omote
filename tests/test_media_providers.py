"""The parts of image and video generation that are worth pinning.

Nothing here talks to a provider. What it tests is the three things that go
wrong without a network: the translation from a model's published schema into
a form, the one polling loop that every hosted service shares, and the rules
about what a generation is allowed to be told.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import subprocess
import tempfile
from pathlib import Path

import pytest

import omote.media.edit as edit_ops
from omote.media.params import coerce
from omote.providers.hosted_media import HostedMedia, Poll, Submitted, sniff, urls_in
from omote.providers.openapi_form import params_from_openapi
from omote.providers.registry import NoProviderError, ProviderRegistry

# ---------------------------------------------------------------------------
# A model's schema becomes a form
# ---------------------------------------------------------------------------

SCHEMA = {
    'components': {
        'schemas': {
            'aspect_ratio': {'type': 'string', 'enum': ['1:1', '16:9', '9:16'], 'title': 'aspect_ratio'},
            'Input': {
                'required': ['prompt'],
                'properties': {
                    'prompt': {'type': 'string', 'x-order': 0, 'title': 'Prompt',
                               'description': 'What to make.'},
                    'aspect_ratio': {'allOf': [{'$ref': '#/components/schemas/aspect_ratio'}],
                                     'default': '16:9', 'x-order': 2,
                                     'description': 'The shape of it.'},
                    'image': {'type': 'string', 'format': 'uri', 'x-order': 1,
                              'description': 'A picture to work from.'},
                    'steps': {'type': 'integer', 'minimum': 1, 'maximum': 50, 'default': 28,
                              'x-order': 3},
                    'guidance': {'type': 'number', 'minimum': 0, 'maximum': 1, 'default': 0.5,
                                 'x-order': 4},
                    'go_fast': {'type': 'boolean', 'default': True, 'x-order': 5},
                    'seed': {'type': 'integer', 'x-order': 6},
                    'lora_weights': {'type': 'object', 'x-order': 7},
                },
            },
        }
    }
}


def _by_name(params):
    return {p.name: p for p in params}


def test_an_enum_behind_a_reference_is_still_an_enum():
    """These schemas spell an enum as `allOf: [$ref]`. A reader that only looks
    at `type` sees a string and renders a text box — and a text box for an
    aspect ratio is a field where 16:9 and 16x9 look equally plausible."""
    param = _by_name(params_from_openapi(SCHEMA))['aspect_ratio']
    assert param.kind == 'enum'
    assert param.options == ['1:1', '16:9', '9:16']
    assert param.default == '16:9'
    # The field's own description wins over the referenced component's.
    assert param.help == 'The shape of it.'


def test_a_file_input_becomes_a_picture_control():
    """`format: uri` is how an image-to-image model asks for an image. As a
    text box it looks broken rather than unsupplied."""
    assert _by_name(params_from_openapi(SCHEMA))['image'].kind == 'image'


def test_a_type_nobody_understands_is_left_out_rather_than_guessed():
    """A control that sends the wrong shape is a 422 the person cannot connect
    to anything they touched. A missing one is merely missing."""
    assert 'lora_weights' not in _by_name(params_from_openapi(SCHEMA))


def test_a_required_field_is_never_folded_away():
    """`advanced` hides a control behind a disclosure, and a form whose one
    mandatory field is hidden fails on submit for no visible reason."""
    assert _by_name(params_from_openapi(SCHEMA))['prompt'].advanced is False


def test_the_authors_own_ordering_is_kept():
    names = [p.name for p in params_from_openapi(SCHEMA)]
    assert names[:4] == ['prompt', 'image', 'aspect_ratio', 'steps']


def test_a_seed_is_a_seed_and_not_just_an_integer():
    assert _by_name(params_from_openapi(SCHEMA))['seed'].kind == 'seed'


def test_a_narrow_float_gets_a_finer_step_than_a_wide_one():
    """A slider from 0 to 1 in tenths has eleven positions, which is not a
    guidance control."""
    params = _by_name(params_from_openapi(SCHEMA))
    assert params['guidance'].step == 0.01
    assert params['steps'].step == 1


def test_fal_names_its_input_schema_after_the_endpoint():
    """Replicate calls it `Input`; fal references it from the POST body under
    whatever name it likes. Both have to be found."""
    doc = {
        'paths': {'/': {'post': {'requestBody': {'content': {'application/json': {
            'schema': {'$ref': '#/components/schemas/FluxDevInput'}}}}}}},
        'components': {'schemas': {'FluxDevInput': {
            'properties': {'prompt': {'type': 'string'}, 'num_images': {'type': 'integer', 'default': 1}}}}},
    }
    assert {p.name for p in params_from_openapi(doc)} == {'prompt', 'num_images'}


def test_a_schema_that_makes_no_sense_produces_no_controls_rather_than_an_error():
    assert params_from_openapi({}) == []
    assert params_from_openapi({'components': {'schemas': {'Input': {}}}}) == []


# ---------------------------------------------------------------------------
# Reading a response that could be shaped any of six ways
# ---------------------------------------------------------------------------


def test_an_output_is_found_wherever_the_service_decided_to_put_it():
    assert urls_in('https://x/a.png') == ['https://x/a.png']
    assert urls_in(['https://x/a.png', 'https://x/b.png']) == ['https://x/a.png', 'https://x/b.png']
    assert urls_in({'video': {'url': 'https://x/v.mp4'}}) == ['https://x/v.mp4']
    assert urls_in({'images': [{'url': 'https://x/a.png'}]}) == ['https://x/a.png']
    assert urls_in({'result': {'sample': 'https://x/s.jpg'}}) == ['https://x/s.jpg']


def test_a_thumbnail_is_never_mistaken_for_the_result():
    """Several of these return a preview alongside the output, and a gallery
    showing the 128px version as the result is a gallery that looks broken."""
    found = urls_in({'url': 'https://x/full.mp4', 'thumbnail_url': 'https://x/thumb.jpg'})
    assert found == ['https://x/full.mp4']


def test_a_non_url_string_is_not_an_output():
    assert urls_in({'status': 'succeeded', 'id': 'abc123'}) == []


def test_what_a_file_is_comes_from_its_bytes_not_its_header():
    """Several of these services label everything application/octet-stream,
    and a PNG stored as .bin is a PNG no gallery will show."""
    assert sniff(b'\x89PNG\r\n\x1a\n' + b'0' * 20, 'application/octet-stream') == 'image/png'
    assert sniff(b'\xff\xd8\xff\xe0' + b'0' * 20) == 'image/jpeg'
    assert sniff(b'\x00\x00\x00\x20ftypisom' + b'0' * 20) == 'video/mp4'
    assert sniff(b'RIFF\x00\x00\x00\x00WEBP') == 'image/webp'
    # Nothing recognised: the header, then the extension.
    assert sniff(b'nonsense', 'image/gif') == 'image/gif'
    assert sniff(b'nonsense', '', 'https://x/y.mp4?sig=1') == 'video/mp4'


# ---------------------------------------------------------------------------
# The loop every hosted provider shares
# ---------------------------------------------------------------------------


class Fake(HostedMedia):
    """A hosted service that answers from a script rather than from a socket."""

    provider_id = 'fake'

    def __init__(self, script, **kw):
        super().__init__('https://example.invalid', 'k', timeout=5, **kw)
        self.script = list(script)
        self.cancelled = False
        self.submitted = None
        self.downloads = []

    async def submit(self, prompt, *, model, **kw):
        self.submitted = {'prompt': prompt, 'model': model, **kw}
        return Submitted(id='job-1', poll_url='https://example.invalid/j/1')

    async def check(self, job):
        return self.script.pop(0) if self.script else Poll(state='running')

    async def cancel(self, job):
        self.cancelled = True

    async def download(self, url):
        self.downloads.append(url)
        return b'\x89PNG\r\n\x1a\n', 'image/png'


@pytest.fixture(autouse=True)
def _fast_polling(monkeypatch):
    """The loop's own waits, shortened. Without this every test here pays a
    real second per poll for a delay that is not what is under test."""
    monkeypatch.setattr('omote.providers.hosted_media.POLL_START', 0.001)
    monkeypatch.setattr('omote.providers.hosted_media.POLL_MAX', 0.001)


async def test_it_polls_until_it_stops_moving_and_then_downloads():
    fake = Fake([
        Poll(state='queued', note='waiting'),
        Poll(state='running', progress=0.5),
        Poll(state='done', outputs=['https://cdn/x.png']),
    ])
    out = await fake.run('a cat', model='m')
    assert len(out) == 1
    assert out[0].media_type == 'image/png'
    assert fake.downloads == ['https://cdn/x.png']


async def test_progress_is_reported_on_every_poll_including_when_it_has_not_moved():
    """"Third in the queue" becoming "second" is the useful signal on a busy
    service, and a caller watching only the number would miss it."""
    seen = []
    fake = Fake([
        Poll(state='queued', note='third in the queue'),
        Poll(state='queued', note='second in the queue'),
        Poll(state='done', outputs=['https://cdn/x.png']),
    ])
    await fake.run('a cat', model='m', progress=lambda f, n: seen.append((f, n)))
    notes = [n for _, n in seen]
    # Accepted, then each poll, then the download. The two queue positions are
    # both reported even though no fraction ever moved.
    assert notes[0] == 'queued'
    assert notes[1:3] == ['third in the queue', 'second in the queue']
    assert notes[-1] == 'downloading'
    # And no fraction was invented for a job that had not started.
    assert [f for f, _ in seen][:3] == [None, None, None]


async def test_a_failure_carries_the_providers_own_words():
    """"Generation failed" is not an error message. `insufficient credits` and
    `NSFW content detected` want different reactions."""
    fake = Fake([Poll(state='failed', error='insufficient credits')])
    with pytest.raises(RuntimeError, match='insufficient credits'):
        await fake.run('a cat', model='m')
    assert fake.cancelled is False


async def test_cancelling_reaches_the_other_end():
    """A job cancelled here is still running there — and on a hosted service
    still billing there — until the provider is told."""
    fake = Fake([Poll(state='running')] * 50)
    task = asyncio.create_task(fake.run('a cat', model='m'))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert fake.cancelled is True


async def test_giving_up_also_cancels_rather_than_leaving_it_running():
    fake = Fake([Poll(state='running')] * 10_000)
    fake.timeout = 0
    with pytest.raises(TimeoutError, match='job-1'):
        await fake.run('a cat', model='m')
    assert fake.cancelled is True


async def test_bytes_already_in_hand_are_not_re_downloaded():
    """Stability returns the video inside the status body. Turning that into a
    URL to fetch would be a round trip in the direction nobody wants."""
    fake = Fake([Poll(state='done', inline=[(b'\x00\x00\x00\x20ftypisom', 'video/mp4')])])
    out = await fake.run('a cat', model='m')
    assert fake.downloads == []
    assert out[0].media_type == 'video/mp4'


# ---------------------------------------------------------------------------
# Kling signs rather than authenticates
# ---------------------------------------------------------------------------


def test_the_kling_token_is_a_real_signed_jwt():
    """Three lines of HMAC rather than a dependency, which is only a good trade
    if the three lines are right."""
    from omote.providers.kling import mint

    token = mint('my-access-key', 'my-secret')
    header_b64, payload_b64, signature_b64 = token.split('.')

    def unpad(chunk):
        return base64.urlsafe_b64decode(chunk + '=' * (-len(chunk) % 4))

    assert json.loads(unpad(header_b64)) == {'alg': 'HS256', 'typ': 'JWT'}
    payload = json.loads(unpad(payload_b64))
    assert payload['iss'] == 'my-access-key'
    # Valid slightly in the past: the clock here and the clock at Kling are not
    # the same clock, and a token that is not yet valid reads as a bad secret.
    assert payload['nbf'] < payload['exp']

    expected = hmac.new(b'my-secret', f'{header_b64}.{payload_b64}'.encode(), hashlib.sha256).digest()
    assert unpad(signature_b64) == expected


def test_kling_says_what_its_credential_is_rather_than_failing_at_the_first_call():
    from omote.providers.kling import KlingProvider

    provider = KlingProvider(api_key='just-one-value')
    with pytest.raises(RuntimeError, match='access_key:secret_key'):
        provider.headers()


def test_kling_picks_its_endpoint_from_how_it_was_built_not_from_the_model_id():
    """`kling-v1-5` is both an image model and a video model, so the id cannot
    decide. Two instances, one per kind — see connections.build."""
    from omote.providers.kling import KlingProvider

    image = KlingProvider(api_key='a:b', kind='image')
    video = KlingProvider(api_key='a:b', kind='video')
    assert image.kind != video.kind


# ---------------------------------------------------------------------------
# What a generation may be told
# ---------------------------------------------------------------------------


def test_a_picture_parameter_survives_coercion_as_the_id_it_is():
    """An image control carries a media id. Coercion must not turn it into a
    number, drop it, or try to read it."""
    from omote.media.params import Param

    schema = [Param('image', 'Image', 'image')]
    assert coerce(schema, {'image': 'abc123def456'}) == {'image': 'abc123def456'}


async def test_every_media_provider_offers_a_form_without_being_asked_anything():
    """`describe()` is what the panel is built from, so a provider that cannot
    answer it offline is a provider whose panel is empty until a key is typed
    in — which is the wrong order to find out the backend is wrong."""
    from omote.providers.bfl import BFLProvider
    from omote.providers.ideogram import IdeogramProvider
    from omote.providers.kling import KlingProvider
    from omote.providers.luma import LumaProvider
    from omote.providers.minimax import MiniMaxProvider
    from omote.providers.openrouter import OpenRouterImages, OpenRouterVideo
    from omote.providers.runway import RunwayProvider
    from omote.providers.sora import SoraProvider
    from omote.providers.stability import StabilityProvider

    providers = [
        BFLProvider(api_key='k'), IdeogramProvider(api_key='k'), KlingProvider(api_key='a:b'),
        LumaProvider(api_key='k'), MiniMaxProvider(api_key='k'), RunwayProvider(api_key='k'),
        SoraProvider(api_key='k'), StabilityProvider(api_key='k'),
        OpenRouterImages(api_key='k'), OpenRouterVideo(api_key='k'),
    ]
    for provider in providers:
        params = await provider.describe('')
        assert params, provider.provider_id
        # Every form has somewhere to say what to make, whether that is words
        # or a picture.
        kinds = {p.name for p in params}
        assert 'prompt' in kinds or 'image' in kinds, provider.provider_id
        for param in params:
            assert param.label, f'{provider.provider_id}.{param.name}'
            assert param.group, f'{provider.provider_id}.{param.name}'


# --- music -------------------------------------------------------------------


def _store(tmp_path):
    from omote.media.store import MediaStore

    return MediaStore(tmp_path / 'media')


class FakeAudio(HostedMedia):
    """A music service: same submit-poll-download shape, audio out.

    Deliberately not a `Fake` with a different constant. The point of the shape
    is that music is *not* a video job with a different content type — the
    media service used to route any kind that was not `image` to a video
    modality, and the failure that produced was a music request answered with
    an mp4 and no complaint from anything.
    """

    provider_id = 'fake-audio'

    def __init__(self, **kw):
        super().__init__('https://example.invalid', 'k', timeout=5, **kw)
        # Three 'running' answers before it succeeds, so the test crosses the
        # submit-poll-download loop rather than skipping it — the loop is the
        # same one video uses and the point is that music uses it too.
        self.script = [
            Poll(state='queued', note='waiting'),
            Poll(state='running', progress=0.5),
            Poll(state='done', outputs=['https://example.invalid/a.wav']),
        ]
        self.submitted = None

    async def submit(self, prompt, *, model, **kw):
        self.submitted = {'prompt': prompt, 'model': model, **kw}
        return Submitted(id='m-1', poll_url='https://example.invalid/m/1')

    async def check(self, job):
        return self.script.pop(0) if self.script else Poll(state='running')

    async def download(self, url):
        return b'RIFF\x24\x08\x00\x00WAVEfmt ', 'audio/wav'

    async def models(self, kind: str = ''):
        return [{'id': 'test-tune', 'provider': self.provider_id, 'label': 'Test Tune',
                 'note': 'one model, for the picker'}]

    async def describe(self, model: str = '', **kw):
        from omote.media.params import common_music

        return common_music()

    async def generate(self, prompt, *, model='', progress=None, **kw):
        # The `MusicProvider` signature, which is not the video one: no `n`, no
        # `size`, and everything else by keyword. A music backend that took
        # `n` and `size` would be a video backend with a music model.
        return await self.run(prompt, model=model, progress=progress, **kw)


def test_audio_bytes_are_recognised_from_their_own_magic():
    """Sniffed, not taken from a header, because several of these services
    return `application/octet-stream` for everything — and a file stored with
    the wrong extension is a file nothing will open."""
    from omote.providers.hosted_media import sniff

    assert sniff(b'RIFF\x24\x08\x00\x00WAVEfmt ') == 'audio/wav'
    assert sniff(b'OggS\x00\x02') == 'audio/ogg'
    assert sniff(b'fLaC\x00\x00\x00"') == 'audio/flac'
    assert sniff(b'MThd\x00\x00\x00\x06') == 'audio/midi'
    assert sniff(b'ID3\x03\x00') == 'audio/mpeg'
    # An MPEG frame sync is eleven set bits, not one of sixteen literals.
    assert sniff(b'\xff\xe0\x00\x00') == 'audio/mpeg'
    assert sniff(b'\xff\xfa\x00\x00') == 'audio/mpeg'
    # And nothing above may have broken what was there.
    assert sniff(b'\x89PNG\r\n\x1a\n') == 'image/png'
    assert sniff(b'\x00\x00\x00\x18ftypmp42') == 'video/mp4'
    assert sniff(b'RIFF\x24\x08\x00\x00WEBPVP8 ') == 'image/webp'


def test_a_mp3_is_recognised_however_it_arrives():
    """Extension, declared type and magic all say mp3, because three services
    each use one of them."""
    from omote.providers.hosted_media import sniff

    assert sniff(b'whatever', url='https://x/y.mp3') == 'audio/mpeg'
    assert sniff(b'whatever', 'audio/mpeg') == 'audio/mpeg'
    assert sniff(b'ID3\x03', 'application/octet-stream') == 'audio/mpeg'


def test_the_store_knows_what_to_call_an_audio_file():
    """The whole reason `Modality.MUSIC` was not enough on its own: a type with
    no entry in this table is not refused, it is stored as `.bin`."""
    from omote.media.store import EXTENSIONS

    assert EXTENSIONS['audio/mpeg'] == '.mp3'
    assert EXTENSIONS['audio/wav'] == '.wav'
    assert EXTENSIONS['audio/midi'] == '.mid'


async def test_a_music_job_reaches_the_provider_as_bytes_and_lands_in_the_store(tmp_path):
    from omote.media.service import MediaService
    from omote.providers.base import Modality, ProviderInfo
    from omote.providers.registry import ProviderRegistry

    audio = FakeAudio()
    registry = ProviderRegistry()
    registry.register(
        ProviderInfo('fake-audio', 'Fake audio', {Modality.MUSIC}, local=False), {Modality.MUSIC: audio}
    )
    service = MediaService(registry=registry, store=_store(tmp_path))

    assert 'music' in service.can_generate()
    job = service.start_music('lo-fi hip hop, upright bass', params={'bars': 16, 'instrumental': True})
    await job.task

    assert job.state == 'done', job.error
    assert audio.submitted['prompt'] == 'lo-fi hip hop, upright bass'
    # `bars` is a music parameter, not a video one, and it has to arrive as
    # itself rather than being dropped by a video-shaped signature.
    assert audio.submitted['bars'] == 16
    assert audio.submitted['instrumental'] is True
    assert job.media[0].media_type == 'audio/wav'
    assert job.media[0].filename.endswith('.wav')
    assert job.media[0].kind == 'music'
    stored = service.store.path(job.media[0].id)
    assert stored is not None and stored.suffix == '.wav'


async def test_a_music_job_never_resolves_to_a_video_provider(tmp_path):
    """The bug the kind table was written for.

    `_resolve` used to be `Modality.IMAGE if kind == 'image' else
    Modality.VIDEO`, so every kind that was not an image meant video. Adding
    music to that expression does not make the else mean "video" — it makes it
    mean "everything that is not an image", and a music job was answered with
    a cheerful mp4 from a diffusion backend.
    """
    from omote.media.service import MediaService, _modality
    from omote.providers.base import Modality, ProviderInfo
    from omote.providers.registry import NoProviderError, ProviderRegistry

    registry = ProviderRegistry()
    registry.register(
        ProviderInfo('video-only', 'Video only', {Modality.VIDEO}),
        {Modality.VIDEO: object()},
    )
    service = MediaService(registry=registry, store=_store(tmp_path))

    assert _modality('music') is Modality.MUSIC
    with pytest.raises(NoProviderError) as caught:
        service.start_music('anything')
    assert 'music' in str(caught.value)

    # And a kind nobody has heard of is refused by name, not folded into video.
    with pytest.raises(NoProviderError) as caught:
        _modality('audio')
    assert "'audio'" in str(caught.value)


async def test_composing_a_score_needs_no_backend_at_all(tmp_path):
    """The exception that proves the rule above.

    Nothing is offered that would fail on its first call — and composing a
    MIDI file cannot fail, because there is nothing to be configured. Refusing
    it on an install with no music model connected would leave the most
    reliable way to make music unavailable on exactly the installs that could
    least afford to be told no.
    """
    from omote.agent.runtime import default_tools, media_tools
    from omote.media.service import MediaService
    from omote.providers.registry import ProviderRegistry

    registry = ProviderRegistry()
    service = MediaService(registry=registry, store=_store(tmp_path))
    assert service.can_generate() == set(), 'nothing is connected, and it says so'

    names = {tool.name for tool in media_tools(service)}
    assert 'compose_music' in names
    assert 'generate_music' not in names, 'a music generator on a machine with none'

    # And `default_tools` is not where this lives — the media service hands it
    # out — so the check above is the whole of it.
    assert not any(tool.name == 'generate_music' for tool in default_tools())


async def test_a_melody_goes_into_the_file_and_the_stores_note_of_it(tmp_path):
    from omote.agent.tools.base import ToolContext
    from omote.agent.tools.media import ComposeMusicTool
    from omote.media.service import MediaService
    from omote.music.midi import read_notes
    from omote.protocol.agent import Risk
    from omote.providers.registry import ProviderRegistry

    service = MediaService(registry=ProviderRegistry(), store=_store(tmp_path))
    tool = ComposeMusicTool(service)

    graded = tool.assess({'key': 'A minor', 'progression': 'ii-V-I'}, None)
    assert graded.risk is Risk.WRITE
    assert graded.invalid is None

    out = await tool.run(
        {'key': 'A minor', 'progression': 'ii-V-I', 'bars': 4,
         'notes': [{'pitch': 60, 'start': 0, 'length': 2}, {'pitch': 64, 'start': 2, 'length': 2}]},
        ToolContext(root=tmp_path, cwd=tmp_path, emit=None, ask=None, session_id='s'),
    )

    assert 'A minor' in out.content
    assert 'FL Studio' in out.content, 'the point of a MIDI file is the DAW that opens it'
    assert '.flp' in (ComposeMusicTool.description or '')

    stored = service.store.get(out.display['media'][0]['id'])
    # The sidecar is what makes a piece reproducible: change one thing, run it
    # again, and the only difference is the thing you changed.
    assert stored.params['key'] == 'A minor'
    assert stored.params['progression'] == 'ii-V-I'

    found = service.store.path(out.display['media'][0]['id'])
    assert found is not None, 'the score was reported as stored and is not'
    assert found.suffix == '.mid', f'a MIDI score stored as {found.name}, which nothing will open'
    tracks = read_notes(found.read_bytes())
    assert any(len(track) > 2 for track in tracks), 'the melody is a third track'


async def test_an_unwritable_progression_is_refused_rather_than_explained(tmp_path):
    """A refusal the model can read is what lets it try a different one. The
    same message to the person is a lecture about chord notation."""
    from omote.agent.tools.base import ToolContext
    from omote.agent.tools.media import ComposeMusicTool
    from omote.media.service import MediaService
    from omote.providers.registry import ProviderRegistry

    tool = ComposeMusicTool(MediaService(registry=ProviderRegistry(), store=_store(tmp_path)))
    out = await tool.run(
        {'key': 'C major', 'progression': 'flibbertigiget'},
        ToolContext(root=tmp_path, cwd=tmp_path, emit=None, ask=None, session_id='s'),
    )
    assert out.invalid, 'a progression that cannot be written must be an invalid, not prose'
    assert 'I–V–vi–IV' in out.invalid, 'and it should say what it does know'


async def test_a_key_the_model_made_up_is_refused_before_anything_is_written(tmp_path):
    from omote.agent.tools.media import ComposeMusicTool

    tool = ComposeMusicTool(None)
    graded = tool.assess({'key': 'Q sharp major', 'progression': 'I-V'}, None)
    assert graded.invalid and 'C major' in graded.invalid


# --- editing -----------------------------------------------------------------


def _image(tmp_path, size=(400, 300), colour=(255, 0, 0)):
    from PIL import Image

    path = tmp_path / 'picture.png'
    Image.new('RGB', size, colour).save(path)
    return path


def test_a_picture_is_cropped_and_the_result_is_a_real_file(tmp_path):
    from omote.media.edit import image

    source = _image(tmp_path)
    target = tmp_path / 'cropped.png'
    said = image('crop', str(source), str(target), {'left': 10, 'top': 20, 'width': 100, 'height': 50})

    assert said['operation'] == 'crop'
    from PIL import Image

    with Image.open(target) as made:
        assert made.size == (100, 50)


async def test_an_edit_records_the_operation_and_not_the_bytes(tmp_path):
    """The sidecar is what makes an edit reproducible, and a recipe holding a
    megabyte of base64 is a sidecar no tool can open."""
    from omote.media.service import MediaService

    service = MediaService(registry=ProviderRegistry(), store=_store(tmp_path / 'media'))
    source = _image(tmp_path)
    stored = service.store.add(
        source.read_bytes(), kind='image', media_type='image/png', prompt='a red rectangle',
    )

    job = service.edit('crop', [stored.id], params={'left': 1, 'top': 1, 'width': 20, 'height': 20})
    await job.task
    assert job.state == 'done', job.error

    recipe = service.store.get(job.media[0].id).params
    assert recipe['operation'] == 'crop'
    assert recipe['inputs'] == [stored.id], 'the input is recorded as an id'
    assert not any(isinstance(v, (bytes, bytearray)) for v in recipe.values()), 'no bytes in a recipe'
    assert job.media[0].filename.endswith('.png'), (
        f'a cropped PNG was stored as {job.media[0].filename}, which nothing will open'
    )


def test_an_edit_takes_ids_and_never_a_path(tmp_path):
    """The one operation where reading an arbitrary file off the disk is most
    tempting, and the ids are the only thing that stops it."""
    from omote.media.service import MediaService

    service = MediaService(registry=ProviderRegistry(), store=_store(tmp_path / 'media'))
    secret = _image(tmp_path)
    with pytest.raises(NoProviderError) as caught:
        service.edit('crop', [str(secret)], params={'width': 10, 'height': 10})
    assert 'no stored file' in str(caught.value)


def test_a_transparent_png_survives_being_converted_to_a_jpeg(tmp_path):
    """It loses the alpha channel — it has to — and it comes out on white
    rather than on black.

    Flattening onto black is what a naive save does, and it turns a logo with
    transparent edges into a logo with black edges.
    """
    from PIL import Image

    from omote.media.edit import image

    source = tmp_path / 'alpha.png'
    Image.new('RGBA', (40, 40), (0, 0, 0, 0)).save(source)
    target = tmp_path / 'flat.jpg'
    image('convert', str(source), str(target), {'format': 'JPEG', 'quality': 92})

    with Image.open(target) as made:
        assert made.mode == 'RGB'
        # Fully transparent became fully white.
        # `getextrema` rather than `getdata`: the latter is a flat tuple of
        # channels and comparing it to a number is comparing a tuple to an int.
        assert min(channel[0] for channel in made.getextrema()) > 200


def test_an_operation_it_does_not_know_is_refused(tmp_path):
    from omote.media.edit import EditRefused, image

    _image(tmp_path)
    with pytest.raises(EditRefused) as caught:
        image('rm-rf', str(tmp_path / 'picture.png'), str(tmp_path / 'o.png'), {})
    assert 'rm-rf' in str(caught.value)


def test_a_number_outside_what_the_operation_accepts_is_refused_with_the_range(tmp_path):
    """A sentence beats handing ffmpeg a 100000x speed and waiting."""
    from omote.media.edit import EditRefused, image

    _image(tmp_path)
    with pytest.raises(EditRefused) as caught:
        image('resize', str(tmp_path / 'picture.png'), str(tmp_path / 'o.png'), {'factor': 5000})
    assert 'between 0.01 and 32' in str(caught.value)


def test_a_crop_with_no_size_is_refused(tmp_path):
    from omote.media.edit import EditRefused, image

    _image(tmp_path)
    with pytest.raises(EditRefused):
        image('crop', str(tmp_path / 'picture.png'), str(tmp_path / 'o.png'), {'width': 0, 'height': 10})


@pytest.mark.skipif(edit_ops.ffmpeg() is None, reason='ffmpeg is not installed')
def test_a_clip_is_trimmed(tmp_path):
    from omote.media.edit import probe, video

    source = _clip(tmp_path)
    target = tmp_path / 'trimmed.mp4'
    said = video('trim', str(source), str(target), {'start': 1, 'length': 2})
    assert said == {'operation': 'trim', 'start': 1.0, 'length': 2.0}
    found = probe(str(target))
    assert 1.0 < found['duration'] < 3.0


@pytest.mark.skipif(edit_ops.ffmpeg() is None, reason='ffmpeg is not installed')
def test_what_a_clip_is_gets_reported_before_it_is_trimmed(tmp_path):
    """"How long is the clip I just imported" is the question before every trim,
    and answering it by opening the file in a player is a worse answer."""
    from omote.media.edit import probe

    found = probe(str(_clip(tmp_path)))
    assert found['duration'] > 3
    assert found['has_video'] and found['has_audio']


@pytest.mark.skipif(edit_ops.ffmpeg() is None, reason='ffmpeg is not installed')
def test_two_clips_join_in_order(tmp_path):
    from omote.media.edit import concat

    first = _clip(tmp_path, name='a', colour='red')
    second = _clip(tmp_path, name='b', colour='blue')
    target = tmp_path / 'joined.mp4'
    concat([str(first), str(second)], str(target))
    assert target.stat().st_size > 0


def _clip(tmp_path, *, name='a', colour='red'):
    """A two-second clip, made with ffmpeg's own test source."""
    binary = edit_ops.ffmpeg()
    assert binary, 'the test needs ffmpeg'
    out = tmp_path / f'{name}.mp4'
    subprocess.run(
        [binary, '-y', '-f', 'lavfi', '-i', 'testsrc=size=160x120:rate=10:duration=2',
         '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', '-shortest',
         '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac', str(out)],
        capture_output=True, check=True, timeout=120,
    )
    del colour
    return out


def test_a_video_operation_without_ffmpeg_says_what_to_install(tmp_path):
    """The refusal is the feature. A trim that half-works is worse than one
    that tells you what is missing."""
    if edit_ops.ffmpeg() is not None:
        pytest.skip('ffmpeg is installed here, so the refusal cannot be observed')
    from omote.media.edit import EditUnavailable, video

    with pytest.raises(EditUnavailable) as caught:
        video('trim', '/tmp/nothing.mp4', '/tmp/out.mp4', {'start': 0, 'length': 1})
    said = str(caught.value)
    assert 'omote[video]' in said, 'and it should say how to get one'


def test_an_editor_is_not_a_modality():
    """Every generator in this project is somebody else's model over a network,
    so the registry is the right place for them. An editor is a binary on this
    disk, and putting it in the registry would imply an install that can fail
    to answer. So it is asked of the machine instead."""
    from omote.media.service import MediaService

    service = MediaService(registry=ProviderRegistry(), store=_store(tmp_path_path()))
    found = service.can_edit()
    assert set(found) <= {'image', 'video', 'audio'}
    for kind, editors in found.items():
        assert all(one in ('ffmpeg', 'pillow', 'gimp') for one in editors), (kind, editors)


def tmp_path_path():
    return Path(tempfile.mkdtemp())


def test_gimp_will_not_run_an_arbitrary_procedure():
    """A Script-Fu expression is code.

    It is evaluated by an interpreter with access to every PDB procedure —
    including `gimp-file-delete` — so passing a model-supplied string through
    unfiltered would be handing arbitrary code execution to anything that could
    reach this, in a project whose whole argument is that the agent's reach is
    bounded. The name comes from a table written here; only numbers and two
    paths are interpolated.
    """
    from omote.providers.gimp import build_script

    for hostile in ('gimp-quit', '(gimp-file-delete "/etc/passwd")', 'anything'):
        with pytest.raises(KeyError):
            build_script(hostile, '/tmp/a.png', '/tmp/o.png', {})

    # And the two paths are quoted, so a filename with a space or a quote in it
    # is still a filename.
    script = build_script('unsharp', "/tmp/a b's.png", '/tmp/out.png', {'amount': 3})
    assert 'unsharp' in script
    assert "a b" in script


def test_gimp_numbers_are_clamped_to_what_the_procedure_accepts():
    from omote.providers.gimp import GimpError, build_script

    # GIMP clamps these itself; refusing here means the value in the sidecar is
    # the value that ran.
    with pytest.raises(GimpError) as caught:
        build_script('unsharp', '/tmp/a.png', '/tmp/o.png', {'amount': 99})
    assert 'between 1 and 5' in str(caught.value)

    with pytest.raises(GimpError):
        build_script('levels', '/tmp/a.png', '/tmp/o.png', {'low': 200, 'high': 10})
