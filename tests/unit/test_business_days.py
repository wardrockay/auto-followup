"""
Tests for Business Days Logic.

Tests the French business day calculations including holidays.
"""

from datetime import date, datetime, timezone

import pytest
from freezegun import freeze_time

from auto_followup.core.business_days import (
    add_business_days,
    get_french_holidays,
    is_business_day,
    next_business_day,
)


class TestGetFrenchHolidays:
    """Tests for get_french_holidays function."""
    
    def test_returns_fixed_holidays(self):
        """Should include fixed French holidays."""
        holidays = get_french_holidays(2024)
        
        assert date(2024, 1, 1) in holidays    # New Year
        assert date(2024, 5, 1) in holidays    # Labour Day
        assert date(2024, 5, 8) in holidays    # Victory Day
        assert date(2024, 7, 14) in holidays   # Bastille Day
        assert date(2024, 8, 15) in holidays   # Assumption
        assert date(2024, 11, 1) in holidays   # All Saints
        assert date(2024, 11, 11) in holidays  # Armistice
        assert date(2024, 12, 25) in holidays  # Christmas
    
    def test_returns_easter_based_holidays(self):
        """Should include Easter-based holidays for 2024."""
        holidays = get_french_holidays(2024)
        
        # Easter 2024 is March 31
        assert date(2024, 4, 1) in holidays    # Easter Monday (April 1)
        assert date(2024, 5, 9) in holidays    # Ascension (May 9)
        assert date(2024, 5, 20) in holidays   # Pentecost Monday (May 20)
    
    def test_different_years_have_different_easter_dates(self):
        """Easter dates should differ by year."""
        holidays_2024 = get_french_holidays(2024)
        holidays_2025 = get_french_holidays(2025)
        
        # Easter 2024 is March 31, Easter 2025 is April 20
        assert date(2024, 4, 1) in holidays_2024  # Easter Monday 2024
        assert date(2025, 4, 21) in holidays_2025  # Easter Monday 2025


class TestIsBusinessDay:
    """Tests for is_business_day function."""
    
    def test_weekday_non_holiday_is_business_day(self):
        """Regular weekdays should be business days."""
        # January 2, 2024 is a Tuesday
        assert is_business_day(date(2024, 1, 2)) is True
    
    def test_saturday_is_not_business_day(self):
        """Saturday should not be a business day."""
        assert is_business_day(date(2024, 1, 6)) is False  # Saturday
    
    def test_sunday_is_not_business_day(self):
        """Sunday should not be a business day."""
        assert is_business_day(date(2024, 1, 7)) is False  # Sunday
    
    def test_holiday_is_not_business_day(self):
        """French holidays should not be business days."""
        assert is_business_day(date(2024, 1, 1)) is False   # New Year
        assert is_business_day(date(2024, 7, 14)) is False  # Bastille Day


class TestNextBusinessDay:
    """Tests for next_business_day function."""
    
    def test_friday_next_is_monday(self):
        """Next business day after Friday should be Monday."""
        friday = date(2024, 1, 5)  # Friday
        result = next_business_day(friday)
        assert result == date(2024, 1, 8)  # Monday
    
    def test_saturday_next_is_monday(self):
        """Next business day from Saturday should be Monday."""
        saturday = date(2024, 1, 6)
        result = next_business_day(saturday)
        assert result == date(2024, 1, 8)
    
    def test_before_holiday_skips_holiday(self):
        """Next business day before a holiday should skip it."""
        # December 24, 2024 is Tuesday, December 25 is Christmas
        tuesday = date(2024, 12, 24)
        result = next_business_day(tuesday)
        assert result == date(2024, 12, 26)


class TestAddBusinessDays:
    """Tests for add_business_days function."""
    
    def test_add_zero_days(self):
        """Adding zero days should return next business day."""
        monday = datetime(2024, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
        result = add_business_days(monday, 0)
        assert result.date() == date(2024, 1, 8)
    
    def test_add_days_within_week(self):
        """Adding days within same week should work correctly."""
        monday = datetime(2024, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
        result = add_business_days(monday, 3)
        assert result.date() == date(2024, 1, 11)  # Thursday
    
    def test_add_days_spanning_weekend(self):
        """Adding days spanning weekend should skip weekends."""
        thursday = datetime(2024, 1, 4, 10, 0, 0, tzinfo=timezone.utc)
        result = add_business_days(thursday, 3)
        assert result.date() == date(2024, 1, 9)  # Tuesday
    
    def test_add_days_spanning_holiday(self):
        """Adding days spanning holiday should skip holidays."""
        # Start December 23, 2024 (Monday), add 3 days
        # Dec 24 (Tue), Dec 25 (Wed - Christmas), Dec 26 (Thu)
        monday = datetime(2024, 12, 23, 10, 0, 0, tzinfo=timezone.utc)
        result = add_business_days(monday, 3)
        assert result.date() == date(2024, 12, 27)  # Friday (skips Christmas)
    
    def test_preserves_time_component(self):
        """Result should preserve time from original datetime."""
        monday = datetime(2024, 1, 8, 14, 30, 45, tzinfo=timezone.utc)
        result = add_business_days(monday, 1)
        assert result.hour == 14
        assert result.minute == 30
        assert result.second == 45
