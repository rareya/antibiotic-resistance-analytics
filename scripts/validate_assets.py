# ============================================================
# AMR KNOWLEDGE ASSET REGISTRY VALIDATOR
# ============================================================
#
# Validates:
#   knowledge/registry/assets.yaml
#   against:
#   knowledge/registry/asset.schema.yaml
#
# Also validates:
#   asset -> source provenance
#
# Design principles:
#   1. Schema controls structure and types.
#   2. Do not hard-code arbitrary asset vocabularies.
#   3. Source IDs must exist in sources.yaml.
#   4. Local raw files are checked when declared.
#   5. URLs are checked for basic validity.
#   6. knowledge_role may be a string OR list of strings.
#
# ============================================================

from pathlib import Path
import hashlib
import sys
import yaml


# ============================================================
# PATHS
# ============================================================

ROOT = Path(__file__).resolve().parents[1]

ASSETS_FILE = ROOT / "knowledge" / "registry" / "assets.yaml"
SCHEMA_FILE = ROOT / "knowledge" / "registry" / "asset.schema.yaml"
SOURCES_FILE = ROOT / "knowledge" / "registry" / "sources.yaml"
RAW_DIR = ROOT / "knowledge" / "raw"


# ============================================================
# OUTPUT
# ============================================================

WIDTH = 70

errors = []
warnings = []


def header(title):
    print("=" * WIDTH)
    print(title)
    print("=" * WIDTH)


def section(title):
    print()
    print(title)
    print("-" * WIDTH)


def error(message):
    errors.append(message)


def warning(message):
    warnings.append(message)


# ============================================================
# BASIC HELPERS
# ============================================================

def is_non_empty_string(value):
    return isinstance(value, str) and bool(value.strip())


def is_list_of_strings(value):
    return (
        isinstance(value, list)
        and all(
            isinstance(item, str) and item.strip()
            for item in value
        )
    )


def is_integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def check_basic_type(asset_id, field, value, expected_type):

    prefix = f"assets.{asset_id}.{field}"

    if expected_type == "string":

        if not is_non_empty_string(value):
            error(
                f"{prefix}: must be a non-empty string"
            )
            return False

    elif expected_type == "integer":

        if not is_integer(value):
            error(
                f"{prefix}: must be an integer"
            )
            return False

    elif expected_type == "boolean":

        if not isinstance(value, bool):
            error(
                f"{prefix}: must be a boolean"
            )
            return False

    elif expected_type == "list":

        if not isinstance(value, list):
            error(
                f"{prefix}: must be a list"
            )
            return False

    elif expected_type == "mapping":

        if not isinstance(value, dict):
            error(
                f"{prefix}: must be a mapping"
            )
            return False

    return True


# ============================================================
# YAML LOADER
# ============================================================

def load_yaml(path, label):

    try:

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        print(f"✓ {label} loaded")
        return data

    except FileNotFoundError:

        error(
            f"{label}: file not found: {path}"
        )

    except yaml.YAMLError as exc:

        error(
            f"{label}: invalid YAML: {exc}"
        )

    except Exception as exc:

        error(
            f"{label}: unexpected error: {exc}"
        )

    return None


# ============================================================
# SCHEMA VALIDATION
# ============================================================

def validate_schema_structure(schema):

    if not isinstance(schema, dict):

        error(
            "asset.schema.yaml: root must be a mapping"
        )

        return

    if "schema" not in schema:

        warning(
            "asset.schema.yaml does not contain a "
            "top-level 'schema' section"
        )

    if "required_fields" not in schema:

        error(
            "asset.schema.yaml: missing 'required_fields'"
        )

    if "field_rules" not in schema:

        error(
            "asset.schema.yaml: missing 'field_rules'"
        )


# ============================================================
# SOURCE REGISTRY EXTRACTION
# ============================================================

