# Lane 6: voice and audio (accessed 2026-10-01)

Research agent report, filed as returned (web research only; no Flow access, no credits spent).

# Voice and audio lane (web research, 2026-10-01)

Resumed after the network cut; complete. The 200-search cap ran out late (gaps listed last). My copyright rule allows one short verbatim quote, so limits are paraphrased beside the URL holding the wording. All fetched 2026-10-01; page dates in brackets. Read, not edited: ~/.claude/skills/flow-film-director/SKILL.md.

## 1. Flow voices
- [official] https://support.google.com/flow/answer/16353334 : a single-speaker voice reference keeps a voice across clips; steps require Omni Flash; typed as @Voice; accepted only by generations that use ingredients, otherwise an error. Custom voice: preset base plus a written Voice Performance, with an 8 s sample preview.
- [official] https://ai.google.dev/gemini-api/docs/speech-generation (2026-09-30), [vendor] https://useapi.net/docs/api-google-flow-v1/post-google-flow-videos : the 30 presets are the Gemini TTS voices: Zephyr, Puck, Charon, Kore, Fenrir, Leda, Orus, Aoede, Callirrhoe, Autonoe, Enceladus, Iapetus, Umbriel, Algieba, Despina, Erinome, Algenib, Rasalgethi, Laomedeia, Achernar, Alnilam, Schedar, Gacrux, Pulcherrima, Achird, Zubenelgenubi, Vindemiatrix, Sadachbia, Sadaltager, Sulafat.
- [official, search title only] https://x.com/FlowbyGoogle/status/2039813897180656015 (2026-04-02 by post id): launched with 30 options, Ultra first; [inference] before Omni (2026-05-20), so on Veo 3.1.
- [vendor] same useapi page, https://useapi.net/docs/changelog (2026-04-03 to 06-09): Veo takes 1 voice, reference mode only, needs an image or character, not on Quality; Omni 1.1 takes up to 5 (3 in video-to-video edit); frames-only Omni requests take no voice, reference image or character. Binding a voice to one of several speakers is undocumented.
- [official, search snippet only] https://support.google.com/labs/answer/16358089 : voice ingredients do not carry over on Extend.
- [official] https://support.google.com/flow/answer/17102997 : an Avatar carries your own face and voice as @me; unavailable in EEA, UK, Switzerland. [community-firsthand] https://discuss.ai.google.dev/t/google-flow-avatar/179475 (2026-08-28): the enrolled voice is ignored unless the prompt asks for it.
- [community-firsthand] https://discuss.ai.google.dev/t/critical-regression-gemini-omni-1-1-flash-update-destroyed-conversational-voice-continuity-for-ugc-creators/182433 (2026-09-11): since 1.1 every clip gets a new voice; presets sound too studio-clean; staff redirected to Flow feedback; another user confirms no audio-file input. No fix or follow-up found. [community-firsthand] https://github.com/ffroliva/gflow-cli/issues/738 (2026-09-07): attaching a voice to a character is proven, its effect on rendered audio is not.

## 2. Non-English speech
- [official] https://ai.google.dev/gemini-api/docs/veo , https://ai.google.dev/gemini-api/docs/omni : English is the only evaluated language. [official] https://deepmind.google/models/veo/ : consistent speech, short segments especially, is still in development.
- [community-firsthand] https://github.com/ulmeanuadrian/kie-mcp/issues/1 (2026-09-22): Romanian on Veo; 3 of 4 prompt variants made the same sentence worse, checked by transcription: variance, not wording. A start image pulls the accent toward the face shown.
- [community-firsthand] https://invideo.io/blog/gemini-omni-flash-review/ : Omni lip sync holds about 6-7 s (30+ outputs).
- No measured Vietnamese report for Veo or Omni found.
- Tips [seo-blog] https://prompt-architects.com/blog/101-veo-dialogue-prompts (2026-08-26): line in the prompt's first third (Veo input cap 1,024 tokens), timed aloud, commas for beats, stage directions for pauses, wider framing.

## 3. Dialogue formatting
- [official] https://cloud.google.com/blog/products/ai-machine-learning/ultimate-prompting-guide-for-veo-3-1 (2025-10-16): Google's examples put the line in quotation marks after says and a comma, add `SFX:` and `Ambient noise:` sentences, and time beats as `[00:00-00:02]`.
- Colon instead of quotes: a search summary credits https://deepmind.google/models/veo/prompt-guide/ , but my fetch showed no such rule there; unconfirmed, treat as [seo-blog].
- [official] Omni guide: Omni renders requested text legibly, so keep the no-subtitles negative.
- [community-firsthand] https://discuss.ai.google.dev/t/veo-3-api-generate-audio-parameter-not-supported-last-frame-limitations/119206 (2026-03-01): Veo has no audio off switch; describe a minimal soundscape.

