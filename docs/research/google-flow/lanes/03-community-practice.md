# Lane 3: what practitioners do with Flow in 2026 (accessed 2026-10-01)

Research agent report, filed as returned (web research only; no Flow access, no credits spent).

# Lane report: Flow practitioners in 2026 (researched 2026-10-01)

Tags: F = [community-firsthand], T = [community-tutorial], B = [seo-blog], G = official page (cross-check). s = evidence shown, a = asserted only. Keys map to URL and date under Sources.

## 1. Character consistency
- Tutorial consensus: build the person with Nano Banana (0 credits), derive a multi-angle sheet from that same photo, register a Character (two images at most), attach a voice, run Omni Ingredients with character plus sheet (Y1, Y2, Y3, C8: T, s).
- Breaks: Characters and Ingredients redraw the face. A Character profile gave a generic person in 20-30 video-to-video tries (D1: F, a). Shoes, hair, outfit change; characters vanish from the library (D2: F, a). An avatar drifted in hair, glasses and room; calm shots held (C1: F, s).
- Frames-first exists: one finished still as start frame (Y4: T, s), or as start and end of every clip (U1: T, s).

## 2. Outfit and product fidelity
- Four-panel orthographic product sheets, one named camera per panel; keep hands, captions and logos off sheets, since sheet content leaks into clips (U1: T, s).
- Small details hold only when clearly visible in the first frame (C5: F, s).
- A Google forum responder: Veo 3.1 Fast distorts fine ornamental patterns, standard 3.1 holds them better (D8: F, a).
- Eight-task test: Omni's bottle changed proportions and colour as the camera moved; Veo Fast held better (B1: B, s).

## 3. Location
- Either the environment stays frozen, or once its elements animate the whole room is reinterpreted (D6: F, a). Room changed by the third shot (C3: F, s).
- Works: one keyframe still holding the whole scene, reused for every clip (U1: T, s); Flow Tools' Shot Explorer for alternate angles from one image, and saving a frame of a good clip as the next anchor (C4: F, s).

## 4. Voice
- A custom voice is a preset plus a sample line and a style note (120 characters each); no audio upload (C8: T, s; U3: T, a).
- Regression: before Omni 1.1 the Flow agent kept one voice across clips from one avatar image; now each clip gets a new voice, with no fix from staff (D3: F, a).
- Workarounds: last frame of the previous clip as start frame (mostly works); an @voice tag found by asking the agent which prompt it wrote (D3 reply: F, a). One voice description pasted into every prompt kept three Frames clips close enough to pass as one take (U1: T, s).
- Hard limit: frames cannot be combined with characters or voice references. Omni Ingredients take up to 7 images and 5 voices, Veo Lite and Fast one voice, Quality none (U2, U4: T, a).
- External voice: one TTS narrator replacing every clip's soundtrack, after the model gave a male creator a female voice (H3: F, a); TTS plus a lip-sync model, 0.60 USD against 2.80 burned on re-prompts (H2: F, s).

## 5. Transitions and long videos
- Flow extends only Veo 3.1 8 s clips, only with Lite; Omni extend is listed as coming soon (G1; Y2: T, s).
- The same still as start and end lets clips join in any order. Finish the action by second 8, hold 2 s, trim to the sound; otherwise Omni runs the line long and jumps to the end frame (U1: T, s).

## 6. Phone-style realism
- Recreate a real TikTok frame with the product swapped in, then Omni Frames to Video, 9:16, 10 s, 15 credits (Y4: T, s). Five real photos as inputs read as filmed footage (C2: F, s).
- Tells: a clean studio voice with no room in it (D3: F, a); laughing shots, unrequested glasses and sci-fi overlays (C1: F, s).

## 7. Filters
- Veo image-to-video trips the audio filter non-deterministically and passes within 3-5 identical retries (H1: F, a). Three straight failures with reference images, while the same character framed as background figures passed first try; audio cannot be switched off (D5: F, a).
- 2026-09-29, Google staff on three failed start-frame jobs (Lite and Fast, 4 s): "a false-positive audio filter triggered specifically by the ambient room tone instruction". Removing that line let Lite succeed; filtered jobs are not charged (D4: F, s).
- Prominent-people false positives blocked fictional characters for weeks from 2026-06-30 (D7: F, a).
- Codes seen: UNSAFE_GENERATION, PROMINENT_PEOPLE_FILTER_FAILED, AUDIO_FILTERED, MINOR, SEXUAL, DANGER_FILTER, IP_INPUT_IMAGE; an image flagged IP_PROHIBITED never passes on retry (U2: T, a).
- Legitimate changes: drop the audio line, retry unchanged, change the composition, replace a flagged reference.

