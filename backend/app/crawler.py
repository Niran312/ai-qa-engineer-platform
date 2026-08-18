import asyncio
import os
import re
import urllib.parse
from playwright.async_api import async_playwright
from .config import settings
from .database import add_scan_log, update_scan_data, update_scan_status, update_scan_perf_data
import google.generativeai as genai
import json
from .schemas import ScopeContext
from typing import Optional

def log_pipeline(scan_id: str, scope_ctx: Optional[ScopeContext], tag: str, message: str):
    scope = "unknown"
    parent = "None"
    selected = "None"
    if scope_ctx:
        scope = scope_ctx.scan_scope or "unknown"
        parent = scope_ctx.parent_module or "None"
        selected = scope_ctx.selected_module or "None"
    prefix = f"[{tag}] [scan_id={scan_id}] [scope={scope}] [parent={parent}] [selected={selected}]"
    add_scan_log(scan_id, f"{prefix} {message}")

def get_gemini_selectors(html_snippet: str, api_key: str = None, scan_id: str = None) -> dict:
    """Helper to extract login selectors using Gemini if key is present."""
    gen_key = api_key or settings.GEMINI_API_KEY
    if not gen_key:
        return {}
    try:
        from .database import track_llm_call
        genai.configure(api_key=gen_key)
        model = genai.GenerativeModel("gemini-1.5-flash")
        prompt = f"""
        Analyze the following HTML elements extracted from a login page:
        ```html
        {html_snippet}
        ```
        Identify the CSS selectors for:
        1. The username, email, or login input field.
        2. The password input field.
        3. The login/submit button.
        Return ONLY a JSON object with these keys:
        - "username_selector": (string or null if not found)
        - "password_selector": (string or null if not found)
        - "submit_selector": (string or null if not found)
        Do not wrap in markdown or add explanations. Return raw JSON.
        """
        with track_llm_call(scan_id, "Login Selectors Extraction"):
            response = model.generate_content(prompt)
        text = response.text.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
        return json.loads(text.strip())
    except Exception as e:
        print(f"[CRAWLER] Gemini selector extraction failed: {e}")
        return {}

async def run_heuristics_login_selectors(page) -> tuple:
    """Fallback heuristics to detect login selectors."""
    inputs = await page.query_selector_all("input")
    pwd_sel = None
    user_sel = None
    submit_sel = None

    for inp in inputs:
        itype = await inp.get_attribute("type")
        if itype == "password":
            name = await inp.get_attribute("name")
            iid = await inp.get_attribute("id")
            if iid:
                pwd_sel = f"#{iid}"
            elif name:
                pwd_sel = f"input[name='{name}']"
            else:
                pwd_sel = "input[type='password']"
            break

    if not pwd_sel:
        return None, None, None

    for inp in inputs:
        itype = await inp.get_attribute("type") or "text"
        if itype in ["text", "email", "number"]:
            name = await inp.get_attribute("name")
            iid = await inp.get_attribute("id")
            if name and any(x in name.lower() for x in ["user", "email", "login", "phone", "username"]):
                if iid:
                    user_sel = f"#{iid}"
                else:
                    user_sel = f"input[name='{name}']"
                break

    if not user_sel:
        for inp in inputs:
            itype = await inp.get_attribute("type") or "text"
            if itype in ["text", "email"]:
                name = await inp.get_attribute("name")
                iid = await inp.get_attribute("id")
                if iid:
                    user_sel = f"#{iid}"
                elif name:
                    user_sel = f"input[name='{name}']"
                else:
                    user_sel = "input[type='text']"
                break

    submits = await page.query_selector_all("button, input[type='submit']")
    for btn in submits:
        btype = await btn.get_attribute("type")
        text = await btn.inner_text() or await btn.get_attribute("value") or ""
        text = text.lower()
        if btype == "submit" or any(x in text for x in ["login", "sign", "submit", "enter"]):
            iid = await btn.get_attribute("id")
            name = await btn.get_attribute("name")
            if iid:
                submit_sel = f"#{iid}"
            elif name:
                submit_sel = f"button[name='{name}']" if btype != "submit" else f"input[name='{name}']"
            else:
                submit_sel = "button[type='submit']" if btype == "submit" else "button"
            break

    if not submit_sel:
        submit_sel = "button"

    return user_sel, pwd_sel, submit_sel

