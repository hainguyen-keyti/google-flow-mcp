# Làm video Lily bằng Google Flow: bài học và cách làm

Viết 2026-10-01, sau bảy lần làm lại (v1 tới v7.1). Mọi con số trong bài đều đo trên clip thật của project
`102445f4` ("LeeLyLy fashion review"), không phải ước lượng. Phim tham chiếu: `out/lly_v7/lily_v7_1_toi_nay_mac_gi.mp4`
(18,9 s, 1080x1920, 24 khung/giây).

## 1. Tóm tắt: bảy điều quyết định

1. **Đồng nhất đến từ ẢNH đầu vào, không đến từ chữ.** Model không nhớ gì giữa hai clip. Muốn cùng một người, một bộ
   đồ, một căn phòng thì mọi clip phải xuất phát từ cùng một ảnh.
2. **Chỉ dùng MỘT ảnh chuẩn.** Mọi ảnh khung khác đều cắt ra hoặc chỉnh từ ảnh đó, và phải đặt cạnh ảnh chuẩn để soi
   trước khi tốn credit.
3. **Cái gì không có trong ảnh đầu vào thì model sẽ bịa.** Lưng váy, khuôn mặt, mảng tường bị che: phải đưa vào ảnh
   khung, hoặc cắt bỏ đoạn model bịa.
4. **Một clip, một việc.** Một động tác, một chuyển động máy. Nhồi hai ba việc thì model bỏ bớt.
5. **Chọn model theo việc.** Veo 3.1 Fast cho cảnh hình (nét gấp đôi, đáp đúng khung cuối), Omni cho cảnh nói (nói
   tiếng Việt khớp miệng, độ dài tuỳ chọn, rẻ).
6. **Giọng thật đến từ câu thoại viết như nói chuyện.** Mỗi clip 6 s một câu ngắn nói với người xem, không gắn giọng
   (chủ repo nghe và chấm 8 clip ngày 2026-10-02, xem mục 11). Cách cũ vẫn dùng được khi cần đúng một lần quay: cả ba
   câu thoại nằm trong một clip 10 s, rồi cắt ra dùng.
7. **Phim "như người làm" nằm ở khâu dựng.** Cắt 2-3 s một lần, cắt đúng lúc đang chuyển động, lời nói chạy đè lên
   cảnh khác, màu các cảnh cân về một ảnh, xuất đúng 24 khung/giây.

## 2. Vì sao video AI hay "lệch": hiểu vấn đề

| Đặc tính của model | Nó gây ra lỗi gì ở phim mình |
|---|---|
| Mỗi clip là một lần sinh độc lập, không có trí nhớ | Mỗi cảnh một kiểu váy, một kiểu phòng, một giọng nói |
| Chữ mơ hồ hơn ảnh rất nhiều ("váy ren đen" có cả nghìn kiểu) | Tả lại nhân vật bằng chữ là mỗi lần ra một người hơi khác |
| Phần không có trong ảnh đầu vào thì tự bịa | Khung đầu chỉ có giày: mặt bị bịa suốt 3 s. Cúi người xuống: lộ ra một cái gương có đèn mà phòng không có. Xoay lưng không có ảnh lưng: váy thành kiểu khoét lưng khác |
| Chữ tả sai thì hình sai đúng như chữ | Tôi viết "tất ren" khi tạo ảnh khung, trong khi ảnh chuẩn là tất mỏng trơn: tất đổi kiểu giữa cảnh |
| Nội suy giữa khung đầu và khung cuối không hiểu vật thể | Cái túi ở mép phải khung đầu và mép trái khung cuối bị vẽ thành HAI cái túi lúc xoay người |
| Model chậm dần ở 10-15% cuối clip | Cắt sang cảnh sau là bị khựng |
| Mỗi model có "chất" riêng | Omni mềm hơn và ép vùng tối sâu hơn Veo, nên cắt qua lại là lệch tông |
| Nối khung do máy sinh ra nhiều lần thì chi tiết mòn dần | Sau 3-4 lần nối, phòng và váy trôi hẳn (bản v4) |

## 3. Từng bản đã hỏng ở đâu

