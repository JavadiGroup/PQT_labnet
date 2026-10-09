# Photo-Agent

The chat sidebar on every page is answered by **Claude Code in headless
mode**. For each message, the Control Center runs `claude -p` and streams the
answer into the chat.
- **Conversations:** the Control Center picks each conversation's id
  (`--session-id`), and follow-ups continue it (`--resume`). The pencil icon
  starts a new chat.
  - If Claude Code no longer has a conversation, the answer starts a new one
    under the same id and says so.
- **Stopping:** the Stop button (the square that replaces Send), or closing
  the page, ends the `claude` process.

**The toolbar under the message box:**
- **Paperclip:** attach files. You can also paste a screenshot or drop files
  onto the box.
  - **Types:** images (PNG, JPG, GIF, WebP), which the model sees; PDFs; and
    text files such as CSV, TSV, TXT, JSON, Python or notebooks.
  - **Limits:** up to 5 files and 20 MB each per message.
  - **Not supported:** Excel and binary formats (HDF5, `.npy`). Export them
    to CSV first.
- **Model:** `--model`, from `LABNET_AGENT_MODELS`. The menu shows the
  version (for example "Sonnet 5.5") once Claude Code has used that model.
- **Effort:** `--effort` (Low, Medium, High, Extra high, Max), meaning how
  much the model thinks before answering. Haiku has no effort levels, so the
  menu is greyed out for it.

Each viewer's model and effort are remembered in their browser.

**Attached files** are saved in `uploads/<conversation id>/` in the
workspace.
- Only that conversation's answers can read them: each run gets `--add-dir`
  for its own folder only.
- They are deleted 14 days after the conversation was last used.

**Account.** It uses the Claude Code login of the Windows account that runs
the Control Center. No API key is used. Any `ANTHROPIC_API_KEY`,
`ANTHROPIC_AUTH_TOKEN` or `CLAUDE_CODE_OAUTH_TOKEN` is removed from its
environment.
- Set up once, on that computer and as that account: install Claude Code,
  run `claude`, and use `/login`.
- The sidebar badge shows **Ready**, **Sign-in needed**, or **Not
  connected** (Claude Code not found). Hover over it for the reason.
- Anthropic's terms limit sharing one person's subscription with other
  people through an app. Before the whole lab uses it, confirm that your
  plan covers this (for example a Team plan).

**What it can do.** It can read, but not act:
- **Tools:** only Read, Glob and Grep. `--permission-mode dontAsk` denies
  everything else instead of asking. It has no shell, no file edits, no
  device control, none of the account's own MCP connectors, and no memory
  between chats (`CLAUDE_CODE_DISABLE_AUTO_MEMORY`).
- **Folders:**
  - its working folder `home/` in the workspace (`LABNET_AGENT_WORKSPACE`);
  - `LABNET_AGENT_SOURCES`, the LabNet code, docs and examples;
  - `LABNET_AGENT_LITERATURE_DIR`, papers, which it can read as PDFs;
  - the conversation's own uploads.
- **Blocked even inside those folders:** `*.env` files, `*.key` files,
  databases and `logs/`. The Control Center's own Central key is never in its
  environment.
- **Live network:** before every message the Control Center writes
  `home/live/network.md` in the workspace: Lab PCs, devices, states, who
  holds them, and methods. Questions about what is online are answered from
  it.
- **An open Automation project:** the chat shows "Photo-Agent sees:
  <project>". With each message it gets:
  - a listing of the project's files;
  - a copy of its text files (up to 4 MB) in that conversation's uploads.

  It gets copies, not the folder itself, because an added folder would also
  load any Claude Code settings someone put in it.
- **Briefing:** its role and the lab's script style (`from_env()`,
  try/except/finally, release in reverse order) are in
  [`agent_briefing.md`](https://github.com/mill0851/labnet/blob/main/packages/labnet-control-center/src/labnet_control_center/agent_briefing.md).

It writes scripts for people to run. It never runs them.

In development, `labnet dev seed` points it at this checkout's `packages/`
and `examples/`, and at `literature/` at the repository root (git-ignored;
drop papers there). Its workspace is `.devnet/agent`.
