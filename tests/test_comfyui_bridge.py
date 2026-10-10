"""Run the exact shipped ComfyUI extension against a small upstream API double."""
from __future__ import annotations

import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / 'integrations' / 'comfyui' / 'omote_bridge' / 'web' / 'bridge.js'

HARNESS = r'''
import { pathToFileURL } from 'url';
const plan = JSON.parse(process.argv[3]);
const replies = [];
const calls = [];
let listener;
const parent = { postMessage: (message, origin) => replies.push({message, origin}) };
const stranger = { postMessage: (message, origin) => replies.push({message, origin, stranger: true}) };
globalThis.window = {
  parent,
  addEventListener(name, fn) { if (name === 'message') listener = fn; },
};
globalThis.appFixture = {
  extensionManager: {setting: {get: () => plan.origins ?? 'https://omote.example'}},
  ui: {settings: {getSettingValue: () => plan.legacyOrigins ?? ''}},
  registerExtension(extension) { calls.push({op: 'register', name: extension.name}); extension.setup(); },
  async graphToPrompt() {
    calls.push({op: 'capture'});
    if (plan.captureError) throw new Error(plan.captureError);
    return {workflow: plan.graph ?? {nodes: [{id: 1}], extra: {renderer: 'Vue', subgraphs: [{id: 'child'}]}},
            output: {'1': {class_type: 'CustomNode', inputs: {prompt: '京都'}}}};
  },
  async loadGraphData(graph) { calls.push({op: 'load', graph}); },
};
if (plan.legacy) delete globalThis.appFixture.extensionManager;
await import(pathToFileURL(process.argv[2]).href);
for (const entry of plan.messages) {
  const message = entry.message ?? {channel: 'omote.comfy.v1', id: 'request', action: 'hello'};
  if (entry.nodes !== undefined) message.graph = {nodes: Array.from({length: entry.nodes}, (_, id) => ({id}))};
  if (entry.large) message.graph = {nodes: [], data: 'x'.repeat(4 * 1024 * 1024)};
  if (entry.unicode) message.graph = {nodes: [], data: '京都'.repeat(800000)};
  if (entry.circular) { message.graph = {nodes: []}; message.graph.self = message.graph; }
  await listener({data: message, origin: entry.origin ?? 'https://omote.example',
                  source: entry.source === 'stranger' ? stranger : entry.source === 'self' ? window : parent});
}
console.log(JSON.stringify({calls, replies}));
'''


@pytest.fixture
def bridge(tmp_path):
    node = shutil.which('node') or shutil.which(str(Path.home() / '.local' / 'bin' / 'node'))
    if not node:
        pytest.skip('Node.js is unavailable')
    (tmp_path / 'package.json').write_text('{"type":"module"}')
    script_dir = tmp_path / 'scripts'
    script_dir.mkdir()
    (script_dir / 'app.js').write_text('export const app = globalThis.appFixture;')
    extension_dir = tmp_path / 'extensions' / 'omote_bridge'
    extension_dir.mkdir(parents=True)
    copy = extension_dir / 'bridge.js'
    copy.write_bytes(BRIDGE.read_bytes())
    harness = tmp_path / 'harness.mjs'
    harness.write_text(HARNESS)

    def run(messages, **options):
        proc = subprocess.run([node, str(harness), str(copy), json.dumps({'messages': messages, **options})],
                              capture_output=True, text=True, timeout=20)
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)

    return run


def message(action, **data):
    return {'channel': 'omote.comfy.v1', 'id': 'req-123', 'action': action, **data}


@pytest.mark.parametrize('entry', [
    {'origin': 'https://omote.example.evil'}, {'origin': 'http://omote.example'}, {'origin': 'null'},
    {'origin': 'https://other.example'}, {'source': 'stranger'}, {'source': 'self'},
    {'message': {'channel': 'other', 'id': 'req', 'action': 'capture'}},
    {'message': {'channel': 'omote.comfy.v1', 'id': 12, 'action': 'capture'}},
    {'message': {'channel': 'omote.comfy.v1', 'id': 'x' * 101, 'action': 'capture'}},
])
def test_untrusted_or_malformed_messages_cannot_read_or_mutate_graph(bridge, entry):
    entry = {'message': message('capture'), **entry}
    result = bridge([entry])
    assert result['replies'] == []
    assert [call['op'] for call in result['calls']] == ['register']


