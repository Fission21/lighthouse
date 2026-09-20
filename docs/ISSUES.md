# 问题账本 · Issues

> 给「今天发现、以后再改」的东西一个落脚处。**不改代码也能记**，回头统一修。
>
> 一条一个问题，格式固定（见下方模板）。**证据必须能复现**——审计原文、命令输出、
> 报错文字，而不是「感觉有问题」。没有证据的条目会被当成待澄清，不会直接进修复队列。
>
> 用法：`bash lighthouse.sh issue "一句话标题" [--area 模块] [--sev 高|中|低] [--detail "现象/证据"]`
> 修完把条目从「待修」移到「已修」，补上 commit 号。

## 严重度定义

| 级别 | 含义 |
|---|---|
| **高** | 安全闸门失效、或核心路径不可用（读不到该读的、能读到不该读的） |
| **中** | 功能可用但体验受损 / 边界行为不符合直觉 / 让用户误判 |
| **低** | 打磨类：文案、输出格式、非关键路径的告警噪音 |

---

## 待修

### #8 `publish` 被无关的 local 窗口挡住

- **发现**：2026-09-18（权限验收测试时）
- **现象**：注册表里只要存在任何一个 `visibility: local` 的窗口（比如仓库自带的 `demo` 示例窗），
  `bash lighthouse.sh publish` 就中止并要求 `--force` —— 哪怕本次要发布的窗口全都标了 public。
- **期望**：只拦「本次要发布的那些窗口」，无关的 local 窗口不该让整条命令停摆。
- **证据**：`bash lighthouse.sh publish` → `⚠️ demo: visibility=local（未标 public）` / `已中止：…或加 --force`
- **区域**：`lighthouse.sh` publish 前置检查
- **严重度**：中
- **已修（2026-09-18）**：不再因「存在 local 窗口」中止；local 窗口只提示 `⏭ 本次不发布它`。
  只在「一个 public 窗口都没有」时中止（防手滑发空隧道）。

### #9 `scope` 看不出「对面到底有没有来问」

- **发现**：2026-09-18（网页 AI 报「被平台安全检查拦住」时，无法一眼分辨真假）
- **现象**：`scope <id>` 只显示授权状态，不显示最近有没有调用。
  要判断「请求到了本机吗」必须去 `tail -f audit/<id>.jsonl`。
- **期望**：`scope` 里带一行调用摘要，例如
  `最近 10 分钟：收到 4 次调用（3 放行 / 1 拒绝）；最近一次：read_file docs/notes.md ✓`
- **影响**：这是**用户最常需要的判断**——「AI 说读不到」到底是它没来问、还是被我拒了。
- **区域**：`core/scope.py` + `lighthouse.sh scope`
- **严重度**：中
- **已修（2026-09-18）**：`scope <id>` 现在带 `recent_calls`（最近 10 分钟：几次调用 / 放行 / 拒绝 / 最近一次是什么），
  没人调时直接点明「请求根本没到本机」。实现 `core/scope.py:recent_activity()`。

### #10 `start` 的端口告警对「已在运行的窗口」误报

- **发现**：2026-09-18
- **现象**：`bash lighthouse.sh start` 输出 `⚠️ 端口 8951 已被占用（窗口 demo）——请改 port`，
  但那个端口正是 demo 自己在监听（服务已经跑着）。这句话会让人以为配置有冲突。
- **期望**：端口被**自己的**服务占用时静默通过；只有被无关进程占用才告警。
- **区域**：`lighthouse.sh` start 前置检查
- **严重度**：低
- **已修（2026-09-18）**：先问 launchd/systemd「这个窗口的服务在跑吗」—— 在跑就跳过端口检查；
  只有「服务没跑、端口却被别人占着」才告警。

### #11 测试盲区：`**/*` 形式的 include 掩盖了 `dir/**` 的匹配缺陷

- **发现**：2026-09-18（`list_files("src")` 被判越界 —— 见已修 #1）
- **现象**：五套测试的窗口都用 `include: ["**/*"]`，而 `**/*` 恰好能匹配裸目录名，
  所以 `dir/**` 生成的正则不匹配目录本身这个缺陷**永远测不出来**。
