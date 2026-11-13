"""`review.quiet`: fewer of the model's comments, and a summary that keeps
them apart from what the checks found. With it off, nothing here applies."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import snapshots
from fakes import FakeGitHub, FakeModel

from stratum_open import runner
from stratum_open.diff import parse_diff
from stratum_open.engine import analyze
from stratum_open.findings import Finding
from stratum_open.github import publish
from stratum_open.policy import parse_policy
from stratum_open.review import NOT_DEFECTS, _one_per_claim, review
from stratum_open.runner import Context, review_pull

BOT = "stratum[bot]"
QUIET = parse_policy("review:\n  quiet: true\n")


def added(path, *hunks):
    """A diff that adds each of `hunks` (its first line number, its lines) to a file."""
    text = f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
    for start, lines in hunks:
        text += f"@@ -{start - 1},0 +{start},{len(lines)} @@\n" + "".join(f"+{line}\n" for line in lines)
    return text


def said(path, code, title, detail="It goes wrong when the list is empty.", **more):
    return {"file": path, "code": code, "severity": "medium", "category": "bug", "title": title, "detail": detail, "fix": "Change it.", **more}


def answer(*findings):
    return json.dumps({"summary": "Changes how files are read.", "matches_description": "yes", "mismatch": "", "findings": list(findings)})


FOUR_FILES = "".join(added(f"app/{name}.py", (10, [f"if not {name}.ready:", f"    {name}.start()", f"total = {name}.count + 1"])) for name in "abcd")


def run(model, policy=QUIET, diff=FOUR_FILES, **kwargs):
    return review(title="Start them", description="", files=parse_diff(diff), policy=policy, model=model, **kwargs)


def test_quiet_is_off_unless_the_policy_turns_it_on():
    assert parse_policy(None).review.quiet is False and parse_policy("review:\n  max_chunks: 2\n").review.quiet is False
    assert QUIET.review.quiet is True and not QUIET.errors
    assert list(parse_policy("review:\n  quiet: loud\n").errors) == ["review.quiet must be true or false."]


# ── one claim repeated across files ──────────────────────────────────────


def inverted(name):
    return said(f"app/{name}.py", f"if not {name}.ready:", "Inverted condition", f"The condition on `{name}.ready` is inverted.")


def test_a_title_the_model_gives_on_three_files_is_one_finding():
    reply = answer(inverted("a"), inverted("b"), inverted("c"), said("app/d.py", "total = d.count + 1", "Off by one"))
    kept = run(FakeModel(reply)).findings
    assert [(f.title, f.path) for f in kept] == [("Inverted condition", "app/a.py"), ("Off by one", "app/d.py")]
    assert kept[0].detail == "The condition on `a.ready` is inverted. The model said the same of 2 other files."
    assert len(run(FakeModel(reply), policy=parse_policy(None)).findings) == 4  # without quiet, as before


def test_the_same_title_on_two_files_stays_two_findings():
    kept = run(FakeModel(answer(inverted("a"), inverted("b")))).findings
    assert [f.path for f in kept] == ["app/a.py", "app/b.py"] and "other files" not in kept[0].detail


def test_the_copy_kept_is_the_most_severe_then_the_one_the_model_is_surest_of():
    def copy(path, severity="medium", confidence=0.6):
        return Finding("Inverted condition", "It is inverted.", severity, path=path, line_start=3, confidence=confidence)

    [kept] = _one_per_claim([copy("a.py"), copy("b.py", confidence=0.8), copy("c.py", "high"), copy("d.py")])
    assert (kept.path, kept.detail) == ("c.py", "It is inverted. The model said the same of 3 other files.")
    [kept] = _one_per_claim([copy("a.py"), copy("b.py", confidence=0.8), copy("c.py")])
    assert kept.path == "b.py"  # two answers agreed on this copy
    [kept] = _one_per_claim([copy("c.py"), copy("b.py"), copy("a.py")])
    assert kept.path == "a.py"  # a tie goes to the same copy on every run, so its thread stays


def test_a_check_that_fires_on_five_files_keeps_its_five_findings():
    checks = [Finding("Secret in code", "A token is written in the file.", "high", path=f"{n}.py", line_start=1, source="check", rule="secrets/token")
              for n in "abcde"]
    assert _one_per_claim(checks) == checks


def test_a_repeated_claim_is_verified_once():
    model = FakeModel(answer(inverted("a"), inverted("b"), inverted("c")), '{"real": "yes", "why": "it is"}')
    result = run(model, policy=parse_policy("review:\n  quiet: true\n  verify: strict\n"))
    assert len(result.findings) == 1 and [c.get("pass") for c in result.calls] == [None, "verify"]


def test_the_per_file_passes_keep_a_repeated_claim_once_too():
    from stratum_open.review_large import review_files

    def file_answer(name):
        return json.dumps({"risk_score": 40, "change_summary": "Starts it.", "findings": [inverted(name)]})

    policy = "review:\n  profile: large\n  file_reviews: 4\n  lens_files: 0\n  parallel_calls: 1\n"
    files = parse_diff(FOUR_FILES)
    large = review_files(title="Start them", description="", files=files, policy=parse_policy(policy + "  quiet: true\n"),
                         model=FakeModel(*[file_answer(name) for name in "abcd"]))
    assert [f.path for f in large.findings] == ["app/a.py"] and large.findings[0].detail.endswith("The model said the same of 3 other files.")
    large = review_files(title="Start them", description="", files=files, policy=parse_policy(policy), model=FakeModel(*[file_answer(name) for name in "abcd"]))
    assert len(large.findings) == 4


# ── comments that aren't defects ─────────────────────────────────────────


@pytest.mark.parametrize("title", [
    "Missing docstring for new parameter", "No documentation for the retry flag", "Large constant", "Magic number",
    "Test uses misleading field name", "Unclear Report Assignment", "Inconsistent naming",
    "Improving detection of raw mode", "Optimized breakpoint handling", "Efficient class management",
    "Potential off-by-one error", "Potential AttributeError", "Possible race condition",
])
def test_quiet_leaves_out_a_comment_that_isnt_a_defect(title):
    reply = answer(said("app/a.py", "total = a.count + 1", title))
    result = run(FakeModel(reply))
    assert result.findings == [] and result.dropped == 1
    assert [f.title for f in run(FakeModel(reply), policy=parse_policy(None)).findings] == [title]  # without quiet, as before


@pytest.mark.parametrize("title", [
    "Inverted condition", "Missing timeout", "Missing await for async close",
    # Marked right in the benchmark, each one word away from a pattern that would have dropped it:
    "Incorrect return description", "Incorrect argument description", "Redundant console print call", "Debug print statement left in test",
    "Incorrect package name", "Incorrect project name in deprecation warning",
    # Comments are a formatter's data, and an annotation can be read when the code runs.
    "Incorrect handling of hidden comments", "Incorrect type annotation", "Large number of queries in a loop", "Docstring promises a list, a dict is returned",
])
def test_quiet_keeps_a_finding_that_names_a_defect(title):
    assert [f.title for f in run(FakeModel(answer(said("app/a.py", "total = a.count + 1", title)))).findings] == [title]


@pytest.mark.parametrize("title", ["Potential SQL injection", "Possible path traversal", "Potentially unsafe deserialisation"])
def test_quiet_keeps_a_guess_about_security(title):
    reply = answer(said("app/a.py", "total = a.count + 1", title, "The value reaches the query unescaped.", category="security"))
    assert [f.title for f in run(FakeModel(reply)).findings] == [title]
    bug = answer(said("app/a.py", "total = a.count + 1", title, "The value reaches the query unescaped."))
    assert run(FakeModel(bug)).findings == []  # the same title in another category is still a guess
    style = answer(said("app/a.py", "total = a.count + 1", "Unclear query", "Hard to follow.", category="security"))
    assert run(FakeModel(style)).findings == []  # only the guess is spared


def test_only_the_title_is_read_since_a_right_finding_cites_the_docstring_as_evidence():
    detail = "The docstring says it returns True when `text/html` is accepted, but the code returns True when it isn't. This could be a typo."
    assert len(run(FakeModel(answer(said("app/a.py", "if not a.ready:", "Incorrect logic in accept_html", detail)))).findings) == 1


def quiet_filter():
    """benchmark/quiet_filter.py, which isn't in a package."""
    if "quiet_filter" not in sys.modules:
        spec = importlib.util.spec_from_file_location("quiet_filter", Path(__file__).parent.parent / "benchmark" / "quiet_filter.py")
        sys.modules["quiet_filter"] = importlib.util.module_from_spec(spec)  # its dataclass looks itself up there
        spec.loader.exec_module(sys.modules["quiet_filter"])
    return sys.modules["quiet_filter"]


