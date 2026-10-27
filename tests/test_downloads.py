from ralenti.core.downloads import TOOLS, is_newer, pick_asset


def test_pick_ffmpeg_full_zip():
    assets = [{"name": "ffmpeg-7.1.1-essentials_build.zip"}, {"name": "ffmpeg-7.1.1-full_build.7z"},
              {"name": "ffmpeg-7.1.1-full_build-shared.zip"}, {"name": "ffmpeg-7.1.1-full_build.zip"}]
    assert pick_asset(assets, TOOLS["ffmpeg"].pattern)["name"] == "ffmpeg-7.1.1-full_build.zip"


def test_pick_rife_windows_zip():
    assets = [{"name": "rife-ncnn-vulkan-20221029-ubuntu.zip"}, {"name": "rife-ncnn-vulkan-20221029-windows.zip"}]
    assert pick_asset(assets, TOOLS["rife"].pattern)["name"].endswith("windows.zip")


def test_fallback_any_windows_zip():
    assert pick_asset([{"name": "rife-v4.26-win64.zip"}], TOOLS["rife"].pattern)["name"] == "rife-v4.26-win64.zip"


def test_versions():
    assert is_newer("7.1.1", "7.1")
    assert not is_newer("20221029", "20221029")