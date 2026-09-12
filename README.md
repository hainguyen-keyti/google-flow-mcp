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
uv run video flow tools <project>           # gallery Tools cộng đồng
uv run video flow characters <project>
uv run video flow download <project> <media_id> --out out/   # tài sản gốc (=s0, =m22), không ghi đè
uv run video flow upload <project> <file>
uv run video flow uploads <project>
uv run video flow project create --title T | rename <id> T | delete <id> --yes
uv run video flow character create <project> "<face prompt>" --name N --personality P | delete <project> <entity> --yes
uv run video flow scene create <project> --title T | delete <project> <scene_id> --yes
uv run video flow agent mode <project> on|off
uv run video flow agent send <project> "<message>"   # agent có thể tự sinh nội dung, tốn credit
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
18 tool: `flow_*`, `project_*`, `character_*` (miễn phí) và `gen_*` (tốn credit, cùng ledger). Không có
chế độ no-spend: chủ repo chốt agent được gọi mọi thứ.

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
- Giá đo được trên gói PRO: Veo 3.1 Lite 720p 8s = 10 credit, ảnh Nano Banana 2 = 0 credit.