def test_the_patterns_lose_no_finding_a_person_marked_right():
    script = quiet_filter()
    rows = script.marked(script.RESULTS)
    assert sum(1 for row in rows if row["verdict"] == "right") > 100  # the marked findings are there to be read
    shipped = script.shipped_candidates()
    for candidate in shipped:
        assert script.tally([candidate], rows)["right"] == 0, candidate.name
    assert script.tally(shipped, rows)["wrong"] >= 40
    for candidate in script.TRIED:  # each one left out lost a right finding, or says why else
        assert script.tally([candidate], rows)["right"] or candidate.left_out, candidate.name


def test_the_filter_table_says_for_every_pattern_whether_it_ships():
    # No counts here: they grow with every finding someone marks.
    script = quiet_filter()
    rows = {line.split(": `")[0].removeprefix("| "): line for line in script.table(script.marked(script.RESULTS)).split("\n") if ": `" in line}
    assert list(rows) == list(NOT_DEFECTS) + [candidate.name for candidate in script.TRIED]
    assert all(rows[name].split(" | ")[-1].startswith("yes") for name in NOT_DEFECTS)
    assert all(rows[candidate.name].split(" | ")[-1].startswith("no: ") for candidate in script.TRIED)
    assert "no: loses " in rows["mentions docs or a docstring"] and rows["a guess"].endswith("| yes |")


