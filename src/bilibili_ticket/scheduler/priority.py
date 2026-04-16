from __future__ import annotations


def expand_candidates(
    date_priority: list[str],
    price_priority: list[int],
) -> list[tuple[str, int]]:
    return [(date, price) for date in date_priority for price in price_priority]


def prioritize_available_candidates(
    available_candidates: list[tuple[str, int]],
    date_priority: list[str],
    price_priority: list[int],
) -> list[tuple[str, int]]:
    allowed = set(expand_candidates(date_priority, price_priority))
    ordered = expand_candidates(date_priority, price_priority)
    available_set = set(available_candidates)
    return [candidate for candidate in ordered if candidate in allowed and candidate in available_set]
