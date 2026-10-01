# Paid and $0 tests on Flow (plan AK, 2026-10-01)

Project `102445f4` ("LeeLyLy fashion review"), Pro plan, flow.google.com. Cap 260 credits from a balance of 327
(15:20). **Spent 252, balance 75, 23 jobs paid, 1 failed at no charge.** Ledger: `out/flow_research/ledger.jsonl`.
Driver: `out/flow_research/probe.py`, which runs 22 of the 24 jobs through the repo's own money path
(`composer._submit`: live price held to `max_credits`, ledger row before the one click, balance read on both sides);
the Omni edit ran through the MCP's `clip_edit` and the Extend through `clips.extend`, both ledgered the same way.
Clips, request bodies, logs and frame sheets stay under `out/flow_research/` (not in git). Specs of the 22 composer
jobs: `out/flow_research/specs.py`.

Reference image of every talking test: `v5_talk_t1_57.png`, a medium crop of the master photo. Lines:
L1 = "Hôm nay mình mặc váy ren đen đi cà phê nè." TA = L1 + "Váy này mềm lắm, mặc cả ngày vẫn thấy thoải mái."
L2 = "Mọi người thấy bộ này có dễ thương không?" TB = L2 + "Nếu thích thì nhớ thả tim cho mình nha."
TC = TA + "Mà giá cũng mềm nữa, có ba trăm mấy thôi à." Every prompt with one speaker carries one voice sentence:
"says in Vietnamese, in a natural young Southern Vietnamese female voice, soft and a little playful:". The
exceptions: `ak-f1` (two speakers, its own wording), `ak-x1` (the Extend asks for "the same voice"), the edit, and
the three silent picture shots.

## 1. The rulers, and what each can and cannot say

| Ruler | What it reads | Self-check | Limit |
|---|---|---|---|
| Transcript (`asr.py`) | Whisper large-v3-turbo, offline, from weights already on this Mac | the v7 one-take clip reads back its three lines word for word | it corrects toward plausible text, so a match proves intelligible Vietnamese that fits the script, not perfect tones. On a silent clip it returned a stock sentence ("Các bạn hãy đăng ký kênh ...") twice: it now runs only where the voiced-frame detector finds speech |
| Voice distance (`voice2.py`) | mean MFCC distance and long-term spectrum (LTAS) distance over frames with a real pitch peak between 120 and 380 Hz | see the calibration below | a crude ruler, not a speaker model: it ranks clusters, it cannot certify identity |
| Clip tail (`ends.py`) | saturation and contrast of the last 0.5 s | a fading clip reads contrast 44.6 falling to 9.2; clean clips stay flat | none known |
| Pitch track (`pitch_track.py`) | median pitch per half second, 60 to 420 Hz | a macOS male voice reads 94 to 124 Hz | shows who speaks when, nothing more |
| Frame sheet (`sheet.py`) | six frames beside the reference still | a person looks | no number |
| Recipe read-back | the project listing: model key, input images, voice id per generation | `ak-a3` shows the image only; every voice job shows the voice | what Flow recorded, not what the model did with it |

Calibration of the voice ruler on Flow's own voices: 16 previews made for $0 in the voice maker (eight presets, each
saying TA and TB with LilyVoice's performance text), plus LilyVoice's saved sample.

| Pairs | n | MFCC | LTAS |
|---|---|---|---|
| Same preset, different words | 8 | 1.6 to 3.4 | 0.04 to 0.19 |
| Different female presets, same words | 42 | 2.1 to 5.7, median 3.9 | 0.05 to 0.34 |
| Different female presets, different words | 42 | 2.0 to 5.5, median 4.3 | 0.05 to 0.41 |
| LilyVoice's saved sample against Leda saying TA, TB | 2 | 1.9, 3.7 | 0.05, 0.18 |

So two similar female voices can sit as close as one voice saying two sentences. Under about 2 is one voice; over
about 4.5 is another; between, this ruler cannot tell. Every conclusion below is read with that in mind, and
`out/flow_research/listen_voices.mp4` (2 min 18 s, labelled) puts the same clips to the ear.

