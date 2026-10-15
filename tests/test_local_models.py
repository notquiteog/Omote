"""Real local HTTP transfers, persistent partials, verified files and owned processes."""

import asyncio
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
from types import SimpleNamespace

import pytest
from aiohttp import web
from fastapi import FastAPI
from starlette.testclient import TestClient

from omote.agent.approval import ApprovalPolicy, Mode
from omote.agent.session import AgentSession
from omote.agent.tools.local_models import LocalModelsTool
from omote.config import config
from omote.local_models import downloads
from omote.local_models.catalog import GIB, Artifact, runtime_artifact
from omote.local_models.downloads import Downloads, DownloadStore, ModelError, PublicResolver
from omote.local_models.runtime import PROVIDER_ID, Runtime, install_archive
from omote.local_models.service import LocalModels
from omote.protocol.agent import Risk, ToolCall
from omote.providers.base import ChatRequest, Message, Modality, ProviderRefused, StreamText, TextBlock
from omote.providers.registry import ProviderRegistry
from omote.routers import local_models
from omote.routers.auth import TokenMiddleware
from tests.test_agent import ScriptedProvider
from tests.test_system import ctx

DATA = b'GGUF\x03\x00\x00\x00' + bytes(range(256)) * 8192


@pytest.fixture
async def source():
    state = {'requests': [], 'ignore_range': False, 'wrong_range': False, 'etag': '"v1"', 'status': 200,
             'data': DATA, 'redirect': None, 'short': False}

    async def serve(request):
        state['requests'].append(dict(request.headers))
        if state['redirect']:
            raise web.HTTPFound(state['redirect'])
        if state['status'] != 200:
            return web.Response(status=state['status'])
        offset = 0
        if request.headers.get('Range') and not state['ignore_range']:
            offset = int(request.headers['Range'].split('=')[1].split('-')[0])
        headers = {'ETag': state['etag'], 'Content-Length': str(len(state['data']) - offset)}
        if offset:
            start = offset + 1 if state['wrong_range'] else offset
            headers['Content-Range'] = f'bytes {start}-{len(state["data"]) - 1}/{len(state["data"])}'
        response = web.StreamResponse(status=206 if offset else 200, headers=headers)
        await response.prepare(request)
        try:
            end = len(state['data']) // 2 if state['short'] else len(state['data'])
            for position in range(offset, end, 32768):
                await response.write(state['data'][position:min(position + 32768, end)])
                await asyncio.sleep(.002)
            if state['short']:
                request.transport.close()
            else:
                await response.write_eof()
        except (ConnectionResetError, RuntimeError):
            pass
        return response

    app = web.Application()
    app.router.add_get('/model', serve)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    state['url'] = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/model'
    yield state
    await runner.cleanup()


def artifact(source, **values):
    return Artifact(**{'id': 'test-model', 'label': 'Test model', 'url': source['url'], 'size': len(source['data']),
                       'sha256': hashlib.sha256(source['data']).hexdigest(), **values})


@pytest.fixture
def transfers(tmp_path, source):
    store = DownloadStore(tmp_path)
    spec = artifact(source)
    store.add(spec)
    manager = Downloads(store, allow_loopback=True)
    yield manager, spec
    store.db.close()


async def finish(manager):
    async with asyncio.timeout(10):
        await manager.task


async def partial(manager, spec):
    async with asyncio.timeout(5):
        while not manager.store.partial(spec).exists() or manager.store.partial(spec).stat().st_size < 32768:  # noqa: ASYNC110 - observe real disk writes
            await asyncio.sleep(.005)


