#!/usr/bin/env python3
"""
json2flow_iso_perfect_hybrid.py
Final production-ready hybrid ISO flowchart generator.

- NO argparse
- NO __main__ section
- Fully import-safe for FastAPI servers
- Exposes one clean API function:
      generate_flowchart_from_json_and_save(flow_json, out_path)

Features:
 - Smart auto-mode (A <= 8 steps else B)
 - Non-linear NO branches using constraint=false + minlen
 - Clean ISO shapes
 - Zero empty boxes (invisible join points)
 - Balanced, non-linear layout
"""

import json
import textwrap
from pathlib import Path
from graphviz import Digraph


# ---------------- utilities ----------------

def wrap_label(s: str, width=28, max_chars=200):
    if s is None:
        return " "
    s = " ".join(str(s).split())
    if len(s) > max_chars:
        s = s[:max_chars-3] + "..."
    lines = textwrap.wrap(s, width=width)
    return "\n".join(lines) if lines else " "


def is_meaningful(s):
    return bool(s and str(s).strip())


# ---------------- counting utility ----------------

def count_meaningful_steps(flow_obj):
    if not isinstance(flow_obj, dict):
        return 0
    steps = flow_obj.get("steps") or []

    def rec_list(lst):
        c = 0
        for it in lst:
            if not isinstance(it, dict) or "type" not in it:
                continue
            t = it["type"].lower()
            c += 1
            if t in ("decision", "loop", "subflow"):
                c += rec_list(it.get("yes") or [])
                c += rec_list(it.get("no") or [])
                c += rec_list(it.get("body") or it.get("steps") or [])
        return c

    return rec_list(steps)


# ---------------- node ID generator ----------------

class IDGen:
    def __init__(self):
        self.i = 0

    def next(self):
        self.i += 1
        return f"n{self.i}"


# ---------------- FlowBuilder ----------------

class FlowBuilder:
    def __init__(self, flow_obj):
        self.flow = flow_obj
        self.idg = IDGen()
        self.nodes = {}   # id -> (label, shape, attrs)
        self.edges = []   # (src, dst, label, opts)

    def add_node(self, label, shape="rectangle", attrs=None):
        nid = self.idg.next()
        self.nodes[nid] = (label or " ", shape, dict(attrs or {}))
        return nid

    def add_edge(self, a, b, label=None, opts=None):
        if a and b:
            self.edges.append((a, b, label, opts or {}))

    def build(self):
        steps = self.flow.get("steps", [])

        def build_seq(seq):
            entry = None
            prev = None
            for st in seq:
                e, exits = build_step(st)
                if e:
                    if prev:
                        for p in prev:
                            self.add_edge(p, e)
                    elif entry is None:
                        entry = e
                    prev = exits
                else:
                    prev = exits or prev
                    if entry is None and prev:
                        entry = prev[0]
            return entry, prev or []

        def build_step(st):
            if not isinstance(st, dict) or "type" not in st:
                return None, []
            t = st["type"].lower()

            # START
            if t == "start":
                n = self.add_node("START", "oval")
                return n, [n]

            # END
            if t == "end":
                n = self.add_node("STOP", "oval")
                return n, [n]

            # INPUT/OUTPUT
            if t in ("input", "output"):
                text = st.get("text") or st.get("label") or ""
                var = st.get("var")
                label = f"{text} -> {var}" if var else text
                if not is_meaningful(label):
                    return None, []
                n = self.add_node(wrap_label(label), "parallelogram")
                return n, [n]

            # PROCESS
            if t in ("process", "assign", "proc"):
                txt = st.get("text") or st.get("expr") or st.get("label") or ""
                if not is_meaningful(txt):
                    return None, []
                n = self.add_node(wrap_label(txt), "rectangle")
                return n, [n]

            # FUNCTION CALL
            if t == "call":
                txt = st.get("text") or ""
                if not is_meaningful(txt):
                    return None, []
                n = self.add_node(wrap_label(txt), "rectangle", {"peripheries": "2"})
                return n, [n]

            # DECISION (non-linear routing)
            if t == "decision":
                cond_raw = st.get("cond") or st.get("label") or "condition"
                cond = wrap_label(f"if ({cond_raw})")
                dec = self.add_node(cond, "diamond", {"fixedsize": "true"})

                yes = st.get("yes") or []
                no = st.get("no") or []

                yes_entry, yes_out = build_seq(yes)
                no_entry, no_out = build_seq(no)

                # YES branch
                if yes_entry:
                    self.add_edge(dec, yes_entry, "Yes")
                elif yes_out:
                    for y in yes_out:
                        self.add_edge(dec, y, "Yes")

                # NO branch (sideways)
                if no_entry:
                    self.add_edge(dec, no_entry, "No", {"constraint": "false", "minlen": "2"})
                elif no_out:
                    for n in no_out:
                        self.add_edge(dec, n, "No", {"constraint": "false", "minlen": "2"})

                # join
                join = self.add_node("", "point", {"style": "invis", "width": "0", "height": "0"})
                for y in (yes_out or []):
                    self.add_edge(y, join)
                for n in (no_out or []):
                    self.add_edge(n, join)

                return dec, [join]

            # LOOP
            if t == "loop":
                cond_raw = st.get("cond") or st.get("label") or "loop"
                cond = wrap_label(f"loop ({cond_raw})")
                dec = self.add_node(cond, "diamond", {"fixedsize": "true"})

                body = st.get("body") or st.get("steps") or []
                if body:
                    b_entry, b_outs = build_seq(body)
                    if b_entry:
                        self.add_edge(dec, b_entry, "Yes")
                    for b in (b_outs or []):
                        self.add_edge(b, dec)
                else:
                    self.add_edge(dec, dec, "Yes")

                after = self.add_node("", "point", {"style": "invis"})
                self.add_edge(dec, after, "No")
                return dec, [after]

            # SUBFLOW
            if t == "subflow":
                inner = st.get("steps") or []
                if not inner:
                    return None, []
                return build_seq(inner)

            # fallback
            txt = st.get("text") or st.get("label") or str(st)
            n = self.add_node(wrap_label(txt), "rectangle")
            return n, [n]

        entry, exits = build_seq(steps)

        if entry is None:
            s = self.add_node("START", "oval")
            t = self.add_node("STOP", "oval")
            self.add_edge(s, t)
            return

        if not any(self.nodes[e][0].upper() == "STOP" for e in exits):
            stop = self.add_node("STOP", "oval")
            for e in exits:
                self.add_edge(e, stop)


