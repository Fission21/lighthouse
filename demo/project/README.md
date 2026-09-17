# 示例项目 · Demo Project

这是 lighthouse 自带的**演示项目**：一扇窗把它开给网页 AI 看，用来把整条链路跑通。

> 验证口令（用来证明 AI 真的读到了本地文件，而不是猜的）：**海雾里的橘子**

## 目录里有什么

```
demo/project/
├── README.md            ← 你正在看的这个（含验证口令）
├── docs/
│   ├── notes.md         随手记的笔记
│   ├── roadmap.md       项目路线图
│   └── .env             ⚠️ 故意留的假密钥文件——用来演示「密钥类文件一律读不到」
├── data/
│   └── tasks.json       一份任务清单（结构化数据示例）
├── src/
│   └── greet.py         一段小代码（源码示例）
└── private/
    └── notes-internal.md ⚠️ 被 exclude 规则挡住，AI 永远看不到（演示排除范围）
```

## 为什么要放 `.env` 和 `private/`

不是为了好看，是为了**让你亲眼看到闸门生效**：

- `docs/.env` 命中的是「默认拉黑」——凡 `.env*`、`*.pem`、`id_rsa*`、`credentials*`、`*.db` 这类文件，
  不管你的 include 写得多宽，一律拒（返回 `命中安全拉黑规则`）。
- `private/**` 命中的是「exclude 排除」——你在 windows.json 里明确说不给看的东西。
- 两个文件的内容都是**假数据**，随便看，但 AI 那边拿不到。

## 用 AI 试一把

在网页 AI（比如 ChatGPT）里挂上这扇窗的连接器后，问：

> 读取 README.md，告诉我里面的「验证口令」是什么

它会真去调用工具（服务端有审计日志），然后答出「海雾里的橘子」。
