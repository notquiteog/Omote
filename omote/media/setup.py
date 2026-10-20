"""One-click, per-user video tools. No shell, pip, administrator account or Python install needed."""
from __future__ import annotations

import asyncio
import os
import platform
import shutil
import tempfile
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from omote.config import config
from omote.local_models.catalog import Artifact
from omote.local_models.downloads import Downloads, DownloadStore, ModelError, checksum
from omote.media.service import Job

# Published imageio-ffmpeg 0.6.0 wheels, pinned to their PyPI SHA-256 digests.
# Only the bundled FFmpeg executable and notices are extracted, never Python code.
BUILDS = {
    ('linux', 'x86_64'): (
        'manylinux2014_x86_64', 'a0/2d/43c8522a2038e9d0e7dbdf3a61195ecc31ca576fb1527a528c877e87d973',
        29498237, 'c7e46fcec401dd990405049d2e2f475e2b397779df2519b544b8aab515195282'),
    ('linux', 'aarch64'): (
        'manylinux2014_aarch64', '33/e7/1925bfbc563c39c1d2e82501d8372734a5c725e53ac3b31b4c2d081e895b',
        25632706, '1d47bebd83d2c5fc770720d211855f208af8a596c82d17730aa51e815cdee6dc'),
    ('darwin', 'aarch64'): (
        'macosx_11_0_arm64', '40/5c/f3d8a657d362cc93b81aab8feda487317da5b5d31c0e1fdfd5e986e55d17',
        21113891, 'b1ae3173414b5fc5f538a726c4e48ea97edc0d2cdc11f103afee655c463fa742'),
    ('darwin', 'x86_64'): (
        'macosx_10_9_intel.macosx_10_9_x86_64', 'da/58/87ef68ac83f4c7690961bce288fd8e382bc5f1513860fc7f90a9c1c1c6bf',
        24932969, '9d2baaf867088508d4a3458e61eeb30e945c4ad8016025545f66c4b5aaef0a61'),
    ('windows', 'x86_64'): (
        'win_amd64', '2c/c6/fa760e12a2483469e2bf5058c5faff664acf66cadb4df2ad6205b016a73d',
        31246824, '02fa47c83703c37df6bfe4896aab339013f62bf02c5ebf2dce6da56af04ffc0a'),
}


def artifact(system=None, machine=None):
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    machine = {'amd64': 'x86_64', 'arm64': 'aarch64'}.get(machine, machine)
    build = BUILDS.get((system, machine))
    if not build:
        return None
    tag, folder, size, digest = build
    return Artifact(id=f'video-tools-060-{system}-{machine.replace("_", "-")}', label='Video export tools',
                    kind='runtime', size=size, sha256=digest,
                    url=f'https://files.pythonhosted.org/packages/{folder}/imageio_ffmpeg-0.6.0-py3-none-{tag}.whl')


def binary_path():
    return config.data_dir / 'tools' / 'video' / ('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')


def extract(archive, directory):
    with zipfile.ZipFile(archive) as wheel:
        candidates = [m for m in wheel.infolist() if
                      PurePosixPath(m.filename).parts[:-1] == ('imageio_ffmpeg', 'binaries')
                      and PurePosixPath(m.filename).name.startswith('ffmpeg-') and not m.is_dir()]
        if len(candidates) != 1 or not 0 < candidates[0].file_size <= 256 * 1024**2:
            raise ModelError('The video-tools package did not contain a supported executable.')
        target = directory / ('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')
        with wheel.open(candidates[0]) as source, target.open('wb') as output:
            shutil.copyfileobj(source, output)
        target.chmod(0o755)
        notices = [m for m in wheel.infolist() if any(word in PurePosixPath(m.filename).name.lower()
                   for word in ('license', 'copying')) and 0 < m.file_size < 1024**2]
        (directory / 'LICENSES.txt').write_text('\n\n'.join(
            m.filename + '\n' + wheel.read(m).decode(errors='replace') for m in notices))
    return target


def start(service):
    from omote.media.edit import ffmpeg
    for job in service.jobs.values():
        if job.model == 'video-tools-setup' and job.task and not job.task.done():
            return job
    spec = artifact()
    if not spec and not ffmpeg():
        raise ModelError('Automatic video-tools setup is not available for this processor yet. Ask the agent to install FFmpeg.')
    job = Job(id=uuid.uuid4().hex[:16], kind='setup', prompt='Set up video export',
              provider='local', model='video-tools-setup')
    service._prune()
    service.jobs[job.id] = job
    job.task = asyncio.create_task(install(job, spec))

    def done(_):
        if job.state in {'queued', 'running'}:
            job.state = 'cancelled'
            job.finished_at = time.time()
    job.task.add_done_callback(done)
    return job


async def install(job, spec):
    from omote.creative.assembly import caption_file, command
    from omote.media.edit import ffmpeg
    transfers = store = None
    try:
        job.state = 'running'
        if ffmpeg():
            job.state, job.note, job.progress = 'done', 'Video export is ready', 1
            return
        destination = binary_path()
        destination.parent.mkdir(parents=True, exist_ok=True)
        store = DownloadStore(config.data_dir / 'tools' / 'downloads')
        store.add(spec)
        if store.get(spec.id)['state'] == 'ready' and not store.final(spec).is_file():
            store.update(spec.id, state='available', downloaded=0)
        transfers = Downloads(store)
        job.note = 'Downloading video tools. You can keep using Omote.'
        await transfers.start(spec.id)
        if transfers.task:
            await transfers.task
        row = store.get(spec.id)
        if row['state'] != 'ready':
            raise ModelError(row['error'] or 'Video tools did not finish downloading. Try again to resume.')
        if await asyncio.to_thread(checksum, store.final(spec)) != spec.sha256:
            store.final(spec).unlink(missing_ok=True)
            store.update(spec.id, state='error', downloaded=0, etag='')
            raise ModelError('Video-tools checksum did not match. The damaged download was removed. Try setup again.')
        job.note = 'Checking video export and captions'
        with tempfile.TemporaryDirectory(prefix='verify-', dir=destination.parent) as scratch:
            directory = await asyncio.to_thread(Path(scratch).resolve)
            binary = extract(store.final(spec), directory)
            caption_file(directory / 'check.ass', 'Ready', 1, 64, 64)
            await command([str(binary), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                           '-f', 'lavfi', '-i', 'color=c=black:s=64x64:r=30', '-vf', 'subtitles=check.ass',
                           '-frames:v', '1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', 'check.mp4'], directory)
            binary.replace(destination)
            (directory / 'LICENSES.txt').replace(destination.parent / 'LICENSES.txt')
        job.state, job.note, job.progress = 'done', 'Video export is ready', 1
    except asyncio.CancelledError:
        job.state, job.note = 'cancelled', 'Setup cancelled. Choose Set up video export to resume.'
        raise
    except Exception as exc:
        job.state, job.error = 'failed', str(exc)
    finally:
        if transfers:
            await transfers.close()
        if store:
            store.db.close()
        job.finished_at = time.time()
