"""A real local workbench database and HTTP router, with only ComfyUI's socket mocked."""
from __future__ import annotations

import copy
import json
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omote.config import config
from omote.providers.base import Modality, ProviderInfo
from omote.providers.comfyui import ComfyUIProvider
from omote.providers.registry import ProviderRegistry, RouteSet
from omote.routers import auth, workbench

PREFIX = '/api/media/comfyui'
CATALOG = {
    'Source': {'input': {'required': {'seed': ['INT', {'min': 0, 'max': 100}],
                                    'mode': [['fast', 'slow'], {}]}}, 'output': ['IMAGE']},
    'SaveImage': {'input': {'required': {'images': ['IMAGE'], 'prefix': ['STRING']}}, 'output': []},
    'Pass': {'input': {'required': {'image': ['IMAGE']}}, 'output': ['IMAGE']},
}
PROMPT = {'1': {'class_type': 'Source', 'inputs': {'seed': 12, 'mode': 'fast'}},
          '2': {'class_type': 'SaveImage', 'inputs': {'images': ['1', 0], 'prefix': 'character'}}}
GRAPH = {'nodes': [{'id': 1, 'type': 'Source', 'widgets_values': [12, 'fast']}], 'links': [],
         'extra': {'workflowRendererVersion': 'Vue', 'subgraphs': [{'id': 'nested', 'nodes': []}]}}


class Response:
    def __init__(self, data, status=200):
        self.data = data if isinstance(data, bytes) else json.dumps(data).encode()
        self.status = status
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass

    async def iter_chunked(self, size):
        for start in range(0, len(self.data), size):
            yield self.data[start:start + size]


class Socket:
    """The transport is a sentinel: bypassing it cannot reach our fake server."""
    tor = True

    def __init__(self):
        self.routes = {
            ('GET', '/object_info'): Response(CATALOG),
            ('POST', '/prompt'): Response({'prompt_id': 'owned-prompt'}),
            ('GET', '/history'): Response({}),
            ('GET', '/queue'): Response({'queue_pending': [], 'queue_running': []}),
            ('POST', '/queue'): Response({}),
        }
        self.calls = []
        self.timeouts = []

    def session(self, timeout):
        self.timeouts.append(timeout)
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.routes[(method, urlsplit(url).path)]

    def get(self, url, **kwargs):
        return self.request('GET', url, **kwargs)

    def post(self, url, **kwargs):
        return self.request('POST', url, **kwargs)


@pytest.fixture
def bench(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'data_dir', tmp_path)
    monkeypatch.setattr(config, 'default_user', 'alice')
    monkeypatch.setattr(config, 'auth_token', '')
    registry = ProviderRegistry()
    socket = Socket()
    impl = ComfyUIProvider('http://configured-comfy.invalid', 'secret-server-key', transport=socket)
    registry.register(ProviderInfo(id='comfy', label='Comfy', modalities={Modality.IMAGE}, local=True),
                      {Modality.IMAGE: impl})
    monkeypatch.setattr(workbench, 'registry', registry)
    app = FastAPI()
    app.add_middleware(auth.TokenMiddleware)
    app.include_router(workbench.router)
    with TestClient(app) as client:
        yield client, socket, registry, impl


def save(client, **changes):
    body = {'name': 'Character study', 'project': 'story', 'graph': copy.deepcopy(GRAPH),
            'prompt': copy.deepcopy(PROMPT), **changes}
    response = client.post(f'{PREFIX}/workflows', json=body)
    assert response.status_code == 200, response.text
    return response.json()


def start(client, **changes):
    saved = save(client)
    response = client.post(f'{PREFIX}/runs', json={'provider': 'comfy', 'workflow_id': saved['id'],
                                                 'revision': 1, **changes})
    assert response.status_code == 200, response.text
    return saved, response.json()


def history(socket, prompt_id='owned-prompt', **changes):
    record = {'status': {'status_str': 'success'}, 'outputs': {
        '2': {'images': [{'filename': 'character.png', 'subfolder': 'story/scene', 'type': 'output'}]},
    }, **changes}
    socket.routes[('GET', '/history')] = Response({prompt_id: record})
    return record