async def test_cancel_resume_survives_daemon_restart(source, transfers, tmp_path):
    manager, spec = transfers
    await manager.start(spec.id)
    await partial(manager, spec)
    await manager.cancel(spec.id)
    saved = manager.store.partial(spec).stat().st_size
    assert 0 < saved < spec.size and manager.store.get(spec.id)['state'] == 'cancelled'
    # A new store/manager reads the same on-disk partial and validators.
    other_store = DownloadStore(tmp_path)
    other = Downloads(other_store, allow_loopback=True)
    try:
        await other.start(spec.id)
        await finish(other)
        assert source['requests'][-1]['Range'] == f'bytes={saved}-'
        assert source['requests'][-1]['If-Range'] == '"v1"'
        assert other.resumed_from == saved
        assert other_store.get(spec.id)['state'] == 'ready'
        assert other_store.final(spec).read_bytes() == DATA
        assert not other_store.partial(spec).exists()
    finally:
        await other.close()
        other_store.db.close()


def test_catalog_text_updates_preserve_transfer_identity(transfers):
    manager, spec = transfers
    manager.store.update(spec.id, state='cancelled', downloaded=100)
    manager.store.add(spec.model_copy(update={'label': 'Updated label', 'ram_bytes': 4 * GIB}))
    row = manager.store.get(spec.id)
    assert row['state'] == 'cancelled' and row['downloaded'] == 100
    assert row['spec'].label == 'Updated label'
    with pytest.raises(ModelError, match='different download metadata'):
        manager.store.add(spec.model_copy(update={'sha256': '0' * 64}))


async def test_server_ignoring_resume_restarts_instead_of_appending(source, transfers):
    manager, spec = transfers
    manager.store.partial(spec).write_bytes(DATA[:65536])
    source['ignore_range'] = True
    await manager.start(spec.id)
    await finish(manager)
    assert manager.resumed_from == 0
    assert manager.store.final(spec).read_bytes() == DATA


async def test_wrong_content_range_keeps_partial_and_never_promotes(source, transfers):
    manager, spec = transfers
    manager.store.partial(spec).write_bytes(DATA[:65536])
    source['wrong_range'] = True
    await manager.start(spec.id)
    await finish(manager)
    assert manager.store.get(spec.id)['state'] == 'error'
    assert manager.store.partial(spec).stat().st_size == 65536
    assert not manager.store.final(spec).exists()


async def test_bad_checksum_is_removed_and_cannot_be_ready(source, tmp_path):
    store = DownloadStore(tmp_path)
    spec = artifact(source, sha256='0' * 64)
    store.add(spec)
    manager = Downloads(store, allow_loopback=True)
    await manager.start(spec.id)
    await finish(manager)
    assert 'Checksum' in store.get(spec.id)['error']
    assert not store.partial(spec).exists() and not store.final(spec).exists()
    store.db.close()


async def test_short_transfer_is_resumable_and_network_errors_are_reported(source, transfers):
    manager, spec = transfers
    source['short'] = True
    await manager.start(spec.id)
    await finish(manager)
    assert manager.store.get(spec.id)['state'] == 'error'
    saved = manager.store.partial(spec).stat().st_size
    assert 0 < saved < spec.size and not manager.store.final(spec).exists()
    source['short'] = False
    await manager.start(spec.id)
    await finish(manager)
    assert manager.store.get(spec.id)['state'] == 'ready'


async def test_disk_reserve_is_checked_before_and_during_download(monkeypatch, transfers):
    manager, spec = transfers
    free = {'value': 10 * GIB}
    monkeypatch.setattr(downloads.shutil, 'disk_usage', lambda _: SimpleNamespace(free=free['value']))
    free['value'] = 100
    with pytest.raises(ModelError, match='disk space'):
        await manager.start(spec.id)
    assert manager.task is None
    free['value'] = 10 * GIB
    await manager.start(spec.id)
    await partial(manager, spec)
    free['value'] = 100
    await finish(manager)
    assert 'Disk space' in manager.store.get(spec.id)['error']
    assert not manager.store.final(spec).exists()


async def test_second_transfer_cannot_double_allocate_disk(source, transfers):
    manager, spec = transfers
    other = artifact(source, id='second-model')
    manager.store.add(other)
    await manager.start(spec.id)
    with pytest.raises(ModelError, match='Another download'):
        await manager.start(other.id)
    await manager.cancel(spec.id)


