"""The narration of the LeeLyLy story: one line per shot, each with its own delivery.

Measured 2026-09-18 on this account: Flow's sample-dialogue box holds 120 characters, and a line of about 60
characters comes back as roughly 5 s of speech in the custom voice. A single delivery for every line is the
first thing that gives an AI video away, so each line carries its own direction and they are deliberately
uneven in length.
"""

from __future__ import annotations

SAMPLE_MAX = 120

VOICE_PRESET = "Achernar"
VOICE_NAME = "LyMienTay18"
BASE_PERFORMANCE = (
    "giọng nữ miền Tây Nam Bộ Việt Nam, khoảng 18 tuổi, nhỏ nhẹ, ngọt, dễ thương, nói chậm rãi, "
    "âm cuối kéo nhẹ, thân mật như đang tâm sự với bạn thân"
)

# (id, text, delivery). The delivery is appended to BASE_PERFORMANCE for that line only.
LINES: list[tuple[str, str, str]] = [
    ("L01", "Bộ đồ này, từ bữa đó tới giờ mình chưa mặc lại lần nào.", "ngập ngừng, hạ giọng ở cuối câu"),
    ("L02", "Mà thôi, để mình kể từ đầu nghen.", "nhẹ, hơi cười, như rủ rê"),
    ("L03", "Mình là Ly, quê miền Tây, lên Sài Gòn được hai năm.", "kể chuyện, đều và ấm"),
    (
        "L04",
        "Ban ngày mình đi làm, tối về bán đồ online trong phòng nhỏ xíu này.",
        "nói nhanh hơn một chút, tự nhiên",
    ),
    ("L05", "Tủ đồ này là cả gia tài của mình đó.", "tự hào, cười nhẹ"),
    ("L06", "Bộ đầu tiên mình bán được là cái váy trắng có ren.", "ấm, chậm, nhớ lại"),
    (
        "L07",
        "Bữa đó có một anh nhắn: mua cho bạn gái, mà mãi không thấy bạn gái đâu.",
        "tinh nghịch, nhướng giọng ở cuối",
    ),
    (
        "L08",
        "Ảnh hỏi mình đủ thứ, size, màu, rồi hỏi luôn tối nay mình ăn gì.",
        "kể nhanh rồi bật cười ở cuối",
    ),
    ("L09", "Rồi tụi mình quen nhau. Đơn giản vậy đó.", "nhẹ tênh, bỏ lửng"),
    ("L10", "Đi chơi lần đầu mình mặc cái áo hoa nhí, run muốn xỉu.", "hồi hộp, hơi run giọng"),
    ("L11", "Ảnh nói: em mặc gì cũng được, miễn em cười.", "dịu hẳn xuống, gần như thì thầm"),
    ("L12", "Nên bộ nào mình chụp, mình cũng để dành khoe với ảnh trước.", "thủ thỉ, ngọt"),
    ("L13", "Cái váy hồng là sinh nhật. Cái áo len là Đà Lạt.", "liệt kê có nhịp, ngắt rõ từng vế"),
    (
        "L14",
        "Cái đầm đen là bữa ảnh nói ảnh phải đi xa một thời gian.",
        "hụt một nhịp ở giữa câu rồi nói tiếp",
    ),
    ("L15", "Lúc đầu còn nhắn. Rồi thưa dần. Rồi thôi.", "ba nhịp, nhỏ dần, câu cuối gần như tắt"),
    ("L16", "Mình vẫn mặc đồ đẹp, vẫn chụp, chỉ là không gửi cho ai nữa.", "bình thản nhưng hơi nghẹn"),
    ("L17", "Có bữa mình ngồi nhìn cái tủ, tự hỏi bỏ hết đi cho rồi.", "mệt, chậm, thở ra"),
    ("L18", "Mà nghĩ lại, mấy bộ này đâu có lỗi gì.", "nhếch mép, tự trấn an"),
    ("L19", "Mỗi bộ là một lần mình vui thiệt, dù người ta đi rồi.", "ấm lại, chắc giọng"),
    ("L20", "Nên mình giữ, mình mặc, mình sống tiếp.", "dứt khoát, ngẩng lên, sáng giọng"),
    ("L21", "Còn bộ đầu video, bữa nào vui mình mặc lại nghen.", "cười, thân mật, khép lại"),
]


def performance_for(delivery: str) -> str:
    return f"{BASE_PERFORMANCE}, {delivery}"


def too_long() -> list[tuple[str, int]]:
    """Lines Flow's sample box would silently cut. It has a maxlength, so a long line loses its ending."""
    return [(line_id, len(text)) for line_id, text, _ in LINES if len(text) > SAMPLE_MAX]
