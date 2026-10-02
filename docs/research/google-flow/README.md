# Google Flow: the whole picture (dossier, 2026-10-01)

How Flow works, what one request can carry, what holds a look and a voice, what fails, what it costs, and how people
produce with it. Built from six research lanes (official docs, model docs, community, GitHub, production method, voice)
and from paid and $0 measurements on this account (Pro plan, Vietnam, flow.google.com, project `102445f4`).

Companion files: `test-results.md` (every job and number), `mcp-gaps.md` (what the MCP lacks), `skill-design.md` (the
skill set), `lanes/01..06-*.md` (the lane reports with every URL and date).

## How to read the tags

| Tag | Meaning |
|---|---|
| [O] | an official Google page |
| [C] | community, first-hand or a tutorial that shows its result |
| [V] | a vendor's documentation or a blog; weaker than [O] and [C] |
| [G] | GitHub: source read at a tag, an issue, a release |
| [M] | measured by this repo; the job id or probe is named |
| [I] | inference, not observed |

- A source key such as `L2.S9` means lane 2, source S9: the URL and its date are in that lane file. Section 15 lists
  the pages most of this stands on.
- An [M] with a job id `ak-...` or no date was measured on 2026-10-01 in plan AK; its numbers are in
  `test-results.md`. The v7 figures are in `docs/agent/HANDOFF.md` and in the `flow-film-director` skill. An [M]
  dated before 2026-09-28 was measured on the repo's previous Flow account.
- Things move: in three days two behaviours of Flow's page and one of Chrome changed under the drivers (section 10).
  A measurement is re-made before it is relied on.

## 1. The product in one page

- Flow is Google Labs' film tool at flow.google.com. The old labs.google host answered an empty session for the
  repo's first account ([M] 2026-09-12).
- Video models: Veo 3.1 Lite, Fast, Quality, and Gemini Omni 1.1 Flash (in Flow since 2026-08-27, `L1.S13`). Image
  models: three Nano Banana models; Nano Banana 2 Lite is the no-charge default ([O] `L1.S1`).
- Surfaces: the project grid with its composer (text, Frames, Ingredients), Characters, Voices, Avatars, Scenes
  (Scenebuilder), the clip editor (Extend, Edit, Download), the Agent, Tools ([O] `L1.S1-S10`); Flow Music, a
  separate product with its own credits ([O] `L1.S26`); Flow TV, a showcase page.
- There is no Flow API ([G] lane 4 section 3). The models have official APIs (Gemini API, Agent Platform) with other
  features: `seed`, `negativePrompt`, `generateAudio`, role tags, Veo chains to 148 s exist there and not in Flow;
  voice references exist in Flow and not there ([O] `L2.S1`, `L2.S2`, `L2.S8`, `L2.S25`).

## 2. Models and modes

| Model | Text and Frames | Ingredients | Extend | Video edit | Credits on Pro |
|---|---|---|---|---|---|
| Veo 3.1 Lite | 4, 6, 8 s [O]; this account's composer shows no length row: 8 s only [M] | 8 s [O][M] | yes, and every Extend is performed by Lite [O][M] | no | 10 [O][M] |
| Veo 3.1 Fast | 4, 6, 8 s [O]; 8 s only [M] | 8 s [O][M] | no | no | 20 [O][M] |
| Veo 3.1 Quality | 4, 6, 8 s [O]; 8 s only [M] | not supported [O]: the composer refuses images, characters, videos and voices [M] | no | no | 100 [O], quoted, never run here |
| Omni 1.1 Flash | 4, 6, 8, 10 s [O][M] | 4, 6, 8, 10 s [O][M] | "coming soon" [O]; greyed out on Omni clips [M] | yes, a segment of at most 10 s [O] | 720p 7/10/12/15, 360p 4/5/6/7 for 4/6/8/10 s [O][M]; edit published 40, measured 20 [M] |

- Aspect: 16:9 and 9:16 for video in every cell ([O] `L1.S1`, [M] $0 probe). Count x1 to x4.
- Output: 24 fps, 720p native. The Download menu of a 720p clip offers a GIF, "720p Original size", "1080p Upscaled"
  (0 credits) and "4K Upscaled" (greyed out on Pro); of a 360p clip: the GIF, "360p Original size", "720p Upscaled"
  (0 credits) ([M]; [O] `L1.S5`).
