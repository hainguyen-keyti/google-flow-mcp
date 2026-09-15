# Video

Điều khiển Google Flow (host `flow.google.com`) từ terminal và từ agent: đọc project, media, credit, tạo và
xoá project, character, scene, upload media, sinh ảnh và video qua `gflow-cli`, có ledger cho mọi lần tiêu
credit, và một MCP server phơi toàn bộ lệnh cho Claude Code.

Đo thật trên tài khoản Google AI Pro đã di cư sang host mới (2026-09-12). Lane `labs.google` của gflow chết
trên tài khoản này; mọi thứ ở đây đi lane host mới (lái Chrome thật trên profile riêng của gflow, đọc
batchexecute).

## Cài

```
uv sync --group dev
uv run video --help
```

Cần: Chrome thật, profile gflow đã đăng nhập tại `~/Library/Application Support/gflow-cli/profile_default`
(marker `.gflow_browser_strategy` = `chrome`), ffmpeg/ffprobe trên PATH. Mọi lệnh đi qua `import video`,
tức bản vá bỏ Keychain pre-read của `browser_cookie3` và log gflow chuyển sang stderr.

## Lệnh

Đọc, không tốn credit:

```
uv run video flow lane                      # MIGRATED projects=16
uv run video flow projects [--json]
uv run video flow credits
uv run video flow media <project> [--json]  # media, model, dung lượng, URL
uv run video flow media <project> --all     # mọi record kể cả clip trong scene, ảnh nháp character (unlisted)
uv run video flow tools <project>           # gallery Tools cộng đồng
uv run video flow characters <project>
uv run video flow download <project> <media_id> --out out/   # tài sản gốc (=s0, =m22), không ghi đè
uv run video flow upload <project> <file>
uv run video flow uploads <project>
uv run video flow project create --title T | rename <id> T | delete <id> --yes
uv run video flow character create <project> "<face prompt>" --name N --personality P | delete <project> <entity> --yes
uv run video flow scene list <project> [--all] | create <project> --title T | delete <project> <scene_id> --yes
uv run video flow agent mode <project> on|off
uv run video flow clip download <project> <media_id> --quality gif|720p|1080p|4k --out out/   # 1080p, 4K = upscale
```

Lệnh trên clip editor và agent, tốn credit, cùng ledger `out/ledger.jsonl`:

```
uv run video flow clip extend <project> <media_id> "<prompt>" --out out    # Extend (Veo 3.1 Lite)
uv run video flow clip edit <project> <media_id> "<prompt>" --out out      # video-to-video, Omni 1.1 Flash
uv run video flow clip reconcile <project> --out out                      # $0, đóng sổ job editor mồ côi
uv run video flow agent send <project> "<message>"   # agent có thể tự sinh nội dung
```

Sinh nội dung, tốn credit (video) hoặc quota (ảnh), luôn cần `--project`:

```
uv run video gen t2v "<prompt>" --project <id> --model veo-lite --aspect 16:9 --out out
uv run video gen r2v "<prompt>" --ref anh.jpg --project <id> --model veo-lite
uv run video gen i2v anh.png "<prompt>" --project <id>          # Frames picker phía Flow đang hỏng, xem giới hạn
uv run video gen t2i "<prompt>" --project <id> --model nano2 --aspect 16:9
uv run video gen i2i "<prompt>" --ref anh.jpg --project <id>
```

Mỗi job ghi vào `out/ledger.jsonl`: dòng `submitted` được ghi TRƯỚC khi gflow chạy, dòng `done` hoặc
`failed` kèm credit trước và sau. Job id (`--job`) đã có dòng `submitted` thì bị từ chối, để retry không
bao giờ submit đôi.

## Video mẫu: nhân vật có sẵn, kịch bản thử đồ để bán đồ

Nhân vật lấy từ `assets/character/` (ảnh tham chiếu của chủ repo + bible json/md). Mặt được khoá bằng
**Character entity của Flow tạo từ chính ảnh đó** (trang New character có nút Upload, $0), rồi gắn vào từng
prompt qua picker Ingredients, nên cả chuỗi dùng chung một gương mặt.

```
uv run video story plan                      # 5 shot + prompt đã khoá nhân vật và căn phòng, $0
uv run video story run <project> --out out/story      # sinh clip, 10 credit mỗi shot, có ledger
uv run video story reconcile <project>                # đóng sổ shot kẹt bằng listing + số dư, $0
uv run video story build --out out/story              # ghép 9:16, chèn chữ chào hàng, contact sheet, $0
uv run python scripts/acceptance/story_tryon.py --out out/story
```

