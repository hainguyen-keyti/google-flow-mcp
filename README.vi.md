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
uv run video flow download <project> <media_id> --out out/   # tài sản gốc (=s0, =m22), không ghi đè; qua MCP thì out_dir phải nằm trong out/
uv run video flow upload <project> <file>
uv run video flow uploads <project>
uv run video flow project create --title T | rename <id> T | delete <id> --yes
uv run video flow character create <project> "<face prompt>" --name N --personality P | delete <project> <entity> --yes
uv run video flow scene list <project> [--all] | create <project> --title T | delete <project> <scene_id> --yes
uv run video flow agent mode <project> on|off
uv run video flow clip download <project> <media_id> --quality gif|720p|1080p|4k --out out/   # 1080p, 4K = upscale; clip 360p: 720p = upscale, không có 1080p
```

Lệnh trên clip editor và agent, tốn credit, cùng ledger `out/ledger.jsonl`:

```
uv run video flow clip extend <project> <media_id> "<prompt>" --out out    # Extend (Veo 3.1 Lite)
uv run video flow clip edit <project> <media_id> "<prompt>" --out out      # video-to-video, Omni 1.1 Flash
uv run video flow clip reconcile <project> --out out                      # $0, đóng sổ job editor mồ côi
uv run video flow agent send <project> "<message>"   # agent có thể tự sinh nội dung
```

Prompt của `clip extend|edit` và message của `agent send` phải một dòng: ô của Flow nhận ký tự xuống dòng như phím
Enter, và việc gõ diễn ra trước khi sổ ghi `submitted`. Cả CLI (exit 2, chưa mở Chrome) lẫn driver đều từ chối.

`clip reconcile` chỉ chấm job editor của đúng project đang đọc. Dòng `opening` của job ghi `project`,
`workflows_before` (mọi workflow id listing có ngay trước khi job mở) và `prompt` đã gõ; "record mới" là record có
workflow id ngoài tập đó, không so giờ, vì đồng hồ của Flow với máy này lệch nhau và `created` của clip đóng dấu lúc
submit.
- `done` chỉ cho job edit, khi đủ cả ba điều:
  - trên chính clip nguồn có đúng một version mới mang đúng prompt của job (so như driver, sau khi bỏ khoảng trắng hai
    đầu) mà chưa job nào khác trong CÙNG sổ giữ làm output `generated`; version còn đang sinh cũng được đếm;
  - version đó đã xong;
  - trong cùng sổ không còn job đối thủ, tức job khác từng ghi cùng clip cùng prompt mà vẫn còn mở (kể cả khi dòng
    `pending` của nó đã giữ một version), hoặc đã đóng mà không giữ output `generated` nào trên clip đó. Lý do: driver
    đóng một lần retry là `failed` khi version của nó hiện ra sau hạn chờ, nên version mới có thể là của lần retry ấy.
    Ngoại lệ: job có dòng cuối là `failed` do chính `clip_reconcile` ghi (số dư bằng, không record mới) không phải đối
    thủ, vì verdict đó đã cho thấy nó không làm ra gì.

  Record đó được ghi vào `outputs` của dòng `done`, nên không job nào nhận lại được. Bản upscale 1080p (workflow
  `..._upsampled`, prompt rỗng, đo 2026-09-15) và bản chép do extend tạo (media id mới) không bao giờ bị nhận.
- Job extend không bao giờ ra `done`: clip mới nằm trên media id mới, cạnh bản chép của mọi version clip nguồn, không có
  gì buộc nó vào job. Nó vẫn ra `failed` theo luật dưới đây; các ca còn lại để người đóng sổ.
- `failed` khi số dư không đổi VÀ project không có record mới nào, kể cả record đã bị job khác nhận (số dư đã được đo là
  tự đổi mà không tiêu gì).
- `unknown` cho mọi ca còn lại, để người quyết; kể cả khi version của job còn đang sinh, khi có hai version, khi còn job
  đối thủ như trên, khi listing không còn record nào của clip nguồn mà job đã thấy, và với dòng `opening` ghi trước khi
  có `workflows_before` hay `prompt`, hoặc có tập workflow rỗng.
- `skipped` cho job gen và agent, và cho job editor của project khác (kèm `project`). Hai loại này không bao giờ bị ghi.

Sổ không có job nào chấm được thì lệnh trả lời ngay, không mở Chrome.

`spent` trên dòng do reconcile ghi là độ lệch số dư từ lúc job mở tới lúc reconcile, không phải giá của job: mọi khoản
chi khác trong khoảng đó, và cả những lần số dư tự đổi mà không tiêu, đều bị tính vào, nên hai job đóng cùng một lượt
đều ghi trọn độ lệch. Đừng cộng các dòng đó làm tổng chi (DECISIONS 2026-09-16).

Sinh nội dung, tốn credit (video) hoặc quota (ảnh), luôn cần `--project`:

```
uv run video gen t2v "<prompt>" --project <id> --model veo-lite --aspect 16:9 --out out
uv run video gen r2v "<prompt>" --ref anh.jpg --project <id> --model veo-lite
uv run video gen i2v anh.png "<prompt>" --project <id> --model omni-flash --duration 10 --aspect 9:16   # 15 credit
uv run video gen i2v anh.png "<prompt>" --project <id> --aspect 9:16                      # bỏ trống model: gflow dùng veo-lite
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