## 8. Non-English speech
- No Vietnamese first-hand report found. A 2025 Vietnamese explainer: English prompt, ask for Vietnamese, short lines, one speaker (B5: B, a).
- Romanian, five generations checked with WhisperX: 3 of 4 prompt variants made pronunciation worse; accent follows the reference photo's look (second-hand Reddit) (H2: F, s).
- Mandarin, about 20 prompts: under 30% success against 70% in English (C6: F, a).

## 9. Multi-character dialogue
- Veo mixes up who speaks when descriptions are similar: tie each line to a visible trait, colon form, short lines (C7: T, s). Lip sync about 40% with several speakers against 80% with one (C6: F, a).
- Only Omni Ingredients carries several voices and characters in one request (U2: T, a).

## 10. Credit economy
- Images, character creation, 1080p upscale and concatenation cost 0; a 29 s three-clip ad cost 45 credits (U1: T, s).
- Omni 360p costs about half and upscales free to 720p or 1080p (U5: T, a); draft 3-4 variants at 360p (B2: B, a).

## 11. Agent mode
- Used for storyboards and shot lists; scenes are then generated by hand (C1: F, s). Queries are free; confirmation before spending is on by default (G2).
- It does not fix consistency: characters changed under it (D2: F, a); it claimed to have saved a storyboard that did not exist (C3: F, s); it stopped carrying the voice after Omni 1.1 (D3: F, a).

## Contradicts or explains our measurements
- Our safest audio line is the kind Google staff named as the trigger (D4). Start-frame-only jobs fail too (H1, D4); nobody ties the filter to first-plus-last frames, so our 5 of 8 against 0 of 3 needs an A/B.
- Frames carry no locked voice: confirmed (U1, U2); a pasted voice description only gets close, like our 242-281 Hz. Idea D (Frames plus @Character) is likely refused; idea C (image plus voice in Ingredients) is supported.
- Omni softer than Veo: B1 rates Veo Fast higher on visual quality (4.54 vs 4.31) and temporal consistency (4.24 vs 3.99).
- Lace: D8 says Fast itself degrades fine patterns, supporting the deferred Quality A/B.
- ~/.claude/skills/flow-film-director/SKILL.md section 7 says Omni extends to 40 s; G1 and Y2 say Flow extends only Veo, through Lite.
- G1 and U2: Veo Quality has no Ingredients; our picker probe saw no refusal, so expect it at submit.
  [Erratum, 2026-10-01 evening: that probe read the wrong marker. Read correctly, the composer itself refuses image,
  character and voice ingredients on Quality; see `../test-results.md` section 7.]

## Open questions only a real test can answer
1. Audio line A/B on Veo Lite, same first-plus-last pair: 5 jobs without an audio line, 5 with our room-tone line (refusals cost 0).
2. Idea C on Omni 360p: master photo plus one custom voice, twice: distance to the master against a Frames clip, and F0 gap.
3. Idea D: Frames plus a voiced @Character: refused, or accepted and voiced?
4. Re-voice a finished Frames clip (video ingredient plus voice ingredient): does the picture survive and the voice lock?
5. A custom voice built from a Vietnamese sample line: native listener plus ASR check.
6. Same still as start and end on two Omni talking clips: join distance and F0 gap.
7. Omni 360p plus free upscale against native 720p for the talking take.
8. Two voiced characters in one Omni clip: wrong-speaker rate.
9. Zero-credit: is Extend offered on an Omni clip; what prompt does the agent write?

## Sources I could not reach
- Reddit: blocked for this tool (search and fetch); nothing read, only second-hand through H2.
- X: HTTP 402.
- YouTube: no transcripts; descriptions and chapters through a reader proxy.
- Medium (socket closed), stork.ai and glasp (403).
- Web search budget ran out mid-run: room plates, multi-speaker and tonal-language searches never ran.