Ba chốt an toàn tiền, đều do trả giá mà có: composer nhớ trạng thái lần trước nên mỗi shot ghi lại đủ
mode + tỉ lệ + x1 và **đọc dòng giá, lệch là không bấm**; mỗi shot bấm đúng một lần rồi ở lại trang tới khi
request bay đi; job id cố định `tryon-01..05`, job đã xong thì bỏ qua, job kẹt `submitted` thì DỪNG cả run
cho tới khi `reconcile` chứng minh được bằng số dư là chưa tiêu gì.

Chữ chào hàng đập bằng ffmpeg chứ không nhờ Veo: sửa chữ là $0, sinh lại clip là 10 credit.

### Vòng 2: khoá phòng, khoá đồ, tay không hỏng

Chủ repo bác bản đầu: máy quay giật, mất tay lúc chuyển động, và **món đồ đổi giữa các clip**. Nguyên nhân
gốc là chữ không khoá được đồ vật, chỉ gương mặt mới có entity. Đường đã đo:

- `hook` và `reveal` đi bằng **character chip** (đúng mặt); `fabric` đi bằng **r2v với ảnh món đồ** (không
  có người trong khung nên vải thắng); `wardrobe`, `pose`, `closing` đi bằng **Frames**, ghim khung cuối của
  cảnh trước làm khung đầu, nên thừa hưởng nguyên phòng, ánh sáng và tư thế.
- **Khung đầu luôn lấy từ bản CHƯA edit** (cô ấy mặc đồ thường): Flow nhận cú bấm rồi không tạo job và
  không trừ tiền khi khung đầu là ảnh cô ấy đang mặc bộ đồ (đo 2026-09-13). Clip editor cũng không phải
  lối vòng: nó mở bản trước edit và panel extend không hiện ra.
- Vì thế **mỗi cảnh khoe đồ trả một Omni edit riêng** (20 credit): `reveal`, `pose`, `closing`. Prompt
  edit giống hệt nhau nên bộ đồ tả một lần duy nhất, không tả lại trong prompt từng cảnh.
- Cảnh nào tay chạm vải thì sinh **2 take**, người soi strip rồi chọn, lý do loại ghi thẳng vào ledger.

```
uv run video story plan2                                  # 6 cảnh + route của từng cảnh, $0
uv run video story run2 <project> --out out/story2         # sinh chuỗi, 8 take + 3 edit = 140 credit
uv run video story pick --key pose --take tryon2-05b --reason "take a dính ngón"   # $0
uv run video story cut2 --out out/story2                   # loudnorm, xfade 0,5 s, chữ, strip + sheet, $0
uv run python scripts/acceptance/story_quality.py --out out/story2
```

Acceptance vòng 2 đo cả **khuyết điểm chủ repo nêu**, không chỉ ống nước: LUFS từng clip và **độ lệch max
-min** (tiếng nhảy ở mỗi mối nối), thời lượng bản cuối đúng công thức xfade (`tổng - (n-1) x fade`), mọi
cảnh hai take phải có dòng chọn kèm lý do, và không cảnh nào được nối tiếp từ take bị loại. Hàng cuối là
**cổng người**: thiếu `out/story2/review.json` với `{"verdict": "pass"}` là FAIL, vì máy không chấm được
mặt, tay và món đồ.

## MCP

`.mcp.json` của repo cắm server vào Claude Code (`uv run --project <repo> --no-sync video mcp run`). Server
đang chạy không tự nạp code mới, và phiên đang mở còn giữ mô tả tool cũ: sửa code xong phải mở phiên mới.

29 tool, chia theo đúng mô tả chi phí của từng tool (mô tả nào cũng nêu giá: "Free.", con số credit, hoặc
"unmeasured"):

- **Tốn credit**, ghi ledger (`out/ledger.jsonl` mặc định): `gen_t2v`, `gen_i2v`, `gen_r2v`, `clip_extend`,
  `clip_edit`, `agent_send` (có thể tốn). Cả 6 tool **bắt buộc `job_id`**: job mới thì id mới, gọi lại cùng job
  thì giữ id. `job_id` đã có BẤT KỲ dòng nào trong sổ (kể cả `opening`, ở sổ mặc định lẫn sổ trong `out_dir`) bị từ
  chối trước khi mở trình duyệt, kèm lời dặn soát `flow_media` và `flow_credits`, nên gọi lại không bao giờ trả tiền
  hai lần. `clip_edit`, `clip_extend`, `agent_send` từ chối prompt có ký tự xuống dòng, vì ô của Flow nhận nó như
  phím Enter trước khi sổ kịp ghi dòng `submitted`.