- **要做**：盘点所有 glob 形态（`x/**`、`**/*`、`*.py`、`a/b/**`、`?`）在
  「列目录 / 读文件 / exclude 目录」三种操作下的行为，补齐对照测试。
- **区域**：`tests/`、`core/server.py:_glob_to_re`
- **严重度**：中
- **进展（2026-09-18）**：写了 glob 形态探测，四组配置（`src/**`、`**/*.py`、`*.md`+`docs/**`、
  `docs/**/v1.md`）× 列目录/读文件全跑一遍 —— **又挖出 #15**（已修），
  并把 `dir/**`（第 ⑧ 节）与 `**/*.py`（第 ⑨ 节）两组固化成回归测试。
- **已修（2026-09-20）**：最后两个盲区补齐 —— 新增第 ⑪ 节 7 项（`?` 只吃一个字符、
  不吃空字符、不跨目录分隔符 `/`；列 `q` 目录只出现命中项），③ 节补 3 项
  （`list_files("private")` / `list_files("PRIVATE")` 被拒、`search` 不返回被 exclude 目录里的内容）。
  加固套件 37 → **47 项**，五套合计 **160 项**全绿。

- **状态**：已修

### #12 网页 AI 平台的中间审查层没有应对手段

- **发现**：2026-09-18
- **现象**：OpenAI 侧会拦掉部分工具调用，提示原文
  `Script error: 此工具调用被 OpenAI 的安全检查屏蔽。请仔细检查你发送的内容。`
  被拦的调用**根本不到本机**（审计零记录），用户只能看到模型转述的一句笼统话。
- **现状**：已记入 `docs/CHATGPT.md`（现象 + 判别方法）与 `docs/SECURITY.md`（明确防不了）。
- **可做的**：文档里的「隔几分钟重试 / 一次一件事 / 改用本机客户端」目前靠人记；
  是否要在工具返回值或文档里给出更明确的引导，待定。
- **区域**：文档 / 交互设计（**超出灯塔控制范围**，只能缓解）
- **严重度**：低
- **状态**：待定

### #13 验收用的临时窗口与项目待清理

- **发现**：2026-09-18
- **现象**：`trial` 窗口 + `~/demo/trial-window` 是为了权限验收临时造的，测试完应清理
  （或保留成 `docs/OPEN_A_WINDOW.md` 的示例）。
- **命令**：`bash lighthouse.sh deny trial` → 从 `windows.local.json` 删除该条 → `bash lighthouse.sh restart`
- **区域**：运维
- **严重度**：低
- **已处理（2026-09-20）**：验收收官（8 个问题全修、五套 160 项全绿）后按上条命令退役：
  授权已收回、注册表条目已删、launchd 服务已卸（plist 删除）、隧道 ingress 已重生成。
  公网实测 `/w-trial-gicwpq → 404`、本机 8941 端口已关；其余三窗复测 beacon/miji 200、demo 404（私密）。
  `~/demo/trial-window`（208K 验收素材）**保留**，重建一扇验收窗：
  `bash lighthouse.sh new trial ~/demo/trial-window --include "README.md,docs/**,src/**" --exclude "src/internal/**" --public`
  —— #14 的对照实验还会用到它。
- **状态**：已处理

<!-- NEW-ISSUES-HERE -->

### #14 网页 AI 的 `request_access` 在 trial 窗口上被平台全数拦截

- **发现**：2026-09-18
- **现象**：让 ChatGPT 用 trial 连接器发起范围申请，模型侧回
  `Script error: 此工具调用被 OpenAI 的安全检查屏蔽`；本机审计里 `request_access` **零记录**
  （只有两条我本地测试的，reason=`验收测试`）。
- **对照（关键）**：同一个工具在 **miji** 连接器上，网页 AI 曾成功发起 4 次
  （11:33 / 11:47 / 12:13 / 12:18）—— 所以**不是「平台一律禁止 AI 申请提权」**，是条件触发。
