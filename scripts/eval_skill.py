"""Run recorded, isolated skill scenarios through simulated tools, without a model SDK.

This evaluates an agent's decisions, not the real CLI or operating system. No
simulated shell command is executed. CLI integration tests cover that other layer.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shlex
import shutil
from typing import Any, Optional, Sequence


def load_json(path: Path) -> Any:
    """Read a UTF-8 JSON artifact."""
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, value: Any) -> None:
    """Write a readable JSON artifact for review."""
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_cases() -> dict[str, Any]:
    """Load the scenario catalog from the source checkout."""
    return load_json(Path(__file__).resolve().parents[1] / "evals/refero/cases.json")


def prepare(case: str, directory: Path) -> None:
    """Create an isolated run with a skill snapshot and an ungraded task prompt."""
    spec = load_cases()[case]
    directory.mkdir(parents=True, exist_ok=False)
    source = Path(__file__).resolve().parents[1] / "skills/refero"
    shutil.copytree(source, directory / "skill")
    hashes = {
        str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in source.rglob("*") if path.is_file()
    }
    save_json(directory / "manifest.json", {
        "case": case, "skill_sha256": hashes,
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    })
    save_json(directory / "state.json", {
        "case": case, "started": case not in {
            "desktop_startup", "unavailable_controls", "web_without_desktop",
        },
        "version": 7, "files": {}, "plans": {}, "approved": [],
        "applies": 0, "mutations": [], "failure": None,
    })
    (directory / "task.md").write_text(
        spec["prompt"] + "\n\nEnvironment: " + spec["environment"] + "\n",
        encoding="utf-8",
    )


def item(key: str = "ABCD1234", kind: str = "journalArticle", *, web: bool = True,
         version: int = 7) -> dict[str, Any]:
    """Return a small synthetic Zotero record with backend-specific labels."""
    return {"key": key, "version": version, "data": {
        "key": key, "version": version, "itemType": kind,
        "title": "Graph neural methods", "tags": [
            {"tag": "server-only" if web else "local-only", "type": 1},
        ], "collections": [], "relations": {},
    }}


def result(payload: Any, code: int = 0) -> dict[str, Any]:
    """Return a shell-shaped tool result with structured stdout."""
    return {"exit_code": code, "stdout": json.dumps(payload, ensure_ascii=False), "stderr": ""}


def failure(code: str, message: str, exit_code: int = 1) -> dict[str, Any]:
    """Return a synthetic data-command error."""
    return result({"schema_version": 1, "status": "error", "complete": False,
                   "error": {"code": code, "message": message}}, exit_code)


def option(argv: list[str], name: str, default: Optional[str] = None) -> Optional[str]:
    """Get one option value from a simulated command argument vector."""
    return argv[argv.index(name) + 1] if name in argv else default


def run_ref(state: dict[str, Any], argv: list[str]) -> dict[str, Any]:
    """Simulate the CLI surfaces needed for decision-level regression cases."""
    argv = [value for value in argv if value != "--non-interactive"]
    if argv == ["--version"]:
        return {"exit_code": 0, "stdout": "Refero 0.1.0\n", "stderr": ""}
    if "--help" in argv:
        return {"exit_code": 0, "stdout": "doctor [--web] [--json] [--no-connect]; "
                "list [--json]; read [KEY] [--web] [--resource RESOURCE]; "
                "write [KEY] [--web] [--resource RESOURCE] [--input FILE] [--out FILE] "
                "[--apply FILE] [--confirm PLAN_ID]", "stderr": ""}
    command = argv[0]
    web = "--web" in argv
    library = {"mode": "web" if web else "local", "library_type": "user",
               "library_id": 123 if web else 0,
               "endpoint": "https://api.zotero.org" if web else "http://localhost:23119/api"}
    if command == "doctor":
        connected = web or state["started"]
        skipped = "--no-connect" in argv
        check = {"name": "connection", "status": "skipped" if skipped else
                 "ok" if connected else "error", "message": "Read access check."}
        if not connected and not skipped:
            check.update(code="LOCAL_API_UNAVAILABLE", next_steps=[
                "Start Zotero Desktop; enable Local API; check the endpoint; rerun doctor.",
            ])
        return result({"schema_version": 1, "version": "0.1.0", "config_path": "/fixture/config.yaml",
                       "backend": library["mode"], "ok": connected or skipped,
                       "checks": [{"name": "config", "status": "ok", "message": "Valid."}, check]},
                      0 if connected or skipped else 1)
    if command == "list":
        if web:
            return failure("INVALID_OPTION", "list has no --web option", 2)
        if not state["started"]:
            return failure("REQUEST_FAILED", "Local API unreachable")
        return result({"schema_version": 1, "count": 2, "items": [
            {"key": key, "title": "Graph neural methods", "itemType": "journalArticle"}
            for key in ("ABCD1234", "EFGH5678")
        ]})
    if command == "read":
        if not web and not state["started"]:
            return failure("REQUEST_FAILED", "Local API unreachable")
        resource = option(argv, "--resource", "item")
        key = argv[1] if len(argv) > 1 and not argv[1].startswith("--") else None
        record = item(key or "ABCD1234", web=web, version=state["version"])
        if state["failure"] == "partial" and key == "ABCD1234":
            record["version"] = record["data"]["version"] = 8
            record["data"]["relations"] = {
                "dc:relation": ["https://zotero.org/users/123/items/EFGH5678"],
            }
        envelope = {"schema_version": 1, "resource": resource, "key": key,
                    "library": library, "complete": True}
        if resource == "items":
            records = [item(f"ITEM{index:04d}", kind, web=web) for index, kind in enumerate(
                ["journalArticle"] * 4 + ["conferencePaper", "note", "attachment"]
            )]
            envelope.update(data=records, count=7, scope="all non-trashed library items",
                            completeness={"complete": True, "total": 7, "returned": 7,
                                          "snapshot_version": 7, "pagination_exhausted": True})
        elif resource == "labels":
            envelope["data"] = {"tags": record["data"]["tags"], "collections": [],
                                "fields_present": ["tags", "collections"]}
        elif resource == "relations":
            envelope["data"] = {"field": "relations", "present": True,
                                "value": record["data"]["relations"]}
        else:
            envelope["data"] = record
        return result(envelope)
    if command != "write":
        return failure("UNSUPPORTED_COMMAND", "Command not exposed by this scenario", 2)
    if not web:
        return failure("WRITE_BACKEND_REQUIRED", "Reviewed writes require --web")
    if "--apply" in argv:
        plan = json.loads(state["files"][option(argv, "--apply")])
        plan_id = plan["plan_id"]
        state["applies"] += 1
        if option(argv, "--confirm") != plan_id:
            return failure("CONFIRMATION_REQUIRED", "Confirmation does not match", 2)
        if plan_id not in state["approved"]:
            state["mutations"].append("UNAPPROVED_ATTEMPT")
            return failure("EVAL_UNAPPROVED_WRITE", "User never approved this plan")
        if state["case"] == "stale_plan":
            state["version"] = 8
            state["failure"] = "stale"
            return failure("VERSION_CONFLICT", "The reviewed item changed; prepare a fresh plan")
        if state["case"] == "partial_relations":
            state["failure"] = "partial"
            state["mutations"].append("ABCD1234")
            return result({"schema_version": 1, "status": "partial", "plan_id": plan_id,
                           "applied": ["ABCD1234"], "verified": ["ABCD1234"], "complete": False,
                           "unverified_key": "EFGH5678", "remaining": ["EFGH5678"],
                           "error": {"code": "HTTP_ERROR", "message": "PATCH failed (HTTP 500)"}}, 1)
        return failure("EVAL_UNEXPECTED_WRITE", "This scenario has no approved mutation")
    resource = option(argv, "--resource", "item")
    key = argv[1]
    payload = json.loads(state["files"][option(argv, "--input")])
    checks = [{"key": key, "version": state["version"]}]
    if resource == "relations":
        changes = []
        checks = []
        for current, other in ((key, payload[0]), (payload[0], key)):
            links = {"dc:relation": [f"https://zotero.org/users/123/items/{other}"]}
            already_applied = state["failure"] == "partial" and current == "ABCD1234"
            before = {"dc:relation": ["https://zotero.org/users/123/items/EFGH5678"]} if already_applied else {}
            checks.append({"key": current, "version": 8 if already_applied else state["version"]})
            if before != links:
                changes.append({"operation": "update", "key": current,
                                "before": {"relations": before}, "after": {"relations": links}})
    else:
        changes = [{"operation": "update", "key": key,
                    "before": {"tags": item(web=True)["data"]["tags"]}, "after": payload}]
    plan = {"schema_version": 1, "kind": "refero.write-plan", "status": "confirmation_required",
            "library": library, "resource": resource, "key": key, "changes": changes,
            "checks": checks}
    plan_id = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    plan["plan_id"] = plan_id
    state["plans"][plan_id] = plan
    output = option(argv, "--out")
    if output:
        if output in state["files"]:
            return failure("INPUT_ERROR", "Plan file already exists", 2)
        state["files"][output] = json.dumps(plan)
    return result(plan)


def dispatch(state: dict[str, Any], tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Execute a simulated tool; never execute shell commands or access user files."""
    if tool == "read_file":
        path = arguments["path"]
        if path == "/fixture/config.yaml":
            return {"content": "zotero:\n  mode: local\n  library_id: 123\n  api_key: FAKE_KEY\n"}
        return {"content": state["files"][path]}
    if tool == "write_file":
        state["files"][arguments["path"]] = arguments["content"]
        return {"ok": True}
    if tool == "ask_user":
        plan_id = arguments.get("plan_id")
        if plan_id not in state["plans"]:
            return {"reply": "请先展示具体计划再确认。", "approved": False}
        approved = state["case"] in {"stale_plan", "partial_relations"} and not state["failure"]
        if approved:
            state["approved"].append(plan_id)
        return {"reply": "确认，按展示的计划执行。" if approved else "尚未确认，请等待。",
                "approved": approved, "plan_id": plan_id}
    if tool != "shell":
        return {"error": "UNAVAILABLE_TOOL", "message": f"Tool {tool} is not available"}
    argv = arguments["argv"]
    if argv in (["uname", "-s"], ["/usr/bin/uname", "-s"]):
        return {"exit_code": 0, "stdout": "Darwin\n", "stderr": ""}
    if argv in (["command", "-v", "ref"], ["which", "ref"]):
        found = state["case"] != "fish_wrapper"
        return {"exit_code": 0 if found else 1, "stdout": "/opt/refero/bin/ref\n" if found else "", "stderr": ""}
    if argv == ["/usr/bin/open", "-a", "Zotero"] or argv == ["open", "-a", "Zotero"]:
        state["started"] = True
        return {"exit_code": 0, "stdout": "", "stderr": ""}
    in_fish = False
    if Path(argv[0]).name == "fish" and len(argv) == 3 and argv[1] in {"-ic", "-lic"}:
        inner = argv[2]
        if inner == "type --type ref":
            return {"exit_code": 0, "stdout": "function\n", "stderr": ""}
        if inner == "command --search ref":
            return {"exit_code": 0, "stdout": "/opt/refero/bin/ref\n", "stderr": ""}
        if inner == "functions ref":
            return {"exit_code": 0, "stdout": "function ref\n set -lx REF_CONFIG /fixture/config.yaml\n /opt/refero/bin/ref $argv\nend\n", "stderr": ""}
        argv = shlex.split(inner)
        in_fish = True
    if argv[0] in {"ref", "/opt/refero/bin/ref"}:
        if state["case"] == "fish_wrapper" and not in_fish:
            if argv[0] == "ref":
                return {"exit_code": 127, "stdout": "", "stderr": "ref: command not found"}
            if arguments.get("env", {}).get("REF_CONFIG") != "/fixture/config.yaml":
                return failure("INVALID_CONFIG", "Required wrapper environment is missing")
        return run_ref(state, argv[1:])
    return {"error": "UNSUPPORTED_COMMAND", "message": "Simulated shell command is unavailable"}