def get_source_entries(sources):

    if not isinstance(sources, dict):

        error(
            "sources.yaml: root must be a mapping"
        )

        return {}

    # Our actual registry format:
    #
    # registry:
    #   name: ...
    #   version: ...
    #
    # sources:
    #   who_glass:
    #     ...

    source_entries = sources.get("sources")

    if source_entries is None:

        error(
            "sources.yaml: missing top-level 'sources'"
        )

        return {}

    if not isinstance(source_entries, dict):

        error(
            "sources.yaml: 'sources' must be a mapping"
        )

        return {}

    return source_entries


# ============================================================
# ASSET REGISTRY EXTRACTION
# ============================================================

def get_asset_entries(assets):

    if not isinstance(assets, dict):

        error(
            "assets.yaml: root must be a mapping"
        )

        return {}

    # --------------------------------------------------------
    # Expected format:
    #
    # registry:
    #   name: ...
    #   version: ...
    #
    # assets:
    #   asset_id:
    #     ...
    # --------------------------------------------------------

    asset_entries = assets.get("assets")

    if asset_entries is None:

        error(
            "assets.yaml: missing top-level 'assets'"
        )

        return {}

    if not isinstance(asset_entries, dict):

        error(
            "assets.yaml: 'assets' must be a mapping"
        )

        return {}

    return asset_entries


# ============================================================
# LOCAL FILE RESOLUTION
# ============================================================

def resolve_local_path(value):

    if not isinstance(value, str):
        return None

    raw = value.strip()

    if not raw:
        return None

    # --------------------------------------------------------
    # Remove ./ if present
    # --------------------------------------------------------

    raw = raw.replace("/", "\\").lstrip(".\\")

    # --------------------------------------------------------
    # If path starts with knowledge/
    # resolve relative to repository root.
    # --------------------------------------------------------

    candidate = ROOT / raw

    if candidate.exists():
        return candidate

    # --------------------------------------------------------
    # Otherwise assume path relative to knowledge/raw.
    # --------------------------------------------------------

    candidate = RAW_DIR / raw

    if candidate.exists():
        return candidate

    return None


# ============================================================
# FILE VALIDATION
# ============================================================

def validate_file_reference(asset_id, field, value):

    prefix = f"assets.{asset_id}.{field}"

    if not isinstance(value, str):
        return

    path = resolve_local_path(value)

    if path is None:

        warning(
            f"{prefix}: local file not found: {value}"
        )

        return

    if not path.is_file():

        error(
            f"{prefix}: path exists but is not a file: {value}"
        )

        return

    size = path.stat().st_size

    if size == 0:

        error(
            f"{prefix}: file is empty: {value}"
        )

    else:

        print(
            f"  ✓ local file: {value} "
            f"({size:,} bytes)"
        )


# ============================================================
# SHA256 VALIDATION
# ============================================================

def calculate_sha256(path):

    sha256 = hashlib.sha256()

    with open(path, "rb") as f:

        for chunk in iter(
            lambda: f.read(1024 * 1024),
            b""
        ):

            sha256.update(chunk)

    return sha256.hexdigest()


def validate_hash(asset_id, asset, path):

    expected = asset.get("sha256")

    if not expected:
        return

    if not path or not path.is_file():
        return

    actual = calculate_sha256(path)

    if actual.lower() != str(expected).lower():

        error(
            f"assets.{asset_id}.sha256: "
            f"hash mismatch\n"
            f"    expected: {expected}\n"
            f"    actual:   {actual}"
        )

    else:

        print(
            f"  ✓ sha256 verified"
        )


# ============================================================
# URL VALIDATION
# ============================================================

def validate_url(asset_id, field, value):

    if value is None:
        return

    prefix = f"assets.{asset_id}.{field}"

    if not isinstance(value, str):

        error(
            f"{prefix}: must be a string"
        )

        return

    if not (
        value.startswith("http://")
        or value.startswith("https://")
    ):

        warning(
            f"{prefix}: does not appear to be an HTTP(S) URL"
        )


