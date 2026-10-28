# Install and configure Refero

These instructions apply to the source revision shipping this guide. Users and
agents follow the same setup: install the CLI, configure the intended library,
verify reads, and install the chosen agent's skill. CLI readiness, library access,
and skill discovery are separate outcomes. Keep CLI and skill from the same
revision; a published package or `main` may differ from an unpublished checkout.

## Inspect the environment

Check `ref --version`, `ref --help`, Git, and Python 3.11 before installing. Use
`command -v ref` on macOS/Linux or `Get-Command ref` in PowerShell to identify an
existing executable. Reuse a suitable selected environment and existing checkout;
do not create a nested clone or replace another installation unintentionally.
Also check that `ref --help` lists `read` and `write` before using editor-backed
data access. A package version alone does not prove that the active executable
and this skill come from the same revision.

If the active command rejects `--version` or lacks the required subcommands, treat
that as a compatibility failure. Do not repeat those same commands without changing
the selected executable or installation. An agent performing setup should finish
all independent installation work, then report any missing library credentials or
external connection requirement without claiming library access is ready.

The package declares Python >=3.9; project development and the examples here use
Python 3.11. `fzf` >=0.55 is needed for interactive pickers. Unattended list, view,
set, and export commands do not need `fzf`, clipboard tools, or an editor.
Local mode requires a running Zotero desktop instance with the Local API enabled.
Web mode requires the intended library ID and an API key with the task's permissions.

### When ref works in fish but not in the agent's shell

A failed lookup in zsh/bash establishes only that `ref` is unavailable in that
shell. Before reinstalling, check the user's existing shell setup and selected
environment. If the user uses fish, run its installed executable with the user's
interactive configuration:

```bash
fish -ic 'type --type ref'
fish -ic 'command --search ref'
```

Use the actual fish executable path if fish itself is absent from the agent's
PATH. If the user's setup depends on a login shell, use `fish -lic` instead.
`command --search` returns an external executable path; validate that path with
`--version`, required-command help, and `doctor --json`, and reuse it for later
calls. Keep any required exported configuration, such as `REF_CONFIG`, when
switching to direct invocation.

If `type --type` reports `function`, inspect only the relevant wrapper with
`fish -ic 'functions ref'`. A missing external-path result can still mean a working
function/alias. Preserve its arguments, environment, and environment activation;
the wrapper may target a different executable than `command --search` finds.
If those settings are needed, use `fish -ic 'ref --version'` and
`fish -ic 'ref doctor --json'` to verify the existing wrapper and keep executing
through it. Account for shell startup messages separately from the CLI's JSON.
Fish's `type --path` can point to a function definition file, not an executable.