49 tool, chia theo đúng mô tả chi phí của từng tool (mô tả nào cũng nêu giá: "Free.", con số credit, hoặc
"unmeasured"); bảng đầy đủ sinh từ server ở `docs/tools.md`:

- **Tốn credit**, ghi ledger (`out/ledger.jsonl` mặc định): `gen_t2v`, `gen_i2v`, `gen_r2v`, `clip_extend`,
  `clip_edit`, `agent_send` (có thể tốn). **Cả 6 đã chạy thật qua MCP ngày 2026-09-16**, mỗi tool đúng một lần, số dư
  kẹp hai đầu: t2v omni-flash 15, t2v veo-lite 10, `clip_edit` 20, `clip_extend` 10, `gen_r2v` 12; `agent_send` một
  tin nhắn thường **0**; `gen_i2v` khi đó vẫn hỏng ở bước chọn khung đầu (**0** credit, dòng sổ `failed`); nó chạy được lần đầu ngày 2026-09-18 sau khi nâng gflow lên 0.78.0, **15** credit.
  **`clip_extend` chỉ chạy trên clip Veo**: trên clip omni-flash, Flow hiện mục `Extend (Veo 3.1 - Lite)` xám và tool
  nói thẳng điều đó thay vì chờ hết giờ. Cả 6 tool **bắt buộc `job_id`**: job mới thì id mới, gọi lại cùng job
  thì giữ id. Trước khi mở trình duyệt, MCP từ chối `job_id` đã có BẤT KỲ dòng nào (kể cả `opening`) trong mọi file
  `ledger.jsonl` dưới `out/`, kèm lời dặn soát `flow_media` và `flow_credits`, nên gọi lại không bao giờ trả tiền hai
  lần. `job_id` đang chạy ở một lời gọi khác cũng bị từ chối, với lời dặn khác hẳn: chờ lời gọi đó xong rồi gọi lại
  với CÙNG `job_id`, không bao giờ đổi id mới (lúc job còn đang bay, `flow_media` và `flow_credits` chưa thấy gì).
  **Mỗi lượt trả tiền đều nói nó ra sao**: câu trả lời mang `outcome {code, charged, retryable, advice}`, câu lỗi mở
  đầu bằng `outcome code=... charged=... retryable=...`, đọc từ chính dòng sổ của job: `charged` là số credit mà hai
  đầu số dư cho thấy đã rời tài khoản, `UNKNOWN` và `CHARGED_NO_OUTPUT` nghĩa là tiền có thể đã mất, còn lỗi không có
  dòng outcome là bị từ chối trước khi job nào bắt đầu. **Một kiểu chạy lại được server bảo lãnh**: khi `gen_video`
  hay `gen_character` báo `retryable=yes` (Flow làm hỏng job mà không trừ tiền, vì bộ lọc âm thanh hoặc không lý do),
  gọi lại với cùng project và prompt, `job_id` MỚI và `retry_of` là id của job bị từ chối. Sổ được kiểm trước khi mở
  trình duyệt: job đó phải hỏng mà không tốn, cùng project và prompt (các thiết lập khác không được so), mỗi job chỉ
  được chạy lại một lần (lần thứ hai bị từ chối khi lần đầu đã bấm, và trong lúc lần đầu còn chạy ở cùng server), tối
  đa hai lần cho một job gốc. Mọi trường hợp khác, đổi `job_id` sau lỗi vẫn là sai.
  `job_id` mang chữ giống bí mật phiên (tên cookie hay header `Authorization`) bị từ chối trước mọi thứ, vì sổ sẽ lưu nó
  thành một id khác và không bao giờ tìm lại được; `Ledger.append` cũng từ chối id như vậy ở mọi đường ghi sổ (CLI,
  story, driver), và mỗi chuỗi trong dòng sổ được lọc riêng nên dòng luôn là JSON đọc được. `clip_extend` và
  `clip_edit` chỉ nhận `out_dir` là thư mục nằm trong `out/`: không thành phần nào tên `ledger.jsonl` (hoa hay thường),
  không thành phần nào đã có sẵn là file. Sổ dưới `out/` được soát cả khi tên file viết hoa. Prompt hay message toàn dấu
  cách bị từ chối; `clip_edit`, `clip_extend`, `agent_send` từ chối prompt có ký tự xuống dòng, vì ô của Flow nhận nó
  như phím Enter trước khi sổ kịp ghi dòng `submitted`.
