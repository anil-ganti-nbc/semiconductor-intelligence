"""Source trust scoring: which leakers are actually reliable, and for whom.

Needs claim-resolution history to mean anything -- a source's evidence only
scores once the claims it weighed in on get resolved (confirmed/debunked)
via `claim resolve`. No AI, no smoothing, just counts, on purpose: a
misleadingly precise "87.3% accurate" number from 3 data points would be
worse than an honest "3/3, too small a sample to trust yet."
"""
