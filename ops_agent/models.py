from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


BoundedText = Annotated[str, Field(min_length=1, max_length=2048)]
BoundedIdentifier = Annotated[str, Field(min_length=1, max_length=256)]


class EvidenceStatus(str, Enum):
    OK = "OK"
    MISSING = "MISSING"
    ERROR = "ERROR"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNAVAILABLE = "UNAVAILABLE"


class FreshnessStatus(str, Enum):
    FRESH = "FRESH"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class CollectionStatus(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class Scope(StrictModel):
    context: str | None = None
    namespace: str
    topic: str
    consumer_group: str


class BundleContext(StrictModel):
    source_sha: str | None = None
    source_dirty: bool | None = None
    collector_tree_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    desired_image: str | None = None
    pod_image_ids: list[str] = Field(default_factory=list)
    argocd_revision: str | None = None
    collector_version: str
    tool_registry_version: str
    policy_version: str


class CollectionMetadata(StrictModel):
    started_at: datetime
    completed_at: datetime
    status: CollectionStatus

    @model_validator(mode="after")
    def validate_time_order(self) -> "CollectionMetadata":
        if self.started_at.utcoffset() is None or self.completed_at.utcoffset() is None:
            raise ValueError("collection timestamps must be timezone-aware")
        if self.started_at > self.completed_at:
            raise ValueError("collection started_at must not exceed completed_at")
        return self


class Freshness(StrictModel):
    status: FreshnessStatus
    age_seconds: float | None = Field(default=None, ge=0)
    max_age_seconds: float | None = Field(default=None, gt=0)
    basis: BoundedIdentifier

    @model_validator(mode="after")
    def validate_age_contract(self) -> "Freshness":
        if self.status == FreshnessStatus.FRESH:
            if self.age_seconds is None or self.max_age_seconds is None:
                raise ValueError("FRESH evidence requires age and max age")
            if self.age_seconds > self.max_age_seconds:
                raise ValueError("FRESH evidence age must not exceed max age")
        if self.status == FreshnessStatus.STALE:
            if self.age_seconds is None or self.max_age_seconds is None:
                raise ValueError("STALE evidence requires age and max age")
            if self.age_seconds <= self.max_age_seconds:
                raise ValueError("STALE evidence age must exceed max age")
        return self


class MetricObservation(StrictModel):
    name: BoundedIdentifier
    value: Any = None
    unit: BoundedIdentifier | None = None
    window: BoundedIdentifier | None = None
    aggregation: BoundedIdentifier | None = None
    sample_count: int | None = Field(default=None, ge=0)


class Coverage(StrictModel):
    expected_count: int | None = Field(default=None, ge=0)
    observed_count: int | None = Field(default=None, ge=0)
    complete: bool | None = None
    expected_items: list[str] = Field(default_factory=list)
    observed_items: list[str] = Field(default_factory=list)
    missing_items: list[str] = Field(default_factory=list)
    extra_items: list[str] = Field(default_factory=list)
    notes: str | None = None


class Semantic(StrictModel):
    type: BoundedIdentifier
    notes: BoundedText
    is_db_commit_rate: bool | None = None
    flags: list[BoundedIdentifier] = Field(default_factory=list, max_length=64)


class EvidenceError(StrictModel):
    type: BoundedIdentifier
    message: BoundedText


class EvidenceItem(StrictModel):
    evidence_id: BoundedIdentifier
    status: EvidenceStatus
    source: BoundedIdentifier
    tool_id: BoundedIdentifier
    source_timestamp: datetime | None = None
    collected_at: datetime
    freshness: Freshness
    metric: MetricObservation
    labels: dict[BoundedIdentifier, BoundedText] = Field(
        default_factory=dict,
        max_length=64,
    )
    coverage: Coverage = Field(default_factory=Coverage)
    semantic: Semantic
    raw_ref: str | None = None
    raw_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    error: EvidenceError | None = None

    @model_validator(mode="after")
    def validate_status_contract(self) -> "EvidenceItem":
        if self.status == EvidenceStatus.ERROR and self.error is None:
            raise ValueError("ERROR evidence requires error detail")
        if (self.raw_ref is None) != (self.raw_sha256 is None):
            raise ValueError("raw_ref and raw_sha256 must be provided together")
        if self.collected_at.utcoffset() is None:
            raise ValueError("collected_at must be timezone-aware")
        if self.source_timestamp is not None:
            if self.source_timestamp.utcoffset() is None:
                raise ValueError("source_timestamp must be timezone-aware")
            if self.source_timestamp > self.collected_at:
                raise ValueError("source_timestamp must not exceed collected_at")
        return self


class EvidenceBundle(StrictModel):
    schema_version: Literal["ops.evidence.v1"] = "ops.evidence.v1"
    bundle_id: str
    incident_id: str
    scenario: Literal["worker_backlog"] = "worker_backlog"
    cluster_profile: str
    scope: Scope
    context: BundleContext
    collection: CollectionMetadata
    evidence: list[EvidenceItem] = Field(max_length=2048)

    @model_validator(mode="after")
    def evidence_ids_are_unique(self) -> "EvidenceBundle":
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence_id values must be unique within a bundle")
        if any(
            item.collected_at > self.collection.completed_at
            for item in self.evidence
        ):
            raise ValueError(
                "evidence collected_at must not exceed collection completion"
            )
        for item in self.evidence:
            if item.freshness.status not in {
                FreshnessStatus.FRESH,
                FreshnessStatus.STALE,
            }:
                continue
            if item.source_timestamp is None:
                raise ValueError("fresh or stale evidence requires source_timestamp")
            expected_age = (
                self.collection.completed_at - item.source_timestamp
            ).total_seconds()
            if expected_age < 0:
                raise ValueError(
                    "freshness source_timestamp must not exceed collection completion"
                )
            assert item.freshness.age_seconds is not None
            if item.freshness.age_seconds + 0.05 < expected_age:
                raise ValueError(
                    "freshness age must not understate source age at completion"
                )
        return self
