import frappe
import html as html_lib
import json
import re
from urllib.parse import urlparse
from frappe import _

BAD_SUMMARY_MARKERS = ('"results":', "'results':", "results=[", '"ai_summary":')


def _request_host():
    request = getattr(frappe.local, "request", None)
    if not request:
        return ""
    host = request.headers.get("Host") or request.host or ""
    return host.split(":")[0].lower()


def _domain_from_url(url):
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        return urlparse(url).netloc.split(":")[0].lower()
    except Exception:
        return ""


def _domain_matches(host, configured):
    configured = (configured or "").strip().lower()
    if not host or not configured:
        return False
    if configured.startswith("*."):
        base = configured[2:]
        return host == base or host.endswith("." + base)
    return host == configured or host.endswith("." + configured)


def _get_website_search_settings():
    if not frappe.db.get_single_value("Website Settings", "navbar_search"):
        return None

    host = _request_host()
    settings = frappe.get_all(
        "Chatbot Settings",
        filters={"enabled": 1, "enable_website_search": 1},
        fields=[
            "name",
            "bot_name",
            "bot_avatar",
            "website_url",
            "ai_agent",
            "website_search_agent",
            "website_search_knowledge_base",
            "website_search_result_limit",
            "website_search_show_website_items",
            "website_search_show_item_groups",
            "website_search_show_blog_posts",
            "website_search_show_web_pages",
        ],
        order_by="creation asc",
        limit_page_length=50,
    )

    if not settings:
        return None

    for row in settings:
        if _domain_matches(host, _domain_from_url(row.website_url)):
            return row

    allowed_domains = frappe.db.sql(
        """
        SELECT parent, domain
        FROM `tabChatbot Allowed Domain`
        WHERE parenttype = 'Chatbot Settings'
        """,
        as_dict=True,
    )
    for domain_row in allowed_domains:
        if _domain_matches(host, domain_row.domain):
            match = next((row for row in settings if row.name == domain_row.parent), None)
            if match:
                return match

    return settings[0] if len(settings) == 1 else None


@frappe.whitelist(allow_guest=True)
def get_search_config():
    settings = _get_website_search_settings()
    if not settings:
        return {"enabled": False}

    agent_name = settings.website_search_agent or settings.ai_agent
    if not agent_name or not frappe.db.exists("AI Agent", agent_name):
        return {"enabled": False}

    return {
        "enabled": True,
        "bot_name": settings.bot_name,
        "bot_avatar": settings.bot_avatar,
        "agent": agent_name,
        "result_limit": _search_result_limit(settings),
    }


def _search_result_limit(settings):
    try:
        return min(max(int(settings.website_search_result_limit or 9), 1), 12)
    except Exception:
        return 9


def _truthy(value):
    if isinstance(value, str):
        return value.lower() in ("1", "true", "yes", "y", "on")
    return bool(value)


def _search_log_filters(query, fingerprint=None):
    user = frappe.session.user
    ip = frappe.local.request_ip if hasattr(frappe.local, "request_ip") else ""

    filters = {"query": query}
    if fingerprint:
        filters["user_fingerprint"] = fingerprint
    elif user != "Guest":
        filters["owner"] = user
    elif ip:
        filters["visitor_ip"] = ip

    return filters


def _get_cached_search_result(query, fingerprint=None, result_limit=9):
    cached_logs = frappe.get_all(
        "Website Search Log",
        filters=_search_log_filters(query, fingerprint),
        fields=["ai_summary", "ai_results"],
        order_by="creation desc",
        limit_page_length=1,
    )
    if not cached_logs:
        return None

    cached = cached_logs[0]
    ai_summary = cached.get("ai_summary") or ""
    ai_results = cached.get("ai_results") or ""
    if not ai_summary and not ai_results:
        return None

    results_list = _coerce_results(ai_results, limit=result_limit)
    ai_summary, results_list = _normalize_search_response(query, ai_summary, results_list, result_limit)
    return {
        "success": True,
        "ai_summary": ai_summary,
        "results": results_list,
        "cached": True,
    }


def _website_search_context(settings):
    return {
        "result_limit": _search_result_limit(settings),
        "show_website_items": bool(settings.website_search_show_website_items),
        "show_item_groups": bool(settings.website_search_show_item_groups),
        "show_blog_posts": bool(settings.website_search_show_blog_posts),
        "show_web_pages": bool(settings.website_search_show_web_pages),
        "knowledge_base": settings.website_search_knowledge_base or None,
    }


