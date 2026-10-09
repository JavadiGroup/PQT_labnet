# Automation

The **Automation** tab holds projects an agent works in. People collect the
papers, data, setup and tasks; the agent's scripts, logs, data and analysis
are kept beside them, so every step can be checked. Tasks are queued as
[runs](#runs); the [Automation runner](runner.md#the-automation-runner) works through
them in Docker.

**A project is a folder** with fixed sections. Each section says whose it is:

| Folder | Whose | Holds |
| --- | --- | --- |
| `Literature/` | yours | Papers only this project needs. The shared **lab library** is shown here too (read-only) |
| `Data/example_data/` | yours | Example data: CSV, TSV, JSON, text and images |
| `Data/new_data/` | agent | Data the agent produces, with `DATA_SUMMARY.md` |
| `Experiment_layout/` | automatic | A snapshot of every linked experiment's canvas, and `DEVICES.md`: each instrument, how to call it, and whether automation may control it |
| `Experiment_layout/LAYOUT.md` | yours | How the setups connect, what may be automated, safety notes |
| `Tasks/` | yours | One Markdown file per task: goal, inputs, constraints, deliverables |
| `Protocols/` | agent | The method and bounds the agent proposes for approval |
| `ScriptsUtilized/` | agent | Every script the agent wrote or ran (`src/`, `analysis_scripts/`, `logs/`), for auditing |
| `Analysis/` | agent | `Analysis.md` and the key figures in `outputs/` |

**On the page:**
- **Left:** the file tree. Your folders take uploads (button or drag and drop) and new folders.
- **Middle:** the open file.
  - Markdown renders as a document. Your own files (tasks, `LAYOUT.md`) have View and Edit.
  - CSV shows as a table; images and PDFs show as themselves; code shows in a code block.
- **Right:** the project's panel.
  - Its tasks, with *Run* and *Run all*, and its [runs](#runs).
  - **Linked experiments:** *Link experiment…*, *Unlink*, and *Update copies* when a newer version is saved on the Experiments tab.
  - **Allowed devices:** only devices on a linked canvas, and only ticked ones, can ever be automated.
  - **Library scope:** topic folders or reading lists; nothing ticked means the whole library.

**Saving.**
- Replacing a file needs the version it was opened with (`If-Match`). If someone else saved in between, the page asks before overwriting.
- Uploads are limited to `LABNET_AUTOMATION_MAX_FILE_MB` per file. The limit is counted as the file streams in.
- Deleting moves a file to the project's trash for 30 days. Deleting a project moves the whole folder to `.trash/`.

**The lab library** is `LABNET_AGENT_LITERATURE_DIR`, the folder Photo-Agent already reads. Lay it out like this:
- **Topic folders** of PDFs.
- **Reading lists** in `collections/<name>.txt`: one library path per line, so a paper can be on several lists without copies.
- **Folders starting with `_`** (for example `_duplicates/`) are not shown.

**Where it lives.**
- `LABNET_AUTOMATION_DIR` holds two trees:
  - `projects/<id>/`: the folders above;
  - `state/<id>/`: the Control Center's own (`project.json`, experiment snapshots, trash, `runs/`);
  - `state/runners/`: one file per runner, holding a hash of its token;
  - `state/library/literature.sqlite` and `state/<id>/literature.sqlite`: the [search indexes](#literature-search).
- Whatever decides what an agent may do (allowed devices, approvals) lives in `state/`, never inside the folder an agent writes.
- In development this is `automation/` at the repository root (git-ignored). In the lab it's `C:\ProgramData\LabNet\automation`, which is worth backing up.

**Paths are checked, never trusted.** Every path is relative to its project or the library. Refused:
- `..`, drive or UNC forms, and `:`;
- hidden (`.`-prefixed) names, reserved device names, and control or invisible characters;
- links and junctions, which are never followed; an opened file is also checked by its handle.

Files are served inert:
- PDFs and images are shown inline, and text is served as plain text.
- An SVG is sandboxed.
- HTML and unknown types are download-only.
- Pages carry a Content-Security-Policy.
- Changes must be JSON (or a raw upload) from the Control Center's own pages.

## Runs

A **run** is one task of one project (`Tasks/Task1.md`), worked through by a
[runner](runner.md#the-automation-runner). Until a runner is connected, queued runs
simply wait in the queue.

**On the page:**
- **Tasks panel:** each task has *Run*, which is disabled (with a tooltip
  saying why) while that task has a queued or active run. *Run all* queues
  every task without one, in name order (`Task2` before `Task10`), with the
  project's run settings.
- **Run with…** (the *…* beside *Run*) queues the task with its own model,
  effort, max turns and budget, for this run only. The dialog starts from the
  project's settings; *Default* there means the Control Center's own default.
  The project's settings don't change.
- **Runs list**, newest first: the status, the task, who queued it, the
  runner, when it started and finished, and how much it uploaded of its
  quota. A queued run says *Waiting for a runner*. Queued and active runs
  have *Stop*, which asks first.
- **Clicking a run** opens its log in the middle: the run's events (kind,
  text and the Control Center's time) and its summary, shown as plain text.
- **Refreshing:** an open log refreshes every 2 s while its run is queued or
  active, and the list every 5 s while any run is. Both pause while the page
  is hidden.

**Run settings** are part of the project (*Settings…*):

| Setting | Default | |
| --- | --- | --- |
| `run_model` | the Control Center's `LABNET_AGENT_MODEL` | One of `LABNET_AGENT_MODELS`; anything else is refused (422) |
| `run_effort` | the Control Center's `LABNET_AGENT_EFFORT` | `low` … `max`. Haiku has no effort levels, so its runs get none |
| `run_max_turns` | `60` | 1 to 200 |
| `run_budget_usd` | `5.0` | More than 0, at most 100 |

- A run copies the settings when it is queued, as `settings: {model, effort,
  max_turns, max_budget_usd}`. Changing the project afterwards changes only
  later runs.
- A save that leaves these fields out keeps them, so an older open page
  can't reset them. Projects saved before these settings existed get the
  defaults.
- **A run's own settings** (*Run with…*): `POST …/runs` may carry
  `"settings": {"model", "effort", "max_turns", "max_budget_usd"}`. Each one
  given replaces the project's for this run only; one left out or `null`
  keeps the project's. They are checked exactly like the project's (a model
  `LABNET_AGENT_MODELS` doesn't offer, an unknown effort, a range or an
  unknown key answers 422 naming `settings.<field>`), and Haiku still gets no
  effort. The run's `settings` record what was used.

Through the API:

| Request | Does |
| --- | --- |
| `POST /api/automation/projects/<id>/runs` `{"task": "Tasks/Task1.md", "settings"?}` | Queues the task (201). The task must be a Markdown file in `Tasks/`; one that is already queued or active is refused (409). `settings`: this run's own model, effort, max turns or budget |
| `GET …/projects/<id>/runs` | The project's runs, newest first |
| `GET …/projects/<id>/runs/<run_id>` | The run and its last 500 events |
| `POST …/projects/<id>/runs/<run_id>/cancel` `{}` | Cancels a queued or active run; the runner notices on its next request |

- A run is `queued`, then `active` once a runner claims it, and ends `done`,
  `failed`, `stopped` (the runner says which) or `cancelled` (a person did).
- With [logins](../control-center/logins.md#logins), `queued_by` and `cancelled_by` are the signed-in
  person; without them, the optional name sent in the body.
- A runner claims the oldest queued run of any project.
- A project can't be deleted while one of its runs is queued or active.
- Events and the summary are written by the runner. Show them as plain
  text, never as HTML or Markdown.
- **Heartbeat and timeout.** Every runner request about an active run counts
  as a sign of life (`last_seen_at`), and so does an upload still streaming
  in. A runner with nothing else to say sends `POST runs/<run_id>/heartbeat`.
  If an active run hears nothing for `LABNET_AUTOMATION_RUN_STALE_MINUTES`
  (10), it is marked `failed` with the summary *The runner stopped reporting
  (nothing for 10 minutes).* and a `timeout` event. Its runner then gets 409.
  - The check runs whenever runs are read, and once a minute in the
    background.
  - After a Control Center restart, the clock starts again, so runners get a
    full window to reconnect.
  - Records from before heartbeats existed count from `claimed_at`.
- Records live in `state/<id>/runs/`: `<run_id>.json` and
  `<run_id>.events.jsonl`.

## Literature search

Papers are searchable by keyword, page by page, with citations like
`lasers_and_stabilization/…/An introduction to Pound–Drever–Hall laser frequency stabilization.pdf p.6`.
The project page's search box and the agent's `search_literature`,
`list_papers` and `read_paper_pages` tools use the same search.

**What is indexed:**
- **The lab library**, by the same rules as its tree: no `_`-folders, no
  `collections/`, no hidden names, links, junctions or hard links. PDFs page
  by page; `.txt` and `.md` files as one page.
- **Each project's own `Literature/`** folder, the same way.
- A PDF with (almost) no text layer, such as a scan, is flagged *no text
  layer* and can't be found by its words. An encrypted or broken one is
  flagged with a short reason. Neither stops the rest.

**Keeping it current:**
- The library is indexed when someone presses **Index library** on the
  Automation tab's *Lab library* card. The card shows how many papers are
  indexed, how many have no text layer and how many couldn't be read, with
  live progress while it runs. Only one indexing runs at a time; pressing
  again meanwhile gets 409.
- Indexing is incremental. A paper with the same size and modification time
  is skipped, one with the same bytes (sha256) only has its record updated,
  and a paper that is gone is dropped. *Index library* also tries papers
  that failed last time again.
  The lab's 62 papers take about two minutes the first time and under a
  second after that.
- A project's own papers are indexed just before it is searched. If new ones
  would take more than about 30 s, the search covers what is indexed and
  says so.

**What a search covers:** the project's [library scope](#automation) (its
topic folders and reading lists, or the whole library when it names neither,
exactly as the runner's library endpoint decides), plus the project's own
papers. `paper=<path>` narrows it to one of them.

**How a query is read:** words and `"quoted phrases"`. A page must have them
all; when no page does, any of them will do (the reply's `mode` says `all`
or `any`). Pages are ranked by BM25, and English endings are matched
(*stabilizing* finds *stabilization*). Everything typed is searched as
words: `AND`, `OR`, `NOT`, `NEAR`, `*`, `:` and parentheses mean nothing
special, and a query can't cause an error.

| Request | Does |
| --- | --- |
| `GET /api/automation/library/index` | The library index: `state` (`idle`, `running` or `failed`), `done` of `total`, the `current` paper, `papers`, `no_text`, `errors`, `message`, and `extractor` (false without pypdf) |
| `POST /api/automation/library/index` `{}` | Starts indexing (202); 409 while it runs; 503 without pypdf |
| `GET …/projects/<id>/literature/search?q=&k=&paper=` | Up to `k` pages (1 to 50, default 10) for `q` (at most 500 characters): `path`, `source` (`library` or `project`), `page`, `score`, `snippet` and `cite` |
| `GET …/projects/<id>/literature/papers` | Every paper the project's search covers, with its pages and status |

Runners have the same three, plus `pages`, under `runs/<run_id>/literature/`
(see [the runner API](runner.md#the-runner-api)). They work only while the run is
active.

- In a `snippet`, each hit is between the characters U+0002 and U+0003. The
  project page turns them into highlights without ever reading paper text as
  HTML.
- `pages` returns at most 20 pages per request, with `more` when the paper
  goes on.

**PDFs are read in a sandbox.** Papers come from the internet, and a PDF
parser is a large attack surface, so pypdf never runs inside the Control
Center:
- Each PDF is copied, hashed, and read by a separate process,
  `python -m labnet_control_center.literature_extract`.
- That process gets the same allow-list environment as experiment scripts
  (no `LABNET_*` settings, no credentials) and no console window.
- It has 60 s per PDF; a slower one keeps the pages read so far and is
  flagged.
- At most 3000 pages are read, 200 000 characters per page and 64 MB per
  paper.
- pypdf comes with the `control-center` extra (`pypdf>=5,<7`). Without it,
  the card says so and indexing is refused.