- **差异假设**（待验证）：
  1. **参数内容触发**：trial 申请的是 `private/**`（含 `private`），miji 申请的是 `tools/**`；
     更早 trial 上读 `src/config/settings.py` 也被拦（含 `config` / `settings`）
  2. **连接器信任度**：trial 是当天新建的，miji 已使用一段时间
- **可测实验**（两个组合能区分）：① 让 ChatGPT 在 trial 上申请 `include:["docs/**"]`（完全中性）；
  ② 再回 miji 上申请一次。①成功→是连接器信任度；①失败②成功→是参数内容。
- **已知可行绕行（已实测）**：**用户主动授予，不让 agent 申请** ——
  `bash lighthouse.sh approve <窗口> --scope "private/**"`（**没有待批申请也能用**），
  之后 agent 直接就能读，全程不需要调 `request_access`。
  实测：授予后 `private/` 可读；且 `exclude` 与默认拉黑**仍然压过**这次授予。
- **区域**：平台交互（超出灯塔控制范围，只能在文档给出绕行指引）
- **严重度**：中（挡掉了「对话里提权」这条既有路径）
- **状态**：待查


---

## 已修

### #1 `list_files("src")` 被误判越界 —— `include: ["src/**"]` 不匹配目录本身

- **发现**：2026-09-18，网页 AI 实测撞到（审计：`list_files {path:src} → ok:false`）
- **根因**：`_glob_to_re` 把 `src/**` 编译成 `^src/.*$`，**匹配不了裸的 `src`**。
- **修复**：`/**` 展开为 `(?:/.*)?` —— 目录本身与整棵子树都算命中。
- **验证**：`tests/test_hardening.py` 第 ⑧ 节 6 项；放宽后 `.env` / exclude / 范围外 / 形近目录全部仍被拒。
- **修于**：`9d9a651`
- **严重度**：高（「列出子目录」是最基本操作）

### #2 `lighthouse.sh new` 登记的窗口永远不会被启动

- **发现**：2026-09-18（登记 `trial` 后 `status` 里根本不出现它）
- **根因**：`core/add_window.py` 把注册表路径写死成 `windows.json`，
  而服务端读的是 `config.REGISTRY_PATH`（优先 `windows.local.json`）——**写进去的文件没人读**。
- **修复**：改用 `config.REGISTRY_PATH`；同类问题（CLI 与服务路径不一致）一并统一。
- **修于**：`9d9a651`
- **严重度**：高

### #3 CLI 与服务的注册表路径不一致

- **现象**：`lighthouse.sh` 认 `windows.json`，服务认 `windows.local.json`；
  会出现「命令说改好了、服务行为照旧」的静默不一致。
- **修复**：统一优先级 `LIGHTHOUSE_REGISTRY` > `WINDOW_REGISTRY` > `*.local.json`。
- **修于**：`090774f`
- **严重度**：高

### #4 `bash lighthouse.sh restart` 在脚本目录内执行会 `command not found`

- **根因**：`restart)` 分支用 `"$0"` 调用自己，`$0` 是相对路径。
- **修复**：改用 `"$HERE/lighthouse.sh"`。
- **修于**：`090774f`
- **严重度**：中

### #5 大小写绕过：`.ENV` 能读到 `.env`、`PRIVATE/` 能绕过 `exclude private/**`

- **发现**：2026-09-18，攻击性探测（macOS/Windows 文件系统大小写不敏感，而规则按大小写敏感比较）
- **修复**：DENY 与 glob 匹配一律 `IGNORECASE`。
- **验证**：`tests/test_hardening.py` ①③ 节
- **修于**：`090774f`
- **严重度**：高（安全）

### #6 `.git/` 只拉黑了 config，其余全可读

- **现象**：`.git/logs/HEAD`（reflog，含作者邮箱与提交历史）、`.git/HEAD`、`.git/objects/*` 均可读。
- **修复**：`.git/` 整目录拉黑（不误伤 `.gitignore` / `.github/`）。
- **修于**：`090774f`
- **严重度**：高（安全）

