# AI Tool: Website Search
# Module: Chatbot
#
# Three-tier search pipeline:
#   1. RAG  — semantic similarity via the configured Knowledge Base vector store
#   2. Intent expansion — LLM expands query into synonyms, run keyword search with them
#   3. Keyword fallback — SQL LIKE on title/description fields
#
# All tiers respect the per-Chatbot-Settings toggles (show_blog_posts, etc.)
# and the result_limit ceiling.

import frappe
import json
import re
from langchain.tools import tool


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

STOP_WORDS = {
    "for", "in", "the", "a", "an", "with", "and", "to", "of",
    "that", "about", "how", "what", "is", "are", "i", "we",
    "my", "our", "me", "do", "can", "you",
}

PRODUCT_INTENT_WORDS = {
    "product", "buy", "price", "equipment", "attachment", "machine",
    "plough", "spreader", "tractor", "mower", "tool", "sweeper",
    "gritter", "de-icer", "salt", "compactor",
}

NEGATIVE_EXCLUSIONS = {
    "erp", "erpnext", "frappe", "ai integration",
    "digital transformation", "software", "saas",
}


# ─────────────────────────────────────────────────────────────────────────────
# Settings helpers
# ─────────────────────────────────────────────────────────────────────────────

def _search_settings() -> dict:
    settings = getattr(frappe.local, "website_search_settings", None) or {}
    return {
        "result_limit": min(max(int(settings.get("result_limit") or 9), 1), 12),
        "show_website_items": settings.get("show_website_items", True),
        "show_item_groups": settings.get("show_item_groups", True),
        "show_blog_posts": settings.get("show_blog_posts", True),
        "show_web_pages": settings.get("show_web_pages", True),
        "knowledge_base": settings.get("knowledge_base") or None,
    }


def _extract_keywords(query: str) -> list:
    words = re.findall(r"\b\w+\b", query.lower())
    return [w for w in words if w not in STOP_WORDS and len(w) >= 2]


# ─────────────────────────────────────────────────────────────────────────────
# Shared result helpers
# ─────────────────────────────────────────────────────────────────────────────

def _clean_result(result: dict) -> dict:
    """Strip internal 'score' key and truncate description."""
    result.pop("score", None)
    if result.get("description"):
        result["description"] = result["description"][:200]
    return result


def _merge_unique(base: list, new_items: list, limit: int) -> list:
    """Append new_items to base, deduplicating by url, up to limit."""
    seen = {r.get("url") for r in base if r.get("url")}
    for item in new_items:
        if len(base) >= limit:
            break
        url = item.get("url")
        if url and url in seen:
            continue
        base.append(item)
        if url:
            seen.add(url)
    return base


# ─────────────────────────────────────────────────────────────────────────────
# TIER 1 — RAG (Knowledge Base vector search)
# ─────────────────────────────────────────────────────────────────────────────

