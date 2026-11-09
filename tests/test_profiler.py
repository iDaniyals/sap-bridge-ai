"""Regression tests for SAP Bridge AI Phase 1 ingestion and validation."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from app.data_profiler import load_csv_bytes, load_csv_dataset, profile_dataframe
from app.validator import (
    STATUS_COLUMN,
    get_validation_column_names,
    load_validation_rules,
    validate_rules_configuration,
    validate_suppliers,
)


CSV_HEADER = "SUPPLIER_ID,SUPPLIER_NAME,COUNTRY_CD,PAY_TERM,TAX_ID,EMAIL"


def valid_supplier(**overrides: object) -> dict[str, object]:
    """Build a valid synthetic supplier record with optional overrides."""
    supplier = {
        "SUPPLIER_ID": "1001",
        "SUPPLIER_NAME": "Example Supplies",
        "COUNTRY_CD": "IN",
        "PAY_TERM": "N030",
        "TAX_ID": "27EXAMPLE1234F1Z5",
        "EMAIL": "accounts@example.test",
    }
    supplier.update(overrides)
    return supplier


def test_csv_ingestion_preserves_leading_zeroes_in_identifiers() -> None:
    csv_bytes = (
        f"{CSV_HEADER}\n001001,Example,IN,N030,000123,accounts@example.test\n".encode("utf-8")
    )

    dataframe = load_csv_bytes(csv_bytes)

    assert dataframe.loc[0, "SUPPLIER_ID"] == "001001"
    assert dataframe.loc[0, "TAX_ID"] == "000123"
    assert isinstance(dataframe.loc[0, "SUPPLIER_ID"], str)


def test_csv_ingestion_rejects_extra_field_rows() -> None:
    csv_bytes = f"{CSV_HEADER}\n1001,Example,IN,N030,TAX,email@example.test,EXTRA\n".encode()

    with pytest.raises(ValueError, match="row 2 has 7 fields; the header has 6"):
        load_csv_bytes(csv_bytes)


def test_csv_ingestion_rejects_short_rows() -> None:
    csv_bytes = f"{CSV_HEADER}\n1001,Example,IN,N030,TAX\n".encode()

    with pytest.raises(ValueError, match="row 2 has 5 fields; the header has 6"):
        load_csv_bytes(csv_bytes)


def test_csv_ingestion_rejects_duplicate_header_names() -> None:
    with pytest.raises(ValueError, match="duplicate column names: SUPPLIER_ID"):
        load_csv_bytes(b"SUPPLIER_ID,SUPPLIER_ID\n1001,1002\n")


def test_csv_ingestion_handles_utf8_bom() -> None:
    dataframe = load_csv_bytes(b"\xef\xbb\xbf" + f"{CSV_HEADER}\n1001,Example,IN,N030,TAX,email@example.test\n".encode())

    assert dataframe.columns.tolist()[0] == "SUPPLIER_ID"
    assert len(dataframe) == 1


def test_path_loader_reports_invalid_encoding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "is_file", lambda _: True)
    monkeypatch.setattr(Path, "read_bytes", lambda _: b"\xff")

    with pytest.raises(ValueError, match="must be UTF-8 encoded"):
        load_csv_dataset("synthetic-invalid-encoding.csv")


def test_empty_csv_is_rejected() -> None:
    with pytest.raises(ValueError, match="empty and has no header row"):
        load_csv_bytes(b"")


def test_profile_returns_correct_counts_and_semantic_missing_values() -> None:
    dataframe = pd.DataFrame(
        [valid_supplier(TAX_ID=None), valid_supplier(SUPPLIER_ID="1002", EMAIL="   ")]
    )

    profile = profile_dataframe(dataframe)

    assert profile["total_rows"] == 2
    assert profile["total_columns"] == 6
    assert profile["missing_values"]["TAX_ID"] == 1
    assert profile["missing_values"]["EMAIL"] == 1
    assert profile["missing_value_percentages"]["TAX_ID"] == 50.0


def test_supplier_id_normalization_is_consistent_in_profile_and_validation() -> None:
    dataframe = pd.DataFrame(
        [
            valid_supplier(SUPPLIER_ID="1001"),
            valid_supplier(SUPPLIER_ID=" 1001 ", SUPPLIER_NAME="Other", TAX_ID="T2"),
            valid_supplier(SUPPLIER_ID="001001", SUPPLIER_NAME="Third", TAX_ID="T3"),
            valid_supplier(SUPPLIER_ID="", SUPPLIER_NAME="Fourth", TAX_ID="T4"),
            valid_supplier(SUPPLIER_ID="   ", SUPPLIER_NAME="Fifth", TAX_ID="T5"),
        ]
    )

    profile = profile_dataframe(dataframe)
    report = validate_suppliers(dataframe)
    issues_column, _ = get_validation_column_names(report)

    assert profile["repeated_supplier_ids"]["count"] == 2
    assert report.loc[[0, 1], issues_column].str.contains("Repeated SUPPLIER_ID").all()
    assert not report.loc[2, issues_column].__contains__("Repeated SUPPLIER_ID")
    assert report.loc[[3, 4], issues_column].str.contains("Repeated SUPPLIER_ID").sum() == 0


def test_valid_supplier_passes_validation() -> None:
    report = validate_suppliers(pd.DataFrame([valid_supplier()]))
    issues_column, status_column = get_validation_column_names(report)

    assert report.loc[0, status_column] == "PASS"
    assert report.loc[0, issues_column] == ""


def test_validation_flags_missing_values_invalid_terms_and_repeated_ids() -> None:
    dataframe = pd.DataFrame(
        [
            valid_supplier(SUPPLIER_ID="1001", TAX_ID=""),
            valid_supplier(SUPPLIER_ID="1001", SUPPLIER_NAME="Other", TAX_ID="T2", EMAIL="  "),
            valid_supplier(SUPPLIER_ID="1003", SUPPLIER_NAME="Third", TAX_ID="T3", PAY_TERM="N090"),
        ]
    )

    report = validate_suppliers(dataframe)
    issues_column, status_column = get_validation_column_names(report)

    assert report[status_column].tolist() == ["REVIEW", "REVIEW", "REVIEW"]
    assert "Missing required value: TAX_ID" in report.loc[0, issues_column]
    assert "Missing required value: EMAIL" in report.loc[1, issues_column]
    assert "Unsupported payment term: N090" in report.loc[2, issues_column]
    assert report.loc[[0, 1], issues_column].str.contains("Repeated SUPPLIER_ID").all()


def test_complete_business_duplicates_are_flagged_but_incomplete_records_are_not() -> None:
    dataframe = pd.DataFrame(
        [
            valid_supplier(SUPPLIER_ID="1001"),
            valid_supplier(SUPPLIER_ID="1002"),
            valid_supplier(SUPPLIER_ID="1003", SUPPLIER_NAME="Incomplete", TAX_ID="", EMAIL=""),
            valid_supplier(SUPPLIER_ID="1004", SUPPLIER_NAME="Incomplete", TAX_ID=" ", EMAIL=" "),
        ]
    )

    report = validate_suppliers(dataframe)
    issues_column, _ = get_validation_column_names(report)

    assert report.loc[[0, 1], issues_column].str.contains(
        "Potential duplicate business record"
    ).all()
    assert not report.loc[[2, 3], issues_column].str.contains(
        "Potential duplicate business record"
    ).any()


def test_validation_rejects_missing_configured_source_columns() -> None:
    dataframe = pd.DataFrame([{"SUPPLIER_ID": "1001", "SUPPLIER_NAME": "Example"}])

    with pytest.raises(ValueError, match="missing configured validation columns"):
        validate_suppliers(dataframe)


def test_validation_column_collisions_preserve_all_source_values() -> None:
    dataframe = pd.DataFrame(
        [
            valid_supplier(
                STATUS="SOURCE_STATUS",
                VALIDATION_ISSUES="SOURCE_VALIDATION_ISSUES",
                ISSUES="SOURCE_ISSUES",
            )
        ]
    )
    original = dataframe.copy(deep=True)

    report = validate_suppliers(dataframe)
    issues_column, status_column = get_validation_column_names(report)

    pd.testing.assert_frame_equal(dataframe, original)
    assert issues_column != "VALIDATION_ISSUES"
    assert status_column != STATUS_COLUMN
    assert report.loc[0, "STATUS"] == "SOURCE_STATUS"
    assert report.loc[0, "VALIDATION_ISSUES"] == "SOURCE_VALIDATION_ISSUES"
    assert report.loc[0, "ISSUES"] == "SOURCE_ISSUES"
    assert report.loc[0, status_column] == "PASS"


def test_validation_preserves_original_dataframe_columns_index_and_cardinality() -> None:
    dataframe = pd.DataFrame(
        [valid_supplier(), valid_supplier(SUPPLIER_ID="1002", SUPPLIER_NAME="Second")],
        index=[42, 99],
    )
    dataframe["LEGACY_NOTE"] = ["retain", "retain"]
    original = dataframe.copy(deep=True)

    report = validate_suppliers(dataframe)
    _, status_column = get_validation_column_names(report)

    pd.testing.assert_frame_equal(dataframe, original)
    assert report.index.tolist() == [42, 99]
    assert len(report) == len(dataframe)
    assert report["LEGACY_NOTE"].tolist() == ["retain", "retain"]
    assert report[status_column].isin(["PASS", "REVIEW"]).all()


def test_validation_configuration_rejects_empty_and_invalid_rule_lists() -> None:
    rules = load_validation_rules()
    rules["allowed_payment_terms"] = []
    with pytest.raises(ValueError, match="allowed_payment_terms.*non-empty"):
        validate_rules_configuration(rules)

    rules = load_validation_rules()
    rules["required_fields"] = "SUPPLIER_ID"
    with pytest.raises(ValueError, match="required_fields.*non-empty list"):
        validate_rules_configuration(rules)


def test_validation_configuration_rejects_duplicate_field_names() -> None:
    rules = load_validation_rules()
    rules["exact_duplicate_fields"].append("EMAIL")

    with pytest.raises(ValueError, match="exact_duplicate_fields.*duplicate"):
        validate_rules_configuration(rules)


def test_empty_dataset_is_rejected_for_profiling_and_validation() -> None:
    dataframe = pd.DataFrame(columns=list(valid_supplier()))

    with pytest.raises(ValueError, match="Dataset is empty"):
        profile_dataframe(dataframe)
    with pytest.raises(ValueError, match="Dataset is empty"):
        validate_suppliers(dataframe)


def test_25000_row_profile_and_validation_preserve_data_integrity() -> None:
    row_count = 25_000
    dataframe = pd.DataFrame(
        {
            "SUPPLIER_ID": [f"{100_000 + index:06d}" for index in range(row_count)],
            "SUPPLIER_NAME": [f"Supplier {index}" for index in range(row_count)],
            "COUNTRY_CD": ["IN"] * row_count,
            "PAY_TERM": ["N030"] * row_count,
            "TAX_ID": [f"TAX{index:06d}" for index in range(row_count)],
            "EMAIL": [f"supplier{index}@example.test" for index in range(row_count)],
            "EXTRA_SOURCE_FIELD": ["retain"] * row_count,
        }
    )
    dataframe.loc[1, "SUPPLIER_ID"] = dataframe.loc[0, "SUPPLIER_ID"]
    original = dataframe.copy(deep=True)

    profile = profile_dataframe(dataframe)
    report = validate_suppliers(dataframe)
    _, status_column = get_validation_column_names(report)

    pd.testing.assert_frame_equal(dataframe, original)
    assert len(dataframe) == row_count
    assert len(report) == row_count
    assert profile["total_rows"] == row_count
    assert profile["repeated_supplier_ids"]["count"] == 2
    assert "EXTRA_SOURCE_FIELD" in report.columns
    assert report["EXTRA_SOURCE_FIELD"].eq("retain").all()
    assert report[status_column].isin(["PASS", "REVIEW"]).all()