| Bản | Bạn thấy gì | Nguyên nhân thật | Cách đã sửa |
|---|---|---|---|
| Phim 1, 2 | Rời rạc, không thành chuyện | Mỗi cảnh sinh riêng từ chữ, không có kịch bản | Viết chuyện và danh sách cảnh trước khi sinh |
| "Một ngày của Lily" | Liền mạch nhưng thiếu hành động | Một clip nhồi nhiều việc | Một clip một việc |
| v4 | Phòng trôi, váy đổi chiều dài, giọng mỗi clip một kiểu | Nối khung cuối của clip trước làm khung đầu clip sau quá nhiều lần; mỗi clip tự tạo giọng | Neo mỗi cảnh vào ảnh gốc; nghĩ lại chuyện giọng |
| v5 | Mặt ở cảnh nói khác mặt ở cảnh câm | Cảnh nói dùng chế độ Ingredients (Flow vẽ lại mặt từ nhân vật), cảnh câm dùng ảnh gốc | Cả phim dùng một chế độ: Frames từ ảnh gốc |
| v5b | Giọng ba câu hơi lệch (đo 242, 262, 281 Hz) | Ba clip là ba lần sinh, ba giọng | Quay cả ba câu trong một clip |
| v6 | Chuyển cảnh chưa mượt, nhiều lỗi hình | Tám ảnh gốc "cùng bộ" thật ra là ít nhất BỐN biến thể (có túi hoặc không, ba kiểu tất, thân váy đục hoặc xuyên thấu, có nơ hoặc không), mỗi cảnh lấy một ảnh ở một vị trí máy khác. Tôi còn xuất 30 khung/giây từ nguồn 24, tự làm hình khựng | Một ảnh chuẩn duy nhất; xuất 24 khung/giây |
| v7 | Khá tốt; lệch màu nhẹ khi chuyển cảnh; hai cái túi lúc xoay | Omni ép vùng tối sâu hơn Veo (cánh cửa đo 19 so với 26). Túi bị nhân đôi do nội suy hai khung | Cân đường cong màu từng clip; quay lại cảnh xoay chỉ với khung đầu |

Bài học về cách làm việc của chính tôi: lỗi lớn nhất (bốn biến thể trang phục) nằm ngay trong ảnh nguồn mà tôi đã
không soi kỹ. Từ v7, mọi thứ đều được đặt cạnh ảnh chuẩn và đo trước khi tin.

## 4. Cách giữ đồng nhất

**Trước khi sinh bất cứ thứ gì**

1. Chọn một ảnh chuẩn và phóng to soi từng món: váy (tay, thân, số tầng bèo, dây nơ), túi (bên nào, dây gì), tất, giày,
   vòng cổ, hình xăm, đồ vật trong phòng. Ghi lại thành một danh sách. Ảnh chuẩn của v7:
   `Character/LeeLyLy/images/indoor_bedroom_closet_scene_in_a_moody_slightly_d_15_batch_5.png`.
2. Mọi ảnh khung đều sinh từ ảnh chuẩn:
   - Cỡ cảnh khác (vừa người, cận giày): **cắt** thẳng từ ảnh chuẩn. Ảnh cắt bị mềm thì cho Nano Banana làm nét lại
     với lời dặn "giữ nguyên mọi thứ", rồi chỉnh màu về đúng ảnh cắt.
   - Góc không có trong ảnh (lưng, cận vải): dùng Nano Banana với ảnh chuẩn làm tham chiếu, kèm ảnh sản phẩm mặt trước
     và mặt sau của đúng chiếc váy.
3. Đặt từng ảnh khung cạnh ảnh chuẩn và soi: chiều dài váy, kiểu tất, tóc, túi. Sai thì làm lại. Bước này 0 credit.

**Khi sinh video**

4. Luôn có khung đầu là ảnh thật. Prompt chỉ tả chuyển động, máy quay, âm thanh. Không tả lại nhân vật.
5. Chỉ nêu đích danh những chi tiết từng bị trôi, và nói chúng giữ nguyên như trong ảnh.
6. Vật đeo trên người mà sẽ đổi bên trong khung hình (túi khi xoay người): chỉ dùng khung đầu, và tả đường đi của vật
   ("đúng một túi, dây vắt vai trái, túi ở hông trái, xoay theo người").
7. Khung đầu và khung cuối chỉ dùng chung khi hai khung gần nhau (cùng phòng, cùng vị trí máy, cùng ánh sáng).
8. Không lấy khung do máy sinh ra làm khung đầu quá một hai lần. Mỗi cảnh quay về ảnh chuẩn.
9. Một vị trí máy cho cả phim. Sự đa dạng đến từ cỡ cảnh (toàn thân, vừa người, cận, cận vải, lưng) và chuyển động
   máy, không đến từ việc đổi góc phòng.
