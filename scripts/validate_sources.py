# ============================================================
# AMR KNOWLEDGE SOURCE REGISTRY VALIDATOR
# ============================================================
# Validates:
#   knowledge/registry/sources.yaml
#   against:
#   knowledge/registry/source.schema.yaml
#
# Design principle:
#   - Schema controls structure/types
#   - Validator performs only sensible semantic checks
#   - source_type, domains, capabilities and retrieval_modes
#     remain extensible lists of strings
# ============================================================

from pathlib import Path
import sys
import yaml


# ============================================================
# PATHS
# ============================================================

ROOT = Path(__file__).resolve().parents[1]

SOURCES_FILE = ROOT / "knowledge" / "registry" / "sources.yaml"
SCHEMA_FILE = ROOT / "knowledge" / "registry" / "source.schema.yaml"


# ============================================================
# OUTPUT
# ============================================================

WIDTH = 70


def header(title):
    print("=" * WIDTH)
    print(title)
    print("=" * WIDTH)


def section(title):
    print()
    print(title)
    print("-" * WIDTH)


# ============================================================
# HELPERS
# ============================================================

errors = []
warnings = []


def error(message):
    errors.append(message)


def warning(message):
    warnings.append(message)


def is_non_empty_string(value):
    return isinstance(value, str) and bool(value.strip())


def is_list_of_strings(value):
    return (
        isinstance(value, list)
        and all(isinstance(item, str) and item.strip() for item in value)
    )


def check_type(source_id, field, value, expected_type):
    if expected_type == "string":
        if not is_non_empty_string(value):
            error(
                f"sources.{source_id}.{field}: "
                f"must be a non-empty string"
            )
            return False

    elif expected_type == "integer":
        # bool is technically an int in Python, but should not count
        if isinstance(value, bool) or not isinstance(value, int):
            error(
                f"sources.{source_id}.{field}: "
                f"must be an integer"
            )
            return False

    elif expected_type == "list":
        if not isinstance(value, list):
            error(
                f"sources.{source_id}.{field}: "
                f"must be a list"
            )
            return False

    return True


# ============================================================
# LOAD YAML
# ============================================================

def load_yaml(path, label):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        print(f"✓ {label} loaded")
        return data

    except FileNotFoundError:
        error(f"{label}: file not found: {path}")
        return None

    except yaml.YAMLError as exc:
        error(f"{label}: invalid YAML: {exc}")
        return None

    except Exception as exc:
        error(f"{label}: unexpected error: {exc}")
        return None


# ============================================================
# SCHEMA VALIDATION
# ============================================================

def validate_schema_structure(schema):
    if not isinstance(schema, dict):
        error("source.schema.yaml: root must be a mapping")
        return

    if "schema" not in schema:
        warning(
            "source.schema.yaml does not contain a top-level "
            "'schema' section"
        )

    if "required_fields" not in schema:
        error(
            "source.schema.yaml: missing 'required_fields'"
        )

    if "field_rules" not in schema:
        error(
            "source.schema.yaml: missing 'field_rules'"
        )


# ============================================================
# SOURCE REGISTRY VALIDATION
# ============================================================

