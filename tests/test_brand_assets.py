from pathlib import Path

from PIL import Image

from scripts.generate_brand_assets import generate_brand_assets

ROOT = Path(__file__).resolve().parent.parent


def test_brand_asset_generator_writes_multiresolution_windows_icon(tmp_path):
    png_path, ico_path = generate_brand_assets(tmp_path)

    with Image.open(png_path) as png:
        assert png.size == (512, 512)
        assert png.mode == "RGBA"

    with Image.open(ico_path) as ico:
        sizes = ico.ico.sizes()
        assert (16, 16) in sizes
        assert (32, 32) in sizes
        assert (48, 48) in sizes
        assert (256, 256) in sizes


def test_web_brand_icon_is_packaged_as_svg():
    icon = (ROOT / "web" / "app-icon.svg").read_text(encoding="utf-8")
    assert 'viewBox="0 0 512 512"' in icon
    assert "linearGradient" in icon
    assert "OpenVINO Windows LLM" in icon