## Sources
Undated pages were read 2026-10-01.
- D1 https://discuss.ai.google.dev/t/major-identity-drift-and-character-consistency-failure-in-video-to-video-generation/173742 (2026-07-06)
- D2 https://discuss.ai.google.dev/t/veo-flow-generation-issues-lost-credits-consistency-problems-and-excessive-failed-generations/147374 (2026-05-22 to 08-17)
- D3 https://discuss.ai.google.dev/t/critical-regression-gemini-omni-1-1-flash-update-destroyed-conversational-voice-continuity-for-ugc-creators/182433 (2026-09-11)
- D4 https://discuss.ai.google.dev/t/repeated-image-to-video-audio-failures-on-veo-3-1-lite-and-fast/184792 (2026-09-25, staff 09-29)
- D5 https://discuss.ai.google.dev/t/veo-3-1-audio-safety-false-positives-are-burning-a-huge-share-of-a-tight-tier-1-daily-quota/175949 (2026-07-24)
- D6 https://discuss.ai.google.dev/t/flow-veo-unable-to-selectively-animate-reference-environment-while-preserving-scene-fidelity/177036 (2026-08-03)
- D7 https://discuss.ai.google.dev/t/critical-bug-google-flow-prominent-people-safety-filter-broken-systematically-blocking-all-fictional-character-generations/173121 (2026-06-30 to 08-20)
- D8 https://discuss.ai.google.dev/t/query-differences-in-details-of-decoration-between-veo-3-1-generate-preview-and-veo-3-1-fast-preview/108272 (2025-11-20)
- H1 https://github.com/googleapis/js-genai/issues/1272 (2026-01-23)
- H2 https://github.com/ulmeanuadrian/kie-mcp/issues/1 (2026-09-22)
- H3 https://github.com/montasirali209/inx-social/pull/245 (2026-09-23)
- U1 https://useapi.net/docs/articles/google-flow-ugc-product-video (2026-09-04)
- U2 https://useapi.net/docs/api-google-flow-v1/post-google-flow-videos
- U3 https://useapi.net/docs/api-google-flow-v1/post-google-flow-voices
- U4 https://useapi.net/docs/articles/omni-flash-bash (updated 2026-09-11)
- U5 https://useapi.net/docs/api-google-flow-v1/post-google-flow-videos-upscale
- Y1 Nova Autonomous, Google Flow: How To Make Stunning Videos Start To Finish, https://www.youtube.com/watch?v=nfoB4SxCseE (2026-08-30)
- Y2 AI Video Studio, Google Flow Tutorial: Google Omni 1.1 Characters & Voice Tested, https://www.youtube.com/watch?v=yRM1df108G0 (2026-09-15)
- Y3 Maamria AI, How to Lock Consistent Characters, Voices & Avatars Using Google Flow, https://www.youtube.com/watch?v=-FcU8fwMLhg (2026-05-30)
- Y4 BigWiz Media, Gemini Omni Just Changed AI UGC Ads, https://www.youtube.com/watch?v=RWU9xs2-X0w (2026-07-14)
- C1 https://www.chatprd.ai/how-i-ai/ai-avatar-video-in-15-minutes-with-google-omni-flow (2026-06-03)
- C2 https://aiblewmymind.substack.com/p/gemini-omni-video-tutorial (2026-05-28)
- C3 https://carlaeng.substack.com/p/google-flow-ai-animation-pipeline (2026-06-02)
- C4 https://carlaeng.substack.com/p/personalize-your-ai-animation-tools (2026-07-03)
- C5 https://theaifilmmaker.substack.com/p/testing-the-limits-of-ai-video-with (2025-10-18)
- C6 https://eastondev.com/blog/en/posts/ai/20251207-veo3-audio-generation-guide/ (2025-12-07)
- C7 https://replicate.com/blog/using-and-prompting-veo-3 (2025-06-10)
- C8 https://kartaca.com/en/mastering-google-flow-the-ultimate-guide-to-character-avatar-creation/ (2026-06-12)
- B1 https://www.glbgpt.com/hub/gemini-omni-flash-vs-veo-3-1-hands-on-video-test-and-benchmark-comparison/ (2026-07-16)
- B2 https://promptslove.com/blog/gemini-omni-1-1-flash-review/ (2026-08-28)
- B5 https://vietnamnet.vn/6-loi-thuong-gap-khi-lam-video-veo-3-va-cach-xu-ly-2411434.html (2025-06-15)
- G1 https://support.google.com/labs/answer/16352836?hl=en
- G2 https://support.google.com/flow/answer/17093911?hl=en
