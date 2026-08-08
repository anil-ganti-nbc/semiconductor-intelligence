"""Pure unit tests for the memory configuration rule -- no database."""

from __future__ import annotations

from semi_intel.contradiction_engine.memory_rules import check_memory_configuration


def test_the_briefs_own_example_is_flagged_as_a_contradiction():
    # 384-bit / 16 GB / 16 Gbit ICs -> impossible, per the original spec.
    result = check_memory_configuration(bus_width_bits=384, chip_density_gbit=16, claimed_total_gb=16)
    assert result.is_consistent is False
    assert result.valid_totals_gb == [24.0, 48.0]
    assert "24" in result.explanation
    assert "48" in result.explanation
    assert "16" in result.explanation


def test_standard_population_is_consistent():
    # RTX 4090-style: 384-bit, 12 chips x 2GB (16 Gbit) = 24GB, standard.
    result = check_memory_configuration(bus_width_bits=384, chip_density_gbit=16, claimed_total_gb=24)
    assert result.is_consistent is True
    assert "standard" in result.explanation


def test_clamshell_population_is_consistent():
    # RTX 4060 Ti 16GB-style: 128-bit, 4 chips standard x 2GB = 8GB, but
    # clamshell doubles to 16GB on the same bus width.
    result = check_memory_configuration(bus_width_bits=128, chip_density_gbit=16, claimed_total_gb=16)
    assert result.is_consistent is True
    assert "clamshell" in result.explanation


def test_bus_width_not_a_multiple_of_32_is_rejected():
    result = check_memory_configuration(bus_width_bits=100, chip_density_gbit=16, claimed_total_gb=16)
    assert result.is_consistent is False
    assert result.valid_totals_gb == []
    assert "32 bits" in result.explanation


def test_non_positive_chip_density_is_rejected():
    result = check_memory_configuration(bus_width_bits=256, chip_density_gbit=0, claimed_total_gb=8)
    assert result.is_consistent is False
    assert result.valid_totals_gb == []


def test_explanation_always_names_the_claimed_total():
    result = check_memory_configuration(bus_width_bits=256, chip_density_gbit=8, claimed_total_gb=99)
    assert "99" in result.explanation
