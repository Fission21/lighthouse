# 受控资料库（kb 模式）—— 一人一条地址 · 按等级授权 · 全程可追踪

给同事看的「只读资料库」：他们用自己的 AI 客户端（ChatGPT 连接器等）连上你发的地址，就能
检索/阅读**你审批过**的资料。看不到的内容 = 没授权，不是坏了。

和普通窗口的区别：

| | 普通窗口 | 资料库窗口（kb 模式） |
|---|---|---|
| 工具面 | 路径型（list_files / read_file / search / write_*） | **只有** `kb_info` / `kb_list` / `kb_search` / `kb_read`；路径与写工具**根本不注册** |
| 读什么 | 命中 include/exclude 的文件 | 只有台账里 `approved` 的篇目 |
| 谁能读哪篇 | 窗口级（一样） | 篇级：每篇一个等级，按那段地址上的等级放行 |
| 对外身份 | 无 | 每人一条专属地址，可停用/换地址/到期 |
| 追踪 | 审计留痕 | 审计留痕 + 「谁读了哪篇」报表 |

## 一、一眼看懂的数据流

```
你的资料目录（原始文档/…）
   │  kb scan        抽文本（MinerU/textutil）→ 登记待批
   ▼
台账 catalog.json ──kb approve──► approved + 等级
   │                                   │
   │                              kb_list / kb_read（外部 AI 只能读 approved）
   ▼                                   ▲
同事申请（网页 /request）── 你批准 ──► 每人一条地址（等级 + 有效期）
```

## 二、快速开始（5 步，全程本机）

```bash
# 0) 依赖：见 README「跑起来需要什么」；可选 MinerU 用来抽 PDF（~/demo/ai-tools/mineru-venv）

# 1) 建资料库骨架（默认 ~/Documents/招投标文档库）
bash lighthouse.sh kb init bidkb

# 2) 把资料放进 ~/Documents/招投标文档库/原始文档/（按分类建子目录）

# 3) 开窗（kb 模式写进 windows.json，见下），然后扫目录、登记待批
bash lighthouse.sh kb scan bidkb

# 4) 看清单、按篇审批（商务档可以直接 --all-pending，技术/核心建议逐篇给）
bash lighthouse.sh kb pending bidkb
bash lighthouse.sh kb approve bidkb --all-pending --level L1-商务
bash lighthouse.sh kb approve bidkb <编号> --level L2-技术

# 5) 给同事发地址（或让他自己去申请页自助申请）
bash lighthouse.sh kb invite bidkb --name 张三 --level "L2-技术" --out ~/Desktop/张三-使用说明.md
bash lighthouse.sh kb admin-url bidkb        # 管理页 + 申请页地址
```

`windows.json`（或 `windows.local.json`）里那个窗口要加 kb 段：

```json
{
  "windows": {
    "bidkb": {
      "title": "招投标资料（受控·分级）",
      "root": "/Users/you/Documents/招投标文档库",
      "include": ["原始文档/**"],
      "exclude": ["同事说明/**"],
      "port": 8951,
      "path": "/w-bidkb",
      "kb": {
        "enabled": true,
        "docs_dir": "原始文档",
        "levels": ["L1-商务", "L2-技术", "L3-核心"],
        "default_level": "L1-商务",
        "portal": {
          "enabled": true,
          "public_levels": ["L1-商务", "L2-技术", "L3-核心"],
          "auto_approve_levels": ["L1-商务"],
          "admin_remote": false
        }
      }
    }
  }
}
```

## 三、等级怎么设

- 等级就是你自己起的名（`levels` 里写什么就是什么，通常是「商务 / 技术 / 核心」三档）。
- **不做继承**：地址上写了 `L1,L2` 才两档都看得到；只写 `L3` 就只能看 `L3` 那几篇。
  要有包含关系就**显式列出来**——这样不会出现「我以为 L3 天然含 L2」的想当然。