- **Model mặc định qua MCP**: bỏ trống model thì `gen_t2v` và `gen_i2v` dùng `omni-flash` 10 s, còn `gen_r2v` dùng
  `omni-flash` 8 s, độ dài duy nhất host này cho r2v. `gen_r2v` không bao giờ gửi `--duration` cho gflow, kể cả khi
  truyền 8: gflow tự ghim 8 s, còn độ dài gửi tường minh làm gflow exit 11 ở cohort không có hàng duration; độ dài
  khác 8 bị từ chối. Số ảnh tham chiếu của `gen_r2v` theo `reference_cap_for` của gflow: omni-flash tối đa 7,
  veo-lite, veo-fast, veo-lite-lp tối đa 3, veo-quality không nhận ảnh. Model veo thì duration để Flow tự chọn (veo
  tối đa 8 s). Model, duration, số ảnh được kiểm theo đúng luật của gflow TRƯỚC khi ghi sổ, để một giá trị gflow sẽ
  từ chối không đốt mất `job_id`. CLI `video gen` giữ nguyên.
- **Nhân vật trong video, `gen_character`** (tốn credit, ghi ledger, bắt buộc `job_id` ở lượt thật): video 8 s x1 có
  nhân vật của project (entity id từ `flow_characters`) và tuỳ chọn ảnh đã có trong project (media id từ `flow_media`,
  chỉ ảnh). gflow vẫn từ chối nhân vật trên host này, nên repo tự gõ `@` cộng tên nhân vật vào ô prompt rồi bấm ĐÚNG
  option theo tên và loại, không bao giờ nhấn Enter; còn ảnh thì gắn qua hộp "+" của composer, chọn đúng hàng theo đuôi
  url của ảnh (nên hai ảnh trùng tên vẫn dùng được), và trước khi bấm tiền tool đọc lại từng chip trên thanh ingredient:
  chip ảnh phải mang đúng workflow id, chip bị Flow làm xám thì dừng kèm nguyên lời của Flow (đo 2026-10-01: trên Veo 3.1
  Lite nhân vật chiếm một trong ba suất ảnh). Giá đo bằng tiền thật qua MCP ngày 2026-09-17:
  omni-flash (mặc định) **12**, veo-lite **10**; veo-fast **20** theo dòng giá của Flow, chưa tiêu. `dry_run=true` trả
  giá và chip, không bấm, không ghi sổ, không cần `job_id`, để composer trống. `out_dir` (tuỳ chọn, phải nằm trong
  `out/`) đặt clip và sổ của lượt đó vào thư mục riêng, để các take của một phim nằm cùng chỗ; `job_id` vẫn bị từ chối
  nếu BẤT KỲ `ledger.jsonl` nào dưới `out/` đã có nó. Ngay trước kiểm giá, tool đọc lại chip
  và chữ ô prompt: chip phải đúng id đã yêu cầu, ô phải đúng bằng tên các chip rồi prompt, lệch là từ chối ($0). Body
  của submit được kiểm có khoá `_r2v_` và mọi id tham chiếu (`body_check`). Sau cú bấm tool chỉ nhận clip mới có prompt
  BẰNG đúng chữ đã gửi (Flow lưu tên chip rồi chữ gõ); không nhận được mà có video mới hay số dư đổi thì ghi `unknown`,
  clip có mà chưa tải được thì `pending`, và câu lỗi mở đầu bằng lời dặn không chạy lại dưới `job_id` mới. Tool nghe
  phản hồi của chính Flow về job (khoá `flow` của dòng sổ): trạng thái 6 đã gửi, 2 đang chạy, 3 xong, **4 hỏng**, kèm
  mã lý do. **Bộ lọc người nổi tiếng của Flow** (`PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED`) đã chặn nhân vật tạo từ
  ảnh người thật, không tính tiền, lúc chặn lúc không: không thử lại cùng đầu vào cho tới khi lọt. File clip đặt tên
  `<media_id>_<8 hex>.mp4`, không bao giờ theo `job_id`.
