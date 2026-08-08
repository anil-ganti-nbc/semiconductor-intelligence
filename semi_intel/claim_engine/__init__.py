"""The claim engine: turns evidence into human-reviewed *suggestions* about
which claims it might support, weaken, or contradict.

Everything here is rules-based and deterministic, on purpose -- see the
architecture discussion's "rules first, AI second" principle. Nothing in
this package writes a ClaimEvidenceLink directly; it only ever proposes.
A human decides the stance and accepts or rejects via the CLI (`suggest
accept` / `suggest reject`), which is the only path to a real link
(SuggestionRepository.accept in repository/repositories.py).
"""
