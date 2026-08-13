from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy.dialects import mysql
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

ID_LENGTH: Final = 32
SHA256_LENGTH: Final = 64

#: Long-form text (page bodies, chunk text, docstrings) that must not be truncated.
LongText: Final = sa.Text().with_variant(mysql.LONGTEXT(), "mysql")

#: Hexadecimal SHA-256 digests, used for content addressing and token fingerprints.
Sha256: Final = sa.String(SHA256_LENGTH)

#: Money with enough precision for per-token model pricing.
Money: Final = sa.Numeric(12, 6)

#: A 0.000-1.000 confidence score attached to inferred analysis results.
Confidence: Final = sa.Numeric(4, 3)


def new_id() -> str:
    """Return an opaque 32-character primary key.

    Opaque keys keep row counts and creation order out of URLs that reviewers share.
    """
    return uuid.uuid4().hex


class UtcDateTime(TypeDecorator[datetime]):
    """A datetime column that refuses naive values and always returns aware UTC.

    MySQL ``DATETIME`` carries no offset, so the conversion has to happen in the mapping
    layer; rejecting naive input on the way in is what makes the way out unambiguous.
    """

    impl = sa.DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "mysql":
            return dialect.type_descriptor(mysql.DATETIME(fsp=6))
        return dialect.type_descriptor(sa.DateTime())

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetimes are ambiguous; pass an aware UTC datetime")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class StrEnumType(TypeDecorator[Any]):
    """Persist a :class:`~enum.StrEnum` as its lowercase wire value.

    Native database enums turn adding a state into a table rewrite, and SQLAlchemy's
    portable ``Enum`` stores member *names*. Storing values keeps the column readable in
    ad-hoc SQL while still rejecting anything outside the vocabulary at bind time.
    """

    impl = sa.String
    cache_ok = True

    def __init__(self, enum_class: type[StrEnum], length: int = 32) -> None:
        super().__init__(length=length)
        self.enum_class = enum_class

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return self.enum_class(value).value

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        return self.enum_class(value)
