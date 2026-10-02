# Manual test of the `video` MCP server: 49 tools, with prices and guard rails

A pass to run by hand, or to hand to another agent that calls the tools over MCP. Every result shape below was
**measured on a real account** between 2026-09-14 and 2026-09-29, not inferred from the code. For the generated
reference of every tool and its arguments, see [tools.md](tools.md).

The order matters: free reads first, then tools that change a scratch project, and only then the ones that spend
credits, reading the balance before and after each.

## 0. Before you start

- **Open a new Claude Code session in this repo.** A running `video` server does NOT reload code. Killing the server
  process makes the next call run new code, but the open session keeps the old tool descriptions and `instructions`
  (measured 2026-09-15), so an agent in that session reads stale guidance.
- Call the two cheapest tools: `flow_lane()` must return `verdict: MIGRATED`, and `flow_credits()` returns the
  balance. Every balance read also appends a line to `out/credits.jsonl`.
- The balance **can move between readings on its own** (Flow tops a Pro plan up daily), so read it again right before
  any spend.
- **Every call drives a real Chrome and waits for Flow to answer.** Measured over MCP on 2026-09-15: a call that opens
  one page takes 12-17 s, one that opens two or three pages about 50 s (`flow_tools()`, `project_rename`,
  `scene_restore`, `scene_delete`), and one `gen_r2v` took 292 s. Slow is not broken: do not call again.
- The window opens off the left edge of the screen with about 40 px visible; set `VIDEO_BROWSER_OFFSCREEN=0` to see it.
- **Tool errors reach the agent with their reason** (cookies and tokens scrubbed), for example
  `LookupError: scene ... is already in the trash`. Unknown arguments are refused before any browser opens.

## 1. Tier 0: let the machine check first, 0 credits

If tier 0 is red, stop: the fault is below anything you would test by hand.

| Command | Expected |
|---|---|
| `uv run pytest -q` | every test green, no `failed` |
| `uv run python scripts/acceptance/ledger_integrity.py` | `fail=0`, offline, no browser |
| `uv run python scripts/acceptance/mcp_smoke.py` | `fail=0`, 2 to 3 minutes; calls the read tools FOR REAL over MCP, plus one `gen_character` refused because its `out_dir` is outside `out/` (no browser, no spend) |
| `uv run python scripts/acceptance/flow_coverage.py --project <id> --character` | the CLI matrix; creates and then deletes an "acceptance probe" project |
| `uv run python -m video.probes.canary --project <id>` | 11 UI anchors still in place; exit 1 when Google changes the page |

## 2. Make a scratch project, so real data is never touched

1. Call `project_create(title="mcp manual test")`. It returns `{"id": "...", "rpcids": [...], "title": "..."}`.
2. **Write that `id` down.** Every later step uses exactly this id.
3. At the end, call `project_delete(project_id=<scratch id>)`.

**`project_delete` permanently deletes clips, ingredients and prompts.** Read the id twice before calling it. Never
paste a real project's id into this tool.

## 3. The 49 tools

Prices were measured on the Pro plan. Groups A and B are safe on any project; group C belongs in the scratch project.

### A. Read only, $0, always safe

