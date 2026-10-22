from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from finance_engine.schedule import (
    Event,
    Interval,
    clip,
    count_slots,
    easter,
    find_conflicts,
    is_night,
    local_day,
    local_month,
    merge,
    national_holidays,
    rest_violations,
    subtract,
    total_minutes,
)

SP = ZoneInfo("America/Sao_Paulo")


def dt(h, m=0, day=5):
    return datetime(2026, 10, day, h, m, tzinfo=SP)


def iv(h1, h2, day=5):
    return Interval(dt(h1, day=day), dt(h2, day=day))


def ev(id, type, h1, h2, day=5, **kw):
    return Event(id, type, dt(h1, day=day), dt(h2, day=day), **kw)


def test_naive_datetimes_rejected():
    with pytest.raises(ValueError):
        Interval(datetime(2026, 1, 1), datetime(2026, 1, 2))


def test_merge_and_subtract():
    assert merge([iv(8, 10), iv(9, 12), iv(14, 15)]) == [iv(8, 12), iv(14, 15)]
    assert subtract([iv(8, 18)], [iv(12, 13), iv(9, 10)]) == [iv(8, 9), iv(10, 12), iv(13, 18)]
    assert subtract([iv(8, 12)], [iv(7, 13)]) == []
    assert total_minutes([iv(8, 10), iv(9, 11)]) == 180  # sem dupla contagem


def test_slots_do_not_span_gaps():
    # 08–12 com bloqueio 10–10:30: 08–10 (3 slots de 40) + 10:30–12 (2 slots) = 5
    free = subtract([iv(8, 12)], [Interval(dt(10), dt(10, 30))])
    assert count_slots(free, 40) == 3 + 2
    # o "total/40" ingênuo daria 210/40 = 5 também; com 60 min a diferença aparece:
    assert count_slots(free, 60) == 2 + 1
    assert total_minutes(free) // 60 == 3
    with pytest.raises(ValueError):
        count_slots(free, 0)


def test_clip_to_month():
    m = local_month(date(2026, 10, 1), SP)
    inside = clip([Interval(dt(8, day=31), datetime(2026, 11, 1, 3, tzinfo=SP))], m)
    assert len(inside) == 1 and inside[0].end == m.end
    assert local_day(date(2026, 10, 5), SP).minutes == 24 * 60


def test_hard_conflict_between_consult_and_shift():
    c = find_conflicts([ev("a", "SHIFT", 7, 19), ev("b", "CONSULT_NEW", 10, 11)])
    assert [(x.kind, x.overlap_minutes) for x in c] == [("HARD", 60)]


def test_back_to_back_is_not_conflict():
    assert find_conflicts([ev("a", "CONSULT_NEW", 8, 9), ev("b", "CONSULT_NEW", 9, 10)]) == []


def test_on_call_overlap_requires_explicit_permission():
    base = [ev("s", "SHIFT", 7, 19)]
    assert find_conflicts(base + [ev("o", "ON_CALL", 8, 20)])[0].kind == "HARD"
    assert (
        find_conflicts(base + [ev("o", "ON_CALL", 8, 20, allow_overlap=True)])[0].kind
        == "CONDITIONAL"
    )
    # dois sobreavisos nunca coexistem, nem com permissão
    two = [
        ev("o1", "ON_CALL", 8, 20, allow_overlap=True),
        ev("o2", "ON_CALL", 9, 10, allow_overlap=True),
    ]
    assert find_conflicts(two)[0].kind == "HARD"
    # bloqueio nunca é compatível
    blk = [ev("o", "ON_CALL", 8, 20, allow_overlap=True), ev("b", "BLOCK", 9, 10)]
    assert find_conflicts(blk)[0].kind == "HARD"


def test_rest_violation_after_24h_shift():
    shift = Event("s", "SHIFT", dt(8), dt(8, day=6), recovery_minutes=11 * 60)
    early = ev("c", "CONSULT_NEW", 14, 15, day=6)  # 6h depois
    late = ev("d", "CONSULT_NEW", 20, 21, day=6)  # 12h depois
    v = rest_violations([shift, early, late])
    assert [(x.next_id, x.gap_minutes, x.required_minutes) for x in v] == [("c", 360, 660)]


def test_on_call_and_breaks_do_not_trigger_rest_violation():
    shift = Event("s", "SHIFT", dt(8), dt(20), recovery_minutes=600)
    assert rest_violations([shift, ev("o", "ON_CALL", 21, 23)]) == []
    assert rest_violations([Event("s", "SHIFT", dt(8), dt(20))]) == []


def test_is_night_uses_local_time():
    assert is_night(ev("n", "SHIFT", 19, 23), SP)
    assert is_night(Event("n", "SHIFT", dt(19), dt(7, day=6)), SP)
    assert not is_night(ev("d", "SHIFT", 7, 19), SP)
    # 00:30 UTC é 21:30 em São Paulo: ainda não é noite
    utc = Event(
        "u",
        "SHIFT",
        datetime(2026, 10, 6, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 6, 0, 30, tzinfo=UTC),
    )
    assert not is_night(utc, SP)
    assert is_night(
        Event("u", "SHIFT", utc.start + timedelta(hours=1), utc.start + timedelta(hours=2)), SP
    )


@pytest.mark.parametrize(
    "year,expected",
    [(2024, date(2024, 3, 31)), (2025, date(2025, 4, 20)), (2026, date(2026, 4, 5))],
)
def test_easter(year, expected):
    assert easter(year) == expected


def test_national_holidays():
    h = dict(national_holidays(2026))
    assert h[date(2026, 4, 3)] == "Sexta-feira Santa"
    assert date(2026, 11, 20) in h and date(2023, 11, 20) not in dict(national_holidays(2023))
    assert len(h) == 10
