# 架构：一次调用是怎么被过滤的

```
外部 AI ──► https://域名/<窗口路径>          （cloudflared 隧道，路径分流到端口）
              │
              ▼
        core/server.py（一个窗口一个进程）
              │
              ├─ ⓪ 这个窗口存在吗？          registry 里没有 → 拒
              │
              ├─ ① 根界闸   realpath 必须在 root 内            → 拒「路径越界」
              ├─ ② exclude 闸  命中 exclude（含子树）          → 拒「不在给看范围」
              ├─ ③ include 闸  必须命中 include 白名单          → 拒「不在给看范围」
              ├─ ④ 拉黑闸  .env*/.pem/id_rsa*/credentials*/.db… → 拒「命中安全拉黑规则」
              │
              ├─ 内容读取（只读：list_files/read_file/search）
              │     └─ 输出脱敏：sk-… / ghp_… / AIza… / AKIA… / Bearer … / 私钥块 → «REDACTED»
              │
              ├─ 写工具（write_file/edit_file/make_dir/delete_file）
              │     ├─ 两级锁：write.enabled（配置）+ 运行时开关（switch.py）——默认全关
              │     ├─ 写前：备份到 ~/.lighthouse/backups/<窗口>/<时间戳>-<路径>
              │     ├─ 写后：记 sha256（前后各一份）进审计
              │     └─ delete_file 必须 confirm=true；edit_file 默认要求唯一匹配
              │
              └─ 审计：每次调用（含被拒）写一行 JSON
                    ~/.lighthouse/audit/<窗口>.jsonl
```

## 为什么这样分层

| 决策 | 原因 |
|---|---|
| 一窗一进程 | 一个窗口崩了不影响别的；不同窗口不同端口，范围完全隔离；可以单独重启 |
| 范围写 JSON 不写代码 | 改范围不用改代码、不用重新审代码；改了立刻生效（服务每次调用现读注册表与开关） |
| fail-closed | 安全组件最怕的不是「拦不住」，是「以为自己拦住了」。判定不了就拒，账上留痕 |
| 默认拉黑不可关 | 人的记性不如机器的规则：`.env` 这类文件被误开过一次就够致命 |
| 写开关放运行时 | 「今天让它帮我改一下」是临场需求；改配置文件 + 重启服务太重，会促使用户干脆永久放开 |
| 单域名 + 路径分流 | 加窗口不再动 DNS，也不用给每个窗口一条证书/记录 |
| 无状态 HTTP（`stateless_http`） | 工具全是请求-应答式，不需要会话状态。有状态时**服务一重启，客户端手里的会话就作废**，而不少客户端（如 WorkBuddy）不自动重连、继续拿旧会话 id 调用 → 服务端回「Session not found」→ 对方误以为「找不到 mcp 环境」。无状态后重启对已连客户端完全无感 |

## 数据流（写操作）

```
AI 调 write_file
   → 闸门四连（越界/exclude/include/拉黑）→ 拒 或 继续
   → 写开关两级检查 → 关着就拒（明确告诉它"去让你开开关"）
   → 旧内容备份 + 记 sha_before
   → 落盘
   → 记 sha_after + 审计行（ok=true, bytes, backup 路径）
```

## 状态目录

```
~/.lighthouse/                 （可用 LIGHTHOUSE_STATE 覆盖）
├── audit/<窗口>.jsonl          每次调用一行
├── backups/<窗口>/…            写操作前的原文备份
├── state/window-write.json     写开关状态
└── logs/window-<窗口>.log      服务日志（launchd/systemd 重定向到这里）
```
