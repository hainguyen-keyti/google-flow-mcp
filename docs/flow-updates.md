# Updating the MCP when Google changes Flow

Flow changes without notice: a new build every few days, new options (a model, a length), renamed labels, moved
elements, a new shape for a reply, a new media host. This repo finds those changes for $0, with three instruments,
instead of through a failed paid run. Follow the steps in order.

## 1. Ask what moved: `flow check`

```bash
uv run video flow check --project <project_id>      # 90 s; without --project, 40 s and the grid only
```

The MCP tool `flow_check` is the same read, for an agent to call before a batch. It compares:

| What | Live read | Baseline | Said as |
|---|---|---|---|
| the Flow build | the `k=` key of the AiSandboxAngularFrontend bundle in the page's script src (`flow/version.py`) | `build` in `src/video/flow/flow_ui.json`, written by `flow survey --write` | `build <live> (baseline <x>, changed since the baseline)` |
| the shape of the replies the server parses | the project grid (`UpteDb`, `nzlxg`, `Yizz8d`) and, with a project, its listing (`Zzl0ze`, `ngNC2`, `yBhWQ`, `HTrJv`, `tRARke`), folded into skeletons (`flow/wire.py`) | `src/video/flow/flow_wire.json`, generated from the test fixtures, which are replies Flow sent | `kind changed`, `position gone`, `shape changed`, `rpc not heard` (drift); `position added`, `kind appeared` (said, not drift) |
| the UI the drivers rely on | the home and project pages' labels and selector counts | the `home` and `project` routes of `flow_ui.json` | `label added`, `label removed`, `selector lost` |

Exit 1 means drift: a reply's shape moved, or a selector the drivers steer by stopped matching, and a paid tool may
misread Flow. A new build, or a label that came or went with one of Flow's banners, is said and is exit 0, since
most builds move nothing this repo reads. Every ledger row and every paid answer carries
`flow_build`, so a run that went wrong can be matched to the build it ran on.

Two more signals come from paid runs themselves: an answer or a ledger row with `flow_reply_read: false` (the submit
reply was heard but named no workflow: the reply's shape changed), and a tool error naming a selector.

## 2. See the whole of it: `flow survey`

```bash
uv run video flow survey                             # picks a project with a finished video, a scene and a character
uv run video flow survey --project <project_id>      # or name one
```

$0: it opens pages, sidebar sections, the composer's settings (clicking every option to read Flow's live price line)
and the clip editor's toolbar menus, then presses Escape. Every click it makes itself goes through `safe_click`, which
reads the element's label first and refuses anything that reads like spending, saving or deleting (`UNSAFE` in
`src/video/flow/survey.py`). It does change three things on the project it walks: Agent mode is turned off for the walk
and put back after; the prompt box is cleared; the composer is left on the Video tab, in the baseline's first mode,
on the first model, at x1 (every generating tool sets its own before it runs). The project and sidebar routes are
recorded after that mode is set, since the mode the last run left made 31 of the 72 differences on 2026-10-08.
A full walk takes 10 to 20 minutes, most of it in the settings pane.

For every route (home, project, each sidebar section, the composer in each mode, the settings pane, the clip editor
and each of its menus, a scene, a character) it records a screenshot under `out/survey/<time>/` (with the whole survey
in `survey.json`), the UI labels (media titles, the project title, prompts, character names, emails and ids are
dropped, so the baseline can live in a public repo) and how many elements each selector of the source matches (the
selectors are gathered from the source's own constants, `collect_selectors`, never listed by hand). It compares with
`flow_ui.json` (labels and selector counts per route) and `flow_options.json` (every video model, resolution, length,
count, aspect and its price; image models and aspects; what `gen_video` checks every argument against and prices
from) and prints one line per difference; exit 1 means Flow changed.

| Line | Meaning | What to do |
|---|---|---|
| `selector lost` | a selector the code uses matched on that route before and matches nothing now | the code that uses it will fail: find the constant (the name is `module.CONSTANT`), open the screenshot, inspect the page, fix the selector, add a test pinned to what was measured |
| `label removed` / `label added` | a button or menu item appeared or went away | removed: grep the source for that text (many drivers click by name); added: a new feature, decide with the owner whether the MCP should expose it |
| `route missing` | the walk could not reach a page it reached before | usually a changed URL or ready selector in `session.py` or the module that opens that page |
| `route skipped` | the project holds no finished video, scene or character, so that route was not walked | walk a project that holds all three (the survey picks one when `--project` is omitted); `--write` refuses a skipped route |
| `route error` | a route failed during the walk (the error is quoted) | look at that route's screenshot; `--write` refuses a walk with a failed route |
| `option layout changed` | the settings pane shows rows this repo has never mapped, or a model offers different options in Frames and Ingredients | extend `options_from_walk` and `video.check_settings` for the new row, with a test built from the survey's own `survey.json` |
| `model added` / `model removed` / `model changed` | the composer offers different models, lengths or resolutions | `--write` updates `flow_options.json`; restart the MCP server (a running one read the file when it started), and `gen_video` takes the new options. Run a `dry_run` of a new cell, and one paid run on the cheapest new cell before telling anyone it works |
| `price changed` | Flow's price line shows a different price for a cell | `--write`, then restart the MCP server; until then `gen_video` refuses that cell, because a live price that differs from the surveyed one means a setting may not have taken |
| `image option changed` | image models, aspects or counts changed | `--write`; the image tools go through gflow, so check gflow supports the new value |

## 3. A reply's shape moved: refresh the fixtures, not the parsers' guesses

The parsers are pinned by fixtures that are replies Flow sent, never typed (a typed `"CAE"` in every fixture hid the
null version field for three days in October 2026). To refresh them:

1. Capture: `VIDEO_CAPTURE_REPLIES=out/replies uv run video ...` on the cheapest paid run that exercises the reply
   (the composer writes every reply it hears, raw, one file per reply, as `<rpcids>_<time_ns>.txt`).
2. Redact into fixtures: `uv run python scripts/acceptance/redact_replies.py --from out/replies <rpcid> ...` writes
   `tests/fixtures/replies/<rpcid>.txt` with every uuid replaced by a stand-in and the request id blanked. Read the
   file before committing it: the prompt is kept.
3. Fix the parsers against the new fixtures (`tests/test_ingredients.py` holds the reader tests over them), then
   regenerate the wire baseline: `uv run python scripts/acceptance/wire_baseline.py` (`--check` says whether it is
   current; `tests/test_wire.py` fails when it is stale).

## 4. Make the current Flow the baseline

```bash
uv run video flow survey --project <project_id> --write   # the current Flow becomes the baseline, build and date recorded
uv run python scripts/acceptance/wire_baseline.py         # after the fixtures changed
uv run python scripts/gen_tool_docs.py                     # tool descriptions follow flow_options.json
uv run ruff check . && uv run ruff format --check . && uv run pytest
uv run python scripts/acceptance/mcp_smoke.py              # the read tools, flow_check among them, against the real Flow
```

`--write` refuses a walk that is not complete: a failed, skipped or unreached route (the baseline's routes must all be
walked), no model, no count, a cell whose price could not be read, a layout it cannot map; `flow_options.json` feeds
`gen_video`'s money guard. Then restart the MCP server (it read the files when it started), run `flow check` once
more: it must report no drift and the baseline's build. Commit the baselines with the fixes, so the diff of
`flow_ui.json`, `flow_options.json` and `flow_wire.json` in git shows what Flow changed.

Media downloads are not covered: if downloads fail with HTTP 400 or 404, compare the listing's media URL with the
`src` the page itself uses (`download.asset_base` holds the host rewrite measured on 2026-09-29).
