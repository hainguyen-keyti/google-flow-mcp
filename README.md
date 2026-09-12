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

## MCP

`.mcp.json` của repo cắm server vào Claude Code (`uv run --project <repo> --no-sync video mcp run`).
26 tool: `flow_*`, `project_*`, `character_*`, `scene_*`, `agent_mode`, `clip_download` (miễn phí) và
`gen_*`, `clip_extend`, `clip_edit`, `agent_send` (tốn credit, cùng ledger). Không có chế độ no-spend:
chủ repo chốt agent được gọi mọi thứ.

## Acceptance

```
uv run python scripts/acceptance/flow_coverage.py --project <id> [--character] [--spend --ref-image anh.jpg]
uv run python scripts/acceptance/mcp_smoke.py
uv run pytest -q
```

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
- Giá đo được trên gói PRO: Veo 3.1 Lite 720p 8s = 10 credit, ảnh Nano Banana 2 = 0 credit.
