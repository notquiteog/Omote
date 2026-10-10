"""Characters, owned files and model validation through the real HTTP surface."""

import base64
import io
import json
import sqlite3
import struct
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omote.config import config
from omote.creative.store import CreativeStore
from omote.creative.validation import InvalidAsset, validate
from omote.media.store import MediaStore
from omote.routers import auth, creative

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aNmcAAAAASUVORK5CYII=')
OBJ = b'v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n'


def model_document():
    binary = struct.pack('<9f', 0, 0, 0, 1, 0, 0, 0, 1, 0)
    return {
        'asset': {'version': '2.0'},
        'buffers': [{'byteLength': len(binary)}],
        'bufferViews': [{'buffer': 0, 'byteOffset': 0, 'byteLength': len(binary)}],
        'accessors': [{'bufferView': 0, 'componentType': 5126, 'count': 3, 'type': 'VEC3',
                       'min': [0, 0, 0], 'max': [1, 1, 0]}],
        'meshes': [{'primitives': [{'attributes': {'POSITION': 0}}]}],
        'nodes': [{'mesh': 0}], 'scenes': [{'nodes': [0]}], 'scene': 0,
    }, binary


def glb(document=None):
    doc, binary = model_document()
    if document is not None:
        doc = document
    encoded = json.dumps(doc).encode()
    encoded += b' ' * (-len(encoded) % 4)
    binary += b'\x00' * (-len(binary) % 4)
    length = 12 + 8 + len(encoded) + 8 + len(binary)
    return (struct.pack('<4sII', b'glTF', 2, length)
            + struct.pack('<II', len(encoded), 0x4E4F534A) + encoded
            + struct.pack('<II', len(binary), 0x004E4942) + binary)


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = CreativeStore(tmp_path / 'creative')
    monkeypatch.setattr(creative, 'store', store)
    monkeypatch.setattr(creative, 'media_store', MediaStore(tmp_path / 'media'))
    monkeypatch.setattr(config, 'default_user', 'owner')
    monkeypatch.setattr(config, 'auth_token', '')
    app = FastAPI()
    app.add_middleware(auth.TokenMiddleware)
    app.include_router(creative.router)
    with TestClient(app) as session:
        yield session


def upload(client, data=PNG, name='portrait.png', **params):
    result = client.post('/api/creative/assets/upload', params={'filename': name, **params}, content=data,
                         headers={'Content-Type': 'application/octet-stream'})
    assert result.status_code == 201, result.text
    return result.json()


def character(client, **fields):
    response = client.post('/api/creative/characters', json={'name': '綾 · Aya', **fields})
    assert response.status_code == 201, response.text
    return response.json()


def test_character_persistence_unicode_search_and_reference_recipe(client):
    asset = upload(client)
    saved = character(client, description='A botanist from Kyoto.', appearance_prompt='Silver braid, green jacket.',
                      consistency_notes='Keep the star-shaped birthmark.', negative_prompt='different eye colour',
                      tags=['京都', 'hero'], project='森の物語', reference_asset_ids=[asset['id']])
    assert client.get('/api/creative/characters', params={'q': '綾'}).json()['total'] == 1
    assert client.get('/api/creative/characters', params={'q': '京都'}).json()['total'] == 1
    assert client.get('/api/creative/characters', params={'project': 'wrong'}).json()['total'] == 0
    recipe = client.get(f'/api/creative/characters/{saved["id"]}/recipe').json()
    assert 'Silver braid' in recipe['prompt'] and 'star-shaped' in recipe['prompt']
    assert recipe['negative_prompt'] == 'different eye colour'
    assert recipe['reference_asset_ids'] == [asset['id']]
    reopened = CreativeStore(creative.store.root)
    assert reopened.get_character('owner', saved['id'])['name'] == '綾 · Aya'
    assert reopened.get_asset('owner', asset['id'])['character_ids'] == [saved['id']]


def test_character_update_is_atomic_and_delete_keeps_original_assets(client):
    first, second = upload(client), upload(client, name='second.png')
    saved = character(client, reference_asset_ids=[first['id']])
    bad = client.put(f'/api/creative/characters/{saved["id"]}',
                     json={'name': 'Changed', 'reference_asset_ids': [second['id'], 'missing']})
    assert bad.status_code == 400
    assert creative.store.get_character('owner', saved['id'])['name'] == saved['name']
    result = client.put(f'/api/creative/characters/{saved["id"]}',
                        json={'name': 'Changed', 'reference_asset_ids': [second['id']]})
    assert result.status_code == 200
    assert creative.store.get_asset('owner', first['id'])['character_ids'] == [saved['id']]
    assert creative.store.get_character('owner', saved['id'])['reference_asset_ids'] == [second['id']]
    assert client.delete(f'/api/creative/characters/{saved["id"]}').status_code == 200
    assert creative.store.get_asset('owner', second['id'])['character_ids'] == []
    assert client.get(second['url']).content == PNG


