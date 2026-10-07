"""Tests for avtalsagent.agent.open_tool_calls: a result for every tool call the model reads.

The rule is tested on message lists; the API's tests run it in the graph,
after a run that broke off during a tool call (tests/unit/api/test_api_agui.py).
"""

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage

from avtalsagent.agent.open_tool_calls import CALL_BROKEN_OFF, answer_open_calls
from tests.unit.agent.scripted_model import tool_call


def test_a_history_where_every_call_has_its_result_is_left_as_it_is() -> None:
    messages: list[AnyMessage] = [
        HumanMessage("Vilken uppsägningstid gäller?"),
        tool_call("search_documents", {"query": "uppsägning"}, "c1"),
        ToolMessage("Träff.", tool_call_id="c1"),
        AIMessage("Tre månader."),
    ]

    assert answer_open_calls(messages) == messages


def test_a_call_without_a_result_gets_an_error_right_after_it() -> None:
    question = HumanMessage("Vilken uppsägningstid gäller?")
    call = tool_call("search_documents", {"query": "uppsägning"}, "c1")
    follow_up = HumanMessage("Och vitet?")

    patched = answer_open_calls([question, call, follow_up])

    assert patched[:2] == [question, call]
    assert patched[3] == follow_up
    result = patched[2]
    assert isinstance(result, ToolMessage)
    assert (result.tool_call_id, result.name, result.status) == ("c1", "search_documents", "error")
    assert result.content == CALL_BROKEN_OFF


def test_of_two_calls_in_one_message_only_the_open_one_gets_an_error() -> None:
    both = AIMessage(
        content="",
        tool_calls=[
            {"name": "search_documents", "args": {}, "id": "c1", "type": "tool_call"},
            {"name": "read_section", "args": {}, "id": "c2", "type": "tool_call"},
        ],
    )
    done = ToolMessage("Träff.", tool_call_id="c1")

    patched = answer_open_calls([both, done])

    results = [message for message in patched if isinstance(message, ToolMessage)]
    assert sorted(result.tool_call_id for result in results) == ["c1", "c2"]
    assert [result.status for result in results if result.tool_call_id == "c2"] == ["error"]


def test_the_history_given_is_not_changed() -> None:
    messages: list[AnyMessage] = [tool_call("search_documents", {}, "c1")]

    answer_open_calls(messages)

    assert len(messages) == 1