def _url_to_frappe_result(url: str, settings: dict, score: float) -> dict | None:
    """
    Given a URL stored in the knowledge base (e.g. https://site.com/products/sweeper),
    resolve it back to the matching Frappe record and return a search result dict,
    or None if the content type is disabled or not found.
    """
    if not url:
        return None

    path = url.strip()
    for prefix in ("https://", "http://"):
        if path.startswith(prefix):
            path = "/" + path.split("/", 3)[-1]
            break
            
    slug = path.rstrip("/").split("/")[-1]
    if not slug:
        return None

    try:
        if settings["show_website_items"]:
            wi = frappe.get_all(
                "Website Item",
                filters={"route": ["like", f"%{slug}%"], "published": 1},
                fields=["web_item_name", "short_description", "website_image", "route", "item_group"],
                limit=1,
            )
            if wi:
                wi = wi[0]
                return {
                    "doctype": "Website Item",
                    "title": wi.web_item_name or "",
                    "description": (wi.short_description or "")[:200],
                    "image": wi.website_image or "",
                    "url": f"/{wi.route}" if wi.route else "",
                    "type": "product",
                    "category": wi.item_group or "",
                    "score": score,
                }
    except Exception:
        pass

    try:
        if settings["show_blog_posts"]:
            bp = frappe.get_all(
                "Blog Post",
                filters={"route": ["like", f"%{slug}%"], "published": 1},
                fields=["title", "blog_intro", "meta_image", "route", "blog_category"],
                limit=1,
            )
            if bp:
                bp = bp[0]
                return {
                    "doctype": "Blog Post",
                    "title": bp.title or "",
                    "description": (bp.blog_intro or "")[:200],
                    "image": bp.meta_image or "",
                    "url": f"/{bp.route}" if bp.route else "",
                    "type": "blog",
                    "category": bp.blog_category or "",
                    "score": score,
                }
    except Exception:
        pass

    try:
        if settings["show_web_pages"]:
            wp = frappe.get_all(
                "Web Page",
                filters={"route": ["like", f"%{slug}%"], "published": 1},
                fields=["title", "meta_description", "meta_image", "route"],
                limit=1,
            )
            if wp:
                wp = wp[0]
                return {
                    "doctype": "Web Page",
                    "title": wp.title or "",
                    "description": (wp.meta_description or "")[:200],
                    "image": wp.meta_image or "",
                    "url": f"/{wp.route}" if wp.route else "",
                    "type": "page",
                    "score": score,
                }
    except Exception:
        pass

    try:
        if settings["show_item_groups"]:
            ig = frappe.get_all(
                "Website Itemgroup",
                filters={"route": ["like", f"%{slug}%"], "show_in_website": 1},
                fields=["website_itemgroup_name", "description", "image", "route"],
                limit=1,
            )
            if ig:
                ig = ig[0]
                return {
                    "doctype": "Website Itemgroup",
                    "title": ig.website_itemgroup_name or "",
                    "description": (ig.description or "")[:200],
                    "image": ig.image or "",
                    "url": f"/{ig.route}" if ig.route else "",
                    "type": "category",
                    "score": score,
                }
    except Exception:
        pass

    return None


def _rag_search(query: str, settings: dict, limit: int) -> list:
    """
    Tier 1: Query the configured Knowledge Base via vector similarity.
    Returns a list of result dicts (without 'score') up to `limit`.
    """
    kb_name = settings.get("knowledge_base")
    if not kb_name:
        return []

    if not frappe.db.exists("Knowledge Base", kb_name):
        return []

    try:
        kb = frappe.get_doc("Knowledge Base", kb_name)
        store = kb.get_vector_store()
        # Fetch slightly more than limit to account for unresolvable URLs
        raw = store.search(query, k=limit * 3)
    except Exception as e:
        frappe.log_error(f"Website Search RAG error: {e}", "Website Search")
        return []

    results = []
    for hit in raw:
        if len(results) >= limit:
            break
        meta = hit.get("metadata") or {}
        url = meta.get("url") or ""
        score = hit.get("score", 1.0)

        result = _url_to_frappe_result(url, settings, score)
        if result:
            results.append(result)

    # Sort by score ascending (lower distance = better for cosine/L2 stores)
    results.sort(key=lambda x: x.get("score", 1.0))
    return results


# ─────────────────────────────────────────────────────────────────────────────
# TIER 2 — Intent / semantic expansion
# ─────────────────────────────────────────────────────────────────────────────

def _expand_query(query: str) -> list:
    """
    Use the chatforge AI agent's underlying LLM to expand the query into a list
    of semantically related search terms.  Returns a flat list of keyword strings.

    Falls back silently to an empty list so the caller can proceed to Tier 3.
    """
    try:
        # Pick an active non-embedding LLM
        providers = frappe.get_all("LLM Provider", filters={}, fields=["name"], limit=1)
        if not providers:
            return []
        llms = frappe.get_all(
            "LLM",
            filters={"provider": providers[0].name, "is_embedding_model": 0},
            fields=["name"],
            limit=1,
        )
        if not llms:
            return []

        llm_doc = frappe.get_doc("LLM", llms[0].name)
        llm = llm_doc.llm

        prompt = (
            f"You are a search query expander for a website search engine. "
            f"Given the user query below, return a JSON array of 5-8 specific "
            f"product names, category names, and related terms that a website "
            f"selling outdoor maintenance equipment might use. Only output a "
            f"JSON array of strings, nothing else.\n\nQuery: {query}"
        )
        response = llm.invoke(prompt)
        text = getattr(response, "content", str(response)).strip()

        # Strip code fences if present
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)

        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(t).lower().strip() for t in parsed if t]
    except Exception:
        pass

    return []


