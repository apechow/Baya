"""
Agent: a thin wrapper around PlanningAgent + ExecutionAgent for single-prompt use.
"""

import asyncio
from typing import Any, Dict, List, Optional

from agent.baya_json_agent.auth import AuthRegistry, MultiAuth
from agent.baya_json_agent.execution_agent import ExecutionAgent, ExecutionResult
from agent.baya_json_agent.planning_agent import PlanningAgent


class Agent:
    """
    Plans and executes a single natural language prompt.

    Usage:
        agent = Agent()
        agent.initialize({"gitlab": "http://instance1:8080/api/v4"})
        result = await agent.run_task("Create an issue titled 'dark mode' in project foo/bar")
        print(result.answer)
        print(agent.last_plan_response.plan)  # available after run_task()

        # Per-run base_url override (e.g. for parallel workers):
        result = await agent.run_task("...", servers={"gitlab": "http://instance2:8080/api/v4"})

        # Multi-server (single task spanning multiple APIs):
        agent.initialize({"gitlab": "http://gl:8080/api/v4", "shopping": "http://shop:9090"})
        result = await agent.run_task("...")
    """

    def __init__(
        self,
        env_file: str = "config/.server_env",
        api_dir: str = "api",
        skip_execution: bool = False,
        debug: bool = False,
    ):
        self.env_file = env_file
        self.api_dir = api_dir
        self.skip_execution = skip_execution
        self.debug = debug

        self.planning_agent: Optional[PlanningAgent] = None
        self.execution_agent: Optional[ExecutionAgent] = None
        self.last_plan_response: Optional[Any] = None  # set after each run_task()
        self._servers: Dict[str, str] = {}

    def initialize(self, servers: Dict[str, str]) -> None:
        """
        Prepare the agent for a given set of servers.

        Args:
            servers: mapping of server_name → base_url, e.g.
                     {"gitlab": "http://instance1:8080/api/v4"}
                     The base_url may be an empty string if it will be
                     supplied per-task via run_task(servers=...).
        """
        if not servers:
            raise ValueError("servers dict must not be empty.")

        self._servers = dict(servers)

        self.planning_agent = PlanningAgent(
            api_dir=self.api_dir,
            debug_responses=self.debug,
            debug_prompts=False,
        )

        if not self.skip_execution:
            registry = AuthRegistry.build_default(self.env_file)
            for server in servers:
                if server not in registry:
                    raise ValueError(
                        f"Server {server!r} not found in auth registry. "
                        f"Check that its token is set in {self.env_file!r}."
                    )
            auths = [registry.get(s) for s in servers]
            combined_auth = auths[0] if len(auths) == 1 else MultiAuth(*auths)
            self.execution_agent = ExecutionAgent(
                auth=combined_auth,
                debug=self.debug,
                api_dir=self.api_dir,
            )

    def _inject_base_urls(self, plan_response, servers: Dict[str, str]):
        """
        Replace the routing tags in step.base_url with actual runtime URLs.

        The planner stores "api_filename|/base_path" in step.base_url
        (e.g. "gitlab_api.json|/api/v4").  This method resolves the api
        filename to a server name, looks up the runtime host from the servers
        dict, and combines it with the swagger base_path:
            "http://127.0.0.1:8024" + "/api/v4"  →  "http://127.0.0.1:8024/api/v4"
        """
        def _resolve(tag: str) -> str:
            api_filename, _, base_path = tag.partition("|")

            # Match api filename to a server by longest name contained in filename
            matches = [s for s in servers if s.lower() in api_filename.lower()]
            server = max(matches, key=len) if matches else (
                next(iter(servers)) if len(servers) == 1 else ""
            )
            host = servers.get(server, "").rstrip("/")
            return host + base_path

        updated_steps = [
            step.model_copy(update={"base_url": _resolve(step.base_url)})
            for step in plan_response.plan
        ]
        return plan_response.model_copy(update={"plan": updated_steps})

    async def run_task(
        self,
        prompt: str,
        servers: Optional[Dict[str, str]] = None,
        sites: Optional[List[str]] = None,
    ) -> ExecutionResult:
        """
        Plan and execute a natural language prompt.

        Args:
            prompt:  The user's request (e.g. "Create an issue in project foo/bar").
            servers: Optional per-run override of the servers dict.  Useful when
                     running tasks in parallel against different server instances:
                         await agent.run_task(prompt, servers={"gitlab": worker_url})
                     Falls back to the dict passed to initialize().
            sites:   Site name(s) this task targets (e.g. ["gitlab"]), used to select
                     which swagger schema file(s) PlanningAgent loads. Falls back to
                     the keys of the active servers dict when not provided.

        Returns:
            ExecutionResult with raw API outputs and a natural language answer.
        """
        if self.planning_agent is None:
            raise RuntimeError("Call initialize() before run_task().")

        active_servers = servers if servers is not None else self._servers
        active_sites = sites if sites is not None else list(active_servers.keys())

        plan_response = await asyncio.wait_for(
            self.planning_agent.plan(prompt, active_sites),
            timeout=600,    # outer budget for all planning LLM calls; inner per-call limit is 300s
        )
        self.last_plan_response = plan_response

        if self.skip_execution:
            return ExecutionResult(outputs={}, answer="(execution skipped)")

        if self.execution_agent is None:
            raise RuntimeError("ExecutionAgent was not initialized (skip_execution=True?).")

        plan_response = self._inject_base_urls(plan_response, active_servers)

        return await asyncio.wait_for(
            self.execution_agent.execute(plan_response, task=prompt),
            timeout=240,
        )
