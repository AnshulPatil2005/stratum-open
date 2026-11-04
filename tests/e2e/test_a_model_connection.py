"""A. The model connection. Whatever the model server does, the checks still
run and are posted, the summary says the model step was skipped and why, the
check run completes, and nothing crashes."""

from __future__ import annotations

import socket
import threading
import time

import pytest

from stratum_open.model import Model, model_from_policy
from stratum_open.policy import parse_policy
from stratum_open.runner import review_pull

from conftest import DIFF, SQL_LINE, head_of
from contract import assert_contract
from fake_model import Response, finding, json_reply

HEAD = head_of(7)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def summary_text(fake_github, number=7) -> str:
    [comment] = fake_github.summary_comments(number)
    return comment["body"]


def checks_only(fake_github, outcome, logs, *, says: str) -> None:
    body = summary_text(fake_github)
    assert "The model didn't answer, so this review has only the checks" in body
    assert says in body, body
    assert all(f.source == "check" for f in outcome.findings)
    assert any(f.rule == "code/sql" for f in outcome.findings)  # the no-model checks still ran and were posted
    assert_contract(fake_github, number=7, head=HEAD, logs=logs)


@pytest.fixture
def pr(fake_github):
    fake_github.add_pull(7, diff=DIFF)
    return fake_github


def test_server_not_running(pr, make_context, make_model, logs):
    ctx = make_context(model=make_model(endpoint=f"http://127.0.0.1:{free_port()}/v1", timeout=3, retries=0))
    started = time.monotonic()
    outcome = review_pull(ctx, 7)
    assert time.monotonic() - started < 15
    checks_only(pr, outcome, logs, says="couldn't reach the model")


def test_wrong_endpoint_dns_failure(pr, make_context, make_model, logs):
    ctx = make_context(model=make_model(endpoint="http://model.invalid:11434/v1", timeout=3, retries=0))
    outcome = review_pull(ctx, 7)
    checks_only(pr, outcome, logs, says="couldn't reach the model")


def test_model_not_pulled(pr, fake_model, make_context, logs):
    fake_model.queue(Response.status(404, {"error": "model 'qwen2.5-coder:7b' not found, try pulling it first"}))
    outcome = review_pull(make_context(), 7)
    checks_only(pr, outcome, logs, says="404")
    assert "not found" in summary_text(pr)


def test_timeout_before_first_byte(pr, fake_model, make_context, make_model, logs):
    fake_model.queue(Response.hang(seconds=30))
    started = time.monotonic()
    outcome = review_pull(make_context(model=make_model(timeout=1, retries=0)), 7)
    assert time.monotonic() - started < 10, "a hung model must not hold the review past its timeout"
    checks_only(pr, outcome, logs, says="ReadTimeout")


def test_slow_model_a_second_past_the_timeout(pr, fake_model, make_context, make_model, logs):
    fake_model.queue(Response.hang(seconds=2))
    outcome = review_pull(make_context(model=make_model(timeout=1, retries=0)), 7)
    checks_only(pr, outcome, logs, says="couldn't reach the model")


def test_connection_dropped_mid_response(pr, fake_model, make_context, make_model, logs):
    fake_model.queue(Response.truncated(), Response.truncated())
    outcome = review_pull(make_context(model=make_model(retries=1)), 7)
    checks_only(pr, outcome, logs, says="couldn't reach the model")


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_http_error_statuses_are_retried_a_bounded_number_of_times(pr, fake_model, make_context, make_model, logs, status):
    fake_model.queue(*[Response.status(status, headers={"Retry-After": "0"})] * 5)
    outcome = review_pull(make_context(model=make_model(retries=2)), 7)
    assert len(fake_model.chat_calls) == 3, "retries must be bounded: one call plus two retries"
    checks_only(pr, outcome, logs, says=str(status))


def test_a_retry_that_succeeds_gives_a_full_review(pr, fake_model, make_context, make_model, logs):
    fake_model.queue(Response.status(503, headers={"Retry-After": "0"}), Response.ok(json_reply([finding("app/users.py", SQL_LINE, line=5)])))
    outcome = review_pull(make_context(model=make_model(retries=1)), 7)
    assert len(fake_model.chat_calls) == 2
    assert {f.title for f in outcome.findings if f.source == "model"} == {"Unknown id"}
    assert "didn't answer" not in summary_text(pr)
    assert_contract(pr, number=7, head=HEAD, logs=logs)


def test_retry_after_given_as_an_http_date_does_not_crash(pr, fake_model, make_context, make_model, logs):
    fake_model.queue(Response.status(503, headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}), Response.ok(json_reply()))
    outcome = review_pull(make_context(model=make_model(retries=1)), 7)
    assert outcome.status == "published" and len(fake_model.chat_calls) == 2
    assert_contract(pr, number=7, head=HEAD, logs=logs)


def test_hosted_model_without_an_api_key_sends_no_authorization(pr, fake_model, make_context, logs):
    policy = parse_policy(f"review:\n  model: hosted\n  endpoint: {fake_model.url}\n")
    model = model_from_policy(policy, env={})
    outcome = review_pull(make_context(policy_text=None, model=model), 7)
    assert outcome.status == "published"
    assert "Authorization" not in fake_model.chat_calls[0]["headers"]
    assert_contract(pr, number=7, head=HEAD, logs=logs)


