import datetime as dt
import json
from types import SimpleNamespace

import pytest
from fakes import FakeLLM, trigram_vector

from ipalpha.agents.analyst import Evidence, ThesisDraft, check_citations
from ipalpha.agents.chat_graph import cited_ids, company_terms, find_companies, lookback_days
from ipalpha.agents.language import detect_language
from ipalpha.agents.mapper import Candidate, Disambiguation, TermIndex, disambiguate, triage
from ipalpha.agents.normalize import PostAnalysis, TranslatedPost, normalize_text
from ipalpha.agents.tools import DocHit
from ipalpha.api.routers.chat import sse
from ipalpha.db.models import Company, IpOwnership
from ipalpha.ingestion.matching import IpMatcher, IpRef
from ipalpha.llm import LLM, StructuredOutputError

# --- LLM client: validate-and-repair -------------------------------------------------------


class _ScriptedClient:
    """Mimics openai.OpenAI's chat.completions.create, returning canned contents in order."""

    def __init__(self, contents: list[str]):
        self.contents = contents
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        content = self.contents[len(self.requests) - 1]
        usage = SimpleNamespace(total_tokens=7)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=usage
        )


def _llm(client: _ScriptedClient) -> LLM:
    return LLM(
        base_url="x", api_key="x", chat_model="m", embedding_model="e", timeout_s=1, client=client
    )


def test_structured_repairs_invalid_output():
    good = {"ip_mentions": [], "event_type": "other", "sentiment": 0.5, "summary": "ok"}
    client = _ScriptedClient([json.dumps({**good, "sentiment": 3}), json.dumps(good)])
    result = _llm(client).structured([{"role": "user", "content": "hi"}], PostAnalysis)

    assert result.value.sentiment == 0.5
    assert result.attempts == 2 and result.tokens == 14
    repair = client.requests[1]["messages"][-1]["content"]
    assert "sentiment" in repair and "failed validation" in repair
    schema = client.requests[0]["response_format"]["json_schema"]
    assert schema["name"] == "PostAnalysis" and "sentiment" in schema["schema"]["properties"]


def test_structured_gives_up_after_repairs():
    client = _ScriptedClient(["not json"] * 3)
    with pytest.raises(StructuredOutputError):
        _llm(client).structured([{"role": "user", "content": "hi"}], PostAnalysis)
    assert len(client.requests) == 3


# --- Normalization -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "lang"),
    [
        ("「薬屋のひとりごと」第3期決定！", "ja"),
        ("Elden Ring DLC is amazing", "en"),
        ("AC6", "en"),
        ("엘든링 재밌다 진짜 최고의 게임", "ko"),
        ("", "en"),
    ],
)
def test_detect_language(text, lang):
    assert detect_language(text) == lang


def test_normalize_asks_for_translation_only_when_not_english():
    analysis = dict(ip_mentions=["Elden Ring"], event_type="discussion", sentiment=0.1, summary="s")
    llm = FakeLLM(
        {
            PostAnalysis: lambda _: PostAnalysis(**analysis),
            TranslatedPost: lambda _: TranslatedPost(**analysis, text_en="translated"),
        }
    )
    normalize_text(llm, "Elden Ring is fun", "en")
    normalize_text(llm, "エルデンリング楽しい", "ja")

    assert [schema for schema, _ in llm.calls] == [PostAnalysis, TranslatedPost]
    system = llm.calls[1][1][0]["content"]
    assert "アニメ化" in system  # the glossary travels with every request
    assert llm.calls[1][1][-1]["content"].endswith("エルデンリング楽しい")


# --- Mapper --------------------------------------------------------------------------------

ELDEN = IpRef(1, "elden-ring", "Elden Ring", ("Elden Ring", "エルデンリング"))
ZELDA = IpRef(2, "zelda", "The Legend of Zelda", ("Zelda", "The Legend of Zelda"))


def test_term_index_ranks_ips_by_best_term():
    llm = FakeLLM()
    index = TermIndex.build([ELDEN, ZELDA], llm)
    candidates = index.candidates(trigram_vector("elden ring"))
    assert [c.ip.slug for c in candidates] == ["elden-ring", "zelda"]
    assert candidates[0].similarity == pytest.approx(1.0)


