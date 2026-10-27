"""Run a reviewed command on a fresh copy of the submission."""
from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .errors import ReviewError
from .io import confined, read_json, sha, write_json
from .results import reviewer_id

ROOT_MARKERS = ("review.html", "repair.md")


def execute(run: Path, plan_path: Path, state: dict) -> dict:
    if any((run / name).exists() for name in ROOT_MARKERS):
        raise ReviewError("正式报告已生成，不能再补做执行")
    plan = read_json(plan_path)
    if not isinstance(plan, dict) or plan.get("schema") != "agent-review.execution-plan.v1":
        raise ReviewError("执行计划须为 agent-review.execution-plan.v1")
    if plan.get("authorization") != "reviewed-host-copy":
        raise ReviewError("只有已审阅并明确授权的宿主副本执行才会启动")
    reviewer = reviewer_id(plan.get("reviewer_id"))
    reason = plan.get("review_reason")
    if not isinstance(reason, str) or len(reason.strip()) < 8:
        raise ReviewError("review_reason 需要说明已查看的入口、读写范围和预期行为")
    command = plan.get("command")
    if not isinstance(command, list) or not command or any(not isinstance(item, str) or "\n" in item for item in command):
        raise ReviewError("command 必须是参数数组，不经 shell 展开")
    timeout = plan.get("timeout_seconds", 60)
    repeats = plan.get("repeats", 1)
    if not isinstance(timeout, int) or not 1 <= timeout <= 3600:
        raise ReviewError("timeout_seconds 须在 1 到 3600 之间")
    if not isinstance(repeats, int) or not 1 <= repeats <= 5:
        raise ReviewError("repeats 须在 1 到 5 之间")
    case = next((item for item in state["cases"] if item["key"] == plan.get("case_key", state["cases"][0]["key"])), None)
    if case is None:
        raise ReviewError("执行计划中的 case_key 不在本次运行内")
    result_file = plan.get("result_file")
    if result_file is not None and not isinstance(result_file, str):
        raise ReviewError("result_file 必须是副本内的相对路径")
    execution_id = datetime.now(timezone.utc).strftime("%H%M%S") + "-" + uuid4().hex[:8]
    folder = run / "_internal" / "executions" / execution_id
    source = Path(case["submission"]["path"])
    attempts = []
    for number in range(1, repeats + 1):
        workspace = folder / f"repeat-{number}"
        if workspace.exists():
            shutil.rmtree(workspace)
        shutil.copytree(source, workspace)
        if result_file:
            previous = confined(workspace, result_file)
            if previous.exists():
                previous.unlink()
        try:
            completed = subprocess.run(command, cwd=workspace, timeout=timeout, capture_output=True, shell=False, check=False)
            log = completed.stdout + b"\n" + completed.stderr
            exit_code, timed_out = completed.returncode, False
        except subprocess.TimeoutExpired as exc:
            log = (exc.stdout or b"") + b"\n" + (exc.stderr or b"")
            exit_code, timed_out = None, True
        except OSError as exc:
            raise ReviewError(f"无法启动命令: {exc}") from exc
        log_path = folder / f"repeat-{number}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_bytes(log)
        result_sha = None
        if result_file:
            produced = confined(workspace, result_file)
            if produced.is_file():
                result_sha = sha(produced)
        attempts.append({"repeat": number, "exit_code": exit_code, "timed_out": timed_out,
                         "log": log_path.relative_to(run).as_posix(), "result_sha256": result_sha})
    receipt = {
        "schema": "agent-review.execution-receipt.v1",
        "execution_id": execution_id,
        "isolation": "host-copy",
        "sandbox": False,
        "note": "这是已审阅命令在交付副本上的宿主执行，不是操作系统沙箱。",
        "reviewer_id": reviewer,
        "review_reason": reason.strip(),
        "case_key": case["key"],
        "command": command,
        "repeats": attempts,
    }
    write_json(folder / "receipt.json", receipt)
    return receipt