Resolve only this command and its required configuration; keep credentials out of
reports. Preserve the user's shell files. Reinstallation is appropriate only when
the selected installation is absent or incompatible, rather than just invisible
to zsh. See fish's [command lookup](https://fishshell.com/docs/current/cmds/command.html),
[type](https://fishshell.com/docs/current/cmds/type.html), and
[startup options](https://fishshell.com/docs/current/cmds/fish.html).

## Install the program from source

Use the current checkout if it contains this guide. Otherwise obtain the requested
release or commit from the repository and verify that it includes `doctor`,
`read`, `write`, and `--non-interactive`. To obtain a checkout:

```bash
git clone https://github.com/chu-/Refero.git
cd Refero
```

If a specific tag or commit was requested, check it out before installation. Record
`git rev-parse HEAD` and any local modifications with the installation result:
`ref --version` identifies a package version, not the exact source revision.

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

In PowerShell, use `.\.venv\Scripts\ref.exe` in place of `ref` in the remaining
examples, or activate the environment if permitted by your existing shell policy.
For a development checkout use that environment's Python with `-m pip install -e .`.
In the commands below, `ref` means this selected executable: activate the environment
for a human shell, or use its full path in an agent's commands. Shell activation may
not persist into the agent's next execution. Do not switch back to a global `ref`
after verifying the environment-specific one.

Alternative sources in an already selected Python environment:

```bash
python -m pip install refero
python -m pip install git+https://github.com/chu-/Refero.git@main
```

Choose one source and check that its required commands exist. These commands
install the CLI, not a skill in an agent's discovery directory. Keep or obtain
the matching checkout/source archive to run `scripts/install_skill.py`.

### Verify the import location

On macOS/Linux:

```bash
command -v ref
.venv/bin/python -I -c "import sys, ref; print(sys.executable); print(ref.__file__)"
```

On PowerShell:

```powershell
Get-Command ref
.\.venv\Scripts\python.exe -I -c "import sys, ref; print(sys.executable); print(ref.__file__)"
```

`-I` prevents the current directory from masking the installed module. Normal
installs resolve inside the selected environment's site-packages; editable installs
resolve to their checkout. Check these paths and the required command help even
when two installations display the same version. If `ref` is not on PATH, keep
using the explicit executable rather than treating a successful installation as a
PATH change.

## Configure the library

The default file is `~/.config/ref/config.yaml` on all platforms. `REF_CONFIG` can
select a different file. Preserve an existing configuration; merge only the fields
needed for the selected library. `doctor` never creates or rewrites this file.

For a new local-only configuration:

```yaml
zotero:
  mode: local
  local_api: http://localhost:23119/api
  library_type: user
```

For Web API access, use the actual user or group library ID and a key obtained by
the user from [Zotero's key settings](https://www.zotero.org/settings/keys).
Replace both placeholder values before running commands:

```yaml
zotero:
  mode: web
  library_id: 123456
  library_type: user  # use group for a group library
  api_key: REPLACE_WITH_YOUR_KEY
```

On POSIX systems, create the config directory and restrict the configuration file
to the owner (`chmod 600 ~/.config/ref/config.yaml`). On Windows use the user's
private profile directory and normal user-only access controls. Do not put keys in
the repository or include them in agent responses. Supply the user's existing
credentials through their chosen local configuration process.

The CLI uses `zotero.local_api` in local mode and `zotero.endpoint` as an optional
Web API endpoint override. Legacy local mutation commands can fall back to the
Web API if credentials are configured. Reviewed `write` commands require the Web
API: while retaining `mode: local`, explicitly pass `--web` during preparation and
application. The Web credentials must select the intended library. Both phases use
Web records/versions, not local snapshots. Do not change modes merely to make a
failed command succeed against another library.

## Verify installation

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

Check exit codes before parsing stdout. The first command verifies config without
network access; the second sends a library read request. An empty library is a
successful connection. `doctor` does not test write permission. A missing `fzf`
warning does not block unattended use; the picker check only detects the binary,
not its version. Run `fzf --version` separately for interactive use.

For explicitly selected Web reads or reviewed writes, use the same executable
with `doctor --web --json` (or `doctor --web --json --no-connect` to validate
credentials/configuration only). The JSON `backend` identifies the selection.
This does not rewrite `mode: local` or connect to the desktop API. A local doctor
failure does not block a task explicitly targeting the Web library. Keep `--web`
on the subsequent `read` and `write` commands; `list/view/export` retain their
configured backend and do not accept this data-command override.

The final command must return valid JSON with `schema_version: 1`, an `items` array,
and `count` equal to its length. An empty array is valid. Do not write test items
into a real library merely to validate installation.

## Troubleshooting

| Failure | Next step |
| --- | --- |
| `ref` missing or old help | Check the executable path and selected virtual environment; install this source revision. |
| Works in fish, missing in zsh/bash | Follow the shell-discovery steps above; reuse the verified executable or required fish wrapper before considering reinstallation. |
| Missing config or invalid YAML | Correct the selected `REF_CONFIG` file; ensure the top level and `zotero` are mappings. |
| `LOCAL_API_UNAVAILABLE` | Start Zotero Desktop on the execution machine, enable Local API access, and check `local_api`, port, and local network settings. A failed read does not prove the process is closed. |
| `LOCAL_API_ACCESS_DENIED` | In Zotero Settings > Advanced, enable “Allow other applications on this computer to communicate with Zotero”; verify the configured endpoint is the intended Zotero instance. |
| Web HTTP 401/403 | Check the library ID, library type, API key, and read access. |
| Web HTTP 404 | Check that the intended library exists and its ID/type are correct. |
| Connection error | Check endpoint, proxy, and network settings; retry only after addressing the cause. |
| Doctor passes but a write fails | Read access does not establish write permission; inspect the result and credentials. |
| `WRITE_BACKEND_REQUIRED` | For local configuration, explicitly select the corresponding Web library with `--web`; review the target before writing. |
| Local doctor fails during an explicitly requested Web task | Diagnose with `doctor --web --json`; use `read/write --web` for that task. Do not launch the desktop app to repair Web credentials or change the library implicitly. |
| Skill is present but commands fail | Verify the agent is invoking the selected executable path; the installer only copies skill files. |

Connection-error checks include `code` and ordered `next_steps` in JSON; text
output prints the same recovery suggestions. Doctor performs one read without
starting applications, changing settings, or falling back to another library.
After the cause is resolved, rerun the same selected executable's doctor check.
Local API settings are described in [Zotero's documentation](https://www.zotero.org/support/dev/web_api/v3/local_api).

### Recover a Local API connection

Doctor diagnoses read access; the agent handles dependency startup using the tools
actually available in its session. For a requested local-library search, starting
the installed Zotero desktop app is routine setup. Library writes still require
the reviewed confirmation flow.

1. Check the connection error code and execution environment. A failed API read
   establishes failed connectivity, not whether Zotero is installed or running.
   An app-control API failure establishes that control path failed, not the
   desktop app's state. Use only methods exposed by the current tool's documented
   interface; switch to available shell execution if native app controls are absent.
2. For `LOCAL_API_UNAVAILABLE` on the user's macOS desktop with shell execution,
   start the installed app using the system launcher:

   ```bash
   /usr/bin/open -a Zotero
   ```

   Run this once, check its exit status, then rerun `doctor --json` using the same
   verified `ref` executable or fish wrapper. A successful launcher exit means
   the launch request succeeded, not that Local API is ready. Allow startup time:
   retry the read-only doctor at most three times with two-second gaps, stopping
   on success, then resume the original search. Preserve the selected library.
3. For `LOCAL_API_ACCESS_DENIED`, direct the user to Zotero Settings > Advanced >
   “Allow other applications on this computer to communicate with Zotero”, then
   recheck after it is enabled. Launching alone does not fix that setting. If the
   API remains unreachable after startup, check the configured endpoint/port and
   report the evidence without asserting a specific unverified cause.
4. On other operating systems, use an available launcher with a verified installed
   app path. Remote/container sessions may lack access to the user's desktop.
   If no usable launch mechanism is available or startup fails, report that limit
   and ask the user to open Zotero. Distinguish “cannot launch from this session”
   from “not installed”; claim the latter only when supported by installation
   checks. A single failed app lookup does not establish absence.

Report separately: launch attempt/result, doctor result, and whether the original
search completed. Keep startup bounded; successful recovery is a passing library
read followed by the requested operation, rather than merely opening an app.

## Install the skill separately

From the source checkout, choose a shared Agent Skills destination or Claude Code:

```bash
.venv/bin/python scripts/install_skill.py --agent agents
.venv/bin/python scripts/install_skill.py --agent claude-code
```

On PowerShell use `.\.venv\Scripts\python.exe` instead of `.venv/bin/python`.
Choose the agent(s) the user wants;
installing the skill does not install or configure the `ref` executable.

The destinations are `~/.agents/skills/refero` for `agents` and
`~/.claude/skills/refero` for `claude-code`. The existing `--agent codex` option
is an alias for `agents`: both manage the same folder, so install only once.
To install only for a target project:

```bash
.venv/bin/python scripts/install_skill.py --agent agents --project /path/to/target-project
.venv/bin/python scripts/install_skill.py --agent claude-code --project /path/to/target-project
```

The script copies the skill and all references. Existing destinations are refused
unless `--replace` is supplied. That option overwrites shipped files and preserves
additional local files; review local edits before upgrading. Keep CLI and skill
from the same revision.

In Codex, explicitly invoke `$refero`; in Claude Code, invoke `/refero`. If absent,
verify `SKILL.md` is in the selected Agent's discovery directory and restart the
session. Ask the Agent to perform a read-only search and validate the JSON results.
Automatic selection depends on the request matching the description; it is not a
guarantee of selection on every mention of research.

Discovery references: [Codex skills](https://learn.chatgpt.com/docs/build-skills),
[Claude Code skills](https://code.claude.com/docs/en/skills).

### Other compatible local agents

The following agents document discovery of both `~/.agents/skills/` and project
`.agents/skills/`. They can reuse the shared Refero installation with its bundled
references; choose `--agent agents` rather than installing a copy for each one.

| Agent | Check discovery or invoke |
| --- | --- |
| [Cursor](https://cursor.com/docs/skills) | Request use of the `refero` skill in Agent chat |
| [Gemini CLI](https://geminicli.com/docs/cli/skills/) | `gemini skills list`; `/skills reload` refreshes an existing session |
| [OpenCode](https://docs.opencode.ai/docs/skills/) | Request use of `refero`; its skill tool loads the instructions |
| [GitHub Copilot in VS Code](https://code.visualstudio.com/docs/agent-customization/agent-skills) | Inspect Skills in Agent Customizations |
| [Devin Desktop / formerly Windsurf](https://docs.devin.ai/desktop/cascade/skills) | `@refero` in Cascade |

This table establishes documented directory compatibility, not end-to-end Refero
testing for every runtime/version. Verify discovery and a read-only operation in
the selected runtime before reporting that setup is complete.

[Cline](https://docs.cline.bot/customization/skills) uses `~/.cline/skills/refero/`
or project `.cline/skills/refero/`. Copy the entire `skills/refero/` folder there,
including `references/`, after reviewing any existing destination. The installer
does not manage that path. Check the Skills menu and invoke `/refero`.

All runtimes need permission to read the references, execute the selected `ref`,
and connect to the intended Zotero library. For remote/cloud execution, install
and configure Refero in that environment; local skill discovery does not expose
the user's desktop Zotero API to a remote machine.

## Upgrade and uninstall

Upgrade the selected checkout/release and reinstall using the same environment:

```bash
.venv/bin/python -m pip install .
.venv/bin/python scripts/install_skill.py --agent agents --replace
```

`--agent codex` updates the same shared folder. Use `--agent claude-code` for
Claude Code. For an existing project installation,
repeat its `--project` argument; otherwise you would install at user scope instead.
On PowerShell use the selected environment's `python.exe`. Review local skill edits
before replacement. Repeat help, doctor, and a read-only query, then explicitly
invoke the skill with that same executable available to the agent.

When installing on a user's behalf, report the CLI's absolute path, source revision
when known, selected library mode, skill destination(s), and which checks passed or
remain pending. A local read-only setup need not have Web write credentials. Do not
include API keys or claim write permission based solely on a successful read.

To uninstall, run that environment's `python -m pip uninstall Refero` and remove
only the installed `refero` skill folder for each selected Agent. Preserve the
Zotero library and user configuration unless the user also requests their removal.
Removing a shared installation removes discovery for every agent using that folder.
