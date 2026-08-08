"""Rules-based validation of GDDR-family memory configuration claims.

Bus width, per-chip density, and total capacity have to be arithmetically
consistent with at least one real chip-population strategy. This is the
"explain WHY" contradiction detector from the original brief's own example
(384-bit / 16 GB / 16 Gbit ICs -> impossible), scoped deliberately narrow:
GDDR-family memory only, one rule, fully deterministic, no AI involved.

Standard population: one chip per 32 data-bus bits -- the standard for every
GDDR generation from GDDR5 through GDDR6X.

Clamshell population: two chips share the same 32 data-bus bits (each chip
uses half the data lines, mirrored), doubling capacity for the same bus
width. This is a real, common board design (e.g. RTX 4060 Ti 16GB, RTX
3090), not an edge case -- which is exactly why a naive "capacity == bus
width / 32 * density" check would produce false positives. Both are checked.

A claimed total capacity is only consistent if it equals:

    (bus_width_bits / 32) * chip_density_gb            [standard], or
    (bus_width_bits / 32) * chip_density_gb * 2         [clamshell]

Anything else is not buildable with current GDDR packaging as described, and
the claim is flagged with an explanation of what totals WOULD be valid at
that bus width and chip density -- useful for spotting a plausible typo
(e.g. someone meant 24 GB, not 16) as much as a fabricated leak.

Deliberately out of scope for M3: HBM (different stack width entirely),
LPDDR (not chip-per-bus-lane in the same way), die size / node
incompatibility, power limits, launch timeline conflicts. Each is a
separate rule module for a later milestone, not a generalization of this one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

BITS_PER_CHIP_STANDARD = 32  # GDDR5/5X/6/6X data bus width per chip, in bits
GBIT_TO_GB = 1 / 8


@dataclass(frozen=True)
class MemoryConfigCheck:
    is_consistent: bool
    claimed_total_gb: float
    valid_totals_gb: List[float]
    explanation: str


def chip_density_gb(chip_density_gbit: float) -> float:
    return chip_density_gbit * GBIT_TO_GB


def check_memory_configuration(
    bus_width_bits: int,
    chip_density_gbit: float,
    claimed_total_gb: float,
    bits_per_chip: int = BITS_PER_CHIP_STANDARD,
) -> MemoryConfigCheck:
    if bus_width_bits <= 0 or bus_width_bits % bits_per_chip != 0:
        return MemoryConfigCheck(
            is_consistent=False,
            claimed_total_gb=claimed_total_gb,
            valid_totals_gb=[],
            explanation=(
                f"{bus_width_bits}-bit bus is not a positive multiple of {bits_per_chip} bits, "
                f"the standard per-chip data width for GDDR-family memory. No valid GDDR chip "
                f"population exists at this bus width."
            ),
        )

    if chip_density_gbit <= 0:
        return MemoryConfigCheck(
            is_consistent=False,
            claimed_total_gb=claimed_total_gb,
            valid_totals_gb=[],
            explanation=f"{chip_density_gbit:g} Gbit is not a valid chip density.",
        )

    standard_chip_count = bus_width_bits // bits_per_chip
    density_gb = chip_density_gb(chip_density_gbit)

    standard_total = round(standard_chip_count * density_gb, 4)
    clamshell_total = round(standard_total * 2, 4)
    valid_totals = [standard_total, clamshell_total]

    is_consistent = any(abs(claimed_total_gb - v) < 1e-6 for v in valid_totals)

    if is_consistent:
        mode = "standard" if abs(claimed_total_gb - standard_total) < 1e-6 else "clamshell"
        chip_count = standard_chip_count if mode == "standard" else standard_chip_count * 2
        explanation = (
            f"{bus_width_bits}-bit bus with {chip_density_gbit:g} Gbit ({density_gb:g} GB) chips "
            f"gives {claimed_total_gb:g} GB under {mode} population "
            f"({chip_count} chip{'s' if chip_count != 1 else ''} x {density_gb:g} GB"
            f"{' effective, clamshell-doubled' if mode == 'clamshell' else ''}). Consistent."
        )
    else:
        explanation = (
            f"{bus_width_bits}-bit bus with {chip_density_gbit:g} Gbit ({density_gb:g} GB) chips "
            f"supports {standard_total:g} GB (standard, {standard_chip_count} chips) or "
            f"{clamshell_total:g} GB (clamshell, {standard_chip_count * 2} chips) -- not "
            f"{claimed_total_gb:g} GB. This configuration is not physically buildable with "
            f"current GDDR packaging as described."
        )

    return MemoryConfigCheck(
        is_consistent=is_consistent,
        claimed_total_gb=claimed_total_gb,
        valid_totals_gb=valid_totals,
        explanation=explanation,
    )
