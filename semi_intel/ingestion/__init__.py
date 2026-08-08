"""Source plugins and the ingestion service that turns their output into
Source/Evidence rows.

Adding a new source means writing one class that implements SourcePlugin --
nothing else in the system needs to change. That's the ingestion-side promise.
It is NOT a promise that entity/claim extraction is free; a new plugin gets
you normalized Evidence rows, not automatically-linked claims. That's still
manual in M1 (see roadmap M2: claim detection).
"""