async def test_private_urls_and_redirects_are_rejected(source, transfers):
    production = Downloads(transfers[0].store)
    for url in ('http://example.com/model.gguf', 'https://127.0.0.1/model', 'https://user:password@example.com/file'):
        with pytest.raises(ModelError):
            await production.validate_url(url)
    source['redirect'] = 'http://10.0.0.1/model'
    manager, spec = transfers
    await manager.start(spec.id)
    await finish(manager)
    assert manager.store.get(spec.id)['state'] == 'error'
    assert not manager.store.final(spec).exists()


async def test_actual_connection_resolver_rejects_rebound_private_address(monkeypatch):
    import aiohttp

    async def rebound(*args):
        return [{'host': '127.0.0.1'}]

    monkeypatch.setattr(aiohttp.resolver.ThreadedResolver, 'resolve', rebound)
    resolver = PublicResolver()
    try:
        with pytest.raises(ModelError, match='private'):
            await resolver.resolve('public.example')
    finally:
        await resolver.close()


def tar_archive(path, name, content=b'data', link=None):
    with tarfile.open(path, 'w:gz') as out:
        member = tarfile.TarInfo(name)
        if link:
            member.type, member.linkname = tarfile.SYMTYPE, link
            out.addfile(member)
        else:
            member.size = len(content)
            out.addfile(member, io.BytesIO(content))


@pytest.mark.parametrize('name,link', [('../escape', None), ('/escape', None), ('server', '/etc/passwd')])
def test_runtime_archive_cannot_escape_installation(tmp_path, name, link):
    archive = tmp_path / 'bad.tar.gz'
    tar_archive(archive, name, link=link)
    with pytest.raises(ModelError):
        install_archive(archive, tmp_path / 'runtime')
    assert not (tmp_path / 'escape').exists()


def test_runtime_install_requires_server_and_limits_expansion(tmp_path, monkeypatch):
    from omote.local_models import runtime

    archive = tmp_path / 'bad.tar.gz'
    tar_archive(archive, 'llama-server', b'x' * 100)
    monkeypatch.setattr(runtime, 'MAX_UNPACKED', 10)
    with pytest.raises(ModelError, match='unpacking limit'):
        install_archive(archive, tmp_path / 'runtime')


@pytest.fixture
def hardware(monkeypatch):
    from omote.local_models import service

    data = {'memory': {'available_bytes': 16 * GIB}, 'gpu': {'devices': [
        {'memory_total_bytes': 16 * GIB, 'memory_used_bytes': GIB}]}}

    async def snapshot(_):
        return data

    monkeypatch.setattr(service.monitor, 'snapshot', snapshot)
    return data


@pytest.fixture
async def library(tmp_path, hardware):
    manager = LocalModels(tmp_path, ProviderRegistry(), allow_loopback=True)
    yield manager
    await manager.close()


async def test_ram_vram_and_platform_preflight_are_explicit(library, hardware):
    model = library.store.get('qwen3-06b-q8')['spec']
    hardware['memory']['available_bytes'] = 1
    assert not library.compatibility(model, hardware)['ok']
    hardware['memory']['available_bytes'] = 16 * GIB
    hardware['gpu']['devices'][0]['memory_used_bytes'] = 16 * GIB - 1
    assert not library.compatibility(model, hardware, 'gpu')['ok']
    assert library.compatibility(model, hardware, 'cpu')['ok']
    assert runtime_artifact('Linux', 'mips') is None
    assert runtime_artifact('Windows', 'AMD64') is not None
    assert runtime_artifact('Darwin', 'arm64') is not None


async def test_duplicate_daemons_cannot_manage_same_library(library):
    with pytest.raises(ModelError, match='Another Omote process'):
        LocalModels(library.root, ProviderRegistry())


