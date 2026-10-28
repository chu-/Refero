# Lossless reads and reviewed writes

Use these commands for data that the original CLI displays in editors or pickers.
`read` and `write` emit JSON by default; they do not require a `--json` flag. They
never open an editor or picker. The original interactive commands remain available.

## Select and read data

```bash
ref --non-interactive read ABCD1234
ref --non-interactive read ABCD1234 --resource abstract
ref --non-interactive read ABCD1234 --resource extra
ref --non-interactive read ABCD1234 --resource notes
ref --non-interactive read ABCD1234 --resource relations
ref --non-interactive read ABCD1234 --resource labels
ref --non-interactive read ABCD1234 --resource children
ref --non-interactive read ABCD1234 --resource attachments
ref --non-interactive read --resource collections
ref --non-interactive read --resource tags
ref --non-interactive read --resource items
```

`--web` explicitly selects the configured Web library instead of a local-mode
library. It uses the configured library ID/type, endpoint, and credentials. Inspect
the returned `library` identity; do not change backends merely to bypass a failure.

| Resource | `data` contains | Scope |
| --- | --- | --- |
| `item` (default) | Complete raw Zotero record, including key, version, data, and metadata | One item; child records are separate |
| `abstract`, `extra`, `relations` | `field`, `present`, and raw `value` | One exact field; missing, null, and empty are distinct |
| `labels` | Raw tag objects and collection keys; `fields_present` lists existing fields | One item's memberships, preserving automatic/manual tag types |
| `notes` | Raw child note records, including HTML and note keys/versions | All direct child notes |
| `children` | Raw child records | All direct children |
| `attachments` | Raw attachment records, plus a separate `locations` array | Direct attachment metadata and local file availability; no viewer is launched |
| `collections`, `tags` | Raw directory records, preserving hierarchy and metadata | Entire selected library directory |
| `items` | Raw item records | All non-trashed library items, including notes and attachments |

For `item`, the editable Zotero mapping is at `response.data.data`. It preserves
original creator objects, complete dates, abstract HTML/text, Extra, tags, relations,
and versions. The existing `view --json-only` remains a normalized summary; use
`read` when fields outside that summary matter.

Lists traverse all pages and check duplicate identities, server totals, and
snapshot versions. `complete: true` means both cardinality and snapshot evidence
were available and consistent. If either header is unavailable, `complete: false`
is returned with the observed records and an explanation; pagination exhaustion
alone does not justify a verified-complete claim. Changing versions or inconsistent
pages fail with structured errors rather than returning a successful partial list.

For filtered child resources, `count` and `completeness.returned` describe the
selected notes/attachments. `source_total` and `source_returned` describe the child
records scanned. `completeness.total` is the verified selected total, or null if
that filtered total cannot be established. Scope is always explicit: a complete
item record does not mean its children or attachment full text were included.

Attachment `locations` are derived from configured storage and the raw path or
filename. `exists: false` means no file was found at the resolved location, not
that the server has no attachment. Full-text/PDF extraction is not implemented by
these commands.

## Prepare a modification without writing

Reviewed writes currently use the Web API. In local mode, explicitly add `--web`
to preparation and application, and provide credentials for the corresponding
library. New writes never silently fall back from a local library to a Web library.
Local API write authentication is not implemented by this interface.
Run `ref doctor --web --json` for this backend. Read the initial values with
`read ... --web`, prepare/apply with `--web`, and verify with `read ... --web`;
local snapshots may be out of sync and are not a replacement-array baseline.

```bash
ref --non-interactive write ABCD1234 --web --resource abstract --input abstract.txt --out change.json
```

This only reads the library and saves a plan. It does not change any Zotero item.
The plan includes the target library, affected keys, exact before/after values,
version/content checks, and a `plan_id` binding the contents. Existing output files
are refused so a reviewed plan is not silently overwritten. Plans contain library
data and are created with owner-only permissions on POSIX.

| Write resource | Input file | Meaning |
| --- | --- | --- |
| `item` | JSON field patch, raw Zotero record, or edited `read --resource item` response | Change supplied fields; unspecified fields remain unchanged |
| `abstract` | UTF-8 text | Replace `abstractNote` exactly |
| `extra` | UTF-8 text | Replace `extra` exactly |
| `note` with `--note-key KEY` | UTF-8 HTML | Replace that selected child note's HTML |
| `note` without `--note-key` | UTF-8 HTML | Explicitly create a new child note, even if notes already exist |
| `labels` | JSON with `tags` and/or `collections` | Replace only the supplied arrays; preserve omitted fields |
| `relations` | JSON array of related item keys | Replace same-library related links and maintain reciprocal links |

Text and HTML inputs preserve line endings and trailing newlines. Markdown is not
implicitly converted. Label tags use Zotero objects, for example:

```json
{"tags": [{"tag": "reading", "type": 1}], "collections": ["COLL1234"]}
```

An empty array deliberately clears that field. Collections use existing keys,
validated against a complete directory read. Keys and versions are identity/
precondition fields and cannot be changed through an item patch. An edited raw
record must retain the reviewed key/version. A `read` envelope must belong to the
same library/backend; read with `--web` first when preparing a Web write.

Example of changing a selected note:

```bash
ref --non-interactive write ABCD1234 --web --resource note --note-key NOTE1234 --input note.html --out note-change.json
```

The note's type and parent are checked. Creation without `--note-key` receives a
write token derived from the reviewed plan; this uses Zotero's replay-protection
window and is not a permanent deduplication guarantee. Inspect the parent notes
after an uncertain result before preparing another creation.

Related-item plans show every reciprocal change. Other relation predicates and
cross-library/opaque links are retained by the dedicated `relations` resource.
An explicit full metadata patch may edit other predicates too; same-library
`dc:relation` changes still generate reciprocal changes. Multi-item updates are
not atomic.

## Obtain approval and submit the exact plan

Show the user the target library, affected items, and changes from `change.json`.
After the user explicitly approves that plan, supply its actual `plan_id`:

```bash
ref --non-interactive write --web --apply change.json --confirm PLAN_ID
```

`PLAN_ID` is a placeholder for the full digest in the saved plan. The command
cannot authenticate human consent; the Agent must obtain it in conversation.
Without matching confirmation, non-interactive application fails before backend
access. Ordinary interactive application shows the plan and asks for confirmation
with a default of no. Approval does not carry over to a changed plan.

Before any mutation, all reviewed item versions/content and the target library are
checked. Each update also sends the reviewed version as an HTTP precondition, so
a change between preflight and submission is rejected. Updates are never forced,
automatically retried, or redirected to another library. Each successful operation
is read back and compared with its reviewed fields.

The receipt reports `applied`, `verified`, `complete`, and the plan ID. On partial
failure it also identifies the unverified item, remaining operations, and error.
Stop, read the affected items, and prepare a fresh plan for what remains; do not
replay the original multi-item sequence blindly. A submission response alone does
not establish verification.

## Errors and legacy commands

New data commands return `schema_version: 1`, `status: error`, `complete: false`,
and `error.code/message` on failure. Usage/confirmation failures exit 2; operational
failures and incomplete application exit 1. Read/write errors are redacted rather
than echoing server bodies or API keys. List completeness that is unverified is
reported in the successful read response and must be checked by the caller.

Original `edit/note/abstract/extra/relate/label/tag/collection` flows remain
interactive. `set/add/delete` in non-interactive mode require `--yes` as the
caller's acknowledgement of explicit user approval; `add --dry-run` is exempt.
For editor-backed data prefer reviewed `write` plans. Existing human command
defaults and editor/picker behavior remain available.
