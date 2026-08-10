"""projectm-01 drop-in package for unicorn-viz.

Core loads symbols by file path (``load_dropin_symbol('projectm-01/
projectm_effect.py', 'ProjectMEffect')``), never through this package --
an eager relative re-export here breaks pytest's module import of the
drop-in tree (see webcam-01/__init__.py, which hit and documented the
same issue). Kept import-free like every other drop-in ``__init__``.
"""
from __future__ import annotations
