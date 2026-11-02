from stratum_open.github.api import GitHub, _login


def test_graphql_bot_logins_match_rest_ones_only_for_real_bots():
    assert _login({"login": "github-actions", "__typename": "Bot"}) == "github-actions[bot]"
    assert _login({"login": "stratum-open", "__typename": "User"}) == "stratum-open"  # a person, not the App
    assert _login(None) == ""


def test_review_threads_carry_rest_style_logins(monkeypatch):
    gh = GitHub("t", "acme/widget")
    page = {"repository": {"pullRequest": {"reviewThreads": {
        "pageInfo": {"hasNextPage": False, "endCursor": None},
        "nodes": [{"id": "T1", "isResolved": False, "path": "a.py", "comments": {"nodes": [
            {"databaseId": 5, "body": "x <!-- stratum:finding 0123456789ab model:x -->", "author": {"login": "github-actions", "__typename": "Bot"}},
        ]}}],
    }}}}
    monkeypatch.setattr(gh, "graphql", lambda query, **variables: page)
    [thread] = gh.review_threads(4)
    assert thread["comments"][0]["author"] == "github-actions[bot]"

    from stratum_open.github.publish import thread_fingerprint

    assert thread_fingerprint(thread, "github-actions[bot]") == "0123456789ab"