- A 360p Omni draft costs half. Its upscale is crisp and cannot restore what the 360p generation never drew: the
  face came out as another face, a star pendant as a round one ([M] `ak-d1`). A draft answers composition and timing,
  not identity.
- When the chosen model lacks the chosen feature, Flow swaps in a compatible model ([O] `L1.S6`). Read the model key
  of the submitted request, not the menu ([M] keys seen: `abra_i2v_6s|8s|10s`, `abra_r2v_6s|8s|10s`, `abra_edit`,
  `veo_3_1_i2v_lite`, `veo_3_1_r2v_lite`, `veo_3_1_i2v_s_fast_portrait`, `..._fl` for first plus last,
  `veo_3_1_interpolation_lite`, `omni_flash_i2v_4s_first_last`, `veo_3_1_upsampler_1080p`).
- What each Veo is for, by Google: Quality for final cuts, Fast for standard production, Lite for volume ([O]
  `L2.S17`). Google's own rating puts Lite near Fast: 54.6% wins on text-to-video, 47.2% on image-to-video ([O]
  `L2.S24`). One shot here, same start frame and words: both kept one bag through a turn; Lite came back with a
  broader smile, Fast calmer; no ranking from one shot ([M] `ak-c1`).
- Veo against Omni on one shot, same frames and prompt ([M] v7): Veo Fast lands closer to the end frame (distance
  3.9 against 8.75) and keeps the dress sharper (131 against 66, master photo 181). Veo arrives on the end frame at
  about 60 to 70% of its 8 s and holds; Omni is still converging on its last frame. A blog's eight-task test rates
  Veo Fast above Omni on visual quality and temporal consistency ([V] `L3.B1`).

## 3. What one request can carry

