# Lane 5: production method and client intake (accessed 2026-10-01)

Research agent report, filed as returned (web research only; no Flow access, no credits spent).

# Production method and client intake (2026-10-01)

Local files read first: ~/.claude/skills/flow-film-director/SKILL.md, docs/leelyly/playbook.md, ~/.claude/research/image-consistency-bible/dossier.md. Tags: the four requested, plus [official] (platform docs, regulators, law-firm notes). Checklists are paraphrased; exact wording is at each URL. n.d. = undated page, read 2026-10-01.

## 1. Pre-production documents (each must end in an image or an exact value [inference])
- Brief with locked deliverables (lengths, aspect ratios, formats). [film-standard S18]
- Script with a claims list and evidence. [community S2]
- Shot list per shot: shot type, camera movement, characters, environment reference, light, one 5-10 s action, prompt draft. [community S19]
- Style frames signed before generation: environment, product, character, logo placement, props. [studio-practice S1]
- Character sheet: five views at identical pose, light and scale, expressions, face close-ups; one sheet per costume. [film-standard S20] [community S21]
- Wardrobe: a look code per costume state; photos front, back, sides, details. [film-standard S22]
- Location bible: environment locked first; other angles derive from it. [community S23]
- Voice: Flow holds a voice through a single-speaker voice reference. [official S6]

## 2. Inputs required from the client
- Brief stage: objectives, messages, audience, legal copy, CTA, then confirmed logo files, product imagery, reference examples [studio-practice S1]; mandatories add talent releases, music licences, disclaimers. [film-standard S18]
- Person, photo-trained identity (Higgsfield): 20-80 recent photos, varied angles and expressions, even light, one full-height, no sunglasses or group shots. [official S15]
- Person, video twin: HeyGen, one uncut 2-minute take at 1080p+, no busy patterns, plus a consent clip by that person; Synthesia, three 2-3 minute takes, ideally 4K, and a consent script. [official S12, S13, S14]
- Person in Flow: a character needs at least one image; subject and product references sit on a plain or segmented background. [official S9, S6]
- Garment: one garment fully visible, front-facing or flat lay, nothing covering details, ideally 1024 px+ [official S5]; plus back, side, texture crop, colour reference, written non-negotiable details. [community S4]
- Product: front, three-quarter, side or rear if seen, in-hand for scale. [community S3]
- Voice: instant clone, 1-2 clean single-speaker minutes; professional clone, 30-180 minutes, own verified voice only; a Flow custom voice is a preset base plus a description. [official S16, S17, S6]
- Location photos: no published checklist found; kit item 8 is [inference].

## 3. Missing inputs
- Gaps are hunted on day one [studio-practice S1]; an open gap gets filled by the team's guess, and "assumptions are what cause reshoots" (The MPLS Egotist). [film-standard S18]
- Generated with approval: characters, environments, style frames, as stills signed before motion. [studio-practice S1]
- Never generated: a real person's likeness without a signed release; the product or its label. [community S2] Netflix requires clearance for generated main characters. [studio-practice S24, secondary report]
- Placeholders follow the temp-track convention: temporary, replaced before release. [film-standard S25]
- Records: generation log [community S2]; per-asset manifest with original file, parameters, reference file, human decision. [community S26]

## 4. Pipeline and gates
- Order: script, board, cast and product sheets, generation, edit, clearance, delivery. [community S2]
- Gate 1: style frames approved; later revisions are bounded by them. [studio-practice S1]
- Keyframes first: Google's guide builds start and end frames as images, then animates. [official S10]
- Cheap drafts: about 20 fast-tier variations, the final on the standard tier [community S27]; a broadcast spot needed 300-400 generations for 15 kept clips. [studio-practice S28]
- Gate 2: internal QC before the client sees a cut (legal copy, hands, faces, edges, text), then three cuts to final. [studio-practice S1]

## 5. Continuity
- Film logs per take: screen direction, eyeline, wardrobe detail, prop positions, photos of the frame and of each complete look. [film-standard S29]
- Grammar: 180-degree rule, 30-degree rule, eyeline match, match on action. [film-standard S30]
- AI adaptation: the reference sheet replaces the memory the model lacks; a new sheet per costume [community S21]; one approved reference set, no mixed variants [community S3]; for a reverse angle, decide what is behind the character first. [community S31]

## 6. QA
- Product: SKU and colour, logo once and in place, label text, scale across cuts, fingers around the object, no invented accessory. [community S3]
- Garment: colour, silhouette, seams, closures, prints, material cues; two blind reviewers, failures logged per criterion. [community S4]
- Method: pause on first, middle, last frame; watch at speed, then slowed; a hard fail (warped face, missing product feature) overrides any average. [community S32]
- Lip sync: lip closure on p, b, m; teeth wobble on head turns. [community S33]
- Metrics: ArcFace cosine 0.82 and delta-E under 2.0, one developer post [community S34]; loudness near -14 LUFS, -1 dBTP, undocumented by platforms. [community S35] The playbook's measures are stricter. [inference]

