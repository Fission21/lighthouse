<div align="center">

# Lighthouse · 灯塔

**Open a window onto your local project — for web AIs to see, and only see what you circled.**

Four gates · output redaction · full audit log · write switch (read-only by default) · elevation is yours to grant, per request or by a standing policy you set · scope is asked, never assumed

<small>[中文版 README](README.md)</small>

<small>Authors · 诗人 & CC</small>

</div>

---

## What it is

Web AIs (ChatGPT connectors, any MCP client) are powerful, but they cannot see **your machine**.
Lighthouse opens a **controlled window** onto one local directory and exposes it over MCP — while
you declare up front what may and may not be seen, secrets are redacted on the way out, every call
leaves a trace, and nothing is ever widened without your approval.

```
your local project
   └─ window MCP server (4 gates · redaction · audit · write switch)   ← starts on boot
        └─ cloudflared tunnel (one hostname, path-routed to each window)
             └─ https://your-domain/<random path per window>
                  └─ web AI (ChatGPT connector / any MCP client)
```

In one line: **you are not handing over your machine — you are opening a window you drew yourself.**

## What it blocks, at a glance

| Concern | Lighthouse's answer |
|---|---|
| Will it browse other directories? | Gate ① realpath must stay under `root` — refused otherwise |
| Will it read my `.env` / private keys? | Gate ④ deny-list always wins: `.env* / *.pem / id_rsa* / credentials* / *.db` … no `include` pattern can override it |
| Will it see what I explicitly excluded? | Gate ② `exclude` globs (a directory match prunes the whole subtree) |
| Will it leak a key into an answer? | Output redaction: `sk-… / ghp_… / AIza… / AKIA… / Bearer … / private key blocks` → `«REDACTED»`, applied to search snippets too |
| Will it edit my code? | **Read-only by default.** You open the write switch by hand (optionally auto-closing in N minutes); every change is backed up first and hashed after |
| Can it widen its own access? | **Not by default.** It can only *ask*: `request_access` records a pending request, and granting is yours (`lighthouse.sh approve`), or you pre-arm a time-limited elevation window (`elevate`). You can also declare a standing policy per window (`auto-grant`, optionally bounded by `--ceiling`) — then requests inside that bound take effect immediately, while anything beyond it still waits for you. Elevation never beats `exclude` or the key deny-list |
| How do I know what it read? | Audit log, one JSON line per call (refusals included): `~/.lighthouse/audit/<window>.jsonl` |
| How do I trust any of this? | Five self-contained test suites (13 + 46 + 25 + 29 + 23 checks) — one command |

## What you need first (read this before installing)

Lighthouse comes in two tiers — take only what your goal needs, so nothing turns up missing halfway:

| | ① Local use (your own MCP clients) | ② Public, for web AIs (includes ①) |
|---|---|---|
| **Required** | Python 3.10+, the `mcp` library, one window entry | **＋ `cloudflared` ＋ a domain whose DNS is hosted on Cloudflare** |
| **Not required** | domain, cloudflared, public network | public IP, port forwarding, inbound ports, certificates |

Keeping services alive uses what the OS already has (macOS launchd / Linux systemd) — nothing to install;
run them in the foreground if you prefer no auto-start.

> **The one that trips people up: the domain.** Giving web AIs access needs a *stable public address*.
> Lighthouse uses a Cloudflare **named tunnel**, whose public entry can only be a subdomain of a zone in
> your Cloudflare account — **there is no way around this step.** It is Cloudflare's rule, not Lighthouse's.
> If you already own a domain, it is just: move its DNS to Cloudflare → create the tunnel → add one DNS record.

No domain yet? Three alternatives, none requiring a purchase:

- **ngrok free** — includes one static dev domain (`xxx.ngrok-free.app`; 1 GB / 20k requests per month). Point the tunnel at `127.0.0.1:<window port>`; no Lighthouse code changes (it just bypasses the `publish` pipeline);
- **Tailscale Funnel** — available on all plans (free included, beta), stable `<device>.<tailnet>.ts.net` hostname, wired up by hand the same way;
- **Cloudflare Quick Tunnel (trycloudflare)** — no domain needed, but the URL changes on every restart and it officially **does not support SSE**, which MCP streamable-http relies on: fine for a local smoke test, not for a connector.

On the web-AI side you also need: a ChatGPT account with Developer mode enabled (Settings → Security). See [`docs/CHATGPT.md`](docs/CHATGPT.md) (in Chinese).

## Quick start (4 steps)

```bash
# 0) Requirements: Python 3.10+ and the mcp library
pip install mcp

# 1) Spin up the sample window and verify the whole chain (no internet needed)
bash lighthouse.sh test            # temp instance → five suites → auto cleanup
#    ✅ smoke 13/13   ✅ read-only audit 46/46   ✅ write switch 25/25   ✅ elevation 29/29   ✅ hardening 23/23

# 2) Open a window onto YOUR project
#    Without a scope it will ASK you how much to expose — it never defaults to "everything"
bash lighthouse.sh new myproj ~/code/myproj
#    Or say it in one go:
bash lighthouse.sh new myproj ~/code/myproj --preset docs+code --title "My project"
bash lighthouse.sh start
bash lighthouse.sh url myproj      # local address — try it yourself first
```

At this point any local MCP client (Claude / Codex / Cursor …) can read it through that address.

```bash
# 3) Expose it to the web (so web-based AIs can see it too) — requires: a domain with DNS on Cloudflare (see "What you need first")
cloudflared tunnel login
cloudflared tunnel create lighthouse          # note the UUID it prints
cloudflared tunnel route dns <UUID> mcp.example.com   # UUID, not the tunnel name
# put UUID + hostname into config.json (tunnel_id / hostname), then:
bash lighthouse.sh publish                    # tunnel config + restart + health check
```

```bash
# 4) In your web AI: Settings → Connectors → Create app → paste https://your-domain/<window path>
```

Then mention that connector in a chat and ask it to “read the README and tell me the pass phrase”.
It will actually call the tools — and your audit log will show exactly what it read.

## Day-to-day commands

```bash
bash lighthouse.sh list                  # which windows exist
bash lighthouse.sh status                # services + ports + scope + write switch
bash lighthouse.sh url <id>              # address (local / public)
bash lighthouse.sh write <id> on 30      # allow writing for 30 minutes (auto-closes)
bash lighthouse.sh write <id> off        # back to read-only immediately
bash lighthouse.sh write status          # who can write right now
bash lighthouse.sh scope <id>            # current grants / pending requests / standing policy
bash lighthouse.sh approve <id>          # approve its elevation request
bash lighthouse.sh elevate <id> 30 --scope "src/**"   # pre-arm: auto-approve such requests for 30 min
bash lighthouse.sh auto-grant <id> on [--ceiling "src/**,docs/**"]   # standing policy: requests inside the bound take effect at once
bash lighthouse.sh auto-grant <id> off   # back to ask-first (applies to the very next request, no restart)
bash lighthouse.sh deny <id>             # revoke every elevation at once
bash lighthouse.sh test [id]             # the five suites
bash lighthouse.sh doctor                # interpreter / deps / tunnel / config check
```

## Raising access from inside a chat

Scenario: the `miji` window only exposes documentation. You say in the chat “let it see the code
as well” — how does it actually get there?

```
you (in chat): give it access to the code, please
   │
   ├─ agent calls request_access(include=["src/**"], reason="user asked for code access")
   │      ├─ the window has a standing policy (auto-grant) and the request is inside its bound → granted ✅
   │      ├─ an elevation window is armed and the request is within its limit → granted ✅
   │      └─ otherwise / beyond the bound → recorded as PENDING; the visible scope does not change ⏸
   │                                       └─ you run approve → effective immediately
   │
   └─ agent relays: run `bash lighthouse.sh approve <window>` on the machine running Lighthouse
```