def _top_up_results(query, results_list, limit):
    results_list = list(results_list or [])[:limit]
    if len(results_list) >= limit:
        return results_list

    try:
        from chatforge.chatbot.ai_tools.website_search import _perform_frappe_search
        direct_results = _perform_frappe_search(query)
    except Exception:
        return results_list

    seen_urls = {result.get("url") for result in results_list if result.get("url")}
    for result in direct_results:
        result = _clean_result(result)
        url = result.get("url")
        if url and url in seen_urls:
            continue
        results_list.append(result)
        if url:
            seen_urls.add(url)
        if len(results_list) >= limit:
            break

    return results_list


def _decode_json_string(value):
    try:
        return json.loads(f'"{value}"')
    except Exception:
        return value


def _summary_from_payload_text(value):
    if not isinstance(value, str):
        return ""

    parsed = _loads_relaxed(value)
    if isinstance(parsed, dict):
        return parsed.get("ai_summary") or ""

    match = re.search(
        r'"ai_summary"\s*:\s*"(?P<summary>(?:\\.|[^"\\])*)"',
        value,
        flags=re.DOTALL,
    )
    if match:
        return _decode_json_string(match.group("summary")).strip()

    match = re.search(
        r"ai_summary\s*=\s*(?P<quote>['\"])(?P<summary>.*?)(?P=quote)",
        value,
        flags=re.DOTALL,
    )
    if match:
        return match.group("summary").strip()

    return ""


def _fallback_summary(query, results_list, bot_name=None):
    brand = bot_name or "our team"
    if results_list:
        first_titles = [result.get("title") for result in results_list[:2] if result.get("title")]
        if first_titles:
            names = " and ".join(f"**{title}**" for title in first_titles)
            return f"For this search, the strongest matches are {names}. Review the recommended products and pages below to compare the most relevant options."
    return f"I could not find a strong match for that query, but {brand} can help identify the right option for your needs."


def _normalize_search_response(query, ai_summary, results_list, result_limit, bot_name=None):
    results_list = _top_up_results(query, results_list, result_limit)
    ai_summary = (ai_summary or "").strip()

    if _is_bad_summary(ai_summary):
        extracted_summary = _summary_from_payload_text(ai_summary)
        if extracted_summary and not _is_bad_summary(extracted_summary):
            ai_summary = extracted_summary
        else:
            ai_summary = _fallback_summary(query, results_list, bot_name=bot_name)
    elif not ai_summary and results_list:
        ai_summary = _fallback_summary(query, results_list, bot_name=bot_name)

    return ai_summary, results_list


def _is_bad_summary(value):
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    return (
        any(marker in value for marker in BAD_SUMMARY_MARKERS)
        or (stripped.startswith("{") and '"ai_summary"' in stripped)
        or (stripped.startswith("[") and '"doctype"' in stripped)
    )


def _model_to_dict(value):
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    if isinstance(value, dict):
        return value
    return None


