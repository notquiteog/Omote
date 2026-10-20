"""Pinned public artifacts; updates require a reviewed version/checksum change."""

import platform
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

GIB = 1024**3
MARGIN = 512 * 1024**2
VERSION = 'b11563'
CONTEXT = 8192
VRAM_OVERHEAD = 1536 * 1024**2


class Artifact(BaseModel):
    id: str = Field(pattern=r'^[a-z0-9][a-z0-9-]{0,79}$')
    label: str = Field(min_length=1, max_length=100)
    kind: Literal['model', 'runtime', 'safetensors', 'workflow'] = 'model'
    category: Literal['checkpoints', 'loras', 'vae', 'text_encoders', 'diffusion_models', 'controlnet', 'clip_vision', 'upscale_models', 'model_patches'] = 'checkpoints'
    filename: str = Field(default='', pattern=r'^(?:[a-zA-Z0-9][a-zA-Z0-9._-]{0,149}\.safetensors)?$')
    media_kind: Literal['image', 'video'] = 'image'
    url: str = Field(max_length=2048)
    size: int = Field(gt=0, le=1024**4)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    ram_bytes: int = Field(default=0, ge=0)
    note: str = Field(default='', max_length=500)

    @field_validator('url')
    @classmethod
    def valid_url(cls, value):
        parsed = urlsplit(value)
        if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Use a public HTTPS URL without embedded credentials.')
        # HTTP is accepted by the data model for isolated loopback tests.
        # Production transport rejects HTTP and private addresses on every hop.
        return value


CATALOG = [
    Artifact(id='qwen3-17b-q8', label='Qwen3 · 1.7B', size=1834426016,
             url='https://huggingface.co/Qwen/Qwen3-1.7B-GGUF/resolve/90862c4b9d2787eaed51d12237eafdfe7c5f6077/Qwen3-1.7B-Q8_0.gguf',
             sha256='061b54daade076b5d3362dac252678d17da8c68f07560be70818cace6590cb1a',
             ram_bytes=4 * GIB, note='Recommended starter for everyday local chat. Q8_0 · Apache 2.0.'),
    Artifact(id='qwen3-06b-q8', label='Qwen3 · 0.6B', size=639446688,
             url='https://huggingface.co/Qwen/Qwen3-0.6B-GGUF/resolve/23749fefcc72300e3a2ad315e1317431b06b590a/Qwen3-0.6B-Q8_0.gguf',
             sha256='9465e63a22add5354d9bb4b99e90117043c7124007664907259bd16d043bb031',
             ram_bytes=2 * GIB, note='Tiny hardware-test model. Limited reliability with long agent prompts. Q8_0 · Apache 2.0.'),
]

# name, exact bytes, GitHub release asset digest. Vulkan avoids a separate
# CUDA toolkit and works across supported NVIDIA/AMD/Intel drivers.
BUILDS = {
    ('linux', 'x86_64'): ('ubuntu-vulkan-x64.tar.gz', 30274622, '98d991a36793868e02a1ad71348c8163c7e51675d4c707e9e8fd1b548d877b05'),
    ('linux', 'aarch64'): ('ubuntu-vulkan-arm64.tar.gz', 24051541, 'ca80399e19c386bbcf209704b6f6f7b8c031a238a07fef244848b0f0b9512e29'),
    ('windows', 'x86_64'): ('win-vulkan-x64.zip', 32076556, '9259728ceba0f29fd5c9c10df6336618477ca2452ad7e2403e12f04ea81e4f54'),
    ('windows', 'aarch64'): ('win-vulkan-arm64.zip', 24677812, 'f4eff9e4bddce39f7b14041ee9ab6faac8969937de412b4e1ded03574f3de8ba'),
    ('darwin', 'aarch64'): ('macos-arm64.tar.gz', 12105453, '6a5c7da020931d891ab8487800b61b0c097db89a1272c02a6bcb6afd054ea5c2'),
    ('darwin', 'x86_64'): ('macos-x64.tar.gz', 11597171, 'a2335b1f4ae00d7b3f6f2da0880daecae9844a60c886d874f7e963616c29bf81'),
}


def runtime_artifact(system: str | None = None, machine: str | None = None) -> Artifact | None:
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    machine = {'amd64': 'x86_64', 'arm64': 'aarch64'}.get(machine, machine)
    build = BUILDS.get((system, machine))
    if not build:
        return None
    name, size, digest = build
    suffix = re.sub(r'[^a-z0-9-]', '-', f'{system}-{machine}')
    return Artifact(id=f'llama-{VERSION}-{suffix}', label=f'llama.cpp {VERSION}', kind='runtime', size=size,
                    url=f'https://github.com/ggml-org/llama.cpp/releases/download/{VERSION}/llama-{VERSION}-bin-{name}',
                    sha256=digest, note='Metal on Apple Silicon; Vulkan on Linux and Windows. Drivers must support the backend.')
