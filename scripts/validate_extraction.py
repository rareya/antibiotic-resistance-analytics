# ============================================================
# AMR KNOWLEDGE EXTRACTION VALIDATOR — V1
#
# Purpose:
#   Validate the output produced by extract_assets.py V2.1
#   before the knowledge base moves into chunking / indexing.
#
# Validates:
#   - extraction manifest
#   - asset existence
#   - SHA-256 provenance
#   - JSON readability
#   - CSV extraction structure
#   - PDF extraction structure
#   - OWL/RDF extraction structure
#   - duplicate asset IDs
#   - orphan extracted files
#   - empty/invalid extraction output
#
# Design principles:
#   - Read-only validation
#   - Never modifies raw/
#   - Never modifies extracted assets
#   - Fails clearly
#   - Produces machine-readable validation_report.json
# ============================================================

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Set


# ============================================================
# PATHS
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]

RAW_DIR = PROJECT_ROOT / "knowledge" / "raw"
EXTRACTED_DIR = PROJECT_ROOT / "knowledge" / "extracted"

MANIFEST_FILE = EXTRACTED_DIR / "extraction_manifest.json"

VALIDATION_REPORT = (
    EXTRACTED_DIR / "validation_report.json"
)


# ============================================================
# CONSTANTS
# ============================================================

SUPPORTED_OUTPUT_FORMATS = {
    "csv",
    "pdf",
    "owl",
}

MIN_PDF_CHARACTERS = 100
MIN_OWL_TRIPLES = 1


# ============================================================
# CONSOLE
# ============================================================

WIDTH = 72


def line(char: str = "=") -> None:
    print(char * WIDTH)


def info(message: str) -> None:
    print(f"  {message}")


def success(message: str) -> None:
    print(f"  ✓ {message}")


def warning(message: str) -> None:
    print(f"  ⚠ {message}")


def error(message: str) -> None:
    print(f"  ✗ {message}")


# ============================================================
# GENERAL UTILITIES
# ============================================================

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(
            lambda: file.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def relative_to_project(path: Path) -> str:
    try:
        return str(
            path.resolve().relative_to(
                PROJECT_ROOT.resolve()
            )
        )
    except ValueError:
        return str(path.resolve())


def write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            data,
            file,
            indent=2,
            ensure_ascii=False,
        )
        file.write("\n")


def load_json(path: Path) -> Any:
    with path.open(
        "r",
        encoding="utf-8",
    ) as file:
        return json.load(file)


# ============================================================
# RAW FILE LOOKUP
# ============================================================

def collect_raw_files() -> List[Path]:
    if not RAW_DIR.exists():
        return []

    return sorted(
        [
            path
            for path in RAW_DIR.rglob("*")
            if path.is_file()
        ],
        key=lambda path: str(path).lower(),
    )


def find_raw_file(
    source_file: str,
) -> Path | None:
    """
    Resolve a manifest source_file against the actual
    project filesystem.

    Manifest paths are expected to look like:

        knowledge/raw/foo.csv
    """

    normalized = (
        str(source_file)
        .replace("\\", "/")
        .strip()
    )

    # --------------------------------------------------------
    # Direct project-relative path
    # --------------------------------------------------------

    direct = PROJECT_ROOT / normalized

    if direct.is_file():
        return direct.resolve()

    # --------------------------------------------------------
    # Filename fallback
    # --------------------------------------------------------

    filename = Path(normalized).name.lower()

    matches = [
        path
        for path in collect_raw_files()
        if path.name.lower() == filename
    ]

    if len(matches) == 1:
        return matches[0].resolve()

    return None


# ============================================================
# MANIFEST VALIDATION
# ============================================================