- **Model mặc định qua MCP**: bỏ trống model thì `gen_t2v` và `gen_i2v` dùng `omni-flash` 10 s, còn `gen_r2v` dùng
  `omni-flash` 8 s, độ dài duy nhất host này cho r2v (gflow tự ghim; truyền độ dài khác bị từ chối). Model veo thì
  duration để Flow tự chọn (veo tối đa 8 s). Model và duration được kiểm theo đúng luật của gflow TRƯỚC khi ghi sổ,
  để một giá trị gflow sẽ từ chối không đốt mất `job_id`. CLI `video gen` giữ nguyên.
- **Miễn credit nhưng tính quota ảnh theo ngày**: `gen_t2i`, `gen_i2i`.
- **Miễn phí, chỉ đọc Flow**: `flow_lane`, `flow_projects`, `flow_credits`, `flow_media` (luôn trả một object,
  `all_versions=true` thêm khoá `versions`), `flow_characters`, `flow_tools` (`project_id` tuỳ chọn, bỏ trống thì
  tự mở project đầu tiên trên grid), `flow_uploads`, `scene_list`, `flow_download` (ghi file vào thư mục đích),
  `clip_reconcile` (đọc listing và số dư, ghi ledger, không sinh gì; trả kèm đường dẫn sổ đã đọc, sổ có tồn tại
  không và số dòng, để `jobs: []` không bị hiểu nhầm là sạch khi đọc nhầm chỗ).
- **Miễn phí nhưng ĐỔI project thật**: `project_create`, `project_rename` (đọc lại tên trên grid rồi mới trả
  `{id, title}`), `project_delete`, `character_create`, `character_delete`, `scene_create`, `scene_delete`,
  `scene_restore` (lấy scene ra khỏi thùng rác, từ chối khi tên khớp hơn một tile), `agent_mode`, `flow_upload`.
- **`clip_download`**: bản 1080p đã đo là $0; bản `4k` do Flow upscale thì **chưa đo giá, có thể tốn credit**, phải
  hỏi chủ repo trước khi dùng (gflow ghi 4K upscale là tier-gated).

Không có chế độ no-spend: chủ repo chốt agent được gọi mọi thứ. Chuỗi `instructions` mà server gửi cho agent lúc
`initialize` nêu đích danh nhóm tốn credit, thời gian chờ và luật giữ `job_id`, và có test canh để nó không lệch
khỏi mô tả của từng tool. Mỗi lần đọc số dư (CLI, MCP, job sinh, clip editor) ghi thêm một dòng kèm giờ vào
`out/credits.jsonl`. Bị Google gắn cờ hoạt động bất thường (WAF, gflow exit 10) thì dừng hẳn: không thử lại,
không đăng nhập lại, báo chủ repo.

mcp 2.2.0 giấu lời của mọi exception không phải `ToolError`, agent chỉ thấy `Error executing tool <tên>` (đo
2026-09-15, cả lời dặn WAF lẫn lời từ chối `job_id`). Server của repo chuyển lỗi thành `ToolError` kèm lý do: cắt
bỏ phần "Call log" của Playwright (nó liệt kê header, có cả cookie), rồi lọc bằng regex của ledger cộng
`redact_error_detail` của gflow (SID, HSID, SSID, APISID, Bearer, URL có chữ ký, email), tối đa 500 ký tự.

## Acceptance

```
uv run python scripts/acceptance/flow_coverage.py --project <id> [--character] [--spend --ref-image anh.jpg]
uv run python scripts/acceptance/mcp_smoke.py
uv run python scripts/acceptance/ledger_integrity.py     # $0, offline, không cần trình duyệt
uv run pytest -q
```

Test tay toàn bộ 29 tool qua MCP, kèm giá từng tool, rào chắn và prompt sẵn để giao cho một agent khác:
`docs/mcp-manual-test.md`.

`ledger_integrity.py` canh đúng một luật: **không credit nào rời tài khoản qua clip editor mà không có
dòng ledger trỏ tới nó**. Nó lái editor bằng stub hỏng đúng chỗ đã hỏng thật ngày 2026-09-13, lúc 20
credit bay mất mà sổ rỗng nên phải đi truy listing của Flow bằng tay. Sáu hàng: hai hàng ghim dòng ý định
còn lại khi extend chết ở menu và khi edit chết lúc mở editor, một hàng ghim dòng đó không chặn chạy lại,
hai hàng ghim reconcile kết luận đúng bằng listing cộng số dư, và một hàng chống rò session.

