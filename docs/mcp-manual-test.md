# Test tay MCP `video`: 32 tool, có giá và có rào chắn

Hướng dẫn để chủ repo tự test, hoặc giao cho một agent khác gọi qua MCP. Mọi hình dạng kết quả dưới đây là
**đo thật ngày 2026-09-14 và 2026-09-15**, không phải suy từ code.

## 0. Trước khi bắt đầu

- **Mở phiên Claude Code mới trong repo này.** Server `video` đang chạy KHÔNG tự nạp code mới. Tắt tiến trình
  server thì lời gọi kế tiếp chạy code mới, nhưng phiên đang mở vẫn giữ mô tả tool và `instructions` cũ (đo
  2026-09-15), nên agent trong phiên cũ đọc hướng dẫn cũ.
- Gọi thử hai tool rẻ nhất: `flow_lane()` phải ra `verdict: MIGRATED`, và `flow_credits()` ra số dư (lần đo cuối
  là **245**, 2026-09-15). Mỗi lần đọc số dư đều ghi thêm một dòng vào `out/credits.jsonl`.
- Số dư **dao động giữa các lần đọc mà chưa rõ cơ chế**, nên trước mỗi lần định tiêu tiền phải đọc lại.
- **Mỗi lời gọi lái một Chrome thật và chờ tới khi Flow trả lời.** Đo 2026-09-15 qua MCP: lời gọi mở một trang mất
  12-17 s, lời gọi mở hai ba trang khoảng 50 s (`flow_tools()`, `project_rename`, `scene_restore`, `scene_delete`),
  một lần sinh `gen_r2v` mất 292 s. Chậm không có nghĩa là hỏng: đừng gọi lại.
- **Lỗi của tool giờ tới được agent kèm lý do** (đã lọc cookie và token), ví dụ
  `LookupError: scene ... is already in the trash`. Trước đó agent chỉ thấy `Error executing tool <tên>`.

## 1. Tầng 0: để máy tự kiểm trước, 0 credit

Tầng 0 đỏ thì dừng, đừng test tay tiếp: lỗi nằm ở tầng dưới chứ không phải ở thao tác của bạn.

| Lệnh | Kỳ vọng |
|---|---|
| `uv run pytest -q` | `308 passed` |
| `uv run python scripts/acceptance/ledger_integrity.py` | `rows=6 pass=6 fail=0`, offline, không mở trình duyệt |
| `uv run python scripts/acceptance/mcp_smoke.py` | `rows=12 pass=12 fail=0`, 2 đến 3 phút, gọi THẬT 8 tool đọc qua MCP |
| `uv run python scripts/acceptance/flow_coverage.py --project <id> --character` | ma trận CLI; tự tạo rồi tự xoá project "acceptance probe" |
| `uv run python -m video.probes.canary --project <id>` | 11 mỏ neo UI còn nguyên; exit 1 khi Google đổi giao diện |

## 2. Dựng project nháp, để không đụng dữ liệu thật

1. Gọi `project_create(title="mcp manual test")`. Kết quả có dạng `{"id": "...", "rpcids": [...], "title": "..."}`.
2. **Ghi lại `id` đó.** Mọi bước sau chỉ dùng đúng id này.
3. Cuối buổi gọi `project_delete(project_id=<id nháp>)`.

**`project_delete` xoá vĩnh viễn cả clip, ingredient và prompt.** Đọc lại id hai lần trước khi gọi. Không bao
giờ dán id của project thật vào tool này.

## 3. Bảng 32 tool

Cột "giá" lấy từ đo trên gói PRO. Nhóm A và B an toàn với mọi project; nhóm C chỉ làm trong project nháp.

### A. Chỉ đọc, $0, an toàn tuyệt đối

