# AGENTS.md — 给 AI 编码助手的项目说明

> 事实标准文件（Codex / OpenCode / Cursor 等直接读取）。改这个项目前先读本文件。

## 这是什么

Lighthouse（灯塔）：把本地目录通过 MCP 协议安全地开给外部 AI 读的工具。
四道闸（根界 / exclude / include / 默认拉黑）+ 输出脱敏 + 审计 + 写开关。

## 三条铁律（改代码时必须保持）

1. **fail-closed**：任何判定失败（路径解析异常、正则异常、说不清）→ 拒绝 + 记审计。
   绝不允许「拿不准就放行」的写法。
2. **默认拉黑不可关**：`core/server.py` 的 `DENY_PATTERNS` 与脱敏规则永远生效，
   不提供关闭开关（窗口的 `deny_extra` 只能加严，不能放松）。
3. **写权限两级锁**：`windows.json` 的 `write.enabled`（总闸）+ 运行时开关
   `~/.lighthouse/state/window-write.json`（`switch.py` 管理）。两个都开才能写；
   写前必须备份、写后必须记 sha256。

## 关键约定

- **窗口范围只写在 `windows.json`**——不要在任何别的文件里重复定义范围。
- **相对路径按仓库根解析**（`core/config.py:window_root`）；不要用进程 CWD 拼路径。
- **状态目录**：审计 / 备份 / 写开关 / 日志都在 `~/.lighthouse`（可用 `LIGHTHOUSE_STATE` 覆盖）。
- **解释器**：`LIGHTHOUSE_PY` 环境变量优先，其次 `python3`。生成的服务定义里会固化解释器路径。
- **多窗口共用域名**：`core/render_ingress.py` 生成「单 hostname + 每窗口一条 path 规则」，
  顺序敏感（具体路径在前，404 兜底在后）。手改隧道配置会被下一次 render 覆盖。

## 改完必须做的验证

```bash
bash tests/run_all_tests.sh      # 三套：通用冒烟 13 / 只读审计 45 / 写开关 25，必须全绿
```

任何安全相关的改动（闸门、脱敏、写路径）都要补一条测试——测试套件是这个项目的安全承诺书。

## 目录速查

| 文件 | 职责 |
|---|---|
| `core/server.py` | 窗口服务：闸门、脱敏、审计、四个读工具 + 四个写工具 |
| `core/config.py` | 配置与注册表读取、路径解析 |
| `core/add_window.py` | `lighthouse.sh new` 的实现 |
| `core/render_services.py` | launchd / systemd 服务定义生成 |
| `core/render_ingress.py` | 隧道 ingress 生成（路径分流） |
| `core/switch.py` | 写开关 CLI |
| `tests/*` | 三套测试 + 一键验收 |