## 7. Editing craft
- Cuts every 1.5-3 s (TikTok), 2.5-4 s (Reels), 3-5 s (Shorts); cut on the beat or a frame early. [community S36]
- J and L cuts, cutaways over a talking head, match on action. [community S37] [film-standard S30]
- Light blur, fine grain, then a grade toward live-action references: raw output is over-sharp. [community S38]
- Keep the source frame rate; a mismatch drops or duplicates frames, worst on pans. [community S39]
- Speed ramps, room tone: nothing new found.

## 8. Legal and platform points to raise
- Likeness: New York Fashion Workers Act (2025-06-19): separate written consent with scope, purpose, pay, duration before a model's digital replica. [official S40] SAG-AFTRA commercials: consent on a reasonably specific description of use. [official S41]
- Voice: ELVIS Act protects voice (2024-07-01) [official S42]; Flow's avatar is the user's own likeness. [official S8]
- Labels on realistic AI video: YouTube [official S43], Meta [official S44], TikTok, which also reads Content Credentials [official S45]; EU AI Act Article 50 from 2026-08-02 [official S46]; New York synthetic performers in ads from 2026-06-09 [official S47]; Vietnam AI Law 134/2025/QH15 from 2026-03-01. [official S48]
- Watermark: every Flow output carries SynthID; a visible mark is automatic for residents of India, South Korea, Vietnam; Google bans passing output off as solely human-made. [official S7, S11]
- Minors: Flow is 18+ and protects minors and uploaded photos of people [official S7]; TikTok's ban on AI likenesses of under-18s is a search snippet only.
- Testimonials: FTC rule (2024-10-21): no blanket ban on avatars, but one posing as a real customer can be a fake testimonial. [official S49]
- Logos and labels: client files, placement fixed in style frames, never generated. [studio-practice S1] [community S2]

## Artefact A: input kit (M must-have, N nice-to-have)
1. M Brief: goal, platform, length, aspect, language, CTA. Else the format is guessed.
2. M Script or key messages, exact spoken lines. Else words and claims are invented.
3. M One master photo per look: full body, sharp, outfit complete. Every keyframe derives from it.
4. M Variant check of that look (bag, socks, jewellery, hair). Mixed variants caused most v6 errors.
5. M Back view, same session. Else the back is invented.
6. N Sides, three-quarter, face close-up. Else turns and close-ups drift.
7. M for product or garment films: front, back, detail crop, on-body or in-hand, one SKU. Else cut, label, scale are invented.
8. M Location master plate, ideally empty; a reverse angle if the camera turns. Else the room is rebuilt each shot.
9. M Voice decision: a Flow voice, one take for all lines, or silent. Else every clip sounds different.
10. M Consent on record for each real face and voice; nobody under 18. Else stop.
11. M Label and watermark decision; credit ceiling; approver for keyframes and final cut.
12. N Brand kit: logo files, hex colours, fonts. Else logos and text garble.
13. N Licensed music or none; reference videos for pace.

## Artefact B: protocol for a missing input [inference; no source publishes one]
1. Before any credit, check the kit against the shot list; a gap blocks when a planned shot needs a must-have.
2. Ask once, in one batch: what is missing, which shots need it, what the model would invent, the exact spec.
3. Options, recommended first: supply it; supply the nearest thing you have; drop or reframe the shots; only if you cannot supply it, approve a generated stand-in.
4. Never generate, whatever the answer: a real person's face or voice without consent on record, anyone under 18, a real product's label, logo or claims, script facts.
5. Generate only when the user cannot supply the item and has said yes, the item is fictional or derived from a supplied master, and it is approved as a still before any paid video.
6. Record per asset: id, class (supplied, derived, generated), source file, model, prompt, date, approver, shots using it. Generated items stay marked temporary until replaced, and the flag reaches the final report and the label decision.
7. Non-blocking gaps: park the question, continue on what is clear, never fill by judgment.

Open question: the publishing markets decide which label law applies.

## Sources I could not reach
TikTok AIGC support and Community Guidelines pages (JavaScript shells); Netflix partner page (empty redirect, CineD used); SitePoint QA article, SAG-AFTRA bulletin PDF, Lexology consent article, media-village brief guide, vpglossary clean plate (403); Google Cloud reference-images page (navigation only).

