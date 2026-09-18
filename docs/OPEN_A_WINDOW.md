# 把本地项目开成一扇窗（含「怎么交代给别的 AI」）

> 本文回答两件事：① 自己动手怎么做；② 如果你想让另一个 AI（Codex / Claude Code / OpenCode / Cursor…）
> 替你做，话该怎么说。核心原则：**范围是你定的，AI 只负责执行**。

## 一、自己动手：四步

```bash
cd ~/demo/lighthouse
export LIGHTHOUSE_PY=~/.hermes/hermes-agent/venv/bin/python    # 本机用这个解释器（带 mcp）

# ① 开窗登记（不给范围它会当场问你——范围是主人的决定，工具不替你默认）
bash lighthouse.sh new myproj ~/路径/项目 --title "我的项目" --preset docs+code --public

# ② 起服务（launchd / systemd user）
bash lighthouse.sh start

# ③ 出公网 + 健康检查（缺 DNS 托管在 Cloudflare 的域名时要先看 README 的前置清单）
bash lighthouse.sh publish
bash lighthouse.sh url myproj --public     # 打印 https://<域名>/<窗口路径>

# ④ 接连接器：ChatGPT → 设置 → 安全防护 → 开发者模式（开）→ 插件页右上「创建应用」
#    名称随便 · 连接方式=服务器URL（填第③步的地址）· 身份验证=无 · 勾确认 → 创建
#    对话里输入框打 @名字 选中芯片，再提问
```

详细接入步骤与排查见 `docs/CHATGPT.md`。

## 二、范围怎么选（这一步别省）

| 预设 | 内容 |
|---|---|
| `--preset docs` | `README*`、`docs/**`、`*.md` |
| `--preset docs+code` | 上面那些 + `src/** app/** lib/** tests/**` + 常见源码后缀（py/js/ts/go/rs/java…） |
| `--preset all` | `**/*` |
| `--preset none` | 什么都不给（先开个空窗，之后再谈） |
| `--include "a/**,b.md"` | 自己列（优先于 preset） |

默认已排除：`node_modules/** .venv/** venv/** __pycache__/** .git/**`。
额外排除用 `--exclude "data/**,*.csv"`。

**无论你怎么配，这三样永远看不到**（默认拉黑，不可关闭）：`.env` 及变体、私钥/证书
（`*.pem *.key id_rsa …`）、凭据类文件名（`*secret* *token* *credential* *apikey*`）、
以及整个 `.git/` 目录。想加严可以 `deny_extra`（只能加严，不能放松）。

## 三、怎么交代给别的 AI（可复制的话术）

给 AI 的话里**必须出现四样东西**，少一样它就会自己发明：

1. 项目路径
2. 窗口 id 与标题
3. **明确的范围**（能看什么、不能看什么）——不写，它会倾向于开 `**/*`
4. 要不要写权限（默认只读，别让它自己开）

再加两条元指令：**先读材料**、**完事汇报生效范围**。

### 模板 A：让它开一扇新窗

```text
用灯塔（lighthouse）把 <项目绝对路径> 开成一扇 MCP 窗口，给网页 AI 读。

动手前先读这两份：
- ~/.hermes/skills/devops/lighthouse/SKILL.md
- ~/demo/lighthouse/AGENTS.md      （项目铁律与约定）

范围我已经定了，不要扩大也不要缩小：
- 可以看：src/**、docs/**、README*、*.md、tests/**
- 不要给：data/**、*.csv、任何密钥文件（就算你不排除，灯塔的默认拉黑也会兜住）
- 只读，不要开写开关

窗口 id 用 <myproj>，标题「<我的项目>」，标 public。开完执行 publish 并跑健康检查。

做完汇报：① 本机地址和公网地址 ② 实际生效的 include/exclude
③ 写开关状态 ④ 这扇窗看不到什么。范围要改先问我。
```

### 模板 B：只让它排查（窗口连不上 / 读不到）

```text
<miji> 这扇窗现在 <连不上 / 读不到 tools/kb.py>。用灯塔自带的诊断排查，不要瞎改配置：
先读 ~/.hermes/skills/devops/lighthouse/SKILL.md，然后：
- bash lighthouse.sh status       看服务与端口
- bash lighthouse.sh doctor       体检解释器/依赖/隧道
- tail -20 ~/.lighthouse/logs/window-<miji>.log
- tail -20 ~/.lighthouse/audit/<miji>.jsonl
结论和证据给我，改配置前先说明你要改什么。
```

### 模板 C：提权相关（要它动手前先讲清楚）

```text
<miji> 窗口的权限不要动，除非我说。如果对话里的 AI 申请扩大范围：
- 默认它会落成【待批准】，需要我本地跑 approve —— 这是设计，不是 bug
- 我在本地可以用 elevate（限时）或 auto-grant（常驻策略）；你只负责转达，别替我做决定
- 密钥/凭据/.git 永远读不到，任何提权都压不过
```

### 模板 D：让它改灯塔自己的代码

```text
改 ~/demo/lighthouse 之前先读 AGENTS.md 的五条铁律（fail-closed、默认拉黑不可关、
写权限两级锁、提权默认需主人批准、开窗必须先问范围），改完必须：
bash tests/run_all_tests.sh     # 五套（13/46/25/29/23）必须全绿
任何安全相关改动都要补一条测试。改完汇报：改了什么、测试结果、有没有破坏既有行为。
```

## 四、开完怎么验收（三条证据）

```bash
bash lighthouse.sh status                      # 服务在跑、端口在听
bash lighthouse.sh url myproj --public         # 公网地址
tail -f ~/.lighthouse/audit/myproj.jsonl       # 每调用一行（含被拒的）
tail -f ~/.lighthouse/logs/window-myproj.log   # 外部请求（OpenAI 出口 IP 的 POST 200）
```

外部 AI「真读了」的典型审计串：`window_info` → `list_files` → `read_file`。
如果只有 `window_info` 没后续，多半是它没权限或没找到文件。

## 五、常见坑

| 坑 | 说明 |
|---|---|
| 公网 404、本机 200 | 隧道 ingress 缺这个 path，跑 `bash lighthouse.sh publish` 重生成 |
| 链接器建不上 | URL 里的随机路径段不能少；先确认 `publish` 的健康检查是 200 |
| 改完代码没生效 | 改 `core/*.py` 必须 `bash lighthouse.sh restart`；只有 `windows.json` 的策略是实时读的 |
| 以为「网页点允许」= 授权 | ChatGPT 的「允许使用 X？」弹窗是**平台侧**的工具调用确认，不是灯塔的授权；反过来，平台弹窗也不一定会出现 |
| 范围越开越大收不回来 | `deny <id>` 一把清空（额外范围/待批/预授权），`auto-grant <id> off` 关常驻策略 |
