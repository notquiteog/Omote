"""Actual media files are decoded/probed, including alpha and codec mismatches."""

import json
import shutil
import subprocess

import pytest

from omote.agent.tools.inspect_export import InspectExportTool
from omote.creative.exports import inspect_file
from tests.test_creative_apps import context


async def test_transparent_png_dimensions_checksums_and_mismatches(tmp_path):
    Image = pytest.importorskip('PIL.Image')
    path = tmp_path / 'transparent.png'
    Image.new('RGBA', (96, 64), (40, 180, 120, 0)).save(path)
    tool = InspectExportTool()
    out = await tool.run({'paths': [path.name], 'expected': {'width': 96, 'height': 64, 'alpha': True, 'has_transparency': True}}, context(tmp_path))
    result = json.loads(out.content)
    assert result['ok'] and len(result['exports'][0]['sha256']) == 64
    assert not inspect_file(path, {'width': 192})['ok']
    path.write_bytes(path.read_bytes()[:32])
    assert not inspect_file(path)['ok']
    assert tool.assess({'paths': ['../outside.png']}, context(tmp_path)).invalid


def test_svg_and_unknown_native_files_are_not_misrepresented(tmp_path):
    path = tmp_path / 'drawing.svg'
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 360"><rect width="640" height="360"/></svg>')
    result = inspect_file(path, {'width': 640, 'height': 360, 'kind': 'vector'})
    assert result['ok'] and result['format'] == 'SVG'
    native = tmp_path / 'project.native'
    native.write_bytes(b'native project data')
    result = inspect_file(native)
    assert not result['verified'] and any('Reopen' in warning for warning in result['warnings'])
    path.write_text('<!DOCTYPE svg [<!ENTITY x "bad">]><svg/>')
    assert not inspect_file(path)['ok']


def test_real_video_codec_frame_rate_duration_and_audio(tmp_path):
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg and ffprobe are needed for actual export decoding')
    path = tmp_path / 'delivery.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=c=navy:s=160x96:r=24', '-f', 'lavfi', '-i',
                    'sine=frequency=440:sample_rate=48000', '-t', '1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(path)], check=True)
    result = inspect_file(path, {'width': 160, 'height': 96, 'codec': 'h264', 'duration': 1, 'frame_rate': 24, 'has_audio': True}, decode=True)
    assert result['ok'] and result['decode_verified'], result
    assert not inspect_file(path, {'codec': 'prores'})['ok']
