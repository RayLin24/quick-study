from __future__ import annotations

from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import MappedColumn, mapped_column

from app.db.types import ID_LENGTH, StrEnumType


def id_fk(
    target: str,
    *,
    ondelete: str = "CASCADE",
    nullable: bool = False,
    index: bool = True,
) -> MappedColumn[Any]:
    """A foreign key to another domain table's opaque primary key.

    ``ondelete`` is spelled out at every call site because the delete policy is part of
    the domain contract: project-scoped rows cascade, references to people and to
    optional evidence become NULL.
    """
    return mapped_column(
        sa.String(ID_LENGTH),
        sa.ForeignKey(target, ondelete=ondelete),
        nullable=nullable,
        index=index,
    )


def project_fk(*, index: bool = True) -> MappedColumn[Any]:
    return id_fk("projects.id", ondelete="CASCADE", index=index)


def enum_column(
    enum_class: type[StrEnum],
    default: StrEnum | None = None,
    *,
    nullable: bool = False,
    index: bool = False,
    length: int = 32,
) -> MappedColumn[Any]:
    return mapped_column(
        StrEnumType(enum_class, length),
        default=default,
        nullable=nullable,
        index=index,
    )
