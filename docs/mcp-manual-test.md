# Test tay MCP `video`: 28 tool, có giá và có rào chắn

Hướng dẫn để chủ repo tự test, hoặc giao cho một agent khác gọi qua MCP. Mọi hình dạng kết quả dưới đây là
**đo thật ngày 2026-09-14**, không phải suy từ code.

## 0. Trước khi bắt đầu

- **Kết nối lại MCP.** Server `video` đang chạy trong phiên cũ KHÔNG tự nạp code mới. Mở phiên Claude Code mới
  trong repo này, hoặc kết nối lại MCP, nếu không bạn sẽ test nhầm bản cũ.
- Gọi thử hai tool rẻ nhất: `flow_lane()` phải ra `verdict: MIGRATED`, và `flow_credits()` ra số dư (lần đo cuối
  là **195**). Mỗi lần đọc số dư đều ghi thêm một dòng vào `out/credits.jsonl`.
- Số dư **dao động giữa các lần đọc mà chưa rõ cơ chế**, nên trước mỗi lần định tiêu tiền phải đọc lại.

## 1. Tầng 0: để máy tự kiểm trước, 0 credit

Tầng 0 đỏ thì dừng, đừng test tay tiếp: lỗi nằm ở tầng dưới chứ không phải ở thao tác của bạn.

| Lệnh | Kỳ vọng |
|---|---|
| `uv run pytest -q` | `263 passed` |
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

## 3. Bảng 28 tool

Cột "giá" lấy từ đo trên gói PRO. Nhóm A và B an toàn với mọi project; nhóm C chỉ làm trong project nháp.

### A. Chỉ đọc, $0, an toàn tuyệt đối

| Tool | Tham số | Kết quả đúng trông như thế nào |
|---|---|---|
| `flow_lane` | không | `{"verdict": "MIGRATED", "projects": 19, "roots": {...}}` |
| `flow_projects` | không | list dict `{id, title, created, cover_media_id, thumbnail_url}`. **Có một id KHÔNG phải UUID**: `8822142b-ca75-46b7-aac8-03d2831_backfill` |
| `flow_credits` | không | `{"balance": <int>, "raw": [...]}` |
| `flow_media` | `project_id`, `all_versions=False` | `{"meta": {id, title}, "media": [...], "models": [4 tên model]}`. `all_versions=true` trả LIST bản ghi có `type` và `workflow_id` |
| `flow_characters` | `project_id` | list `{entity_id, name, portrait_media_id}`; project mới ra `[]` |
| `flow_tools` | `project_id` | list 62 tool của gallery cộng đồng (giống nhau ở mọi project) |
| `flow_uploads` | `project_id` | `{"count": n}` và không có gì khác |
| `scene_list` | `project_id`, `include_trashed=False` | list `{scene_id, title, trashed, created, updated}`. Mặc định ẩn scene đã xoá; `include_trashed=true` mới thấy |

### B. Ghi file trên máy, $0

| Tool | Tham số | Ghi chú |
|---|---|---|
| `flow_download` | `project_id`, `media_id`, `out_dir` | trả đường dẫn file trong `out/` |
| `clip_download` | `project_id`, `media_id`, `quality="1080p"`, `out_dir`, `workflow_id` | mặc định lấy bản MỚI NHẤT đã xong; truyền `workflow_id` để chỉ đích danh một version. **`quality="4k"` là bản upscale của Flow, CHƯA ĐO GIÁ, có thể tốn credit: đừng gọi nếu chưa muốn trả tiền** |
| `clip_reconcile` | `project_id`, `out_dir` | đóng sổ cho job editor mồ côi; project sạch thì trả `[]` |

### C. Đổi dữ liệu Flow, $0, CHỈ làm trong project nháp

| Tool | Tham số | Ghi chú |
|---|---|---|
| `project_create` | `title` | trả `{"id", "rpcids", "title"}` |
| `project_rename` | `project_id`, `title` | trả tên mới |
| `project_delete` | `project_id` | **xoá vĩnh viễn**; chỉ dùng cho id nháp |
| `scene_create` | `project_id`, `title` | trả dict có `scene_id` |
| `scene_delete` | `project_id`, `scene_id` | là "move to trash", scene vẫn còn trong `scene_list(include_trashed=true)` |
| `character_create` | `project_id`, `prompt`, `name`, `personality`, `wait=90` | chân dung vẽ bằng Nano Banana 2, **không tốn credit** |
| `character_delete` | `project_id`, `entity_id` | xoá vĩnh viễn nhân vật |
| `flow_upload` | `project_id`, `path` | đường dẫn file trên máy; trả `{file, bytes, rpcids, tiles, media_id, ...}` |
| `agent_mode` | `project_id`, `enabled` | bật xong **nhớ tắt**: bật thì Flow giấu chip và nút settings của composer |

### D. Tiêu credit, chỉ chạy khi bạn cố ý

| Tool | Giá đã đo | Ghi chú |
|---|---|---|
| `gen_t2i`, `gen_i2i` | **0 credit** | tính vào quota ảnh theo ngày, không phải credit |
| `gen_t2v` | **10** (veo-lite, 720p, 8 giây, count 1) | `count=2` thành 20; `model="omni-flash"` với `duration=10` là 15 |
| `gen_r2v` | **10** | dùng ảnh tham chiếu; Flow từ chối ảnh có người mặc đồ lót |
| `gen_i2v` | **10** | **hay hỏng phía Flow** ở bước chọn khung đầu; hỏng thì 0 credit. Đường thay thế là `gen_r2v` |
| `clip_extend` | **10** | tạo scene mới và chép clip nguồn vào đó |
| `clip_edit` | **20** | Omni 1.1 Flash, sửa video theo chữ |
| `agent_send` | **chưa đo** | "may spend credits"; hỏi trước khi gọi |

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
5. scene_create, scene_list, scene_delete, rồi scene_list với include_trashed=true
6. character_create với prompt tuỳ bạn, flow_characters, character_delete
7. flow_upload một file ảnh nhỏ có sẵn trên máy, rồi flow_uploads xem count tăng
8. agent_mode bật rồi tắt
9. clip_reconcile cho <ID NHÁP>

Cuối cùng: liệt kê tool nào chạy đúng, tool nào sai hoặc báo lỗi, kèm nguyên văn lỗi.
```

## 5. Khi bạn quyết định chạy phần tiêu credit

1. Gọi `flow_credits()` và ghi lại số dư.
2. Gọi đúng **một** lần, ví dụ `gen_t2v(prompt="...", project="<ID NHÁP>", model="veo-lite", aspect="9:16")`.
3. Gọi lại `flow_credits()`.
4. Đối chiếu bằng chứng:
   - `out/ledger.jsonl`: job đó phải có dòng `submitted` rồi `done`, kèm `credits_before`, `credits_after`, `spent`.
   - `out/credits.jsonl`: hai dòng có giờ, khớp với số dư trước và sau.
5. **Không bao giờ bấm lại khi nghi ngờ.** Flow có thể nhận cú bấm, bắn request, rồi không tạo job và không trừ
   tiền. Kết luận thành hay bại bằng `flow_media` cộng số dư, đừng bằng cách gọi lại.

## 6. Bẫy đã đo, sẽ gặp khi bấm tay

- **Server MCP đang chạy không nạp code mới.** Sửa code xong phải kết nối lại.
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