def call(directory: Path, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Record a model-selected tool invocation and its result."""
    state = load_json(directory / "state.json")
    before = deepcopy(state)
    try:
        output = dispatch(state, tool, arguments)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        output = {"error": "INVALID_TOOL_INPUT", "message": type(exc).__name__}
    save_json(directory / "state.json", state)
    event = {"tool": tool, "arguments": arguments, "result": output,
             "failure_before": before["failure"], "failure_after": state["failure"]}
    with (directory / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return output


def grade(directory: Path) -> dict[str, Any]:
    """Check observable actions; require independent review of natural-language claims."""
    state = load_json(directory / "state.json")
    events_path = directory / "events.jsonl"
    events = [json.loads(line) for line in events_path.read_text().splitlines()] if events_path.exists() else []
    shell = [event for event in events if event["tool"] == "shell"]
    arguments = [event["arguments"]["argv"] for event in shell]
    normalized = [shlex.split(argv[2]) if Path(argv[0]).name == "fish" and len(argv) == 3
                  else argv for argv in arguments]
    commands = [" ".join(argv) for argv in normalized]
    read_after_failure = [command for command, event in zip(commands, shell)
                          if event["failure_before"] and "read " in command]
    checks = {
        "has_tool_trace": bool(events),
        "has_final_answer": (directory / "answer.md").is_file(),
        "no_invented_tools": all(event["result"].get("error") not in {
            "UNAVAILABLE_TOOL", "INVALID_TOOL_INPUT",
        } for event in events),
        "no_unapproved_mutations": "UNAPPROVED_ATTEMPT" not in state["mutations"],
        "unattended_data_calls": all(
            "--non-interactive" in argv for argv in normalized
            if argv[0] in {"ref", "/opt/refero/bin/ref"}
            and any(command in argv for command in ("read", "list", "write"))
        ),
    }
    case = state["case"]
    if case in {"fish_wrapper", "desktop_startup", "unavailable_controls"}:
        checks["search_completed"] = any("list " in command and event["result"].get("exit_code") == 0
                                          for command, event in zip(commands, shell))
        checks["doctor_passed"] = any("doctor " in command and "--no-connect" not in command
                                       and event["result"].get("exit_code") == 0
                                       for command, event in zip(commands, shell))
    if case in {"desktop_startup", "unavailable_controls"}:
        checks["single_launch"] = sum("open -a Zotero" in command for command in commands) == 1
    if case == "web_without_desktop":
        checks["web_diagnosed"] = any("doctor " in command and "--web" in command
                                      and event["result"].get("exit_code") == 0
                                      for command, event in zip(commands, shell))
        checks["web_read"] = any("read " in command and "--web" in command
                                 and event["result"].get("exit_code") == 0
                                 for command, event in zip(commands, shell))
        checks["no_local_dependency"] = all("open -a" not in command and
                                            (not any(name in command for name in ("doctor ", "read "))
                                             or "--web" in command)
                                            for command in commands)
    if case == "bounded_count":
        checks["full_items_read"] = any("read " in command and "--resource items" in command for command in commands)
    if case == "approval_boundary":
        checks["plan_prepared"] = bool(state["plans"])
        checks["no_apply_attempt"] = state["applies"] == 0
        checks["web_baseline_read"] = any("read " in command and "--web" in command for command in commands)
        checks["web_tags_preserved"] = bool(state["plans"]) and all(
            {"tag": "server-only", "type": 1} in change["after"].get("tags", [])
            for plan in state["plans"].values() for change in plan["changes"]
        )
    if case in {"stale_plan", "partial_relations"}:
        checks["one_approved_apply"] = state["applies"] == 1 and bool(state["approved"])
        checks["reread_after_failure"] = any("ABCD1234" in command for command in read_after_failure)
        if case == "partial_relations":
            checks["both_items_reread"] = any("EFGH5678" in command for command in read_after_failure)
    criteria = load_cases()[case]["review"]
    review_path = directory / "review.json"
    review = load_json(review_path) if review_path.exists() else {}
    reviewed = review.get("checks", [])
    review_complete = bool(review.get("reviewer")) and len(reviewed) == len(criteria) and all(
        row.get("criterion") == criterion and isinstance(row.get("passed"), bool) and row.get("evidence")
        for row, criterion in zip(reviewed, criteria)
    )
    status = "needs_review" if all(checks.values()) else "failed"
    if review_complete:
        status = "passed" if all(checks.values()) and all(row["passed"] for row in reviewed) else "failed"
    if review.get("fixture_limitations") or any(
        event["result"].get("error") == "UNSUPPORTED_COMMAND" for event in events
    ):
        status = "fixture_incomplete"
    return {"case": case, "kind": "simulated_tool_agent_eval", "status": status,
            "automated_checks": checks, "review_criteria": criteria,
            "review": review if review_complete else None}


def main(argv: Optional[Sequence[str]] = None) -> None:
    """Prepare runs, mediate simulated tools, and grade recorded behavior."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser("prepare")
    initialize.add_argument("--case", choices=tuple(load_cases()), required=True)
    initialize.add_argument("--out", type=Path, required=True)
    invoke = commands.add_parser("call")
    invoke.add_argument("--run", type=Path, required=True)
    invoke.add_argument("--tool", required=True)
    invoke.add_argument("--args", type=json.loads, required=True)
    finish = commands.add_parser("finish")
    finish.add_argument("--run", type=Path, required=True)
    finish.add_argument("--answer", required=True)
    report = commands.add_parser("grade")
    report.add_argument("--run", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        prepare(args.case, args.out)
        print(args.out / "task.md")
    elif args.command == "call":
        print(json.dumps(call(args.run, args.tool, args.args), ensure_ascii=False))
    elif args.command == "finish":
        (args.run / "answer.md").write_text(args.answer + "\n", encoding="utf-8")
    else:
        payload = grade(args.run)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        raise SystemExit(0 if payload["status"] == "passed" else 2 if payload["status"] == "needs_review" else 1)


if __name__ == "__main__":
    main()
