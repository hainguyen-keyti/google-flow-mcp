# What Flow offers against what the MCP exposes (2026-10-01)

The MCP when this was written: 44 tools (`docs/tools.md`): 11 reads, 3 downloads, 20 free account changes, 9 that
spend, and `gen_video`. This file lists what Flow can do, or what a production needs from Flow, that those tools do
not give, each with its evidence and a proposed surface. Plan AK built none of it: it forbade source changes.

**Status after plan AL (2026-10-01, the same day): 45 tools. G6, G3, G2, G1 and G4 are closed**, each marked below
with its commit and with what closing it measured. G5, G7, G8, sections 2 and 3 stand as written, except the rows
marked done.

Scope line, unchanged: the MCP exposes Flow and guards the money. How a film is made (script, shots, QA, edit) is the
skill set's job (`skill-design.md`).

Evidence keys: a job id (`ak-...`) is a paid test in `test-results.md`; `README` is the dossier; `file:line` is this
repo's source at `615bcac`.

## 1. Gaps that stand between the MCP and a consistent film

### G1. A voice cannot be attached to a generation

- Evidence: a voice ingredient beside one image (three in `ak-a7c`) returned the same voice on seven clips of two
  models (`ak-a1`, `ak-a2`, `ak-a7`, `ak-a7b`, `ak-v3~r2`, `ak-v4`, `ak-a7c`), and the same image and words without it
  returned another voice (`ak-k1`). Two voices in one Omni clip each went to the right speaker (`ak-f1`). The request carries voices in
  a field of their own: a custom voice by id, a preset by lowercase name (`[["17b8dafb-..."], ["achird"]]`).
- Today: `gen_video` takes `characters` and `media_ids` only. The only way to a voiced clip is a character with a voice
  attached, and a character redraws the face.