def validate_manifest(
    manifest: Any,
    report: Dict[str, Any],
) -> List[Dict[str, Any]]:

    issues = report["issues"]

    if not isinstance(manifest, dict):
        issues.append({
            "severity": "ERROR",
            "type": "manifest_structure",
            "message": (
                "Extraction manifest root must be a JSON object."
            ),
        })
        return []

    required_fields = [
        "manifest_version",
        "pipeline",
        "generated_at",
        "assets_discovered",
        "assets",
        "successful",
        "failed",
    ]

    for field in required_fields:
        if field not in manifest:
            issues.append({
                "severity": "ERROR",
                "type": "manifest_missing_field",
                "field": field,
                "message": (
                    f"Manifest is missing required field: {field}"
                ),
            })

    assets = manifest.get("assets")

    if not isinstance(assets, list):
        issues.append({
            "severity": "ERROR",
            "type": "manifest_assets_structure",
            "message": (
                "Manifest 'assets' must be a list."
            ),
        })
        return []

    discovered = manifest.get(
        "assets_discovered"
    )

    if discovered != len(assets):
        issues.append({
            "severity": "ERROR",
            "type": "asset_count_mismatch",
            "message": (
                f"assets_discovered={discovered}, "
                f"but assets list contains {len(assets)}."
            ),
        })

    successful = manifest.get(
        "successful"
    )

    failed = manifest.get(
        "failed"
    )

    if isinstance(successful, int):
        actual_successful = sum(
            1
            for asset in assets
            if isinstance(asset, dict)
            and asset.get("status") == "success"
        )

        if successful != actual_successful:
            issues.append({
                "severity": "ERROR",
                "type": "successful_count_mismatch",
                "message": (
                    f"Manifest successful={successful}, "
                    f"but {actual_successful} assets have "
                    f"status=success."
                ),
            })

    if isinstance(failed, int):
        actual_failed = sum(
            1
            for asset in assets
            if isinstance(asset, dict)
            and asset.get("status") == "failed"
        )

        if failed != actual_failed:
            issues.append({
                "severity": "ERROR",
                "type": "failed_count_mismatch",
                "message": (
                    f"Manifest failed={failed}, "
                    f"but {actual_failed} assets have "
                    f"status=failed."
                ),
            })

    return assets


# ============================================================
# ASSET ID VALIDATION
# ============================================================

def validate_asset_ids(
    assets: List[Dict[str, Any]],
    report: Dict[str, Any],
) -> None:

    issues = report["issues"]

    seen: Set[str] = set()

    for asset in assets:
        asset_id = asset.get("asset_id")

        if not asset_id:
            issues.append({
                "severity": "ERROR",
                "type": "missing_asset_id",
                "message": (
                    "Asset is missing asset_id."
                ),
            })
            continue

        if asset_id in seen:
            issues.append({
                "severity": "ERROR",
                "type": "duplicate_asset_id",
                "asset_id": asset_id,
                "message": (
                    f"Duplicate asset_id detected: {asset_id}"
                ),
            })

        seen.add(asset_id)


# ============================================================
# OUTPUT PATH VALIDATION
# ============================================================

def resolve_output_path(
    asset: Dict[str, Any],
) -> Path | None:

    output = asset.get("output")

    if not isinstance(output, str):
        return None

    path = Path(output)

    if path.is_absolute() and path.is_file():
        return path.resolve()

    project_path = PROJECT_ROOT / path

    if project_path.is_file():
        return project_path.resolve()

    # Filename fallback
    filename = path.name.lower()

    matches = [
        candidate
        for candidate in EXTRACTED_DIR.glob("*.json")
        if candidate.name.lower() == filename
    ]

    if len(matches) == 1:
        return matches[0].resolve()

    return None


# ============================================================
# COMMON ASSET VALIDATION
# ============================================================

def validate_common_asset_fields(
    asset: Dict[str, Any],
    raw_path: Path | None,
    output_path: Path | None,
    report: Dict[str, Any],
) -> None:

    issues = report["issues"]

    asset_id = asset.get(
        "asset_id",
        "<unknown>",
    )

    # --------------------------------------------------------
    # Raw source
    # --------------------------------------------------------

    if raw_path is None:
        issues.append({
            "severity": "ERROR",
            "type": "raw_file_missing",
            "asset_id": asset_id,
            "message": (
                f"Could not resolve raw source for "
                f"{asset_id}."
            ),
        })
    else:
        expected_hash = asset.get(
            "source_sha256"
        )

        if expected_hash:
            actual_hash = sha256_file(
                raw_path
            )

            if actual_hash != expected_hash:
                issues.append({
                    "severity": "ERROR",
                    "type": "sha256_mismatch",
                    "asset_id": asset_id,
                    "message": (
                        "Raw source SHA-256 does not match "
                        "the extraction manifest."
                    ),
                    "expected": expected_hash,
                    "actual": actual_hash,
                })

    # --------------------------------------------------------
    # Extraction output
    # --------------------------------------------------------

    if output_path is None:
        issues.append({
            "severity": "ERROR",
            "type": "output_missing",
            "asset_id": asset_id,
            "message": (
                "Extraction output file could not be resolved."
            ),
        })


# ============================================================
# CSV VALIDATION
# ============================================================

