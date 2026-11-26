"""Population growth since the 2020 census (七普), one rule for cities, provinces and the nation (CN_PLAN §5.2).

Helper module (no build()): used by cities.py and states.py.

growth = population(year) / pop_census_2020 − 1, a fraction, over growth_years = [CENSUS_YEAR, year], where `year`
is the cn_stats_city stats year (nation.json "year"). `population` is the 年末常住人口 of a 统计公报 and the base the
七普 常住人口 (2020-11-01), so the window is about 3 years. growth is null (never a guess) when:

* the record has no population of the stats year (its bulletin is unread; no other year is substituted), or no
  census population (Taiwan);
* |growth| > MAX_GROWTH (a sanity cap; cn_stats_city already drops a 2023 population more than 25% off the census).
"""

from __future__ import annotations

CENSUS_YEAR = 2020
MAX_GROWTH = 0.25


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def rate(pop, census) -> float | None:
    """pop / census − 1, rounded to 4 places; None if either is unknown or |rate| > MAX_GROWTH."""
    if not _is_num(pop) or not _is_num(census) or census <= 0 or pop <= 0:
        return None
    g = pop / census - 1
    return round(g, 4) if abs(g) <= MAX_GROWTH else None


def window(year: int | None) -> list[int] | None:
    """growth_years for the stats year, or None."""
    return [CENSUS_YEAR, year] if isinstance(year, int) and year > CENSUS_YEAR else None
