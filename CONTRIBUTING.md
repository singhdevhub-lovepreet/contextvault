# Contributing to ContextVault

ContextVault is early-stage. Issues, design discussions, and PRs welcome.

## Philosophy

1. **Privacy by default.** Anything that leaves the local machine — an API call, an embedding lookup, a network bind beyond loopback — is opt-in behind explicit consent (`--allow-egress`, bound interfaces). The vault is the user's data; we don't ship it anywhere without their say-so.
2. **Deterministic core, optional intelligence.** Extraction, summarization, and lint must work offline with no LLM. LLM-quality tiers are escape hatches, not load-bearing.
3. **Smallest unit that works.** No speculative abstractions. Three real callers minimum before an abstraction lands.
4. **Failure is the spec.** Every new failure mode needs explicit handling and a documented undo plan.

## Workflow

1. Open an issue for non-trivial changes. Describe the problem, proposal, and blast radius.
2. Fork, branch with `feat/...` / `fix/...` / `docs/...` prefix.
3. `pipx install -e .[dev]` for the editable install with dev deps.
4. Make the change. Add tests under `tests/`.
5. `pytest tests/` — must be green, hermetic (no network).
6. `ruff check src/ tests/` and `mypy src/` — must be clean.
7. Open a PR with a description of behavior change, test plan, and undo plan.

## Tests are hermetic by default

`pytest tests/` runs offline. No Anthropic API, no ollama, no HTTP egress. If your change requires network, gate it behind a marker:

```python
@pytest.mark.egress
def test_real_anthropic_call():
    ...
```

These are skipped by default and only run when explicitly opted into via `pytest -m egress`.

## Commit messages

Conventional Commits:

```
<type>(<scope>): <short description>

<longer body>
```

Types: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `perf`, `style`.