10. Một giọng: mọi câu thoại trong một lần quay.

**Sau khi sinh**

11. Soi từng clip bằng dải khung hình (mỗi 0,25-0,7 s một khung), phóng to bàn tay ở các nhịp có động tác.
12. Đoạn nào model bịa sai thì cắt bỏ hoặc tua nhanh qua, không cố dùng.

## 5. Cách để video trông như người làm

**Nội dung**

- Có mạch: chào (mặt nhìn máy), khoe tổng thể, chi tiết (giày, vải, lưng), một khoảnh khắc đời thường (con mèo), rồi
  lời kêu gọi. Mỗi clip đúng một việc.
- Tả kiểu "quay bằng điện thoại": máy ngang ngực, hơi rung tay, không ánh sáng studio, không màu điện ảnh.

**Dựng**

- Cắt 2-3 s một lần. Cảnh dài hơn thì bên trong phải đổi nhịp (tua nhanh, đổi động tác).
- Cắt đúng lúc đang chuyển động, và giữ cùng hướng chuyển động qua chỗ cắt.
- Cắt khớp động tác: tay nhón bèo váy ở cảnh vừa người, cắt sang tay xoa ren ở cảnh cận.
- Lời nói chạy đè lên cảnh khác: câu 1 nói tiếp trên cảnh lia giày; câu 2 nói nốt trên cảnh cận ren.
- Chuỗi liền không thấy chỗ cắt: clip trước kết thúc đúng ở ảnh chuẩn, clip sau bắt đầu đúng từ ảnh chuẩn. Điểm nối
  chọn bằng cách đo cặp khung giống nhau nhất (độ lệch 4,1, ngang mức chuyển động bình thường trong một cảnh).
- Bỏ phần đuôi chậm dần của mỗi clip.
- Tua nhanh có nhoè chuyển động (kèm một tiếng "vút" nhỏ) để đổi nhịp và để lướt qua đoạn model vẽ sai mặt.
- Xuất đúng số khung/giây của nguồn (24). Đổi sang 30 là hình khựng định kỳ.

**Màu và độ nét**

- Mọi clip được kéo về đường cong màu của ảnh tĩnh mà nó xuất phát (đều cắt từ một ảnh chuẩn), rồi đo lại trên cùng
  một mảng tường và cánh cửa ở mọi cảnh. Chênh vùng tối giữa cảnh nói và cảnh hình: từ 6,6 xuống 0,3.
- Cảnh có nền khác (cận vải) thì cân theo màu da, không cân theo nền.
- Cảnh Omni được làm nét nhẹ; cả phim phủ một lớp hạt mỏng và giảm bão hoà nhẹ để các clip "dính" vào nhau.

**Tiếng**

- Một giọng. Một lớp tiếng phòng liên tục dưới mọi cảnh. Tiếng gốc của các cảnh không lời để nhỏ.
- Âm lượng chuẩn hoá về khoảng -16 LUFS (bản v7.1: -16,8).

**Những dấu hiệu "AI" cần tránh**

Trang phục đổi giữa cảnh, phòng mọc thêm đồ, vật bị nhân đôi, da nhựa, bàn tay sai, chữ hiện trên hình, bước chân
trượt, mọi cảnh cùng một tốc độ chậm đều, giọng mỗi câu một kiểu.

## 6. Dùng Google Flow thế nào

**Hai chế độ tạo video (mỗi video chạy một chế độ)**

| Chế độ | Đầu vào | Giữ tốt | Yếu |
|---|---|---|---|
| Frames | Khung đầu, tuỳ chọn thêm khung cuối | Mặt, trang phục, phòng đúng như ảnh | Không có giọng khoá sẵn |
| Ingredients | Ảnh tham chiếu và nhân vật Flow (có thể gắn giọng) | Giọng nhân vật | Flow vẽ lại mặt và đồ, lệch với ảnh gốc |

Phim v7 dùng Frames cho mọi cảnh.

**Một request mang được những gì (đo trên giao diện Flow 2026-10-01, 0 credit)**

