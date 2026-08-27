# ============================================================
# AMR KNOWLEDGE ASSET EXTRACTOR — V2.1
#
# Purpose:
#   Extract heterogeneous knowledge assets from knowledge/raw/
#   into knowledge/extracted/
#
# Supported:
#   - CSV
#   - PDF
#   - OWL / RDF / XML
#
# Design principles:
#   - Never modify knowledge/raw/
#   - Uses asset_inspection.json when available
#   - Resolves paths against the real filesystem
#   - Handles messy WHO CSV exports
#   - Preserves extraction provenance
#   - Produces one extraction manifest
#   - Fails per asset, never the entire pipeline
#   - JSON output is always serializable
# ============================================================

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# PATHS
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]

RAW_DIR = PROJECT_ROOT / "knowledge" / "raw"
METADATA_DIR = PROJECT_ROOT / "knowledge" / "metadata"
EXTRACTED_DIR = PROJECT_ROOT / "knowledge" / "extracted"

INSPECTION_FILE = METADATA_DIR / "asset_inspection.json"
MANIFEST_FILE = EXTRACTED_DIR / "extraction_manifest.json"


# ============================================================
# CONSTANTS
# ============================================================

SUPPORTED_FORMATS = {
    ".csv": "csv",
    ".pdf": "pdf",
    ".owl": "owl",
    ".rdf": "owl",
    ".xml": "owl",
}

TEXT_ENCODINGS = [
    "utf-8-sig",
    "utf-8",
    "cp1252",
    "latin-1",
]

WIDTH = 70


# ============================================================
# CONSOLE HELPERS
# ============================================================

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


def safe_filename(value: str) -> str:
    value = str(value)

    value = re.sub(
        r'[<>:"/\\|?*]',
        "_",
        value,
    )

    value = re.sub(
        r"\s+",
        "_",
        value,
    )

    value = re.sub(
        r"_+",
        "_",
        value,
    )

    return value.strip("._") or "asset"


def repo_relative(path: Path) -> str:
    """
    Convert a path into a repository-relative POSIX-style path.
    """

    try:
        relative = path.resolve().relative_to(
            PROJECT_ROOT.resolve()
        )
        return relative.as_posix()

    except ValueError:
        return str(path)


def json_safe(value: Any) -> Any:
    """
    Recursively convert non-JSON-native values into
    JSON-safe representations.
    """

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, dict):
        return {
            str(key): json_safe(val)
            for key, val in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [
            json_safe(item)
            for item in value
        ]

    return value


def write_json(
    path: Path,
    data: Any,
) -> None:

    path = Path(path)

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            json_safe(data),
            file,
            indent=2,
            ensure_ascii=False,
        )

        file.write("\n")


# ============================================================
# INSPECTION MANIFEST
# ============================================================

def load_inspection_manifest() -> Any:
    """
    Load asset_inspection.json.

    The extractor can technically operate without inspection
    metadata because knowledge/raw/ is authoritative, but when
    the inspection file exists we use it for provenance.
    """

    if not INSPECTION_FILE.exists():
        warning(
            "asset_inspection.json not found."
        )

        warning(
            "Continuing using knowledge/raw/ discovery."
        )

        return {}

    with INSPECTION_FILE.open(
        "r",
        encoding="utf-8",
    ) as file:

        inspection = json.load(file)

    success(
        "asset_inspection.json loaded"
    )

    return inspection


# ============================================================
# RAW FILE DISCOVERY
# ============================================================

def collect_raw_files() -> List[Path]:
    """
    Discover supported files physically present under
    knowledge/raw/.

    The filesystem is authoritative.
    """

    if not RAW_DIR.exists():
        raise FileNotFoundError(
            f"Raw directory does not exist: {RAW_DIR}"
        )

    files: List[Path] = []

    for path in RAW_DIR.rglob("*"):

        if not path.is_file():
            continue

        if path.name.startswith("~$"):
            continue

        if path.suffix.lower() not in SUPPORTED_FORMATS:
            continue

        files.append(path)

    return sorted(
        files,
        key=lambda p: str(p).lower(),
    )


# ============================================================
# INSPECTION RECORD NORMALIZATION
# ============================================================