@pytest.mark.parametrize('owned', [False, True])
def test_orphan_recovery_only_stops_process_with_matching_ownership(tmp_path, owned):
    import psutil

    key = 'owned-runtime-test-nonce'
    child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)', key])
    try:
        process = psutil.Process(child.pid)
        record = {'pid': child.pid, 'created': process.create_time(), 'executable': process.exe(),
                  'key': key if owned else 'not-this-process'}
        (tmp_path / 'runtime.json').write_text(json.dumps(record))
        Runtime(tmp_path, ProviderRegistry())
        assert (child.poll() is not None) if owned else child.poll() is None
        assert not (tmp_path / 'runtime.json').exists()
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()


async def test_runtime_repair_reuses_verified_archive(library, source, tmp_path):
    archive = tmp_path / 'fixture.tar.gz'
    tar_archive(archive, 'llama-server', b'#!/bin/sh\necho fixture\n')
    source['data'] = archive.read_bytes()
    spec = artifact(source, id='fixture-runtime', kind='runtime')
    library.runtime_spec = spec
    library.store.add(spec)
    await library.download(spec.id)
    await finish(library.downloads)
    original = library.binary().read_bytes()
    library.binary().write_bytes(b'corrupt executable')
    requests = len(source['requests'])
    await library.repair_runtime()
    await finish(library.downloads)
    assert library.binary().read_bytes() == original
    assert len(source['requests']) == requests


async def test_missing_ready_model_is_reported_and_can_download_again(library, source):
    model = artifact(source)
    library.store.add(model)
    library.store.update(model.id, state='ready', downloaded=model.size)
    row = next(row for row in (await library.snapshot())['models'] if row['id'] == model.id)
    assert row['state'] == 'error' and 'missing' in row['error']
    await library.download(model.id)
    await finish(library.downloads)
    assert library.store.get(model.id)['state'] == 'ready'
    await library.remove(model.id)
    assert not library.store.final(model).exists()


def test_local_model_api_is_authenticated(monkeypatch):
    monkeypatch.setattr(config, 'auth_token', 'model-test-token')
    app = FastAPI()
    app.add_middleware(TokenMiddleware)
    app.include_router(local_models.router)
    client = TestClient(app)
    assert client.get('/api/local-models').status_code == 401
    assert client.post('/api/local-models/x/download', json={'mode': 'gpu'}).status_code == 401
    assert client.post('/api/local-models/runtime/stop').status_code == 401
    assert client.delete('/api/local-models/x').status_code == 401


async def test_managed_context_uses_actual_runtime_window_and_compacts_before_it(tmp_path):
    from omote.local_models.catalog import CONTEXT
    from omote.local_models.runtime import ManagedChat

    provider = ManagedChat(SimpleNamespace(state='stopped'))
    session = AgentSession(root=tmp_path, provider=provider, model='qwen3-06b-q8', tools=[], policy=ApprovalPolicy())
    assert session.context_report().window == CONTEXT
    assert 0 < session.compact_at < CONTEXT


async def test_valid_checksum_with_wrong_format_cannot_be_loaded(source, tmp_path):
    source['data'] = b'not a GGUF model'
    store = DownloadStore(tmp_path)
    spec = artifact(source)
    store.add(spec)
    manager = Downloads(store, allow_loopback=True)
    await manager.start(spec.id)
    await finish(manager)
    assert store.get(spec.id)['state'] == 'error'
    assert 'GGUF' in store.get(spec.id)['error']
    assert not store.final(spec).exists()
    store.db.close()


async def test_model_tool_permissions_are_action_specific_and_read_only_cannot_start(tmp_path):
    tool = LocalModelsTool()
    assert tool.assess({'action': 'list'}, ctx()).risk is Risk.READ
    assert tool.assess({'action': 'download', 'id': 'x'}, ctx()).risk is Risk.NETWORK
    assert tool.assess({'action': 'remove', 'id': 'x'}, ctx()).risk is Risk.DESTRUCTIVE
    assert tool.assess({'action': 'start'}, ctx()).invalid
    assert tool.assess({'action': []}, ctx()).invalid
    session = AgentSession(root=tmp_path, provider=ScriptedProvider([]), model='test', tools=[tool],
                           policy=ApprovalPolicy(mode=Mode.READ_ONLY))
    result = await session.invoke(ToolCall(id='blocked', name=tool.name, arguments={'action': 'start', 'id': 'x'}), 'turn')
    assert result.is_error