| Thứ đưa vào | Frames | Ingredients | Ghi chú |
|---|---|---|---|
| Chữ (prompt, lời thoại, tả âm thanh) | Có | Có | Âm thanh của clip do model sinh từ chữ |
| Khung đầu, khung cuối | Có | Không | Hình bám ảnh nhất |
| Ảnh tham chiếu | Không | Có | Omni tối đa 7 ảnh, Veo Lite và Fast tối đa 3, Veo Quality không nhận (đo lại, mục 11) |
| Video tham chiếu | Không | Có, nhưng khi đó request thành lệnh SỬA chính video ấy | Omni, 20 credit; bản ra gần như chép lại hình và tiếng gốc (đo thật, mục 11) |
| Nhân vật Flow | Ô soạn nhận chip và báo giá, nhưng request gửi đi KHÔNG có nhân vật (đo thật, mục 11) | Có | Nhân vật mang theo giọng đã gắn |
| Giọng (Flow gọi là "audio ingredient") | Không (`@` không liệt kê giọng) | Có, kèm điều kiện | Xem dưới |
| File âm thanh (mp3, wav) | Không | Không | Ô upload chỉ nhận ảnh và video |

Luật của giọng, theo đúng câu Flow báo:

- Giọng đứng một mình bị từ chối ở mọi model: "An audio ingredient requires other ingredients to function."
- Omni: nhận giọng đi cùng ảnh, và cả đi cùng nhân vật đã có giọng.
- Veo Lite và Fast: nhận giọng đi cùng ảnh; tối đa một giọng ("Maximum audio ingredients reached (1 allowed)"), nhân
  vật đã gắn giọng tính là một.
- Veo Quality: không nhận giọng ("You cannot use audio ingredients with this model.").

MCP hiện phơi ra: khung đầu, khung cuối, ảnh tham chiếu, nhân vật (kèm giọng của nhân vật). Chưa phơi: giọng như một
ingredient riêng. Video tham chiếu và nhân vật đi cùng khung đầu thì không đáng phơi: Flow biến cái đầu thành lệnh
sửa video và lặng lẽ bỏ cái sau (mục 11).

**Model và giá (gói Pro, giá đọc trên Flow)**

| Model | Độ dài | Giá | Dùng cho |
|---|---|---|---|
| Nano Banana (ảnh) | | 0 credit | Tạo và sửa ảnh khung |
| Omni 1.1 Flash 720p | 4, 6, 8, 10 s | 7, 10, 12, 15 | Cảnh nói |
| Veo 3.1 Lite | 8 s | 10 | Cảnh hình giá rẻ, và cảnh nói có gắn giọng (mục 11) |
| Veo 3.1 Fast | 8 s | 20 | Cảnh hình |
| Veo 3.1 Quality | 8 s | 100 | Chưa cần |

**Omni và Veo Fast, đo trên cùng một cảnh, cùng khung đầu và khung cuối**

| | Omni | Veo 3.1 Fast |
|---|---|---|
| Khung cuối lệch so với ảnh neo (thấp là tốt) | 8,75 | 3,9 |
| Độ nét vùng váy ren (ảnh chuẩn 181) | 66 | 131 |
| Cuối clip | Vẫn đang chuyển động, chưa tới ảnh neo | Tới ảnh neo ở khoảng 70% rồi đứng yên |
| Dấu trên hình | ✦ to ở góc | Chữ "Veo" rất nhỏ |
| Nói tiếng Việt | Dùng được (chưa được Google đánh giá chính thức) | Fast chưa thử; Veo Lite nói đúng câu ở 11 trên 11 clip (mục 11) |

**Những lần Flow từ chối (đều không trừ credit)**

- Nội dung không an toàn: góc máy sát sàn nhìn ngược lên váy ngắn, và một ảnh cắt cận vùng ngực. Đổi sang góc ngang
  tầm mắt và cỡ cảnh rộng hơn thì qua. Không cố lách bộ lọc này.
- Bộ lọc âm thanh của Veo (số cập nhật 2026-10-01): khi có cả khung đầu lẫn khung cuối và có người trong hình, 5 trên
  10 lượt bị chặn. Khi chỉ có khung đầu, 7 trên 7 lượt qua. Bộ lọc này ngẫu nhiên: cùng một cặp khung trên Veo Lite
  qua cả khi có lẫn khi không có câu "Audio: quiet room tone only, no speech, no music.", nên không có "câu an toàn".
  Bị chặn thì chạy lại y nguyên vài lần (không mất credit), hoặc dùng chỉ khung đầu, hoặc đổi sang Omni.

**Mẫu prompt đã dùng (viết bằng tiếng Anh, lời thoại để nguyên tiếng Việt)**