## 4. Audio safety filter
- [official] Veo guide: a video can be blocked for safety or processing problems in its audio; the API does not bill it. [seo-blog] https://creatide.ai/blog/veo-content-violation-why-flow-rejects-prompts-and-wastes-credits : Flow refunds are not officially confirmed.
- [community-firsthand] https://github.com/googleapis/js-genai/issues/1272 (2026-01-23): the same prompt and image pass or fail at random, 3-5 tries; closed, not planned. https://discuss.ai.google.dev/t/veo-3-1-audio-safety-false-positives-are-burning-a-huge-share-of-a-tight-tier-1-daily-quota/175949 (2026-07-24): three failures in a row despite simpler prompts; another composition passed at once; audio cannot be switched off. [vendor] useapi lists PUBLIC_ERROR_AUDIO_FILTERED among the most seen failures.
- [inference] The check judges generated audio, so rewording does little. Legitimate: start frame only, or Omni for first+last shots; plain room tone; no named music, real voices, singing, children's voices; change composition rather than re-roll; file Flow feedback.

## 5. External route
- Gemini 3.8 Flash TTS [official] https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-8-text-to-speech/ (2026-09-23), https://ai.google.dev/gemini-api/docs/pricing : Vietnamese named; 0.50 USD/M text plus 9 USD/M audio tokens until 2026-12-31, then double; free tier; replication from a 30 s sample plus the owner's consent recording (region-limited in AI Studio; Vietnam not excluded); SynthID. [community-firsthand] https://discuss.ai.google.dev/t/gemini-2-5-pro-preview-tts-inconsistent-voice-and-tone-output/108511 : older TTS drifted between identical calls.
- ElevenLabs [vendor] https://elevenlabs.io/docs/overview/capabilities/text-to-speech/eleven-v4 , https://elevenlabs.io/pricing : v4, v3 and Flash v2.5 list Vietnamese, Multilingual v2 does not. Starter 6 USD (instant clone, commercial), Creator 22 USD (professional clone).
- Azure [official] https://learn.microsoft.com/en-us/azure/ai-services/speech-service/personal-voice-overview (2026-09-09): clone from 5-90 s plus the speaker's recorded statement; limited access.
- Google Cloud [official] https://docs.cloud.google.com/text-to-speech/docs/chirp3-hd , https://docs.cloud.google.com/text-to-speech/docs/chirp3-instant-custom-voice : vi-VN supported, no pause tags or custom pronunciation; cloning allow-listed, Vietnamese consent sentence provided.
- Vbee [vendor] https://help.vbee.vn/docs/faq : Vietnamese-only cloning from 10 s, commercial use allowed, API by quote. FPT.AI [vendor] https://fpt.ai/products/fpt-ai-voice-maker/ : three regional accents, no prices shown.
- Open models [vendor]: https://github.com/pnnbao97/VieNeu-TTS Apache-2.0, 48 kHz, clones from 3-8 s; https://huggingface.co/capleaf/viXTTS Coqui licence, weak under 10 words; https://huggingface.co/hynt/F5-TTS-Vietnamese-ViVoice non-commercial.
- Keep Flow's lips, swap the timbre: [community-firsthand] https://ai-for-real-life.beehiiv.com/p/the-secret-to-consistent-characters-in-veo-3-and-how-to-fix-the-voices-too (2025-08-18) runs every clip through ElevenLabs Voice Changer. [vendor] https://elevenlabs.io/docs/overview/capabilities/voice-changer : 29 languages, Vietnamese absent; 1,000 characters per minute. Open alternative https://github.com/Plachtaa/seed-vc , GPL-3.0.
- Lip sync [vendor]: https://sync.so/docs/models/lipsync : lipsync-2 0.04-0.05, 2-pro 0.067-0.083, sync-3 0.107-0.133 USD/s; the source must already look like it is talking; 512 px face and weak profiles on v2. https://kling.ai/quickstart/ai-lip-sync-guide : 5 credits per 5 s. https://www.hedra.com/models/video/hedra/character-3 : still image plus audio only. HeyGen [seo-blog] https://fluxnote.io/guides/heygen-video-translation-guide : translation workflow; flags Vietnamese sync problems. https://github.com/bytedance/LatentSync : Apache-2.0, 512 px, 18 GB, 25 fps. https://github.com/TMElyralab/MuseTalk : MIT, 256 px face, jitter. https://github.com/Rudrabha/Wav2Lip : non-commercial.
- 9:16 [inference]: undocumented; a 512 px face patch softens a 720x1280 selfie mouth; 25 fps tools re-time 24 fps clips.

