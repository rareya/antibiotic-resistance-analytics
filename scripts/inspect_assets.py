# ============================================================
# AMR KNOWLEDGE ASSET INSPECTOR
# ============================================================
#
# Purpose:
#   Inspect every asset currently stored under knowledge/raw/
#   and produce a structural inventory before extraction.
#
# Output:
#   knowledge/metadata/asset_inspection.json
#
# The script does NOT:
#   - modify raw files
#   - classify assets permanently
#   - chunk documents
#   - create embeddings
#   - build a vector database
#
# It only answers:
#   "What is actually inside each raw asset?"
# ============================================================

from __future__ import annotations

import csv
import hashlib
import json
import mimetypes
import re
import sys
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ============================================================
# PATHS
# ============================================================

ROOT = Path(__file__).resolve().parents[1]

RAW_DIR = ROOT / "knowledge" / "raw"
METADATA_DIR = ROOT / "knowledge" / "metadata"
OUTPUT_FILE = METADATA_DIR / "asset_inspection.json"


# ============================================================
# OPTIONAL DEPENDENCIES
# ============================================================

try:
    import pandas as pd

    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False


try:
    import fitz  # PyMuPDF

    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False


try:
    from bs4 import BeautifulSoup

    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False


# ============================================================
# CONSTANTS
# ============================================================

TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".rst",
    ".log",
    ".xml",
    ".json",
    ".yaml",
    ".yml",
    ".tsv",
}

CSV_EXTENSIONS = {
    ".csv",
}

PDF_EXTENSIONS = {
    ".pdf",
}

HTML_EXTENSIONS = {
    ".html",
    ".htm",
}

ARCHIVE_EXTENSIONS = {
    ".zip",
    ".tar",
    ".gz",
    ".bz2",
    ".tgz",
}

ONTOLOGY_EXTENSIONS = {
    ".owl",
}

SUPPORTED_INSPECTION_EXTENSIONS = (
    TEXT_EXTENSIONS
    | CSV_EXTENSIONS
    | PDF_EXTENSIONS
    | HTML_EXTENSIONS
    | ARCHIVE_EXTENSIONS
    | ONTOLOGY_EXTENSIONS
)


# ============================================================
# UTILITIES
# ============================================================