| Input | Frames mode | Ingredients mode |
|---|---|---|
| Start image | yes | no |
| End image | yes (with a start image) | no |
| Reference images | no | yes: Omni 7, Veo Lite and Fast 3, Veo Quality none ([M] the composer's own refusals). Flow publishes no cap ([O] lane 1 section 2); the APIs say 3 for Veo and 10 for Omni ([O] `L2.S1`, `L2.S2`); a vendor table says 7 for Omni ([V] `L3.U2`). Veo Lite ran with 3 images and a voice for 10 credits ([M] `ak-a7c`) |
| Character (`@Name`) | The composer accepts the chip and quotes a price; the request drops it and sends the name as plain text ([M] `ak-a3`) | yes; the entity id rides in the request ([M] Plan E, 2026-09-17). Two characters are accepted on Omni, Veo Lite and Fast; Veo Quality refuses characters ([M]) |
| Voice | no: "You can add voice references only to video generations that use ingredients." ([O] `L1.S2`) | yes, as an "audio ingredient", through the "+" dialog only, never alone. Omni: up to 5. Veo Lite and Fast: 1 (a voiced character counts as it). Veo Quality: none ([M]) |
| Video | no | yes, and then the request is an EDIT of that video: on Omni, rpc `jIps6`, 20 credits quoted at 6 s and at 10 s ([M] `ak-n3`). Veo Quality refuses video ingredients; Lite and Fast were not tried |
| Avatar (`@me`) | no | yes by the docs ([O] `L1.S8`); none on this account |
| Audio file | no | no. Upload takes `.png .jpg .jpeg .webp .gif .heif .heic .mp4 .m4v .mov .3gp .avi` only ([M]); the Omni API also rejects audio references ([O] `L2.S2`) |

- The composer's words ([M]): a voice alone, "An audio ingredient requires other ingredients to function."; "Maximum
  audio ingredients reached (1 allowed)" on Veo Lite and Fast, "(5 allowed)" on Omni; "Maximum image ingredients
  reached (3 allowed)" on Veo Lite and Fast, "(7 allowed)" on Omni; on Veo Quality, "You cannot use image
  ingredients with this model.", and the same sentence for character, video and audio ingredients.
- A refused chip is marked by an error icon. Its class differs by kind, and a probe that read one class reported
  Quality as accepting images (corrected in `test-results.md` section 7).
- A custom voice goes into the request by its id; a preset goes by its lowercase name (`achernar`) ([M] bodies of
  `ak-v3~r2`, `ak-k2`, `ak-f1`).
- The project listing records each generation's recipe (model key, input images with their role, voices), so what a
  clip carried can be read back for $0 ([M]).
- The official Omni API can mix a first frame with reference images through role tags ([O] `L2.S2`). Flow's composer
  cannot: Frames and Ingredients are separate modes ([M]).

## 4. Keeping the look

- **Frames is the only exact route.** The first frame is the image, and dress, bag, necklace, tattoos, room and cat
  held in six talking start-frame clips at 720p on two models (6 s and 8 s) ([M]). What still moves: the expression
  (broad smiles the photo does not have); anything the frame does not show (in the turn the back came with a large
  tattoo and the bag's front with a clasp of the model's choosing, and no supplied image says what is right); and an
  object that sits on opposite sides in the first and last frame, which is duplicated mid-move unless its path is
  written ([M] v7 against `ak-n2a`).
- **Ingredients redraws.** Omni redraws loosely: three runs, three framings, poses and sets of accessories ([M]
  `ak-a1`, `ak-a2`, `ak-f1`). Veo 3.1 Lite redraws faithfully, with bag, necklace, tattoos, room and cat, at the
  reference's framing or close to it ([M] seven clips). Five of the seven carry a fault: a phone border drawn from a
  prompt word, a hair clip, a hair bow, the bag moved to her hand with a fade to grey in the last 0.4 s, a second cat.
- **More references, more of everything.** Three images (medium, full body, back) gave the full-body framing with
  the right socks, shoes and room, and the cat, which all three references show, was drawn twice ([M] `ak-a7c`).
  Content of a reference sheet leaks into clips: keep hands, captions and logos off it ([C] `L3.U1`).
- **Characters.** Google's claim: a character keeps face, clothing and voice strictly consistent across generations
  ([O] `L1.S4`). That is consistency between generations, not fidelity to a source photo ([I]). Practitioners report
  redrawn faces, changed shoes, hair and outfits ([C] `L3.D1`, `L3.D2`). Our early films, which mixed character,
  image-chip and Frames clips, were rejected by the owner for a look, outfit and voice that changed between clips
  ([M] 2026-09-30). Both model cards list full consistency through complex scenes, motion or edits as unsolved ([O]
  `L2.S19`, `L2.S20`).
- **One master photo.** The visual errors of v5 and v6 came from four photo variants of "the same outfit". Every
  keyframe of v7 was cut or derived from ONE master (Nano Banana image-to-image, 0 credits) and checked against it
  before any video ([M]). Tutorials reach the same place from the other side: one finished still as the start frame
  of every clip ([C] `L3.U1`, `L3.Y4`).
- **Small details hold only when clearly visible in the first frame** ([C] `L3.C5`); a prompt that makes a hand touch
  a garment part can grow that part ([M] v5b: a long lace sleeve for 2.5 s).
- **Reference images**: plain or segmented background, one look across all of them, nothing extra in a location or
  style reference, and prompt text that complements and never contradicts the images ([O] `L1.S2`).

## 5. Keeping the voice

Facts:

- A voice is one of 30 presets, the Gemini TTS voices ([O] lane 6 section 1), or a custom voice: a preset plus a
  written performance and a sample line of up to 120 characters ([M] UI, 2026-09-18). It is a restyled preset, not a
  cloned recording; there is no audio upload ([O] `L1.S12`, [M]). LilyVoice is the preset Leda plus its performance
  text ([M] listing record).
- Making, previewing and attaching a voice costs 0; a preview is a wav the page fetches and can be saved ([M]).
- Voice ingredients do not carry over on Extend ([O] search snippet, lane 6 section 1).

Routes to one voice across separate clips, measured (rulers and their limits: `test-results.md` section 1):

| Route | Look | Voice | Cost | Evidence |
|---|---|---|---|---|
| Omni Frames, 6 s, no voice ingredient, ONE short line said as talk | exact (Frames) | the owner's ear: real, decisive, one person, 4 clips of 4 over two accounts | 10 per 6 s | [M] `ak-a3`, `voice-a3-rep-1`, `voice-a3-rep-5`, `new-acct-talk-1`; `test-results.md` section 3 |
| The same, with a line that describes the product | exact (Frames) | the owner's ear: fake, another person, slow, 4 clips of 4 | 10 per 6 s | [M] `voice-a3-rep-2` to `-4`, `new-acct-describe-1` |
| All lines in ONE Omni 10 s take, cut apart in the edit | exact (Frames) | one take, one voice | 15 per 10 s | [M] v7 |
| Voice ingredient + image, Veo 3.1 Lite | faithful redraw, small inventions | the Flow voice, clip after clip; the owner's ear: less real than the first row | 10 per 8 s | [M] `ak-a7`, `ak-a7b`, `ak-v3~r2`, `ak-v4`, `ak-a7c` |
| Voice ingredient + image, Omni | loose redraw | the Flow voice; up to 5 voices, two went to the right two speakers; the owner's ear: less real than the first row | 10 per 6 s | [M] `ak-a1`, `ak-a2`, `ak-f1` |
| Frames + the same voice sentence in every prompt | exact | a family of similar voices, not one | 10 to 12 per 8 s | [M] `ak-v1`, `ak-v2`, `ak-v5`, `ak-v6` |
| Extend a talking Veo clip | continues the picture: 8 + 7 = 15 s in one shot | not held across the join by our ruler | 10 per 7 s | [M] `ak-x0`, `ak-x1` |
| Video ingredient, or Omni edit, with a new line | copies the source | cannot change speech; the edit burns the line in as a subtitle | 20 | [M] `ak-n3`, `ak-n1-edit-line` |
| External TTS plus a lip-sync model | untouched picture | any | outside Flow | [C] `L3.H2`, `L3.H3`; not tested here |

- The numbers ([M], crude ruler): two clips of one model saying the same words are 1.0 to 1.2 apart with the voice
  ingredient and 2.5 to 3.5 apart without it (across the two models: 2.1 to 2.6 with it, 1.9 to 4.4 without);
  ingredient against no ingredient, 3.3 to 6.2. Clips with the ingredient sound most like the preset behind the Flow
  voice in two of three; clips without it sound most like other presets every time.
- Settled by the owner's ear on 2026-10-01 and 2026-10-02, where the ruler could not say (1.5 to 4.7 straddles its
  line): every clip with a voice ingredient sounds less real than `ak-a3`, and without an ingredient the kind of line
  decides. A short line said as talk (first person, a particle such as "nè" or "nha", or a question to the viewers)
  gave a real, decisive voice heard as one person in 4 clips of 4; a line that describes the product failed in 4 of
  4. Tried at 6 s on Omni Frames with one start frame and one kind of voice only (`test-results.md` section 3).
