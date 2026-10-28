# Compose and verify Refero operations

Use an absolute path to `ref` if the Agent's shell does not inherit the selected
virtual environment. Examples use POSIX shell syntax; the Python subprocess
pattern works on Windows too. Check every command's exit code before proceeding.

## Search, select, and export

```bash
ref doctor --json
ref --non-interactive list --query "graph neural" --limit 20 --json > matches.json
```

Inspect `matches.json`, select the intended item keys, then export those exact keys:

```bash
ref --non-interactive export ABCD1234 EFGH5678 --format csljson --out references.json
ref --non-interactive export ABCD1234 EFGH5678 --format bibtex --out references.bib
```

Verify CSL-JSON is an array with the expected entries and compare DOI/title fields
to the selected results. Inspect BibTeX entries and citekeys. A nonempty file alone
does not prove the correct references were exported. `--out` uses the existing
overwrite behavior; select a path consistent with the user's request.

When automating composition, pass keys as argument values rather than constructing
a shell command from item titles or other library data:

```python
import json
import subprocess

ref_command = "ref"  # Or the absolute path to the installed executable.
search = subprocess.run(
    [ref_command, "--non-interactive", "list", "--query", "graph neural",
     "--limit", "20", "--json"],
    check=True, capture_output=True, text=True,
)
result = json.loads(search.stdout)
if result["schema_version"] != 1 or result["count"] != len(result["items"]):
    raise ValueError("Unexpected search response")
keys = list(dict.fromkeys(item["key"] for item in result["items"]))
if keys:
    subprocess.run(
        [ref_command, "--non-interactive", "export", *keys,
         "--format", "csljson", "--out", "references.json"],
        check=True,
    )
```

This exports the bounded search results, not every match in the library. Add the
user's selection criteria before export when they requested a subset.

## Add a tag and verify it

Reviewed writes use the Web library. After selecting an item within the user's
requested scope, check that backend and read its current labels as the baseline:

```bash
ref doctor --web --json
ref --non-interactive read ABCD1234 --web --resource labels
```

Build a JSON input containing the desired `tags` array, retaining existing tag
objects and types. Then prepare a plan using the Web library and show the diff:

```bash
ref --non-interactive write ABCD1234 --web --resource labels --input labels.json --out labels-plan.json
```

After user approval of the concrete plan, apply its actual plan ID:

```bash
ref --non-interactive write --web --apply labels-plan.json --confirm PLAN_ID
ref --non-interactive read ABCD1234 --web --resource labels
```

Verify that the Web target is the intended library. Build the replacement arrays
from that Web read, since unsynced local labels may differ. Check that the final
`tags` contains `reading` and preserves
pre-existing tags. For collection changes, verify keys in `collections`. See
[data access](data.md) for exact inputs, completeness, and partial-failure recovery.

## Import by DOI or arXiv

Search for existing records first, then compare actual identifiers. If the bounded
search cannot establish absence, report that uncertainty rather than promising
duplicate-free import.

```bash
ref --non-interactive list --doi 10.1234/example --json
ref --non-interactive add --doi 10.1234/example --dry-run
```

The identifier above is illustrative; substitute the user's real DOI. Inspect the
proposed metadata and obtain explicit user approval of the creation. Then execute
without `--dry-run`, acknowledge approval with `--yes`, and read back the key:

```bash
ref --non-interactive add --doi 10.1234/example --yes
ref --non-interactive view NEWKEY12 --json-only
```

For arXiv use `add --arxiv IDENTIFIER` and inspect the imported URL/title. Verify
requested tags and collections too. If creation times out, look for the item
before retrying: no idempotency token is currently supported.

## Validate installation without library writes

Use `--version`, `doctor --json --no-connect`, `doctor --json`, and a one-item JSON
search. Test skill discovery separately in each installed Agent: explicitly invoke
the skill, request a read-only library search, and check that the Agent selects
non-interactive JSON commands and accurately reports empty or failed results.
Do not describe file installation alone as a verified Agent workflow.
