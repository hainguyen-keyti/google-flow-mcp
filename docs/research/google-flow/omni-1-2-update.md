# Flow's Omni 1.2 update (measured 2026-11-09)

What changed in Google Flow between build `aZ4BlYOkwqQ` (2026-10-22, the repo's baseline) and build
`YyfdEJNu4Zk.2018.O` (2026-11-09), what it breaks here, and how to use the new model. Every number below says
where it came from: **measured** (this repo, account harleychina0, Pro plan, $0 walks), **Flow** (Flow's own
in-app changelog, read 2026-11-09), **Google** (Google's blog or help pages) or **third party**.

## 1. What changed

### Model lineup (Flow changelog "Introducing Omni 1.2!", 2026-11-05)

- Flow keeps two video models: **Omni 1.2 Flash** (flagship, the composer's default) and **Veo 3.1 Lite**
  (low cost).
- **Omni 1.1 Flash, Veo 3.1 Fast and Veo 3.1 Quality leave on 2026-12-05.** The picker marks each one
  "Leaving Dec 5" (measured). The changelog says they stay "for the next 30 days".
- Google's help page (support.google.com/flow/answer/16352836) adds two things:
  - Veo Extend was retired on 2026-11-05, so extend is now Omni 1.2 only, on paid plans.
  - Veo 3.1 Lite stays at 720p, with 4, 6 or 8 s in Frames and 8 s only in Ingredients.
- The changelog's highlights for Omni 1.2:
  - Better reference adherence (character and scene), better control over jump cuts, sharper picture.
  - Clips up to 15 s, with length set to the exact second.
  - Native 360p and 720p, with upscale to 1K and 4K.
  - Extend up to 10 s at a time, now taking image, character and voice references.
  - Durations beyond 10 s, extend and 1K/4K upscale are paid tier only.
- **Pricing (Flow):** "per-second pricing for generation, editing, extension and upscaling", with edit
  priced the same as generation.

### Omni 1.2 in the composer (measured, $0 walks of 2026-11-09)

- **Length is a slider, not radios.** It is an `input[type=range]` with `aria-label="Length"`, value 0 = 3 s, one step
  a second. On the default account it runs to **15 s**; on harleychina0 it stops at **10 s** (Flow: past 10 s is
  paid tier only, so the reach follows the plan). There are no `4s/6s/8s/10s` radios any more.
- **Price is per second, the same in Frames and Ingredients, times the count:** 1 credit a second at 360p and 3 at
  720p up to 10 s, then twice that for each second past 10.

  | | 3 s | 4 s | 6 s | 8 s | 10 s | 11 s | 12 s | 15 s |
  |---|---|---|---|---|---|---|---|---|
  | 360p | 3 | 4 | 6 | 8 | 10 | 12 | 14 | 20 |
  | 720p | 9 | 12 | 18 | 24 | 30 | 36 | 42 | 60 |

  The owner's screenshot (360p, 8 s, x2 = 16) matches.
- **Compared with Omni 1.1 at the same length:** dearer above 4 s at 360p (8 s: 8 against 6) and at 720p from 8 s
  (8 s: 24 against 12). Google's "30% lower" is an API rate, not Flow credits. **The cheap Flow cell is 360p short
  drafts.**
- Veo 3.1 Lite stays 10 credits a clip (8 s), in both modes.
- **Ingredient caps (read off the composer's own refusals):** Omni 1.2 7 images and 5 voices, Veo 3.1 Lite 3 and 1,
  the same as Omni 1.1.
- **Image tab:** on the default account it grew a Low / Medium / High row (not walked further here).

### Paid behaviour (plan AV's paid run, default account, every bill read off the balance)

- Every generation cost exactly its table cell: Frames, start and end frame, Ingredients with an image, with a voice,
  `job_submit`, the gflow tools, Veo Lite.
- **Edit** of an Omni 1.2 360p 4 s clip: 4 credits (the clip's own cell).
- **Extend** of the same clip: 4 credits, and it came back as a **new version of the clip holding the whole of it**
  (an 8.0 s file by ffprobe), not a new scene as Veo Extend made.
- **Characters:** the editor's trash button reads "Move to trash", opens no dialog and returns to the project; the
  listing keeps the entry with `true` at `Zzl0ze[5][*][3][4]`.

### Other UI changes (measured by `flow survey`, 24 differences, `out/survey/20261109_081847/`)

- The clip editor has a **model picker** ("Select model family": Omni 1.2 Flash, Omni 1.1 Flash). It opened on
  Omni 1.1 Flash on a clip made with 1.1, so an edit could silently run on the model that is leaving.
- Delete is now **Move to trash**, and deleted media can be restored (Flow changelog "Easier asset management",
  2026-11-02). The same entry brings collections in the sidebar and a new project cover rule.
- Every sidebar route gained an "add Create tool" button, and the home page lost "Start Creating".
- **A banner on every page: "Your oldest creations will expire soon. Opt-in to keep them around."** Opting in is
  an account setting, so the owner decides; nothing was clicked. Until then, clips worth keeping should be
  downloaded into `out/`.

## 2. What it breaks here

| Part | Why | When |
|---|---|---|
| `gen_t2v`, `gen_i2v`, `gen_r2v` (gflow 0.78.0) | gflow finds the model by the label "Omni 1.1 Flash" | 2026-12-05 |
| `gen_video`, `job_submit`, `gen_character` with Omni | lengths are set by radio; Omni 1.2 has a slider | now, for Omni 1.2 |
| price table (`flow_options.json`, `video.price`, `ingredients.PRICES`) | Omni 1.2 cells absent; old models listed | now |
| `clip_extend` | it drove Veo 3.1 Lite Extend, which Google retired | now |
| `clip_edit` | the editor opens on Omni 1.1; the price and model of an edit changed | now, then 2026-12-05 |
| `flow_capabilities`, tool descriptions, `docs/tools.md`, README | name the old models and prices | now |
| flow_skills (`flow-shot`, `flow-mechanics`, ...) | recommend Veo Fast and Omni 1.1 cells | now |

**gflow 0.96.1** (2026-11-08) already supports Omni 1.2:
- `--model omni-1.2-flash` takes 3-15 s through the slider and reads the slider back before Send.
- A start and end frame above 10 s is refused before upload, since Flow drops the end frame there and bills a
  start-only clip.
- A wrong submit is now aborted before it leaves the browser.

Since 0.95.0 gflow also sends a weekly usage report unless `GFLOW_CLI_TELEMETRY=0` is set.

## 3. Using Omni 1.2 so it does not invent things

Sources: Google's blog post and DeepMind page (via the research of 2026-11-09), this repo's earlier measurements
(`test-results.md`), and the owner's verdict of 2026-10-01 (rule 14). Points not yet measured on 1.2 are marked.

1. **Pass the thing, do not describe it.** Every element that must not drift goes in as a reference, and the
   prompt names it. That means the product photo, the face, the outfit, and the printed pattern or logo shown
   close up. Text alone lets the model invent the print.
   - Google claims 1.2 holds faces, garments, packaging type and brand colours across angles.
   - Omni 1.2 takes 7 images and 5 voices (read off the composer 2026-11-09), the same as Omni 1.1.
2. **One reference per job, named for its job.** Use a clean product shot without people for the product, and a
   neutral face for the person. Then say in the prompt which reference is which: "the bowl from the product
   image", "the woman from the character image".
   - Reference images that carry extra objects get those objects copied into the clip.
3. **Frames over Ingredients when the composition is fixed.** A start frame pins the first picture exactly. An
   end frame pins where a state change lands (box closed to open).
   - Keep a start and end frame at 10 s or less: above that, Flow drops the end frame and still bills (gflow
     0.96.1, reporter's measurement).
4. **Say what must stay the same, and the exclusions, in plain words.** Google's examples write the exclusions
   out: "One continuous shot, no jump cuts", "Don't add text", "Only one item at a time".
   - For a product: "the pear print on the bowl stays exactly as in the reference, same count and position;
     no new prints appear".
5. **Short, slow, few changes per shot.** Google lists fast action, quick changes in the script and complex
   physics as the weak spots.
   - A rotation that shows a new side of a printed object is where prints get duplicated (the peer session's
     Omni 1.1 clip, 2026-10-30). Keep the turn small, or start from a frame that already shows that side.
6. **Dialogue: write the exact line and name the speaker**, one speaker per line. Google claims better lip-sync
   and per-character voices on 1.2.
   - The owner judged on 2026-10-01 that every clip with a voice ingredient sounded less real. That stands
     until the owner hears 1.2 (rule 14).
7. **Draft cheap, then commit.** Draft at 360p and 3-6 s (3-6 credits), and pick with the owner. Past 10 s every
   second costs double, so two 8 s clips cost less than one 16 s one would. Then render the
   keeper at 720p, or upscale it.
   - For a flaw in one detail, use an edit rather than a fresh generation: an edit costs the same as generating
     and keeps the rest of the clip.
8. **Extend instead of long generations.** Extend continues from the last frame, is billed like a generation of the
   added seconds, returns the whole clip as a new version, and takes the same image, character and voice references.
   Pass them again on every extension so the identity carries.

Quality claims above are Google's until the owner has watched Omni 1.2 clips side by side with Omni 1.1 and Veo
Lite (rule 14).

## 4. Sources

- Flow in-app changelog, `https://flow.google.com/changelogs`, entries of 2026-11-05 and 2026-11-02 (screenshots
  `out/flow-1109/changelog_0_*.png`, `changelog_1_*.png`).
- Google blog, "Introducing Gemini Omni 1.2 Flash", 2026-11-05; Flow help, support.google.com/flow/answer/16352836;
  DeepMind, deepmind.google/models/gemini-omni.
- gflow-cli CHANGELOG 0.93.0-0.96.1 (github.com/ffroliva/gflow-cli).
- This repo: `out/survey/20261109_081847/survey.json`, `out/flow-1109/slider_probe.json`.
