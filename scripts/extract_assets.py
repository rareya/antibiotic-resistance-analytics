# ============================================================
# AMR KNOWLEDGE ASSET EXTRACTOR
# Version 1.1
# ============================================================
#
# Purpose:
#   Deterministically extract machine-readable content from:
#
#       knowledge/raw/
#
# Supported:
#   CSV -> metadata-aware tabular extraction
#   PDF -> page-aware text extraction
#   OWL -> RDF / ontology extraction
#
# IMPORTANT:
#   knowledge/raw/ is NEVER modified.
#
# ============================================================

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import pandas as pd
except ImportError:
    pd = None

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

try:
    from rdflib import Graph, URIRef
    from rdflib.namespace import RDF, OWL
except ImportError:
    Graph = None
    URIRef = None
    RDF = None
    OWL = None


# ============================================================
# PATHS
# ============================================================

ROOT = Path(__file__).resolve().parents[1]

RAW_DIR = ROOT / "knowledge" / "raw"
METADATA_DIR = ROOT / "knowledge" / "metadata"
OUTPUT_DIR = ROOT / "knowledge" / "extracted"

INSPECTION_FILE = METADATA_DIR / "asset_inspection.json"
MANIFEST_FILE = OUTPUT_DIR / "extraction_manifest.json"


# ============================================================
# CONFIGURATION
# ============================================================

CSV_PREVIEW_ROWS = 10
CSV_HEADER_SCAN_ROWS = 40
PDF_MAX_CHARS_PER_PAGE = 100_000
OWL_SAMPLE_LIMIT = 100


# ============================================================
# TERMINAL HELPERS
# ============================================================

WIDTH = 70


def line(char="─"):
    print(char * WIDTH)


def header(text):
    print()
    line("=")
    print(text)
    line("=")


def info(message):
    print(f"  {message}")


def success(message):
    print(f"  ✓ {message}")


def warning(message):
    print(f"  ⚠ {message}")


def error(message):
    print(f"  ✗ {message}")


