"""Preservation audits must reject lossy encodes, broken tracks and trivial copies."""
import json
import shutil
import subprocess
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from omote.media.replacement import BackgroundAudit, composite_frame, repeated_runs, subject_mask
from tools.finish_subject_replacement import finish


def frames():
    source = np.random.default_rng(8).integers(0, 256, (64, 96, 3), dtype=np.uint8)
    generated = np.full_like(source, (210, 175, 140))
    masks = [np.zeros((64, 96), np.uint8) for _ in range(2)]
    masks[0][15:50, 20:30] = 255
    masks[1][15:50, 60:70] = 255
    return source, generated, subject_mask(masks)


def test_composition_preserves_input_and_copies_visible_background():
    source, generated, mask = frames()
    before = source.copy()
    output = composite_frame(source, generated, mask)
    np.testing.assert_array_equal(source, before)
    np.testing.assert_array_equal(output[mask == 0], source[mask == 0])
    np.testing.assert_array_equal(output[mask == 255], generated[mask == 255])
    assert np.any((mask > 0) & (mask < 255))


def test_audit_rejects_drift_in_a_single_channel_and_missing_frames():
    source, generated, mask = frames()
    audit = BackgroundAudit(2)
    output = composite_frame(source, generated, mask)
    audit.add(source, output, mask)
    assert not audit.result()['background_exact']
    output[0, 0, 1] ^= 1
    audit.add(source, output, mask)
    report = audit.result()
    assert report['complete'] and not report['background_exact']
    assert report['changed_background_pixels'] == 1 and report['max_channel_error'] == 1
    with pytest.raises(ValueError, match='extra frames'):
        audit.add(source, output, mask)


def test_empty_and_overbroad_masks_cannot_hide_failure():
    source, generated, mask = frames()
    for bad in (np.zeros_like(mask), np.full_like(mask, 255)):
        with pytest.raises(ValueError):
            subject_mask([bad])
        with pytest.raises(ValueError):
            BackgroundAudit(1).add(source, generated, bad)
    with pytest.raises(ValueError, match='missing'):
        subject_mask([mask, np.zeros_like(mask)])
    with pytest.raises(ValueError, match='8-bit'):
        composite_frame(source, generated, mask.astype(np.float32))
    with pytest.raises(ValueError, match='dimensions'):
        composite_frame(source, generated[:, :-1], mask)


def test_copying_the_source_is_not_a_successful_replacement():
    source, _, mask = frames()
    audit = BackgroundAudit(1)
    audit.add(source, source, mask)
    result = audit.result()
    assert result['background_exact']
    assert not result['replacement_present_every_frame']
    assert not result['identity_verified'] and not result['motion_verified']


@pytest.mark.parametrize('codec,exact', [('libx264rgb', True), ('libx264', False)])
def test_audit_checks_real_encoded_artifact(tmp_path, codec, exact):
    binary = shutil.which('ffmpeg')
    if not binary:
        pytest.skip('FFmpeg is required for codec integration.')
    source, generated, mask = frames()
    composed = composite_frame(source, generated, mask)
    target = tmp_path / 'result.mkv'
    encoding = ['-c:v', codec, '-crf', '0' if exact else '18', '-preset', 'ultrafast']
    if not exact:
        encoding += ['-pix_fmt', 'yuv420p']
    subprocess.run([binary, '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', '96x64',
                    '-r', '30', '-i', 'pipe:0', *encoding, str(target)],
                   input=composed.tobytes() * 3, check=True, capture_output=True, timeout=30)
    decoded = subprocess.check_output([binary, '-v', 'error', '-i', str(target), '-f', 'rawvideo',
                                       '-pix_fmt', 'rgb24', 'pipe:1'], timeout=30)
    video = np.frombuffer(decoded, np.uint8).reshape(-1, 64, 96, 3)
    audit = BackgroundAudit(3)
    for frame in video:
        audit.add(source, frame, mask)
    assert audit.result()['background_exact'] is exact


