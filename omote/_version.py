"""The one place the version is written down.

Three things want to know it, and they used to each have their own answer, all
of which were wrong in a different way:

* `pyproject.toml`, which is authoritative for a build and for `pip`.
* `importlib.metadata`, which is authoritative for an *installed* copy — and is
  stale the instant the file above is bumped, so a daemon running from a
  checkout through an editable install reports the version that checkout
  stopped being several commits ago.
* `sys.frozen`. A PyInstaller bundle is the only install for which the update
  path matters at all, and it has no `pyproject.toml` beside it and — unless the
  spec says otherwise — none of the project's own distribution metadata either.

That last one is not hypothetical. The bundled daemon reported `0.0.0`, and
`0.0.0` is *older than everything ever published*, so the updater found every
release newer than it, lit the update dot permanently, and would have cheerfully
handed an install on 0.7.0 a build of 0.1.0 — a downgrade presented as an
upgrade.

So the number lives here, in a module of the package, which means it is
collected by the wheel, by `collect_submodules` in the frozen build, and by an
editable install alike. `pyproject.toml` reads it rather than restating it, so
there is nothing left to drift.

Kept deliberately dependency-free and deliberately boring: this file is imported
before anything else in the process, including on a build machine.
"""

__version__ = '0.11.8'

__all__ = ['__version__']
