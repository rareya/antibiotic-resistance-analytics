"""
validate_sources.py
===================

Validate the AMR knowledge-base source governance registry.

Expected registry structure (matches the current sources.yaml contract,
which nests metadata under `registry:`):

    registry:
      schema_version
      registry_version
      registry_name
      created_at
    global_policy
    sources[]
    routing_rules
    compatibility_rules
    generation_contract

Run:
    python scripts/validate_sources.py
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


# ============================================================================
# PATHS
# ============================================================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]

SOURCES_PATH = (
    PROJECT_ROOT
    / "knowledge"
    / "registry"
    / "sources.yaml"
)


# ============================================================================
# CONTRACT
# ============================================================================

REQUIRED_TOP_LEVEL_FIELDS = {
    "registry",
    "global_policy",
    "sources",
    "routing_rules",
    "compatibility_rules",
    "generation_contract",
}

REQUIRED_REGISTRY_META_FIELDS = {
    "schema_version",
    "registry_version",
    "registry_name",
    "created_at",
}

REQUIRED_SOURCE_FIELDS = {
    "source_id",
    "asset_id",
    "name",
    "source_class",
    "evidence_class",
    "tier",
    "format",
    "retrieval_mode",
    "domain",
    "intended_uses",
    "ai_restrictions",
    "citation_required",
}

ALLOWED_SOURCE_CLASSES = {
    "guideline",
    "molecular_database",
    "ontology",
    "scientific_literature",
    "surveillance",
}

ALLOWED_EVIDENCE_CLASSES = {
    "clinical",
    "literature",
    "molecular",
    "surveillance",
}

ALLOWED_TIERS = {
    "tier_1",
    "tier_2",
    "tier_3",
}

ALLOWED_FORMATS = {
    "csv",
    "owl",
    "pdf",
}

ALLOWED_RETRIEVAL_MODES = {
    "structured",
    "vector",
    "ontology",
}

# Molecular evidence in this registry spans tier_2 (aro, megares) and
# tier_3 (genome_wode) by design - only clinical evidence is pinned to tier_1.
ALLOWED_MOLECULAR_TIERS = {
    "tier_2",
    "tier_3",
}

REQUIRED_GLOBAL_POLICY_FIELDS = {
    "answer_must_be_grounded",
    "citations_required",
    "allow_uncited_claims",
    "allow_external_knowledge_without_retrieval",
    "numeric_questions_prefer_structured",
    "source_restrictions_are_hard_constraints",
    "tier_order",
    "evidence_classes",
    "forbidden_inferences",
}

REQUIRED_ROUTES = {
    "structured_numeric",
    "clinical_interpretation",
    "molecular_lookup",
    "research_context",
    "surveillance_analysis",
}

REQUIRED_COMPATIBILITY_RULES = {
    "clinical_interpretation",
    "clinical_breakpoints",
    "numeric_surveillance",
    "molecular_evidence",
    "research_evidence",
}

REQUIRED_GENERATION_FIELDS = {
    "grounded_generation_required",
    "source_registration_required",
    "citations_required",
    "numeric_answers_require_source",
    "clinical_answers_require_authoritative_source",
    "molecular_claims_require_molecular_source",
    "unsupported_claims_must_be_refused",
    "modeled_data_must_be_explicitly_labeled",
    "observed_data_must_be_distinguished_from_estimates",
}


# ============================================================================
# VALIDATION STATE
# ============================================================================

errors: list[str] = []
warnings: list[str] = []


def error(message: str) -> None:
    errors.append(message)


def warning(message: str) -> None:
    warnings.append(message)


# ============================================================================
# HELPERS
# ============================================================================

def is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def is_non_empty_list(value: Any) -> bool:
    return isinstance(value, list) and len(value) > 0


def require_mapping(value: Any, path: str) -> bool:
    if not isinstance(value, dict):
        error(f"{path}: must be a mapping")
        return False
    return True


def require_string(value: Any, path: str) -> bool:
    if not is_non_empty_string(value):
        error(f"{path}: must be a non-empty string")
        return False
    return True


def require_boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        error(f"{path}: must be boolean")
        return False
    return True


def require_list(value: Any, path: str) -> bool:
    if not isinstance(value, list):
        error(f"{path}: must be a list")
        return False
    return True


def require_non_empty_list(value: Any, path: str) -> bool:
    if not is_non_empty_list(value):
        error(f"{path}: must be a non-empty list")
        return False
    return True


def check_enum(value: Any, path: str, allowed: set[str]) -> None:
    if value not in allowed:
        error(f"{path}: invalid value {value!r}. Allowed: {sorted(allowed)}")


# ============================================================================
# LOAD YAML
# ============================================================================

print("=" * 70)
print("AMR KNOWLEDGE SOURCE REGISTRY VALIDATOR")
print("=" * 70)
print()
print(f"Sources : {SOURCES_PATH}")
print()

if not SOURCES_PATH.exists():
    error(f"sources.yaml not found: {SOURCES_PATH}")

if errors:
    print("=" * 70)
    print("ERRORS")
    print("=" * 70)
    for message in errors:
        print(f"✗ {message}")
    print()
    print("❌ VALIDATION FAILED")
    raise SystemExit(1)

print("Loading sources registry...")
print("-" * 70)

try:
    with SOURCES_PATH.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
except yaml.YAMLError as exc:
    error(f"sources.yaml contains invalid YAML: {exc}")
    data = None
except OSError as exc:
    error(f"Unable to read sources.yaml: {exc}")
    data = None

if data is None:
    error("sources.yaml is empty")
elif not isinstance(data, dict):
    error("sources.yaml top level must be a mapping")
else:
    print("✓ sources.yaml loaded")


# ============================================================================
# TOP-LEVEL VALIDATION
# ============================================================================

if isinstance(data, dict):
    missing_top_level = REQUIRED_TOP_LEVEL_FIELDS - set(data.keys())
    for field in sorted(missing_top_level):
        error(f"missing required top-level field '{field}'")


# ============================================================================
# REGISTRY METADATA (nested under `registry:`)
# ============================================================================

if isinstance(data, dict):
    registry_meta = data.get("registry")

    if require_mapping(registry_meta, "registry"):
        missing_meta = REQUIRED_REGISTRY_META_FIELDS - set(registry_meta.keys())
        for field in sorted(missing_meta):
            error(f"registry: missing required field '{field}'")

        for field in REQUIRED_REGISTRY_META_FIELDS:
            if field in registry_meta:
                require_string(registry_meta[field], f"registry.{field}")


# ============================================================================
# GLOBAL POLICY
# ============================================================================

if isinstance(data, dict):
    global_policy = data.get("global_policy")

    if require_mapping(global_policy, "global_policy"):
        missing = REQUIRED_GLOBAL_POLICY_FIELDS - set(global_policy.keys())
        for field in sorted(missing):
            error(f"global_policy: missing required field '{field}'")

        boolean_fields = {
            "answer_must_be_grounded",
            "citations_required",
            "allow_uncited_claims",
            "allow_external_knowledge_without_retrieval",
            "numeric_questions_prefer_structured",
            "source_restrictions_are_hard_constraints",
        }
        for field in boolean_fields:
            if field in global_policy:
                require_boolean(global_policy[field], f"global_policy.{field}")

        tier_order = global_policy.get("tier_order")
        if tier_order != ["tier_1", "tier_2", "tier_3"]:
            error("global_policy.tier_order: must be ['tier_1', 'tier_2', 'tier_3']")

        require_list(
            global_policy.get("forbidden_inferences"),
            "global_policy.forbidden_inferences",
        )

        evidence_classes = global_policy.get("evidence_classes")
        if require_mapping(evidence_classes, "global_policy.evidence_classes"):
            missing_classes = ALLOWED_EVIDENCE_CLASSES - set(evidence_classes.keys())
            for cls in sorted(missing_classes):
                error(f"global_policy.evidence_classes: missing '{cls}'")


# ============================================================================
# SOURCES
# ============================================================================

sources = None

if isinstance(data, dict):
    sources = data.get("sources")

    if not isinstance(sources, list):
        error("sources: must be a list of source registry entries")
    else:
        if len(sources) == 0:
            error("sources: must contain at least one source")

        print()
        print(f"Validating registered sources ({len(sources)})...")
        print("-" * 70)

        source_ids: list[str] = []
        asset_ids: list[str] = []

        for index, source in enumerate(sources):
            path = f"sources[{index}]"

            if not isinstance(source, dict):
                error(f"{path}: must be a mapping")
                continue

            missing = REQUIRED_SOURCE_FIELDS - set(source.keys())
            for field in sorted(missing):
                error(f"{path}: missing required field '{field}'")

            for field in ("source_id", "asset_id", "name"):
                if field in source:
                    require_string(source[field], f"{path}.{field}")

            if "source_class" in source:
                check_enum(source["source_class"], f"{path}.source_class", ALLOWED_SOURCE_CLASSES)

            if "evidence_class" in source:
                check_enum(source["evidence_class"], f"{path}.evidence_class", ALLOWED_EVIDENCE_CLASSES)

            if "tier" in source:
                check_enum(source["tier"], f"{path}.tier", ALLOWED_TIERS)

            if "format" in source:
                check_enum(source["format"], f"{path}.format", ALLOWED_FORMATS)

            if "retrieval_mode" in source:
                check_enum(source["retrieval_mode"], f"{path}.retrieval_mode", ALLOWED_RETRIEVAL_MODES)

            if "domain" in source:
                require_non_empty_list(source["domain"], f"{path}.domain")

            if "intended_uses" in source:
                require_non_empty_list(source["intended_uses"], f"{path}.intended_uses")

            if "citation_required" in source:
                require_boolean(source["citation_required"], f"{path}.citation_required")

            # ai_restrictions: free-form named keys, each a non-empty string.
            restrictions = source.get("ai_restrictions")
            if require_mapping(restrictions, f"{path}.ai_restrictions"):
                if len(restrictions) == 0:
                    error(f"{path}.ai_restrictions: must contain at least one restriction")
                for key, value in restrictions.items():
                    require_string(value, f"{path}.ai_restrictions.{key}")

            source_id = source.get("source_id")
            asset_id = source.get("asset_id")

            if is_non_empty_string(source_id):
                if source_id in source_ids:
                    error(f"{path}.source_id: duplicate source_id '{source_id}'")
                source_ids.append(source_id)

            if is_non_empty_string(asset_id):
                if asset_id in asset_ids:
                    error(f"{path}.asset_id: duplicate asset_id '{asset_id}'")
                asset_ids.append(asset_id)

            # Semantic consistency checks
            if source.get("format") == "csv" and source.get("retrieval_mode") != "structured":
                warning(f"{path}: CSV source should normally use retrieval_mode='structured'")

            if source.get("format") == "owl" and source.get("retrieval_mode") != "ontology":
                warning(f"{path}: OWL source should normally use retrieval_mode='ontology'")

            if source.get("evidence_class") == "clinical" and source.get("tier") != "tier_1":
                error(f"{path}: clinical evidence must be tier_1")

            if (
                source.get("evidence_class") == "molecular"
                and source.get("tier") not in ALLOWED_MOLECULAR_TIERS
            ):
                error(f"{path}: molecular evidence must be tier_2 or tier_3")


# ============================================================================
# EXPECTED CURRENT ASSET COUNT
# ============================================================================

if isinstance(sources, list):
    if len(sources) != 12:
        warning(
            "Expected 12 registered AMR sources based on the "
            f"current knowledge base, found {len(sources)}."
        )

EXPECTED_SOURCE_IDS = {
    "eucast_detection_resistance_mechanisms",
    "glass_amr_implementation",
    "glass_testing_coverage_pathogen_antibiotic_blood",
    "glass_testing_coverage_infection_type",
    "glass_resistance_time_series",
    "glass_global_testing_maps_blood",
    "glass_afghanistan_bci_frequency",
    "genome_wode",
    "pubmed_literature",
    "source_attribution",
    "aro",
    "megares",
}

if isinstance(sources, list):
    actual_source_ids = {s.get("source_id") for s in sources if isinstance(s, dict)}
    missing_expected = EXPECTED_SOURCE_IDS - actual_source_ids
    unexpected = actual_source_ids - EXPECTED_SOURCE_IDS

    for source_id in sorted(missing_expected):
        error(f"sources: expected source_id missing: '{source_id}'")

    for source_id in sorted(unexpected):
        warning(f"sources: unexpected source_id present: '{source_id}'")


# ============================================================================
# ROUTING RULES
# ============================================================================

if isinstance(data, dict):
    routing_rules = data.get("routing_rules")

    if require_mapping(routing_rules, "routing_rules"):
        missing_routes = REQUIRED_ROUTES - set(routing_rules.keys())
        for route in sorted(missing_routes):
            error(f"routing_rules: missing required route '{route}'")

        for route_name, route in routing_rules.items():
            path = f"routing_rules.{route_name}"
            if not isinstance(route, dict):
                error(f"{path}: must be a mapping")
                continue
            if "preferred_tiers" not in route:
                error(f"{path}: missing 'preferred_tiers'")


# ============================================================================
# COMPATIBILITY RULES
# ============================================================================

if isinstance(data, dict):
    compatibility_rules = data.get("compatibility_rules")

    if require_mapping(compatibility_rules, "compatibility_rules"):
        missing_rules = REQUIRED_COMPATIBILITY_RULES - set(compatibility_rules.keys())
        for rule in sorted(missing_rules):
            error(f"compatibility_rules: missing required rule '{rule}'")


# ============================================================================
# GENERATION CONTRACT
# ============================================================================

if isinstance(data, dict):
    generation_contract = data.get("generation_contract")

    if require_mapping(generation_contract, "generation_contract"):
        missing = REQUIRED_GENERATION_FIELDS - set(generation_contract.keys())
        for field in sorted(missing):
            error(f"generation_contract: missing required field '{field}'")

        for field in REQUIRED_GENERATION_FIELDS:
            if field in generation_contract:
                require_boolean(generation_contract[field], f"generation_contract.{field}")


# ============================================================================
# FINAL REPORT
# ============================================================================

print()

if warnings:
    print("=" * 70)
    print("WARNINGS")
    print("=" * 70)
    for message in warnings:
        print(f"⚠ {message}")

print()

if errors:
    print("=" * 70)
    print("ERRORS")
    print("=" * 70)
    for message in errors:
        print(f"✗ {message}")
    print()
    print("=" * 70)
    print("❌ VALIDATION FAILED")
    print("=" * 70)
    raise SystemExit(1)

print("=" * 70)
print("VALIDATION SUMMARY")
print("=" * 70)

if isinstance(sources, list):
    print(f"✓ Registered sources : {len(sources)}")

print(f"✓ Errors             : {len(errors)}")
print(f"✓ Warnings           : {len(warnings)}")
print()
print("✓ Registry structure valid")
print("✓ Source entries valid")
print("✓ Tier contract valid")
print("✓ Retrieval-mode contract valid")
print("✓ AI restriction contract valid")
print("✓ Routing rules valid")
print("✓ Compatibility rules valid")
print("✓ Generation contract valid")
print()
print("=" * 70)
print(" VALIDATION PASSED")
print("=" * 70)