import asyncio
import json
import re

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from video.flow import composer


def test_price_is_read_from_the_composer_settings_line():
    assert composer.price_from("crop_9_16 9:16 Veo 3.1 - Lite x1 Generating will use 10 credits") == 10
    assert composer.price_from("Generating will use 20 credits") == 20
    assert composer.price_from("Generating will use 1 credit") == 1
    assert composer.price_from("no price here") is None


def test_ensure_price_blocks_a_submit_that_would_cost_more_than_planned():
    # The composer remembers the last run: leaving it on x2 silently doubles the bill.
    composer.ensure_price(10, 10)
    with pytest.raises(RuntimeError, match="20"):
        composer.ensure_price(20, 10)
    with pytest.raises(RuntimeError, match="could not read"):
        composer.ensure_price(None, 10)


def test_frames_mode_is_recognised_from_the_buttons_the_composer_really_renders():
    # Measured 2026-09-13 on tryon2-02: the composer WAS in Frames mode and the driver still refused,
    # because the marker asked for an exact aria-label. Judge by the slot buttons instead.
    empty = ["Start", "Swap first and last frames", "End", "Agent", "Settings trigger", "Start generation"]
    assert composer.mode_visible(empty, "Frames")
    assert composer.mode_visible(["  Start\n", "End", "Start generation"], "Frames")
    filled = ["Image ingredient, tryon2-02_start.png", "Swap first and last frames", "End", "Agent"]
    assert composer.mode_visible(filled, "Frames")


def test_a_composer_with_no_slots_is_not_in_frames_mode():
    # "Start generation" is the submit button: counting it as the Start slot would hide a real failure.
    assert not composer.mode_visible(["Agent", "Settings trigger", "Start generation"], "Frames")
    assert not composer.mode_visible([], "Frames")


def test_ingredients_mode_is_recognised_and_an_unchecked_mode_passes():
    assert composer.mode_visible(["Add ingredient", "Agent", "Start generation"], "Ingredients")
    assert not composer.mode_visible(["Start", "End", "Start generation"], "Ingredients")
    assert composer.mode_visible(["Agent"], "Video")


def test_ingredients_mode_is_told_by_its_add_button_never_by_a_chip_or_a_pinned_frame():
    # Measured 2026-10-02 (out/al/t7_mode.json): the "+" button exists in Ingredients mode only. A chip on the bar is
    # labelled "Ingredient", and once the composer is put in Frames the same image becomes the start frame, labelled
    # "Image ingredient": read loosely, a Frames composer holding one image passed for Ingredients, and only the
    # request check, after the money, would have told (review of plan AL).
    with_a_chip = [
        "Ingredient",
        "Clear prompt",
        "Add ingredients to the prompt box",
        "Agent",
        "Settings trigger",
    ]
    in_frames = [
        "Image ingredient",
        "Swap first and last frames",
        "End",
        "Clear prompt",
        "Agent",
        "Settings trigger",
    ]
    assert composer.mode_visible(with_a_chip, "Ingredients")
    assert not composer.mode_visible(in_frames, "Ingredients")
    assert composer.mode_visible(in_frames, "Frames")
    assert not composer.mode_visible(["Ingredient", "Agent", "Start generation"], "Ingredients")


def test_start_slot_is_only_filled_once_it_carries_an_image():
    assert not composer.start_slot_filled(["Start", "Swap first and last frames", "End"])
    assert composer.start_slot_filled(["Image ingredient, tryon2-02_start.png", "End"])


def test_pick_output_prefers_the_record_carrying_our_prompt():
    prompt = "SHOT: full-body mirror selfie. turning slowly. Wearing cream lace slip dress."
    fresh = [
        {"workflow_id": "w1", "kind": "video", "created": 10, "prompt": "something else", "status": 3},
        {"workflow_id": "w2", "kind": "video", "created": 20, "prompt": prompt, "status": 3},
    ]
    assert composer.pick_output(fresh, prompt)["workflow_id"] == "w2"


def test_pick_output_falls_back_to_the_newest_video_and_ignores_images():
    prompt = "a prompt that no record echoes"
    fresh = [
        {"workflow_id": "img", "kind": "image", "created": 99, "prompt": None, "status": 3},
        {"workflow_id": "old", "kind": "video", "created": 10, "prompt": None, "status": 3},
        {"workflow_id": "new", "kind": "video", "created": 20, "prompt": None, "status": 3},
    ]
    assert composer.pick_output(fresh, prompt)["workflow_id"] == "new"
    assert composer.pick_output([], prompt) is None