def validate_csv_asset(
    asset: Dict[str, Any],
    extracted: Dict[str, Any],
    report: Dict[str, Any],
) -> None:

    issues = report["issues"]

    asset_id = asset["asset_id"]

    required_fields = [
        "asset_type",
        "columns",
        "row_count",
        "records",
        "delimiter",
        "encoding",
    ]

    for field in required_fields:
        if field not in extracted:
            issues.append({
                "severity": "ERROR",
                "type": "csv_missing_field",
                "asset_id": asset_id,
                "field": field,
                "message": (
                    f"CSV extraction missing field: {field}"
                ),
            })

    if extracted.get("asset_type") != "csv":
        issues.append({
            "severity": "ERROR",
            "type": "csv_wrong_asset_type",
            "asset_id": asset_id,
            "message": (
                "CSV output does not identify itself "
                "as asset_type=csv."
            ),
        })

    columns = extracted.get(
        "columns"
    )

    records = extracted.get(
        "records"
    )

    row_count = extracted.get(
        "row_count"
    )

    if not isinstance(columns, list):
        return

    if not isinstance(records, list):
        return

    if row_count != len(records):
        issues.append({
            "severity": "ERROR",
            "type": "csv_row_count_mismatch",
            "asset_id": asset_id,
            "message": (
                f"row_count={row_count}, "
                f"but records contains {len(records)} rows."
            ),
        })

    if len(columns) == 0:
        issues.append({
            "severity": "ERROR",
            "type": "csv_empty_columns",
            "asset_id": asset_id,
            "message": (
                "CSV extraction contains no columns."
            ),
        })

    duplicate_columns = [
        column
        for column in set(columns)
        if columns.count(column) > 1
    ]

    if duplicate_columns:
        issues.append({
            "severity": "WARNING",
            "type": "csv_duplicate_columns",
            "asset_id": asset_id,
            "columns": duplicate_columns,
            "message": (
                "CSV contains duplicate column names."
            ),
        })

    for index, record in enumerate(records):

        if not isinstance(record, dict):
            issues.append({
                "severity": "ERROR",
                "type": "csv_invalid_record",
                "asset_id": asset_id,
                "row": index,
                "message": (
                    "CSV record is not a JSON object."
                ),
            })
            continue

        missing_columns = [
            column
            for column in columns
            if column not in record
        ]

        if missing_columns:
            issues.append({
                "severity": "ERROR",
                "type": "csv_record_schema_mismatch",
                "asset_id": asset_id,
                "row": index,
                "missing_columns": missing_columns,
                "message": (
                    "CSV record does not contain all "
                    "declared columns."
                ),
            })

            break


# ============================================================
# PDF VALIDATION
# ============================================================

def validate_pdf_asset(
    asset: Dict[str, Any],
    extracted: Dict[str, Any],
    report: Dict[str, Any],
) -> None:

    issues = report["issues"]

    asset_id = asset["asset_id"]

    required_fields = [
        "asset_type",
        "page_count",
        "character_count",
        "pages",
    ]

    for field in required_fields:
        if field not in extracted:
            issues.append({
                "severity": "ERROR",
                "type": "pdf_missing_field",
                "asset_id": asset_id,
                "field": field,
                "message": (
                    f"PDF extraction missing field: {field}"
                ),
            })

    if extracted.get("asset_type") != "pdf":
        issues.append({
            "severity": "ERROR",
            "type": "pdf_wrong_asset_type",
            "asset_id": asset_id,
            "message": (
                "PDF output does not identify itself "
                "as asset_type=pdf."
            ),
        })

        return

    page_count = extracted.get(
        "page_count"
    )

    pages = extracted.get(
        "pages"
    )

    character_count = extracted.get(
        "character_count"
    )

    if isinstance(page_count, int) and isinstance(pages, list):

        if page_count != len(pages):
            issues.append({
                "severity": "ERROR",
                "type": "pdf_page_count_mismatch",
                "asset_id": asset_id,
                "message": (
                    f"page_count={page_count}, "
                    f"but pages contains {len(pages)} entries."
                ),
            })

    if (
        isinstance(character_count, int)
        and character_count < MIN_PDF_CHARACTERS
    ):
        issues.append({
            "severity": "WARNING",
            "type": "pdf_low_text_content",
            "asset_id": asset_id,
            "characters": character_count,
            "message": (
                "PDF extraction produced very little text. "
                "Manual inspection may be required."
            ),
        })

    if isinstance(pages, list):

        calculated_characters = 0

        for page in pages:

            if not isinstance(page, dict):
                issues.append({
                    "severity": "ERROR",
                    "type": "pdf_invalid_page_record",
                    "asset_id": asset_id,
                    "message": (
                        "PDF page record is not an object."
                    ),
                })
                continue

            text = page.get(
                "text",
                "",
            )

            characters = page.get(
                "characters"
            )

            if not isinstance(text, str):
                issues.append({
                    "severity": "ERROR",
                    "type": "pdf_invalid_page_text",
                    "asset_id": asset_id,
                    "message": (
                        "PDF page text is not a string."
                    ),
                })
                continue

            calculated_characters += len(text)

            if characters != len(text):
                issues.append({
                    "severity": "ERROR",
                    "type": "pdf_character_count_mismatch",
                    "asset_id": asset_id,
                    "page": page.get("page"),
                    "message": (
                        "Page character count does not "
                        "match extracted text length."
                    ),
                })

        if (
            isinstance(character_count, int)
            and character_count != calculated_characters
        ):
            issues.append({
                "severity": "ERROR",
                "type": "pdf_total_character_mismatch",
                "asset_id": asset_id,
                "message": (
                    f"character_count={character_count}, "
                    f"but calculated total is "
                    f"{calculated_characters}."
                ),
            })


