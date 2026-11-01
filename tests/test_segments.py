from ralenti.core.segments import (add_cut, boundaries, kept_ranges, output_to_source, remove_cut,
                                   source_to_output, toggle)


def test_ranges_merge_adjacent_kept_segments():
    assert kept_ranges({"on": True, "cuts": [2, 4, 6], "drop": [1]}, 10) == [(0.0, 2.0), (4.0, 10.0)]
    assert kept_ranges({"on": True, "start": 1, "end": 3}, 10) == [(1.0, 3.0)]
    assert kept_ranges({"on": False}, 10) == [(0.0, 10.0)]
    assert kept_ranges({"on": True, "cuts": [5], "drop": [0, 1]}, 10) == []


def test_add_cut_keeps_choices():
    t = add_cut({"on": True, "cuts": [4], "drop": [0]}, 2, 10)
    assert t["cuts"] == [2.0, 4.0] and t["drop"] == [0, 1]
    assert add_cut({"on": True, "cuts": [4], "drop": [0]}, 6, 10)["drop"] == [0]
    legacy = add_cut({"on": True, "start": 1, "end": 8}, 5, 10)
    assert legacy["cuts"] == [1.0, 5.0, 8.0] and legacy["drop"] == [0, 3]


def test_remove_and_toggle():
    assert remove_cut({"on": True, "cuts": [2, 4], "drop": [1]}, 0, 10) == {"on": True, "cuts": [4.0], "drop": []}
    assert remove_cut({"on": True, "cuts": [2, 4], "drop": [1]}, 1, 10)["cuts"] == [2.0]
    assert toggle({"on": True, "cuts": [2], "drop": []}, 1, 10)["drop"] == [1]


def test_time_mapping():
    r = [(0.0, 2.0), (4.0, 10.0)]
    assert boundaries(r) == [2.0]
    assert source_to_output(5.0, r) == 3.0 and output_to_source(3.0, r) == 5.0