- **Mọi option video của composer, `gen_video`** (tốn credit, ghi ledger): model, 360p hay 720p, 4 tới 10 s, x1 tới
  x4, khung đầu và khung cuối, hoặc Ingredients gồm nhân vật, ảnh và **giọng** (`voices`: tên giọng như `flow_voices`
  liệt kê, preset hay giọng tự tạo). Rào tiền là dòng giá sống của Flow so với `max_credits`. Giọng phải đi kèm ít nhất
  một ảnh hay nhân vật; số giọng mỗi model nhận, đo 2026-10-01: omni-flash 5, veo-lite và veo-fast 1 (nhân vật đã có
  giọng chiếm luôn suất đó), veo-quality không nhận. Trước cú bấm tool đọc lại từng chip (tên giọng lấy từ thẻ hover),
  lúc request đi ra tool kiểm chính trường giọng của nó, và sau lượt trả tiền tool đọc lại recipe của clip: Flow bỏ mất
  giọng hay ảnh thì báo lỗi dù đã trả tiền. Đo bằng tiền thật 2026-10-01: Veo Lite + ảnh + giọng tự tạo **10**, Omni 8 s
  + ảnh + hai giọng **12**.
- **Nộp rồi quay lại lấy, vẫn đúng một cú bấm: `job_submit`, `job_status`, `job_collect`.** `job_submit` là chính lượt
  chạy của `gen_video` (cùng các phép kiểm, cùng rào `job_id`, cùng một cú bấm duy nhất, mỗi lần một clip, không có
  `dry_run`), nhưng trả lời ngay khi THẤY request submit của Flow rời đi, với `outcome STARTED`, thay vì chờ render.
  `job_status` đọc job đang ở đâu (`rendering`, `ready`, `not_listed`, `ambiguous`, `settled`, `in_another_call`) và
  không ghi gì; `job_collect` tải clip đã xong về cạnh sổ của job và ghi ĐÚNG MỘT dòng quyết toán, job đã quyết toán thì
  trả lời từ dòng đó, không mở trình duyệt. Hai tool này không bấm và không gõ được gì. Clip chỉ được nhận theo
  workflow id do chính reply submit của Flow nêu; không nghe được reply thì nhận clip mới DUY NHẤT mang đúng prompt, có
  hai ứng viên thì không chọn. `spent` chỉ là hiệu số dư của riêng job khi không job nào khác có thể đã làm số dư đổi
  trong lúc đó; còn lại ghi theo giá Flow báo trước cú bấm, và dòng sổ nói rõ là cái nào (`spent_from`, kèm
  `balance_shared` trên dòng sổ và `charged_from` trong outcome). Job Ingredients được đọc lại recipe lúc thu, như
  `gen_video`. Cái mất khi không chờ: lý do Flow từ chối chỉ trang đã submit nghe được, nên job bị từ chối sau khi lời
  gọi đã rời đi sẽ không bao giờ hiện, sau 10 phút được quyết toán là không sinh ra gì, và không nhận `retry_of`; cần
  biết lý do thì dùng `gen_video` (lời từ chối nghe được khi trang còn mở thì được ghi ngay tại đó, có mã, và
  `job_submit` nhận `retry_of` cho nó như `gen_video`). Trong lúc clip của một job đã nộp còn có thể hiện ra (từ lúc
  gọi `job_submit` tới khi thu xong, hoặc một giờ sau khi nó được quyết toán mà không có clip), `gen_video` và
  `gen_character` bị từ chối với cùng project và prompt: tool chặn nhận clip mới duy nhất mang prompt của nó, mà clip
  đó có thể là của job đã nộp. Một tool chặn chạy cạnh job còn render cũng đọc số tiêu của mình trên số dư mà job kia
  còn có thể làm đổi. **Đo bằng tiền thật 2026-10-03** (hai job Omni 360p 4 s nộp liền nhau, 4 credit
  mỗi job): mỗi `job_submit` trả lời sau **153 s** kèm workflow id do Flow nêu, số dư đã trừ ngay sau lúc nộp (không
  phải lúc xong), hai clip render cùng lúc sau khi trang đã đóng và sẵn sàng trong khoảng 4 phút; `job_status` và
  `job_collect` mỗi lượt khoảng 47 s; mỗi job thu đúng clip của mình; thu lần hai trả lời ngay từ dòng sổ; nộp lại id
  cũ bị từ chối kèm `outcome code=DONE charged=4`.