FAKE_SERVER = r'''
import json,sys,time,pathlib
from http.server import BaseHTTPRequestHandler,HTTPServer
if '--list-devices' in sys.argv:
    print('Vulkan0: Test GPU (8192 MiB)\nVulkan1: llvmpipe (software)')
    raise SystemExit(0)
port=int(sys.argv[sys.argv.index('--port')+1])
key=pathlib.Path(sys.argv[sys.argv.index('--api-key-file')+1]).read_text().strip()
sys.stdout.write('key=' + key[:20]); sys.stdout.flush(); time.sleep(.03)
sys.stdout.write(key[20:] + '\n'); sys.stdout.flush()
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self):
        if self.headers.get('Authorization') != 'Bearer '+key:
            self.send_response(401); self.end_headers(); return
        self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(b'{"status":"ok"}')
    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length',0)))
        self.send_response(200); self.send_header('Content-Type','text/event-stream'); self.end_headers()
        self.wfile.write(b'data: {"choices":[{"delta":{"content":"hello local"},"finish_reason":null}]}\n\ndata: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
HTTPServer(('127.0.0.1',port), Handler).serve_forever()
'''


def fake_installed_runtime(library, source):
    model = artifact(source)
    library.store.add(model)
    library.store.final(model).write_bytes(DATA)
    library.store.update(model.id, state='ready', downloaded=model.size)
    binary = library.root / 'runtime' / 'fixture' / 'llama-server'
    binary.parent.mkdir(parents=True)
    binary.write_text(f'#!{sys.executable}\n' + FAKE_SERVER)
    binary.chmod(0o755)
    library.manifest.write_text(json.dumps({'id': library.runtime_spec.id, 'path': str(binary.relative_to(library.root)),
                                           'sha256': hashlib.sha256(binary.read_bytes()).hexdigest()}))
    library.store.update(library.runtime_spec.id, state='ready')
    return model


@pytest.mark.skipif(os.name == 'nt', reason='Fixture uses POSIX executable script')
async def test_owned_runtime_start_stop_redacts_keys_and_existing_chat_reconnects(library, source):
    model = fake_installed_runtime(library, source)
    await library.start(model.id)
    await library.runtime.start_task
    assert library.runtime.state == 'running', library.runtime.snapshot()
    import psutil

    assert library.runtime.key not in psutil.Process(library.runtime.process.pid).cmdline()
    key_file = library.runtime.key_file
    assert await library.runtime.devices(library.binary()) == ['Vulkan0']
    assert library.runtime.key not in json.dumps(library.runtime.snapshot())
    proxy = library.runtime.registry.impl(PROVIDER_ID, Modality.CHAT)
    request = ChatRequest(model=model.id, messages=[Message(role='user', content=[TextBlock(text='hi')])])
    events = [event async for event in proxy.stream(request)]
    assert any(isinstance(event, StreamText) and 'hello local' in event.text for event in events)
    with pytest.raises(ModelError, match='Stop this model'):
        await library.remove(model.id)
    process = library.runtime.process
    await library.runtime.stop()
    assert process.returncode is not None
    assert library.runtime.state == 'stopped' and not library.runtime.record.exists()
    assert not key_file.exists()
    with pytest.raises(ProviderRefused, match='stopped'):
        _ = [event async for event in proxy.stream(request)]
    await library.start(model.id)
    await library.runtime.start_task
    events = [event async for event in proxy.stream(request)]
    assert any(isinstance(event, StreamText) for event in events)
    # An existing chat cannot silently use a different newly-loaded model.
    request.model = 'different-model'
    with pytest.raises(ProviderRefused):
        _ = [event async for event in proxy.stream(request)]
    key_file = library.runtime.key_file
    library.runtime.process.kill()
    await library.runtime.pump_task
    assert library.runtime.state == 'error'
    assert not key_file.exists()
