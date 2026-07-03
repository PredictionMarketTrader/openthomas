from openthomas.research.news import Article, NewsDesk, build_query, _dedupe


def art(title, **kw):
    defaults = dict(source="reuters.com", published="2026-07-01", url="http://x")
    defaults.update(kw)
    return Article(title=title, **defaults)


class StubRetriever:
    def __init__(self, articles):
        self.articles = articles
        self.calls = 0

    def search(self, query, limit=8):
        self.calls += 1
        return self.articles


class ExplodingRetriever:
    def search(self, query, limit=8):
        raise ConnectionError("down")


def test_build_query_strips_question_scaffold():
    assert build_query("Will the Fed cut rates in September?") == "the Fed cut rates in September"
    assert build_query("Who will win the next presidential election?") == "win the next presidential election"


def test_build_query_caps_terms():
    q = build_query("Will " + " ".join(f"w{i}" for i in range(20)) + "?")
    assert len(q.split()) == 8


def test_dedupe_by_normalized_title():
    articles = [art("Fed Cuts Rates!"), art("fed cuts rates"), art("Something else")]
    assert len(_dedupe(articles)) == 2


def test_desk_merges_and_survives_dead_source():
    desk = NewsDesk(retrievers=[ExplodingRetriever(), StubRetriever([art("A"), art("B")])])
    assert len(desk.search("q")) == 2


def test_desk_caches():
    stub = StubRetriever([art("A")])
    desk = NewsDesk(retrievers=[stub])
    desk.search("q")
    desk.search("q")
    assert stub.calls == 1


def test_brief_formats_markdown():
    desk = NewsDesk(retrievers=[StubRetriever([art("Fed holds steady")])])
    brief = desk.brief("Will the Fed cut rates?")
    assert brief == "- [reuters.com · 2026-07-01] Fed holds steady"


def test_brief_empty_when_no_news():
    desk = NewsDesk(retrievers=[StubRetriever([])])
    assert desk.brief("Will X happen?") == ""
