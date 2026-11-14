"""algo/tests/test_liabilities.py — recall test for the liability scanner (T5.1).

The core contract (D12): on an all-motif sequence, the scanner must flag
EVERY motif. The gate is `findings >= expected` — zero misses. False
positives are tolerated by design, so we do not assert against extras.

Runs with pytest, or standalone: `python3 algo/tests/test_liabilities.py`.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
from liabilities import (  # noqa: E402
    scan_liabilities,
    liability_counts,
    LiabilityConfig,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# PRIMARY: synthetic all-motif sequence. Ground truth is controlled here —
# every segment is isolated by G separators so positions are unambiguous.
#   GGG NGS GGG DG GGG DP GGG M GGG W GGG C GGG VLIFVA GGG KRRRK GGG
ALL_MOTIF = (
    "GGG" "NGS" "GGG" "DG" "GGG" "DP" "GGG" "M" "GGG" "W" "GGG" "C"
    "GGG" "VLIFVA" "GGG" "KRRRK" "GGG"
)

# (kind, start) that MUST appear. 1-based positions, verified by construction.
EXPECTED = {
    ("deamidation_NG",   4),    # N4 G5
    ("nglyc_sequon",     4),    # N4 G5 S6  (G != P, S in {S,T})
    ("isomerization_DG", 10),   # D10 G11
    ("fragmentation_DP", 15),   # D15 P16
    ("oxidation_M",      20),   # M20
    ("oxidation_W",      24),   # W24
    ("unpaired_cysteine", 28),  # lone C28 -> odd parity
    ("hydrophobic_patch", 32),  # V32..A37
    ("positive_patch",   41),   # K41..K45
}

# SECONDARY recall fixture — the compact illustration noted in Utgang T5.
# Also packs all nine kinds into 22 residues.
FLYER_ILLUSTRATION = "SANGSWDGDPYMCTVLIFVKRR"

# REALISTIC input — the guide's example VHH. Its TWO canonical cysteines must
# NOT trip the unpaired-C rule (Utgang T5 constraint).
EXAMPLE_VHH = (
    "QVQLQESGGGLVQAGGSLRLSCAASGRTFSSYAMGWFRQAPGKEREFVAAISWSGGSTYYTDSVKG"
    "RFTISRDNAKNTVYLQMNSLKPEDTAVYYCAAKWRPLRYSDNPSNSDYNYWGQGTQVTVSS"
)

ALL_NINE_KINDS = {
    "deamidation_NG", "isomerization_DG", "fragmentation_DP",
    "oxidation_M", "oxidation_W", "unpaired_cysteine",
    "nglyc_sequon", "hydrophobic_patch", "positive_patch",
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_all_motif_recall():
    """The recall gate: every expected (kind, start) is flagged. Zero misses."""
    found = {(f.kind, f.start) for f in scan_liabilities(ALL_MOTIF)}
    missing = EXPECTED - found
    assert not missing, f"MISSED liabilities (fatal): {sorted(missing)}"


def test_all_motif_patch_spans():
    """Property runs are trimmed to the stretch itself, not the padding."""
    by_kind = {}
    for f in scan_liabilities(ALL_MOTIF):
        by_kind.setdefault(f.kind, []).append((f.start, f.end))
    assert (32, 37) in by_kind["hydrophobic_patch"], by_kind["hydrophobic_patch"]
    assert (41, 45) in by_kind["positive_patch"], by_kind["positive_patch"]


def test_flyer_illustration_hits_all_nine():
    """The compact 22-mer illustration flags all nine kinds."""
    kinds = {f.kind for f in scan_liabilities(FLYER_ILLUSTRATION)}
    missing = ALL_NINE_KINDS - kinds
    assert not missing, f"illustration missed: {sorted(missing)}"


def test_example_vhh_canonical_cysteines_not_flagged():
    """The two canonical VHH cysteines must NOT be reported as unpaired."""
    assert EXAMPLE_VHH.count("C") == 2
    findings = scan_liabilities(EXAMPLE_VHH)
    assert not [f for f in findings if f.kind == "unpaired_cysteine"]


def test_single_cysteine_is_flagged():
    """Odd parity -> the lone cysteine is unpaired."""
    findings = scan_liabilities("AAACAAA")
    cys = [f for f in findings if f.kind == "unpaired_cysteine"]
    assert len(cys) == 1 and cys[0].start == 4


def test_three_cysteines_flag_the_extra():
    """Two can pair; the third is unpaired."""
    findings = scan_liabilities("CAAACAAAC")  # C at 1,5,9
    cys = sorted(f.start for f in findings if f.kind == "unpaired_cysteine")
    assert cys == [9], cys


def test_nglyc_excludes_proline():
    """N-P-S is NOT a sequon; N-A-S is."""
    assert not [f for f in scan_liabilities("AANPSAA") if f.kind == "nglyc_sequon"]
    assert [f for f in scan_liabilities("AANASAA") if f.kind == "nglyc_sequon"]


def test_region_tagging_with_cdr_spans():
    """A finding inside a CDR span is tagged cdr; outside, framework."""
    # NG at positions 4-5; mark CDR covering 4-6.
    findings = scan_liabilities("GGGNGSGGGMGGG", cdr_spans=[(4, 6)])
    ng = next(f for f in findings if f.kind == "deamidation_NG")
    m = next(f for f in findings if f.kind == "oxidation_M")
    assert ng.region == "cdr"
    assert m.region == "framework"


def test_region_unknown_without_spans():
    findings = scan_liabilities("GGGNGSGGG")
    assert all(f.region == "unknown" for f in findings)


def test_severity_is_ordinal_label_not_a_number():
    """Severity is never a score (Utgang §0.7)."""
    for f in scan_liabilities(ALL_MOTIF):
        assert f.severity in {"high", "medium", "low"}


def test_rejects_non_standard_sequence():
    for bad in ["ACDXYZ", "acgt", "AC-DE", "AC*DE", ""]:
        try:
            scan_liabilities(bad)
        except ValueError:
            continue
        raise AssertionError(f"should have rejected: {bad!r}")


def test_length_not_enforced_by_scanner():
    """D14: short sequences are scannable; length is Pipeline intake's job."""
    findings = scan_liabilities("NG")  # 2 aa
    assert any(f.kind == "deamidation_NG" for f in findings)


def test_counts_helper():
    counts = liability_counts(scan_liabilities(ALL_MOTIF))
    assert counts["total"] == sum(v for k, v in counts.items() if k != "total")
    assert counts["deamidation_NG"] == 1


def test_pure_no_mutation():
    """Same input -> identical output; input unchanged."""
    seq = ALL_MOTIF
    a = scan_liabilities(seq)
    b = scan_liabilities(seq)
    assert a == b
    assert seq == ALL_MOTIF


# ---------------------------------------------------------------------------
# Standalone runner (no pytest needed)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