async def extract_page_elements(page) -> dict:
    """Extracts rich interactive page elements, dynamic React forms, and table components."""
    title = await page.title()
    url = page.url
    
    js_extractor = """
    () => {
      const selectQuery = (selector) => Array.from(document.querySelectorAll(selector));
      
      const matchesKeyword = (el, keywords) => {
        const text = (el.innerText || el.value || el.placeholder || el.className || el.id || '').toLowerCase();
        return keywords.some(k => text.includes(k));
      };

      // 1. Forms & Fields
      const forms = selectQuery('form').map(form => {
        const fields = Array.from(form.querySelectorAll('input, textarea, select')).map(input => {
          return {
            name: input.getAttribute('name') || input.id || '',
            tag: input.tagName.toLowerCase(),
            type: input.getAttribute('type') || '',
            placeholder: input.getAttribute('placeholder') || '',
            required: input.hasAttribute('required'),
            defaultValue: input.value || input.getAttribute('value') || '',
            label: (() => {
              if (input.id) {
                const labelEl = document.querySelector(`label[for="${input.id}"]`);
                if (labelEl) return labelEl.innerText.trim();
              }
              const parentLabel = input.closest('label');
              if (parentLabel) return parentLabel.innerText.trim();
              return '';
            })()
          };
        });
        return {
          id: form.id || '',
          action: form.getAttribute('action') || '',
          method: form.getAttribute('method') || 'get',
          fields
        };
      });

      // Extract SPA/React-controlled inputs without standard form tags
      const orphanedInputs = selectQuery('input, textarea, select').filter(el => {
        const type = (el.getAttribute('type') || '').toLowerCase();
        if (['button', 'submit', 'hidden'].includes(type)) return false;
        return !el.closest('form');
      });

      if (orphanedInputs.length > 0) {
        forms.push({
          id: 'react-dynamic-form',
          action: 'dynamic-submit',
          method: 'post',
          fields: orphanedInputs.map(input => {
            return {
              name: input.getAttribute('name') || input.id || input.placeholder || 'input',
              tag: input.tagName.toLowerCase(),
              type: input.getAttribute('type') || '',
              placeholder: input.getAttribute('placeholder') || '',
              required: input.hasAttribute('required') || input.getAttribute('aria-required') === 'true',
              defaultValue: input.value || '',
              label: (() => {
                if (input.id) {
                  const labelEl = document.querySelector(`label[for="${input.id}"]`);
                  if (labelEl) return labelEl.innerText.trim();
                }
                const parentLabel = input.closest('label');
                if (parentLabel) return parentLabel.innerText.trim();
                return '';
              })()
            };
          })
        });
      }

      // 2. Buttons
      const buttons = selectQuery('button, input[type="button"], input[type="submit"]').map(btn => {
        return {
          text: (btn.innerText || btn.value || '').trim(),
          type: btn.getAttribute('type') || 'button',
          id: btn.id || '',
          className: btn.className || ''
        };
      }).filter(b => b.text);

      // 3. Dropdowns
      const dropdowns = selectQuery('select').map(sel => {
        const options = Array.from(sel.querySelectorAll('option')).map(opt => ({
          label: opt.innerText.trim(),
          value: opt.value || ''
        }));
        return {
          name: sel.getAttribute('name') || sel.id || '',
          options: options.slice(0, 10)
        };
      });

      // 4. Checkboxes and Radios
      const checkboxes = selectQuery('input[type="checkbox"]').map(ch => ({
        name: ch.getAttribute('name') || ch.id || '',
        required: ch.hasAttribute('required'),
        checked: ch.checked,
        label: ch.closest('label')?.innerText.trim() || ''
      }));
      
      const radios = selectQuery('input[type="radio"]').map(rd => ({
        name: rd.getAttribute('name') || rd.id || '',
        value: rd.value || '',
        checked: rd.checked,
        label: rd.closest('label')?.innerText.trim() || ''
      }));

      // 5. File Uploads & Downloads
      const fileUploads = selectQuery('input[type="file"]').map(fi => ({
        name: fi.getAttribute('name') || fi.id || '',
        required: fi.hasAttribute('required')
      }));
      
      const downloadLinks = selectQuery('a[download], a[href*="download"], a[href$=".zip"], a[href$=".pdf"], a[href$=".xlsx"]').map(a => ({
        text: a.innerText.trim(),
        href: a.getAttribute('href') || ''
      }));

      // 6. Tables
      const tables = selectQuery('table').map(tbl => {
        const headers = Array.from(tbl.querySelectorAll('th')).map(th => th.innerText.trim());
        const rowCount = tbl.querySelectorAll('tr').length;
        return { headers, rowCount };
      });

      // 7. Search, Filters, Sorting
      const searchFields = selectQuery('input').filter(inp => {
        const type = (inp.getAttribute('type') || '').toLowerCase();
        const name = (inp.getAttribute('name') || '').toLowerCase();
        const id = (inp.id || '').toLowerCase();
        const placeholder = (inp.getAttribute('placeholder') || '').toLowerCase();
        return type === 'search' || [name, id, placeholder].some(str => str.includes('search') || str.includes('find') || str.includes('query'));
      }).map(inp => ({
        name: inp.getAttribute('name') || inp.id || '',
        placeholder: inp.getAttribute('placeholder') || ''
      }));

      const filters = selectQuery('button, a, select, div').filter(el => {
        return matchesKeyword(el, ['filter', 'category', 'status-filter', 'dropdown-filter']);
      }).map(el => ({
        text: (el.innerText || el.value || '').trim().slice(0, 50),
        tag: el.tagName.toLowerCase()
      })).slice(0, 10);

      const sortingControls = selectQuery('th, button, a').filter(el => {
        return matchesKeyword(el, ['sort', 'order', 'asc', 'desc', 'orderby']);
      }).map(el => ({
        text: (el.innerText || '').trim().slice(0, 50),
        tag: el.tagName.toLowerCase()
      })).slice(0, 10);

      // 8. Pagination
      const pagination = (() => {
        const pagContainers = selectQuery('div, ul, nav').filter(el => {
          return matchesKeyword(el, ['pagination', 'pager', 'page-navigation']);
        });
        if (pagContainers.length > 0) {
          return pagContainers.map(c => ({
            text: c.innerText.trim().replace(/\\s+/g, ' ').slice(0, 100)
          }));
        }
        const nextPrev = selectQuery('button, a').filter(el => {
          return matchesKeyword(el, ['next', 'prev', 'previous', 'page']);
        });
        return nextPrev.map(el => ({ text: el.innerText.trim(), tag: el.tagName.toLowerCase() })).slice(0, 5);
      })();

      // 9. Modals & Drawers
      const titleOf = (el) => {
        const t = el.querySelector('h1,h2,h3,h4,h5,[class*="title"],[class*="header"]');
        return t ? (t.innerText || '').trim() : '';
      };
      const modals = selectQuery('[class*="modal"], [class*="dialog"], [role="dialog"]').map(el => ({
        id: el.id || '',
        className: el.className || '',
        title: titleOf(el),
        visible: el.offsetHeight > 0 && el.offsetWidth > 0
      })).slice(0, 5);

      const drawers = selectQuery('[class*="drawer"], [class*="sidebar"]').map(el => ({
        id: el.id || '',
        className: el.className || '',
        title: titleOf(el),
        visible: el.offsetHeight > 0 && el.offsetWidth > 0
      })).slice(0, 5);

      // 10. Tabs
      const tabs = selectQuery('[role="tab"], [class*="tab-btn"], [class*="nav-tabs"] a').map(el => ({
        text: el.innerText.trim(),
        href: el.tagName.toLowerCase() === 'a' ? el.href : '',
        active: el.classList.contains('active') || el.getAttribute('aria-selected') === 'true'
      })).slice(0, 10);

      // 11. Validation and Toasts
      const validationMessages = selectQuery('[class*="error"], [class*="validation"], [class*="invalid"]').filter(el => el.offsetHeight > 0).map(el => ({
        text: el.innerText.trim()
      })).slice(0, 5);

      const toasts = selectQuery('[class*="toast"], [class*="notification"], [class*="alert"]').filter(el => el.offsetHeight > 0).map(el => ({
        text: el.innerText.trim()
      })).slice(0, 5);

      return {
        forms,
        buttons,
        selects: dropdowns,
        tables,
        checkboxes,
        radios,
        fileUploads,
        downloadLinks,
        searchFields,
        filters,
        sortingControls,
        pagination,
        modals,
        drawers,
        tabs,
        validationMessages,
        toasts
      };
    }
    """
    
    try:
        js_data = await page.evaluate(js_extractor)
    except Exception as e:
        print(f"[CRAWLER] DOM element extraction evaluation failed: {e}")
        js_data = {}
        
    return {
        "title": title,
        "url": url,
        "forms": js_data.get("forms", []),
        "buttons": js_data.get("buttons", [])[:15],
        "tables": js_data.get("tables", []),
        "selects": js_data.get("selects", []),
        "checkboxes": js_data.get("checkboxes", []),
        "radios": js_data.get("radios", []),
        "fileUploads": js_data.get("fileUploads", []),
        "downloadLinks": js_data.get("downloadLinks", []),
        "searchFields": js_data.get("searchFields", []),
        "filters": js_data.get("filters", []),
        "sortingControls": js_data.get("sortingControls", []),
        "pagination": js_data.get("pagination", []),
        "modals": js_data.get("modals", []),
        "drawers": js_data.get("drawers", []),
        "tabs": js_data.get("tabs", []),
        "validationMessages": js_data.get("validationMessages", []),
        "toasts": js_data.get("toasts", [])
    }

