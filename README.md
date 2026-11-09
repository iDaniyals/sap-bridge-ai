# SAP Bridge AI

SAP Bridge AI is a portfolio prototype for assessing legacy Oracle ERP supplier data before an eventual SAP S/4HANA migration. It profiles CSV data, applies transparent, configurable quality checks, and produces review-ready CSV reports.

> This application does not connect to live Oracle or SAP systems. It is not SAP-certified, production-ready, or a source of official SAP field requirements. The included rules are deliberately illustrative.

## Problem statement

ERP migrations depend on trustworthy source data. Teams need a quick way to expose incomplete fields, duplicated records, invalid codes, and other quality risks before mapping and migration work begins. SAP Bridge AI provides an auditable first-pass assessment without changing source records.

## Current capabilities

- Load a bundled synthetic CSV or a user-uploaded UTF-8/UTF-8-BOM CSV through one strict ingestion path.
- Preserve all ingested source values as text, including leading zeroes in supplier and tax identifiers.
- Reject malformed CSV quoting, duplicate/empty headers, header-only files, and rows with too many or too few fields.
- Profile row and column counts, semantic missing values, exact duplicate rows, and repeated supplier IDs.
- Treat nulls, empty strings, and whitespace-only strings as missing.
- Validate supplier data from JSON configuration in `config/validation_rules.json`.
- Flag required-value gaps, unsupported country/payment codes, repeated IDs, and potential duplicate business records.
- Show `PASS` or `REVIEW` status with readable row-level issue messages.
- Preserve every source column in validation reports, including source columns named `STATUS`, `VALIDATION_ISSUES`, or `ISSUES`.
- Download a complete validation report or a review-only exception report.

## Profiling versus validation

Profiling measures the shape and quality signals in a dataset: for example, how many values are missing or how many full rows are exact duplicates. Validation applies configured business checks and assigns each record a review status.

An **exact duplicate row** matches another row in every source column and is counted by the profiler after the first occurrence. A **potential duplicate business record** is a validation finding: it compares the configured business fields (such as supplier name, country, terms, tax ID, and email) while deliberately excluding `SUPPLIER_ID`. Records with missing comparison values are excluded from this duplicate rule so that unrelated incomplete records are not falsely grouped together.

Supplier IDs use one internal comparison policy in both profiling and validation: leading/trailing whitespace is removed and values are compared case-insensitively. Original source values are never normalized in the displayed or downloaded data, so source case, spacing, and leading zeroes remain intact. Empty and whitespace-only IDs are missing values, not duplicates.

Generated validation columns normally use `STATUS` and `VALIDATION_ISSUES`. If either name already exists in the source, SAP Bridge AI preserves it and writes the generated field using a non-conflicting `SAP_BRIDGE_`-prefixed name instead.

## CSV ingestion and malformed files

CSV data must be UTF-8 encoded; UTF-8 BOM files are supported. The application checks every parsed row against the header width before creating a dataframe. Files with malformed quoting, duplicate headers, empty headers, no records, or rows with too many or too few fields are rejected with an actionable message. This prevents source values from being silently shifted into a dataframe index or dropped from downloaded reports.

## Technology stack

- Python 3.12 or 3.13
- Pandas
- Streamlit
- Plotly
- Pydantic
- Pytest

## Project structure

```text
sap-bridge-ai/
├── app/
│   ├── __init__.py
│   ├── data_profiler.py
│   ├── validator.py
│   └── dashboard.py
├── config/
│   └── validation_rules.json
├── data/sample/
│   └── oracle_suppliers.csv
├── tests/
│   ├── __init__.py
│   └── test_profiler.py
├── .gitignore
├── requirements.txt
└── README.md
```

## Installation on Windows

Open PowerShell in this project folder and create an isolated environment:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If Python 3.12 is not installed, replace `py -3.12` with your available Python 3.12 or 3.13 command.

## Run the application

```powershell
python -m streamlit run app/dashboard.py
```

The dashboard opens in a browser and begins with the bundled synthetic dataset. Uploaded CSV files are only parsed as tabular data; they are never executed.

## Run automated tests

```powershell
python -m pytest -v -p no:cacheprovider
```

`-p no:cacheprovider` is useful in restricted environments where an inherited `.pytest_cache` directory is not writable. It does not change application behavior.

## Sample data

`data/sample/oracle_suppliers.csv` contains entirely synthetic supplier data. It intentionally includes a potential duplicate business record, missing tax IDs, a missing email, an unsupported payment term, and incomplete similar records that require human assessment. It contains no real personal or business data.

## Current limitations

- CSV is the only current source format.
- Validation rules are examples, not authorized SAP business configuration.
- Findings are review signals and do not transform, delete, merge, or load source records.
- There is no live Oracle, SAP, API, identity, or database integration.
- Processing is held in the active Streamlit browser session. This avoids globally sharing uploaded data, but large uploads still consume that session's memory.

## Planned future capabilities

1. AI-assisted source-to-target field mapping.
2. Mapping confidence scores and human approval.
3. Configurable data transformations.
4. Versioned mapping rules.
5. Migration impact analysis.
6. Source-to-target reconciliation.
7. Record-level audit history.
8. Optional database persistence.
9. API development.
10. Docker-based deployment.
11. SAP integration where supported by a real SAP environment and authorized interfaces.
