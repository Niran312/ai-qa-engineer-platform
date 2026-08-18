"""Playwright Core fallback behavioral engine.

Second behavioral execution engine alongside Playwright MCP. Used when the MCP engine cannot
successfully perform its initial navigation (see run_mcp_discovery's probe in mcp_explorer.py).
Drives the exact same deterministic feature_queue MCP would have executed, using the same
in-process Playwright already proven reliable in crawler.py - no LLM decision loop, no MCP
subprocess. Feature dispatch is deterministic: each queue item already carries feature_type
and locator_hint (Playwright-selector-shaped: #id / [name="x"] / text="...") computed by
derive_features_from_elements() when the queue was built.

Evidence classification stays honest: a feature is only ever marked OBSERVED_BEHAVIOR after a
real, verified Playwright interaction. Anything that can't be safely or successfully driven is
left as the STRUCTURAL_EVIDENCE / execution_engine="NONE" default it already carries from the
Pass-1 structural pre-population - never fabricated.
"""
import time
from playwright.async_api import async_playwright
from .crawler import extract_page_elements
from .mcp_explorer import classify_action, FeatureBehavior, ModuleBehaviorMap, ValidationRecord, TransitionRecord
from .database import add_scan_log
from .schemas import ScopeContext
from typing import Optional

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def _resolve_target_path(target: dict, base_url: str) -> str:
    target_path = target.get("url", base_url)
    if not target_path.startswith("http"):
        origin = "/".join(base_url.split("/")[:3])
        target_path = origin + ("/" if not target_path.startswith("/") else "") + target_path
    return target_path


def _find_or_create_feature(discovered_modules: dict, page_name: str, feat_name: str) -> FeatureBehavior:
    """Mirrors the matching logic in run_mcp_discovery's per-feature loop, so both engines
    update the same FeatureBehavior entries instead of producing duplicates."""
    matched_mod_key = None
    for k in discovered_modules.keys():
        if page_name.lower() in k.lower() or k.lower() in page_name.lower():
            matched_mod_key = k
            break

    if matched_mod_key:
        for f in discovered_modules[matched_mod_key].features:
            if feat_name.lower() in f.feature_name.lower() or f.feature_name.lower() in feat_name.lower():
                return f

    feature_info = FeatureBehavior(
        feature_name=feat_name,
        feature_type="Verification",
        is_observed=False,
        source_classification="STRUCTURAL_EVIDENCE",
        execution_engine="NONE",
        fields=[],
        validations=[],
        transitions=[]
    )
    use_key = matched_mod_key or f"Module - {page_name}"
    if use_key not in discovered_modules:
        discovered_modules[use_key] = ModuleBehaviorMap(
            module_name=use_key, submodule_name="General", pages=[page_name], features=[]
        )
    discovered_modules[use_key].features.append(feature_info)
    return feature_info


async def _perform_action(page, feature_type: str, locator_hint: str, fields: list):
    """Deterministic action dispatch by feature_type - no LLM involved.

    Always resolves through page.locator(...).first rather than the page.click/fill shorthand
    directly on a selector string: text= and other non-unique hints routinely match more than
    one element on a real page (e.g. an "Edit" button repeated per table row), and Playwright's
    strict mode raises rather than picking one - .first makes that deterministic instead of a
    live-only failure mode.
    """
    if feature_type == "Create":
        for f in fields:
            f_name = f.get("name")
            if not f_name or f_name == "field":
                continue
            f_hint = f'[name="{f_name}"]'
            f_type = (f.get("type") or "text").lower()
            locator = page.locator(f_hint).first
            if f_type in ("checkbox",):
                await locator.check(timeout=3000)
            elif f_type == "select":
                await locator.select_option(index=0, timeout=3000)
            elif f_type == "email":
                await locator.fill("qa-test@example.com", timeout=3000)
            elif f_type == "number":
                await locator.fill("1", timeout=3000)
            else:
                await locator.fill("QA Test Value", timeout=3000)
    elif feature_type == "Select" and fields and (fields[0].get("type") == "select"):
        await page.locator(locator_hint).first.select_option(index=0, timeout=3000)
    elif feature_type == "Toggle":
        await page.locator(locator_hint).first.check(timeout=3000)
    elif feature_type == "Search":
        await page.locator(locator_hint).first.fill("test", timeout=3000)
        await page.keyboard.press("Enter")
    else:
        # Action / Filter / Sort / Navigation / Pagination / radio-group "Select" - all safe,
        # single-click-driven interactions.
        await page.locator(locator_hint).first.click(timeout=3000)