async def discover_dynamic_modules(page) -> dict:
    """
    Analyzes the loaded page's DOM to extract navigation links and hierarchically map parents to children.
    This works generically on typical sidebar layouts, lists, nav elements, nested divs.
    Returns: dict mapping Parent Module Name -> dict of (Child Module Name -> Target URL)
    """
    hierarchy = await page.evaluate('''() => {
        const result = {};
        
        // 1. Helper to normalize text
        const cleanText = (t) => (t || "").replace(/[\\r\\n\\t]+/g, " ").replace(/\\s+/g, " ").trim();
        
        // 2. Try to find sidebar or nav container (prioritize aside/sidebar to avoid notification menus)
        const navContainer = document.querySelector('aside') || 
                             document.querySelector('[class*="sidebar"]') || 
                             document.querySelector('[class*="drawer-side"]') ||
                             document.querySelector('nav') || 
                             document.querySelector('[class*="menu"]') || 
                             document.querySelector('[class*="navigation"]');
        const searchScope = navContainer || document.body;
        
        // Find all links
        const links = Array.from(searchScope.querySelectorAll('a'));
        
        links.forEach(a => {
            const href = a.getAttribute('href');
            if (!href || href.startsWith('#') || href.startsWith('javascript:')) return;
            
            // Resolve full URL
            const resolvedUrl = a.href;
            const text = cleanText(a.innerText || a.textContent);
            if (!text) return;
            
            // Find if there is a parent container representing a dropdown or section header
            let parentLabel = null;
            
            // Search upwards for parent elements with list items or headers
            let current = a.parentElement;
            let depth = 0;
            while (current && current !== searchScope && depth < 6) {
                // Heuristic A: Nested ul/li list headers
                if (current.tagName === 'UL' || current.tagName === 'LI') {
                    // Look for a preceding sibling that is not a list item, or preceding element with text
                    let sibling = current.previousElementSibling;
                    if (sibling && sibling.tagName !== 'LI' && sibling.tagName !== 'UL') {
                        const sibText = cleanText(sibling.innerText || sibling.textContent);
                        if (sibText && sibText.length < 30 && isNaN(sibText)) {
                            parentLabel = sibText;
                            break;
                        }
                    }
                }
                
                // Heuristic B: Grouping by class names like 'group', 'dropdown', 'section'
                const classStr = current.className || '';
                if (typeof classStr === 'string' && (classStr.includes('group') || classStr.includes('dropdown') || classStr.includes('nav-item'))) {
                    // Look for headers inside this container
                    const header = current.querySelector('[class*="header"], [class*="title"], h1, h2, h3, h4, h5, h6');
                    if (header && header !== a) {
                        const hText = cleanText(header.innerText || header.textContent);
                        if (hText && hText.length < 30) {
                            parentLabel = hText;
                            break;
                        }
                    }
                }
                
                current = current.parentElement;
                depth++;
            }
            
            // Default to 'General' if no parent label found
            const parentKey = parentLabel || 'General';
            if (!result[parentKey]) {
                result[parentKey] = {};
            }
            result[parentKey][text] = resolvedUrl;
        });
        
        return result;
    }''')
    return hierarchy

