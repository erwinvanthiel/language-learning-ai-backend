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
    triggers: tuple[str, ...]
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
    triggers = tuple(item.strip().lower() for item in metadata.get("triggers", "").split(",") if item.strip())
    return ConversationSkill(
        name=name,
        description=metadata.get("description", ""),
        triggers=triggers,
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


def _fallback_skill(message: str, skills: tuple[ConversationSkill, ...]) -> ConversationSkill:
    lowered = message.lower()
    for skill in skills:
        if any(trigger in lowered for trigger in skill.triggers):
            return skill
    return next((skill for skill in skills if skill.name == "general-conversation"), skills[0])


def select_conversation_skill(
    client: OpenAI,
    deployment: str,
    message: str,
    trace: Callable[..., None] | None = None,
) -> ConversationSkill:
    """Select one skill from metadata without changing persona or tone rules."""
    skills = load_conversation_skills()
    metadata = [
        {
            "name": skill.name,
            "description": skill.description,
            "triggers": skill.triggers,
            "requires_retrieval": skill.requires_retrieval,
        }
        for skill in skills
    ]
    try:
        result = client.responses.create(
            model=deployment,
            instructions=(
                "Select the best conversation skill using only the supplied metadata. "
                "Do not write a response and do not infer a skill that is not listed. "
                'Return only JSON: {"skill": "skill-name"}.'
            ),
            input=json.dumps({"message": message, "skills": metadata}, ensure_ascii=False),
            max_output_tokens=80,
        )
        selected_name = json.loads(result.output_text).get("skill")
        selected = next((skill for skill in skills if skill.name == selected_name), None)
        if selected:
            if trace:
                trace("conversation_skill_selected", response={"skill": selected.name})
            return selected
    except (OpenAIError, TypeError, ValueError, json.JSONDecodeError):
        if trace:
            trace("conversation_skill_selection_failed")
    selected = _fallback_skill(message, skills)
    if trace:
        trace("conversation_skill_selected", response={"skill": selected.name, "fallback": True})
    return selected