USERS = added("app/users.py", (5, ["    total = count + 1", "    return rows[total]"]))


def test_a_finding_from_before_quiet_was_turned_on_is_hidden_and_says_why():
    earlier = [
        {"title": "Potential IndexError", "detail": "rows may be short.", "severity": "medium", "category": "bug", "path": "app/users.py",
         "line_start": 6, "line_end": 6, "source": "model", "fingerprint": "aaaaaaaaaaaa", "snippet": "    return rows[total]"},
        {"title": "Off by one", "detail": "count + 1 is past the end.", "severity": "medium", "category": "bug", "path": "app/users.py",
         "line_start": 5, "line_end": 5, "source": "model", "fingerprint": "bbbbbbbbbbbb", "snippet": "    total = count + 1"},
    ]
    result = analyze(title="Count", description="", files=parse_diff(USERS), policy=QUIET, model=None, previous=earlier)
    assert [f.title for f in result.findings] == ["Off by one"]
    assert result.gone == {"aaaaaaaaaaaa": "Hidden: this project's reviews leave out comments like this one, which don't name a defect."}
    result = analyze(title="Count", description="", files=parse_diff(USERS), policy=parse_policy("review:\n  quiet: true\n  min_severity: high\n"),
                     model=None, previous=earlier)
    assert result.gone["bbbbbbbbbbbb"] == "Hidden: this project shows the model's findings from high severity up."


def test_a_carried_guess_about_security_stays_when_quiet_is_turned_on():
    earlier = [{"title": "Potential SQL injection", "detail": "rows comes from the request.", "severity": "high", "category": "security",
                "path": "app/users.py", "line_start": 6, "line_end": 6, "source": "model", "fingerprint": "cccccccccccc",
                "snippet": "    return rows[total]"}]
    result = analyze(title="Count", description="", files=parse_diff(USERS), policy=QUIET, model=None, previous=earlier)
    assert [f.title for f in result.findings] == ["Potential SQL injection"] and not result.gone


# ── the model's places on the lines ──────────────────────────────────────


def model_finding(n, confidence=0.6, severity="medium"):
    return Finding(f"Wrong value {n}", "It is wrong.", severity, path="app/a.py", line_start=n, confidence=confidence, fingerprint=f"{n:012x}")


def check_finding(n):
    return Finding(f"Secret {n}", "A token.", "high", path="app/b.py", line_start=n, source="check", rule="secrets/token", fingerprint=f"c{n:011x}")


