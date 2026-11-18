from sqlalchemy import BigInteger, Column, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class BindingHead(SQLModel, table=True):
    __tablename__ = "cbi_binding_head"

    workspace: int = Field(sa_column=Column(BigInteger, primary_key=True))
    id: str = Field(sa_column=Column(String(48), primary_key=True))
    active: int = Field(sa_column=Column(Integer, nullable=False))
    last_version: int = Field(sa_column=Column(Integer, nullable=False))


class BindingVersion(SQLModel, table=True):
    __tablename__ = "cbi_binding_version"

    workspace: int = Field(sa_column=Column(BigInteger, primary_key=True))
    id: str = Field(sa_column=Column(String(48), primary_key=True))
    version: int = Field(sa_column=Column(Integer, primary_key=True))
    state: dict = Field(sa_column=Column(JSONB, nullable=False))


class MethodFeedback(SQLModel, table=True):
    __tablename__ = "cbi_method_feedback"

    id: str = Field(sa_column=Column(String(32), primary_key=True))
    workspace: int = Field(sa_column=Column(BigInteger, nullable=False, index=True))
    chat_id: int = Field(sa_column=Column(BigInteger, ForeignKey("chat.id", ondelete="CASCADE"), nullable=False))
    state: dict = Field(sa_column=Column(JSONB, nullable=False))
