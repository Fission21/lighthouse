<div align="center">

# 灯塔 · Lighthouse

**Filesystem MCP gives an AI access. Lighthouse governs that access.**
**给 AI 开门之前，先把锁、记录和审批装上。**

四道闸 · 出口脱敏 · 全程审计 · 写开关（默认全只读）· 提权要你点头 · 开窗先问你范围

<small>[English README](README_EN.md)</small> · <small>[和 filesystem MCP 差在哪](#和-filesystem-mcp-差在哪)</small>

<small>作者 · 诗人 & CC</small>

</div>

---

## 先说问题

你在网页版 ChatGPT（或任何 AI）里说「看看我本地这个项目」。今天的标准做法是：起一个
filesystem MCP，套上 Cloudflare 隧道或 Tailscale，把 URL 交给它。能用——但它不回答下面五个问题：

| | 问题 | 典型 filesystem MCP |
|---|---|---|
| ① | 它能**读到什么**？ | 挂载点以下的一切，包括 `.env`、私钥、你早忘了的备份 |
| ② | 它**什么时候读了什么**？ | 通常没有任何记录 |
| ③ | 读到的密钥会不会**被写进回答**、发给模型厂商？ | 没有出口检查 |
| ④ | 它能不能**改**文件？ | 很多实现默认读写全给 |
| ⑤ | 它想**要更多权限**时，谁批准？ | 没有这个概念 |

filesystem MCP 回答的是「**能不能访问**」。上面这五条，是「**这次访问怎么被治理**」。

## 灯塔的答案：Agent 不直接碰文件

它必须穿过一条带闸门的通道：

```
                       网页 AI / Agent
                             │
                             │ ① 我要读 X（想扩范围时同样走这条路）
                             ▼
                   ┌───────────────────┐
                   │ 请求权限          │  request_access —— 只能申请，无法自授
                   ├───────────────────┤
                   │ Policy            │  include / exclude / 默认拉黑 / 常驻上限
                   ├───────────────────┤
                   │ Owner 批准        │  默认逐次点头；也可事先声明策略，松紧你定
                   ├───────────────────┤
                   │ Redaction         │  出口检查：sk-… / ghp_… / 私钥块 → «REDACTED»
                   ├───────────────────┤
                   │ Audit             │  每次调用一行，被拒的也记
                   └───────────────────┘
                             │
                             ▼
                          你的文件
```

**真正的差别在信任模型。** filesystem MCP 假设「客户端可信」——对面是你自己机器上、你自己
启动的进程；灯塔假设「客户端不可信」——对面是 OpenAI 的服务器，从公网访问你的磁盘。
客户端不可信，所以每一层都要能独立说「不」，每一次都要留下痕迹。

> 反过来说得很直白：**如果你只是让自己机器上的本地 agent（Claude Desktop、Cursor 之类）
> 读文件，filesystem MCP + 隧道就够用，不必上灯塔。** 灯塔解决的是另一种局面——
> 对面那台机器不是你的，你却要把磁盘开出去。

## 30 秒看懂效果

有意思的不是「它能读文件」，而是**它伸手去拿不该拿的东西时会发生什么**：

```
$ tail -5 ~/.lighthouse/audit/miji.jsonl          # 真实输出，时间戳与部分字段已截短
{"tool":"read_file","args":{"path":"tools/kb.py"},"ok":true,"bytes":22332,"redactions":0}
{"tool":"read_file","args":{"path":"docs/.env"},"ok":false,"reason":"命中安全拉黑规则（密钥/凭据/数据库类）"}
{"tool":"read_file","args":{"path":"../../etc/hosts"},"ok":false,"reason":"路径越界（窗口外一律拒）"}
{"tool":"read_file","args":{"path":".git/logs/HEAD"},"ok":false,"reason":"命中安全拉黑规则"}
{"tool":"request_access","args":{"include":["**"]},"ok":true,"pending":true,"note":"没有生效中的预授权窗口"}
```

读得到的是**你圈给它的**；`.env` 与 `.git` 连门都进不去（就算它申请「整个仓库」也一样）；
想扩范围只能留一条**待批申请**等你点头。这份日志就是全部真相，没有第二套账。

## 和 filesystem MCP 差在哪

| | filesystem MCP（+ 隧道） | 灯塔 |
|---|---|---|
| 解决的问题 | 让 AI **能访问**你的文件 | 让这次访问**被治理** |
| 授权粒度 | 挂载点 | 每扇窗独立的 include / exclude / 上限 |
| 密钥 | 靠你自己别放进目录 | **默认拉黑**：`.env*`、`*.pem`、`id_rsa*`、`*secret*`、`*token*`、整个 `.git/` —— 无条件拒绝，include 写多宽都压不过 |
| 出口内容 | 原样返回 | 出口脱敏：`sk-… / ghp_… / AKIA… / 私钥块` → `«REDACTED»`，检索片段也过一遍 |
| 写入 | 常默认开放 | **两级锁**，默认全只读；要写先手动开（可 30 分钟自动关），写前备份、写后记 sha256 |
| 提权 | 无此概念，改配置=重启服务 | Agent 只能 `request_access` 申请；默认落成待批，你 `approve`；也可用 `elevate`（限时）或 `auto-grant`（常驻策略，可带上限） |
| 审计 | 通常没有 | 每次调用一行（含被拒的），`~/.lighthouse/audit/<窗口>.jsonl` |
| 信任模型 | 客户端可信（本地进程） | **客户端不可信**（公网、第三方服务器） |

两条都能让你「把本地文件给 AI 看」。区别是出事的时候，哪一边说得清发生了什么。

## 四道闸具体拦什么

| 你会担心的事 | 灯塔的回答 |
|---|---|
| 它会不会翻我别的目录？ | ① 根界闸：realpath 出 `root` 一律拒（符号链接穿透也拦） |
| 它会不会看到我的 `.env` / 私钥？ | ④ 默认拉黑：`.env* / *.pem / id_rsa* / credentials* / *.db`、整个 `.git/` 等无条件拒绝，include 写多宽都没用；大小写变体（`.ENV`）同样拦 |
| 它会看到我明确划出去的东西吗？ | ② exclude 闸：目录命中连子树一起排除（`PRIVATE/` 也拦） |
| 它会不会把我密钥抄进回答里？ | 出口脱敏：`sk-… / ghp_… / AIza… / AKIA… / Bearer … / 私钥块` → `«REDACTED»`，连检索片段也过一遍 |
| 它会不会改我的代码？ | **默认全只读。** 想让它动手，得你亲手开写开关（还能设 30 分钟自动关）；开着时每次改动先备份、后记 sha256 |
| 它会不会自己把范围扩大？ | **默认不会。** 它只能「申请」：`request_access` 默认只记成待批申请，批准权在你手里（`lighthouse.sh approve`），或你先开限时预授权窗口（`elevate`）。也可以给某扇窗声明常驻策略 `auto-grant`（可带 `--ceiling` 上限），之后上限内的申请直接生效——超出上限的照样要你点头。提权永远压不过 exclude 与密钥拉黑 |
| 我怎么知道它看过什么？ | 审计日志：每次调用一行（含被拒的），`~/.lighthouse/audit/<窗口>.jsonl` |
| 我怎么证明它真的挡得住？ | 自带五套测试（13 + 46 + 25 + 29 + 23 项，含一整套攻击性探测），一条命令跑完 |

## 跑起来需要什么（前置，先看这个）

灯塔分两档用，按你要达到的效果取件——省得装到一半才发现少东西：

| | ① 本机自己用（给本机 MCP 客户端读） | ② 出公网给网页 AI 看（含①全部） |
|---|---|---|
| **必需** | Python 3.10+、`mcp` 库、一条窗口声明 | **＋ `cloudflared` ＋ 一个 DNS 托管在 Cloudflare 的域名** |
| **不需要** | 域名、cloudflared、公网 | 公网 IP、路由器端口映射、开入站端口、证书申请 |

服务常驻用系统自带（macOS launchd / Linux systemd），无需另装；不想开机自启就前台手动跑。

> **最容易卡住的一条：域名。** 让网页 AI 看见，就需要一个**稳定的公网地址**；灯塔用的 Cloudflare
> 命名隧道，对外入口只能是你 Cloudflare 账户下某个域名的子域——**这一步绕不过去**。
> 这是 Cloudflare 的规则，不是灯塔的限制。已有域名的话，只是「挂到 Cloudflare → 建隧道 → 给子域加一条 DNS 记录」的事。

还没有域名？三条替代，都不用买：

- **ngrok 免费版** —— 送 1 个固定 dev 域名（`xxx.ngrok-free.app`，额度 1GB/月、2 万请求/月）；把隧道指向 `127.0.0.1:<窗口端口>` 即可，不用改灯塔代码（只是不走 `publish` 流水线）；
- **Tailscale Funnel** —— 所有套餐可用（含免费，beta），固定域名 `<设备>.<tailnet>.ts.net`，同样手工接；
- **Cloudflare 临时隧道（trycloudflare）** —— 不用域名，但 URL 每次重启就变，而且官方明确**不支持 SSE**，MCP 的 streamable-http 会用到事件流：它只够本地冒烟，别拿它接连接器。

网页 AI 那一侧还需要：ChatGPT 账号 + 打开开发者模式（设置 → 安全防护）。详见 [`docs/CHATGPT.md`](docs/CHATGPT.md)。

## 快速开始（4 步）

```bash
# 0) 依赖：Python 3.10+ 与 mcp 库
pip install mcp

# 1) 起一个示例窗口，先把链路跑通（不需要公网）
bash lighthouse.sh test            # 一键验收：起临时实例 → 跑五套测试 → 自动收拾
#    ✅ 通用冒烟 13/13   ✅ 只读审计 46/46   ✅ 写开关 25/25   ✅ 提权 29/29   ✅ 加固 23/23

# 2) 给【你自己的项目】开一扇窗
#    不指定范围时会【问你】要放多大 —— 范围由你定，它不做全开默认
bash lighthouse.sh new myproj ~/code/myproj
#    也可以一次说清：
bash lighthouse.sh new myproj ~/code/myproj --preset docs+code --title "我的项目"
bash lighthouse.sh start
bash lighthouse.sh url myproj      # 拿到本机地址，先自己试
```

到这一步，任何 MCP 客户端（本机的 Claude/Codex/Cursor 等）都能通过上面的地址读它了。

```bash
# 3) 出公网（让网页版 AI 也能看见）—— 需要：一个 DNS 托管在 Cloudflare 的域名（见上「跑起来需要什么」）
cloudflared tunnel login
cloudflared tunnel create lighthouse          # 记下输出的 UUID
cloudflared tunnel route dns <UUID> mcp.你的域名   # 必须写 UUID，写隧道名会认错隧道
# 把 UUID 和域名填进 config.json：tunnel_id / hostname
bash lighthouse.sh publish                    # 生成隧道配置 + 重启 + 健康检查
```

```bash
# 4) 网页 AI 侧：设置 → 连接器 →「创建应用」→ 填 https://你的域名/<窗口路径>
```

之后在对话里 @ 一下这个连接器，或者直接问它「读一下 README，告诉我验证口令」——
它会真去调工具，你这边审计日志能看到它读了什么。

## 日常命令

```bash
bash lighthouse.sh list                  # 有哪些窗口
bash lighthouse.sh status                # 服务 + 端口 + 范围 + 写开关总览
bash lighthouse.sh url <id>              # 查地址（本机 / 公网）
bash lighthouse.sh write <id> on 30      # 开 30 分钟写权限（到点自动关）
bash lighthouse.sh write <id> off        # 立刻回到只读
bash lighthouse.sh write status          # 现在谁能写
bash lighthouse.sh scope <id>            # 当前授权状态（额外范围 / 待批申请 / 预授权窗口）
bash lighthouse.sh approve <id>          # 批准它的提权申请
bash lighthouse.sh elevate <id> 30 --scope "src/**"   # 预授权：30 分钟内这类申请自动批
bash lighthouse.sh deny <id>             # 收回全部提权
bash lighthouse.sh test [id]             # 一键验收
bash lighthouse.sh doctor                # 体检：解释器 / 依赖 / 隧道 / 配置
```

## 对话里提权：想看更多，得你点头

场景：miji 那扇窗只给了文档。对话里你说「把代码也给它看」——它是怎么拿到的？

```
你（对话里）: 提高访问权限，允许看代码
   │
   ├─ agent 调 request_access(include=["src/**"], reason="用户要求看代码")
   │      ├─ 该窗口开了常驻策略 auto-grant，且申请在上限内 → 立即生效 ✅（你不用跑任何命令）
   │      ├─ 有生效中的预授权窗口（你之前跑过 elevate）且在上限内 → 立即生效 ✅
   │      └─ 都不是 / 超出上限 → 只记成【待批准申请】，范围一个字都不变 ⏸
   │                                └─ 你敲 approve → 立刻生效；`deny` 随时收回
   │
   └─ agent 转达：请在部署这台机器的终端里执行 `bash lighthouse.sh approve <窗口>`
```

要点：

- **默认 agent 无法自我提权**：没有你的批准，`request_access` 只能留下一条申请，`window_info` 里能查到；
- **常驻策略是「你事先授权」**：`bash lighthouse.sh auto-grant <窗口> on --ceiling "src/**,docs/**"`
  之后，落在上限内的申请直接生效、不用再跑命令；超出上限的照样转成待批。
  不设 `--ceiling` 就是「任何范围申请都自动生效」（密钥拉黑与 exclude 仍然拦得住）。
  策略是**实时读的**：`off` 一敲，下一次申请立刻回到 pending，不用重启服务；
- **网页那边点的是另一层**：ChatGPT 弹的「允许使用 X？」是**平台自己的**工具调用确认，不是灯塔的授权。
  开了自动授予之后，申请甚至可能**没有任何弹窗**就直接生效——所以 `--ceiling` 和审计日志才是你的知情手段。
  要多松多紧由你定：`off` 回到逐次批准，`--ceiling` 圈定最大范围，`deny` 随时收回。
- **提权不等于解禁**：`.env`、私钥、`exclude` 的目录，提权之后照样看不到（拉黑与排除压过一切授予）；
- **三种给法**：常驻策略（`auto-grant`）、事后批准（`approve`）、事前预授权窗口（`elevate <id> 30 --scope "src/**"`，到期自动失效）；
- **随时收回**：`deny <id>` 一把清空（额外范围 + 待批申请 + 预授权窗口）；`auto-grant <id> off` 关掉常驻策略。

这就是「把选择的权利交给你」的落地方式：**方便归方便，闸门永远在你手里。**

## 目录结构

```
lighthouse/
├── lighthouse.sh            # 唯一入口（new/start/stop/status/url/publish/write/test/doctor）
├── config.json              # 全局配置：域名 / 隧道 / 状态目录
├── windows.json             # 窗口注册表：每扇窗的给看范围写在这里
├── core/
│   ├── server.py            # 窗口 MCP 服务（四道闸 + 脱敏 + 审计 + 写工具 + 提权申请）
│   ├── config.py            # 配置读写
│   ├── scope.py             # 范围授权（grant / pending / arm / ceiling 上限判定）
│   ├── add_window.py        # 登记新窗口（不给范围时会问你）
│   ├── render_services.py   # 生成 launchd / systemd 服务定义
│   ├── render_ingress.py    # 生成隧道配置（单域名 + 路径分流）
│   └── switch.py            # 写开关
├── tests/
│   ├── smoke_window.py      # 通用冒烟 13 项（任何窗口都能测）
│   ├── audit_readonly.py    # 只读审计 46 项（含文件指纹前后比对）
│   ├── test_write.py        # 写开关 25 项（关=全拒 / 开=全流程 / 关回=只读）
│   ├── test_elevate.py      # 提权 29 项（申请≠授予 / 批准 / 收回 / 预授权 / 常驻策略 / 过期）
│   ├── test_hardening.py    # 闸门加固 23 项（大小写绕过 / .git / 路径逃逸 / 枚举面）
│   └── run_all_tests.sh     # 一键验收（自带临时实例，不碰线上）
├── demo/project/            # 示例项目（含验证口令，用来证明"真的读到了本地"）
└── docs/
    ├── ARCHITECTURE.md      # 一扇窗是怎么被四道闸过滤的
    ├── SECURITY.md          # 威胁模型与边界（该防的防，防不了的说清楚）
    ├── CHATGPT.md           # 接网页版 ChatGPT 的完整步骤（含踩坑）
    └── OPEN_A_WINDOW.md     # 开窗四步 + 交代给别的 AI 的话术模板（中文）
```

## 它是怎么做到既方便又安全的

**范围写在一处。** `windows.json` 里一条声明 = 一扇窗：

```json
"myproj": {
  "title": "我的项目",
  "root": "~/code/myproj",                 // 只看这棵树
  "include": ["README.md", "docs/**", "src/**"],   // 只给看这些
  "exclude": ["private/**"],               // 这些连子树一起排除
  "port": 8940,
  "path": "/w-myproj-6m1yo0",              // 路径带随机段（当弱口令）
  "visibility": "local",                   // local=私密，发布时会被拦下
  "write": { "enabled": true }             // 允许被开写权限（默认仍然关着）
}
```

改范围 = 改一个文件，不用动代码。

**判定失败就往拒绝走（fail-closed）。** 路径解析异常、匹配不出来、任何说不清的情况——一律拒绝，然后记进审计。

**多窗口共用一个域名。** 靠路径分流到各自端口：加窗口不用再动 DNS，`publish` 一条命令重建分流表。

## 依赖与致谢

| 依赖 | 用途 | 链接 |
|---|---|---|
| Python 3.10+ | 运行环境 | https://www.python.org/ |
| MCP Python SDK | 实现 MCP 服务端（streamable-http） | https://github.com/modelcontextprotocol/python-sdk |
| cloudflared | 把本机端口安全地放到公网（出站隧道，无需开端口） | https://github.com/cloudflare/cloudflared |
| macOS launchd / Linux systemd | 守护窗口服务（开机自启、挂了自动拉起） | 系统自带 |

**特别感谢**：Model Context Protocol 团队把「AI 怎么接外部世界」这件事做成了公开协议；
Cloudflare 的 cloudflared 让「不出站也安全」变成了默认选项。没有这两样，灯塔就只是一堆本地脚本。

## 踩坑速查（都是实际撞过的）

| 坑 | 现象 | 解法 |
|---|---|---|
| MCP SDK 的 DNS 重绑定保护 | 本机 200，公网域名 404 | 服务端设 `TransportSecuritySettings(enable_dns_rebinding_protection=False)`（已内置） |
| `cloudflared tunnel route dns` | 写了隧道**名字**，DNS 指向了另一条隧道 | 必须写隧道 **UUID** |
| 隧道复用 | 多个连接器挂同一隧道，请求被负载均衡到别的机器 → 404 | 给灯塔建**专用隧道** |
| 注册表位置 | 服务从 `core/` 找 `windows.json` 找不到 | 相对路径按**仓库根**解析（已修，见 core/server.py） |
| 网页 AI 回答卡在第一个字 | 其实生成完了 | 刷新页面 |
| ChatGPT 无法从本机直连 | —— | 灯塔全程走隧道出站，不需要公网 IP、不需要开端口 |

## 更新日志

- **v1.2** —— 常驻提权策略 `auto-grant`（`bash lighthouse.sh auto-grant <id> on [--ceiling "src/**,docs/**"] | off | status`）：
  开启后**上限内的申请立即生效，不用再跑本地命令**；超出上限仍转待批；策略实时读注册表，`off` 一敲立刻收紧（不用重启服务）；
  配置可疑一律 fail-closed。修两处：CLI 与服务的注册表路径统一（`LIGHTHOUSE_REGISTRY` > `WINDOW_REGISTRY`，此前 CLI 可能改到另一份文件），
  `restart` 不再因 `$0` 是相对路径而「command not found」。第 4 套测试扩到 28 项，测试总数 112 项。
- **v1.1** —— 对话内提权（`request_access` 申请制 + `approve`/`elevate`/`deny`/`scope`）+ 开窗先问范围（不给范围时会问你，脚本环境拒绝静默默认）+ 第 4 套测试（提权 18 项）+ 本机私有配置 `*.local.json` 约定。测试总数 102 项。
- **v1.0** —— 首个开源版本：四道闸、脱敏、审计、写开关（两级锁）、三套自带测试、多窗口路径分流、launchd/systemd 服务生成。

## 作者

由 **诗人**（GitHub [@Fission21](https://github.com/Fission21)）与 **CC** 设计并实现。

## License

MIT © 诗人 & CC（Poet & CC）
