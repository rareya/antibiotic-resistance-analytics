# ============================================================
# AMR KNOWLEDGE ASSET CHUNKER — V1
#
# Purpose:
#   Convert validated extraction outputs into retrieval-ready
#   knowledge units for the AMR tier-aware RAG system.
#
# Supported:
#   - PDF extraction JSON -> semantic text chunks
#   - OWL extraction JSON -> ontology units
#   - CSV extraction JSON -> structured-data registry
#
# IMPORTANT:
#   - CSV data is NOT embedded/chunked.
#   - OWL is NOT treated as ordinary prose.
#   - No embeddings are generated here.
#   - Raw and extracted assets are never modified.
#
# Output:
#
# knowledge/chunks/
# ├── documents/
# │   ├── <asset>.json
# │   └── ...
# ├── ontology/
# │   └── <asset>.json
# ├── structured/
# │   └── structured_asset_manifest.json
# └── chunk_manifest.json
#
# ============================================================

from __future__ import annotations

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

KNOWLEDGE_DIR = PROJECT_ROOT / "knowledge"
EXTRACTED_DIR = KNOWLEDGE_DIR / "extracted"
CHUNKS_DIR = KNOWLEDGE_DIR / "chunks"

DOCUMENTS_DIR = CHUNKS_DIR / "documents"
ONTOLOGY_DIR = CHUNKS_DIR / "ontology"
STRUCTURED_DIR = CHUNKS_DIR / "structured"

EXTRACTION_MANIFEST = EXTRACTED_DIR / "extraction_manifest.json"
CHUNK_MANIFEST = CHUNKS_DIR / "chunk_manifest.json"


# ============================================================
# CONFIGURATION
# ============================================================

CHUNKER_VERSION = "1.0"

# Target chunk size.
# This is character-based intentionally; tokenization belongs
# to the embedding stage.
TARGET_CHUNK_CHARS = 1400

# Maximum acceptable chunk size before a paragraph is split.
MAX_CHUNK_CHARS = 1800

# Character overlap between adjacent semantic chunks.
CHUNK_OVERLAP_CHARS = 220

# Very short fragments are usually headers/noise.
MIN_FRAGMENT_CHARS = 40

# Number of surrounding pages retained as metadata context.
# The actual chunk text remains page-aware.
MAX_PAGE_CONTEXT = 1


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


def json_safe(value: Any) -> Any:
    """
    Convert non-JSON-native values into JSON-safe values.
    """

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, dict):
        return {
            str(k): json_safe(v)
            for k, v in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [
            json_safe(v)
            for v in value
        ]

    return value


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            json_safe(data),
            f,
            indent=2,
            ensure_ascii=False,
        )

        f.write("\n")


def read_json(path: Path) -> Any:
    with path.open(
        "r",
        encoding="utf-8",
    ) as f:
        return json.load(f)


def sha256_text(text: str) -> str:
    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


def make_chunk_id(
    asset_id: str,
    content: str,
    locator: str,
) -> str:

    raw = (
        f"{asset_id}|"
        f"{locator}|"
        f"{content}"
    )

    digest = sha256_text(raw)

    return f"chunk_{digest[:20]}"


# ============================================================
# TEXT NORMALIZATION
# ============================================================

def normalize_text(text: str) -> str:
    """
    Clean PDF extraction noise without aggressively changing
    scientific terminology.
    """

    if not text:
        return ""

    text = text.replace(
        "\u00a0",
        " ",
    )

    text = text.replace(
        "\r\n",
        "\n",
    )

    text = text.replace(
        "\r",
        "\n",
    )

    # Remove trailing whitespace.
    text = re.sub(
        r"[ \t]+$",
        "",
        text,
        flags=re.MULTILINE,
    )

    # Collapse excessive blank lines.
    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text,
    )

    # Collapse repeated spaces but preserve newlines.
    text = re.sub(
        r"[ \t]{2,}",
        " ",
        text,
    )

    return text.strip()


