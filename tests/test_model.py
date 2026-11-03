import requests

from stratum_open.model import Model, _billions


class FakeResponse:
    def __init__(self, data, status=200):
        self._data, self.status_code, self.ok = data, status, status < 400

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, routes):
        self.routes = routes  # (method, url) -> data, or an exception to raise

    def _answer(self, method, url):
        found = self.routes.get((method, url))
        if isinstance(found, Exception):
            raise found
        return FakeResponse(found if found is not None else {}, 200 if found is not None else 404)

    def get(self, url, **kwargs):
        return self._answer("GET", url)

    def post(self, url, **kwargs):
        return self._answer("POST", url)


def test_ollama_reports_size_and_context():
    session = FakeSession({
        ("GET", "http://localhost:11434/api/version"): {"version": "0.40.13"},
        ("POST", "http://localhost:11434/api/show"): {
            "details": {"parameter_size": "7.6B"}, "model_info": {"qwen2.context_length": 32768, "general.architecture": "qwen2"},
        },
    })
    info = Model("http://localhost:11434/v1", "qwen2.5-coder:7b", session=session).describe()
    assert (info.billions, info.context, info.local) == (7.6, 32768, True)


def test_a_hosted_server_reports_context_and_the_name_gives_the_size():
    session = FakeSession({
        ("GET", "https://openrouter.ai/api/version"): requests.ConnectionError(),
        ("GET", "https://openrouter.ai/api/v1/models"): {"data": [{"id": "google/gemma-4-31b-it:free", "context_length": 131072}]},
    })
    info = Model("https://openrouter.ai/api/v1", "google/gemma-4-31b-it:free", session=session).describe()
    assert (info.billions, info.context, info.local) == (31, 131072, False)


def test_describe_never_fails():
    session = FakeSession({("GET", "https://example.invalid/api/version"): requests.ConnectionError(),
                           ("GET", "https://example.invalid/v1/models"): requests.ConnectionError()})
    info = Model("https://example.invalid/v1", "mystery", session=session).describe()
    assert (info.billions, info.context, info.local) == (None, None, False)


def test_parameter_sizes():
    assert _billions("7.6B") == 7.6
    assert _billions("8x7B") == 56
    assert _billions("500M") == 0.5
    assert _billions("") is None
