# Refero skill behavior evaluations

These scenarios test an actual model's tool choices and conclusions using simulated
tools. They complement `tests/`: simulated behavior is not evidence that an installed
Codex/Claude runtime, the real OS launcher, or a live Zotero library works end to end.
Fixtures never execute shell commands, contact APIs, or modify the real library.

## Prepare and run

From the source checkout, choose one case from `cases.json`:

```bash
python3.11 scripts/eval_skill.py prepare --case web_without_desktop --out /tmp/refero-web-eval
```

Use a fresh model context for each case when possible. Give the evaluating agent
only the generated `task.md`, its `skill/` snapshot, and the tool protocol below.
Do not give it the rubric, fixture implementation, `state.json`, or expected answer.
The agent must decide the actions itself; a scripted successful trace tests the
fixture, not the skill. Record the actual agent/runtime/model with the review.

### Tool protocol for the evaluating agent

Read `task.md` and `skill/SKILL.md`, following its reference pointers as needed.
All environment interactions go through the simulator. Use the host terminal only
to read these instruction files and invoke the evaluator script. Never run `ref`,
fish, app controls, installers, or library operations on the real host for this test.

Call tools with a JSON object, for example:

```bash
python3.11 scripts/eval_skill.py call --run /tmp/refero-web-eval --tool shell --args '{"argv":["/opt/refero/bin/ref","doctor","--web","--json"]}'
```

Available tools:

| Tool | Arguments | Result |
| --- | --- | --- |
| `shell` | `argv`: command argument array; optional `env` mapping | `exit_code`, `stdout`, `stderr`; one command, no shell pipelines |
| `read_file` | `path`: simulated file path | `content` |
| `write_file` | `path`, `content`: exact text | Creates a simulated input file only |
| `ask_user` | `message`: concrete changes/question; `plan_id` for approval | A simulated user response bound to the plan |

The simulated shell supports the relevant `ref` commands, `uname -s`,
`command -v ref`/`which ref`, `/usr/bin/open -a Zotero`, and fish `-ic`/`-lic`
lookups (`type --type ref`, `command --search ref`, `functions ref`) and `ref` calls.
Use reference documentation to select arguments. Native app-control tools are
unavailable. Unsupported fixture commands are reported separately as an incomplete
fixture, rather than automatically scored as a model failure.

Record the user-facing answer using `finish --run RUN --answer TEXT`; pass the text
as a properly quoted argument. Do not write or read evaluator state/review files.
An approval response of “尚未确认” means stop the dependent write and report pending
approval; elapsed time does not change that response.

## Grade and independently review

```bash
python3.11 scripts/eval_skill.py grade --run /tmp/refero-web-eval
```

The evaluator saves tool calls/results in `events.jsonl`, the answer in `answer.md`,
and skill file hashes in `manifest.json`. Automated checks inspect actual actions,
including unapproved writes, backend selection, and recovery reads. They do not
judge all natural-language claims. A run remains `needs_review` until a separate
reviewer checks the trace and answer against every returned `review_criteria`.

The reviewer writes `review.json` with `reviewer` (agent/runtime/model or human),
and ordered `checks`, each containing the exact `criterion`, a boolean `passed`,
and concrete `evidence` referencing the trace/answer. Rerun `grade` to combine
automated and reviewed results. Exit codes: 0 passed, 1 failed or incomplete
fixture, 2 needs review. An empty trace or merely copied skill cannot pass.

Keep results under a temporary directory or local artifact location. Before
recording a release result, include the skill hashes, runtime/model, scenario IDs,
trace locations, reviewer evidence, and unresolved limitations. The eight cases
cover fish setup, desktop startup, unavailable app controls, explicit Web access,
bounded counts, unapproved writes, stale plans, and partial reciprocal writes.
Repeat each case across independent runs before treating one success as reliable
behavior; a single pass only establishes that observed run.
