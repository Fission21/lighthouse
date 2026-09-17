<div align="center">

# Lighthouse

**Open a window onto your local project — for web AIs to see, and only see what you circled.**

Four gates · output redaction · full audit log · write switch (read-only by default)

</div>

---

## What it is

Web AIs (ChatGPT connectors, any MCP client) are powerful, but they can't see **your machine**.
Lighthouse opens a **controlled window** onto one local directory, exposing it over MCP — while
you declare up front what may and may not be seen, secrets are redacted on the way out, and every
call leaves a trace.

```
your local project
   └─ window MCP server (4 gates · redaction · audit · write switch)   ← auto-start on boot
        └─ cloudflared tunnel (one hostname, path-routed to each window)
             └─ https://your-domain/<random path per window>
                  └─ web AI (ChatGPT connector / any MCP client)
```

## What it blocks

| Concern | Answer |
|---|---|
| Will it browse other directories? | Gate ① realpath must stay under `root` |
| Will it read my `.env` / private keys? | Gate ④ deny-list always wins: `.env* / *.pem / id_rsa* / credentials* / *.db` … |
| Will it see what I explicitly excluded? | Gate ② `exclude` globs (directory match prunes the subtree) |
| Will it leak a key into an answer? | Output redaction: `sk-… / ghp_… / AIza… / AKIA… / Bearer … / private key blocks` → `«REDACTED»` |
| Will it edit my code? | **Read-only by default.** You open the write switch by hand (optionally auto-closing in N minutes); every change is backed up and hashed |
| How do I know what it read? | Audit log, one JSON line per call (refusals included): `~/.lighthouse/audit/<window>.jsonl` |
| How do I trust any of this? | Three self-contained test suites (13 + 45 + 25 checks) — one command |

## Quick start (4 steps)

```bash
pip install mcp

bash lighthouse.sh test          # 1) verify the whole chain locally (no internet needed)
                                 #    ✅ smoke 13/13  ✅ read-only audit 45/45  ✅ write switch 25/25

bash lighthouse.sh new myproj ~/code/myproj \    # 2) open a window onto your own project
     --title "My project" \
     --include "README.md,docs/**,src/**" \
     --exclude "private/**,**/*.log"
bash lighthouse.sh start
bash lighthouse.sh url myproj

cloudflared tunnel login                          # 3) expose it to the web
cloudflared tunnel create lighthouse              #    note the UUID
cloudflared tunnel route dns <UUID> mcp.example.com   #    UUID — not the tunnel name
# put UUID + hostname into config.json, then:
bash lighthouse.sh publish

# 4) In your web AI: Settings → Connectors → Create app → paste https://your-domain/<window path>
```

## Commands

```bash
bash lighthouse.sh list | status | url <id> | doctor
bash lighthouse.sh write <id> on 30 | off | status     # write switch
bash lighthouse.sh test [id]                            # all three suites
bash lighthouse.sh publish                              # tunnel config + restart + health check
```

## Layout

```
lighthouse.sh          single entry point        core/server.py     the window MCP server
config.json            hostname / tunnel / state core/*.py          registry, services, ingress, switch
windows.json           per-window scope           tests/*            smoke / audit / write suites
demo/project/          sample project with a pass phrase (proves real reads)
docs/                  ARCHITECTURE, SECURITY, CHATGPT
```

## Design notes

- **Scope lives in one file.** A window is one entry in `windows.json`: root, include, exclude,
  port, random path, visibility, and whether writing is allowed at all.
- **Fail-closed.** Anything ambiguous — bad path, resolution error, no match — is refused and logged.
- **One hostname, many windows.** Path routing keeps DNS untouched when you add a window;
  `publish` rebuilds the routing table.

## Dependencies & thanks

| Dependency | Purpose | Link |
|---|---|---|
| Python 3.10+ | runtime | https://www.python.org/ |
| MCP Python SDK | MCP server over streamable-http | https://github.com/modelcontextprotocol/python-sdk |
| cloudflared | outbound-only tunnel (no open ports) | https://github.com/cloudflare/cloudflared |
| launchd / systemd | keep the window services alive | built-in |

Thanks to the Model Context Protocol team for making "how AIs reach the outside world" an open
protocol, and to Cloudflare for making outbound-only tunnels the default safe option.

## Gotchas (all hit in practice)

| Gotcha | Symptom | Fix |
|---|---|---|
| MCP DNS-rebinding protection | 200 locally, 404 on the public hostname | disable it server-side (`TransportSecuritySettings`, built in) |
| `cloudflared route dns` with the tunnel *name* | DNS points at the wrong tunnel | always use the tunnel **UUID** |
| Sharing one tunnel | requests load-balanced to another machine → 404 | dedicated tunnel per service |
| Registry path | server looked for `windows.json` inside `core/` | resolved relative to the repo root |
| ChatGPT stream stalls on first token | generation actually finished | reload the page |

## License

MIT © Lighthouse contributors
