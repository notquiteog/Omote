"""Reviewed upstream releases. No shell installers or moving source branches."""
import platform

from omote.local_models.catalog import Artifact

UV_VERSION = '0.13.3'
COMFY_REVISION = 'b1a247c9cf9dbfda4b2c0a0c1d50950667eeaa0c'
PLAYWRIGHT_VERSION = '1.64.0'
UV_BUILDS = {
    ('linux', 'x86_64'): ('x86_64-unknown-linux-gnu.tar.gz', 16767662, 'e4f169ec380f1793b898a6f1fcbacd22db25cf7ca0349a6e96ffb85b69998d04'),
    ('linux', 'aarch64'): ('aarch64-unknown-linux-gnu.tar.gz', 15857962, '611ff684f193a84d2fdad29c5f12505d02ece94984c7c982cdde950eba5c95b9'),
    ('darwin', 'x86_64'): ('x86_64-apple-darwin.tar.gz', 17182824, '5c35634957db7ebd9c2e4cc6c66b8979fb2c26a6b0dbd1d214d6acbac4033a89'),
    ('darwin', 'aarch64'): ('aarch64-apple-darwin.tar.gz', 14309841, 'bf1e4fb6f5f5079a2902ad3f3a9e2d337575b226a1378f2449d8404353650608'),
    ('windows', 'x86_64'): ('x86_64-pc-windows-msvc.zip', 15204998, 'bb701bd35b586c45966654ea2703c9bd116a6a4fc0a8ca755bd644c2c41c5ebe'),
    ('windows', 'aarch64'): ('aarch64-pc-windows-msvc.zip', 15479943, 'aeaca1a1b721c1686c09b4aa23d57f938df049bed1b8e534b5bf79a032b8b87b'),
}


def host():
    machine = platform.machine().lower()
    return platform.system().lower(), {'amd64': 'x86_64', 'arm64': 'aarch64'}.get(machine, machine)


def uv_artifact(system=None, machine=None):
    system, machine = (system, machine) if system and machine else host()
    build = UV_BUILDS.get((system, machine))
    if not build:
        raise ValueError('Automatic setup is not available for this operating system and processor.')
    name, size, digest = build
    return Artifact(id='uv-' + UV_VERSION.replace('.', '-') + '-' + system + '-' + machine.replace('_', '-'),
                    kind='runtime', label='Local setup tools', size=size, sha256=digest,
                    url=f'https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/uv-{name}')


COMFY_SOURCE = Artifact(id='comfy-' + COMFY_REVISION, kind='runtime', label='ComfyUI',
    url=f'https://codeload.github.com/Comfy-Org/ComfyUI/zip/{COMFY_REVISION}',
    size=13509383, sha256='14d2ffe3775d656ebe3d9a6f684b5f1874ac89992ca296ee3c4646c14ebe4e1e')

# Resolved from Microsoft's official vc14 links. Pin the resolved download and
# verify its digest; Windows additionally verifies Authenticode before elevation.
VC_BUILDS = {
    'x86_64': (
        'ebdab8e5-1d7b-4d9f-a11b-cbb1720c3b12',
        '843068991daaa1f73ad9f6239bce4d0f6a07a51f18c37ea2a867e9beca71295c', 18731856, 'x64'),
    'aarch64': (
        '6ad63323-a427-4c58-8464-9f1f19f62359',
        'b70ef586669a620a0a30a1156969c05c6a3831dc8f8bc992da75779d2a92f944', 11870816, 'arm64'),
}


def vc_artifact(machine=None):
    revision, digest, size, arch = VC_BUILDS[machine or host()[1]]
    return Artifact(id='msvc-' + digest[:12], kind='runtime', label='Windows generation support',
                    size=size, sha256=digest,
                    url=f'https://download.visualstudio.microsoft.com/download/pr/{revision}/{digest.upper()}/VC_redist.{arch}.exe')


def comfy_host_error():
    if host() == ('darwin', 'x86_64'):
        return ('Local studio needs an Apple silicon Mac with the current generation engine. '
                'On this Intel Mac, connect a ComfyUI server in Connections to use Studio.')
    return ''