- 每篇资料的等级在**审批那一刻**定（`--level`），之后随时可改：重新 `approve` 一次即可。
- 审批**压不过**安全拉黑：命中黑名单（密钥/凭据/数据库类）的文件即使标成 approved 也读不到；
  include/exclude 之外的路径同样一律拒。

## 四、同事怎么拿到地址

两条路，任选：

1. **自助申请**：把 `…/request` 发给同事 → 他填「姓名/部门/用途/想要的等级」→ 拿到「申请号 + 查询码」→
   到 `…/request/status` 查进度取地址。
   - `auto_approve_levels` 里的等级（默认「商务」）**自动放行**，提交完当场看到地址；
   - 其余等级进入待批，你批完他再回状态页取地址。
2. **你直接发**：`kb invite` / `kb grant` 一条命令，附带一页使用说明。

地址形态：`https://<你的域名>/kb-<随机段>`。**口令进 URL** 是硬需求——ChatGPT 网页连接器创建
应用时只能填 URL，没有自定义 header 输入框，所以地址本身就是凭据。

地址管理（都在那一条地址上，**改权限不用换地址**）：

```bash
bash lighthouse.sh kb users bidkb                      # 谁在用、什么等级、调用/被拒次数、最后活跃
bash lighthouse.sh kb set-level bidkb --name 张三 --level "L1-商务,L2-技术"
bash lighthouse.sh kb rotate bidkb --name 张三          # 换新地址（旧的立刻失效，等级不变）
bash lighthouse.sh kb revoke bidkb --name 张三          # 停用（随时 --enable 恢复）
```

## 四·五、同事直接拿文件（不只是用 AI 查）

两条路，都过同一套闸门（审批 + 等级 + 拉黑 + 原文件哈希 + 越界），都记审计：

1. **门户下载页（不用 AI）**：同事在他自己的地址后面加 `/files` 打开，就能看到「他能看的那几篇」，
   逐条下载**原件**或**抽取文本**，也可以勾几篇**打包成 zip** 一次拿走。
   链接跟着他的地址走 —— 地址一停用/换新，那些链接立刻失效。
2. **让 AI 给链接**：同事在对话里说「把《XX》的原件给我」，AI 调 `kb_link(doc_id)` 拿到一条
   **限时下载链接**（默认 15 分钟，只绑这一篇 + 这个人），直接贴给他点。

配置（`windows.json` 里那扇窗的 `kb.download`，不写就是下面这套默认）：

```json
"download": { "enabled": true, "original": true, "link_minutes": 15,
              "max_bundle_mb": 200, "max_file_mb": 50 }
```

| 字段 | 作用 |
|---|---|
| `enabled` | 总开关。关掉后只剩 MCP 在线阅读，下载页与 `kb_link` 都返回「未开放下载」 |
| `original` | 是否给**原件**（.docx/.pdf…）。关掉则只给抽取后的文本 |
| `link_minutes` | `kb_link` 给的限时链接有效期（1~1440 分钟） |
| `max_file_mb` / `max_bundle_mb` | 单文件 / 单次打包的上限 |

两件要知道的事：

- **原件可能自带修订记录、批注、隐藏工作表、文档属性（作者/路径）** —— 这些不在灯塔的脱敏
  范围内（脱敏只作用于读进对话的文本）。对外发原件前，先在自己这边「接受所有修订 / 清除个人信息」，
  或者把 `original` 关掉只发文本。
- 限时链接是**分享链接**语义：拿到链接的人 15 分钟内能下这一篇。要更紧就调小 `link_minutes`，
  或者让对方改用自己的地址页下载。

### 短地址要重新 publish 一次

同事的地址是 `https://域名/kb-<口令>`（短）或 `https://域名/<窗口路径>/kb-<口令>`（长，永远可用）。
短地址靠隧道里一条 `path: /kb-*` 规则分流，**加/改资料库窗口后要跑一次
`bash lighthouse.sh publish`**，否则外网是 404（本机直连却正常，容易误判成代码问题）。
同时只有**一个**资料库窗口时才会加这条规则 —— 多个资料库窗口时短地址会串门，一律用长地址。