def test_prepare_returns_generator_media_ids_and_reuses_copies(client):
    reference = upload(client)
    saved = character(client, appearance_prompt='A red scarf', reference_asset_ids=[reference['id']])
    upload(client, OBJ, 'model.obj', character_id=saved['id'])
    endpoint = f'/api/creative/characters/{saved["id"]}/prepare'
    first = client.post(endpoint)
    assert first.status_code == 200, first.text
    recipe = first.json()
    assert len(recipe['reference_media_ids']) == 1
    assert recipe['image'] == recipe['reference_media_ids'][0]
    assert creative.media_store.path(recipe['image']).read_bytes() == PNG
    assert creative.media_store.get(recipe['image']).source == 'upload'
    assert client.post(endpoint).json()['reference_media_ids'] == recipe['reference_media_ids']
    creative.media_store.remove(recipe['image'])
    assert client.post(endpoint).json()['image'] != recipe['image']


def test_generated_asset_is_owned_snapshot_with_redacted_provenance(client):
    media = creative.media_store.add(PNG, kind='image', media_type='image/png', prompt='A character sheet',
                                    provider='comfy', model='sdxl', seed=123,
                                    params={'steps': 30, 'api_key': 'do-not-copy', 'nested': {'token': 'secret'}})
    response = client.post('/api/creative/assets/from-media', json={'media_id': media.id, 'name': 'Sheet'})
    assert response.status_code == 201, response.text
    saved = response.json()
    assert saved['provenance']['seed'] == 123
    assert saved['provenance']['params'] == {'steps': 30, 'nested': {}}
    creative.media_store.remove(media.id)
    assert client.get(saved['url']).content == PNG


def test_asset_edits_filters_pagination_and_full_export(client):
    saved = character(client)
    for index in range(5):
        asset = upload(client, name=f'{index}.png')
        assert client.patch(f'/api/creative/assets/{asset["id"]}', json={
            'name': f'Straße {index}', 'project': 'Tokyo', 'tags': ['夏'], 'character_ids': [saved['id']],
        }).status_code == 200
    response = client.get('/api/creative/assets', params={'q': 'STRASSE', 'limit': 2, 'offset': 2,
                                                         'project': 'Tokyo', 'character_id': saved['id']}).json()
    assert response['total'] == 5 and len(response['assets']) == 2
    assert client.get('/api/creative/assets', params={'kind': 'model'}).json()['total'] == 0
    assert client.get('/api/creative/assets', params={'q': '夏'}).json()['total'] == 5
    exported = client.get('/api/creative/export').json()
    assert len(exported['assets']) == 5 and len(exported['characters']) == 1
    assert 'blob' not in exported['assets'][0] and 'user' not in exported['assets'][0]
    response = client.get('/api/creative/export/archive')
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert len(manifest['assets']) == 5
        assert archive.read(manifest['assets'][0]['archive_path']) == PNG
        assert all(not name.startswith('/') and '..' not in name for name in archive.namelist())


def test_user_ownership_covers_rows_files_associations_export_and_delete(client, monkeypatch):
    asset, saved = upload(client), character(client)
    monkeypatch.setattr(config, 'default_user', 'other')
    for path in (f'/api/creative/assets/{asset["id"]}', asset['url'],
                 f'/api/creative/characters/{saved["id"]}', f'/api/creative/characters/{saved["id"]}/recipe'):
        assert client.get(path).status_code == 404
    assert client.get('/api/creative/assets').json()['total'] == 0
    assert client.get('/api/creative/export').json()['assets'] == []
    assert client.delete(f'/api/creative/assets/{asset["id"]}').status_code == 404
    response = client.post('/api/creative/characters', json={'name': 'Other', 'reference_asset_ids': [asset['id']]})
    assert response.status_code == 400
    response = client.post('/api/creative/assets/upload', params={'filename': 'a.png', 'character_id': saved['id']}, content=PNG)
    assert response.status_code == 400


