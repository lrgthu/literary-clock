"""Upstream corpus adapters and import pipeline."""

from litclock.importers.catalog import default_source_specs
from litclock.importers.pipeline import import_corpora

__all__ = ["default_source_specs", "import_corpora"]
