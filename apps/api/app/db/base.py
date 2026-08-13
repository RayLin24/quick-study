from __future__ import annotations

from datetime import datetime
from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.clock import utcnow
from app.db.types import ID_LENGTH, UtcDateTime, new_id

#: Deterministic constraint names so migrations can drop what earlier revisions created.
NAMING_CONVENTION: Final = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s",
    "pk": "pk_%(table_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}

#: Every table needs InnoDB for transactions and FULLTEXT, and utf8mb4 for real text.
TABLE_OPTIONS: Final[dict[str, Any]] = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


class Base(DeclarativeBase):
    metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {
        datetime: UtcDateTime(),
        dict[str, Any]: sa.JSON(),
    }
    __table_args__: Any = TABLE_OPTIONS


class IdMixin:
    id: Mapped[str] = mapped_column(sa.String(ID_LENGTH), primary_key=True, default=new_id)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