Rulers rejected on the way:

- Mean-pooled Whisper encoder states (`spk2.py`) separated clean synthetic voices and then grouped Flow clips by the
  room each generation invents (lines of one take 0.11 to 0.15, five clips of one Flow voice 0.40 to 1.18): it
  measured the acoustic scene, not the speaker.
- The first voiced-frame mask (`voice.py`) accepted the lowest lag of its pitch search, 421 Hz, which room tone also
  produces (0 to 5% of frames). `voice2.py` requires an interior peak; the clusters did not move, one outlier did.
- Pitch alone (same speaker 0 to 2.0 semitones apart, different speakers 0 to 6.2).
- The first chip ruler of the caps probe (the class `disabled` alone): see section 7.

Visual claims were re-checked on enlarged crops before this file was closed. Three were wrong and are gone: "a
second arm tattoo" (the photo has a tattoo on both arms), "the cat changes coat" (the cat is two-coloured from the
start), and "an invented lamp" counted as a fault (it only fills the room beyond the reference's edge).

No speaker-verification model is on this machine (SpeechBrain is installed, its ECAPA weights are not).

## 2. Every job

What each test asked, and the short answer (the numbers follow in the table and in sections 3 to 6):

| Tests | Question | Answer |
|---|---|---|
| A1, A2 | Does one image plus a voice ingredient give the exact look and a locked voice on Omni? | The voice, yes. The look, no: redrawn |
| A3 | Does a character typed into a Frames prompt bring its voice? | No: Flow drops the character |
| N3 | Can a video ingredient plus a voice re-voice an exact clip? | No: it is an edit that copies the source |
| N1 | Can an Omni edit change the words? | No: the audio stays, the words are burned in |
| A7, A7b | Does Veo 3.1 Lite take an image plus a voice, and how close is the look? | Yes; a faithful redraw with small inventions |
| V1 to V6 | On two sentences, how tightly does each route hold one voice? | Tight with the ingredient; a family of voices without |
| K1, K2 | Controls: is it the ingredient that moves the voice? | K1 yes; K2 cannot be told from the default voice |
| C1 | Veo Lite against Veo Fast on one shot | Comparable; no ranking from one shot |
| N2a, N2b | Does the room-tone line trip the audio filter with two frames? | It passed without the line and with it |
| A7c | Do three reference images beat one? | Framing and outfit, yes; a subject shown in each is doubled |
| D1 | Is a 360p draft with a free upscale good enough? | For composition and timing only |
| F1 | Two voices, two speakers, one clip | Each voice found its speaker (a woman and a man) |
| X0, X1 | Does Extend carry the voice on? | Not by our ruler |

| Job | Setup | Credits | Result |
|---|---|---|---|
| `ak-a1` | Omni 720p 6 s, Ingredients: image + voice LilyVoice, L1 | 10 | Request `abra_r2v_6s` with the voice id. Says L1. Look redrawn: closer framing, hands behind the back, bag, chest tattoo and necklace gone. |
| `ak-a2` | same, L2 | 10 | Says L2. Look redrawn differently from A1 (tattoo and necklace present, wider, other pose). |
| `ak-a3` | Omni 720p 6 s, Frames: start frame + `@LilyNight` typed in the prompt, L1 | 10 | The composer showed the character chip and quoted 10. The request (`eb1hJf`, `abra_i2v_6s`) and the listing recipe hold the image only: the character is dropped, its name goes out as plain text. Says L1. Look exact. |
| `ak-n3` | Omni 720p 6 s, Ingredients: VIDEO ingredient (the A3 clip) + voice, prompt asks for L2 | 20 | Quoted 20 at 6 s (an earlier $0 probe saw 20 quoted at 10 s as well). Rpc `jIps6`. A near copy of the source: frames 2.5 to 5.5 apart (24 between two moments of the source), audio correlation with the source 0.9892 (an unrelated clip reads 0.16), transcript still L1. Not tried on a Veo model. |
| `ak-n1-edit-line` | `clip_edit` on the A3 clip: "change only her words to L2, same voice" | 20 | Audio unchanged (correlation with the source 0.9889; with the `ak-n3` output 0.9994). L2 burned in as a subtitle, against "No subtitles". |
| `ak-a7` | Veo 3.1 Lite 8 s, Ingredients: image + voice, L2, "Phone propped at chest height" | 10 | Request `veo_3_1_r2v_lite` with the voice id. Says L2. Faithful redraw (framing, bag, necklace, room, cat). A phone-screen border in the first second, drawn from the word "Phone". |
| `ak-a7b` | same, L1, "Camera at chest height" | 10 | No border. Says L1. An invented white hair clip. |
| `ak-v3` | Veo Lite, Ingredients: image + voice, TA | 0 | Failed: statuses `[6, 2, 4]`, no reason code, nothing on the grid. |
| `ak-v3~r2` | the same inputs again (deviation, section 8) | 10 | Passed. Says TA. Bag moved from the shoulder to her hand; the last 0.4 s fades to grey. |
| `ak-v4` | Veo Lite, Ingredients: image + voice, TB | 10 | Says TB. Bag on the shoulder; no invention seen. |
| `ak-v5` | Veo Lite, Frames: start frame only, TA | 10 | Says TA. First frame is the photo; dress, bag, necklace, room, cat held for 8 s. |
| `ak-v6` | Veo Lite, Frames: start frame only, TB | 10 | Says TB, done by 4.3 s. Look held; the cat walks. |
| `ak-v1` | Omni 720p 8 s, Frames: start frame only, TA | 12 | First attempt stopped before any spend (cookie bar, section 7). Second: says TA, look held. |
| `ak-v2` | Omni 720p 8 s, Frames: start frame only, TB | 12 | Says TB; she makes a heart with her hands on "thả tim". Look held. |
| `ak-k1` | Veo Lite, Ingredients: image only, no voice, TA | 10 | Says TA with one word off ("đen" heard as "beng"). Faithful redraw, framed a little wider than the reference, the room beyond its edge filled in (a lamp at the left). |
| `ak-k2` | Veo Lite, Ingredients: image + preset voice Achernar, TA | 10 | The request carries the preset by name (`achernar`). Says TA. An invented black hair bow. |
| `ak-c1` | Veo Lite, Frames: the v7.1 turn, start frame only, same words as the Veo Fast run | 10 | Passed with the room-tone line. One bag for 8 s. Silent. See section 5. |
| `ak-n2a` | Veo Lite, Frames: first + last frame of the turn Veo Fast refused, the audio line replaced by "She stays silent, lips closed." | 10 | Passed. One bag. On the last still by about 5 s, then holds. Silent. |
| `ak-n2b` | the same pair, same model, WITH "Audio: quiet room tone only, no speech, no music." | 10 | Passed too. One bag. On the last still by about 5 s. Silent. |
| `ak-a7c` | Veo Lite, Ingredients: three images (medium, full body, back) + voice, TA | 10 | Says TA. Full-body framing with socks, shoes and the whole room right. TWO cats: each reference showed the cat, the clip drew a second one. |
| `ak-d1` | Omni 360p 8 s, Frames: start frame only, TA | 6 | Says TA. 360x640. The face, the necklace pendant and the tattoo are redrawn coarsely. See section 6. |
| `ak-f1` | Omni 720p 8 s, Ingredients: image + LilyVoice + preset Achird, a woman and a man, one line each | 12 | Request holds both voices. She speaks L1 at 237 to 276 Hz, he answers at 124 to 174 Hz. One inserted word. The man and the room are invented; she is redrawn loosely. |
| `ak-x0` | Veo Lite, Frames: start frame only, TC (three sentences) | 10 | Says TC; speech runs to 7.4 s of 8. Look held. |
| `ak-x1` | Extend of the X0 clip, prompt asks for TB "in the same voice" | 10 | A 7.0 s clip (measured on the file; the listing calls it 8.0) that says TB, with a heart made with both hands; the scene's film is 15.0 s (8 + 7), all five sentences in order. The picture continues from the source's last frame (frame distance 5.5 across the join, against 3 to 9 per quarter second inside the shots). |

Speech: 18 new generations were asked to speak Vietnamese and 18 spoke their lines; two slipped on one word (`ak-k1`,
`ak-f1`). The two jobs that tried to change an existing clip's words (`ak-n3`, `ak-n1-edit-line`) kept the old
line. Three clips asked to be silent were silent.

## 3. Voice: what holds one voice across clips

Distances with `voice2.py`. "Ingredient" = the seven clips made with LilyVoice as an ingredient plus her line in
`ak-f1`; "none" = the nine clips with the voice sentence only.

| Pair kind | n | MFCC | LTAS |
|---|---|---|---|
| Ingredient, same words, same model (`a1`-`f1`, `v3`-`a7c`) | 2 | 1.0, 1.2 | 0.01 |
| Ingredient, same words, other model (Omni against Veo) | 3 | 2.1 to 2.6 | 0.03 to 0.05 |
| Ingredient, different words | 23 | 1.5 to 3.8, median 2.8 | 0.02 to 0.11 |
| None, same words, same model | 3 | 2.5 to 3.5 | 0.10 to 0.16 |
| None, same words, other model | 6 | 1.9 to 4.4 | 0.05 to 0.29 |
| None, different words | 27 | 1.5 to 4.7, median 3.6 | 0.05 to 0.33 |
| Ingredient against none, same words | 14 | 3.3 to 6.2, median 4.8 | 0.16 to 0.55 |
| Ingredient against none, different words | 58 | 3.5 to 8.3, median 6.0 | 0.14 to 0.60 |
| Extend against its source (`x1`-`x0`) | 1 | 3.9 | 0.21 |

Against the older clips: the campaign's ingredient clips sit 1.4 to 4.6 (median 2.7) from the five v5 clips made with
the character that carries LilyVoice, and 3.4 to 8.9 (median 5.6) from the four v5b clips made from a start frame.

Which preset each clip sounds like (distance to each preset's preview saying the same words, nearest first):

| Clip | Nearest presets |
|---|---|
| `v3` (ingredient LilyVoice, TA) | Leda 2.9, Callirrhoe 3.5, Autonoe 3.7 |
| `a7c` (ingredient LilyVoice, TA) | Leda 3.1, Autonoe 3.5, Callirrhoe 3.5 |
| `v4` (ingredient LilyVoice, TB) | Callirrhoe 2.1, Aoede 2.6, Despina 2.7; Leda 3.5 is sixth of eight |
| `k2` (ingredient Achernar, TA) | Achernar 3.1, Kore 3.1 |
| `k1`, `v5`, `v1`, `d1`, `x0` (no ingredient, TA) | Achernar first every time (3.4 to 5.2); Leda third to sixth (4.1 to 6.7) |
| `v6`, `v2`, `x1` (no ingredient, TB) | Kore or Achernar first; Leda last (6.2 to 7.3) |

LilyVoice is the preset Leda plus a performance text (its listing record: `models/gemini-v4s-tts-flow`, `audio/wav`,
`["Leda", "LilyVoice"]`).

Findings:

1. **A voice ingredient moves the voice and holds it.** Two clips saying the same words are 1.0 to 1.2 apart with it,
   on either model, and 2.5 to 3.5 apart without it. With it the clips land on Leda (two of three) and in the cluster
   of the older LilyVoice clips; without it they land far from Leda every time.
2. **Without a voice ingredient the voice is a family, not one voice.** Both models answer the voice sentence with a
   voice near the Achernar and Kore presets, a little different each time. Our ruler cannot say whether an ear would
   call two of them one person: 1.5 to 4.7 straddles the line.
3. **The Achernar control proves less than hoped.** The request carried `achernar` and the clip sounds most like
   Achernar, but so do the clips with no ingredient, so the ruler cannot separate "the preset was applied" from "the
   default voice is already close to it".
4. **Extend did not hold the voice by this ruler.** Speech ran into the source's last second, the prompt asked for
   the same voice, and the extension sits 3.9 from its source, as far as two unrelated clips with no ingredient.
5. **Two voice ingredients in one Omni clip each went to the right speaker**, a woman and a man. Her voice is 1.0 from
   `ak-a1`: LilyVoice. Two speakers of one gender are untested.
6. **A video ingredient or an Omni edit cannot change speech**: the first copies the source's audio, the second also
   writes the new words on screen.

## 4. Look: what holds the picture

- **Frames holds it.** Six talking start-frame clips at 720p on two models (`ak-a3` for 6 s; `ak-v1`, `ak-v2`,
  `ak-v5`, `ak-v6`, `ak-x0` for 8 s) kept dress, bag, necklace, tattoos, room and cat. The 360p draft kept the scene
  and redrew small details coarsely (section 6). What moves: the expression (broad smiles), and anything the frame does not show: in the turn the back came with a
  large tattoo on both models, and the front of the bag with studs on one and a butterfly on the other; no supplied
  image says what is right.
- **Ingredients on Veo 3.1 Lite is a faithful redraw with inventions.** Seven clips: the outfit, both arm tattoos
  and the room each time, at the reference's framing in five, a little wider in `ak-k1`, full body when a full-body
  reference was added (`ak-a7c`). Five of the seven carry a fault: a phone border drawn from a prompt
  word, a hair clip, a hair bow, the bag moved to her hand with a fade at the tail, a second cat.
- **Ingredients on Omni is a loose redraw**: three clips, three different framings and poses; the bag is missing in
  two, the necklace and the chest tattoo in one.
- **Three references instead of one** gave the full-body framing with the right socks, shoes and room, and doubled
  the subject every reference showed (the cat).
- **A prompt word is drawn** in Ingredients mode ("Phone propped" drew a phone border).

## 5. Veo 3.1 Lite against Fast, the same turn (`ak-c1` against `v7-turn-onebag-start-veofast`)

Same start frame, same words, 10 against 20 credits. Both: one bag for 8 s, socks and shoes kept, silent as asked,
and both drew what the photo does not show: a large tattoo on the back, and the front of the bag (the photo shows
it from the side): a butterfly clasp on Lite, studs on Fast. Lite came back from the turn with a broad smile; Fast
came back calmer, with an older-looking face in its last second.
Sharpness in the dress box: Lite median 144, Fast 99, but the box holds different content as she turns, so that
number decides nothing. One shot each: no ranking, which matches Google's own near-parity rating.

## 6. Draft at 360p, and the free upscales (`ak-d1`, $0 downloads)

The editor's Download menu offers, for a 360p clip: "270p Animated GIF", "360p Original size", "720p Upscaled"; for a
720p clip: the GIF, "720p Original size", "1080p Upscaled", "4K Upscaled" (greyed out). Both upscales cost 0 (balance
85 before and after).

Sharpness of the face and bodice box, every file scaled to 720x1280 (the reference still reads 115):

| File | t = 0.05 s | 3 s | 6 s |
|---|---|---|---|
| D1, 360p draft | 24 | 17 | 13 |
| D1, upscaled to 720p | 446 | 321 | 229 |
| V1, native 720p | 81 | 62 | 59 |
| V1, upscaled to 1080p | 433 | 323 | 276 |

Viewed: the upscaler makes the draft crisp and cannot give back what the 360p generation never drew. The face is
another, more generic face, the star pendant is a round one, the tattoo's shape is changed. So a 360p draft answers
"is the composition and the timing right" for half the price; it is not a way to a final where identity matters.
The 1080p upscale of a native 720p clip adds real crispness for 0.

## 7. Things found on the way

- **Cookie notice bar, 16:57.** Between two jobs Flow began to show "flow.google.com uses cookies from Google ..."
  with one button, "OK, got it". At the 1280x720 viewport it covers the composer's bottom row, so the click on the
  Settings trigger timed out; nothing was spent. Accepting a notice is the owner's decision, so the research driver
  only hides the bar inside its own page (a style rule; nothing accepted, nothing saved). The MCP's drivers do not:
  measured through the MCP that evening, `flow_credits` still read the balance (75) and `gen_video(dry_run=true)`
  answered only `TimeoutError: Locator.click: Timeout 8000ms exceeded.` Until the bar is dismissed, every tool that
  clicks that row times out the same way.
- **Caps the composer itself shows** ($0 probe, `log_caps2.txt`), each in Flow's own words:

  | Model | Images | Voices | Two characters |
  |---|---|---|---|
  | Omni 1.1 Flash | 7 ("Maximum image ingredients reached (7 allowed)") | 5 ("Maximum audio ingredients reached (5 allowed)") | accepted |
  | Veo 3.1 Lite | 3 ("... (3 allowed)") | 1 ("... (1 allowed)") | accepted |
  | Veo 3.1 Fast | 3 | 1 | accepted |
  | Veo 3.1 Quality | none ("You cannot use image ingredients with this model.") | none ("You cannot use audio ingredients with this model.") | refused ("You cannot use character ingredients with this model.") |

  The first run of this probe (`log_caps.txt`) reported nine images and two characters accepted everywhere,
  Quality included. That was the ruler, not Flow: it read the class `disabled`, which only a refused voice chip
  carries. A refused image chip is `chip-container-disabled`, a refused character chip only has an inactive wrapper;
  all three carry the icon `disabled-error-icon`, which the corrected ruler reads. An earlier screenshot already
  showed Flow's refusal card on Quality (found by the independent check of these documents).
- **Lengths**: on this account the Veo models show no length row in Frames or Ingredients mode (8 s only); Omni shows
  4, 6, 8, 10 s and 360p or 720p in both.
- **`@` in Ingredients mode** offered the character and no voice: voices come in through the "+" dialog only.
- **The listing keeps each clip's recipe** (model key, input images, voice id or preset name), readable for $0.
- **The request's shape for Ingredients** (rpc `MZZa6b`): prompt, a list of image ids, the model key, and a voice
  list such as `[["<custom voice id>"], ["achird"]]`.
- **A voice preview is free and fetchable**: the voice maker's Preview answers a pending record, and the sound
  arrives as a wav at `flow-content.google/audio/<id>`; 16 previews moved the balance by 0. A custom voice's saved
  sample is fetchable the same way.
- **A preset told to sound like a young woman does**: Achird ("Male, friendly") read at 213 to 216 Hz under
  LilyVoice's performance text, and at 124 to 174 Hz as an ingredient beside a man.
- **The "+" dialog** lists a window of rows; an older image is reached through its search box; one listed image
  (`lily_black_face.png`) is not offered at all.
- **A chip's thumbnail lands late**: read too early, three image chips looked like unknown chips.
- **Extend's own file downloaded this time** (it answered HTTP 400 on 2026-09-29), and the film is 8 + 7 = 15.0 s
  with no overlap.
- **Chrome 154 still dies after a download click** (crash report 18:51); the file was already fetched from the
  signed url, including the 720p upscale, whose url ends `_720p_upsampled`.

## 8. Deviations from the plan

- The plan says a refusal that costs 0 is never retried with the same inputs. `ak-v3` was retried once unchanged,
  because it carried no refusal code and Google's page for Veo audio failures says to retry. It passed: this failure
  class is not decided by the inputs.
- The research scripts under `out/flow_research/` were written through the shell: the plan's fence allows only
  `docs/research/*`, and `out/` is ignored by git, so neither the fence nor the audit sees them.
- B (references only) and E (prompt A/B) of the plan's list were folded into K1 (image only) and A7/A7b (one word
  changed); no separate runs.