def test_workflow_versions_preserve_graph_subgraphs_and_prompt(bench):
    client, *_ = bench
    original = save(client, name='  Character study  ', notes='First draft')
    assert original['name'] == 'Character study'
    assert original['graph'] == GRAPH
    assert original['prompt'] == PROMPT
    changed = copy.deepcopy(PROMPT)
    changed['1']['inputs']['seed'] = 99
    response = client.post(f"{PREFIX}/workflows/{original['id']}/versions", json={
        'name': 'Character final', 'project': 'story', 'notes': 'New seed', 'base_revision': 1,
        'graph': GRAPH, 'prompt': changed,
    })
    assert response.status_code == 200, response.text
    assert response.json()['revision'] == 2
    old = client.get(f"{PREFIX}/workflows/{original['id']}?revision=1").json()
    assert old['graph'] == GRAPH
    assert old['prompt'] == PROMPT
    assert old['notes'] == 'First draft'
    newest = client.get(f"{PREFIX}/workflows/{original['id']}").json()
    assert newest['prompt'] == changed
    versions = client.get(f"{PREFIX}/workflows/{original['id']}/versions").json()['versions']
    assert [version['revision'] for version in versions] == [2, 1]


def test_stale_save_and_missing_revision_cannot_overwrite_work(bench):
    client, *_ = bench
    saved = save(client)
    body = {'name': 'Next', 'prompt': PROMPT, 'base_revision': 1}
    route = f"{PREFIX}/workflows/{saved['id']}/versions"
    assert client.post(route, json=body).status_code == 200
    assert client.post(route, json=body).status_code == 409
    body.pop('base_revision')
    assert client.post(route, json=body).status_code == 409
    assert client.get(f"{PREFIX}/workflows/{saved['id']}?revision=2").json()['name'] == 'Next'
    assert client.get(f"{PREFIX}/workflows/{saved['id']}?revision=3").status_code == 404


def test_owner_is_checked_for_every_workflow_and_run_operation(bench, monkeypatch):
    client, socket, *_ = bench
    saved, run = start(client)
    socket.calls.clear()
    monkeypatch.setattr(config, 'default_user', 'bob')
    assert client.get(f'{PREFIX}/workflows').json()['workflows'] == []
    assert client.get(f'{PREFIX}/runs').json()['runs'] == []
    for method, route, body in [
        ('get', f"workflows/{saved['id']}", None),
        ('get', f"workflows/{saved['id']}/versions", None),
        ('post', f"workflows/{saved['id']}/versions", {'name': 'steal', 'prompt': PROMPT, 'base_revision': 1}),
        ('delete', f"workflows/{saved['id']}", None),
        ('post', 'runs', {'provider': 'comfy', 'workflow_id': saved['id'], 'revision': 1}),
        ('get', f"runs/{run['id']}", None),
        ('delete', f"runs/{run['id']}", None),
    ]:
        options = {'json': body} if body is not None else {}
        assert getattr(client, method)(f'{PREFIX}/{route}', **options).status_code == 404
    assert not socket.calls, 'a foreign run must not reach the ComfyUI server'


def test_filter_pagination_delete_and_authentication(bench, monkeypatch):
    client, *_ = bench
    saved = save(client, name='京都', project='Japan')
    save(client, name='Berlin', project='Europe')
    assert client.get(f'{PREFIX}/workflows', params={'project': 'Japan', 'q': '京都'}).json()['total'] == 1
    assert len(client.get(f'{PREFIX}/workflows?limit=1').json()['workflows']) == 1
    assert len(client.get(f'{PREFIX}/workflows?offset=1').json()['workflows']) == 1
    assert client.get(f'{PREFIX}/workflows?limit=0').status_code == 422
    monkeypatch.setattr(config, 'auth_token', 'workspace-secret')
    assert client.get(f'{PREFIX}/workflows').status_code == 401
    assert client.get(f'{PREFIX}/connections/comfy/nodes').status_code == 401
    assert client.post(f'{PREFIX}/runs', json={}).status_code == 401
    headers = {'Authorization': 'Bearer workspace-secret'}
    assert client.delete(f"{PREFIX}/workflows/{saved['id']}", headers=headers).status_code == 200
    assert client.get(f"{PREFIX}/workflows/{saved['id']}", headers=headers).status_code == 404
    with workbench.database() as db:
        assert db.execute('SELECT COUNT(*) FROM versions WHERE workflow_id=?', (saved['id'],)).fetchone()[0] == 0


