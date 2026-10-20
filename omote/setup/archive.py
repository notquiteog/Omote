"""Extract regular files into private staging directories, never archive links."""
import shutil
import tarfile
import zipfile
from pathlib import PurePosixPath

from omote.setup.process import SetupError


def extract(archive, directory):
    total = count = 0
    def destination(name, size):
        nonlocal total, count
        parts = PurePosixPath(name.replace('\\', '/'))
        total += size
        count += 1
        if parts.is_absolute() or '..' in parts.parts or ':' in str(parts):
            raise SetupError('The installation package contains an unsafe path.')
        if total > 512 * 1024**2 or count > 20000:
            raise SetupError('The installation package exceeds its unpacking limit.')
        path = directory.joinpath(*parts.parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as source:
            for member in source.infolist():
                if member.is_dir():
                    continue
                if (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise SetupError('Installation packages may not contain links.')
                path = destination(member.filename, member.file_size)
                with source.open(member) as incoming, path.open('xb') as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
    else:
        with tarfile.open(archive) as source:
            for member in source:
                if member.isdir():
                    continue
                if not member.isfile():
                    raise SetupError('Installation packages may only contain regular files.')
                path = destination(member.name, member.size)
                with source.extractfile(member) as incoming, path.open('xb') as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