## 四·五·五、换配色主题

门户的配色集中在 `core/theme.py`（设计令牌 + 三套主题），页面只引用变量：

```bash
bash lighthouse.sh kb theme bidkb            # 看当前主题与可选
bash lighthouse.sh kb theme bidkb teal       # indigo（墨蓝，默认）/ teal（松石绿）/ paper（暖纸质）
bash lighthouse.sh restart bidkb             # 重启窗口生效
```

写页面的三条硬规矩见 [`DESIGN.md`](DESIGN.md)；测试里也有断言盯着（样式块以外出现 `#rrggbb` 就失败）。

## 四·六、管理页（网页上管文件，不用敲命令）

> 界面约定：**同事列表默认收起成一行**（点「编辑 ▾」才展开表单），**长说明一律收进 `?` 悬停提示**，
> 资料与同事列表都**分页**（资料每页 20 篇、同事每页 8 位，翻页链接带管理令与筛选条件）。
> 加东西时别把大段说明直接铺在页面上。
>
> **给用户看/复制的链接一律是完整地址**（`_copybtn()` 负责复制，`_Portal._abs()` 负责补域名）。
> 页面里只写相对路径（`/w-xxx/register?c=…`）会让「复制」复制出半截链接 —— 踩过这个坑。


`<域名>/<窗口路径>/admin?k=<管理令>`（取法：`bash lighthouse.sh kb admin-url bidkb`）。一页里做完全部维护：

| 区域 | 能干什么 |
|---|---|
| **资料 · 添加** | **把文件或整个文件夹拖进虚线框**（最省事，松手就自动上传）；或「选择文件」/「选择文件夹」。选好分类（或新建分类名）与等级，点「上传，先进待批」或「上传并直接公开」。文件夹层级原样保留，上传后自动抽取文本、自动入库；上传中有百分比进度 |
| **资料 · 清单** | 搜索 + 按状态/分类/等级筛选；**每行的「等级」列可以直接改**，再点「保存等级」或「公开」；「看原件」是你自己预览（不受等级限制，不占同事地址） |
| **资料 · 批量** | 勾选多篇 → 栏里选等级 → 「设为公开」/「只改等级」/「下架」/「移除条目（不删文件）」。长库建议先按分类筛选再全选 |
| **资料 · 扫描** | 文件已经用 Finder 放进资料目录时点「扫描资料目录」（只认新增/改动，不动已定好的等级） |
| **已公开** | 改等级（同事地址不变，权限立即跟着变）、下架（回到待批，同事立刻看不到也下不到）、**看原件**（维护者预览，不受等级限制） |
| **类型不支持** | 提示需人工转成 .md/.txt，可从台账删掉（文件不动，下次扫描会重新进来） |
| **待批申请** | 同事的地址申请：当场选等级与有效期 → 批准 / 驳回 |
| **已授权的同事** | 一行里改**等级（可多选）**、部门、备注、姓名、有效期、启用停用；换地址、删除、看地址复制、给同事**开通/重置网页账号**、看他的用量 |
| **直接发一条地址** | 不经申请，直接给某人发地址 |
| 用量看板 | 按人 / 按天 / 按资料 / 按工具 |

网页上的每一步都写审计（`actor: admin`），跟命令行等价 —— 同一份台账、同一套闸门。

**上传的限制与开关**（窗口配置 `kb.upload`，不写就是下面这套）：

```json
"upload": { "enabled": true, "max_file_mb": 100, "max_total_mb": 300 }
```