def test_repeatability_requires_independent_renders_and_quality_review():
    base = {'seed': 1, 'sha256': 'a', 'source_sha256': 'source', 'full_clip': True, 'audio_unchanged': True,
            'audit': {'background_exact': True,
            'replacement_present_every_frame': True}, 'identity_verified': True,
            'motion_verified': True, 'visual_review_passed': True}
    assert not repeated_runs([base])['passed']
    assert not repeated_runs([base] * 3)['passed']
    runs = [dict(base, seed=i, sha256=str(i)) for i in range(3)]
    assert repeated_runs(runs)['passed']
    runs[1]['identity_verified'] = False
    assert not repeated_runs(runs)['passed']
    runs[1]['identity_verified'] = True
    runs[1]['full_clip'] = False
    assert not repeated_runs(runs)['passed']
    runs[1]['full_clip'] = True
    runs[1]['source_sha256'] = 'another clip'
    assert not repeated_runs(runs)['passed']


@pytest.mark.parametrize('tamper', [False, True])
def test_finishing_verifies_against_independent_source_decode(tmp_path, tamper):
    binary, probe_binary = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not binary or not probe_binary:
        pytest.skip('This integration test requires FFmpeg and ffprobe.')
    source, generated, _ = frames()
    clip = tmp_path / 'original.mp4'
    subprocess.run([binary, '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', '96x64',
                    '-r', '24', '-i', 'pipe:0', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.166667',
                    '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                    '-c:a', 'aac', '-shortest', str(clip)],
                   input=source.tobytes() * 4, check=True, capture_output=True, timeout=30)
    prepared = tmp_path / 'source'
    (prepared / 'frames').mkdir(parents=True)
    subprocess.run([binary, '-v', 'error', '-i', str(clip), '-start_number', '0',
                    str(prepared / 'frames/%05d.png')], check=True, capture_output=True, timeout=30)
    probe = subprocess.check_output([probe_binary, '-v', 'error', '-show_streams', '-of', 'json', str(clip)])
    (prepared / 'probe.json').write_bytes(probe)
    run = tmp_path / 'run'
    (run / 'frames').mkdir(parents=True)
    (run / 'manifest.json').write_text(json.dumps({'seed': 1, 'generated_frames': 4, 'workflow_sha256': 'workflow'}))
    for side, x in [('left', 20), ('right', 60)]:
        (tmp_path / f'source-mask-{side}').mkdir()
        (run / f'target-mask-{side}').mkdir()
        mask = np.zeros((64, 96), dtype=np.uint8)
        mask[15:50, x:x + 10] = 255
        for index in range(4):
            for directory in (tmp_path / f'source-mask-{side}', run / f'target-mask-{side}'):
                Image.fromarray(mask).save(directory / f'{index:05d}.png')
            Image.fromarray(generated).save(run / f'frames/{index:05d}.png')
    if tamper:
        altered = np.array(Image.open(prepared / 'frames/00000.png'))
        altered[0, 0] = 255 - altered[0, 0]
        Image.fromarray(altered).save(prepared / 'frames/00000.png')
    args = SimpleNamespace(directory=tmp_path, run=run, source=clip)
    if tamper:
        with pytest.raises(ValueError, match='failed preservation'):
            finish(args)
        assert not (run / 'finished/replacement-preview.mp4').exists()
    else:
        finish(args)
        preview = run / 'finished/replacement-preview.mp4'
        assert preview.is_file()
        streams = json.loads(subprocess.check_output(
            [probe_binary, '-v', 'error', '-show_streams', '-of', 'json', str(preview)]))['streams']
        video = next(s for s in streams if s['codec_type'] == 'video')
        assert int(video['nb_frames']) == 4 and float(video['start_time']) == 0
        assert video['color_space'] == video['color_transfer'] == video['color_primaries'] == 'bt709'
    report = json.loads((run / 'finished/audit.json').read_text())
    assert report['full_clip'] and report['frames'] == 4
    assert report['audit']['background_exact'] is not tamper
    assert report['audio_unchanged'] and report['original_audio_packets'] > 0
    assert not report['visual_review_passed']
