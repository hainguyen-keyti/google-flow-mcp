# Character + Room Visual Design System — v1.0

## Purpose
This is a reusable **visual continuity bible** for a fictional adult woman and her bedroom. Give this document to another image/video/design agent as persistent context.

**Core principle:** lock identity and environment; vary only camera, pose, outfit and scene conditions.

---

## 1. Non-negotiable identity locks

1. Face identity and facial landmarks
2. Long dark hair: length, density and silhouette
3. Natural skin texture
4. Cheek dimple
5. Established beauty marks
6. Chest tattoo below necklace
7. Upper-arm tattoo zone
8. Large upper/mid-back tattoo composition
9. Delicate necklace + small pendant
10. Slender natural adult proportions

### Character
- **Type:** fictional adult woman
- **Visual direction:** Vietnamese/East Asian-looking, realistic rather than model-perfect
- **Face:** soft oval/tapered face, defined but delicate structure, large dark almond eyes, natural lower-eye fullness, small straight-to-soft nose, softly defined lips, subtle asymmetry.
- **Expression:** calm, slightly shy, natural; avoid permanent exaggerated smile.
- **Skin:** light warm-neutral tone, visible pores and fine texture, subtle tonal variation and tiny imperfections; never airbrushed.
- **Hair:** very dark brown/near-black, thick, long, reaching well below shoulders toward mid-back/waist, mostly straight with soft bends/waves and flyaways, soft center-to-slight-side part, long face-framing strands.

---

## 2. Tattoo system

### Chest
- Center-upper chest.
- Directly below the necklace.
- Butterfly-like central motif with fine-line ornamental/organic detailing.
- Clearly visible; not a tiny minimalist mark.

### Upper arm
- Upper arm/shoulder region.
- Fine-line botanical/organic illustrative style.

### Back
- Large composition across upper/mid back.
- Fine-line ornamental/botanical language.
- Must remain recognizable in rear views.

> **Important:** the generated references contain some frame-to-frame variation in exact tattoo linework. Placement zones and motif family are locked for now. Exact linework should be frozen later with a dedicated tattoo reference sheet.

---

## 3. Jewelry & accessories

- Thin delicate silver-toned necklace.
- Small pendant.
- Pendant sits above the chest tattoo.
- Other jewelry remains minimal and understated unless explicitly changed.

---

## 4. Wardrobe system

### Aesthetic
Romantic feminine bedroom wardrobe with lace, soft textures and vintage-inspired details.

### Palette
Ivory, cream, white, dusty pink, black, soft gray, muted mauve.

### Categories
- Lace-trimmed tops
- Camisoles
- Feminine dresses
- Short skirts
- Soft knit layers
- Casual tops
- Knee-high/soft socks
- Light and dark contrasting outfits

### Materials
Lace, cotton, soft knit and lightweight woven fabrics.

**Rule:** garments must have believable seams, folds, stretch and fabric texture.

---

# 5. Room Bible

## Overall mood
Cozy, intimate, lived-in, creative and slightly nostalgic.

## Architecture
- Light neutral walls
- Warm medium wood / wood-look floor
- Large window with city lights visible in night scenes
- Dark brown/wood bedroom door
- Wall-mounted AC

## Major layout anchors
1. Large bed
2. Dark desk/workstation
3. Dark task/office chair
4. Open wardrobe
5. Full-length mirror
6. Drawers/shelving
7. Dense photo wall

## Decor
- Significant wall area covered by small personal photos/postcards.
- Shelves with bags, keepsakes and small decorative objects.
- Warm practical lamps.
- Pale blue/white patterned bedding.
- Small plush/decorative objects around the bed.
- Laptop/monitor and personal desk items.

## Wardrobe
Open and densely filled rather than minimalist:
- Hanging clothes: white, cream, pink, black, gray, muted mauve.
- Folded clothing in drawers.
- Shoes and handbags on shelves.
- Should look like a real personal wardrobe, not a retail showroom.

### Recurring props
Full-length mirror, photo wall, dark desk, dark chair, laptop/monitor, warm lamps, open wardrobe, shoes, handbags, pale patterned bedding, small decorative/plush items.

---

# 6. Photography system

## Default look
Photorealistic casual lifestyle photography with a high-end smartphone feel.

### Focal-length language
- **24–28mm equivalent:** wide room/environment shots
- **35–50mm equivalent:** environmental portraits
- **Phone selfie perspective:** mirror and handheld selfies

### Required angle library
- Full-body mirror selfie
- Wide shot from doorway
- Wide shot from room corner
- Rear three-quarter
- Direct rear
- Low-angle floor-level
- High-angle/downward
- Side profile
- Medium environmental portrait
- Face close-up

### Lighting
Warm practical lamps + soft ambient room light.

For night scenes, mix warm interior light with cooler city/window light.

### Avoid
Hard studio flash, beauty-filter glow, perfect commercial lighting, excessive cinematic fog, excessive bokeh and glossy influencer filters.

---

# 7. Continuity rules for another AI agent

Use two immutable contexts:

### CHARACTER_LOCK
Preserve:
- face
- eyes/nose/lips
- dimple
- beauty marks
- skin texture
- hair length/density
- tattoos
- necklace
- body proportions

### ROOM_LOCK
Preserve:
- bed
- desk
- chair
- wardrobe
- mirror
- photo wall
- door
- window
- AC
- major props
- approximate spatial relationships

### Scene variables that may change
- Camera position
- Focal length
- Pose
- Expression
- Outfit
- Time of day
- Lighting intensity

**Never redesign the character or room just because the camera angle changes.**

---

# 8. Negative prompt / anti-drift rules

Do NOT:
- make skin porcelain-smooth
- change face shape
- shorten the hair
- remove tattoo zones
- move the chest tattoo above the necklace
- create doll-like proportions
- exaggerate eyes or facial features
- turn the room into a luxury hotel or minimalist showroom
- randomly rearrange furniture
- create impossible mirror reflections
- warp room geometry
- use excessive beauty filters
- introduce unrelated tattoos or jewelry
- change the wardrobe aesthetic without instruction

---

# 9. Recommended master-reference package

The current collage is a strong **style/reference board**, but it is not sufficient by itself to lock every microscopic detail because generated frames contain small inconsistencies.

For maximum cross-agent consistency, create these assets next:

1. **Character turnaround:** front / 3⁄4 / side / rear
2. **Face sheet:** eyes, nose, lips, dimple, beauty marks, pores
3. **Tattoo sheet:** chest, arm and back exact linework
4. **Hair turnaround**
5. **Jewelry/accessory sheet**
6. **Room floor plan / top-down layout**
7. **Room four-corner reference**
8. **Wardrobe catalog**
9. **Lighting reference**

Once those are created, the package becomes much closer to a true **production character bible** rather than a collection of prompts.

---

# 10. Agent-ready master instruction

> Treat the Character Bible and Room Bible as immutable visual identity context. Reproduce the same fictional adult woman and the same bedroom across all shots. Preserve facial landmarks, natural skin texture, cheek dimple, beauty marks, hair length/density, tattoo placement and motif family, necklace and body proportions. Preserve the room's bed, desk, chair, wardrobe, full-length mirror, photo wall, door, window, AC and major props. Only change explicitly requested scene variables such as camera position, focal length, pose, outfit, expression, time and lighting. Prioritize physical plausibility, realistic skin/fabric texture, stable anatomy and stable spatial relationships over visual novelty.

