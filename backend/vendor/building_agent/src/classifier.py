"""
spatial_dot_py_fixed.py
=======================
StructureHintClassifier — rewritten so unit_count drives classification,
exactly matching the spec table (section 5.1):

  Class       Units          Land-use codes
  ─────────── ────────────── ──────────────────────
  SFU         1              R1 / RS / 100
  MDU_SMALL   2–4            R2 / 102–103
  MDU_LARGE   ≥5             R4 / RM / 110–115
  MXU         ≥2 + comm      MX / C-R / 120
  ANCHOR      N/A            C2 / I / GV / ED / MED

Root-cause of the zero-MDU bug
-------------------------------
The previous version tested `area < 600 AND elongation < 5` for SFU
*before* any unit-count check.  Because nearly every residential parcel
has a modest footprint and moderate shape, the SFU rule vacuumed up
duplexes and quadplexes that should have been MDU_SMALL, and small
apartment blocks that should have been MDU_LARGE.

Fix: unit_count is checked first.  Geometry (area / elongation) is only
used as a fallback when unit_count is absent from the record.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Land-use / owner-type lookup tables (from spec §5.1)
# ---------------------------------------------------------------------------

# Land-use codes that mean ANCHOR regardless of geometry
ANCHOR_LAND_USE_CODES = {
    # Spec codes
    "C2", "I", "GV", "ED", "MED",
    # Descriptive equivalents
    "GOVERNMENT", "INSTITUTIONAL", "HOSPITAL", "SCHOOL",
    "PLACE_OF_WORSHIP", "CHURCH", "PARK", "INDUSTRIAL",
}

# Owner-type strings that force ANCHOR
ANCHOR_OWNER_TYPES = {"GOV", "EDU", "MED", "RELIG", "CHURCH", "NONPROFIT"}

# Land-use codes that mean MXU (mixed-use)
MXU_LAND_USE_CODES = {"MX", "C-R", "120"}

# SFU land-use codes
SFU_LAND_USE_CODES = {"R1", "RS", "100"}

# MDU_SMALL land-use codes (duplex / triplex / quadplex)
MDU_SMALL_LAND_USE_CODES = {"R2", "102", "103"}

# MDU_LARGE land-use codes (apartment / condo)
MDU_LARGE_LAND_USE_CODES = {"R4", "RM", "110", "111", "112", "113", "114", "115"}

# ---------------------------------------------------------------------------
# Distance thresholds
# ---------------------------------------------------------------------------
DISTANCE_FAR_M = 100   # beyond this → UNRESOLVED

# ---------------------------------------------------------------------------
# Geometry-only fallback thresholds (used when unit_count is None)
# ---------------------------------------------------------------------------
GEOM_SFU_MAX_AREA       = 300   # m² — small footprint → likely SFU
GEOM_MDU_SMALL_MAX_AREA = 700   # m² — medium footprint → MDU_SMALL candidate
GEOM_MDU_LARGE_MIN_AREA = 700   # m² — large footprint  → MDU_LARGE candidate
GEOM_ANCHOR_MIN_AREA    = 1200  # m² — very large + low unit density → ANCHOR
GEOM_MDU_ELONG_MIN      = 3.5   # elongation typical of apartment blocks


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

class StructureHintClassifier:

    def __init__(self, config: dict):
        self.config     = config
        self.thresholds = config.get("thresholds", {})
        self.geom_mdu_elong_min = float(
            self.thresholds.get("elongation_mdu_threshold", GEOM_MDU_ELONG_MIN)
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get(d: dict | None, key: str, default=None):
        """Safe dict getter that handles None dicts."""
        if d is None:
            return default
        return d.get(key, default)

    @staticmethod
    def _norm(value) -> str:
        """Upper-case string, empty string when None."""
        return str(value or "").strip().upper()

    # ------------------------------------------------------------------
    # Confidence scoring
    # ------------------------------------------------------------------

    def compute_confidence(
        self,
        area: float,
        elongation: float,
        distance_m: float | None,
        structure: str,
    ) -> int:

        # Area score
        if structure == "SFU":
            area_score = 1.0 - abs(area - 200) / max(200, 1)
        elif structure == "MDU_SMALL":
            area_score = min(area / 500, 1.0)
        elif structure == "MDU_LARGE":
            area_score = min(area / 900, 1.0)
        elif structure == "MXU":
            area_score = min(area / 800, 1.0)
        else:  # ANCHOR
            area_score = min(area / 1200, 1.0)

        area_score = max(min(area_score, 1.0), 0.2)

        # Shape score
        if elongation < 2.5:
            shape_score = 1.0
        elif elongation < 3.5:
            shape_score = 0.8
        else:
            shape_score = 0.6

        # Distance score
        if distance_m is None:
            distance_score = 0.5
        elif distance_m <= 10:
            distance_score = 1.0
        elif distance_m <= 20:
            distance_score = 0.85
        elif distance_m <= 50:
            distance_score = 0.70
        elif distance_m <= 100:
            distance_score = 0.55
        else:
            distance_score = 0.40

        score = area_score * 0.4 + shape_score * 0.3 + distance_score * 0.3
        return int(score * 100)

    # ------------------------------------------------------------------
    # Main classify
    # ------------------------------------------------------------------

    def classify(self, features: dict, assessor: dict | None = None) -> dict:

        area       = features.get("footprint_area_m2", 0)
        elongation = features.get("elongation_ratio", 0)
        distance_m = features.get("footprint_match_distance_m", None)
        unit_count = features.get("unit_count", None)
        min_area   = self.thresholds.get("min_area_valid_m2", 40)

        # ── Rule 0: invalid footprint ─────────────────────────────────
        if area < min_area:
            return {
                "structure_hint":  "UNRESOLVED",
                "hint_confidence": 0,
                "hint_signals":    ["invalid_area"],
            }

        # ── Rule 1: too far from any building ─────────────────────────
        if distance_m is not None and distance_m > DISTANCE_FAR_M:
            return {
                "structure_hint":  "UNRESOLVED",
                "hint_confidence": 30,
                "hint_signals":    ["far_from_building"],
            }

        # ── Extract assessor signals ──────────────────────────────────
        owner_type = self._norm(self._get(assessor, "owner_type"))
        land_use   = self._norm(self._get(assessor, "land_use"))
        bldg_use   = self._norm(self._get(assessor, "building_use"))
        is_commercial = self._get(assessor, "has_commercial_space", False)

        # ── Rule 2: Assessor → ANCHOR ─────────────────────────────────
        if (owner_type in ANCHOR_OWNER_TYPES
                or land_use  in ANCHOR_LAND_USE_CODES
                or bldg_use  in ANCHOR_LAND_USE_CODES):
            structure = "ANCHOR"
            signals   = ["assessor_anchor_signal"]

        # ── Rule 3: Assessor land-use → MXU ──────────────────────────
        elif land_use in MXU_LAND_USE_CODES or bldg_use in MXU_LAND_USE_CODES:
            structure = "MXU"
            signals   = ["mxu_land_use"]

        # ── Rule 4: unit_count present — this is the PRIMARY axis ─────
        elif unit_count is not None:
            structure, signals = self._classify_by_units(
                unit_count, is_commercial, area, land_use, bldg_use
            )

        # ── Rule 5: No unit_count — fall back to geometry ─────────────
        else:
            structure, signals = self._classify_by_geometry(
                area, elongation, land_use, bldg_use
            )

        confidence = self.compute_confidence(area, elongation, distance_m, structure)

        return {
            "structure_hint":  structure,
            "hint_confidence": confidence,
            "hint_signals":    signals,
        }

    # ------------------------------------------------------------------
    # Unit-count classification (primary path)
    # ------------------------------------------------------------------

    def _classify_by_units(
        self,
        unit_count: int,
        is_commercial: bool,
        area: float,
        land_use: str,
        bldg_use: str,
    ) -> tuple[str, list[str]]:

        # MXU: mixed-use (residential units + commercial floor)
        if unit_count >= 2 and is_commercial:
            return "MXU", ["unit_count_commercial_mix"]

        # SFU: exactly 1 unit
        if unit_count == 1:
            return "SFU", ["unit_count_1"]

        # MDU_SMALL: 2–4 units
        if 2 <= unit_count <= 4:
            return "MDU_SMALL", ["unit_count_2_to_4"]

        # MDU_LARGE: 5+ units
        # Exception: very large footprint with low unit density could be a
        # misreported church / school — but at 5+ units we trust the data.
        if unit_count >= 5:
            # Additional guard: if area is huge and unit density is extremely
            # low (< 1 unit per 200 m²), it may be a non-residential building
            # with erroneous unit data → classify as ANCHOR to be safe.
            if area > 1200 and (area / unit_count) > 200:
                return "ANCHOR", ["large_area_low_unit_density"]
            return "MDU_LARGE", ["unit_count_gte_5"]

        # Shouldn't reach here, but just in case
        return "SFU", ["unit_count_fallback"]

    # ------------------------------------------------------------------
    # Geometry-only fallback (when unit_count is absent)
    # ------------------------------------------------------------------

    def _classify_by_geometry(
        self,
        area: float,
        elongation: float,
        land_use: str,
        bldg_use: str,
    ) -> tuple[str, list[str]]:

        # Land-use code shortcuts (even without owner_type check)
        if land_use in SFU_LAND_USE_CODES or bldg_use in SFU_LAND_USE_CODES:
            return "SFU", ["land_use_sfu"]
        if land_use in MDU_SMALL_LAND_USE_CODES or bldg_use in MDU_SMALL_LAND_USE_CODES:
            return "MDU_SMALL", ["land_use_mdu_small"]
        if land_use in MDU_LARGE_LAND_USE_CODES or bldg_use in MDU_LARGE_LAND_USE_CODES:
            return "MDU_LARGE", ["land_use_mdu_large"]

        # Very large footprint without unit data → cautiously ANCHOR
        if area >= GEOM_ANCHOR_MIN_AREA:
            return "ANCHOR", ["large_footprint_no_units"]

        elong_thresh = self.geom_mdu_elong_min

        # Large footprint → apartment-scale (avoid mislabeling long blocks as SFU)
        if area >= GEOM_MDU_LARGE_MIN_AREA:
            if elongation >= elong_thresh:
                return "MDU_LARGE", ["geometry_large_elongated"]
            if elongation >= 2.3:
                return "MDU_LARGE", ["geometry_large_moderate_elongation"]
            return "MDU_SMALL", ["geometry_large_compact"]

        # Medium strip / row buildings (elongated but below old 3.5 threshold)
        if area > GEOM_SFU_MAX_AREA:
            if elongation >= elong_thresh:
                return "MDU_SMALL", ["geometry_medium_elongated"]
            if elongation >= 2.5 and area >= 400:
                return "MDU_SMALL", ["geometry_moderate_elongation"]
            if elongation >= 2.4 and area >= 500:
                return "MDU_SMALL", ["geometry_strip_block"]

        # Small compact footprint → SFU
        if area <= GEOM_SFU_MAX_AREA:
            return "SFU", ["geometry_small_compact"]

        # Medium area, low elongation → ranch / bungalow
        return "SFU", ["geometry_default_sfu"]

    # ------------------------------------------------------------------
    # Batch
    # ------------------------------------------------------------------

    def classify_batch(self, records: list[dict]) -> list[dict]:
        """
        Each record may contain an optional 'assessor' key.

        Example record::

            {
                "footprint_area_m2": 320,
                "elongation_ratio": 1.8,
                "footprint_match_distance_m": 12,
                "unit_count": 3,
                "assessor": {"owner_type": "EDU", "land_use": "R2"}
            }
        """
        results = []
        for r in records:
            features = {k: v for k, v in r.items() if k != "assessor"}
            assessor = r.get("assessor", None)
            results.append(self.classify(features, assessor))
        return results


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    config = {"thresholds": {"min_area_valid_m2": 40}}
    clf    = StructureHintClassifier(config)

    test_cases = [
        # ── Unit-count driven (primary path) ──────────────────────────
        {
            "label": "SFU — unit_count=1",
            "footprint_area_m2": 210, "elongation_ratio": 1.6,
            "footprint_match_distance_m": 12, "unit_count": 1,
            "expected": "SFU",
        },
        {
            "label": "MDU_SMALL — unit_count=2 (duplex)",
            "footprint_area_m2": 300, "elongation_ratio": 2.0,
            "footprint_match_distance_m": 8, "unit_count": 2,
            "expected": "MDU_SMALL",
        },
        {
            "label": "MDU_SMALL — unit_count=4 (quadplex)",
            "footprint_area_m2": 480, "elongation_ratio": 3.2,
            "footprint_match_distance_m": 6, "unit_count": 4,
            "expected": "MDU_SMALL",
        },
        {
            "label": "MDU_LARGE — unit_count=5",
            "footprint_area_m2": 750, "elongation_ratio": 4.5,
            "footprint_match_distance_m": 5, "unit_count": 5,
            "expected": "MDU_LARGE",
        },
        {
            "label": "MDU_LARGE — unit_count=40, apartment block",
            "footprint_area_m2": 1100, "elongation_ratio": 5.2,
            "footprint_match_distance_m": 5, "unit_count": 40,
            "expected": "MDU_LARGE",
        },
        {
            "label": "MXU — unit_count=6 + commercial",
            "footprint_area_m2": 900, "elongation_ratio": 2.8,
            "footprint_match_distance_m": 4, "unit_count": 6,
            "assessor": {"has_commercial_space": True},
            "expected": "MXU",
        },
        # ── Assessor-driven ───────────────────────────────────────────
        {
            "label": "ANCHOR — church via assessor land_use",
            "footprint_area_m2": 1400, "elongation_ratio": 2.3,
            "footprint_match_distance_m": 8, "unit_count": 8,
            "assessor": {"land_use": "PLACE_OF_WORSHIP"},
            "expected": "ANCHOR",
        },
        {
            "label": "ANCHOR — government owner_type",
            "footprint_area_m2": 2000, "elongation_ratio": 1.5,
            "footprint_match_distance_m": 10, "unit_count": None,
            "assessor": {"owner_type": "GOV"},
            "expected": "ANCHOR",
        },
        # ── Geometry fallback (no unit_count) ─────────────────────────
        {
            "label": "SFU — geometry fallback, small compact",
            "footprint_area_m2": 220, "elongation_ratio": 1.7,
            "footprint_match_distance_m": 22, "unit_count": None,
            "expected": "SFU",
        },
        {
            "label": "MDU_LARGE — geometry fallback, large+elongated",
            "footprint_area_m2": 950, "elongation_ratio": 4.8,
            "footprint_match_distance_m": 7, "unit_count": None,
            "expected": "MDU_LARGE",
        },
        {
            "label": "MDU_LARGE — geometry fallback, large+moderate elongation (Cedarbrook-like)",
            "footprint_area_m2": 750, "elongation_ratio": 2.5,
            "footprint_match_distance_m": 8, "unit_count": None,
            "expected": "MDU_LARGE",
        },
        {
            "label": "MDU_SMALL — geometry fallback, medium strip wing",
            "footprint_area_m2": 450, "elongation_ratio": 2.6,
            "footprint_match_distance_m": 10, "unit_count": None,
            "expected": "MDU_SMALL",
        },
        {
            "label": "ANCHOR — geometry fallback, huge footprint no units",
            "footprint_area_m2": 1400, "elongation_ratio": 2.0,
            "footprint_match_distance_m": 8, "unit_count": None,
            "expected": "ANCHOR",
        },
        # ── Edge / regression ─────────────────────────────────────────
        {
            "label": "UNRESOLVED — GPS too far",
            "footprint_area_m2": 250, "elongation_ratio": 1.5,
            "footprint_match_distance_m": 150, "unit_count": 1,
            "expected": "UNRESOLVED",
        },
        {
            "label": "Image1 regression — SFU Maple St (was UNRESOLVED)",
            "footprint_area_m2": 210, "elongation_ratio": 1.6,
            "footprint_match_distance_m": 22, "unit_count": 1,
            "expected": "SFU",
        },
        {
            "label": "Image2 regression — SFU Lafayette St (was UNRESOLVED)",
            "footprint_area_m2": 280, "elongation_ratio": 2.1,
            "footprint_match_distance_m": 30, "unit_count": 1,
            "expected": "SFU",
        },
        {
            "label": "Image3 regression — Church Armory Dr (was MDU_LARGE)",
            "footprint_area_m2": 1400, "elongation_ratio": 2.3,
            "footprint_match_distance_m": 8, "unit_count": 8,
            "assessor": {"land_use": "PLACE_OF_WORSHIP"},
            "expected": "ANCHOR",
        },
    ]

    passed = 0
    failed = 0

    print(f"\n{'─'*80}")
    print(f"{'LABEL':<50} {'EXPECTED':<12} {'GOT':<12} {'CONF':>5}  {'OK?'}")
    print(f"{'─'*80}")

    for tc in test_cases:
        label    = tc.pop("label")
        expected = tc.pop("expected")
        assessor = tc.pop("assessor", None)
        result   = clf.classify(tc, assessor)
        hint     = result["structure_hint"]
        ok       = "✓" if hint == expected else "✗ FAIL"
        if hint == expected:
            passed += 1
        else:
            failed += 1
        print(
            f"{label:<50} {expected:<12} {hint:<12} "
            f"{result['hint_confidence']:>4}%  {ok}"
        )

    print(f"{'─'*80}")
    print(f"Passed: {passed}/{passed+failed}\n")