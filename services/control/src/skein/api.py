"""控制面的 HTTP 接口：健康检查、transcript 上传、Outbox 写入。"""

from __future__ import annotations

import base64
import binascii
import sqlite3

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from skein.outbox import EmitResult, Outbox
from skein.session_store import SessionStore, TurnConflict


class TurnIn(BaseModel):
    session_id: str
    turn_index: int = Field(ge=0)
    data: str
    head_sha: str | None = None
    branch: str | None = None


class EmitIn(BaseModel):
    key: str
    kind: str
    object: str
    payload: dict = Field(default_factory=dict)


def create_app(
    conn: sqlite3.Connection,
    blob_root: str,
    token: str,
    writer,
    effects=None,
) -> FastAPI:
    app = FastAPI(title="skein-control", version="0.1.0")
    sessions = SessionStore(conn, blob_root)
    outbox = Outbox(conn, writer)
    effect_rows = effects if effects is not None else conn

    def auth(x_skein_token: str = Header(default="")) -> None:
        if not token or x_skein_token != token:
            raise HTTPException(status_code=401, detail="unauthorized")

    @app.get("/health/live")
    def live() -> dict:
        return {"status": "ok"}

    @app.post("/internal/worker/turn", dependencies=[Depends(auth)])
    def upload_turn(body: TurnIn) -> dict:
        try:
            data = base64.b64decode(body.data, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=400, detail="invalid base64")
        try:
            sessions.open(body.session_id)
            turn = sessions.append_turn(
                body.session_id, body.turn_index, data, body.head_sha, body.branch
            )
        except TurnConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"session_id": turn.session_id, "turn_index": turn.turn_index, "sha256": turn.sha256}

    @app.post("/internal/outbox/emit", dependencies=[Depends(auth)])
    def emit(body: EmitIn) -> dict:
        result = outbox.emit(body.key, body.kind, body.object, body.payload)
        if result == EmitResult.FAILED:
            raise HTTPException(status_code=502, detail="effect failed")
        return {"result": result}

    return app
