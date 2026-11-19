from skein.admission import Event
from skein.router.context import compact, event_context, inline_text, spill_text


def test_compact_collapses_whitespace() -> None:
    assert compact("  a\n\n b\t c  ") == "a b c"


def test_inline_truncates_at_limit() -> None:
    text = "字" * 30
    result = inline_text(text, 10)
    assert result == "字" * 10 + "…"


def test_spill_returns_remainder() -> None:
    text = "a" * 12
    inline, spilled = spill_text(text, 5)
    assert inline == "aaaaa…"
    assert spilled == "a" * 7


def test_event_context_inlines_summary_and_keeps_size() -> None:
    event = Event(
        source="forum_comment",
        origin_id="c1",
        text="问" * 30,
        mentioned=True,
        parent_id="p1",
    )
    context = event_context(event, 0, "t1", limit=10)
    assert context["index"] == 0
    assert context["thread_id"] == "t1"
    assert context["text"] == "问" * 10 + "…"
    assert context["text_chars"] == 30
    assert context["mentioned"] is True
    assert context["parent_id"] == "p1"