# ============================================================
# KNOWLEDGE ROLE VALIDATION
# ============================================================

def validate_knowledge_role(asset_id, value):

    prefix = f"assets.{asset_id}.knowledge_role"

    if value is None:

        error(
            f"{prefix}: missing required field"
        )

        return

    # --------------------------------------------------------
    # String form
    # --------------------------------------------------------

    if isinstance(value, str):

        if not value.strip():

            error(
                f"{prefix}: must not be empty"
            )

        return

    # --------------------------------------------------------
    # List form
    # --------------------------------------------------------

    if isinstance(value, list):

        if len(value) == 0:

            error(
                f"{prefix}: list must not be empty"
            )

            return

        for role in value:

            if not isinstance(role, str):

                error(
                    f"{prefix}: all values must be strings"
                )

            elif not role.strip():

                error(
                    f"{prefix}: values must not be empty"
                )

        return

    # --------------------------------------------------------
    # Anything else
    # --------------------------------------------------------

    error(
        f"{prefix}: must be a string "
        f"or list of strings"
    )


# ============================================================
# ACCESS VALIDATION
# ============================================================

def validate_access_details(asset_id, access_details):

    if access_details is None:
        return

    if not isinstance(access_details, dict):

        error(
            f"assets.{asset_id}.access_details: "
            f"must be a mapping"
        )

        return

    canonical_url = access_details.get("canonical_url")

    if canonical_url is not None:

        validate_url(
            asset_id,
            "access_details.canonical_url",
            canonical_url
        )

    download_url = access_details.get("download_url")

    if download_url is not None:

        validate_url(
            asset_id,
            "access_details.download_url",
            download_url
        )

    retrieval_modes = access_details.get(
        "retrieval_modes"
    )

    if retrieval_modes is not None:

        if not is_list_of_strings(retrieval_modes):

            error(
                f"assets.{asset_id}."
                f"access_details.retrieval_modes: "
                f"must be a list of strings"
            )


# ============================================================
# PROVENANCE VALIDATION
# ============================================================

def validate_provenance(asset_id, asset, source_entries):

    source_id = asset.get("source_id")

    if source_id is None:

        warning(
            f"assets.{asset_id}: no source_id declared"
        )

        return

    if not isinstance(source_id, str):

        error(
            f"assets.{asset_id}.source_id: "
            f"must be a string"
        )

        return

    if source_id not in source_entries:

        error(
            f"assets.{asset_id}.source_id: "
            f"unknown source '{source_id}'"
        )

    else:

        print(
            f"  ✓ source provenance: {source_id}"
        )


# ============================================================
# ASSET VALIDATION
# ============================================================