# ============================================================
# GENERAL UTILITIES
# ============================================================

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def relative_to_root(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def safe_filename(path: Path) -> str:

    name = path.stem

    name = re.sub(r"[^\w\-]+", "_", name)
    name = re.sub(r"_+", "_", name)

    return name.strip("_").lower()


def sha256_file(path: Path) -> str:

    digest = hashlib.sha256()

    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def ensure_output_dirs():

    (OUTPUT_DIR / "csv").mkdir(
        parents=True,
        exist_ok=True
    )

    (OUTPUT_DIR / "pdf").mkdir(
        parents=True,
        exist_ok=True
    )

    (OUTPUT_DIR / "owl").mkdir(
        parents=True,
        exist_ok=True
    )


# ============================================================
# INSPECTION REPORT
# ============================================================

def load_inspection_report():

    if not INSPECTION_FILE.exists():

        raise FileNotFoundError(
            f"Inspection report not found:\n"
            f"{INSPECTION_FILE}\n\n"
            f"Run inspect_assets.py first."
        )

    with INSPECTION_FILE.open(
        "r",
        encoding="utf-8"
    ) as f:

        return json.load(f)


# ============================================================
# CSV UTILITIES
# ============================================================

def detect_encoding(path: Path) -> str:

    encodings = [
        "utf-8-sig",
        "utf-8",
        "cp1252",
        "latin-1",
    ]

    for encoding in encodings:

        try:

            with path.open(
                "r",
                encoding=encoding
            ) as f:

                f.read(20_000)

            return encoding

        except UnicodeDecodeError:
            continue

    return "latin-1"


def read_sample_lines(
    path: Path,
    encoding: str,
    max_lines: int = CSV_HEADER_SCAN_ROWS
) -> List[str]:

    with path.open(
        "r",
        encoding=encoding,
        errors="replace"
    ) as f:

        lines = []

        for _ in range(max_lines):

            line_value = f.readline()

            if not line_value:
                break

            lines.append(
                line_value.rstrip("\n\r")
            )

    return lines


def detect_delimiter_from_lines(
    lines: List[str]
) -> str:

    candidates = [
        ",",
        ";",
        "\t",
        "|",
    ]

    best_delimiter = ","
    best_score = -1

    for delimiter in candidates:

        counts = []

        for line_value in lines:

            if not line_value.strip():
                continue

            try:

                parsed = next(
                    csv.reader(
                        [line_value],
                        delimiter=delimiter
                    )
                )

                counts.append(len(parsed))

            except Exception:
                continue

        if not counts:
            continue

        # A real table delimiter should consistently
        # produce multiple columns.
        multi_column = sum(
            1 for count in counts
            if count > 1
        )

        consistency = (
            len(set(counts))
            if counts
            else 999
        )

        score = (
            multi_column * 10
            - consistency
        )

        if score > best_score:

            best_score = score
            best_delimiter = delimiter

    return best_delimiter


def normalize_text(value: Any) -> str:

    if value is None:
        return ""

    value = str(value)

    value = value.replace("\ufeff", "")
    value = value.replace("\xa0", " ")

    value = re.sub(
        r"\s+",
        " ",
        value
    )

    return value.strip()


def clean_column_name(column: Any) -> str:

    value = normalize_text(column)

    value = re.sub(
        r"[^\w]+",
        "_",
        value
    )

    value = re.sub(
        r"_+",
        "_",
        value
    )

    return value.strip("_").lower()


def looks_like_metadata_line(
    parsed: List[str]
) -> bool:

    if not parsed:
        return True

    non_empty = [
        x for x in parsed
        if normalize_text(x)
    ]

    return len(non_empty) <= 1


def score_header_candidate(
    parsed: List[str],
    line_number: int
) -> float:

    if len(parsed) < 2:
        return -100.0

    values = [
        normalize_text(x)
        for x in parsed
    ]

    values = [
        x for x in values
        if x
    ]

    if len(values) < 2:
        return -100.0

    score = 0.0

    # --------------------------------------------------------
    # More columns = more likely to be the table
    # --------------------------------------------------------

    if len(values) >= 3:
        score += 15

    if len(values) >= 5:
        score += 10

    if len(values) >= 8:
        score += 5

    # --------------------------------------------------------
    # Header-like vocabulary
    # --------------------------------------------------------

    header_keywords = {
        "country",
        "region",
        "year",
        "date",
        "pathogen",
        "bacteria",
        "bacterial",
        "antibiotic",
        "antibiotics",
        "drug",
        "resistance",
        "resistant",
        "infection",
        "specimen",
        "isolate",
        "percentage",
        "percent",
        "frequency",
        "testing",
        "coverage",
        "indicator",
        "value",
        "rate",
        "group",
        "sex",
        "age",
        "origin",
        "organism",
    }

    for value in values:

        lowered = value.lower()

        for keyword in header_keywords:

            if keyword in lowered:

                score += 4
                break

    # --------------------------------------------------------
    # Headers tend to be relatively short
    # --------------------------------------------------------

    average_length = sum(
        len(x)
        for x in values
    ) / len(values)

    if average_length < 60:
        score += 5

    if average_length < 35:
        score += 5

    # --------------------------------------------------------
    # Metadata often appears before the table.
    #
    # A candidate very early in the file is slightly
    # penalized so that a descriptive row isn't mistaken
    # for the actual table.
    # --------------------------------------------------------

    if line_number <= 2:
        score -= 5

    return score


def detect_header_row(
    lines: List[str],
    delimiter: str
) -> Dict[str, Any]:

    candidates = []

    for index, line_value in enumerate(
        lines
    ):

        if not line_value.strip():
            continue

        try:

            parsed = next(
                csv.reader(
                    [line_value],
                    delimiter=delimiter
                )
            )

        except Exception:
            continue

        score = score_header_candidate(
            parsed,
            index + 1
        )

        if score > -50:

            candidates.append(
                {
                    "line_number": index + 1,
                    "score": score,
                    "columns": [
                        normalize_text(x)
                        for x in parsed
                    ],
                }
            )

    if not candidates:

        return {
            "found": False,
            "line_number": None,
            "score": None,
            "columns": [],
        }

    candidates.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    best = candidates[0]

    return {
        "found": True,
        "line_number": best["line_number"],
        "score": best["score"],
        "columns": best["columns"],
        "candidates": candidates[:10],
    }


def extract_csv(path: Path) -> Dict[str, Any]:

    if pd is None:

        raise RuntimeError(
            "pandas is required for CSV extraction.\n"
            "Install with:\n"
            "python -m pip install pandas"
        )

    encoding = detect_encoding(path)

    lines = read_sample_lines(
        path,
        encoding
    )

    if not lines:

        raise RuntimeError(
            "CSV file is empty."
        )

    delimiter = detect_delimiter_from_lines(
        lines
    )

    header_info = detect_header_row(
        lines,
        delimiter
    )

    if not header_info["found"]:

        raise RuntimeError(
            "Could not identify a table header."
        )

    header_line = header_info["line_number"]

    # pandas skiprows is zero-based.
    skiprows = header_line - 1

    # --------------------------------------------------------
    # Read actual table
    # --------------------------------------------------------

    try:

        df = pd.read_csv(
            path,
            encoding=encoding,
            sep=delimiter,
            skiprows=skiprows,
            low_memory=False,
        )

    except Exception as exc:

        # ----------------------------------------------------
        # Fallback to Python engine.
        #
        # The Python parser is slower but considerably more
        # forgiving of irregular public datasets.
        # ----------------------------------------------------

        try:

            df = pd.read_csv(
                path,
                encoding=encoding,
                sep=delimiter,
                skiprows=skiprows,
                engine="python",
                on_bad_lines="warn",
            )

        except Exception as fallback_exc:

            raise RuntimeError(
                f"Could not read CSV after header detection.\n"
                f"Primary parser: {exc}\n"
                f"Fallback parser: {fallback_exc}"
            ) from fallback_exc

    # --------------------------------------------------------
    # Clean columns
    # --------------------------------------------------------

    original_columns = [
        str(column)
        for column in df.columns
    ]

    normalized_columns = [
        clean_column_name(column)
        for column in df.columns
    ]

    # Prevent duplicate normalized column names.
    seen = {}

    final_columns = []

    for column in normalized_columns:

        if column not in seen:

            seen[column] = 0
            final_columns.append(column)

        else:

            seen[column] += 1

            final_columns.append(
                f"{column}_{seen[column]}"
            )

    df.columns = final_columns

    # --------------------------------------------------------
    # Remove completely empty rows
    # --------------------------------------------------------

    before_rows = len(df)

    df = df.dropna(
        how="all"
    ).reset_index(
        drop=True
    )

    removed_empty_rows = (
        before_rows - len(df)
    )

    # --------------------------------------------------------
    # Column metadata
    # --------------------------------------------------------

    columns = []

    for original, normalized in zip(
        original_columns,
        final_columns
    ):

        series = df[normalized]

        sample_values = (
            series
            .dropna()
            .astype(str)
            .head(5)
            .tolist()
        )

        columns.append(
            {
                "name": original,
                "normalized_name": normalized,
                "dtype": str(series.dtype),
                "non_null_count": int(
                    series.notna().sum()
                ),
                "null_count": int(
                    series.isna().sum()
                ),
                "unique_count": int(
                    series.nunique(
                        dropna=True
                    )
                ),
                "sample_values": sample_values,
            }
        )

    # --------------------------------------------------------
    # Numeric summary
    # --------------------------------------------------------

    numeric_summary = {}

    for column in df.columns:

        if pd.api.types.is_numeric_dtype(
            df[column]
        ):

            series = pd.to_numeric(
                df[column],
                errors="coerce"
            )

            numeric_summary[column] = {
                "min": safe_number(
                    series.min()
                ),
                "max": safe_number(
                    series.max()
                ),
                "mean": safe_number(
                    series.mean()
                ),
                "median": safe_number(
                    series.median()
                ),
            }

    # --------------------------------------------------------
    # Preview
    # --------------------------------------------------------

    preview_df = df.head(
        CSV_PREVIEW_ROWS
    )

    preview = json.loads(
        preview_df.to_json(
            orient="records",
            date_format="iso"
        )
    )

    # --------------------------------------------------------
    # Preserve metadata preceding header
    # --------------------------------------------------------

    metadata_lines = lines[
        :header_line - 1
    ]

    metadata_lines = [
        normalize_text(x)
        for x in metadata_lines
        if normalize_text(x)
    ]

    metadata = {
        "lines_before_header": len(
            metadata_lines
        ),
        "raw_lines": metadata_lines,
    }

    # --------------------------------------------------------
    # Write normalized CSV
    # --------------------------------------------------------

    output_stem = safe_filename(path)

    output_csv = (
        OUTPUT_DIR
        / "csv"
        / f"{output_stem}.csv"
    )

    output_metadata = (
        OUTPUT_DIR
        / "csv"
        / f"{output_stem}.metadata.json"
    )

    df.to_csv(
        output_csv,
        index=False,
        encoding="utf-8"
    )

    extraction_metadata = {

        "format": "csv",

        "source_file": relative_to_root(
            path
        ),

        "output_file": relative_to_root(
            output_csv
        ),

        "metadata_file": relative_to_root(
            output_metadata
        ),

        "encoding": encoding,

        "delimiter": delimiter,

        "header_detection": {
            "header_line": header_line,
            "header_score": header_info["score"],
            "candidate_headers": (
                header_info.get(
                    "candidates",
                    []
                )
            ),
        },

        "metadata_before_table": metadata,

        "row_count": int(len(df)),

        "column_count": int(
            len(df.columns)
        ),

        "removed_empty_rows": (
            removed_empty_rows
        ),

        "columns": columns,

        "numeric_summary": numeric_summary,

        "preview": preview,

        "extraction_status": "success",
    }

    with output_metadata.open(
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            extraction_metadata,
            f,
            indent=2,
            ensure_ascii=False
        )

    return {
        "format": "csv",

        "source_file": relative_to_root(
            path
        ),

        "output_file": relative_to_root(
            output_csv
        ),

        "metadata_file": relative_to_root(
            output_metadata
        ),

        "encoding": encoding,

        "delimiter": delimiter,

        "header_line": header_line,

        "header_score": header_info["score"],

        "row_count": int(len(df)),

        "column_count": int(
            len(df.columns)
        ),

        "extraction_status": "success",
    }


def safe_number(value):

    if pd is None:
        return value

    try:

        if pd.isna(value):
            return None

    except Exception:
        pass

    try:
        return float(value)

    except Exception:
        return value


# ============================================================
# PDF EXTRACTION
# ============================================================

def clean_pdf_text(text: str) -> str:

    if not text:
        return ""

    text = text.replace(
        "\r\n",
        "\n"
    )

    text = text.replace(
        "\r",
        "\n"
    )

    text = re.sub(
        r"[ \t]+",
        " ",
        text
    )

    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text
    )

    return text.strip()


