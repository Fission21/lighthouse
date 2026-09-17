# 接网页版 ChatGPT（连接器 / Connectors）

> 2026-09 实测于 ChatGPT 网页版（含 Pro）。其它 MCP 客户端（Claude、Cursor、Codex…）流程类似，
> 只要支持「远程 MCP 服务器 URL」即可。

## 前置

1. 你已经有公网地址：`bash lighthouse.sh publish` 跑完，`bash lighthouse.sh url <id>` 能打印出
   `https://你的域名/<窗口路径>`，并且 `publish` 的健康检查显示 `http=200`。
2. 窗口是 `visibility: public`（不然 `publish` 会拦住你——那是防手滑，不是 bug）。

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
| 想让它改文件 | `bash lighthouse.sh write <id> on 30`（30 分钟自动关）；不用了 `… off` |
| 想撤掉 | 插件页 → 对应应用 → 删除；同时 `bash lighthouse.sh stop` 关掉本地服务 |

## 提醒

- 连接器 = 外部服务商能访问你的这台机器上、**那扇窗里**的内容。开之前再看一眼 `windows.json`
  里的 include/exclude 是不是你想要的。
- 「开发者模式」是高风险开关：它允许添加**未经验证的**连接器。只加你自己搭的。
