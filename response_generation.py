"""Application-layer response workflow.

The HTTP route should only authenticate, validate input, and translate errors.
This module keeps the ordered agentic stages visible and makes each integration
point injectable, which also keeps the workflow easy to test in isolation.
"""

import json
from typing import Any, Callable
from uuid import uuid4

from openai import OpenAI

from models import FeedbackAnnotation, LanguageSettings


Trace = Callable[..., None]
HistoryLoader = Callable[[str], list[dict[str, str]]]
HistorySelector = Callable[[OpenAI, str, str, list[dict[str, str]], str], list[dict[str, str]]]
SearchSelector = Callable[[OpenAI, str, dict[str, Any]], dict[str, Any]]
Retriever = Callable[[str, str, OpenAI], list[dict[str, str]]]
Indexer = Callable[[dict[str, str], str, OpenAI], bool]
AgentRunner = Callable[[OpenAI, str, str, dict[str, Any]], tuple[str, bool]]
InstructionBuilder = Callable[[LanguageSettings], str]
FeedbackGenerator = Callable[[OpenAI, str, str, str, str], list[FeedbackAnnotation]]
MessageWriter = Callable[..., None]


def generate_response(
    *,
    client: OpenAI,
    deployment: str,
    user_id: str,
    request_context: dict[str, Any],
    settings: LanguageSettings,
    load_history: HistoryLoader,
    select_history: HistorySelector,
    retrieve_knowledge: Retriever,
    select_search: SearchSelector,
    index_page: Indexer,
    run_agent: AgentRunner,
    build_instructions: InstructionBuilder,
    generate_feedback: FeedbackGenerator,
    write_message: MessageWriter,
    trace: Trace,
) -> tuple[str, list[FeedbackAnnotation]]:
    """Run the response workflow in explicit, observable stages."""
    message_text = request_context.get("text")
    if not isinstance(message_text, str):
        message_text = json.dumps(request_context, ensure_ascii=False)
    trace_id = uuid4().hex

    # Stage 1: load durable conversation context and select only relevant turns.
    # Reminder articles are deliberately not loaded here: their reusable content
    # is represented by Azure AI Search and must pass the RAG sufficiency check.
    history = load_history(user_id)
    history = select_history(client, deployment, message_text, history, trace_id)
    generation_input: dict[str, Any] = {
        key: value for key, value in request_context.items()
        if key not in {"native_language", "learning_language"}
    }
    generation_input["trace_id"] = trace_id
    if history:
        generation_input["conversation_history"] = history
    # Stage 2: search the persistent knowledge base before considering the web.
    retrieval_query = message_text
    if history:
        retrieval_query = "\n".join(
            f"{turn['role']}: {turn['text']}" for turn in history[-4:]
        ) + f"\nuser: {message_text}"
    knowledge = retrieve_knowledge(retrieval_query, user_id, client)
    trace("knowledge_retrieval_completed", trace_id, prompt=retrieval_query, response=knowledge)
    if knowledge:
        generation_input["knowledge_context"] = knowledge

    # Stage 3: evaluate whether fresh factual context would help, then retrieve
    # at most one web page and index it for future conversations.
    generation_input = select_search(client, deployment, generation_input)
    web_context = generation_input.get("skill_results", {}).get("internet_search")
    if web_context:
        trace("web_search_requested", trace_id, prompt=message_text, response=web_context)
        indexed = index_page(web_context[0], user_id, client)
        trace("web_page_indexed", trace_id, response=indexed)
        if indexed:
            refreshed = retrieve_knowledge(retrieval_query, user_id, client)
            if refreshed:
                generation_input["knowledge_context"] = refreshed

    # Stage 4: persist the user turn before generating the answer.
    write_message(user_id, message_text, "user", web_context=web_context)

    # Stage 5: draft and evaluate the natural conversational response.
    response_text, structured = run_agent(
        client, deployment, build_instructions(settings), generation_input
    )

    # Stage 6: independently annotate language-learning mistakes.
    feedback = generate_feedback(
        client, deployment, message_text, response_text, settings.native_language
    ) if response_text and structured else []
    write_message(user_id, response_text, "assistant", feedback)
    return response_text, feedback
