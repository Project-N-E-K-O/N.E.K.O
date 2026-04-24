"""Weather plugin routers."""

from .current import CurrentWeatherRouter
from .travel import TravelAdviceRouter
from .hourly import HourlyForecastRouter

__all__ = ["CurrentWeatherRouter", "TravelAdviceRouter", "HourlyForecastRouter"]
