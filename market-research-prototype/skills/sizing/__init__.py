"""
skills/sizing/ — the numbers-right engine (Layer 3, the moat).

Scale-adaptive market sizing. The classifier routes a venture to the correct
sizing method so that "a restaurant in LA" is sized by trade-area catchment,
not by a national TAM ÷ ARPU formula meant for global SaaS.

  classify_market_scale  → market_scale  (which method to use, + signals)
  size_hyperlocal        → market_sizing (trade-area catchment)     [next]
  size_regional          → market_sizing (per-location × rollout)   [next]
  size_national_digital  → market_sizing (top-down ÷ bottom-up)     [next]
  validate_numbers       → validation    (triangulation gate)       [next]

Importing this package registers its skills.
"""
from . import classify         # noqa: F401  — scale classifier (routing keystone)
from . import validate         # noqa: F401  — mandatory numbers gate
from . import hyperlocal        # noqa: F401  — trade-area catchment sizing
from . import regional          # noqa: F401  — per-location rollout
from . import national_digital  # noqa: F401  — top-down ÷ bottom-up (gated legacy)
from . import bottom_up         # noqa: F401  — live-grounded bottom-up (Census CBP × ARPU)
from . import dispatch          # noqa: F401  — size_market: classify → route → validated

__all__ = ["classify", "validate", "hyperlocal", "regional", "national_digital", "dispatch"]
