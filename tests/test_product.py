import pytest

from video.story import product


def test_the_product_image_is_looked_for_in_the_local_assets_folder():
    # The product photos stay on this machine and out of the public repo (DECISIONS 2026-09-28).
    assert product.IMAGE.parent == product.ASSETS and product.IMAGE.suffix == ".png", product.IMAGE


def test_a_missing_product_image_is_named_before_anything_is_uploaded(monkeypatch, tmp_path):
    monkeypatch.setattr(product, "IMAGE", tmp_path / "pink_floral_set.png")
    with pytest.raises(FileNotFoundError, match="product image missing"):
        product.check_image()


def test_find_matches_an_upload_by_its_filename():
    media = [
        {"id": "a", "title": "something else.png", "kind": "image"},
        {"id": "b", "title": product.IMAGE.name, "kind": "image"},
    ]
    assert product.find(media)["id"] == "b"
    assert product.find([{"id": "a", "title": "nope.png", "kind": "image"}]) is None
    assert product.find([]) is None


def test_find_ignores_a_video_that_happens_to_share_the_name():
    media = [{"id": "v", "title": product.IMAGE.name, "kind": "video"}]
    assert product.find(media) is None


def test_describe_names_every_feature_a_buyer_would_check():
    # The description is what Veo reconstructs the garment from, so it names the details visible in
    # assets/product/pink_floral_set.png rather than a vague "pink set".
    text = product.DESCRIPTION.lower()
    for token in ("pink", "floral", "camisole", "shorts", "ruffle", "lace", "bow", "strap"):
        assert token in text, f"description is missing {token!r}"
    assert "dress" not in text, "the product is a two piece set, not a dress"


def test_the_image_is_the_garment_only_shot_flow_accepts():
    # Flow rejected the earlier photo that had a model wearing the set (status 4, three times, 0 credits).
    assert product.IMAGE.name == "pink_floral_set.png"


def test_ensure_refuses_a_missing_image(monkeypatch, tmp_path):
    monkeypatch.setattr(product, "IMAGE", tmp_path / "gone.png")
    with pytest.raises(FileNotFoundError):
        product.check_image()