def test_triage_thresholds():
    strong = [Candidate(ELDEN, 0.9), Candidate(ZELDA, 0.6)]
    middling = [Candidate(ELDEN, 0.7), Candidate(ZELDA, 0.55), Candidate(ZELDA, 0.2)]
    assert triage(strong, accept=0.85, review=0.5) == ("accept", strong[:1])
    assert triage(middling, accept=0.85, review=0.5) == ("ask", middling[:2])
    assert triage([Candidate(ELDEN, 0.3)], accept=0.85, review=0.5) == ("reject", [])


@pytest.mark.parametrize(("choice", "expected"), [(2, ZELDA), (0, None), (7, None)])
def test_disambiguate_maps_choice_to_candidate(choice, expected):
    llm = FakeLLM(
        {Disambiguation: lambda _: Disambiguation(reading="Zelda", reason="r", choice=choice)}
    )
    shortlist = [Candidate(ELDEN, 0.7), Candidate(ZELDA, 0.6)]
    labels = {1: "Elden Ring (game)", 2: "The Legend of Zelda (game)"}
    assert disambiguate(llm, "Zelda TotK", "summary", shortlist, labels) == expected
    prompt = llm.calls[0][1][-1]["content"]
    assert "1. Elden Ring (game)" in prompt and "0. None of these" in prompt


# --- Citations -----------------------------------------------------------------------------


def _doc(doc_id: int) -> DocHit:
    return DocHit(
        id=doc_id,
        source="reddit",
        url=f"https://example.com/{doc_id}",
        published_at=dt.datetime(2026, 10, 30, tzinfo=dt.UTC),
        ip_name="Elden Ring",
        text="text",
        summary=None,
        sentiment=0.5,
        event_type="dlc_or_update",
    )


def test_thesis_citations_must_reference_retrieved_documents():
    draft = ThesisDraft(
        headline="h",
        stance="hype_ahead_of_price",
        claim="c",
        evidence=[Evidence(document_id=1, point="p"), Evidence(document_id=99, point="made up")],
        risks=["r"],
        confidence=0.4,
    )
    valid, invalid = check_citations(draft, [_doc(1), _doc(2)])
    assert [e["document_id"] for e in valid] == [1]
    assert valid[0]["url"] == "https://example.com/1"
    assert invalid == [99]


def test_chat_citation_parsing():
    assert cited_ids("Up on DLC news [12], see also [3, 12] and [7].") == [12, 3, 7]
    assert cited_ids("no citations") == []
    assert cited_ids("driven by fan activity [7-10] and [9]") == [7, 8, 9, 10]


# --- Chat understanding --------------------------------------------------------------------


def _company(ticker: str, name: str, name_ja: str, owned: dict[int, float]) -> Company:
    company = Company(ticker=ticker, name=name, name_ja=name_ja, exchange="TSE", currency="JPY")
    company.ownerships = [
        IpOwnership(ip_id=i, weight=w, role="publisher") for i, w in owned.items()
    ]
    return company


COMPANIES = [
    _company("9468.T", "KADOKAWA Corporation", "KADOKAWA", {1: 0.6}),
    _company("7974.T", "Nintendo Co., Ltd.", "任天堂", {2: 1.0}),
    _company("6758.T", "Sony Group Corporation", "ソニーグループ", {1: 0.1}),
    _company("9684.T", "Square Enix Holdings Co., Ltd.", "スクウェア・エニックス", {}),
]


def test_company_terms_strip_legal_suffixes():
    assert {"Sony", "6758", "ソニー"} <= company_terms(COMPANIES[2])
    assert "Square Enix" in company_terms(COMPANIES[3])


@pytest.mark.parametrize(
    ("question", "tickers"),
    [
        ("Why is Kadokawa up 4% today?", ["9468.T"]),
        ("任天堂の株はなぜ下がった？", ["7974.T"]),
        ("how is sony doing vs square enix", ["6758.T", "9684.T"]),
        ("What's going on with 9684?", ["9684.T"]),
        ("Is Elden Ring hype moving any stock?", ["9468.T"]),  # via the largest owner
        ("What is the market doing?", []),
    ],
)
def test_find_companies(question, tickers):
    matcher = IpMatcher([ELDEN, ZELDA])
    assert find_companies(question, COMPANIES, matcher) == tickers


def test_lookback_days():
    assert lookback_days("Why is Kadokawa up today?") == 3
    assert lookback_days("How did Capcom do this month?") == 30
    assert lookback_days("Anything new on Sony?") == 7


def test_sse_frame():
    frame = sse({"type": "token", "text": "上昇"})
    assert frame == 'event: token\ndata: {"type": "token", "text": "上昇"}\n\n'
