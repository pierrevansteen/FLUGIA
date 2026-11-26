from .app_db import AppDBCollector
from .base import Collector, CollectorError, CollectResult
from .ga4 import GA4Collector
from .geo import GeoCollector
from .google_ads import GoogleAdsCollector
from .google_calendar import GoogleCalendarCollector
from .linkedin import LinkedInCollector
from .manual import ManualCollector
from .meta import MetaCollector
from .search_console import SearchConsoleCollector
from .stripe_billing import StripeCollector

ALL_COLLECTORS: list[type[Collector]] = [
    GoogleAdsCollector,
    MetaCollector,
    LinkedInCollector,
    SearchConsoleCollector,
    GA4Collector,
    GeoCollector,
    GoogleCalendarCollector,
    AppDBCollector,
    StripeCollector,
    ManualCollector,
]

__all__ = ["ALL_COLLECTORS", "Collector", "CollectorError", "CollectResult"]