# ============================================================
# OWL VALIDATION
# ============================================================

def validate_owl_asset(
    asset: Dict[str, Any],
    extracted: Dict[str, Any],
    report: Dict[str, Any],
) -> None:

    issues = report["issues"]

    asset_id = asset["asset_id"]

    required_fields = [
        "asset_type",
        "triple_count",
        "namespaces",
        "triples",
    ]

    for field in required_fields:
        if field not in extracted:
            issues.append({
                "severity": "ERROR",
                "type": "owl_missing_field",
                "asset_id": asset_id,
                "field": field,
                "message": (
                    f"OWL extraction missing field: {field}"
                ),
            })

    if extracted.get("asset_type") != "owl":
        issues.append({
            "severity": "ERROR",
            "type": "owl_wrong_asset_type",
            "asset_id": asset_id,
            "message": (
                "OWL output does not identify itself "
                "as asset_type=owl."
            ),
        })

        return

    triples = extracted.get(
        "triples"
    )

    triple_count = extracted.get(
        "triple_count"
    )

    namespaces = extracted.get(
        "namespaces"
    )

    if not isinstance(triples, list):
        issues.append({
            "severity": "ERROR",
            "type": "owl_invalid_triples",
            "asset_id": asset_id,
            "message": (
                "OWL triples must be a list."
            ),
        })
        return

    if not isinstance(namespaces, dict):
        issues.append({
            "severity": "ERROR",
            "type": "owl_invalid_namespaces",
            "asset_id": asset_id,
            "message": (
                "OWL namespaces must be an object."
            ),
        })

    if triple_count != len(triples):
        issues.append({
            "severity": "ERROR",
            "type": "owl_triple_count_mismatch",
            "asset_id": asset_id,
            "message": (
                f"triple_count={triple_count}, "
                f"but triples contains {len(triples)}."
            ),
        })

    if isinstance(triple_count, int):
        if triple_count < MIN_OWL_TRIPLES:
            issues.append({
                "severity": "ERROR",
                "type": "owl_empty_graph",
                "asset_id": asset_id,
                "message": (
                    "OWL extraction contains no RDF triples."
                ),
            })

    for index, triple in enumerate(triples[:100]):

        if not isinstance(triple, dict):
            issues.append({
                "severity": "ERROR",
                "type": "owl_invalid_triple",
                "asset_id": asset_id,
                "index": index,
                "message": (
                    "OWL triple is not an object."
                ),
            })
            continue

        for field in (
            "subject",
            "predicate",
            "object",
        ):
            if field not in triple:
                issues.append({
                    "severity": "ERROR",
                    "type": "owl_triple_missing_field",
                    "asset_id": asset_id,
                    "index": index,
                    "field": field,
                    "message": (
                        f"OWL triple missing {field}."
                    ),
                })


# ============================================================
# SINGLE ASSET VALIDATION
# ============================================================

