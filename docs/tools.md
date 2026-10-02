# MCP tools

Generated from the running server by `scripts/gen_tool_docs.py`; do not edit by hand. Prices are the ones measured on the account this was built for, and they are part of each tool's own description, which is what an agent reads before spending.

**49 tools.**

## Read, free

Nothing here changes anything on the account.

### `flow_lane`

**Arguments**: none

Which lane the profile is on (LABS, MIGRATED, SIGNED_OUT). Free.

### `flow_projects`

**Arguments**: none

List the account's Flow projects (id, title, created). Free.

### `flow_credits`

**Arguments**: none

Current Flow credit balance. Free.

### `flow_capabilities`

**Arguments**: none

What Flow offers and what it costs on this account, as data and with no browser: for each video model its modes, resolutions, lengths, the price of every cell at x1, and how many image and voice ingredients it takes; the counts and aspects; the image models; gen_character's prices by length; and the measured prices of clip_extend, clip_edit and agent_send. Every number is the one the tools themselves refuse and charge by, each part with the date it was measured; it is the surveyed table, not a live read. The live price of one cell is gen_video with dry_run=true, and a paid call is refused when Flow's own price line is over its max_credits, so budget from this table and let the live line decide. Free.

### `flow_media`

**Arguments**: `project_id`, `all_versions` (optional), `kind` (optional), `since` (optional), `limit` (optional), `brief` (optional)

