"""Bounded, self-contained assets: validation never fetches a URL or loads a plugin."""

from __future__ import annotations

import base64
import binascii
import json
import math
import re
import struct
from pathlib import PurePosixPath
from typing import Any

MAX_UPLOAD = 128 * 1024 * 1024
MAX_JSON = 16 * 1024 * 1024
TYPES = {
    '.png': ('image', 'image/png'), '.jpg': ('image', 'image/jpeg'),
    '.jpeg': ('image', 'image/jpeg'), '.webp': ('image', 'image/webp'),
    '.gif': ('image', 'image/gif'), '.mp4': ('video', 'video/mp4'),
    '.webm': ('video', 'video/webm'), '.glb': ('model', 'model/gltf-binary'),
    '.gltf': ('model', 'model/gltf+json'), '.obj': ('model', 'model/obj'),
    '.stl': ('model', 'model/stl'),
}


class InvalidAsset(ValueError):
    """The proposed file is unsupported, malformed or not self-contained."""


def filename(value: str) -> str:
    if (not value or len(value) > 200 or '/' in value or '\\' in value
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or value.startswith('.') or PurePosixPath(value).suffix.lower() not in TYPES):
        raise InvalidAsset('Use a plain filename ending in PNG, JPG, WEBP, GIF, MP4, WEBM, GLB, GLTF, OBJ or STL.')
    return value


def gltf_document(data: bytes) -> dict[str, Any]:
    if len(data) > MAX_JSON:
        raise InvalidAsset('The glTF JSON document exceeds 16 MB; use a binary GLB for large models.')
    try:
        document = json.loads(data)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise InvalidAsset('The glTF JSON document is invalid.') from exc
    if (not isinstance(document, dict) or not isinstance(document.get('asset'), dict)
            or document['asset'].get('version') != '2.0'):
        raise InvalidAsset('Only glTF 2.0 is supported.')
    for field in ('meshes', 'buffers', 'bufferViews', 'accessors', 'images', 'textures', 'materials', 'nodes', 'scenes'):
        values = document.get(field, [])
        if not isinstance(values, list) or any(not isinstance(value, dict) for value in values):
            raise InvalidAsset(f'The model {field} must be an array of objects.')
    for resource in document.get('images', []):
        if 'uri' in resource and (not isinstance(resource['uri'], str) or not resource['uri'].startswith('data:image/')):
            raise InvalidAsset('Image textures must use embedded image data or a GLB buffer view.')
        if 'mimeType' in resource and resource['mimeType'] not in ('image/png', 'image/jpeg', 'image/webp'):
            raise InvalidAsset('Only PNG, JPEG and WEBP textures are supported.')
    pending = [document]
    visited = 0
    decoded = 0
    while pending:
        item = pending.pop()
        visited += 1
        if visited > 250_000:
            raise InvalidAsset('The model JSON has too many objects.')
        if isinstance(item, dict):
            for key, value in item.items():
                if key.lower() in ('uri', 'url'):
                    if not isinstance(value, str) or not re.fullmatch(
                        r'data:(?:image/(?:png|jpeg|webp)|application/(?:octet-stream|gltf-buffer));base64,[A-Za-z0-9+/=\r\n]*',
                        value,
                    ):
                        raise InvalidAsset('External model resources are disabled. Embed all buffers and textures, preferably in GLB.')
                    try:
                        payload = base64.b64decode(value.split(',', 1)[1].replace('\n', '').replace('\r', ''), validate=True)
                    except (ValueError, binascii.Error) as exc:
                        raise InvalidAsset('An embedded model resource contains invalid base64.') from exc
                    decoded += len(payload)
                    if decoded > MAX_UPLOAD:
                        raise InvalidAsset('Embedded model resources exceed the upload limit.')
                pending.append(value)
        elif isinstance(item, list):
            pending.extend(item)
    if not isinstance(document.get('meshes'), list) or not document['meshes']:
        raise InvalidAsset('The model must contain a mesh.')
    # Unknown loader extensions may create their own resource-loading behavior.
    allowed = {
        'KHR_materials_unlit', 'KHR_materials_clearcoat', 'KHR_materials_ior',
        'KHR_materials_iridescence', 'KHR_materials_sheen', 'KHR_materials_specular',
        'KHR_materials_transmission', 'KHR_materials_volume', 'KHR_materials_emissive_strength',
        'KHR_texture_transform', 'KHR_mesh_quantization', 'KHR_lights_punctual',
        'EXT_mesh_gpu_instancing',
    }
    required = document.get('extensionsRequired', [])
    if not isinstance(required, list) or any(not isinstance(e, str) or e not in allowed for e in required):
        raise InvalidAsset('This model needs an unsupported decoder extension. Export an uncompressed GLB.')
    return document


