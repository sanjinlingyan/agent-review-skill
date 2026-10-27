"""Formal reports. The reading order follows Review Gate's chapter report."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .results import ORIGINS, REPAIR_OWNERS
from .virtual import WARNING, CHECKLIST

WORDS = {"pass": "通过", "fail": "未通过", "no_applicable": "无适用项"}
CSS = """
*{box-sizing:border-box}body{margin:0;overflow-wrap:anywhere;background:#f3f5f7;color:#22313d;font:16px/1.65 system-ui,'Microsoft YaHei',sans-serif}
main{max-width:1160px;margin:auto;padding:28px 24px 80px}header,.panel,.document{background:white;padding:28px;border:1px solid #dce3e9;border-radius:14px;margin-bottom:22px}
h1{font-size:34px;margin:8px 0}h2{font-size:23px}h3{font-size:19px}h4{font-size:17px;margin:8px 0}a{color:#176a81}
.fail{color:#b22e25}.pass{color:#16724d}.no_applicable{color:#526571}.meta,.citation{font-size:13px;color:#526571}
.stats{font-size:13px;background:#edf2f5;padding:8px 12px;border-radius:6px}.chapter{margin-top:24px}
.finding{scroll-margin-top:110px;border:1px solid #dce3e9;border-radius:8px;padding:18px;margin:14px 0}.finding p{margin:10px 0}
.status{font-weight:bold}details{margin:12px 0}summary{cursor:pointer;color:#176a81}
blockquote{margin:12px 0;padding:12px;background:#f6f8fa;font-size:13px}blockquote p{white-space:pre-wrap}
li{margin:10px 0}.shared{padding:12px;border-bottom:1px solid #e1e7ec}:target{outline:2px solid #2789a1;scroll-margin-top:110px}
.filter{position:sticky;top:0;background:#fff;padding:12px;border:1px solid #dce3e9;z-index:1}
body:has(#fail-only:checked) .pass-row,body:has(#fail-only:checked) .passed-group{display:none}
@media(max-width:650px){main{padding:12px}header,.panel,.document{padding:17px}.finding{padding:12px}h1{font-size:28px}}
@media print{.filter{display:none}.document{break-before:page}}
"""


def esc(value) -> str:
    return (str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def md(value) -> str:
    return str(value).replace("<", "&lt;").replace(">", "&gt;").replace("\r", "").replace("\n", " ")


def anchor(value: str) -> str:
    return "a-" + hashlib.sha256(value.encode()).hexdigest()[:20]


def summary(rows: list[dict]) -> str:
    total = len(rows)
    if total == 0:
        return "无适用项 · 不构成验收通过 · 零分母不显示为 100%"
    passed = sum(row["status"] == "pass" for row in rows)
    failed = total - passed
    label = "本题适用要求全部通过" if failed == 0 else "本题适用要求未全部通过"
    return (f"{label} · 已审 {total}/{total} · 审查完成率 100%"
            f" · 验收通过率 {passed / total:.1%} · 通过 {passed} / 未通过 {failed}")


def verdict_of(rows: list[dict]) -> str:
    if not rows:
        return "no_applicable"
    return "fail" if any(row["status"] == "fail" for row in rows) else "pass"


def _refs(row):
    values = []
    for place in row["places"]:
        value = f"{place['filename']} · {place['section']} · {place['locator']}"
        if value not in values:
            values.append(value)
    return values


def _target(entry, state):
    if entry["root"] == "standards":
        return Path(state["standards"]["path"]) / entry["path"]
    if entry["root"] == "run":
        return Path(state["run_path"]) / entry["path"]
    case = next(item for item in state["cases"] if item["key"] == entry["case_key"])
    return Path(case[entry["root"]]["path"]) / entry["path"]


def _evidence_link(entry, state):
    label = Path(entry["path"]).name + " · " + entry["locator"]
    if entry.get("missing"):
        return esc(label)
    target = _target(entry, state)
    href = target.resolve().as_uri()
    page = re.search(r"第\s*(\d+)\s*页", entry.get("locator", ""))
    if target.suffix.lower() == ".pdf" and page:
        href += "#page=" + page.group(1)
    return f'<a href="{esc(href)}">{esc(label)}</a>'


def _card(row, state, shown):
    aid = anchor(row["instance_id"])
    scope = " / ".join(filter(None, [row.get("task_id"), row.get("round")])) or "批次"
    css = "pass-row" if row["status"] == "pass" else "fail-row"
    if row["instance_id"] in shown:
        return (f'<div class="shared {css}"><span class="{row["status"]}">{WORDS[row["status"]]}</span> · {esc(scope)}'
                f' · <a href="#{aid}">{esc(row["title"])} · 查看共享判断</a></div>')
    shown.add(row["instance_id"])
    citations = _refs(row)
    refs = "<br>".join(esc(item) for item in citations[:2])
    extra = (f'<details class="additional-citations"><summary>其余 {len(citations) - 2} 处依据</summary>'
             + "<br>".join(esc(item) for item in citations[2:]) + "</details>") if len(citations) > 2 else ""
    primary = next((item for item in row["evidence"] if item["root"] != "standards" and not item["missing"]), row["evidence"][0])
    actions = "".join(f"<li>{esc(item['detail'])}</li>" for item in row["action_results"])
    evidence = "".join(f"<li>{_evidence_link(item, state)}（{esc(ORIGINS[item['origin']])}）</li>" for item in row["evidence"])
    excerpts = []
    seen_units = set()
    for place in row["places"]:
        if place["unit_id"] in seen_units:
            continue
        seen_units.add(place["unit_id"])
        excerpts.append(f'<blockquote><b>{esc(place["filename"])} · {esc(place["locator"])}</b><p>{esc(place["excerpt"])}</p></blockquote>')
    detail = (f'<details><summary>核查过程与完整证据</summary><ul>{actions}</ul>'
              f'<p><b>验收影响：</b>{esc(row["impact"])}</p><ul>{evidence}</ul></details>'
              f'<details><summary>相关原文依据</summary>{"".join(excerpts)}</details>')
    if row.get("virtual"):
        detail = f'<p><b>【虚拟测试集】</b>{esc(row["origin_class"])} · {esc(row["observed"])}</p>' + detail
    return (f'<article class="finding {css}" id="{aid}"><div class="status {row["status"]}">{WORDS[row["status"]]} · {esc(scope)}</div>'
            f'<h4>{esc(row["title"])}</h4><p><b>要求：</b>{esc(row["text"])}</p>'
            f'<p><b>实际发现：</b>{esc(row["summary"])}</p><p class="citation"><b>依据：</b>{refs}</p>{extra}'
            f'<p><b>关键证据：</b>{_evidence_link(primary, state)}（{esc(ORIGINS[primary["origin"]])}）</p>{detail}</article>')


def render_html(state, rows) -> str:
    hypothetical = bool(state.get("virtual_suite"))
    def stats(items):
        return summary(items).replace("验收通过率", "内部综合通过率") if any(i.get("virtual") for i in items) else summary(items)
    shown = set()
    nav, bodies = [], []
    for doc in state["document_order"]:
        associated = [row for row in rows if any(place["document_id"] == doc["id"] for place in row["places"])]
        if not associated:
            continue
        buckets, order = {}, []
        for row in associated:
            place = next(item for item in row["places"] if item["document_id"] == doc["id"])
            if place["section"] not in buckets:
                buckets[place["section"]] = []
                order.append(place["section"])
            buckets[place["section"]].append(row)
        chapters = []
        for title in order:
            items = buckets[title]
            group = "passed-group" if all(item["status"] == "pass" for item in items) else ""
            body = "".join(_card(item, state, shown) for item in items)
            chapters.append(f'<section class="chapter {group}"><h3>{esc(title)}</h3><p class="stats">{stats(items)}</p>{body}</section>')
        doc_group = "passed-group" if all(item["status"] == "pass" for item in associated) else ""
        note = " 原文由审查者抽取，脚本未核对与版面一致。" if doc.get("unverified") else ""
        bodies.append(
            f'<section class="document {doc_group}" id="{anchor(doc["id"])}"><h2>{esc(doc["title"])}</h2>'
            f'<p class="meta">版本：{esc(doc["version"])} · 范围：仅本题适用要求。{esc(note)}</p>'
            f'<p class="stats">{stats(associated)}</p>{"".join(chapters)}</section>'
        )
        nav.append(f'<li><a href="#{anchor(doc["id"])}">{esc(doc["title"])}</a><div class="stats">{stats(associated)}</div></li>')
    groups = {}
    for row in rows:
        if row["status"] == "fail":
            groups.setdefault(row["clause_id"], []).append(row)
    problems = []
    for group in list(groups.values())[:5]:
        links = "；".join(
            f'<a href="#{anchor(row["instance_id"])}">{esc(row.get("task_id") or "批次")} {esc(row.get("round") or "")}</a>'
            for row in group
        )
        problems.append(f'<li><b>{esc(group[0]["title"])}</b><p>{esc(group[0]["summary"])}</p>{links}</li>')
    if not problems:
        problems.append("<li>本次适用验收要求全部通过。</li>" if rows else "<li>没有适用验收项。零分母不记为通过。</li>")
    subjects = "；".join(f'{case["task_id"]} / {case.get("revision") or "冻结提交"}' for case in state["cases"]) or "批次范围"
    verdict = verdict_of(rows)
    diagnostics = state.get("virtual_diagnostics", [])
    diagnostic_html = ""
    if diagnostics:
        diagnostic_html = '<section class="panel"><h2>【虚拟测试集】诊断观察（不计成败分母）</h2>' + "".join(
            f'<article><h3>{esc(d["title"])}</h3><p>{esc(d["source_locator"])}</p><pre style="white-space:pre-wrap">{esc(d["values"])}</pre><p>{esc(d["reason"])}</p></article>' for d in diagnostics) + '</section>'
    title = "内部综合审查结论（含虚拟测试）" if hypothetical else "甲方验收报告"
    notice = ""
    if hypothetical:
        official = state["official_rows"]
        virtual_rows = [r for r in rows if r.get("virtual")]
        validation = "软件验证材料，不是真实交付。" if state["virtual_suite"].get("validation_only") else ""
        notice = (f'<aside style="border:3px solid #b35b00;padding:18px"><b>【虚拟测试集】{esc(WARNING)}</b>'
                  f'<p>{validation}固定回归样例不等于甲方真实隐藏测试集；未自动标为可直接外发。</p>'
                  f'<p><b>官方依据审查结论：{WORDS[verdict_of(official)]}</b> · {summary(official)}</p>'
                  f'<p><b>虚拟测试集：</b>{stats(virtual_rows)}</p><b>人工核实清单</b><ul>'
                  + "".join(f"<li>{esc(i)}</li>" for i in CHECKLIST) + '</ul></aside>')
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Agent Review · {title}</title><style>{CSS}</style></head><body><main>
<header>{notice}<p class="meta">AGENT REVIEW / {title}</p><h1 class="{verdict}">{title}：{WORDS[verdict]}</h1><p>{esc(subjects)}</p>
<p>{stats(rows)}</p><p>范围：本题适用的通用要求、逐题细则及有效分轮要求。无关题目、背景和建议不进入验收统计。</p>
<p>全部必要核查已完成。跨文件引用整体去重；验收通过率不是模型得分。本报告判断交付内容是否满足官方适用义务。</p>
<a href="repair.md">乙方 Agent 修复单</a><p class="meta">运行 {esc(state["run_id"])}</p></header>
<section class="panel"><h2>影响验收的主要问题</h2><ol>{"".join(problems)}</ol></section>
<nav class="panel"><h2>按文件与章节查看</h2><ol>{"".join(nav)}</ol></nav>
<div class="filter"><label><input type="radio" name="view" checked> 全部</label>　<label><input id="fail-only" type="radio" name="view"> 仅未通过</label><small>　统计始终对应全部适用要求</small></div>
{"".join(bodies)}{diagnostic_html}</main></body></html>'''


def render_repair(state, rows) -> str:
    verdict = verdict_of(rows)
    lines = ["# 乙方 Agent 修复单", "", f"**甲方验收结论：{WORDS[verdict]}**", "",
             f"运行：{state['run_id']}；全部适用验收项已完成审查。", "",
             "按实际责任修复；保留原题和历史记录，不代签、不补造历史。", ""]
    if state.get("virtual_suite"):
        lines[2] = f"**内部综合审查结论（含虚拟测试）：{WORDS[verdict]}**"
        official = state["official_rows"]
        lines[0:0] = [f"> 【虚拟测试集】{WARNING}", "> 固定回归不等于甲方隐藏测试集；综合通过不自动表示可直接外发。", ""]
        lines += [f"官方依据审查结论：{WORDS[verdict_of(official)]}；{summary(official)}", "",
                  "虚拟测试集：" + summary([r for r in rows if r.get("virtual")]).replace("验收通过率", "内部测试通过率"), "",
                  "人工核实清单：", "", *["- " + item for item in CHECKLIST], ""]
        if state["virtual_suite"].get("validation_only"):
            lines += ["**软件验证材料，不是真实交付。**", ""]
    found = False
    for row in rows:
        if row["status"] != "fail":
            continue
        found = True
        repair = row["repair"]
        lines += [f"## {md(row['instance_id'])} · {md(row['title'])}", "",
                  f"状态：未通过；责任：{({**REPAIR_OWNERS, 'reviewer': 'Agent Review 维护者'})[repair['owner']]}", "", "虚拟测试来源（含推测）：" if row.get("virtual") else "甲方依据：", ""]
        lines += ["- " + md(item) for item in _refs(row)]
        allowed = "、".join(repair["allowed_paths"]) or "由对应责任人补充或决定"
        for title, value in (("要求", row["text"]), ("实际发现", row["observed"]), ("缺陷", repair["problem"]), ("验收影响", row["impact"]),
                             ("位置", repair["location"]), ("行动", repair["action"]), ("完成条件", repair["done_when"]),
                             ("允许修改", allowed)):
            lines += ["", f"{title}：{md(value)}"]
        lines += ["", "交付证据：", ""]
        lines += ["- " + md(f"{item['root']}/{item['path']} · {item['locator']} · {ORIGINS[item['origin']]}") for item in row["evidence"]]
        lines.append("")
    if not found:
        lines += ["本次适用验收项没有未通过记录。", ""]
    if state.get("virtual_diagnostics"):
        lines += ["## 【虚拟测试集】诊断观察（不计成败分母，不补造修复项）", ""]
        for item in state["virtual_diagnostics"]:
            lines += [f"- {md(item['title'])}：{md(item['values'])}；{md(item['source_locator'])}", ""]
    lines += ["## 复验要求", "", "提交新版本，按同一适用条款重新审查；不能仅更新哈希、复制旧签收或手改审查结论。", ""]
    return "\n".join(lines)