- **Miễn credit nhưng tính quota ảnh theo ngày**: `gen_t2i`, `gen_i2i`.
- **Miễn phí, chỉ đọc Flow**: `flow_lane`, `flow_projects`, `flow_credits`, `flow_capabilities` (giá từng ô, độ dài, trần
  ảnh và giọng của từng model thành DỮ LIỆU, đọc từ chính bảng mà tool dùng để từ chối và tính tiền; không mở trình
  duyệt, không phải số đọc sống: giá sống của một ô là `gen_video` với `dry_run=true`), `flow_media` (luôn trả một object,
  `all_versions=true` thêm khoá `versions`; bốn bộ lọc `kind` là `video` hay `image`, `since` nhận epoch hoặc ngày
  ISO, `limit` giữ N dòng mới nhất của từng list và xếp mới trước, `brief=true` bỏ `url` và cắt `prompt` còn 120 ký
  tự. Không lọc thì thứ tự là thứ tự listing của Flow, KHÔNG sắp theo tuổi: đọc `created`. Có lọc thì kết quả mang
  thêm `media_total`, `truncated` (và `versions_total` khi `all_versions=true`), trong đó `truncated` đếm dòng bị
  bỏ chứ không đếm trường mà `brief` cắt; lọc sai giá trị bị từ chối trước khi mở browser. Đo 2026-09-17 trên project nháp:
  không lọc 28.378 ký tự, `all_versions=true` 122.919, trong đó `url` 28.205 và `prompt` 24.607),
  `flow_characters`, `flow_tools` (`project_id` tuỳ chọn, bỏ trống thì
  tự mở project đầu tiên trên grid), `flow_uploads`, `scene_list`, `scene_clips` (timeline của một scene),
  `flow_download` (ghi file vào thư mục đích),
  `clip_recipe` (đọc lại từ listing một clip được tạo từ gì: model, khung đầu và khung cuối, ảnh tham chiếu, giọng,
  nhân vật, và clip nguồn của một lượt edit hay extend, mỗi thứ kèm id và tên),
  `job_status` (job do `job_submit` nộp đang ở đâu; chỉ đọc listing và số dư), `job_collect` (tải clip của job đó về
  và ghi dòng quyết toán vào sổ; không bấm gì trên Flow),
  `clip_reconcile` (đọc listing và số dư, ghi ledger, không sinh gì; trả kèm đường dẫn sổ đã đọc, sổ có tồn tại
  không và số dòng, để `jobs: []` không bị hiểu nhầm là sạch khi đọc nhầm chỗ; job gen, job agent và job editor của
  project khác ra `skipped`; sổ không có gì để chấm thì trả ngay, không mở Chrome).
- **Miễn phí nhưng ĐỔI project thật**: `project_create`, `project_rename` (đọc lại tên trên grid rồi mới trả
  `{id, title}`), `project_delete`, `character_create` (nhận đúng một trong hai: `prompt`, hay `image` là file ảnh trên
  máy tải qua nút Upload của trang New character; Flow từng im lặng từ chối ảnh người mặc đồ ren), `character_delete`,
  `scene_create`, `scene_delete`,
  `scene_restore` (lấy scene ra khỏi thùng rác). Tile scene trên grid lẫn trong thùng rác không mang scene id (đo
  2026-09-16), nên cả hai tool tìm tile theo tên đúng nguyên. Grid và thùng rác nằm trong một
  `div.cdk-virtual-scrollable` chỉ vẽ một cửa sổ tile (đo 2026-09-17: 7/8 tile ở grid, 16/37 ở thùng rác), nên tool
  QUÉT CUỘN: kéo tile cuối cùng đang vẽ vào tầm nhìn tới khi không còn cửa sổ mới, trần 60 nấc. Tính duy nhất của
  tên lấy từ LISTING (nơi biết mọi scene), không lấy từ số tile đang vẽ. Từ chối thay vì đoán khi: tên rỗng hay chỉ
  gồm ký tự vô hình sau chuẩn hoá của Playwright, listing có hơn một scene cùng tên ở cùng trạng thái, trang hiện hai
  tile cùng khớp chính xác, hay quét hết mà không thấy tile nào. Bấm xong, cả hai tool đọc lại listing và gọi tên
  scene khác nếu cờ thùng rác của nó đổi.
  Cùng nhóm: `agent_mode`, `flow_upload`, và các tool Scenebuilder dưới đây.