| Tool | Tham số | Kết quả đúng trông như thế nào |
|---|---|---|
| `flow_lane` | không | `{"verdict": "MIGRATED", "projects": 19, "roots": {...}}` |
| `flow_projects` | không | list dict `{id, title, created, cover_media_id, thumbnail_url}`. **Có một id KHÔNG phải UUID**: `8822142b-ca75-46b7-aac8-03d2831_backfill` |
| `flow_credits` | không | `{"balance": <int>, "raw": [...]}` |
| `flow_media` | `project_id`, `all_versions=False` | `{"meta": {id, title}, "media": [...], "models": [4 tên model]}`. `all_versions=true` trả CÙNG object đó, thêm khoá `versions`: list bản ghi có `type` và `workflow_id` |
| `flow_characters` | `project_id` | list `{entity_id, name, portrait_media_id, portrait_workflow_id}`. Tải ảnh chân dung bằng `portrait_media_id` (là `null` khi listing chưa có record của ảnh); project mới ra `[]` |
| `flow_tools` | `project_id` tuỳ chọn | list tool của gallery cộng đồng, giống nhau ở mọi project; số lượng đổi theo thời gian (62 ngày 14/9, 60 rồi 62 ngày 15/9). Bỏ trống `project_id` thì server tự mở project đầu tiên trên grid, vì Flow chỉ nạp gallery bên trong một project |
| `flow_uploads` | `project_id` | `{"count": n}` và không có gì khác |
| `scene_list` | `project_id`, `include_trashed=False` | list `{scene_id, title, trashed, created, updated}`. Mặc định ẩn scene đã xoá; `include_trashed=true` mới thấy |

### B. Ghi file trên máy, $0

| Tool | Tham số | Ghi chú |
|---|---|---|
| `flow_download` | `project_id`, `media_id`, `out_dir` | trả đường dẫn file trong `out/`. `media_id` phải là `id` của listing (`flow_media`); workflow id sẽ bị từ chối |
| `clip_download` | `project_id`, `media_id`, `quality="1080p"`, `out_dir`, `workflow_id` | mặc định lấy bản MỚI NHẤT đã xong; truyền `workflow_id` để chỉ đích danh một version. **`quality="4k"` là bản upscale của Flow, CHƯA ĐO GIÁ, có thể tốn credit: đừng gọi nếu chưa muốn trả tiền** |
| `clip_reconcile` | `project_id`, `out_dir` | đóng sổ cho job editor mồ côi; trả `{"ledger": <đường dẫn tuyệt đối>, "ledger_exists", "ledger_rows", "jobs"}`. `jobs: []` chỉ nghĩa là sạch khi `ledger_exists` là `true` và đường dẫn đúng sổ bạn định đọc. Verdict: `done` (chỉ cho `clip_edit`: trên chính clip nguồn có đúng một version mới mang đúng prompt của job, chưa job nào khác trong cùng sổ giữ làm output `generated`, đã xong, và trong cùng sổ không còn job đối thủ cùng clip cùng prompt: job còn mở, hoặc đã đóng mà không giữ version `generated` nào trên clip đó, trừ job có dòng cuối là `failed` do `clip_reconcile` ghi; ghi vào `outputs`; bản upscale 1080p và bản chép do extend tạo không bao giờ bị nhận), `failed` (số dư không đổi VÀ project không có record mới nào; `clip_extend` cũng có thể ra `failed`), `unknown` (để người quyết; với `clip_extend` là mọi ca không `failed`; cả khi version còn đang sinh, khi có hai version, khi còn job đối thủ như trên, khi listing không còn record nào của clip nguồn mà job đã thấy, và với dòng `opening` cũ thiếu tập workflow hay prompt, hoặc tập workflow rỗng), `skipped` (job gen hay agent, hoặc job editor của project khác kèm `project`; không bao giờ bị ghi). Sổ không có gì để chấm thì trả ngay, không mở Chrome. `spent` của dòng do reconcile ghi là độ lệch số dư từ lúc job mở, có thể gồm khoản chi khác: đừng cộng làm tổng chi |

### C. Đổi dữ liệu Flow, $0, CHỈ làm trong project nháp