def validate_asset(
    asset: Dict[str, Any],
    report: Dict[str, Any],
) -> Dict[str, Any]:

    asset_id = asset.get(
        "asset_id",
        "<unknown>",
    )

    result = {
        "asset_id": asset_id,
        "status": "valid",
        "format": asset.get(
            "format"
        ),
        "source_file": asset.get(
            "source_file"
        ),
        "checks": {},
    }

    raw_path = find_raw_file(
        asset.get(
            "source_file",
            "",
        )
    )

    output_path = resolve_output_path(
        asset
    )

    validate_common_asset_fields(
        asset,
        raw_path,
        output_path,
        report,
    )

    result["checks"]["raw_exists"] = (
        raw_path is not None
    )

    result["checks"]["output_exists"] = (
        output_path is not None
    )

    # --------------------------------------------------------
    # SHA validation
    # --------------------------------------------------------

    if raw_path is not None:

        expected_hash = asset.get(
            "source_sha256"
        )

        if expected_hash:

            actual_hash = sha256_file(
                raw_path
            )

            result["checks"][
                "sha256_match"
            ] = actual_hash == expected_hash

        else:
            result["checks"][
                "sha256_match"
            ] = False

    # --------------------------------------------------------
    # Output JSON validation
    # --------------------------------------------------------

    extracted = None

    if output_path is not None:

        try:
            extracted = load_json(
                output_path
            )

            result["checks"][
                "json_valid"
            ] = True

        except Exception as exc:

            result["checks"][
                "json_valid"
            ] = False

            report["issues"].append({
                "severity": "ERROR",
                "type": "invalid_json",
                "asset_id": asset_id,
                "message": (
                    f"Could not parse extraction JSON: {exc}"
                ),
            })

    # --------------------------------------------------------
    # Format-specific validation
    # --------------------------------------------------------

    if isinstance(extracted, dict):

        fmt = asset.get(
            "format"
        )

        if fmt not in SUPPORTED_OUTPUT_FORMATS:
            report["issues"].append({
                "severity": "ERROR",
                "type": "unsupported_manifest_format",
                "asset_id": asset_id,
                "format": fmt,
                "message": (
                    f"Unsupported extraction format: {fmt}"
                ),
            })

        elif fmt == "csv":

            validate_csv_asset(
                asset,
                extracted,
                report,
            )

        elif fmt == "pdf":

            validate_pdf_asset(
                asset,
                extracted,
                report,
            )

        elif fmt == "owl":

            validate_owl_asset(
                asset,
                extracted,
                report,
            )

    # --------------------------------------------------------
    # Determine asset status
    # --------------------------------------------------------

    asset_errors = [
        issue
        for issue in report["issues"]
        if issue.get("asset_id") == asset_id
        and issue.get("severity") == "ERROR"
    ]

    if asset_errors:
        result["status"] = "invalid"

    return result


# ============================================================
# ORPHAN OUTPUT CHECK
# ============================================================

def validate_orphan_outputs(
    assets: List[Dict[str, Any]],
    report: Dict[str, Any],
) -> None:

    registered_outputs: Set[Path] = set()

    for asset in assets:

        output_path = resolve_output_path(
            asset
        )

        if output_path is not None:
            registered_outputs.add(
                output_path.resolve()
            )

    # validation_report itself is intentionally excluded
    # from orphan detection.

    extracted_json_files = {
        path.resolve()
        for path in EXTRACTED_DIR.glob("*.json")
        if path.name != VALIDATION_REPORT.name
    }

    orphans = (
        extracted_json_files
        - registered_outputs
    )

    for orphan in sorted(
        orphans,
        key=lambda path: str(path).lower(),
    ):
        report["issues"].append({
            "severity": "WARNING",
            "type": "orphan_extraction_output",
            "file": relative_to_project(orphan),
            "message": (
                "JSON file exists in extracted/ but is not "
                "registered in extraction_manifest.json."
            ),
        })


# ============================================================
# MAIN VALIDATION
# ============================================================

