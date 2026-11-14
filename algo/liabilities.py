"""algo/liabilities.py — VHH developability liability scanner (T5.1).

Spec v0.1.1 lite · decisions D12 (design), D14 (scope).

PURE function: takes a sequence, returns a list of findings. No DB writes,
no side effects, no affinity/score invented (Utgang §0.7 — we rank and
compare evidence, we never make up an affinity number). Severity is an
ORDINAL label for sorting only.

Design (D12):
  - Aho-Corasick for LITERAL motifs: NG, DG, DP, M, W, and cysteine (C).
  - regex for the WILDCARD motif: N-glycosylation sequon  N[^P][ST].
  - sliding window for PROPERTY RUNS: hydrophobic / positive stretches.

Tuned for RECALL (D12): a missed liability can send a dud into a scarce
Adaptyv slot (fatal); a false positive is cheap. So thresholds lean low
and we flag every cysteine the canonical pair can't absorb.

Scope (D14): the scanner validates the ALPHABET only and raises on a
non-standard sequence (that is a pipeline bug — catch it here). It does
NOT enforce length; the 80-160 aa check lives in Pipeline intake, so a
short test fixture is still scannable.

Callers:
  - Pipeline (T2) developability station calls scan_liabilities() and maps
    each Liability to a `developability`-class metric row. It does NOT
    reimplement the scan.
  - Ranking (T3) reads the counts (see liability_counts); it does not rescan.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import re

__all__ = [
    "Liability",
    "LiabilityConfig",
    "scan_liabilities",
    "liability_counts",
]

# The 20 standard amino acids, uppercase. No gaps / X / * / lowercase.
STANDARD_AA = frozenset("ACDEFGHIKLMNPQRSTVWY")


@dataclass(frozen=True)
class Liability:
    """One liability finding. Positions are 1-based inclusive (D9 positional)."""
    kind: str      # canonical id, e.g. "deamidation_NG", "nglyc_sequon"
    start: int     # 1-based, inclusive
    end: int       # 1-based, inclusive
    motif: str     # the exact matched substring
    severity: str  # "high" | "medium" | "low" — ORDINAL label, never a score
    region: str    # "cdr" | "framework" | "unknown"
    note: str      # plain-language reason


@dataclass(frozen=True)
class LiabilityConfig:
    """Tunable knobs. Defaults lean toward recall (D12)."""
    hydrophobic_set: frozenset = frozenset("AFILMVWY")
    positive_set: frozenset = frozenset("KR")
    hydro_window: int = 7          # flag if >= hydro_min hydrophobic in any window of this size
    hydro_min: int = 5
    pos_window: int = 5            # flag if >= pos_min positive in any window of this size
    pos_min: int = 3
    nglyc_exclude_proline: bool = True   # canonical sequon excludes proline at position +1
    expected_cys_pairs: int = 1    # VHH canonical disulfide C22-C92 = 1 pair


# Literal motif -> (kind, severity, note). "C" is collected, then judged in a
# post-pass (unpaired only), so it is not emitted directly from the automaton.
_LITERAL_MOTIFS = {
    "NG": ("deamidation_NG",   "high",   "Asn-Gly: fast deamidation hotspot"),
    "DG": ("isomerization_DG", "high",   "Asp-Gly: isomerization hotspot"),
    "DP": ("fragmentation_DP", "medium", "Asp-Pro: acid-labile bond, fragmentation"),
    "M":  ("oxidation_M",      "medium", "Met: oxidation-prone"),
    "W":  ("oxidation_W",      "medium", "Trp: oxidation-prone"),
}
_AC_KEYWORDS = tuple(_LITERAL_MOTIFS.keys()) + ("C",)


class _AhoCorasick:
    """Minimal Aho-Corasick multi-pattern matcher over the AA alphabet.

    One automaton finds every literal motif (and every cysteine) in a single
    left-to-right pass, overlaps included. Built once at import, reused for
    every candidate.
    """

    def __init__(self, keywords):
        self._goto = [{}]        # trie transitions per node
        self._out = [set()]      # keywords ending at each node
        self._fail = [0]         # failure links
        for kw in keywords:
            self._add(kw)
        self._build()

    def _add(self, kw):
        node = 0
        for ch in kw:
            nxt = self._goto[node].get(ch)
            if nxt is None:
                nxt = len(self._goto)
                self._goto.append({})
                self._out.append(set())
                self._fail.append(0)
                self._goto[node][ch] = nxt
            node = nxt
        self._out[node].add(kw)

    def _build(self):
        q = deque()
        for nxt in self._goto[0].values():
            self._fail[nxt] = 0
            q.append(nxt)
        while q:
            r = q.popleft()
            for ch, u in self._goto[r].items():
                q.append(u)
                v = self._fail[r]
                while v != 0 and ch not in self._goto[v]:
                    v = self._fail[v]
                fu = self._goto[v].get(ch, 0)
                self._fail[u] = fu if fu != u else 0
                self._out[u] |= self._out[self._fail[u]]

    def find(self, text):
        """Yield (end_index_0based, keyword) for every match."""
        node = 0
        goto, fail, out = self._goto, self._fail, self._out
        for i, ch in enumerate(text):
            while node != 0 and ch not in goto[node]:
                node = fail[node]
            node = goto[node].get(ch, 0)
            if out[node]:
                for kw in out[node]:
                    yield i, kw


_AUTOMATON = _AhoCorasick(_AC_KEYWORDS)


def _region_tagger(cdr_spans):
    """Return a function mapping a 1-based [start,end] to cdr/framework/unknown."""
    if not cdr_spans:
        return lambda s, e: "unknown"
    spans = list(cdr_spans)

    def tag(s, e):
        for cs, ce in spans:
            if s <= ce and e >= cs:   # overlap
                return "cdr"
        return "framework"

    return tag


def _window_runs(seq, members, window, min_count):
    """Sliding-window property runs, merged and trimmed to member boundaries.

    A position is 'in a patch' if it falls in any window of size `window`
    that contains >= `min_count` set members. Qualifying windows are merged,
    then each merged span is trimmed to its first/last member so the reported
    span is the stretch itself, not the padding around it. Returns 1-based
    (start, end) spans.
    """
    n = len(seq)
    if n == 0 or min_count <= 0:
        return []
    is_member = [ch in members for ch in seq]
    marked = [False] * n
    # prefix sums for O(n) window counts
    pref = [0] * (n + 1)
    for i in range(n):
        pref[i + 1] = pref[i] + (1 if is_member[i] else 0)
    for i in range(n):
        j = min(i + window, n)
        if pref[j] - pref[i] >= min_count:
            for k in range(i, j):
                marked[k] = True
    spans = []
    k = 0
    while k < n:
        if not marked[k]:
            k += 1
            continue
        s = k
        while k < n and marked[k]:
            k += 1
        e = k - 1
        # trim to first/last actual member inside [s, e]
        lo = next(p for p in range(s, e + 1) if is_member[p])
        hi = next(p for p in range(e, s - 1, -1) if is_member[p])
        spans.append((lo + 1, hi + 1))  # -> 1-based
    return spans


def scan_liabilities(sequence, cdr_spans=None, config=None):
    """Scan one VHH sequence for developability liabilities.

    Args:
        sequence:  uppercase 20-AA sequence (no gaps / X / *). Length is NOT
                   checked here (D14 — that is Pipeline intake's job).
        cdr_spans: optional list of 1-based inclusive (start, end) CDR ranges.
                   When given, each finding is tagged cdr / framework; when
                   None, every finding is tagged "unknown".
        config:    optional LiabilityConfig; defaults lean toward recall.

    Returns:
        list[Liability], sorted by (start, kind).

    Raises:
        ValueError: on an empty sequence or any non-standard character.
    """
    if config is None:
        config = LiabilityConfig()
    if not sequence:
        raise ValueError("empty sequence")
    bad = sorted({ch for ch in sequence if ch not in STANDARD_AA})
    if bad:
        raise ValueError(
            f"non-standard sequence (expect uppercase 20-AA, no gaps/X/*); "
            f"offending characters: {bad}"
        )

    tag = _region_tagger(cdr_spans)
    findings = []
    cys_positions = []  # 0-based indices of every cysteine

    # 1) literal motifs via Aho-Corasick (+ collect cysteines)
    for end_i, kw in _AUTOMATON.find(sequence):
        if kw == "C":
            cys_positions.append(end_i)
            continue
        start_i = end_i - len(kw) + 1
        kind, sev, note = _LITERAL_MOTIFS[kw]
        findings.append(Liability(
            kind=kind, start=start_i + 1, end=end_i + 1, motif=kw,
            severity=sev, region=tag(start_i + 1, end_i + 1), note=note,
        ))

    # 2) unpaired cysteine (post-pass over the C positions)
    #    canonical capacity = 2 * expected_cys_pairs. Flag every cysteine the
    #    canonical pairs cannot absorb, and on odd parity flag the trailing one.
    cap = 2 * config.expected_cys_pairs
    flagged = set()
    if len(cys_positions) > cap:
        flagged.update(cys_positions[cap:])
    if len(cys_positions) % 2 == 1:
        flagged.add(cys_positions[-1])
    for idx in sorted(flagged):
        findings.append(Liability(
            kind="unpaired_cysteine", start=idx + 1, end=idx + 1, motif="C",
            severity="high", region=tag(idx + 1, idx + 1),
            note="cysteine beyond the canonical pair: free-thiol aggregation / scrambling",
        ))

    # 3) N-glycosylation sequon via regex (overlapping, lookahead)
    x = "[^P]" if config.nglyc_exclude_proline else "."
    sequon = re.compile(f"(?=(N{x}[ST]))")
    for m in sequon.finditer(sequence):
        s = m.start()              # 0-based start
        motif = m.group(1)
        findings.append(Liability(
            kind="nglyc_sequon", start=s + 1, end=s + len(motif), motif=motif,
            severity="medium", region=tag(s + 1, s + len(motif)),
            note="N-x-S/T: potential N-glycosylation site",
        ))

    # 4) property runs via sliding window
    for (s, e) in _window_runs(sequence, config.hydrophobic_set,
                               config.hydro_window, config.hydro_min):
        findings.append(Liability(
            kind="hydrophobic_patch", start=s, end=e, motif=sequence[s - 1:e],
            severity="medium", region=tag(s, e),
            note="hydrophobic stretch: aggregation-prone surface",
        ))
    for (s, e) in _window_runs(sequence, config.positive_set,
                               config.pos_window, config.pos_min):
        findings.append(Liability(
            kind="positive_patch", start=s, end=e, motif=sequence[s - 1:e],
            severity="medium", region=tag(s, e),
            note="positive charge cluster: polyreactivity / PK risk",
        ))

    findings.sort(key=lambda f: (f.start, f.kind))
    return findings


def liability_counts(findings):
    """Convenience: counts per kind plus a total. Pure. For Ranking/Pipeline.

    Returns a dict like {"total": 7, "deamidation_NG": 1, "nglyc_sequon": 1, ...}.
    Counts only; it invents no score.
    """
    counts = {"total": len(findings)}
    for f in findings:
        counts[f.kind] = counts.get(f.kind, 0) + 1
    return counts
