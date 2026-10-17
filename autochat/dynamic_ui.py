"""Shared Tk widgets for the dynamic analysis card."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk


STAGE_LABELS = {"pending": "等待", "running": "进行中", "complete": "完成",
                "failed": "失败", "cancelled": "已取消"}
EVIDENCE_LABELS = {"sufficient": "材料较充分", "insufficient": "缺少关键材料",
                   "conflicting": "原文有冲突", "unjudged": "Jev 未判断"}


def render_dynamic_card(parent: tk.Misc, output: dict, *, wraplength: int = 720,
                        summary_limit: int | None = None) -> None:
    parent = ttk.LabelFrame(parent, text="Jev 判断卡片 · 可能潜台词", padding=8)
    parent.pack(fill="x", pady=(4, 6))
    ttk.Label(parent, text="DS 仅提出待判断假设并撰写措辞；潜台词和回复方向由 Jev 判断，DS 不输出分析或审核结论。",
              wraplength=wraplength, foreground="#666666").pack(anchor="w", pady=(0, 3))
    meta = output.get("dynamic_analysis", {})
    stages = meta.get("stages", {})
    status = " · ".join(f"{name}: {STAGE_LABELS.get(stages.get(key, {}).get('status'), '未知')}"
                        for key, name in (("propose", "DS 提问"), ("judge", "Jev 判断"), ("reply", "DS 写措辞")))
    ttk.Label(parent, text=status, wraplength=wraplength).pack(anchor="w", pady=(4, 2))
    if meta.get("fallback_reason"):
        ttk.Label(parent, text="降级/失败：" + meta["fallback_reason"], foreground="#a3332b",
                  wraplength=wraplength).pack(anchor="w")
    if stages.get("reply", {}).get("status") == "failed":
        ttk.Label(parent, text="回复生成失败；已完成的分析仍可查看。", foreground="#a3332b").pack(anchor="w")
    tension = (output.get("jev") or {}).get("answers", {}).get("relationship_tension")
    direction = output.get("jev_reply_direction")
    if direction:
        ttk.Label(parent, text=f"Jev 选定回复方向：{direction['choice']}（置信度 {direction['confidence']:.2f}）",
                  wraplength=wraplength).pack(anchor="w")
    if tension:
        context_note = "；材料仍有歧义/缺失，需人工核对" if meta.get("relationship_tension_context_status") == "needs_review" else ""
        ttk.Label(parent, text=f"关系紧张量表：{tension['score']:.2f} / 4；置信度 {tension['confidence']:.2f}（非生气概率）{context_note}",
                  wraplength=wraplength).pack(anchor="w")
    rows = output.get("subtext_hypotheses", [])
    if not rows:
        ttk.Label(parent, text="当前没有通过引用校验的可能潜台词。", foreground="#666666").pack(anchor="w")
    visible_rows = rows if summary_limit is None else rows[:summary_limit]
    for row in visible_rows:
        frame = ttk.LabelFrame(parent, text=f"可能潜台词 · {row['id']} · DS 提出", padding=5)
        frame.pack(fill="x", pady=3)
        ttk.Label(frame, text=row["hypothesis"], wraplength=wraplength, justify="left").pack(anchor="w")
        probability = row.get("support_probability")
        score = "未判断" if probability is None else f"{probability * 100:.1f}%"
        evidence = EVIDENCE_LABELS.get(row.get("evidence_status"), "未知")
        ttk.Label(frame, text=f"Jev 原文支持度：{score} · 证据状态：{evidence}",
                  foreground="#9a5b1b" if row.get("status") != "judged" else "#225b38").pack(anchor="w")
        expanded = tk.BooleanVar(value=False)
        details = ttk.Frame(frame)
        for ref in row.get("evidence_refs", []):
            ttk.Label(details, text=f"原文引用 {ref['source_type']} {ref['source_id']}：{ref['quote']}",
                      wraplength=wraplength, justify="left").pack(anchor="w")
        for item in row.get("missing_context", []):
            ttk.Label(details, text=f"待核实：{item}", wraplength=wraplength,
                      justify="left").pack(anchor="w")
        if row.get("evidence_probabilities"):
            probs = "、".join(f"{EVIDENCE_LABELS.get(k, k)} {v * 100:.1f}%"
                             for k, v in row["evidence_probabilities"].items())
            ttk.Label(details, text=f"证据分布：{probs}；置信度 {row['evidence_confidence']:.2f}",
                      wraplength=wraplength).pack(anchor="w")

        def toggle(widget=details, flag=expanded):
            if flag.get():
                widget.pack_forget()
                flag.set(False)
            else:
                widget.pack(fill="x")
                flag.set(True)

        ttk.Button(frame, text="查看引用与缺失信息", command=toggle).pack(anchor="w")
    if summary_limit is not None and len(rows) > summary_limit:
        extra = ttk.Frame(parent)
        for row in rows[summary_limit:]:
            frame = ttk.LabelFrame(extra, text=f"可能潜台词 · {row['id']} · DS 提出", padding=5)
            frame.pack(fill="x", pady=3)
            ttk.Label(frame, text=row["hypothesis"], wraplength=wraplength).pack(anchor="w")
            probability = row.get("support_probability")
            score = "未判断" if probability is None else f"{probability * 100:.1f}%"
            ttk.Label(frame, text=f"Jev 原文支持度：{score} · 证据状态：{EVIDENCE_LABELS.get(row.get('evidence_status'), '未知')}",
                      wraplength=wraplength).pack(anchor="w")
            for ref in row.get("evidence_refs", []):
                ttk.Label(frame, text=f"原文引用 {ref['source_type']} {ref['source_id']}：{ref['quote']}",
                          wraplength=wraplength).pack(anchor="w")
            for item in row.get("missing_context", []):
                ttk.Label(frame, text=f"待核实：{item}", wraplength=wraplength).pack(anchor="w")
        def toggle_extra() -> None:
            if extra.winfo_manager():
                extra.pack_forget()
            else:
                extra.pack(fill="x")
        ttk.Button(parent, text=f"查看其余 {len(rows) - summary_limit} 条", command=toggle_extra).pack(anchor="w")
    ttk.Label(parent, text="支持度是 Jev 对原文支持假设的概率判断，未经中文聊天校准；假设不等于对方真实心理。",
              foreground="#666666", wraplength=wraplength).pack(anchor="w", pady=(3, 0))
