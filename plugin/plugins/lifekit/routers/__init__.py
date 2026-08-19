"""Life Kit plugin routers."""

from .air_quality import AirQualityRouter
from .countdown import CountdownRouter
from .currency import CurrencyRouter
from .current import CurrentWeatherRouter
from .food import FoodRecommendRouter
from .hourly import HourlyForecastRouter
from .locations import LocationsRouter
from .nearby import NearbyRouter
from .recipe import RecipeRouter
from .travel import TravelAdviceRouter
from .trip import TripRouter
from .unit_convert import UnitConvertRouter

__all__ = [
    "CurrentWeatherRouter", "TravelAdviceRouter", "HourlyForecastRouter",
    "LocationsRouter", "TripRouter", "NearbyRouter",
    "FoodRecommendRouter", "RecipeRouter",
    "AirQualityRouter", "CurrencyRouter",
    "CountdownRouter", "UnitConvertRouter",
]