| Tool | Tham số | Ghi chú |
|---|---|---|
| `project_create` | `title` | trả `{"id", "rpcids", "title"}` |
| `project_rename` | `project_id`, `title` | đọc lại tên trên grid rồi trả `{"id", "title"}`; grid hiện tên khác thì báo lỗi |
| `project_delete` | `project_id` | **xoá vĩnh viễn**; chỉ dùng cho id nháp |
| `scene_create` | `project_id`, `title` | trả dict có `scene_id` |
| `scene_delete` | `project_id`, `scene_id` | là "move to trash", scene vẫn còn trong `scene_list(include_trashed=true)`; lấy lại bằng `scene_restore`. Tile trên grid không mang scene id nên tool chờ grid hiện đủ một tile cho mỗi scene đang hoạt động (tối đa 15 s) rồi mới tìm theo tên đúng nguyên: tên rỗng hay chỉ ký tự vô hình, hai scene cùng tên, hay số tile không khớp, thì từ chối; bấm xong đọc lại listing, scene khác bị đổi cờ thì báo tên nó |
| `scene_restore` | `project_id`, `scene_id` | lấy scene ra khỏi thùng rác, đọc lại listing rồi trả `{"scene_id", "trashed": false, "rpcids", "active"}`. Tile trong thùng rác không mang scene id nên tool chờ thùng rác hiện đủ một tile cho mỗi scene đã xoá (tối đa 15 s) rồi mới tìm theo tên đúng nguyên, và **từ chối khi tên rỗng hay chỉ ký tự vô hình, khi tên có ở hơn một tile**, hay khi số tile không khớp số scene đã xoá |
| `scene_rename` | `project_id`, `scene_id`, `title` | đổi tên scene rồi đọc LẠI listing để xác nhận. Trang scene có đúng một ô sửa được trong header (đo 2026-09-16) và tool đòi đúng một ô đó; tên rỗng hay chỉ ký tự vô hình bị từ chối, vì các tool scene tìm nhau bằng tên |
| `scene_add_clip` | `project_id`, `scene_id`, `media_id` | đặt một clip của project lên timeline của scene, đây là cách một scene thành phim nhiều cảnh. Picker không mang media id nên tool tra `media_id` ra **title** từ listing rồi khớp đúng nguyên tên; từ chối khi title rỗng, khi media khác trong project trùng title, hay khi picker hiện title đó hơn một lần. Trả `duration_before`, `duration_after`, `changed` đọc từ nhãn `Total duration` của editor. **`changed: false` thường chỉ là nhãn chưa kịp cập nhật, KHÔNG phải add hỏng**: đọc lại phim bằng `scene_download` thay vì thêm lần nữa, vì thêm hai lần là clip vào timeline hai lần |
| `scene_download` | `project_id`, `scene_id`, `out_dir=None` | tải cả scene thành MỘT phim, không phải một clip: đo 2026-09-16, hai clip 8 s ra một mp4 **16,0 giây**. Scene rỗng bị từ chối, `out_dir` phải nằm trong `out/`. Trang scene KHÔNG có menu chọn chất lượng (khác trang clip), nên muốn 270p/720p/1080p/4k thì dùng `clip_download`. Trả thêm `attempts`: bằng 2 nghĩa là lần đầu trình duyệt chết đúng lúc phim vừa tải và tool lấy lại trong phiên mới |
| `character_create` | `project_id`, `prompt`, `name`, `personality`, `wait=90` | chân dung vẽ bằng Nano Banana 2, **không tốn credit**. Trả `portrait.workflow_id`, **không phải media id**: lấy media id bằng `flow_characters` |
| `character_delete` | `project_id`, `entity_id` | xoá vĩnh viễn nhân vật |
| `flow_upload` | `project_id`, `path` | đường dẫn file trên máy; trả `{file, bytes, rpcids, tiles, media_id, workflow_id, size_bytes, ...}`. `media_id` là id dùng được với `flow_download`. `bytes` là file trên máy, `size_bytes` là bản Flow lưu (Flow nén ảnh lại: PNG 139 KB thành JPEG 5,6 KB). `tiles` đếm tile của view đang mở, không phải số upload |
| `agent_mode` | `project_id`, `enabled` | bật xong **nhớ tắt**: bật thì Flow giấu chip và nút settings của composer |

### D. Tiêu credit, chỉ chạy khi bạn cố ý