def flatten_inspection_records(
    inspection: Any,
) -> List[Dict[str, Any]]:
    """
    Convert multiple possible inspection manifest structures
    into a simple list of asset dictionaries.

    Supported examples:

        {
            "assets": [...]
        }

        {
            "files": [...]
        }

        {
            "assets": {
                "asset_id": {...}
            }
        }

        [...]
    """

    records: List[Dict[str, Any]] = []

    if isinstance(
        inspection,
        list,
    ):

        for item in inspection:

            if isinstance(
                item,
                dict,
            ):
                records.append(item)

        return records

    if not isinstance(
        inspection,
        dict,
    ):
        return records

    # --------------------------------------------------------
    # Preferred containers
    # --------------------------------------------------------

    for container_key in (
        "assets",
        "files",
        "records",
        "inspection",
        "items",
        "results",
    ):

        container = inspection.get(
            container_key
        )

        if isinstance(
            container,
            list,
        ):

            for item in container:

                if isinstance(
                    item,
                    dict,
                ):
                    records.append(item)

            if records:
                return records

        elif isinstance(
            container,
            dict,
        ):

            for key, value in container.items():

                if isinstance(
                    value,
                    dict,
                ):

                    record = dict(value)

                    if "id" not in record:
                        record["id"] = key

                    records.append(record)

            if records:
                return records

    # --------------------------------------------------------
    # Generic dictionary-of-assets
    # --------------------------------------------------------

    for key, value in inspection.items():

        if not isinstance(
            value,
            dict,
        ):
            continue

        possible_file = any(
            field in value
            for field in (
                "path",
                "file",
                "filename",
                "filepath",
                "relative_path",
                "source_path",
                "raw_path",
                "local_path",
            )
        )

        if possible_file:

            record = dict(value)

            if "id" not in record:
                record["id"] = key

            records.append(record)

    return records


# ============================================================
# PATH NORMALIZATION
# ============================================================

def normalize_relative_path(
    value: str,
) -> str:
    """
    Normalize Windows / POSIX paths.
    """

    value = str(value).strip()

    value = value.replace(
        "\\",
        "/",
    )

    return value


def candidate_path_values(
    asset: Dict[str, Any],
) -> List[str]:
    """
    Extract all likely path fields from an inspection record.
    """

    candidates: List[str] = []

    for key in (
        "path",
        "file",
        "filename",
        "filepath",
        "relative_path",
        "source_path",
        "raw_path",
        "local_path",
    ):

        value = asset.get(key)

        if isinstance(
            value,
            str,
        ) and value.strip():

            candidates.append(
                value.strip()
            )

    return candidates


# ============================================================
# ASSET PATH RESOLUTION
# ============================================================

def resolve_asset_path(
    asset: Dict[str, Any],
    raw_files: List[Path],
) -> Optional[Path]:
    """
    Resolve an inspection record to an actual raw file.

    Resolution order:

      1. Absolute path
      2. Project-relative path
      3. Raw-relative path
      4. knowledge/raw/... stripped path
      5. Filename match
      6. Asset ID / name match
    """

    candidates = candidate_path_values(
        asset
    )

    # --------------------------------------------------------
    # Direct path matching
    # --------------------------------------------------------

    for candidate in candidates:

        normalized = normalize_relative_path(
            candidate
        )

        candidate_path = Path(
            candidate
        )

        # Absolute path
        if (
            candidate_path.is_absolute()
            and candidate_path.exists()
            and candidate_path.is_file()
        ):
            return candidate_path.resolve()

        # Project-relative
        project_candidate = (
            PROJECT_ROOT / normalized
        )

        if (
            project_candidate.exists()
            and project_candidate.is_file()
        ):
            return project_candidate.resolve()

        # Raw-relative
        raw_candidate = (
            RAW_DIR / normalized
        )

        if (
            raw_candidate.exists()
            and raw_candidate.is_file()
        ):
            return raw_candidate.resolve()

        # Strip common prefixes
        stripped = normalized

        prefixes = (
            "knowledge/raw/",
            "knowledge/raw",
            "raw/",
            "raw",
        )

        lowered = stripped.lower()

        for prefix in prefixes:

            if lowered.startswith(
                prefix.lower()
            ):

                stripped = stripped[
                    len(prefix):
                ].lstrip("/")

                break

        raw_candidate = (
            RAW_DIR / stripped
        )

        if (
            raw_candidate.exists()
            and raw_candidate.is_file()
        ):
            return raw_candidate.resolve()

    # --------------------------------------------------------
    # Filename matching
    # --------------------------------------------------------

    filenames = []

    for candidate in candidates:

        normalized = normalize_relative_path(
            candidate
        )

        filenames.append(
            Path(normalized).name.lower()
        )

    for filename in filenames:

        matches = [
            path
            for path in raw_files
            if path.name.lower() == filename
        ]

        if len(matches) == 1:
            return matches[0].resolve()

    # --------------------------------------------------------
    # Asset ID / name matching
    # --------------------------------------------------------

    identifiers: List[str] = []

    for key in (
        "id",
        "asset_id",
        "name",
        "asset_name",
    ):

        value = asset.get(key)

        if isinstance(
            value,
            str,
        ) and value.strip():

            identifiers.append(
                safe_filename(
                    value
                ).lower()
            )

    for identifier in identifiers:

        # Exact stem match
        exact = [
            path
            for path in raw_files
            if safe_filename(
                path.stem
            ).lower() == identifier
        ]

        if len(exact) == 1:
            return exact[0].resolve()

        # Partial match
        partial = [
            path
            for path in raw_files
            if (
                identifier
                in safe_filename(
                    path.stem
                ).lower()
            )
            or (
                safe_filename(
                    path.stem
                ).lower()
                in identifier
            )
        ]

        if len(partial) == 1:
            return partial[0].resolve()

    return None


