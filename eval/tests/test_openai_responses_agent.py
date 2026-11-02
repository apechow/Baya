"""Unit coverage for the provider-isolated OpenAI Responses agent path."""

from types import SimpleNamespace

from react_agent.react_web.codeact_agent.openai_responses_agent import (
    OpenAIResponsesLLM,
    OpenAIResponsesReactAgent,
    OpenAIResponsesWebAgent,
    configured_agent,
)
from react_agent.react_web.react_agent_runner import ReactAgentRunner


def _call(response_id, name, arguments, call_id):
    return SimpleNamespace(
        id=response_id,
        output=[
            SimpleNamespace(
                type="function_call",
                name=name,
                arguments=arguments,
                call_id=call_id,
            )
        ],
        usage=SimpleNamespace(input_tokens=10, output_tokens=4, input_tokens_details=None),
        _hidden_params={"response_cost": 0.01},
    )


def test_responses_transport_continues_with_function_output(monkeypatch):
    import litellm

    requests = []
    responses = iter(
        [
            _call(
                "resp-1",
                "finish_task",
                '{"task_type":"NAVIGATE","status":"SUCCESS","retrieved_data_json":"null",'
                '"error_details":null,"evidence_summary":""}',
                "call-finish",
            ),
            _call("resp-2", "execute_browse", '{"actions":"goto(\\"http://site\\")"}', "call-browse"),
        ]
    )

    def fake_responses(**kwargs):
        requests.append(kwargs)
        return next(responses)

    monkeypatch.setattr(litellm, "responses", fake_responses)
    llm = OpenAIResponsesLLM("gpt-5.6-luna", web_only=True)

    first = llm.completion([
        {"role": "system", "content": "instructions"},
        {"role": "user", "content": "open todos"},
    ])
    assert "<finish>" in first.choices[0].message.content
    second = llm.completion([
        {"role": "system", "content": "instructions"},
        {"role": "user", "content": "finish_task rejected: no evidence"},
    ])

    assert "<execute_browse>" in second.choices[0].message.content
    assert requests[1]["previous_response_id"] == "resp-1"
    assert requests[1]["input"] == [{
        "type": "function_call_output",
        "call_id": "call-finish",
        "output": "finish_task rejected: no evidence",
    }]
    assert requests[0]["reasoning"] == {"effort": "medium"}
    assert llm.token_usage["llm_calls"] == 2
    assert llm.total_cost == 0.02


def test_web_and_react_expose_different_openai_tools(monkeypatch):
    import litellm

    requests = []

    def fake_responses(**kwargs):
        requests.append(kwargs)
        return _call("r", "execute_browse", '{"actions":"go_back()"}', "c")

    monkeypatch.setattr(litellm, "responses", fake_responses)
    OpenAIResponsesLLM("gpt-5.6-luna", web_only=True).completion(
        [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    )
    OpenAIResponsesLLM("gpt-5.6-luna", web_only=False).completion(
        [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    )

    web_names = {tool["name"] for tool in requests[0]["tools"]}
    react_names = {tool["name"] for tool in requests[1]["tools"]}
    assert web_names == {"execute_browse", "finish_task"}
    assert react_names == {"execute_ipython", "execute_bash", "execute_browse", "finish_task"}


def test_every_array_in_openai_tool_schemas_declares_item_types(monkeypatch):
    """OpenAI rejects array schemas whose items are unconstrained (items: {})."""
    import litellm

    captured = {}

    def fake_responses(**kwargs):
        captured.update(kwargs)
        return _call("r", "execute_browse", '{"actions":"go_back()"}', "c")

    monkeypatch.setattr(litellm, "responses", fake_responses)
    OpenAIResponsesLLM("gpt-5.6-luna", web_only=False).completion(
        [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    )

    def assert_array_items(schema):
        if not isinstance(schema, dict):
            return
        schema_type = schema.get("type")
        if schema_type == "array" or isinstance(schema_type, list) and "array" in schema_type:
            assert isinstance(schema.get("items"), dict)
            assert schema["items"].get("type"), schema
        for value in schema.values():
            if isinstance(value, dict):
                assert_array_items(value)
            elif isinstance(value, list):
                for item in value:
                    assert_array_items(item)

    for tool in captured["tools"]:
        assert_array_items(tool["parameters"])
    finish = next(tool for tool in captured["tools"] if tool["name"] == "finish_task")
    assert finish["strict"] is True
    retrieved = finish["parameters"]["properties"]["retrieved_data_json"]
    assert retrieved["type"] == "string"


def test_finish_requires_environment_evidence():
    valid = {
        "task_type": "NAVIGATE",
        "status": "SUCCESS",
        "retrieved_data": None,
        "error_details": None,
        "evidence_summary": "The todos page URL and heading were observed.",
    }
    reject = ReactAgentRunner._openai_finish_rejection
    assert "No environment action" in reject(valid, 0, 0, 0)
    assert "successful observation" in reject({**valid, "evidence_summary": ""}, 1, 1, 0)
    assert reject(valid, 1, 1, 0) is None

    failed = {**valid, "status": "PERMISSION_DENIED_ERROR", "error_details": "HTTP 403"}
    assert "observed action failure" in reject(failed, 1, 1, 0)
    assert reject(failed, 1, 0, 1) is None


def test_legacy_message_finish_is_not_used_by_openai_responses_runner():
    runner = ReactAgentRunner(webarena_output_dir="unused")
    runner._uses_openai_responses = True
    suffix = "\n".join(runner._task_prompt_suffix("http://site", "http://site"))
    assert "finish_task" in suffix
    assert "Finish[" not in suffix
    assert "<finish>" not in suffix


def test_provider_routing_is_openai_only(monkeypatch):
    import react_agent.react_web.codeact_agent.openai_responses_agent as module
    from react_agent.react_web.codeact_agent.codeact_agent import CodeActAgent

    class FakeConfigurator:
        def __init__(self, provider, protocol="responses"):
            self._data = {
                "agent_llm_provider": provider,
                "agent_llm_model": "gpt-5.6-luna" if provider == "openai" else "gemini-3.5-flash",
                "llm_providers": {
                    provider: [{
                        "model": "gpt-5.6-luna" if provider == "openai" else "gemini-3.5-flash",
                        "temp": 0.0,
                        "agent_protocol": protocol,
                    }]
                },
            }

        def load_all_env(self):
            return None

    monkeypatch.setattr(module, "Configurator", lambda: FakeConfigurator("openai"))
    react, enabled = configured_agent(web_only=False)
    web, web_enabled = configured_agent(web_only=True)
    assert enabled and isinstance(react, OpenAIResponsesReactAgent)
    assert web_enabled and isinstance(web, OpenAIResponsesWebAgent)

    monkeypatch.setattr(module, "Configurator", lambda: FakeConfigurator("google"))
    legacy, enabled = configured_agent(web_only=False)
    assert not enabled
    assert type(legacy) is CodeActAgent

    monkeypatch.setattr(module, "Configurator", lambda: FakeConfigurator("openai", "legacy"))
    legacy, enabled = configured_agent(web_only=False)
    assert not enabled
    assert type(legacy) is CodeActAgent
