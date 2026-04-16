def test_expand_candidates_by_date_then_price():
    from bilibili_ticket.scheduler.priority import expand_candidates

    candidates = expand_candidates(
        date_priority=["2026-05-01", "2026-05-02"],
        price_priority=[680, 480],
    )

    assert candidates == [
        ("2026-05-01", 680),
        ("2026-05-01", 480),
        ("2026-05-02", 680),
        ("2026-05-02", 480),
    ]


def test_filter_out_non_whitelisted_candidates():
    from bilibili_ticket.scheduler.priority import prioritize_available_candidates

    available = [
        ("2026-05-03", 680),
        ("2026-05-02", 680),
        ("2026-05-01", 480),
        ("2026-05-01", 999),
    ]

    prioritized = prioritize_available_candidates(
        available_candidates=available,
        date_priority=["2026-05-01", "2026-05-02"],
        price_priority=[680, 480],
    )

    assert prioritized == [
        ("2026-05-01", 480),
        ("2026-05-02", 680),
    ]
