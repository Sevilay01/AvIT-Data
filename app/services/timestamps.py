"""Exact comparisons for the application's UTC ISO text stored in SQLite."""

from datetime import UTC, datetime

from sqlalchemy import String, case, func, literal, type_coerce


def iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def timestamp_key(column):
    # UTCDateTime and SQLite defaults store UTC with zero, three or six
    # fractional digits. Pad the stored fraction without floating-point date
    # conversion: julianday() can round a value into the following second.
    raw = type_coerce(column, String)
    fraction = case(
        (func.substr(raw, 20, 1) == ".", func.replace(func.substr(raw, 21, 6), "Z", "")),
        else_="",
    )
    return (
        func.substr(raw, 1, 19)
        + literal(".")
        + func.substr(fraction + literal("000000"), 1, 6)
        + literal("Z")
    )
