from ralenti.core.commands import scaled_size


def test_scaled_size():
    assert scaled_size(3840, 2160, 0.5) == (1920, 1080)
    assert scaled_size(1920, 1080, 1.0) == (1920, 1080)
