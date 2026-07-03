"""Typed data models for the Weather Event Analyzer.

All data flowing between modules uses these dataclasses.
Import from here for the base interfaces, or from subpackages for source-specific types.
"""

from core.models.base import BaseDataResult
from core.models.atmosphere import AtmosphereData, VariableTimeSeries
from core.models.pressure_levels import PressureLevelSnapshot, PressureLevelHourly
from core.models.discussion import Discussion
from core.models.fronts import FrontalFeature
from core.models.alerts import Alert, SourceResult
from core.models.satellite import SatelliteFrame
