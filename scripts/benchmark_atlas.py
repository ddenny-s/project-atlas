#!/usr/bin/env python3
"""Reproduce Project Atlas effectiveness calculations from explicit inputs.

This helper deliberately separates:

* MODELLED_ASSUMPTION scenarios, which are forecasts with visible inputs; and
* MEASURED receipts, which may use only provider-reported token totals.

It never infers subscription quota percentages from tokens and never stores raw
prompts, transcripts, local absolute paths, or repository contents.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import sys
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable


SCHEMA_VERSION = "0.1"
MAX_JSON_BYTES = 1_048_576
MAX_RECEIPTS = 10_000
MIN_WALL_TIME_MS = 100
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
FORBIDDEN_KEYS = {
    "api_key",
    "credential",
    "local_path",
    "output_text",
    "prompt",
    "raw_output",
    "secret",
    "token_value",
    "transcript",
}
CONDITIONS = {"BASELINE", "ATLAS_BUILD", "ATLAS_USE", "ATLAS_REFRESH"}
RESULT_STATUSES = {"PASS", "FAIL", "ERROR"}
WALL_TIME_SOURCES = {"HOST_MONOTONIC"}
RECEIPT_KEYS = {
    "acceptance_contract_sha256",
    "adapter",
    "adapter_version",
    "campaign_id",
    "classification",
    "condition",
    "counterbalance_position",
    "effective_model",
    "ended_at",
    "fixture",
    "fixture_sha256",
    "fresh_session",
    "input_atlas_sha256",
    "oracle_sha256",
    "output_atlas_sha256",
    "pair_id",
    "permission_profile_sha256",
    "reasoning_effort",
    "requested_model",
    "result",
    "revision",
    "run_id",
    "schema_version",
    "started_at",
    "task_id",
    "task_sha256",
    "tokens",
    "wall_time_ms",
    "wall_time_source",
}
MODELLED_ROOT_KEYS = {
    "as_of",
    "caveat",
    "classification",
    "horizons",
    "scenarios",
    "schema_version",
}
MODELLED_SCENARIO_KEYS = {
    "atlas_build_minutes",
    "atlas_build_tokens",
    "atlas_contract_pass_percent",
    "atlas_use_minutes_per_task",
    "atlas_use_tokens_per_task",
    "baseline_contract_pass_percent",
    "baseline_minutes_per_task",
    "baseline_tokens_per_task",
    "id",
    "refresh_minutes_per_task",
    "refresh_tokens_per_task",
}
TOKEN_KEYS = {"cached", "input", "output", "reasoning", "source", "total"}
RESULT_KEYS = {
    "contract_pass",
    "dangling_refs",
    "expected_items",
    "observed_items",
    "output_sha256",
    "status",
    "unclassified_claims",
    "unsafe_refs",
}


class BenchmarkError(ValueError):
    """A bounded benchmark input is invalid."""


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def oracle_digest(expected_items: list[str]) -> str:
    payload = json.dumps(
        {"expected_items": sorted(expected_items)},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256((payload + "\n").encode("utf-8")).hexdigest()


def read_json_file(path: Path) -> Any:
    if path.is_symlink():
        raise BenchmarkError(f"symbolic links are not accepted: {path.name}")
    if not path.is_file():
        raise BenchmarkError(f"JSON input is not a regular file: {path}")
    size = path.stat().st_size
    if size > MAX_JSON_BYTES:
        raise BenchmarkError(f"JSON input exceeds {MAX_JSON_BYTES} bytes: {path.name}")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise BenchmarkError(f"JSON input is not UTF-8: {path.name}") from exc
    try:
        return json.loads(text, parse_float=Decimal)
    except json.JSONDecodeError as exc:
        raise BenchmarkError(f"invalid JSON in {path.name}: {exc}") from exc


def require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BenchmarkError(f"{label} must be a JSON object")
    return value


def require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise BenchmarkError(f"{label} must be a JSON array")
    return value


def require_string(value: Any, label: str, *, substantive: bool = True) -> str:
    if not isinstance(value, str):
        raise BenchmarkError(f"{label} must be a string")
    if substantive and not value.strip():
        raise BenchmarkError(f"{label} must not be empty")
    return value


def require_safe_id(value: Any, label: str) -> str:
    text = require_string(value, label)
    if not SAFE_ID_RE.fullmatch(text):
        raise BenchmarkError(f"{label} is not a safe bounded identifier")
    return text


def reject_unknown_fields(
    value: dict[str, Any], allowed: set[str], label: str
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise BenchmarkError(f"{label} contains unknown fields: {unknown!r}")


def require_number(
    value: Any,
    label: str,
    *,
    integer: bool = False,
    minimum: float = 0,
) -> Decimal | float | int:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, float)):
        raise BenchmarkError(f"{label} must be numeric")
    if integer and not isinstance(value, int):
        raise BenchmarkError(f"{label} must be an integer")
    if not math.isfinite(float(value)) or float(value) < minimum:
        raise BenchmarkError(f"{label} must be finite and >= {minimum}")
    return value


def require_hash(value: Any, label: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    text = require_string(value, label)
    if not HASH_RE.fullmatch(text):
        raise BenchmarkError(f"{label} must be a lowercase SHA-256 digest")
    return text


def parse_utc(value: Any, label: str) -> datetime:
    text = require_string(value, label)
    if not text.endswith("Z"):
        raise BenchmarkError(f"{label} must be a UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise BenchmarkError(f"{label} is not a real timestamp") from exc
    if parsed.tzinfo != timezone.utc:
        raise BenchmarkError(f"{label} must be UTC")
    return parsed


def reject_private_shapes(value: Any, label: str = "root") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if lowered in FORBIDDEN_KEYS:
                raise BenchmarkError(f"{label} contains forbidden field {key!r}")
            reject_private_shapes(child, f"{label}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            reject_private_shapes(child, f"{label}[{index}]")
        return
    if not isinstance(value, str):
        return

    text = value.strip()
    if text.startswith(("file://", "~/", "/Users/", "/home/", "/private/")):
        raise BenchmarkError(f"{label} contains a local absolute path")
    if PurePosixPath(text).is_absolute() or PureWindowsPath(text).is_absolute():
        raise BenchmarkError(f"{label} contains a local absolute path")


def round_percent(value: float) -> float:
    return round(value, 1)


def clean_number(value: float | int) -> float | int:
    numeric = float(value)
    return int(numeric) if numeric.is_integer() else numeric


def break_even(build_cost: float, per_task_saving: float) -> int | None:
    if per_task_saving <= 0:
        return None
    return math.ceil(build_cost / per_task_saving)


def require_decimal(value: Any, label: str, *, minimum: int = 0) -> Decimal:
    checked = require_number(value, label, minimum=minimum)
    decimal = checked if isinstance(checked, Decimal) else Decimal(str(checked))
    if not decimal.is_finite() or decimal < Decimal(minimum):
        raise BenchmarkError(f"{label} must be finite and >= {minimum}")
    return decimal


def clean_decimal(value: Decimal) -> float | int:
    return int(value) if value == value.to_integral_value() else float(value)


def round_decimal_percent(value: Decimal) -> float:
    rounded = value.quantize(Decimal("0.1"), rounding=ROUND_HALF_EVEN)
    return float(rounded)


def decimal_break_even(
    build_cost: Decimal,
    per_task_saving: Decimal,
) -> int | None:
    if per_task_saving <= 0:
        return None
    build_numerator, build_denominator = build_cost.as_integer_ratio()
    saving_numerator, saving_denominator = per_task_saving.as_integer_ratio()
    numerator = build_numerator * saving_denominator
    denominator = build_denominator * saving_numerator
    return (numerator + denominator - 1) // denominator


def modelled_horizon_result(
    *,
    horizon: int,
    build_cost: Decimal,
    baseline_per_task: Decimal,
    atlas_per_task: Decimal,
    refresh_per_task: Decimal,
) -> dict[str, Any]:
    baseline_total = baseline_per_task * horizon
    atlas_total = build_cost + (atlas_per_task + refresh_per_task) * horizon
    net_saving = baseline_total - atlas_total
    return {
        "atlas_total": clean_decimal(atlas_total),
        "baseline_total": clean_decimal(baseline_total),
        "horizon_tasks": horizon,
        "net_saving": clean_decimal(net_saving),
        "net_saving_percent_of_baseline": round_decimal_percent(
            net_saving / baseline_total * Decimal(100)
        ),
        "roi_on_build_percent": round_decimal_percent(
            net_saving / build_cost * Decimal(100)
        ),
    }


def modelled_summary(payload: Any) -> dict[str, Any]:
    root = require_object(payload, "modelled input")
    reject_unknown_fields(root, MODELLED_ROOT_KEYS, "modelled input")
    if root.get("schema_version") != SCHEMA_VERSION:
        raise BenchmarkError(f"schema_version must be {SCHEMA_VERSION!r}")
    if root.get("classification") != "MODELLED_ASSUMPTION":
        raise BenchmarkError("classification must be MODELLED_ASSUMPTION")
    as_of = require_string(root.get("as_of"), "as_of")
    parse_utc(f"{as_of}T00:00:00Z", "as_of")
    caveat = require_string(root.get("caveat"), "caveat")
    horizons = [
        int(require_number(item, "horizon", integer=True, minimum=1))
        for item in require_list(root.get("horizons"), "horizons")
    ]
    if not horizons or horizons != sorted(set(horizons)):
        raise BenchmarkError("horizons must be a non-empty sorted unique list")

    scenarios: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(require_list(root.get("scenarios"), "scenarios")):
        scenario = require_object(raw, f"scenarios[{index}]")
        reject_unknown_fields(
            scenario,
            MODELLED_SCENARIO_KEYS,
            f"scenarios[{index}]",
        )
        scenario_id = require_safe_id(scenario.get("id"), f"scenarios[{index}].id")
        if scenario_id in seen_ids:
            raise BenchmarkError(f"duplicate scenario id: {scenario_id}")
        seen_ids.add(scenario_id)

        values: dict[str, Decimal] = {}
        for key in (
            "atlas_build_tokens",
            "atlas_build_minutes",
            "baseline_contract_pass_percent",
            "atlas_contract_pass_percent",
            "baseline_tokens_per_task",
            "atlas_use_tokens_per_task",
            "refresh_tokens_per_task",
            "baseline_minutes_per_task",
            "atlas_use_minutes_per_task",
            "refresh_minutes_per_task",
        ):
            values[key] = require_decimal(
                scenario.get(key),
                f"{scenario_id}.{key}",
            )
        for key in (
            "baseline_contract_pass_percent",
            "atlas_contract_pass_percent",
        ):
            if values[key] > 100:
                raise BenchmarkError(f"{scenario_id}.{key} exceeds 100")

        if values["atlas_build_tokens"] <= 0 or values["atlas_build_minutes"] <= 0:
            raise BenchmarkError(f"{scenario_id} build cost must be positive")
        if (
            values["baseline_tokens_per_task"] <= 0
            or values["baseline_minutes_per_task"] <= 0
        ):
            raise BenchmarkError(f"{scenario_id} baseline cost must be positive")

        token_saving = (
            values["baseline_tokens_per_task"]
            - values["atlas_use_tokens_per_task"]
            - values["refresh_tokens_per_task"]
        )
        minute_saving = (
            values["baseline_minutes_per_task"]
            - values["atlas_use_minutes_per_task"]
            - values["refresh_minutes_per_task"]
        )
        quality_delta = (
            values["atlas_contract_pass_percent"]
            - values["baseline_contract_pass_percent"]
        )
        quality_relative = (
            quality_delta
            / values["baseline_contract_pass_percent"]
            * Decimal(100)
            if values["baseline_contract_pass_percent"] > 0
            else None
        )
        scenarios.append(
            {
                "assumptions": {
                    key: clean_decimal(value)
                    for key, value in values.items()
                },
                "id": scenario_id,
                "quality": {
                    "atlas_contract_pass_percent": clean_decimal(
                        values["atlas_contract_pass_percent"]
                    ),
                    "baseline_contract_pass_percent": clean_decimal(
                        values["baseline_contract_pass_percent"]
                    ),
                    "contract_pass_delta_points": round_decimal_percent(
                        quality_delta
                    ),
                    "contract_pass_relative_improvement_percent": (
                        round_decimal_percent(quality_relative)
                        if quality_relative is not None
                        else None
                    ),
                },
                "time": {
                    "break_even_tasks": decimal_break_even(
                        values["atlas_build_minutes"], minute_saving
                    ),
                    "horizons": [
                        modelled_horizon_result(
                            horizon=horizon,
                            build_cost=values["atlas_build_minutes"],
                            baseline_per_task=values["baseline_minutes_per_task"],
                            atlas_per_task=values["atlas_use_minutes_per_task"],
                            refresh_per_task=values["refresh_minutes_per_task"],
                        )
                        for horizon in horizons
                    ],
                    "per_task_saving_minutes": clean_decimal(minute_saving),
                },
                "tokens": {
                    "break_even_tasks": decimal_break_even(
                        values["atlas_build_tokens"], token_saving
                    ),
                    "horizons": [
                        modelled_horizon_result(
                            horizon=horizon,
                            build_cost=values["atlas_build_tokens"],
                            baseline_per_task=values["baseline_tokens_per_task"],
                            atlas_per_task=values["atlas_use_tokens_per_task"],
                            refresh_per_task=values["refresh_tokens_per_task"],
                        )
                        for horizon in horizons
                    ],
                    "per_task_saving_tokens": clean_decimal(token_saving),
                },
            }
        )

    if not scenarios:
        raise BenchmarkError("at least one modelled scenario is required")
    return {
        "as_of": as_of,
        "caveat": caveat,
        "classification": "MODELLED_ASSUMPTION",
        "formulae": {
            "break_even_tasks": "ceil(build_cost / per_task_saving), only when per_task_saving > 0",
            "contract_pass_delta_points": "atlas_contract_pass_percent - baseline_contract_pass_percent",
            "contract_pass_relative_improvement_percent": "(atlas_contract_pass_percent - baseline_contract_pass_percent) / baseline_contract_pass_percent * 100",
            "net_saving": "baseline_per_task * N - build_cost - (atlas_use_per_task + refresh_per_task) * N",
            "net_saving_percent": "net_saving / (baseline_per_task * N) * 100",
            "per_task_saving": "baseline_per_task - atlas_use_per_task - refresh_per_task",
            "roi_on_build": "net_saving / build_cost * 100",
        },
        "schema_version": SCHEMA_VERSION,
        "scenarios": scenarios,
    }


def optional_token(value: Any, label: str) -> int | None:
    if value is None:
        return None
    return int(require_number(value, label, integer=True, minimum=0))


def validate_receipt(payload: Any, *, source_name: str = "receipt") -> dict[str, Any]:
    reject_private_shapes(payload, source_name)
    root = require_object(payload, source_name)
    reject_unknown_fields(root, RECEIPT_KEYS, source_name)
    if root.get("schema_version") != SCHEMA_VERSION:
        raise BenchmarkError(f"{source_name}.schema_version must be {SCHEMA_VERSION!r}")
    if root.get("classification") != "MEASURED":
        raise BenchmarkError(f"{source_name}.classification must be MEASURED")

    for key in (
        "campaign_id",
        "run_id",
        "fixture",
        "task_id",
        "adapter",
        "adapter_version",
        "requested_model",
        "effective_model",
        "reasoning_effort",
        "revision",
    ):
        require_safe_id(root.get(key), f"{source_name}.{key}")

    condition = require_string(root.get("condition"), f"{source_name}.condition")
    if condition not in CONDITIONS:
        raise BenchmarkError(f"{source_name}.condition is unsupported")
    pair_id = root.get("pair_id")
    if condition in {"BASELINE", "ATLAS_USE"}:
        require_safe_id(pair_id, f"{source_name}.pair_id")
    elif pair_id is not None:
        require_safe_id(pair_id, f"{source_name}.pair_id")

    if root.get("fresh_session") is not True:
        raise BenchmarkError(f"{source_name}.fresh_session must be true")
    require_number(
        root.get("counterbalance_position"),
        f"{source_name}.counterbalance_position",
        integer=True,
        minimum=1,
    )
    require_hash(root.get("fixture_sha256"), f"{source_name}.fixture_sha256")
    require_hash(root.get("task_sha256"), f"{source_name}.task_sha256")
    acceptance_contract_hash = require_hash(
        root.get("acceptance_contract_sha256"),
        f"{source_name}.acceptance_contract_sha256",
        nullable=True,
    )
    if condition in {"BASELINE", "ATLAS_USE"}:
        if acceptance_contract_hash is None:
            raise BenchmarkError(
                f"{source_name} {condition} requires acceptance_contract_sha256"
            )
    elif acceptance_contract_hash is not None:
        raise BenchmarkError(
            f"{source_name} {condition} must not bind an acceptance contract"
        )
    require_hash(
        root.get("permission_profile_sha256"),
        f"{source_name}.permission_profile_sha256",
    )
    input_atlas_hash = require_hash(
        root.get("input_atlas_sha256"),
        f"{source_name}.input_atlas_sha256",
        nullable=True,
    )
    output_atlas_hash = require_hash(
        root.get("output_atlas_sha256"),
        f"{source_name}.output_atlas_sha256",
        nullable=True,
    )
    require_hash(root.get("oracle_sha256"), f"{source_name}.oracle_sha256")
    if condition == "BASELINE" and (
        input_atlas_hash is not None or output_atlas_hash is not None
    ):
        raise BenchmarkError(f"{source_name} BASELINE must not contain map hashes")
    if condition == "ATLAS_BUILD" and (
        input_atlas_hash is not None or output_atlas_hash is None
    ):
        raise BenchmarkError(
            f"{source_name} ATLAS_BUILD requires only output_atlas_sha256"
        )
    if condition == "ATLAS_USE" and (
        input_atlas_hash is None or output_atlas_hash is not None
    ):
        raise BenchmarkError(
            f"{source_name} ATLAS_USE requires only input_atlas_sha256"
        )
    if condition == "ATLAS_REFRESH" and (
        input_atlas_hash is None or output_atlas_hash is None
    ):
        raise BenchmarkError(
            f"{source_name} ATLAS_REFRESH requires input and output map hashes"
        )

    started = parse_utc(root.get("started_at"), f"{source_name}.started_at")
    ended = parse_utc(root.get("ended_at"), f"{source_name}.ended_at")
    if ended <= started:
        raise BenchmarkError(f"{source_name}.ended_at must follow started_at")
    wall_time_ms = int(require_number(
        root.get("wall_time_ms"),
        f"{source_name}.wall_time_ms",
        integer=True,
        minimum=MIN_WALL_TIME_MS,
    ))
    if root.get("wall_time_source") not in WALL_TIME_SOURCES:
        raise BenchmarkError(
            f"{source_name}.wall_time_source must be HOST_MONOTONIC"
        )
    timestamp_duration_ms = (ended - started).total_seconds() * 1000
    if timestamp_duration_ms < MIN_WALL_TIME_MS:
        raise BenchmarkError(
            f"{source_name} timestamp duration is below {MIN_WALL_TIME_MS} ms"
        )
    wall_tolerance_ms = max(5.0, timestamp_duration_ms * 0.02)
    if abs(wall_time_ms - timestamp_duration_ms) > wall_tolerance_ms:
        raise BenchmarkError(
            f"{source_name}.wall_time_ms is inconsistent with timestamps"
        )

    tokens = require_object(root.get("tokens"), f"{source_name}.tokens")
    reject_unknown_fields(tokens, TOKEN_KEYS, f"{source_name}.tokens")
    if tokens.get("source") not in {"PROVIDER_REPORTED", "UNMEASURED"}:
        raise BenchmarkError(
            f"{source_name}.tokens.source must be PROVIDER_REPORTED or UNMEASURED"
        )
    token_values = {
        key: optional_token(tokens.get(key), f"{source_name}.tokens.{key}")
        for key in ("input", "output", "reasoning", "cached", "total")
    }
    if tokens["source"] == "UNMEASURED" and any(
        value is not None for value in token_values.values()
    ):
        raise BenchmarkError(f"{source_name} has token values without provider telemetry")
    if tokens["source"] == "PROVIDER_REPORTED" and token_values["total"] is None:
        raise BenchmarkError(f"{source_name} provider telemetry requires total tokens")
    if tokens["source"] == "PROVIDER_REPORTED" and token_values["total"] == 0:
        raise BenchmarkError(f"{source_name} provider token total must be positive")

    result = require_object(root.get("result"), f"{source_name}.result")
    reject_unknown_fields(result, RESULT_KEYS, f"{source_name}.result")
    if result.get("status") not in RESULT_STATUSES:
        raise BenchmarkError(f"{source_name}.result.status is unsupported")
    if not isinstance(result.get("contract_pass"), bool):
        raise BenchmarkError(f"{source_name}.result.contract_pass must be boolean")
    if result["status"] in {"FAIL", "ERROR"} and result["contract_pass"]:
        raise BenchmarkError(
            f"{source_name} {result['status']} requires contract_pass=false"
        )
    require_hash(result.get("output_sha256"), f"{source_name}.result.output_sha256")
    for key in ("unsafe_refs", "dangling_refs", "unclassified_claims"):
        require_number(
            result.get(key),
            f"{source_name}.result.{key}",
            integer=True,
            minimum=0,
        )
    for key in ("expected_items", "observed_items"):
        items = [
            require_hash(item, f"{source_name}.result.{key}[]")
            for item in require_list(result.get(key), f"{source_name}.result.{key}")
        ]
        if len(items) > 10_000:
            raise BenchmarkError(
                f"{source_name}.result.{key} exceeds 10000 identifiers"
            )
        if len(items) != len(set(items)):
            raise BenchmarkError(f"{source_name}.result.{key} contains duplicates")
    if root["oracle_sha256"] != oracle_digest(result["expected_items"]):
        raise BenchmarkError(
            f"{source_name}.oracle_sha256 does not bind expected_items"
        )
    if condition in {"ATLAS_BUILD", "ATLAS_REFRESH"} and (
        output_atlas_hash != result["output_sha256"]
    ):
        raise BenchmarkError(
            f"{source_name}.output_atlas_sha256 must match the produced map hash"
        )
    return root


def median(values: Iterable[float]) -> float | None:
    collected = list(values)
    if not collected:
        return None
    return statistics.median(collected)


def percent_saving(baseline: float, atlas: float) -> float:
    return (baseline - atlas) / baseline * 100 if baseline > 0 else 0.0


def score_items(receipts: Iterable[dict[str, Any]]) -> dict[str, Any]:
    true_positive = 0
    false_positive = 0
    false_negative = 0
    for receipt in receipts:
        result = receipt["result"]
        expected = set(result["expected_items"])
        observed = set(result["observed_items"])
        true_positive += len(expected & observed)
        false_positive += len(observed - expected)
        false_negative += len(expected - observed)
    precision = (
        true_positive / (true_positive + false_positive)
        if true_positive + false_positive
        else 0.0
    )
    recall = (
        true_positive / (true_positive + false_negative)
        if true_positive + false_negative
        else 0.0
    )
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "f1_percent": round_percent(f1 * 100),
        "false_negative": false_negative,
        "false_positive": false_positive,
        "precision_percent": round_percent(precision * 100),
        "recall_percent": round_percent(recall * 100),
        "true_positive": true_positive,
    }


def reference_integrity_summary(
    baseline_receipts: list[dict[str, Any]],
    atlas_receipts: list[dict[str, Any]],
) -> dict[str, Any]:
    summary: dict[str, Any] = {"sample_n": len(baseline_receipts)}
    for key in ("unsafe_refs", "dangling_refs", "unclassified_claims"):
        baseline_values = [receipt["result"][key] for receipt in baseline_receipts]
        atlas_values = [receipt["result"][key] for receipt in atlas_receipts]
        baseline_total = sum(baseline_values)
        atlas_total = sum(atlas_values)
        paired_deltas = [
            atlas_value - baseline_value
            for baseline_value, atlas_value in zip(
                baseline_values, atlas_values, strict=True
            )
        ]
        summary[key] = {
            "baseline_mean_per_task": clean_number(
                baseline_total / len(baseline_receipts)
            ),
            "baseline_total": baseline_total,
            "paired_delta_mean": clean_number(
                sum(paired_deltas) / len(paired_deltas)
            ),
            "paired_delta_total": sum(paired_deltas),
            "with_atlas_mean_per_task": clean_number(
                atlas_total / len(atlas_receipts)
            ),
            "with_atlas_total": atlas_total,
        }
    return summary


def reject_overlapping_intervals(receipts: list[dict[str, Any]]) -> None:
    ordered = sorted(
        receipts,
        key=lambda receipt: parse_utc(
            receipt["started_at"],
            f"{receipt['run_id']}.started_at",
        ),
    )
    for previous, current in zip(ordered, ordered[1:], strict=False):
        previous_ended = parse_utc(
            previous["ended_at"],
            f"{previous['run_id']}.ended_at",
        )
        current_started = parse_utc(
            current["started_at"],
            f"{current['run_id']}.started_at",
        )
        if current_started < previous_ended:
            raise BenchmarkError(
                "overlapping measured intervals: "
                f"{previous['run_id']} ({previous['condition']}) and "
                f"{current['run_id']} ({current['condition']})"
            )


def measured_summary(receipts: list[dict[str, Any]]) -> dict[str, Any]:
    if not receipts:
        raise BenchmarkError("at least one measured receipt is required")
    campaign_ids = {receipt["campaign_id"] for receipt in receipts}
    if len(campaign_ids) != 1:
        raise BenchmarkError("all receipts must belong to one campaign")
    reject_overlapping_intervals(receipts)

    by_pair: dict[str, dict[str, dict[str, Any]]] = {}
    builds: list[dict[str, Any]] = []
    refreshes: list[dict[str, Any]] = []
    for receipt in receipts:
        condition = receipt["condition"]
        if condition == "ATLAS_BUILD":
            builds.append(receipt)
            continue
        if condition == "ATLAS_REFRESH":
            refreshes.append(receipt)
            continue
        pair = by_pair.setdefault(receipt["pair_id"], {})
        if condition in pair:
            raise BenchmarkError(
                f"pair {receipt['pair_id']} has duplicate {condition} receipts"
            )
        pair[condition] = receipt

    if not by_pair:
        raise BenchmarkError("no BASELINE/ATLAS_USE pairs were found")

    comparable_fields = (
        "fixture",
        "task_id",
        "acceptance_contract_sha256",
        "adapter",
        "adapter_version",
        "requested_model",
        "effective_model",
        "reasoning_effort",
        "revision",
        "fixture_sha256",
        "oracle_sha256",
        "task_sha256",
        "permission_profile_sha256",
    )
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for pair_id, pair in sorted(by_pair.items()):
        if set(pair) != {"BASELINE", "ATLAS_USE"}:
            raise BenchmarkError(f"pair {pair_id} is incomplete")
        baseline = pair["BASELINE"]
        atlas = pair["ATLAS_USE"]
        for field in comparable_fields:
            if baseline[field] != atlas[field]:
                raise BenchmarkError(f"pair {pair_id} differs in {field}")
        if set(baseline["result"]["expected_items"]) != set(
            atlas["result"]["expected_items"]
        ):
            raise BenchmarkError(f"pair {pair_id} differs in expected oracle items")
        if {
            baseline["counterbalance_position"],
            atlas["counterbalance_position"],
        } != {1, 2}:
            raise BenchmarkError(
                f"pair {pair_id} must use counterbalance positions 1 and 2"
            )
        first = (
            baseline
            if baseline["counterbalance_position"] == 1
            else atlas
        )
        second = atlas if first is baseline else baseline
        if parse_utc(first["ended_at"], f"pair {pair_id} first ended_at") > parse_utc(
            second["started_at"], f"pair {pair_id} second started_at"
        ):
            raise BenchmarkError(
                f"pair {pair_id} counterbalance positions contradict timestamps"
            )
        pairs.append((baseline, atlas))

    campaign_fields = (
        "fixture",
        "adapter",
        "adapter_version",
        "requested_model",
        "effective_model",
        "reasoning_effort",
        "revision",
        "fixture_sha256",
        "permission_profile_sha256",
    )
    first_baseline, _ = pairs[0]
    task_id_to_hash: dict[str, str] = {}
    seen_task_hashes: set[str] = set()
    for baseline, _ in pairs:
        task_id = baseline["task_id"]
        task_hash = baseline["task_sha256"]
        if task_id in task_id_to_hash or task_hash in seen_task_hashes:
            raise BenchmarkError(
                f"duplicate task identity in campaign: {task_id}"
            )
        task_id_to_hash[task_id] = task_hash
        seen_task_hashes.add(task_hash)
    for baseline, atlas in pairs[1:]:
        for field in campaign_fields:
            if baseline[field] != first_baseline[field]:
                raise BenchmarkError(
                    f"campaign pairs differ in {field}; break-even would mix contexts"
                )

    if len(builds) != 1:
        raise BenchmarkError("campaign requires exactly one ATLAS_BUILD")
    build = builds[0]
    for field in (
        "fixture",
        "adapter",
        "adapter_version",
        "revision",
        "fixture_sha256",
        "permission_profile_sha256",
    ):
        if build[field] != first_baseline[field]:
            raise BenchmarkError(f"ATLAS_BUILD differs from campaign in {field}")

    for refresh in refreshes:
        for field in (
            "fixture",
            "adapter",
            "adapter_version",
            "revision",
            "fixture_sha256",
            "permission_profile_sha256",
        ):
            if refresh[field] != first_baseline[field]:
                raise BenchmarkError(
                    f"ATLAS_REFRESH differs from campaign in {field}"
                )

    producers = [build]
    current_hash = build["output_atlas_sha256"]
    current_ready_at = parse_utc(build["ended_at"], "ATLAS_BUILD.ended_at")
    for refresh in sorted(
        refreshes,
        key=lambda receipt: parse_utc(
            receipt["started_at"], "ATLAS_REFRESH.started_at"
        ),
    ):
        refresh_started = parse_utc(
            refresh["started_at"], "ATLAS_REFRESH.started_at"
        )
        if (
            refresh_started < current_ready_at
            or refresh["input_atlas_sha256"] != current_hash
        ):
            raise BenchmarkError("ATLAS_REFRESH breaks map lineage")
        producers.append(refresh)
        current_hash = refresh["output_atlas_sha256"]
        current_ready_at = parse_utc(
            refresh["ended_at"], "ATLAS_REFRESH.ended_at"
        )

    for _, atlas in pairs:
        use_started = parse_utc(atlas["started_at"], "ATLAS_USE.started_at")
        available = [
            producer
            for producer in producers
            if parse_utc(producer["ended_at"], "map producer ended_at")
            <= use_started
        ]
        if not available:
            raise BenchmarkError("ATLAS_USE starts before map lineage exists")
        latest = max(
            available,
            key=lambda receipt: parse_utc(
                receipt["ended_at"], "map producer ended_at"
            ),
        )
        if atlas["input_atlas_sha256"] != latest["output_atlas_sha256"]:
            raise BenchmarkError("ATLAS_USE breaks map lineage")

    baseline_first = sum(
        baseline["counterbalance_position"] == 1 for baseline, _ in pairs
    )
    atlas_first = len(pairs) - baseline_first
    if abs(baseline_first - atlas_first) > 1:
        raise BenchmarkError("campaign order is not counterbalanced across pairs")

    time_savings_ms = [
        baseline["wall_time_ms"] - atlas["wall_time_ms"]
        for baseline, atlas in pairs
    ]
    time_percentages = [
        percent_saving(baseline["wall_time_ms"], atlas["wall_time_ms"])
        for baseline, atlas in pairs
    ]
    exact_token_pairs = [
        (baseline, atlas)
        for baseline, atlas in pairs
        if baseline["tokens"]["total"] is not None
        and atlas["tokens"]["total"] is not None
    ]
    token_savings = [
        baseline["tokens"]["total"] - atlas["tokens"]["total"]
        for baseline, atlas in exact_token_pairs
    ]
    token_percentages = [
        percent_saving(baseline["tokens"]["total"], atlas["tokens"]["total"])
        for baseline, atlas in exact_token_pairs
    ]

    build_time_ms = (
        sum(receipt["wall_time_ms"] for receipt in builds) if builds else None
    )
    refresh_time_ms = sum(receipt["wall_time_ms"] for receipt in refreshes)
    exact_build_tokens = [
        receipt["tokens"]["total"]
        for receipt in builds
        if receipt["tokens"]["total"] is not None
    ]
    exact_refresh_tokens = [
        receipt["tokens"]["total"]
        for receipt in refreshes
        if receipt["tokens"]["total"] is not None
    ]
    build_tokens = (
        sum(exact_build_tokens)
        if builds and len(exact_build_tokens) == len(builds)
        else None
    )
    refresh_tokens = (
        sum(exact_refresh_tokens)
        if len(exact_refresh_tokens) == len(refreshes)
        else None
    )

    median_time_saving = median(time_savings_ms)
    median_token_saving = median(token_savings)
    amortized_refresh_time_ms = refresh_time_ms / len(pairs)
    amortized_refresh_tokens = (
        refresh_tokens / len(pairs) if refresh_tokens is not None else None
    )
    exact_token_costs_complete = (
        len(exact_token_pairs) == len(pairs)
        and build_tokens is not None
        and refresh_tokens is not None
    )
    median_net_time_saving = (
        float(median_time_saving) - amortized_refresh_time_ms
        if median_time_saving is not None
        else None
    )
    median_net_token_saving = (
        float(median_token_saving) - amortized_refresh_tokens
        if exact_token_costs_complete
        and median_token_saving is not None
        and amortized_refresh_tokens is not None
        else None
    )
    time_break_even = (
        break_even(build_time_ms, median_net_time_saving)
        if build_time_ms is not None and median_net_time_saving is not None
        else None
    )
    token_break_even = (
        break_even(build_tokens, median_net_token_saving)
        if build_tokens is not None and median_net_token_saving is not None
        else None
    )

    baseline_receipts = [baseline for baseline, _ in pairs]
    atlas_receipts = [atlas for _, atlas in pairs]
    baseline_contract_passes = sum(
        receipt["result"]["contract_pass"] for receipt in baseline_receipts
    )
    atlas_contract_passes = sum(
        receipt["result"]["contract_pass"] for receipt in atlas_receipts
    )
    baseline_contract_rate = baseline_contract_passes / len(pairs) * 100
    atlas_contract_rate = atlas_contract_passes / len(pairs) * 100
    contract_rate_delta = atlas_contract_rate - baseline_contract_rate
    contract_rate_relative = (
        contract_rate_delta / baseline_contract_rate * 100
        if baseline_contract_rate > 0
        else None
    )
    return {
        "campaign_id": next(iter(campaign_ids)),
        "classification": "MEASURED",
        "costs": {
            "build_time_ms": build_time_ms,
            "build_tokens": build_tokens,
            "refresh_time_ms": refresh_time_ms,
            "refresh_tokens": refresh_tokens,
        },
        "formulae": {
            "amortized_refresh_per_task": "total_refresh_cost / complete_pairs",
            "break_even_tasks": "ceil(build_cost / median_net_saving_after_refresh), only when net saving > 0",
            "contract_pass_delta_points": "contract_pass_with_atlas_percent - contract_pass_baseline_percent",
            "contract_pass_rate": "contract_pass_receipts / complete_pairs * 100",
            "contract_pass_relative_improvement_percent": "contract_pass_delta_points / contract_pass_baseline_percent * 100, null when baseline is 0",
            "median_net_saving_after_refresh": "median_paired_saving - amortized_refresh_per_task",
            "paired_saving": "baseline_cost - atlas_use_cost",
            "paired_saving_percent": "(baseline_cost - atlas_use_cost) / baseline_cost * 100",
            "reference_integrity_mean_per_task": "condition_total / complete_pairs",
            "reference_integrity_paired_delta_mean": "paired_delta_total / complete_pairs",
            "reference_integrity_paired_delta_total": "sum(atlas_use_result_metric - baseline_result_metric)",
            "reference_integrity_total": "sum(result_metric across receipts in one condition)",
        },
        "pairs": {
            "complete": len(pairs),
            "contract_pass_baseline": baseline_contract_passes,
            "contract_pass_with_atlas": atlas_contract_passes,
            "exact_token_pairs": len(exact_token_pairs),
        },
        "quality": {
            "contract_pass_baseline_percent": round_percent(
                baseline_contract_rate
            ),
            "contract_pass_delta_points": round_percent(contract_rate_delta),
            "contract_pass_relative_improvement_percent": (
                round_percent(contract_rate_relative)
                if contract_rate_relative is not None
                else None
            ),
            "contract_pass_with_atlas_percent": round_percent(
                atlas_contract_rate
            ),
        },
        "reference_integrity": reference_integrity_summary(
            baseline_receipts,
            atlas_receipts,
        ),
        "schema_version": SCHEMA_VERSION,
        "structure": {
            "baseline": score_items(baseline_receipts),
            "with_atlas": score_items(atlas_receipts),
        },
        "time": {
            "amortized_refresh_per_task_ms": clean_number(
                amortized_refresh_time_ms
            ),
            "break_even_tasks": time_break_even,
            "median_paired_saving_ms": median_time_saving,
            "median_paired_saving_percent": round_percent(
                float(median(time_percentages) or 0)
            ),
            "median_net_saving_after_refresh_ms": clean_number(
                median_net_time_saving
            ),
            "range_paired_saving_ms": [
                min(time_savings_ms),
                max(time_savings_ms),
            ],
            "sample_n": len(time_savings_ms),
        },
        "tokens": {
            "amortized_refresh_per_task": (
                clean_number(amortized_refresh_tokens)
                if amortized_refresh_tokens is not None
                else None
            ),
            "break_even_tasks": token_break_even,
            "median_paired_saving": median_token_saving,
            "median_paired_saving_percent": (
                round_percent(float(median(token_percentages)))
                if token_percentages
                else None
            ),
            "median_net_saving_after_refresh": (
                clean_number(median_net_token_saving)
                if median_net_token_saving is not None
                else None
            ),
            "range_paired_saving": (
                [min(token_savings), max(token_savings)] if token_savings else None
            ),
            "sample_n": len(token_savings),
        },
    }


def load_receipts(directory: Path) -> list[dict[str, Any]]:
    if directory.is_symlink() or not directory.is_dir():
        raise BenchmarkError("receipt input must be a regular directory")
    files = sorted(directory.glob("*.json"))
    if len(files) > MAX_RECEIPTS:
        raise BenchmarkError(f"receipt count exceeds {MAX_RECEIPTS}")
    receipts: list[dict[str, Any]] = []
    seen_runs: set[str] = set()
    for path in files:
        receipt = validate_receipt(read_json_file(path), source_name=path.name)
        if receipt["run_id"] in seen_runs:
            raise BenchmarkError(f"duplicate run_id: {receipt['run_id']}")
        seen_runs.add(receipt["run_id"])
        receipts.append(receipt)
    return receipts


def check_expected(output: str, expected_path: Path | None) -> None:
    if expected_path is None:
        sys.stdout.write(output)
        return
    expected = expected_path.read_text(encoding="utf-8")
    if expected != output:
        raise BenchmarkError(
            f"derived output differs from {expected_path}; regenerate intentionally"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and reproduce Project Atlas effectiveness calculations."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    model = subparsers.add_parser("model", help="Calculate labelled assumption scenarios.")
    model.add_argument("--input", required=True, type=Path)
    model.add_argument("--check", type=Path)

    receipt = subparsers.add_parser(
        "validate-receipt", help="Validate one privacy-safe measured receipt."
    )
    receipt.add_argument("--receipt", required=True, type=Path)

    derive = subparsers.add_parser(
        "derive", help="Derive paired measured metrics from a receipt directory."
    )
    derive.add_argument("--input-dir", required=True, type=Path)
    derive.add_argument("--check", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "model":
            output = canonical_json(modelled_summary(read_json_file(args.input)))
            check_expected(output, args.check)
            return 0
        if args.command == "validate-receipt":
            validate_receipt(read_json_file(args.receipt), source_name=args.receipt.name)
            print("OK")
            return 0
        if args.command == "derive":
            output = canonical_json(measured_summary(load_receipts(args.input_dir)))
            check_expected(output, args.check)
            return 0
    except (BenchmarkError, OSError) as exc:
        print(f"benchmark error: {exc}", file=sys.stderr)
        return 2
    parser.error("unsupported command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
