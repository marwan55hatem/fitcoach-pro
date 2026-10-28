"""Pure body-metric helpers (no I/O)."""

import datetime as dt

MIN_AGE = 18


def age_years(birth_date: dt.date, today: dt.date) -> int:
    """Completed years. A Feb 29 birthday is reached on Mar 1 in non-leap years."""
    if birth_date > today:
        raise ValueError("birth_date is in the future")
    years = today.year - birth_date.year
    if (today.month, today.day) < (birth_date.month, birth_date.day):
        years -= 1
    return years


def is_adult(birth_date: dt.date, today: dt.date, minimum_age: int = MIN_AGE) -> bool:
    return age_years(birth_date, today) >= minimum_age


def bmi(weight_kg: float, height_cm: float) -> float:
    if weight_kg <= 0 or height_cm <= 0:
        raise ValueError("weight and height must be positive")
    height_m = height_cm / 100
    return weight_kg / (height_m * height_m)
