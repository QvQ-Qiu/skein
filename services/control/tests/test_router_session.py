from skein.admission import Event
from skein.router.session import RouterSession


def event(index: int) -> Event:
    return Event(source="forum_comment", origin_id=f"c{index}", text=f"问题 {index}")


def test_drain_is_bounded_and_preserves_order() -> None:
    session = RouterSession()
    for index in range(5):
        session.submit(event(index), f"t{index}")
    batch = session.drain(2)
    assert [item.event.origin_id for item in batch] == ["c0", "c1"]
    assert session.pending_count == 3
    assert session.drain(10)[0].event.origin_id == "c2"


def test_queue_overflow_drops_oldest() -> None:
    session = RouterSession(max_pending=2)
    session.submit(event(0), "t0")
    session.submit(event(1), "t1")
    assert session.submit(event(2), "t2") is True
    assert [item.event.origin_id for item in session.drain(10)] == ["c1", "c2"]
    assert session.dropped_count == 1


def test_history_is_bounded() -> None:
    session = RouterSession(max_history=3)
    for index in range(6):
        session.remember("user", f"消息 {index}")
    assert [item["content"] for item in session.history()] == ["消息 3", "消息 4", "消息 5"]


def test_remember_batch_records_summary() -> None:
    session = RouterSession()
    session.submit(event(0), "t0")
    batch = session.drain(1)
    session.remember_batch(batch, ["dispatch"])
    history = session.history()
    assert history[0]["role"] == "user"
    assert "问题 0" in history[0]["content"]
    assert history[1] == {"role": "assistant", "content": "决策：0:dispatch"}
