"""One module per source. Every source returns Quote objects and nothing else."""

from .base import Source, SourceSpec, REGISTRY, register, get_enabled  # noqa: F401
from . import ixigo, easemytrip, cleartrip, goibibo  # noqa: F401,E402
