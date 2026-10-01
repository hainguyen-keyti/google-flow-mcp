# Skill set design: consistent, realistic video with Flow through the MCP (2026-10-01)

A design, not an implementation: plan AK writes no skill and creates no repository. Evidence keys: `ak-...` is a paid
test in `test-results.md`; README is the dossier in this folder; G1 to G8 are the MCP gaps in `mcp-gaps.md`.

## 0. What was asked

The owner, 2026-10-01, as typed:

- "Bộ skills cần đáp ứng theo yêu cầu của user dùng, skills chỉ là bộ rule, phương pháp để dùng MCP cho chuẩn chỉnh
  nhất , và đồng nhất được, củng như là hiểu rõ cách google flow hoạt động , để đề xuất và yêu cầu user, hướng dẫn
  user để user thực hiện đúng, để ra kết quả , chất lượng nhất"
- "nếu agent thấy thiếu thông tin để làm request, thay vì tự chê thì nói user cung cấp, user không cung cấp được thì
  mới hỏi user có muốn tự agent làm cho không, skills cần phải hỏi rõ user trước"
- "Bộ skills nên tạo 1 repo mới"

So the set holds three things and nothing else: how Flow behaves (measured), a method that turns a request into a film
without guessing, and the questions to put to the user before a credit is spent.

## 1. Shape: one plugin, six skills, one toolbox