- Community: since Omni 1.1 the Agent no longer carries one voice across clips ([C] `L3.D3`); one voice description
  pasted into every prompt kept three Frames clips close enough to pass as one take for one author ([C] `L3.U1`).
- With several speakers Veo mixes up who speaks when descriptions are alike: tie each line to a visible trait ([C]
  `L3.C7`). Here a woman's and a man's voice each found its speaker on Omni ([M] `ak-f1`); one gender twice is
  untested.

## 6. Speech

- Languages: only English is evaluated for both models ([O] `L2.S1`, `L2.S2`). Vietnamese works here: 18 of 18 new
  generations spoke their scripted lines, checked by transcription; two slipped on one word ([M]). Elsewhere:
  Mandarin under 30% success against 70% in English ([C] `L3.C6`); Romanian prompt variants made pronunciation worse,
  by transcription ([C] `L3.H2`).
- Form: Google contradicts itself. Its best-practice page says a colon after the speaker's action and no quotation
  marks, because quoted text is rendered on screen ([O] `L2.S9`); its Veo guide and blog use quotation marks ([O]
  `L2.S1`, `L2.S16`). All our talking clips use the colon form with "No subtitles, no on-screen text." and none drew
  subtitles, except the Omni edit that was asked to change words ([M]).
- English prompt, the spoken line in its own language, one speaker, short lines ([V] `L3.B5`, [M]). Two sentences of
  about 20 Vietnamese words take 4.3 to 7 s; three take 7.4 s of an 8 s clip ([M]).
- A word in the line can become a gesture: "thả tim" produced a heart made with both hands in two of the four clips
  that said it ([M] `ak-v2`, `ak-x1`).
- Lip sync has no specification; DeepMind lists consistent natural speech, short lines especially, as unfinished for
  Veo ([O] `L2.S15`). Omni's sync holds about 6 to 7 s by one reviewer ([C] lane 6 section 2).

## 7. Sound and the audio filter

- Veo's audio cannot be switched off in Flow or the Gemini API; the Agent Platform has `generateAudio` ([O] `L2.S8`,
  [C] lane 6 section 3). Omni invents a soundtrack unless told otherwise; exclude with short negatives ([O] `L2.S2`).