def _keyword_search_with_terms(
    terms: list,
    settings: dict,
    limit: int,
    query_has_negative: bool,
    has_product_intent: bool,
) -> list:
    """
    Run SQL LIKE search using the provided list of terms (may be expanded or original).
    Shared by both Tier 2 (expanded terms) and Tier 3 (original keywords).
    """
    results = []

    def _score(title: str, desc: str) -> int:
        score = 0
        title_l = title.lower()
        desc_l = desc.lower()
        for t in terms:
            if t in title_l:
                score += 2
            if t in desc_l:
                score += 1
        return score

    # ── Blog Posts ────────────────────────────────────────────────────────────
    if settings["show_blog_posts"]:
        try:
            or_filters = []
            for t in terms:
                or_filters.extend([
                    ["title", "like", f"%{t}%"],
                    ["blog_intro", "like", f"%{t}%"],
                    ["blog_category", "like", f"%{t}%"],
                ])
            blogs = frappe.get_list(
                "Blog Post",
                filters={"published": 1},
                or_filters=or_filters,
                fields=["title", "blog_intro", "meta_image", "route", "blog_category"],
                limit_page_length=30,
                order_by="modified desc",
                ignore_permissions=True,
            )
            for blog in blogs:
                title = blog.get("title") or ""
                desc = blog.get("blog_intro") or ""
                title_lower = title.lower()
                if not query_has_negative and any(neg in title_lower for neg in NEGATIVE_EXCLUSIONS):
                    continue
                score = _score(title, desc)
                if score > 0:
                    results.append({
                        "doctype": "Blog Post",
                        "title": title,
                        "description": desc[:200],
                        "image": blog.get("meta_image") or "",
                        "url": f"/{blog.get('route')}" if blog.get("route") else "",
                        "type": "blog",
                        "category": blog.get("blog_category") or "",
                        "score": score,
                    })
        except Exception:
            pass

    # ── Web Pages ─────────────────────────────────────────────────────────────
    if settings["show_web_pages"]:
        try:
            or_filters = []
            for t in terms:
                or_filters.extend([
                    ["title", "like", f"%{t}%"],
                    ["meta_description", "like", f"%{t}%"],
                ])
            web_pages = frappe.get_list(
                "Web Page",
                filters={"published": 1},
                or_filters=or_filters,
                fields=["title", "meta_description", "meta_image", "route"],
                limit_page_length=30,
                order_by="modified desc",
                ignore_permissions=True,
            )
            for wp in web_pages:
                title = wp.get("title") or ""
                desc = wp.get("meta_description") or ""
                title_lower = title.lower()
                if not query_has_negative and any(neg in title_lower for neg in NEGATIVE_EXCLUSIONS):
                    continue
                score = _score(title, desc)
                if score > 0:
                    results.append({
                        "doctype": "Web Page",
                        "title": title,
                        "description": desc[:200],
                        "image": wp.get("meta_image") or "",
                        "url": f"/{wp.get('route')}" if wp.get("route") else "",
                        "type": "page",
                        "score": score,
                    })
        except Exception:
            pass

    # ── Website Items ─────────────────────────────────────────────────────────
    if settings["show_website_items"]:
        try:
            or_filters = []
            for t in terms:
                or_filters.extend([
                    ["web_item_name", "like", f"%{t}%"],
                    ["short_description", "like", f"%{t}%"],
                    ["description", "like", f"%{t}%"],
                ])
            website_items = frappe.get_list(
                "Website Item",
                filters={"published": 1},
                or_filters=or_filters,
                fields=["web_item_name", "short_description", "website_image", "route", "item_group"],
                limit_page_length=30,
                order_by="modified desc",
                ignore_permissions=True,
            )
            for wi in website_items:
                title = wi.get("web_item_name") or ""
                desc = wi.get("short_description") or ""
                score = _score(title, desc)
                if score > 0:
                    if has_product_intent:
                        score += 10
                    results.append({
                        "doctype": "Website Item",
                        "title": title,
                        "description": desc[:200],
                        "image": wi.get("website_image") or "",
                        "url": f"/{wi.get('route')}" if wi.get("route") else "",
                        "type": "product",
                        "category": wi.get("item_group") or "",
                        "score": score,
                    })
        except Exception:
            pass

    # ── Item Groups ───────────────────────────────────────────────────────────
    if settings["show_item_groups"]:
        try:
            or_filters = []
            for t in terms:
                or_filters.append(["website_itemgroup_name", "like", f"%{t}%"])
            item_groups = frappe.get_list(
                "Website Itemgroup",
                filters={"show_in_website": 1},
                or_filters=or_filters,
                fields=["website_itemgroup_name", "description", "image", "route"],
                limit_page_length=15,
                order_by="modified desc",
                ignore_permissions=True,
            )
            for ig in item_groups:
                title = ig.get("website_itemgroup_name") or ""
                desc = ig.get("description") or ""
                score = _score(title, desc)
                if score > 0:
                    if has_product_intent:
                        score += 10
                    results.append({
                        "doctype": "Website Itemgroup",
                        "title": title,
                        "description": desc[:200],
                        "image": ig.get("image") or "",
                        "url": f"/{ig.get('route')}" if ig.get("route") else "",
                        "type": "category",
                        "score": score,
                    })
        except Exception:
            pass

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:limit]


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def _perform_frappe_search(query: str) -> list:
    """
    Three-tier search pipeline. Returns a clean list of result dicts (no 'score').
    """
    if not query or not query.strip():
        return []

    query = query.strip()
    query_lower = query.lower()
    settings = _search_settings()
    limit = settings["result_limit"]

    query_has_negative = any(neg in query_lower for neg in NEGATIVE_EXCLUSIONS)
    has_product_intent = any(pi in query_lower for pi in PRODUCT_INTENT_WORDS)
    keywords = _extract_keywords(query_lower) or [w for w in query_lower.split() if len(w) >= 2]

    results = []

    # ── Tier 1: RAG ───────────────────────────────────────────────────────────
    rag_results = _rag_search(query, settings, limit)
    results = _merge_unique(results, rag_results, limit)

    # ── Tier 2: Semantic intent expansion ─────────────────────────────────────
    if len(results) < limit:
        expanded_terms = _expand_query(query)
        if expanded_terms:
            all_terms = list(dict.fromkeys(expanded_terms + keywords))  # expanded first, then original
            expanded_results = _keyword_search_with_terms(
                all_terms, settings, limit - len(results),
                query_has_negative, has_product_intent,
            )
            results = _merge_unique(results, expanded_results, limit)

    # ── Tier 3: Keyword fallback ───────────────────────────────────────────────
    if len(results) < limit:
        keyword_results = _keyword_search_with_terms(
            keywords, settings, limit - len(results),
            query_has_negative, has_product_intent,
        )
        results = _merge_unique(results, keyword_results, limit)

    return [_clean_result(r) for r in results]


# ─────────────────────────────────────────────────────────────────────────────
# LangChain tool definition
# ─────────────────────────────────────────────────────────────────────────────

@tool("website_search", return_direct=False)
def website_search_tool(query: str) -> str:
    """
    Search the website for blog posts, web pages, products, and item groups
    matching the given query using RAG (vector similarity), semantic intent
    expansion, and keyword fallback — in that order.

    Args:
        query: The search query string.

    Returns:
        A JSON string containing an array of search results, each with
        title, description, image, url, type, and doctype fields.
    """
    results = _perform_frappe_search(query)
    if not results:
        return json.dumps({"message": "No results found for the query.", "results": []})
    return json.dumps(results)
