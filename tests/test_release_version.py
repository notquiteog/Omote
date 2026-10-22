"""Release validation must follow the package's dynamic version source."""

import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def release_version():
    path = Path(__file__).resolve().parents[1] / 'desktop/ci/version.py'
    spec = importlib.util.spec_from_file_location('release_version', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_repository_versions_match(release_version):
    from omote._version import __version__
    assert set(release_version.versions().values()) == {__version__}


def test_release_tag_mismatch_is_rejected(release_version, monkeypatch, capsys):
    monkeypatch.setattr(release_version.sys, 'argv', ['version.py', 'v999.0.0'])
    with pytest.raises(SystemExit) as failure:
        release_version.main()
    assert failure.value.code == 1
    assert 'does not match the tag' in capsys.readouterr().err


@pytest.mark.parametrize('manifest', ['ide/vscode/package.json', 'desktop/src-tauri/Cargo.lock'])
def test_manifest_version_drift_is_rejected(release_version, tmp_path, monkeypatch, capsys, manifest):
    root = release_version.ROOT
    for name in ('pyproject.toml', 'omote/_version.py', 'desktop/src-tauri/Cargo.toml',
                 'desktop/src-tauri/Cargo.lock', 'desktop/src-tauri/tauri.conf.json', 'ide/vscode/package.json'):
        dest = tmp_path / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text((root / name).read_text())
    path = tmp_path / manifest
    if path.suffix == '.json':
        data = json.loads(path.read_text())
        data['version'] = '999.0.0'
        path.write_text(json.dumps(data))
    else:
        from omote._version import __version__
        before = f'name = "omote-desktop"\nversion = "{__version__}"'
        assert before in path.read_text()
        path.write_text(path.read_text().replace(before, 'name = "omote-desktop"\nversion = "999.0.0"'))
    monkeypatch.setattr(release_version, 'ROOT', tmp_path)
    monkeypatch.setattr(release_version.sys, 'argv', ['version.py'])
    with pytest.raises(SystemExit) as failure:
        release_version.main()
    assert failure.value.code == 1
    assert f'{manifest} says 999.0.0' in capsys.readouterr().err