def _clean_result_description(value, max_length=220):
    text = html_lib.unescape(str(value or ""))
    text = re.sub(r"<[^>]*>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_length:
        return text[:max_length].rstrip() + "..."
    return text


def _clean_result(result):
    result["description"] = _clean_result_description(result.get("description", ""))
    return result


def _coerce_results(raw_results, limit=9):
    results_list = []
    if isinstance(raw_results, str):
        raw_results = _loads_relaxed(raw_results) or []
    if not isinstance(raw_results, list):
        return results_list

    for result in raw_results[:limit]:
        result_dict = _model_to_dict(result)
        if result_dict:
            results_list.append(_clean_result(result_dict))
        else:
            results_list.append(_clean_result({
                "title": str(result),
                "url": "",
                "description": "",
                "doctype": "",
                "image": "",
                "type": "page",
            }))
    return results_list


def _strip_code_fence(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _loads_relaxed(text):
    if not isinstance(text, str):
        return text

    text = _strip_code_fence(text)
    if not text:
        return None

    try:
        return json.loads(text, strict=False)
    except Exception:
        pass

    # LLM/tool output sometimes escapes apostrophes as \', which is invalid JSON.
    try:
        return json.loads(text.replace("\\'", "'"), strict=False)
    except Exception:
        return None


def _extract_balanced_json(text):
    if not isinstance(text, str):
        return None

    for opening, closing in (("{", "}"), ("[", "]")):
        start = text.find(opening)
        while start != -1:
            depth = 0
            in_string = False
            escape = False
            for index in range(start, len(text)):
                char = text[index]
                if escape:
                    escape = False
                    continue
                if char == "\\":
                    escape = True
                    continue
                if char == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if char == opening:
                    depth += 1
                elif char == closing:
                    depth -= 1
                    if depth == 0:
                        return text[start:index + 1]
            start = text.find(opening, start + 1)
    return None


def _extract_tool_content(value):
    if isinstance(value, dict):
        if "content" in value:
            return value.get("content")
        if "website_search_response" in value:
            return _extract_tool_content(value.get("website_search_response"))
    return value



def _parse_agent_payload(payload):
    payload_dict = _model_to_dict(payload)
    if payload_dict:
        return (
            payload_dict.get("ai_summary", "") or "",
            _coerce_results(payload_dict.get("results", [])),
        )

    if isinstance(payload, dict):
        output = payload.get("output") or payload.get("content")
        if output:
            return _parse_agent_payload(output)

    if not isinstance(payload, str):
        return "", []

    text = payload.strip()

    # Older prompt versions asked for: consultant summary, delimiter, raw results.
    for delimiter in ("<<<SEARCH_RESULTS_FOLLOW>>>", "<<<<>>>>", "<<>>"):
        if delimiter in text:
            summary, raw_results = text.split(delimiter, 1)
            parsed_results = _loads_relaxed(raw_results)
            parsed_results = _extract_tool_content(parsed_results)
            parsed_results = _loads_relaxed(parsed_results)
            return summary.strip(), _coerce_results(parsed_results)

    parsed = _loads_relaxed(text)
    parsed = _extract_tool_content(parsed)
    if parsed is not None and parsed is not text:
        return _parse_agent_payload(parsed)

    json_text = _extract_balanced_json(text)
    if json_text:
        parsed = _loads_relaxed(json_text)
        parsed = _extract_tool_content(parsed)
        if isinstance(parsed, list):
            return text[:text.find(json_text)].strip(), _coerce_results(parsed)
        if isinstance(parsed, dict):
            return _parse_agent_payload(parsed)

    return text, []


@frappe.whitelist(allow_guest=True)
def search(query=None, fingerprint=None, session_id=None, use_cache=False):
    """
    API endpoint for the AI-powered website search.
    Invokes the 'Website Search Agent' with the user's query.
    """
    if not query:
        return {"success": False, "ai_summary": "", "results": []}

    query = query.strip()
    search_settings = _get_website_search_settings()
    if not search_settings:
        return {"success": False, "ai_summary": "", "results": [], "disabled": True}

    agent_name = search_settings.website_search_agent or search_settings.ai_agent
    if not agent_name:
        return {"success": False, "ai_summary": "", "results": [], "disabled": True}
    
    result_limit = _search_result_limit(search_settings)

    if _truthy(use_cache):
        try:
            cached_result = _get_cached_search_result(query, fingerprint, result_limit)
            if cached_result:
                return cached_result
        except Exception as e:
            frappe.log_error(f"Search API Cache Read Error: {str(e)}", "Website Search API")

    try:
        # We need to run the agent as Administrator since guests can't normally invoke AI agents directly
        original_user = frappe.session.user
        frappe.set_user("Administrator")
        
        try:
            # Look for configured AI Agent
            if not frappe.db.exists("AI Agent", agent_name):
                frappe.log_error(f"AI Agent '{agent_name}' not found", "Website Search API")
                return {
                    "success": False, 
                    "ai_summary": "Search agent is currently being configured. Please try again later.",
                    "results": []
                }
                
            agent_doc = frappe.get_doc("AI Agent", agent_name)
            if search_settings.website_search_knowledge_base:
                agent_doc.knowledge_base = search_settings.website_search_knowledge_base
            agent_service = agent_doc.agent_service
            frappe.local.website_search_settings = _website_search_context(search_settings)
            
            # Capture the raw LLM text before Pydantic validation so we can
            # parse the content ourselves if Pydantic raises a validation error.
            # Guard with hasattr — if the internal method is renamed in a future
            # agent library version this degrades gracefully instead of crashing.
            raw_output_text = []
            _has_parser = hasattr(agent_service.agent, "_parse_output_to_pydantic")
            if _has_parser:
                original_parser = agent_service.agent._parse_output_to_pydantic

                def tracking_parser(output_text):
                    raw_output_text.append(output_text)
                    return original_parser(output_text)

                agent_service.agent._parse_output_to_pydantic = tracking_parser

            bot_name = search_settings.bot_name

            # Invoke the agent — same pattern as kersten_ai_seo/api/ai_preview.py
            try:
                result = agent_service.invoke(query=query)

                ai_summary, results_list = _parse_agent_payload(result)
                ai_summary, results_list = _normalize_search_response(
                    query, ai_summary, results_list, result_limit, bot_name=bot_name
                )

            except Exception as parse_err:
                # Use the captured raw LLM output, or fallback to the error string
                err_str = raw_output_text[0] if raw_output_text else str(parse_err)
                ai_summary, results_list = _parse_agent_payload(err_str)
                ai_summary, results_list = _normalize_search_response(
                    query, ai_summary, results_list, result_limit, bot_name=bot_name
                )
                if ai_summary and not results_list:
                    results_list = _top_up_results(query, [], result_limit)
                if not ai_summary and not results_list:
                    frappe.log_error(
                        f"Search API JSON Fallback Parse Error: {str(parse_err)}\n{frappe.get_traceback()}",
                        "Website Search API"
                    )

            finally:
                # Always restore the original parser if we patched it
                if _has_parser:
                    agent_service.agent._parse_output_to_pydantic = original_parser
            
            # Log the search
            try:
                client_ip = frappe.local.request_ip if hasattr(frappe.local, "request_ip") else ""
                log_doc = frappe.get_doc({
                    "doctype": "Website Search Log",
                    "query": query,
                    "visitor_ip": client_ip,
                    "user_fingerprint": fingerprint,
                    "session_id": session_id,
                    "results_count": len(results_list),
                    "ai_summary": ai_summary,
                    "ai_results": json.dumps(results_list),
                })
                log_doc.insert(ignore_permissions=True)
                frappe.db.commit()
            except Exception as e:
                frappe.log_error(f"Failed to log search: {str(e)}", "Website Search API")

            return {
                "success": True,
                "ai_summary": ai_summary,
                "results": results_list
            }
            
        finally:
            if hasattr(frappe.local, "website_search_settings"):
                delattr(frappe.local, "website_search_settings")
            frappe.set_user(original_user)
            
    except Exception as e:
        frappe.log_error(f"Search API Error: {str(e)}\n{frappe.get_traceback()}", "Website Search API")
        return {
            "success": False, 
            "ai_summary": "Sorry, I encountered an error while searching. Please try again.",
            "results": []
        }

# fast_search removed — single AI request with animated status messages is used instead

@frappe.whitelist(allow_guest=True)
def get_recent_history(fingerprint=None, limit=5):
    """Fetches the last unique queries for the current user/IP/fingerprint"""
    try:
        user = frappe.session.user
        ip = frappe.local.request_ip if hasattr(frappe.local, 'request_ip') else ""
        
        filters = {}
        if fingerprint:
            filters["user_fingerprint"] = fingerprint
        elif user != "Guest":
            filters["owner"] = user
        elif ip:
            filters["visitor_ip"] = ip
        else:
            return {"success": True, "history": []}
            
        logs = frappe.get_all(
            "Website Search Log", 
            filters=filters, 
            fields=["query"], 
            order_by="creation desc", 
            limit_page_length=20
        )
        
        history = []
        seen = set()
        for log in logs:
            q = log.query.strip()
            if q and q.lower() not in seen:
                history.append(q)
                seen.add(q.lower())
                if len(history) >= int(limit):
                    break
                    
        return {"success": True, "history": history}
    except Exception as e:
        frappe.log_error(f"History API Error: {str(e)}", "Website Search API")
        return {"success": False, "history": []}

@frappe.whitelist(allow_guest=True)
def get_client_ip():
    """Returns the client's IP address for fingerprinting"""
    ip = frappe.local.request_ip if hasattr(frappe.local, 'request_ip') else "127.0.0.1"
    return {"success": True, "ip": ip}
