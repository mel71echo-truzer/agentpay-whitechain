"""Phase 5 STEP 5 — economics invariant (property-style).

    gross = price_units
    fee   = gross * fee_bps // 10000
    net   = gross - fee
    gross == fee + net           (nothing created or lost)

All integer minimal units; Python ints are unbounded so there is no overflow, and
the human boundary (decimals=6) is never confused with minimal units.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unified.config import fee_split, from_minimal_units, load_unified_config, to_minimal_units  # noqa: E402

AMOUNTS = [1, 2, 99, 100, 999, 10_000, 20_000, 1_000_000, 10**18, 10**30]
FEE_BPS = [0, 1, 25, 50, 100, 500, 1000, 9999, 10000]


@pytest.mark.parametrize("gross", AMOUNTS)
@pytest.mark.parametrize("bps", FEE_BPS)
def test_fee_net_invariant(gross, bps):
    fee, net = fee_split(gross, bps)
    assert fee == gross * bps // 10000        # floor
    assert net == gross - fee
    assert fee + net == gross                 # conservation
    assert 0 <= fee <= gross and 0 <= net <= gross


def test_fee_bps_bounds_rejected():
    for bad in (-1, 10001, 100000):
        with pytest.raises(ValueError):
            fee_split(1000, bad)


def test_decimals_six_and_unit_layers_distinct():
    teurc = load_unified_config(env={}).canonical
    assert teurc.decimals == 6
    # 1 tEURC (human) == 1_000_000 minimal units; they are NOT interchangeable numbers.
    one_human_in_units = to_minimal_units("1", teurc)
    assert one_human_in_units == 1_000_000
    assert from_minimal_units(1, teurc) == "0.000001"      # 1 minimal unit != 1 tEURC
    assert from_minimal_units(1_000_000, teurc) == "1.000000"


def test_roundtrip_minimal_units_preserved():
    teurc = load_unified_config(env={}).canonical
    # Human formatting reuses money.py's Decimal (28-digit precision), so bound the
    # round-trip to realistic token amounts. The integer fee_split invariant above
    # holds for arbitrary ints (incl 10**30); only human display is precision-bounded.
    for units in [1, 2, 99, 100, 999, 10_000, 20_000, 1_000_000, 10**12, 10**18]:
        human = from_minimal_units(units, teurc)
        assert to_minimal_units(human, teurc) == units      # lossless round-trip