def split_into_paragraphs(text: str) -> List[str]:
    """
    First-level semantic segmentation.

    PDF extraction commonly represents paragraphs as either
    blank-line separated blocks or line-based blocks.
    """

    text = normalize_text(text)

    if not text:
        return []

    blocks = re.split(
        r"\n\s*\n",
        text,
    )

    paragraphs: List[str] = []

    for block in blocks:

        block = normalize_text(block)

        if not block:
            continue

        # If extraction produced many short lines, combine them.
        lines = [
            line.strip()
            for line in block.split("\n")
            if line.strip()
        ]

        if not lines:
            continue

        combined = " ".join(lines)

        combined = re.sub(
            r"\s+",
            " ",
            combined,
        ).strip()

        if combined:
            paragraphs.append(combined)

    return paragraphs


# ============================================================
# HEADER / FOOTER DETECTION
# ============================================================

def looks_like_page_number(text: str) -> bool:
    value = text.strip()

    if not value:
        return True

    return bool(
        re.fullmatch(
            r"(page\s*)?\d+(\s*of\s*\d+)?",
            value,
            flags=re.IGNORECASE,
        )
    )


def detect_repeated_page_lines(
    pages: List[Dict[str, Any]],
) -> set[str]:
    """
    Identify lines repeated across many PDF pages.

    These are often headers, footers, document titles, or
    page-number artifacts.
    """

    counts: Dict[str, int] = {}
    page_count = len(pages)

    for page in pages:

        text = str(
            page.get("text", "")
        )

        lines = [
            normalize_text(line)
            for line in text.splitlines()
        ]

        seen_on_page = set()

        for value in lines:

            value = value.strip()

            if not value:
                continue

            if len(value) > 160:
                continue

            if looks_like_page_number(value):
                continue

            if value in seen_on_page:
                continue

            seen_on_page.add(value)

            counts[value] = (
                counts.get(value, 0) + 1
            )

    threshold = max(
        3,
        int(page_count * 0.30),
    )

    return {
        value
        for value, count in counts.items()
        if count >= threshold
    }


def remove_repeated_page_lines(
    text: str,
    repeated_lines: set[str],
) -> str:

    if not repeated_lines:
        return text

    output = []

    for line_value in text.splitlines():

        normalized = normalize_text(
            line_value
        )

        if normalized in repeated_lines:
            continue

        if looks_like_page_number(normalized):
            continue

        output.append(line_value)

    return "\n".join(output)


# ============================================================
# SECTION DETECTION
# ============================================================

def looks_like_heading(text: str) -> bool:
    """
    Conservative heading detector.

    We do not attempt full PDF layout reconstruction.
    """

    value = normalize_text(text)

    if not value:
        return False

    if len(value) > 120:
        return False

    if value.endswith("."):
        return False

    words = value.split()

    if len(words) > 14:
        return False

    heading_patterns = [
        r"^\d+(\.\d+)*\s+.+",
        r"^[A-Z][A-Z\s\-:&/]{4,}$",
        r"^(Introduction|Methods|Materials|Results|Discussion|"
        r"Conclusion|References|Background|Objectives?|"
        r"Recommendations?|Appendix|Definitions?)$",
    ]

    for pattern in heading_patterns:

        if re.match(
            pattern,
            value,
            flags=re.IGNORECASE,
        ):
            return True

    return False


def assign_sections(
    pages: List[Dict[str, Any]],
    repeated_lines: set[str],
) -> List[Dict[str, Any]]:
    """
    Convert page text into page-level semantic blocks while
    tracking the current section.
    """

    blocks: List[Dict[str, Any]] = []

    current_section = "Document"

    for page in pages:

        page_number = int(
            page.get("page", 0)
        )

        raw_text = str(
            page.get("text", "")
        )

        cleaned = remove_repeated_page_lines(
            raw_text,
            repeated_lines,
        )

        cleaned = normalize_text(cleaned)

        if not cleaned:
            continue

        paragraphs = split_into_paragraphs(
            cleaned
        )

        for paragraph in paragraphs:

            if looks_like_heading(paragraph):

                current_section = paragraph

                blocks.append(
                    {
                        "page": page_number,
                        "section": current_section,
                        "text": paragraph,
                        "is_heading": True,
                    }
                )

                continue

            blocks.append(
                {
                    "page": page_number,
                    "section": current_section,
                    "text": paragraph,
                    "is_heading": False,
                }
            )

    return blocks


