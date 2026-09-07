"""SQLAlchemy declarative base for the ported Room entities.

Ported from: the ``androidx.room`` setup in
``src/android/app/src/main/java/com/openminis/app/data/db/``.

Room generates the schema from ``@Entity`` annotations; SQLAlchemy needs a
declarative base instead. Every ported entity subclasses :class:`Base` and
declares ``__tablename__`` to match the original ``tableName``.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase

__all__ = ["Base"]


class Base(DeclarativeBase):
    """Declarative base for every ported Room ``@Entity``."""