def extract_pdf(path: Path):

    if PdfReader is None:

        raise RuntimeError(
            "pypdf is required for PDF extraction.\n"
            "Install with:\n"
            "python -m pip install pypdf"
        )

    reader = PdfReader(
        str(path)
    )

    page_records = []

    total_characters = 0
    pages_with_text = 0

    for page_number, page in enumerate(
        reader.pages,
        start=1
    ):

        try:

            text = (
                page.extract_text()
                or ""
            )

        except Exception as exc:

            warning(
                f"page {page_number}: "
                f"extraction failed: {exc}"
            )

            text = ""

        text = clean_pdf_text(
            text
        )

        if text:
            pages_with_text += 1

        total_characters += len(text)

        if len(text) > PDF_MAX_CHARS_PER_PAGE:

            text = text[
                :PDF_MAX_CHARS_PER_PAGE
            ]

        page_records.append(
            {
                "page": page_number,
                "character_count": len(text),
                "has_text": bool(text),
                "text": text,
            }
        )

    output_name = (
        safe_filename(path)
        + ".json"
    )

    output_path = (
        OUTPUT_DIR
        / "pdf"
        / output_name
    )

    result = {

        "format": "pdf",

        "source_file": relative_to_root(
            path
        ),

        "output_file": relative_to_root(
            output_path
        ),

        "page_count": len(
            reader.pages
        ),

        "pages_with_text": (
            pages_with_text
        ),

        "total_characters": (
            total_characters
        ),

        "pages": page_records,

        "extraction_status": "success",
    }

    with output_path.open(
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            result,
            f,
            indent=2,
            ensure_ascii=False
        )

    return {
        "format": "pdf",

        "source_file": relative_to_root(
            path
        ),

        "output_file": relative_to_root(
            output_path
        ),

        "page_count": len(
            reader.pages
        ),

        "pages_with_text": (
            pages_with_text
        ),

        "total_characters": (
            total_characters
        ),

        "extraction_status": "success",
    }


