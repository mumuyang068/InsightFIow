"""Persistent, versioned evaluation definitions and execution receipts."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


def _json_column(*, nullable=False):
    return Column(JSON().with_variant(JSONB(), "postgresql"), nullable=nullable)


class EvaluationSuite(SQLModel, table=True):
    __tablename__ = "cbi_evaluation_suite"
    __table_args__ = (
        UniqueConstraint(
            "theme_id", "actor_id", "request_id", name="uq_cbi_eval_suite_request"
        ),
        Index("ix_cbi_eval_suite_scope", "theme_id", "workspace_id", "created_at"),
    )

    id: str = Field(sa_type=String(36), primary_key=True)
    theme_id: int = Field(sa_type=BigInteger, foreign_key="cbi_theme.id")
    workspace_id: int = Field(sa_type=BigInteger)
    actor_id: int = Field(sa_type=BigInteger)
    request_id: str = Field(sa_type=String(100))
    request_digest: str = Field(sa_type=String(64))
    previous_id: str | None = Field(
        default=None, sa_type=String(36), foreign_key="cbi_evaluation_suite.id"
    )
    revision: int
    status: str = Field(sa_type=String(16))
    title: str = Field(sa_type=String(160))
    description: str = Field(sa_type=Text)
    cases: list[dict] = Field(sa_column=_json_column())
    digest: str = Field(sa_type=String(64))
    created_at: datetime = Field(sa_type=DateTime(timezone=True))


class EvaluationBatch(SQLModel, table=True):
    __tablename__ = "cbi_evaluation_batch"
    __table_args__ = (
        UniqueConstraint(
            "theme_id", "actor_id", "request_id", name="uq_cbi_eval_batch_request"
        ),
        Index("ix_cbi_eval_batch_scope", "theme_id", "workspace_id", "created_at"),
    )

    id: str = Field(sa_type=String(36), primary_key=True)
    theme_id: int = Field(sa_type=BigInteger, foreign_key="cbi_theme.id")
    workspace_id: int = Field(sa_type=BigInteger)
    actor_id: int = Field(sa_type=BigInteger)
    request_id: str = Field(sa_type=String(100))
    request_digest: str = Field(sa_type=String(64))
    suite_id: str = Field(sa_type=String(36), foreign_key="cbi_evaluation_suite.id")
    parent_batch_id: str | None = Field(
        default=None, sa_type=String(36), foreign_key="cbi_evaluation_batch.id"
    )
    label: str = Field(sa_type=String(160))
    budget: int
    declared_variable: str = Field(sa_type=String(32))
    context: dict = Field(sa_column=_json_column())
    case_ids: list[str] = Field(sa_column=_json_column())
    remaining: list[str] = Field(sa_column=_json_column())
    status: str = Field(sa_type=String(16))
    stop_reason: str | None = Field(default=None, sa_type=String(100))
    worker_epoch: str = Field(sa_type=String(36))
    created_at: datetime = Field(sa_type=DateTime(timezone=True))
    updated_at: datetime = Field(sa_type=DateTime(timezone=True))


class EvaluationAttempt(SQLModel, table=True):
    __tablename__ = "cbi_evaluation_attempt"
    __table_args__ = (
        UniqueConstraint(
            "batch_id", "case_id", "number", name="uq_cbi_eval_attempt_number"
        ),
    )

    id: str = Field(sa_type=String(36), primary_key=True)
    batch_id: str = Field(sa_type=String(36), foreign_key="cbi_evaluation_batch.id")
    case_id: str = Field(sa_type=String(100))
    number: int
    status: str = Field(sa_type=String(24))
    observation: dict | None = Field(
        default=None, sa_column=_json_column(nullable=True)
    )
    judgement: dict | None = Field(default=None, sa_column=_json_column(nullable=True))
    model_calls: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default=text("0"))
    )
    started_at: datetime = Field(sa_type=DateTime(timezone=True))
    finished_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class EvaluationCommand(SQLModel, table=True):
    __tablename__ = "cbi_evaluation_command"

    batch_id: str = Field(
        sa_type=String(36), foreign_key="cbi_evaluation_batch.id", primary_key=True
    )
    request_id: str = Field(sa_type=String(100), primary_key=True)
    request_digest: str = Field(sa_type=String(64))
    actor_id: int = Field(sa_type=BigInteger)
    action: str = Field(sa_type=String(12))
    created_at: datetime = Field(sa_type=DateTime(timezone=True))
