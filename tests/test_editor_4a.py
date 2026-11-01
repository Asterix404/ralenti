import array

from ralenti.core.encoders import ENCODERS
from ralenti.core.quality_test import picture_chain, size_factor
from ralenti.core.waveform import bucket


def test_bucket_peaks_are_normalised():
    raw = array.array("h", [0, 16384, -32768, 100]).tobytes()
    assert bucket(raw, 2) == [0.5, 1.0]
    assert bucket(b"", 10) == []


def test_picture_chain_crop_rotate_scale():
    assert picture_chain([640, 360, 0, 0], 90, False, False, 0.5, 0, 1280, 720) == [
        "crop=640:360:0:0", "transpose=clock", "scale=180:320:flags=lanczos"]
    assert picture_chain("none", 0, False, False, 1.0, 0, 1280, 720) == []


def test_size_factor():
    e = ENCODERS["x265"]
    assert size_factor(e, 95.0) == 1.0
    assert abs(size_factor(e, None, e.q_default + 6) - 0.5) < 1e-9
    assert size_factor(ENCODERS["prores"], 95.0) == 1.0