def validate(data: bytes, name: str) -> tuple[str, str]:
    name = filename(name)
    if not data:
        raise InvalidAsset('The upload is empty.')
    if len(data) > MAX_UPLOAD:
        raise InvalidAsset('The upload exceeds 128 MB.')
    ext = PurePosixPath(name).suffix.lower()
    kind, mime = TYPES[ext]
    valid = True
    if ext == '.png':
        valid = len(data) >= 33 and data[:8] == b'\x89PNG\r\n\x1a\n' and data[12:16] == b'IHDR'
    elif ext in ('.jpg', '.jpeg'):
        valid = data.startswith(b'\xff\xd8\xff') and len(data) >= 8
    elif ext == '.webp':
        valid = data.startswith(b'RIFF') and data[8:12] == b'WEBP' and len(data) >= 20
    elif ext == '.gif':
        valid = data[:6] in (b'GIF87a', b'GIF89a') and len(data) >= 13
    elif ext == '.mp4':
        valid = data[4:8] == b'ftyp' and len(data) >= 16
    elif ext == '.webm':
        valid = data.startswith(b'\x1aE\xdf\xa3') and len(data) >= 8
    elif ext == '.gltf':
        doc = gltf_document(data)
        if any(not isinstance(b, dict) or 'uri' not in b for b in doc.get('buffers', [])):
            raise InvalidAsset('A .gltf file must embed every buffer as a data URI; use GLB for binary buffers.')
    elif ext == '.glb':
        if len(data) < 20 or data[:4] != b'glTF':
            raise InvalidAsset('The GLB header is invalid.')
        version, length = struct.unpack_from('<II', data, 4)
        if version != 2 or length != len(data):
            raise InvalidAsset('The GLB version or declared length is invalid.')
        offset, chunks = 12, []
        while offset < len(data):
            if len(data) - offset < 8:
                raise InvalidAsset('The GLB chunk header is truncated.')
            size, chunk_type = struct.unpack_from('<II', data, offset)
            if size % 4 or size > len(data) - offset - 8:
                raise InvalidAsset('The GLB chunk length is invalid.')
            chunks.append((chunk_type, data[offset + 8:offset + 8 + size]))
            offset += 8 + size
        if not chunks or chunks[0][0] != 0x4E4F534A or len(chunks) > 2:
            raise InvalidAsset('GLB must begin with one JSON chunk followed by an optional binary chunk.')
        if len(chunks) == 2 and chunks[1][0] != 0x004E4942:
            raise InvalidAsset('The GLB binary chunk is invalid.')
        doc = gltf_document(chunks[0][1].rstrip(b' \x00'))
        binary_size = len(chunks[1][1]) if len(chunks) == 2 else 0
        for buffer in doc.get('buffers', []):
            if not isinstance(buffer, dict) or not isinstance(buffer.get('byteLength'), int):
                raise InvalidAsset('The model buffer description is invalid.')
            if 'uri' not in buffer and not 0 <= buffer['byteLength'] <= binary_size:
                raise InvalidAsset('The GLB binary buffer is missing or truncated.')
    elif ext == '.obj':
        try:
            text = data.decode('utf-8-sig')
        except UnicodeError as exc:
            raise InvalidAsset('OBJ must be UTF-8 text.') from exc
        vertices = 0
        faces = 0
        maximum_index = 0
        for line in text.splitlines():
            parts = line.split()
            if not parts or parts[0].startswith('#'):
                continue
            if parts[0].lower() in ('mtllib', 'call', 'csh') or parts[0].lower().startswith('map_'):
                raise InvalidAsset('External OBJ material files and commands are disabled. Use a self-contained GLB for textures.')
            if parts[0] == 'v':
                try:
                    coords = [float(v) for v in parts[1:]]
                except ValueError as exc:
                    raise InvalidAsset('OBJ vertex coordinates must be numeric.') from exc
                if not 3 <= len(coords) <= 7 or not all(math.isfinite(c) for c in coords):
                    raise InvalidAsset('OBJ vertex coordinates are invalid.')
                vertices += 1
            if parts[0] == 'f':
                faces += 1
                if len(parts) < 4:
                    raise InvalidAsset('OBJ faces need at least three vertices.')
                for index in parts[1:]:
                    try:
                        value = int(index.split('/')[0])
                    except ValueError as exc:
                        raise InvalidAsset('OBJ face indices are invalid.') from exc
                    if value == 0 or value < -vertices:
                        raise InvalidAsset('OBJ face indices must point to existing vertices.')
                    maximum_index = max(maximum_index, value)
        valid = vertices >= 3 and faces > 0 and maximum_index <= vertices
    elif ext == '.stl':
        if len(data) >= 84 and len(data) == 84 + struct.unpack_from('<I', data, 80)[0] * 50:
            valid = struct.unpack_from('<I', data, 80)[0] > 0
        else:
            try:
                text = data.decode('ascii')
            except UnicodeError as exc:
                raise InvalidAsset('The STL is neither binary nor ASCII.') from exc
            valid = text.lstrip().startswith('solid') and 'endsolid' in text and 'facet normal' in text
            vertex_lines = [line.split() for line in text.splitlines() if line.strip().startswith('vertex ')]
            valid = valid and len(vertex_lines) >= 3 and len(vertex_lines) % 3 == 0
            for line in vertex_lines:
                try:
                    valid = valid and len(line) == 4 and all(math.isfinite(float(x)) for x in line[1:])
                except ValueError:
                    valid = False
    if not valid:
        raise InvalidAsset(f'The file does not contain a valid {ext[1:].upper()} header or mesh.')
    return kind, mime