# ============================================================
# BUILD ASSET RECORDS
# ============================================================

def build_asset_records(
    inspection: Any,
    raw_files: List[Path],
) -> List[Dict[str, Any]]:
    """
    Build the definitive extraction asset list.

    Inspection metadata provides IDs/provenance.

    knowledge/raw/ provides the authoritative physical files.

    If inspection metadata is incomplete, raw files are added
    automatically.
    """

    inspection_records = flatten_inspection_records(
        inspection
    )

    resolved_records: List[
        Dict[str, Any]
    ] = []

    # --------------------------------------------------------
    # Inspection-driven resolution
    # --------------------------------------------------------

    for record in inspection_records:

        path = resolve_asset_path(
            record,
            raw_files,
        )

        if path is None:
            continue

        normalized = dict(record)

        normalized["_resolved_path"] = str(
            path
        )

        resolved_records.append(
            normalized
        )

    # --------------------------------------------------------
    # Add files not represented in inspection metadata
    # --------------------------------------------------------

    represented = {
        Path(
            record["_resolved_path"]
        ).resolve()
        for record in resolved_records
    }

    for path in raw_files:

        resolved_path = path.resolve()

        if resolved_path in represented:
            continue

        resolved_records.append(
            {
                "id": safe_filename(
                    path.stem
                ),
                "name": path.name,
                "_resolved_path": str(
                    resolved_path
                ),
                "inspection_fallback": True,
            }
        )

    # --------------------------------------------------------
    # Stable ordering
    # --------------------------------------------------------

    resolved_records.sort(
        key=lambda record: str(
            record["_resolved_path"]
        ).lower()
    )

    return resolved_records


# ============================================================
# CSV HELPERS
# ============================================================

def read_text_with_encoding(
    path: Path,
) -> Tuple[str, str]:
    """
    Try common encodings used by downloaded datasets.
    """

    last_error: Optional[
        UnicodeDecodeError
    ] = None

    for encoding in TEXT_ENCODINGS:

        try:

            with path.open(
                "r",
                encoding=encoding,
                errors="strict",
            ) as file:

                return (
                    file.read(),
                    encoding,
                )

        except UnicodeDecodeError as exc:
            last_error = exc

    raise UnicodeDecodeError(
        "unknown",
        b"",
        0,
        1,
        (
            f"Could not decode {path}: "
            f"{last_error}"
        ),
    )