def test_api_key_is_sent_but_never_leaks(pr, fake_model, make_context, logs):
    key = "sk-" + "LiveKey" * 3 + "0123456789ABCDEF"  # built in parts so the secret check doesn't flag this test's source
    policy = parse_policy(f"review:\n  model: hosted\n  endpoint: {fake_model.url}\n")
    fake_model.queue(Response.status(401, {"error": {"message": "Incorrect API key provided: " + key}}))
    outcome = review_pull(make_context(policy_text=None, model=model_from_policy(policy, env={"STRATUM_MODEL_API_KEY": key})), 7)
    assert fake_model.chat_calls[0]["headers"]["Authorization"] == f"Bearer {key}"
    assert "401" in summary_text(pr)
    assert key not in pr.posted_text(), "the server echoed the key back; it must not reach the PR"
    assert_contract(pr, number=7, head=HEAD, logs=logs, secrets=[key])
    assert all(f.source == "check" for f in outcome.findings)


def test_endpoint_returns_html_with_status_200(pr, fake_model, make_context, logs):
    fake_model.queue(Response.html(status=200), Response.html(status=200))
    outcome = review_pull(make_context(), 7)
    checks_only(pr, outcome, logs, says="isn't JSON")


def test_endpoint_returns_an_empty_body(pr, fake_model, make_context, logs):
    fake_model.queue(Response.empty(), Response.empty())
    outcome = review_pull(make_context(), 7)
    checks_only(pr, outcome, logs, says="empty reply")


@pytest.mark.parametrize("body", ["null", "[]", '"just a string"', "42", '{"choices": [null]}', '{"choices": []}', '{"message": "not an object"}',
                                  '{"message": {"content": null}}', '{"choices": [{"message": null}]}', '{"error": "model runner has stopped"}'])
def test_json_that_is_not_the_expected_shape(pr, fake_model, make_context, logs, body):
    fake_model.queue(Response.raw(body), Response.raw(body))
    outcome = review_pull(make_context(), 7)
    assert outcome.status == "published"
    assert_contract(pr, number=7, head=HEAD, logs=logs)
    assert all(f.source == "check" for f in outcome.findings)
    assert "only the checks" in summary_text(pr)


def test_context_window_exceeded_on_an_openai_server(pr, fake_openai_model, make_context, make_model, logs):
    error = {"error": {"message": "This model's maximum context length is 8192 tokens. However, your messages resulted in 9000 tokens.",
                       "type": "invalid_request_error", "code": "context_length_exceeded"}}
    fake_openai_model.queue(Response.status(400, error), Response.status(400, error))
    outcome = review_pull(make_context(model=make_model(endpoint=fake_openai_model.url, retries=0)), 7)
    assert [c["path"] for c in fake_openai_model.chat_calls] == ["/v1/chat/completions"] * 2  # once with JSON mode, once without
    checks_only(pr, outcome, logs, says="maximum context length")


def test_an_openai_only_server_without_json_mode_still_works(pr, fake_openai_model, make_context, make_model, logs):
    fake_openai_model.queue(Response.status(400, {"error": {"message": "response_format is not supported"}}),
                            Response.ok(json_reply([finding("app/users.py", SQL_LINE, line=5)])))
    outcome = review_pull(make_context(model=make_model(endpoint=fake_openai_model.url, retries=0)), 7)
    bodies = [c["body"] for c in fake_openai_model.chat_calls]
    assert "response_format" in bodies[0] and "response_format" not in bodies[1]
    assert {f.title for f in outcome.findings if f.source == "model"} == {"Unknown id"}
    assert_contract(pr, number=7, head=HEAD, logs=logs)


def test_ollama_gets_num_ctx_and_json_format(pr, fake_model, make_context, make_model):
    # The policy owns the context window (the profile fills it in when the file is silent), so set it there.
    review_pull(make_context(policy_text="review:\n  context_tokens: 8192\n", model=make_model()), 7)
    [call] = fake_model.chat_calls
    assert call["path"] == "/api/chat"
    assert call["body"]["options"]["num_ctx"] == 8192 and call["body"]["format"] == "json" and call["body"]["stream"] is False


def test_two_prs_reviewed_concurrently_against_one_model(fake_github, fake_model, make_context, make_model, logs):
    fake_github.add_pull(7, diff=DIFF)
    fake_github.add_pull(8, diff=DIFF.replace("app/users.py", "app/other.py"))
    fake_model.default = Response.hang(seconds=0.5)  # slow enough that the two calls overlap
    ctx = make_context(model=make_model(timeout=10))
    outcomes: dict[int, object] = {}
    errors: list[BaseException] = []

    def run(number: int) -> None:
        try:
            outcomes[number] = review_pull(ctx, number)
        except BaseException as exc:  # noqa: BLE001 - the test records whatever escaped
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(n,)) for n in (7, 8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not errors and {o.status for o in outcomes.values()} == {"published"}
    for number in (7, 8):
        assert_contract(fake_github, number=number, head=head_of(number), logs=logs)
    assert len(fake_model.chat_calls) == 2


def test_model_object_survives_any_exception_from_the_transport(pr, make_context, logs, monkeypatch):
    """A bug in the HTTP layer (anything that isn't a requests error) must still
    leave the checks and the check run standing."""
    model = Model("http://127.0.0.1:1/v1", "fake-7b", timeout=1, retries=0)
    monkeypatch.setattr(model, "_ollama_base", lambda: (_ for _ in ()).throw(TypeError("unexpected None in transport")))
    outcome = review_pull(make_context(model=model), 7)
    assert outcome.status == "published"
    assert "Stratum hit a bug during the model step (TypeError), so this review has only the checks." in summary_text(pr)
    assert "didn't answer" not in summary_text(pr), "a bug of ours must not be dressed up as a model outage"
    assert any("model review failed" in line and "Traceback" in line for line in logs), "the traceback belongs in the log"
    assert "Traceback" not in pr.posted_text()
    assert_contract(pr, number=7, head=HEAD, logs=[])  # logs hold the traceback on purpose; posted text must not
