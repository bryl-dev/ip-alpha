import datetime as dt
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import respx

from ipalpha.ingestion import http, jikan, oricon, steam
from ipalpha.ingestion.common import guess_lang
from ipalpha.ingestion.matching import IpMatcher, IpRef

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def matcher() -> IpMatcher:
    return IpMatcher(
        [
            IpRef(
                1,
                "re-zero",
                "Re:Zero",
                ("Re:Zero", "ReZero", "Re:ゼロから始める異世界生活", "リゼロ"),
            ),
            IpRef(2, "sword-art-online", "Sword Art Online", ("SAO", "Sword Art Online")),
            IpRef(3, "elden-ring", "Elden Ring", ("Elden Ring", "Shadow of the Erdtree")),
        ]
    )


def test_matcher_word_boundaries_and_case(matcher: IpMatcher):
    assert [i.slug for i in matcher.match("elden ring DLC is huge")] == ["elden-ring"]
    assert [i.slug for i in matcher.match("SAO season 4 announced")] == ["sword-art-online"]
    # Short aliases are case-sensitive and must be whole words.
    assert matcher.match("sao paulo trip") == []
    assert matcher.match("SAOS") == []
    assert [i.slug for i in matcher.match("リゼロ4期決定!")] == ["re-zero"]


def test_guess_lang():
    assert guess_lang("リゼロ4期") == "ja"
    assert guess_lang("Season 4") == "en"


def test_parse_oricon_fixture(matcher: IpMatcher):
    ranking = oricon.parse_ranking((FIXTURES / "oricon_obb.html").read_text(encoding="utf-8"))
    assert ranking.week_start == dt.date(2026, 10, 19)
    assert ranking.week_end == dt.date(2026, 10, 25)
    assert [e.rank for e in ranking.entries] == [1, 2, 3]
    first = ranking.entries[0]
    assert first.publisher == "KADOKAWA"
    assert first.units == 33_656
    # Both Re:Zero volumes are summed into one IP total.
    assert oricon.units_by_ip(ranking, matcher) == {1: 33_656 + 8_000}


def test_parse_oricon_layout_change_raises():
    with pytest.raises(oricon.OriconParseError):
        oricon.parse_ranking("<html><body>maintenance</body></html>")


def test_steam_parsers():
    assert steam.parse_ccu({"response": {"player_count": 1234, "result": 1}}) == 1234
    assert steam.parse_ccu({"response": {"result": 42}}) is None
    summary = steam.parse_review_summary(
        {
            "success": 1,
            "query_summary": {"total_reviews": 200, "total_positive": 150, "total_negative": 50},
        }
    )
    assert summary == {"review_total": 200.0, "review_positive_ratio": 0.75}
    assert steam.parse_steamspy({"positive": 900, "negative": 100}) == {
        "review_total": 1000.0,
        "review_positive_ratio": 0.9,
    }
    assert steam.parse_steamspy({"appid": 1}) is None


def test_mal_official_parser():
    assert jikan.parse_mal_official({"id": 1, "mean": 8.5, "num_list_users": 10}) == {
        "mal_score": 8.5,
        "mal_members": 10.0,
    }


@respx.mock
def test_jikan_fetch_retries_on_429(monkeypatch):
    monkeypatch.setattr(http, "time", SimpleNamespace(sleep=lambda _s: None))
    route = respx.get("https://api.jikan.moe/v4/anime/31240").mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(
                200, json={"data": {"score": 8.2, "members": 2_500_000, "favorites": None}}
            ),
        ]
    )
    with httpx.Client() as client:
        payload = jikan.fetch_anime(client, 31240)
    assert route.call_count == 2
    assert jikan.parse_anime(payload) == {"mal_score": 8.2, "mal_members": 2_500_000.0}
