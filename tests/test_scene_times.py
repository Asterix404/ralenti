from ralenti.core.edit import scene_times


def test_scene_times_follow_the_kept_parts():
    ranges = [(0.0, 4.0), (8.0, 12.0)]
    assert scene_times([2.0, 6.0, 9.5], ranges) == [2.0, 5.5]     # 6.0 is in a removed part
    assert scene_times([3.99], ranges) == []                       # on a join: the join already cuts there
    assert scene_times(None, ranges) == []
    assert scene_times([1.5], [(1.0, 5.0)]) == [0.5]               # single trim: relative to its start
