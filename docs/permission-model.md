# 权限模型：两级身份，一个开关

> 2026-11-21 · 收敛版：只有集市用户和管理员；管理员由 UniSSO 的 `internal` 标志决定。
> 不做本地角色表，不做 reviewer/operator/superadmin 分级。

## 0. 结论

- **两个身份：集市用户（`internal=false`）和管理员（`internal=true`）。**
- **UniSSO 说谁是管理员，谁就是管理员。** 不在 Skein 里再建一套角色。
- **集市用户只能聊天；管理员能进控制台、ops、验收、派工、配置。**
- **不新开界面。** 集市用户用聊天页，管理员用控制台；以后小秋 chat/ops 并进来，还是这两级。

## 1. 两个身份

| 身份 | UniSSO 标志 | 能做什么 | 不能做什么 |
| --- | --- | --- | --- |
| 集市用户 | `internal=false` | 和小秋对话；看自己的会话 | 控制台、ops、工单、节点、日志、别人的会话 |
| 管理员 | `internal=true` | 控制台、ops、工单、验收、派工、配置 | 删 pod（红线）；自动合入（红线） |

所有管理员权限相同。对 1–3 人的维护团队够用；人多了再谈分级。

## 2. 现在代码怎么改（三处）

### 2.1 把“必须 internal”从全局挪到入口

现在 `SsoConfig.require_internal=True` 是全局开关，把集市用户挡在 chat 的 SSO 探测外面。改成按入口判断：

| 入口 | 要求 |
| --- | --- |
| `/console/*`、`/api/console/*` | `internal=true` |
| `/ops/*` | `internal=true` |
| `/internal/*`（人类会话） | `internal=true` |
| chat（`/api/v1/agent/*`、`/task/*`） | 允许 `internal=false` |
| `/api/session?surface=market` | 允许 `internal=false` |

### 2.2 chat 把 SSO 身份带给小秋

现在 chat 透传的是论坛 JWT。改成（或先兼容）由 Skein 带一个签名头：

```text
X-Skein-User: <sub>|<internal>|<email>|<exp>.<HMAC>
```

- 小秋按 `sub` 绑定会话；`internal=false` 只能进 `user` 图，`internal=true` 可进 `admin` 图。
- 不再让客户端自己声称角色；角色只来自 UniSSO 的 `internal` 标志。
- 论坛 JWT 在迁移期继续兼容，但只当集市用户，不给管理员权限。

### 2.3 控制台和 ops 不动权限逻辑

- 能登录进来的 `internal=true` 用户就是管理员；现在就是这样，不用改。
- `qa_proxy.py` 给 ops 签固定 `panel_sub` 的问题**先不动**：它不影响“谁能进”，只影响“ops 审计里显示是谁”。以后想查人再改成真实 `sub`。
- 不需要 `principals` / `role_bindings` / `service_tokens` 这几张表。

## 2.4 已实现（2026-11-21）

- `/api/session?surface=market` 与 `POST /api/session/login {surface:"market"}` 允许 `internal=false`；控制台、ops、`/internal/*` 仍要求 `internal=true`。
- `SsoAuth` 缓存只缓存“会话对应的主体”，`internal` 检查每次请求都做；先走 market 再走控制台不能绕过。
- chat 代理在 SSO 有效时带 `X-Skein-User: v1.<base64url>.<hmac>`；浏览器自带的同名头会被剥掉，不能伪造。
- 小秋 `app/api/deps.py` 校验身份头：`internal=true` → `admin`，`false` → `user`；`SKEIN_USER_SECRET` 未配置时忽略头、回退 JWT。
- 部署：`/etc/skein/env` 的 `SKEIN_ASSERTION_KEY` 与 `/etc/skein/xiaoqiu-chat.env` 的 `SKEIN_USER_SECRET` 必须是同一个随机值；先发小秋镜像，再发控制面，最后重启两边。
- 测试：控制面 209 个测试、小秋身份头 5 个用例通过。

## 3. 不需要做的

- 不需要 reviewer / operator / admin / superadmin 分级。
- 不需要“我的反馈”页面；集市用户聊天即可。以后想让用户看处理进度，再加一个可选页面，不是新系统。
- 不需要拆机器令牌；node / watchdog 继续用现在的 `SKEIN_TOKEN`。这是机器，不是人的权限级。
- 不需要新界面；控制台就是管理员的界面，聊天就是集市用户的界面。

## 4. 知道就行的风险

- **所有管理员一样大**：一个人能验收、能派工、能改提示词。小团队可接受；人多了再分级。
- **机器令牌共享**：泄露一把影响所有机器。以后要拆再拆，不影响两级模型。
- **`internal` 必须可靠**：如果 UniSSO 的 `internal` 就是管理员标志，直接用；如果另有 admin 字段，换成那个字段即可。兜底方案是 `UNISSO_ADMIN_SUBS` 名单。

## 5. 验收

- [ ] 集市用户登录：能聊天；打开 `/console/`、`/ops/`、`/internal/*` 全部 403。
- [ ] 管理员登录：能进控制台；能验收、派工、看 ops。
- [ ] 同一浏览器切换账号：会话按 UniSSO 走，不串号。
- [ ] 小秋 chat 能看到 `sub`，会话不串用户。
- [ ] ops 仍只允许 `internal=true`。
- [ ] 不新增页面、不新增角色表。

## 6. 一句话

**UniSSO 已经把“集市用户 / 管理员”分好了；Skein 只需要认这个标志，不要再造第二套角色。**
