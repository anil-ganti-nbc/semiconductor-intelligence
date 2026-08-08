"""Story scoring: which OPEN claims deserve investigation right now.

Three deterministic, capped, weighted signals -- novelty, corroboration,
momentum -- combined into one inspectable number, the same pattern as every
other scoring module in this platform (confidence, claim-link suggestions,
memory-config contradictions). Deliberately excludes soft/editorial
dimensions from the original brief's wishlist (SEO potential, market
significance, engineering significance, coverage saturation): a rules
engine has no reliable way to approximate those, and a fabricated number
would be worse than not having one. Those stay a human judgment call.
"""