Cả 6 tool tốn credit **bắt buộc `job_id`**. `job_id` đã có bất kỳ dòng nào trong mọi `ledger.jsonl` dưới `out/` (tên
file hoa hay thường) bị từ chối ngay, trước khi mở trình duyệt; `job_id` mang chữ giống bí mật phiên (tên cookie hay
header `Authorization`) cũng bị từ chối; `job_id` đang chạy ở lời gọi khác cũng bị từ chối, lúc đó phải chờ rồi gọi
lại với CÙNG `job_id`, không đổi id mới. `clip_edit`, `clip_extend`, `agent_send` từ chối prompt có ký tự xuống dòng;
prompt toàn dấu cách bị từ chối; `out_dir` của `clip_edit`, `clip_extend` phải là thư mục nằm trong `out/`, không có
thành phần nào tên `ledger.jsonl` (hoa hay thường) hay đã có sẵn là file.

| Tool | Giá đã đo | Ghi chú |
|---|---|---|
| `gen_t2i`, `gen_i2i` | **0 credit** (nano2) | tính vào quota ảnh theo ngày, không phải credit |
| `gen_t2v` | **15** khi bỏ trống model (omni-flash 10 s, count 1); **10** với `model="veo-lite"` (8 s) | `count` nhân giá (veo-lite `count=2` là 20); `count` phải 1-4, `aspect` chỉ `9:16` hoặc `16:9` |
| `gen_r2v` | **12** khi bỏ trống model (omni-flash, luôn 8 s, đo 2026-09-15); **10** với `model="veo-lite"` | host này chỉ cho r2v 8 s: đừng truyền `duration` (truyền 8 cũng không được gửi đi). Số ảnh: omni-flash tối đa 7, veo-lite và veo-fast tối đa 3, veo-quality không nhận ảnh; quá số bị từ chối trước khi tiêu. Flow từ chối ảnh có người mặc đồ lót |
| `gen_i2v` | **chưa đo** | chưa lần nào thành công. Lần thứ năm, 2026-09-16 qua MCP, vẫn hỏng y hệt: gflow exit 23 `UiSelectorDriftError`, "the frame picker stayed open 15s after picking", **0 credit**, sổ có dòng `failed` kèm mã lỗi. Đường thay thế là `gen_r2v` |
| `clip_extend` | **10** | tạo scene mới và chép clip nguồn vào đó (output có `role: copy`). **Chỉ chạy trên clip Veo**: đo 2026-09-16 bằng đối chứng đổi đúng một biến, clip omni-flash làm mục `Extend (Veo 3.1 - Lite)` hiện XÁM nên tool từ chối kèm lời giải thích, clip veo-lite thì chạy ngay |
| `clip_edit` | **20** | Omni 1.1 Flash, sửa video theo chữ |
| `agent_send` | **0** ở 4 lần agent không sinh gì (lần thứ tư qua MCP, 2026-09-16, số dư 193 trước và sau) | agent tự sinh media thì trả giá của lần sinh đó; hỏi trước khi gọi |

## 4. Prompt sẵn để giao cho agent khác

Dán nguyên khối này, thay `<ID NHÁP>` bằng id vừa tạo ở mục 2:

```
Bạn có MCP server "video" điều khiển Google Flow. Hãy test nó và báo cáo lại.

RÀO CHẮN, không được vi phạm:
- Chỉ thao tác trong project <ID NHÁP>. Không đụng project nào khác.
- Không gọi project_delete với bất kỳ id nào khác <ID NHÁP>.
- Không gọi tool tiêu credit (gen_t2v, gen_i2v, gen_r2v, clip_extend, clip_edit, agent_send) và không gọi
  clip_download với quality="4k". Nếu thấy cần, hãy DỪNG và hỏi tôi trước.
- Nếu tool nào báo Google gắn cờ hoạt động bất thường, DỪNG hẳn: không thử lại, không đăng nhập lại, báo tôi.

Việc cần làm, theo thứ tự, và sau mỗi bước dán nguyên JSON trả về:
1. flow_lane, flow_projects, flow_credits
2. flow_media cho <ID NHÁP>, rồi flow_media với all_versions=true
3. flow_characters, flow_tools, flow_uploads, scene_list cho <ID NHÁP>
4. project_rename đổi tên thành "mcp manual test 2", rồi flow_projects xem tên đã đổi chưa
5. scene_create, scene_list, scene_delete, scene_list với include_trashed=true, rồi scene_restore và scene_list lần nữa
6. character_create với prompt tuỳ bạn, flow_characters, character_delete
7. flow_upload một file ảnh nhỏ có sẵn trên máy, rồi flow_uploads xem count tăng
8. agent_mode bật rồi tắt
9. clip_reconcile cho <ID NHÁP>

Cuối cùng: liệt kê tool nào chạy đúng, tool nào sai hoặc báo lỗi, kèm nguyên văn lỗi.
```

