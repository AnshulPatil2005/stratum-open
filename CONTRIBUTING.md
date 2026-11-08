# Contributing

Thanks for helping. Stratum Open is for maintainers who are short on review
time, so it holds itself to what it asks of contributors:

- **Open an issue first** for anything bigger than a small fix, so we agree on
  the change before you spend time on it.
- **Explain the change in the PR:** what it does, why, and how you checked it.
- **Add or update tests.** Run them with `pytest`; they need no network and no
  model. `tests/e2e/` runs the whole pipeline against a fake model server and
  a fake GitHub API (`make e2e`); `make e2e-live` runs it against a real model
  when you have one. A fix for a crash comes with a test that failed before it.
- **Keep the data page true.** If your change stores something new, or sends
  something new off the maintainers' machines, update [DATA.md](DATA.md) in
  the same PR. `tests/test_data_page.py` fails when a stored field isn't listed.
- **Sign the CLA** ([CLA.md](CLA.md)) when the bot asks on your first PR.

AI-assisted contributions are welcome when you've read, run and understood
the code you submit, and you can answer questions about it.

## Setup

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e ".[test,app]"
pytest
```

To try a review against a real model, install [Ollama](https://ollama.com), run
`ollama pull qwen2.5-coder:7b`, then `stratum-open review --local main` in any repo.

## Reporting a security problem

Please don't open a public issue. See [SECURITY.md](SECURITY.md).
