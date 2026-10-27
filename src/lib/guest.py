"""Load the guest scripts and fill in the non-secret names."""

from __future__ import annotations

from pathlib import Path

GUEST_DIR = Path(__file__).resolve().parents[1] / "guest"


def load_script(name: str, replacements: dict[str, str]) -> str:
    common = (GUEST_DIR / "common.sh").read_text(encoding="utf-8")
    body = (GUEST_DIR / name).read_text(encoding="utf-8")
    script = common + "\n" + body
    for key, value in replacements.items():
        script = script.replace(key, value)
    if "__" in script:
        raise ValueError(f"{name} still has an unfilled placeholder.")
    return script


def startup_replacements(project_id: str) -> dict[str, str]:
    from src.lib.config import DB_ADDRESS, SECRET_NAME, WEB_PREFIX

    return {
        "__PROJECT_ID__": project_id,
        "__SECRET_NAME__": SECRET_NAME,
        "__DB_ADDRESS__": DB_ADDRESS,
        "__WEB_PREFIX__": WEB_PREFIX,
    }
