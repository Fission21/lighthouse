<div align="center">

# Lighthouse · 灯塔

**Filesystem MCP gives an AI access. Lighthouse governs that access.**
**Before you open the door for an AI, put locks, logs and an approval step on it.**

Four gates · egress redaction · full audit log · write switch (read-only by default) · elevation is yours to grant, per request or by a standing policy you set · scope is asked, never assumed

<small>[中文版 README](README.md)</small> · <small>[How it differs from filesystem MCP](#how-it-differs-from-filesystem-mcp)</small>

<small>Authors · 诗人 & CC</small>

</div>

---

## The problem

You tell the ChatGPT web app (or any AI) “look at this local project”. Today's standard recipe:
run a filesystem MCP, put it behind a Cloudflare tunnel or Tailscale, hand over the URL. It works —
and leaves five questions unanswered:

| | Question | A typical filesystem MCP |
|---|---|---|
| ① | What can it **read**? | Everything under the mount point — `.env`, private keys, backups you forgot about |
| ② | What did it read, and when? | Usually nothing is recorded |
| ③ | Can a secret it reads **end up in an answer** sent to the model vendor? | No egress check |
| ④ | Can it **write**? | Many setups grant read-write by default |
| ⑤ | Who approves it **wanting more**? | The concept does not exist |

A filesystem MCP answers “**can it access**”. These five are “**how is that access governed**”.

## Lighthouse's answer: the agent never touches files directly

It goes through a channel with gates:

```
                       web AI / Agent
                             │
                             │ ① I want to read X (same road when it wants more)
                             ▼
                   ┌───────────────────┐
                   │ Request access    │  request_access — it can only ask, never self-grant
                   ├───────────────────┤
                   │ Policy            │  include / exclude / deny-list / standing bound
                   ├───────────────────┤
                   │ Owner approval    │  per request by default; a standing policy if you set one
                   ├───────────────────┤
                   │ Redaction         │  egress check: sk-… / ghp_… / key blocks → «REDACTED»
                   ├───────────────────┤
                   │ Audit             │  one line per call, refusals included
                   └───────────────────┘
                             │
                             ▼
                          your files
```

**The real difference is the trust model.** A filesystem MCP assumes the *client is trusted* — it is
a process you started on your own machine. Lighthouse assumes the *client is untrusted*: it is
OpenAI's server, reaching your disk over the public internet. An untrusted client means every layer
must be able to say “no” on its own, and every call must leave a trace.

> Stated the other way round: **if you only need a local agent on your own machine (Claude Desktop,
> Cursor, …) to read files, a filesystem MCP plus a tunnel is enough — you do not need Lighthouse.**
> It exists for the other situation: the machine on the far side is not yours, and you still have to
> open your disk to it.

## See it refuse in 30 seconds

The interesting demo is not “it can read a file” — it is what happens when it reaches for something
it must not have:

```
$ tail -5 ~/.lighthouse/audit/miji.jsonl          # real output, timestamps/some fields trimmed
{"tool":"read_file","args":{"path":"tools/kb.py"},"ok":true,"bytes":22332,"redactions":0}
{"tool":"read_file","args":{"path":"docs/.env"},"ok":false,"reason":"deny-list: secrets/credentials/database"}
{"tool":"read_file","args":{"path":"../../etc/hosts"},"ok":false,"reason":"path escape (outside the window)"}
{"tool":"read_file","args":{"path":".git/logs/HEAD"},"ok":false,"reason":"deny-list"}
{"tool":"request_access","args":{"include":["**"]},"ok":true,"pending":true,"note":"no elevation window armed"}
```

What it can read is **what you circled**; `.env` and `.git` never get in (even after asking for
“the whole repository”); widening the scope can only produce a **pending request** waiting for you.
That log is the whole truth — there is no second set of books.

## How it differs from filesystem MCP

| | filesystem MCP (+ tunnel) | Lighthouse |
|---|---|---|
| Problem it solves | Letting an AI **access** your files | Making that access **governed** |
| Grant granularity | A mount point | Per-window include / exclude / bound |
| Secrets | On you to keep them out of the directory | **Deny-list by default**: `.env*`, `*.pem`, `id_rsa*`, `*secret*`, `*token*`, all of `.git/` — refused unconditionally, no include can override it |
| Egress content | Returned as-is | Egress redaction: `sk-… / ghp_… / AKIA… / key blocks` → `«REDACTED»`, search snippets included |
| Writing | Often open by default | **Two locks**, read-only by default; open it by hand (optionally auto-closing in 30 min), backup before and sha256 after |
| Elevation | No such concept — you edit config and restart | The agent can only `request_access`; by default that is a pending entry you `approve`. Also `elevate` (time-boxed) or `auto-grant` (standing policy, optionally bounded) |
| Audit | Usually none | One line per call, refusals included: `~/.lighthouse/audit/<window>.jsonl` |
| Trust model | Client is trusted (local process) | **Client is untrusted** (public internet, third-party server) |

Both can show an AI your local files. The difference shows up when something goes wrong — and in
which side can say exactly what happened.

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
#    ✅ smoke 13/13   ✅ read-only audit 46/46   ✅ write switch 25/25   ✅ elevation 51/51   ✅ hardening 47/47

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
bash lighthouse.sh approve <id> [--for 2h]   # approve its elevation request (--for picks how long: 30m/2h/1d/7d/forever)
bash lighthouse.sh elevate <id> --for 30m --scope "src/**"   # pre-arm: auto-approve such requests while it lasts
bash lighthouse.sh auto-grant <id> on [--ceiling "src/**,docs/**"]   # standing policy: requests inside the bound take effect at once
bash lighthouse.sh auto-grant <id> off   # back to ask-first (applies to the very next request, no restart)
bash lighthouse.sh chat-approval <id> on [--ceiling "src/**"]   # in-chat approval: you say "I authorize it" and it takes effect (off by default)
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
  what was requested), or pre-arm a window (`elevate <id> --for 30m --scope "src/**"` — expires on its own);