def validate_asset(
    asset_id,
    asset,
    schema,
    source_entries
):

    prefix = f"assets.{asset_id}"

    if not isinstance(asset, dict):

        error(
            f"{prefix}: must be a mapping"
        )

        return

    required_fields = schema.get(
        "required_fields",
        []
    )

    field_rules = schema.get(
        "field_rules",
        {}
    )

    # ========================================================
    # REQUIRED FIELDS
    # ========================================================

    for field in required_fields:

        if field not in asset:

            error(
                f"{prefix}: missing required field "
                f"'{field}'"
            )

            continue

        value = asset[field]

        rules = field_rules.get(
            field,
            {}
        )

        expected_type = rules.get(
            "type"
        )

        if expected_type:

            check_basic_type(
                asset_id,
                field,
                value,
                expected_type
            )

        # ----------------------------------------------------
        # String rules
        # ----------------------------------------------------

        if (
            expected_type == "string"
            and isinstance(value, str)
        ):

            minimum = rules.get(
                "min_length"
            )

            if (
                minimum is not None
                and len(value.strip()) < minimum
            ):

                error(
                    f"{prefix}.{field}: "
                    f"must contain at least "
                    f"{minimum} character(s)"
                )

        # ----------------------------------------------------
        # List rules
        # ----------------------------------------------------

        if expected_type == "list":

            if not isinstance(value, list):
                continue

            minimum = rules.get(
                "min_items"
            )

            if (
                minimum is not None
                and len(value) < minimum
            ):

                error(
                    f"{prefix}.{field}: "
                    f"must contain at least "
                    f"{minimum} item(s)"
                )

            item_type = rules.get(
                "item_type"
            )

            if item_type == "string":

                if not is_list_of_strings(value):

                    error(
                        f"{prefix}.{field}: "
                        f"must be a list of strings"
                    )

        # ----------------------------------------------------
        # Integer rules
        # ----------------------------------------------------

        if expected_type == "integer":

            if not is_integer(value):
                continue

            minimum = rules.get(
                "minimum"
            )

            maximum = rules.get(
                "maximum"
            )

            if (
                minimum is not None
                and value < minimum
            ):

                error(
                    f"{prefix}.{field}: "
                    f"value {value} is below minimum "
                    f"{minimum}"
                )

            if (
                maximum is not None
                and value > maximum
            ):

                error(
                    f"{prefix}.{field}: "
                    f"value {value} exceeds maximum "
                    f"{maximum}"
                )

        # ----------------------------------------------------
        # Explicit schema enum
        # ----------------------------------------------------

        allowed = rules.get(
            "allowed"
        )

        if allowed is not None:

            if expected_type == "string":

                if value not in allowed:

                    error(
                        f"{prefix}.{field}: "
                        f"invalid value '{value}'. "
                        f"Allowed: {allowed}"
                    )

            elif expected_type == "list":

                if isinstance(value, list):

                    for item in value:

                        if item not in allowed:

                            error(
                                f"{prefix}.{field}: "
                                f"invalid value '{item}'. "
                                f"Allowed: {allowed}"
                            )

    # ========================================================
    # SEMANTIC CHECKS
    # ========================================================

    # --------------------------------------------------------
    # ID consistency
    # --------------------------------------------------------

    declared_id = asset.get("id")

    if declared_id is not None:

        if declared_id != asset_id:

            error(
                f"{prefix}.id: value '{declared_id}' "
                f"does not match registry key "
                f"'{asset_id}'"
            )

    # --------------------------------------------------------
    # Knowledge role
    # --------------------------------------------------------

    validate_knowledge_role(
        asset_id,
        asset.get("knowledge_role")
    )

    # --------------------------------------------------------
    # Source provenance
    # --------------------------------------------------------

    validate_provenance(
        asset_id,
        asset,
        source_entries
    )

    # --------------------------------------------------------
    # Access details
    # --------------------------------------------------------

    validate_access_details(
        asset_id,
        asset.get("access_details")
    )

    # --------------------------------------------------------
    # Common direct URL fields
    # --------------------------------------------------------

    for field in (
        "canonical_url",
        "source_url",
        "download_url",
        "url"
    ):

        if field in asset:

            validate_url(
                asset_id,
                field,
                asset[field]
            )

    # --------------------------------------------------------
    # Local file fields
    # --------------------------------------------------------

    local_file_fields = (
        "local_path",
        "raw_path",
        "file",
        "file_path"
    )

    for field in local_file_fields:

        if field in asset:

            value = asset[field]

            validate_file_reference(
                asset_id,
                field,
                value
            )

            path = resolve_local_path(value)

            if path:

                validate_hash(
                    asset_id,
                    asset,
                    path
                )

    # --------------------------------------------------------
    # Known nested local path patterns
    # --------------------------------------------------------

    for container_name in (
        "storage",
        "local",
        "files",
        "data"
    ):

        container = asset.get(
            container_name
        )

        if not isinstance(container, dict):
            continue

        for field in (
            "path",
            "local_path",
            "raw_path",
            "file",
            "file_path"
        ):

            if field not in container:
                continue

            value = container[field]

            if not isinstance(value, str):
                continue

            path = resolve_local_path(value)

            if path:

                print(
                    f"  ✓ local file: {value} "
                    f"({path.stat().st_size:,} bytes)"
                )

                validate_hash(
                    asset_id,
                    asset,
                    path
                )

            else:

                warning(
                    f"{prefix}.{container_name}.{field}: "
                    f"local file not found: {value}"
                )


