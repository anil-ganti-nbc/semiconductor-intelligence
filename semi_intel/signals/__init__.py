"""Signal Radar absorption: the sensory pipeline.

Provider collection -> SignalItem (raw, immutable) -> signal analysis
-> SignalCandidate (bounded, attention-scored) -> promotion -> canonical
Evidence/EditorialStory (unchanged, semi_intel.editorial/domain).

This package is a new, separate front end that eventually *produces*
Evidence rows, the same way semi_intel/ingestion/ does today -- it does not
change how Claims, Evidence, or EditorialStory work. See PHASE0_AUDIT.md for
why Signal Radar's own story engine was not ported.
"""