- **Revoke anytime**: `deny <id>` clears everything (extra scope + pending request + elevation window);
  `auto-grant <id> off` drops the standing policy.
- **In-chat approval (opt-in)**: with `chat-approval <id> on`, the agent asks, you answer "yes, authorize it" in the
  conversation, and it re-requests with `user_confirmed=true` to take effect — no terminal needed. Trust-based
  (the server cannot verify what you actually said), off by default, meant for local/trusted agents only.

That is how “the choice stays with the owner” is implemented: **as open or as tight as you decide — the gates stay in your hand.**

## Layout

```
lighthouse/
├── lighthouse.sh        # single entry point (new/start/stop/status/url/publish/write/elevate/auto-grant/approve/deny/scope/test/doctor)
├── config.json          # global config: hostname / tunnel / state dir  (*.local.json overrides, gitignored)
├── windows.json         # window registry: the visible scope of every window lives here
├── core/                # server.py(window MCP server) · config.py · scope.py(authorization) · add_window.py
│                        #   render_services.py · render_ingress.py · switch.py(write switch CLI)
├── tests/               # five suites (smoke 13 / audit 46 / write 25 / elevation 51 / hardening 47) + run_all_tests.sh
├── demo/project/        # sample project (with a pass phrase, proving reads are real)
└── docs/                # ARCHITECTURE · SECURITY · CHATGPT · OPEN_A_WINDOW · ROADMAP · ISSUES
```

## How the four gates are wired

**Scope lives in one place.** One entry in `windows.json` = one window:

```json
"myproj": {
  "root": "~/code/myproj",                         // ① root boundary: only this tree; anything resolving outside is refused (symlink escapes too)
  "include": ["README.md", "docs/**", "src/**"],   // ③ include: only these globs are served
  "exclude": ["private/**"],                       // ② exclude: pruned, subtree included (case variants too)
  "path": "/w-myproj-6m1yo0",                      // random path segment (unguessable — but NOT authentication; see docs/SECURITY.md)
  "visibility": "local",                           // local = private; never written into the public ingress
  "write": { "enabled": true }                     // write master switch (off by default; the runtime switch must be on too)
}
```

④ **The deny-list always comes first**: `.env*`, `*.pem`, `id_rsa*`, `*secret*`, `*token*`, `*.db`, all of
`.git/` — refused unconditionally, no include pattern can override it, case variants (`.ENV`) included.
Egress goes through **redaction** as well (`sk-… / ghp_… / key blocks` → `«REDACTED»`, search snippets included).

Changing the scope means editing this one file — no code changes. **Fail-closed**: path resolution errors,
unmatched patterns, anything ambiguous — refused and logged. **One hostname, many windows**: path routing
forwards to each window's port, so adding a window never touches DNS and a single `publish` rebuilds the table.

## Dependencies & thanks

| Dependency | Purpose | Link |
|---|---|---|
| Python 3.10+ | runtime | https://www.python.org/ |
| MCP Python SDK | MCP server over streamable-http | https://github.com/modelcontextprotocol/python-sdk |
| cloudflared | publish a local port safely (outbound-only tunnel, no open ports) | https://github.com/cloudflare/cloudflared |
| macOS launchd / Linux systemd | keep window services alive | built-in |

**With thanks to**: the MCP team for turning “how AIs reach the outside world” into an open protocol, and
Cloudflare for making outbound-only tunnels the default safe option — without those two, Lighthouse would be
nothing but a pile of local scripts.

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

- **v1.2** — standing elevation policy `auto-grant` (requests inside the bound take effect immediately, anything
  beyond it stays pending; the policy is read live, so `off` tightens at once); gate hardening after an attack probe
  found real bypasses (case-insensitive deny matching, the whole `.git/` directory, more key-name variants);
  5th test suite (hardening). Also: CLI and server now resolve the same registry file, and
  `restart` no longer fails when `$0` is a relative path. Grant durations are user-selectable
  (`--for 30m|2h|1d|7d|forever`; `auto-grant --ttl` makes each auto-grant expire). New: in-chat approval
  (`chat-approval` — the user says "I authorize it" in the conversation; opt-in, trust-based, local agents only) — 182 checks total.
- **v1.1** — in-chat elevation (request-only + `approve` / `elevate` / `deny` / `scope`) · scope is asked at
  window-creation time (never silently defaults to `**/*`) · 4th test suite (102 checks total).
- **v1.0** — first public release: four gates, redaction, audit, write switch (two locks), three test suites,
  multi-window path routing, launchd/systemd service generation.

## Authors

Designed and built by **诗人 (Poet)** ([@Fission21](https://github.com/Fission21)) and **CC**.

## License

MIT © Poet & CC (诗人 & CC)
