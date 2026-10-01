# Lane 1: Google's official documentation for Flow (accessed 2026-10-01)

Research agent report, filed as returned. `[Sn]` = an official Google page; URL and date in the source list (help-center
pages are undated: access date). Key rules were queried twice through a summarising fetch tool. A copyright rule allowed
the agent one verbatim sentence; everything else is paraphrase with exact numbers. No Flow access, no credits spent.

## 1. Modes and tools

- Video: Text, Frames (first, or first and last), Ingredients/References, Extend, Video to Video editing [S1].
  Ingredients: dragged images or videos, `@` assets, characters (`@Name`), avatar (`@me`), voices [S2].
- Edit and refine (Omni Flash only): source up to 60 s and 1 GB, trimmed to 30 s if longer; edits a segment of at most
  10 s; 3 conversational turns keep context [S3]. Not for uploaded video in EEA, Switzerland, UK [S7].
- Extend: appends footage, Veo clips only; an extended clip cannot take insert, remove or camera edits [S3]. Those three
  are only named in the help center; a blog describes object insertion, removal and camera moves [S22].
- Scenebuilder: sequence, reorder, trim, preview, download; no stated limits or resolution. Jump to is absent from
  today's help page. Save frame feeds ingredients or start/end frames [S3].
- Agent: storyboards, generates, edits, batches variations, organises assets; queries free under a daily quota, media
  costs credits [S9]. Tools: built from a description; media billed to whoever runs it [S10].
- Flow Music: separate product with its own credits [S26]. Flow TV: a showcase page.
- Images: three Nano Banana models; Nano Banana 2 Lite is the no-charge default [S1].

## 2. Support matrix and limits [S1]

| Model | Text, Frames (first; first+last) | Ingredients | Extend | Video edit |
|---|---|---|---|---|
| Veo 3.1 Lite | 4, 6, 8 s | 8 s only | yes | no |
| Veo 3.1 Fast | 4, 6, 8 s | 8 s only | no | no |
| Veo 3.1 Quality | 4, 6, 8 s | no | no | no |
| Gemini Omni Flash 1.1 | 4, 6, 8, 10 s | 4, 6, 8, 10 s | coming soon | yes, up to 10 s |

- Both aspect ratios in every supported cell. Any 8 s Veo 3.1 clip can be extended, but only Veo 3.1 Lite performs the
  extension. Omni: 720p standard, 360p draft at half the credits, upscaled to 720p for 0 credits on Pro and Ultra.
- NO official Flow cap for reference images, characters or voices per request, nor a maximum length after Extend. API
  numbers, which may differ in Flow: Veo takes up to 3 reference images, clip fixed at 8 s; extension adds 7 s, up to
  20 times, 148 s maximum [S16]. Omni: at most 3 video references of 3 s each [S15]; 10 s extensions to 40 s total [S14].

## 3. Characters [S4]

- Steps: Characters panel; describe or pick a pregenerated character; upload or generate 1 or 2 images of the look;
  Generate; name; Select a voice (library or custom); Add to Character; Done. Minimum one image. Called with `@Name` [S2].
- Google's claim: a reusable bundle of visual and audio references whose face, clothing and voice stay strictly
  consistent across generations.
- Not stated: image type, characters per prompt, supported models, what is not kept. The model card admits consistency
  through edits is still hard [S25].

## 4. Voices and audio

- Rule, verbatim: "You can add voice references only to video generations that use ingredients." [S2]
- Use: Omni Flash, Ingredients, Add, Voices; 10 s sample on hover; cite with `@Voice`; single-speaker reference [S2].
- Custom voice: Create New Voice; preset Base Voice; name; Voice Performance text (accent, timbre); optional Sample
  Dialogue for an 8 s preview; Sync; Save [S2]. A restyled preset, not a cloned recording.
- No audio upload: at launch voice references were Omni's only audio input [S12]; the API rejects uploaded audio
  references and voice editing [S15].
- Languages: Flow's interface list includes Vietnamese, but Google recommends English prompts [S7]. Veo and Omni API docs:
  English fully supported, others not evaluated [S15][S16]. Nothing official on Vietnamese speech. DeepMind: consistent
  natural speech, especially short lines, is still in development for Veo [S30].

## 5. Avatars [S8]

Your own visual and audio likeness, created from the profile menu by scanning a QR code with a phone, used with `@me`.
Unavailable in EEA, UK, Switzerland. Not shareable through Tools or public links. What is recorded is not stated.

## 6. Videos as ingredients

Flow's help says only that a video can be dragged in as a reference [S2]. Omni uses up to 3 s of video for visual context
and character consistency [S17]; per the API it works best for likeness and its audio is ignored [S15].

## 7. Credits and plans

- Grants: 50 daily for everyone; monthly 200 (Plus), 1,000 (Pro), 10,000 (Ultra $100), 25,000 (Ultra $200); no
  rollover [S5].
- Veo per generation or Extend, non-Ultra / Ultra: Lite 10 / 5, Fast 20 / 10, Quality 100 / 100; same for 4, 6, 8 s;
  Ultra also gets a 0-credit lower-priority Lite [S5].
- Omni 720p 7/10/12/15, 360p 4/5/6/7 for 4/6/8/10 s; Omni edit 40 [S5].
- Upscale: 1080p 0 credits for Plus, Pro, Ultra; 4K 50 credits, Ultra only [S5].
- Images free [S22]; failed generations not charged [S6].
- Vietnam monthly prices: Plus 132,000, Pro 489,000, Ultra 2,250,000 or 5,500,000 VND [S18].

## 8. Safety and filters

