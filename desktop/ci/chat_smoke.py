"""Send messages through a packaged daemon, execute a file tool and check errors.

python desktop/ci/chat_smoke.py /path/to/omote-daemon
python desktop/ci/chat_smoke.py --live-model openrouter/free /path/to/omote-daemon

The default uses a local HTTP/SSE fixture, with real daemon/provider/websocket
wiring. --live-model sends synthetic prompts to a zero-priced OpenRouter model
using OPENROUTER_API_KEY. Everything written belongs to a temporary workspace.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from websockets.sync.client import connect

ANSWER = 'OMOTE_CHAT_OK café 世界'
CONTENT = 'Omote tool execution verified. café 世界\n'


class Provider(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        model = request['model']
        if model == 'stream-error':
            chunks = [{'error': {'code': 429, 'message': 'Fixture upstream rate limit; choose another model.'}}]
        elif model == 'blank':
            chunks = [{'choices': [{'delta': {}, 'finish_reason': 'stop'}]}]
        elif not any(m['role'] == 'tool' for m in request['messages']):
            chunks = [
                {
                    'choices': [
                        {
                            'delta': {
                                'tool_calls': [
                                    {
                                        'index': 0,
                                        'id': 'fixture_write',
                                        'type': 'function',
                                        'function': {
                                            'name': 'write_file',
                                            'arguments': json.dumps({'path': 'chat-smoke.txt', 'content': CONTENT}),
                                        },
                                    }
                                ]
                            },
                            'finish_reason': 'tool_calls',
                        }
                    ]
                }
            ]
        else:
            chunks = [
                {'choices': [{'delta': {'content': ANSWER}, 'finish_reason': None}]},
                {'choices': [{'delta': {}, 'finish_reason': 'stop'}]},
            ]
        body = ''.join('data: ' + json.dumps(c, ensure_ascii=False) + '\n\n' for c in chunks) + 'data: [DONE]\n\n'
        payload = body.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        # Split writes across both JSON and UTF-8 characters. TCP/SSE framing
        # must not depend on packet boundaries or the OS's console encoding.
        for offset in range(0, len(payload), 7):
            self.wfile.write(payload[offset : offset + 7])
        self.wfile.flush()


def http(base, path, data=None, method=None):
    request = urllib.request.Request(
        base + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={'Content-Type': 'application/json'},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=40) as response:
        return json.load(response)


def conversation(base, root, model, *, error=''):
    session = http(
        base,
        '/api/sessions',
        {
            'root': str(root),
            'provider': 'chat-smoke',
            'model': model,
            'mode': 'auto_edit',
            'tools': ['write_file', 'read_file'],
        },
    )
    text, tools, errors = '', [], []
    with connect(base.replace('http:', 'ws:') + '/ws/agent?session=' + session['id']) as ws:
        ws.send(
            json.dumps(
                {
                    'type': 'turn.submit',
                    'text': (
                        'Use write_file to create chat-smoke.txt in the current workspace with exactly this UTF-8 content: '
                        + repr(CONTENT)
                        + '. Then reply with exactly: '
                        + ANSWER
                        + '. Do not ask questions or use any other tools.'
                    ),
                }
            )
        )
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            event = json.loads(ws.recv(timeout=max(0.1, deadline - time.monotonic())))
            if event['type'] == 'text.delta':
                text += event['text']
            elif event['type'] == 'tool.completed':
                if not event['result']['ok']:
                    raise RuntimeError('File tool failed: ' + event['result']['content'][:500])
                tools.append(event['result']['name'])
            elif event['type'] == 'tool.proposed':
                # Only the expected file write may be approved in this fixture.
                call = event['call']
                path = call['arguments'].get('path', '')
                if (
                    call['name'] != 'write_file'
                    or not path
                    or (root / path).resolve() != root.joinpath('chat-smoke.txt').resolve()
                ):
                    raise RuntimeError('Unexpected approval request: ' + call['name'])
                ws.send(json.dumps({'type': 'tool.approve', 'call_id': call['id']}))
            elif event['type'] == 'error':
                errors.append(event['message'])
            elif event['type'] == 'turn.completed':
                if error:
                    if event['stop_reason'] != 'error' or not any(error in e for e in errors):
                        raise RuntimeError(f'Expected a visible error, got {event["stop_reason"]}: {errors}')
                elif errors or event['stop_reason'] != 'end_turn':
                    raise RuntimeError(f'Chat did not finish: {errors or event["stop_reason"]}')
                break
        else:
            raise RuntimeError('No completed turn received.')
    if not error:
        if 'write_file' not in tools or root.joinpath('chat-smoke.txt').read_text(encoding='utf-8') != CONTENT:
            raise RuntimeError('The model did not create the expected file with the file tool.')
        if 'OMOTE_CHAT_OK' not in text:
            raise RuntimeError('The model did not send the expected final answer: ' + text[:500])
        print(f'{model}: streamed answer, UTF-8 file write and completed turn passed.', flush=True)
    else:
        print(f'{model}: visible error and failed turn passed.', flush=True)
    http(base, '/api/sessions/' + session['id'], method='DELETE')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live-model', action='append', default=[])
    parser.add_argument('program', nargs='+')
    args = parser.parse_args()
    key = os.getenv('OPENROUTER_API_KEY', '')
    if args.live_model:
        if not key:
            parser.error('OPENROUTER_API_KEY is required for live checks')
        listing = http('https://openrouter.ai/api/v1', '/models')
        free = {
            row['id']
            for row in listing['data']
            if all(
                str(row.get('pricing', {}).get(kind, 'unknown')) in ('0', '0.0') for kind in ('prompt', 'completion')
            )
        }
        if any(model not in free for model in args.live_model):
            parser.error('Every live model must currently have zero prompt and completion pricing')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='omote-chat-smoke-') as temporary:
            root = Path(temporary)
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                port = listener.getsockname()[1]
            base = f'http://127.0.0.1:{port}'
            env = {
                k: v
                for k, v in os.environ.items()
                if k.upper()
                in {
                    'PATH',
                    'HOME',
                    'SYSTEMROOT',
                    'WINDIR',
                    'TEMP',
                    'TMP',
                    'TMPDIR',
                    'APPDATA',
                    'LOCALAPPDATA',
                    'SSL_CERT_FILE',
                    'SSL_CERT_DIR',
                }
            }
            env.update(
                OMOTE_ENV=os.devnull,
                OMOTE_PORT=str(port),
                OMOTE_DATA_DIR=str(root / 'data'),
                OMOTE_WORKSPACE=str(root),
                OMOTE_EXIT_WITH_STDIN='1',
                OMOTE_UPDATE_CHECK='false',
            )
            # Preserve virtualenv symlinks when exercising a source daemon.
            command = [os.path.abspath(args.program[0]), *args.program[1:]]
            with root.joinpath('daemon.log').open('wb') as log:
                process = subprocess.Popen(
                    command, env=env, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT
                )
            try:
                deadline = time.monotonic() + 180
                while True:
                    try:
                        if http(base, '/healthz').get('service') == 'omote':
                            break
                    except (OSError, ValueError):
                        pass
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError('Packaged daemon did not start.')
                    time.sleep(0.2)
                connection = {
                    'id': 'chat-smoke',
                    'adapter': 'openai',
                    'base_url': f'http://127.0.0.1:{server.server_port}/v1',
                    'modalities': ['chat'],
                    'local': True,
                }
                http(base, '/api/providers/connections', connection, method='PUT')
                conversation(base, root, 'fixture')
                conversation(base, root, 'stream-error', error='Fixture upstream rate limit')
                conversation(base, root, 'blank', error='no answer after a retry')
                if args.live_model:
                    connection.update(
                        adapter='openrouter', base_url='https://openrouter.ai/api/v1', api_key=key, local=False
                    )
                    http(base, '/api/providers/connections', connection, method='PUT')
                    for model in args.live_model:
                        root.joinpath('chat-smoke.txt').unlink(missing_ok=True)
                        conversation(base, root, model)
                process.stdin.close()
                if process.wait(30) != 0:
                    raise RuntimeError('Packaged daemon did not stop cleanly.')
            except Exception:
                # Print diagnostics without the API key, including provider errors.
                diagnostic = root.joinpath('daemon.log').read_text(errors='replace')[-12000:]
                print(diagnostic.replace(key, '[redacted]') if key else diagnostic, flush=True)
                raise
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(10)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


if __name__ == '__main__':
    main()