Cảnh hình, chỉ khung đầu (cảnh xoay người):

```
One continuous shot, no scene cuts. A phone camera at chest height stays in place. She lets go of the wardrobe door
and turns around in place in one smooth half turn to show the back of the dress, her long black hair and the tiered
lace skirt swinging gently, then looks back over her shoulder at the camera with a soft smile. She carries exactly one
small black shoulder bag: its single studded strap stays over her left shoulder and the bag stays at her left hip,
turning with her body. The back of the dress is a smocked black bodice with short lace puff sleeves and the same three
tiers of lace ruffles. The sheer plain black knee-high socks, the black mary jane shoes and the room stay exactly as in
the image. Audio: quiet room tone only, no speech, no music.
```

Cảnh nói, một lần quay cho ba câu:

```
One continuous shot, no scene cuts. Phone propped at chest height, slight handheld micro-jitter. She speaks Vietnamese
to the camera in one natural young Southern Vietnamese female voice, soft, light and a little playful, the same voice
for all three lines, with a short pause between the lines. [0-3s] She gives a small wave with her right hand and says:
Hello mọi người! Outfit đi chơi tối nay của mình nè. [3.5-6.5s] She pinches the lace ruffle at the side of her skirt
and says: Váy ren này mềm lắm, không ngứa đâu nha. [7-10s] She makes a small finger heart, then waves and says: Thấy
xinh thì thả tim nha, bye bye! Natural blinks, lips in sync with the words. The black lace dress with short puff
sleeves, the small black shoulder bag and the room stay exactly as in the image. Audio: her voice and quiet room tone,
no music. No subtitles, no on-screen text.
```

Làm nét một ảnh cắt bằng Nano Banana:

```
Sharpen and restore this exact photo in high resolution. Keep the composition, crop, camera angle, pose, lighting and
colors identical [...]. Do not add, remove or move anything; only make the existing details crisp and realistic, like a
sharp raw smartphone photo.
```

Cấu trúc chung của một prompt video: một cảnh liên tục; máy quay ở đâu và chuyển động thế nào; đúng một hành động; các
chi tiết từng bị trôi "giữ nguyên như trong ảnh"; một dòng âm thanh. Với Omni viết ngắn; với Veo nói điều mình muốn
thay vì điều mình cấm.

**Công cụ**

Mọi thao tác trên Flow đi qua MCP của repo này: `gen_i2i` (ảnh khung), `flow_upload` (đưa ảnh vào project),
`gen_video` (có `dry_run` để đọc giá miễn phí, `max_credits` để chặn giá), `clip_download` 1080p (0 credit),
`flow_media` và sổ `ledger.jsonl` để đối chiếu chi tiêu. Flow đổi giao diện thường xuyên: trong ba ngày làm phim này
MCP đã phải sửa sáu lần (Plan AE tới AJ).

## 7. Quy trình đã làm ra bản v7.1

1. Chọn ảnh chuẩn, soi và ghi lại từng món trang phục.
2. Tạo ảnh khung (0 credit): toàn thân 9:16 và vừa người (cắt từ ảnh chuẩn), cận giày (cắt, làm nét, cân màu), cận
   ren (Nano Banana từ ảnh chuẩn). So từng ảnh với ảnh chuẩn.
3. Thử so sánh model trên một cảnh (27 credit) rồi mới chọn Veo Fast cho cảnh hình.
4. Sinh clip, mỗi clip một việc, bấm đúng một lần, soi ngay sau khi có.
5. Tải bản 1080p của từng clip (0 credit).
6. Dựng bằng ffmpeg trên máy.

| Giây | Cảnh | Khung đầu vào | Model | Credit |
|---|---|---|---|---|
| 0-1,5 | Vẫy tay, "Hello mọi người!" | Ảnh chuẩn cắt vừa người | Omni 10 s, một lần quay cho cả ba câu | 15 |
| 1,5-4,8 | Lia từ giày lên, tua nhanh, đáp xuống toàn thân; câu 1 nói tiếp | Đầu: cận giày cắt từ ảnh chuẩn. Cuối: ảnh chuẩn | Veo Fast | 20 |
| 4,8-9,7 | Xoay một vòng: lưng, ngoái lại | Ảnh chuẩn | Veo Fast, chỉ khung đầu | 20 |
| 9,7-11,4 | Nhón bèo váy, câu 2 | Cùng lần quay nói | | |
| 11,4-13,6 | Cận ren, tay xoa mép bèo; câu 2 nói nốt | Ảnh cận ren | Veo Fast, chỉ khung đầu | 20 |
| 13,6-16,4 | Bắn tim, vẫy chào, câu 3 | Cùng lần quay nói | | |
| 16,4-18,9 | Mèo lại dụi chân, mờ dần | Ảnh chuẩn | Veo Fast, chỉ khung đầu | 20 |

