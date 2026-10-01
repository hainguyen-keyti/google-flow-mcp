# Lane 4: GitHub and tooling (accessed 2026-10-01)

Research agent report, filed as returned (web and GitHub research only; no Flow access, no credits spent).

**Lane: GitHub and tooling, researched 2026-10-01.** Tags: [github] = repo, issue or source read through the API; [official] = Google page; [community] = third party; [local] = measured on this Mac. `gf#N` = github.com/ffroliva/gflow-cli/issues/N; `gh:x/y` = github.com/x/y; `hf:x/y` = huggingface.co/x/y. Untagged table rows are [github]; dates are release or last push.

## 1. gflow-cli, 0.78.0 to 0.81.0

Newest: v0.81.0, 2026-09-30, MIT, 246 stars [github gh:ffroliva/gflow-cli/releases].

| Version | Added since 0.78.0 |
|---|---|
| 0.79.0, 09-18 | `gflow data download <media_id>`, MCP `gflow_download_media`: re-fetch a billed clip, video only |
| 0.79.1, 09-22 | Signed-url download retried on ECONNRESET; a failed job keeps its media id |
| 0.80.0, 09-29 | `--resolution 360p|720p`; `nano2-lite`; refusals typed from the wire (`PUBLIC_ERROR_UNUSUAL_ACTIVITY` exit 10, content exit 5); login detects the `/about` re-check |
| 0.81.0, 09-30 | Generation browser opens off-screen (`GFLOW_CLI_BROWSER_WINDOW_POSITION`) |

None of our gaps closed. On flow.google.com at 0.81.0 [github, source and KNOWN_ISSUES.md at the tag]:

