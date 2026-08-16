from __future__ import annotations

from stackman.stack_ids import new_auto_stack_id


def test_new_auto_stack_id_shape_and_uniqueness() -> None:
    ids = {new_auto_stack_id() for _ in range(200)}
    assert len(ids) == 200
    for sid in ids:
        assert sid.startswith("sm_")
        assert len(sid) == len("sm_") + 12
        rest = sid.removeprefix("sm_")
        assert len(rest) == 12
        int(rest, 16)