def test_deleting_asset_removes_blob_and_references(client):
    asset = upload(client)
    path = creative.store.asset_path('owner', asset['id'])
    saved = character(client, reference_asset_ids=[asset['id']])
    assert client.delete(f'/api/creative/assets/{asset["id"]}').status_code == 200
    assert not path.exists()
    assert creative.store.get_character('owner', saved['id'])['reference_asset_ids'] == []
    assert client.get(asset['url']).status_code == 404


@pytest.mark.parametrize('name', ['../../x.png', 'x/y.png', 'x\\y.png', '.private.png', 'photo.svg', 'a.html', 'bad\x00.png'])
def test_unsafe_filenames_are_rejected_before_writing(client, name):
    response = client.post('/api/creative/assets/upload', params={'filename': name}, content=PNG)
    assert response.status_code in (400, 422)
    assert list(creative.store.blobs.iterdir()) == []


def test_upload_is_bounded_by_declared_and_streamed_bytes(client, monkeypatch):
    monkeypatch.setattr(creative, 'MAX_UPLOAD', 10)
    for body in (PNG, iter([b'a' * 8, b'b' * 8])):
        response = client.post('/api/creative/assets/upload', params={'filename': 'test.png'}, content=body)
        assert response.status_code == 413
    assert list(creative.store.blobs.iterdir()) == []


def test_headers_wrong_formats_and_symlink_escape(client, tmp_path):
    asset = upload(client)
    response = client.get(asset['url'])
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert response.headers['content-type'] == 'image/png'
    assert response.headers['content-disposition'].startswith('inline')
    assert client.get(asset['download_url']).headers['content-disposition'].startswith('attachment')
    assert client.post('/api/creative/assets/upload', params={'filename': 'fake.png'}, content=b'<script>bad</script>').status_code == 400
    path = creative.store.asset_path('owner', asset['id'])
    path.unlink()
    secret = tmp_path / 'private.txt'
    secret.write_text('secret')
    path.symlink_to(secret)
    assert client.get(asset['url']).status_code == 404
    assert client.delete(f'/api/creative/assets/{asset["id"]}').status_code == 200
    assert secret.read_text() == 'secret'


def test_all_library_endpoints_are_behind_existing_auth(client, monkeypatch):
    monkeypatch.setattr(config, 'auth_token', 'library-secret')
    for path in ('/api/creative/characters', '/api/creative/assets', '/api/creative/export', '/api/creative/capabilities'):
        assert client.get(path).status_code == 401
        assert client.get(path, headers={'Authorization': 'Bearer library-secret'}).status_code == 200


def test_accepts_valid_self_contained_glb_gltf_obj_and_stl(client):
    doc, binary = model_document()
    doc['buffers'][0]['uri'] = 'data:application/octet-stream;base64,' + base64.b64encode(binary).decode()
    samples = [('mesh.glb', glb()), ('mesh.gltf', json.dumps(doc).encode()), ('mesh.obj', OBJ),
               ('mesh.stl', b'Omote'.ljust(80, b' ') + struct.pack('<I', 1) + b'\x00' * 50)]
    for name, data in samples:
        asset = upload(client, data, name)
        assert asset['kind'] == 'model'
        assert client.get(asset['url']).content == data


@pytest.mark.parametrize('uri', ['https://evil.test/track.png', 'http://localhost:8000/secret', 'file:///etc/passwd',
                                  '../secret.bin', '//evil.test/a', 'data:image/svg+xml;base64,PHN2Zz4=',
                                  'data:image/png;base64,invalid==='])
def test_gltf_cannot_fetch_external_buffers_textures_or_active_content(uri):
    doc, _ = model_document()
    doc['images'] = [{'uri': uri}]
    with pytest.raises(InvalidAsset):
        validate(glb(doc), 'mesh.glb')


@pytest.mark.parametrize('content', [b'glTF', glb()[:-1], b'{}', b'v 0 0 0\nmtllib https://evil.test/x\n',
                                    b'v nan 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n'])
def test_truncated_models_and_unsafe_obj_are_rejected(content):
    with pytest.raises(InvalidAsset):
        validate(content, 'mesh.obj' if content.startswith(b'v ') else 'mesh.glb')


def test_unknown_required_model_decoder_is_reported():
    doc, _ = model_document()
    doc['extensionsRequired'] = ['KHR_draco_mesh_compression']
    with pytest.raises(InvalidAsset, match='uncompressed GLB'):
        validate(glb(doc), 'mesh.glb')


def test_invalid_associations_leave_no_files_or_rows(client):
    response = client.post('/api/creative/assets/upload', params={'filename': 'a.png', 'character_id': 'missing'}, content=PNG)
    assert response.status_code == 400
    assert list(creative.store.blobs.iterdir()) == []
    assert client.get('/api/creative/assets').json()['total'] == 0


