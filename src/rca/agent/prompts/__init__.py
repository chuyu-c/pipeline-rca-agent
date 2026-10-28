"""Prompt templates shipped with the package."""

from importlib import resources


def load_prompt(name: str) -> str:
    return (resources.files(__package__) / f"{name}.md").read_text(encoding="utf-8")
