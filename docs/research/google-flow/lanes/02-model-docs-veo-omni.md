# Lane 2: official model documentation for Veo 3.1 and Omni 1.1 Flash (accessed 2026-10-01)

Research agent report, filed as returned (web research only; no Flow access, no credits spent).

# Official model documentation: Veo 3.1 and Omni 1.1 Flash

Tags: [S#] = official Google page (URL and date at the end); [F] = Google-run forum; [I] = my inference. Vertex AI docs now sit under Gemini Enterprise Agent Platform (AP). My rules allow one short verbatim quote; the rest is close paraphrase with exact numbers and identifiers (backticks).

## 1. Inputs and outputs
- Veo 3.1 and Fast: text (1,024 tokens), first frame `image`, `lastFrame` (only with `image`), up to 3 `referenceImages` typed `asset`, `video` for extension; no audio input. Output 4, 6 or 8 s, 24 fps, 16:9 or 9:16, 720p, 1080p, 4k; 8 s is forced for references, 1080p, 4k and extension. [S1][S6]
- A start frame excludes references and video; references need a prompt and exclude `image`, `video`, `lastFrame`; at most 3 asset images or 1 style image. [S8]
- Lite: text, first and last frame; no references, no 4k. [S1][S6]
- Omni 1.1 Flash: text, up to 10 images, up to 3 videos (10 s; reference clips 3 s each, their audio ignored). Audio input is unsupported in both APIs, although the model card lists audio as an input. Output 3 to 10 s, 24 fps, 360p or 720p native; 1080p and 4k are upscales. [S2][S5][S7][S19]
- Start image: 720p or larger, 16:9 or 9:16; other shapes may be resized or centre-cropped. [S22]

## 2. What each model is for
- Veo 3.1: final cuts where fidelity comes first. Fast: standard production. Lite: cheapest, for volume and iteration. [S17] Lite against Fast in Google's rating: 54.6% wins text-to-video, 47.2% image-to-video. [S24]
- The Gemini API overview says default to Omni (coherence, character consistency, conversational editing), Veo for extension and last-frame control; that page (2026-06-30) predates Omni 1.1. [S3]
- DeepMind: Veo needs precise instructions, Omni less prescription. [S14]
- Veo 3.1 is still the newest Veo: no successor in the changelog to 2026-09-23. [S5][S6]

## 3. Audio and speech
- Dialogue form: Google contradicts itself. AP best practices (Omni and Veo): colon after the speaker's action, no quotation marks, because quoted text gets rendered on screen. [S9] The Gemini API Veo guide and the Cloud blog say use quotes; S1 also uses speaker labels. [S1][S16]
- Audio goes in separate sentences; labels `SFX:`, `Ambient noise:`, `Audio:`. [S10][S15][S16] Omni invents a soundtrack by default: ask for music explicitly, exclude with `No dialogue`, `No extra sound effects`. [S2]
- Languages, both models: English fully supported, others not evaluated. [S1][S2] No lip sync specification exists; DeepMind lists natural, consistent speech, especially short segments, as unfinished for Veo. [S15]
- One voice: (a) name the character, add one fixed voice-style sentence, paste it unchanged, reuse the same `seed`. [S9] Seed is not dependable while `enhancePrompt` is on (default). [S8] (b) Omni API: no audio references, no voice editing. [S2] (c) Omni extension keeps audio coherent from the last 10 s and may add speech when extending its own clip. [S2] (d) Separate product: Gemini 3.8 TTS gives persistent `voice_...` ids; Vietnamese supported. [S23]
- Silence: AP has `generateAudio` (default true) and video-only prices; Gemini API Veo audio is always on. [S1][S8][S13]

## 4. Consistency
- Veo references: up to 3 asset images of one person, character or product, 8 s only, never with a start frame. [S1][S8]
- Omni: `<IMAGE_REF_N>` and `<VIDEO_REF_N>` tags, an example with 6 references, and a start frame plus references in one request (`[# Sources <FIRST_FRAME>@Image1] [# References <IMAGE_REF_0>@Image2]`). [S2]
- Both model cards list full consistency through complex scenes, motion or edits as unsolved. [S19][S20]

## 5. Prompting
- Veo formula: Cinematography + Subject + Action + Context + Style and Ambiance. [S16] Omni: framing and motion, style, lighting, location, action; state the intent. [S14]
- Camera vocabulary: S10 warns that some advanced angles and lenses are not officially supported. Omni terms: `one continuous shot`, `locked off`, `natural smartphone zoom`. [S14]
- Timing: Veo `[00:00-00:02]` blocks give several shots in one clip [S16], yet best practices say one moment per clip. [S9] Omni takes natural language or `[0-3s]` and cuts by default unless told `In a single continuous shot` or `No scene cuts`. [S2] The Veo 3 report admits a bias to frequent cuts and dramatic angles. [S20]
- Negatives: AP field `negativePrompt`, written as nouns (`wall, frame`), not with `no` or `don't`. [S10] The Gemini API Veo page no longer lists it. [S1] Omni has no field: short negatives inside the prompt. [S2]
- With an image: motion only; no re-description of character, background or light; generic pronouns; camera motion is the most reliable. [S9]

## 6. Interpolation and extension
- Guidance: describe the first frame, the exact camera path, and what the last frame reveals. [S14][S16] Omni loops when both frames are the same image. [S2] No failure mode is documented.
- Veo extension, Gemini API: 7 s per step, up to 20 times, 148 s maximum, 720p, only Veo clips from the last 2 days; it continues from the final second, and a voice absent from that second is not carried. [S1] AP: input 1 to 30 s, 37 s total. [S11]
- Omni extension: 3 to 10 s per step, 40 s total, last 10 s as context, final input frames get re-edited; uploads capped at 10 s; no new dialogue on an uploaded talking clip. [S2][S21]

## 7. Safety
- Veo has a documented audio error: safety filters or audio processing can block a video, uncharged. [S1][S4] Omni's docs list none.
- Google staff, 2026-09-29, on start-frame-only Lite and Fast failures: "false-positive audio filter triggered specifically by the ambient room tone instruction"; removing that line made the prompt pass on Lite. [F18]
- Support codes: Child 58061214, 17301594; Celebrity 29310472, 15236754; Video safety violation 64151117, 42237218; Sexual 90789179, 43188360; eight more categories; no audio code. [S12] Flow's `PUBLIC_ERROR_*` names are in no Google page I found.
- `personGeneration`: image-to-video, interpolation and references accept `allow_adult` only, and the Child code fires without `allow_all`, so a young-looking adult can trip it. [S1][S12][I] Omni: no uploads of minors (EEA, CH, UK) or of certain recognizable people. [S2]

## 8. Watermark and price
- SynthID on all output, C2PA supported; model docs say nothing about a visible mark. [S1][S2][S6]
- USD per second with audio: Veo 3.1 0.40 (720p, 1080p), 0.60 (4k); Fast 0.10, 0.12, 0.30; Lite 0.05, 0.08. [S4] Video-only on AP: 0.20, 0.40; 0.08, 0.10, 0.25; 0.03, 0.05. [S13] Omni: about 0.034, 0.10, 0.15, 0.30 at 360p, 720p, 1080p, 4k (17.50 per 1M output tokens). [S13]

## 9. API against Flow
- API only: `seed`, `negativePrompt`, `generateAudio`, `compressionQuality` `lossless`, Veo chains to 148 s, Omni role tags, multi-turn editing. [S1][S2][S8]
- Flow only: voice references for Omni. [S25]
- Lite extension: Gemini API no, AP yes, Flow help names Lite as the model that extends. [S1][S6][S25]

## Contradicts or explains our measurements
- Veo sharper and closer to the end frame: fits its fidelity positioning, contradicts the advice to default to Omni. Veo takes the last frame as a dedicated field; Omni decides from the prompt how to use images. [S1][S2][S3][I]
- Audio refusals: a Veo-only error class matches Omni 0 of 7. The staff reply contradicts our room-tone line (~/.claude/skills/flow-film-director/SKILL.md, section 11). 5 of 8 against 0 of 3 is p about 0.12; the link to two frames is undocumented. [I]
- One take, one voice: matches Omni's 10 s context design.
- Tail slowdown: undocumented; both extensions rework the last second or frames instead of starting from a literal last frame. [I]
- Duplicated object, invented face: undocumented. Veo cannot add a face reference to a start frame; Omni can. [S2][S8]

## Open questions only a real test can answer
1. Veo first and last frame with no audio line, against the room-tone line.
2. Veo speaking Vietnamese.
3. Whether Flow passes `<FIRST_FRAME>` and `<IMAGE_REF_0>` to Omni.
4. Whether Flow's Omni extend adds dialogue in the same voice.
5. Pitch spread across clips with one verbatim voice sentence.
6. Whether Flow's 1080p Veo download is an upscale.

## Sources I could not reach
- Web search quota ran out: no search for a visible-watermark policy or for Flow's error names.
- WebFetch failed on docs.cloud.google.com and once on TLS; all were then read in the browser.
- Veo 3.1 has no model card of its own. The Omni blog price table is an image.
- The fetch tool cached two PDFs outside the repo; nothing else written.

## Sources
S1 https://ai.google.dev/gemini-api/docs/veo (2026-09-17)
S2 https://ai.google.dev/gemini-api/docs/omni (2026-09-23)
S3 https://ai.google.dev/gemini-api/docs/video (2026-06-30)
S4 https://ai.google.dev/gemini-api/docs/pricing (2026-10-01)
S5 https://ai.google.dev/gemini-api/docs/changelog (2026-09-23)
S6 https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/veo/3-1-generate (2026-10-01)
S7 https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/omni-1-1-flash (2026-10-01)
S8 https://docs.cloud.google.com/gemini-enterprise-agent-platform/reference/rest/Shared.Types/VideoGenerationModelInstance (2026-09-01), https://docs.cloud.google.com/gemini-enterprise-agent-platform/reference/rest/Shared.Types/VideoGenerationModelParams (2026-05-07)
S9 https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/best-practice (2026-10-01)
S10 https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/video-gen-prompt-guide (2026-10-01)
S11 https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/extend-videos (2026-10-01)
S12 https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/responsible-ai-and-usage-guidelines (2026-10-01)
S13 https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing (accessed 2026-10-01)
S14 https://deepmind.google/models/gemini-omni/prompt-guide/ (accessed 2026-10-01)
S15 https://deepmind.google/models/veo/ (accessed 2026-10-01)
S16 https://cloud.google.com/blog/products/ai-machine-learning/ultimate-prompting-guide-for-veo-3-1 (2025-10-16)
S17 https://cloud.google.com/blog/products/ai-machine-learning/veo-3-1-lite-and-a-new-veo-upscaling-capability-on-vertex-ai (2026-04-04)
F18 https://discuss.ai.google.dev/t/repeated-image-to-video-audio-failures-on-veo-3-1-lite-and-fast/184792 (2026-09-29)
S19 https://deepmind.google/models/model-cards/gemini-omni-flash/ (August 2026)
S20 https://storage.googleapis.com/deepmind-media/Model-Cards/Veo-3-Model-Card.pdf (2026-01-13), https://storage.googleapis.com/deepmind-media/veo/Veo-3-Tech-Report.pdf (accessed 2026-10-01)
S21 https://blog.google/innovation-and-ai/technology/developers-tools/build-with-gemini-omni-1-1-flash/ (2026-08-27)
S22 https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/generate-videos-from-an-image (2026-10-01)
S23 https://ai.google.dev/gemini-api/docs/voice-replication (2026-09-24), https://ai.google.dev/gemini-api/docs/speech-generation (2026-09-30)
S24 https://deepmind.google/models/model-cards/veo-3-1-lite/ (2026-04-08)
S25 https://support.google.com/flow/answer/16353334, https://support.google.com/flow/answer/16352836 (accessed 2026-10-01)
