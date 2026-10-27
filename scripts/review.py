#!/usr/bin/env python3
"""Agent review command line. A zero exit means the command finished, not that the delivery passed."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_review.demo import create_demo
from agent_review.engine import execute, finalize, load_batch, prepare, status, virtual_prepare, virtual_execute, virtual_collect
from agent_review.errors import ReviewError, ReviewIncomplete


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Agent Review — 按官方原文审查交付内容")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="冻结依据和交付，不生成正式报告")
    run.add_argument("--submission", type=Path)
    run.add_argument("--standards", type=Path)
    run.add_argument("--original", type=Path)
    run.add_argument("--batch", type=Path)
    run.add_argument("--out", type=Path)
    run.add_argument("--virtual-suite", type=Path)
    for name in ("virtual-prepare", "virtual-execute", "virtual-collect"):
        sub = commands.add_parser(name, help="虚拟测试集：冻结、执行或收集")
        sub.add_argument("--run", type=Path, required=True)
        if name == "virtual-execute":
            sub.add_argument("--plan", type=Path, required=True)
    view = commands.add_parser("status", help="查看进度，不生成正式报告")
    view.add_argument("--run", type=Path, required=True)
    done = commands.add_parser("finalize", help="校验完整判断并生成两份报告")
    done.add_argument("--run", type=Path, required=True)
    replay = commands.add_parser("execute", help="在交付副本上执行已审阅的计划")
    replay.add_argument("--run", type=Path, required=True)
    replay.add_argument("--plan", type=Path, required=True)
    demo = commands.add_parser("demo", help="在仓库外生成虚构材料")
    demo.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            print(create_demo(args.out))
        elif args.command == "run":
            if bool(args.batch) == bool(args.submission):
                raise ReviewError("使用 --submission 或 --batch 其中之一")
            if not args.standards:
                raise ReviewError("需要 --standards")
            if args.batch:
                if args.original:
                    raise ReviewError("--batch 与 --original 不能混用；原题写在批次清单里")
                pairs, scope = load_batch(args.batch.resolve())
                run_dir = prepare(pairs, args.standards, args.out, batch_path=args.batch.resolve(), scope=scope, virtual_suite=args.virtual_suite)
            else:
                run_dir = prepare([(args.original, args.submission)], args.standards, args.out, virtual_suite=args.virtual_suite)
            print(json.dumps({
                "run": str(run_dir), "phase": "agent_review_required", "review_completed": False,
                "message": "已冻结输入。请阅读 _internal/source-inventory.json，写入 catalog.json 和 results.json，然后 finalize。不要在这一步停止，也不要生成正式报告。",
            }, ensure_ascii=False))
        elif args.command == "status":
            print(json.dumps(status(args.run), ensure_ascii=False))
        elif args.command == "finalize":
            print(json.dumps(finalize(args.run), ensure_ascii=False))
        elif args.command in {"virtual-prepare", "virtual-execute", "virtual-collect"}:
            result = virtual_execute(args.run, args.plan) if args.command == "virtual-execute" else virtual_prepare(args.run) if args.command == "virtual-prepare" else virtual_collect(args.run)
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(json.dumps(execute(args.run, args.plan), ensure_ascii=False))
        return 0
    except ReviewIncomplete as exc:
        print(f"Agent Review: {exc}", file=sys.stderr)
        return 3
    except (ReviewError, OSError, KeyError, TypeError) as exc:
        print(f"Agent Review: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    sys.exit(main())