def validate_sources(sources, schema):

    if not isinstance(sources, dict):
        error("sources.yaml: root must be a mapping")
        return

    # --------------------------------------------------------
    # Registry metadata
    # --------------------------------------------------------

    registry = sources.get("registry")

    if registry is None:
        error("sources.yaml: missing top-level 'registry'")
    elif not isinstance(registry, dict):
        error("sources.yaml: 'registry' must be a mapping")

    # --------------------------------------------------------
    # Sources
    #
    # IMPORTANT:
    # `sources` is a sibling of `registry`.
    #
    # registry:
    #   name: ...
    #   version: ...
    #
    # sources:
    #   who_glass:
    #     ...
    # --------------------------------------------------------

    source_entries = sources.get("sources")

    if source_entries is None:
        error("sources.yaml: missing top-level 'sources'")
        return

    if not isinstance(source_entries, dict):
        error("sources.yaml: 'sources' must be a mapping")
        return

    required_fields = schema.get("required_fields", [])
    field_rules = schema.get("field_rules", {})

    # --------------------------------------------------------
    # Validate each source
    # --------------------------------------------------------

    for source_id, source in source_entries.items():

        prefix = f"sources.{source_id}"

        if not isinstance(source, dict):
            error(f"{prefix}: must be a mapping")
            continue

        # ====================================================
        # REQUIRED FIELDS
        # ====================================================

        for field in required_fields:

            if field not in source:
                error(
                    f"{prefix}: missing required field '{field}'"
                )
                continue

            value = source[field]

            rules = field_rules.get(field, {})
            expected_type = rules.get("type")

            # ------------------------------------------------
            # Type
            # ------------------------------------------------

            if expected_type:
                check_type(
                    source_id,
                    field,
                    value,
                    expected_type
                )

            # ------------------------------------------------
            # String rules
            # ------------------------------------------------

            if (
                expected_type == "string"
                and isinstance(value, str)
            ):

                min_length = rules.get("min_length")

                if (
                    min_length is not None
                    and len(value.strip()) < min_length
                ):
                    error(
                        f"{prefix}.{field}: "
                        f"must contain at least "
                        f"{min_length} character(s)"
                    )

            # ------------------------------------------------
            # List rules
            # ------------------------------------------------

            if expected_type == "list":

                if not isinstance(value, list):
                    continue

                min_items = rules.get("min_items")

                if (
                    min_items is not None
                    and len(value) < min_items
                ):
                    error(
                        f"{prefix}.{field}: "
                        f"must contain at least "
                        f"{min_items} item(s)"
                    )

                item_type = rules.get("item_type")

                if item_type == "string":

                    if not is_list_of_strings(value):
                        error(
                            f"{prefix}.{field}: "
                            f"must be a non-empty list "
                            f"of strings"
                        )

            # ------------------------------------------------
            # Integer rules
            # ------------------------------------------------

            if expected_type == "integer":

                if (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                ):
                    continue

                minimum = rules.get("minimum")
                maximum = rules.get("maximum")

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

            # ------------------------------------------------
            # Allowed values
            #
            # Only enforced if the schema explicitly defines
            # them.
            # ------------------------------------------------

            allowed = rules.get("allowed")

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

        # ====================================================
        # SEMANTIC CHECKS
        # ====================================================

        validate_source_semantics(
            source_id,
            source
        )


# ============================================================
# SEMANTIC VALIDATION
# ============================================================

