"""Dependabot이 develop과 호환성 묶음 정책을 지키는지 검증한다."""
from pathlib import Path

import yaml

from scripts.preflight_release import prerelease_requirement_pins

ROOT = Path(__file__).resolve().parents[1]


def _pip_update_config() -> dict:
    config = yaml.safe_load(
        (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    )
    return next(
        update
        for update in config["updates"]
        if update["package-ecosystem"] == "pip" and update["directory"] == "/"
    )


def test_dependabot_targets_develop_and_groups_langstack():
    update = _pip_update_config()

    assert update["target-branch"] == "develop"
    assert set(update["groups"]["langstack"]["patterns"]) == {
        "langchain*",
        "langgraph*",
        "langsmith",
    }


def test_dependabot_excludes_known_release_candidate():
    update = _pip_update_config()
    ignored_versions = {
        version
        for rule in update["ignore"]
        if rule["dependency-name"] == "langgraph"
        for version in rule["versions"]
    }

    assert "1.0.10rc1" in ignored_versions


def test_repository_requirements_do_not_pin_prereleases():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()

    assert prerelease_requirement_pins(requirements) == []