@pytest.mark.parametrize('changes', [
    {'name': '   '}, {'graph': {'wrong': []}}, {'graph': {'nodes': [{}] * 5001}},
    {'prompt': {}}, {'prompt': {'1': {'class_type': 'Source'}}},
    {'graph': None, 'prompt': None},
])
def test_invalid_snapshots_are_rejected_without_database_writes(bench, changes):
    client, *_ = bench
    response = client.post(f'{PREFIX}/workflows', json={'name': 'Draft', 'graph': GRAPH, 'prompt': PROMPT, **changes})
    assert response.status_code == 422
    assert client.get(f'{PREFIX}/workflows').json()['total'] == 0


def test_graph_size_is_bounded_before_storage(bench, monkeypatch):
    client, *_ = bench
    monkeypatch.setattr(workbench, 'MAX_GRAPH', 100)
    response = client.post(f'{PREFIX}/workflows', json={'name': 'Large', 'graph': GRAPH, 'prompt': PROMPT})
    assert response.status_code == 422
    assert '4 MB' in response.text


@pytest.mark.parametrize(('prompt', 'error'), [
    ({'1': {'class_type': 'MissingCustomNode', 'inputs': {}}}, 'missing node type'),
    ({'1': {'class_type': 'Source', 'inputs': {'seed': 4}}}, 'required input'),
    ({'1': {'class_type': 'Source', 'inputs': {'seed': 101, 'mode': 'fast'}}}, 'supported range'),
    ({'1': {'class_type': 'Source', 'inputs': {'seed': True, 'mode': 'fast'}}}, 'expected INT'),
    ({'1': {'class_type': 'Source', 'inputs': {'seed': 1.5, 'mode': 'fast'}}}, 'expected INT'),
    ({'1': {'class_type': 'Source', 'inputs': {'seed': 1, 'mode': 'not-installed'}}}, 'not installed'),
    ({'1': {'class_type': 'Pass', 'inputs': {'image': ['missing', 0]}}}, 'linked node missing'),
    ({'1': {'class_type': 'Pass', 'inputs': {'image': ['1', 8]}}}, 'output slot 8'),
    ({'1': {'class_type': 'Pass', 'inputs': {'image': ['1', -1]}}}, 'output slot -1'),
    ({'1': {'class_type': 'Pass', 'inputs': {'image': ['2', 0]}},
      '2': {'class_type': 'Pass', 'inputs': {'image': ['1', 0]}}}, 'dependency cycle'),
])
def test_graph_inspector_finds_missing_nodes_cycles_and_invalid_inputs(bench, prompt, error):
    client, *_ = bench
    response = client.post(f'{PREFIX}/connections/comfy/validate', json={'prompt': prompt})
    assert response.status_code == 200, response.text
    report = response.json()
    assert not report['valid']
    assert any(error in message for message in report['errors'])


def test_custom_type_mismatch_is_reported_as_a_warning(bench):
    client, socket, *_ = bench
    catalog = copy.deepcopy(CATALOG)
    catalog['Source']['output'] = ['CUSTOM_IMAGE']
    socket.routes[('GET', '/object_info')] = Response(catalog)
    report = client.post(f'{PREFIX}/connections/comfy/validate', json={'prompt': PROMPT}).json()
    assert report['valid']
    assert len(report['warnings']) == 1
    assert 'CUSTOM_IMAGE differs from IMAGE' in report['warnings'][0]


def test_configured_transport_headers_and_redirect_policy_are_used(bench):
    client, socket, *_ = bench
    response = client.get(f'{PREFIX}/connections/comfy/nodes')
    assert response.status_code == 200
    assert response.json()['nodes'] == CATALOG
    assert socket.tor and socket.timeouts == [30]
    method, url, options = socket.calls[0]
    assert method == 'GET' and url == 'http://configured-comfy.invalid/object_info'
    assert options['headers']['Authorization'] == 'Bearer secret-server-key'
    assert options['allow_redirects'] is False
    assert 'secret-server-key' not in response.text


def test_local_only_unknown_and_non_comfy_connections_never_contact_network(bench):
    client, socket, registry, impl = bench
    registry.register(ProviderInfo(id='remote', label='Remote', modalities={Modality.IMAGE}, local=False),
                      {Modality.IMAGE: impl})
    registry.register(ProviderInfo(id='other', label='Other', modalities={Modality.IMAGE}, local=True),
                      {Modality.IMAGE: object()})
    registry.set_defaults(RouteSet(local_only=True))
    assert client.get(f'{PREFIX}/connections/remote/nodes').status_code == 403
    assert client.get(f'{PREFIX}/connections/missing/nodes').status_code == 404
    assert client.get(f'{PREFIX}/connections/other/nodes').status_code == 400
    assert not socket.calls


@pytest.mark.parametrize(('data', 'status'), [(b'not JSON', 200), ({'error': 'Denied'}, 401), ({}, 302)])
def test_bad_upstream_responses_fail_cleanly_without_credentials(bench, data, status):
    client, socket, *_ = bench
    socket.routes[('GET', '/object_info')] = Response(data, status)
    response = client.get(f'{PREFIX}/connections/comfy/nodes')
    assert response.status_code == 502
    assert 'secret-server-key' not in response.text
    assert socket.calls[-1][2]['allow_redirects'] is False


def test_run_uses_exact_saved_revision_and_preserves_editor_metadata(bench):
    client, socket, *_ = bench
    saved, run = start(client)
    assert run['state'] == 'queued'
    queued = [call for call in socket.calls if call[0] == 'POST'][0][2]['json']
    assert queued['prompt'] == saved['prompt']
    assert queued['extra_data']['extra_pnginfo']['workflow'] == GRAPH
    assert queued['client_id'].startswith('omote-')
    # Deleting a source workflow must not erase the run's reproduction snapshot.
    assert client.delete(f"{PREFIX}/workflows/{saved['id']}").status_code == 200
    history(socket)
    result = client.get(f"{PREFIX}/runs/{run['id']}").json()
    assert result['state'] == 'done' and result['name'] == saved['name']
    assert result['revision'] == 1
    assert result['outputs'][0]['filename'] == 'character.png'


def test_editor_only_and_invalid_prompts_cannot_queue(bench):
    client, socket, *_ = bench
    editor_only = save(client, prompt=None)
    response = client.post(f'{PREFIX}/runs', json={'provider': 'comfy', 'workflow_id': editor_only['id'], 'revision': 1})
    assert response.status_code == 400
    invalid = save(client, prompt={'1': {'class_type': 'Uninstalled', 'inputs': {}}})
    response = client.post(f'{PREFIX}/runs', json={'provider': 'comfy', 'workflow_id': invalid['id'], 'revision': 1})
    assert response.status_code == 422
    assert not [call for call in socket.calls if call[0] == 'POST']


def test_only_owned_pending_prompt_can_be_cancelled(bench):
    client, socket, *_ = bench
    _, run = start(client)
    socket.routes[('GET', '/queue')] = Response({'queue_pending': [[0, 'another-user'], [1, 'owned-prompt']]})
    socket.calls.clear()
    response = client.delete(f"{PREFIX}/runs/{run['id']}")
    assert response.status_code == 200
    writes = [call for call in socket.calls if call[0] == 'POST']
    assert len(writes) == 1
    assert writes[0][1].endswith('/queue')
    assert writes[0][2]['json'] == {'delete': ['owned-prompt']}
    socket.routes[('GET', '/queue')] = Response({'queue_running': [[1, 'owned-prompt']]})
    socket.calls.clear()
    assert client.delete(f"{PREFIX}/runs/{run['id']}").status_code == 409
    assert not [call for call in socket.calls if call[0] == 'POST'], 'never use the global interrupt'