def validate_source_semantics(source_id, source):

    prefix = f"sources.{source_id}"

    # --------------------------------------------------------
    # ID consistency
    # --------------------------------------------------------

    if "id" in source:

        if source["id"] != source_id:
            error(
                f"{prefix}.id: value '{source['id']}' "
                f"does not match registry key '{source_id}'"
            )

    # --------------------------------------------------------
    # Canonical URL
    # --------------------------------------------------------

    if "canonical_url" in source:

        url = source["canonical_url"]

        if url is not None:

            if not isinstance(url, str):
                error(
                    f"{prefix}.canonical_url: "
                    f"must be a string"
                )

            elif not (
                url.startswith("http://")
                or url.startswith("https://")
            ):
                warning(
                    f"{prefix}.canonical_url: "
                    f"does not appear to be an HTTP(S) URL"
                )

    # --------------------------------------------------------
    # Priority
    # --------------------------------------------------------

    if "priority" in source:

        priority = source["priority"]

        if isinstance(priority, bool) or not isinstance(priority, int):

            error(
                f"{prefix}.priority: "
                f"must be an integer"
            )

        elif not 1 <= priority <= 100:

            error(
                f"{prefix}.priority: "
                f"must be between 1 and 100"
            )

    # --------------------------------------------------------
    # Tier
    #
    # This is intentionally the ONLY controlled source
    # classification.
    # --------------------------------------------------------

    allowed_tiers = {
        "primary",
        "authoritative",
        "research",
        "reference",
        "secondary",
    }

    tier = source.get("tier")

    if tier is not None:

        if tier not in allowed_tiers:

            error(
                f"{prefix}.tier: invalid value '{tier}'. "
                f"Allowed: {sorted(allowed_tiers)}"
            )

    # --------------------------------------------------------
    # Capabilities
    #
    # We intentionally DO NOT restrict the vocabulary.
    # --------------------------------------------------------

    capabilities = source.get("capabilities")

    if isinstance(capabilities, list):

        if len(capabilities) == 0:
            error(
                f"{prefix}.capabilities: "
                f"must not be empty"
            )

        for capability in capabilities:

            if not isinstance(capability, str):
                error(
                    f"{prefix}.capabilities: "
                    f"all values must be strings"
                )

    # --------------------------------------------------------
    # Retrieval modes
    #
    # Also intentionally extensible.
    # --------------------------------------------------------

    retrieval_modes = source.get("retrieval_modes")

    if isinstance(retrieval_modes, list):

        if len(retrieval_modes) == 0:
            error(
                f"{prefix}.retrieval_modes: "
                f"must not be empty"
            )

        for mode in retrieval_modes:

            if not isinstance(mode, str):
                error(
                    f"{prefix}.retrieval_modes: "
                    f"all values must be strings"
                )

    # --------------------------------------------------------
    # Domains
    # --------------------------------------------------------

    domains = source.get("domains")

    if isinstance(domains, list):

        if len(domains) == 0:
            error(
                f"{prefix}.domains: "
                f"must not be empty"
            )

        for domain in domains:

            if not isinstance(domain, str):
                error(
                    f"{prefix}.domains: "
                    f"all values must be strings"
                )

    # --------------------------------------------------------
    # Clinical / susceptibility capability sanity check
    #
    # This is a warning, NOT a hard failure.
    # --------------------------------------------------------

    clinical_terms = {
        "clinical_susceptibility",
        "breakpoints",
        "antibiotic_breakpoints",
        "susceptibility_testing",
        "clinical_guidance",
    }

    capabilities_lower = {
        str(x).lower()
        for x in capabilities
        if isinstance(x, str)
    } if isinstance(capabilities, list) else set()

    if capabilities_lower.intersection(clinical_terms):

        source_types = {
            str(x).lower()
            for x in source.get("source_type", [])
            if isinstance(x, str)
        }

        clinical_types = {
            "guideline",
            "clinical_guideline",
            "laboratory_standard",
            "standards",
            "clinical_guidance",
        }

        if not source_types.intersection(clinical_types):

            warning(
                f"{prefix}: clinical susceptibility/"
                f"breakpoint capability declared without an "
                f"explicit guideline/standard source type"
            )


# ============================================================
# MAIN
# ============================================================

def main():

    header("AMR KNOWLEDGE SOURCE REGISTRY VALIDATOR")

    print()
    print(f"Sources : {SOURCES_FILE}")
    print(f"Schema  : {SCHEMA_FILE}")

    section("Loading schema...")

    schema = load_yaml(
        SCHEMA_FILE,
        "source.schema.yaml"
    )

    if schema is None:
        print()
        print("❌ VALIDATION FAILED")
        sys.exit(1)

    section("Loading sources registry...")

    sources = load_yaml(
        SOURCES_FILE,
        "sources.yaml"
    )

    if sources is None:
        print()
        print("❌ VALIDATION FAILED")
        sys.exit(1)

    section("Running validation...")

    validate_schema_structure(schema)

    if not errors:
        validate_sources(
            sources,
            schema
        )

    # --------------------------------------------------------
    # Results
    # --------------------------------------------------------

    if warnings:

        section("WARNINGS")

        for item in warnings:
            print(f"⚠ {item}")

    if errors:

        section("ERRORS")

        for item in errors:
            print(f"✗ {item}")

        print()
        print("=" * WIDTH)
        print("❌ VALIDATION FAILED")
        print("=" * WIDTH)

        sys.exit(1)

    print()
    print("=" * WIDTH)
    print("✓ VALIDATION PASSED")
    print("=" * WIDTH)

    print()
    print(
        "All registered sources satisfy the source registry schema."
    )

    if warnings:
        print(
            f"{len(warnings)} warning(s) should be reviewed."
        )


if __name__ == "__main__":
    main()