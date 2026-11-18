from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


def _json_column():
    return Column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)


class Theme(SQLModel, table=True):
    __tablename__ = "cbi_theme"
    __table_args__ = (
        ForeignKeyConstraint(
            ["id", "active_version"],
            ["cbi_theme_version.theme_id", "cbi_theme_version.version"],
            name="fk_cbi_theme_active_version",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["id", "initial_version"],
            ["cbi_theme_version.theme_id", "cbi_theme_version.version"],
            name="fk_cbi_theme_initial_version",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "active_version IS NOT NULL OR initial_version IS NOT NULL",
            name="ck_cbi_theme_version_present",
        ),
    )

    id: int = Field(sa_column=Column(BigInteger, primary_key=True, autoincrement=False))
    oid: int = Field(sa_type=BigInteger, index=True)
    active_version: str | None = Field(default=None, sa_type=String(128))
    source_digest: str = Field(sa_type=String(64))
    initial_version: str | None = Field(default=None, sa_type=String(128))
    registration_request_id: str | None = Field(default=None, sa_type=String(100))
    registration_digest: str | None = Field(default=None, sa_type=String(64))
    enabled: bool = Field(
        default=True, sa_column=Column(Boolean, nullable=False, server_default=true())
    )


class Version(SQLModel, table=True):
    __tablename__ = "cbi_theme_version"

    theme_id: int = Field(
        sa_type=BigInteger, primary_key=True, foreign_key="cbi_theme.id"
    )
    version: str = Field(sa_type=String(128), primary_key=True)
    content: dict = Field(sa_column=_json_column())
    content_digest: str = Field(sa_type=String(64))
    dependency: str | None = Field(default=None, sa_type=String(64))
    created_by: int | None = Field(default=None, sa_type=BigInteger)
    created_at: datetime = Field(sa_type=DateTime(timezone=True))


class Draft(SQLModel, table=True):
    __tablename__ = "cbi_theme_draft"
    __table_args__ = (
        ForeignKeyConstraint(
            ["theme_id", "base_version"],
            ["cbi_theme_version.theme_id", "cbi_theme_version.version"],
        ),
        Index("ix_cbi_theme_draft_actor", "theme_id", "actor_id", "saved_at"),
    )

    theme_id: int = Field(sa_type=BigInteger, primary_key=True)
    id: str = Field(sa_type=String(36), primary_key=True)
    revision: int = Field(sa_type=Integer, primary_key=True)
    base_version: str = Field(sa_type=String(128))
    actor_id: int = Field(sa_type=BigInteger)
    content: dict = Field(sa_column=_json_column())
    content_digest: str = Field(sa_type=String(64))
    saved_at: datetime = Field(sa_type=DateTime(timezone=True))


class QualityReport(SQLModel, table=True):
    __tablename__ = "cbi_theme_quality_report"
    __table_args__ = (
        ForeignKeyConstraint(
            ["theme_id", "draft_id", "revision"],
            [
                "cbi_theme_draft.theme_id",
                "cbi_theme_draft.id",
                "cbi_theme_draft.revision",
            ],
        ),
        ForeignKeyConstraint(
            ["theme_id", "version"],
            ["cbi_theme_version.theme_id", "cbi_theme_version.version"],
        ),
        CheckConstraint(
            "(version IS NOT NULL AND draft_id IS NULL AND revision IS NULL) OR "
            "(version IS NULL AND draft_id IS NOT NULL AND revision IS NOT NULL)",
            name="ck_cbi_theme_quality_target",
        ),
        UniqueConstraint("theme_id", "request_id", name="uq_cbi_theme_quality_request"),
        Index("ix_cbi_theme_quality_draft", "theme_id", "draft_id", "completed_at"),
        Index("ix_cbi_theme_quality_version", "theme_id", "version", "completed_at"),
    )

    theme_id: int = Field(
        sa_type=BigInteger, primary_key=True, foreign_key="cbi_theme.id"
    )
    id: str = Field(sa_type=String(36), primary_key=True)
    request_id: str = Field(sa_type=String(128))
    author_id: int = Field(sa_type=BigInteger)
    version: str | None = Field(default=None, sa_type=String(128))
    draft_id: str | None = Field(default=None, sa_type=String(36))
    revision: int | None = None
    config_digest: str = Field(sa_type=String(64))
    scope: dict = Field(sa_column=_json_column())
    receipt: dict = Field(sa_column=_json_column())
    started_at: datetime = Field(sa_type=DateTime(timezone=True))
    completed_at: datetime = Field(sa_type=DateTime(timezone=True))


