"""Exception hierarchy for the EPUB optimization pipeline."""

from __future__ import annotations


class EpubOptimizerError(Exception):
    """Base class for every error raised by the optimization pipeline."""


class InvalidEpubError(EpubOptimizerError):
    """The input file is not a readable EPUB (ZIP) archive."""


class OptimizerCancelled(EpubOptimizerError):
    """Raised inside the pipeline when the user requests cancellation.

    Unlike the other exceptions this one is expected: the GUI worker
    catches it and stops the batch cleanly instead of treating it as an
    error.
    """