Các clip có mặt trong phim tốn 95 credit. Cả quá trình v7 tốn 122 (thêm 7 cho bản Omni của phép so sánh và 20 cho cảnh
xoay bị hai túi). Các bản trước: v5 134, v6 38.

**Hậu kỳ (tôi làm bằng ffmpeg, ngoài Flow)**

Chọn đoạn dùng của mỗi clip và thứ tự; tua nhanh có nhoè ở đoạn lia giày, tua gấp đôi ở hai đoạn của cảnh xoay; điểm
nối chọn theo số đo; đường cong màu riêng
từng clip; làm nét nhẹ cảnh Omni; hạt và giảm bão hoà chung; ghép lời thoại, tiếng gốc, tiếng vút, tiếng phòng; chuẩn
hoá âm lượng; mờ dần ở cuối; xuất 1080x1920, 24 khung/giây.

Phân vai: Flow sinh hình, giọng, khẩu hình và bản 1080p. Phần chọn ảnh, viết prompt, chọn model, soi lỗi và dựng là
việc của người (ở đây là agent) điều khiển.

## 8. Kiểm tra bằng số, không chỉ bằng mắt

| Câu hỏi | Cách đo | Con số ở v7.1 |
|---|---|---|
| Clip có đáp đúng ảnh neo không | Sai khác trung bình giữa khung cuối và ảnh neo | Veo 3,9; Omni 8,75 |
| Clip nét cỡ nào | Phương sai Laplace vùng váy | Veo 131; Omni 66; ảnh chuẩn 181 |
| Chỗ nối có giật không | Sai khác giữa hai khung liền kề tại điểm nối, so với bên trong cảnh | 4,1 so với 0,9-1,6 |
| Màu có lệch giữa các cảnh không | Màu trung bình của cùng mảng tường và cửa ở mọi cảnh | Vùng tối lệch 0,3; vùng sáng 0,2 |
| Giọng có đều không | Cao độ từng câu | 246-281 Hz trong một lần quay |
| Âm lượng | LUFS | -16,8 |

Luật đi kèm: thước đo bằng ảnh phải được vẽ khung lên hình và nhìn tận mắt trước khi tin số. Trong lần này hai khung đo
đầu tiên đã dính ren và bàn tay, số đo ra sai cho tới khi dời khung.

## 9. Giới hạn còn lại

- Chưa ai nghe kiểm lời tiếng Việt và khẩu hình bằng tai. Việc này cần người.
- Cảnh nói (Omni) vẫn mềm hơn cảnh Veo, nhìn kỹ còn thấy.
- Dấu ✦ của Flow còn trên cảnh nói, chữ "Veo" trên cảnh hình. Đó là dấu Google đánh cho video AI ở gói này; tôi không
  xoá trên video thành phẩm.
- Chỉ có một vị trí máy. Muốn thêm góc phòng khác thì phải tạo ảnh khung góc đó từ ảnh chuẩn và soi rất kỹ.
- Bộ lọc âm thanh của Veo không đoán trước được khi dùng khung cuối.
- Phim 19 s. Dài hơn thì cần thêm cảnh, mỗi cảnh hình khoảng 20 credit.

## 10. Danh sách kiểm cho phim sau

- [ ] Một ảnh chuẩn, đã soi và ghi từng món trang phục.
- [ ] Kịch bản, lời thoại, danh sách cảnh (mỗi cảnh một việc) và giá, được duyệt trước.
- [ ] Mọi ảnh khung sinh từ ảnh chuẩn và đã so cạnh ảnh chuẩn.
- [ ] Cảnh nói: một lần quay Omni cho mọi câu. Cảnh hình: Veo Fast, ưu tiên chỉ khung đầu.
- [ ] Prompt: một cảnh liên tục, một hành động, nêu đích danh chi tiết phải giữ, câu âm thanh an toàn.
- [ ] Chạy `dry_run` đọc giá trước mỗi kiểu cảnh mới; bấm thật đúng một lần.
- [ ] Soi từng clip bằng dải khung hình; cắt bỏ đoạn model bịa.
- [ ] Dựng: cắt 2-3 s, cắt lúc đang chuyển động, lời đè lên cảnh, bỏ đuôi chậm, 24 khung/giây.
- [ ] Đo: điểm nối, màu trên mảng nền chung, âm lượng.
- [ ] Một người nghe và xem lại trước khi đăng.

