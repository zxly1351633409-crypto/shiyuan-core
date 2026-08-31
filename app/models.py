from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


MemoryStatus = Literal["candidate", "confirmed", "rejected", "superseded"]


class BootstrapRequest(BaseModel):
    body: str = Field(min_length=1, max_length=64)
    device: str = Field(default="unknown", max_length=128)
    session_id: str | None = Field(default=None, max_length=256)
    project: str | None = Field(default=None, max_length=256)


class RecallRequest(BootstrapRequest):
    query: str = Field(default="", max_length=8000)
    limit: int = Field(default=8, ge=1, le=30)


class ContextResolveRequest(RecallRequest):
    include_recent: bool = True


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2_000_000)
    timestamp: str | None = Field(default=None, max_length=80)
    message_id: str | None = Field(default=None, max_length=256)


class HistorySessionImport(BaseModel):
    source: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    source_session_id: str = Field(min_length=1, max_length=512)
    title: str = Field(default="", max_length=500)
    source_locator: str = Field(default="", max_length=2048)
    source_fingerprint: str = Field(default="", max_length=128)
    started_at: str | None = Field(default=None, max_length=80)
    ended_at: str | None = Field(default=None, max_length=80)
    import_version: int = Field(default=1, ge=1, le=100)
    messages: list[HistoryMessage] = Field(min_length=1, max_length=50_000)


class HistoryMessageAppend(BaseModel):
    source: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    source_session_id: str = Field(min_length=1, max_length=512)
    title: str = Field(default="", max_length=500)
    source_locator: str = Field(default="live-hook", max_length=2048)
    idempotency_key: str = Field(min_length=16, max_length=256)
    message: HistoryMessage


class WorkTurnStart(BootstrapRequest):
    prompt: str = Field(default="", max_length=12000)
    turn_id: str | None = Field(default=None, max_length=256)


class WorkReceiptCreate(BootstrapRequest):
    workstream_id: str | None = Field(default=None, max_length=64)
    turn_id: str | None = Field(default=None, max_length=256)
    status: Literal["waiting", "completed", "blocked"] = "waiting"
    result_summary: str = Field(min_length=1, max_length=2000)
    decisions: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    idempotency_key: str | None = Field(default=None, max_length=256)


class WorkCheckpointCreate(BootstrapRequest):
    workstream_id: str | None = Field(default=None, max_length=64)
    turn_id: str | None = Field(default=None, max_length=256)
    phase: Literal["investigating", "implementing", "verifying", "waiting", "blocked"]
    summary: str = Field(min_length=1, max_length=2000)
    artifacts: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    idempotency_key: str | None = Field(default=None, max_length=256)


class WorkCatchUpRequest(BootstrapRequest):
    limit: int = Field(default=30, ge=1, le=100)
    advance: bool = True


class MemoryProposal(BaseModel):
    kind: str = Field(default="fact", max_length=64)
    content: str = Field(min_length=2, max_length=12000)
    scope: str = Field(default="personal", max_length=64)
    source: str = Field(default="unknown", max_length=512)
    confidence: float = Field(default=0.7, ge=0, le=1)
    sensitivity: str = Field(default="normal", max_length=32)
    evidence: str | None = Field(default=None, max_length=12000)


class MemoryDecision(BaseModel):
    note: str | None = Field(default=None, max_length=4000)


class ResponsePreferences(BaseModel):
    response_style_mode: Literal["off", "canary"] = "canary"
    response_marker: str = Field(default="🐳 十元在线", max_length=64)


class EventCreate(BaseModel):
    event_type: str = Field(min_length=1, max_length=128)
    body: str = Field(min_length=1, max_length=64)
    device: str = Field(default="unknown", max_length=128)
    session_id: str | None = Field(default=None, max_length=256)
    project: str | None = Field(default=None, max_length=256)
    summary: str | None = Field(default=None, max_length=12000)
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=256)


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    objective: str = Field(min_length=1, max_length=12000)
    assigned_body: str | None = Field(default=None, max_length=64)
    project: str | None = Field(default=None, max_length=256)
    context: dict[str, Any] = Field(default_factory=dict)
    acceptance: list[str] = Field(default_factory=list)
    source_body: str = Field(default="unknown", max_length=64)


class TaskReport(BaseModel):
    body: str = Field(min_length=1, max_length=64)
    status: Literal["in_progress", "completed", "blocked", "failed"]
    summary: str = Field(min_length=1, max_length=12000)
    artifacts: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