# ============================================================
# TEXT SPLITTING
# ============================================================

def split_long_text(
    text: str,
    max_chars: int,
) -> List[str]:
    """
    Split oversized paragraphs while attempting to preserve
    sentence boundaries.
    """

    text = normalize_text(text)

    if len(text) <= max_chars:
        return [text]

    sentences = re.split(
        r"(?<=[.!?])\s+",
        text,
    )

    pieces: List[str] = []
    current = ""

    for sentence in sentences:

        sentence = sentence.strip()

        if not sentence:
            continue

        if not current:
            current = sentence
            continue

        candidate = (
            current
            + " "
            + sentence
        )

        if len(candidate) <= max_chars:
            current = candidate
        else:

            pieces.append(
                current.strip()
            )

            current = sentence

    if current:
        pieces.append(
            current.strip()
        )

    # If a sentence itself is too long, hard split it.
    final_pieces: List[str] = []

    for piece in pieces:

        if len(piece) <= max_chars:
            final_pieces.append(piece)
            continue

        start = 0

        while start < len(piece):

            end = min(
                start + max_chars,
                len(piece),
            )

            final_pieces.append(
                piece[start:end].strip()
            )

            start = end

    return [
        piece
        for piece in final_pieces
        if piece
    ]


def add_overlap(
    previous: str,
    current: str,
    overlap_chars: int,
) -> str:

    if not previous:
        return current

    if overlap_chars <= 0:
        return current

    tail = previous[
        -overlap_chars:
    ]

    # Try to start at a word boundary.
    match = re.search(
        r"\b",
        tail,
    )

    if match:
        tail = tail[
            match.start():
        ]

    combined = (
        tail.strip()
        + " "
        + current.strip()
    )

    return combined.strip()


# ============================================================
# PDF CHUNKING
# ============================================================

