# 接网页版 ChatGPT（连接器 / Connectors）

> 2026-09 实测于 ChatGPT 网页版（含 Pro）。其它 MCP 客户端（Claude、Cursor、Codex…）流程类似，
> 只要支持「远程 MCP 服务器 URL」即可。

## 前置

1. 一个 **DNS 托管在 Cloudflare 的域名**——命名隧道的硬性要求（没有域名的替代方案见 README
   「跑起来需要什么（前置，先看这个）」）。
2. 你已经有公网地址：`bash lighthouse.sh publish` 跑完，`bash lighthouse.sh url <id>` 能打印出
   `https://你的域名/<窗口路径>`，并且 `publish` 的健康检查显示 `http=200`。
3. 窗口是 `visibility: public`（不然 `publish` 会拦住你——那是防手滑，不是 bug）。

## 步骤

1. **开开发者模式**：ChatGPT → 设置 → 安全防护 → 拉到最下面「开发者模式 / 开发人员模式」打开。
   （描述里写得很直白：允许你添加未经验证的连接器，风险自担。）
2. **进插件页**：设置 → 插件（或直接访问 `https://chatgpt.com/plugins?view=personal`），
   右上角**创建应用**。
3. **填表**：
   - 名称：随便起（例如「我的项目」）
   - 描述：一句话（可选）
   - 连接方式：选「服务器 URL」，填 `https://你的域名/<窗口路径>`
   - 身份验证：**无身份验证**（灯塔的路径随机段就是它的弱口令；要更强请在隧道层加 Cloudflare Access）
   - 勾「我了解并希望继续」→ 创建
4. 页面尾部出现「"XXX"已连接」就成功了。

## 在对话里用它

打开一个新对话，在输入框里打 **`@`** + 你起的名字（例如 `@我的项目`），
在弹出列表里选中它（会插成一个芯片），然后正常提问：

> 读取 README.md，告诉我里面的「验证口令」是什么。必须真实调用工具，不要猜。

## 怎么确认它是真读了（而不是猜的）

三个地方任一可见：

1. **回答里**会显示"已调用工具/正在使用 XXX"之类的痕迹；
2. **你本机**：`tail -f ~/.lighthouse/logs/window-<id>.log`，会看到来自 OpenAI 出口 IP 的
   `POST … 200 OK`；
3. **审计**：`tail -f ~/.lighthouse/audit/<id>.jsonl`，最典型的一串是
   `window_info`（模型先看自己能看到什么）→ `list_files` → `read_file`。

## 常见问题

| 现象 | 原因 / 解法 |
|---|---|
| 创建时报连不上 | 先用 `publish` 的健康检查确认公网 200；确认 URL 里的路径段完整（随机段不能少） |
| 本机访问 200，公网 404 | MCP 的 DNS 重绑定保护（服务端已内置关闭）；或隧道 ingress 缺这条 path |
| 回答卡在第一个字 | 生成其实已完成，刷新页面 |
| **提示「此工具调用被 OpenAI 的安全检查屏蔽」** | 这是 **OpenAI 平台侧**的内容审查拦住了工具调用，**不是灯塔拒的**。判断依据：`tail ~/.lighthouse/audit/<窗口>.jsonl` —— **没有**对应记录，说明请求根本没发到你机器上。<br>已知会被拦的例子：连续多次文件系统调用、读取名字含 `config` / `settings` 的路径。<br>应对：① 隔几分钟重试（多为临时风控）；② 一次只让它做一件事，别让它「批量读取」；③ 要稳定读写就走**本机 MCP 客户端**（Claude Desktop / Codex / Cursor 直连 `http://127.0.0.1:<端口>/<路径>`）—— 不经过公网，也就没有这一层审查 |
| 想让它改文件 | `bash lighthouse.sh write <id> on 30`（30 分钟自动关）；不用了 `… off` |
| 想让它看更多 | 对话里说「提高访问权限」「代码也给它看」→ 它会调 `request_access` 申请 → 你在机器上 `bash lighthouse.sh approve <id>`。想省去每次批：`bash lighthouse.sh elevate <id> 30 --scope "src/**"`（限时预授权）或 `bash lighthouse.sh auto-grant <id> on [--ceiling "src/**,docs/**"]`（常驻策略：上限内的申请直接生效）。注意 ChatGPT 那个「允许使用 X？」弹窗属于平台侧、**不一定会出现**，它不等于灯塔的授权 |
| 想收回权限 | `bash lighthouse.sh deny <id>`（额外范围 / 待批申请 / 预授权窗口一把清空） |
| 想撤掉 | 插件页 → 对应应用 → 删除；同时 `bash lighthouse.sh stop` 关掉本地服务 |

## 提醒

- 连接器 = 外部服务商能访问你的这台机器上、**那扇窗里**的内容。开之前再看一眼 `windows.json`
  里的 include/exclude 是不是你想要的。
- 「开发者模式」是高风险开关：它允许添加**未经验证的**连接器。只加你自己搭的。