- "Audio Generation Failed" is a Veo-only failure class: Google says Veo sometimes produces low-quality audio, makes
  no video, refunds, and advises to retry or change the prompt ([O] `L1.S6`). It is random: the same prompt and image
  pass or fail, typically within 3 to 5 tries ([C] `L3.H1`).
- Our ledger rows that record a model ([M], to 2026-10-01): Veo with a start AND an end frame, 11 jobs, 5 failed
  with `PUBLIC_ERROR_AUDIO_FILTERED`; all five showed a person and carried an ambient or room-tone instruction. Veo
  with a start frame only, 7 of 7 passed. Veo with no frame, 11 jobs, one failed with no code and passed on an
  identical retry. Omni, 73 paid jobs and 6 uncharged failures (two `UNSAFE_GENERATION`, two
  `PROMINENT_PEOPLE_FILTER_FAILED`, two with no code), none for audio. Older rows that record no model are left out;
  among them three Veo Lite reference jobs failed with no code.
- Google staff blamed "the ambient room tone instruction" for false positives on Lite and Fast ([C] `L2.F18`,
  2026-09-29). Our pair test does not show it: the same two frames on Veo Lite passed without the line (`ak-n2a`) and
  with it (`ak-n2b`). Nine of our ten two-frame jobs with a person carried such a line and five failed; the data
  cannot separate the line from chance.
- Nothing is charged for a failed generation ([O] `L1.S6`, [M] every failure here cost 0).

## 8. Motion, transitions, length

- First plus last frame works when the two frames are close (same room, light, scale) ([C] practitioner sources
  collected in the `flow-film-director` skill, section 10). Describe the first frame, the exact camera path, and
  what the last frame reveals ([O] `L2.S14`, `L2.S16`).