- Audio Generation Failed: Veo sometimes produces low-quality audio, so no video is made and credits are refunded;
  advice: retry or change the prompt [S6]. API: Veo 3.1 sometimes blocks over safety filters or audio processing issues,
  uncharged [S16].
- Protections for minors and uploaded photos of people block some requests; advice: send feedback [S6]. Never supported:
  prominent people, editing uploaded video of identifiable minors [S7]. Veo's API allows adults only with image
  input [S16]. Policy bans sexually explicit content and circumventing filters [S24].
- A model lacking the chosen feature is swapped for a compatible one [S6].
- Unusual Activity: wait a few minutes, disable VPN or proxy [S6].
- Watermark: invisible SynthID always; the visible one is automatic for residents of India, South Korea, Vietnam [S6].

## 9. Prompting advice

- Flow [S2]: references on a plain or segmented background; location and style references without extra subjects; text
  complements, never contradicts, the images; name the frames or ingredients in the prompt; one look across ingredient
  images.
- Omni [S17]: state intent, less prescriptive than Veo; ask for one continuous shot; when editing say what must stay.
  First and last frames: describe the first frame, the exact camera path, and what the last frame reveals.
- Veo [S16]: quotation marks for exact speech; describe sound effects and ambience explicitly.

## 10. Changes, August to October 2026

- 2026-08-27: Omni 1.1 Flash in Flow: start and end frames, 1080p and 4K export, 360p drafts [S13]; API release with
  40 s extension and video references [S14].
- 2026-09-23: Gemini 3.8 Flash TTS, Vietnamese among its best rated; not announced for Flow [S32].
- 2026-09-23: six community Tools [S20].
- No public Flow changelog found.

## Contradicts or explains our measurements

1. Veo length: docs say 4, 6, 8 s for text and Frames, 8 s only for Ingredients; we saw 8 s fixed. Check the mode.
2. Veo Quality showed no ingredient refusal; docs say unsupported and that Flow swaps models (inference: at submit).
   [Erratum, 2026-10-01 evening: the probe behind "no refusal" read the wrong marker. Read correctly, the composer
   refuses image, character and voice ingredients on Quality; see `../test-results.md` section 7.]
3. Extend at 10 credits fits extension running on Veo Lite. Omni extension is API-only so far.
4. The voice rule explains the voice-alone refusal and voiceless Frames. The 1-voice cap on Veo is undocumented.
5. AUDIO_FILTERED: Google blames low-quality audio and refunds. No official link to first+last frames; our 5 of 8 stays
   unexplained.
6. UNSAFE_GENERATION fits the people protections and sexual-content policy.
7. Omni edit: published 40, measured 20. Video-ingredient surcharge: undocumented.
8. Confirmed: 1080p free on Pro, 4K Ultra-only, no audio upload, Omni prices, Extend only on Veo.
9. Visible marks on our clips: the automatic watermark for Vietnam residents.
10. Google's consistency claim is between generations, not fidelity to a source photo.

## Open questions only a real test can answer

- Does Frames plus `@Character` carry the voice?
- Which model and price does Quality plus ingredients actually get?
- Are 4 and 6 s offered for Veo in text or Frames mode?
- Real caps per model for images, characters, voices; total length after repeated Extend.
- How is a video ingredient longer than 3 s trimmed?
- Can a Flow voice speak acceptable Vietnamese?
- First+last audio-filter rate, Veo against Omni, larger sample.
- Does Jump to still exist?

## Sources the agent could not reach

- x.com (HTTP 402): @FlowbyGoogle posts, seen only as search-result titles (voices as ingredients, 30 options, Ultra
  first).
- docs.cloud.google.com (Omni 1.1 Flash, Veo responsible AI): navigation only.
- flow.google.com and flow.google: not opened, by instruction.
- labs.google/flow/tv: no descriptive text.
- USD prices for Plus and Pro (pages served in VND); a September recap post.

## Sources

- S1 https://support.google.com/flow/answer/16352836
- S2 https://support.google.com/flow/answer/16353334
- S3 https://support.google.com/flow/answer/16935718
- S4 https://support.google.com/flow/answer/16935308
- S5 https://support.google.com/flow/answer/16526234
- S6 https://support.google.com/flow/answer/16353333
- S7 https://support.google.com/flow/answer/16353544
- S8 https://support.google.com/flow/answer/17102997
- S9 https://support.google.com/flow/answer/17093911
- S10 https://support.google.com/flow/answer/17104535
- S12 (May 2026) https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-omni/
- S13 (2026-08-27) https://blog.google/innovation-and-ai/models-and-research/google-labs/new-creative-controls-google-flow/
- S14 (2026-08-27) https://blog.google/innovation-and-ai/technology/developers-tools/build-with-gemini-omni-1-1-flash/
- S15 https://ai.google.dev/gemini-api/docs/omni
- S16 https://ai.google.dev/gemini-api/docs/veo
- S17 https://deepmind.google/models/gemini-omni/ and https://deepmind.google/models/gemini-omni/prompt-guide/
- S18 https://gemini.google/subscriptions/
- S20 (2026-09-23) https://blog.google/innovation-and-ai/models-and-research/google-labs/six-new-tools-built-by-creatives/
- S22 (2026-02-25) https://blog.google/innovation-and-ai/models-and-research/google-labs/flow-updates-february-2026/
- S24 (2024-12-17) https://policies.google.com/terms/generative-ai/use-policy
- S25 (updated August 2026) https://deepmind.google/models/model-cards/gemini-omni-flash/
- S26 https://support.google.com/flow/answer/17083870
- S30 https://deepmind.google/models/veo/
- S32 (2026-09-23) https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-8-text-to-speech/
