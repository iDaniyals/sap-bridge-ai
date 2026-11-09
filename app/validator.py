"""Configurable supplier validation for SAP Bridge AI."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pandas as pd

from app.data_profiler import missing_value_mask, normalise_supplier_id, validate_column_names


REQUIRED_RULE_KEYS = (
    "required_fields",
    "allowed_country_codes",
    "allowed_payment_terms",
    "fields_requiring_non_empty_values",
    "exact_duplicate_fields",
)
CORE_VALIDATION_FIELDS = ("SUPPLIER_ID", "COUNTRY_CD", "PAY_TERM")
ISSUES_COLUMN = "VALIDATION_ISSUES"
STATUS_COLUMN = "STATUS"
VALIDATION_COLUMNS_ATTRIBUTE = "sap_bridge_validation_columns"


def default_rules_path() -> Path:
    """Return the rules file bundled with this project."""
    return Path(__file__).resolve().parents[1] / "config" / "validation_rules.json"


def _validate_string_list(value: Any, key: str) -> list[str]:
    """Validate a required non-empty JSON list containing unique strings."""
    if not isinstance(value, list) or not value:
        raise ValueError(f"Validation rule '{key}' must be a non-empty list of strings.")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"Validation rule '{key}' must contain only non-empty strings.")
    duplicates = sorted({item for item in value if value.count(item) > 1})
    if duplicates:
        raise ValueError(
            f"Validation rule '{key}' contains duplicate value(s): {', '.join(duplicates)}."
        )
    return list(value)


def validate_rules_configuration(rules: Any) -> dict[str, list[str]]:
    """Validate parsed rules and return a safe, typed copy.

    The configuration intentionally remains editable JSON, but it must declare
    every required rule as a non-empty, unique string list before validation can
    run. Core fields used by validation logic must be required source fields.
    """
    if not isinstance(rules, Mapping):
        raise ValueError("Validation rules must be a JSON object.")

    missing_keys = [key for key in REQUIRED_RULE_KEYS if key not in rules]
    if missing_keys:
        names = ", ".join(missing_keys)
        raise ValueError(f"Validation rules are missing required keys: {names}.")

    validated = {key: _validate_string_list(rules[key], key) for key in REQUIRED_RULE_KEYS}
    missing_core_fields = [
        field for field in CORE_VALIDATION_FIELDS if field not in validated["required_fields"]
    ]
    if missing_core_fields:
        names = ", ".join(missing_core_fields)
        raise ValueError(
            "Validation rule 'required_fields' must include core field(s): "
            f"{names}."
        )
    return validated


def load_validation_rules(rules_path: str | Path | None = None) -> dict[str, list[str]]:
    """Load and validate the JSON rule configuration used by the validator."""
    path = Path(rules_path) if rules_path is not None else default_rules_path()
    if not path.is_file():
        raise FileNotFoundError(f"Validation rules file not found: {path}")

    try:
        with path.open("r", encoding="utf-8") as rules_file:
            rules = json.load(rules_file)
    except json.JSONDecodeError as error:
        raise ValueError(f"Validation rules file is not valid JSON: {path}") from error
    except UnicodeDecodeError as error:
        raise ValueError(f"Validation rules file must be UTF-8 encoded: {path}") from error

    return validate_rules_configuration(rules)


def _normalise_code(value: object) -> str:
    """Normalise a code for comparison while preserving source values in output."""
    return str(value).strip().upper()


def _add_issue_for_mask(
    issues: list[list[str]], mask: pd.Series, message_factory: Callable[[int], str]
) -> None:
    """Append an issue to positional rows selected by a boolean series."""
    for position, matches in enumerate(mask.to_numpy()):
        if matches:
            issues[position].append(message_factory(position))


def _output_column_name(preferred_name: str, source_columns: pd.Index) -> str:
    """Create a deterministic generated-column name that cannot overwrite input."""
    used_names = {str(column) for column in source_columns}
    if preferred_name not in used_names:
        return preferred_name

    candidate = f"SAP_BRIDGE_{preferred_name}"
    suffix = 2
    while candidate in used_names:
        candidate = f"SAP_BRIDGE_{preferred_name}_{suffix}"
        suffix += 1
    return candidate


def get_validation_column_names(dataframe: pd.DataFrame) -> tuple[str, str]:
    """Return the generated ``(issues_column, status_column)`` names for a report."""
    metadata = dataframe.attrs.get(VALIDATION_COLUMNS_ATTRIBUTE, {})
    issues_column = metadata.get("issues") if isinstance(metadata, Mapping) else None
    status_column = metadata.get("status") if isinstance(metadata, Mapping) else None
    if isinstance(issues_column, str) and isinstance(status_column, str):
        return issues_column, status_column
    return ISSUES_COLUMN, STATUS_COLUMN


def _configured_source_fields(rules: Mapping[str, list[str]]) -> list[str]:
    """Return every source field referenced by the active validation rules."""
    fields: list[str] = []
    for rule_key in (
        "required_fields",
        "fields_requiring_non_empty_values",
        "exact_duplicate_fields",
    ):
        for field in rules[rule_key]:
            if field not in fields:
                fields.append(field)
    return fields


def validate_suppliers(
    dataframe: pd.DataFrame, rules_path: str | Path | None = None
) -> pd.DataFrame:
    """Return a new dataframe with supplier validation issues and status.

    Every input row is retained. Source columns named ``STATUS``,
    ``VALIDATION_ISSUES``, or ``ISSUES`` are preserved: generated columns use
    their normal names when available and an ``SAP_BRIDGE_`` prefix on conflict.
    The generated names are stored in dataframe attributes and can be obtained
    with :func:`get_validation_column_names`.
    """
    if dataframe.empty:
        raise ValueError("Dataset is empty; at least one data row is required for validation.")
    validate_column_names(dataframe)
    rules = load_validation_rules(rules_path)

    missing_source_fields = [
        field for field in _configured_source_fields(rules) if field not in dataframe.columns
    ]
    if missing_source_fields:
        names = ", ".join(missing_source_fields)
        raise ValueError(f"Dataset is missing configured validation columns: {names}.")

    result = dataframe.copy(deep=True)
    issues: list[list[str]] = [[] for _ in range(len(result))]

    for field in rules["required_fields"]:
        missing_mask = missing_value_mask(result[field])
        _add_issue_for_mask(issues, missing_mask, lambda _: f"Missing required value: {field}")

    for field in rules["fields_requiring_non_empty_values"]:
        missing_mask = missing_value_mask(result[field])
        _add_issue_for_mask(issues, missing_mask, lambda _: f"Missing required value: {field}")

    country_values = result["COUNTRY_CD"]
    country_missing = missing_value_mask(country_values)
    allowed_countries = {_normalise_code(code) for code in rules["allowed_country_codes"]}
    invalid_country_mask = (~country_missing) & ~country_values.map(_normalise_code).isin(
        allowed_countries
    )
    _add_issue_for_mask(
        issues,
        invalid_country_mask,
        lambda position: f"Unsupported country code: {country_values.iloc[position]}",
    )

    payment_values = result["PAY_TERM"]
    payment_missing = missing_value_mask(payment_values)
    allowed_terms = {_normalise_code(term) for term in rules["allowed_payment_terms"]}
    invalid_payment_mask = (~payment_missing) & ~payment_values.map(_normalise_code).isin(
        allowed_terms
    )
    _add_issue_for_mask(
        issues,
        invalid_payment_mask,
        lambda position: f"Unsupported payment term: {payment_values.iloc[position]}",
    )

    supplier_ids = result["SUPPLIER_ID"]
    normalised_ids = supplier_ids.map(normalise_supplier_id)
    repeated_supplier_mask = normalised_ids.notna() & normalised_ids.duplicated(keep=False)
    _add_issue_for_mask(
        issues,
        repeated_supplier_mask,
        lambda position: f"Repeated SUPPLIER_ID: {supplier_ids.iloc[position]}",
    )

    duplicate_fields = rules["exact_duplicate_fields"]
    complete_business_record = ~pd.concat(
        [missing_value_mask(result[field]) for field in duplicate_fields], axis=1
    ).any(axis=1)
    business_keys = result.loc[:, duplicate_fields].copy()
    for field in duplicate_fields:
        business_keys[field] = business_keys[field].map(_normalise_code)
    potential_duplicate_mask = complete_business_record & business_keys.duplicated(keep=False)
    matching_fields = ", ".join(duplicate_fields)
    _add_issue_for_mask(
        issues,
        potential_duplicate_mask,
        lambda _: f"Potential duplicate business record (matching: {matching_fields})",
    )

    issues_column = _output_column_name(ISSUES_COLUMN, result.columns)
    status_column = _output_column_name(STATUS_COLUMN, result.columns)
    if status_column == issues_column:
        status_column = _output_column_name(f"SAP_BRIDGE_{STATUS_COLUMN}", result.columns)
    result[issues_column] = ["; ".join(row_issues) for row_issues in issues]
    result[status_column] = ["REVIEW" if row_issues else "PASS" for row_issues in issues]
    result.attrs[VALIDATION_COLUMNS_ATTRIBUTE] = {
        "issues": issues_column,
        "status": status_column,
    }
    return result
