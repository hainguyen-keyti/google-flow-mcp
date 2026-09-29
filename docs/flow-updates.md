# Updating the MCP when Google changes Flow

Flow changes without notice: new options (a new model, a new length), new buttons, renamed labels, moved elements, a
new media host. This repo finds those changes by walking Flow and comparing what it sees with two baselines kept in the
repo, instead of finding them through a failed paid run.

## The survey

```bash
uv run video flow survey --project <project_id>
```

$0: it opens pages, sidebar sections, the composer's settings (clicking every option to read Flow's live price line)
and the clip editor's toolbar menus, then presses Escape. Every click it makes itself goes through `safe_click`, which
reads the element's label first and refuses anything that reads like spending, saving or deleting (`UNSAFE` in
`src/video/flow/survey.py`). It does change three things on the project it walks: Agent mode is turned off for the walk
and put back after; the prompt box is cleared; the composer is left on the last model and count it walked (every
generating tool sets its own before it runs). Use a project that holds at least one finished video, one scene and one character, so every route is walked.
A full walk takes 10 to 20 minutes, most of it in the settings pane.

For every route (home, project, each sidebar section, the composer in Frames and in Ingredients, the settings pane, the
clip editor and each of its menus, a scene, a character) it records:

- a screenshot, under `out/survey/<time>/`, with the whole survey in `survey.json` beside it;
- the UI labels: buttons, menu items, radios, tabs, sidebar items. Media titles, the project title, prompts, character
  names, emails and ids are dropped, so the baseline can live in a public repo;
- how many elements each selector the source relies on matches. The selectors are gathered from the source's own
  constants (`collect_selectors` in `src/video/flow/survey.py`), never listed by hand.

It then compares with:

| Baseline | Holds | Read by |
|---|---|---|
| `src/video/flow/flow_ui.json` | labels and selector counts per route | the survey only |
| `src/video/flow/flow_options.json` | every video model, resolution, length, count, aspect and its price; image models and aspects | `gen_video` (checks every argument against it, prices from it, its description is written from it) |

and prints one line per difference. Exit code 1 means Flow changed.

## Reading the report

| Line | Meaning | What to do |
|---|---|---|
| `selector lost` | a selector the code uses matched on that route before and matches nothing now | the code that uses it will fail: find the constant (the name is `module.CONSTANT`), open the screenshot, inspect the page, fix the selector, add a test pinned to what was measured |
| `label removed` / `label added` | a button or menu item appeared or went away | removed: grep the source for that text (many drivers click by name); added: a new feature, decide with the owner whether the MCP should expose it |
| `route missing` | the walk could not reach a page it reached before | usually a changed URL or ready selector in `session.py` or the module that opens that page |
| `route error` | a route failed during the walk (the error is quoted) | look at that route's screenshot; `--write` refuses a walk with a failed route |
| `option layout changed` | the settings pane shows rows this repo has never mapped, or a model offers different options in Frames and Ingredients | extend `options_from_walk` and `video.check_settings` for the new row, with a test built from the survey's own `survey.json` |
| `model added` / `model removed` / `model changed` | the composer offers different models, lengths or resolutions | `--write` updates `flow_options.json`; restart the MCP server (a running one read the file when it started), and `gen_video` takes the new options. Run a `dry_run` of a new cell, and one paid run on the cheapest new cell before telling anyone it works |
| `price changed` | Flow's price line shows a different price for a cell | `--write`, then restart the MCP server; until then `gen_video` refuses that cell, because a live price that differs from the surveyed one means a setting may not have taken |
| `image option changed` | image models, aspects or counts changed | `--write`; the image tools go through gflow, so check gflow supports the new value |

## After the fixes

```bash
uv run video flow survey --project <project_id> --write   # the current Flow becomes the baseline
uv run python scripts/gen_tool_docs.py                     # tool descriptions follow flow_options.json
uv run ruff check . && uv run ruff format --check . && uv run pytest
uv run python scripts/acceptance/mcp_smoke.py              # the read tools against the real Flow
```

`--write` refuses a walk that is not complete (a failed route, no model, no count, a cell whose price could not be
read, a layout it cannot map), since `flow_options.json` feeds `gen_video`'s money guard. Then run the survey once
more without `--write`: it must report 0 differences. Commit the baselines with the fixes, so
the diff of `flow_ui.json` and `flow_options.json` in git shows what Flow changed.

Media downloads are not covered by the walk: if downloads fail with HTTP 400 or 404, compare the listing's media URL
with the `src` the page itself uses (`download.asset_base` holds the host rewrite measured on 2026-09-29).
