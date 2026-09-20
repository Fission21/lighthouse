<div align="center">

# 灯塔 · Lighthouse

**把电脑上的一个目录，开成一扇 AI 能安全访问的「窗」。**

<small>Filesystem MCP gives an AI access. Lighthouse governs that access. · 给 AI 开门之前，先把锁、记录和审批装上。</small>

四道闸 · 出口脱敏 · 全程审计 · 写开关（默认全只读）· 提权要你点头 · 开窗先问你范围

<small>[English README](README_EN.md)</small> · <small>[和 filesystem MCP 差在哪](#和-filesystem-mcp-差在哪)</small>

<small>作者 · 诗人 & CC</small>

</div>

---

## 这是什么

**一句话：给 AI 开一扇「窗」—— 它看得见什么、能不能动手、什么时候看的，全由你说了算。**

- **给谁用** —— 想在网页版 ChatGPT（或本机 AI 助手）里看自己项目、又不想把整台机器交出去的人。
- **怎么用** —— 本机一条命令开窗 → 把地址接进 ChatGPT 连接器（或 WorkBuddy / Codex 这类本机客户端）→ 它就能读了。
- **窗多大你定** —— 只给看 `docs/**`？能不能写？写完要不要留备份？密钥类文件（`.env`、私钥…）默认永远不给看。
- **全程留痕** —— 它读了什么、什么时候读的、被拒了什么，逐条进审计；它想扩大范围，得先问你点头。

它和普通 filesystem MCP 的关系：**filesystem MCP 给 AI「访问权」，灯塔给这份访问权装上闸门** —— 为什么需要，从下面的「先说问题」看起。

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

还没有域名？**不必买**：接本机 AI 完全不需要域名或隧道；接网页 AI 也有零成本路线（ngrok 免费版 / Tailscale Funnel），
甚至可以直接用「本机 IP / 局域网 IP」连接。所有路数与优缺点，见下面的「**接入方式 · 任你选**」。

网页 AI 那一侧还需要：ChatGPT 账号 + 打开开发者模式（设置 → 安全防护）。详见 [`docs/CHATGPT.md`](docs/CHATGPT.md)。

## 快速开始（4 步）

```bash
# 0) 依赖：Python 3.10+ 与 mcp 库
pip install mcp

# 1) 起一个示例窗口，先把链路跑通（不需要公网）
bash lighthouse.sh test            # 一键验收：起临时实例 → 跑五套测试 → 自动收拾
#    ✅ 通用冒烟 13/13   ✅ 只读审计 46/46   ✅ 写开关 25/25   ✅ 提权 56/56   ✅ 加固 49/49

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

## 接入方式 · 任你选（不买域名也全能用）

同一个窗口，给谁连、走哪条路，由你挑：

| 想给谁看 | 怎么连 | 免费？ | 要域名？ |
|---|---|---|---|
| **本机 AI 客户端**（WorkBuddy / Codex / Claude Code…） | 默认就这样：`http://127.0.0.1:<端口>/<路径>` | ✅ | 不要 |
| **同一网络里的设备**（手机、另一台电脑） | `bash lighthouse.sh lan <id> on` → 用 `http://<局域网IP>:<端口>/<路径>` 直连 | ✅ | 不要 |
| **你自己的异地设备**（跨地域像在同一局域网） | Tailscale：私网 IP `http://100.x.y.z:<端口>/<路径>`，或 `tailscale funnel` 出公网 | ✅ | 不要 |
| **网页版 / 手机版 ChatGPT** | **ngrok 免费版**（账户自带 1 个固定域名）或 **Tailscale Funnel**（`https://<设备>.<tailnet>.ts.net`） | ✅ | 不要 |
| 同上、要最省心 | Cloudflare 命名隧道 + 自己域名（走 `publish` 流水线） | 域名钱（便宜域名即可） | 要 |

几条要说清的：

- **本机（默认）**：窗口只听 `127.0.0.1`，外面的机器摸不到——最安全的一档，什么都不用配。
- **局域网直连**：`lan <id> on` 后窗口改听 `0.0.0.0`，同网段设备直接连 `http://192.168.x.x:<端口>/<路径>`。
  ⚠️ 局域网内可见、没有 TLS（路径里的随机段就是那道弱口令）——只在信得过的网络开，用完 `lan <id> off` 收回。改动在 `restart` 后生效。
- **ngrok 免费版**：注册即送 1 个固定 `xxx.ngrok-free.app` 域名；`ngrok http --domain=<你的免费域名> <窗口端口>`，
  然后把这个 `https://…/<窗口路径>` 填进 ChatGPT 连接器。免费额度内个人自用足够。
- **Tailscale**：装好后每台设备有 `100.x.y.z` 私网 IP——同一 tailnet 的设备直接 `http://100.x.y.z:<端口>/<路径>` 访问，
  不需要域名、也不用把服务开给公网；要接网页 AI 再 `tailscale funnel <端口>`，得到 `https://<设备>.<tailnet>.ts.net` 的免费 HTTPS 地址。
- **Cloudflare 快速隧道**（`cloudflared tunnel --url …`）：不用账号、不用域名，但 URL 每次重启都变、官方不支持 SSE——
  本机实测里它连普通请求都路由不通（Cloudflare 边缘直接 404）。**不建议**拿它接 MCP。
- **纯公网 IP 直连**：技术上行得通（公网 IP + 端口转发 + 自备证书），但国内宽带多为 NAT、不给公网 IP，
  网页 AI 连接器又要求 HTTPS 主机名——**现实里只适合局域网/VPN 内的客户端**。
- **对方不吃 SSE？** `json-response <id> on` 让窗口用纯 JSON 回应（标准客户端两种都吃，默认 SSE）。

> 一句话：**接本机 AI 不需要域名；接网页 AI 也不一定买——本机 IP、局域网 IP、Tailscale、ngrok 全是零成本路线。**

## 日常命令

```bash
bash lighthouse.sh list                  # 有哪些窗口
bash lighthouse.sh status                # 服务 + 端口 + 范围 + 写开关总览
bash lighthouse.sh url <id>              # 查地址（本机 / 公网）
bash lighthouse.sh write <id> on 30      # 开 30 分钟写权限（到点自动关）
bash lighthouse.sh write <id> off        # 立刻回到只读
bash lighthouse.sh write status          # 现在谁能写
bash lighthouse.sh scope <id>            # 当前授权状态（额外范围 / 待批申请 / 常驻策略）
bash lighthouse.sh approve <id> [--for 2h]   # 批准它的提权申请（--for 选时长：30m/2h/1d/7d/forever）
bash lighthouse.sh elevate <id> --for 30m --scope "src/**"   # 限时预授权：期间这类申请自动批
bash lighthouse.sh auto-grant <id> on [--ceiling "src/**,docs/**"] [--ttl 2h]   # 常驻策略：上限内申请立即生效（--ttl 限每次授予时长）
bash lighthouse.sh auto-grant <id> off   # 回到逐次批准（下次申请立即生效，不用重启）
bash lighthouse.sh chat-approval <id> on [--ceiling "src/**"]   # 对话内授权：你回一句「授权你」即生效（默认关）
bash lighthouse.sh lan <id> on|off|status            # 局域网直连：同网段用「本机 IP:端口」访问（默认关）
bash lighthouse.sh json-response <id> on|off|status  # POST 回应改纯 JSON（给不吃 SSE 的隧道/客户端；默认 SSE）
bash lighthouse.sh deny <id>             # 收回全部提权
bash lighthouse.sh test [id]             # 一键验收
bash lighthouse.sh doctor                # 体检：解释器 / 依赖 / 隧道 / 配置
```

## 对话里提权：想看更多，谁说了算

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

- **默认 agent 无法自我提权**：没有你的批准，`request_access` 只能留下一条申请（`window_info` 里能查到）；
  而且授予只加宽 include，**压不过** exclude 与密钥拉黑——提权 ≠ 解禁；
- **常驻策略是「你事先授权」**：`auto-grant <窗口> on --ceiling "src/**,docs/**"` 之后，上限内的申请直接生效、
  不用再跑命令；超出上限照样转待批；不设 `--ceiling` 就是「任何范围申请都自动生效」。
  加个 `--ttl 2h`，每次自动授予就只保持 2 小时——到期自动收回，AI 需要时再申请，授权不会永久累积；
  策略**实时读**：`off` 一敲，下一次申请立刻回到 pending，不用重启服务；
- **网页那边点的是另一层**：ChatGPT 弹的「允许使用 X？」是**平台自己的**工具调用确认，不是灯塔的授权；
  开了自动授予之后，申请甚至可能**没有任何弹窗**就直接生效——`--ceiling` 与审计日志才是你的知情手段；
- **三种给法、随时收回**：常驻策略 `auto-grant` / 事后批准 `approve` / 限时预授权 `elevate`——
  **授权多久由你挑**：`--for 30m|2h|1d|7d|forever`（不写 = 无期限）；
  `deny <id>` 一把清空，`auto-grant <id> off` 关掉常驻策略。
- **本机 agent 少跑命令：对话内授权**——`chat-approval <id> on` 之后，agent 申请、你在对话里回一句
  「授权你」、它带 `user_confirmed=true` 再申请一次即生效（全程不用碰终端）。⚠️ 这是**信任式**通道
  （服务端验证不了「你真说了同意」，它信任 agent 的转述）：默认关，只给**本机/可信 agent** 开，网页 AI 窗口不要开。
- **写范围记得带通配**：`--scope "dir/"` 只覆盖目录条目本身、读不到目录里的文件；要放开目录内的文件写 `dir/**`。
  （授予回执现在会对这类 pattern 当场提示，不会再出现「批准了却读不到」的错觉。）

这就是「把选择的权利交给你」的落地方式：**方便归方便，闸门永远在你手里。**

## 目录结构

```
lighthouse/
├── lighthouse.sh        # 唯一入口（new/start/stop/status/url/publish/write/elevate/auto-grant/approve/deny/scope/test/doctor）
├── config.json          # 全局配置：域名 / 隧道 / 状态目录（*.local.json 覆盖，已 gitignore）
├── windows.json         # 窗口注册表：每扇窗的给看范围只写在这里
├── core/                # server.py(窗口服务) · config.py · scope.py(授权) · add_window.py(开窗)
│                        #   render_services.py(服务定义) · render_ingress.py(隧道分流) · switch.py(写开关)
├── tests/               # 五套测试（冒烟 13 / 审计 46 / 写开关 25 / 提权 56 / 加固 55）+ run_all_tests.sh
├── demo/project/        # 示例项目（含验证口令，用来证明"真的读到了本地"）
└── docs/                # ARCHITECTURE · SECURITY · CHATGPT · OPEN_A_WINDOW · ROADMAP · ISSUES
```

## 四道闸是怎么落的

**范围写在一处。** `windows.json` 里一条声明 = 一扇窗：

```json
"myproj": {
  "root": "~/code/myproj",                         // ① 根界闸：只看这棵树，realpath 出界一律拒（符号链接穿透也拦）
  "include": ["README.md", "docs/**", "src/**"],   // ③ include 闸：只放行这些 glob
  "exclude": ["private/**"],                       // ② exclude 闸：命中连子树一起排除（大小写变体也拦）
  "path": "/w-myproj-6m1yo0",                      // 路径带随机段（不可枚举，但不是认证 —— 见 docs/SECURITY.md）
  "visibility": "local",                           // local = 私密；不会写进公网入口（publish 会跳过它）
  "write": { "enabled": true }                     // 写权限总闸（默认关；还要运行时开关才真正可写）
}
```

④ **默认拉黑永远排在最前**：`.env*`、`*.pem`、`id_rsa*`、`*secret*`、`*token*`、`*.db`、整个 `.git/` ——
无条件拒绝，include 写多宽都压不过，大小写变体（`.ENV`）一并拦。出口还会过一遍**脱敏**
（`sk-… / ghp_… / 私钥块` → `«REDACTED»`，检索片段也算）。

改范围 = 改这一个文件，不用动代码。**判定失败就往拒绝走**（fail-closed）：路径解析异常、匹配不出来、
任何说不清的情况，一律拒绝并记进审计。**多窗口共用一个域名**，靠路径分流到各自端口——
加窗口不用再动 DNS，`publish` 一条命令重建分流表。

## 依赖与致谢

| 依赖 | 用途 | 链接 |
|---|---|---|
| Python 3.10+ | 运行环境 | https://www.python.org/ |
| MCP Python SDK | 实现 MCP 服务端（streamable-http） | https://github.com/modelcontextprotocol/python-sdk |
| cloudflared | 把本机端口安全地放到公网（出站隧道，无需开端口） | https://github.com/cloudflare/cloudflared |
| macOS launchd / Linux systemd | 守护窗口服务（开机自启、挂了自动拉起） | 系统自带 |

**特别感谢**：MCP 团队把「AI 怎么接外部世界」做成了公开协议，cloudflared 让「不出站也安全」成为默认选项——
没有这两样，灯塔只是一堆本地脚本。

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

- **v1.2** —— 常驻提权策略 `auto-grant`（上限内的申请立即生效，超出仍待批；策略实时读，`off` 立刻收紧）；
  闸门加固（大小写绕过、`.git/` 整目录、密钥名变体）；新增第 5 套「加固」攻击性测试；
  **授权时长可自选**（`--for 30m|2h|1d|7d|forever`，`approve` / `elevate` / `auto-grant --ttl` 通用）；
  新增**对话内授权**（`chat-approval`：你回一句「授权你」即生效；默认关、信任式通道，仅限本机可信 agent）；服务端改用**无状态传输**（`stateless_http`），重启服务不再作废已连客户端的会话——
  不自动重连的客户端（如 WorkBuddy）不会再报「Session not found」；授予回执对 `dir/` 这类「只覆盖目录本身」的
   pattern 当场提示改用 `dir/**`（不再假成功），`window_info` 补全工具清单；**接入方式任选**（`lan` 局域网 IP 直连 / `json-response` 纯 JSON 回应 / ngrok 与 Tailscale 免费路线），共 195 项。
  另修两处：CLI 与服务的注册表路径统一、`restart` 不再因 `$0` 相对路径失败。
- **v1.1** —— 对话内提权（申请制 + `approve`/`elevate`/`deny`/`scope`）+ 开窗先问范围 + 第 4 套测试（共 102 项）。
- **v1.0** —— 首个开源版：四道闸、脱敏、审计、写开关两级锁、三套测试、多窗口路径分流、launchd/systemd 服务生成。

## 作者

由 **诗人**（GitHub [@Fission21](https://github.com/Fission21)）与 **CC** 设计并实现。

## License

MIT © 诗人 & CC（Poet & CC）
