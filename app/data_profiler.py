"""Reusable, Streamlit-independent CSV ingestion and profiling utilities."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

import pandas as pd


def _duplicate_names(column_names: list[object]) -> list[str]:
    """Return duplicated names once each, preserving their original order."""
    seen: set[str] = set()
    duplicates: list[str] = []
    for column in column_names:
        name = str(column)
        if name in seen and name not in duplicates:
            duplicates.append(name)
        seen.add(name)
    return duplicates


def validate_column_names(dataframe: pd.DataFrame) -> None:
    """Raise a useful error when a dataframe has invalid column labels."""
    column_names = [str(column) for column in dataframe.columns]
    empty_names = [name for name in column_names if not name.strip()]
    if empty_names:
        raise ValueError("Dataset contains an empty column name.")

    duplicates = _duplicate_names(column_names)
    if duplicates:
        joined_names = ", ".join(duplicates)
        raise ValueError(f"Dataset contains duplicate column names: {joined_names}.")


def missing_value_mask(series: pd.Series) -> pd.Series:
    """Return a mask for null, empty-string, and whitespace-only values."""
    blank_string = series.map(lambda value: isinstance(value, str) and not value.strip())
    return series.isna() | blank_string


def normalise_supplier_id(value: object) -> str | None:
    """Return the internal comparison key for a supplier ID.

    IDs are compared case-insensitively after trimming leading and trailing
    whitespace. The source dataframe is never changed, so its original case,
    spacing, and leading zeroes remain available for display and export.
    """
    if value is None or pd.isna(value):
        return None
    normalised = str(value).strip().casefold()
    return normalised or None


def load_csv_bytes(csv_bytes: bytes, source_name: str = "CSV dataset") -> pd.DataFrame:
    """Load a strict UTF-8 CSV from bytes while preserving every value as text.

    The parser validates duplicate headers and enforces that every data row has
    exactly the same number of fields as the header. This prevents pandas from
    inferring an index or silently shifting values in ragged rows.
    """
    try:
        text = csv_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError(f"{source_name} must be UTF-8 encoded.") from error
    return load_csv_text(text, source_name)


def load_csv_text(csv_text: str, source_name: str = "CSV dataset") -> pd.DataFrame:
    """Parse strict CSV text into a string-typed dataframe.

    Empty files, header-only datasets, malformed quoting, duplicate headers,
    and rows with a different field count are rejected with actionable errors.
    """
    if not csv_text:
        raise ValueError(f"{source_name} is empty and has no header row.")

    reader = csv.reader(io.StringIO(csv_text, newline=""), strict=True)
    try:
        header = next(reader)
    except StopIteration as error:
        raise ValueError(f"{source_name} is empty and has no header row.") from error
    except csv.Error as error:
        raise ValueError(f"{source_name} is malformed: {error}.") from error

    if not header:
        raise ValueError(f"{source_name} has no readable column headers.")
    empty_headers = [position + 1 for position, name in enumerate(header) if not name.strip()]
    if empty_headers:
        positions = ", ".join(str(position) for position in empty_headers)
        raise ValueError(f"{source_name} contains empty header names at column(s): {positions}.")
    duplicate_headers = _duplicate_names(header)
    if duplicate_headers:
        names = ", ".join(duplicate_headers)
        raise ValueError(f"Dataset contains duplicate column names: {names}.")

    records: list[list[str]] = []
    expected_width = len(header)
    try:
        for row_number, row in enumerate(reader, start=2):
            actual_width = len(row)
            if actual_width != expected_width:
                raise ValueError(
                    f"{source_name} row {row_number} has {actual_width} fields; "
                    f"the header has {expected_width}."
                )
            records.append(row)
    except csv.Error as error:
        raise ValueError(f"{source_name} is malformed: {error}.") from error

    if not records:
        raise ValueError(f"{source_name} is empty; at least one data row is required.")

    dataframe = pd.DataFrame(records, columns=header, dtype="string")
    validate_column_names(dataframe)
    return dataframe


def load_csv_dataset(csv_path: str | Path) -> pd.DataFrame:
    """Load a strict UTF-8 CSV file from a path without inferring source types."""
    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"CSV file not found: {path}")
    try:
        csv_bytes = path.read_bytes()
    except OSError as error:
        raise OSError(f"Could not read CSV file: {path}") from error
    return load_csv_bytes(csv_bytes, source_name="CSV dataset")


def profile_dataframe(dataframe: pd.DataFrame) -> dict[str, Any]:
    """Calculate quality metrics without changing the supplied dataframe.

    Empty strings and whitespace-only strings count as missing. Exact duplicate
    rows are counted after their first occurrence. Supplier IDs are compared by
    the shared trim-and-casefold normalization policy.
    """
    if dataframe.empty:
        raise ValueError("Dataset is empty; at least one data row is required for profiling.")
    validate_column_names(dataframe)

    total_rows, total_columns = dataframe.shape
    missing_counts = {
        str(column): int(missing_value_mask(dataframe.iloc[:, position]).sum())
        for position, column in enumerate(dataframe.columns)
    }
    missing_percentages = {
        column: (count / total_rows) * 100 for column, count in missing_counts.items()
    }

    repeated_supplier_id_count = 0
    repeated_supplier_id_values: list[str] = []
    if "SUPPLIER_ID" in dataframe.columns:
        normalised_ids = dataframe["SUPPLIER_ID"].map(normalise_supplier_id)
        present_ids = normalised_ids.notna()
        repeated_mask = present_ids & normalised_ids.duplicated(keep=False)
        repeated_supplier_id_count = int(repeated_mask.sum())
        repeated_supplier_id_values = normalised_ids[repeated_mask].drop_duplicates().tolist()

    return {
        "total_rows": int(total_rows),
        "total_columns": int(total_columns),
        "missing_values": missing_counts,
        "missing_value_percentages": missing_percentages,
        "exact_duplicate_rows": int(dataframe.duplicated().sum()),
        "repeated_supplier_ids": {
            "count": repeated_supplier_id_count,
            "values": repeated_supplier_id_values,
        },
    }
