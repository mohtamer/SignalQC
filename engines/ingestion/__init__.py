"""Versioned ingestion engine derived from the original hdf5_writer_v7.3."""
from .hdf5_writer_v10_ncm import discover, run

__all__ = ["discover", "run"]