## 11. Đo thêm ngày 2026-10-01: hình, giọng, và thứ Flow thật sự nhận

Sau bản v7.1, tôi chạy 24 lượt sinh thật (23 lượt trả tiền, 252 credit; 1 lượt Flow làm hỏng, không trừ credit) và
nhiều lượt đọc 0 credit để trả lời những câu còn bỏ ngỏ ở trên. Số đo đầy đủ nằm ở `docs/research/google-flow/` (tiếng Anh): `README.md` là bức tranh toàn cảnh,
`test-results.md` là từng lượt chạy, `mcp-gaps.md` là thứ MCP còn thiếu, `skill-design.md` là thiết kế bộ skills.

**Giữ hình**

- Khung đầu (Frames) vẫn là đường duy nhất giữ đúng ảnh: 7 clip trên cả Omni lẫn Veo Lite giữ váy, túi, dây chuyền,
  hình xăm, phòng và con mèo suốt 8 giây. Thứ ảnh không cho thấy thì model tự vẽ theo ý nó: ở cảnh xoay người, cả Veo
  Lite lẫn Veo Fast đều vẽ một hình xăm lớn trên lưng và tự chọn mặt trước của túi (một bên khoá đinh, một bên hình
  bướm), mà không ảnh nào cho biết thật ra sao.
- Ingredients là vẽ lại. Omni vẽ lỏng: mỗi lần một khung, một dáng khác. Veo 3.1 Lite vẽ sát ảnh, đúng cỡ cảnh, nhưng
  5 trên 7 clip có một lỗi: viền điện thoại (do chữ trong prompt), kẹp tóc, nơ tóc, túi chuyển từ vai xuống tay kèm
  mờ dần sang xám ở 0,4 giây cuối, và con mèo thứ hai.
- Đưa ba ảnh tham chiếu thay vì một: khung toàn thân, tất và giày đúng hơn, nhưng thứ nào có mặt trong cả ba ảnh thì
  bị vẽ thành hai (hai con mèo).
- Ở Ingredients, chữ nào trong prompt cũng dễ thành vật trong hình: viết "Phone propped" là ra viền màn hình điện thoại.

**Giữ giọng**

| Cách | Hình | Giọng | Giá |
|---|---|---|---|
| Khung đầu, Omni 6 s, KHÔNG gắn giọng, MỘT câu ngắn viết như nói chuyện | Đúng ảnh | Chủ repo chấm: thật, dứt khoát, cùng một người (4 trên 4 clip, hai tài khoản) | 10 cho 6 s |
| Như trên nhưng câu thoại là câu mô tả sản phẩm | Đúng ảnh | Chủ repo chấm: giả như máy đọc, khác người, chậm (4 trên 4 clip) | 10 cho 6 s |
| Mọi câu trong MỘT lần quay Omni 10 s, cắt ra khi dựng | Đúng ảnh | Một giọng | 15 |
| Gắn giọng Flow làm ingredient + một ảnh, Veo 3.1 Lite | Vẽ lại sát ảnh | Đúng giọng đó, clip nào cũng vậy; chủ repo chấm: kém thật hơn hàng đầu | 10 cho 8 s |
| Gắn giọng Flow làm ingredient + một ảnh, Omni | Vẽ lại lỏng | Đúng giọng đó; hai giọng thì mỗi giọng vào đúng người; chủ repo chấm: kém thật hơn hàng đầu | 10 đến 12 |
| Khung đầu + cùng một câu tả giọng ở mọi prompt, hai câu trong clip 8 s | Đúng ảnh | Một "họ giọng" na ná nhau, không phải một giọng; chủ repo chấm: không clip nào thật | 10 đến 12 |
| Kéo dài (Extend) một clip Veo đang nói | Liền hình, 8 + 7 = 15 s | Thước của tôi không thấy giữ giọng | 10 cho 7 s |

- Hai clip nói cùng một câu: có gắn giọng thì lệch 1,0 đến 1,2; không gắn thì 2,5 đến 3,5; clip có gắn so với clip
  không gắn là 3,3 đến 6,2 (thước tự viết, thô; hai giọng nữ na ná nhau nó không phân biệt chắc được).