- **Dựng phim trong chính Flow** (miễn phí, đo lại 2026-09-17 bằng `src/video/probes/scene_editor.py`):
  - `scene_clips` đọc timeline từ listing (`Zzl0ze[6]`, nơi Flow giữ clip của mọi scene kèm chỉ số): clip theo đúng
    thứ tự phim chạy, mỗi clip có `position` (đếm từ 0), `clip_id`, `title`, `seconds`, cộng tỉ lệ khung và tổng giây.
    Thứ tự này khớp phim tải về từng đoạn. Nhãn `Total duration` của trang đổi TRƯỚC khi Flow lưu nên không còn là
    bằng chứng: đọc lại `scene_clips` sau mỗi thay đổi.
  - `scene_add_clip` luôn đặt clip vào **CUỐI** timeline: Flow chèn clip mới ngay sau clip đang chọn và trang vừa tải
    chọn clip 0, nên tool chọn clip cuối trước; bấm hàng picker chỉ chọn, bấm lại hàng đang chọn là thêm luôn, nên tool
    không bao giờ bấm hàng đã chọn sẵn và bấm "Add media" đúng một lần. Flow chỉ lưu khi trả lời request `oWTRd`
    (11 tới 14 s sau cú bấm), nên tool kiểm request (đúng media, đúng chỉ số cuối), chờ reply rồi đọc lại listing;
    kết quả có `position`, `clip_id`, `clips`. Lỗi sau cú bấm luôn dặn đọc `scene_clips` trước khi thêm lại. Picker
    không mang media id, chỉ có **title**, nên title rỗng, trùng media khác hay hiện hai hàng đều bị từ chối.
  - `scene_set_aspect` đặt 9:16 hay 16:9 (scene mới là 16:9); tỉ lệ đúng rồi thì không bấm.
  - `scene_move_clip` (kéo thả, zoom out tới khi hai chỗ cùng hiện) và `scene_remove_clip` (menu chuột phải, Flow
    không hỏi) gọi clip bằng `clip_id`, rồi kiểm thứ tự trang gửi (`GoMJte`) và listing đọc lại.
  - `scene_download` tải **cả scene thành MỘT phim**. Phim được dựng ngay trong trang (spinner, snackbar "Exporting
    your scene…"; phim 40 s mất 33 tới 42 s), nên tool chờ theo độ dài phim, báo ngay khi cú bấm không khởi động xuất,
    và chép file sang tên tạm rồi mới đổi tên: không bao giờ có phim dở dưới tên trả về (lượt hỏng của bài test
    dancer từng để lại phim đúng 18 MiB). Kết quả có `seconds` và `clips` của listing để đối chiếu, `attempts: 2` là
    lần đầu hỏng giữa đường và đã lấy lại trong phiên mới.
  - `scene_rename` đổi tên. Trang scene không có menu chất lượng, muốn chọn mức thì dùng `clip_download`.
- **Giọng nói của nhân vật** (miễn phí, đo 2026-09-18 bằng `src/video/probes/voice_and_frames.py`): giọng thuộc về
  NHÂN VẬT, và chỉ có trên trang `/project/<id>/character/<entity>` của nó.
  - `flow_voices` đọc cả danh sách: 30 preset của Flow (`Achernar` tới `Zubenelgenubi`, mỗi giọng một dòng mô tả
    kiểu `Female, youthful, mid-high pitch`) cộng những giọng riêng đã lưu trên tài khoản, mỗi dòng có `custom`.
    Danh sách vẽ theo cửa sổ như grid nên tool quét cuộn, và đọc tên từ chính `span.asset-title` của hàng chứ
    không cắt chữ: `textContent` của hàng dính liền không dấu cách (`voice_selectionAchernarFemale, soft...`).
  - `character_set_voice` gắn một giọng có sẵn (rpc `rzMKMb`), `character_clear_voice` gỡ ra. Nhân vật ĐÃ có giọng
    thì nút mở hộp thoại không còn tên "Select a voice": chữ `voice_selection` nằm trong icon `aria-hidden`, nên
    tool tìm thêm theo chữ trong DOM. Hộp thoại mở ra đã chọn sẵn giọng nó đang sửa; bấm lại đúng hàng đó là BỎ
    chọn và xoá luôn nút xác nhận, nên tool chỉ bấm hàng khi cần.
  - `character_make_voice` tạo **giọng riêng**: một preset cộng một câu mô tả lối diễn (`giọng nữ Sài Gòn, nhỏ nhẹ,
    nhí nhảnh, khoảng 20 tuổi`), lưu lại dưới một cái tên rồi gắn cho nhân vật. Dùng nó khi câu mô tả quan trọng:
    `character_set_voice` gửi bản cập nhật nhân vật chỉ mang TÊN preset (đo trên body rpc 2026-09-18), nên chữ
    viết cạnh preset không nằm trong thứ được gắn. Gõ chữ KHÔNG gửi gì
    cả (footer chỉ đổi icon `play_arrow` thành `autorenew`): phải bấm Preview, Flow tổng hợp thử giọng (rpc
    `no0P6`, `gemini_v4s_tts_flow`, khoảng 24 s), và nút `Save new voice` **trông vẫn bật suốt thời gian đó**
    nhưng bấm sớm thì không gửi gì hết. Tool chờ đúng câu trả lời ấy rồi mới bấm, và một cú bấm không sinh rpc
    nào bị báo lỗi chứ không được gọi là đã lưu. Lưu xong fires `lt8g5` cộng `mYWVGd`, giọng hiện trên đầu danh
    sách. Đo thật: 44 s, $0. `sample` là câu Flow đọc thử, tối đa 120 ký tự; `attach=false` lưu mà không đổi giọng
    đang gắn. Lưu ý cho agent đọc listing: mẫu giọng đã lưu **hiện trong `flow_media`** thành một hàng
    `kind: "video"` mang tên giọng, `url` và `prompt` đều `null`. Đó là giọng chứ không phải clip.
- **Biến clip thành tài sản của project** (miễn phí, đo 2026-09-18): `clip_save_frame` lưu khung hình clip editor
  đang mở thành ẢNH của project (rpc `maseQ`, tiêu đề `Saved frame from <clip>`) để làm `initial_frame` cho cảnh
  sau (tải bằng `flow_download` rồi đưa FILE cho `gen_i2v`; đưa thẳng `media_id` cho `gen_i2v` chưa ai chạy thử);
  `scene_save_clip` chép một clip trong timeline ra grid thành media riêng (chuột phải, `Save to Project`,
  rpc `Sc7aEb`). Cả hai: Flow đánh chỉ mục khoảng **40 s** sau cú bấm, nên tool chờ listing thấy media mới thay vì
  đọc một lần rồi kết luận. `clip_save_frame` còn một bẫy đắt: trang sửa vẽ clip vào **canvas** khoảng 5 s sau khi
  sẵn sàng, và body `maseQ` mang chính ảnh PNG của canvas đó, nên bấm sớm là lưu ra **ảnh đen** (đo: YAVG 0 trong
  khi khung đầu clip là 111, tool vẫn báo thành công và `flow_download` vẫn tải được). Tool giờ chờ canvas sáng
  rồi mới bấm, trang không bao giờ sáng thì từ chối; lần đo lại: 53 s, ảnh 372 KB, YAVG 113,6. **Và nó không phải
  lúc nào cũng chạy**: 5 cú bấm thật ngày 2026-09-18 chỉ ra 3 ảnh, 2 lần Flow hiện snackbar "Saving frame..." rồi
  không đẻ ra media nào trong 160 s. Tool nói rõ rơi vào ca nào thay vì bịa media id.
- **`clip_download`**: bản 1080p đã đo là $0; bản `4k` bị Flow làm mờ trên account Pro này (đo 2026-09-29; bảng giá
  của Flow chỉ bán từ gói Ultra, 50 credit) nên tool từ chối trước khi bấm, không tốn gì. **Clip 360p có menu khác**
  (đo 2026-10-03): "270p Animated GIF", "360p Original size", "720p Upscaled", không có 1080p. `quality=720p` trên clip
  360p tải bản upscale qua link đã ký (720x1280, $0); mức mà menu của clip không có, kể cả 1080p mặc định, bị từ chối
  kèm đúng các mục menu đang có; file 360p gốc lấy bằng `flow_download`.
- **`flow_upload`**: đọc danh sách ảnh của project TRƯỚC khi chọn file. Flow trả lời (rpc `maseQ`, đo 7,8 tới 9,1 s)
  thì dùng câu trả lời đó. Không thấy câu trả lời thì quyết toán bằng listing: đúng một ảnh mới mang tên file là bản
  vừa upload (`found_by: "listing"`); không có ảnh mới nào thì báo "chưa upload gì, upload lại an toàn"; có hai thì
  không chọn bừa; file video thì bảo xem `flow_media` vì chưa ai đo tên video trong listing. Lý do: ngày 2026-10-02
  tool báo lỗi hai lần trong khi ảnh đã lên, và lần gọi lại để lại hai ảnh trùng tên.

Không có chế độ no-spend: chủ repo chốt agent được gọi mọi thứ. Chuỗi `instructions` mà server gửi cho agent lúc
`initialize` nêu đích danh nhóm tốn credit, thời gian chờ và luật giữ `job_id`, và có test canh để nó không lệch
khỏi mô tả của từng tool. Mỗi lần đọc số dư (CLI, MCP, job sinh, clip editor) ghi thêm một dòng kèm giờ vào
`out/credits.jsonl`. Bị Google gắn cờ hoạt động bất thường (WAF, gflow exit 10) thì dừng hẳn: không thử lại,
không đăng nhập lại, báo chủ repo.

mcp 2.2.0 giấu lời của mọi exception không phải `ToolError`, agent chỉ thấy `Error executing tool <tên>` (đo
2026-09-15, cả lời dặn WAF lẫn lời từ chối `job_id`). Server của repo chuyển lỗi thành `ToolError` kèm lý do: cắt
bỏ phần "Call log" của Playwright (nó liệt kê header, có cả cookie), rồi lọc bằng regex của ledger cộng
`redact_error_detail` của gflow (SID, HSID, SSID, APISID, Bearer, URL có chữ ký, email), tối đa 500 ký tự.

Mọi tool dùng trình duyệt xếp hàng qua một khoá phiên. Client hết giờ đọc thì gửi `notifications/cancelled` và SDK
huỷ handler; lời gọi bị huỷ trong lúc chờ khoá trước đây vẫn lấy khoá rồi giữ luôn, làm treo mọi tool sau đó tới khi
khởi động lại server (đo 2026-09-15). Giờ khoá được chờ bằng vòng hỏi không chặn, bị huỷ thì không cầm gì. Lời gọi
bị huỷ khi ĐANG giữ trình duyệt cũng vẫn thoát client của gflow (chỗ trả khoá profile), kể cả khi đóng trang lỗi;
trước đây mọi lời gọi sau nó lỗi `ProfileLockedError` tới khi khởi động lại. Teardown chờ `page.close()` tối đa 10 s
(`PAGE_CLOSE_TIMEOUT_S`): quá hạn thì ghi cảnh báo rồi vẫn thoát client và trả khoá, không ném lỗi đè lên kết quả thật
của lời gọi; lời gọi bị huỷ trong lúc đó vẫn bị huỷ.

## Acceptance

```
uv run python scripts/acceptance/flow_coverage.py --project <id> [--character] [--spend --ref-image anh.jpg]
uv run python scripts/acceptance/mcp_smoke.py
uv run python scripts/acceptance/ledger_integrity.py     # $0, offline, không cần trình duyệt
uv run python scripts/acceptance/character_gen.py --project <id nháp>   # $0, 8 hàng, gen_character dry_run thật
uv run python scripts/acceptance/character_gen.py compare --project <id> --job <job_id> --entity <entity_id>
uv run python scripts/acceptance/scene_build.py --project <id nháp>     # $0, 11 hàng, ghép phim 5 clip qua MCP
uv run pytest -q
```

Test tay qua MCP, kèm giá từng tool, rào chắn và prompt sẵn để giao cho một agent khác:
`docs/mcp-manual-test.md` (số tool và tên từng tool trong đó được test so với server đang phục vụ).

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
- `gen i2v` từng hỏng 5/5 lần ("frame picker stayed open 15s", 0 credit mỗi lần) tới khi nâng gflow lên 0.78.0:
  bản 0.74.0 sửa picker không commit ở cú bấm đầu và lần chạy lại bám nhầm khung cũ (#792), 0.78.0 sửa tiếp
  picker không bind được khung vừa upload. Đo 2026-09-18: **15 credit**, 105 s, clip 10,005 s, khung đầu đúng
  ảnh đưa vào (đã crop). **Không truyền `--aspect` thì ra 9:16**, vì `--aspect` của gflow mặc định `9:16` cho cả
  t2v, i2v, r2v; đó là mặc định của gflow chứ không phải tỉ lệ còn sót của lần trước. Ảnh khác tỉ lệ bị Flow CẮT
  cho vừa: ảnh 16:9 ra clip 720x1280 với con thuyền bị đẩy nửa ra khỏi mép trái. Truyền đúng tỉ lệ mình muốn.
- `gen i2v --end-frame` (khung cuối, nội suy giữa hai ảnh local): gflow 0.78.0 mới port lên host mới, đi submit
  riêng (rpc `nprQif`, model nội suy). **Đo 2026-09-18 qua MCP: 15 credit, 119 s** cho omni-flash 10 s, bằng đúng
  giá chạy một khung đầu (105 s, khác ảnh khác prompt nên đừng đọc hiệu hai con số). Clip ra đúng hai đầu: khung cuối lệch
  1,9/255 so với ảnh cuối, và trong 240 khung thì khung GẦN ảnh cuối nhất chính là khung cuối cùng, hội tụ đều
  10,0 - 9,1 - 8,0 - 6,2 - 3,9 - 1,9 ở sáu khung chót; khung gần ảnh đầu nhất là khung 1 (2,1); chéo nhau khoảng
  50 (`out/plan_i/end_frame_sheet.png`, đã nhìn). **Chỉ omni-flash 10 s được đo**, model hay độ dài khác bị tool
  từ chối vì Flow chọn model nội suy theo cohort, giá chưa ai trả.
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
  Flash 8 s = 12 (đo 2026-09-15, 292 s đầu cuối qua MCP), `gen i2v` omni-flash 10 s = 15 (đo 2026-09-18 qua MCP, 105 s).
  Bảng giá chính thức của Flow lệch ở hai chỗ: Omni Flash Edit ghi **40** trong khi đo được 20 nhiều lần, và **4K
  chỉ có từ gói Ultra** (50 credit), tài khoản này là Pro. Mô tả tool nói cả hai con số, và `clip_edit` KHÔNG đọc
  dòng giá trước khi bấm (khác `gen_character`), nên chốt chặn duy nhất của nó là số dư đọc trước và sau.
- Thao tác tốn credit chỉ bấm "Start generation" ĐÚNG MỘT LẦN. Bấm lại khi tưởng cú trước hụt là cách
  nhanh nhất để trả tiền hai lần: đo 2026-09-13, cú thứ hai rơi vào ô prompt thường và cộng thêm một
  Omni edit 20 credit lên trên extend 10 credit (hoá đơn 30). Thành hay bại đọc ở listing và số dư.
