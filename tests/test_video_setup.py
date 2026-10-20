"""Portable one-click setup uses verified archives and never installs wheel code."""
import asyncio
import hashlib
import io
import zipfile

import pytest

from omote.config import config
from omote.creative import assembly
from omote.media import edit, setup
from omote.media.service import Job, MediaService
from omote.media.store import MediaStore
from omote.providers.registry import ProviderRegistry


@pytest.mark.parametrize('system,machine,tag', [
    ('Linux', 'x86_64', 'manylinux2014_x86_64'), ('Linux', 'aarch64', 'manylinux2014_aarch64'),
    ('Darwin', 'arm64', 'macosx_11_0_arm64'), ('Darwin', 'x86_64', 'macosx_10_9'),
    ('Windows', 'AMD64', 'win_amd64'),
])
def test_platform_selects_exact_pinned_archive(system, machine, tag):
    spec = setup.artifact(system, machine)
    assert tag in spec.url
    assert spec.url.startswith('https://files.pythonhosted.org/')
    assert len(spec.sha256) == 64 and spec.size > 20 * 1024**2
    assert spec.kind == 'runtime'


def test_unsupported_architecture_is_explicit():
    assert setup.artifact('Linux', 'mips') is None
    assert setup.artifact('Windows', 'ARM64') is None


def archive():
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as z:
        z.writestr('imageio_ffmpeg/binaries/ffmpeg-test', b'example-executable')
        z.writestr('imageio_ffmpeg/__init__.py', 'raise RuntimeError("must never execute")')
        z.writestr('imageio_ffmpeg-0.6.0.dist-info/LICENSE', 'Example license')
        z.writestr('../../outside', 'must never extract')
    return data.getvalue()


async def test_install_verifies_and_smoke_tests_before_publishing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'data_dir', tmp_path)
    monkeypatch.setattr(edit, 'ffmpeg', lambda: None)
    content = archive()
    spec = setup.artifact().model_copy(update={'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()})
    seen = []

    class Download:
        def __init__(self, store):
            self.store, self.task = store, None

        async def start(self, ident):
            self.store.final(spec).write_bytes(content)
            self.store.update(ident, state='ready')

        async def close(self):
            pass

    async def verify(args, cwd):
        assert not setup.binary_path().exists(), 'Do not publish a binary before it passes the check.'
        assert 'subtitles=check.ass' in args
        assert '-c:v' in args and 'libx264' in args
        assert (cwd / 'check.ass').is_file()
        assert not (cwd / 'imageio_ffmpeg').exists()
        seen.append(args)

    monkeypatch.setattr(setup, 'Downloads', Download)
    monkeypatch.setattr(assembly, 'command', verify)
    job = Job(id='setup', kind='setup', prompt='', provider='local', model='video-tools-setup')
    await setup.install(job, spec)
    assert job.state == 'done', job.error
    assert setup.binary_path().read_bytes() == b'example-executable'
    assert 'Example license' in (setup.binary_path().parent / 'LICENSES.txt').read_text()
    assert len(seen) == 1
    assert not (tmp_path / 'outside').exists()


async def test_corrupt_archive_never_reaches_execution(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'data_dir', tmp_path)
    monkeypatch.setattr(edit, 'ffmpeg', lambda: None)
    spec = setup.artifact()

    class Download:
        def __init__(self, store):
            self.store, self.task = store, None

        async def start(self, ident):
            self.store.final(spec).write_bytes(archive())
            self.store.update(ident, state='ready')

        async def close(self):
            pass
    monkeypatch.setattr(setup, 'Downloads', Download)
    job = Job(id='setup', kind='setup', prompt='', provider='local', model='video-tools-setup')
    await setup.install(job, spec)
    assert job.state == 'failed' and 'checksum' in job.error
    assert not setup.binary_path().exists()


async def test_repeated_setup_reuses_active_job_and_shutdown_cancels(tmp_path, monkeypatch):
    service = MediaService(MediaStore(tmp_path / 'media'), ProviderRegistry())
    started = asyncio.Event()

    async def installing(job, spec):
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(setup, 'install', installing)
    first = setup.start(service)
    second = setup.start(service)
    assert first is second
    await started.wait()
    await service.close()
    assert first.state == 'cancelled'