# ---------------- Renderer ----------------

class FlowRendererPerfect:
    def __init__(self, flow_obj, outdir: Path, mode=None, formats=("png",)):
        self.flow = flow_obj
        self.outdir = Path(outdir)
        self.mode = mode
        self.formats = formats

    def render(self):
        cnt = count_meaningful_steps(self.flow)
        mode = self.mode or ("A" if cnt <= 8 else "B")

        fb = FlowBuilder(self.flow)
        fb.build()

        # Mode settings
        if mode == "A":
            nodesep = "0.12"
            ranksep = "0.18"
            fontsize = "10"
            label_distance = "1.0"
            arrow_size = "0.85"
            diamond_hw = {"width": "1.0", "height": "0.7"}
        else:
            nodesep = "0.30"
            ranksep = "0.45"
            fontsize = "11"
            label_distance = "1.4"
            arrow_size = "0.95"
            diamond_hw = {"width": "1.8", "height": "1.1"}

        name = (self.flow.get("name") or "flow").replace(" ", "_")[:80]

        for fmt in self.formats:
            dot = Digraph(name, format=fmt)
            dot.attr(rankdir="TB", splines="ortho", nodesep=nodesep, ranksep=ranksep)

            # render nodes
            for nid, (label, shape, attrs) in fb.nodes.items():
                attrs = dict(attrs or {})

                # invisible
                if attrs.get("style") == "invis":
                    dot.node(nid, "", shape="point", style="invis", width="0", height="0")
                    continue

                # shape mapping
                if shape == "oval":
                    node_shape = "ellipse"
                elif shape == "diamond":
                    node_shape = "diamond"
                    attrs.setdefault("fixedsize", "true")
                    attrs.setdefault("width", diamond_hw["width"])
                    attrs.setdefault("height", diamond_hw["height"])
                elif shape == "parallelogram":
                    node_shape = "parallelogram"
                    attrs.setdefault("skew", "0.25" if mode == "B" else "0.15")
                else:
                    node_shape = "rectangle"

                node_attrs = {
                    "shape": node_shape,
                    "style": "filled",
                    "fillcolor": "white",
                    "color": "black",
                    "penwidth": "1.3",
                    "fontname": "Helvetica",
                    "fontsize": fontsize,
                    "margin": "0.08",
                }
                node_attrs.update(attrs)

                lab = label if isinstance(label, str) else str(label)
                dot.node(nid, lab, **node_attrs)

            # render edges
            for a, b, lbl, opts in fb.edges:
                attrs = {
                    "arrowhead": "normal",
                    "arrowsize": arrow_size,
                    "fontname": "Helvetica-Bold",
                    "fontsize": fontsize,
                }

                for k, v in opts.items():
                    attrs[k] = v

                if lbl:
                    attrs["label"] = lbl
                    attrs["labeldistance"] = label_distance

                dot.edge(a, b, **attrs)

            outpath = self.outdir / f"flow_{name}"
            dot.render(str(outpath), cleanup=True)


# ---------------- JSON Validation ----------------

def validate_json(doc):
    if not isinstance(doc, dict):
        raise ValueError("JSON root must be object")
    flows = doc.get("flows")
    if not isinstance(flows, list):
        raise ValueError("'flows' must be a list")
    for f in flows:
        if "steps" not in f or not isinstance(f["steps"], list):
            raise ValueError("Each flow must contain 'steps' list")


# ---------------------------------------------------------------------------
# PUBLIC SERVER API FUNCTION
# ---------------------------------------------------------------------------

def generate_flowchart_from_json_and_save(flow_json: dict, out_path: Path) -> bool:
    """
    FastAPI server will call this.

    Args:
        flow_json: The JSON containing {"flows":[ {...} ]}
        out_path:  Full path where qsX.png should be saved.

    Returns:
        True if saved successfully, else False.
    """
    try:
        validate_json(flow_json)

        flow_obj = flow_json["flows"][0]

        renderer = FlowRendererPerfect(
            flow_obj=flow_obj,
            outdir=out_path.parent,
            mode=None,
            formats=("png",)
        )

        renderer.render()

        name = (flow_obj.get("name") or "flow").replace(" ", "_")
        generated = out_path.parent / f"flow_{name}.png"

        if generated.exists():
            generated.replace(out_path)
            return True

        return False

    except Exception as e:
        print("Flowchart engine error:", e)
        return False