def detect_delimiter(
    lines: List[str],
) -> str:
    """
    Detect CSV delimiter from the file contents.

    WHO exports may contain metadata before the actual table.
    """

    sample = "\n".join(
        lines[:100]
    )

    try:

        dialect = csv.Sniffer().sniff(
            sample,
            delimiters=",;\t|",
        )

        return dialect.delimiter

    except csv.Error:

        candidates = {
            ",": 0,
            ";": 0,
            "\t": 0,
            "|": 0,
        }

        for line_text in lines[:50]:

            for delimiter in candidates:

                candidates[delimiter] += (
                    line_text.count(
                        delimiter
                    )
                )

        return max(
            candidates,
            key=candidates.get,
        )


def score_header(
    row: List[str],
) -> float:
    """
    Score how likely a row is a real table header.
    """

    if not row:
        return 0.0

    values = [
        str(value).strip()
        for value in row
    ]

    nonempty = [
        value
        for value in values
        if value
    ]

    if len(nonempty) < 2:
        return 0.0

    score = 0.0

    # --------------------------------------------------------
    # Column count
    # --------------------------------------------------------

    if len(nonempty) >= 3:
        score += 2

    if len(nonempty) >= 5:
        score += 2

    # --------------------------------------------------------
    # Header vocabulary
    # --------------------------------------------------------

    header_words = (
        "country",
        "year",
        "pathogen",
        "antibiotic",
        "resistance",
        "infection",
        "specimen",
        "indicator",
        "value",
        "rate",
        "percentage",
        "percent",
        "blood",
        "bacteria",
        "drug",
        "organism",
        "age",
        "sex",
        "origin",
        "region",
        "testing",
        "coverage",
        "isolates",
    )

    joined = " ".join(
        value.lower()
        for value in nonempty
    )

    for word in header_words:

        if word in joined:
            score += 1

    # --------------------------------------------------------
    # Headers should not be huge prose blocks
    # --------------------------------------------------------

    average_length = (
        sum(
            len(value)
            for value in nonempty
        )
        / len(nonempty)
    )

    if average_length < 80:
        score += 1

    # --------------------------------------------------------
    # Penalize rows that look like ordinary prose
    # --------------------------------------------------------

    if len(nonempty) == 1:
        score -= 5

    return score


def detect_header_row(
    rows: List[List[str]],
) -> Optional[int]:
    """
    Search the first 100 rows for the most likely header.
    """

    best_index: Optional[int] = None
    best_score = 0.0

    for index, row in enumerate(
        rows[:100]
    ):

        score = score_header(
            row
        )

        if score > best_score:

            best_score = score
            best_index = index

    return best_index


# ============================================================
# CSV EXTRACTION
# ============================================================

