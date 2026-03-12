from __future__ import annotations
"""
Nexus Orchestrator
- Plans tasks using Claude Sonnet (or best available)
- Spawns parallel sub-agents using cheapest appropriate model (Kimi)
- Acquires missing tools dynamically
- Tracks performance and self-improves
"""
import asyncio, json, re, time
from typing import AsyncGenerator
from core.providers import LLMProvider
from tools.tools import BUILTIN_TOOLS, execute_tool
from memory.memory import get_memory

PLAN_SYSTEM = """You are Nexus, a powerful autonomous orchestrator AI.

When given a task, decide the best execution strategy:

**MULTI-AGENT**: Use when task needs parallel work, research + synthesis, multiple specialized skills, OR when you have tools that can help.
**DIRECT**: Use ONLY for simple conversational Q&A where no tools or research are needed.

You have powerful tools available including:
- Web search, URL fetching, code execution, file I/O
- **n8n workflow automation**: n8n_search_nodes (find integrations), n8n_get_node (node details), n8n_search_templates (find pre-built workflows), n8n_create_workflow (build automations), n8n_list_workflows, n8n_activate_workflow, n8n_test_workflow, n8n_deploy_template, n8n_health, n8n_validate_workflow

IMPORTANT: When the user asks about n8n, automations, integrations, workflows, or anything that could use n8n — ALWAYS use multi_agent strategy with subtasks that use the n8n tools. Never answer n8n questions from memory alone.

For MULTI-AGENT, respond ONLY with valid JSON:
{
  "strategy": "multi_agent",
  "reason": "brief why",
  "needs_tools": ["tool_name_if_missing"],
  "subtasks": [
    {"id": "research_1", "task": "Use n8n_search_nodes to find X", "type": "research", "priority": 1},
    {"id": "analysis_1", "task": "analyze X", "type": "analysis", "priority": 2, "depends_on": ["research_1"]}
  ]
}

For DIRECT, respond ONLY with valid JSON:
{"strategy": "direct", "response": "your answer"}

Task types: research, analysis, coding, creative, summarize, cta, image_prompt, tool_research, self_improve, general
Priority 1 = runs first, priority 2 = runs after priority 1 completes, etc.
Max 6 subtasks. Return ONLY JSON."""

SYNTH_SYSTEM = """You are synthesizing results from multiple specialized AI sub-agents.
Combine their findings into a clear, comprehensive, actionable response.
Structure it well. Be direct and useful. Include specific recommendations, not vague advice."""

SELF_IMPROVE_SYSTEM = """You are analyzing this AI system's performance data to identify weaknesses and improvements.
Look at failure patterns, slow tasks, common errors.
Propose specific, implementable improvements.
Return JSON: {"weaknesses": [...], "improvements": [...], "priority_fix": "most important issue"}"""


