"""论坛来源：读公开视图，转成 RawItem。

只读两视图（v_posts_public / v_pcomments_public），不碰基表。
连接由外部注入，测试用假查询。
"""

from __future__ import annotations

import time
from typing import Callable

from skein.ingest import RawItem

Query = Callable[[str, tuple], list[dict]]

POSTS_SQL = (
    "SELECT postID, title, ptext, UNIX_TIMESTAMP(post_time) AS ts, author_alias"
    " FROM v_posts_public WHERE post_time > FROM_UNIXTIME(%s)"
    " ORDER BY post_time ASC LIMIT %s"
)

COMMENTS_SQL = (
    "SELECT pcommentID, ptargetID, pctext, UNIX_TIMESTAMP(time) AS ts, author_alias"
    " FROM v_pcomments_public WHERE time > FROM_UNIXTIME(%s)"
    " ORDER BY time ASC LIMIT %s"
)


class ForumSource:
    name = "forum"

    def __init__(
        self,
        query: Query,
        lookback_s: int = 86400,
        mention: str = "@小秋",
    ) -> None:
        self._query = query
        self._lookback_s = lookback_s
        self._mention = mention

    def fetch(self, since: float | None, limit: int) -> list[RawItem]:
        start = since if since is not None else time.time() - self._lookback_s
        items: list[RawItem] = []

        for row in self._query(POSTS_SQL, (start, limit)):
            title = (row.get("title") or "").strip()
            body = (row.get("ptext") or "").strip()
            text = f"{title}\n{body}".strip() if title else body
            items.append(
                RawItem(
                    source="forum_post",
                    origin_id=str(row["postID"]),
                    text=text,
                    created_at=float(row["ts"]),
                    actor=row.get("author_alias"),
                )
            )

        for row in self._query(COMMENTS_SQL, (start, limit)):
            body = row.get("pctext") or ""
            items.append(
                RawItem(
                    source="forum_comment",
                    origin_id=str(row["pcommentID"]),
                    text=body,
                    created_at=float(row["ts"]),
                    parent_id=str(row["ptargetID"]),
                    actor=row.get("author_alias"),
                    mentioned=self._mention in body,
                )
            )

        items.sort(key=lambda item: item.created_at)
        return items[:limit]


def make_mysql_query(
    host: str, port: int, user: str, password: str, database: str = "ssemarket"
) -> Query:
    import pymysql

    def query(sql: str, params: tuple) -> list[dict]:
        conn = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            charset="utf8mb4",
            autocommit=True,
            cursorclass=pymysql.cursors.DictCursor,
        )
        try:
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
                return list(cursor.fetchall())
        finally:
            conn.close()

    return query