def build_pdf_chunks(
    asset: Dict[str, Any],
    extracted: Dict[str, Any],
) -> List[Dict[str, Any]]:

    asset_id = str(
        asset.get("asset_id")
        or asset.get("id")
        or Path(
            asset["source_file"]
        ).stem
    )

    pages = extracted.get(
        "pages",
        [],
    )

    repeated_lines = detect_repeated_page_lines(
        pages
    )

    blocks = assign_sections(
        pages,
        repeated_lines,
    )

    chunks: List[Dict[str, Any]] = []

    current_text = ""
    current_section = "Document"
    current_start_page: Optional[int] = None
    current_end_page: Optional[int] = None

    def flush() -> None:

        nonlocal current_text
        nonlocal current_section
        nonlocal current_start_page
        nonlocal current_end_page

        text = normalize_text(
            current_text
        )

        if not text:
            return

        if len(text) < MIN_FRAGMENT_CHARS:
            return

        locator = (
            f"pages:"
            f"{current_start_page}-"
            f"{current_end_page}|"
            f"section:"
            f"{current_section}"
        )

        chunk_id = make_chunk_id(
            asset_id,
            text,
            locator,
        )

        chunks.append(
            {
                "chunk_id": chunk_id,
                "chunk_type": "document",
                "content": text,

                "metadata": {
                    "asset_id": asset_id,
                    "source_id": asset_id,
                    "source_type": "pdf",
                    "source_file": asset.get(
                        "source_file"
                    ),
                    "section": current_section,
                    "page_start": current_start_page,
                    "page_end": current_end_page,
                    "version": asset.get(
                        "version"
                    ),
                },
            }
        )

        current_text = ""
        current_start_page = None
        current_end_page = None

    for block in blocks:

        text = normalize_text(
            block["text"]
        )

        if not text:
            continue

        page = int(
            block.get("page", 0)
        )

        section = str(
            block.get(
                "section",
                "Document",
            )
        )

        is_heading = bool(
            block.get(
                "is_heading",
                False,
            )
        )

        # Heading starts a new semantic region.
        if is_heading:

            flush()

            current_section = section
            current_text = ""

            continue

        if not current_text:

            current_text = text
            current_section = section
            current_start_page = page
            current_end_page = page

            continue

        candidate = (
            current_text
            + " "
            + text
        )

        # If section changes, prefer a new chunk.
        if (
            section != current_section
            and current_text
        ):

            flush()

            current_section = section
            current_text = text
            current_start_page = page
            current_end_page = page

            continue

        # Normal target-size split.
        if len(candidate) <= TARGET_CHUNK_CHARS:

            current_text = candidate
            current_end_page = page

            continue

        # Flush current chunk.
        previous_text = current_text

        flush()

        # Preserve overlap.
        overlapped = add_overlap(
            previous_text,
            text,
            CHUNK_OVERLAP_CHARS,
        )

        # Avoid exceeding maximum chunk size.
        if len(overlapped) > MAX_CHUNK_CHARS:

            pieces = split_long_text(
                overlapped,
                MAX_CHUNK_CHARS,
            )

            for piece_index, piece in enumerate(
                pieces
            ):

                locator = (
                    f"page:{page}|"
                    f"section:{section}|"
                    f"piece:{piece_index}"
                )

                chunks.append(
                    {
                        "chunk_id": make_chunk_id(
                            asset_id,
                            piece,
                            locator,
                        ),
                        "chunk_type": "document",
                        "content": piece,
                        "metadata": {
                            "asset_id": asset_id,
                            "source_id": asset_id,
                            "source_type": "pdf",
                            "source_file": asset.get(
                                "source_file"
                            ),
                            "section": section,
                            "page_start": page,
                            "page_end": page,
                            "version": asset.get(
                                "version"
                            ),
                        },
                    }
                )

            current_text = ""
            current_start_page = None
            current_end_page = None

        else:

            current_text = overlapped
            current_section = section
            current_start_page = page
            current_end_page = page

    flush()

    return chunks


def extract_pdf_chunks(
    asset: Dict[str, Any],
    extracted: Dict[str, Any],
    output_path: Path,
) -> Dict[str, Any]:

    chunks = build_pdf_chunks(
        asset,
        extracted,
    )

    result = {
        "chunker_version": CHUNKER_VERSION,
        "asset_id": asset.get(
            "asset_id"
        ),
        "source_id": asset.get(
            "asset_id"
        ),
        "source_type": "pdf",
        "source_file": asset.get(
            "source_file"
        ),
        "chunk_count": len(chunks),
        "chunking": {
            "strategy": (
                "page-aware semantic "
                "paragraph chunking"
            ),
            "target_chars": TARGET_CHUNK_CHARS,
            "max_chars": MAX_CHUNK_CHARS,
            "overlap_chars": CHUNK_OVERLAP_CHARS,
        },
        "chunks": chunks,
        "generated_at": utc_now(),
    }

    write_json(
        output_path,
        result,
    )

    return {
        "format": "pdf",
        "chunks": len(chunks),
        "output": output_path,
    }


# ============================================================
# OWL / ONTOLOGY CHUNKING
# ============================================================

