from skein.forum_source import ForumSource


class FakeQuery:
    def __init__(self, posts, comments):
        self.posts = posts
        self.comments = comments
        self.calls = []

    def __call__(self, sql, params):
        self.calls.append((sql, params))
        return self.posts if "v_posts_public" in sql else self.comments


def test_posts_and_comments_become_items():
    query = FakeQuery(
        posts=[{"postID": 1, "title": "白屏", "ptext": "切换会话时白屏", "ts": 100.0, "author_alias": "a1"}],
        comments=[
            {"pcommentID": 9, "ptargetID": 1, "pctext": "@小秋 帮忙", "ts": 101.0, "author_alias": "a2"}
        ],
    )
    items = ForumSource(query).fetch(since=99.0, limit=50)
    assert [i.origin_id for i in items] == ["1", "9"]
    assert items[0].text.startswith("白屏")
    assert items[1].parent_id == "1"
    assert items[1].mentioned is True
    assert items[1].actor == "a2"


def test_mention_is_detected_only_in_comments():
    query = FakeQuery(
        posts=[{"postID": 1, "title": "", "ptext": "@小秋 在帖子里", "ts": 100.0, "author_alias": None}],
        comments=[],
    )
    items = ForumSource(query).fetch(since=99.0, limit=10)
    assert items[0].mentioned is False


def test_items_are_sorted_by_time():
    query = FakeQuery(
        posts=[{"postID": 2, "title": "b", "ptext": "", "ts": 200.0, "author_alias": None}],
        comments=[
            {"pcommentID": 1, "ptargetID": 2, "pctext": "c", "ts": 150.0, "author_alias": None}
        ],
    )
    items = ForumSource(query).fetch(since=0.0, limit=10)
    assert [i.created_at for i in items] == [150.0, 200.0]


def test_lookback_when_no_watermark():
    query = FakeQuery(posts=[], comments=[])
    ForumSource(query, lookback_s=3600).fetch(since=None, limit=10)
    _, params = query.calls[0]
    assert params[1] == 10