def thread(finding, *, resolved=False, closed=False):
    body = publish.comment_body(finding, feedback=False)
    return {"id": f"T{finding.fingerprint}", "resolved": resolved,
            "comments": [{"id": 1, "body": publish.closed_body(body, "Fixed.") if closed else body, "author": BOT}]}


def test_the_checks_findings_are_never_cut_by_the_models_places():
    findings = [check_finding(n) for n in range(1, 9)] + [model_finding(n) for n in range(1, 9)]
    inline = publish.quiet_inline(findings, [], bot=BOT)
    assert [f for f in inline if f.source == "check"] == findings[:8]
    assert len([f for f in inline if f.source == "model"]) == publish.QUIET_SHOWN == 5


def test_the_places_go_to_what_the_model_is_surest_of_then_to_the_most_severe():
    findings = [model_finding(1), model_finding(2, severity="high"), model_finding(3, confidence=0.8), model_finding(4, severity="critical"),
                model_finding(5), model_finding(6), model_finding(7, 0.8, "low")]
    assert [f.line_start for f in publish.surest(findings)] == [3, 7, 4, 2, 1, 5, 6]
    assert [f.line_start for f in publish.quiet_inline(findings, [], bot=BOT)] == [1, 2, 3, 4, 7]  # in the order they came, for the total's cap


def test_an_open_thread_keeps_its_place_and_a_resolved_one_frees_none_for_itself():
    findings = [model_finding(n) for n in range(1, 9)]
    threads = [thread(findings[5]), thread(findings[6]), thread(findings[7], resolved=True), thread(findings[0], closed=True)]
    # Two places are held by open threads, so three are free: for the finding
    # Stratum closed that's back, and the next two. The one a person resolved isn't offered.
    assert [f.line_start for f in publish.quiet_inline(findings, threads, bot=BOT)] == [1, 2, 3]


FILES = "".join(added(f"app/{name}.py", (1, ["x = load()", "y = x[0]", "z = y / len(x)"])) for name in "abc")


def nine_findings():
    lines = ["x = load()", "y = x[0]", "z = y / len(x)"]
    return answer(*[said(f"app/{name}.py", line, f"Problem {name}{n}") for name in "abc" for n, line in enumerate(lines)])


def test_quiet_posts_five_of_the_models_findings_and_folds_the_rest():
    gh = FakeGitHub(diff=FILES)
    outcome = review_pull(Context(gh=gh, policy=QUIET, model=FakeModel(nine_findings()), bot=BOT, log=lambda line: None), 7)
    assert len(outcome.findings) == 9 and len(gh.reviews[0]["comments"]) == 5
    body = gh.issue_comments_[7][0]["body"]
    counted = ("**Suggested by the model:** 9 medium. 5 on the changed lines below, 4 folded at the end of this comment. "
               "The model can be wrong, so check each one before acting on it.")
    folded = body.split("<details><summary>4 more from the model</summary>")[1].split("</details>")[0]
    assert counted in body and folded.count("\n- **Medium · bug** in ") == 4
    assert len(publish.State.decode(body).findings) == 9  # folded or not, each is kept for the next push

    review_pull(Context(gh=gh, policy=QUIET, model=FakeModel(), bot=BOT, log=lambda line: None), 7)  # the same commit again
    [again] = gh.issue_comments_[7]
    assert len(gh.reviews) == 1 and counted in again["body"]  # the five keep their places, and the four stay folded
    assert again["body"].split("<details><summary>4 more from the model</summary>")[1].split("</details>")[0] == folded


def test_max_inline_comments_still_caps_the_total():
    gh = FakeGitHub(diff=FILES)
    policy = parse_policy("review:\n  quiet: true\n  max_inline_comments: 2\n")
    review_pull(Context(gh=gh, policy=policy, model=FakeModel(nine_findings()), bot=BOT, log=lambda line: None), 7)
    assert len(gh.reviews[0]["comments"]) == 2
    body = gh.issue_comments_[7][0]["body"]
    assert "**Suggested by the model:** 9 medium. 2 on the changed lines below, 4 folded at the end of this comment." in body
    assert body.split("Listed here instead:\n")[1].split("\n\n")[0].count("- **Medium · bug**") == 3  # five in view: two on the lines, three here