def extract_csv(
    source: Path,
    output_dir: Path,
) -> Dict[str, Any]:

    text, encoding = (
        read_text_with_encoding(
            source
        )
    )

    lines = text.splitlines()

    # --------------------------------------------------------
    # Empty file handling
    # --------------------------------------------------------

    if not lines:

        output_name = (
            safe_filename(
                source.stem
            )
            + ".json"
        )

        output_path = (
            output_dir / output_name
        )

        result = {
            "asset_type": "csv",
            "source_file": repo_relative(
                source
            ),
            "encoding": encoding,
            "delimiter": None,
            "header_row": None,
            "columns": [],
            "row_count": 0,
            "records": [],
            "empty_source": True,
            "extracted_at": utc_now(),
        }

        write_json(
            output_path,
            result,
        )

        return {
            "output": output_path,
            "format": "csv",
            "header_row": None,
            "rows": 0,
            "columns": 0,
            "encoding": encoding,
            "delimiter": None,
            "empty_source": True,
        }

    # --------------------------------------------------------
    # Delimiter
    # --------------------------------------------------------

    delimiter = detect_delimiter(
        lines
    )

    # --------------------------------------------------------
    # Parse rows
    # --------------------------------------------------------

    reader = csv.reader(
        lines,
        delimiter=delimiter,
    )

    rows = list(reader)

    if not rows:

        raise ValueError(
            "CSV contained no readable rows."
        )

    # --------------------------------------------------------
    # Detect header
    # --------------------------------------------------------

    header_index = detect_header_row(
        rows
    )

    if header_index is None:

        # A completely empty / metadata-only CSV
        # should still produce an extraction artifact.

        nonempty_rows = [
            row
            for row in rows
            if any(
                str(cell).strip()
                for cell in row
            )
        ]

        if not nonempty_rows:

            output_name = (
                safe_filename(
                    source.stem
                )
                + ".json"
            )

            output_path = (
                output_dir / output_name
            )

            result = {
                "asset_type": "csv",
                "source_file": repo_relative(
                    source
                ),
                "encoding": encoding,
                "delimiter": delimiter,
                "header_row": None,
                "columns": [],
                "row_count": 0,
                "records": [],
                "empty_source": True,
                "extracted_at": utc_now(),
            }

            write_json(
                output_path,
                result,
            )

            return {
                "output": output_path,
                "format": "csv",
                "header_row": None,
                "rows": 0,
                "columns": 0,
                "encoding": encoding,
                "delimiter": delimiter,
                "empty_source": True,
            }

        raise ValueError(
            "Could not identify a table header."
        )

    # --------------------------------------------------------
    # Header
    # --------------------------------------------------------

    header = [
        str(value).strip()
        for value in rows[
            header_index
        ]
    ]

    # --------------------------------------------------------
    # Data rows
    # --------------------------------------------------------

    data_rows = rows[
        header_index + 1:
    ]

    data_rows = [
        row
        for row in data_rows
        if any(
            str(cell).strip()
            for cell in row
        )
    ]

    # --------------------------------------------------------
    # Normalize row widths
    # --------------------------------------------------------

    width = len(header)

    normalized_rows = []

    for row in data_rows:

        if len(row) < width:

            row = row + (
                [""] * (
                    width - len(row)
                )
            )

        elif len(row) > width:

            row = row[:width]

        normalized_rows.append(
            row
        )

    # --------------------------------------------------------
    # Build records
    # --------------------------------------------------------

    records = []

    for row in normalized_rows:

        record: Dict[str, Any] = {}

        for index in range(width):

            column_name = (
                header[index]
                or f"column_{index + 1}"
            )

            record[
                column_name
            ] = row[index]

        records.append(
            record
        )

    # --------------------------------------------------------
    # Output
    # --------------------------------------------------------

    output_name = (
        safe_filename(
            source.stem
        )
        + ".json"
    )

    output_path = (
        output_dir / output_name
    )

    result = {
        "asset_type": "csv",
        "source_file": repo_relative(
            source
        ),
        "source_size_bytes": source.stat().st_size,
        "encoding": encoding,
        "delimiter": delimiter,
        "header_row": header_index,
        "columns": header,
        "row_count": len(records),
        "records": records,
        "extracted_at": utc_now(),
    }

    write_json(
        output_path,
        result,
    )

    return {
        "output": output_path,
        "format": "csv",
        "header_row": header_index,
        "rows": len(records),
        "columns": len(header),
        "encoding": encoding,
        "delimiter": delimiter,
    }


# ============================================================
# PDF EXTRACTION
# ============================================================

def extract_pdf(
    source: Path,
    output_dir: Path,
) -> Dict[str, Any]:

    try:

        from pypdf import PdfReader

    except ImportError as exc:

        raise RuntimeError(
            "pypdf is required for PDF extraction. "
            "Install with: "
            "python -m pip install pypdf"
        ) from exc

    reader = PdfReader(
        str(source)
    )

    pages = []
    total_characters = 0

    for page_number, page in enumerate(
        reader.pages,
        start=1,
    ):

        try:

            text = (
                page.extract_text()
                or ""
            )

        except Exception as exc:

            warning(
                f"page {page_number}: "
                f"text extraction warning: "
                f"{exc}"
            )

            text = ""

        total_characters += len(
            text
        )

        pages.append(
            {
                "page": page_number,
                "text": text,
                "characters": len(text),
            }
        )

    output_name = (
        safe_filename(
            source.stem
        )
        + ".json"
    )

    output_path = (
        output_dir / output_name
    )

    result = {
        "asset_type": "pdf",
        "source_file": repo_relative(
            source
        ),
        "source_size_bytes": source.stat().st_size,
        "page_count": len(
            reader.pages
        ),
        "character_count": total_characters,
        "pages": pages,
        "extracted_at": utc_now(),
    }

    write_json(
        output_path,
        result,
    )

    return {
        "output": output_path,
        "format": "pdf",
        "pages": len(
            reader.pages
        ),
        "characters": total_characters,
    }


# ============================================================
# OWL / RDF EXTRACTION
# ============================================================