class SubAgent:
    def __init__(self, agent_id: str, task: str, task_type: str, provider: LLMProvider, dynamic_tools: dict = None):
        self.id = agent_id
        self.task = task
        self.task_type = task_type
        self.provider = provider
        self.model_id = provider.resolve(task_type)
        self.model_label = provider.label(self.model_id)
        self.dynamic_tools = dynamic_tools or {}
        self.status = "pending"
        self.result = ""
        self.steps = []
        self.start_time = None

    async def run(self) -> AsyncGenerator[dict, None]:
        self.status = "running"
        self.start_time = time.time()
        mem = get_memory()

        yield {"type": "agent_start", "agent_id": self.id, "task": self.task,
               "model": self.model_label, "task_type": self.task_type}

        system = f"""You are a specialized {self.task_type} sub-agent with one job: {self.task}
Use your tools to gather real information. Be thorough and specific.
Return ONLY the result — no preamble, no meta-commentary."""

        messages = [{"role": "user", "content": self.task}]
        use_tools = self.model_id.startswith("claude")

        for iteration in range(8):
            response = await self.provider.chat(
                model_id=self.model_id,
                messages=messages,
                system=system,
                tools=BUILTIN_TOOLS if use_tools else None
            )

            if not response["ok"]:
                self.status = "error"
                self.result = response["content"]
                duration = time.time() - self.start_time
                await mem.save_agent_run(self.id, self.task_type, self.model_id,
                    self.task, self.result, False, duration, response["content"])
                yield {"type": "agent_error", "agent_id": self.id, "error": response["content"]}
                return

            raw = response.get("raw")

            # Claude tool use
            if use_tools and hasattr(raw, "content") and raw.stop_reason == "tool_use":
                tool_results = []
                for block in raw.content:
                    if block.type == "text" and block.text:
                        step = f"💭 {block.text[:150]}"
                        self.steps.append(step)
                        yield {"type": "agent_step", "agent_id": self.id, "step": step}
                    elif block.type == "tool_use":
                        preview = json.dumps(block.input)[:80]
                        step = f"🔧 {block.name}({preview})"
                        self.steps.append(step)
                        yield {"type": "agent_step", "agent_id": self.id, "step": step}
                        result = await execute_tool(block.name, block.input, self.dynamic_tools)
                        tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result[:4000]})
                messages.append({"role": "assistant", "content": raw.content})
                messages.append({"role": "user", "content": tool_results})
                continue

            # Non-Claude: simulate web search for research tasks
            if self.task_type in ("research", "analysis", "tool_research") and iteration == 0:
                from tools.tools import web_search
                step = f"🔍 Searching: {self.task[:60]}"
                self.steps.append(step)
                yield {"type": "agent_step", "agent_id": self.id, "step": step}
                search_result = await web_search(self.task)
                messages.append({"role": "assistant", "content": response["content"]})
                messages.append({"role": "user", "content": f"Search results:\n{search_result}\n\nNow synthesize a thorough answer."})
                continue

            self.result = response["content"]
            break

        self.status = "done"
        duration = time.time() - self.start_time
        await mem.save_agent_run(self.id, self.task_type, self.model_id,
            self.task, self.result, True, duration)

        yield {"type": "agent_done", "agent_id": self.id, "result": self.result,
               "model": self.model_label, "steps": self.steps, "duration": round(duration, 1)}


