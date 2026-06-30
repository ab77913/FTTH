import argparse
import os
from pathlib import Path
from dotenv import load_dotenv
from src.extractors.loader import load_records
from src.core.address_parser import canonicalize
from src.providers.mock_provider import MockSmartyProvider, MockMelissaProvider
from src.providers.smarty_adapter import SmartyProvider
from src.providers.melissa_adapter import MelissaProvider
from src.core.compare import compare_results
from src.core.scoring import choose_result, structure_hint, validation_status
from src.core.output import write_outputs
from src.models.schemas import FinalValidationRecord
from src.utils.logging import get_logger

from src.providers.cache_lookup import AddressCache, generate_cache_key
from src.core.provider_arbitration import provider_arbitration

logger = get_logger(__name__)


def build_providers(use_mock: bool):
    if use_mock:
        return MockSmartyProvider(), MockMelissaProvider()
    return SmartyProvider(), MelissaProvider()


def run(input_path: str, output_path: str, limit: int | None = None, use_mock: bool = True):
    logger.info("Loading input records from %s", input_path)
    raw_records = load_records(input_path)
    if limit:
        raw_records = raw_records[:limit]
    logger.info("Loaded %d candidate address records", len(raw_records))

    smarty_provider, melissa_provider = build_providers(use_mock)
    final_records = []

    # Load cache ONCE at the start of the run
    cache = AddressCache().load()
    logger.info("Cache loaded â€” %d existing entries", cache.stats()["total_entries"])

    for raw in raw_records:

        canonical = canonicalize(raw)

        print("\n========== CANONICAL ==========")
        print(canonical.normalized_full_address)

        cache_key = generate_cache_key(canonical)
        cached = cache.get(cache_key)

        # =========================
        # CACHE HIT â€” no API call
        # =========================
        if cached:
            print("\n========== CACHE HIT ==========")
            logger.debug("Cache hit for: %s", canonical.normalized_full_address)

            score = cached.get("confidence_score", cached.get("score", 0))
            status = cached.get("validation_status") or (
                "AUTO_ACCEPT" if score >= 80
                else "MANUAL_REVIEW" if score >= 40
                else "REJECT"
            )

            final_records.append(FinalValidationRecord(
                source_file=canonical.source_file,
                row_id=canonical.row_id,
                address_id=canonical.address_id,
                raw_address=canonical.raw_address,
                canonical_address=canonical.normalized_full_address,
                # Restore full provider data if available in cache
                smarty_standardized_address=cached.get("smarty_standardized_address"),
                melissa_standardized_address=cached.get("melissa_standardized_address"),
                chosen_standardized_address=cached.get("chosen_standardized_address")
                    or cached.get("standardized_address"),
                smarty_dpv=cached.get("smarty_dpv"),
                melissa_dpv=cached.get("melissa_dpv"),
                smarty_zip_plus_4=cached.get("smarty_zip_plus_4"),
                melissa_zip_plus_4=cached.get("melissa_zip_plus_4"),
                smarty_vacant=cached.get("smarty_vacant"),
                melissa_vacant=cached.get("melissa_vacant"),
                smarty_record_type=cached.get("smarty_record_type"),
                melissa_record_type=cached.get("melissa_record_type"),
                chosen_provider=cached.get("chosen_provider", cached.get("provider", "cache")),
                structure_hint=cached.get("structure_hint", "CACHE_HIT"),
                confidence_score=score,
                validation_status=status,
                exception_reason=cached.get("exception_reason"),
                comparison_reason="Loaded from cache",
            ))
            continue

        # =========================
        # CACHE MISS â€” call APIs
        # =========================
        print("\n========== CACHE MISS â€” calling APIs ==========")
        logger.debug("Cache miss for: %s", canonical.normalized_full_address)

        smarty = smarty_provider.validate(canonical)
        print("\n========== SMARTY ==========")
        print(vars(smarty))

        melissa = melissa_provider.validate(canonical)
        print("\n========== MELISSA ==========")
        print(vars(melissa))

        # =========================
        # COMPARISON
        # =========================
        if smarty.success and melissa.success:
            comparison = compare_results(smarty, melissa)
        else:
            comparison = None

        # =========================
        # PROVIDER ARBITRATION
        # =========================
        chosen, score = provider_arbitration(smarty, melissa)

        hint = structure_hint(chosen, canonical.normalized_full_address)
        status, exception_reason = validation_status(score, chosen, comparison)
        comparison_reason = (
            f"{comparison.conflict_level}: {comparison.reason}"
            if comparison
            else "Both providers failed"
        )

        # =========================
        # SAVE FULL RESULT TO CACHE
        # Store everything needed so future cache hits return complete data.
        # =========================
        cache.set(cache_key, {
            # Identity
            "normalized_full_address": canonical.normalized_full_address,
            # Chosen result
            "chosen_provider": chosen.provider,
            "chosen_standardized_address": chosen.standardized_address,
            "confidence_score": score,
            "validation_status": status,
            "structure_hint": hint,
            "exception_reason": exception_reason,
            # Smarty details
            "smarty_standardized_address": smarty.standardized_address,
            "smarty_dpv": smarty.dpv_match,
            "smarty_zip_plus_4": smarty.zip_plus_4,
            "smarty_vacant": smarty.vacant,
            "smarty_record_type": smarty.record_type,
            "smarty_lat": smarty.latitude,
            "smarty_lon": smarty.longitude,
            "smarty_geocode_precision": smarty.geocode_precision,
            # Melissa details
            "melissa_standardized_address": melissa.standardized_address,
            "melissa_dpv": melissa.dpv_match,
            "melissa_zip_plus_4": melissa.zip_plus_4,
            "melissa_vacant": melissa.vacant,
            "melissa_record_type": melissa.record_type,
            "melissa_lat": melissa.latitude,
            "melissa_lon": melissa.longitude,
        })

        final_records.append(FinalValidationRecord(
            source_file=canonical.source_file,
            row_id=canonical.row_id,
            address_id=canonical.address_id,
            raw_address=canonical.raw_address,
            canonical_address=canonical.normalized_full_address,
            smarty_standardized_address=smarty.standardized_address,
            melissa_standardized_address=melissa.standardized_address,
            chosen_standardized_address=chosen.standardized_address,
            smarty_dpv=smarty.dpv_match,
            melissa_dpv=melissa.dpv_match,
            smarty_zip_plus_4=smarty.zip_plus_4,
            melissa_zip_plus_4=melissa.zip_plus_4,
            smarty_vacant=smarty.vacant,
            melissa_vacant=melissa.vacant,
            smarty_record_type=smarty.record_type,
            melissa_record_type=melissa.record_type,
            chosen_provider=chosen.provider,
            structure_hint=hint,
            confidence_score=score,
            validation_status=status,
            exception_reason=exception_reason,
            comparison_reason=comparison_reason,
        ))

    # Save cache ONCE at end (efficient â€” no per-address file writes)
    cache.save()
    logger.info("Cache saved â€” %d total entries", cache.stats()["total_entries"])
    cache.print_stats()

    paths = write_outputs(final_records, output_path)
    logger.info("Output generated: %s", paths)
    return paths


if __name__ == "__main__":
    load_dotenv()
    parser = argparse.ArgumentParser(description="FTTH Smarty + Melissa Address Validation Agent")
    parser.add_argument("--input", default=os.getenv("INPUT_PATH", "data/input"), help="Input file or folder")
    parser.add_argument("--output", default=os.getenv("OUTPUT_PATH", "outputs"), help="Output folder")
    parser.add_argument("--limit", type=int, default=None, help="Limit records for POC testing")
    parser.add_argument("--real-apis", action="store_true", help="Use real Smarty/Melissa APIs instead of mock mode")
    args = parser.parse_args()
    use_mock = os.getenv("USE_MOCK_PROVIDERS", "true").lower() == "true"
    if args.real_apis:
        use_mock = False
    run(args.input, args.output, args.limit, use_mock)


