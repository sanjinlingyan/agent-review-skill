"""Fictional inputs for the review flow. These are not a real delivery."""
from __future__ import annotations

from pathlib import Path

from .engine import REPO
from .errors import ReviewError
from .io import write_json

GUIDE = """# 虚构样品验收说明

本文件只用于演示审查流程，不是真实甲方标准。

## 样品说明

交付需要一份说明，同时满足下面两项。

| 项目 | 要求 |
| 名称 | 写明样品名称 |
| 测量值 | 给出测量值并带单位 |

## 附图

建议附上一张示意图，便于阅读。示意图不是验收条件。

## 其他题目

其他编号样品的专用阈值不适用于本题。
"""

NOTES = "样品名称：演示翼片\n测量值：12\n"


def create_demo(out: Path) -> str:
    out = out.resolve()
    if out == REPO or REPO in out.parents or out in REPO.parents:
        raise ReviewError("演示材料必须放在仓库外")
    standards = out / "standards"
    submission = out / "submission"
    standards.mkdir(parents=True)
    (submission / "R1").mkdir(parents=True)
    (standards / "guide.md").write_text(GUIDE, encoding="utf-8")
    (submission / "R1" / "notes.md").write_text(NOTES, encoding="utf-8")
    write_json(submission / "record.json", {"id": "DEMO-001", "revision": "v1"})
    (out / "README.md").write_text("这些是虚构材料，只用于验证审查流程，不是真实交付或真实标准。\n", encoding="utf-8")
    return f"虚构材料已写到 {out}。standards 是依据，submission 是交付。下一步在仓库外运行 review.py run。"
