"""Timestamps: always timezone-aware, stored in UTC with a fixed format so text comparison orders correctly."""

from datetime import UTC, datetime

_DB_FORMAT = "%Y-%m-%dT%H:%M:%S.%f+00:00"


def utcnow() -> datetime:
    return datetime.now(UTC)


def to_db(value: datetime) -> str:
    """Aware datetime → fixed-width UTC string. Naive datetimes are rejected: a guessed offset is a wrong value."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("naive datetime; pass a timezone-aware value")
    return value.astimezone(UTC).strftime(_DB_FORMAT)


def from_db(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)