| Tool | Arguments | What a correct result looks like |
|---|---|---|
| `flow_lane` | none | `{"verdict": "MIGRATED", "projects": <n>, "roots": {...}}` |
| `flow_projects` | none | list of `{id, title, created, cover_media_id, thumbnail_url}`. **An id need not be a UUID**: one account has `8822142b-ca75-46b7-aac8-03d2831_backfill` |
| `flow_credits` | none | `{"balance": <int>, "raw": [...]}` |
| `flow_capabilities` | none | no browser, answers at once: `{note, video: {measured, modes, aspects, counts, models: {<model>: {label, modes, resolutions, seconds, chooses_length, credits_x1: [{resolution, seconds, credits}], image_ingredients, voice_ingredients}}, caps_measured}, character_video, image, editor}`. Every number is the one the tools refuse and charge by (the surveyed `flow_options.json` and the measured constants), with its date; it is not a live read: `gen_video(dry_run=true)` is the live price of one cell |
| `flow_media` | `project_id`, `all_versions=False`, `kind`, `since`, `limit`, `brief=False` | `{"meta": {id, title}, "media": [...], "models": [...]}`. `all_versions=true` adds a `versions` key: records with `type` and `workflow_id`. Filters: `kind` is `video` or `image`; `since` takes an epoch or an ISO date (a date without a zone is read in local time); `limit` keeps the N newest rows of EACH list, newest first; `brief=true` drops `url` and cuts `prompt` to 120 characters plus an ellipsis. With any filter the result adds `media_total` and `truncated` (and `versions_total` with `all_versions=true`); `truncated` counts dropped ROWS, so `brief` alone leaves it false. Unfiltered, the order is Flow's listing order, NOT newest first: read `created`. Bad values (`kind="clip"`, `limit=0`, `since="yesterday"`) are REFUSED. Measured 2026-09-17: unfiltered 28,378 characters, `all_versions=true` 122,919 |
| `flow_characters` | `project_id` | list of `{entity_id, name, portrait_media_id, portrait_workflow_id}`. Download the portrait with `portrait_media_id` (`null` while the listing has no record of the image); a new project gives `[]` |
| `flow_tools` | `project_id` optional | the community tool gallery, the same in every project; its size changes over time. With no `project_id` the server opens the first project on the grid, because Flow only loads the gallery inside a project |
| `flow_uploads` | `project_id` | `{"count": n}` and nothing else |
| `scene_list` | `project_id`, `include_trashed=False` | list of `{scene_id, title, trashed, created, updated}`. Trashed scenes are hidden unless `include_trashed=true` |
| `flow_voices` | `project_id`, `entity_id` | list of `{name, description, custom}`: Flow's 30 presets plus every saved custom voice (`custom: true`, listed first). It needs a character because the character page is the ONLY place Flow lists voices; the list renders a window at a time, so the tool scrolls |
| `scene_clips` | `project_id`, `scene_id` | the timeline read from the listing: `{scene_id, title, trashed, aspect, seconds, clips}`, `clips` in play order, each `{position, clip_id, title, seconds}`, `position` from 0. `clip_id` is the timeline clip's id, not a media id: adding one media twice gives two `clip_id`s. The page's `Total duration` label changes before Flow saves (measured 2026-09-17), so read this tool again after every change |
| `clip_recipe` | `project_id`, `media_id`, `workflow_id` optional | what one clip was made from, read back off the listing: `{media_id, workflow_id, model_key, kind, frames, reference_images, voices, characters, source}`. `kind` is `frames`, `ingredients`, `derived` (an edit or an upscale), `extend` or `unknown`; a voice row says whether it is `custom`. Defaults to the newest version of the media. An image keeps no recipe and is refused |
| `job_status` | `job_id` | where a job that `job_submit` started stands: `{job_id, state, workflow_id, media_id, project, quoted_credits, credits_before, credits_now, age_s, outcome}`. `state` is `rendering`, `ready` (fetch it with `job_collect`), `not_listed`, `ambiguous`, `settled` (the job's ledger row is the answer, read with no browser) or `in_another_call`. It reads the listing and the balance: no click, no keystroke, no ledger row. A `job_id` no ledger under `out/` holds is an error that opens with `outcome code=NOT_SUBMITTED` |

### B. Writes files on this machine, $0

| Tool | Arguments | Notes |
|---|---|---|
| `flow_download` | `project_id`, `media_id`, `out_dir` | returns a path inside `out/`; `out_dir` must be inside `out/` (outside it, or naming a file, is refused before a browser opens). `media_id` is the listing `id` from `flow_media`; a workflow id is refused |
| `clip_download` | `project_id`, `media_id`, `quality="1080p"`, `out_dir`, `workflow_id` | `out_dir` must be inside `out/`. Defaults to the NEWEST finished version; pass `workflow_id` to fetch one version (measured 2026-09-28: all four versions of an edited clip, each distinct). 1080p measured 0 credits. **`quality="4k"`: Flow greys it out on this Pro account** (measured 2026-09-29 on every clip tried; Flow's table offers it from Ultra at 50), so it is refused before any click and costs nothing. **A 360p clip has another menu** (measured 2026-10-03: "270p Animated GIF", "360p Original size", "720p Upscaled"): `quality="720p"` on it fetches the upscale, 720x1280, 0 credits; the default 1080p is refused with those three items, and its own 360p file comes with `flow_download`. A media the listing marks `listed: false` lives only inside a scene (an extension does): it is refused at once, and `scene_download` fetches it |
| `clip_reconcile` | `project_id`, `out_dir` | `out_dir` must be inside `out/` (a ledger outside it escapes the `job_id` guard). Closes the books for orphaned editor jobs; returns `{"ledger": <absolute path>, "ledger_exists", "ledger_rows", "jobs"}`. `jobs: []` means clean only when `ledger_exists` is `true` and the path is the ledger you meant. Verdicts: `done` (for `clip_edit` only: exactly one new finished version on the source clip carrying the job's prompt, not already claimed as `generated` by another job in the same ledger, and no rival job on the same clip with the same prompt still open or closed without a version on that clip, unless its last row is a `failed` written by `clip_reconcile`; written to `outputs`; a 1080p upscale or a copy made by extend is never claimed), `failed` (balance unchanged AND no new record in the project; `clip_extend` can end `failed` too), `unknown` (for a person to decide; for `clip_extend` every case that is not `failed`; also while a version is still generating, with two versions, with a rival job as above, when the listing no longer holds a record of the source clip the job saw, and for an old `opening` row missing its workflow set or prompt, or with an empty workflow set), `skipped` (gen or agent jobs, or editor jobs of another project given `project`; never written). A ledger with nothing to judge returns at once, without Chrome. The `spent` a reconcile row writes is the balance change since the job opened and can include other spends: never add those rows up as a total |
| `job_collect` | `job_id` | fetches the finished clip of a `job_submit` job at 720p beside the job's ledger and writes the job's ONE settled row: `{job_id, state: "collected", media_id, path, spent, spent_from, credits_before, credits_after, workflow_id, outcome}`. `spent_from` is `bracket` (the job's own balance change) or `quoted` (another job could have moved the balance meanwhile, so the price quoted before the click is written instead). A clip still `rendering`, or `not_listed` yet, is answered as such with nothing written: call again later. A job already settled is answered from its row with no browser, so a second collect fetches nothing and writes nothing. It never clicks and never types |

### C. Changes Flow data, $0, scratch project ONLY

| Tool | Arguments | Notes |
|---|---|---|
| `project_create` | `title` | returns `{"id", "rpcids", "title"}` |
| `project_rename` | `project_id`, `title` | reads the name back from the grid and returns `{"id", "title"}`; a different name on the grid is an error |
| `project_delete` | `project_id` | **permanent**; scratch ids only |
| `scene_create` | `project_id`, `title` | returns a dict with `scene_id` |
| `scene_delete` | `project_id`, `scene_id` | "move to trash": the scene stays in `scene_list(include_trashed=true)` and `scene_restore` brings it back. Grid tiles carry no scene id, so the tool SCROLLS the virtual grid (`div.cdk-virtual-scrollable`, measured 2026-09-17 drawing 7 of 8 tiles) until a tile with the exact name shows, up to 60 steps. Name uniqueness comes from the listing: two live scenes with one name, a blank or invisible-only name, or two matching tiles drawn at once are refused (a same-name tile in another window is caught after the click, by re-reading the listing); after the click the listing is read again, and another scene whose flag changed is named |
| `scene_restore` | `project_id`, `scene_id` | takes a scene out of the trash, re-reads the listing and returns `{"scene_id", "trashed": false, "rpcids", "active"}`. Trash tiles carry no scene id either, so the tool scrolls the trash (measured 2026-09-17: 16 of 37 tiles drawn) until the exact name shows, and **refuses a blank or invisible-only name, more than one trashed scene with that name in the listing, or two matching tiles drawn at once**, or a full scroll with no match |
| `scene_rename` | `project_id`, `scene_id`, `title` | renames and re-reads the listing to confirm. The scene page has exactly one editable field in its header (measured 2026-09-16) and the tool insists on exactly that one; a blank or invisible-only name is refused, since the scene tools find each other by name |
| `scene_add_clip` | `project_id`, `scene_id`, `media_id` | puts a project clip at the **END** of the timeline, which is how a scene becomes a multi-shot film: add in play order. The picker carries no media id, so the tool maps `media_id` to its **title** from the listing and matches it exactly; it refuses a blank title, another media with the same title, or a title shown more than once in the picker. Flow saves only when it answers the add, 11 to 14 s after the click, so the tool waits and re-reads the listing; returns `{scene_id, media_id, title, position, clip_id, seconds, clips, rpcids}`. **After an error past the click the clip may still be in: read `scene_clips` first and do not add again unread**, or the clip plays twice |
| `scene_set_aspect` | `project_id`, `scene_id`, `aspect` (`9:16` or `16:9`) | sets the film's frame; a new scene is 16:9. Returns `{scene_id, aspect, changed, rpcids}`: already right gives `changed: false` and no click; a page showing a different aspect from the listing is refused rather than clicked at random |
| `scene_move_clip` | `project_id`, `scene_id`, `clip_id`, `position` | moves a clip (by its `clip_id` from `scene_clips`) to a 0-based position, shifting the clips between by one. Flow reorders only by drag and drop, so the tool zooms out until both places show, then drags; it checks the order the page sends and the listing read back. A clip already in place gives `moved: false`. Returns `clips` in the new order |
| `scene_remove_clip` | `project_id`, `scene_id`, `clip_id` | removes a clip from the timeline through the right-click menu; Flow **asks for no confirmation** (measured 2026-09-17), so the tool clicks only when the page shows as many clips as the listing and the right-click selected the right one. The media stays in the project and `scene_add_clip` can put it back at the end. Returns `removed_position` and the remaining `clips` |
| `scene_download` | `project_id`, `scene_id`, `out_dir=None` | downloads the whole scene as ONE film, not a clip: measured 2026-09-16, two 8 s clips gave one **16.0 s** mp4. The film is rendered in the page (measured 2026-09-17: a 40 s film took 33 to 42 s), so the tool waits by the film's length and says at once when the click did not start an export. The file is written under a temporary name and then renamed, so the returned name never holds half a film. An empty scene is refused and `out_dir` must be inside `out/`. Also returns `seconds`, the listing's `clips` for comparison, and `attempts`: 2 means the first try broke midway and the tool fetched it again in a new session. The scene page has NO quality menu; for 270p/720p/1080p use `clip_download` |
| `character_set_voice` | `project_id`, `entity_id`, `voice` | attaches a voice from `flow_voices` (preset or custom) to the character, rpc `rzMKMb`, about 20 s. The voice belongs to the CHARACTER: attach once and every later generation uses it. A name not in the list is refused with the real list |
| `character_make_voice` | `project_id`, `entity_id`, `preset`, `performance`, `name`, `sample`, `attach=True` | makes a CUSTOM voice: a preset plus a line describing the delivery, clicks Preview, waits for Flow to synthesize it (rpc `no0P6`, about 24 s), then saves (`lt8g5`, `mYWVGd`); measured 2026-09-18: 44 s, $0. Use it when the description matters: `character_set_voice` sends only the preset's NAME in the character update (read from the rpc body), so text written next to a preset does not travel. `sample` is at most 120 characters; `attach=false` saves without changing the attached voice. A saved voice sample shows up in `flow_media` as a `kind: "video"` row named after the voice, with `url` and `prompt` `null`. Saving before Flow answers sends NOTHING, so the tool reports an error instead of claiming a save |
| `character_clear_voice` | `project_id`, `entity_id` | removes the voice; the character is silent again |
| `clip_save_frame` | `project_id`, `media_id` | saves a clip's first frame as a project IMAGE (rpc `maseQ`, title `Saved frame from <clip>`), to use as the `initial_frame` of the next shot (download it with `flow_download` and give the file to `gen_i2v`; passing the media id directly has never been tried); measured 2026-09-18: 53 s, $0, a 1080x1920 image. The editor paints the clip onto a CANVAS about 5 s after the page is ready and Flow sends whatever the canvas holds, so the tool waits for a lit canvas and REFUSES while it is blank: an early click saved a fully BLACK image (YAVG 0, against 111 for the clip's first frame). Flow indexes it about 40 s after the click, so the tool waits up to 90 s for the listing. **It does not always work**: 5 real clicks gave 3 images, twice a snackbar and no media after 160 s; the error tells the two cases apart (`save_started`) |
| `scene_save_clip` | `project_id`, `scene_id`, `clip_id` | copies a timeline clip out to the grid as its own media (right click, `Save to Project`, rpc `Sc7aEb`); the timeline is unchanged. Also waits about 40 s for the listing |
| `character_create` | `project_id`, `prompt` OR `image`, `name`, `personality`, `wait=90` | **costs no credits** and takes exactly one of the two: with `prompt` the portrait is drawn by Nano Banana 2; `image` is a local path (png, jpg, jpeg, webp) uploaded through the New character page (measured 2026-09-16: a close-cropped face was accepted; Flow once silently refused a photo of a person in lace, and the tool says "created no character"). Returns `portrait.workflow_id`, **not a media id**: get the media id from `flow_characters` |
| `character_delete` | `project_id`, `entity_id` | deletes the character permanently |
| `flow_upload` | `project_id`, `path` | a local file path; returns `{file, bytes, rpcids, tiles, media_id, workflow_id, size_bytes, ...}`. `media_id` works with `flow_download`. `bytes` is the local file and `size_bytes` the copy Flow keeps (Flow recompresses images: a 139 KB PNG became a 5.6 KB JPEG). `tiles` counts tiles in the open view, not uploads. When Flow's reply is not seen the tool settles by the listing: an answer with `found_by: "listing"` is the one new image of that file name; an error saying nothing was uploaded means a second try is safe; an error naming two new images, or one about a video, means look at `flow_media` before uploading again |
| `agent_mode` | `project_id`, `enabled` | the chip is the **"Agent" button in the prompt bar**, right of "+". While on, Flow hides the composer's settings; the five gflow tools (`gen_t2v`, `gen_i2v`, `gen_r2v`, `gen_t2i`, `gen_i2i`) turn it off before they run and back on after (measured 2026-09-29: Agent on, `gen_t2i` made its image, `agent_mode_restored: true`; about 19 s added, 35 s when it was on), and `gen_character` does the same. `clip_extend` and `clip_edit` do not: turn it off first |

### D. Spends credits, run only on purpose

Every spending tool **requires a `job_id`** (except `gen_character` and `gen_video` with `dry_run=true`, which click nothing). A
`job_id` that already has any row in any `ledger.jsonl` under `out/` (any letter case in the file name) is refused
before a browser opens; a `job_id` that looks like a session secret (a cookie name or an `Authorization` header) is
refused; a `job_id` running in another call is refused, and then you wait and call again with the SAME `job_id`, never
a new one. Every answer of a real run carries `outcome {code, charged, retryable, advice}` and every error of one
opens with `outcome code=... charged=... retryable=...`: read `charged` first; `UNKNOWN` and `CHARGED_NO_OUTPUT` mean
money may be gone, and an error with no outcome line was refused before any job started. Only when `gen_video` or
`gen_character` says `retryable=yes` may you call again under a NEW `job_id`, with the same project and prompt and `retry_of` set
to the refused `job_id` (the server checks the ledger first and allows two retries of one original at most). `clip_edit`, `clip_extend` and `agent_send` refuse a prompt with a newline; an all-space prompt is refused;
the `out_dir` of `clip_edit` and `clip_extend` must be a folder inside `out/` with no part named `ledger.jsonl` (any
case) and no part that is an existing file.

`clip_extend`, `clip_edit` and `agent_send` answer `balance_moved {kind, measured, moved, note}` when the balance
moved by anything other than the measured price; when the call ends in an error, the job's ledger row holds it.

| Tool | Measured price | Notes |
|---|---|---|
| `gen_video` | Flow's live price line, which must not exceed the `max_credits` you pass. Surveyed 2026-09-29: Omni 1.1 Flash 720p 4/6/8/10 s = 7/10/12/15, 360p = 4/5/6/7; Veo 3.1 Lite 10, Fast 20, Quality 100 (8 s, 720p); x2-x4 multiply | every video option in one tool. Text alone runs Frames; `start_frame` and `end_frame` (project image media ids) fill Frames' Start and End, the picker tile chosen by the image's listing url; `characters` and `media_ids` run Ingredients, and `voices` (names from `flow_voices`) ride beside at least one image or character. `model`, `aspect`, `resolution` and `duration` (omni-flash only), `count` 1-4. **Call `dry_run=true` first**: $0, returns the live quote and the settings. A real run needs `job_id` and `max_credits`; the settings are read back before the click and the submit body is checked afterwards for the mode and length asked |
| `job_submit` | the same price as `gen_video`, read off Flow's live line and held to the `max_credits` you pass. Measured 2026-10-03: omni-flash 360p 4 s **4**, twice | `gen_video`'s own run, one clip per call and no dry run, that answers once the submit request has left instead of waiting for the render: `{job_id, state: "started", workflow_id, quoted_credits, credits_before, body_check, outcome: {code: "STARTED"}}`. Then `job_status` until `ready`, then `job_collect`. Measured on two jobs submitted back to back: each call answered in **153 s**, the balance had dropped by the price right after each submit (not at the finish), both clips rendered after the page had closed and were ready within about 4 min. A `job_id` with rows is refused before a browser opens, with that job's outcome (`outcome code=DONE charged=4`). A job Flow refuses after the call has left never shows: `job_collect` settles it as nothing generated ten minutes after the submit, with no reason and no retry; when the reason matters, use `gen_video`. A refusal Flow gives while the call is still on the page is settled there as `gen_video` does, and `retry_of` is taken for it. While the job's clip may still show (from the call until it is collected, or for an hour after it was settled with no clip), `gen_video` and `gen_character` are REFUSED for the same project and prompt (a blocking run takes the one new clip carrying its prompt, which could be this job's); a second `job_submit` of the prompt is fine |
| `gen_t2i`, `gen_i2i` | **0 credits** (nano2) | counts against a daily image quota, not credits |
| `gen_t2v` | **15** with no model (omni-flash 10 s, count 1); **10** with `model="veo-lite"` (8 s) | `count` multiplies the price (veo-lite `count=2` is 20); `count` is 1-4, `aspect` only `9:16` or `16:9` |
| `gen_r2v` | **12** with no model (omni-flash, always 8 s, measured 2026-09-15); **10** with `model="veo-lite"` | through gflow r2v runs 8 s only: leave `duration` out. For 10 s from images, upload them and use `gen_character` with `media_ids`. Images: omni-flash up to 7, veo-lite and veo-fast up to 3, veo-quality none; more is refused before any spend. Flow refuses images of people in underwear |
| `gen_character` | **12** with no model (omni-flash, 8 s); **10** with `model="veo-lite"`; **15** for omni-flash `duration=10`; **20** for `model="veo-fast"` by Flow's price line, never spent | arguments `project`, `prompt`, `characters` (entity ids), `media_ids` (images already in the project), at least one of the two; `model`, `aspect` (`9:16` or `16:9`), `duration`, `dry_run`, `job_id`, `out_dir` (optional, inside `out/`, keeps a film's clips and ledger together; outside `out/` or naming a file is refused before a browser opens, dry run included). **Call `dry_run=true` first**: $0, returns `quoted_credits`, `price_ok`, `chips` (characters carry their entity id, images their WORKFLOW id), `prompt_text`, `composer_left`. Character names and image titles must be unique in the project. A real run returns `media_id`, `path`, `spent`, `body_check`. Images alone, 10 s: measured 2026-09-28, 15 credits, body `abra_r2v_10s`, a 10.0 s clip that matches the image. Flow can refuse under its content filters without charging, with the reason in the error (status 4, `PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED` for a character made from a real person's photo): do not retry the same inputs |
| `gen_i2v` | **15** with no model (omni-flash 10 s, measured 2026-09-18 over MCP, 105 s end to end) | works from **gflow 0.78.0**. **Pass `aspect`**: omitted is 9:16 (gflow's own default for t2v, i2v and r2v), and Flow CROPS an image of another shape to fit: a 16:9 image became a 720x1280 clip with the subject pushed half off the edge. `end_frame` (a local image) interpolates between two frames through a separate Flow submit: **measured 2026-09-18 also 15 credits, 119 s**, the clip starts and ends on the two images. **ONLY omni-flash 10 s is measured**: another model or `duration` with `end_frame` is REFUSED |
| `clip_extend` | **10** | makes a new scene and copies the source clip into it (outputs with `role: copy`). **Flow greys Extend out on some clips**: on omni-flash clips (measured 2026-09-16, with a one-variable control where a veo-lite clip ran), and on a veo-lite clip after two Omni edits and a 1080p upscale (measured 2026-09-29). The tool then refuses before the click, at no cost. On a fresh veo-lite clip it ran (measured 2026-09-29, 10 credits): the new clip's own file answered HTTP 400 and `clip_download` could not open it, so fetch it with `scene_download` on the returned `scene_id` (15.0 s of video stream for an 8 s source) |
| `clip_edit` | **20** measured; Flow's own table lists Omni Flash Edit at **40** | Omni 1.1 Flash, text-guided video edit. It does NOT read the price line before clicking (unlike `gen_character`): that line shows only while hovering Start and read 12 for an edit that charged 20. The guard is the balance read before and after, returned as `credits_before` and `credits_after`, plus `balance_moved`: budget 40, usually 20 |
| `agent_send` | **0** in every measured send where the agent generated nothing (5 sends to 2026-09-28) | if the agent generates media, that generation's price applies; ask before calling |

## 4. A ready prompt to hand to another agent

Paste this block as is, replacing `<SCRATCH ID>` with the id from section 2:

```
You have an MCP server "video" that drives Google Flow. Test it and report back.

GUARD RAILS, not to be broken:
- Work only in project <SCRATCH ID>. Touch no other project.
- Never call project_delete with any id other than <SCRATCH ID>.
- Do not call a credit-spending tool (gen_video, job_submit, gen_t2v, gen_i2v, gen_r2v, gen_character, clip_extend,
  clip_edit, agent_send). gen_character and gen_video with dry_run=true are free and allowed. If spending seems
  needed, STOP and ask me.
- If any tool reports that Google flagged unusual activity, STOP completely: no retry, no new sign-in, tell me.

Do these in order, and after each step paste the JSON returned, verbatim:
1. flow_lane, flow_projects, flow_credits
2. flow_media for <SCRATCH ID>, then flow_media with all_versions=true
3. flow_characters, flow_tools, flow_uploads, scene_list for <SCRATCH ID>
4. project_rename to "mcp manual test 2", then flow_projects to see the new name
5. scene_create, scene_list, scene_delete, scene_list with include_trashed=true, then scene_restore and scene_list again
   5b. On that scene: scene_set_aspect 9:16, scene_add_clip three differently titled videos of <SCRATCH ID>,
   scene_clips (order and positions 0, 1, 2), scene_move_clip the first clip to position 2, scene_remove_clip the
   clip at position 1, scene_clips again, then scene_download: the film must last exactly the seconds of scene_clips
6. character_create with a prompt of your choice, flow_characters, character_delete
7. flow_upload a small image already on this machine, then flow_uploads to see the count go up
8. agent_mode on, then off
9. clip_reconcile for <SCRATCH ID>
10. character_create with a prompt of your choice and a unique name, then gen_character with dry_run=true for that
    character: check quoted_credits is 12, chips carry the right entity id, composer_left is empty
11. clip_download with quality="4k" on any clip: it must be refused as greyed out, with 0 credits spent

Finally: list which tools worked, and which failed or errored, with the exact error text.
```

## 5. When you decide to run the spending part

1. Call `flow_credits()` and note the balance.
2. Call exactly **once**, for example `gen_t2v(prompt="...", project="<SCRATCH ID>", job_id="try-t2v-1",
   model="veo-lite", aspect="9:16")`. `job_id` is required and yours to choose; with no `model` the server uses
   omni-flash 10 s (15 credits).
3. Call `flow_credits()` again.
4. Check the evidence:
   - `out/ledger.jsonl`: the job has a `submitted` row and then `done`, with `credits_before`, `credits_after`, `spent`
     and `code` (the git sha that ran it).
   - `out/credits.jsonl`: two timestamped lines matching the balance before and after.
5. **Never click again on a hunch.** Flow can take the click, send the request, and then make no job and charge
   nothing. Decide success or failure from `flow_media` plus the balance, never by calling again. If you do call the
   same job again, keep the SAME `job_id`: the ledger refuses it at once (`already has ledger rows`) and nothing is
   charged twice.

The same without waiting for the render, as run on 2026-10-03 (8 credits for two clips):

1. `gen_video(..., dry_run=true)` with the settings you mean to pay for: the quote must be the price you expect (4 for
   omni-flash 360p 4 s).
2. `job_submit(project, prompt, job_id="try-submit-1", max_credits=4, model="omni-flash", resolution="360p",
   duration=4)`: `state: "started"`, a `workflow_id`, `outcome.code: "STARTED"`; the ledger holds a `submitted` and a
   `started` row, and `flow_credits()` already reads 4 lower.
3. `job_status(job_id="try-submit-1")` until `state` is `ready` (`rendering` and `not_listed` mean ask again later;
   it took under 4 minutes).
4. `job_collect(job_id="try-submit-1")`: `state: "collected"`, a `path` inside the job's own folder, one `done` row.
   Open the file: it must be the clip of THIS prompt. With another job in flight `spent_from` is `quoted` and the
   outcome says `charged_from: "quoted"`.
5. `job_collect` once more answers `state: "settled"` from the row at once, and `job_submit` with the same `job_id`
   is refused with `outcome code=DONE`.

## 6. Measured traps you will meet by hand

- **A running MCP server does not load new code, and an open session keeps the old tool descriptions.** After a code
  change, open a new session.
- **A project's sidebar renders after the page is ready**, about 2000 ms (a project with uploads) to 3000 ms (an empty
  one). The tools wait correctly; if you drive the browser yourself, do not read the sidebar too early.
- **The Uploads section filters on the client**: clicking it makes no request and does not change the URL.
- **`clip_download` takes the newest version by default.** For the version one edit produced, take its `workflow_id`
  from `flow_media(all_versions=true)`.
- **Deleting a scene is a soft trash**; it stays in the listing.
- **The composer's add-ingredient button attaches one ingredient per prompt.** Typing `@` attaches several:
  `gen_character` attached a character plus an image in one prompt (measured 2026-09-17).
- **Flow refuses every generation that draws a person wearing a product being sold**: the submit goes out, a long
  wait, no record, 0 credits, no message.
- **Flow's prominent-people filter judges the output frames**, so it blocks some runs and not others: the same
  character made from a real person's photo failed once with no reason on the page, passed once, and was blocked once
  with `PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED` (2026-09-17); both failures cost 0 credits.
- **A rendition can 404 right after generation**, even permanently for one workflow while another workflow of the same
  media still downloads.
- **Do not run `gflow auth login`** on this account, even when gflow suggests it.

## 7. Clean up after the test

1. `agent_mode(project_id=<SCRATCH ID>, enabled=false)` if it was turned on.
2. `project_delete(project_id=<SCRATCH ID>)`.
3. `flow_projects()` to see that the scratch project is gone.
4. Review `out/ledger.jsonl` and `out/credits.jsonl` if the spending part was run.

## 8. How to report a bug so it can be fixed

For each error, send four things: the tool name, the arguments passed, the exact JSON or error message returned, and
the project id. For an error while spending, add the matching lines of `out/ledger.jsonl`.