def _fetch_world(monkeypatch, tmp_path):
    urls = []

    async def fetch(request, url, stem):
        urls.append(url)
        return tmp_path / "clip.mp4"

    async def fallback(request, record, stem, attempts=6):
        urls.append(f"fallback {record['url']}")
        return tmp_path / "sd.mp4"

    async def no_sleep(seconds):
        return None

    monkeypatch.setattr(composer.download_mod, "fetch_to_file", fetch)
    monkeypatch.setattr(composer.clips, "_fetch_with_retry", fallback)
    monkeypatch.setattr(composer.asyncio, "sleep", no_sleep)
    return urls


class _FetchSession:
    page = type("P", (), {"request": object()})()


def test_fetch_720_asks_flow_for_a_moved_url(monkeypatch, tmp_path):
    """Plan AD: the listing's new host answers 400 to every suffix; flow.google.com serves the same token."""
    urls = _fetch_world(monkeypatch, tmp_path)
    record = {
        "kind": "video",
        "url": "https://contribution.fife.usercontent.google.com/asb/T",
        "model": "veo_3_1_t2v_lite",
    }

    asyncio.run(composer.fetch_720(_FetchSession(), record, tmp_path / "shot", project_id="P"))

    assert urls == ["https://flow.google.com/asb/T=m22"], urls


def test_a_360p_clip_is_not_held_waiting_for_a_720p_file_it_never_gets(monkeypatch, tmp_path):
    """Measured 2026-09-29 (job ab-1, key abra_t2v_4s_360p): =m22 answers 404 and =m18 is the 360p file."""
    urls = _fetch_world(monkeypatch, tmp_path)
    record = {
        "kind": "video",
        "url": "https://contribution.fife.usercontent.google.com/asb/T",
        "model": "abra_t2v_4s_360p",
    }

    out = asyncio.run(composer.fetch_720(_FetchSession(), record, tmp_path / "shot", project_id="P"))

    assert out == tmp_path / "sd.mp4"
    assert not any(u.endswith("=m22") for u in urls), urls


_IMAGE_TAB = [
    "image Image",
    "videocam Video",
    "crop_16_9 16:9",
    "crop_landscape 4:3",
    "crop_square 1:1",
    "crop_portrait 3:4",
    "crop_9_16 9:16",
    "x1",
    "x2",
    "x3",
    "x4",
]
_VIDEO_TAB = [
    "image Image",
    "videocam Video",
    "crop_free Frames",
    "chrome_extension Ingredients",
    "crop_16_9 16:9",
    "crop_9_16 9:16",
    "x1",
    "x2",
    "x3",
    "x4",
]
# flow_ui.json: the Ingredients composer and the Image composer render the very same buttons.
_PLAIN = ["Add ingredients to the prompt box", "Agent", "Settings trigger", "Start generation"]
_FRAMES = ["Agent", "End", "Settings trigger", "Start", "Start generation", "Swap first and last frames"]


class _Panel:
    """Flow's composer settings as measured 2026-09-30: a gen_i2i run leaves the panel on its Image tab, which
    offers no Frames and no Ingredients until the Video tab is chosen."""

    def __init__(self, tab: str = "Image", video_sticks: bool = True):
        self.tab, self.mode, self.video_sticks, self.clicks = tab, "Ingredients", video_sticks, []

    def items(self, selector: str) -> list[str]:
        if selector == composer.SETTINGS:
            return ["Settings trigger"]
        return _VIDEO_TAB if self.tab == "Video" else _IMAGE_TAB

    def click(self, text: str) -> None:
        self.clicks.append(text)
        if text.endswith("Video"):
            self.tab = "Video" if self.video_sticks else self.tab
        elif text.endswith("Image"):
            self.tab = "Image"
        elif text.endswith(("Frames", "Ingredients")):
            self.mode = text.split()[-1]

    def buttons(self) -> list[str]:
        return _FRAMES if self.tab == "Video" and self.mode == "Frames" else _PLAIN


