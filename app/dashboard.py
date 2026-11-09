"""Streamlit dashboard for the SAP Bridge AI data-quality prototype."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

from app.data_profiler import load_csv_bytes, load_csv_dataset, profile_dataframe
from app.validator import get_validation_column_names, validate_suppliers


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DATASET = PROJECT_ROOT / "data" / "sample" / "oracle_suppliers.csv"
SOURCE_PREVIEW_ROWS = 100
VALIDATION_DISPLAY_ROWS = 500
SESSION_RESULT_KEY = "sap_bridge_processed_dataset"


def _load_uploaded_csv(uploaded_file: Any) -> pd.DataFrame:
    """Load an uploaded CSV through the shared strict ingestion implementation."""
    return load_csv_bytes(uploaded_file.getvalue(), source_name="Uploaded CSV")


def _dataset_key(source: str, uploaded_bytes: bytes | None = None) -> str:
    """Create a session-local identity that changes when the selected data changes."""
    if source == "sample":
        sample_stat = SAMPLE_DATASET.stat()
        return f"sample:{sample_stat.st_mtime_ns}:{sample_stat.st_size}"
    if uploaded_bytes is None:
        raise ValueError("Uploaded CSV content is required.")
    return f"upload:{hashlib.sha256(uploaded_bytes).hexdigest()}"


def _process_dataset(dataset_key: str, uploaded_bytes: bytes | None = None) -> dict[str, Any]:
    """Process a dataset once per Streamlit session and source-content hash.

    Session state is deliberately used instead of a global Streamlit cache so
    one user's uploaded source data is never shared with another session.
    """
    cached_result = st.session_state.get(SESSION_RESULT_KEY)
    if cached_result and cached_result["dataset_key"] == dataset_key:
        return cached_result

    dataframe = (
        load_csv_dataset(SAMPLE_DATASET)
        if uploaded_bytes is None
        else load_csv_bytes(uploaded_bytes, source_name="Uploaded CSV")
    )
    validation_report = validate_suppliers(dataframe)
    issues_column, status_column = get_validation_column_names(validation_report)
    processed_result = {
        "dataset_key": dataset_key,
        "dataframe": dataframe,
        "profile": profile_dataframe(dataframe),
        "validation_report": validation_report,
        "issues_column": issues_column,
        "status_column": status_column,
        "report_csv": None,
        "exceptions_csv": None,
    }
    st.session_state[SESSION_RESULT_KEY] = processed_result
    return processed_result


def _render_profile(dataframe: pd.DataFrame, profile: dict[str, Any]) -> None:
    """Render source preview and quality-profile components."""
    st.header("Source Data Preview")
    source_metrics = st.columns(2)
    source_metrics[0].metric("Total records", profile["total_rows"])
    source_metrics[1].metric("Total columns", profile["total_columns"])
    st.caption(f"Showing the first {min(len(dataframe), SOURCE_PREVIEW_ROWS):,} records.")
    st.dataframe(dataframe.head(SOURCE_PREVIEW_ROWS), use_container_width=True, hide_index=True)

    st.header("Data Quality Profile")
    quality_metrics = st.columns(4)
    quality_metrics[0].metric("Total records", profile["total_rows"])
    quality_metrics[1].metric("Total columns", profile["total_columns"])
    quality_metrics[2].metric("Exact duplicate rows", profile["exact_duplicate_rows"])
    repeated_ids = profile["repeated_supplier_ids"]
    quality_metrics[3].metric("Repeated supplier-ID records", repeated_ids["count"])

    missing_table = pd.DataFrame(
        {
            "Column": list(profile["missing_values"].keys()),
            "Missing values": list(profile["missing_values"].values()),
            "Missing percentage": [
                round(value, 2) for value in profile["missing_value_percentages"].values()
            ],
        }
    )
    st.subheader("Missing values by column")
    st.dataframe(missing_table, use_container_width=True, hide_index=True)
    figure = px.bar(
        missing_table,
        x="Column",
        y="Missing values",
        text="Missing values",
        title="Missing values by column",
    )
    figure.update_layout(showlegend=False, xaxis_title=None)
    st.plotly_chart(figure, use_container_width=True)


def _report_csvs(processed_result: dict[str, Any]) -> tuple[bytes, bytes]:
    """Generate each download once per session dataset rather than per rerun."""
    report = processed_result["validation_report"]
    status_column = processed_result["status_column"]
    if processed_result["report_csv"] is None:
        processed_result["report_csv"] = report.to_csv(index=False).encode("utf-8")
    if processed_result["exceptions_csv"] is None:
        exceptions = report[report[status_column] == "REVIEW"]
        processed_result["exceptions_csv"] = exceptions.to_csv(index=False).encode("utf-8")
    return processed_result["report_csv"], processed_result["exceptions_csv"]


def _render_validation(processed_result: dict[str, Any]) -> None:
    """Render validation metrics, search, filters, and report downloads."""
    validation_report = processed_result["validation_report"]
    issues_column = processed_result["issues_column"]
    status_column = processed_result["status_column"]
    st.header("Supplier Validation")
    total_records = len(validation_report)
    passing_records = int((validation_report[status_column] == "PASS").sum())
    review_records = total_records - passing_records
    pass_percentage = (passing_records / total_records * 100) if total_records else 0.0
    validation_metrics = st.columns(4)
    validation_metrics[0].metric("Total records", total_records)
    validation_metrics[1].metric("Passing", passing_records)
    validation_metrics[2].metric("Require review", review_records)
    validation_metrics[3].metric("Validation pass rate", f"{pass_percentage:.1f}%")

    filter_label = st.radio(
        "Show records",
        options=["All records", "Passing records", "Records requiring review"],
        horizontal=True,
    )
    search_query = st.text_input("Search validation results", placeholder="Search any source value or issue")
    if filter_label == "Passing records":
        displayed_report = validation_report[validation_report[status_column] == "PASS"]
    elif filter_label == "Records requiring review":
        displayed_report = validation_report[validation_report[status_column] == "REVIEW"]
    else:
        displayed_report = validation_report

    if search_query.strip():
        contains_query = displayed_report.astype("string").apply(
            lambda column: column.str.contains(search_query.strip(), case=False, regex=False, na=False)
        )
        displayed_report = displayed_report[contains_query.any(axis=1)]

    st.caption(
        f"Showing {min(len(displayed_report), VALIDATION_DISPLAY_ROWS):,} of "
        f"{len(displayed_report):,} matching records. Validation issues are in "
        f"`{issues_column}`."
    )
    st.dataframe(
        displayed_report.head(VALIDATION_DISPLAY_ROWS), use_container_width=True, hide_index=True
    )

    st.header("Reports")
    complete_csv, exceptions_csv = _report_csvs(processed_result)
    download_columns = st.columns(2)
    download_columns[0].download_button(
        "Download complete validation report",
        data=complete_csv,
        file_name="sap_bridge_ai_validation_report.csv",
        mime="text/csv",
    )
    download_columns[1].download_button(
        "Download exception report",
        data=exceptions_csv,
        file_name="sap_bridge_ai_exceptions.csv",
        mime="text/csv",
    )


def main() -> None:
    """Run the SAP Bridge AI dashboard."""
    st.set_page_config(page_title="SAP Bridge AI", page_icon="🔗", layout="wide")
    st.title("SAP Bridge AI")
    st.caption("AI-Assisted ERP Data Migration Intelligence Platform")
    st.info(
        "This prototype profiles and validates CSV data only. Its rules are illustrative "
        "and are not official SAP configuration; it has no live Oracle or SAP connectivity."
    )

    st.header("Upload")
    source = st.radio(
        "Choose a dataset", ["Use bundled synthetic sample", "Upload a CSV file"], horizontal=True
    )
    uploaded_bytes: bytes | None = None
    try:
        if source == "Upload a CSV file":
            uploaded_file = st.file_uploader("CSV file", type=["csv"])
            if uploaded_file is None:
                st.info("Choose a CSV file to begin profiling and validation.")
                return
            uploaded_bytes = uploaded_file.getvalue()
            processed_result = _process_dataset(_dataset_key("upload", uploaded_bytes), uploaded_bytes)
        else:
            processed_result = _process_dataset(_dataset_key("sample"))
    except (FileNotFoundError, OSError, ValueError) as error:
        st.error(str(error))
        return
    except Exception:
        st.error("The dataset could not be processed. Confirm that it is a readable UTF-8 CSV file.")
        return

    _render_profile(processed_result["dataframe"], processed_result["profile"])
    _render_validation(processed_result)


if __name__ == "__main__":
    main()
