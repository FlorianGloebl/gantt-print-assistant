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