## 5. Khi bạn quyết định chạy phần tiêu credit

1. Gọi `flow_credits()` và ghi lại số dư.
2. Gọi đúng **một** lần, ví dụ `gen_t2v(prompt="...", project="<ID NHÁP>", job_id="thu-t2v-1", model="veo-lite",
   aspect="9:16")`. `job_id` là bắt buộc và do bạn đặt; bỏ trống `model` thì server dùng omni-flash 10 s (15 credit).
3. Gọi lại `flow_credits()`.
4. Đối chiếu bằng chứng:
   - `out/ledger.jsonl`: job đó phải có dòng `submitted` rồi `done`, kèm `credits_before`, `credits_after`, `spent`.
   - `out/credits.jsonl`: hai dòng có giờ, khớp với số dư trước và sau.
5. **Không bao giờ bấm lại khi nghi ngờ.** Flow có thể nhận cú bấm, bắn request, rồi không tạo job và không trừ
   tiền. Kết luận thành hay bại bằng `flow_media` cộng số dư, đừng bằng cách gọi lại. Lỡ gọi lại cùng job thì giữ
   đúng `job_id` cũ: sổ từ chối ngay (`already has ledger rows`), không trừ tiền lần hai.

## 6. Bẫy đã đo, sẽ gặp khi bấm tay

- **Server MCP đang chạy không nạp code mới, và phiên đang mở giữ mô tả tool cũ.** Sửa code xong phải mở phiên
  mới.
- **Sidebar của project render sau khi trang sẵn sàng**, khoảng 2000ms (project có upload) đến 3000ms (project
  rỗng). Tool đã chờ đúng cách, nhưng nếu bạn tự lái trình duyệt thì đừng đọc sidebar quá sớm.
- **Mục Uploads lọc phía client**: bấm vào không gọi mạng, URL không đổi.
- **`clip_download` mặc định lấy bản mới nhất.** Muốn đúng bản của một lần edit thì lấy `workflow_id` từ
  `flow_media(all_versions=true)`.
- **Xoá scene là soft trash**, không biến mất khỏi listing.
- **Composer chỉ gắn được một ingredient mỗi prompt.**
- **Flow từ chối mọi generation vẽ người đang mặc đồ bán hàng**: submit đi bình thường, chờ rất lâu, không có
  record nào, 0 credit, không báo gì.
- **Rendition có thể 404 ngay sau khi sinh**, thậm chí 404 vĩnh viễn với một workflow trong khi workflow khác
  cùng media vẫn tải được.
- **Đừng chạy `gflow auth login`** trên tài khoản này, kể cả khi gflow khuyên thế.

## 7. Dọn dẹp sau khi test

1. `agent_mode(project_id=<ID NHÁP>, enabled=false)` nếu đã bật.
2. `project_delete(project_id=<ID NHÁP>)`.
3. `flow_projects()` xem project nháp đã biến mất chưa.
4. Xem lại `out/ledger.jsonl` và `out/credits.jsonl` nếu có chạy phần tiêu credit.

## 8. Báo lỗi thế nào cho sửa được

Với mỗi lỗi, gửi đủ bốn thứ: tên tool, tham số đã truyền, nguyên văn JSON hoặc thông báo lỗi trả về, và id
project. Nếu là lỗi lúc tiêu tiền thì kèm các dòng liên quan trong `out/ledger.jsonl`.
