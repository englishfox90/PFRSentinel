"""
Time Context for Dev Mode

The computation moved to ``services/time_context.py`` so that ``MLService``
can feed the models the same ``is_astronomical_night`` flag the training data
carried without a service importing up into ui/controllers (issue #86). This
module remains for existing callers.
"""
from services.time_context import (  # re-exported for existing callers
    ASTRAL_AVAILABLE,
    compute_time_context,
)

__all__ = ['ASTRAL_AVAILABLE', 'compute_time_context']