class _PanelLocator:
    def __init__(self, panel: _Panel, selector: str, pattern=None):
        self.panel, self.selector, self.pattern = panel, selector, pattern

    def _hits(self) -> list[str]:
        items = self.panel.items(self.selector)
        return [t for t in items if self.pattern is None or self.pattern.search(t)]

    def filter(self, has_text=None):
        pattern = has_text if hasattr(has_text, "search") else re.compile(re.escape(has_text or ""))
        return _PanelLocator(self.panel, self.selector, pattern)

    @property
    def first(self):
        return self

    async def count(self) -> int:
        return len(self._hits())

    async def wait_for(self, **_):
        return None

    async def click(self, **_):
        hits = self._hits()
        if hits and hits[0] != "Settings trigger":
            self.panel.click(hits[0])


class _PanelKeys:
    async def press(self, key):
        return None


class _PanelPage:
    def __init__(self, panel: _Panel):
        self.panel, self.keyboard = panel, _PanelKeys()

    def locator(self, selector: str):
        return _PanelLocator(self.panel, selector)

    async def wait_for_timeout(self, ms):
        return None

    async def wait_for_function(self, js, timeout=None):
        return None

    async def evaluate(self, js, *args):
        if js == composer._COMPOSER_BUTTONS_JS:
            return self.panel.buttons()
        if "aria-checked" in js:
            return self.panel.tab == "Video"
        return " ".join(self.panel.items("pane")) + " Generating will use 12 credits"


class _PanelSession:
    def __init__(self, panel: _Panel):
        self.page = _PanelPage(panel)


@pytest.mark.parametrize("mode", ["Frames", "Ingredients"])
def test_a_panel_left_on_the_image_tab_is_moved_to_video_before_the_mode_is_chosen(mode):
    """Measured 2026-09-30: after gen_i2i every gen_video failed 'composer did not switch to Frames'."""
    panel = _Panel(tab="Image")

    out = asyncio.run(composer.configure(_PanelSession(panel), mode=mode, label="pre"))

    assert panel.tab == "Video" and panel.mode == mode, (panel.tab, panel.mode, panel.clicks)
    assert out["applied"][mode] is True, out


def test_ingredients_is_refused_while_the_panel_stays_on_image_whatever_the_buttons_say():
    """The Ingredients composer and the Image composer show the same buttons, so only the Video tab can tell."""
    panel = _Panel(tab="Image", video_sticks=False)

    with pytest.raises(RuntimeError, match="Video"):
        asyncio.run(composer.configure(_PanelSession(panel), mode="Ingredients", label="pre"))


class _StuckTrigger:
    """A Settings trigger Playwright never gets to click, as on 2026-10-01 under Flow's cookie notice."""

    def __init__(self, page):
        self.page = page

    @property
    def first(self):
        return self

    async def wait_for(self, **_):
        return None

    async def click(self, **_):
        self.page.tried += 1
        raise PlaywrightTimeoutError("Locator.click: Timeout 8000ms exceeded.")


class _StuckPage:
    def __init__(self, cover):
        self.cover, self.tried, self.keyboard = cover, 0, _PanelKeys()

    def locator(self, selector):
        return _StuckTrigger(self)

    async def wait_for_timeout(self, ms):
        return None

    async def evaluate(self, js, *args):
        return self.cover


def test_a_settings_trigger_under_an_unknown_overlay_is_refused_in_the_overlays_own_words():
    page = _StuckPage({"tag": "div", "id": "promo", "text": "Try the new Flow agent Dismiss"})

    with pytest.raises(LookupError, match="Try the new Flow agent Dismiss"):
        asyncio.run(composer._open_settings(page, "pre"))

    assert page.tried == 1, "a covered trigger is not clicked a second time"


def test_a_settings_trigger_that_times_out_with_nothing_over_it_keeps_its_own_error():
    page = _StuckPage(None)

    with pytest.raises(PlaywrightTimeoutError):
        asyncio.run(composer._open_settings(page, "pre"))