async def crawl_site(scan_id: str, start_url: str, username: str = None, password: str = None, api_key: str = None,
                        scope_ctx: ScopeContext = None, user_description: str = None):
    """Core crawler engine using Playwright BFS crawling with scoped links filtering."""
    log_pipeline(scan_id, scope_ctx, "CRAWLER-SCOPE", f"Starting crawl for: {start_url}")
    storage_state_path = None
    
    parsed_start = urllib.parse.urlparse(start_url)
    start_domain = parsed_start.netloc
    
    # Pre-resolve scoping keywords for MODULE_WISE scans
    allowed_path_patterns = []
    skipped_path_patterns = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-http-cache", "--disable-cache"]
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        await page.set_extra_http_headers({
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        })
        
        try:
            log_pipeline(scan_id, scope_ctx, "CRAWLER-SCOPE", "Loading starting URL...")
            await page.goto(start_url, wait_until="load", timeout=30000)
            
            # --- Handle Login Flow ---
            if username or password:
                log_pipeline(scan_id, scope_ctx, "CRAWLER-SCOPE", "Credentials provided. Searching for login components...")
                form_html = await page.evaluate(
                    "() => Array.from(document.querySelectorAll('form, input, button')).map(el => el.outerHTML).join('\\n')"
                )
                ai_selectors = get_gemini_selectors(form_html[:40000], api_key=api_key, scan_id=scan_id)
                u_sel = ai_selectors.get("username_selector")
                p_sel = ai_selectors.get("password_selector")
                s_sel = ai_selectors.get("submit_selector")
                
                if not (u_sel and p_sel and s_sel):
                    log_pipeline(scan_id, scope_ctx, "CRAWLER-SCOPE", "Using heuristic selector rules...")
                    u_sel, p_sel, s_sel = await run_heuristics_login_selectors(page)
 
                if u_sel and p_sel:
                    log_pipeline(scan_id, scope_ctx, "CRAWLER-SCOPE", f"Login fields found. Username: {u_sel}, Password: {p_sel}")
                    try:
                        await page.fill(u_sel, username or "")
                        await page.fill(p_sel, password or "")
                        if s_sel:
                            log_pipeline(scan_id, scope_ctx, "CRAWLER-SCOPE", f"Clicking submit button: {s_sel}")
                            await page.click(s_sel)
                        else:
                            await page.keyboard.press("Enter")
                        await page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception as e:
                        log_pipeline(scan_id, scope_ctx, "CRAWLER-SCOPE", f"[WARNING] Login interaction failed: {e}")
                    
                    screenshot_name = f"{scan_id}_login_result.png"
                    screenshot_path = os.path.join(settings.SCREENSHOTS_DIR, screenshot_name)
                    await page.screenshot(path=screenshot_path)
                    log_pipeline(scan_id, scope_ctx, "CRAWLER-SCOPE", f"Captured login screen. Logged-in page: {page.url}")

                    # Export the authenticated session (cookies/localStorage) so the separate
                    # MCP browser subprocess can start already logged in via --storage-state,
                    # instead of re-running its own fragile LLM-driven login flow from scratch.
                    try:
                        storage_state_path = os.path.join(settings.STORAGE_STATE_DIR, f"{scan_id}_storage_state.json")
                        await context.storage_state(path=storage_state_path)
                    except Exception as e:
                        log_pipeline(scan_id, scope_ctx, "CRAWLER-SCOPE", f"[WARNING] Failed to export session storage state: {e}")
                        storage_state_path = None
                else:
                    log_pipeline(scan_id, scope_ctx, "CRAWLER-SCOPE", "[WARNING] No password field detected. Skipping login submission.")

            # --- Dynamic Module Discovery & Scope Resolution ---
            await asyncio.sleep(2.0)
            hierarchy = await discover_dynamic_modules(page)
            log_pipeline(scan_id, scope_ctx, "SCOPE-RESOLUTION", f"Discovered dynamic hierarchy: {json.dumps(hierarchy)}")
            
            if scope_ctx and scope_ctx.scan_scope in ("module", "MODULE", "MODULE_WISE"):
                target_url_found = None
                target_key_found = None
                parent_key_found = None
                
                target_parent = (scope_ctx.parent_module or "").lower().strip()
                target_selected = (scope_ctx.selected_module or "").lower().strip()
                
                # Rule 1: Look for exact nested parent -> child match in hierarchy
                for parent_name, children in hierarchy.items():
                    if parent_name.lower().strip() == target_parent:
                        for child_name, child_url in children.items():
                            if child_name.lower().strip() == target_selected:
                                target_url_found = child_url
                                target_key_found = child_name
                                parent_key_found = parent_name
                                break
                    if target_url_found:
                        break
                
                # Rule 2: Look for child link matching target_parent whose URL contains target_selected
                if not target_url_found:
                    for parent_name, children in hierarchy.items():
                        for child_name, child_url in children.items():
                            c_name_lower = child_name.lower().strip()
                            c_url_lower = child_url.lower()
                            if c_name_lower == target_parent and (target_selected in c_url_lower or target_selected.rstrip('s') in c_url_lower):
                                target_url_found = child_url
                                target_key_found = scope_ctx.selected_module
                                parent_key_found = scope_ctx.parent_module
                                break
                        if target_url_found:
                            break

                # Rule 3: Look for any child link matching target_selected directly
                if not target_url_found:
                    for parent_name, children in hierarchy.items():
                        for child_name, child_url in children.items():
                            if child_name.lower().strip() == target_selected:
                                target_url_found = child_url
                                target_key_found = scope_ctx.selected_module
                                parent_key_found = scope_ctx.parent_module
                                break
                        if target_url_found:
                            break

                # Rule 4: Look for parent matching target_selected directly and return its first child
                if not target_url_found:
                    for parent_name, children in hierarchy.items():
                        if parent_name.lower().strip() == target_selected and children:
                            first_child_name = list(children.keys())[0]
                            target_url_found = children[first_child_name]
                            target_key_found = scope_ctx.selected_module
                            parent_key_found = scope_ctx.parent_module
                            break

                # Rule 5: Fallback: check if target_selected or target_parent is a substring in any child URL
                if not target_url_found:
                    for parent_name, children in hierarchy.items():
                        for child_name, child_url in children.items():
                            c_url_lower = child_url.lower()
                            if target_selected in c_url_lower or target_selected.rstrip('s') in c_url_lower:
                                target_url_found = child_url
                                target_key_found = scope_ctx.selected_module
                                parent_key_found = scope_ctx.parent_module
                                break
                        if target_url_found:
                            break

                if target_url_found:
                    if not parent_key_found:
                        parent_key_found = scope_ctx.parent_module
                    if not target_key_found:
                        target_key_found = scope_ctx.selected_module
                    scope_ctx.target_urls = [target_url_found]
                    scope_ctx.allowed_features = [f"{parent_key_found} -> {target_key_found}"]
                    log_pipeline(scan_id, scope_ctx, "SCOPE-RESOLUTION", f"Successfully resolved MODULE scope context: '{parent_key_found} -> {target_key_found}' to URL: {target_url_found}")
                    
                    # Dynamically construct allowed/skipped patterns
                    path_part = urllib.parse.urlparse(target_url_found).path.strip('/')
                    if path_part:
                        allowed_path_patterns.append(path_part)
                    # else: target resolves to the domain root; leave allowed_path_patterns
                    # empty so the generic "is_allowed defaults to True" rule below applies.

                    # Skipped patterns are all other discovered paths
                    for parent_name, children in hierarchy.items():
                        for child_name, child_url in children.items():
                            if child_url != target_url_found:
                                path_part = urllib.parse.urlparse(child_url).path.strip('/')
                                if path_part:
                                    skipped_path_patterns.append(path_part)
                else:
                    err_msg = f"Failed to resolve MODULE scope boundaries for Parent={scope_ctx.parent_module}, Child={scope_ctx.selected_module}"
                    log_pipeline(scan_id, scope_ctx, "SCOPE-RESOLUTION", f"CRITICAL ERROR: {err_msg}")
                    raise ValueError(err_msg)
            
            log_pipeline(scan_id, scope_ctx, "CRAWLER-SCOPE", f"Crawler scope resolution: allowed_path_patterns={allowed_path_patterns}, skipped_path_patterns={skipped_path_patterns}")

            # --- BFS Scoped Crawling Phase ---
            queue = asyncio.Queue()
            await queue.put((page.url, 0))
            
            visited_pages = {}
            edges = []
            skipped_pages_count = 0
            
            log_pipeline(scan_id, scope_ctx, "CRAWLER-SCOPE", "Beginning link exploration...")
            
            while not queue.empty() and len(visited_pages) < settings.MAX_CRAWLED_PAGES:
                current_url, depth = await queue.get()
                normalized_url = urllib.parse.urlsplit(current_url)._replace(fragment="").geturl()
                
                if normalized_url in visited_pages:
                    continue
                
                # Enforce dynamic route scope check at depth >= 1
                if scope_ctx and scope_ctx.scan_scope in ("module", "MODULE", "MODULE_WISE") and depth >= 1:
                    parsed_path = urllib.parse.urlparse(normalized_url).path.lower()
                    is_skipped = any(sk in parsed_path for sk in skipped_path_patterns) if skipped_path_patterns else False
                    is_allowed = any(al in parsed_path for al in allowed_path_patterns) if allowed_path_patterns else True
                    
                    # Dashboard routing exception (always allow entry gate paths)
                    is_dashboard_exception = any(x in parsed_path for x in ["/dashboard", "/login", "/logout"])
                    
                    if (is_skipped or not is_allowed) and not is_dashboard_exception:
                        log_pipeline(scan_id, scope_ctx, "CRAWLER-SCOPE", f"Bypassing URL out of scope: {normalized_url}")
                        skipped_pages_count += 1
                        continue

                add_scan_log(scan_id, f"[CRAWLER] Scanning: {normalized_url} (depth={depth})")
                
                try:
                    await page.goto(normalized_url, wait_until="load", timeout=20000)
                    await page.wait_for_timeout(1000)
                    
                    safe_url_name = re.sub(r'[^a-zA-Z0-9]', '_', normalized_url.replace(start_url, "")) or "index"
                    screenshot_name = f"{scan_id}_{safe_url_name}.png"
                    screenshot_path = os.path.join(settings.SCREENSHOTS_DIR, screenshot_name)
                    await page.screenshot(path=screenshot_path)
                    
                    page_details = await extract_page_elements(page)
                    page_details["screenshot"] = f"/static/screenshots/{screenshot_name}"
                    visited_pages[normalized_url] = page_details
                    
                    nodes = []
                    for u, detail in visited_pages.items():
                        nodes.append({
                            "id": u,
                            "label": detail["title"] or u.replace(f"http://{start_domain}", "").replace(f"https://{start_domain}", ""),
                            "url": u,
                            "screenshot": detail["screenshot"]
                        })
                    
                    current_map = {"nodes": nodes, "edges": edges}
                    update_scan_data(scan_id, app_map=current_map)
                    update_scan_perf_data(scan_id, {
                        "crawler": {
                            "duration": 0.0,
                            "pages_discovered": len(visited_pages),
                            "pages_visited": len(visited_pages)
                        }
                    })
                    
                    # Link expansion
                    if depth < settings.MAX_CRAWL_DEPTH:
                        anchors = await page.query_selector_all("a")
                        for anchor in anchors:
                            href = await anchor.get_attribute("href")
                            if not href:
                                continue
                            
                            full_href = urllib.parse.urljoin(normalized_url, href)
                            parsed_href = urllib.parse.urlparse(full_href)
                            href_domain = parsed_href.netloc
                            
                            is_same_domain = (href_domain == start_domain)
                            is_http = parsed_href.scheme in ["http", "https"]
                            file_ext_match = re.search(r'\.(pdf|png|jpg|jpeg|zip|tar|gz|mp4|css|js)$', parsed_href.path, re.I)
                            is_action = any(x in parsed_href.path.lower() or x in parsed_href.query.lower() for x in ["logout", "signout", "delete", "destroy", "remove"])
                            
                            if is_same_domain and is_http and not file_ext_match and not is_action:
                                clean_href = urllib.parse.urlsplit(full_href)._replace(fragment="").geturl()
                                if clean_href not in visited_pages:
                                    await queue.put((clean_href, depth + 1))
                                
                                edge = {"source": normalized_url, "target": clean_href}
                                if edge not in edges and normalized_url != clean_href:
                                    edges.append(edge)
                                    current_map["edges"] = edges
                                    update_scan_data(scan_id, app_map=current_map)
                
                except Exception as e:
                    log_pipeline(scan_id, scope_ctx, "CRAWLER-SCOPE", f"[WARNING] Error visiting {normalized_url}: {e}")
                    continue
            
            log_pipeline(scan_id, scope_ctx, "CRAWLER-SCOPE", f"Exploration completed. Discovered {len(visited_pages)} pages. Skipped {skipped_pages_count} pages due to scope.")
            return visited_pages, edges, skipped_pages_count, storage_state_path
            
        except Exception as e:
            log_pipeline(scan_id, scope_ctx, "CRAWLER-SCOPE", f"[ERROR] Crawler failed: {e}")
            raise e
        finally:
            await browser.close()
