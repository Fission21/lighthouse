# 路线图：从「受治理的文件访问」到「Agent 访问网关」

> 本文记录**方向**，不是功能承诺表。每一节都标了「现状 / 缺口 / 触发条件」——
> **没到触发条件之前不做**。项目哲学：先把已经有的那一层做扎实，再堆下一层。

## 一句话

v1.x 解决的是「让 AI 读本地文件时，这次访问被治理」。
再往前一步是「**任何 Agent 在触碰真实资源之前，先过身份 + 策略 + 审批 + 审计**」。
那一步**尚未开始**。

## 成长线（哪些已交付、哪些没有）

```
v1     安全 filesystem MCP（四道闸 + 脱敏 + 审计 + 写锁）              ← 已交付
  ↓
v1.1   对话内提权（申请制：只有用户能批准）                            ← 已交付
  ↓
v1.2   常驻提权策略 auto-grant + 闸门加固（攻击性探测修穿透）           ← 已交付
  ↓
下一步  Identity + Policy（认证 → 身份 → 按身份分权）                  ← 缺口在这
  ↓
再下一步 Resource Adapter（git / db / docker / ssh …）                 ← 未开始
  ↓
最终    Agent Access Gateway                                          ← 愿景
```

现在的请求链与目标请求链：

```
今天：  Internet → 随机 URL 路径段 → [ 授权 · 脱敏 · 审计 ] → 文件
                                    ↑ 这一层已经很扎实

目标：  Internet → Authentication → Identity → Authorization/Policy → Resource
                   ↑ 缺的就是这一层
```

## 1. 认证与身份（下一阶段的第一优先）

**现状**：随机 URL 路径段 = **不可枚举**，不是认证。审计粒度停在资源上
（`window / tool / args / ok / reason`），多个客户端接同一个窗口时**分不出是谁**。

**缺口**：

- 请求里没有 principal；
- 没有「谁在调」的概念，因此也没有「给谁看什么」。

**做了之后长什么样**：

```
Agent A → identity = chatgpt-john       Policy: docs/**
Agent B → identity = claude-code-mac    Policy: src/**
```

审计从资源粒度升级为决策粒度：

```jsonc
// 今天
{"tool":"read_file","args":{"path":"src/a.py"},"ok":true}

// 有了 principal 之后
{"principal":"chatgpt-john","tool":"read_file","resource":"src/a.py",
 "policy":"project-read","decision":"allow"}
```

**触发条件**：出现第一个真实需求——「同一扇窗要服务两个不同客户端，且两者应当看到不同范围」。
在那之前，`windows.json` 的「一窗一范围」完全够用；**不要为了架构美感提前上身份系统**
（会得到一堆没有主语的规则）。

**短期替代（今天就能用）**：在隧道层加 Cloudflare Access。属于部署侧配置，本 repo 不内置。

## 2. Policy 按身份分权

有了 principal 之后，「一窗一范围」升级为「一窗多策略」。
顺序必须是 **先 identity、后 per-principal policy**——反过来做会得到一堆无法归因的规则。

## 3. 审计升级为决策记录

目标字段：`principal / tool / resource / policy / decision`（外加今天的 ok/reason 语义）。
**前置依赖 1 和 2**：现在改只能往每条记录里塞一个空的 principal 字段，没有意义。

## 4. 资源适配器

```
              Lighthouse
                  │
      ┌───────────┼───────────┐
      ↓           ↓           ↓
  Filesystem     Git       Database
      ↓           ↓           ↓
   Docker        SSH      Internal API
```

所有资源共享同一套：**Identity · Policy · Approval · Redaction · Audit**（Rollback 待定）。

**现状**：只有 filesystem 一种资源，直接实现在 `core/server.py` 里。
**触发条件**：第二个资源类型真的出现时（例如「让 AI 读某张表的 schema」），
**先把接口抽出来、再接第二个**——不要先接第二个再回头抽象。

## 工程约束（现在就要遵守，不需要等触发条件）

### `core/server.py` 已经 35 KB —— 别让它长成上帝文件

**拆分触发条件**（满足任一，就先拆、再加新能力）：

- 文件超过 ~1500 行；或
- 要往里加第 5 个概念（identity / policy / session / rate-limit / adapter 任一）；或
- 单个函数超过 ~300 行或复杂度 30。

**目标结构**（届时按此拆；一次搬一层，每步测试必须全绿）：

```
core/
├── server.py          MCP transport / dispatch（只留骨架）
├── policy/            evaluator.py   rules.py   scope.py
├── security/          path_guard.py  redaction.py  blacklist.py
├── approval/          request.py     grant.py
├── audit/             logger.py
└── resources/         filesystem.py   （未来：git.py  postgres.py  docker.py）
```

**现在就要守的纪律**：新逻辑不要再往 `server.py` 里追加。它目前靠清晰的段落划分维持可读性，
再堆两三个概念就会失控。

## 明确不做（至少现在）

- ❌ 多租户 / 云托管 —— 这工具的价值恰恰在「跑在你自己的机器上」；
- ❌ 内置 OAuth / 认证服务器 —— 用隧道层的现成方案，别在 repo 里造认证；
- ❌ 为了对标竞品而加功能 —— 闸门数量不是指标，「一次访问说得清」才是；
- ❌ 拿愿景当卖点 —— 本文里的 Identity / Adapter 都是**计划**，README 的功能清单里不会出现它们，
  直到真的能用。

## 怎么参与

路线图上的每一项都必须过 `AGENTS.md` 的五条铁律（fail-closed · 默认拉黑不可关 ·
写权限两级锁 · 提权默认需用户批准 · 开窗必须先问范围），并且**测试套件必须跟着长**——
安全相关的改动没有测试，等于没有改动。