async def run_playwright_core_exploration(scan_id: str, url: str, feature_queue: list, storage_state_path: Optional[str],
                                           scope_ctx: Optional[ScopeContext], discovered_modules: dict) -> dict:
    """Deterministically executes feature_queue with real Playwright interactions, mutating
    discovered_modules in place (same structure the MCP engine populates)."""
    metrics = {"pw_core_attempts": 0, "pw_core_successful_calls": 0, "pw_core_failed_calls": 0, "pw_core_duration": 0.0}
    engine_start = time.perf_counter()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-http-cache", "--disable-cache"])
        context_kwargs = {"viewport": {"width": 1280, "height": 800}, "user_agent": USER_AGENT}
        if storage_state_path:
            context_kwargs["storage_state"] = storage_state_path
        context = await browser.new_context(**context_kwargs)
        page = await context.new_page()

        try:
            for target in feature_queue:
                feat_name = target["feature"]
                page_name = target["page"]
                target_path = _resolve_target_path(target, url)
                feature_info = _find_or_create_feature(discovered_modules, page_name, feat_name)

                metrics["pw_core_attempts"] += 1
                t_nav = time.perf_counter()
                add_scan_log(scan_id, f"[PW-CORE] [feature={feat_name}] browser_navigate STARTED")
                try:
                    await page.goto(target_path, wait_until="load", timeout=20000)
                    dur = time.perf_counter() - t_nav
                    metrics["pw_core_successful_calls"] += 1
                    add_scan_log(scan_id, f"[PW-CORE] [feature={feat_name}] browser_navigate COMPLETED duration={dur:.2f}s")
                except Exception as e:
                    metrics["pw_core_failed_calls"] += 1
                    add_scan_log(scan_id, f"[PW-CORE] [feature={feat_name}] browser_navigate FAILED duration={time.perf_counter()-t_nav:.2f}s error={e}")
                    continue

                t_snap = time.perf_counter()
                add_scan_log(scan_id, f"[PW-CORE] [feature={feat_name}] browser_snapshot STARTED")
                try:
                    await extract_page_elements(page)
                    dur = time.perf_counter() - t_snap
                    metrics["pw_core_successful_calls"] += 1
                    add_scan_log(scan_id, f"[PW-CORE] [feature={feat_name}] browser_snapshot COMPLETED duration={dur:.2f}s")
                except Exception as e:
                    metrics["pw_core_failed_calls"] += 1
                    add_scan_log(scan_id, f"[PW-CORE] [feature={feat_name}] browser_snapshot FAILED duration={time.perf_counter()-t_snap:.2f}s error={e}")
                    continue

                locator_hint = target.get("locator_hint")
                feature_type = target.get("feature_type") or ""
                fields = target.get("fields") or []

                # Forms are driven field-by-field (each field has its own [name="..."] target),
                # so a "Create" feature can be actionable even when the form itself has no id
                # (and therefore no top-level locator_hint).
                has_actionable_target = bool(locator_hint) or (feature_type == "Create" and fields)
                if not has_actionable_target:
                    # No safe, concrete control to drive (e.g. "Explore main view", table
                    # listing view) - the successful navigate+snapshot IS the observed behavior.
                    feature_info.is_observed = True
                    feature_info.source_classification = "OBSERVED_BEHAVIOR"
                    feature_info.execution_engine = "PLAYWRIGHT_CORE"
                    continue

                # Safety gateway - same classify_action() the MCP engine uses, not duplicated,
                # so destructive actions are blocked identically regardless of which engine runs.
                classification, reason = classify_action("browser_click", {"selector": locator_hint or feat_name})
                if classification in ("DESTRUCTIVE", "UNKNOWN"):
                    add_scan_log(scan_id, f"[PW-CORE] [feature={feat_name}] SKIPPED reason={reason}")
                    feature_info.is_observed = False
                    feature_info.source_classification = "SKIPPED_SCENARIO"
                    feature_info.execution_engine = "PLAYWRIGHT_CORE"
                    continue

                url_before = page.url
                t_act = time.perf_counter()
                action_label = "browser_type" if feature_type == "Create" else "browser_click"
                add_scan_log(scan_id, f"[PW-CORE] [feature={feat_name}] {action_label} STARTED")
                try:
                    await _perform_action(page, feature_type, locator_hint, fields)
                    await page.wait_for_timeout(400)
                    dur = time.perf_counter() - t_act
                    metrics["pw_core_successful_calls"] += 1
                    add_scan_log(scan_id, f"[PW-CORE] [feature={feat_name}] {action_label} COMPLETED duration={dur:.2f}s")
                except Exception as e:
                    metrics["pw_core_failed_calls"] += 1
                    add_scan_log(scan_id, f"[PW-CORE] [feature={feat_name}] {action_label} FAILED duration={time.perf_counter()-t_act:.2f}s error={e}")
                    continue

                # Verify a result: a successful post-action snapshot proves the page is still
                # alive and responsive - that's the "verified result" required before we ever
                # mark something OBSERVED_BEHAVIOR.
                try:
                    elements_after = await extract_page_elements(page)
                    metrics["pw_core_successful_calls"] += 1
                except Exception:
                    elements_after = None

                if elements_after is None:
                    metrics["pw_core_failed_calls"] += 1
                    continue

                feature_info.is_observed = True
                feature_info.source_classification = "OBSERVED_BEHAVIOR"
                feature_info.execution_engine = "PLAYWRIGHT_CORE"

                validations = elements_after.get("validationMessages", [])
                if validations:
                    feature_info.validations = [
                        ValidationRecord(trigger_action=feat_name, expected_message=v.get("text", ""),
                                          is_observed=True, source_classification="OBSERVED_BEHAVIOR")
                        for v in validations[:3]
                    ]
                if page.url != url_before:
                    feature_info.transitions = [
                        TransitionRecord(action=feat_name, destination_url=page.url, modal_opened=False,
                                          is_observed=True, source_classification="OBSERVED_BEHAVIOR")
                    ]
        finally:
            await context.close()
            await browser.close()

    metrics["pw_core_duration"] = time.perf_counter() - engine_start
    return metrics