A project's media (id, kind, model, size, url), meta and models, always as one object, in Flow's own listing order, which is NOT sorted by age: read `created` (epoch seconds) to tell what is new. all_versions=true adds a versions list holding every generation record: each Omni edit or upscale stacks another version onto the SAME media id, and only that list shows them, so it is how you find the clip an edit produced. A whole project is big (measured 2026-09-17: 28,378 characters, and 122,919 with all_versions), so four filters cut it: kind is 'video' or 'image'; since keeps rows made at or after epoch seconds or an ISO date (2026-09-17, or 2026-09-17T08:30+07:00; a date with no zone is this machine's day); limit keeps that many rows of EACH list, the newest by created, and puts them newest first; brief=true drops each row's url and cuts its prompt to 120 characters plus an ellipsis, which is where most of the weight sits. Ask for any of them and the answer also carries media_total (and versions_total with all_versions), how many rows the project holds BEFORE filtering, plus truncated, true when any row was left out; brief cuts fields, not rows, so it leaves truncated false. A filter it cannot honour is refused, never silently ignored. Free.

### `flow_characters`

**Arguments**: `project_id`

A project's characters: entity_id, name, portrait_media_id (the id flow_download accepts; null when the portrait is not in the listing) and portrait_workflow_id. Free.

### `flow_voices`

**Arguments**: `project_id`, `entity_id`

The voices a character can speak with, read off the character's own voice selector: 30 presets on this account (measured 2026-09-18), each a name and a one-line description like 'Female, youthful, mid-high pitch', plus every voice character_make_voice has saved here, which are listed first and say so under `custom`. Needs a character to read them from, because that page is the only place Flow shows them. Free.

### `flow_tools`

**Arguments**: `project_id` (optional)

The community Tools gallery (id, name, author, tags), the same in every project. project_id is optional: Flow only loads the gallery inside a project, so without one the first project on the grid is opened, which costs one extra page load. Free.

### `flow_uploads`

**Arguments**: `project_id`

How many items the project's Uploads view holds, as {count}. Free.

### `scene_list`

**Arguments**: `project_id`, `include_trashed` (optional)

List a project's scenes (Scenebuilder); trashed ones on request. Free.

### `scene_clips`

**Arguments**: `project_id`, `scene_id`

Read one scene's timeline as Flow stores it: its clips in the order the film plays them, each with its position (counted from 0), clip_id, title and seconds, plus the scene's aspect ratio (9:16 or 16:9, null when the listing names neither) and its total seconds. A clip_id names one clip on this timeline, not a project media: adding the same media twice gives two clip_ids, and scene_move_clip and scene_remove_clip take a clip_id. The page's own Total duration label moves before Flow has stored a change (measured 2026-09-17), so read this again after changing the timeline instead of trusting the label or an earlier answer. Free.

### `clip_reconcile`

**Arguments**: `project_id`, `out_dir` (optional)

Close out editor jobs that spent credits without recording an outcome, by checking the listing and the balance. It reads Flow and writes the ledger, it never generates. Run this after a clip_extend or clip_edit died mid-flight, otherwise the spend has no outcome against it. Replies with the ledger it read (absolute path), ledger_exists, ledger_rows and one verdict per open job in jobs: jobs [] means nothing is left open in THAT ledger, so check that it exists; an error means the check itself failed. Verdicts: done (clip_edit only: exactly one new version on the source clip carries the job's own prompt and is held by no other job's generated outputs in that ledger, counting one still rendering; that version is finished; and no rival is left in that ledger, a job that named that clip and prompt and is open, or closed holding no version there, unless its last row is clip_reconcile's failed; it is written into outputs), failed (balance unchanged AND no record in the project that the job had not already seen when it opened; clip_extend can end here too), unknown (left open for a person: every clip_extend that is not failed, since nothing ties its new clip to the job; also while the version is still rendering, when two versions or a rival could own it, when the listing holds none of the source clip's records the job saw, or when the job's row predates recorded workflows and prompt or lists no workflows), skipped (never written: a gen_* or agent_send job, which names no clip, so check it with flow_media and flow_credits; or another project's editor job, whose project is given, so run clip_reconcile on that project). It opens the browser only when the ledger holds a job of this project it can judge, otherwise it answers at once. The spent it writes is the balance change since the job opened, which can include other spends and the balance moving on its own, so never add those rows up as a total. Free.

### `clip_recipe`

**Arguments**: `project_id`, `media_id`, `workflow_id` (optional)

What one clip was made from, read back off the project listing: the model key Flow ran, the kind of run (frames, ingredients, derived for an edit or an upscale, extend), the start and end frame, the reference images, the voices and the characters, each with its id and its name, and for a derived or extended clip the clip it came from. Use it after a paid generation to check that the clip carried what was asked: Flow drops a character typed into a Frames prompt without saying so (measured 2026-10-01), and this is where that shows. A voice is listed as Flow recorded it: a preset by its lowercase name, a voice made on this account by its id (a uuid), which is what `custom` true means; `name` is null when this project's listing does not name it. Defaults to the newest version of the media, passing over the upscale a 1080p download leaves beside it; pass workflow_id (from flow_media with all_versions=true) to read one specific version. An image keeps no recipe and is refused. Free.

### `job_status`

**Arguments**: `job_id`

Where a job that job_submit started stands, by its job_id. state is rendering (Flow lists the clip and it is not finished), ready (finished: fetch it with job_collect), not_listed (no record yet, or Flow refused the job), ambiguous (Flow named no workflow at the submit and the new clips carrying the prompt cannot be told apart: several of them, or one that another unnamed job of the same prompt could own), settled (the job's ledger row is written: the answer is that row, read with no browser) or in_another_call (the call that submitted it, or a job_collect, has not answered yet; that answer holds the state and a note only). It reads the project listing, the balance and the ledgers under the out folder; it never clicks Start generation, never types and writes no ledger row (the balance it read is appended to out/credits.jsonl, as every balance read is), so it may be asked as often as needed. A clip Flow keeps listed and unfinished is answered rendering for as long as it stays so; age_s says how long. The answer carries the workflow_id, the media_id once the clip is listed, the price quoted before the click, the balance before the click and now, the seconds since the submit, and the job's outcome. A job_id no ledger under the out folder holds, a job with no started row (a blocking tool ran it, or its job_submit ended before the request left) and ledgers that cannot be read are errors that open with an outcome line. A look at Flow took 47 s (measured 2026-10-03). Free.

## Write a file on this machine, free

Free for Flow, but they put bytes on your disk, always inside `out/`.

### `flow_download`

**Arguments**: `project_id`, `media_id`, `out_dir` (optional)

Download one media item to out_dir as <media_id>.<ext>. out_dir must be inside the out folder, and a folder it names is created for you. Free.

### `clip_download`

**Arguments**: `project_id`, `media_id`, `quality` (optional), `out_dir` (optional), `workflow_id` (optional)

Download a clip rendition from the editor: gif (270p), 720p, 1080p or 4k (upscaled by Flow). Defaults to the NEWEST finished version of the media; pass workflow_id (from flow_media with all_versions=true) to fetch one specific version, such as the clip a particular edit produced. 1080p measured 0 credits. A 360p clip has another menu (measured 2026-10-03): its 720p is an upscale, fetched the same way at 0 credits (720x1280), and it has no 1080p and no 4k, so asking for one of those, the default included, is refused with what the menu offers; its own 360p file comes with flow_download. 4k is an upscale Flow's price table offers only from the Ultra plan, at 50 credits; on this Pro account the Download menu shows it greyed out (measured 2026-09-29 on every clip tried), so a 4k request is refused before any click and costs nothing.

### `scene_download`

**Arguments**: `project_id`, `scene_id`, `out_dir` (optional)

Download a scene as ONE film, the whole timeline rather than a single clip: two 8 s clips came back as one 16.0 s mp4 (measured 2026-09-16). The film is built inside the page before it is handed over, and a 40 s film took 33 to 42 s to build (measured 2026-09-17), so the call waits while Flow shows the export running, as long as the film's length warrants, and fails fast when the click starts no export. A scene with nothing on its timeline is refused, and out_dir must sit inside out/. The file is written under a temporary name and renamed only once whole, so a partial film never sits under the name returned. The result carries the seconds and clips the listing gives the scene, to check the film against, and attempts: 2 means the first try failed on the way and the film was fetched again in a fresh session. A scene offers no quality choice, so use clip_download for a single media and its 270p, 720p, 1080p or 4k renditions. Free.

### `job_collect`

**Arguments**: `job_id`

Fetch the clip of a job that job_submit started and write the job's one settled row in the ledger. A finished clip is downloaded beside the job's ledger, at 720p or, for a 360p job, as the 360p file it is, and answered as state collected with path, media_id and spent; a clip still rendering, or not listed yet, is answered as such with nothing written, so call again later. For an Ingredients job the clip's recipe is read back first, as gen_video does: a voice, image or character the clip dropped is an error, with the clip fetched and its row written all the same (recipe_check). spent is the job's own balance bracket (spent_from bracket) only when no other job could have moved the balance since the submit; otherwise it is the price Flow quoted before the click (spent_from quoted, and the outcome says charged_from quoted), which is the usual case when several jobs render at once; a job settled as an error on such a shared balance says nothing of what it was charged. A job with no record 10 minutes after its submit is settled as an error: nothing generated when the balance did not move, unknown otherwise; Flow's reason for a refusal is not heard here. With no workflow named at the submit, a clip another job's rows name is never taken, and neither is one of several candidates, nor one that another job of the same prompt, still open and named no workflow either, could own. A job already settled is answered from its row with no browser, so collecting twice never fetches or writes twice. It never clicks Start generation and never types in the prompt box, so it cannot start or pay for anything: the credits were spent by job_submit; the one thing it may click is the editor's own download (the clip's version entry and the Download menu), when every direct link to the file fails. Every answer and every error carries the job's outcome. Fetching a 4 s 360p clip took 47 s, and a second collect of it answered from the row at once (measured 2026-10-03). Free.

## Change the account, free

These edit real projects, characters and scenes. Try them on a scratch project first.

### `project_create`

**Arguments**: `title` (optional)

Create a project on the grid, optionally renaming it. Free.

### `project_rename`

**Arguments**: `project_id`, `title`

Rename a project, then confirm the new title on the project grid (the listing flow_projects reads). Replies {id, title} with the title as the grid lists it, and fails if the grid shows another. Free.

### `project_delete`

**Arguments**: `project_id`

Delete a project permanently (clips, ingredients, prompts). Free.

### `character_create`

**Arguments**: `project_id`, `prompt` (optional), `image` (optional), `name` (optional), `personality` (optional), `wait` (optional)

Create a character, then set name and personality. Give exactly one of prompt (a face described in words; the portrait comes from Nano Banana 2, credit-free) and image (a local png, jpg, jpeg or webp of a face; the upload becomes the portrait). Flow has refused photos without a message, for example of people wearing lace (measured 2026-09-13), while a close-up portrait was accepted. The reply's portrait.workflow_id is NOT a media id: call flow_characters for the portrait's media id, which flow_download accepts. Free.

### `character_delete`

**Arguments**: `project_id`, `entity_id`

Delete a character entity permanently. Free.

### `character_set_voice`

**Arguments**: `project_id`, `entity_id`, `voice`

Give a character one of Flow's preset voices (names from flow_voices), so a generation starring that character can speak. The voice belongs to the CHARACTER, not to a generation: set it once and every later run of that character uses it. Measured 2026-09-18: free, about 20 s, and the page then shows the voice with a play button. A name the selector does not offer is refused with the list it does. Free.

### `character_make_voice`

**Arguments**: `project_id`, `entity_id`, `preset`, `performance`, `name`, `sample` (optional), `attach` (optional)

Make a VOICE OF YOUR OWN and give it to a character: a preset from flow_voices plus a written performance ('giọng nữ Sài Gòn, nhỏ nhẹ, nhí nhảnh, khoảng 20 tuổi'), saved under a name so later characters can reuse it. Use it rather than character_set_voice when the words matter: that tool sends the character update with the preset NAME only (measured rpc body, 2026-09-18), so a performance written next to a preset is not part of what gets attached. Measured 2026-09-18: free, about 45 s, because Flow synthesises a preview first and the save does nothing until that answer lands. sample is the line Flow speaks in the preview, 120 characters at most. attach false saves the voice without changing the character's current one. The saved sample also shows up in flow_media as a row whose kind is video, titled with the voice name, carrying no url and no prompt: that row is the voice, not a clip. Free.

### `character_clear_voice`

**Arguments**: `project_id`, `entity_id`

Take the voice off a character, leaving it silent again. Free.

### `scene_create`

**Arguments**: `project_id`, `title` (optional)

Create a scene (Scenebuilder), optionally titled. Free.

### `scene_rename`

**Arguments**: `project_id`, `scene_id`, `title`

Rename a scene, confirmed by re-reading the listing. A title that is blank or only invisible characters is refused, since the scene tools find a scene by its exact title. Free.

### `scene_delete`

**Arguments**: `project_id`, `scene_id`

Move a scene to the project's trash; scene_restore brings it back. Grid tiles carry no scene id, so the tile is found by the scene's exact title, once the grid shows one tile per active scene (waited for, up to 15 s). A title that is blank or only invisible characters, a title two scenes share, and a grid that never shows that many tiles are all refused rather than guessed. Free.

### `scene_restore`

**Arguments**: `project_id`, `scene_id`

Bring a trashed scene back from the project's trash (undoes scene_delete), confirmed by the listing. The trash shows no ids, so the tile is found by the scene's exact title, once the trash shows one tile per trashed scene (waited for, up to 15 s). A title that is blank or only invisible characters, a title more than one trashed scene shares, and a trash that never shows that many tiles are all refused. Free.

### `scene_add_clip`

**Arguments**: `project_id`, `scene_id`, `media_id`

Put one of the project's clips at the end of a scene's timeline, which is how a scene becomes a film made of several clips: add them in the order the film should play them. The picker carries no media id, only the media's title, so the media id is resolved to its title through the listing and refused rather than guessed when that title is blank, when another media in the project shares it, or when the picker shows it more than once. Flow stores a clip only when it answers the add, 11 to 14 s after the click (measured 2026-09-17), so the call waits for that answer and then reads the listing back: the result carries the clip's position (counted from 0), its clip_id, the scene's seconds, every clip in order, and answered, which is false when Flow's own answer never arrived and the listing alone showed the clip on the timeline. If the call fails after the click, the clip may still land: read scene_clips first and never add it again before you have, or the film gets it twice. Free.

### `scene_move_clip`

**Arguments**: `project_id`, `scene_id`, `clip_id`, `position`

Move one clip, named by its clip_id from scene_clips, to a position counted from 0 on a scene's timeline; the clips in between shift by one. Flow reorders a timeline only by drag and drop, so the editor is zoomed out until both places are on screen and the clip is dragged there, then the order the page sends and the listing read back must both match the one asked for. A clip already at that position is left alone. The result carries every clip in its new order. Free.

### `scene_remove_clip`

**Arguments**: `project_id`, `scene_id`, `clip_id`

Take one clip off a scene's timeline, named by its clip_id from scene_clips; the clips after it move up one position. Flow asks nothing before deleting (measured 2026-09-17), so the call acts only on the clip the listing places at that position once the editor shows as many clips as the listing holds, and checks the change the page sends before reading the listing back. The media itself stays in the project and scene_add_clip can put it back at the end. The result carries the remaining clips in order. Free.

### `scene_set_aspect`

**Arguments**: `project_id`, `scene_id`, `aspect`

Set the aspect ratio a scene's film is exported in: 9:16 (portrait) or 16:9 (landscape); a new scene starts at 16:9 (measured 2026-09-17). The editor offers one toggle, so a scene already at the ratio asked for is left alone, and a scene whose page shows another ratio than Flow's listing is refused rather than toggled blind. Confirmed by reading the listing back; scene_clips shows the ratio as aspect. Free.

### `scene_save_clip`

**Arguments**: `project_id`, `scene_id`, `clip_id`

Copy one clip of a scene's timeline (clip_id from scene_clips) onto the project grid as its own media, so other scenes and tools can use it: a clip that lives only inside a scene is invisible to flow_media. The timeline is not changed. The answer's rpcids are only what was overheard after the click, not proof: a live run copied the clip without Sc7aEb showing up in that window, so the listing row is the evidence. Measured 2026-09-18: free, and the new media appears about 40 s later, which this tool waits for. Free.

### `clip_save_frame`

**Arguments**: `project_id`, `media_id`

Save the frame the clip editor opens on as an IMAGE of the project, and answer its media_id. That image is how a later shot continues this one: download it with flow_download and give that file to gen_i2v as initial_frame, which takes a path on this machine. Passing the media_id straight to gen_i2v has never been run, so do not assume it works. Measured 2026-09-18: free, about 55 s when it works: the grid shows the image titled 'Saved frame from <clip>' about 40 s after the click, which this tool waits 90 s for. It does NOT always work: of five live clicks on 2026-09-18 three left an image (media 1765128f, 68413992 and 508cb3cd on project 118aece2) and two left none, both of those after Flow had raised its 'Saving frame' notice, so a refusal here can also mean a save Flow took and lost. It never invents a media id. It saves the frame the editor shows, which is the clip's start. The editor draws into a canvas about five seconds after the page is ready and Flow uploads whatever that canvas holds, so the tool waits until it has painted and REFUSES a blank editor rather than store a black picture (a run that clicked too early saved 1080x1920 of pure black). Free.

### `flow_upload`

**Arguments**: `project_id`, `path`

Upload a local image or video into a project. Free.

### `agent_mode`

**Arguments**: `project_id`, `enabled`

Turn Flow's agent mode on or off for a project; leaving it on hides the composer settings. Free.

## Spend credits

Each of these needs a `job_id`, writes a ledger row before the click, and refuses an id that any ledger under `out/` has already seen.

### `gen_video`

**Arguments**: `project`, `prompt`, `job_id` (optional), `max_credits` (optional), `model` (optional), `aspect` (optional), `resolution` (optional), `duration` (optional), `count` (optional), `start_frame` (optional), `end_frame` (optional), `characters` (optional), `media_ids` (optional), `dry_run` (optional), `out_dir` (optional), `voices` (optional), `retry_of` (optional)

One video from Flow's composer with any option it offers; it spends credits and is ledgered. Text alone runs Frames; start_frame (and end_frame) are project image media ids for the first and last frame; characters (entity ids) and media_ids (project images) run Ingredients. Frames and ingredients do not mix: given a character beside a frame, Flow itself sends the request without the character, at full price (measured 2026-10-01), so that is refused here. voices are voice names as flow_voices lists them (a preset such as Achird, or one character_make_voice saved): each rides as an audio ingredient beside at least one image or character, and a character that has a voice takes one of the model's voice places. Voices each model takes (read off the composer on 2026-10-01): omni-flash 5, veo-lite 1, veo-fast 1, veo-quality none. In an Ingredients run every chip is read back before the click, the request's own voice field is checked as it leaves (every voice asked must ride; one nobody asked for is said under unasked_voices in body_check and is no error), and once the clip is fetched its recipe is read back: a voice, image or character Flow dropped is an error even though it was paid. Models and x1 prices from Flow's price line on 2026-09-30: omni-flash (Omni 1.1 Flash): 360p 4s 4/6s 5/8s 6/10s 7, 720p 4s 7/6s 10/8s 12/10s 15; veo-lite (Veo 3.1 - Lite): 10 credits, 8 s, 720p; veo-fast (Veo 3.1 - Fast): 20 credits, 8 s, 720p; veo-quality (Veo 3.1 - Quality): 100 credits, 8 s, 720p. count 1-4 multiplies the price; aspect one of ['16:9', '9:16']. flow_capabilities answers this table as data. resolution and duration apply to omni-flash only (defaults 720p and 8 s). The money guard is Flow's own price line, read right before the single click: a real run needs max_credits and is refused when the live price is over it; dry_run=true reads that price and the settings for free, clicks nothing and needs no job_id or max_credits. Every setting is read back before the click, and the submit request is checked afterwards for the mode and length asked: a mismatch is reported as an error even though it was paid. x2-x4 return every clip in outputs. Flow's Agent mode is turned off for the run and put back after. out_dir must be inside out/. Allow 3-8 min for a real run, 1-2 min for a dry run.When the balance moved by anything other than the measured price, the answer carries balance_moved {kind, measured, moved, note}, or, when the call ends in an error, the job's ledger row does: read it before assuming the price in this description still holds. job_id is required: use a new one for each new job, and keep the SAME one when calling again after an error or a timeout. Any job_id already in a ledger under the out folder is refused before a browser opens, so a retry never pays twice; that refusal means the job may already have spent credits, so check flow_media and flow_credits before starting it under a new one. A job_id still running in another call is refused too: wait for that call to finish and call again with the SAME job_id, never a new one. The answer of a real run carries outcome {code, charged, retryable, advice}, and an error opens with 'outcome code=... charged=... retryable=...' (the whole outcome is its structured content): charged is what the balance bracket says left the account, UNKNOWN and CHARGED_NO_OUTPUT mean money may be gone, and an error with no outcome line was refused before any job started. When an error says retryable=yes (Flow failed the job and charged nothing, with code AUDIO_FILTERED or NO_REASON), it may pass as it is: call again with the same project and prompt, a NEW job_id, and retry_of set to the refused job_id. The server checks the ledger before a browser opens: a job is retried once (a second retry of it is refused once the first has clicked, and while the first runs in this server), and one original job gets at most 2 retries; for any other code a new job_id after an error is still wrong. A real run is refused while a job that job_submit started with the same project and prompt may still show its clip (not collected yet, or settled with no clip within the last hour): a blocking run takes the one new clip that carries its prompt, which could be that job's. Fetch that job with job_collect first, or start this one with job_submit too.

### `job_submit`

**Arguments**: `project`, `prompt`, `job_id`, `max_credits`, `model` (optional), `aspect` (optional), `resolution` (optional), `duration` (optional), `start_frame` (optional), `end_frame` (optional), `characters` (optional), `media_ids` (optional), `out_dir` (optional), `voices` (optional), `retry_of` (optional)

gen_video that does not wait for the render: the same composer run, options and guards, one clip per call, left once Flow's own submit request has been seen going out. It spends credits and is ledgered: the price is the one gen_video names, read off Flow's live price line right before the single click and refused when it is over max_credits. Models and x1 prices from Flow's price line on 2026-09-30: omni-flash (Omni 1.1 Flash): 360p 4s 4/6s 5/8s 6/10s 7, 720p 4s 7/6s 10/8s 12/10s 15; veo-lite (Veo 3.1 - Lite): 10 credits, 8 s, 720p; veo-fast (Veo 3.1 - Fast): 20 credits, 8 s, 720p; veo-quality (Veo 3.1 - Quality): 100 credits, 8 s, 720p. aspect one of ['16:9', '9:16']. flow_capabilities answers this table as data. The answer is state started, with the workflow_id Flow named for the clip and outcome code STARTED: the clip is then Flow's to render, and several submitted jobs render at once. Ask job_status with the same job_id to see where it stands and fetch it with job_collect once it is ready, which writes the ledger's settled row; until then what the job cost is not on record. There is no dry run here (gen_video with dry_run=true quotes for free) and one clip per call. Frames, ingredients and voices follow gen_video's rules, and the request is checked as it leaves: a mismatch is an error that still names the started job; the recipe of an Ingredients clip is read back by job_collect. When the submit request is never seen leaving, or Flow fails the job while this call is still on the page, the call stays and settles the job as gen_video does, with gen_video's own answer or error and no state started; a refusal heard that way may say retryable=yes, and retry_of is taken here as gen_video takes it. A job Flow refuses after this call has left never shows in the listing, and Flow's reason is heard only by a call that waits: job_collect settles such a job as nothing generated, with no reason and no retry, so use gen_video when the reason matters. While a submitted job may still show its clip (not collected yet, or settled with no clip within the last hour, and from the moment this call starts), gen_video and gen_character are refused for the same project and prompt, since a blocking run takes the one new clip carrying its prompt and that could be the submitted job's; a second job_submit of the prompt is fine. A blocking tool run beside a rendering job also reads its own spend off a balance that job can still move (Flow charged at the submit in both measured jobs, so what is left to land is a refund if Flow fails it): its spent and balance_moved can be off by that. Measured 2026-10-03 on two omni-flash 360p 4 s jobs submitted back to back: each call answered in 153 s (most of it setting the composer up; allow 2-4 min), Flow named the workflow both times, the balance had dropped by the price when it was read right after each submit, not at the finish, and both clips went on rendering after the page had closed and were ready within about 4 min of their submits. out_dir must be inside out/. job_id is required: use a new one for each new job, and keep the SAME one when calling again after an error or a timeout. Any job_id already in a ledger under the out folder is refused before a browser opens, so a retry never pays twice; that refusal means the job may already have spent credits, so check flow_media and flow_credits before starting it under a new one. A job_id still running in another call is refused too: wait for that call to finish and call again with the SAME job_id, never a new one. The answer of a real run carries outcome {code, charged, retryable, advice}, and an error opens with 'outcome code=... charged=... retryable=...' (the whole outcome is its structured content): charged is what the balance bracket says left the account, UNKNOWN and CHARGED_NO_OUTPUT mean money may be gone, and an error with no outcome line was refused before any job started. When an error says retryable=yes (Flow failed the job and charged nothing, with code AUDIO_FILTERED or NO_REASON), it may pass as it is: call again with the same project and prompt, a NEW job_id, and retry_of set to the refused job_id. The server checks the ledger before a browser opens: a job is retried once (a second retry of it is refused once the first has clicked, and while the first runs in this server), and one original job gets at most 2 retries; for any other code a new job_id after an error is still wrong.

### `gen_t2v`

**Arguments**: `prompt`, `project`, `job_id`, `model` (optional), `aspect` (optional), `count` (optional), `duration` (optional)

Text to video via gflow. It spends credits and is ledgered, measured on the PRO plan: omni-flash 10 s x1 = 15 credits (the default when model is omitted), veo-lite 8 s x1 = 10 credits, and count multiplies it (veo-lite x2 = 20 credits). Allow 2-5 min: the gflow job took 74-85 s, plus a balance read before and after. job_id is required: use a new one for each new job, and keep the SAME one when calling again after an error or a timeout. Any job_id already in a ledger under the out folder is refused before a browser opens, so a retry never pays twice; that refusal means the job may already have spent credits, so check flow_media and flow_credits before starting it under a new one. A job_id still running in another call is refused too: wait for that call to finish and call again with the SAME job_id, never a new one. The answer of a real run carries outcome {code, charged, retryable, advice}, and an error opens with 'outcome code=... charged=... retryable=...' (the whole outcome is its structured content): charged is what the balance bracket says left the account, UNKNOWN and CHARGED_NO_OUTPUT mean money may be gone, and an error with no outcome line was refused before any job started. Flow's Agent mode is turned off in the project before gflow runs, since gflow cannot generate while it is on, and turned back on afterwards if it was on; measured 2026-09-29, this adds about 19 s, or about 35 s when it was on.

### `gen_i2v`

**Arguments**: `initial_frame`, `prompt`, `project`, `job_id`, `end_frame` (optional), `model` (optional), `aspect` (optional), `duration` (optional)

Image (first frame, optionally a last frame too) to video via gflow. It spends credits and is ledgered, both forms measured once each on 2026-09-18 at omni-flash 10 s x1, the default when model is omitted: a start frame alone = 15 credits in 105 s, and start + end_frame = 15 credits in 119 s. Those two runs used different images and prompts, so read each time on its own, not the gap between them. Pass end_frame to interpolate between two local images, at omni-flash 10 s only, which is the cell that was priced: any other model or length is refused, because Flow picks its interpolation model by cohort and that run has never been paid for here. The measured clip did begin and end on the frames given: over its 240 frames the one closest to the end image IS the last (1.9 of 255, converging 10.0, 9.1, 8.0, 6.2, 3.9, 1.9 over the final six), the one closest to the start image is frame 1 (2.1), and either image against the other end of the clip reads about 50. Both runs that finished did so on gflow 0.78.0; every attempt before it died in Flow's frame picker and spent nothing. PASS aspect, and match it to your images: leaving it out means 9:16, gflow's own default, and Flow CROPS a frame of another shape to fit, which pushed the subject of a 16:9 photo half out of the left edge. Allow 2-5 min. job_id is required: use a new one for each new job, and keep the SAME one when calling again after an error or a timeout. Any job_id already in a ledger under the out folder is refused before a browser opens, so a retry never pays twice; that refusal means the job may already have spent credits, so check flow_media and flow_credits before starting it under a new one. A job_id still running in another call is refused too: wait for that call to finish and call again with the SAME job_id, never a new one. The answer of a real run carries outcome {code, charged, retryable, advice}, and an error opens with 'outcome code=... charged=... retryable=...' (the whole outcome is its structured content): charged is what the balance bracket says left the account, UNKNOWN and CHARGED_NO_OUTPUT mean money may be gone, and an error with no outcome line was refused before any job started. Flow's Agent mode is turned off in the project before gflow runs, since gflow cannot generate while it is on, and turned back on afterwards if it was on; measured 2026-09-29, this adds about 19 s, or about 35 s when it was on.

### `gen_r2v`

**Arguments**: `refs`, `prompt`, `project`, `job_id`, `model` (optional), `aspect` (optional), `duration` (optional)

Reference images (ingredients) to video via gflow. It spends credits and is ledgered: omni-flash x1 = 12 credits (the default when model is omitted, measured 2026-09-15) and veo-lite x1 = 10 credits (measured). It always runs 8 s through gflow, so leave duration out; for 10 s, put the images in the project with flow_upload and pass their media ids to gen_character. omni-flash takes up to 7 reference images, veo-lite, veo-fast and veo-lite-lp up to 3, veo-quality none; more is refused before anything is spent. Allow 2-5 min: that omni-flash run took 292 s end to end. job_id is required: use a new one for each new job, and keep the SAME one when calling again after an error or a timeout. Any job_id already in a ledger under the out folder is refused before a browser opens, so a retry never pays twice; that refusal means the job may already have spent credits, so check flow_media and flow_credits before starting it under a new one. A job_id still running in another call is refused too: wait for that call to finish and call again with the SAME job_id, never a new one. The answer of a real run carries outcome {code, charged, retryable, advice}, and an error opens with 'outcome code=... charged=... retryable=...' (the whole outcome is its structured content): charged is what the balance bracket says left the account, UNKNOWN and CHARGED_NO_OUTPUT mean money may be gone, and an error with no outcome line was refused before any job started. Flow's Agent mode is turned off in the project before gflow runs, since gflow cannot generate while it is on, and turned back on afterwards if it was on; measured 2026-09-29, this adds about 19 s, or about 35 s when it was on.

### `gen_character`

**Arguments**: `project`, `prompt`, `characters` (optional), `job_id` (optional), `media_ids` (optional), `model` (optional), `aspect` (optional), `dry_run` (optional), `out_dir` (optional), `duration` (optional), `retry_of` (optional)

Video starring the project's characters (entity ids from flow_characters), with or without images already in the project (media ids from flow_media, images only), or from those images alone, which is the way to a 10 s reference video since gen_r2v runs 8 s only. A character goes into the prompt as a Flow @ mention and an image through the composer's '+' dialog, and every chip is checked against its id before anything is spent; a chip Flow refuses (an image over the model's cap, a character taking one of its image slots) stops the run before the click, in Flow's own words. Once a paid clip is fetched its recipe is read back off the listing, and a clip that dropped or gained a reference is an error even though it was paid. It spends credits and is ledgered, at x1: 8 s by default, omni-flash (the default) 12 credits and veo-lite 10 credits, both measured; veo-fast 20 credits by Flow's own price table, unmeasured (flow_capabilities answers these as data). duration=10 is offered on omni-flash at 15 credits (the composer's own quote). Veo 3.1 Lite showed no length choice here (measured); veo-fast stays at 8 s, unmeasured. The live price line is read first and a different price is refused before the click. dry_run=true returns the quote and the chips, clicks nothing, writes no ledger row, leaves the composer empty and needs no job_id (each chip's id is the character's entity id or the image's workflow id). out_dir puts the clip and its ledger in a folder of your own, which keeps one film's takes together; it must be inside out/, and a job_id is refused when ANY ledger under out/ already holds it, that folder's included. For a real run, job_id is required: use a new one for each new job, and keep the SAME one when calling again after an error or a timeout. Any job_id already in a ledger under the out folder is refused before a browser opens, so a retry never pays twice; that refusal means the job may already have spent credits, so check flow_media and flow_credits before starting it under a new one. A job_id still running in another call is refused too: wait for that call to finish and call again with the SAME job_id, never a new one. The answer of a real run carries outcome {code, charged, retryable, advice}, and an error opens with 'outcome code=... charged=... retryable=...' (the whole outcome is its structured content): charged is what the balance bracket says left the account, UNKNOWN and CHARGED_NO_OUTPUT mean money may be gone, and an error with no outcome line was refused before any job started. When an error says retryable=yes (Flow failed the job and charged nothing, with code AUDIO_FILTERED or NO_REASON), it may pass as it is: call again with the same project and prompt, a NEW job_id, and retry_of set to the refused job_id. The server checks the ledger before a browser opens: a job is retried once (a second retry of it is refused once the first has clicked, and while the first runs in this server), and one original job gets at most 2 retries; for any other code a new job_id after an error is still wrong. A real run is refused while a job that job_submit started with the same project and prompt may still show its clip (not collected yet, or settled with no clip within the last hour): a blocking run takes the one new clip that carries its prompt, which could be that job's. Fetch that job with job_collect first, or start this one with job_submit too. Flow can refuse a run under its content filters and charges nothing for it: the error then opens with that and carries Flow's own status, reason and words, for example status 4 with PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED for a character made from a real person's photo (measured 2026-09-17). The filter judges the generated video, so the same character can pass one run and be refused the next, and a dry run cannot tell in advance; do not retry the same inputs hoping they pass. Allow 3-7 min for a real run, about 1-2 min for a dry run.

### `gen_t2i`

**Arguments**: `prompt`, `project`, `model` (optional), `aspect` (optional), `count` (optional), `job_id` (optional)

Text to image via gflow: 0 credits with the default nano2 model, but it draws on a daily image quota. Each output's media_id is the project media id that gen_video takes as start_frame, end_frame or media_ids; workflow_id is the id gflow itself reports. The listing can trail a new image, so this call reads it for up to about a minute; when it never shows the image, media_id is null and media_id_note says how to find it. Flow's Agent mode is turned off in the project before gflow runs, since gflow cannot generate while it is on, and turned back on afterwards if it was on; measured 2026-09-29, this adds about 19 s, or about 35 s when it was on.

### `gen_i2i`

**Arguments**: `refs`, `prompt`, `project`, `model` (optional), `aspect` (optional), `count` (optional), `job_id` (optional)

Reference images to image via gflow: 0 credits with the default nano2 model, but it draws on a daily image quota. Each output's media_id is the project media id that gen_video takes as start_frame, end_frame or media_ids; workflow_id is the id gflow itself reports. The listing can trail a new image, so this call reads it for up to about a minute; when it never shows the image, media_id is null and media_id_note says how to find it. Flow's Agent mode is turned off in the project before gflow runs, since gflow cannot generate while it is on, and turned back on afterwards if it was on; measured 2026-09-29, this adds about 19 s, or about 35 s when it was on.

### `clip_extend`

**Arguments**: `project_id`, `media_id`, `prompt`, `job_id`, `out_dir` (optional)

Extend a clip with Veo 3.1 Lite. It spends credits and is ledgered: 10 credits per extend (measured). Flow greys Extend out on some clips (measured: on Omni clips, and on a Veo clip after Omni edits and a 1080p upscale); the call is then refused before the click, at no cost. The extension is a new clip inside a new scene (the source is copied in first): its own file answered HTTP 400 and clip_download could not open it (measured 2026-09-29), so fetch it with scene_download on the scene_id this tool returns; that film's video stream ran 15.0 s for an 8 s source, 8 + 7: the extension is a 7.0 s clip, though the listing calls it 8 (measured on the file, 2026-10-01). Takes about 2-3 min, up to about 7 min when Flow is slow. out_dir, when given, must be inside the out folder. When the balance moved by anything other than the measured price, the answer carries balance_moved {kind, measured, moved, note}, or, when the call ends in an error, the job's ledger row does: read it before assuming the price in this description still holds. job_id is required: use a new one for each new job, and keep the SAME one when calling again after an error or a timeout. Any job_id already in a ledger under the out folder is refused before a browser opens, so a retry never pays twice; that refusal means the job may already have spent credits, so check flow_media and flow_credits before starting it under a new one. A job_id still running in another call is refused too: wait for that call to finish and call again with the SAME job_id, never a new one. The answer of a real run carries outcome {code, charged, retryable, advice}, and an error opens with 'outcome code=... charged=... retryable=...' (the whole outcome is its structured content): charged is what the balance bracket says left the account, UNKNOWN and CHARGED_NO_OUTPUT mean money may be gone, and an error with no outcome line was refused before any job started.

### `clip_edit`

**Arguments**: `project_id`, `media_id`, `prompt`, `job_id`, `out_dir` (optional)

Video-to-video edit of a clip with Omni 1.1 Flash. It spends credits and is ledgered: every edit measured on this account cost 20 credits, while Flow's own price table lists Omni Flash Edit at 40, so budget for 40 and expect 20. This tool does not read the live price line before it clicks, so what stands between a changed price and a surprise bill is the balance read before and after, answered as credits_before and credits_after (the ledger row holds their difference as `spent`). An edit cannot change what is said: it keeps the clip's audio, and a new line asked for in the prompt came out burned in as a subtitle (measured 2026-10-01). Takes about 2-3 min, up to about 7 min when Flow is slow. out_dir, when given, must be inside the out folder. When the balance moved by anything other than the measured price, the answer carries balance_moved {kind, measured, moved, note}, or, when the call ends in an error, the job's ledger row does: read it before assuming the price in this description still holds. job_id is required: use a new one for each new job, and keep the SAME one when calling again after an error or a timeout. Any job_id already in a ledger under the out folder is refused before a browser opens, so a retry never pays twice; that refusal means the job may already have spent credits, so check flow_media and flow_credits before starting it under a new one. A job_id still running in another call is refused too: wait for that call to finish and call again with the SAME job_id, never a new one. The answer of a real run carries outcome {code, charged, retryable, advice}, and an error opens with 'outcome code=... charged=... retryable=...' (the whole outcome is its structured content): charged is what the balance bracket says left the account, UNKNOWN and CHARGED_NO_OUTPUT mean money may be gone, and an error with no outcome line was refused before any job started.

### `agent_send`

**Arguments**: `project_id`, `message`, `job_id`, `wait` (optional)

Send a message to Flow's agent in a project. It may spend credits: 0 credits in every measured send where the agent generated nothing, but a message that makes it generate media costs that generation's price. The send itself took 69-80 s in those runs, plus a balance read before and after. When the balance moved by anything other than the measured price, the answer carries balance_moved {kind, measured, moved, note}, or, when the call ends in an error, the job's ledger row does: read it before assuming the price in this description still holds. job_id is required: use a new one for each new job, and keep the SAME one when calling again after an error or a timeout. Any job_id already in a ledger under the out folder is refused before a browser opens, so a retry never pays twice; that refusal means the job may already have spent credits, so check flow_media and flow_credits before starting it under a new one. A job_id still running in another call is refused too: wait for that call to finish and call again with the SAME job_id, never a new one. The answer of a real run carries outcome {code, charged, retryable, advice}, and an error opens with 'outcome code=... charged=... retryable=...' (the whole outcome is its structured content): charged is what the balance bracket says left the account, UNKNOWN and CHARGED_NO_OUTPUT mean money may be gone, and an error with no outcome line was refused before any job started.