### #7 密钥类文件名覆盖不全

- **现象**：`apikey.txt`、`my-secrets.txt`、`config.env`、`token.md` 都不在拉黑名单里。
- **修复**：改为「包含式 + 后缀式」匹配。
- **修于**：`090774f`
- **严重度**：中（安全）

### #15 列举目录时用 include 剪枝，导致「按类型给看」的配置下目录树整体消失

- **发现**：2026-09-18（按 #11 做 glob 形态探测时挖出）
- **现象**：`include: ["**/*.py"]` 这类按类型的配置下 ——
  `list_files("")` 返回 **0 项**（子目录被 include 剪掉，整棵树消失）；
  `list_files("src")` 直接拒绝（目录名不可能匹配 `**/*.py`）。
  → agent 完全发现不了文件，只能靠猜路径。
- **根因**：`_iter_files` 用 `check(sub + "/")` 给目录剪枝，而按类型的 include 永远不匹配目录名。
- **修复**：
  1. 新增 `Window.pruned()` —— 只按 exclude / 默认拉黑剪枝，**不用 include 剪枝**；
  2. `list_files` 入口 check 失败时用 `Window.has_visible_under()` 兜底：只要该目录下
     有能给看的文件就允许列举（只判「有没有」，不返回内容；列出的每一项仍逐个过闸）。
- **验证**：`tests/test_hardening.py` 第 ⑨ 节 5 项；glob 形态探测四组配置全符合直觉
  （`**/*.py` 下 `list_files('')` 从 0 项 → 3 项，`src` 从拒绝 → 2 项）。
- **严重度**：高（与 #1 同族：用户按类型配了范围，agent 却什么都发现不了）

### #16 `visibility: local` 只是注释，私密窗口照样出公网（安全）

- **发现**：2026-09-18（修 #8 时顺手核对隧道配置，发现 `render_ingress.py` 只给 local 窗口加了
  一句注释 `# ⚠️ visibility=local`，**照样把它写进 ingress**）
- **实测**：标着 local（私密）的 `demo` 窗口，公网 `POST /mcp-demo-7g2xq1` 返回 **HTTP 200**
  —— 也就是说文档里「local 窗口发布时会被拦」的承诺，实际没有任何执行点。
- **修复**：`render_ingress.py` 只把 `visibility=public` 的窗口写进隧道（备用入口同样只指向 public）；
  `publish` 会明示「跳过：demo」。修后实测 `demo → HTTP 404`，`trial/miji/beacon → HTTP 200`。
- **教训**：安全承诺必须落在**行为**上。写在注释里、或只写成一句警告的，等于没做 ——
  这条以后要作为 review 检查项：每句「会被拦 / 不会发生」的承诺，都要能指到执行它的那行代码。
- **严重度**：高

### #17 `lighthouse.sh` 里的注册表路径与服务端不是同一个文件

- **发现**：2026-09-18（修 #8 时，publish 报告「没有任何 public 窗口」，而实际上有三个）
- **根因**：`lighthouse.sh` 第 31 行硬编码 `REGISTRY="$HERE/windows.json"`（仓库示例，只含 demo），
  而服务端读的是 `config.REGISTRY_PATH`（本机 `windows.local.json`）。
  于是**可见性检查读一份、真正的发布读另一份**。
- **修复**：`REGISTRY` 改为向 `config.py` 取值（唯一来源），取不到再退回示例文件。
- **备注**：这是 #3（CLI 与服务注册表不一致）的遗漏面 —— 上次只修了 Python 侧。
- **严重度**：中（后果是检查失真，不是越权）

---

### #18 授予「只覆盖目录本身」的 pattern 会假成功（`dir/` 报 granted，但读 `dir/file` 仍被拒）

- **发现**：2026-09-20（外部实测者 WorkBuddy 在「先拒后授权」演练中撞到并当场报告）
- **现象**：申请 `perm-test/` → 回执 `status: granted`，看似成功；但再读 `perm-test/hello.md` 仍回
  「不在给看范围」，同目录 `list_files` 也被拒。补申请 `perm-test/**` 才真正读通。
