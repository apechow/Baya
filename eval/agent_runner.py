# agent_runner_template.py
#
# Template for plugging a custom agent into the PTE benchmark test suite.
#
# SETUP
# ─────
# 1. Copy this file and rename it, e.g. "my_agent_runner.py"
# 2. Fill in _init_agent() and _run_task() below
# 3. Run the benchmark pointing at your class:
#
#    python3 -m pytest tests/test_agent_program_html.py \
#        --agent-runner my_agent_runner.MyAgentRunner \
#        --task-limit 5 -v -s
#
# WHAT YOU NEED TO RETURN FROM _run_task()
# ─────────────────────────────────────────
# A dict with at least these two keys:
#
#   "final_url"  (str | None)
#       The URL your agent ended on after completing the task.
#       Required for url_match tasks. Can be None for string_match tasks.
#
#   "answer"     (str | None)
#       Your agent's text answer to the task.
#       Required for string_match tasks. Can be None for program_html tasks.
#
# To signal a hard failure (agent crashed, timed out, etc.), return:
#   {"success": False, "error": "description of what went wrong"}
#
# TASK DICT STRUCTURE
# ───────────────────
# Each task dict passed to _run_task() looks like:
#
#   {
#     "task_id":   389,
#     "intent":    "Post 'lgtm' for the merge request related to ...",
#     "sites":     ["gitlab"],
#     "start_url": "__GITLAB__/primer/design/-/merge_requests",
#     "eval": {
#       "eval_types": ["program_html"],   # or "url_match", "string_match"
#       "program_html": [...],            # evaluator config (ignore this)
#       "reference_url": "...",           # expected URL (url_match tasks)
#       "reference_answers": {...},       # expected answer (string_match tasks)
#     }
#   }
#
# You only need task["intent"] to run your agent. The other fields are
# available if your agent needs context (e.g. which site, start URL).
#
# BASE URLS (if your agent needs to navigate to a site)
# ──────────────────────────────────────────────────────
#   GitLab:         http://localhost:8023
#   Reddit:         http://localhost:9999
#   Shopping:       http://ec2-18-218-205-96.us-east-2.compute.amazonaws.com:8082
#   Shopping Admin: http://ec2-18-218-205-96.us-east-2.compute.amazonaws.com:8082/admin
#
# RUNNING SPECIFIC SUBSETS
# ────────────────────────
#   By eval type:
#     python3 -m pytest tests/test_agent_program_html.py --agent-runner my_agent_runner.MyAgentRunner -v
#     python3 -m pytest tests/test_agent_url_match.py    --agent-runner my_agent_runner.MyAgentRunner -v
#     python3 -m pytest tests/test_agent_string_match.py --agent-runner my_agent_runner.MyAgentRunner -v
#
#   By site:
#     python3 -m pytest tests/ --agent-runner my_agent_runner.MyAgentRunner -k "gitlab" -v
#     python3 -m pytest tests/ --agent-runner my_agent_runner.MyAgentRunner -k "reddit" -v
#     python3 -m pytest tests/ --agent-runner my_agent_runner.MyAgentRunner -k "shopping" -v
#
#   Limit to first N tasks (useful for smoke testing):
#     python3 -m pytest tests/ --agent-runner my_agent_runner.MyAgentRunner --task-limit 5 -v -s
#
#   Save output to a file:
#     python3 -m pytest tests/test_agent_program_html.py \
#         --agent-runner my_agent_runner.MyAgentRunner -v \
#         2>&1 | tee my_results.txt

from typing import Any, Dict

from eval.run_pte_benchmark import BaseAgentRunner
from agent.baya_json_agent.agent import Agent


class MyAgentRunner(BaseAgentRunner):
    """
    Replace 'MyAgentRunner' with your own class name, and update the
    --agent-runner flag to match: --agent-runner my_agent_runner.MyAgentRunner
    """

    async def _init_agent(self) -> None:
        """
        Initialise your agent here. Called once at the start of the test session.

        Examples:
            self.agent = MyAgent(api_key="...", model="my-model")
            self.client = MyAPIClient(base_url="http://...")
        """
        self.myAgent = Agent()
        self.myAgent.initialize({getattr(self, "server", "gitlab"): getattr(self, "base_url", "")})
        # TODO: replace with your agent initialisation
        # raise NotImplementedError("Fill in _init_agent() with your agent setup")

    async def _run_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        Run your agent on a single task and return the result.

        Args:
            task: the full task dict (see TASK DICT STRUCTURE above).
                  Use task["intent"] as the input to your agent.

        Returns:
            dict with keys:
                "final_url"  (str | None) — URL the agent ended on
                "answer"     (str | None) — agent's text answer

            On hard failure, return:
                {"success": False, "error": "what went wrong"}
        """
        intent = task.get("intent", "")
        start_url = task.get("start_url", "")
        repo_path = start_url.replace("__GITLAB__", "").replace("__SHOPPING__", "").replace("__REDDIT__", "").strip("/")
        prompt = f"Project path: {repo_path}\n\nTask: {intent}" if repo_path else intent

        sites = task.get("sites", [])
        result = await self.myAgent.run_task(prompt, sites=sites)
        return {
            "final_url": None,
            "answer": result.answer,
        }

        # TODO: replace with your agent call
        # result = await self.agent.run(intent)
        # return {
        #     "final_url": result.url,
        #     "answer":    result.answer,
        # }

        # raise NotImplementedError("Fill in _run_task() with your agent call")