def build_ontology_units(
    asset: Dict[str, Any],
    extracted: Dict[str, Any],
) -> List[Dict[str, Any]]:

    asset_id = str(
        asset.get("asset_id")
        or asset.get("id")
        or "ontology"
    )

    triples = extracted.get(
        "triples",
        [],
    )

    units: List[Dict[str, Any]] = []

    for index, triple in enumerate(
        triples
    ):

        subject = str(
            triple.get(
                "subject",
                "",
            )
        )

        predicate = str(
            triple.get(
                "predicate",
                "",
            )
        )

        obj = str(
            triple.get(
                "object",
                "",
            )
        )

        if not subject or not predicate:
            continue

        content = (
            f"Subject: {subject}\n"
            f"Predicate: {predicate}\n"
            f"Object: {obj}"
        )

        locator = (
            f"triple:{index}"
        )

        chunk_id = make_chunk_id(
            asset_id,
            content,
            locator,
        )

        units.append(
            {
                "chunk_id": chunk_id,
                "chunk_type": "ontology_triple",
                "content": content,

                "metadata": {
                    "asset_id": asset_id,
                    "source_id": asset_id,
                    "source_type": "owl",
                    "source_file": asset.get(
                        "source_file"
                    ),
                    "version": asset.get(
                        "version"
                    ),
                    "triple_index": index,

                    "subject": subject,
                    "predicate": predicate,
                    "object": obj,
                },
            }
        )

    return units


def extract_owl_chunks(
    asset: Dict[str, Any],
    extracted: Dict[str, Any],
    output_path: Path,
) -> Dict[str, Any]:

    units = build_ontology_units(
        asset,
        extracted,
    )

    result = {
        "chunker_version": CHUNKER_VERSION,
        "asset_id": asset.get(
            "asset_id"
        ),
        "source_id": asset.get(
            "asset_id"
        ),
        "source_type": "owl",
        "source_file": asset.get(
            "source_file"
        ),
        "unit_count": len(units),
        "representation": (
            "RDF triples preserved as "
            "ontology retrieval units"
        ),
        "units": units,
        "generated_at": utc_now(),
    }

    write_json(
        output_path,
        result,
    )

    return {
        "format": "owl",
        "chunks": len(units),
        "output": output_path,
    }


# ============================================================
# STRUCTURED CSV REGISTRY
# ============================================================

def extract_structured_registry(
    asset: Dict[str, Any],
    extracted: Dict[str, Any],
    output_path: Path,
) -> Dict[str, Any]:

    asset_id = str(
        asset.get("asset_id")
        or asset.get("id")
        or "structured_asset"
    )

    columns = extracted.get(
        "columns",
        [],
    )

    row_count = extracted.get(
        "row_count",
        0,
    )

    result = {
        "chunker_version": CHUNKER_VERSION,
        "asset_id": asset_id,
        "source_id": asset_id,
        "source_type": "csv",
        "source_file": asset.get(
            "source_file"
        ),

        # Explicitly tell downstream systems:
        # DO NOT embed this as a normal document.
        "retrieval_mode": "structured",

        "embedding_allowed": False,

        "query_path": (
            "pandas_or_sql_structured_lookup"
        ),

        "schema": {
            "columns": columns,
            "row_count": row_count,
        },

        "metadata": {
            "asset_id": asset_id,
            "source_id": asset_id,
            "source_type": "csv",
            "source_file": asset.get(
                "source_file"
            ),
            "version": asset.get(
                "version"
            ),
        },

        "generated_at": utc_now(),
    }

    write_json(
        output_path,
        result,
    )

    return {
        "format": "csv",
        "chunks": 0,
        "rows": row_count,
        "output": output_path,
    }


# ============================================================
# EXTRACTION MANIFEST HANDLING
# ============================================================

def load_extraction_manifest() -> Dict[str, Any]:

    if not EXTRACTION_MANIFEST.exists():

        raise FileNotFoundError(
            "Extraction manifest not found: "
            f"{EXTRACTION_MANIFEST}"
        )

    manifest = read_json(
        EXTRACTION_MANIFEST
    )

    if not isinstance(
        manifest,
        dict,
    ):

        raise ValueError(
            "Extraction manifest must "
            "contain a JSON object."
        )

    return manifest


def resolve_extracted_output(
    asset: Dict[str, Any],
) -> Path:

    output = asset.get(
        "output"
    )

    if not output:
        raise ValueError(
            "Asset has no extraction output path."
        )

    candidate = Path(
        str(output)
    )

    # Absolute output path.
    if (
        candidate.is_absolute()
        and candidate.exists()
    ):
        return candidate.resolve()

    # Project-relative path.
    project_candidate = (
        PROJECT_ROOT
        / candidate
    )

    if project_candidate.exists():
        return project_candidate.resolve()

    # Extracted-directory fallback.
    extracted_candidate = (
        EXTRACTED_DIR
        / candidate.name
    )

    if extracted_candidate.exists():
        return extracted_candidate.resolve()

    raise FileNotFoundError(
        "Could not resolve extracted output: "
        f"{output}"
    )