# ============================================================
# OWL EXTRACTION
# ============================================================

def uri_label(uri):

    value = str(uri)

    if "#" in value:
        return value.rsplit(
            "#",
            1
        )[-1]

    return value.rstrip(
        "/"
    ).rsplit(
        "/",
        1
    )[-1]


def extract_owl(path: Path):

    if Graph is None:

        raise RuntimeError(
            "rdflib is required for OWL extraction.\n"
            "Install with:\n"
            "python -m pip install rdflib"
        )

    graph = Graph()

    try:

        graph.parse(
            str(path),
            format="xml"
        )

    except Exception:

        graph.parse(
            str(path)
        )

    triple_count = len(graph)

    namespaces = {}

    for prefix, namespace in graph.namespaces():

        namespaces[str(prefix)] = str(
            namespace
        )

    classes = set()
    object_properties = set()
    datatype_properties = set()

    for subject, predicate, obj in graph:

        if predicate == RDF.type:

            if obj == OWL.Class:

                classes.add(
                    str(subject)
                )

            elif obj == OWL.ObjectProperty:

                object_properties.add(
                    str(subject)
                )

            elif obj == OWL.DatatypeProperty:

                datatype_properties.add(
                    str(subject)
                )

    class_records = []

    for class_uri in sorted(classes):

        class_records.append(
            {
                "uri": class_uri,
                "label": uri_label(
                    class_uri
                ),
            }
        )

    property_records = []

    for prop in sorted(
        object_properties
    ):

        property_records.append(
            {
                "uri": prop,
                "label": uri_label(prop),
                "property_type":
                    "object_property",
            }
        )

    for prop in sorted(
        datatype_properties
    ):

        property_records.append(
            {
                "uri": prop,
                "label": uri_label(prop),
                "property_type":
                    "datatype_property",
            }
        )

    sample_triples = []

    for index, (
        subject,
        predicate,
        obj
    ) in enumerate(graph):

        if index >= OWL_SAMPLE_LIMIT:
            break

        sample_triples.append(
            {
                "subject": str(subject),
                "subject_label": uri_label(
                    subject
                ),

                "predicate": str(predicate),
                "predicate_label": uri_label(
                    predicate
                ),

                "object": str(obj),

                "object_label": (
                    uri_label(obj)
                    if isinstance(
                        obj,
                        URIRef
                    )
                    else str(obj)
                ),
            }
        )

    output_name = (
        safe_filename(path)
        + ".json"
    )

    output_path = (
        OUTPUT_DIR
        / "owl"
        / output_name
    )

    result = {

        "format": "owl",

        "source_file": relative_to_root(
            path
        ),

        "output_file": relative_to_root(
            output_path
        ),

        "triple_count": triple_count,

        "class_count": len(classes),

        "object_property_count": len(
            object_properties
        ),

        "datatype_property_count": len(
            datatype_properties
        ),

        "namespaces": namespaces,

        "classes": class_records,

        "properties": property_records,

        "sample_triples": sample_triples,

        "extraction_status": "success",
    }

    with output_path.open(
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            result,
            f,
            indent=2,
            ensure_ascii=False
        )

    return {
        "format": "owl",

        "source_file": relative_to_root(
            path
        ),

        "output_file": relative_to_root(
            output_path
        ),

        "triple_count": triple_count,

        "class_count": len(classes),

        "object_property_count": len(
            object_properties
        ),

        "datatype_property_count": len(
            datatype_properties
        ),

        "extraction_status": "success",
    }


