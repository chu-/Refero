# Refero CLI contract for local agents

The existing human-facing CLI, aliases, shortcut search, and interactive defaults
remain available. Agent features are opt-in. Put global options before a command:

```bash
ref --version
ref --non-interactive list --query "graph neural" --json
```

`REF_NON_INTERACTIVE=1` also disables interaction. Prefer the per-invocation flag
when sharing a shell with a human. `--json` alone changes output, not interaction.

## Check backend readiness

Use `ref doctor --json` for the configured default, or `ref doctor --web --json`
for explicitly selected Web reads and reviewed writes. Inspect `backend`, the exit
code, and individual checks before using that backend. `--no-connect` validates
configuration only; a missing picker is a warning for unattended operations.

Keep `--web` on the initial `read`, plan preparation, application, and final read
for a Web write. A failed local connection does not block an explicitly requested
Web operation. For local errors follow [local recovery](install.md#recover-a-local-api-connection);
for Web errors follow the credential/network `next_steps`. Recheck the same backend
after addressing the cause; starting the desktop app does not repair Web access.

## Available operations

| Command | Output | Side effects |
| --- | --- | --- |
| `ref --version` | Package version | None; no config or backend access |
| `ref doctor --json` | Versioned report | Library read request; no file or library writes |
| `ref doctor --web --json` | Versioned report for the configured Web library | Explicit Web read; no desktop dependency or fallback |
| `ref doctor --json --no-connect` | Versioned report | Reads configuration only |
| `ref --non-interactive list ... --json` | Versioned normalized search results | Library reads |
| `ref --non-interactive view KEY --json-only` | One normalized item | Library reads |
| `ref --non-interactive read KEY [--resource RESOURCE]` | Lossless data, scope, and completeness | Library reads |
| `ref --non-interactive write KEY --resource RESOURCE --input FILE` | Review plan; no mutation | Library reads |
| `ref --non-interactive write --apply PLAN --confirm PLAN_ID` | Verified write receipt | Only reviewed library changes |
| `ref --non-interactive export KEY... --format FORMAT [--out PATH]` | BibTeX, CSL-JSON, or Markdown | Reads library; writes PATH if supplied |
| `ref --non-interactive set KEY --tag VALUE --yes` | Human-readable status | Updates selected labels or attachments after user approval |
| `ref --non-interactive add --doi DOI --dry-run` | Proposed Zotero item JSON | Fetches metadata/template/collections; no item creation |
| `ref --non-interactive add --doi DOI --yes` | Status including new key | Creates an item after user approval |
| `ref --non-interactive delete KEY... --yes` | Human-readable status | Deletes selected items; see the legacy deletion limitation below |

Normal library commands retain the existing config loader, which creates defaults
when the config is missing and tightens existing POSIX file permissions. Run doctor
first to detect a missing config without these changes. In non-interactive Web
mode, authentication is checked with a read request. The original interactive
Web credential probe remains unchanged.

## Search and identifiers

Use explicit `list` in scripts; humans can continue using `ref "graph neural"`.
Search accepts `--query/-q`, `--limit/-l` (default 50), `--collection/-c`, `--tag/-g`,
`--author/-a`, `--title/-t`, `--year/-y`, and `--doi/-d`. Collections accept keys or
unambiguous names; use resolved keys for repeatable operations.

JSON uses exactly the existing search implementation. Some filters are performed
by Zotero quicksearch and others locally; a single-field quicksearch is not a
guarantee of a strict field match. DOI search takes precedence over query,
collection, tag, author, title, and year. For exact identity, inspect each result's
DOI, URL, or key rather than assuming the search filters prove it.

Limits are inherited from the existing backend. There is no public cursor, total
count, or completeness guarantee in this first JSON interface; increasing a limit
does not establish that every matching item was returned. Do not interpret an
export or modification of a bounded search as a complete-library operation.

Use Zotero `key` for view/set/export/delete. `citekey` is a bibliography identifier
and can be synthesized or changed. Do not pass it as an item key.

## JSON outputs

`list --json` writes one JSON object to stdout, without a display header or ANSI
formatting:

```json
{
  "schema_version": 1,
  "items": [
    {
      "key": "ABCD1234",
      "citekey": "example2025",
      "title": "Example paper",
      "creators": ["Smith, Ada"],
      "year": "2025",
      "doi": "10.1234/example",
      "url": "https://example.org/paper",
      "publication": "Example Journal",
      "journalAbbreviation": "Ex. J.",
      "itemType": "journalArticle",
      "collections": ["COLL1234"],
      "tags": ["reading"]
    }
  ],
  "count": 1
}
```

`count` is the array length, not the total number of matches. No results returns
`{"schema_version": 1, "items": [], "count": 0}` with exit code 0.
`--group-by-collection` additionally includes `collection_paths` on each item.
Consumers should validate the schema version and tolerate additional fields.

`view KEY --json-only` retains its existing normalized object shape;
`view KEY --json` is an alias. Collection paths are resolved in both display and
JSON modes. This is not a raw Zotero record and has no
`schema_version` envelope, abstracts, note bodies, full text, or item versions.
Field names and normalization match list results.

For full fields, child notes, abstract/Extra, related links, labels, attachment
locations, complete directories, and reviewed modifications, use
[data access and reviewed writes](data.md). These commands return their own
versioned JSON directly. `read --resource items` includes notes and attachments;
its record count is not equivalent to a count of papers.

Doctor returns `schema_version`, `version`, `config_path`, `backend`, `ok`, and `checks`.
`backend` identifies the selected `local`/`web` mode (null when the default cannot
be determined). `--web` selects the configured Web library for this invocation
without changing configuration. It requires Web credentials even with
`--no-connect`. Diagnose the same backend used by the intended operation; a
failure of another backend does not establish failure of this one. Web recovery
suggestions preserve an explicit `--web` selection.
Each check has `name`, `status` (`ok`, `warning`, `error`, or `skipped`), and
`message`. Warnings do not make the overall check fail. Skipped connectivity does
not establish a working connection. The picker check detects presence only.
Connection errors also provide `code` and `next_steps` (an ordered array of
suggestions). Local codes are `LOCAL_API_UNAVAILABLE` and
`LOCAL_API_ACCESS_DENIED` (HTTP 403); these describe a failed Local API read, not
confirmed desktop process state. Web codes are `WEB_ACCESS_DENIED` (401/403),
`WEB_LIBRARY_NOT_FOUND` (404), and `WEB_READ_FAILED` (other failures). Follow the
suggestions, then rerun doctor after addressing the cause. Text output includes
the same suggestions. Doctor never starts Zotero, changes settings, or switches
backends as a repair. See [installation troubleshooting](install.md#troubleshooting).

## Non-interactive execution and failures

Non-interactive mode rejects `pick`, `edit`, `picker-edit`, `note`, `abstract`,
`extra`, `label`, `tag`, `collection/coll`, and `relate/link` before backend access.
It rejects `set`, non-dry-run `add`, and `delete` without `--yes`; the caller must
have explicit user approval before supplying that acknowledgement. It does not
answer prompts on the user's behalf. Network errors propagate without prompting
or automatic retries in the
shared retry helper. Underlying HTTP libraries may still perform their own retries.
`open` remains an explicit operation that launches the configured system viewer.

Search and view write data to stdout; diagnostics and usage errors go to stderr.
Legacy errors do not share a structured JSON envelope. Check the exit code before
parsing output. Doctor and the new `read/write` commands also emit JSON on failure:

| Exit code | Meaning |
| --- | --- |
| `0` | Command returned successfully; verify the requested outcome separately |
| `1` | Operational failure, or a failed doctor check |
| `2` | CLI usage error, including an interactive-only command in unattended mode |

Legacy interactive cancellation can also return 0. Existing mutation commands and
exports retain their formats; new `read/write` commands always emit JSON. Do not
assume that all commands accept `--json`.

## Writes and recovery

`set KEY --tag VALUE` adds a tag; `--tag-rm VALUE` removes one. `--collection VALUE`
adds membership and `--collection-rm VALUE` removes it. The tag/collection merge
avoids adding duplicate values. Attachments, imports, and multi-item operations
are not promised to be idempotent or atomic. There is no cross-item transaction or
stdin key input. New `write` plans provide diffs, version preconditions, and JSON
receipts for editor-backed changes; legacy mutations retain their status output.

`add --dry-run` previews item JSON but may use network requests. Repeating a
successful import can create duplicates. Search for the DOI or arXiv URL before
importing and inspect the item after a timeout before retrying. After a partial
failure, re-read each affected key; do not blindly replay the whole sequence.

Local reads do not imply local writes. Legacy write commands can fall back to
the Web API when matching library credentials are configured. For unresolved
version conflicts, re-read and report the conflict rather than forcing an
overwrite. The current Web backend calls the same delete operation with and without
`--permanent`. Do not promise recoverable Trash behavior based on the flag or CLI
status message. Deletion requires explicit user approval of the selected keys and
this backend limitation; it is not an installation verification operation.