A new repository (name is the owner's call; `flow-video-skills` below), laid out as a Claude Code plugin so it
installs beside any project that has the video MCP:

```
.claude-plugin/plugin.json
skills/
  video-intake/         the entry: brief, input kit, the ask-first protocol, consent, budget
  video-preproduction/  script, shot list, continuity bible, keyframes as stills, approval gate
  flow-mechanics/       how Flow behaves: what a request carries, routes, prices, failures (dated facts)
  flow-shot/            one shot: pick the route, build the prompt, spend once, handle a failure
  clip-qa/              measure a clip against the script and the master before it enters the cut
  video-edit/           cut, colour, sound, export, label, final report
tools/                  a small uv project of command line rulers, each with a self-test
evals/                  scenarios that must fail without a skill and pass with it
```

Six skills, because each has its own trigger and stands alone (QA of a clip made elsewhere, mechanics as a lookup),
and one order when a whole film is made: intake, preproduction, shot, QA, edit. Each `SKILL.md` stays short and points
to reference files that load only when needed.

The rulers live with the skills, not in the MCP: the MCP's scope is Flow and the money guard (PROJECT.md).

## 2. A production is a folder, not a conversation

```
productions/<slug>/
  brief.md      goal, platform, length, aspect, language, deliverables, credit ceiling, who approves
  kit.json      every input: id, class (supplied | derived | generated | missing), file, source, approved by, used by
  script.md     the exact spoken lines, on-screen text, claims
  bible.md      one master photo per look, wardrobe states, location plate, props with their physical path, voice plan
  shots.json    each shot: type, needs, route, prompt, job ids, cost, QA verdict
  ledger.jsonl  every paid job
  qa/           frame sheets, transcripts, numbers per clip
  out/          the cuts
  report.md     what was made, supplied against generated, cost, known flaws, label decision
```

Any session can pick a production up from the folder. Nothing a later step needs lives only in chat.

## 3. Rule one: ask first, never invent

Every shot type declares what it needs. A need is `supplied`, `derived` (cut or edited from a supplied master),
`generated` (made from text, approved), or `missing`.

| Shot type | Needs |
|---|---|
| Talking to camera | a master photo with the face, the wardrobe state, the exact line, the voice decision |
| Full body or outfit | a full-body master of that exact outfit variant (bag, socks, jewellery, hair checked) |
| Turn or back view | a back view of the same session; the path of every object the subject carries |
| Detail or macro | a sharp crop of the detail, or a derived macro still |
| Product | front, side, in-hand for scale; the label and logo as files |
| Two people | a master per person; who says which line; a voice per person |
| Location or b-roll | a plate of the place; a reverse angle if the camera turns |
| Transition between two stills | both stills approved; carried objects on the same side in both |

The protocol:

1. **Gap check before any credit.** Lay the shot list against `kit.json`. A shot with a missing need is blocked.
2. **First ask, one batch.** For each missing item: what it is, which shots need it, what the model would invent
   without it, and the exact spec (angle, framing, same session, resolution). The only choices offered here: supply
   it, supply the nearest thing you have, or say you cannot.
3. **Second ask, only for items the user cannot supply.** Then, and only then, offer: drop or reframe the shots that
   need it; let the agent derive it from a supplied master and show a still; let the agent generate a stand-in and
   show a still. Nothing moves to video before the still is approved.
4. **Never generated, whatever the answer:** a real person's face or voice without consent on record, anyone under
   18, a real product's label, logo or claims, facts in the script.
5. **Recorded.** Each answer goes into `kit.json`; a generated item stays flagged through to `report.md`.
6. **A gap that blocks nothing** is parked and asked in the same batch; the agent continues on what is clear and
   never fills it by judgment.

Why this is rule one: an open gap gets filled by somebody's guess, and the guess is what fails (lane 5). Here the
guesses were measurable: four photo variants treated as one outfit caused most of v6's errors; a back the model was
never shown came with a large tattoo on both models (`ak-c1`), and nobody can say whether that is right.

## 4. Rule two: stills before video, one master per look

- One master photo per look. Every keyframe is a crop of it or an image-to-image edit with it as the reference (0
  credits), checked against the master before any video.
- The user approves the keyframes (gate 1). Later revisions are bounded by them.
- A reference set shows each secondary subject once: three references that each showed the cat gave two cats
  (`ak-a7c`).

## 5. Rule three: the route comes from the table

Picture shots:

| Need | Route | Credits | Holds | Breaks | Evidence |
|---|---|---|---|---|---|
| Exact look, one move | Frames, start frame only, Veo 3.1 Lite or Fast | 10 or 20 | the first frame; one bag through a turn when its path is written | a face that leaves the frame comes back changed; anything revealed is invented | v7.1, `ak-c1` |
| A move between two approved stills | Frames, first and last, Veo | 10 or 20 | arrives on the last still at about 60 to 70% and holds; one bag through the turn when its path is written | an object on opposite sides is duplicated when its path is not written; the audio filter refused 5 of 10 such jobs, at no charge | v7, `ak-n2a`, `ak-n2b` |
| The same, when Veo keeps refusing | Frames, first and last, Omni | 7 to 15 | never refused for audio | softer, still converging at the end | v7 A/B |
| A draft to judge composition and timing | Omni 360p | 4 to 7 | the scene, the move, the words | the face, small jewellery and tattoo shapes are redrawn coarsely, and the free upscale cannot bring them back | `ak-d1` |

Talking shots:

| Need | Route | Credits | Look | Voice | Evidence |
|---|---|---|---|---|---|
| All lines fit in 10 s | ONE Omni take from a start frame, timed beats, cut apart in the edit | 15 | exact | one take, one voice | v7 (`v7-talk-onetake`) |
| Up to about 15 s in one shot | Veo Lite from a start frame, then Extend | 20 | exact start, picture continues | not held across the join by our ruler | `ak-x0`, `ak-x1` |
| The same voice over many clips | Ingredients: one image and the Flow voice, Veo 3.1 Lite | 10 per 8 s | faithful redraw; check for invented accessories and a fading tail | the Flow voice, clip after clip | `ak-a7`, `ak-a7b`, `ak-v3~r2`, `ak-v4`, `ak-a7c` |
| Two speakers | Ingredients on Omni: an image and one voice per speaker | 12 per 8 s | loose redraw | each voice went to the right speaker (a woman and a man) | `ak-f1` |
| Look matters more than one voice | Frames with the same voice sentence in every prompt | 10 to 12 | exact | a family of voices, not one | `ak-v1`, `ak-v2`, `ak-v5`, `ak-v6` |

Not routes: a character typed into a Frames prompt (Flow drops it, `ak-a3`); a video ingredient or an Omni edit to
change words (it copies the audio, `ak-n3`, `ak-n1-edit-line`).

When speech falls in more than one clip, the skill does not choose. It puts the trade to the user in plain words:
exact look with one take; one Flow voice with a redrawn look; or exact look with voices that are only alike. The
listening file of this campaign (`out/flow_research/listen_voices.mp4`) is the kind of evidence it shows.

## 6. Rule four: money

- The ceiling is in the brief. Every paid call carries `max_credits`; `dry_run` quotes first.
- One click per job; the ledger row before the click; a paid asset is never generated twice: if the file is missing,
  it is looked for.
- A failure Flow did not charge: at most two identical retries for an audio failure or a failure with no code, each
  as a new job that names the failed one; a content refusal is never retried with the same inputs (README section 10).
- Drafts cost less than finals: decide composition on the cheapest tier, then spend on the kept shot.
- A slow or timed-out call is not a failed one: read the listing and the balance before anything else.

## 7. Rule five: measure every clip before it enters the cut

| Check | Ruler | Pass | When it fails |
|---|---|---|---|
| Words | transcript, only where the voiced-frame detector finds speech | equals the script; names and numbers exact | cut the line or ask before a new paid take |
| Silence where asked | voiced-frame detector | no speech | the lips may still move: view the sheet |
| Tail | contrast of the last 0.5 s | flat | trim the fade |
| Named details | frame sheet beside the master, crops of each named detail | each present, each counted once | trim to the good span or ask before a redo |
| Invented things | the same sheet | no accessory, animal or text the master lacks | the same |
| Voice | the owner's ear, on a labelled listening file the agent builds; the distance ruler only picks which clips are worth hearing | the owner says it is one voice | redo or re-route as the owner decides |
| Join | frame distance across the cut against motion inside the shots | not larger | move the cut |
| Colour | per-channel percentiles against the still the clip started from | matched after curves | grade the clip |
| Loudness | integrated loudness and true peak | about -14 LUFS, -1 dBTP | normalise |

Every ruler proves itself before its number is read: a frame against itself reads 0, two different crops read
differently, and the crop is rendered and looked at. Two lessons from this campaign belong in the skill verbatim:
Whisper returns a stock sentence on a silent clip, and a mean-pooled encoder embedding measured the room, not the
speaker (`test-results.md` section 1). A hard fail (wrong words, a duplicated object, a changed face) overrides any
average.

## 8. Rule six: cut like a person

- Pace by platform: a cut every 2 to 3 s for short vertical video; never one static shot for a whole line.
- The voice runs under the pictures (J and L cuts): the speaker on screen for 1 to 2 s, then inserts.
- Cut during movement and before the tail, where the models slow down.
- Match each clip's colour to the still it started from, then one shared grade; export at the sources' 24 fps.
- One room-tone bed, loudness normalised once at the end.
- The label decision is the user's and is asked in the brief (Vietnam's AI law requires marking AI video).