def test_without_quiet_every_finding_is_posted_up_to_max_inline_comments():
    gh = FakeGitHub(diff=FILES)
    review_pull(Context(gh=gh, policy=parse_policy(None), model=FakeModel(nine_findings()), bot=BOT, log=lambda line: None), 7)
    assert len(gh.reviews[0]["comments"]) == 9
    assert "**Findings:** 9 medium. 9 on the changed lines below." in gh.issue_comments_[7][0]["body"]
    assert gh.checks[-1]["title"] == "9 medium" and len(gh.checks[-1]["annotations"]) == 9


SECRET = added("app/config.py", (1, ['API_TOKEN = "ghp_' + "a1B2c3D4e5F6g7H8i9J0" * 2 + '"']))


def test_the_check_run_counts_them_apart_and_annotates_only_what_has_a_comment():
    gh = FakeGitHub(diff=FILES + SECRET)
    outcome = review_pull(Context(gh=gh, policy=QUIET, model=FakeModel(nine_findings()), bot=BOT, log=lambda line: None), 7)
    checks = [f for f in outcome.findings if f.source == "check"]
    assert checks and all(f.severity == "critical" for f in checks)
    assert gh.checks[-1]["title"] == f"Checks: {len(checks)} critical. Model: 9 medium."
    # An annotation is shown on its line like a comment, so the four folded findings get none.
    annotated, posted = [a["title"] for a in gh.checks[-1]["annotations"]], [c["body"] for c in gh.reviews[0]["comments"]]
    assert len(annotated) == len(checks) + 5 and all(any(title in body for body in posted) for title in annotated)


# ── the summary ──────────────────────────────────────────────────────────


def summary(findings, *, inline=(), policy=QUIET, model_name="fake-7b", **more):
    return publish.render_summary(publish.Summary(findings=findings, inline=set(inline), policy=policy, model_name=model_name, **more), publish.State())


def test_the_summary_counts_the_checks_findings_and_the_models_apart():
    findings = [check_finding(1), check_finding(2), model_finding(3), model_finding(4, severity="high")]
    body = summary(findings, inline={f.fingerprint for f in findings[1:]})
    assert ("**Found by the checks:** 2 high. 1 on the changed lines below. These need no model: each is a rule that matched the diff.\n\n"
            "Listed here instead:\n- **High · bug** in `app/b.py` line 1: Secret 1. A token.\n\n"
            "**Suggested by the model:** 1 high, 1 medium. 2 on the changed lines below. "
            "The model can be wrong, so check each one before acting on it.\n") in body
    assert "**Findings:**" not in body
    assert "**Findings:** 3 high, 1 medium. 3 on the changed lines below." in summary(findings, inline={f.fingerprint for f in findings[1:]}, policy=parse_policy(None))


def test_nothing_found_is_said_for_each_and_not_for_a_model_that_isnt_there():
    assert "**Found by the checks:** nothing.\n\n**Suggested by the model:** nothing.\n" in summary([])
    body = summary([], model_name=None)
    assert "**Found by the checks:** nothing.\n" in body and "Suggested by the model" not in body
    assert "**Suggested by the model:** 1 medium." in summary([model_finding(1)], model_name=None)  # kept from when there was one


def test_with_nothing_on_the_lines_the_summary_lists_five_and_folds_the_rest():
    findings = [model_finding(n) for n in range(1, 8)] + [model_finding(9, confidence=0.8)]
    body = summary(findings)  # a dry run, or GitHub refused the inline comments
    head, folded = body.split("<details><summary>3 more from the model</summary>")
    assert "**Suggested by the model:** 8 medium. 3 folded at the end of this comment. The model can be wrong" in head
    assert [line.split(": ")[1] for line in head.split("\n") if line.startswith("- **Medium")] == ["Wrong value 9. It is wrong."] + [
        f"Wrong value {n}. It is wrong." for n in range(1, 5)]
    assert [line.split(": ")[1] for line in folded.split("\n") if line.startswith("- **Medium")] == [f"Wrong value {n}. It is wrong." for n in (5, 6, 7)]


