"""Business glossary of the data catalogue."""
from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, JSONType, TimestampMixin


class GlossaryTerm(IdMixin, TimestampMixin, Base):
    """Business vocabulary ("Active customer", "Net revenue") linked to datasets / columns."""

    __tablename__ = "glossary_terms"
    name: Mapped[str] = mapped_column(String(120), unique=True)
    definition: Mapped[str] = mapped_column(Text)
    synonyms: Mapped[list] = mapped_column(JSONType, default=list)
    # [{"dataset": "gold.revenue_by_month", "column": "revenue"}]
    links: Mapped[list] = mapped_column(JSONType, default=list)
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
