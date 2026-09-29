# google-flow-mcp

Drive [Google Flow](https://flow.google.com) (Veo video, Nano Banana images) from a terminal and from an AI
agent: read projects, media and credits; create and delete projects, characters and scenes; upload media;
generate images and video; assemble scenes into a film. Every paid action is written to a ledger before the
click, and an MCP server exposes the whole surface to Claude Code or any MCP client.

Everything in this README was **measured on a real account**, not inferred from the code. Where a number has
not been measured, it says so.

> **This is not an official Google project.** It automates the Flow web app through a real browser session.
> Read [Risk](#risk-read-this-before-the-first-run) before you run anything.

## What it is, and what it is not

**It is** a control layer: 44 MCP tools plus a CLI, with a spend ledger, a double-charge guard, and prices in
every tool description.

**It is not** a video maker. The script, the shot list, the camera angles and the quality of the result are the
job of the agent *using* these tools. The repo owner settled this on 2026-09-13; the `story` package is only a
worked example and a place to exercise the paid paths for real.

## Will it run for you?

All four must be true. The first one is the one that catches people.

| Requirement | Why |
|---|---|
| A Google account whose Flow has **migrated to `flow.google.com`** | On this account the old `labs.google` lane is dead: `labs.google/fx/api/auth/session` answers `{}` and every gflow command that goes through it fails. Re-running `gflow auth login` does not fix it |
| **macOS** with real Google Chrome | The browser lane drives Chrome with the `gflow-cli` profile at `~/Library/Application Support/gflow-cli/profile_default`. Nothing here is tested on Linux or Windows |
| `gflow-cli` already logged in on that profile | This repo never logs in for you; it reuses the profile gflow created |
| `ffmpeg` and `ffprobe` on PATH | Post-production, duration checks and every measurement in the acceptance scripts |

A Google AI Pro (or higher) plan is what the measured prices below come from. Flow tops the balance up by about
50 credits a day on that plan, which is why a balance can go **up** between two readings: never infer spending
from the difference between two readings taken far apart.

## Install

```bash
git clone https://github.com/hainguyen-keyti/google-flow-mcp.git
cd google-flow-mcp
uv sync --group dev
uv run video --help
```

Check the lane before anything else. This costs nothing and tells you whether the account is usable:

```bash
uv run video flow lane
```

`verdict: MIGRATED` means you are on the supported lane. Anything else means the tools here will not work for
that account, and no amount of retrying will change it.

## Quickstart

Free reads:

```bash
uv run video flow projects
uv run video flow credits
uv run video flow media <project_id>
```

A paid generation, 10 credits on the measured plan, written to `out/ledger.jsonl`:

```bash
uv run video gen t2v "a red paper boat drifting on a pond" --project <project_id> --model veo-lite --out out
```

## Using it from an agent (MCP)

```bash
cp .mcp.json.example .mcp.json
```

Edit the copy if `uv` is not on your PATH, then open the folder with an MCP client (Claude Code reads
`.mcp.json` from the project root). The server serves **44 tools**; every description carries its price.

A server that is already running does **not** pick up new code, and an open session keeps the old tool
descriptions: after changing anything here, start a new session.

See [docs/tools.md](docs/tools.md) for the full tool list with prices, generated from the running server rather
than typed by hand.

## What a call costs

Measured on a Google AI Pro account in September 2026. A price marked *unmeasured* is one this repo refuses to
guess about; the tool says so too.

| Action | Credits |
|---|---|
| `gen_video`: any option Flow's composer offers, guarded by Flow's live price line and your `max_credits` | Omni 1.1 Flash 720p 4/6/8/10 s: 7/10/12/15; 360p: 4/5/6/7; Veo 3.1 Lite 10, Fast 20, Quality 100 (8 s); x2-x4 multiply (Flow's price line, 2026-09-29) |
| `gen_t2v` / `gen_r2v`, veo-lite, 8 s | 10 |
| `gen_t2v` / `gen_i2v`, omni-flash, 10 s | 15 |
| `gen_r2v`, omni-flash, 8 s (the only length gflow offers it) | 12 |
| `gen_character` (video starring a project character), omni-flash | 12 |
| `gen_character`, veo-lite | 10 |
| `gen_character`, veo-fast | 20, from Flow's own price table, *unmeasured* |
| `gen_character`, omni-flash, 10 s, with characters or from project images alone (the only 10 s reference video; `gen_r2v` runs 8 s) | 15 |
| `clip_extend` (Veo clips only) | 10 |
| `clip_edit` (Omni 1.1 Flash) | 20 measured, Flow's table says 40 |
| `gen_t2i` / `gen_i2i` (Nano Banana 2) | 0 credits, but a daily image quota |
| `clip_download` at 1080p | 0 |
| `clip_download` at 4k (a Flow upscale) | not offered on Pro: greyed out, refused before any click (Flow's table: Ultra, 50) |
| Every read: lane, projects, media, credits, characters, scenes, voices | 0 |

Timing, so a slow call is not mistaken for a broken one: a read takes 15 to 50 s, a change about 50 s, a
generation 2 to 5 min, `clip_extend` and `clip_edit` up to 7 min.

## How the money is guarded

These are not suggestions; they are enforced in code and pinned by tests.

- **A ledger line is written before the click, not after.** A driver that dies mid-action leaves a `submitted`
  row, never a silent charge. This exists because 20 credits were once spent with an empty ledger.
- **A `job_id` is spent once.** Before opening a browser, a paid tool refuses any `job_id` that appears in ANY
  `ledger.jsonl` under `out/`, and refuses one that another call is running right now. Retrying with the same
  id is safe; inventing a new id for a job you already paid for is how you buy the same thing twice.
- **Outputs stay inside `out/`.** Every tool that writes a file or a ledger forces its target inside `out/`.
- **Click once, then wait.** Flow submits late, about 20 s after the click. Leaving the page early cancels the
  request in flight and looks exactly like a dead button; clicking again submits a second, paid job.
- **Prices live in the tool description**, and a tool refuses a cell whose price nobody has measured.

## Risk, read this before the first run

- This drives the Flow web app with your logged-in browser profile. That may be against Google's terms for your
  account; you are the one taking that risk. If Flow flags unusual activity (a WAF rejection), the tools stop
  and tell you: do not retry and do not re-authenticate.
- Generations spend **real credits**. Nothing here asks for confirmation on your behalf; if you wire these
  tools into an autonomous agent, you are wiring it to your wallet.
- Generated clips carry Google's visible sparkle watermark. Removing it is your decision and your jurisdiction's
  problem, not something this repo does for you.

## Known limits

- macOS only, one account lane (`flow.google.com`), one browser (real Chrome with the gflow profile).
- Chrome cannot run headless here (reCAPTCHA answers a headless browser with a 403), so every call opens a real
  window and then moves it off the left edge of the screen; macOS keeps a strip of about 40 px visible. Set
  `VIDEO_BROWSER_OFFSCREEN=0` to see the windows. Signing in (`gflow auth login`) always opens a visible window.
- `clip_edit` does not read the live price line before clicking, unlike `gen_character`: the only guard is the
  balance read before and after.
- Flow's Agent mode (the "Agent" chip in the prompt bar) blocks generation while on. The five gflow MCP tools and
  `gen_character` turn it off first and back on after (about 19 s, or 35 s when it was on); the CLI and the editor
  tools (`clip_extend`, `clip_edit`) do not, so turn it off with `agent_mode` before using them.
- Trimming a clip's head or tail inside a scene is not implemented.
- Deliberately out of scope: collections, media rename, media trash, project zip export, the Tools gallery,
  Flow's own agent mode beyond an on/off switch, Omni 360p and upscale, project settings, GIF export, share
  links and publishing to YouTube.

## When Flow changes

Flow changes without notice. `uv run video flow survey --project <id>` walks Flow's pages for $0, screenshots them, and
compares their buttons, menus, options, prices and the selectors this repo relies on with baselines kept in the repo;
exit code 1 means something changed. See [docs/flow-updates.md](docs/flow-updates.md).

## Testing

```bash
uv run ruff check . && uv run ruff format --check . && uv run pytest
```

893 tests, none of which call Flow or spend anything: tests use fixtures and fakes, which is a hard rule here.
About 25 of them do render and measure real video, so **ffmpeg must be installed**, and two of them draw
captions, so a unicode font must exist: macOS ships one, on Linux install `fonts-dejavu-core` or point
`VIDEO_FONT` at a `.ttf` or `.ttc` of your own. The live acceptance scripts under `scripts/acceptance/` do talk to Flow, are
read-only by default, and say in their own output what they spent.

## Documentation

| File | What is in it |
|---|---|
| [docs/tools.md](docs/tools.md) | Every MCP tool with its price, generated from the running server |
| [docs/flow-updates.md](docs/flow-updates.md) | When Google changes Flow: the $0 survey that finds what changed, and how to update the MCP |
| [README.vi.md](README.vi.md) | The original Vietnamese README: the full measured record, including traps paid for in credits |
| [docs/mcp-manual-test.md](docs/mcp-manual-test.md) | A by-hand test pass over the tools, with a ready prompt for another agent |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to run the gate and the rules that keep the money safe |

## License

MIT, see [LICENSE](LICENSE).
