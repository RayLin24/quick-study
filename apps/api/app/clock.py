from datetime import UTC, datetime


def utcnow() -> datetime:
    """Return the current instant as an aware UTC datetime.

    Everything that persists a timestamp goes through this helper so tests can freeze or
    offset time in one place instead of patching each call site.
    """
    return datetime.now(UTC)