- Entities (`--reference-entity`, `@Name`): refused at `migrated_composer.py:433-469`. The host accepts them; gflow's observer times out on a null `MZZa6b` reply. A user patched 0.79.1 and got clips carrying the character's face and voice, 12 credits each [gf#639, 2026-09-27].
- Also unported: frames or references by media id, scenes, instructions, tools, credits, `character list`.
- Voices: no voice input on `gflow video`; custom voices unported (gf#826); whether a bound voice reaches the audio is unverified (gf#738) [2026-09-16].
- Open PRs: Extend, gf#882 (rpc `fZytfe`, 7 s, 2026-09-20); upscale and 1080p, gf#922 (`p0UkFb`, then `<media_id>_upsampled`, 2026-09-30); real Chrome over CDP, gf#907, since Playwright submits are refused while manual Chrome works (gf#906, 2026-09-27). Omni edit, video ingredients: no command.
- Picker url suffix, Chrome 154: no gflow issue; it binds tiles by display name and GETs the signed url directly (`migrated_composer.py:2266`, `2885-2935`). The crash is upstream, github.com/microsoft/playwright/issues/42506 (open, 2026-09-29): Chromium 153-154 dies when a persistent profile with download history receives `Browser.setDownloadBehavior`; emptying the `downloads*` tables in `Default/History` gave no crash in 20, 5 and 4 runs. Every report naming an OS is Windows.

## 2. Other Flow, Veo and Omni automation

| Project | Surface worth copying | Session, credits, caveats |
|---|---|---|
| gh:crisng95/flowkit (906 stars, 2026-09-30, MIT) | REST over projects, characters, scenes, requests; 30+ `/fk-*` skills; review-then-regenerate, capped per scene | Extension runs batchexecute in a signed-in tab; 5 concurrent, 10 s cooldown. Issue 67 (2026-09-27) reports two accounts blocked even in the manual UI |
| useapi.net Flow API v1, paid proxy [community useapi.net/docs/api-google-flow-v1] | `/videos` with `/extend`, `/upscale`, `/gif`, `/concatenate`; `/voices`, `/characters`, `/assets`, `/jobs`; `referenceAudio_1..5`, `referenceVideo_1` with a frame trim window, `character_1..7`, `seed`, `async` | Their servers hold the session and solve reCAPTCHA |
| gh:kodelyx/flow-agent (182 stars, 2026-09-25) | 5 MCP tools, OpenAI-compatible REST | Extension bridge, pooled accounts |
| gh:TheSmallHanCat/flow2api (2,990 stars, 2026-10-01, MIT) | Model names encode mode, length, orientation; extend; 1080p, 4K upsample | Token pool and proxies: not a model for us |
| gh:RohaanA/google-flow-mcp (2026-09-27, MIT) | `flow_update_character`, `flow_regenerate_portrait`, `flow_video_status`, `flow_agent_respond`, `flow_set_generation_defaults`, `flow_list_models` | Live quote, `max_credits`, `dry_run` |
| gh:miyakejima/google-flow-mcp (2026-09-29, MIT) | `flow_inspect_account` (live capability map), `flow_job_status`, `flow_download_job`, `flow_upscale_video` | Login bridge extension |
| gh:roshanarnav25-sloth/google-flow-mcp (2026-09-26, MIT) | `flow_estimate_cost`, `flow_budget`, `flow_collect`, `flow_delete_media`, `flow_open_app` | Its README admits no paid run |
| gh:ParkSangGwon/google-flow-mcp (2026-09-05, MIT) | `flow_scene_extend` (idempotent, `resume`), `flow_screenshot` | Measured 7.0 s per hop, Veo Lite only |
| gh:Comfy-Org/ComfyUI (2026-09-18, GPL-3.0); gh:n8n-io/n8n Gemini video actions | `Veo3FirstLastFrameNode`; `GeminiVideoOmniV2`: `task_type` with edit and extend, 14 images, 3 videos | Official API, paid per call |

## 3. Official routes

- Veo 3.1 [official ai.google.dev/gemini-api/docs/veo]: `google-genai`, `client.models.generate_videos()`; `veo-3.1-generate-preview`, `veo-3.1-fast-generate-preview`, `veo-3.1-lite-generate-preview`; `image`, `lastFrame`, `referenceImages` (3), `video` (extension to 148 s, 720p), `durationSeconds` 4, 6, 8 (8 with references, 1080p or 4k).
- Omni [official ai.google.dev/gemini-api/docs/omni]: Interactions API, `client.interactions.create()`, `gemini-omni-1.1-flash`; `response_format` (aspect, 360p to 4k); `video_config.task` = text_to_video, image_to_video, reference_to_video, edit, extend; extend by 10 s up to 40 s. Google's doc: "Uploading audio references is unsupported". Voice editing is unsupported and only English is evaluated. A first frame and reference images can be combined through role tags [official gh:google-gemini/gemini-skills, 2026-09-02].
- Price [official ai.google.dev/gemini-api/docs/pricing, 2026-10-01]: Veo 3.1 $0.40/s, Fast $0.10, Lite $0.05 at 720p; Omni about $0.10/s.
- MCP: Google's managed list has none for Veo, Gemini media or Flow [official docs.cloud.google.com/mcp/supported-products, 2026-09-30]. Google Cloud's open-source `mcp-genmedia` (README disclaims official support) has `veo_t2v`, `veo_i2v`, `veo_extend_video`, `veo_first_last_to_video`, `veo_reference_to_video`, `omni_video_generation`, `gemini_audio_tts`, `gemini_transcribe` and 11 ffmpeg tools [github gh:GoogleCloudPlatform/genmedia-creative-studio, mcp-v3.20.1, 2026-09-21, Apache-2.0].
- Flow API: none found; the help centre lists no API article [official support.google.com/flow]. A character with a voice exists only inside Flow.

## 4. Prompt libraries and skills

| Source | Rules encoded | Tests |
|---|---|---|
| gflow `skills/video-production` (2026-09-17) | Claims tagged CONSTRAINT, CALIBRATED, CONVENTION, UNEXPLORED; identity ladder; only an entity carries a voice; a costume change is a new entity | Yes: 19 scored tasks, `clip_qa.py --selftest` |
| `gemini-omni-flash-api` [official gh:google-gemini/gemini-skills] | Ask for one continuous shot; keep edit prompts short and name what must not change; `[0-3s]` beats; strip audio to regenerate it | None found |
| flowkit `/fk-review-video` | Six weighted dimensions, 14-error catalogue, a CRITICAL error caps the score | Reviewer proven on a clip with a planted defect (2026-09-20) |
| gh:louchi1984-coder/voxeasy (113 stars) | Timeline schema | `evals/` |
| gh:songguoxs/awesome-video-prompts (584 stars, 2025-10), gh:AtlasCloudAI/awesome-gemini-omni-prompts | Galleries | None |

## 5. QA building blocks (this Mac: M1, 16 GB [local])

| Check | Candidate, licence | Weight | Notes |
|---|---|---|---|
| Face identity | OpenCV Zoo YuNet + SFace (MIT, Apache-2.0) | 0.23 + 38.7 MB, CPU | Permissive |
| | InsightFace ArcFace (code MIT, models non-commercial) | onnxruntime 21.5 MB | 2026-09 |
| Voice similarity | SpeechBrain ECAPA (Apache-2.0) | torch, 128 MB wheel | 1.1.1, 2026-08 |
| Vietnamese ASR | faster-whisper (MIT), PhoWhisper (BSD-3) | CTranslate2 arm64 wheel; 244M to 1.55B params | PhoWhisper-large WER 4.67 VIVOS, 8.14 CMV-Vi; frozen since 2024-11 |
| Lip sync | gflow `clip_qa.py` (MIT); gh:joonson/syncnet_python (MIT); LatentSync `eval_sync_conf` | ffmpeg only; torch; GPU | First is calibrated: r 0.6-0.8 on singles, 0.1 on two-shots |
| Shot cuts | PySceneDetect 0.7.1 (BSD-3) | 0.1 MB plus OpenCV | 2026-07 |
| Sharpness, freeze, drift | ffmpeg `blurdetect`, `freezedetect`, `blackdetect`, `ssim`, `libvmaf`, `scdet` | Installed, 8.0.1 [local] | pyiqa, DOVER are non-commercial |
| Burned-in text | RapidOCR 3.9.2 (Apache-2.0); ocrmac, Apple Vision (MIT) | 27 MB; none | Local tesseract has only `eng` [local] |
| Duplicate objects | OWLv2, Grounding DINO (Apache-2.0) | torch | Ultralytics is AGPL-3.0 |
| Clothing | SigLIP2, DINOv2, Marqo-FashionSigLIP (Apache-2.0) | torch | VBench-2.0 `Human_Clothes`, `Human_Identity`, `Human_Anatomy` need CUDA and 7B models |

Repos: gh:opencv/opencv_zoo, gh:deepinsight/insightface, gh:speechbrain/speechbrain, gh:SYSTRAN/faster-whisper, gh:VinAIResearch/PhoWhisper, gh:Breakthrough/PySceneDetect, gh:RapidAI/RapidOCR, gh:straussmaximilian/ocrmac, gh:IDEA-Research/GroundingDINO, gh:facebookresearch/dinov2, gh:Vchitect/VBench, hf:google/owlv2-base-patch16-ensemble, hf:google/siglip2-base-patch16-224, hf:Marqo/marqo-fashionSigLIP. M1 speed is inferred from wheels, not run.

## 6. Lip-sync and Vietnamese voice

| Tool | Notes | Licence |
|---|---|---|
| gh:bytedance/LatentSync 1.6 (2025-06) | Mouth region, 512 px; 18 GB VRAM (1.5: 8 GB), not for this Mac | Code Apache-2.0, weights OpenRAIL++ |
| gh:TMElyralab/MuseTalk 1.5 | 256 px face, trained at 25 fps (ours is 24); 4 GB GPU, 8 s in about 5 min | MIT |
| gh:Rudrabha/Wav2Lip | Old open model | Research or personal use only |
| gh:MeiGen-AI/InfiniteTalk | Regenerates the whole frame on Wan2.1 14B | Apache-2.0 |
| gh:pnnbao97/VieNeu-TTS v3 Turbo (2026-09-23) | 25 voices, North, Central, South; clone from 3-8 s; 48 kHz; torch-free CPU, RTF about 0.5 on a Core i5 | Apache-2.0, code and weights |
| gh:k2-fsa/OmniVoice (2026-09-28) | 600+ languages with Vietnamese; cloning; `duration=` fits a line to a length; MPS | Code Apache-2.0; model card untagged |
| hf:hynt/F5-TTS-Vietnamese-ViVoice, hf:capleaf/viXTTS, hf:facebook/mms-tts-vie | Vietnamese models | Non-commercial or unclear |
| gh:resemble-ai/chatterbox V3 | 23 languages, no Vietnamese | MIT |

## 7. Editing automation

Google's `mcp-avtool-go` (trim, concat, overlay, loudness, GIF) is the closest agent-shaped reference. Others: gh:WyattBlue/auto-editor 31.6.0 (2026-09-06, Unlicense, macOS arm64 binary, cuts by audio or motion threshold); gh:Zulko/moviepy 2.2.1 (MIT); gh:lucemia/typed-ffmpeg 4.5 (MIT; ffmpeg-python is frozen since 2019); gh:mifi/editly (declarative JSON); gh:AcademySoftwareFoundation/OpenTimelineIO; gh:colour-science/colour 0.4.7 (BSD-3, LUT files); gh:hahnec/color-matcher (GPL-3.0). ffmpeg `lut3d`, `haldclut`, `curves`, `xfade`, `loudnorm` are installed [local].

## What a complete MCP surface would need, by evidence

1. Voice as an ingredient of a generation (useapi `referenceAudio_N`; our section 12 probe).
2. Video as an ingredient, and edits with a trim window and image references (useapi, ComfyUI, Google's skill).
3. Start frame together with characters or references (the official Omni API allows it; ours refuses).
4. Non-blocking submit, job status, collect (miyakejima, RohaanA, roshanarnav25, useapi; gf#741).
5. Live capability and model catalogue as a tool (miyakejima, RohaanA; gf#883).
6. Character update, portrait regeneration, a second body reference (RohaanA, useapi).
7. Voice library without a character: list, delete (useapi).
8. Media delete (roshanarnav25, useapi).
9. Agent control: approve or reject a quoted spend, sessions, instructions, defaults (RohaanA; gflow's six `gflow_instructions_*`).
10. Run a Flow Tool, not only list it (TMSSS05, roshanarnav25).
11. Image upscale 2K, 4K (gflow, useapi).
12. Run-level budget, `seed`, timeline trim, extend at a timeline position, screenshot or inspect.
13. Avatars and collections: in Flow's help centre, covered by nobody.

## Open questions only a real test can answer

1. Does a start frame plus `@Character` carry the character's voice (4 credits)? useapi says frames exclude characters; the official API mixes them.
2. Does a voice ingredient plus the master photo hold one voice across clips? Upstream never measured it (gf#738).
3. Is our macOS Chrome 154 crash playwright 42506, and does clearing download history stop it? That touches the profile: owner's call.
4. Does 0.81.0 keep our prices and flows?
5. On this plan, does Veo offer 1:1, 4:3, 3:4 and 4 or 6 s (useapi yes; gflow saw only 16:9, 9:16 on 2026-09-17)? Does Veo Quality take image ingredients (useapi, gflow no; our probe saw no refusal)?
6. Thresholds on generated material: SFace against ArcFace; ECAPA across clips of one Flow voice; `clip_qa.py` on 9:16 phone framing; PhoWhisper against Whisper large-v3 on short Vietnamese lines; OCR on garbled Vietnamese text.
7. Unusual-activity risk under our drivers: watch it, never provoke it.

## Sources I could not reach

- WebSearch: the session budget ran out after one query; discovery ran on `gh search` and direct fetches, so forums and non-GitHub projects are thin.
- Google Cloud's Veo 3.1 model card and API reference returned navigation only: Vertex parameter names unverified.
- ai.google.dev and useapi pages came through the fetch summariser, and `/veo`, `/omni` showed no page date: re-read before coding against them. Hugging Face read by API tag only; flow.google.com not opened.

Downloads: `/tmp/gflow_research` only. The harness saved one oversized tool output under `~/.claude/projects` by itself.
