# Contributing

This repo drives a paid service with a real browser session. Most of the rules below exist because breaking
them cost credits.

## The gate

```bash
uv run ruff check . && uv run ruff format --check . && uv run pytest
```

Everything must be green before a pull request. CI runs exactly this.

## Rules that are not negotiable

1. **Tests never call Flow.** Every test uses fixtures or fakes. A test that opens a browser is a test that
   spends money on someone else's account. Anything that must talk to Flow lives in `scripts/acceptance/`,
   is run by hand, and prints what it spent.
2. **A ledger row is written before the click, never after.** If you add a path that can spend, it goes
   through the same guard: a `job_id` is required, and it is refused if any `ledger.jsonl` under `out/`
   already holds it.
3. **Outputs stay inside `out/`.** Tools that write files force their target there. Do not add a path that
   writes elsewhere.
4. **A tool description states its price.** `free`, a credit number, or `unmeasured`. The description is the
   contract an agent reads before spending; `tests/test_tool_docs.py` fails a tool that says nothing.
5. **Do not guess a price.** If a model, a duration or a quality level has never been measured on a real run,
   the tool refuses it and says so. Guessing here bills the user.
6. **Green must be green of the thing you claim.** A check has to READ what it says it covers. Hand-typed
   lists of tools have gone stale twice in this repo; generate them (see `scripts/gen_tool_docs.py`) or read
   them back from the server.

## After changing tool descriptions or the roster

```bash
uv run python scripts/gen_tool_docs.py
```

`docs/tools.md` is generated, not edited.

## Running against a real account

You need the environment described in the README: a Flow account migrated to `flow.google.com`, macOS, real
Chrome with the `gflow-cli` profile, and ffmpeg. Start with:

```bash
uv run video flow lane
```

If the verdict is not `MIGRATED`, stop: the tools here cannot work on that account, and retrying will not
change it.
