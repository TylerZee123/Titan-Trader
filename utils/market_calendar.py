"""
MarketCalendar — Never trade on a closed market
=================================================
Checks NYSE market hours including all federal holidays.
Used by every entry point to bail out early if market is closed.
"""

import logging
from datetime import datetime, date, time
from typing import Optional
import pytz

logger = logging.getLogger("titan_trader")

ET = pytz.timezone("America/New_York")

# NYSE holidays 2025-2027
NYSE_HOLIDAYS = {
    # 2025
    date(2025, 1, 1),   date(2025, 1, 20),  date(2025, 2, 17),
    date(2025, 4, 18),  date(2025, 5, 26),  date(2025, 6, 19),
    date(2025, 7, 4),   date(2025, 9, 1),   date(2025, 11, 27),
    date(2025, 12, 25),
    # 2026
    date(2026, 1, 1),   date(2026, 1, 19),  date(2026, 2, 16),
    date(2026, 4, 3),   date(2026, 5, 25),  date(2026, 6, 19),
    date(2026, 7, 3),   date(2026, 9, 7),   date(2026, 11, 26),
    date(2026, 12, 25),
    # 2027
    date(2027, 1, 1),   date(2027, 1, 18),  date(2027, 2, 15),
    date(2027, 3, 26),  date(2027, 5, 31),  date(2027, 6, 18),
    date(2027, 7, 5),   date(2027, 9, 6),   date(2027, 11, 25),
    date(2027, 12, 24),
}

MARKET_OPEN  = time(9, 30)
MARKET_CLOSE = time(16, 0)
PRE_MARKET   = time(4, 0)
POST_MARKET  = time(20, 0)


def is_market_open() -> bool:
    """Is the NYSE currently open for regular trading?"""
    now_et = datetime.now(ET)
    today  = now_et.date()
    if now_et.weekday() >= 5:
        return False
    if today in NYSE_HOLIDAYS:
        return False
    current_time = now_et.time()
    return MARKET_OPEN <= current_time < MARKET_CLOSE


def is_trading_day(check_date: Optional[date] = None) -> bool:
    """Is a given date (default today) a trading day?"""
    d = check_date or datetime.now(ET).date()
    if d.weekday() >= 5:
        return False
    return d not in NYSE_HOLIDAYS


def is_pre_market() -> bool:
    """Is it pre-market hours (4am-9:30am ET)?"""
    now_et = datetime.now(ET)
    if not is_trading_day():
        return False
    t = now_et.time()
    return PRE_MARKET <= t < MARKET_OPEN


def is_post_market() -> bool:
    """Is it post-market hours (4pm-8pm ET)?"""
    now_et = datetime.now(ET)
    if not is_trading_day():
        return False
    t = now_et.time()
    return MARKET_CLOSE <= t < POST_MARKET


def minutes_to_open() -> int:
    """Minutes until market opens. 0 if already open."""
    now_et = datetime.now(ET)
    if is_market_open():
        return 0
    open_dt = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    if now_et >= open_dt:
        return 0
    return int((open_dt - now_et).total_seconds() / 60)


def assert_trading_day(mode: str) -> bool:
    """
    Call at the top of every run mode.
    Logs a clear message and returns False if market is closed.
    Caller should exit(0) if this returns False.
    """
    if not is_trading_day():
        now = datetime.now(ET)
        logger.info(
            f"Market closed today ({now.strftime('%A %B %d')}) — "
            f"{'weekend' if now.weekday() >= 5 else 'holiday'}. "
            f"Titan Trader [{mode}] skipping."
        )
        return False
    return True
