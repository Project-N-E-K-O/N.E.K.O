"""Weather plugin routers."""

from .current import CurrentWeatherRouter
from .travel import TravelAdviceRouter
from .hourly import HourlyForecastRouter
from .locations import LocationsRouter
from .trip import TripRouter
from .nearby import NearbyRouter

__all__ = ["CurrentWeatherRouter", "TravelAdviceRouter", "HourlyForecastRouter", "LocationsRouter", "TripRouter", "NearbyRouter"]
