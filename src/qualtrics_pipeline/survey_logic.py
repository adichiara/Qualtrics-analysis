"""Parse Qualtrics display logic into data-level predicates and evaluate them.

Qualtrics stores per-question ``DisplayLogic`` that gates whether a respondent
is shown the question. To report a correct base (denominator) for conditionally
displayed questions, we:

1. At export time, resolve each logic condition's ``ChoiceLocator`` (e.g.
   ``q://QID2/SelectableChoice/1``) into a concrete predicate against an export
   data column (e.g. ``Q1.5 == "1"``), using the survey's recode values and
   selector type. The result is a normalized boolean tree stored in
   ``display_logic.json`` so the frequency stage stays fully offline.
2. At analysis time, evaluate that normalized tree against each response row to
   count how many respondents were eligible to see the question.

The normalized tree uses these node shapes::

    {"type": "and"|"or", "operands": [ <node>, ... ]}
    {"type": "pred", "column": str, "op": "equals"|"not_equals", "value": str}
    {"type": "const", "value": bool}
    {"type": "unsupported", "detail": str}

A tree is "fully evaluable" when it contains no ``unsupported`` node.
"""

from __future__ import annotations

from typing import Any

MULTI_SELECTORS = {"MAVR", "MAHR", "MACOL", "MSB"}


# ---------------------------------------------------------------------------
# Export-time parsing (Qualtrics-specific)
# ---------------------------------------------------------------------------

def _resolve_choice_locator(
    locator: str, questions_meta: dict[str, Any]
) -> dict[str, Any] | None:
    """Resolve ``q://QID/SelectableChoice/<id>`` to (column, value, is_multi).

    Returns None when the locator cannot be resolved against the metadata.
    """
    if not locator or not locator.startswith("q://"):
        return None
    parts = locator.split("/")
    # ['q:', '', 'QID2', 'SelectableChoice', '1', ...]
    if len(parts) < 5 or parts[3] != "SelectableChoice":
        return None
    qid = parts[2]
    choice_id = parts[4]
    q = questions_meta.get(qid)
    if not q:
        return None
    tag = q.get("DataExportTag")
    if not tag:
        return None
    recodes = q.get("RecodeValues", {}) or {}
    recode = str(recodes.get(choice_id, choice_id))
    if q.get("Selector") in MULTI_SELECTORS:
        # Multi-answer: each choice is a binary column "<tag>_<recode>",
        # "1" when selected.
        return {"column": f"{tag}_{recode}", "value": "1"}
    # Single-answer: the question's column holds the recode value of the choice.
    return {"column": tag, "value": recode}


def _parse_condition(cond: dict[str, Any], questions_meta: dict[str, Any]) -> dict[str, Any]:
    """Normalize a single Qualtrics logic condition into a tree node."""
    logic_type = cond.get("LogicType")

    if logic_type == "BooleanValue":
        return {"type": "const", "value": str(cond.get("Value", "")).lower() == "true"}

    if logic_type == "Question":
        operator = cond.get("Operator")
        if operator not in {"Selected", "NotSelected"}:
            return {"type": "unsupported", "detail": f"Question operator '{operator}'"}
        resolved = _resolve_choice_locator(cond.get("ChoiceLocator", ""), questions_meta)
        if resolved is None:
            return {"type": "unsupported", "detail": f"Unresolvable locator '{cond.get('ChoiceLocator')}'"}
        op = "equals" if operator == "Selected" else "not_equals"
        return {"type": "pred", "column": resolved["column"], "op": op, "value": resolved["value"]}

    return {"type": "unsupported", "detail": f"LogicType '{logic_type}'"}


def _combine(items: list[tuple[dict[str, Any], str | None]]) -> dict[str, Any]:
    """Combine (node, conjunction) pairs with AND binding tighter than OR.

    ``conjunction`` is how each item joins to the previous one ("And"/"Or");
    the first item's conjunction is ignored. Produces an OR of AND-segments.
    """
    if not items:
        return {"type": "const", "value": True}
    # Split into OR-segments at each "Or" boundary; AND within a segment.
    segments: list[list[dict[str, Any]]] = [[items[0][0]]]
    for node, conj in items[1:]:
        if str(conj).lower() == "or":
            segments.append([node])
        else:  # default/And
            segments[-1].append(node)

    def _and(nodes: list[dict[str, Any]]) -> dict[str, Any]:
        return nodes[0] if len(nodes) == 1 else {"type": "and", "operands": nodes}

    and_nodes = [_and(seg) for seg in segments]
    return and_nodes[0] if len(and_nodes) == 1 else {"type": "or", "operands": and_nodes}


def parse_display_logic(
    display_logic: dict[str, Any], questions_meta: dict[str, Any]
) -> dict[str, Any]:
    """Parse a Qualtrics ``DisplayLogic`` object into a normalized tree."""
    group_items: list[tuple[dict[str, Any], str | None]] = []
    for gkey in sorted((k for k in display_logic if k.isdigit()), key=int):
        group = display_logic[gkey]
        cond_items: list[tuple[dict[str, Any], str | None]] = []
        for ckey in sorted((k for k in group if k.isdigit()), key=int):
            cond = group[ckey]
            # Qualtrics misspells the field as "Conjuction".
            conj = cond.get("Conjuction") or cond.get("Conjunction")
            cond_items.append((_parse_condition(cond, questions_meta), conj))
        group_node = _combine(cond_items)
        group_conj = group.get("Conjuction") or group.get("Conjunction")
        group_items.append((group_node, group_conj))
    return _combine(group_items)


def _has_unsupported(node: dict[str, Any]) -> bool:
    if node.get("type") == "unsupported":
        return True
    return any(_has_unsupported(child) for child in node.get("operands", []))


def build_display_logic_map(questions_meta: dict[str, Any]) -> dict[str, Any]:
    """Build {qid: {tree, fully_evaluable}} for all questions with display logic."""
    out: dict[str, Any] = {}
    for qid, q in questions_meta.items():
        dl = q.get("DisplayLogic")
        if not dl:
            continue
        tree = parse_display_logic(dl, questions_meta)
        out[qid] = {"tree": tree, "fully_evaluable": not _has_unsupported(tree)}
    return out


# ---------------------------------------------------------------------------
# Analysis-time evaluation (offline; operates on a response row)
# ---------------------------------------------------------------------------

def _is_blank(value: str | None) -> bool:
    if value is None:
        return True
    return str(value).strip() == ""


def evaluate(node: dict[str, Any], row: dict[str, str]) -> bool:
    """Evaluate a normalized logic tree against one response row."""
    ntype = node.get("type")
    if ntype == "and":
        return all(evaluate(child, row) for child in node["operands"])
    if ntype == "or":
        return any(evaluate(child, row) for child in node["operands"])
    if ntype == "const":
        return bool(node["value"])
    if ntype == "pred":
        actual = row.get(node["column"])
        actual = "" if _is_blank(actual) else str(actual).strip()
        if node["op"] == "equals":
            return actual == node["value"]
        return actual != node["value"]
    # unsupported nodes are not evaluable; treat as not satisfiable
    raise ValueError(f"Cannot evaluate unsupported logic node: {node.get('detail')}")