def test_history_from_other_prompt_does_not_show_up_in_owned_run(bench):
    client, socket, *_ = bench
    _, run = start(client)
    history(socket, 'another-users-prompt')
    result = client.get(f"{PREFIX}/runs/{run['id']}").json()
    assert result['outputs'] == []
    assert result['state'] == 'unavailable'
    history(socket, status={'status_str': 'error', 'messages': ['custom node failed']})
    result = client.get(f"{PREFIX}/runs/{run['id']}").json()
    assert result['state'] == 'failed'
    assert 'custom node failed' in result['error']


@pytest.mark.parametrize('item', [
    {'filename': '../private.png'}, {'filename': '/private.png'}, {'filename': 'folder/file.png'},
    {'filename': r'folder\file.png'}, {'filename': 'safe.png', 'subfolder': '../private'},
    {'filename': 'safe.png', 'subfolder': '/private'}, {'filename': 'safe.png', 'subfolder': r'C:\private'},
    {'filename': 'safe.png', 'type': 'input'}, {'filename': '..'}, {'filename': ''},
    {'filename': 'safe.png', 'subfolder': 'C:/private'},
])
def test_output_metadata_cannot_request_paths_outside_generated_outputs(item):
    record = {'outputs': {'1': {'images': [item]}}}
    assert workbench.output_files(record) == []


def test_malformed_output_metadata_is_ignored():
    record = {'outputs': {'1': {'images': [None, 'x', 1, {'filename': 3}]}, '2': None, '3': ['not-a-mapping']}}
    assert workbench.output_files(record) == []


def test_import_downloads_owned_output_once_with_provenance(bench, monkeypatch):
    from omote.routers import creative

    client, socket, *_ = bench
    saved, run = start(client)
    history(socket)
    socket.routes[('GET', '/view')] = Response(b'valid-image-bytes')
    added = []

    class Library:
        def add_asset(self, user, data, **kwargs):
            added.append((user, data, kwargs))
            return {'id': 'asset-one'}

    monkeypatch.setattr(creative, 'store', Library())
    response = client.post(f"{PREFIX}/runs/{run['id']}/import")
    assert response.status_code == 200, response.text
    assert response.json() == {'asset_ids': ['asset-one'], 'errors': []}
    assert added[0][0] == 'alice' and added[0][1] == b'valid-image-bytes'
    provenance = added[0][2]['provenance']
    assert provenance['workflow_id'] == saved['id'] and provenance['revision'] == 1
    assert added[0][2]['project'] == 'story'
    view_call = [call for call in socket.calls if call[1].endswith('/view')][0]
    assert view_call[2]['params'] == {'filename': 'character.png', 'subfolder': 'story/scene', 'type': 'output'}
    assert view_call[2]['allow_redirects'] is False
    assert view_call[2]['headers']['Authorization'] == 'Bearer secret-server-key'
    assert client.post(f"{PREFIX}/runs/{run['id']}/import").json()['asset_ids'] == ['asset-one']
    assert len(added) == 1
    assert len([call for call in socket.calls if call[1].endswith('/view')]) == 1


def test_import_rejects_foreign_runs_and_size_overflow(bench, monkeypatch):
    from omote.routers import creative

    client, socket, *_ = bench
    _, run = start(client)
    history(socket)
    monkeypatch.setattr(creative, 'store', object())
    monkeypatch.setattr(config, 'default_user', 'bob')
    socket.calls.clear()
    assert client.post(f"{PREFIX}/runs/{run['id']}/import").status_code == 404
    assert not socket.calls
    monkeypatch.setattr(config, 'default_user', 'alice')
    monkeypatch.setattr(workbench, 'MAX_OUTPUT', 4)
    socket.routes[('GET', '/view')] = Response(b'12345')
    response = client.post(f"{PREFIX}/runs/{run['id']}/import")
    assert response.status_code == 200
    assert response.json()['asset_ids'] == []
    assert 'exceeds' in response.json()['errors'][0]
