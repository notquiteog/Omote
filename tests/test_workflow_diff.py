"""Version review is local, immutable, bounded, and ownership checked."""

import copy

from omote.config import config
from omote.workflow_diff import compare
from tests.test_workbench import GRAPH, PREFIX, PROMPT, save
from tests.test_workbench import bench as bench


def test_diff_preserves_types_missing_null_and_pointer_keys():
    result = compare(
        {'a/b': {'~': None}, 'seed': 1, 'remove': True}, {'a/b': {'~': '新しい'}, 'seed': True, 'new': None}
    )
    changes = {item['path']: item for item in result['changes']}
    assert changes['/a~1b/~0']['after'] == '"新しい"'
    assert changes['/seed']['kind'] == 'changed'
    assert changes['/new']['before'] is None and changes['/new']['after'] == 'null'
    assert result['counts'] == {'added': 1, 'removed': 1, 'changed': 2}


def test_diff_large_values_and_many_changes_are_bounded():
    result = compare({str(i): 0 for i in range(1000)}, {str(i): 'x' * 4000 for i in range(1000)})
    assert result['total'] == 1000 and result['truncated']
    assert len(result['changes']) == 250
    assert all(len(item['after']) < 2200 for item in result['changes'])


def test_compare_nodes2_metadata_prompt_and_versions_stay_unchanged(bench, monkeypatch):
    client, socket, *_ = bench
    original = save(client, graph=GRAPH, prompt=PROMPT)
    graph, prompt = copy.deepcopy(GRAPH), copy.deepcopy(PROMPT)
    graph['extra']['subgraphs'][0]['nodes'] = [{'id': 'custom', 'widget': {'kind': 'vue', 'value': 'new'}}]
    prompt['1']['inputs']['seed'] = 42
    response = client.post(
        f'{PREFIX}/workflows/{original["id"]}/versions',
        json={
            'name': original['name'],
            'graph': graph,
            'prompt': prompt,
            'base_revision': 1,
        },
    )
    assert response.status_code == 200
    endpoint = f'{PREFIX}/workflows/{original["id"]}/compare?before=1&after=2'
    result = client.get(endpoint).json()
    assert any(
        item['path'] == '/prompt/1/inputs/seed' and item['before'] == '12' and item['after'] == '42'
        for item in result['changes']
    )
    assert any(item['path'].startswith('/graph/extra/subgraphs') for item in result['changes'])
    assert not socket.calls
    assert client.get(f'{PREFIX}/workflows/{original["id"]}?revision=1').json()['graph'] == GRAPH
    assert client.get(f'{PREFIX}/workflows/{original["id"]}?revision=2').json()['prompt'] == prompt
    assert client.get(endpoint.replace('after=2', 'after=9')).status_code == 404
    monkeypatch.setattr(config, 'default_user', 'bob')
    assert client.get(endpoint).status_code == 404