class Preview(SQLModel, table=True):
    __tablename__ = "cbi_theme_preview"
    __table_args__ = (
        ForeignKeyConstraint(
            ["theme_id", "draft_id", "revision"],
            [
                "cbi_theme_draft.theme_id",
                "cbi_theme_draft.id",
                "cbi_theme_draft.revision",
            ],
        ),
        ForeignKeyConstraint(
            ["theme_id", "quality_report_id"],
            ["cbi_theme_quality_report.theme_id", "cbi_theme_quality_report.id"],
            name="fk_cbi_theme_preview_quality_report",
        ),
        Index("ix_cbi_theme_preview_draft", "theme_id", "draft_id", "created_at"),
    )

    theme_id: int = Field(sa_type=BigInteger, primary_key=True)
    id: str = Field(sa_type=String(36), primary_key=True)
    draft_id: str = Field(sa_type=String(36))
    revision: int = Field(sa_type=Integer)
    actor_id: int = Field(sa_type=BigInteger)
    dependency: str = Field(sa_type=String(64))
    receipt: dict = Field(sa_column=_json_column())
    checked: bool = Field(
        default=False, sa_column=Column(Boolean, nullable=False, server_default=false())
    )
    checked_by: int | None = Field(default=None, sa_type=BigInteger)
    checked_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    quality_report_id: str | None = Field(default=None, sa_type=String(36))
    note: str | None = Field(default=None, sa_type=Text)
    created_at: datetime = Field(sa_type=DateTime(timezone=True))


class Publication(SQLModel, table=True):
    __tablename__ = "cbi_theme_publication"
    __table_args__ = (
        ForeignKeyConstraint(
            ["theme_id", "previous_version"],
            ["cbi_theme_version.theme_id", "cbi_theme_version.version"],
        ),
        ForeignKeyConstraint(
            ["theme_id", "version"],
            ["cbi_theme_version.theme_id", "cbi_theme_version.version"],
        ),
        ForeignKeyConstraint(
            ["theme_id", "draft_id", "revision"],
            [
                "cbi_theme_draft.theme_id",
                "cbi_theme_draft.id",
                "cbi_theme_draft.revision",
            ],
        ),
        ForeignKeyConstraint(
            ["theme_id", "validation_id"],
            ["cbi_theme_preview.theme_id", "cbi_theme_preview.id"],
        ),
        ForeignKeyConstraint(
            ["theme_id", "quality_report_id"],
            ["cbi_theme_quality_report.theme_id", "cbi_theme_quality_report.id"],
            name="fk_cbi_theme_publication_quality_report",
        ),
        Index("ix_cbi_theme_publication_draft", "theme_id", "draft_id"),
    )

    theme_id: int = Field(sa_type=BigInteger, primary_key=True)
    request_id: str = Field(sa_type=String(128), primary_key=True)
    action: str = Field(sa_type=String(8))
    previous_version: str = Field(sa_type=String(128))
    version: str = Field(sa_type=String(128))
    actor_id: int = Field(sa_type=BigInteger)
    draft_id: str | None = Field(default=None, sa_type=String(36))
    revision: int | None = None
    validation_id: str | None = Field(default=None, sa_type=String(36))
    quality_report_id: str | None = Field(default=None, sa_type=String(36))
    created_at: datetime = Field(sa_type=DateTime(timezone=True))