- An object on opposite sides of the two frames was duplicated mid-move (two bags, [M] v7). With its physical path
  written ("exactly one small black shoulder bag: its single studded strap stays over her left shoulder and the bag
  stays at her left hip, turning with her body") the same turn kept one bag, from a start frame only and from both
  frames ([M] v7.1, `ak-c1`, `ak-n2a`, `ak-n2b`).
- Models slow to near-stillness in the last part of a clip: Veo with two frames is on the last still by about 5 s of
  8 and holds ([M]). Cut before the hold, and seed the next clip from a frame before it ([C] same skill section).
- A face that leaves the frame comes back changed, on Lite and on Fast ([M] `ak-c1`): cut before it returns.
- Omni cuts by default: say "one continuous shot" and "No scene cuts" ([O] `L2.S2`). Veo is biased to frequent cuts
  and dramatic angles ([O] `L2.S20`).
- Extend: Veo 8 s clips only, performed by Lite, 10 credits, a 7.0 s clip in a new scene whose film is 8 + 7 =
  15.0 s; the picture continues from the source's last frame, the voice was not held ([O] `L1.S1`, [M] `ak-x1`).
  An extended clip takes no insert, remove or camera edit ([O] `L1.S3`).
- Edit (Omni): source up to 60 s, a segment of at most 10 s, three conversational turns keep context ([O] `L1.S3`).
  It changes pixels, not speech ([M]).
- Scenebuilder: sequence, reorder, trim, download ([O] `L1.S3`); the MCP drives all of it ([M] Plan AB).

## 9. Prompting

- Veo wants precise instructions; Omni wants the intent and less prescription ([O] `L2.S14`). Veo formula:
  cinematography, subject, action, context, style and ambiance ([O] `L2.S16`).
- With a start image: write the motion only, no re-description of character, background or light; camera motion is the
  most reliable instruction ([O] `L2.S9`). Name only the details that drifted before, and say they stay ([M]).
- In Ingredients mode every noun is a thing to draw: "Phone propped at chest height" drew a phone-screen border;
  "Camera at chest height" did not ([M] `ak-a7`, `ak-a7b`).
- One moment per clip ([O] `L2.S9`), although timed blocks (`[00:00-00:02]`, `[0-3s]`) give several beats ([O]
  `L2.S16`, `L2.S2`); the one-take voice method uses timed beats ([M] v7).
- Negatives: the API field wants nouns ("wall, frame"); in Flow there is no field, so short negatives go in the prompt
  ([O] `L2.S10`, `L2.S2`).
- Never write "then it cuts to": it morphs ([C] same skill section).
- A camera low under a short skirt, and medium crops of revealing photos, are refused as `UNSAFE_GENERATION` ([M]);
  keep the camera at knee or eye level.

## 10. Failures, filters, and things that shift

| What | Charged | What to do | Source |
|---|---|---|---|
| `PUBLIC_ERROR_AUDIO_FILTERED` (Veo) | no | retry unchanged (the skill design allows two); a start frame only, or Omni for first plus last | [O][C][M] |
| failure with no code (statuses `[6, 2, 4]`) | no | an identical retry passed here | [M] `ak-v3` |
| `PUBLIC_ERROR_UNSAFE_GENERATION` | no | change the photo or the camera angle; do not rephrase to get past it | [M] |
| `PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED` | no | a character made from a real person's photo; false positives blocked fictional characters for weeks in mid 2026 | [M], [C] `L3.D7` |
| minors, uploaded photos of people | no | prominent people, and editing uploaded video of identifiable minors, are never supported ([O] `L1.S6`, `L1.S7`); Veo's API allows adults only with image input ([O] `L2.S12`), so a young-looking adult may trip it ([I]) | as cited |
| `IP_PROHIBITED` on an input image | no | never passes on retry: replace the image | [V] `L3.U2` |
| "Unusual activity" | no | stop, wait, no retry, no new login | [O] `L1.S6`, repo rule |

Things that shifted under the drivers in three days ([M]): picker tile urls gained a `=s512-rw` suffix (2026-10-01,
every start-frame job failed until fixed); a cookie notice bar appeared mid-session and covers the composer's bottom
row (2026-10-01 16:57); and Chrome 154 crashes after a download click on this Mac (eight crash reports that day),
possibly playwright issue 42506, whose reports name Windows only ([G] lane 4 section 1). Expect this rate of change.

## 11. Credits

- Grants: 50 daily for everyone, plus 1,000 a month on Pro (200 Plus, 10,000 and 25,000 Ultra), no rollover ([O]
  `L1.S5`).
- Free: images, character and voice creation, voice previews, the 720p and 1080p upscales, Scenebuilder, Agent
  queries under a daily quota ([O], [M]).
- The v7 film and its v7.1 fixes (18.9 s): 7 paid video jobs and 4 uncharged refusals, 122 credits ([M]
  `out/lly_v7/ledger.jsonl`).
- This campaign: 23 paid jobs and 1 uncharged failure, 252 credits ([M]).
- A practitioner ratio to plan against: about 20 cheap variations per kept clip on fast tiers; a broadcast spot needed
  300 to 400 generations for 15 kept clips ([C] `L5.S27`, `L5.S28`).

## 12. Agent, Tools, Scenes

- The Agent storyboards, generates, edits and batches; queries are free under a daily quota, media costs credits, and
  it confirms before spending by default ([O] `L1.S9`). It does not fix consistency and once claimed a storyboard that
  did not exist ([C] `L3.C3`, `L3.D2`).
- Tools are built from a description; whoever runs one pays for its media ([O] `L1.S10`). Shot Explorer derives
  alternate angles from one image ([C] `L3.C4`).

## 13. Tooling around Flow

- gflow-cli: 0.81.0 on 2026-09-30; this repo pins 0.78.0. Since 0.78.0 it gained re-download of a billed clip,
  `--resolution`, typed refusals, an off-screen browser. Still unported on flow.google.com: entities, frames by media
  id, scenes, voices, custom voices; Extend and upscale are open pull requests ([G] lane 4 section 1).
- Other automation worth copying from: non-blocking submit with job status and collect, a live capability map, a
  cost estimate and run budget, character update, media delete, review-then-regenerate with a cap ([G] lane 4 section
  2). A paid proxy exposes `referenceAudio_1..5`, `referenceVideo_1`, `character_1..7`, `seed`, `async` ([V]).
- Checks an agent can run locally: transcript (Whisper, guarded against silence), tail fade, frame sheets, voice
  distance (crude here; a speaker model such as SpeechBrain ECAPA would be the proper ruler, and the owner chose to
  judge voices by ear instead), face identity (YuNet plus
  SFace), shot cuts (PySceneDetect), burned-in text (OCR), duplicate objects (OWLv2) ([G] lane 4 section 5; [M] the
  first four ran here).

## 14. Producing with it: method, intake, law

- Order of work: brief, script, shot list, stills approved, then video, edit, clearance ([C] `L5.S1`, `L5.S2`).
  Google's own guide builds start and end frames as images first, then animates ([O] `L5.S10`).
- An open gap gets filled by somebody's guess, and guesses cause reshoots ([C] `L5.S18`). The input kit and the
  protocol for a missing input are in `skill-design.md`.
- Never generated: a real person's likeness without a signed release, the product or its label ([C] `L5.S2`); the
  facts of a script, by this project's own rule ([I]). Flow is 18+ ([O] `L5.S7`).
- Editing for short video: cuts every 1.5 to 3 s (TikTok) to 3 to 5 s (Shorts), J and L cuts, cutaways over a talking
  head, light blur and grain, the source frame rate kept ([C] `L5.S36-S39`; [M] v6 juddered at 30 fps from 24).
- Loudness: no platform publishes a target; about -14 LUFS integrated and -1 dBTP survives TikTok, Reels and Shorts
  ([V] lane 6 section 7).
- Disclosure: Vietnam's AI Law 134/2025/QH15 (in force 2026-03-01) requires marking AI audio and video ([C] `L5.S48`,
  lane 6 section 8); YouTube, Meta and TikTok require labels on realistic AI video ([O] `L5.S43-S45`); EU AI Act
  Article 50 applies from 2026-08-02 ([O] `L5.S46`). Every Flow output carries SynthID, and residents of Vietnam,
  India and South Korea get a visible mark automatically ([O] `L1.S6`).

