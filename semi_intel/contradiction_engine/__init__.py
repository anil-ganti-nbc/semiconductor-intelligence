"""Rules-based engineering-consistency checks.

Scoped deliberately narrow for M3: memory configuration validity only (bus
width x chip density x claimed total capacity). Rules-based first, AI
second -- there is no AI anywhere in this package. A check either surfaces a
fact ("this isn't buildable, here's why") or it doesn't; it never changes a
claim's status or confidence on its own. See memory_rules.py for the
arithmetic and service.py for how it's wired into Claim/ClaimEvent.

Future domains (die size vs. node compatibility, power limits, launch
timeline conflicts) get their own rule module here, one at a time, each
proven out the same way this one was -- not a generic "impossibility"
framework guessed at up front.
"""
