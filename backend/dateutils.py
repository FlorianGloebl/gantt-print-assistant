from datetime import date, timedelta


def add_workdays(d: date, n: int) -> date:
    """Add n working days (Mon-Fri) to date d."""
    if n <= 0:
        return d
    added = 0
    while added < n:
        d = d + timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d


def subtract_workdays(d: date, n: int) -> date:
    """Subtract n working days (Mon-Fri) from date d."""
    if n <= 0:
        return d
    removed = 0
    while removed < n:
        d = d - timedelta(days=1)
        if d.weekday() < 5:
            removed += 1
    return d


def count_workdays(start: date, end: date) -> int:
    """Signed workday count compatible with add_workdays/subtract_workdays.

    count_workdays(d, add_workdays(d, n)) == n for any n >= 0.
    Returns negative values when end < start.
    """
    if start == end:
        return 0
    if start < end:
        d, count = start, 0
        while d < end:
            d += timedelta(days=1)
            if d.weekday() < 5:
                count += 1
        return count
    return -count_workdays(end, start)
