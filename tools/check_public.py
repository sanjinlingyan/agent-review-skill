"""Publication check. It reports locations and never prints secret values."""
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOP_FILES = {"README.md", "AGENTS.md", "LICENSE", ".gitignore", ".gitattributes"}
DIRS = {"agent_review", "tests", "references", "skills", "scripts", "tools", ".github"}
EXTENSIONS = {".py", ".md", ".yml", ".yaml"}
PATTERNS = {
    "credential": re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9_-]{25,}|AKIA[A-Z0-9]{16}|-----BEGIN (?:RSA |OPENSSH )?PRIVATE KEY-----)"),
    "personal_absolute_path": re.compile(r"(?:[A-Za-z]:[\\/]+Users[\\/]+[^\s/\\\"']+|/(?:Users|home)/[A-Za-z0-9_.-]+/)"),
    "private_document_link": re.compile(r"https?://(?:[^/]+\.)?(?:dingtalk\.com|feishu\.cn|larksuite\.com)/[^\s\"']+"),
    "possible_real_task_id": re.compile(r"\b[A-Z]{3}-\d{2}\b"),
    "source_uuid": re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I),
}


def scan(staged=True):
    args = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"] if staged else ["git", "ls-files", "-z"]
    names = subprocess.check_output(args, cwd=ROOT).decode("utf-8").split("\0")
    problems = []
    for name in filter(None, names):
        path = Path(name)
        if name not in TOP_FILES and (not path.parts or path.parts[0] not in DIRS or path.suffix not in EXTENSIONS):
            problems.append(f"{name}: 文件不在公开允许清单")
            continue
        if staged:
            content = subprocess.check_output(["git", "show", ":" + name], cwd=ROOT).decode("utf-8")
        else:
            content = (ROOT / path).read_text(encoding="utf-8")
        for number, line in enumerate(content.splitlines(), 1):
            for label, pattern in PATTERNS.items():
                if pattern.search(line):
                    problems.append(f"{name}:{number}: {label}")
    return problems


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", action="store_true")
    mode.add_argument("--tracked", action="store_true")
    args = parser.parse_args()
    findings = scan(staged=args.staged)
    print("\n".join(findings) if findings else "公开文件检查通过；仍须人工核对完整 diff。")
    raise SystemExit(1 if findings else 0)
