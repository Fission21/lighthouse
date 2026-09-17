<div align="center">

# 灯塔 · Lighthouse

**把本地项目开成一扇窗，让网页 AI 看见 —— 只见你圈出来的那一部分。**

四道闸 · 输出脱敏 · 全程审计 · 写开关（默认全只读）

</div>

---

## 这是什么

网页上的 AI（ChatGPT 的 MCP 连接器等）很强，但它看不见**你本机的东西**。
灯塔做的事：**给本地的一个目录开一扇「受控窗口」**，让外面的 AI 通过 MCP 协议读到它——
而你事先声明它能看什么、不能看什么，给出去的内容自动脱敏，每次调用都留账。

```
本地项目目录
   └─ 窗口 MCP 服务（四道闸 · 脱敏 · 审计 · 写开关）   ← 开机自启
        └─ cloudflared 隧道（一个域名，按路径分流到各窗口）
             └─ https://你的域名/<每个窗口一条随机路径>
                  └─ 网页 AI（ChatGPT 连接器 / 任何 MCP 客户端）
```

一句话：**你不是把机器交出去，你是开了一扇你自己画的窗。**

## 一分钟看懂它能挡住什么

| 你会担心的事 | 灯塔的回答 |
|---|---|
| 它会不会翻我别的目录？ | ① 根界闸：realpath 出 `root` 一律拒 |
| 它会不会看到我的 `.env` / 私钥？ | ④ 默认拉黑：`.env* / *.pem / id_rsa* / credentials* / *.db` 等无条件拒绝，include 写多宽都没用 |
| 它会看到我明确划出去的东西吗？ | ② exclude 闸：目录命中连子树一起排除 |
| 它会不会把我密钥抄进回答里？ | 输出脱敏：`sk-… / ghp_… / AIza… / AKIA… / Bearer … / 私钥块` → `«REDACTED»`，连检索片段也过一遍 |
| 它会不会改我的代码？ | **默认全只读**。想让它动手，得你亲手开写开关（还能设 30 分钟自动关）；开着时每次改动先备份、后记 sha256 |
| 我怎么知道它看过什么？ | 审计日志：每次调用一行（含被拒的），`~/.lighthouse/audit/<窗口>.jsonl` |
| 我怎么证明它真的挡得住？ | 自带三套测试（13 + 45 + 25 项），一条命令跑完 |

## 快速开始（4 步）

```bash
# 0) 依赖：Python 3.10+ 与 mcp 库
pip install mcp

# 1) 起一个示例窗口，先把链路跑通（不需要公网）
bash lighthouse.sh test            # 一键验收：起临时实例 → 跑三套测试 → 自动收拾
#    ✅ 通用冒烟 13/13   ✅ 只读审计 45/45   ✅ 写开关 25/25

# 2) 给【你自己的项目】开一扇窗
bash lighthouse.sh new myproj ~/code/myproj \
     --title "我的项目" \
     --include "README.md,docs/**,src/**" \
     --exclude "private/**,**/*.log"
bash lighthouse.sh start
bash lighthouse.sh url myproj      # 拿到本机地址，先自己试
```

到这一步，任何 MCP 客户端（本机的 Claude/Codex/Cursor 等）都能通过上面的地址读它了。

```bash
# 3) 出公网（让网页版 AI 也能看见）
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
bash lighthouse.sh test [id]             # 一键验收
bash lighthouse.sh doctor                # 体检：解释器 / 依赖 / 隧道 / 配置
```

## 目录结构

```
lighthouse/
├── lighthouse.sh            # 唯一入口（new/start/stop/status/url/publish/write/test/doctor）
├── config.json              # 全局配置：域名 / 隧道 / 状态目录
├── windows.json             # 窗口注册表：每扇窗的给看范围写在这里
├── core/
│   ├── server.py            # 窗口 MCP 服务（四道闸 + 脱敏 + 审计 + 写工具）
│   ├── config.py            # 配置读写
│   ├── add_window.py        # 登记新窗口
│   ├── render_services.py   # 生成 launchd / systemd 服务定义
│   ├── render_ingress.py    # 生成隧道配置（单域名 + 路径分流）
│   └── switch.py            # 写开关
├── tests/
│   ├── smoke_window.py      # 通用冒烟 13 项（任何窗口都能测）
│   ├── audit_readonly.py    # 只读审计 45 项（含文件指纹前后比对）
│   ├── test_write.py        # 写开关 25 项（关=全拒 / 开=全流程 / 关回=只读）
│   └── run_all_tests.sh     # 一键验收（自带临时实例，不碰线上）
├── demo/project/            # 示例项目（含验证口令，用来证明"真的读到了本地"）
└── docs/
    ├── ARCHITECTURE.md      # 一扇窗是怎么被四道闸过滤的
    ├── SECURITY.md          # 威胁模型与边界（该防的防，防不了的说清楚）
    └── CHATGPT.md           # 接网页版 ChatGPT 的完整步骤（含踩坑）
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

- **v1.0** —— 首个开源版本：四道闸、脱敏、审计、写开关（两级锁）、三套自带测试、多窗口路径分流、launchd/systemd 服务生成。

## License

MIT © Lighthouse contributors