- 上传只写进资料目录内部：`../`、绝对路径、隐藏文件都会被洗掉或拒绝（已写测试）。
- 收下的类型会尽力抽取（Word/PDF/PPT/Excel 靠 textutil / MinerU）；抽不出文本的类型标「类型不支持」，
  不能公开（防止同事拿到一篇读不出内容的资料）。
- 上限默认**单文件 200MB / 一次 1GB / 一次最多 2000 个文件**（`kb.upload.max_file_mb`、`max_total_mb`）；
  超限的文件会被拒收并在回执里点名列出来，已收下的不受影响。
- 大文件夹别用「表单直传」那种老路子 —— 页面现在的上传是带进度的异步上传：上传中会显示百分比，
  不会看起来像“点了没反应”。
- 同名文件会被覆盖 —— 覆盖等于「内容变了」，该篇会**自动退回待批**，需要你重新点头。
- 关掉上传（`enabled: false`）后，管理页不再给上传区，只能放进目录再点扫描。

**要从手机批**：窗口配置里 `kb.portal.admin_remote: true`（默认 false=只允许部署机直连）。
开了以后管理页地址能从公网打开，**那条带 `?k=` 的链接就等于管理口令，别转发**；万一发出去过，
`bash lighthouse.sh kb admin-url <窗口> --rotate` 换一把（旧链接立刻失效）。

## 四·七、同事改权限（管理页 / 命令行都行）

管理页「已授权的同事」里每人一行 = 一个完整小表单：**等级可多选**（勾哪几档看哪几档）、姓名/部门/备注、
有效期（天数 / 具体某天 / 无期限）、启用停用；同一行还有**更换地址**（旧地址当场作废）、**删除**、
**看地址**（复制完整 URL 发本人）、**看他的使用明细**（跳到按人过滤的用量看板）、
**网页账号**（开通 / 重置密码 / 删账号）。改完点那行「保存」**立刻生效**，不用重启 —— 权限是每次请求现读台账算的。

```bash
bash lighthouse.sh kb edit bidkb --name 张三 --levels "L1-商务,L3-核心" --days 90 --dept 技术部 --note "XX 项目"
bash lighthouse.sh kb edit bidkb --name 张三 --until 2026-12-31     # 到某天（当天 23:59 到期）
bash lighthouse.sh kb edit bidkb --name 张三 --disable              # 停用（--enable 恢复）
bash lighthouse.sh kb edit bidkb --name 张三 --rename 张三丰        # 改名，地址不变
bash lighthouse.sh kb rm   bidkb --name 张三 --yes                 # 彻底删除（连地址一起）
```

两条纪律：**等级至少要留一个**（一个都不勾 = 让他什么都看不到，想断开就用停用或删除）；**等级不继承、只影响本人**。

## 四·八、登录闸门（网页不再谁都能看）

网页挂在公网域名上，光靠「一条地址口令」不够 —— 任何人拿到页面都能看、都能试。所以门户加了一层
**账号密码登录**：

| 谁 | 怎么进 | 能看到什么 |
|---|---|---|
| **管理员** | `/login` 用管理员账号登录，**或** 老的管理令地址（`?k=`，兼容桌面快捷方式与脚本） | 管理页、用量、全部资料 |
| **同事** | `/login` 用给他的账号登录（用户名 + 一次性临时密码） | 只有他自己等级范围：资料清单、下载原件/文本、申请页 |
| **AI 助手（MCP）** | 不变：他那条专属地址（`/kb-<令牌>`） | 只读工具 + 限时下载链接，**完全不受登录影响**（机器不会登录） |
| **陌生人** | 什么都打不开（管理页 / 文件页 / 申请页一律 401 或 403） | — |

**要点**

- 密码只存 **scrypt 加盐哈希**（`~/.lighthouse/state/portal-users.json`，0600），文件里看不到明文；
  生成时只显示一次，忘了就重置。
- 登录后发 **HMAC 签名的会话 cookie**（`lh_sess`，HttpOnly / SameSite=Lax，密钥是 0600 的本机文件）：
  改一个字符、过期、改过密码、账号被删 —— 任何一样都立刻失效。默认 12 小时，勾「记住我」30 天。
