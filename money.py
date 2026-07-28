"""Грошова межа (Фаза 2, рішення №4): єдине місце конверсії людських сум
tEURC ↔ мінімальні одиниці (wei). Усередині системи гроші — ЗАВЖДИ int wei;
float у грошових шляхах не існує. Тут і тільки тут відбувається парсинг
Decimal→int (на межі входу) і форматування int→рядок (на межі виходу).

Чому не float: подвійна точність накопичує похибку при `+=` і дає тихе
округлення цін тонших за точність токена. int wei — точний і симетричний з
on-chain сумою.

Функції чисті (decimals передається явно), тому модуль не імпортує config і
не створює циклів; виклики передають config.TEURC_DECIMALS.
"""

from __future__ import annotations

from decimal import Decimal


def teurc_to_wei(amount, decimals: int) -> int:
    """Людська сума tEURC (Decimal/str/int) → int wei.

    Відхиляє суми, тонші за точність токена (більше ніж `decimals` знаків після
    коми): вони не представні у wei, і тихо округлювати їх — приховувати
    помилку ціни. Приймати float СВІДОМО не дозволяємо (джерело похибки):
    подавай рядок або Decimal.
    """
    if isinstance(amount, float):
        raise TypeError("teurc_to_wei не приймає float (джерело похибки) — подавай str/Decimal/int.")
    d = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    scaled = d * (Decimal(10) ** decimals)
    if scaled != scaled.to_integral_value():
        raise ValueError(
            f"Сума {amount} tEURC має більше за {decimals} десяткових знаків — "
            f"не представна у wei (суб-wei ціни заборонені)."
        )
    return int(scaled)


def wei_to_teurc_str(wei: int, decimals: int) -> str:
    """int wei → людський рядок tEURC (через Decimal, без float).

    Використовується ЛИШЕ на межі показу (відповіді API, логи, demo). Ніколи
    не для арифметики — рахунок завжди в int wei.
    """
    wei = int(wei)
    q = Decimal(1).scaleb(-decimals)  # 10^-decimals
    return str((Decimal(wei) / (Decimal(10) ** decimals)).quantize(q))