- Proposed: `gen_video(..., voices=[name or id, ...])`, Ingredients mode only.
  - Refuse before any click: a voice with no image, video or character ("An audio ingredient requires other
    ingredients to function."); a voice in Frames mode; more than one audio ingredient on Veo Lite or Fast, counting a
    voiced character; any voice on Veo Quality; a sixth voice on Omni ("Maximum audio ingredients reached (5
    allowed)"). The composer itself refuses the first, third, fourth and fifth (read for $0 on 2026-10-01); Frames
    mode simply offers no way to add a voice.
  - Read back before the click: one voice chip per voice asked, none refused. A refused chip is told by the icon
    `disabled-error-icon`, not by one class: an image chip gets `chip-container-disabled`, a voice chip `disabled`, a
    character chip only an inactive wrapper (the research probe read `disabled` alone and reported nine images
    accepted on Veo Quality, which accepts none).
  - Check after the click: the request's voice field holds exactly the ids or names asked.
  - `flow_voices` without a character: the "+" dialog lists the same voices.
- Tier 1 (money path). Proof: validator and fake-page tests red first; `dry_run` live for $0; one paid Veo Lite run
  with a voice (10) and one Omni run with two voices (12).
- **Closed by plan AL (`97d3c7f`)**: `gen_video(..., voices=[names])`, names as `flow_voices` lists them. Built as
  proposed, with what the build measured:
  - a voice chip carries no name; its hover card does ("play_arrow 0:06 voice_selection Achird"), so the read-back
    hovers each voice chip, and Flow's refusal is the part of the card before the player;
  - a voice of your own is a listing record with a speech arm (`audio/wav`, the preset it is built on, its name); the
    request names it by its workflow id and a preset by its lowercase id, at item `[7]` of the `MZZa6b` body, and the
    check parses that field rather than searching the body, since a prompt may name a voice;
  - a character that has a voice takes the one voice place of Veo Lite: refused live in Flow's words ("Maximum audio
    ingredients reached (1 allowed)"); only the count of named voices is refused before the browser opens;
  - after a paid run the clip's recipe is read back and compared (voices, images, characters);
  - paid once each: Veo 3.1 Lite, one image, LilyVoice, 10 credits; Omni 1.1 Flash 8 s, one image, LilyVoice and
    Achird, 12 credits; request and recipe carried exactly those voices.
  - Not built: `flow_voices` without a character (its signature was a non-goal).

### G2. Images reach a generation only as title mentions

- Evidence: `gen_video` and `gen_character` put each `media_id` in as an `@` mention by title, so a repeated title is
  refused and an auto-titled image needs an upload under a new name (`flow-film-director` skill, section 9). The "+" dialog attaches by
  picking the tile: three images and a voice went in that way (`ak-a7c`). Its list renders a window of rows, so an
  older image is found only through the dialog's search box, and some listed images are not offered at all
  (`lily_black_face.png`).
- Proposed: attach `media_ids` through the "+" dialog, search by title, pick the tile whose url tail is the image's
  (the rule `video.pin_frame` already uses for frames), and report an image the dialog does not offer by name.
- Tier 2. Proof: fake picker with twin titles and a missing image; live `dry_run` with three images.
- **Closed by plan AL (`002402e`)**: images go in through the "+" dialog, characters stay `@` mentions. Measured on
  the way:
  - the dialog's search takes a whole title; a click adds only the ACTIVE row (the first), any other row becomes
    active and "Add to prompt" adds it;
  - a bar chip's thumbnail is a signed url whose path ends with the image's WORKFLOW id, so each image chip is
    checked against the image asked for before the click;
  - the price line does not move for a refused chip (10 on Veo Lite beside a refused fourth image), so the bar is
    read in `setup` and again right before the price check;
  - the `@` picker is the same dialog and keeps the category it last showed, under which it offers no character:
    characters are mentioned first;
  - on Veo 3.1 Lite a mentioned character takes one of the three image places (a character and two images fill it);
  - twin titles are refused only when their urls end alike; `lily_black_face.png` is still offered by no row, and
    the tool says so.

### G3. Nothing reads back what a clip carried

- Evidence: the project listing holds, per generation, the model key, the input images with a role (1 start frame,
  2 end frame, 4 reference) and the voices (a custom voice's id, a preset's name). Read for $0 it showed the image
  only for `ak-a3` (the character had been dropped), three references and the voice for `ak-a7c`, `achernar` for
  `ak-k2`, both voices for `ak-f1`, and two frames under `veo_3_1_interpolation_lite` for `ak-n2b`.
- Today: the proof that a request carried what was asked exists only inside the run that made it (`body_check`: the
  mode, the length, the resolution and the reference ids), and it knows nothing of voices.
- Proposed: `clip_recipe(project_id, media_id, workflow_id=None)` answering `{model_key, frames, reference_images,
  voices, entities, source_clip}`; and the spending tools compare that recipe with what was asked before they answer.
- Tier 3 for the read, tier 1 where it joins the money path's verdict.
- **Closed by plan AL (`a5753d8` the tool, `97d3c7f` the verdict)**: `clip_recipe(project_id, media_id,
  workflow_id=None)` answers `{model_key, kind, frames, reference_images, voices, characters, source}`, each input
  named; an upscale left by a download is not taken for the newest version. `gen_video` and `gen_character` read it
  back after a paid Ingredients run and report a clip that dropped or gained an input as an error.

### G4. Inputs Flow silently drops are refused without the reason

- Evidence: a character typed into a Frames prompt shows as a chip, is quoted, and is dropped from the request
  (`ak-a3`). A video ingredient turns the request into an edit that copies the source and ignores a new line and a
  voice (`ak-n3`); an Omni edit asked to change speech burns the words in as a subtitle (`ak-n1-edit-line`).
- Today: `gen_video` refuses frames with ingredients ("Frames and ingredients do not mix"), which is right, and says
  nothing of why. `clip_edit` accepts any prompt.
- Proposed: say what Flow would do ("Flow sends a Frames request without the character: use Ingredients, or a start
  frame alone"); `clip_edit`'s description states that speech cannot be changed.
- Tier 3 (wording and one validator).
- **Closed by plan AL (the commit that carries this line)**: the refusal of a character beside frames says Flow would
  send the request without the character at full price; `clip_edit` says an edit cannot change what is said;
  `clip_extend` says 8 + 7 s; a voice with nothing beside it is refused with Flow's sentence "An audio ingredient
  requires other ingredients to function."

### G5. A failed generation is one opaque error

- Evidence: `ak-v3` failed with statuses `[6, 2, 4]`, no reason code, 0 credits, and passed on an identical retry.
  Veo's audio filter is random and uncharged (README section 7). The tools refuse a job id that already has a ledger
  row, so a retry needs a new id, while every description warns against a new id after an error or a timeout.
- Proposed: every spending tool answers a typed outcome: `{charged, code, retryable, advice}`; codes at least
  `AUDIO_FILTERED` and `NO_REASON` (retry unchanged, at most twice, the bound `skill-design.md` uses),
  `UNSAFE_GENERATION` (never retry the same inputs), `PROMINENT_PEOPLE`, `UNUSUAL_ACTIVITY` (stop everything). A retry names the failed job (`retry_of`) and is
  accepted only when that job's row shows `spent: 0`.
- Tier 1 (it decides when a second click is allowed).

### G6. An overlay on the page looks like a dead button

- Evidence: 2026-10-01 16:57, a cookie notice bar appeared over the composer's bottom row; the click on the Settings
  trigger timed out after 8 s with Playwright's own note that the bar intercepts pointer events (`ak-v1`, first
  attempt, $0). No source file knows the bar. Through the MCP itself that evening: `flow_credits` still read the
  balance, and `gen_video(dry_run=true)` answered only `TimeoutError: Locator.click: Timeout 8000ms exceeded.`
- Decided by the owner, 2026-10-01: "Cho phép MCP tự bấm nếu có hiện." The MCP may click this notice's own button
  ("OK, got it") whenever the bar shows.
- Proposed: before the composer is driven, look for an element covering its bottom row. Flow's cookie notice
  (`glue-cookie-notification-bar`) is dismissed with its own button and the click is recorded in the tool's answer;
  any other overlay fails the call at once with its text and its buttons, and is never clicked.
- Tier 2.
- **Closed by plan AL (`f6d2725`)**: Playwright's locator handler presses the notice's own button whenever the bar
  shows and the tool's answer names it under `dismissed_notices`; anything else covering the Settings trigger is
  named in the error and never clicked. The dismissed state lives in localStorage `glue.CookieNotificationBar`.

### G7. A generation blocks the call for minutes

- Evidence: runs take 3 to 8 minutes; once in this project a client-side "try again" arrived while the job had in
  fact finished. Other Flow MCPs split submit, status and collect (lane 4 section 2).
- Proposed: `job_submit` (the same guards, returns after the request is seen leaving), `job_status`, `job_collect`
  (downloads, writes the ledger's `done` row). The blocking tools stay as they are for simple callers.
- Tier 1 (the one-click rule and the ledger row before the click must hold across two calls).

### G8. Prices, lengths and caps are prose in tool descriptions

- Evidence: two changes in Flow's page and one in Chrome in three days (README section 10); Veo's lengths differ
  between Google's table (4, 6, 8 s) and this account's composer (no length row on any Veo model, in Frames or
  Ingredients, 2026-10-01); the image and voice caps exist only as chip refusals (Omni 7 and 5, Veo Lite and Fast 3
  and 1, Veo Quality none); the survey already reads most of the rest (`flow survey`).
- Proposed: `flow_capabilities(project_id)` answering the live map: per model and mode the lengths, resolutions,
  counts, price line, and the caps for images and voices; tool descriptions point to it instead of quoting numbers.
- Tier 3.

## 2. Flow features with no tool

| Feature | Evidence | Proposed |
|---|---|---|
| Voice preview audio | the voice maker's Preview answers a pending record and the page then fetches a wav from `flow-content.google/audio/<id>`; 16 previews and one saved sample were captured for 0 credits | `voice_preview(preset, performance, sample)` answering a wav, so a voice is heard and measured before it is used |
| 360p draft, then upscale | a 360p clip's Download menu offers "720p Upscaled" (url tail `_720p_upsampled`, 0 credits) and no 1080p; `clip_download` knows only the 1080p tail (`ak-d1`) | `clip_download` takes the upscale the clip offers; `gen_video` says what a draft is good for |
| Extend with speech | 8 + 7 = 15.0 s, the words asked for, the voice not held, the extension's own file downloadable this time (`ak-x1`) | done in plan AL for the length: `clip_extend` says 8 + 7 s and no longer "overlapping the source's last second"; what the voice does is left out until the owner has judged it by ear |
| Video as an ingredient | `ak-n3`: rpc `jIps6`, 20 credits, an edit | keep it out of `gen_video`; it is `clip_edit` under another door |
| Delete media | lane 4 (one other MCP, one proxy) | `media_delete` |
| Character update | lane 4 | rename, regenerate the portrait, add the second body image |
| Run a Flow Tool | lane 4 | `tool_run` beside `flow_tools` |
| Agent: approve or reject a quoted spend, instructions | lane 4 | extend `agent_send` |
| Image upscale 2K, 4K | lane 4 | `image_upscale` |
| A budget across calls | this campaign needed one by hand | `budget_set(max_credits)` refusing any spend past it |
| Avatars, collections | none on this account | not now |

## 3. Hazards in today's code (fix when the file is next touched)

| Where | Hazard | Evidence |
|---|---|---|
| `src/video/flow/composer.py:37-39`, used at `:693` | `button[aria-label*='ngredient']` also matches an attached chip, whose label is "Ingredient"; clicking it removes the chip | $0 probe 2026-10-01; the research driver uses the exact label "Add ingredients to the prompt box" |
| `src/video/flow/composer.py:605-611` | the chip counter counts every chip in the prompt box: ledger rows of an empty composer read `"chips": 4` | v7 ledger rows, `left_over` |
| `src/video/flow/video.py:299`, `src/video/flow/ingredients.py:403` | the submit check looks only at gflow's four rpcs; a composer submit with a video ingredient leaves as `jIps6` | `ak-n3` |
| `clip_edit` | no live price is read before the click (its own description says so) | tool description |
| 720p and gif downloads | still go through Chrome's own download, after which Chrome 154 crashes on this Mac (eight crash reports on 2026-10-01); `clip_download` knows the 1080p url tail only, not `_720p_upsampled` | plan AJ, `ak-d1`; possibly playwright issue 42506, whose reports name Windows only |
| gflow pin | repo 0.78.0, `uv tool` 0.73.1, upstream 0.81.0 | lane 4 section 1 |
| Whisper on a silent clip | not MCP code, but any QA tool must know: it returns a stock sentence | `ak-c1`, `ak-n2a` |
| `src/video/flow/parsers.py` `_record_fields` | a voice saved with `character_make_voice` is listed by `flow_media` as `kind: "video"` (any record of eight or more fields is one); `parsers.custom_voices` now tells it by its speech arm, the `kind` is unchanged | found in plan AL T5, 2026-10-01 |
| `scripts/gen_tool_docs.py` `GROUPS` | the groups are typed by hand, so a new tool lands under "Ungrouped" (`clip_recipe` does) | plan AL T3; the script was outside that plan's radius |
| `docs/mcp-manual-test.md` | written for 44 tools; `clip_recipe` and `gen_video`'s `voices` are not in it | plan AL T6; the file was outside that plan's radius |
| the `@` picker | it is the "+" dialog and keeps the category that dialog last showed, under which it offers no character; `ingredients.generate` mentions characters before it opens the dialog, any new caller must do the same | $0 probes `out/al/t4.json`, `t4b.json`, 2026-10-01 |

## 4. Proposed order (plans to write after approval)

| Plan | Holds | Tier | Paid proof |
|---|---|---|---|
| AL (done 2026-10-01) | G6 overlays first (the bar blocked every composer tool), then G3 recipe read-back, G2 picker attach, G1 voices, G4 wording | 1 | 22 credits spent of a ceiling of 40 |
| AM | G5 typed outcomes and `retry_of` | 1 | 0 to 10 |
| AN | G7 submit, status, collect | 1 | about 20 |
| AO | G8 capability map, section 2 rows, section 3 hazards | 2 to 3 | 0 |

Each plan follows the repo's rules: red test first, mutants on every guard, a live run of the exact case that failed,
one commit per task.

## 5. Plan AL in outline (written, approved and done on 2026-10-01; kept as the outline it was)

- Goal: a generation can carry voices and images the way the composer's "+" dialog attaches them, what it carried
  can be read back, and a page overlay is named instead of timing out.
- Non-goals: no submit, status, collect split; no typed outcomes; no QA tool; no skill.
- Invariants touched (tier 1): the live price held to `max_credits`, the ledger row before the one click, the balance
  on both sides. New: every ingredient asked for is on the bar and enabled before the click, and the request's voice
  field equals what was asked.
- Tasks:
  1. $0 probes: the overlay's selector and box; the "+" dialog's Voices list across its window of rows; whether an
     image tile there carries the listing url tail the Frames picker's tiles carry.
  2. G6: detect an element covering the composer's bottom row; dismiss Flow's cookie notice with its own button (the
     owner's decision), refuse on any other overlay with its text.
  3. G1: `voices` on `gen_video`, with the refusals of section 1, the chip read-back and the request check.
  4. G2: `media_ids` attached through the "+" dialog by url tail; twin titles and an image the dialog does not offer.
  5. G3: `clip_recipe`, and the spending tools' verdict reads it.
  6. G4: the two refusal and description wordings.
- Proof: red tests and mutants per guard; `dry_run` live for each new shape ($0); one paid Veo Lite run with a voice
  (10) and one Omni run with two voices (12); the roster test and `docs/tools.md` regenerated.
- Credit ceiling to ask for: 40.
