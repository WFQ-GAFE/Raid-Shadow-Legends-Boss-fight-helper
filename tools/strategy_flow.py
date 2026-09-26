"""Bounded, offline-evaluable rule graphs. No game I/O belongs in this module."""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Callable

PORTS = {
    "rule": ("noMatch", "unavailable"), "condition": ("yes", "no"),
    "reserve": ("next",), "opener": ("next",), "adaptive": ("next",), "pause": (),
}
MAX_NODES = 80


def flow_revision(flow: dict) -> str:
    # Layout is not execution state. Moving a node must not invalidate a trace.
    execution = {"entry": flow.get("entry"), "nodes": {
        key: {k: v for k, v in node.items() if k not in {"x", "y"}}
        for key, node in flow.get("nodes", {}).items()}}
    return hashlib.sha256(json.dumps(execution, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def validate_flow(flow: Any, validate_rule: Callable) -> None:
    if not isinstance(flow, dict) or type(flow.get("version")) is not int or flow.get("version") != 1:
        raise ValueError("流程图格式无效（需要 version=1）")
    nodes = flow.get("nodes")
    if not isinstance(nodes, dict) or not 1 <= len(nodes) <= MAX_NODES:
        raise ValueError(f"流程图需要 1–{MAX_NODES} 个节点")
    if not isinstance(flow.get("entry"), str) or flow["entry"] not in nodes:
        raise ValueError("请选择有效的流程入口")
    for key, node in nodes.items():
        if not isinstance(key, str) or not key or len(key) > 100 or not isinstance(node, dict):
            raise ValueError("节点编号或内容无效")
        kind = node.get("type")
        if not isinstance(kind, str) or kind not in PORTS:
            raise ValueError(f"节点 {key} 类型不受支持")
        edges = node.get("edges", {})
        if not isinstance(edges, dict) or set(edges) != set(PORTS[kind]):
            raise ValueError(f"节点 {key} 的每个出口都必须连接；不继续的出口请连接等待节点")
        if any(not isinstance(target, str) or target not in nodes for target in edges.values()):
            raise ValueError(f"节点 {key} 连接了不存在的节点")
        if kind in {"rule", "condition", "reserve"}:
            rule = node.get("rule")
            if not isinstance(rule, dict) or rule.get("type", "rule") != "rule":
                raise ValueError(f"节点 {key} 需要有效规则")
            validate_rule(rule, path=f"strategyFlow.nodes.{key}.rule")
        if kind == "reserve":
            owner = nodes.get(node.get("owner")) if isinstance(node.get("owner"), str) else None
            skill = node["rule"].get("action", {}).get("skillTypeId")
            if (node["rule"].get("action", {}).get("type") not in {"cast", "transform"}
                    or not isinstance(skill, int) or isinstance(skill, bool) or not isinstance(owner, dict)
                    or owner.get("type") != "rule"
                    or owner.get("rule", {}).get("action", {}).get("skillTypeId") != skill):
                raise ValueError(f"保留节点 {key} 必须指定释放同一技能的动作节点")
    seen, active = set(), set()

    def visit(key: str) -> None:
        if key in active:
            raise ValueError("流程图存在循环；请连接备用动作或等待节点")
        if key in seen:
            return
        active.add(key)
        for target in nodes[key].get("edges", {}).values():
            visit(target)
        active.remove(key)
        seen.add(key)

    visit(flow["entry"])
    if len(seen) != len(nodes):
        raise ValueError("存在入口无法到达的节点，请连接或删除这些节点")


def convert_rules(config: dict) -> dict:
    """Preserve the list engine's phases, opener, reservations and adaptive tail."""
    if config.get("strategyTree"):
        raise ValueError("该策略已有旧式策略树，请保留原策略并在副本中创建流程")
    rules = copy.deepcopy(config.get("rules", []))
    if not isinstance(rules, list) or len(rules) + 3 > MAX_NODES:
        raise ValueError("规则过多，请先按英雄拆分策略组（最多 77 条规则）")
    phase = lambda rule: {"executeTrialRecipe": 1, "defaultSkillPriority": 2}.get(rule.get("action", {}).get("type"), 0)
    ordered = sorted(enumerate(rules), key=lambda item: phase(item[1]))
    nodes = {"opener": {"type": "opener", "name": "首回合技能", "edges": {"next": "adaptive"}, "x": 60, "y": 60}}
    previous = nodes["opener"]
    for index, (original, rule) in enumerate(ordered):
        key = f"rule-{original + 1}"
        for port in previous["edges"]:
            previous["edges"][port] = key
        nodes[key] = {"type": "rule", "name": rule.get("name") or f"规则 {original + 1}", "rule": rule,
                      "edges": {"noMatch": "adaptive", "unavailable": "adaptive"},
                      "x": 60 + (index % 3) * 320, "y": 270 + (index // 3) * 240}
        previous = nodes[key]
    y = 510 + (len(rules) // 3) * 240
    nodes["adaptive"] = {"type": "adaptive", "name": "自动试炼备用动作", "edges": {"next": "wait"}, "x": 60, "y": y}
    nodes["wait"] = {"type": "pause", "name": "等待手动处理", "edges": {}, "x": 380, "y": y}
    return {"version": 1, "entry": "opener", "nodes": nodes}


def evaluate_flow(config: dict, state: dict, capability_memory=None, planner_state=None, engine=None):
    # Delayed import avoids a cycle while reusing exactly the existing legal-action evaluator.
    import chimera_controller as c
    c = engine or c
    flow = config["strategyFlow"]
    validate_flow(flow, c.validate_strategy_node)
    nodes = flow["nodes"]
    rules = [node["rule"] for node in nodes.values() if node["type"] == "rule"]
    owners = c.trial_rule_skill_owners(rules, state)
    local = {**state, "_trialSkillOwners": owners}
    reserved = c.strict_rule_reserved_skill_ids(rules, local) | set(owners)
    state["_reservedStrictSkillTypeIds"] = sorted(reserved)
    local["_decisionTrace"] = state.setdefault("_decisionTrace", [])
    state["_flowTrace"] = trace = []
    state["_flowRevision"] = flow_revision(flow)
    preferred = c.default_combat_skill_priority(rules, local)
    automatic = c.objective_trial_ids(config, local)
    key = flow["entry"]
    matched_trial = None
    for _ in range(MAX_NODES):
        node = nodes[key]
        kind = node["type"]
        rule = node.get("rule", {})
        name = node.get("name") or rule.get("name") or key
        decision = None
        trace_start = len(local["_decisionTrace"])
        matched = any(c.matches(rule.get("when", {}), candidate)
                      for candidate in c.states_for_rule_targets(local, rule.get("action", {})))
        if kind == "pause":
            outcome = "waiting"
        elif kind == "condition":
            outcome = "yes" if matched else "no"
        elif kind == "reserve":
            if matched:
                skill_id = rule["action"]["skillTypeId"]
                owner = nodes[node["owner"]]["rule"]
                # The explicit reservation's owner can spend it; all other paths cannot.
                owners.setdefault(skill_id, []).append(owner)
                reserved.add(skill_id)
                state["_reservedStrictSkillTypeIds"] = sorted(reserved)
            outcome = "next"
        elif kind == "opener":
            decision = c.first_turn_default_decision(rules, local)
            outcome = "selected" if decision else "next"
        elif kind == "adaptive":
            if matched_trial:
                memory = capability_memory or c.SkillCapabilityMemory()
                memory.observe_state(local)
                decision = c.adaptive_combat_decision(f"{matched_trial} · 当前无可执行试炼专用动作", local, memory,
                                                      excluded_skill_type_ids=reserved)
            outcome = "selected" if decision else "next"
        else:
            decision = c.evaluate_strategy_node(rule, local, capability_memory=capability_memory,
                reserved_skill_type_ids=reserved, automatic_trial_ids=automatic,
                preferred_skill_type_ids=preferred, planner_state=planner_state)
            if matched and rule.get("action", {}).get("type") == "executeTrialRecipe" and matched_trial is None:
                matched_trial = rule.get("name", "按当前试炼自动决策")
            c.record_rule(local, rule, len(trace), decision, c.matches, c.states_for_rule_targets, c.effect_count_checks)
            outcome = "selected" if decision else "unavailable" if matched else "noMatch"
        next_key = node.get("edges", {}).get(outcome)
        reservation_block = any(row.get("reason") == "skill_reserved_for_trial_rule"
                                for row in local["_decisionTrace"][trace_start:])
        trace.append({"nodeId": key, "name": name, "kind": kind, "outcome": outcome,
                      "next": next_key, "matched": matched,
                      "reason": "skill_reserved" if reservation_block and decision is None else None,
                      "reservedSkillTypeIds": sorted(reserved)})
        if decision is not None or kind == "pause":
            return decision
        key = next_key
    raise ValueError("流程超出节点上限")


def replay_flow(config: dict, snapshot: dict) -> dict:
    """Evaluate supplied data only. Never fetch a live state or submit a command."""
    import chimera_controller as c
    from decision_observability import decision_details
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("skills"), list):
        raise ValueError("请选择包含 skills、heroes、bosses 的完整决策快照 JSON")
    state = copy.deepcopy(snapshot)
    state["_decisionTrace"] = []
    for key in ("_trialSkillOwners", "_flowTrace", "_flowRevision"):
        state.pop(key, None)
    previous_mode = c.ACTIVE_BOSS_MODE
    try:
        c.ACTIVE_BOSS_MODE = config.get("bossMode", "chimera")
        result = evaluate_flow({**config, "executionMode": "flow"}, state)
    finally:
        c.ACTIVE_BOSS_MODE = previous_mode
    return decision_details(state, result)