## 6. Video carrying audio
- [official] Omni guide: "any audio in a video reference is ignored"; references are at most 3 clips of 3 s; no audio uploads, no voice editing; an uploaded talking video cannot be extended with new dialogue; speech is supported when extending the model's own clip in the same session.
- [vendor] useapi changelog (2026-09-02): an Omni video-to-video edit of a Flow clip, prompted with the next line, returns the next take with the voice carried over.
- [official] Veo guide: Extend continues a voice only if it is present in the last second. [official] https://support.google.com/flow/answer/16352836 : Omni Extend is listed as coming soon.

## 7. Post-production
- [seo-blog] https://mrvocal.com/posts/loudness-for-shorts (2026-05-11): no platform publishes a target; -14 LUFS integrated, -1 dBTP survives TikTok, Reels and Shorts.
- [official] https://ffmpeg.org/ffmpeg-filters.html , chain [inference]: `highpass=f=80` and `afftdn` (or https://github.com/Rikorose/DeepFilterNet ) to strip each clip's own ambience; tone and level matched to one reference take with https://github.com/sergree/matchering (GPL-3.0) or `firequalizer`; one room-tone bed via `amix=normalize=0`; one shared impulse response via `afir`; `acrossfade=d=0.03` at joins; two-pass `loudnorm=I=-14:TP=-1:LRA=11`, then `alimiter`; 48 kHz throughout.

## 8. Consent and disclosure
- Vietnam [seo-blog] https://www.tilleke.com/insights/a-closer-look-at-vietnams-new-ai-law-what-it-means-for-ai-businesses/ (2026-01-09): AI Law 134/2025/QH15, in force 2026-03-01, requires marking AI audio and video and visible labels when a real person is imitated; decrees pending. Data law 91/2025/QH15 (2026-01-01): biometric data is sensitive, consent explicit and provable [search snippet].
- [official] https://artificialintelligenceact.eu/article/50/ : from 2026-08-02 deployers must disclose deepfakes; lighter for fiction.
- [official] https://support.google.com/youtube/answer/14328491 : disclose realistic synthetic content; cloning your own voice is exempt. [official] https://about.fb.com/news/2024/04/metas-approach-to-labeling-ai-generated-content-and-manipulated-media/ : self-disclosure, AI info label. TikTok requires labels on realistic AI audio [search snippet].
- Every cloning service above requires the speaker's recorded consent.

## 9. Measuring sameness
- [official] https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb , https://github.com/speechbrain/speechbrain/blob/develop/speechbrain/inference/speaker.py : ECAPA, 16 kHz mono, `verify_batch` default threshold 0.25.
- [official] https://huggingface.co/microsoft/wavlm-base-plus-sv : example threshold 0.86, dataset-dependent.
- [community-firsthand] https://github.com/keonlee9420/evaluate-zero-shot-tts : on WavLM-large, real same-speaker recordings score about 0.74, a strong clone 0.58.
- [inference] Calibrate on our own audio: same-take pairs as ceiling, two presets as floor, 3 s or more of denoised speech each. Pitch alone is not identity.

## Contradicts or explains our measurements
- Veo Lite/Fast taking one voice, Quality none, voice alone refused: matches the vendor table. [inference] The help page naming only Omni is stale.
- No audio upload: confirmed by the Omni guide and the forum.
- Drift across separate clips (242-281 Hz): the September thread describes the same; a voice reference narrowed ours (239, 250 Hz), nobody has shown it locks.
- Room-tone wording not helping: the filter is random and judges output. Nobody else reports a first+last-frame link; our 5 of 8 against 0 of 3 is new.
- Idea D: the vendor table says frames-only requests take no character.
- Skill section 2 credits the colon rule to Google; Google's guides use quotes.

## Open questions only a real test can answer
1. Does a voice change Veo Lite/Fast audio? Same image and line, two presets, two repeats, ECAPA cosine.
2. Idea C on Omni: master photo plus custom voice, three separate Vietnamese clips; cosine against the same-take ceiling.
3. Omni video-to-video edit of our best talking take with the next line: voice carried, face redrawn?
4. Frames plus @Character: one 4-credit probe.
5. Two characters, two voices, one Omni clip: who gets which?
6. Veo Extend with speech in the last second: cosine across the join.
7. Flow preset against the same-named Gemini TTS voice: same timbre?
8. Voice Changer and Seed-VC on our three drifted clips: cosine after, tones intact by transcription?

## Sources I could not reach
x.com post (HTTP 402); Flow changelog body (index page returned); Google Cloud Omni and Veo responsible-AI pages (navigation only); TikTok support (empty); cloud.google.com TTS pricing (truncated); marketplace.fptcloud.com (empty); thefutureintellect.com (DNS). Not searched before the cap: Reddit and YouTube Vietnamese tests, Zalo AI, US voice law, ElevenLabs consent page, HeyGen prices.
