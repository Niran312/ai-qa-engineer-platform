"""Server-side default AI instructions for a scan.

The default QA prompt used to live in the frontend and was only sent when the user happened to
open the scan-scope dropdown, so scan quality depended on the form. It now lives here and is
applied to every scan; anything the user types is appended as extra instructions on top of it.
"""
from pathlib import Path
from typing import Optional

from .schemas import ScopeContext

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_TEMPLATES = {
    "entire": (_PROMPTS_DIR / "entire_scan_instructions.txt").read_text(encoding="utf-8"),
    "module": (_PROMPTS_DIR / "module_scan_instructions.txt").read_text(encoding="utf-8"),
}


def _clean(text: Optional[str]) -> str:
    return (text or "").strip()


def build_scan_instructions(scope_ctx: Optional[ScopeContext], extra_instructions: Optional[str] = None) -> str:
    """Return the full instructions for a scan: the default prompt for its scope, with every
    {{placeholder}} filled in, plus the user's optional extra instructions."""
    scope = scope_ctx.scan_scope if scope_ctx and scope_ctx.scan_scope in _TEMPLATES else "entire"
    extra = _clean(extra_instructions)
    target_urls = (scope_ctx.target_urls if scope_ctx else None) or []
    allowed_features = (scope_ctx.allowed_features if scope_ctx else None) or []

    values = {
        "scan_scope": "ENTIRE APPLICATION" if scope == "entire" else "MODULE WISE",
        "parent_module": _clean(scope_ctx.parent_module if scope_ctx else None) or "None",
        "selected_module": _clean(scope_ctx.selected_module if scope_ctx else None) or "None",
        "target_urls": "\n".join(target_urls) or "All pages discovered during the scan",
        "allowed_features": ", ".join(allowed_features) or "All features discovered during the scan",
        "user_description": extra or "None",
    }
    text = _TEMPLATES[scope]
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    text = text.strip()

    # The module template places the user's text itself; the entire-app template has no slot for it.
    if extra and scope == "entire":
        text += "\n\n==================================================\nADDITIONAL USER INSTRUCTIONS\n" \
                "==================================================\n\n" + extra
    return text