def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Calculate SHA-256 without loading the whole file into memory."""
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)

            if not chunk:
                break

            digest.update(chunk)

    return digest.hexdigest()


def safe_read_text(
    path: Path,
    max_chars: int | None = None,
) -> tuple[str | None, str | None]:
    """
    Try common encodings and return:
        (text, encoding)
    """

    encodings = [
        "utf-8",
        "utf-8-sig",
        "utf-16",
        "latin-1",
    ]

    for encoding in encodings:
        try:
            with path.open(
                "r",
                encoding=encoding,
                errors="strict",
            ) as handle:
                text = handle.read()

            if max_chars is not None:
                text = text[:max_chars]

            return text, encoding

        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError:
            break

    return None, None


def clean_text(text: str) -> str:
    """Normalize whitespace without destroying content."""
    text = text.replace("\x00", " ")

    text = re.sub(
        r"[ \t]+",
        " ",
        text,
    )

    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text,
    )

    return text.strip()


def count_words(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


def detect_mime_type(path: Path) -> str | None:
    mime, _ = mimetypes.guess_type(path.name)
    return mime


def relative_to_raw(path: Path) -> str:
    return str(path.relative_to(RAW_DIR)).replace("\\", "/")


# ============================================================
# FORMAT DETECTION
# ============================================================

def detect_format(path: Path) -> str:
    suffix = path.suffix.lower()

    if suffix == ".csv":
        return "csv"

    if suffix == ".pdf":
        return "pdf"

    if suffix in {".html", ".htm"}:
        return "html"

    if suffix == ".json":
        return "json"

    if suffix in {".yaml", ".yml"}:
        return "yaml"

    if suffix == ".owl":
        return "owl"

    if suffix == ".zip":
        return "zip"

    if suffix in {".tar", ".gz", ".bz2", ".tgz"}:
        return "archive"

    if suffix in TEXT_EXTENSIONS:
        return "text"

    return "unknown"


# ============================================================
# CSV INSPECTION
# ============================================================

def inspect_csv(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "format": "csv",
        "readable": False,
    }

    # --------------------------------------------------------
    # Preferred path: pandas
    # --------------------------------------------------------

    if HAS_PANDAS:
        try:
            df = pd.read_csv(
                path,
                low_memory=False,
            )

            result.update(
                {
                    "readable": True,
                    "reader": "pandas",
                    "rows": int(df.shape[0]),
                    "columns_count": int(df.shape[1]),
                    "columns": [
                        str(column)
                        for column in df.columns
                    ],
                    "dtypes": {
                        str(column): str(dtype)
                        for column, dtype in df.dtypes.items()
                    },
                    "missing_values": {
                        str(column): int(value)
                        for column, value in df.isna().sum().items()
                    },
                    "duplicate_rows": int(
                        df.duplicated().sum()
                    ),
                }
            )

            # ------------------------------------------------
            # Basic sample
            # ------------------------------------------------

            sample = df.head(5)

            result["sample"] = json.loads(
                sample.to_json(
                    orient="records",
                    date_format="iso",
                )
            )

            # ------------------------------------------------
            # Unique counts
            # ------------------------------------------------

            unique_counts: dict[str, int] = {}

            for column in df.columns:
                try:
                    unique_counts[str(column)] = int(
                        df[column].nunique(
                            dropna=True
                        )
                    )
                except Exception:
                    pass

            result["unique_values"] = unique_counts

            # ------------------------------------------------
            # Numeric summary
            # ------------------------------------------------

            numeric_columns = df.select_dtypes(
                include="number"
            ).columns.tolist()

            if numeric_columns:
                numeric_summary = {}

                for column in numeric_columns:
                    series = df[column]

                    numeric_summary[str(column)] = {
                        "min": safe_json_value(
                            series.min()
                        ),
                        "max": safe_json_value(
                            series.max()
                        ),
                        "mean": safe_json_value(
                            series.mean()
                        ),
                    }

                result["numeric_summary"] = numeric_summary

            return result

        except Exception as exc:
            result["error"] = str(exc)

    # --------------------------------------------------------
    # Fallback: standard library CSV reader
    # --------------------------------------------------------

    try:
        with path.open(
            "r",
            encoding="utf-8-sig",
            newline="",
        ) as handle:

            sample_text = handle.read(10000)
            handle.seek(0)

            try:
                dialect = csv.Sniffer().sniff(
                    sample_text
                )
            except csv.Error:
                dialect = csv.excel

            reader = csv.reader(
                handle,
                dialect,
            )

            rows = list(reader)

        if not rows:
            result["readable"] = True
            result["rows"] = 0
            result["columns_count"] = 0
            result["columns"] = []
            result["reader"] = "csv"

            return result

        headers = rows[0]
        data_rows = rows[1:]

        result.update(
            {
                "readable": True,
                "reader": "python_csv",
                "rows": len(data_rows),
                "columns_count": len(headers),
                "columns": headers,
                "sample": [
                    dict(
                        zip(
                            headers,
                            row,
                        )
                    )
                    for row in data_rows[:5]
                ],
            }
        )

        return result

    except Exception as exc:
        result["error"] = str(exc)

        return result


# ============================================================
# PDF INSPECTION
# ============================================================

def inspect_pdf(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "format": "pdf",
        "readable": False,
    }

    if not HAS_PYMUPDF:
        result["error"] = (
            "PyMuPDF not installed. "
            "Install with: pip install pymupdf"
        )

        return result

    try:
        document = fitz.open(path)

        page_count = len(document)

        page_text_lengths = []
        total_text = []

        for page_number, page in enumerate(document):
            text = page.get_text("text") or ""

            text = clean_text(text)

            page_text_lengths.append(
                {
                    "page": page_number + 1,
                    "characters": len(text),
                    "words": count_words(text),
                }
            )

            if text:
                total_text.append(text)

        full_text = "\n\n".join(total_text)

        metadata = document.metadata or {}

        result.update(
            {
                "readable": True,
                "reader": "pymupdf",
                "pages": page_count,
                "text_characters": len(full_text),
                "text_words": count_words(full_text),
                "text_available": bool(full_text.strip()),
                "page_text_lengths": page_text_lengths,
                "metadata": {
                    key: value
                    for key, value in metadata.items()
                    if value
                },
            }
        )

        # ----------------------------------------------------
        # First-page preview
        # ----------------------------------------------------

        if page_count > 0:
            first_page = clean_text(
                document[0].get_text("text") or ""
            )

            result["first_page_preview"] = first_page[:3000]

        # ----------------------------------------------------
        # Basic heading detection
        # ----------------------------------------------------

        headings = detect_possible_headings(
            full_text
        )

        result["possible_headings"] = headings[:100]

        # ----------------------------------------------------
        # Table detection
        # ----------------------------------------------------

        table_count = 0

        for page in document:
            try:
                tables = page.find_tables()

                if tables:
                    table_count += len(
                        tables.tables
                    )

            except Exception:
                pass

        result["detected_tables"] = table_count

        document.close()

        return result

    except Exception as exc:
        result["error"] = str(exc)

        return result


# ============================================================
# HTML INSPECTION
# ============================================================

def inspect_html(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "format": "html",
        "readable": False,
    }

    text, encoding = safe_read_text(path)

    if text is None:
        result["error"] = "Unable to decode HTML."

        return result

    result["readable"] = True
    result["encoding"] = encoding
    result["html_characters"] = len(text)

    if HAS_BS4:
        try:
            soup = BeautifulSoup(
                text,
                "html.parser",
            )

            title = (
                soup.title.get_text(
                    " ",
                    strip=True,
                )
                if soup.title
                else None
            )

            headings = []

            for tag in soup.find_all(
                ["h1", "h2", "h3", "h4"]
            ):
                heading = tag.get_text(
                    " ",
                    strip=True,
                )

                if heading:
                    headings.append(
                        heading
                    )

            page_text = soup.get_text(
                "\n",
                strip=True,
            )

            result.update(
                {
                    "reader": "beautifulsoup4",
                    "title": title,
                    "text_characters": len(page_text),
                    "text_words": count_words(
                        page_text
                    ),
                    "headings": headings[:100],
                    "links": len(
                        soup.find_all("a")
                    ),
                    "tables": len(
                        soup.find_all("table")
                    ),
                    "text_available": bool(
                        page_text.strip()
                    ),
                }
            )

        except Exception as exc:
            result["error"] = str(exc)

    else:
        result["reader"] = "raw_text"
        result["text_characters"] = len(text)
        result["text_words"] = count_words(text)

    return result


# ============================================================
# TEXT / JSON / YAML / OWL
# ============================================================

def inspect_text(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "format": detect_format(path),
        "readable": False,
    }

    text, encoding = safe_read_text(path)

    if text is None:
        result["error"] = "Unable to decode file."

        return result

    cleaned = clean_text(text)

    result.update(
        {
            "readable": True,
            "encoding": encoding,
            "text_characters": len(cleaned),
            "text_words": count_words(cleaned),
            "text_available": bool(cleaned),
            "preview": cleaned[:3000],
        }
    )

    # --------------------------------------------------------
    # JSON
    # --------------------------------------------------------

    if path.suffix.lower() == ".json":
        try:
            data = json.loads(text)

            result["json_valid"] = True
            result["json_type"] = type(data).__name__

            if isinstance(data, dict):
                result["json_keys"] = list(
                    data.keys()
                )[:100]

            elif isinstance(data, list):
                result["json_items"] = len(data)

        except Exception as exc:
            result["json_valid"] = False
            result["json_error"] = str(exc)

    # --------------------------------------------------------
    # OWL
    # --------------------------------------------------------

    if path.suffix.lower() == ".owl":
        result["ontology_signals"] = {
            "classes": count_xml_like(
                text,
                r"<owl:Class\b"
            ),
            "object_properties": count_xml_like(
                text,
                r"<owl:ObjectProperty\b"
            ),
            "datatype_properties": count_xml_like(
                text,
                r"<owl:DatatypeProperty\b"
            ),
            "individuals": count_xml_like(
                text,
                r"<owl:NamedIndividual\b"
            ),
        }

    return result


# ============================================================
# ARCHIVE INSPECTION
# ============================================================

def inspect_zip(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "format": "zip",
        "readable": False,
    }

    try:
        with zipfile.ZipFile(path, "r") as archive:

            members = archive.infolist()

            files = [
                member
                for member in members
                if not member.is_dir()
            ]

            result.update(
                {
                    "readable": True,
                    "members": len(members),
                    "files": len(files),
                    "directories": len(members) - len(files),
                    "uncompressed_bytes": sum(
                        member.file_size
                        for member in files
                    ),
                    "compressed_bytes": sum(
                        member.compress_size
                        for member in files
                    ),
                    "contents": [
                        {
                            "name": member.filename,
                            "size_bytes": member.file_size,
                            "compressed_bytes": member.compress_size,
                        }
                        for member in files[:200]
                    ],
                }
            )

    except Exception as exc:
        result["error"] = str(exc)

    return result


# ============================================================
# HEADING DETECTION
# ============================================================

def detect_possible_headings(
    text: str,
) -> list[str]:

    headings = []

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if not line:
            continue

        if len(line) > 180:
            continue

        # Numbered headings:
        #
        # 1 Introduction
        # 2.1 Surveillance
        # 3.2.1 Resistance
        #

        if re.match(
            r"^\d+(\.\d+)*[\s.)]+[A-Z]",
            line,
        ):
            headings.append(line)
            continue

        # Common uppercase headings

        if (
            len(line) >= 4
            and len(line) <= 120
            and line.upper() == line
            and re.search(r"[A-Z]", line)
        ):
            headings.append(line)

    # Remove duplicates while preserving order

    seen = set()
    unique = []

    for heading in headings:
        key = heading.lower()

        if key in seen:
            continue

        seen.add(key)
        unique.append(heading)

    return unique


# ============================================================
# SMALL HELPERS
# ============================================================

def count_xml_like(
    text: str,
    pattern: str,
) -> int:

    return len(
        re.findall(
            pattern,
            text,
            flags=re.IGNORECASE,
        )
    )


def safe_json_value(value: Any) -> Any:
    """Convert numpy/scalar values into JSON-safe values."""

    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    try:
        return value.item()
    except Exception:
        pass

    if isinstance(
        value,
        (str, int, float, bool),
    ):
        return value

    return str(value)


# ============================================================
# SINGLE ASSET INSPECTION
# ============================================================

def inspect_asset(path: Path) -> dict[str, Any]:

    file_size = path.stat().st_size

    file_format = detect_format(path)

    record: dict[str, Any] = {
        "asset_file": relative_to_raw(path),
        "filename": path.name,
        "extension": path.suffix.lower(),
        "format": file_format,
        "mime_type": detect_mime_type(path),
        "size_bytes": file_size,
        "sha256": sha256_file(path),
        "modified_time": datetime.fromtimestamp(
            path.stat().st_mtime,
            tz=timezone.utc,
        ).isoformat(),
    }

    # --------------------------------------------------------
    # Format-specific inspection
    # --------------------------------------------------------

    if file_format == "csv":
        details = inspect_csv(path)

    elif file_format == "pdf":
        details = inspect_pdf(path)

    elif file_format == "html":
        details = inspect_html(path)

    elif file_format == "zip":
        details = inspect_zip(path)

    elif file_format in {
        "text",
        "json",
        "yaml",
        "owl",
    }:
        details = inspect_text(path)

    else:
        details = {
            "format": file_format,
            "readable": False,
            "inspection": "unsupported_format",
        }

    record["inspection"] = details

    return record


# ============================================================
# SUMMARY
# ============================================================

def build_summary(
    assets: list[dict[str, Any]],
) -> dict[str, Any]:

    format_counts = Counter(
        asset["format"]
        for asset in assets
    )

    readable_counts = Counter()

    for asset in assets:
        readable = asset.get(
            "inspection",
            {},
        ).get(
            "readable"
        )

        readable_counts[
            "readable" if readable else "not_readable"
        ] += 1

    total_size = sum(
        asset["size_bytes"]
        for asset in assets
    )

    return {
        "asset_count": len(assets),
        "total_size_bytes": total_size,
        "formats": dict(format_counts),
        "readability": dict(readable_counts),
    }


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    print("=" * 70)
    print("AMR KNOWLEDGE ASSET INSPECTOR")
    print("=" * 70)

    print()
    print(f"Raw      : {RAW_DIR}")
    print(f"Output   : {OUTPUT_FILE}")
    print()

    if not RAW_DIR.exists():
        print(
            f"ERROR: raw directory does not exist:\n"
            f"{RAW_DIR}"
        )

        return 1

    METADATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    # --------------------------------------------------------
    # Discover files
    # --------------------------------------------------------

    files = sorted(
        path
        for path in RAW_DIR.rglob("*")
        if path.is_file()
    )

    print(
        f"Found {len(files)} file(s) in knowledge/raw/"
    )

    print()

    assets = []

    # --------------------------------------------------------
    # Inspect
    # --------------------------------------------------------

    for index, path in enumerate(
        files,
        start=1,
    ):

        relative_path = relative_to_raw(path)

        print(
            f"[{index:02d}/{len(files):02d}] "
            f"{relative_path}"
        )

        try:
            record = inspect_asset(path)

            assets.append(record)

            inspection = record["inspection"]

            if inspection.get("readable"):
                print(
                    "      ✓ inspected"
                )
            else:
                print(
                    "      ⚠ limited inspection"
                )

        except Exception as exc:

            print(
                f"      ✗ inspection failed: {exc}"
            )

            assets.append(
                {
                    "asset_file": relative_path,
                    "filename": path.name,
                    "extension": path.suffix.lower(),
                    "format": detect_format(path),
                    "size_bytes": path.stat().st_size,
                    "inspection_error": str(exc),
                }
            )

    # --------------------------------------------------------
    # Build report
    # --------------------------------------------------------

    report = {
        "metadata": {
            "name": "amr_asset_inspection",
            "version": "1.0",
            "generated_at": utc_now(),
            "raw_directory": str(RAW_DIR),
        },
        "summary": build_summary(
            assets
        ),
        "assets": assets,
    }

    # --------------------------------------------------------
    # Write JSON
    # --------------------------------------------------------

    with OUTPUT_FILE.open(
        "w",
        encoding="utf-8",
    ) as handle:

        json.dump(
            report,
            handle,
            indent=2,
            ensure_ascii=False,
        )

    # --------------------------------------------------------
    # Final output
    # --------------------------------------------------------

    summary = report["summary"]

    print()
    print("=" * 70)
    print("INSPECTION SUMMARY")
    print("=" * 70)

    print(
        f"Assets discovered : "
        f"{summary['asset_count']}"
    )

    print(
        f"Total size        : "
        f"{summary['total_size_bytes']:,} bytes"
    )

    print()

    print("Formats:")

    for fmt, count in sorted(
        summary["formats"].items()
    ):
        print(
            f"  {fmt:<12} {count}"
        )

    print()

    print("Readability:")

    for status, count in sorted(
        summary["readability"].items()
    ):
        print(
            f"  {status:<15} {count}"
        )

    print()
    print(
        f"✓ Inspection report written to:"
    )
    print(
        f"  {OUTPUT_FILE}"
    )

    print()
    print(
        "Next stage: extract_assets.py"
    )

    return 0


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    sys.exit(main())