def test_unconfigured_and_wildcard_origins_do_not_grant_access(bridge):
    for origins in ['', '*', 'null']:
        assert bridge([{'message': message('capture')}], origins=origins)['replies'] == []
    assert bridge([{'origin': 'null'}], origins='null')['replies'] == []


def test_handshake_has_exact_origin_and_matching_request_id(bridge):
    result = bridge([{'message': message('hello')}])
    assert result['replies'] == [{'origin': 'https://omote.example', 'message': {
        'channel': 'omote.comfy.v1', 'id': 'req-123', 'ok': True, 'capabilities': ['capture', 'load'],
    }}]
    assert len(result['calls']) == 1


def test_capture_round_trips_native_nodes2_subgraphs_and_api_prompt(bridge):
    graph = {'nodes': [{'id': 1, 'type': 'custom', 'widgets_values': ['京都']}],
             'extra': {'workflowRendererVersion': 'Vue', 'subgraphs': [{'id': 'child', 'nodes': []}]}}
    result = bridge([{'message': message('capture')}], graph=graph)
    reply = result['replies'][0]['message']
    assert reply['ok']
    assert reply['graph'] == graph
    assert reply['prompt']['1']['inputs']['prompt'] == '京都'
    assert [call['op'] for call in result['calls']] == ['register', 'capture']


def test_loading_preserves_native_graph_without_executing_it(bridge):
    graph = {'nodes': [{'id': 23, 'type': 'CustomNode'}], 'extra': {'subgraphs': []}}
    result = bridge([{'message': message('load', graph=graph)}])
    assert result['calls'][-1] == {'op': 'load', 'graph': graph}
    assert result['replies'][0]['message']['ok']
    assert [call['op'] for call in result['calls']] == ['register', 'load']


@pytest.mark.parametrize('entry', [
    {'message': message('load')}, {'message': message('load', graph={'nodes': {}})},
    {'message': message('load'), 'nodes': 5001}, {'message': message('load'), 'large': True},
    {'message': message('load'), 'circular': True}, {'message': message('load'), 'unicode': True},
    {'message': message('queue')},
])
def test_invalid_loads_and_queue_commands_do_not_mutate_editor(bridge, entry):
    result = bridge([entry])
    assert [call['op'] for call in result['calls']] == ['register']
    assert len(result['replies']) == 1
    assert not result['replies'][0]['message']['ok']
    assert result['replies'][0]['message']['error']


def test_multiple_exact_trusted_origins_and_legacy_settings(bridge):
    result = bridge([{'message': message('hello'), 'origin': 'http://127.0.0.1:8477'}],
                    origins='https://omote.example, http://127.0.0.1:8477')
    assert result['replies'][0]['origin'] == 'http://127.0.0.1:8477'
    result = bridge([{'message': message('hello')}], legacy=True, legacyOrigins='https://omote.example')
    assert result['replies'][0]['message']['ok']


def test_upstream_errors_are_bounded_and_keep_request_correlation(bridge):
    result = bridge([{'message': message('capture')}], captureError='error-' * 1000)
    reply = result['replies'][0]['message']
    assert not reply['ok']
    assert reply['id'] == 'req-123'
    assert len(reply['error']) <= 500



def test_downloaded_bridge_archive_matches_reviewed_integration_source():
    with zipfile.ZipFile(ROOT / 'omote' / 'static' / 'omote-comfyui-bridge.zip') as archive:
        assert archive.read('omote_bridge/web/bridge.js') == BRIDGE.read_bytes()
        assert archive.read('omote_bridge/__init__.py') == (BRIDGE.parents[1] / '__init__.py').read_bytes()