def test_no_arbitrary_metadata_mutations_or_null_name(client):
    asset = upload(client)
    endpoint = f'/api/creative/assets/{asset["id"]}'
    assert client.patch(endpoint, json={'blob': '../../private'}).status_code == 422
    assert client.patch(endpoint, json={'name': None}).status_code == 400
    assert client.patch(endpoint, json={'name': '   '}).status_code == 400
    assert client.get(endpoint).json()['name'] == 'portrait.png'


@pytest.mark.parametrize('field,value', [('asset', []), ('buffers', 'bad'), ('images', [None]),
                                         ('meshes', None), ('asset', None)])
def test_malformed_model_schemas_fail_as_user_errors(client, field, value):
    doc, _ = model_document()
    doc[field] = value
    response = client.post('/api/creative/assets/upload', params={'filename': 'bad.glb'}, content=glb(doc))
    assert response.status_code == 400
    assert list(creative.store.blobs.iterdir()) == []


def test_reference_order_survives_reopen_and_asset_metadata_edits(client):
    refs = [upload(client, name=f'{i}.png')['id'] for i in range(3)]
    refs.sort(reverse=True)
    saved = character(client, reference_asset_ids=refs)
    assert saved['reference_asset_ids'] == refs
    reopened = CreativeStore(creative.store.root)
    assert reopened.get_character('owner', saved['id'])['reference_asset_ids'] == refs
    response = client.patch(f'/api/creative/assets/{refs[0]}', json={'name': 'Primary', 'character_ids': [saved['id']]})
    assert response.status_code == 200
    prepared = client.post(f'/api/creative/characters/{saved["id"]}/prepare').json()
    assert prepared['reference_asset_ids'] == refs
    assert creative.media_store.get(prepared['image']).params['creative_asset_id'] == refs[0]


@pytest.mark.parametrize('face', ['f 1 2 50000', 'f -4 -2 -1', 'f 0 1 2'])
def test_obj_faces_cannot_reference_nonexistent_vertices(face):
    with pytest.raises(InvalidAsset):
        validate(('v 0 0 0\nv 1 0 0\nv 0 1 0\n' + face + '\n').encode(), 'model.obj')


def test_associated_artwork_is_not_automatically_an_identity_reference(client):
    saved = character(client)
    image = upload(client, character_id=saved['id'])
    model = upload(client, OBJ, 'artwork.obj', character_id=saved['id'])
    current = creative.store.get_character('owner', saved['id'])
    assert current['reference_asset_ids'] == []
    assert creative.store.list_assets('owner', character_id=saved['id'])['total'] == 2
    response = client.put(f'/api/creative/characters/{saved["id"]}', json={
        'name': saved['name'], 'reference_asset_ids': [image['id']],
    })
    assert response.status_code == 200
    assert response.json()['reference_asset_ids'] == [image['id']]
    assert creative.store.get_asset('owner', model['id'])['character_ids'] == [saved['id']]
    bad = client.put(f'/api/creative/characters/{saved["id"]}', json={
        'name': 'Invalid change', 'reference_asset_ids': [model['id']],
    })
    assert bad.status_code == 400
    assert creative.store.get_character('owner', saved['id'])['name'] == saved['name']
    # Removing a general association must also remove its selected identity reference.
    response = client.patch(f'/api/creative/assets/{image["id"]}', json={'character_ids': []})
    assert response.status_code == 200
    assert creative.store.get_character('owner', saved['id'])['reference_asset_ids'] == []


def test_existing_catalogue_migration_preserves_only_image_references_once(client):
    saved = character(client)
    image = upload(client, character_id=saved['id'])
    model = upload(client, OBJ, 'existing.obj', character_id=saved['id'])
    with sqlite3.connect(creative.store.database) as db:
        db.execute('DROP TABLE character_references')
        db.execute('PRAGMA user_version=0')
    migrated = CreativeStore(creative.store.root)
    assert migrated.get_character('owner', saved['id'])['reference_asset_ids'] == [image['id']]
    assert migrated.get_asset('owner', model['id'])['character_ids'] == [saved['id']]
    migrated.save_character('owner', {'name': saved['name'], 'reference_asset_ids': []}, saved['id'])
    # Reopening must never promote all associated images back into references.
    again = CreativeStore(creative.store.root)
    assert again.get_character('owner', saved['id'])['reference_asset_ids'] == []
    assert again.get_asset('owner', image['id'])['character_ids'] == [saved['id']]
