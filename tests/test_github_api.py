from stratum_open.github.api import GitHub, _login


def test_graphql_bot_logins_match_rest_ones_only_for_real_bots():
    assert _login({"login": "github-actions", "__typename": "Bot"}) == "github-actions[bot]"
    assert _login({"login": "stratum-open", "__typename": "User"}) == "stratum-open"  # a person, not the App
    assert _login(None) == ""


def test_a_label_with_a_slash_stays_one_path_segment(monkeypatch):
    gh = GitHub("t", "acme/widget")
    urls = []
    monkeypatch.setattr(gh, "request", lambda method, url, **kw: urls.append(url))
    gh.remove_label(7, "triage/needs work")
    gh.ensure_label("triage/ready", "ffffff", "")
    assert urls[0].endswith("/issues/7/labels/triage%2Fneeds%20work")
    assert urls[1].endswith("/labels/triage%2Fready")


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


def test_a_too_big_diff_keeps_a_move_as_a_move(monkeypatch):
    from stratum_open.diff import parse_diff
    from stratum_open.github.api import GitHubError

    gh = GitHub("t", "acme/widget")

    def refuse(*args, **kwargs):
        raise GitHubError(406, "diff too large")

    monkeypatch.setattr(gh, "request", refuse)
    monkeypatch.setattr(gh, "paginate", lambda path, **params: iter([
        {"filename": ".github/workflows/ci.yml", "previous_filename": "docs/ci.yml", "status": "renamed", "changes": 0},
        {"filename": "app/logo.png", "previous_filename": "logo.png", "status": "renamed", "changes": 2},  # edited, no patch
    ]))
    move, edited = parse_diff(gh.pull_diff(7))
    assert (move.status, move.old_path, move.path, move.binary) == ("renamed", "docs/ci.yml", ".github/workflows/ci.yml", False)
    assert edited.binary
