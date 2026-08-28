"""Controlled normalized evidence acquisition for the local Scenario Lab."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import Field, model_validator

from ops_agent.diagnosis_models import (
    DiagnosisEvidenceStatus,
    DiagnosisFreshness,
)
from ops_agent.diagnosis_tools import TOOL_BY_ID, TOOL_SPECS
from ops_agent.diagnosis_v2_models import (
    ControlledScenarioProvenance,
    DiagnosisAcquisitionMode,
    DiagnosisEvidenceV2,
)
from ops_agent.evaluation_models import FrozenModel, canonical_sha256
from ops_agent.models import FreshnessStatus


SCENARIO_CONTRACT_VERSION = "ops.diagnosis.scenario.v1"
SCENARIO_REGISTRY_VERSION = "ops.diagnosis.tools.v2"


class ScenarioBranchExpectation(FrozenModel):
    after_tool_id: str = Field(min_length=1, max_length=128)
    expected_next_tools: list[str] = Field(default_factory=list, max_length=16)


class ScenarioObservation(FrozenModel):
    status: DiagnosisEvidenceStatus = DiagnosisEvidenceStatus.OK
    observed_at: datetime
    freshness: FreshnessStatus = FreshnessStatus.FRESH
    max_age_seconds: float | None = Field(default=15.0, gt=0)
    metrics: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_observation(self) -> "ScenarioObservation":
        if self.observed_at.utcoffset() is None:
            raise ValueError("scenario observed_at must be timezone-aware")
        failed = self.status in {
            DiagnosisEvidenceStatus.ERROR,
            DiagnosisEvidenceStatus.UNAVAILABLE,
        }
        if failed != (self.error_code is not None):
            raise ValueError("failed scenario observations require only an error_code")
        if failed and self.metrics:
            raise ValueError("failed scenario observations cannot contain invented metrics")
        return self


class ScenarioDefinition(FrozenModel):
    fixture_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=128)
    fixture_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_primary_hypothesis: str = Field(min_length=1, max_length=128)
    branch_expectations: list[ScenarioBranchExpectation] = Field(
        min_length=1,
        max_length=8,
    )
    observations: dict[str, ScenarioObservation]

    @model_validator(mode="after")
    def validate_tools(self) -> "ScenarioDefinition":
        if set(self.observations) != set(TOOL_BY_ID):
            raise ValueError("each scenario must define every allowlisted tool observation")
        unknown = {
            expectation.after_tool_id
            for expectation in self.branch_expectations
            if expectation.after_tool_id not in TOOL_BY_ID
        }
        unknown.update(
            tool_id
            for expectation in self.branch_expectations
            for tool_id in expectation.expected_next_tools
            if tool_id not in TOOL_BY_ID
        )
        if unknown:
            raise ValueError(f"scenario branch expectation uses unknown tools: {sorted(unknown)}")
        return self

    def verify_integrity(self) -> None:
        payload = self.model_dump(mode="json", exclude={"fixture_digest"})
        if self.fixture_digest != canonical_sha256(payload):
            raise ValueError(f"scenario fixture digest mismatch: {self.fixture_id}")


class ScenarioCatalog(FrozenModel):
    schema_version: str = Field(pattern=r"^ops\.diagnosis\.scenario-catalog\.v1$")
    scenario_contract_version: str = Field(pattern=r"^ops\.diagnosis\.scenario\.v1$")
    activation: dict[str, Any]
    thresholds: dict[str, float]
    scenarios: list[ScenarioDefinition] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def validate_catalog(self) -> "ScenarioCatalog":
        fixture_ids = [item.fixture_id for item in self.scenarios]
        if len(fixture_ids) != len(set(fixture_ids)):
            raise ValueError("scenario fixture IDs must be unique")
        if self.activation.get("condition") != "CORE_BACKLOG_PRESSURE":
            raise ValueError("scenario catalog must preserve CORE_BACKLOG_PRESSURE")
        if self.activation.get("state") != "PRESENT":
            raise ValueError("scenario catalog requires a frozen PRESENT activation")
        digests = self.activation.get("source_bundle_digests")
        if not isinstance(digests, list) or not digests:
            raise ValueError("scenario activation requires source bundle digests")
        required_thresholds = {
            "worker_stage_elevated_ratio",
            "postgres_max_replication_delay_bytes",
        }
        if not required_thresholds <= set(self.thresholds):
            raise ValueError("scenario normalizer thresholds are incomplete")
        for scenario in self.scenarios:
            scenario.verify_integrity()
        return self

    def scenario(self, fixture_id: str) -> ScenarioDefinition:
        for item in self.scenarios:
            if item.fixture_id == fixture_id:
                return item
        raise ValueError(f"unknown scenario fixture: {fixture_id}")


class DiagnosisEvidenceRegistry(Protocol):
    @property
    def tool_ids(self) -> tuple[str, ...]: ...

    @property
    def called_tool_ids(self) -> frozenset[str]: ...

    def function_tools(self) -> list[dict[str, Any]]: ...

    def execute(self, tool_id: str) -> DiagnosisEvidenceV2: ...


def load_scenario_catalog(path: Path) -> ScenarioCatalog:
    if path.is_symlink() or not path.is_file():
        raise ValueError("scenario catalog must be a regular non-symlink file")
    return ScenarioCatalog.model_validate_json(path.read_bytes())


class ControlledScenarioRegistry:
    """Zero-argument tool registry over one immutable controlled fixture."""

    def __init__(self, *, catalog: ScenarioCatalog, fixture_id: str) -> None:
        self.catalog = catalog
        self.scenario = catalog.scenario(fixture_id)
        self._called: set[str] = set()

    @property
    def tool_ids(self) -> tuple[str, ...]:
        return tuple(item.tool_id for item in TOOL_SPECS)

    @property
    def called_tool_ids(self) -> frozenset[str]:
        return frozenset(self._called)

    def function_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "name": item.tool_id,
                "description": item.description,
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
            }
            for item in TOOL_SPECS
            if item.tool_id not in self._called
        ]

    def execute(self, tool_id: str) -> DiagnosisEvidenceV2:
        if tool_id not in TOOL_BY_ID:
            raise ValueError(f"tool is not allowlisted: {tool_id}")
        if tool_id in self._called:
            raise ValueError(f"tool may not be repeated: {tool_id}")
        self._called.add(tool_id)
        observation = self.scenario.observations[tool_id]
        summary = self._normalize(tool_id, observation.metrics)
        identity = {
            "tool_registry_version": SCENARIO_REGISTRY_VERSION,
            "tool_id": tool_id,
            "fixture_id": self.scenario.fixture_id,
            "fixture_digest": self.scenario.fixture_digest,
            "status": observation.status.value,
            "observed_at": observation.observed_at.isoformat(),
            "summary": summary,
            "error_code": observation.error_code,
        }
        return DiagnosisEvidenceV2(
            evidence_id=f"diagnosis.{tool_id}.{canonical_sha256(identity)[:24]}",
            tool_id=tool_id,
            status=observation.status,
            observed_at=observation.observed_at,
            freshness=DiagnosisFreshness(
                status=observation.freshness,
                oldest_source_timestamp=(
                    observation.observed_at
                    if observation.freshness != FreshnessStatus.UNKNOWN
                    else None
                ),
                newest_source_timestamp=(
                    observation.observed_at
                    if observation.freshness != FreshnessStatus.UNKNOWN
                    else None
                ),
                max_age_seconds=observation.max_age_seconds,
            ),
            semantic_type=f"normalized_{tool_id}_observation",
            summary=summary,
            provenance=ControlledScenarioProvenance(
                acquisition_mode=DiagnosisAcquisitionMode.CONTROLLED_SCENARIO,
                fixture_id=self.scenario.fixture_id,
                fixture_digest=self.scenario.fixture_digest,
            ),
            error_code=observation.error_code,
        )

    def _normalize(self, tool_id: str, metrics: dict[str, Any]) -> dict[str, Any]:
        if not metrics:
            return {"observation": "UNAVAILABLE"}
        if tool_id == "get_worker_stage_latency":
            value = float(metrics["worker_stage_mean_ms"])
            baseline = float(metrics["scenario_baseline_ms"])
            ratio = value / baseline if baseline > 0 else None
            threshold = self.catalog.thresholds["worker_stage_elevated_ratio"]
            return {
                **metrics,
                "relative_to_baseline": ratio,
                "semantic_flag": (
                    "ABOVE_SCENARIO_BASELINE"
                    if ratio is not None and ratio >= threshold
                    else "WITHIN_SCENARIO_BASELINE"
                ),
                "commit_latency": "UNAVAILABLE",
            }
        if tool_id == "get_worker_replica_status":
            desired = int(metrics["desired_replicas"])
            available = int(metrics["available_replicas"])
            return {
                **metrics,
                "availability_gap": max(desired - available, 0),
                "semantic_flag": (
                    "WORKER_CAPACITY_SHORTFALL"
                    if available < desired
                    else "WORKER_CAPACITY_AVAILABLE"
                ),
            }
        if tool_id == "get_postgres_health":
            delay = int(metrics["max_replication_delay_bytes"])
            degraded = (
                not bool(metrics["ha_mode"])
                or not bool(metrics["primary_reachable"])
                or int(metrics["standby_count"]) < int(metrics["required_standby_count"])
                or int(metrics["sync_standby_count"])
                < int(metrics["required_sync_standby_count"])
                or delay
                > self.catalog.thresholds["postgres_max_replication_delay_bytes"]
            )
            return {
                **metrics,
                "semantic_flag": (
                    "POSTGRES_PATH_DEGRADED" if degraded else "POSTGRES_PATH_HEALTHY"
                ),
            }
        if tool_id == "get_partition_lag":
            partitions = metrics["per_partition_lag"]
            total = sum(int(value) for value in partitions.values())
            maximum = max((int(value) for value in partitions.values()), default=0)
            return {
                **metrics,
                "total_lag_records": total,
                "maximum_partition_share": maximum / total if total else 0.0,
            }
        if tool_id == "get_runtime_image":
            desired = metrics["desired_image"]
            observed = list(metrics["pod_images"])
            return {
                **metrics,
                "semantic_flag": (
                    "RUNTIME_IMAGE_MATCH"
                    if observed and all(item == desired for item in observed)
                    else "RUNTIME_IMAGE_MISMATCH"
                ),
            }
        return dict(metrics)


__all__ = [
    "ControlledScenarioRegistry",
    "DiagnosisEvidenceRegistry",
    "SCENARIO_CONTRACT_VERSION",
    "SCENARIO_REGISTRY_VERSION",
    "ScenarioBranchExpectation",
    "ScenarioCatalog",
    "ScenarioDefinition",
    "ScenarioObservation",
    "load_scenario_catalog",
]