- **连错 5 次锁 10 分钟**（账号 + 设备各算一份），提示统一为「用户名或密码不对」（不暴露账号是否存在）；
  登录成功/失败/锁定/登出/改密**全部进审计**，带 IP 与 UA。
- 申请页默认**也要登录**（同事先拿账号再申请）；真要放开陌生人自助申请，才把
  `kb.portal.allow_public_request` 设成 `true`。同事申请时**姓名强制绑他自己**，顶不了别人的名。
- 响应头带 `X-Frame-Options: DENY`、`Referrer-Policy: no-referrer`、`X-Content-Type-Options: nosniff`，
  页面 `no-store` —— 防钓鱼框架、防把带口令的地址泄给外站、防旧页面缓存。

```bash
bash lighthouse.sh kb passwd bidkb --admin                            # 给自己设管理员密码（只打印一次）
bash lighthouse.sh kb passwd bidkb --user zhangsan --person 张三       # 给同事开通网页账号
bash lighthouse.sh kb accounts bidkb                                  # 谁有账号、什么角色、上次登录
```

## 四·九、邀请码（同事自助注册，一问就知道是谁）

> **别删的东西**：管理员密码存在 `~/.lighthouse/state/admin-password.txt`（只有你这台机器能读，权限 600）。
> 桌面有快捷方式「资料库-管理员密码.txt」直达它。丢了密码就 `bash lighthouse.sh kb passwd bidkb --admin`
> 重设一个（旧的立刻作废）；`kb passwd bidkb --delete --user <名>` 删同事账号。


**注册必须凭邀请码**，码只能由维护者生成 —— 这样每个同事的来源都可追溯：

```
# 发一张码（等级、给谁、有效期、备注都能定）
bash lighthouse.sh kb code bidkb --levels "L2-技术" --for 30d --person 张三 --note "XX 项目"
# → 邀请码 KH7M2-9PQRS ＋ 注册链接 https://…/w-bidkb-ny1s15/register?c=KH7M2-9PQRS
bash lighthouse.sh kb codes bidkb --links      # 看状态：可用/已用完/已过期/已停用，谁用的、什么 IP
bash lighthouse.sh kb code-off bidkb --code KH7M2-9PQRS [--delete]
```

同事拿到链接 → 填码 + 姓名/部门/用途 + 自设用户名密码 → **当场同时拿到**：

- **一条专属地址**（`…/kb-<口令>`，填进他的 AI 助手；等级、有效期按码上定的来）
- **一个网页账号**（用他自己设的密码登录，只能看自己等级内的资料）

管理页上也有一节「邀请码」，能发码、看状态、复制注册链接、停用/删除，不用敲命令。

规矩：

- 码**一码一人**（默认 1 次，最多 20 次）；有有效期；**停用/删除即刻失效**。
- 码上可以**指定给谁** —— 填了就只能用这个名字注册，等于把码绑在人头上。
- 已经领过地址的人再拿新码注册会被拒（**一人一条地址**，要换地址找维护者）。
- 码不对 / 过期 / 用过 / 停用，都拒；拒的原因写在页面上，也进审计。
- 谁生成的、谁用的、什么时间、什么 IP、用的哪个用户名 —— 全在 `kb codes` 和审计里。

## 五、追踪：谁在用、做了什么

每条 MCP 调用都进审计（`<状态目录>/audit/<窗口>.jsonl`），字段包含：

`ts` · `window` · **`principal`（人名）** · **`levels`（他当时的等级）** · `tool` · `args`
（含 `doc_id`/关键词/下载字节数）· `ok` / `denied` · `ip` · `ua`

下载相关的工具名：`kb_files`（打开清单页）、`kb_download`（下了一篇，含原件/文本与字节数）、
`kb_bundle`（打包，含勾选的编号与总字节）、`kb_link`（AI 生成了限时链接）。