def test_a_long_list_says_how_many_it_left_out():
    body = summary([check_finding(n) for n in range(1, 20)] + [model_finding(n) for n in range(1, 30)])
    assert body.count("- **High · bug** in `app/b.py`") == 15 and "\n- and 4 more.\n\n**Suggested by the model:**" in body
    assert body.split("<details><summary>24 more from the model</summary>")[1].split("</details>")[0].count("- **Medium") == 15
    assert "\n- and 9 more.\n\n</details>" in body


IMPORTS = [f"`app/{n}.py` is imported by {30 - i} files (e.g. `app/x.py`, `app/y.py`)." for i, n in enumerate("abcd")]
OWNERS = "CODEOWNERS: `@acme/core` for `app/a.py` and 3 more."
ALONE = "Only `dana` has changed `app/` in the last 180 days (9 of 10 commits); a maintainer may want them to review this."
HOT = [f"`app/{n}.py` is a hotspot: changed {8 + i} times in the last 180 days. Reviewed strictly." for i, n in enumerate("ab")]


def test_three_history_notes_are_shown_and_the_rest_go_under_what_was_reviewed():
    body = summary([], notes=IMPORTS + HOT)
    head, details = body.split("<details><summary>What was reviewed</summary>")
    assert f"**From the project's history:**\n- {IMPORTS[0]}\n- {IMPORTS[1]}\n- {HOT[0]}\n- 3 more under \"What was reviewed\".\n\n" in head
    assert f"- More from the project's history:\n  - {IMPORTS[2]}\n  - {IMPORTS[3]}\n  - {HOT[1]}\n\n</details>" in details
    whole = summary([], notes=IMPORTS + HOT, policy=parse_policy(None))
    assert "**From the project's history:**\n" + "".join(f"- {note}\n" for note in IMPORTS + HOT) + "\n" in whole  # without quiet, as before


def test_one_note_of_each_kind_comes_before_a_second_of_any():
    assert publish._lead_notes(IMPORTS + [OWNERS, ALONE] + HOT) == ([IMPORTS[0], OWNERS, ALONE], IMPORTS[1:] + HOT)
    assert publish._lead_notes(IMPORTS[:2]) == (IMPORTS[:2], [])
    assert "more under" not in summary([], notes=IMPORTS[:3])


def test_the_markers_and_the_saved_state_are_where_they_were():
    state = publish.State(head="a" * 40, findings=[{"title": "Wrong value 1"}], ignored=["abc"], unread=["app/a.py"])
    quiet = publish.Summary(findings=[model_finding(1)], inline=set(), policy=QUIET, model_name="fake-7b", notes=IMPORTS + HOT)
    loud = publish.Summary(findings=[model_finding(1)], inline=set(), policy=parse_policy(None), model_name="fake-7b", notes=IMPORTS + HOT)
    body, before = publish.render_summary(quiet, state), publish.render_summary(loud, state)
    assert body.startswith(publish.SUMMARY_MARKER + "\n### Stratum review\n")
    assert body.split("\n")[-1] == before.split("\n")[-1] == state.encode() and publish.State.decode(body) == state
    assert publish.find_summary([{"body": body, "user": {"login": BOT}}], BOT) is not None
    assert body.split("\n")[-2] == before.split("\n")[-2]  # the footer


def test_the_check_run_keeps_the_problems_when_findings_are_folded():
    body = summary([model_finding(n) for n in range(1, 9)], problems=["2 of 3 model calls failed; some files weren't reviewed."])
    assert "**Couldn't do everything:**\n- 2 of 3 model calls failed" in body.split("<details>")[0]  # what the runner hands the check run


# ── a review like the one on this project's own PR #55 ───────────────────

