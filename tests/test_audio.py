from ralenti.core.audio import AudioMode, atempo_chain, audio_filter, resolve_mode


def test_atempo_chain_4x():
    assert atempo_chain(0.25) == "atempo=0.5,atempo=0.5"


def test_natural_pitch():
    assert audio_filter(AudioMode.NATURAL, 2, 48000) == "asetrate=24000,aresample=48000"


def test_rubberband():
    assert audio_filter(AudioMode.KEEP_PITCH, 4, 48000, rubberband=True).startswith("rubberband=tempo=0.25")


def test_copy_kept_with_slowmo():
    assert resolve_mode(AudioMode.COPY, 2, True) == AudioMode.COPY


def test_slowdown_modes_copy_at_1x():
    assert resolve_mode(AudioMode.NATURAL, 1, True) == AudioMode.COPY