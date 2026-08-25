"""One module per source. Every source returns Quote objects and nothing else."""

from . import cleartrip, easemytrip, goibibo, ixigo  # noqa: F401,E402
from .base import REGISTRY, Source, SourceSpec, get_enabled, register  # noqa: F401