- **根因**：授予校验（`_validate_patterns`：只查绝对路径/`..`/长度）与读取校验（glob 匹配）是两套逻辑——
  `dir/` 这类 pattern 能被收下并写进 grant，却匹配不到 `dir/` 下的任何文件路径。
- **期望**：授予回执必须说清这条 pattern 实际放开什么；这类 pattern 当场提示改用 `dir/**`。
- **证据**：审计 `~/.lighthouse/audit/miji.jsonl`（2026-09-20）——
  `16:19:21 request_access include=['perm-test/'] → granted` → `16:19:29 read_file perm-test/hello.md → 不在给看范围`
  → `16:20:08 request_access ['perm-test/**']` → `16:20:17 read_file perm-test/hello.md ✅`。
- **区域**：`core/scope.py`（匹配语义）、`core/server.py`（request_access 回执）、`lighthouse.sh`（approve/elevate）
- **严重度**：中（功能可用，但会让用户误判「已经批了」）
- **状态**：已修（commit `c34cafb`）：新增 `scope.dir_only_patterns()` / `dir_only_hint()`；自动授予、对话内授权、
  预授权与待批回执全部带 `hint`；CLI `approve` / `elevate` 同样打 ⚠️；提权套件 ⑪（5 项）锁死该行为。

---

### #19 服务重启后客户端手里的旧会话被作废 → 对方误报「找不到 mcp 环境」

- **发现**：2026-09-20（WorkBuddy 实测：服务重启后它再调用一直失败，误以为「mcp 环境没了」，还跑去翻本机 site-packages）
- **现象**：`Failed to callTool … Streamable HTTP error: {"code":-32001,"message":"MCP session not found"}`；重试无效（客户端不自动重新握手）
- **根因**：streamable-http 有状态模式下服务端用 `mcp-session-id` 跟踪会话——服务一重启，旧会话全部作废；而不少客户端会缓存会话 id 且不重连
- **证据**：WorkBuddy 会话日志 `[MCP] Failed to callTool`（2026-09-20 15:57）；本机复现：重启服务后拿旧会话 id POST → `Session not found`
- **修复**：服务端改**无状态传输** `stateless_http=True`——旧会话 id 直接被忽略、无会话 id 也照常工作；**重启服务从此对已连客户端无感**
- **区域**：`core/server.py`（传输层）
- **严重度**：中（服务重启会波及全部已连客户端）
- **状态**：已修（commit `53ef1a7`，加固套件 ⑫ 锁死）

### #20 人类用浏览器打开窗口地址 → 一直转圈（看起来像「服务挂了」）

- **发现**：2026-09-20（手机实测「打不开、请求超时」，而服务日志里它明明 200 OK）
- **现象**：浏览器 GET 窗口地址会挂在一条永不结束的事件流上（MCP 的 SSE 语义），页面永远加载不完
- **根因**：窗口地址是给 AI 客户端用的 MCP 接口，不是网页；浏览器没有「页面」可渲染
- **证据**：服务日志 `192.168.3.229 - "GET …" 200 OK`（手机确实连上了）+ 手机侧表现为超时/转圈
- **修复**：加一层轻中间件——浏览器式请求（Accept 含 text/html 或 UA 含 Mozilla，且**不要** text/event-stream）返回一句人话提示页；MCP 客户端请求原样放行
- **区域**：`core/server.py`（传输层）
- **严重度**：低（体验/误判，无安全影响）
- **状态**：已修（commit `2325b9f`，加固套件 ⑬ 锁死）

---

## 模板（复制用）

```markdown
### #N 一句话说清现象

- **发现**：YYYY-MM-DD（怎么撞到的）
- **现象**：（可观察到的行为，最好带原文）
- **期望**：（应该是什么样）
- **证据**：（命令输出 / 审计原文 / 报错文字 —— 必须能复现）
- **区域**：（文件或子系统）
- **严重度**：高 | 中 | 低
- **状态**：待修 | 待定 | 已修（commit）
```