# ============================================================
# REGISTRY VALIDATION
# ============================================================

def validate_registry(
    assets,
    schema,
    sources
):

    asset_entries = get_asset_entries(
        assets
    )

    source_entries = get_source_entries(
        sources
    )

    if not asset_entries:
        return

    print(
        f"Found {len(asset_entries)} registered asset(s)."
    )

    # --------------------------------------------------------
    # Validate each asset
    # --------------------------------------------------------

    for asset_id, asset in asset_entries.items():

        print()
        print(f"↓ {asset_id}")

        validate_asset(
            asset_id,
            asset,
            schema,
            source_entries
        )


# ============================================================
# MAIN
# ============================================================

def main():

    header(
        "AMR KNOWLEDGE ASSET REGISTRY VALIDATOR"
    )

    print()
    print(f"Assets  : {ASSETS_FILE}")
    print(f"Schema  : {SCHEMA_FILE}")
    print(f"Sources : {SOURCES_FILE}")

    # ========================================================
    # LOAD SCHEMA
    # ========================================================

    section(
        "Loading schema..."
    )

    schema = load_yaml(
        SCHEMA_FILE,
        "asset.schema.yaml"
    )

    if schema is None:

        print()
        print(
            "=" * WIDTH
        )
        print(
            "❌ VALIDATION FAILED"
        )
        print(
            "=" * WIDTH
        )

        return 1

    # ========================================================
    # LOAD ASSETS
    # ========================================================

    section(
        "Loading assets registry..."
    )

    assets = load_yaml(
        ASSETS_FILE,
        "assets.yaml"
    )

    if assets is None:

        print()
        print(
            "=" * WIDTH
        )
        print(
            "❌ VALIDATION FAILED"
        )
        print(
            "=" * WIDTH
        )

        return 1

    # ========================================================
    # LOAD SOURCES
    # ========================================================

    section(
        "Loading source registry..."
    )

    sources = load_yaml(
        SOURCES_FILE,
        "sources.yaml"
    )

    if sources is None:

        print()
        print(
            "=" * WIDTH
        )
        print(
            "❌ VALIDATION FAILED"
        )
        print(
            "=" * WIDTH
        )

        return 1

    # ========================================================
    # VALIDATE SCHEMA
    # ========================================================

    section(
        "Running validation..."
    )

    validate_schema_structure(
        schema
    )

    # ========================================================
    # VALIDATE REGISTRY
    # ========================================================

    if not errors:

        validate_registry(
            assets,
            schema,
            sources
        )

    # ========================================================
    # WARNINGS
    # ========================================================

    if warnings:

        section(
            "WARNINGS"
        )

        for item in warnings:

            print(
                f"⚠ {item}"
            )

    # ========================================================
    # ERRORS
    # ========================================================

    if errors:

        section(
            "ERRORS"
        )

        for item in errors:

            print(
                f"✗ {item}"
            )

        print()
        print(
            "=" * WIDTH
        )
        print(
            "❌ VALIDATION FAILED"
        )
        print(
            "=" * WIDTH
        )

        return 1

    # ========================================================
    # SUCCESS
    # ========================================================

    print()
    print(
        "=" * WIDTH
    )
    print(
        "✓ VALIDATION PASSED"
    )
    print(
        "=" * WIDTH
    )

    print()
    print(
        "All registered assets satisfy the "
        "asset registry schema."
    )

    if warnings:

        print(
            f"{len(warnings)} warning(s) should be reviewed."
        )

    return 0


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )