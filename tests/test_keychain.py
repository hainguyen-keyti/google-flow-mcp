import browser_cookie3
import pytest


def test_import_video_disables_browser_cookie3_keychain_read():
    import video  # noqa: F401  (import applies the patch)

    with pytest.raises(browser_cookie3.BrowserCookieError):
        browser_cookie3.chrome(cookie_file="/nonexistent/Cookies")
    with pytest.raises(browser_cookie3.BrowserCookieError):
        browser_cookie3.load()


def test_patch_is_idempotent():
    from video import keychain

    keychain.apply()
    first = browser_cookie3.chrome
    keychain.apply()
    assert browser_cookie3.chrome is first