def _dead_renditions(monkeypatch, tmp_path):
    """Job lly-v5-s1c-reveal, 2026-09-30: paid 10, then =m22 and =m18 both HTTP 404 and it ended pending; the editor's
    Download at 720p fetched the same clip for 0 credits."""
    editor = []

    async def dead(request, *args, **kwargs):
        raise RuntimeError("no rendition of video returned a real asset: o=m22: HTTP 404; o=m18: HTTP 404")

    async def no_sleep(seconds):
        return None

    async def download_rendition(session, project_id, media_id, quality, out_dir, *, workflow_id=None):
        editor.append((project_id, media_id, quality, out_dir, workflow_id))
        return out_dir / f"{media_id}_{quality}.mp4"

    monkeypatch.setattr(composer.download_mod, "fetch_to_file", dead)
    monkeypatch.setattr(composer.clips, "_fetch_with_retry", dead)
    monkeypatch.setattr(composer.clips, "download_rendition", download_rendition)
    monkeypatch.setattr(composer.asyncio, "sleep", no_sleep)
    return editor


def test_a_paid_clip_whose_renditions_404_is_fetched_from_the_editor(monkeypatch, tmp_path):
    editor = _dead_renditions(monkeypatch, tmp_path)
    record = {
        "id": "M1",
        "workflow_id": "W2",
        "kind": "video",
        "url": "https://flow.google.com/asb/T",
        "model": "omni_flash_i2v_6s_first_last",
    }

    out = asyncio.run(composer.fetch_720(_FetchSession(), record, tmp_path / "M1_ab", project_id="P"))

    # The version handed in, not the newest; and a folder of the stem's own, since the editor names every download
    # <media>_720p and refuses to overwrite, which a later version of the same clip would hit (review of plan AF).
    assert editor == [("P", "M1", "720p", tmp_path / "M1_ab", "W2")], editor
    assert out == tmp_path / "M1_ab" / "M1_720p.mp4"


def test_an_error_that_is_not_a_404_is_not_hidden_behind_the_editor(monkeypatch, tmp_path):
    editor = _dead_renditions(monkeypatch, tmp_path)

    async def refused(request, *args, **kwargs):
        raise RuntimeError("HTTP 403 forbidden")

    monkeypatch.setattr(composer.download_mod, "fetch_to_file", refused)
    monkeypatch.setattr(composer.clips, "_fetch_with_retry", refused)
    record = {"id": "M1", "kind": "video", "url": "https://flow.google.com/asb/T", "model": "abra_i2v_8s"}

    with pytest.raises(RuntimeError, match="403"):
        asyncio.run(composer.fetch_720(_FetchSession(), record, tmp_path / "M1_ab", project_id="P"))
    assert editor == []


def test_the_video_check_in_the_page_uses_the_pattern_that_matches_flows_radio_text():
    """Live dry run 2026-09-30: the Video click took, yet the check read False. The radio's textContent is the icon
    name glued to the label ('videocamVideo'), and the page-side regex wanted a space the fake never questioned."""
    assert json.dumps(composer._VIDEO_TAB.pattern) in composer._VIDEO_CHECKED_JS
    for text in ("videocamVideo", "videocam Video", "Video"):
        assert composer._VIDEO_TAB.search(text), text
    for text in ("imageImage", "Video generation off", "chrome_extensionIngredients"):
        assert not composer._VIDEO_TAB.search(text), text


def test_renditions_that_all_answer_400_fall_back_to_the_editor_too(monkeypatch, tmp_path):
    """Live 2026-09-30: flow.google.com answers 400, not 404, to a token it will not serve; the paid clip is lost to
    the job the same way, and the editor Download costs nothing."""
    editor = _dead_renditions(monkeypatch, tmp_path)

    async def bad_request(request, *args, **kwargs):
        raise RuntimeError("no rendition of video returned a real asset: A=m22: HTTP 400; A=m18: HTTP 400")

    monkeypatch.setattr(composer.download_mod, "fetch_to_file", bad_request)
    monkeypatch.setattr(composer.clips, "_fetch_with_retry", bad_request)
    record = {
        "id": "M1",
        "workflow_id": "W2",
        "kind": "video",
        "url": "https://flow.google.com/asb/T",
        "model": "abra_i2v_8s",
    }

    out = asyncio.run(composer.fetch_720(_FetchSession(), record, tmp_path / "M1_ab", project_id="P"))

    assert editor == [("P", "M1", "720p", tmp_path / "M1_ab", "W2")], editor
    assert out == tmp_path / "M1_ab" / "M1_720p.mp4"