Key points:

- **By default the agent cannot elevate itself.** Without your approval, `request_access` only leaves a
  pending entry (visible in `window_info`);
- **A standing policy is *your* prior authorization.** `bash lighthouse.sh auto-grant <window> on --ceiling "src/**,docs/**"`
  makes requests inside the bound take effect immediately — nothing left to run by hand; anything beyond it
  still becomes a pending request. With no `--ceiling`, any in-scope request is accepted (the key deny-list
  and `exclude` still hold). The policy is read live, so `off` applies to the very next request without a restart;
- **What you click on the provider side is a different layer.** ChatGPT's “allow this app?” dialog is the
  platform's own tool-call confirmation, not a Lighthouse grant — and with a standing policy on, a request
  can take effect without any dialog appearing at all. `--ceiling` and the audit log are what keep you
  informed. Which trade-off you want is yours to pick;
- **Elevation is not declassification.** `.env`, private keys, `exclude`d directories stay invisible —
  the deny-list and exclusions outrank every grant;
- **Three ways to give it**: a standing policy (`auto-grant`), approve afterwards (`approve`, for exactly
  what was requested), or pre-arm a window (`elevate <id> 30 --scope "src/**"` — expires on its own);
- **Revoke anytime**: `deny <id>` clears everything (extra scope + pending request + elevation window);
  `auto-grant <id> off` drops the standing policy.

That is how “the choice stays with the owner” is implemented: **as open or as tight as you decide — the gates stay in your hand.**

## Layout

```
lighthouse/
├── lighthouse.sh            # single entry point (new/start/stop/status/url/publish/write/elevate/auto-grant/approve/deny/scope/test/doctor)
├── config.json              # global config: hostname / tunnel / state dir  (config.local.json overrides, gitignored)
├── windows.json             # window registry: the visible scope of every window lives here
├── core/
│   ├── server.py            # the window MCP server (gates + redaction + audit + write tools + request_access)
│   ├── config.py            # config & registry loading, path resolution
│   ├── scope.py             # scope authorization (grant / pending / arm / ceiling bound check)
│   ├── add_window.py        # `lighthouse.sh new` (asks the owner when no scope is given)
│   ├── render_services.py   # launchd / systemd service generation
│   ├── render_ingress.py    # tunnel ingress generation (one hostname, path routing)
│   └── switch.py            # write switch CLI
├── tests/
│   ├── smoke_window.py      # generic smoke, 13 checks (works on any window)
│   ├── audit_readonly.py    # read-only audit, 46 checks (incl. before/after file fingerprints)
│   ├── test_write.py        # write switch, 25 checks (off=refuse all / on=full flow / off again)
│   ├── test_elevate.py      # elevation, 29 checks (ask≠grant / approve / revoke / pre-arm / standing policy / expiry)
│   ├── test_hardening.py    # gate hardening, 23 checks (case-insensitive bypass, .git, escapes, enumeration)
│   └── run_all_tests.sh     # all five suites against a throw-away instance
├── demo/project/            # sample project (with a pass phrase, proving reads are real)
└── docs/
    ├── ARCHITECTURE.md      # how a single call is filtered (in Chinese)
    ├── SECURITY.md          # threat model & boundaries (in Chinese)
    ├── CHATGPT.md           # step-by-step ChatGPT connector setup (in Chinese)
    └── OPEN_A_WINDOW.md     # four steps to open a window + prompts to hand the job to another AI (in Chinese)
```

## How it manages to be both convenient and safe

**Scope lives in one place.** One entry in `windows.json` = one window:

```json
"myproj": {
  "title": "My project",
  "root": "~/code/myproj",                 // only this tree
  "include": ["README.md", "docs/**", "src/**"],   // only these files
  "exclude": ["private/**"],               // pruned, subtree included
  "port": 8940,
  "path": "/w-myproj-6m1yo0",              // random path segment as a weak secret
  "visibility": "local",                   // local = private; publish refuses to expose it
  "write": { "enabled": true }             // writing *may* be enabled (still off by default)
}
```

Changing the scope means editing one file — no code changes.

**Fail-closed.** Path resolution errors, unmatched patterns, anything ambiguous — refused, and logged.

**One hostname, many windows.** Path routing forwards to each window's port: adding a window never
touches DNS, and a single `publish` rebuilds the routing table.

**Write switch, two locks.** `write.enabled` in the registry (master) plus a runtime switch
(`~/.lighthouse/state/window-write.json`). Both must be open. Every write is backed up first and
recorded with before/after sha256 hashes.

## Dependencies & thanks

| Dependency | Purpose | Link |
|---|---|---|
| Python 3.10+ | runtime | https://www.python.org/ |
| MCP Python SDK | MCP server over streamable-http | https://github.com/modelcontextprotocol/python-sdk |
| cloudflared | publish a local port safely (outbound-only tunnel, no open ports) | https://github.com/cloudflare/cloudflared |
| macOS launchd / Linux systemd | keep window services alive | built-in |

**With thanks to**: the Model Context Protocol team, for turning “how AIs reach the outside world”
into an open protocol; and Cloudflare, for making outbound-only tunnels the default safe option.
Without those two, Lighthouse would be nothing but a pile of local scripts.

## Gotchas (all hit in practice)

| Gotcha | Symptom | Fix |
|---|---|---|
| MCP SDK DNS-rebinding protection | 200 locally, 404 on the public hostname | disable it server-side (`TransportSecuritySettings`, built in) |
| `cloudflared tunnel route dns` with the tunnel *name* | DNS points at the wrong tunnel | always pass the tunnel **UUID** |
| Sharing one tunnel | requests load-balanced to another machine → 404 | give Lighthouse its own dedicated tunnel |
| Registry path | the service looked for `windows.json` inside `core/` | relative paths resolve against the repo root (fixed in `core/server.py`) |
| ChatGPT stream stalls on the first token | the answer is actually complete | reload the page |
| ChatGPT cannot reach your machine directly | — | Lighthouse goes fully outbound through the tunnel: no public IP, no port forwarding |

## Changelog

- **v1.2** — standing elevation policy `auto-grant` (`bash lighthouse.sh auto-grant <id> on [--ceiling "src/**,docs/**"] | off | status`):
  requests inside the bound take effect immediately (no local command to run), anything beyond it still turns into a
  pending request; the policy is read live so `off` tightens at once without a restart; suspicious config degrades
  fail-closed. Gate hardening after an attack probe found real bypasses: all deny/glob matching is now case-insensitive
  (on macOS/Windows `.ENV` / `Id_Rsa` used to read the very files `.env` / `id_rsa` are denied, and `PRIVATE/x` slipped
  past `exclude private/**`), the whole `.git/` directory is denied (it used to deny only `.git/config`), and the key
  name list covers `apikey.txt` / `my-secrets.txt` / `config.env` / `token.md` variants. 5th test suite (hardening, 23 checks).
  Fixes: CLI and server now resolve the same registry file, and `restart` no longer fails when `$0` is a relative path.
- **v1.1** — in-chat elevation (`request_access` request-only + `approve` / `elevate` / `deny` / `scope`) ·
  scope is asked at window-creation time (never silently defaults to `**/*`) · 4th test suite (elevation, 18 checks) ·
  private local config convention (`*.local.json`). 102 checks total.
- **v1.0** — first public release: four gates, redaction, audit, write switch (two locks), three test suites,
  multi-window path routing, launchd/systemd service generation.

## Authors

Designed and built by **诗人 (Poet)** ([@Fission21](https://github.com/Fission21)) and **CC**.

## License

MIT © Poet & CC (诗人 & CC)