def extract_owl(
    source: Path,
    output_dir: Path,
) -> Dict[str, Any]:

    try:

        from rdflib import Graph

    except ImportError as exc:

        raise RuntimeError(
            "rdflib is required for OWL extraction. "
            "Install with: "
            "python -m pip install rdflib"
        ) from exc

    graph = Graph()

    graph.parse(
        str(source)
    )

    triples = []

    for subject, predicate, obj in graph:

        triples.append(
            {
                "subject": str(
                    subject
                ),
                "predicate": str(
                    predicate
                ),
                "object": str(
                    obj
                ),
            }
        )

    output_name = (
        safe_filename(
            source.stem
        )
        + ".json"
    )

    output_path = (
        output_dir / output_name
    )

    namespaces = {
        str(prefix): str(namespace)
        for prefix, namespace
        in graph.namespaces()
    }

    result = {
        "asset_type": "owl",
        "source_file": repo_relative(
            source
        ),
        "source_size_bytes": source.stat().st_size,
        "triple_count": len(
            triples
        ),
        "namespaces": namespaces,
        "triples": triples,
        "extracted_at": utc_now(),
    }

    write_json(
        output_path,
        result,
    )

    return {
        "output": output_path,
        "format": "owl",
        "triples": len(
            triples
        ),
    }


# ============================================================
# DISPATCH
# ============================================================

def extract_asset(
    source: Path,
    output_dir: Path,
) -> Dict[str, Any]:

    suffix = source.suffix.lower()

    if suffix == ".csv":

        return extract_csv(
            source,
            output_dir,
        )

    if suffix == ".pdf":

        return extract_pdf(
            source,
            output_dir,
        )

    if suffix in {
        ".owl",
        ".rdf",
        ".xml",
    }:

        return extract_owl(
            source,
            output_dir,
        )

    raise ValueError(
        f"Unsupported file type: {suffix}"
    )


# ============================================================
# MANIFEST
# ============================================================

