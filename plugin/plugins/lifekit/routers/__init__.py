"""Life Kit plugin routers."""

from .current import CurrentWeatherRouter
from .travel import TravelAdviceRouter
from .hourly import HourlyForecastRouter
from .locations import LocationsRouter
from .trip import TripRouter
from .nearby import NearbyRouter
from .food import FoodRecommendRouter
from .recipe import RecipeRouter
from .air_quality import AirQualityRouter
from .currency import CurrencyRouter
from .countdown import CountdownRouter
from .unit_convert import UnitConvertRouter

__all__ = [
    "CurrentWeatherRouter", "TravelAdviceRouter", "HourlyForecastRouter",
    "LocationsRouter", "TripRouter", "NearbyRouter",
    "FoodRecommendRouter", "RecipeRouter",
    "AirQualityRouter", "CurrencyRouter",
    "CountdownRouter", "UnitConvertRouter",
]