# ============================================================
# ASSET PROCESSING
# ============================================================

def process_asset(
    asset: Dict[str, Any],
) -> Dict[str, Any]:

    asset_id = str(
        asset.get(
            "asset_id",
            "unknown",
        )
    )

    fmt = str(
        asset.get(
            "format",
            "",
        )
    ).lower()

    extracted_path = resolve_extracted_output(
        asset
    )

    extracted = read_json(
        extracted_path
    )

    if fmt == "pdf":

        output_path = (
            DOCUMENTS_DIR
            / (
                safe_filename(
                    asset_id
                )
                + ".json"
            )
        )

        return extract_pdf_chunks(
            asset,
            extracted,
            output_path,
        )

    if fmt == "owl":

        output_path = (
            ONTOLOGY_DIR
            / (
                safe_filename(
                    asset_id
                )
                + ".json"
            )
        )

        return extract_owl_chunks(
            asset,
            extracted,
            output_path,
        )

    if fmt == "csv":

        output_path = (
            STRUCTURED_DIR
            / (
                safe_filename(
                    asset_id
                )
                + ".json"
            )
        )

        return extract_structured_registry(
            asset,
            extracted,
            output_path,
        )

    raise ValueError(
        f"Unsupported extracted format: {fmt}"
    )


# ============================================================
# MANIFEST
# ============================================================

