"""Load and select conversation skills defined as Markdown contracts."""

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Callable

from openai import OpenAI, OpenAIError


@dataclass(frozen=True)
class ConversationSkill:
    name: str
    description: str
    requires_retrieval: bool
    instructions: str


def _metadata_value(value: str) -> str:
    return value.strip().strip('"\'')


def _parse_skill(path: Path) -> ConversationSkill:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"Skill {path} is missing front matter")
    _, metadata_text, instructions = text.split("---\n", 2)
    metadata: dict[str, str] = {}
    for line in metadata_text.splitlines():
        key, separator, value = line.partition(":")
        if separator:
            metadata[key.strip()] = _metadata_value(value)
    name = metadata.get("name", path.stem)
    if not re.fullmatch(r"[a-z][a-z0-9_-]{1,50}", name):
        raise ValueError(f"Invalid skill name: {name}")
    return ConversationSkill(
        name=name,
        description=metadata.get("description", ""),
        requires_retrieval=metadata.get("requires_retrieval", "false").lower() == "true",
        instructions=instructions.strip(),
    )


@lru_cache(maxsize=1)
def load_conversation_skills() -> tuple[ConversationSkill, ...]:
    directory = Path(__file__).with_name("skills")
    skills = tuple(_parse_skill(path) for path in sorted(directory.glob("*.md")))
    if not skills:
        raise RuntimeError(f"No conversation skills found in {directory}")
    return skills


def retrieve_skill_candidates(
    message: str, skills: tuple[ConversationSkill, ...], top_n: int = 3
) -> tuple[ConversationSkill, ...]:
    """Retrieve likely skills without embedding policy in the selector prompt."""
    query_terms = set(re.findall(r"[a-z0-9]{3,}", message.lower()))
    scored = []
    for skill in skills:
        text = f"{skill.name} {skill.description} {skill.instructions}".lower()
        score = sum(1 for term in query_terms if term in text)
        scored.append((score, skill.name, skill))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return tuple(item[2] for item in scored[:max(1, top_n)])


def select_conversation_skill(
    client: OpenAI,
    deployment: str,
    message: str,
    trace: Callable[..., None] | None = None,
) -> ConversationSkill | None:
    """Retrieve candidate skills, then let the LLM choose one or choose none."""
    skills = load_conversation_skills()
    candidates = retrieve_skill_candidates(message, skills)
    metadata = [
        {
            "name": skill.name,
            "description": skill.description,
            "requires_retrieval": skill.requires_retrieval,
            "instructions": skill.instructions,
        }
        for skill in candidates
    ]
    try:
        result = client.responses.create(
            model=deployment,
            instructions=(
                "Decide whether one candidate skill is specifically needed for the user message. "
                "Use only the candidate metadata and instructions. If no skill applies, choose none. "
                'Return only JSON: {"skill": "skill-name" or null}.'
            ),
            input=json.dumps({"message": message, "skills": metadata}, ensure_ascii=False),
            max_output_tokens=80,
        )
        selected_name = json.loads(result.output_text).get("skill")
        selected = next((skill for skill in candidates if skill.name == selected_name), None)
        if selected:
            if trace:
                trace("conversation_skill_selected", response={"skill": selected.name})
            return selected
    except (OpenAIError, TypeError, ValueError, json.JSONDecodeError):
        if trace:
            trace("conversation_skill_selection_failed")
    if trace:
        trace("conversation_skill_selected", response={"skill": None})
    return None