## Sources
S1 https://www.lemonlight.com/blog/ai-video-production-workflow/ 2026-08-27
S2 https://www.screenweaver.ai/blog/produce-ad-with-ai-workflow 2026-09-21
S3 https://www.rasgo.ai/blog/stop-ai-ugc-changing-product 2026-08-26
S4 https://uselamina.ai/blog/ai-virtual-try-on-vs-apparel-photoshoots-lamina-and-photoroom 2026-08-23
S5 https://support.google.com/merchants/answer/14096369?hl=en n.d.
S6 https://support.google.com/flow/answer/16353334?hl=en n.d.
S7 https://support.google.com/flow/answer/16353333?hl=en n.d.
S8 https://support.google.com/flow/answer/17102997?hl=en n.d.
S9 https://support.google.com/flow/answer/16935308?hl=en n.d.
S10 https://cloud.google.com/blog/products/ai-machine-learning/ultimate-prompting-guide-for-veo-3-1 2025-10-16
S11 https://policies.google.com/terms/generative-ai/use-policy 2024-12-17
S12 https://help.heygen.com/en/articles/8389138-digital-twin-video-avatar-filming-tips n.d.
S13 https://help.heygen.com/en/articles/12092609-recording-your-consent-video n.d.
S14 https://docs.synthesia.io/docs/studio-avatars n.d.
S15 https://higgsfield.ai/creator-hub/help-center/ai-models/how-do-i-create-and-use-a-soul-id-character n.d.
S16 https://elevenlabs.io/docs/eleven-creative/voices/voice-cloning/instant-voice-cloning n.d.
S17 https://elevenlabs.io/docs/eleven-creative/voices/voice-cloning n.d.
S18 https://www.themplsegotist.com/how-to-create-a-video-production-brief-that-prevents-reshoots/ 2026-08-08
S19 https://www.mindstudio.ai/blog/storyboards-character-sheets-ai-video-generation 2026-05-19
S20 https://www.dreampixelforge.com/blog/character-turnaround 2026-07-21
S21 https://tmff.net/character-consistency-in-ai-filmmaking-why-it-breaks-and-what-fixes-it/ 2026-07-23
S22 https://blockreeldao.com/blog/costume-breakdowns-building-character-arcs-with-wardrobe-continuity 2026-05-19
S23 https://nat.io/blog/consistent-environment-generation-guide 2026-02-11
S24 https://www.cined.com/netflix-publishes-generative-ai-guidelines-for-content-production/ 2025-08-26
S25 https://en.wikipedia.org/wiki/Temp_track n.d.
S26 https://dev.to/hirodeath/recording-generated-asset-provenance-originals-and-selection-decisions-3p0a 2026-09-30
S27 https://www.mindstudio.ai/blog/what-is-google-veo-3-1-fast-video 2026-02-13
S28 https://www.yahoo.com/entertainment/articles/chaotic-kalshi-ad-during-nba-173937071.html 2025-06-13
S29 https://storyflow.so/blog/what-is-continuity-in-film 2026-07-29
S30 https://www.studiobinder.com/blog/what-is-continuity-editing-in-film/ 2021-03-21
S31 https://invideo.io/faq/how-do-you-generate-reverse-angles-and-coverage-shots/ 2026-07-27
S32 https://apiframe.ai/blog/how-to-test-quality-of-ai-generated-video 2026-08-25
S33 https://opencreator.io/blog/ai-lip-sync-workflow 2026-02-05
S34 https://dev.to/biffer_rowley_4cdbf203087/automated-multimodal-vision-audits-grading-character-consistency-frame-by-frame-12l3 09-30 (year not shown)
S35 https://www.criticallisteninglab.com/en/learn/loudness/social-media n.d.
S36 https://shortzly.com/blog/short-form-video-pacing-editing-guide 2026-08-08
S37 https://captions.ai/blog/six-common-types-of-cuts-in-film 2026-03-30
S38 https://invideo.io/blog/ai-video-post-production/ 2026-07-15
S39 https://www.frankschrader.us/video-frame-rate-explained-different-frame-rate-on-timeline/ 2018-12-03
S40 https://www.beneschlaw.com/resources/seeing-double-new-york-fashion-workers-act-creates-new-consent-requirements-for-use-of-generative-ai-tools-to-create-models-digital-replicas.html 2025-07-10
S41 https://www.dglaw.com/importance-of-digital-replica-consents-under-the-sag-aftra-commercials-contract/ 2026-01-20
S42 https://law.vanderbilt.edu/why-tennessees-elvis-act-is-the-king-of-artificial-intelligence-protections/ 2025-03-24
S43 https://support.google.com/youtube/answer/14328491 n.d.
S44 https://about.fb.com/news/2024/02/labeling-ai-generated-images-on-facebook-instagram-and-threads/ 2024-02-06
S45 https://newsroom.tiktok.com/en-us/partnering-with-our-industry-to-advance-ai-transparency-and-literacy 2024-05-09
S46 https://artificialintelligenceact.eu/transparency-rules-article-50/ 2026-05-14
S47 https://www.mcdermottlaw.com/insights/new-yorks-synthetic-performer-disclosure-law-what-advertisers-need-to-know/ 2026-06-09
S48 https://blogs.duanemorris.com/vietnam/2026/03/03/vietnam-the-first-law-on-artificial-intelligence-what-you-must-know/ 2026-03-03
S49 https://www.ftc.gov/business-guidance/resources/consumer-reviews-testimonials-rule-questions-answers n.d.
