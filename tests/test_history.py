from ralenti.core.history import History


def test_undo_redo_and_coalescing():
    h = History()
    h.record({"v": 0}, "a", now=0.0)
    h.record({"v": 1}, "a", now=0.5)        # same setting, quick: one step
    h.record({"v": 2}, "b", now=0.6)
    assert h.back({"v": 3}) == {"v": 2}
    assert h.back({"v": 2}) == {"v": 0}
    assert h.back({"v": 0}) is None
    assert h.forward({"v": 0}) == {"v": 2}
    h.record({"v": 2}, "c", now=5.0)
    assert h.redo == []