class Orchestrator:
    def __init__(self, provider: LLMProvider):
        self.provider = provider
        self.dynamic_tools = {}
        self._load_dynamic_tools()

    def _load_dynamic_tools(self):
        """Load previously acquired tools from memory."""
        try:
            mem = get_memory()
            # Sync load
            import sqlite3
            db = sqlite3.connect(str(Path.home() / ".nexus" / "memory.db"))
            rows = db.execute("SELECT name, description, code FROM tool_registry").fetchall()
            for name, desc, code in rows:
                self.dynamic_tools[name] = {"description": desc, "code": code}
            db.close()
        except: pass

    async def run(self, user_message: str, history: list = None) -> AsyncGenerator[dict, None]:
        start = time.time()
        mem = get_memory()
        yield {"type": "status", "message": "🧠 Planning..."}

        orch_model = self.provider.resolve("orchestrator")
        messages = list(history or []) + [{"role": "user", "content": user_message}]

        # Check memory context
        recent = await mem.get_recent_conversations(5)
        context = ""
        if recent:
            context = "\n\nRecent context:\n" + "\n".join([f"User: {r['user_msg'][:100]}" for r in recent[-3:]])

        plan_resp = await self.provider.chat(
            model_id=orch_model,
            messages=[{"role": "user", "content": user_message + context}],
            system=PLAN_SYSTEM
        )
        yield {"type": "orchestrator_model", "model": self.provider.label(orch_model)}

        if not plan_resp["ok"]:
            yield {"type": "error", "message": plan_resp["content"]}
            return

        plan = self._parse_json(plan_resp["content"])

        # Direct response
        if not plan or plan.get("strategy") == "direct":
            content = plan.get("response", plan_resp["content"]) if plan else plan_resp["content"]
            yield {"type": "direct_response", "content": content}
            await mem.save_conversation(user_message, content, "", 0, time.time() - start)
            return

        # Check for missing tools needed
        needs_tools = plan.get("needs_tools", [])
        if needs_tools:
            yield {"type": "status", "message": f"🔍 Acquiring {len(needs_tools)} missing tools..."}
            for tool_name in needs_tools:
                async for event in self._acquire_tool(tool_name):
                    yield event

        # Multi-agent execution
        subtasks = plan.get("subtasks", [])
        reason = plan.get("reason", "")
        yield {"type": "plan", "reason": reason, "subtask_count": len(subtasks),
               "subtasks": [{"id": s["id"], "task": s["task"], "type": s["type"]} for s in subtasks]}

        # Group by priority for sequential-within-priority execution
        priorities = {}
        for st in subtasks:
            p = st.get("priority", 1)
            priorities.setdefault(p, []).append(st)

        results = {}
        for priority_level in sorted(priorities.keys()):
            group = priorities[priority_level]
            agents = [SubAgent(s["id"], s["task"], s["type"], self.provider, self.dynamic_tools) for s in group]

            # Run this priority group in parallel
            queues = [asyncio.Queue() for _ in agents]

            async def fill(agent, queue):
                async for event in agent.run():
                    await queue.put(event)
                await queue.put(None)

            tasks = [asyncio.create_task(fill(a, q)) for a, q in zip(agents, queues)]
            active = len(queues)
            while active > 0:
                for queue in queues:
                    try:
                        ev = queue.get_nowait()
                        if ev is None:
                            active -= 1
                        else:
                            if ev["type"] == "agent_done":
                                results[ev["agent_id"]] = ev["result"]
                            yield ev
                    except asyncio.QueueEmpty:
                        pass
                await asyncio.sleep(0.04)
            await asyncio.gather(*tasks, return_exceptions=True)

        # Synthesize
        if results:
            yield {"type": "status", "message": "🔗 Synthesizing all results..."}
            results_text = "\n\n---\n\n".join([
                f"**{s['id']} ({s['type']})**: {s['task']}\n\nFindings:\n{results.get(s['id'], 'No result')}"
                for s in subtasks
            ])
            synth_resp = await self.provider.chat(
                model_id=orch_model,
                messages=[{"role": "user", "content": f"User request: {user_message}\n\nAgent findings:\n{results_text}"}],
                system=SYNTH_SYSTEM
            )
            content = synth_resp.get("content", "")
            yield {"type": "synthesis", "content": content, "model": self.provider.label(orch_model)}
            await mem.save_conversation(user_message, content,
                json.dumps(plan), 0, time.time() - start)

    async def _acquire_tool(self, tool_name: str) -> AsyncGenerator[dict, None]:
        """Research, write, and register a new dynamic tool."""
        yield {"type": "tool_acquisition", "tool": tool_name, "status": "researching"}
        research_agent = SubAgent(
            f"tool_researcher_{tool_name}", f"Research and write Python code for tool: {tool_name}. Find the best free/cheap library, write a 'run(**kwargs)' function.",
            "tool_research", self.provider
        )
        code_result = ""
        async for event in research_agent.run():
            if event["type"] == "agent_done":
                code_result = event["result"]
            yield event

        # Extract code block
        code_match = re.search(r'```python\s*([\s\S]*?)```', code_result)
        if code_match:
            code = code_match.group(1)
            self.dynamic_tools[tool_name] = {"description": f"Dynamic: {tool_name}", "code": code}
            mem = get_memory()
            await mem.register_tool(tool_name, f"Dynamically acquired: {tool_name}", code)
            yield {"type": "tool_acquisition", "tool": tool_name, "status": "installed"}

    async def self_improve(self) -> AsyncGenerator[dict, None]:
        """Analyze performance and generate improvements."""
        mem = get_memory()
        yield {"type": "status", "message": "🔬 Analyzing performance..."}
        stats = await mem.get_agent_stats()
        weaknesses = await mem.get_weaknesses()
        analysis_resp = await self.provider.chat(
            model_id=self.provider.resolve("self_improve"),
            messages=[{"role": "user", "content": f"Performance stats:\n{json.dumps(stats, indent=2)}\n\nFailure patterns:\n{json.dumps(weaknesses, indent=2)}\n\nAnalyze and suggest improvements."}],
            system=SELF_IMPROVE_SYSTEM
        )
        content = analysis_resp.get("content", "")
        improvement = self._parse_json(content) or {"analysis": content}
        if improvement:
            await mem.save_improvement(json.dumps(weaknesses), content)
        yield {"type": "self_improvement", "analysis": improvement, "content": content}

    def _parse_json(self, text: str) -> dict | None:
        try:
            m = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', text)
            if m: return json.loads(m.group(1))
            m = re.search(r'\{[\s\S]*"strategy"[\s\S]*\}', text)
            if m: return json.loads(m.group(0))
            # Try direct parse
            return json.loads(text.strip())
        except: return None

from pathlib import Path
