"""Pixel-preserving finishing and evidence for experimental subject replacement.

Generation and tracking are separate from compositing. A model's promise to
keep the background is not evidence: audit the decoded delivered video against
the source and the independently saved edit masks. This module does not judge
identity, anatomy or motion quality.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np


def rgb(frame: np.ndarray) -> None:
    if frame.dtype != np.uint8 or frame.ndim != 3 or frame.shape[2] != 3:
        raise ValueError('Use decoded RGB frames with three 8-bit channels.')


def alpha_mask(mask: np.ndarray, shape: tuple[int, int]) -> None:
    if mask.dtype != np.uint8 or mask.shape != shape:
        raise ValueError('The edit mask must be an 8-bit mask at the original frame size.')


def subject_mask(masks: list[np.ndarray], *, margin: int = 2, feather: float = 1.0,
                 max_fraction: float = .65) -> np.ndarray:
    """Union original/replacement silhouettes, with a small, bounded edge blend.

    Every supplied mask must contain a subject. Empty tracks are an error, not
    an excuse to publish a frame with an unchanged original person. Large masks
    fail closed so an all-frame mask cannot make preservation pass vacuously.
    """
    from PIL import Image, ImageFilter

    if not masks or not 0 <= margin <= 16 or not 0 <= feather <= 4 or not 0 < max_fraction < 1:
        raise ValueError('Choose subject masks and bounded edge settings.')
    shape = masks[0].shape
    if len(shape) != 2:
        raise ValueError('Subject masks must have two dimensions.')
    union = np.zeros(shape, dtype=bool)
    for mask in masks:
        alpha_mask(mask, shape)
        foreground = mask >= 128
        if np.count_nonzero(foreground) < max(4, mask.size * .0005):
            raise ValueError('A subject track is missing. Repair its mask before finishing.')
        union |= foreground
    image = Image.fromarray(union.astype(np.uint8) * 255)
    if margin:
        image = image.filter(ImageFilter.MaxFilter(margin * 2 + 1))
    if feather:
        image = image.filter(ImageFilter.GaussianBlur(feather))
    result = np.asarray(image).copy()
    # A soft transition must not leave original subjects showing through.
    result[union] = 255
    fraction = np.count_nonzero(result) / result.size
    if fraction > max_fraction:
        raise ValueError('The edit covers too much of the frame to verify background preservation.')
    return result


def composite_frame(source: np.ndarray, replacement: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Integer alpha composition; zero-mask pixels are copied byte for byte."""
    rgb(source)
    rgb(replacement)
    if replacement.shape != source.shape:
        raise ValueError('Replacement and source frames must have the same dimensions.')
    alpha_mask(mask, source.shape[:2])
    weight = mask.astype(np.uint32)[..., None]
    blended = (replacement.astype(np.uint32) * weight
               + source.astype(np.uint32) * (255 - weight) + 127) // 255
    return blended.astype(np.uint8)


@dataclass
class BackgroundAudit:
    """Accumulate independently decoded artifact measurements, frame by frame."""

    expected_frames: int
    max_edit_fraction: float = .65
    frames: int = 0
    compared_pixels: int = 0
    changed_background_pixels: int = 0
    max_channel_error: int = 0
    min_preserved_fraction: float = 1.0
    changed_subject_pixels: int = 0
    unchanged_subject_frames: list[int] = field(default_factory=list)

    def __post_init__(self):
        if not 1 <= self.expected_frames <= 36000 or not 0 < self.max_edit_fraction < 1:
            raise ValueError('Declare a bounded frame count and edit coverage.')

    def add(self, source: np.ndarray, delivered: np.ndarray, mask: np.ndarray) -> None:
        rgb(source)
        rgb(delivered)
        if delivered.shape != source.shape:
            raise ValueError('The delivered frame size changed.')
        if self.frames >= self.expected_frames:
            raise ValueError('The delivered video has extra frames.')
        alpha_mask(mask, source.shape[:2])
        edited = mask > 0
        coverage = np.count_nonzero(edited) / edited.size
        if not 0 < coverage <= self.max_edit_fraction:
            raise ValueError('Missing or excessive edit coverage; background cannot be verified.')
        difference = np.abs(source.astype(np.int16) - delivered.astype(np.int16))
        changed = np.any(difference != 0, axis=2)
        self.compared_pixels += int(np.count_nonzero(~edited))
        self.changed_background_pixels += int(np.count_nonzero(changed & ~edited))
        self.max_channel_error = max(self.max_channel_error, int(difference[~edited].max(initial=0)))
        self.min_preserved_fraction = min(self.min_preserved_fraction, 1 - coverage)
        subject_changes = int(np.count_nonzero(changed & edited))
        self.changed_subject_pixels += subject_changes
        if not subject_changes:
            self.unchanged_subject_frames.append(self.frames)
        self.frames += 1

    def result(self) -> dict:
        complete = self.frames == self.expected_frames
        return {
            **asdict(self),
            'complete': complete,
            'background_exact': complete and self.changed_background_pixels == 0 and self.compared_pixels > 0,
            'replacement_present_every_frame': complete and not self.unchanged_subject_frames,
            # These require separate tracking/visual evidence; never infer them
            # from preserved pixels or count a copied source as a successful swap.
            'identity_verified': False,
            'motion_verified': False,
        }


def repeated_runs(runs: list[dict], *, minimum: int = 3) -> dict:
    """Report measured repeatability without upgrading missing quality evidence."""
    if minimum < 2:
        raise ValueError('Repeatability requires multiple independent renders.')
    seeds = {r.get('seed') for r in runs if isinstance(r.get('seed'), int)}
    artifacts = {r.get('sha256') for r in runs if r.get('sha256')}
    sources = {r.get('source_sha256') for r in runs}
    checks = [
        bool(r.get('full_clip') is True
             and r.get('source_sha256')
             and r.get('audio_unchanged') is True
             and r.get('audit', {}).get('background_exact')
             and r.get('audit', {}).get('replacement_present_every_frame')
             and r.get('identity_verified') is True
             and r.get('motion_verified') is True
             and r.get('visual_review_passed') is True)
        for r in runs
    ]
    return {
        'required_runs': minimum, 'completed_runs': len(runs),
        'distinct_seeds': len(seeds), 'distinct_artifacts': len(artifacts),
        'passed_runs': sum(checks),
        'passed': len(runs) >= minimum and len(seeds) >= minimum and len(artifacts) >= minimum
                  and len(sources) == 1 and all(checks),
    }
