"""Guards Phase 0's own invariants so a later edit can't silently break them."""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_compose_pins_image_tags():
    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    images = re.findall(r"^\s*image:\s*(\S+)", compose, re.MULTILINE)
    assert images, "expected at least one image: line in docker-compose.yml"
    for image in images:
        assert not image.endswith(":latest"), f"{image} must pin a real tag, not :latest"
        assert ":" in image, f"{image} has no tag at all"


def test_makefile_creates_topic_with_three_partitions():
    makefile = (REPO_ROOT / "Makefile").read_text()
    assert "rpk topic create" in makefile
    assert "--partitions 3" in makefile


def test_env_example_has_no_real_secret():
    env_example = (REPO_ROOT / ".env.example").read_text()
    assert "GROQ_API_KEY=" in env_example
    for line in env_example.splitlines():
        if line.startswith("GROQ_API_KEY="):
            value = line.split("=", 1)[1].strip()
            assert value == "", "GROQ_API_KEY in .env.example must stay empty, not a real key"