def build_manifest(
    extraction_manifest: Dict[str, Any],
    successful: List[Dict[str, Any]],
    failed: List[Dict[str, Any]],
) -> Dict[str, Any]:

    pdf_chunks = sum(
        int(
            item.get(
                "chunks",
                0,
            )
        )
        for item in successful
        if item.get("format") == "pdf"
    )

    owl_units = sum(
        int(
            item.get(
                "chunks",
                0,
            )
        )
        for item in successful
        if item.get("format") == "owl"
    )

    structured_assets = sum(
        1
        for item in successful
        if item.get("format") == "csv"
    )

    return {
        "manifest_version": "1.0",

        "pipeline": (
            "amr_knowledge_asset_chunking"
        ),

        "chunker_version": CHUNKER_VERSION,

        "generated_at": utc_now(),

        "input": {
            "extraction_manifest": str(
                EXTRACTION_MANIFEST.relative_to(
                    PROJECT_ROOT
                )
            ),
            "extraction_manifest_version":
                extraction_manifest.get(
                    "manifest_version"
                ),
        },

        "output_directory": str(
            CHUNKS_DIR.relative_to(
                PROJECT_ROOT
            )
        ),

        "design": {
            "pdf_strategy":
                "page-aware semantic "
                "paragraph chunking",

            "owl_strategy":
                "RDF triples preserved "
                "as ontology units",

            "csv_strategy":
                "structured registry only; "
                "no embedding chunks",

            "embedding_generated": False,
        },

        "assets_discovered": (
            len(successful)
            + len(failed)
        ),

        "successful": len(
            successful
        ),

        "failed": len(
            failed
        ),

        "statistics": {
            "pdf_chunks": pdf_chunks,
            "owl_units": owl_units,
            "structured_csv_assets":
                structured_assets,
        },

        "assets": successful + failed,

        "failed_assets": failed,
    }


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    line()

    print(
        "AMR KNOWLEDGE ASSET CHUNKER — V1"
    )

    line()

    info(
        f"Input    : {EXTRACTED_DIR}"
    )

    info(
        f"Output   : {CHUNKS_DIR}"
    )

    info(
        f"Manifest : {EXTRACTION_MANIFEST}"
    )

    print()

    # --------------------------------------------------------
    # Load extraction manifest
    # --------------------------------------------------------

    try:

        extraction_manifest = (
            load_extraction_manifest()
        )

    except Exception as exc:

        error(
            f"Could not load extraction manifest: "
            f"{exc}"
        )

        return 1

    assets = extraction_manifest.get(
        "assets",
        [],
    )

    if not isinstance(
        assets,
        list,
    ):

        error(
            "Extraction manifest contains "
            "no valid assets list."
        )

        return 1

    print(
        f"Found {len(assets)} extracted asset(s).\n"
    )

    # --------------------------------------------------------
    # Prepare output directories
    # --------------------------------------------------------

    DOCUMENTS_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    ONTOLOGY_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    STRUCTURED_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    successful: List[
        Dict[str, Any]
    ] = []

    failed: List[
        Dict[str, Any]
    ] = []

    # --------------------------------------------------------
    # Process assets
    # --------------------------------------------------------

    for index, asset in enumerate(
        assets,
        start=1,
    ):

        asset_id = str(
            asset.get(
                "asset_id",
                "unknown",
            )
        )

        fmt = str(
            asset.get(
                "format",
                "unknown",
            )
        ).upper()

        print(
            f"[{index:02d}/{len(assets):02d}] "
            f"{asset_id} "
            f"({fmt})"
        )

        try:

            output = process_asset(
                asset
            )

            result = {
                "asset_id": asset_id,
                "source_file": asset.get(
                    "source_file"
                ),
                "format": output.get(
                    "format"
                ),
                "status": "success",
                "output": str(
                    Path(
                        output["output"]
                    ).relative_to(
                        PROJECT_ROOT
                    )
                ),
                "chunks": output.get(
                    "chunks",
                    0,
                ),
            }

            if "rows" in output:

                result["rows"] = output[
                    "rows"
                ]

            successful.append(
                result
            )

            if fmt == "PDF":

                success(
                    f"chunked — "
                    f"{output['chunks']} "
                    f"document chunks"
                )

            elif fmt == "OWL":

                success(
                    f"parsed — "
                    f"{output['chunks']:,} "
                    f"ontology units"
                )

            elif fmt == "CSV":

                success(
                    "registered as structured "
                    f"data — "
                    f"{output.get('rows', 0)} rows"
                )

        except Exception as exc:

            failure = {
                "asset_id": asset_id,
                "source_file": asset.get(
                    "source_file"
                ),
                "format": asset.get(
                    "format"
                ),
                "status": "failed",
                "error": str(exc),
            }

            failed.append(
                failure
            )

            error(
                f"chunking failed: {exc}"
            )

        print()

    # --------------------------------------------------------
    # Build manifest
    # --------------------------------------------------------

    manifest = build_manifest(
        extraction_manifest,
        successful,
        failed,
    )

    write_json(
        CHUNK_MANIFEST,
        manifest,
    )

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    line()

    print(
        "CHUNKING SUMMARY"
    )

    line()

    print(
        f"Assets discovered : "
        f"{len(assets)}"
    )

    print(
        f"Successfully processed : "
        f"{len(successful)}"
    )

    print(
        f"Failed : "
        f"{len(failed)}"
    )

    print()

    print(
        "Generated knowledge units:"
    )

    print(
        f"  PDF document chunks : "
        f"{manifest['statistics']['pdf_chunks']:,}"
    )

    print(
        f"  OWL ontology units  : "
        f"{manifest['statistics']['owl_units']:,}"
    )

    print(
        f"  CSV structured assets : "
        f"{manifest['statistics']['structured_csv_assets']}"
    )

    print()

    success(
        "Chunk manifest written to:"
    )

    print(
        f"  {CHUNK_MANIFEST}"
    )

    if failed:

        print()

        warning(
            "Some assets failed chunking."
        )

        print()

        print(
            "Failed assets:"
        )

        for item in failed:

            print(
                f"  - "
                f"{item['asset_id']}: "
                f"{item['error']}"
            )

        return 1

    print()

    success(
        "All extracted assets processed successfully."
    )

    return 0


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    sys.exit(main())