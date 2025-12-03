"""
Business days calculation module.

Handles French holidays and business day computations.
Pure business logic with no external dependencies.
"""

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import FrozenSet, Set, Union


def now_utc() -> datetime:
    """Get the current UTC datetime."""
    return datetime.now(timezone.utc)


@lru_cache(maxsize=10)
def get_french_holidays(year: int) -> FrozenSet[date]:
    """
    Calculate French public holidays for a given year.
    
    Includes fixed holidays and moveable holidays based on Easter
    (Pâques, Ascension, Pentecôte).
    
    Args:
        year: The calendar year.
        
    Returns:
        Frozenset of holiday dates for the year.
    """
    holidays: Set[date] = set()
    
    # Fixed holidays
    fixed_holidays = [
        (1, 1),    # Jour de l'an
        (5, 1),    # Fête du travail
        (5, 8),    # Victoire 1945
        (7, 14),   # Fête nationale
        (8, 15),   # Assomption
        (11, 1),   # Toussaint
        (11, 11),  # Armistice
        (12, 25),  # Noël
    ]
    
    for month, day in fixed_holidays:
        holidays.add(date(year, month, day))
    
    # Calculate Easter (Meeus/Jones/Butcher algorithm)
    easter = _calculate_easter(year)
    
    # Moveable holidays based on Easter
    holidays.add(easter + timedelta(days=1))     # Lundi de Pâques
    holidays.add(easter + timedelta(days=39))    # Ascension
    holidays.add(easter + timedelta(days=50))    # Lundi de Pentecôte
    
    return frozenset(holidays)


def _calculate_easter(year: int) -> date:
    """
    Calculate Easter Sunday using the Meeus/Jones/Butcher algorithm.
    
    Args:
        year: The calendar year.
        
    Returns:
        Date of Easter Sunday.
    """
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    
    return date(year, month, day)


def is_business_day(check_date: Union[datetime, date]) -> bool:
    """
    Check if a date is a business day (not weekend, not French holiday).
    
    Args:
        check_date: The date to check.
        
    Returns:
        True if the date is a business day.
    """
    if isinstance(check_date, datetime):
        check_date = check_date.date()
    
    # Weekend check (Saturday=5, Sunday=6)
    if check_date.weekday() >= 5:
        return False
    
    # Holiday check
    holidays = get_french_holidays(check_date.year)
    return check_date not in holidays


def next_business_day(from_date: datetime) -> datetime:
    """
    Get the next business day from a given date.
    
    If the date is already a business day, returns it unchanged.
    Preserves the original time component.
    
    Args:
        from_date: The starting datetime.
        
    Returns:
        The next business day datetime.
    """
    original_time = from_date.time()
    current_date = from_date.date()
    
    while not is_business_day(current_date):
        current_date = current_date + timedelta(days=1)
    
    return datetime.combine(current_date, original_time, tzinfo=from_date.tzinfo)


def add_business_days(start_date: datetime, business_days: int) -> datetime:
    """
    Add a number of business days to a date.
    
    Supports both positive (future) and negative (past) business days.
    The resulting date is guaranteed to be a business day and set to 1:00 AM UTC.
    
    Args:
        start_date: The starting datetime.
        business_days: Number of business days to add (positive) or subtract (negative).
        
    Returns:
        The resulting datetime after adding business days, set to 1:00 AM UTC.
    """
    current = start_date
    days_to_add = abs(business_days)
    direction = 1 if business_days >= 0 else -1
    days_added = 0
    
    while days_added < days_to_add:
        current = current + timedelta(days=direction)
        if is_business_day(current):
            days_added += 1
    
    # Set time to 1:00 AM UTC
    return current.replace(hour=1, minute=0, second=0, microsecond=0)
