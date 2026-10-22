"""Source video, speech and distinct characters must reach their own graph inputs."""
import json
import threading
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from omote.media import workflow
from omote.providers.comfyui import ComfyUIProvider

PNG = b'\x89PNG\r\n\x1a\n' + b'character one'
JPEG = b'\xff\xd8\xff' + b'character two'
VIDEO = b'\x00\x00\x00\x20ftypisom' + b'source clip'
AUDIO = b'RIFF\x00\x00\x00\x00WAVE' + b'speech track'


def graph():
    return {'1': {'class_type': 'LoadImage', 'inputs': {'image': 'one.png'}},
            '2': {'class_type': 'LoadImage', 'inputs': {'image': 'two.jpg'}},
            '3': {'class_type': 'LoadVideo', 'inputs': {'file': 'source.mp4'}},
            '4': {'class_type': 'LoadAudio', 'inputs': {'audio': 'speech.wav'}},
            '5': {'class_type': 'LoadVideo', 'inputs': {'file': 'pose.mp4'}}}


def test_import_creates_distinct_file_controls_and_keeps_existing_bindings():
    original = graph()
    original['1']['inputs']['image'] = '%image_2%'
    template, tokens = workflow.tokenise(original)
    assert original['2']['inputs']['image'] == 'two.jpg'
    assert template['1']['inputs']['image'] == '%image_2%'
    assert template['2']['inputs']['image'] == '%image%'
    assert set(tokens) == {'image', 'image_2', 'video', 'video_2', 'audio'}
    schema = {p.name: p for p in workflow.describe(template, {'video_2': {'label': 'Pose guide'},
                                                            'unused': {'kind': 'audio'}})}
    assert schema['video_2'].label == 'Pose guide'
    assert schema['audio'].kind == 'audio' and schema['image_2'].kind == 'image'
    assert 'unused' not in schema
    filled = workflow.inject(template, {name: name + '.input' for name in tokens})
    assert filled['1']['inputs']['image'] != filled['2']['inputs']['image']
    assert workflow.tokens_in({'path': '%unknown_2%'}) == set()
    assert workflow.tokenise(template)[0] == template


def test_numbered_inputs_keep_character_order_past_nine():
    template = {'inputs': ['%image_10%', '%image_2%', '%image%']}
    assert [p.name for p in workflow.describe(template)] == ['image', 'image_2', 'image_10']


@pytest.fixture
def comfy():
    state = {'uploads': [], 'prompts': [], 'reply': None}

    class Handler(BaseHTTPRequestHandler):
        def reply(self, body, content_type='application/json'):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):  # noqa: N802
            raw = self.rfile.read(int(self.headers['Content-Length']))
            if self.path == '/upload/image':
                message = BytesParser(policy=default).parsebytes(
                    ('Content-Type: ' + self.headers['Content-Type'] + '\r\n\r\n').encode() + raw)
                parts = list(message.iter_parts())
                file = next(p for p in parts if p.get_filename())
                state['uploads'].append({'name': file.get_filename(), 'type': file.get_content_type(),
                                         'data': file.get_payload(decode=True)})
                self.reply(state['reply'] or {'name': 'renamed-' + file.get_filename(), 'subfolder': 'references'})
            else:
                assert self.path == '/prompt'
                state['prompts'].append(json.loads(raw)['prompt'])
                self.reply({'prompt_id': 'test-render'})

        def do_GET(self):  # noqa: N802
            if self.path == '/history':
                self.reply({'test-render': {'outputs': {
                    '3': {'images': [{'filename': 'source-preview.mp4', 'type': 'input'}]},
                    '9': {'videos': [{'filename': 'result.mp4'}]}}}})
            else:
                assert self.path.startswith('/view?')
                assert 'source-preview' not in self.path
                self.reply(VIDEO, 'video/mp4')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield ComfyUIProvider(f'http://127.0.0.1:{server.server_port}'), state
    server.shutdown()
    server.server_close()
    worker.join(timeout=5)


async def test_render_uploads_every_declared_media_input_with_its_real_format(comfy, monkeypatch):
    provider, state = comfy
    template, _ = workflow.tokenise(graph())
    monkeypatch.setattr(provider, '_load_workflow', lambda _: (template, {}))
    result = await provider.generate('Two distinct characters', model='local', seed=42,
                                    image=PNG, image_2=JPEG, video=VIDEO, video_2=VIDEO, audio=AUDIO)
    assert len(result) == 1 and result[0].data == VIDEO and result[0].seed == 42
    assert len(state['uploads']) == 5
    assert {item['type'] for item in state['uploads']} == {'image/png', 'image/jpeg', 'video/mp4', 'audio/wav'}
    assert {item['data'] for item in state['uploads']} == {PNG, JPEG, VIDEO, AUDIO}
    sent = state['prompts'][0]
    files = [sent[node]['inputs'][field] for node, field in [('1', 'image'), ('2', 'image'),
             ('3', 'file'), ('4', 'audio'), ('5', 'file')]]
    assert len(set(files)) == 5
    assert all(path.startswith('references/renamed-') for path in files)
    assert [path.rsplit('.', 1)[-1] for path in files] == ['png', 'jpg', 'mp4', 'wav', 'mp4']


@pytest.mark.parametrize('value', [None, '', []])
async def test_missing_reference_fails_before_queueing(comfy, monkeypatch, value):
    provider, state = comfy
    monkeypatch.setattr(provider, '_load_workflow', lambda _: ({'1': {'inputs': {'audio': '%audio%'}}}, {}))
    with pytest.raises(ValueError, match='Choose a file'):
        await provider.generate('Speak', model='local', audio=value)
    assert not state['prompts'] and not state['uploads']


async def test_wrong_file_kind_is_refused_before_upload(comfy):
    provider, state = comfy
    with pytest.raises(ValueError, match='recognized audio'):
        await provider._upload(PNG, 'audio', 'audio')
    assert not state['uploads']


@pytest.mark.parametrize('reply', [{'name': '../escape.wav'}, {'name': 'ok.wav', 'subfolder': '/outside'},
                                 {'name': 'ok.wav', 'subfolder': '..'}, {'name': 42}])
async def test_upload_response_cannot_redirect_a_workflow_outside_input(comfy, reply):
    provider, state = comfy
    state['reply'] = reply
    with pytest.raises(ValueError, match='invalid input filename'):
        await provider._upload(AUDIO, 'audio', 'audio')
