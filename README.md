<div align="center">

<h1>Refero</h1>

<img src="https://github.com/chu-/Refero/blob/main/assets/logo.png" width="160" alt="Refero logo">

A scholarly workflow for Zotero, from your terminal or a local agent.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue?style=flat)](LICENSE)
[![macOS Supported](https://img.shields.io/badge/macOS-supported?logo=apple&logoColor=white)](https://www.apple.com/macos)
[![Linux Supported](https://img.shields.io/badge/Linux-supported?logo=linux&logoColor=white)](https://kernel.org/)
[![Windows Supported](https://img.shields.io/badge/Windows-supported?logo=windows&logoColor=white)](https://www.microsoft.com/windows)
[![Zotero Desktop for local mode](https://img.shields.io/badge/local%20mode-Zotero%20Desktop%20%E2%89%A57-blue)](https://github.com/zotero/zotero)
[![fzf for pickers](https://img.shields.io/badge/pickers-fzf%20%E2%89%A50.55-blue)](https://github.com/junegunn/fzf)
[![pyzotero required >=1.5](https://img.shields.io/badge/pyzotero%20required->=%201.5-blue)](https://github.com/urschrei/pyzotero)

[Getting Started](#getting-started) •
[Agent Examples](#agent-examples) •
[Features](#features) •
[Usage](#usage) •
[Local Agents](#local-agents) •
[Picker Styles](#picker-styles) •
[Keybindings](#keybindings) •
[Contributing](#contributing) •
[License](#license)

</div>

### Overview

Refero provides an interactive terminal interface and non-interactive data commands
for [Zotero](https://www.zotero.org/). A portable skill teaches Codex, Claude Code,
and other local agents how to use the same CLI.

| Use Refero for | What to install | Start here |
| --- | --- | --- |
| Terminal search, editing, and export | CLI; `fzf` for pickers | [Getting Started](#getting-started), then [Usage](#usage) |
| Local agent access | CLI and the skill for your chosen agent | [Getting Started](#getting-started), then [Local Agents](#local-agents) |

Local mode reads a running Zotero desktop library. Web mode uses a Zotero library
ID and API key without requiring the desktop application. Legacy mutation commands
can fall back from local mode to the configured Web library. Reviewed `write`
plans use the Web API and require explicit `--web` selection in local mode.

### System Support

- `MacOS` – Supported
- `Linux` – Supported
- `Windows` – Supported

### Features

- **Interactive fuzzy picker (fzf)**: fast search with presets (`compact`, `aligned`, `colored`, `preview`, `title-preview`), configurable preview window, and rich keybindings.
- **Local + Web backends**: browse offline via Zotero Local API(pyzotero); switch to Zotero Web API for full create/edit/delete.
- **Search & filters**: query by title/author/year/tags/collections; supports grouping by collection path.
- **Agent data access**: JSON search results and lossless reads of item fields, notes, relations, labels, and attachment metadata, with scope and completeness evidence.
- **Reviewed writes**: prepare exact before/after changes, obtain user approval, and apply with version checks and read-back verification.
- **Portable agent skill**: install in a shared Agent Skills directory or for Claude Code, with self-contained setup and command references.
- **Smart open**: open best attachment (PDF → HTML → URL/DOI), or a specific type; open directly in Zotero.
- **Export**: generate bibliographies (e.g., BibTeX), including to files named by citekey.
- **Add by identifier**: create items from DOI/arXiv; apply tags and collections; dry‑run to preview Zotero JSON.
- **Edit metadata (web)**: update fields and content fields (Note, Abstract, Extra); Markdown note editor supported.
- **Label editor**: manage Tags and Collections together or individually; interactive picker-based flows.
- **Attachment management**: attach/remove PDFs; bulk remove all PDF attachments from an item.
- **Deletion controls**: confirmation prompts and explicit unattended acknowledgement; see [deletion behavior](#deletion-behavior).
- **Configurable**: tune `fzf` header/match formats, preview pane, and extra flags to fit your workflow.

---

## Getting Started

These steps are shared by users and agents performing installation. Install the
program, configure the intended library, verify read access, and then install the
skill if you want agent integration. Installing a skill alone does not install
the program or connect Zotero.

### Requirements

| Dependency | Needed for |
| --- | --- |
| Python >=3.9; Python 3.11 used below | CLI and installer; project development uses 3.11 |
| Git | Obtaining a source checkout; unnecessary if you already have one |
| Running Zotero Desktop >=7 with Local API enabled | Local-mode reads only |
| Zotero library ID/type and API key | Web access and reviewed writes |
| `fzf` >=0.55 | Interactive pickers only |
| System viewer/editor and clipboard utilities | Opening files, interactive editing, and clipboard bindings |

Search, JSON reads, and export do not require `fzf`, a clipboard utility, or an
editor. Python dependencies are installed by pip.

### 1. Install the CLI

Use the checkout containing the features described here. If you are already in
that checkout, skip cloning. If a particular release or commit was requested,
check it out before installation; `main` may differ from an unpublished local
checkout. Published packages can also differ from this README, so verify actual
commands rather than relying on a version number alone.

```bash
git clone https://github.com/chu-/Refero.git
cd Refero
```

If `.venv` already exists and is the selected environment, skip its creation
command. Otherwise create it as shown below.

On macOS/Linux:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/ref --version
.venv/bin/ref doctor --help
.venv/bin/ref read --help
.venv/bin/ref write --help
```

On Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\ref.exe --version
.\.venv\Scripts\ref.exe doctor --help
.\.venv\Scripts\ref.exe read --help
.\.venv\Scripts\ref.exe write --help
```

Reuse a suitable existing environment instead of overwriting it. For development,
replace `pip install .` with `pip install -e .`. Keep the checkout or matching
source archive: the skill installer is a source file, not a command installed by
the Python wheel.

The remaining examples use `ref` as shorthand for the executable just installed.
On macOS/Linux you can activate it with `source .venv/bin/activate`; on PowerShell
you can continue using `.\.venv\Scripts\ref.exe` directly. An agent's next shell
may not inherit activation, so give it the **absolute executable path**.

Already using a selected Python environment? These alternative CLI installs are
also available; inspect their help before using this README's agent commands:

```bash
python -m pip install refero
python -m pip install git+https://github.com/chu-/Refero.git@main
```

Choose one installation source. Installing the CLI this way does not copy a skill
into an agent's discovery directory.

### 2. Configure the library

Use `~/.config/ref/config.yaml` on all platforms, or select another file with
`REF_CONFIG`. Preserve existing settings and credentials; merge only the fields
needed for the intended library. `doctor` does not create or rewrite this file.

For local reading, start Zotero, enable its Local API, and create this minimal
configuration if you do not already have one:

```yaml
zotero:
  mode: local
  local_api: http://localhost:23119/api
  library_type: user
```

For Web access, replace the sample ID and key with your library's actual values:

```yaml
zotero:
  mode: web
  library_id: 123456          # replace with your numeric user or group ID
  library_type: user         # user | group
  api_key: YOUR_ZOTERO_API_KEY
```

Obtain credentials from [Zotero's API key settings](https://www.zotero.org/settings/keys).
Use an appropriately scoped key and keep it in local configuration. On POSIX,
restrict the file to its owner with `chmod 600 ~/.config/ref/config.yaml`.

To retain local reads while using reviewed Web writes, keep `mode: local` and add
the corresponding `library_id`, `library_type`, and `api_key`. Select `--web` when
preparing **and** applying a write plan. Both phases read the Web library's item
versions; they do not silently switch from local records.

<details>
<summary>Optional picker and editor configuration</summary>

Merge any desired settings from this example into your existing file. None of the
picker customization below is needed for unattended data access.

```yaml
fzf-binary: fzf
fzf-extra-flags: ["--ansi", "--multi", "-i", "--delimiter= :: "]
fzf-extra-bindings: [
  "ctrl-s:jump",
  "ctrl-r:execute(open zotero://select/library/items/{5})",
  "ctrl-o:execute(ref open {5})",
  "ctrl-e:execute(ref edit {5})",
  "ctrl-t:execute(ref label {+5} --pick)",
  "ctrl-l:execute(ref relate {5} --pick)",
  #"ctrl-t:execute(ref tag {+5} --pick)",
  #"ctrl-t:execute(ref collection {+5})",
  "ctrl-k:execute(ref note {5})",
  #"ctrl-k:execute(ref abstract {5})",
  #"ctrl-k:execute(ref extra {5})",
  "ctrl-a:execute(ref export {5} --format bibtex --out {6}.bib)",
  "ctrl-d:execute(ref delete {+5})+abort",
  # "ctrl-d:execute(ref delete -y {+5})",   # skip confirmation
  # macOS defaults to pbcopy; Linux uses xclip -selection clipboard automatically
  "alt-y:execute-silent(echo -n {5} | pbcopy)+abort"
]
fzf-header-format: "{doc[title]:<70.70} :: {doc[author]} :: «{doc[year]}» :: :{doc[tags]} :: {doc[key]} :: {doc[citekey]}"
match-format: "{doc[title]} :: {doc[author]} :: {doc[year]} :: :{doc[tags]}"
# Default note editor mode: text | markdown
note_editor: markdown
# Diff display style for concurrent edits: table | inline | pairwise
diff_style: inline
zotero:
  mode: local            # local | web
  local_api: http://localhost:23119/api  # Zotero desktop local API
  endpoint: null         # optional Web API base URL override; local mode uses local_api
  library_id: 0          # required when mode: web (your numeric Zotero user id)
  library_type: user
  api_key: null          # required when mode: web (Zotero Web API key)
  storage_dir: ~/Zotero/storage  # local Zotero storage path for attachments
```

> **Linux:** `ref` detects Linux at runtime and swaps the default picker bindings to
> use `xdg-open` for Zotero links and `xclip -selection clipboard` for clipboard
> actions. Install `xclip` first (for example, `sudo apt install xclip`) to enable
> the `alt-y` clipboard binding.
>
> **Windows:** bindings fall back to PowerShell (`Start-Process`) for opening
> Zotero links and the built-in `clip` utility for clipboard actions.

Install `fzf` separately for picker use, for example `brew install fzf` on macOS or
your Linux distribution's package manager. Check `fzf --version` for >=0.55;
`doctor` currently checks presence, not the version. Linux clipboard bindings also
need `xclip`; Windows uses `clip` and macOS uses `pbcopy`.

</details>

### 3. Verify the selected installation

Use the same executable/environment for every check:

On macOS/Linux:

```bash
.venv/bin/ref doctor --json --no-connect
.venv/bin/ref doctor --json
.venv/bin/ref --non-interactive list --limit 1 --json
```

On PowerShell:

```powershell
.\.venv\Scripts\ref.exe doctor --json --no-connect
.\.venv\Scripts\ref.exe doctor --json
.\.venv\Scripts\ref.exe --non-interactive list --limit 1 --json
```

Check exit codes and JSON contents. The first command verifies configuration only;
the second verifies read access. A successful empty search is valid. A missing
picker warning does not block agent reads, and successful read access does not
establish write permission. Installation verification needs no library writes.

For an explicitly selected Web read or reviewed write, use `ref doctor --web --json`.
It checks the configured Web library independently of the desktop API and reports
the selected `backend` in JSON. Preserve `--web` through the initial `read`, plan,
application, and verification. The default doctor continues to check the configured
mode; `--web` does not change the configuration file.

If a required command is missing, identify which installation is running:

```bash
command -v ref
.venv/bin/python -I -c "import sys, ref; print(sys.executable); print(ref.__file__)"
```

In PowerShell use `Get-Command ref` and
`.\.venv\Scripts\python.exe -I -c "import sys, ref; print(sys.executable); print(ref.__file__)"`.
The `-I` probe avoids importing the current directory accidentally. A normal
installation loads from that environment's site-packages; editable installs load
from their checkout. Check the executable, module path, and command support even
when two installations report the same package version.

If `ref` works in your fish terminal but the agent uses zsh/bash, first try
`fish -ic 'command --search ref'` and verify the returned executable. A fish
function or alias can also provide required environment settings; follow the
[shell-discovery guide](skills/refero/references/install.md#when-ref-works-in-fish-but-not-in-the-agents-shell)
before reinstalling or bypassing that wrapper.

For a failed local connection, doctor now gives a machine-readable `code` and
`next_steps`: start Zotero Desktop, enable Local API access in Settings > Advanced,
and check the configured endpoint. A failed read does not establish that Zotero is
closed. Doctor prints the same advice in text mode and leaves launching or changing
settings outside the diagnostic command. For a requested local search, an agent
with shell access on your Mac can start Zotero with `/usr/bin/open -a Zotero`,
then recheck doctor and continue. See [local connection recovery](skills/refero/references/install.md#recover-a-local-api-connection)
for unavailable app controls, startup limits, and settings that need user action.

For connection/configuration failures, follow the
[installation troubleshooting guide](skills/refero/references/install.md#troubleshooting).

### 4. Install the Agent skill

From the matching checkout, choose the shared directory for compatible agents or
the Claude Code directory:

```bash
.venv/bin/python scripts/install_skill.py --agent agents
.venv/bin/python scripts/install_skill.py --agent claude-code
```

On PowerShell replace `.venv/bin/python` with `.\.venv\Scripts\python.exe`.
Each command copies the skill and its references; neither installs the `ref`
executable nor supplies credentials.

| Agent | User installation | Project installation | Explicit invocation |
| --- | --- | --- | --- |
| Shared (`--agent agents`) | `~/.agents/skills/refero/` | `PROJECT/.agents/skills/refero/` | Depends on agent; request “use the refero skill” |
| Codex | `~/.agents/skills/refero/` | `PROJECT/.agents/skills/refero/` | `$refero` |
| Claude Code | `~/.claude/skills/refero/` | `PROJECT/.claude/skills/refero/` | `/refero` |

`--agent codex` remains an alias for the shared destination. Install it once;
agents that discover that directory can reuse the same complete skill folder.

Directory references: [Codex skills](https://learn.chatgpt.com/docs/build-skills)
and [Claude Code skills](https://code.claude.com/docs/en/skills).

Other local agents document support for the shared directory:

| Agent | Discovery or verification |
| --- | --- |
| [Cursor](https://cursor.com/docs/skills) | Discovers user and project `.agents/skills/` directories |
| [Gemini CLI](https://geminicli.com/docs/cli/skills/) | Discovers both scopes; check with `gemini skills list` |
| [OpenCode](https://docs.opencode.ai/docs/skills/) | Discovers both scopes; loads skills through its skill tool |
| [GitHub Copilot in VS Code](https://code.visualstudio.com/docs/agent-customization/agent-skills) | Discovers both scopes; check the Skills customization view |
| [Devin Desktop / formerly Windsurf](https://docs.devin.ai/desktop/cascade/skills) | Discovers both scopes; invoke with `@refero` |

These are documented discovery paths, not end-to-end Refero validation for every
agent or version. For [Cline](https://docs.cline.bot/customization/skills), copy the
entire `skills/refero/` directory, including `references/`, into
`~/.cline/skills/refero/` or `PROJECT/.cline/skills/refero/`; this installer does
not manage Cline's directory. Check its Skills menu, then invoke `/refero`.

For a project-only skill, add `--project /path/to/target-project`; the target is
the project where the agent will use Refero, which need not be this repository.
Existing destinations are refused by default. Review local edits before using
`--replace`, which overwrites shipped files and preserves additional local files.

In a new agent message, invoke the skill and request a read-only search. Supply
the absolute `ref` path if needed. If the skill is not listed, check the chosen
agent's destination and restart its session. Skill discovery and CLI connectivity
are separate checks. See [agent examples](#agent-examples) for tasks to try.
The agent's execution environment needs the selected `ref` executable and access
to the intended Zotero library. Remote or cloud agents require their own setup;
a skill copied there does not provide access to Zotero on your computer.

### Upgrade an existing installation

Update the chosen checkout/release, reinstall the CLI into the **same selected
environment**, and update each selected agent's skill from that revision:

```bash
.venv/bin/python -m pip install .
.venv/bin/python scripts/install_skill.py --agent agents --replace
```

`--agent codex` updates the same shared folder. Use `--agent claude-code` for
Claude Code, and repeat `--project` for an existing
project-scoped installation. On PowerShell use that environment's `python.exe`.
Repeat the help, doctor, and read-only checks, then explicitly invoke the skill.
Report the executable path, source revision (when available), and verification
results when installing on someone else's behalf; keep credentials out of reports.

---

## Usage

Replace sample item keys `ABCD1234` and `EFGH5678` with actual keys returned by
your library. Collection arguments likewise use actual collection keys or names.

Basic help:

```bash
ref -h
ref --help
ref list -h
ref pick -h
ref view -h
ref read -h
ref write -h
ref doctor -h
ref export -h
ref label -h
ref tag -h
ref collection -h
ref coll -h
ref set -h
ref add -h
ref delete -h
```

Quick search (default subcommand is `list`, `-q` implied with positional args):

```bash
ref "graph neural"
ref smith
ref keyword1 keyword2 keyword3
ref keyword --author chu
ref --year 2025 keyword1 keyword2
```

Filters and collections:

```bash
ref -q graph --limit 10
ref --tag nlp
ref --author chu
ref -c ABCD1234
ref -c "Reading List"
ref --title transformer
ref --year 2021 --title "transformer"
```

Picker (interactive with `fzf`; positional text is the search query):

```bash
ref pick "graph neural" --limit 10
ref pick --tag nlp
ref pick -c "NLP"
ref pick --author smith --year 2021
```

Open best attachment / specific types:

```bash
ref open ABCD1234           # best: PDF -> HTML -> URL/DOI
ref open --pdf ABCD1234
ref open --html ABCD1234
ref open --url ABCD1234
```

View metadata:

```bash
ref view ABCD1234
ref preview ABCD1234
```

Export:

```bash
ref export ABCD1234 EFGH5678 --format bibtex
ref export ABCD1234 EFGH5678 --format bibtex --out out.bib
```

Add by identifier (web mode only):

```bash
# requires zotero.api_key and zotero.library_id
ref add --doi xx.xxxx/xx -c "Reading List,AI"    # collections by name or key
ref add --arxiv xx.xxxx/xx --tag "ml,arxiv"
ref add --doi xx.xxxx/xx --dry-run               # show Zotero JSON only
```

Edit metadata (web mode only):

```bash
# requires zotero.api_key and zotero.library_id
ref edit ABCD1234
```

Label editor (tags and collections):

```bash
# Editor mode: opens a temp file to edit collections and tags
ref label ABCD1234

# Interactive mode: pick tags, then collections via fzf
# Use Tab to toggle labels, type a new tag, then Enter/Esc to confirm/cancel.

ref label ABCD1234 --pick
ref label ABCD1234 --pick -l 100
```

Edit tags only:

```bash
ref tag ABCD1234
ref tag ABCD1234 --pick
```

Edit collections only:

```bash
ref collection ABCD1234
ref coll ABCD1234 --pick
```

Set metadata (web mode only):

```bash
# requires zotero.api_key and zotero.library_id
# add tags
ref set ABCD1234 -g ml -g reading
ref set ABCD1234 --tag "ml,reading,arxiv"

# add to collections (by key or name; partial names supported)
ref set ABCD1234 -c ABCD1234 -c "Reading List"
ref set ABCD1234 --collection "AI,Reading List"

# attach a local PDF as a linked-file attachment
ref set ABCD1234 --pdf ~/Downloads/paper.pdf

# remove from collections / remove tags
ref set ABCD1234 --collection-rm "Reading List" --tag-rm ml

# remove all PDF attachments
ref set ABCD1234 --pdf-rm

# combine
ref set ABCD1234 -g nlp -c "Reading List" --pdf ./paper.pdf
```

### Deletion behavior

Deletion requires an explicit choice of item keys. The current Web backend uses
the same delete operation with and without `--permanent`; do not rely on that
flag or the status message to distinguish a recoverable Trash operation. This is
a limitation of the legacy backend, not a guarantee that deletion is reversible.

```bash
ref delete ABCD1234 EFGH5678           # prompts for confirmation
ref delete -y ABCD1234 EFGH5678        # acknowledges approval without prompting
```

Content Fields Editing:

Edit text-based fields on an item:

```bash
ref note ABCD1234       # Edit or create a child Note
# use Markdown editor mode for notes (requires deps: markdown, markdownify)
ref note ABCD1234 --note-editor markdown
# or set config: note_editor: markdown|text, and markdown is default
ref abstract ABCD1234   # Edit the abstractNote field
ref extra ABCD1234      # Edit the Extra field

```

### Editing concurrency
- Applies to `ref edit ABCD1234`, `ref extra ABCD1234`, and `ref abstract ABCD1234`.
- When editing, if changed remotely after you opened the editor, ref detects it and shows a three‑way diff of base → remote → yours.
- **Actions**:
  - **[y]ours**: overwrite the remote note with your edited text.
  - **[r]emote**: reload the latest remote content into the editor and let you re‑edit.
  - **[m]erge**: merge metadata changes, only in `ref edit ABCD1234`.
  - **[a]bort**: cancel without saving.
- The diff display is controlled by `diff_style` in `config.yaml` (`table` | `inline` | `pairwise`).
- Note editor mode is controlled by `note_editor` in `config.yaml` (default `markdown`) or `--note-editor` per‑command.

Legacy child-note editing does not offer the same three-way conflict dialog.
For reviewed, version-checked note updates use the non-interactive `write` entry.

### Diff style for concurrent edits

When editing text fields and a newer remote version exists, ref shows a diff before you choose an action. Control the display via `diff_style` in `config.yaml`:

- table: concise summary showing values for base, yours, remote
- inline: single-line bracketed inline diff like `[base → remote → yours]`
- pairwise: compact inline diff using the C++ Diff Match Patch engine via `fast-diff-match-patch`. Deletions are shown in red; remote insertions(base→remote) in blue; local insertions(base→yours) in green. 

Example:

```yaml
diff_style: inline   # table | inline | pairwise
```

More examples

```bash
# list (explicit)
ref list -q "graph neural"
ref list --tag nlp
ref list -d 7b00316
ref list --doi 7b00316
ref list --author smith
ref list -c ABCD1234
ref list -c "Reading List"
ref list --title transformer
ref list --year 2021
ref list -a smith -y 2021
ref list --author smith --year 2021
ref list --title transformer --tag nlp
ref list --author "smith" --title "transformer"
ref list --year 2021 --title "transformer"

# group by collection path
ref list --group-by-collection                 # groups items under "Parent -> Child"

# pick (explicit)
ref pick -q graph
ref pick -d 7b00316
ref pick --doi 7b00316
ref pick -a smith
ref pick --title transformer
```

## Local Agents

Install the CLI and your selected skill using the shared
[Getting Started](#getting-started) steps. A user or an agent can follow that same
installation path. For example, you can ask an agent:

> Install Refero from this checkout in a dedicated Python 3.11 environment, install
> its skill for Codex, and verify a read-only library search. Preserve my existing
> config and report the absolute CLI path, installed skill path, and check results.
> Do not change library records during setup.

Use explicit commands and the verified executable path for unattended work:

```bash
ref --non-interactive list --query "graph neural" --limit 20 --json
ref --non-interactive read ABCD1234
ref --non-interactive read ABCD1234 --resource notes
ref --non-interactive read --resource collections
ref --non-interactive export ABCD1234 --format bibtex --out references.bib
```

Replace example keys with keys returned by your library. These interfaces have
different result contracts:

| Interface | Result and limits |
| --- | --- |
| `list --json` | Bounded search results; `count` is returned rows, not the total matches |
| `view --json-only` | Normalized summary with resolved collection paths; no abstract, note body, or item version |
| `read` | Raw data and explicit scope/completeness; JSON is the default, with no `--json` needed |
| `write` | JSON plan first, then a receipt after confirmed submission |
| `export` | BibTeX, CSL-JSON, or Markdown; `--out` writes a local file |

`--non-interactive` goes before the subcommand. It disables picker/editor flows
and retry prompts; it does not grant permission to write. Existing human CLI
defaults remain available. `read --resource items` traverses all library items,
including notes and attachments: classify those records before reporting paper
counts, and inspect `complete` before claiming an exhaustive result. Attachment
reads return metadata/paths, not PDF contents. `open` launches a system viewer.

For a library change, first create a local input file containing the requested
replacement. For example, put the intended Extra text in `extra.txt`. With Web
credentials configured, prepare a plan:

```bash
ref --non-interactive write ABCD1234 --web --resource extra --input extra.txt --out change.json
```

Show the user the target library, affected keys, and before/after values. Only
after explicit approval, replace `PLAN_ID` with the plan's actual full digest:

```bash
ref --non-interactive write --web --apply change.json --confirm PLAN_ID
```

The confirmation must match that exact plan; changed versions/content require a
new review. Reciprocal relation changes appear in the plan. Multi-item writes
are not atomic, so inspect partial-failure receipts before deciding what remains.
Legacy non-interactive `set/add/delete` also require `--yes` after user approval;
`add --dry-run` is exempt. The flag itself is not evidence of human consent.

### Agent examples

Invoke `$refero` in Codex or `/refero` in Claude Code, then try one of these requests.
Replace topics, collection names, and selected items with your own. These combine
the CLI's data access with the agent's reasoning; no separate skill is needed for
each task.

| Ask the agent | Expected result | Library changes |
| --- | --- | --- |
| “Give me a library health report: counts by Zotero item type, paper-type records missing DOI/year, and documents or webpages that may need a type review.” | A report based on complete reads; distinguish typed paper counts from inferred candidates, and exclude notes/attachments/annotations from bibliographic totals | None |
| “Prepare a reading-group pack on graph neural networks from my NLP collection: choose up to eight references, export `reading-group.bib`, and write a short `briefing.md`.” | Selected keys, selection reasons, bibliography, and briefing with the search limit and evidence sources stated | None; creates local files |
| “Compare these three papers using their stored abstracts and notes. List agreements, disagreements, and questions to investigate.” | A comparison grounded in the retrieved fields, identifying gaps rather than claiming to have read the PDFs | None |
| “Which selected papers have a PDF available on this computer? List their attachment keys and paths without opening them.” | Attachment metadata and checked local availability; distinguish missing local files from absent attachments | None |
| “Propose a `reading-group` tag for the selected papers, preserving their current tags and automatic/manual types. Show all changes and wait for my confirmation.” | Concrete label plans for the selected keys; submit only the approved, unchanged plans and verify them | Only after confirmation |
| “Suggest related-item links between these papers using their abstracts and notes. Explain the pairs, preview both directions, and wait for approval.” | Evidence-based suggestions and a plan covering reciprocal links; report any partial failure | Only after confirmation |

Keep the CLI and skill from the same source revision. Full references are packaged
inside the skill so they remain available after installation:

- [Install, configure, verify, and upgrade](skills/refero/references/install.md)
- [CLI inputs, outputs, limits, and error handling](skills/refero/references/cli.md)
- [Lossless data access and confirmed writes](skills/refero/references/data.md)
- [Compose tasks and verify their results](skills/refero/references/workflows.md)
- [Refero skill instructions](skills/refero/SKILL.md)

## Picker Styles

You can quickly change how items are rendered in the picker using the `fzf-style` preset. Supported values: `compact`, `aligned`, `colored`, `preview`, `title-preview`.

Minimal example:

```yaml
fzf-style: aligned  # compact | aligned | colored | preview | title-preview
```

Preset details:

- aligned: padded columns for title/author/year
- compact: shorter columns (good for narrow terminals)
- colored: ANSI-colored title/author/year (requires `--ansi`), set color_scheme in config:calm|bright|minimal|contrast
- preview: adds a right-side preview pane with colored JSON using `jq` if available
- title-preview: colored title line + compact metadata preview

Preview preset sets (simplified):

```yaml
fzf-extra-flags:
  - --ansi
  - --delimiter
  - " :: "
  - --preview
  - "sh -c 'if command -v jq >/dev/null 2>&1; then ref view \"$1\" | jq -C .; else ref view \"$1\" | cat; fi' sh {5}"
  - --preview-window
  - right,60,border
```

You can override the preview window regardless of preset:

```yaml
fzf-preview-window: up,60,border
# or:
fzf-preview-window: up,1,wrap
fzf-preview-window: down,1,wrap
```

---

## Keybindings

Press Ctrl-h in any picker to show a dynamic help overlay derived from your `fzf-extra-bindings` in `config.yaml`. If no bindings are configured, a concise default help is shown.

In the `pick` view:

- ctrl-n/p: navigate items
- Tab: toggle select/unselect (multi-select)
- ctrl-r: open in Zotero (`zotero://select/library/items/{key}`)
- ctrl-s: jump to target item
- ctrl-o: open best attachment (such as `ref open {key}`)
- ctrl-e: edit metadata (web mode; such as `ref edit {key}`)
- ctrl-t: edit Label, Tags or Collections (such as `ref label {key} --pick`)
- ctrl-l: relate items (such as `ref relate {key} --pick`)
- ctrl-k: edit Content Fields: Note, Extra or Abstract with default Markdown editor
- ctrl-a: export to BibTeX as `{citekey}.bib`
- ctrl-d: delete selected items (`ref delete {+5}`; add `-y` to skip confirmation)
- alt-y: copy field of item (clipboard command defaults to `pbcopy` on macOS, `xclip -selection clipboard` on Linux, or `clip` on Windows). Use {1},{2},{3}... to select different field item.

---

## Notes

- With `zotero.mode: local`, `ref` uses `zotero.local_api` (default `http://localhost:23119/api`).
- For the Zotero Web API, set `zotero.mode: web` and provide `zotero.api_key` and `zotero.library_id`. Optionally set `zotero.endpoint`.
- `fzf` matches displayed fields; include fields in `fzf-header-format` to broaden matching.

---

## Contributing

Issues and PRs are welcome.

Run `python3.11 -m unittest discover -s tests` and
`python3.11 -m compileall ref scripts tests` for CLI and installer changes.
Skill changes also have [Agent behavior evaluations](evals/refero/README.md):
eight isolated scenarios record actual model-selected simulated tool calls,
followed by independent review of the answers. CLI tests, simulated Agent
evaluations, and live runtime verification establish different kinds of evidence.

## License

This project is licensed under the terms of the [MIT License](LICENSE).