def build_manifest(
    records: List[Dict[str, Any]],
) -> Dict[str, Any]:

    return {
        "manifest_version": "2.1",
        "pipeline": (
            "amr_knowledge_asset_extraction"
        ),
        "generated_at": utc_now(),
        "raw_directory": repo_relative(
            RAW_DIR
        ),
        "output_directory": repo_relative(
            EXTRACTED_DIR
        ),
        "assets_discovered": len(
            records
        ),
        "assets": records,
    }


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    line()

    print(
        "AMR KNOWLEDGE ASSET EXTRACTOR — V2.1"
    )

    line()

    info(
        f"Raw      : {RAW_DIR}"
    )

    info(
        f"Metadata : {INSPECTION_FILE}"
    )

    info(
        f"Output   : {EXTRACTED_DIR}"
    )

    print()

    # ========================================================
    # LOAD INSPECTION METADATA
    # ========================================================

    try:

        inspection = (
            load_inspection_manifest()
        )

    except Exception as exc:

        error(
            f"Could not load inspection metadata: "
            f"{exc}"
        )

        return 1

    # ========================================================
    # DISCOVER RAW FILES
    # ========================================================

    try:

        raw_files = collect_raw_files()

    except Exception as exc:

        error(
            f"Could not discover raw assets: "
            f"{exc}"
        )

        return 1

    print(
        f"Found {len(raw_files)} extractable file(s)."
    )

    if not raw_files:

        warning(
            "No supported assets found in knowledge/raw/"
        )

        return 0

    # ========================================================
    # RESOLVE ASSETS
    # ========================================================

    records = build_asset_records(
        inspection,
        raw_files,
    )

    print(
        f"Resolved {len(records)} asset(s)."
    )

    print()

    # ========================================================
    # PREPARE OUTPUT
    # ========================================================

    EXTRACTED_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    successful: List[
        Dict[str, Any]
    ] = []

    failed: List[
        Dict[str, Any]
    ] = []

    # ========================================================
    # EXTRACT EACH ASSET
    # ========================================================

    for index, asset in enumerate(
        records,
        start=1,
    ):

        source = Path(
            asset["_resolved_path"]
        )

        display_path = repo_relative(
            source
        )

        print(
            f"[{index:02d}/{len(records):02d}] "
            f"{display_path}"
        )

        # ----------------------------------------------------
        # Defensive source check
        # ----------------------------------------------------

        if not source.exists():

            asset_id = asset.get(
                "id",
                safe_filename(
                    source.stem
                ),
            )

            failure = {
                "asset_id": asset_id,
                "source_file": display_path,
                "status": "failed",
                "error": (
                    "Resolved source file "
                    "does not exist."
                ),
            }

            failed.append(
                failure
            )

            error(
                failure["error"]
            )

            print()

            continue

        # ----------------------------------------------------
        # Extract
        # ----------------------------------------------------

        try:

            output = extract_asset(
                source,
                EXTRACTED_DIR,
            )

            asset_id = asset.get(
                "id",
                safe_filename(
                    source.stem
                ),
            )

            result = {
                "asset_id": asset_id,
                "source_file": display_path,
                "source_sha256": sha256_file(
                    source
                ),
                "source_size_bytes": (
                    source.stat().st_size
                ),
                "status": "success",
                **output,
            }

            successful.append(
                result
            )

            # ------------------------------------------------
            # Console reporting
            # ------------------------------------------------

            if output["format"] == "csv":

                header = output.get(
                    "header_row"
                )

                rows = output.get(
                    "rows",
                    0,
                )

                columns = output.get(
                    "columns",
                    0,
                )

                if output.get(
                    "empty_source",
                    False,
                ):

                    success(
                        "extracted — "
                        "empty/metadata-only CSV"
                    )

                else:

                    success(
                        "extracted — "
                        f"header row {header}, "
                        f"{rows} rows, "
                        f"{columns} columns"
                    )

            elif output["format"] == "pdf":

                success(
                    "extracted — "
                    f"{output['pages']} pages, "
                    f"{output['characters']:,} characters"
                )

            elif output["format"] == "owl":

                success(
                    "extracted — "
                    f"{output['triples']:,} RDF triples"
                )

        except Exception as exc:

            asset_id = asset.get(
                "id",
                safe_filename(
                    source.stem
                ),
            )

            failure = {
                "asset_id": asset_id,
                "source_file": display_path,
                "status": "failed",
                "error": str(exc),
            }

            failed.append(
                failure
            )

            error(
                f"extraction failed: {exc}"
            )

        print()

    # ========================================================
    # BUILD MANIFEST
    # ========================================================

    all_results = (
        successful + failed
    )

    manifest = build_manifest(
        all_results
    )

    manifest["successful"] = len(
        successful
    )

    manifest["failed"] = len(
        failed
    )

    manifest["formats"] = {}

    for result in successful:

        fmt = result.get(
            "format",
            "unknown",
        )

        manifest["formats"][fmt] = (
            manifest["formats"].get(
                fmt,
                0,
            )
            + 1
        )

    manifest["failed_assets"] = failed

    # ========================================================
    # WRITE MANIFEST
    # ========================================================

    try:

        write_json(
            MANIFEST_FILE,
            manifest,
        )

    except Exception as exc:

        error(
            f"Could not write extraction manifest: "
            f"{exc}"
        )

        return 1

    # ========================================================
    # SUMMARY
    # ========================================================

    line()

    print(
        "EXTRACTION SUMMARY"
    )

    line()

    print(
        f"Assets discovered : {len(records)}"
    )

    print(
        f"Successfully extracted : "
        f"{len(successful)}"
    )

    print(
        f"Failed : {len(failed)}"
    )

    print()

    if manifest["formats"]:

        print("Formats:")

        for fmt, count in sorted(
            manifest["formats"].items()
        ):

            print(
                f"  {fmt.upper():<5} : {count}"
            )

        print()

    success(
        "Extraction manifest written to:"
    )

    print(
        f"  {MANIFEST_FILE}"
    )

    # ========================================================
    # FAILURE REPORT
    # ========================================================

    if failed:

        print()

        warning(
            "Some assets failed extraction."
        )

        print()

        print(
            "Failed assets:"
        )

        for item in failed:

            print(
                f"  - {item['asset_id']}: "
                f"{item['error']}"
            )

        return 1

    # ========================================================
    # SUCCESS
    # ========================================================

    print()

    success(
        "All assets extracted successfully."
    )

    return 0


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    sys.exit(main())