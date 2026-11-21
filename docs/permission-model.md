# 权限模型：两级身份（集市用户 / 管理员）

> 2026-11-21 · 更正版：`internal` 是集市用户，不是管理员；管理员由 unisso `is_admin` 决定。

## 0. 结论

- **集市用户** = unisso `internal=true`（院内身份，所有集市用户）。
- **管理员** = unisso `is_admin=true`（集市用户的子集；`is_super_admin` 也算）。
- **`internal` 不等于管理员。** 管理员必须 `internal=true` 且 `is_admin=true`。
- **集市用户只能聊天；管理员进控制台、ops、验收、派工。**
- **不建本地角色表。** unisso 是唯一身份源，Skein 只认 `internal` 和 `is_admin` 两个标志。
- **不新开界面。** 集市用户用聊天页，管理员用控制台。

## 1. 两个身份

| 身份 | unisso 标志 | 能做什么 | 不能做什么 |
| --- | --- | --- | --- |
| 集市用户 | `internal=true`、`is_admin=false` | 和小秋对话；看自己的会话 | 控制台、ops、工单、节点、日志、别人的会话 |
| 管理员 | `internal=true`、`is_admin=true`（或 `is_super_admin=true`） | 控制台、ops、工单、验收、派工、配置 | 删 pod（红线）；自动合入（红线） |
| 外部用户 | `internal=false` | 无 | 聊天、控制台、ops、内部接口都不进 |

所有管理员权限相同。对 1–3 人的维护团队够用；人多了再谈分级。

## 2. 代码落点

### 2.1 主体

`Principal` 带四个关键字段：

```text
sub         unisso 主体 ID
internal    true = 集市用户（院内）
admin       true = unisso is_admin 或 roles 里有 admin
super_admin true = unisso is_super_admin 或 roles 里有 super_admin
```

`principal_from()` 同时读 `is_admin`、`is_super_admin`、`roles`；`is_super_admin` 自动算 admin。

### 2.2 入口策略

`SsoAuth.verify(cookies, level)` 的 `level` 只有两个：

| 入口 | level | 判定 |
| --- | --- | --- |
| `/console/*`、`/api/console/*`、`/ops/*`、`/internal/*`（人类） | `admin` | `internal=true` 且 `admin=true` |
| chat（`/api/v1/agent/*`、`/task/*`）、`/api/session?surface=market` | `market` | `internal=true` |

- 缓存只缓存“会话对应的主体”，每次请求按入口重新判；先走 market 再走控制台不能绕过。
- 会话登录走同一套策略：`surface=market` 放集市用户，默认控制台放管理员。
- 错误码：外部用户 `not_internal`，集市用户撞控制台 `not_admin`。

### 2.3 chat 身份头

- 控制面在 SSO 有效时给小秋 chat 带 `X-Skein-User: v1.<base64url>.<hmac>`。
- 载荷包含 `sub / internal / admin / super_admin / email / name / exp`，60 秒有效。
- 小秋按 `admin` 或 `super_admin` 映射：有则 `admin` 图，否则 `user` 图；**不再看 `internal`**。
- 浏览器自带的同名头会被剥掉，不能伪造；未配 `SKEIN_USER_SECRET` 时小秋忽略头、回退论坛 JWT。

## 3. unisso 改动

`backend/src/app/handlers_oauth.cpp` 的 `userClaimsJson()` 增加：

```cpp
u["is_admin"] = JsonValue(user.isAdmin);
u["is_super_admin"] = JsonValue(user.isSuperAdmin);
```

- `password/verify` 与 `session/verify` 都会返回这两个字段。
- 2026-11-21 已构建并重启 `deploy-5829cb069e42fd20`；自检 E2E 账号返回
  `internal=true, is_admin=false`（集市用户），说明字段生效。
- 当前 unisso 库里 `is_admin=1` 的活跃账号有 3 个；控制台只认这些账号。
- 代码提交：unisso `260021c`（`SysuMirror/unisso`，已推送）。

## 4. 部署

- 控制面：`/etc/skein/env` 的 `SKEIN_ASSERTION_KEY`。
- 小秋 chat：`/etc/skein/xiaoqiu-chat.env` 的 `SKEIN_USER_SECRET`。
- 两者必须是同一个随机值（600，不进仓库）。
- 顺序：unisso（已做）→ 小秋镜像 → 控制面。小秋未配 secret 时忽略头、回退 JWT，滚动发布安全。

### 部署状态（2026-11-21）

- 控制面 `5588dab`、chat 镜像 `xiaoqiu:1.0.93-skein-admin` 已上线，chat healthy。
- E2E 集市账号：控制台登录 403 `not_admin`；`surface=market` 200；market cookie 访问
  console / internal / ops 均 403；chat bootstrap（不带 JWT）200，close 200。
- 管理员控制台未用真实管理员账号实测；unisso 中 `is_admin=1` 的活跃账号 3 个，
  代码路径有单测覆盖。

## 5. 验收

- [ ] 集市用户（`internal=true`、`is_admin=false`）：`surface=market` 200；控制台、ops、`/internal/*` 403 `not_admin`。
- [ ] 外部用户（`internal=false`）：chat 与 market 都 403 `not_internal`。
- [ ] 管理员（`is_admin=true`）：控制台、ops 200；chat 身份头 `admin=true`，小秋 `admin` 图。
- [ ] 非管理员 chat 身份头 `admin=false`，小秋 `user` 图。
- [ ] 先走 market 再走控制台不绕过。
- [ ] 浏览器伪造 `X-Skein-User` 被剥掉。
- [ ] 不新增角色表、不新增页面。

## 6. 一句话

**unisso 的 `internal` 是“集市用户”，`is_admin` 才是“管理员”；Skein 只认这两个标志。**