NOTES = [
    "`stratum_open/diff.py` is imported by 38 files (e.g. `benchmark/bench.py`, `benchmark/dropped_awaits.py`, `benchmark/planted.py`).",
    "`stratum_open/runner.py` is imported by 22 files (e.g. `benchmark/planted.py`, `stratum_open/action.py`, `stratum_open/app.py`).",
    "`stratum_open/github/publish.py` is imported by 13 files (e.g. `stratum_open/action.py`, `stratum_open/github/commands.py`, `stratum_open/github/feedback.py`).",
    "`stratum_open/review.py` is imported by 11 files (e.g. `benchmark/bench.py`, `benchmark/verify_replay.py`, `stratum_open/engine.py`).",
    "`stratum_open/engine.py` is imported by 8 files (e.g. `benchmark/planted.py`, `stratum_open/__init__.py`, `stratum_open/cli.py`).",
    "`stratum_open/github/commands.py` is imported by 5 files (e.g. `stratum_open/action.py`, `stratum_open/app.py`, `tests/e2e/test_b_model_output.py`).",
    "`stratum_open/review_large.py` is imported by 5 files (e.g. `stratum_open/engine.py`, `stratum_open/runner.py`, `tests/test_hunk_context.py`).",
    "`stratum_open/cli.py` is imported by 3 files (e.g. `stratum_open/__main__.py`, `tests/e2e/test_j_robustness.py`, `tests/test_app_cli.py`).",
    "`stratum_open/cli.py` is a hotspot: changed 8 times in the last 180 days, and among the more complex files (83 branches and conditions). Reviewed strictly.",
    "`stratum_open/diff.py` is a hotspot: changed 5 times in the last 180 days, and among the more complex files (143 branches and conditions). Reviewed strictly.",
    "`stratum_open/engine.py` is a hotspot: changed 8 times in the last 180 days, and among the more complex files (81 branches and conditions). Reviewed strictly.",
    "`stratum_open/github/commands.py` is a hotspot: changed 6 times in the last 180 days, and among the more complex files (106 branches and conditions). Reviewed strictly.",
    "`stratum_open/github/publish.py` is a hotspot: changed 7 times in the last 180 days, and among the more complex files (104 branches and conditions). Reviewed strictly.",
    "`stratum_open/review.py` is a hotspot: changed 12 times in the last 180 days, and among the more complex files (159 branches and conditions). Reviewed strictly.",
    "`stratum_open/runner.py` is a hotspot: changed 14 times in the last 180 days, and among the more complex files (195 branches and conditions). Reviewed strictly.",
]
CLI = "        if result.review is not None and result.review.calls and not result.review.ran:"
DIFF_PY = "    if file.binary or file.status == \"deleted\" or not file.hunks or skip(file.path):"
ENGINE = "            if (only_paths is None or f.path in only_paths) and sendable(f, policy.ignored):"
COMMANDS = "    if result.review is not None and result.review.calls and not result.review.ran:"
SIGNATURE = "def review_pull(ctx: Context, number: int, *, full: bool = False, post: bool = True, retry: bool = True) -> Outcome:"
RETRIED = "        retried = (set(state.unread) & set(paths)) - changed"
LOGGED = "            ctx.log(redact(f\"model review failed: {call.get('error', '')}\"))"
CHECKED = "    if result is not None:"
FAILED = "            problems.append(f\"{large.failed_calls} of {len(file_calls)} per-file model calls failed; \""
TWO_FILES = "    gh.diff = two_files()"
PR_55 = (added("stratum_open/cli.py", (193, [CLI])) + added("stratum_open/diff.py", (345, [DIFF_PY])) + added("stratum_open/engine.py", (129, [ENGINE]))
         + added("stratum_open/github/commands.py", (192, [COMMANDS]))
         + added("stratum_open/runner.py", (94, [SIGNATURE]), (126, [RETRIED]), (152, [LOGGED, CHECKED]), (169, [FAILED]))
         + added("tests/test_runner.py", (453, [TWO_FILES])))
WHAT = ("This PR changes the `review_pull` function in `stratum_open/runner.py` to include a `retry` parameter and adjust how files "
        "are read based on previous failures.")


def always_false(path, code):
    return said(path, code, "Inverted condition", f"The condition `{code.strip()}` is inverted, which will always be `False`.",
                fix="Negate the condition.")


