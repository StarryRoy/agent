"""Procurement triggers, using Harness selection and native load_skill execution."""

from __future__ import annotations

import json
from uuid import uuid4

from agent_harness import AgentMiddleware, LexicalSkillSelector, ModelRequest, Skill
from langchain_core.messages import AIMessage, HumanMessage

from .context import decode


class ProcurementSkillMiddleware(AgentMiddleware):
    def __init__(self, role: str, skills: list[Skill], selector: LexicalSkillSelector):
        self.role, self.skills, self.selector = role, skills, selector

    def _load(self, request: ModelRequest):
        if request.purpose != "agent":
            return None
        task = next(
            (decode(m.content) for m in reversed(request.messages) if isinstance(m, HumanMessage)),
            {},
        )
        results = request.state.get("procurement_results", {})
        results = results if isinstance(results, dict) else {}
        # Match current task/replan only, never stale raw history or catalog descriptions.
        if self.role == "main":
            risk = results.get("risk_analysis", {})
            pricing = results.get("pricing_analysis", {})
            budget = results.get("budget_analysis", {})
            requirement = results.get("requirement", {})
            reason = risk.get("replan_reason") or pricing.get("replan_reason") or ""
            goal = requirement.get("request") or task
            gap = budget.get("over_budget_amount")
        else:
            envelope = task if isinstance(task, dict) else {}
            reason = (
                str(envelope.get("replan_reason") or "")
                + " "
                + str(envelope.get("analysis_strategy") or "")
            )
            goal = envelope.get("request", task)
            gap = envelope.get("budget_analysis", {}).get("over_budget_amount")
        query = None
        if self.role != "main" and self.skills:
            # Query SubAgents each receive exactly one normal domain Skill.
            query = self.skills[0].name
        elif "delivery" in reason:
            query = "delivery-recovery"
        elif "risk" in reason:
            query = "supplier-risk-review"
        elif (
            "budget" in reason
            or "cost_reduction" in reason
            or (isinstance(gap, (float, int)) and gap > 0)
        ):
            query = "cost-optimization"
        elif (isinstance(goal, dict) and goal.get("priority") in {"high", "urgent"}) or any(
            word in json.dumps(goal, ensure_ascii=False) for word in ("紧急", "加急", "urgent")
        ):
            query = "urgent-procurement"
        if not query:
            return None
        candidates = [skill for skill in self.skills if skill.name == query]
        if not candidates:
            return None
        selected = self.selector.select(candidates, query=query, limit=1)[0]
        if selected.identifier in request.state.get("loaded_skills", []):
            return None
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "load_skill",
                    "args": {"name": selected.identifier},
                    "id": f"skill-{uuid4().hex}",
                    "type": "tool_call",
                }
            ],
        )

    def wrap_model_call(self, request, call_next):
        return self._load(request) or call_next(request)

    async def awrap_model_call(self, request, call_next):
        load = self._load(request)
        return load if load is not None else await call_next(request)