# ============================================================
# DISPATCH
# ============================================================

def extract_file(path: Path):

    suffix = path.suffix.lower()

    if suffix == ".csv":
        return extract_csv(path)

    if suffix == ".pdf":
        return extract_pdf(path)

    if suffix == ".owl":
        return extract_owl(path)

    raise ValueError(
        f"Unsupported file format: {suffix}"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    header(
        "AMR KNOWLEDGE ASSET EXTRACTOR"
    )

    print()
    print(
        f"Raw      : {RAW_DIR}"
    )

    print(
        f"Metadata : {INSPECTION_FILE}"
    )

    print(
        f"Output   : {OUTPUT_DIR}"
    )

    print()

    if not RAW_DIR.exists():

        error(
            f"Raw directory does not exist: "
            f"{RAW_DIR}"
        )

        return 1

    try:

        load_inspection_report()

        success(
            "asset_inspection.json loaded"
        )

    except Exception as exc:

        error(str(exc))

        return 1

    ensure_output_dirs()

    files = sorted(
        [
            path
            for path in RAW_DIR.rglob("*")
            if path.is_file()
            and path.suffix.lower()
            in {
                ".csv",
                ".pdf",
                ".owl",
            }
        ]
    )

    print()
    print(
        f"Found {len(files)} "
        f"extractable file(s)."
    )

    manifest = {

        "pipeline":
            "AMR Knowledge Extraction",

        "version": "1.1",

        "generated_at":
            utc_now(),

        "raw_directory":
            relative_to_root(
                RAW_DIR
            ),

        "inspection_report":
            relative_to_root(
                INSPECTION_FILE
            ),

        "output_directory":
            relative_to_root(
                OUTPUT_DIR
            ),

        "assets": [],

        "summary": {
            "total": 0,
            "success": 0,
            "failed": 0,
            "csv": 0,
            "pdf": 0,
            "owl": 0,
        },
    }

    # --------------------------------------------------------
    # Process assets
    # --------------------------------------------------------

    for index, path in enumerate(
        files,
        start=1
    ):

        print()

        print(
            f"[{index:02d}/{len(files):02d}] "
            f"{relative_to_root(path)}"
        )

        record = {

            "source_file":
                relative_to_root(path),

            "filename":
                path.name,

            "extension":
                path.suffix.lower(),

            "size_bytes":
                path.stat().st_size,

            "sha256": None,

            "status": "failed",

            "extraction": None,

            "error": None,
        }

        try:

            record["sha256"] = (
                sha256_file(path)
            )

            extraction = extract_file(
                path
            )

            record["extraction"] = (
                extraction
            )

            record["status"] = (
                "success"
            )

            manifest[
                "summary"
            ][
                "success"
            ] += 1

            extension = (
                path.suffix
                .lower()
                .lstrip(".")
            )

            if extension in (
                manifest[
                    "summary"
                ]
            ):

                manifest[
                    "summary"
                ][extension] += 1

            # Helpful CSV output
            if extension == "csv":

                info(
                    f"header row: "
                    f"{extraction.get('header_line')}"
                )

                info(
                    f"rows: "
                    f"{extraction.get('row_count')}"
                )

                info(
                    f"columns: "
                    f"{extraction.get('column_count')}"
                )

            success(
                "extracted"
            )

        except Exception as exc:

            record["error"] = str(exc)

            manifest[
                "summary"
            ][
                "failed"
            ] += 1

            error(str(exc))

        manifest[
            "assets"
        ].append(record)

        manifest[
            "summary"
        ][
            "total"
        ] += 1

    # --------------------------------------------------------
    # Write manifest
    # --------------------------------------------------------

    with MANIFEST_FILE.open(
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            manifest,
            f,
            indent=2,
            ensure_ascii=False
        )

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    header(
        "EXTRACTION SUMMARY"
    )

    print(
        f"Assets discovered : "
        f"{manifest['summary']['total']}"
    )

    print(
        f"Successfully extracted : "
        f"{manifest['summary']['success']}"
    )

    print(
        f"Failed : "
        f"{manifest['summary']['failed']}"
    )

    print()

    print("Formats:")

    print(
        f"  CSV : "
        f"{manifest['summary']['csv']}"
    )

    print(
        f"  PDF : "
        f"{manifest['summary']['pdf']}"
    )

    print(
        f"  OWL : "
        f"{manifest['summary']['owl']}"
    )

    print()

    success(
        "Extraction manifest written to:"
    )

    print(
        f"  {MANIFEST_FILE}"
    )

    if manifest[
        "summary"
    ][
        "failed"
    ] > 0:

        print()

        warning(
            "Some assets failed extraction."
        )

        return 1

    print()

    success(
        "ALL ASSETS EXTRACTED SUCCESSFULLY"
    )

    return 0


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    sys.exit(main())