def main() -> int:

    line()

    print(
        "AMR KNOWLEDGE EXTRACTION VALIDATOR — V1"
    )

    line()

    info(
        f"Project  : {PROJECT_ROOT}"
    )

    info(
        f"Raw      : {RAW_DIR}"
    )

    info(
        f"Extracted: {EXTRACTED_DIR}"
    )

    info(
        f"Manifest : {MANIFEST_FILE}"
    )

    print()

    report: Dict[str, Any] = {
        "validation_version": "1.0",
        "pipeline": (
            "amr_knowledge_asset_extraction"
        ),
        "validated_at": utc_now(),
        "status": "valid",
        "issues": [],
        "assets": [],
        "summary": {},
    }

    # ========================================================
    # PRE-FLIGHT
    # ========================================================

    if not RAW_DIR.exists():

        error(
            f"Raw directory does not exist: {RAW_DIR}"
        )

        report["status"] = "invalid"

        write_json(
            VALIDATION_REPORT,
            report,
        )

        return 1

    if not EXTRACTED_DIR.exists():

        error(
            f"Extracted directory does not exist: "
            f"{EXTRACTED_DIR}"
        )

        report["status"] = "invalid"

        write_json(
            VALIDATION_REPORT,
            report,
        )

        return 1

    if not MANIFEST_FILE.exists():

        error(
            "Extraction manifest does not exist."
        )

        report["status"] = "invalid"

        write_json(
            VALIDATION_REPORT,
            report,
        )

        return 1

    # ========================================================
    # LOAD MANIFEST
    # ========================================================

    try:

        manifest = load_json(
            MANIFEST_FILE
        )

        success(
            "Extraction manifest loaded."
        )

    except Exception as exc:

        error(
            f"Could not read extraction manifest: {exc}"
        )

        report["status"] = "invalid"

        report["issues"].append({
            "severity": "ERROR",
            "type": "manifest_unreadable",
            "message": str(exc),
        })

        write_json(
            VALIDATION_REPORT,
            report,
        )

        return 1

    # ========================================================
    # MANIFEST STRUCTURE
    # ========================================================

    assets = validate_manifest(
        manifest,
        report,
    )

    if not assets:

        error(
            "No asset records could be validated."
        )

    else:

        success(
            f"Manifest contains {len(assets)} asset(s)."
        )

    # ========================================================
    # ASSET IDs
    # ========================================================

    validate_asset_ids(
        assets,
        report,
    )

    # ========================================================
    # ASSET VALIDATION
    # ========================================================

    print()

    valid_count = 0
    invalid_count = 0

    for index, asset in enumerate(
        assets,
        start=1,
    ):

        asset_id = asset.get(
            "asset_id",
            "<unknown>",
        )

        print(
            f"[{index:02d}/{len(assets):02d}] "
            f"{asset_id}"
        )

        result = validate_asset(
            asset,
            report,
        )

        report["assets"].append(
            result
        )

        if result["status"] == "valid":

            valid_count += 1

            success(
                "validation passed"
            )

        else:

            invalid_count += 1

            error(
                "validation failed"
            )

        print()

    # ========================================================
    # ORPHAN OUTPUTS
    # ========================================================

    validate_orphan_outputs(
        assets,
        report,
    )

    # ========================================================
    # SUMMARY
    # ========================================================

    error_count = sum(
        1
        for issue in report["issues"]
        if issue.get("severity") == "ERROR"
    )

    warning_count = sum(
        1
        for issue in report["issues"]
        if issue.get("severity") == "WARNING"
    )

    report["summary"] = {
        "assets_registered": len(assets),
        "assets_valid": valid_count,
        "assets_invalid": invalid_count,
        "errors": error_count,
        "warnings": warning_count,
    }

    if error_count > 0:
        report["status"] = "invalid"

    else:
        report["status"] = "valid"

    # ========================================================
    # WRITE REPORT
    # ========================================================

    write_json(
        VALIDATION_REPORT,
        report,
    )

    # ========================================================
    # CONSOLE SUMMARY
    # ========================================================

    line()

    print(
        "EXTRACTION VALIDATION SUMMARY"
    )

    line()

    print(
        f"Assets registered : {len(assets)}"
    )

    print(
        f"Assets valid      : {valid_count}"
    )

    print(
        f"Assets invalid    : {invalid_count}"
    )

    print(
        f"Errors            : {error_count}"
    )

    print(
        f"Warnings          : {warning_count}"
    )

    print()

    success(
        "Validation report written to:"
    )

    print(
        f"  {VALIDATION_REPORT}"
    )

    print()

    if error_count > 0:

        error(
            "EXTRACTION VALIDATION FAILED."
        )

        print()

        print(
            "Errors:"
        )

        for issue in report["issues"]:

            if issue.get("severity") != "ERROR":
                continue

            asset_id = issue.get(
                "asset_id",
                "global",
            )

            print(
                f"  - [{asset_id}] "
                f"{issue.get('message')}"
            )

        return 1

    if warning_count > 0:

        warning(
            "Extraction is structurally valid "
            "but has warnings."
        )

        return 0

    success(
        "EXTRACTION VALIDATION PASSED."
    )

    success(
        "All extracted assets are structurally "
        "and provenance valid."
    )

    return 0


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    sys.exit(main())