"""UniSSO 应用注册工具（服务端运维脚本，非 Web 服务代码）。

用法：
    python3 unisso_clients.py list
    python3 unisso_clients.py ensure <slug> <name> <redirects> <scopes>

ensure 会创建（或重置）一个内接应用：kind=internal、status=approved、m2m_enabled=1，
并把 client_id / client_secret 写到 /root/unisso-migration/clients/<slug>.env（600）。

2026-11-19 修复：原版 INSERT 硬编码 organization_id，与当前 applications 表不一致；
现在按 SHOW COLUMNS 的实际列拼 INSERT，缺 organization_id 时自动跳过。
"""

import json
import os
import secrets
import sys
import uuid

import bcrypt
import pymysql

OWNER = "2db389ae-8afa-47bd-af46-5304d1ef072a"  # sse_561 super admin
ORG = "00000000-0000-0000-0000-000000000001"  # default SSE org
ENV_PATH = "/root/unisso-panel.env"


def env():
    data = {}
    for line in open(ENV_PATH):
        if "=" in line:
            key, value = line.rstrip("\n").split("=", 1)
            data[key] = value
    return data


E = env()


def conn():
    return pymysql.connect(
        host="127.0.0.1",
        port=25006,
        user=E["MYSQL_USER"],
        password=E["MYSQL_PASSWORD"],
        database=E["MYSQL_DB"],
        charset="utf8mb4",
        autocommit=True,
    )


def list_apps():
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT name,client_id,redirect_uris,allowed_scopes,is_active,"
            "m2m_enabled,kind,status FROM applications ORDER BY created_at"
        )
        for row in cur.fetchall():
            print("\t".join(str(x) for x in row))


def ensure(slug, name, redirects, scopes):
    redirects = [x.strip() for x in redirects.split(",") if x.strip()]
    scopes = [x.strip() for x in scopes.split(",") if x.strip()]
    raw_secret = "sk_" + secrets.token_urlsafe(32)
    secret_hash = bcrypt.hashpw(raw_secret.encode(), bcrypt.gensalt(rounds=12)).decode()
    client_id = "app_" + secrets.token_hex(8)
    with conn() as c, c.cursor() as cur:
        cur.execute("SHOW COLUMNS FROM applications")
        columns = {row[0] for row in cur.fetchall()}
        cur.execute("SELECT id, client_id FROM applications WHERE name=%s LIMIT 1", (name,))
        row = cur.fetchone()
        if row:
            app_id, client_id = row
            cur.execute(
                "UPDATE applications SET redirect_uris=%s, allowed_scopes=%s,"
                " client_secret=%s, is_active=1, m2m_enabled=1, kind='internal',"
                " status='approved', updated_at=NOW() WHERE id=%s",
                (
                    json.dumps(redirects, ensure_ascii=False),
                    json.dumps(scopes, ensure_ascii=False),
                    secret_hash,
                    app_id,
                ),
            )
        else:
            app_id = str(uuid.uuid4())
            values = {
                "id": app_id,
                "client_id": client_id,
                "client_secret": secret_hash,
                "name": name,
                "description": None,
                "icon": None,
                "redirect_uris": json.dumps(redirects, ensure_ascii=False),
                "allowed_scopes": json.dumps(scopes, ensure_ascii=False),
                "homepage_url": None,
                "callback_url": None,
                "is_active": 1,
                "is_confidential": 1,
                "owner_id": OWNER,
                "m2m_enabled": 1,
                "kind": "internal",
                "status": "approved",
            }
            if "organization_id" in columns:
                values["organization_id"] = ORG
            present = [key for key in values if key in columns]
            placeholders = ", ".join(["%s"] * len(present))
            cur.execute(
                f"INSERT INTO applications ({', '.join(present)}, created_at, updated_at)"
                f" VALUES ({placeholders}, NOW(), NOW())",
                [values[key] for key in present],
            )
    path = "/root/unisso-migration/clients/" + slug + ".env"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("UNISSO_CLIENT_ID=" + client_id + "\n")
        f.write("UNISSO_CLIENT_SECRET=" + raw_secret + "\n")
        f.write("UNISSO_ISSUER=https://sso.ssemarket.cn\n")
        f.write("UNISSO_REDIRECT_URIS=" + ",".join(redirects) + "\n")
        f.write("UNISSO_SCOPES=" + ",".join(scopes) + "\n")
    os.chmod(path, 0o600)
    print(name + "\t" + app_id + "\t" + client_id + "\tsecret_written=" + path)


if __name__ == "__main__":
    if sys.argv[1] == "list":
        list_apps()
    elif sys.argv[1] == "ensure":
        ensure(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
    else:
        print(__doc__)