## 15. The pages most of this stands on

| Key | Page | Date |
|---|---|---|
| L1.S1 | https://support.google.com/flow/answer/16352836 (models and modes) | read 2026-10-01 |
| L1.S2 | https://support.google.com/flow/answer/16353334 (ingredients, voices) | read 2026-10-01 |
| L1.S5 | https://support.google.com/flow/answer/16526234 (credits) | read 2026-10-01 |
| L1.S6 | https://support.google.com/flow/answer/16353333 (failures, watermark) | read 2026-10-01 |
| L2.S1 | https://ai.google.dev/gemini-api/docs/veo | 2026-09-17 |
| L2.S2 | https://ai.google.dev/gemini-api/docs/omni | 2026-09-23 |
| L2.S9 | https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/best-practice | 2026-10-01 |
| L2.S14 | https://deepmind.google/models/gemini-omni/prompt-guide/ | read 2026-10-01 |
| L2.S16 | https://cloud.google.com/blog/products/ai-machine-learning/ultimate-prompting-guide-for-veo-3-1 | 2025-10-16 |
| L2.F18 | https://discuss.ai.google.dev/t/repeated-image-to-video-audio-failures-on-veo-3-1-lite-and-fast/184792 | 2026-09-29 |
| L3.U1 | https://useapi.net/docs/articles/google-flow-ugc-product-video | 2026-09-04 |
| L3.D3 | https://discuss.ai.google.dev/t/critical-regression-gemini-omni-1-1-flash-update-destroyed-conversational-voice-continuity-for-ugc-creators/182433 | 2026-09-11 |
| lane 4 | https://github.com/ffroliva/gflow-cli/releases | 2026-09-30 |

## 16. Open questions

1. Answered on 2026-10-02 (section on voice routes, and `test-results.md` section 3): the owner heard one person in
   the four clips whose line is said as talk, and a fake, other voice in the four whose line describes the product.
   What is still open under it: does the rule hold past 6 s, with two lines of talk in one clip, with another
   character or start frame, with a male or a Northern voice?
2. Two speakers of one gender: which voice goes to whom, and is there a way to bind them?
3. A voice ingredient on Veo Fast: the composer allows one; never run.
4. Does anything a prompt controls change the Veo audio filter's odds? Five of eleven two-frame jobs failed; one
   pair passed both with and without the room-tone line.
5. More than one Extend in a row; Extend of a clip made with a voice ingredient.
6. Veo 3.1 Quality: never run (100 credits a clip).
7. External TTS with a lip-sync model, for a real recorded voice: not tested.
8. How Flow chooses the framing among several references (it followed the full-body one).
9. A video ingredient on Veo Lite or Fast; whether `@` reaches images in Ingredients mode (one probe saw none).
10. 16:9, and any language but Vietnamese.
11. Does gflow 0.81.0 keep this repo's prices and flows?
12. Sources the lanes could not reach: Reddit, X, YouTube transcripts, some Google Cloud pages (listed per lane).