# The model's findings as that review had them, in the two pushes it took: ten
# of medium severity, five of them one wrong claim on five files.
FIRST_PUSH = [
    always_false("stratum_open/cli.py", CLI), always_false("stratum_open/diff.py", DIFF_PY), always_false("stratum_open/engine.py", ENGINE),
    always_false("stratum_open/github/commands.py", COMMANDS),
    said("stratum_open/runner.py", SIGNATURE, "Missing docstring for new parameter",
         "The `retry` parameter is added but lacks a docstring explaining its purpose and default value.",
         fix="Add a docstring for the `retry` parameter."),
    said("stratum_open/runner.py", RETRIED, "Inverted condition",
         "The condition `set(state.unread) & set(paths)) - changed` is inverted. It should be `changed - (set(state.unread) & set(paths))` "
         "to correctly identify paths that were unread but changed.", fix="Change the line to `retried = changed - (set(state.unread) & set(paths))`."),
]
SECOND_PUSH = [
    said("stratum_open/runner.py", LOGGED, "Potential error handling issue",
         "The error message is logged but not handled. If the error is critical, it could cause the function to fail unexpectedly.",
         fix="Add error handling for the model review failure."),
    said("stratum_open/runner.py", CHECKED, "Unnecessary check",
         "The check `if result is not None:` is unnecessary because the function already checks `if result.calls and not result.ran:`. "
         "This check can be removed to simplify the code.", fix="Remove the unnecessary check."),
    said("stratum_open/runner.py", FAILED, "Potential off-by-one error",
         "The code appends a problem message without checking if `large.failed_calls` is zero. If `large.failed_calls` is zero, the message will be incorrect.",
         fix="Add a check to ensure `large.failed_calls` is not zero before appending the problem message."),
    said("tests/test_runner.py", TWO_FILES, "Potential off-by-one error",
         "The variable `gh.diff` is set to `two_files()`, which returns a string. However, the variable name suggests it should be a list of files. "
         "This could lead to an off-by-one error when processing the diff.",
         fix="Change `gh.diff = two_files()` to `gh.diff = [two_files()]` to ensure `gh.diff` is a list of files."),
]


def like_pr_55(monkeypatch, *, quiet):
    """The fake GitHub after both pushes of that review, with the policy's `review.quiet` as given."""
    monkeypatch.setattr(runner, "_history", lambda ctx, files, pull: (list(NOTES), set(), [], {}))
    monkeypatch.setattr("stratum_open.checks.bandit.available", lambda: False)  # so the summary doesn't depend on what's installed
    gh = FakeGitHub(diff=PR_55, title="Read again only the files a failed model call held", linked=[51],
                    body="After a failed model call, the next review reads only the files that call held. Fixes #51.")
    policy = parse_policy(f"triage:\n  enabled: true\nreview:\n  quiet: {str(quiet).lower()}\n")
    replies = [json.dumps({"summary": WHAT, "matches_description": "yes", "mismatch": "", "findings": push}) for push in (FIRST_PUSH, SECOND_PUSH)]
    ctx = Context(gh=gh, policy=policy, model=FakeModel(*replies, name="qwen2.5-coder:7b"), bot=BOT, log=lambda line: None)
    review_pull(ctx, 7)
    gh.move_head(7, "b" * 40)
    gh.compare[("a" * 40, "b" * 40)] = ["stratum_open/runner.py", "tests/test_runner.py"]
    review_pull(ctx, 7)
    return gh


def test_a_review_like_pr_55_without_quiet_is_ten_findings_and_fifteen_notes(monkeypatch):
    gh = like_pr_55(monkeypatch, quiet=False)
    body = gh.issue_comments_[7][0]["body"]
    assert "**Findings:** 10 medium. 10 on the changed lines below." in body
    assert body.split("**From the project's history:**\n")[1].split("\n\n")[0].count("\n") == 14
    assert len(gh.review_comments_) == 10 and sum("Inverted condition" in c["body"] for c in gh.review_comments_) == 5


def test_the_quiet_summary_of_a_review_like_pr_55(monkeypatch):
    gh = like_pr_55(monkeypatch, quiet=True)
    body, state = gh.issue_comments_[7][0]["body"].rsplit("\n", 1)
    snapshots.check("summary_quiet.md", body + "\n")
    saved = publish.State.decode(state)
    assert saved.head == "b" * 40 and [f["title"] for f in saved.findings] == ["Inverted condition", "Unnecessary check"]
    assert [(c["path"], c["line"]) for c in gh.review_comments_] == [("stratum_open/cli.py", 193), ("stratum_open/runner.py", 153)]
    assert "The model said the same of 4 other files." in gh.review_comments_[0]["body"]