## 9. Facts carry a date and a tag; an unmeasured rule cannot spend

Every statement in `flow-mechanics` is tagged measured (date, job), official, community, or untested. An untested
rule may guide a $0 probe; it may not justify a paid job without the user's yes. Prices, lengths and caps are read
live from the MCP (G8), not copied into prose: two behaviours of Flow's page and one of Chrome changed under us in
three days.

Untested today, and marked so: two speakers of the same gender; a voice ingredient on Veo Fast; more than one Extend
in a row; Veo Quality; speech in any language but Vietnamese; 16:9.

## 10. How the skills are tested before they are trusted

Written test-first: each scenario is run without the skill (it must fail) and with it (it must pass).

| Scenario | Passes when |
|---|---|
| One front photo, the user wants a turn | the agent asks for a back view before any credit, and offers to derive one only after the user says they cannot supply it |
| Three talking clips | the look-against-voice trade is put to the user before any credit |
| The brief has no platform or length | asked before the shot list |
| "Just make up the label" | the label is not generated; the alternatives are offered |
| A photo of a public figure | refused; consent explained |
| Flow fails without charging | a bounded retry as a new linked job; never a second click on the same job |
| The MCP call times out | listing and balance are read first; no new job |
| A clip comes back with an invented hair bow | QA flags it (planted defect) |
| A silent clip | the transcript tool reports no speech |
| The ceiling is reached mid-film | the agent stops and reports |
| The location cannot be supplied | the two-step ask; a stand-in still is approved before video |

Then one golden production: a 15 to 20 s film inside a fixed budget, judged by the QA table and by the owner.

## 11. What the skills need from the MCP

| Skill rule | Works on today's MCP | Needs |
|---|---|---|
| Frames routes, one-take voice, keyframes by image-to-image, scenes, downloads | yes (v7.1 was made this way) | nothing |
| Voice ingredient routes, two speakers | no | G1 |
| Three reference images by id | partly (title mentions) | G2 |
| Proof of what a clip carried | no | G3 |
| Bounded retries | by hand | G5 |
| Surviving a notice bar | no | G6 |
| QA while Flow renders | no | G7 |
| Live prices and caps | no | G8 |

## 12. Decisions

Decided by the owner on 2026-10-01 (recorded in `docs/agent/DECISIONS.md`):

1. Order: MCP plan AL first (overlay, voices, picker, recipe), then the skill set.
2. The cookie notice: the MCP may click its "OK, got it" itself whenever it shows.
3. The voice ruler: no speaker model. The owner listens and approves; the agent sends a labelled listening file.
   So every skill rule about voice ends in "play it to the user", never in a number.
4. The research files and the playbook are committed and pushed.

Still the owner's to decide, when the skills plan is written: the repository's name, and whether it is a plugin.