被拒的也记：越级读、错口令、停用/到期的地址、路径越界。看报表：

```bash
bash lighthouse.sh kb usage bidkb --days 7            # 按人：调用/被拒/最后活跃/常读篇目
bash lighthouse.sh kb usage bidkb --days 7 --by doc   # 哪几篇最常被读
bash lighthouse.sh kb usage bidkb --days 7 --csv > 用量.csv
```

管理页里也有同一份用量看板（含最近调用明细）。

## 六、网页

| 页面 | 谁能开 | 干什么 |
|---|---|---|
| `<窗路径>/request` | 任何人 | 自助申请（限速 + 蜜罐 + 等级白名单，不信任前端传的等级） |
| `<窗路径>/request/status` | 申请人（凭申请号+查询码） | 查进度、取地址 |
| `<窗路径>/admin?k=<管理令>` | **默认只允许在部署机上访问** | 待批一键批准、直接发放、停用/换地址、用量看板 |

- 管理令是单独一条口令（`adm_…`），和同事的访问地址不同，两者互不通用。
- 要把管理页开放到公网（例如你想在手机上批）：把 `portal.admin_remote` 设为 `true`。
  此时**必须**带上管理令（`?k=…`），否则 401；`bash lighthouse.sh kb admin-url` 打印完整地址。
- 判断依据是「有没有云端转发头」：带 `CF-Connecting-IP`/`X-Forwarded-For` 的请求默认直接 403，
  所以哪怕域名被人猜到，管理页在关闭状态下也打不开。

## 七、让 agent 帮你盯申请

```bash
bash lighthouse.sh kb notify bidkb            # 列出还没汇报过的新申请（自动放行的也在内）
bash lighthouse.sh kb notify bidkb --json     # 机器可读
bash lighthouse.sh kb notify bidkb --ack      # 汇报完打标，避免重复提醒
```

配合定时任务（Hermes cron / launchd / 系统 crontab）每隔几分钟跑一次 `kb notify bidkb`，
有新申请就让 agent 告诉你。**申请只读不写**：MCP 侧没有任何写台账的能力，
同事的 AI 无论如何都改不了「谁能看什么」。

## 八、部署坑：资料库别放在被系统保护的目录里

macOS 上 `~/Documents`、`~/Desktop`、`~/Downloads` 属于 TCC 保护目录。窗口服务常驻运行（launchd/systemd），
**不在**你的终端权限里 —— 如果资料库根目录放在这些位置，服务启动看起来正常、台账也读得到，
但只要有一篇「这个地址有权看」的资料，任何工具调用都会**永久挂住**（等一个不会出现的权限弹窗）：

```
$ log show --last 15m --predicate 'eventMessage CONTAINS "Documents"'
… AUTHREQ_PROMPTING: service=kTCCServiceSystemPolicyDocumentsFolder, subject=…/venv/bin/python
```

**做法**：资料库根目录放在非保护目录（例如 `~/资料库`、`~/demo/招投标文档库`）。
确实想放 `~/Documents`，就把跑服务那个 python 二进制加进「系统设置 → 隐私与安全性 → 完全磁盘访问权限」。

## 九、安全边界（写清楚哪些事做不到）

- 文档里说「审批压不过拉黑、等级不做继承、内容一改自动退回待批、MCP 侧零写能力」——
  这四条都有测试守着（`tests/test_kb.py`）。
- `kb_read` 返回的是**抽取后的文本**，不是原件；原件（.docx/.pdf）不对外。
- 台账、同事记录、管理令都在本机 `<状态目录>` 下，权限等同你的账号。
- 地址泄漏的止损动作是 `kb rotate`（换地址）或 `kb revoke`（停用），都是立即生效。
- 没有做的事：不做全文向量检索、不做原件下载、不做多窗口共享台账、不做同事自助改权限。
