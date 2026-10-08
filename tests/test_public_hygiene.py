"""The repo is public: nothing tracked may carry a personal address or a secret-shaped string (plan AQ, B4; the
owner's address sat in a test of the survey from 2026-09-30 to 2026-10-08)."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# An address at a personal mail host; example.com and the like are documentation.
PERSONAL_ADDRESS = re.compile(
    r"[A-Za-z0-9._%+-]+@(gmail|googlemail|yahoo|hotmail|outlook|icloud|me)\.com", re.IGNORECASE
)
# What a leaked Google credential of this account looks like: the cookie and header shapes are the ones gen.py
# scrubs from gflow's output, the rest are Google's key, token and signed-url formats.
SECRET_SHAPES = {
    "google api key": re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    "oauth token": re.compile(r"ya29\.[0-9A-Za-z_-]{20,}"),
    "sapisidhash header": re.compile(r"SAPISIDHASH [0-9a-f_]{20,}"),
    "session cookie": re.compile(r"(__Secure-[13]PSID[A-Z]*|SAPISID|HSID|SSID)=[A-Za-z0-9_./-]{20,}"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "signed url": re.compile(r"[?&](Signature|X-Goog-Signature)=[A-Za-z0-9_~%-]{20,}", re.IGNORECASE),
}


def findings(text: str, where: str) -> list[tuple[str, str]]:
    found = [(where, f"personal address {match.group()}") for match in PERSONAL_ADDRESS.finditer(text)]
    for name, shape in SECRET_SHAPES.items():
        found += [(where, f"{name} {match.group()[:14]}...") for match in shape.finditer(text)]
    return found


def tracked_files() -> list[Path]:
    listed = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [ROOT / name for name in listed.decode("utf-8").split("\0") if name]


def test_the_scan_sees_an_address_and_every_secret_shape_in_a_fixture():
    # The ruler first; the strings are assembled from pieces so that this file never holds one of them itself.
    fixture = "\n".join(
        [
            "mail me at " + "someone.private" + "@" + "gmail.com",
            "key " + "AIza" + "B" * 35,
            "token " + "ya29." + "c" * 40,
            "Authorization: " + "SAPISIDHASH " + "1" * 10 + "_" + "a" * 40,
            "Cookie: " + "__Secure-1PSID" + "=" + "g" * 40,
            "-----BEGIN " + "PRIVATE KEY-----",
            "https://h/x?" + "Signature" + "=" + "s" * 40,
        ]
    )

    kinds = sorted(what.split(" ", 2)[:2] for _, what in findings(fixture, "fixture"))

    assert kinds == sorted([["personal", "address"]] + [name.split(" ")[:2] for name in SECRET_SHAPES]), kinds


def test_nothing_tracked_carries_a_personal_address_or_a_secret_shaped_string():
    found = []
    for path in tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        found += findings(text, str(path.relative_to(ROOT)))

    assert found == [], "\n".join(f"{where}: {what}" for where, what in found)
