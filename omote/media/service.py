"""Making an image or a video, whoever is making it.

This is the layer that turns "a prompt and a pile of settings" into a stored
result, and it exists so that three callers — the HTTP API, the agent's tools
and the studio panel — do the same thing rather than three similar things.

The interesting decisions are about *honesty with the person waiting*.

**A request says which provider it went to.** Not for bookkeeping: image
generation is the modality where "local or hosted" is most likely to be a
deliberate choice and least likely to be visible from the result, and a
picture that quietly went to a paid API because the local server was down is
the failure this whole registry design exists to prevent. So the route is
resolved explicitly and the answer carries the provider it used.

**Parameters are validated against what the provider says it has**, not
against a list kept here. Anything the schema does not know is dropped and
reported, so a client that is one version behind is told which of its settings
did nothing rather than silently getting a default.

**Both kinds are jobs.** Video always was — minutes rather than seconds. Images
became one when this build grew past local diffusion: a hosted image model can
sit in a queue for ninety seconds, and an HTTP request held open that long dies
to somebody's proxy, taking the GPU's work with it. So a generation is started,
watched and cancellable whatever it is making, and `generate_image` is kept as
a *synchronous wrapper* over the same path for the agent, whose turn is
easier to reason about when a tool call returns the thing it made.

**Progress is reported, never invented.** Providers say what they honestly
know — a fraction from Runway or Sora, a queue position from fal or ComfyUI,
nothing at all from Veo — and a job carries whichever it got. A bar that
creeps to ninety percent and stops is a lie, and one lie of that kind is
enough to make people stop believing the true ones.

**Cancelling reaches the other end.** A cancelled job is still running at the
provider, and on a hosted one still billing, until the provider is told. So
cancellation is propagated rather than merely stopping the local wait — see
`omote.providers.hosted_media`.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from omote.media import edit as edit_ops
from omote.media.params import Param, coerce, unknown_keys
from omote.media.store import Media, MediaStore
from omote.providers.base import Modality
from omote.providers.registry import NoProviderError, ProviderRegistry, Route, RouteSet

log = logging.getLogger(__name__)

# What can be made, and which modality each kind goes to.
#
# A table rather than a ternary, because there were two kinds for a long time
# and the ternary said so: `Modality.IMAGE if kind == 'image' else
# Modality.VIDEO`. Adding a third kind to that expression does not make the
# else mean "video" — it makes it mean "everything that is not an image",
# which is how a music job ends up resolving to a video provider and reporting
# a cheerful mp4.
KINDS: tuple[str, ...] = ('image', 'video', 'music')
MODALITY_FOR_KIND: dict[str, Modality] = {
    'image': Modality.IMAGE,
    'video': Modality.VIDEO,
    'music': Modality.MUSIC,
}


def _modality(kind: str) -> Modality:
    """The modality for a kind, refusing a kind this install has never heard of.

    Raises rather than defaulting, because the caller has already been told
    the kinds that exist — `can_generate` — and answering a question about
    `audio` with the video provider is the failure this exists to stop.
    """
    try:
        return MODALITY_FOR_KIND[kind]
    except KeyError:
        raise NoProviderError(
            f'{kind!r} is not something this can make. It can make: {", ".join(KINDS)}.'
        ) from None

#: How many finished jobs to keep. They are only ever read by a client that
#: wants to know what happened; the media itself is in the store and outlives
#: all of this. Unbounded, they are a slow leak on a daemon that stays up for
#: weeks.
KEEP_JOBS = 200


@dataclass(slots=True)
class Job:
    """One generation, long enough to be worth watching."""

    id: str
    kind: str
    prompt: str
    provider: str
    model: str
    started_at: float = field(default_factory=time.time)
    state: str = 'queued'           # queued | running | done | failed | cancelled
    #: 0..1, or None when nothing downstream honestly knows. See the module
    #: docstring: None is a real answer here and is rendered as one.
    progress: float | None = None
    #: What is happening, in words. "number 3 in the queue", "rendering on the
    #: GPU", "downloading". Worth more than a number that cannot move.
    note: str = ''
    error: str = ''
    media: list[Media] = field(default_factory=list)
    #: What it was asked with, so a result can be sent back to the form it
    #: came from without the client having kept a copy.
    params: dict[str, Any] = field(default_factory=dict)
    finished_at: float = 0.0
    task: Any = field(default=None, repr=False)

    @property
    def elapsed(self) -> int:
        return int((self.finished_at or time.time()) - self.started_at)

    def to_json(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'kind': self.kind,
            'prompt': self.prompt,
            'provider': self.provider,
            'model': self.model,
            'state': self.state,
            'progress': self.progress,
            'note': self.note,
            'error': self.error,
            'elapsed': self.elapsed,
            'params': self.params,
            'media': [m.to_json() for m in self.media],
        }


class MediaService:
    def __init__(self, store: MediaStore, registry: ProviderRegistry) -> None:
        self.store = store
        self.registry = registry
        self.jobs: dict[str, Job] = {}

    def can_generate(self) -> set[str]:
        """Which kinds of media this install can actually make, right now.

        Asked before the tools are offered. A model shown `generate_video` on
        a machine with no video backend will propose it, be told nothing is
        configured, and spend the turn recovering — the same reason
        `write_file` is left out of a read-only session rather than registered
        and refused. On a small model the cost is worse than a wasted step: a
        tool list full of things that cannot work is a tool list it reasons
        about instead of doing the task.
        """
        available = self.registry.capabilities()
        return {kind for kind in KINDS if available.get(kind)}

    def can_edit(self) -> dict[str, list[str]]:
        """Which editors this machine has, by the kind of file they handle.

        Asked of the machine rather than assumed, for the same reason
        `can_generate` is: a `trim` button on a machine with no ffmpeg is a
        button that fails on the first press, and a tool list containing
        something that cannot work is a list the model reasons about instead of
        doing the task.

        Note what this is *not*: a modality. Every generator in this project is
        somebody else's model reached over a network, and the registry is the
        right place for those because they can be swapped. An editor is a
        binary on this disk — Pillow, ffmpeg, GIMP — so it belongs to the
        machine and asking the registry would imply an install that could fail
        to answer.
        """
        found = edit_ops.capabilities()
        out: dict[str, list[str]] = {}
        for kind in ('image', 'video', 'audio'):
            editors = found.for_kind(kind)
            if editors:
                out[kind] = editors
        return out

    def editors(self, kind: str = '') -> list[dict[str, Any]]:
        """What each editor on this machine can be asked to do.

        Drawn from the back ends rather than listed here, because the two do
        not offer the same operations and a `convert` that only works for
        images is a control that fails half the time.
        """
        out: list[dict[str, Any]] = []
        for one_kind, names in self.can_edit().items():
            if kind and kind != one_kind:
                continue
            for name in names:
                if name == 'ffmpeg':
                    params = [Param('operation', 'Do what', 'enum',
                                    default='trim', options=list(edit_ops.VIDEO_OPERATIONS),
                                    group='edit',
                                    help='What to do to it. Every one of these is a real editor '
                                         'operation and none of them re-encodes more than it has to.')]
                elif name == 'pillow':
                    params = [Param('operation', 'Do what', 'enum',
                                    default='crop', options=list(edit_ops.IMAGE_OPERATIONS),
                                    group='edit',
                                    help='What to do to it. Deterministic and reversible, which is '
                                         'why it is the first thing offered for a picture.')]
                else:
                    params = [Param('operation', 'Do what', 'string', group='edit',
                                    help='A Script-Fu procedure name, e.g. '
                                         'gimp-image-flatten or plug-in-unsharp.')]
                out.append({
                    'id': name,
                    'kind': one_kind,
                    'label': f'{name} ({one_kind})',
                    'params': [p.to_json() for p in params],
                })
        return out

    def edit(
        self,
        operation: str,
        inputs: list[str],
        *,
        kind: str = '',
        editor: str = '',
        params: dict[str, Any] | None = None,
    ) -> Job:
        """Change a stored file and keep the result beside it.

        `inputs` are **stored media ids**, never paths and never data URIs.
        That is the same rule every generation parameter follows here, and it
        is the load-bearing one: an editor is the operation where reading an
        arbitrary file off the disk is most tempting, and the only thing
        standing between it and `open('/etc/passwd')` is that a caller can name
        a file here only if this server put it in its own store.
        """
        if not inputs:
            raise NoProviderError('an edit needs at least one input')
        paths: list[str] = []
        kinds: list[str] = []
        for media_id in inputs:
            found = self.store.path(media_id)
            if found is None:
                raise NoProviderError(
                    f'no stored file with id {media_id!r}. Upload the file first, or use an id this '
                    'server issued.'
                )
            paths.append(str(found))
            kinds.append(self.store.get(media_id).kind)

        first = kind or kinds[0]
        available = self.can_edit().get(first, [])
        if not available:
            raise NoProviderError(
                f'this machine cannot edit {first} files. It has: '
                + (', '.join(k for k, v in self.can_edit().items()) or 'nothing that edits')
                + '.'
            )
        chosen = editor or available[0]
        if chosen not in available:
            raise NoProviderError(
                f'{chosen} cannot edit {first} files here. It can: {", ".join(available)}.'
            )

        params = dict(params or {})
        params.pop('operation', None)
        job = Job(
            id=uuid.uuid4().hex[:16],
            kind='edit',
            provider=chosen,
            model='',
            prompt=str(operation),
            params={'operation': operation, 'inputs': list(inputs), **params},
        )
        job.note = 'starting'
        self.jobs[job.id] = job
        job.task = asyncio.create_task(self._run_edit(job, chosen, operation, paths, params))
        return job

    async def _run_edit(self, job: Job, chosen: str, operation: str, paths: list[str], params: dict[str, Any]):
        try:
            job.state = 'running'
            # Scratch space beside the store, because ffmpeg and GIMP both want
            # a real path to write to and neither accepts bytes.
            scratch = self.store.root.parent / 'edit'
            scratch.mkdir(parents=True, exist_ok=True)
            target = scratch / f'{job.id}{edit_ops.scratch_suffix(chosen, operation)}'

            if chosen == 'gimp':
                from omote.providers.gimp import GimpProvider

                outcome = await GimpProvider().run(operation, paths, target, params)
                media_type = outcome['media_type']
            elif chosen == 'ffmpeg':
                media_type = edit_ops.guess_media_type(target)
                edit_ops.video(operation, paths[0], str(target), params,
                               second=str(self._second(paths)) if len(paths) > 1 else '',
                               subtitles=str(params.get('subtitle_file') or ''))
            else:
                outcome = edit_ops.image(operation, paths[0], str(target), params)
                media_type = outcome.pop('media_type', '')
            if not media_type:
                media_type = edit_ops.guess_media_type(target)

            job.media = [
                self.store.add(
                    target.read_bytes(),
                    kind=job.kind,
                    media_type=media_type,
                    prompt=f'{operation}: {job.params.get("inputs")}',
                    provider=chosen,
                    params=job.params,
                )
            ]
            target.unlink(missing_ok=True)
            job.state = 'done'
        except (edit_ops.EditUnavailable, edit_ops.EditRefused) as exc:
            log.info('edit %s failed: %s', job.id, exc)
            job.state = 'failed'
            job.error = str(exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception('edit %s failed', job.id)
            job.state = 'failed'
            job.error = str(exc)
        finally:
            job.finished_at = time.time()

    @staticmethod
    def _second(paths: list[str]) -> Path | None:
        return Path(paths[1]) if len(paths) > 1 else None

    def providers(self, kind: str = '') -> list[dict[str, Any]]:
        """Who could answer for this kind of generation.

        Per kind rather than one list, because they genuinely differ now: an
        install can have six things that make pictures and two that make
        video, and offering the six in the video picker is offering four
        failures.
        """
        modalities = list(MODALITY_FOR_KIND) if not kind else [_modality(kind)]
        seen: dict[str, dict[str, Any]] = {}
        for modality in modalities:
            for info in self.registry.providers_for(modality):
                seen.setdefault(info.id, {'id': info.id, 'label': info.label, 'local': info.local})
        return sorted(seen.values(), key=lambda p: (not p['local'], p['id']))

    # -- what can be asked for ----------------------------------------------

    def _resolve(self, kind: str, provider: str | None, model: str | None):
        modality = _modality(kind)
        routes = (
            RouteSet(routes={modality: Route(provider=provider, model=model or '')})
            if provider else None
        )
        return self.registry.resolve(modality, routes)

    @staticmethod
    async def _models(impl: Any, kind: str) -> list[dict[str, Any]]:
        """A provider's models, asked for this kind of generation where it can be.

        The aggregators host both and their lists barely overlap, so `kind` is
        passed to anything whose signature takes it. Checked by inspection
        rather than by calling and catching TypeError: a TypeError raised
        *inside* a provider's own listing code would otherwise be swallowed and
        retried, which turns a bug into a silently empty dropdown.
        """
        lister = getattr(impl, 'models', None)
        if lister is None:
            return []
        try:
            if 'kind' in inspect.signature(lister).parameters:
                return await lister(kind)
            return await lister()
        except Exception as exc:  # noqa: BLE001
            log.info('%s did not list models: %s', getattr(impl, 'provider_id', impl), exc)
            return []

    async def describe(self, kind: str, provider: str | None = None, model: str | None = None) -> dict[str, Any]:
        """The form for one generator: its models, and its parameters.

        Asked of the provider every time. An install's samplers, upscalers and
        workflow templates are what is on that machine right now, and a form
        built from a remembered list offers a sampler that was uninstalled.
        """
        impl, route, info = self._resolve(kind, provider, model)
        chosen = model or route.model

        models = await self._models(impl, kind)
        if not chosen and models:
            chosen = str(models[0].get('id') or '')

        params = await self._schema(impl, chosen)

        return {
            'provider': info.id,
            'local': info.local,
            'model': chosen,
            'models': models,
            'params': [p.to_json() for p in params],
            'providers': self.providers(kind),
        }

    async def _schema(self, impl: Any, model: str) -> list[Param]:
        describer = getattr(impl, 'describe', None)
        if describer is None:
            # A provider with no opinion gets the common form, so a backend
            # this build has never met is still driveable rather than being
            # offered with no controls at all.
            from omote.media.params import common_image

            return common_image()
        return await describer(model)

    # -- making things ------------------------------------------------------

    def start(
        self,
        kind: str,
        prompt: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> Job:
        """Queue a generation and return immediately.

        Returning a job rather than awaiting one is not an optimisation. A
        video is minutes and a hosted image can be a minute and a half; an
        HTTP request that takes minutes is a request that dies to somebody's
        proxy, and a person watching a spinner with no way to stop it will
        kill the browser tab and leave a GPU working on something nobody
        wants any more.
        """
        impl, route, info = self._resolve(kind, provider, model)
        job = Job(
            id=uuid.uuid4().hex[:12],
            kind=kind,
            prompt=prompt,
            provider=info.id,
            model=model or route.model,
        )
        self.jobs[job.id] = job
        self._prune()
        job.task = asyncio.create_task(self._run(job, impl, info, params or {}))
        return job

    async def _run(self, job: Job, impl: Any, info: Any, params: dict[str, Any]) -> None:
        try:
            if not job.model:
                listed = await self._models(impl, job.kind)
                if not listed:
                    raise NoProviderError(self._nothing_to_use(job, info))
                job.model = str(listed[0]['id'])

            schema = await self._schema(impl, job.model)
            clean = coerce(schema, params)
            job.params = dict(clean)
            ignored = unknown_keys(schema, params)
            if ignored:
                log.info('%s ignored %s', info.id, ', '.join(ignored))

            # Reference images are stored media, referred to by id. They are
            # turned into bytes here — once, centrally — because every provider
            # wants them in a different envelope (a data URI, bare base64, a
            # multipart part, an upload to the render box first) and none of
            # them should have to know how this build stores files.
            clean = self._load_images(schema, clean)

            # `size` is popped because the image contract takes it as its own
            # argument, and remembered because whether it was *asked for* is
            # the thing the recipe needs to know — see `_recipe`.
            asked_size = clean.pop('size', None)
            size = asked_size or f'{clean.get("width", 1024)}x{clean.get("height", 1024)}'
            count = int(clean.pop('n', 1) or 1)
            # Not a parameter — it is the list of LoRA names the server has,
            # sent to the form so a person can see what to type into a prompt.
            clean.pop('loras_available', None)

            def report(fraction: float | None, note: str) -> None:
                job.state = 'running'
                job.progress = fraction
                job.note = note

            job.state = 'running'
            started = time.monotonic()
            if job.kind == 'image':
                results = await impl.generate(
                    job.prompt, model=job.model, n=count, size=size, progress=report, **clean
                )
            else:
                # Video and music share a signature and differ only in what the
                # backend does with the parameters; an image backend wants `n`
                # and `size` spelled out and the rest take **kw.
                results = await impl.generate(job.prompt, model=job.model, progress=report, **clean)
            took = int((time.monotonic() - started) * 1000)

            recipe = self._recipe(clean, size=asked_size, count=count, kind=job.kind)
            job.media = [
                self.store.add(
                    media.data,
                    kind=job.kind,
                    media_type=media.media_type,
                    prompt=job.prompt,
                    provider=job.provider,
                    model=job.model,
                    seed=media.seed,
                    params=recipe,
                    notes=str(media.meta.get('revised_prompt', '')),
                )
                for media in results
            ]
            job.progress = 1.0
            job.note = f'{len(job.media)} made in {took // 1000}s' if job.media else ''
            job.state = 'done' if job.media else 'failed'
            if not job.media:
                job.error = (
                    'it finished and produced nothing this could read. For a ComfyUI template that '
                    'usually means no output node; for a hosted provider it usually means a safety '
                    'filter that returned success with an empty result.'
                )
        except asyncio.CancelledError:
            job.state = 'cancelled'
            job.note = 'stopped'
            # The provider is told, because a job abandoned rather than
            # cancelled keeps a GPU busy and keeps a hosted account billing.
            # Every hosted adapter implements this; ComfyUI has an interrupt
            # endpoint and most local ones have nothing, which is why a
            # missing method is an ordinary outcome rather than an error.
            for name in ('interrupt', 'stop'):
                stopper = getattr(impl, name, None)
                if stopper is not None:
                    try:
                        await stopper()
                    except Exception:  # noqa: BLE001
                        log.debug('could not interrupt %s', info.id, exc_info=True)
                    break
            raise
        except Exception as exc:  # noqa: BLE001 - a failed job is a state, not a crash
            log.exception('%s job %s failed', job.kind, job.id)
            job.state = 'failed'
            # The backend's own message, which is the useful one: "CUDA out of
            # memory", "insufficient credits" and "NSFW content detected" want
            # three different reactions and a generic failure tells you none.
            job.error = str(exc)[:600]
        finally:
            job.finished_at = time.time()

    @staticmethod
    def _recipe(clean: dict[str, Any], *, size: str | None, count: int, kind: str) -> dict[str, Any]:
        """What is worth recording about how this was made.

        **The rule is: record what was sent.** A recipe is read by somebody
        trying to get the same result again, and anything in it that was not
        actually a setting is worse than absent — it reads as a knob that was
        turned, and a person changing one thing and going again starts from a
        false picture of what the last run did.

        Three things broke that rule, and all three were visible in the panel
        before they were fixed here:

        **A size nobody asked for.** `generate` takes a size because the image
        contract has always had one, so it is synthesised from the width and
        height — or, failing those, from a hard-coded 1024 — when the caller
        gave none. That made a model which sizes by aspect ratio record
        `size 1024x1024`: not what it did, and not a control it has. Recorded
        only when a size was genuinely asked for; a caller who set width and
        height has those recorded instead, which is the same fact in the units
        that backend actually uses.

        **`seed -1`.** -1 is a request for a random seed, not a seed. It is the
        first thing anyone looking for reproducibility reads, and it is the one
        value guaranteed not to reproduce anything — the seed that was really
        used is on the media record, where the provider put it.

        **`n 1`.** The default, on the parameter that means "how many of these
        did I ask for". One is what asking once looks like.

        A reference image is dropped too, but for a different reason: it is
        megabytes of picture and it is already in the store under its own id.
        """
        recipe = {
            k: v for k, v in clean.items()
            if not isinstance(v, bytes | bytearray | list)
        }
        if recipe.get('seed') in (-1, '-1'):
            recipe.pop('seed')
        if kind == 'image':
            if size:
                recipe = {'size': size, **recipe}
            if count > 1:
                recipe = {'n': count, **recipe}
        elif kind == 'music' and count > 1:
            recipe = {'n': count, **recipe}
        return recipe

    @staticmethod
    def _nothing_to_use(job: Job, info: Any) -> str:
        if job.kind == 'video' and 'comfy' in info.id.lower():
            return ('no workflow template is installed, so there is nothing to generate with. '
                    'Export a graph from ComfyUI with Save (API format) and import it.')
        return f'{info.id} offered no {job.kind} models, and none was named.'

    def _load_images(self, schema: list[Param], clean: dict[str, Any]) -> dict[str, Any]:
        """Turn stored media ids into bytes, for the controls that take a picture.

        A control of kind `image` carries an id from the media store — never a
        path and never a data URI from the client. That is deliberate: the
        value travels through the same JSON as every other parameter and the
        only thing a client can name is something this server already has, so
        there is nothing here that turns a parameter into a file read.
        """
        wanted = {p.name: p for p in schema if p.kind in ('image', 'video', 'audio')}
        out: dict[str, Any] = {}
        for key, value in clean.items():
            if key not in wanted or not value:
                out[key] = value
                continue
            ids = [v.strip() for v in str(value).split(',') if v.strip()]
            param = wanted[key]
            if param.max_images is not None and len(ids) > param.max_images:
                raise ValueError(f'{param.label} accepts at most {param.max_images} reference file(s).')
            for ident in ids:
                media = self.store.get(ident)
                if media is None or not media.media_type.startswith(param.kind + '/'):
                    raise ValueError(f'{param.label} needs a stored {param.kind} reference.')
            blobs = [data for data in (self._read(i) for i in ids) if data]
            if len(blobs) != len(ids):
                raise ValueError(f'A file for {param.label} is missing.')
            if not blobs:
                continue
            # One image stays one image. Several providers take a single blob
            # and would be given a list of one, which is a different request.
            out[key] = blobs if wanted[key].multiple or len(blobs) > 1 else blobs[0]
        return out

    def _read(self, media_id: str) -> bytes | None:
        path = self.store.path(media_id)
        if path is None:
            log.warning('a generation referred to media %s, which is not in the store', media_id)
            return None
        try:
            return path.read_bytes()
        except OSError as exc:
            log.warning('could not read %s: %s', path, exc)
            return None

    async def generate_image(
        self,
        prompt: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make an image and wait for it — the shape the agent's tool wants.

        The same job underneath, awaited. An agent turn that has to poll for
        its own tool result is a turn that spends two more round trips saying
        nothing, so the tool gets to block and the panel does not.
        """
        job = self.start('image', prompt, provider=provider, model=model, params=params)
        try:
            await job.task
        except asyncio.CancelledError:
            raise
        if job.state == 'failed':
            raise RuntimeError(job.error)

        impl, _, info = self._resolve('image', provider, model)
        schema = await self._schema(impl, job.model)
        return {
            'media': [m.to_json() for m in job.media],
            'provider': job.provider,
            'local': info.local,
            'model': job.model,
            'took_ms': job.elapsed * 1000,
            'ignored': unknown_keys(schema, params or {}),
        }

    def start_video(
        self,
        prompt: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> Job:
        """Kept because the video endpoint and the agent's tool both call it."""
        return self.start('video', prompt, provider=provider, model=model, params=params)

    def start_music(
        self,
        prompt: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> Job:
        """Make a piece of music. Minutes, not seconds, so this is a job.

        The same shape as video on purpose: a hosted music model is a submit,
        a poll and a download, and pretending it is a request would mean either
        holding a socket open for two minutes or lying about how long it took.
        """
        return self.start('music', prompt, provider=provider, model=model, params=params)

    def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job is None or job.task is None or job.task.done():
            return False
        job.task.cancel()
        return True

    async def close(self) -> None:
        tasks = [job.task for job in self.jobs.values() if job.task and not job.task.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _prune(self) -> None:
        """Forget the oldest finished jobs.

        Only finished ones, and only past the cap: a daemon that stays up for
        a fortnight should not accumulate every generation anyone ever made,
        and the results themselves are in the store regardless.
        """
        if len(self.jobs) <= KEEP_JOBS:
            return
        done = sorted(
            (j for j in self.jobs.values() if j.finished_at),
            key=lambda j: j.finished_at,
        )
        for job in done[: len(self.jobs) - KEEP_JOBS]:
            self.jobs.pop(job.id, None)