- Vì thước thô, tai người là trọng tài: `out/flow_research/listen_voices.mp4` xếp các clip theo nhóm để nghe.
- **Tai chủ repo đã chấm (2026-10-01 và 2026-10-02), và lời chấm đứng trên các số đo ở trên.** Mọi clip gắn giọng đều
  kém thật hơn clip `ak-a3` không gắn. Tám clip cùng công thức của `ak-a3` (Omni, khung đầu `v5_talk_t1_57.png`, 6 s,
  720p, không gắn giọng, một câu 9 tới 11 âm tiết), trên hai tài khoản:

  | Câu thoại | Kiểu câu | Chủ repo chấm |
  |---|---|---|
  | "Hôm nay mình mặc váy ren đen đi cà phê nè." | nói chuyện | đạt |
  | "Mọi người thấy bộ này có dễ thương không?" | nói chuyện | đạt, cùng người |
  | "Mình mặc váy này cả ngày vẫn thấy thoải mái nè." | nói chuyện | đạt |
  | "Tối nay mình mặc bộ này đi chơi nha mọi người." | nói chuyện | đạt, cùng người |
  | "Váy này mềm lắm, mặc cả ngày vẫn thấy thoải mái." (hai lần) | mô tả | không đạt |
  | "Váy này mặc cả ngày vẫn thấy thoải mái." | mô tả | không đạt |
  | "Chất vải của váy này rất mềm và thoáng mát." | mô tả | không đạt |

- **Luật viết thoại**: mỗi clip một câu, viết như đang nói chuyện với người xem: xưng "mình", có từ đệm ("nè", "nha"),
  hoặc hỏi thẳng ("Mọi người thấy ... không?"). Không viết câu mô tả sản phẩm kiểu quảng cáo ("Váy này ...", "Chất vải
  ..."): model đọc nó như đọc văn bản, chậm hơn (3,9 tới 4,6 so với 4,9 tới 5,2 âm tiết một giây), thường trầm hơn, và
  nghe ra người khác. Một vế hay hai vế không đổi kết quả. Ý nào cần nói thì đổi sang lời nói chuyện trước khi gửi.
- Luật này mới thử trên một nhân vật, một ảnh khung đầu, giọng nữ miền Nam, clip 6 s. Dài hơn, hai câu trong một clip,
  nhân vật khác: chưa thử.
- Cao độ (Hz) ở mục 8 là thước yếu: hai người khác nhau vẫn có thể cùng cao độ.
- Sửa lời bằng Omni edit hay bằng video tham chiếu đều không được: tiếng giữ nguyên, lời mới bị in thành phụ đề.

**Nói tiếng Việt**: 18 trên 18 lượt nói đúng câu trong prompt (kiểm bằng Whisper chạy trên máy), hai lượt trượt một chữ.

**Bản nháp 360p** (nửa giá): dùng để xem bố cục, chuyển động, độ dài lời. Mặt, mặt dây chuyền, hình xăm bị vẽ thô và
bản phóng lên 720p miễn phí không lấy lại được. Bản phóng 1080p của clip 720p thì nét thật, 0 credit.

**Veo Lite hay Veo Fast**: cùng cảnh xoay, cùng khung đầu, cả hai giữ một túi. Lite ra mặt cười rộng hơn và bịa cái
khoá túi hình bướm; Fast điềm hơn. Một cảnh thì chưa xếp hạng được; Google cũng tự chấm hai model gần ngang nhau.

**Thứ Flow nhận, theo chính câu nó báo**: Omni tối đa 7 ảnh và 5 giọng; Veo Lite và Fast tối đa 3 ảnh và 1 giọng; Veo
Quality không nhận ảnh, nhân vật hay giọng nào. Hai nhân vật cùng lúc thì Omni, Veo Lite và Fast đều nhận. Veo ở tài
khoản này chỉ có 8 giây. (Lượt đo đầu của tôi đọc sai dấu hiệu từ chối nên từng ghi "không model nào từ chối ảnh";
đã đo lại bằng dấu hiệu đúng.)

**Còn mở**: luật viết thoại có đứng ở clip dài hơn 6 s, hai câu nói chuyện trong một clip, nhân vật hay ảnh khung đầu
khác không; hai người cùng giới thì giọng nào vào ai; gắn giọng trên Veo Fast; kéo dài nhiều lần liên tiếp.
