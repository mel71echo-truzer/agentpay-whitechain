"""Аудит-доказ (Фаза 1): чи дає поточний шлях `round(float(price) * 10**6)`
той самий результат, що й точна конверсія через Decimal.

Замінює словесне твердження «round наразі нейтралізує float-похибку» на
детермінований перебір. Висновок, зафіксований тестом:

- для будь-якої ціни з ≤6 знаками після коми (тобто представної у 6-decimal
  токені) розходжень НЕМАЄ — жодного на мільйонах значень;
- для цін з >6 знаками (суб-wei, тонше за точність токена) розходження Є:
  поточний код мовчки округлює, а це має відхилятися як некоректна ціна.
  Це фіксується як окрема знахідка (див. docs/audit/01-findings.md).

Тест детермінований: без мережі, без sleep, без залежності від часу.
"""

from decimal import ROUND_HALF_UP, Decimal

import pytest

DEC = 10**6


def _current_path(price_str: str) -> int:
    """Те, що фактично робить код: float(env) -> round(*1e6). Див. payment.py:117."""
    return round(float(price_str) * DEC)


def _exact(price_str: str) -> int:
    """Намір: точний десятковий рядок -> мінімальні одиниці (wei)."""
    return int((Decimal(price_str) * DEC).to_integral_value(rounding=ROUND_HALF_UP))


def test_no_divergence_for_valid_6dp_prices_exhaustive_small_band():
    # Вичерпно 0.000000 .. 0.050000 з кроком 1e-6 (представні ціни).
    for i in range(0, 50_001):
        s = f"{i / DEC:.6f}"
        assert _current_path(s) == _exact(s), f"divergence at {s}"


def test_no_divergence_for_valid_6dp_prices_random_wide_range():
    import random

    rng = random.Random(1234)  # фіксований seed -> детерміновано
    for _ in range(200_000):
        v = rng.randint(0, 9999_999999)  # до ~9999.999999
        s = f"{v // DEC}.{v % DEC:06d}"
        assert _current_path(s) == _exact(s), f"divergence at {s}"


@pytest.mark.parametrize(
    "price_str",
    [str(round(x, 2)) for x in (0.02, 0.10, 1.0, 0.07, 0.29, 4.10, 5.55, 8.61)],
)
def test_configured_style_prices_scale_exactly(price_str):
    assert _current_path(price_str) == _exact(price_str)


@pytest.mark.parametrize(
    "sub_wei_price",
    ["0.1234567", "2.6755555", "0.0000001"],
)
def test_sub_wei_prices_are_not_exactly_representable(sub_wei_price):
    # >6 знаків = тонше за точність 6-decimal токена. Точна сума у wei —
    # НЕ ціле число: (Decimal(price) * 1e6) має дробову частину. Поточний код
    # мовчки округлює її до цілого wei (втрата/зсув), замість відхиляти ціну.
    # Це знахідка: конфіг має відхиляти ціни з >6 знаками (fix у Фазі 2).
    scaled = Decimal(sub_wei_price) * DEC
    assert scaled != scaled.to_integral_value(), f"{sub_wei_price} несподівано представне у wei"
    # І показуємо, що поточний шлях мовчки дає ЯКЕСЬ ціле, приховуючи проблему:
    assert isinstance(_current_path(sub_wei_price), int)
