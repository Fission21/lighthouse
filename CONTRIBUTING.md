# 参与开发（Contributing）

灯塔是一个「把本地项目开成受控窗口给 AI 看」的小工具，欢迎 PR。这份文档说清三件事：
**怎么跑起来、代码要什么风格、什么东西绝对不能进仓库**。

---

## 一、本地跑起来

```bash
git clone https://github.com/Fission21/lighthouse && cd lighthouse
python3 -m venv .venv && .venv/bin/pip install mcp      # 只依赖 mcp 库（Python 3.10+）

export LIGHTHOUSE_PY="$PWD/.venv/bin/python"            # 测试脚本用它起服务
bash lighthouse.sh test                                 # 全量验收，必须全绿
```

- `bash lighthouse.sh test` 会跑 6 套共 400+ 项断言，其中「受控资料库」那套自带隔离环境
  （临时目录 + 自己的服务进程 + 假资料库），不会碰你的真实配置。
- 只改了一部分时也可以单跑：`$LIGHTHOUSE_PY tests/test_kb.py`（受控资料库）、
  `$LIGHTHOUSE_PY tests/test_audit.py` 等。
- 改完 `core/*.py` 记得重启窗口：`bash lighthouse.sh restart`（只改 `windows*.json` 策略是实时生效的）。

## 二、代码风格

- **格式与 lint**：`ruff`（配置在 `pyproject.toml`）。提交前跑一次：

  ```bash
  pip install ruff && ruff check . && ruff format --check .
  ```

- 行长 110，Python 3.10+ 语法（`dict[str, str]` 这种内置泛型可以用）。
- 中文注释可以、也鼓励（这个项目的中文用户多），但**注释写「为什么」，不写「做了什么」**。
- 对外文案（README、docs、代码注释、工具描述、测试输出）一律用「用户」或「你」，
  **不要出现私人称呼**（项目所有者、家庭/亲密称呼等）。自查：`git grep -nE '主人|宝贝' | grep -v CONTRIBUTING` 应为空。

## 三、改 UI 之前先读

门户页面的颜色/间距/组件都有规范，见 [`docs/DESIGN.md`](docs/DESIGN.md)。三条硬规矩：

1. **页面里不许写死颜色**，只用 `core/theme.py` 里的 CSS 变量（有测试盯着）。
2. **一个区块只留一个主按钮**，其余用 `ghost`/`danger`/`quiet`/`tiny`。
3. 给用户看或复制的链接**必须是完整地址**（用 `_copybtn()` / `_Portal._abs()`）。

## 四、绝对不能进仓库的东西

| 别提交 | 为什么 | 放哪 |
|---|---|---|
| `config.local.json` / `windows.local.json` | 含真实域名、隧道 ID、窗口路径、开关 | 已在 `.gitignore`，示例配置进 `config.json` / `windows.json` |
| 地址令牌、管理令、邀请码、密码哈希、cookie 密钥 | 等于钥匙 | `~/.lighthouse/state/`（0600） |
| 资料库内容、审计日志、导出的原文 | 是用户的数据 | `~/.lighthouse/`、用户自己的目录 |

提交前自查：`git status --short` 里不该出现任何 `*.local.json` 或 `state/` 下的东西。

## 五、提交与 PR

- 提交信息用前缀：`feat:` `fix:` `docs:` `test:` `chore:` `refactor:`，一句话说清**用户能感知的变化**。
- PR 里请带上：**改了什么**、**怎么验证的**（贴测试输出/截图/命令）、**有什么风险**。
  测试没跑或没跑全，请直接说明，不要写「应该没问题」。
- 安全相关的改动（认证、鉴权、文件路径、下载签名）请额外说明威胁模型：
  谁能触发、最坏后果、怎么被挡住。
- 新功能请同时补测试；只加代码不加测试的 PR 会被要求补上。
- 大改动请先开 issue 说清场景，避免白做。

## 六、目录速览

```
core/            服务与工具实现
  server.py        MCP 服务 + 门户路由 + 审计
  config.py        全局配置与窗口注册表
  theme.py         设计令牌与配色主题
  kb_*.py          受控资料库（台账 / 门户 / 认证 / 邀请码 / 下载 / 用量 / CLI）
tests/           6 套验收脚本（纯 Python，无 pytest 依赖）
docs/            KB.md（受控资料库手册）、DESIGN.md（设计规范）等
lighthouse.sh    统一入口（起服务、测试、publish、kb 子命令）
```

## 七、有问题

开 issue，或看 `docs/` 下对应文档。安全问题请不要开公开 issue，先私下联系维护者。