## Canary: biết trước khi Google đổi UI

```
uv run python -m video.probes.canary --project <id>     # $0, exit 1 khi mỏ neo biến mất
```

Test của repo dùng fixture nên **xanh cả khi Flow đã đổi sạch giao diện**. Canary là chỗ bù: nó mở Flow
thật, đếm đúng 11 mỏ neo mà driver đang bám vào, rồi exit khác 0 khi thiếu một cái. Nó **dùng chung hằng
số với driver** chứ không chép lại, nên không thể xanh trong khi driver hỏng: dòng giá chấm bằng chính
`composer.PRICE_RE`, shell trang bằng `session.PROJECT_READY`, trang editor bằng `clips.EDITOR`.

Thứ mới xuất hiện trên trang không bao giờ tính là trôi, chỉ thứ mình với tay tới mà mất mới tính. Mỗi
hàng đỏ in kèm câu "hỏng cái này thì mất gì", ví dụ mất dòng giá là mất luôn chốt chặn chi tiêu. Toàn bộ
quan sát ghi ra `out/canary_<thời-gian>.json` để so bằng mắt khi cần. Chạy nó trước mỗi batch.

## Giới hạn đã đo (2026-09-12)

- `gflow auth login`, `credits`, `character list` của gflow chết trên tài khoản di cư (lane labs). Repo này
  không dùng chúng.
- `gen i2v` (Frames picker) hỏng 4/4 lần tối 2026-09-12 phía Flow ("frame picker stayed open 15s"),
  không mất credit khi hỏng. Dùng `gen r2v --ref` cho ảnh tham chiếu.
- URL trong listing là poster; tải tài sản gốc bằng hậu tố lh3 (`=s0` ảnh, `=m22` rồi `=m18` video).
- Agent mode bật là Flow lưu theo project và ẩn chip settings của composer; tắt lại bằng
  `flow agent mode <project> off`.
- `clip extend` tạo một scene mới (rpc `rqZuUc`) và clip mở rộng (Veo 3.1 Lite, 7 s, 720p) là record trong
  listing KHÔNG có tile trên grid; lệnh trả `scene_id` và `outputs[].media_id`, `flow download` tải được
  mọi record kể cả loại này. Mở scene trong Flow để xem bản ghép; "Download scene" xuất bản ghép.
- `clip edit` (Omni 1.1 Flash, 20 credit cho clip 8 s) và upscale 1080p (0 credit) không tạo media mới: chúng
  thêm một **version** (record loại `CAI`) trên cùng media id, thấy ở "Show history". `flow media --all` liệt
  kê từng version với `workflow_id`; `flow download <media>` lấy version mới nhất đã xong; `clip edit` lưu
  file là `<media>_<workflow8>.mp4`.
- Xoá scene là "Move to trash" trên tile của grid (rpc `BpMsoe`): scene vẫn nằm trong listing với cờ
  trashed và hiện ở view Trash; `scene list` mặc định ẩn scene đã trash, `--all` để thấy. Nút "Move to
  trash" bên trong editor scene không làm gì (đo 2 lần).
- View Trash mở thẳng được ở `/project/<id>/trash`. Hover tile scene đã xoá hiện "Restore" và "Delete
  permanently"; "Restore" bắn `BpMsoe`, không hỏi xác nhận. Tile trong thùng rác **không mang scene id** nên
  `scene_restore` chỉ tìm được theo tên (đo 2026-09-15).
- Giá đo được trên gói PRO: Veo 3.1 Lite 720p 8s = 10 credit, `clip extend` (7 s) = 10, `clip edit` Omni
  1.1 Flash = 20, Omni Flash 10 s = 15, `--count 2` = 20, ảnh Nano Banana 2 = 0, upscale 1080p = 0, r2v Omni
  Flash 8 s = 12 (đo 2026-09-15, 292 s đầu cuối qua MCP). `gen i2v` chưa thành công lần nào nên chưa có giá.
- Thao tác tốn credit chỉ bấm "Start generation" ĐÚNG MỘT LẦN. Bấm lại khi tưởng cú trước hụt là cách
  nhanh nhất để trả tiền hai lần: đo 2026-09-13, cú thứ hai rơi vào ô prompt thường và cộng thêm một
  Omni edit 20 credit lên trên extend 10 credit (hoá đơn 30). Thành hay bại đọc ở listing và số dư.
