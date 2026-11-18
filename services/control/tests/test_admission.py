from skein.admission import Decision, Event, SourcePolicy, Verdict, decide


def test_mention_is_processed():
    d = decide(Event(source="forum_comment", origin_id="1", text="帮我看下这个", mentioned=True))
    assert d.verdict is Verdict.PROCESS
    assert d.reason == "explicit_mention"


def test_short_mention_is_recorded_not_processed():
    d = decide(Event(source="forum_comment", origin_id="2", text="@", mentioned=True))
    assert d.verdict is Verdict.RECORD
    assert d.reason == "mention_too_short"


def test_allowed_followup_is_processed():
    d = decide(
        Event(
            source="forum_comment",
            origin_id="3",
            text="继续说说",
            in_allowlist=True,
            replies_to_agent=True,
        )
    )
    assert d.verdict is Verdict.PROCESS
    assert d.reason == "allowed_followup"


def test_followup_outside_allowlist_is_ignored():
    d = decide(
        Event(source="forum_comment", origin_id="4", text="继续说说", replies_to_agent=True)
    )
    assert d.verdict is Verdict.IGNORE


def test_allowlisted_unmentioned_is_only_recorded():
    d = decide(Event(source="forum_comment", origin_id="5", text="随便聊聊", in_allowlist=True))
    assert d.verdict is Verdict.RECORD
    assert d.reason == "allowlisted_unmentioned"


def test_allowlisted_can_stop_recording():
    policy = SourcePolicy(record_unmentioned=False)
    d = decide(Event(source="forum_comment", origin_id="6", text="随便聊聊", in_allowlist=True), policy)
    assert d.verdict is Verdict.IGNORE


def test_allowlisted_without_mention_requirement_is_processed():
    policy = SourcePolicy(require_mention=False)
    d = decide(Event(source="forum_comment", origin_id="6b", text="继续讲讲", in_allowlist=True), policy)
    assert d.verdict is Verdict.PROCESS
    assert d.reason == "allowlisted_no_mention_required"


def test_stranger_is_ignored():
    d = decide(Event(source="forum_comment", origin_id="7", text="路人发言"))
    assert d.verdict is Verdict.IGNORE


def test_disabled_source_is_ignored():
    d = decide(Event(source="rss", origin_id="8", text="x", mentioned=True), SourcePolicy(accept=False))
    assert d.verdict is Verdict.IGNORE


def test_decision_is_frozen():
    d = Decision(Verdict.IGNORE, "x")
    try:
        d.reason = "y"
    except Exception:
        return
    raise AssertionError("Decision should be immutable")
