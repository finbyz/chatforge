"""
Chatbot SaaS API Endpoints
==========================
Production-ready endpoints with:
- Domain validation and security
- IP address and geolocation capture
- Visitor metadata extraction
- AI Agent integration via AgentService
- System prompt and Knowledge Base support
"""

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
import json
import requests
from urllib.parse import urlparse


# ═══════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

def get_visitor_ip():
    """
    Get the real IP address of the visitor.
    Handles proxies, load balancers, and CDNs.
    """
    headers = frappe.request.headers if frappe.request else {}
    
    # Check common proxy headers (in order of reliability)
    ip_headers = [
        'CF-Connecting-IP',      # Cloudflare
        'X-Real-IP',             # Nginx proxy
        'X-Forwarded-For',       # Standard proxy header
        'True-Client-IP',        # Akamai
    ]
    
    for header in ip_headers:
        ip = headers.get(header)
        if ip:
            # X-Forwarded-For can contain multiple IPs, get the first (client)
            if ',' in ip:
                ip = ip.split(',')[0].strip()
            return ip
    
    # Fallback to remote_addr
    return frappe.request.remote_addr if frappe.request else None


def get_visitor_metadata():
    """
    Extract all available metadata from the request.
    Useful for analytics, lead generation, and personalization.
    """
    headers = frappe.request.headers if frappe.request else {}
    
    metadata = {
        "ip_address": get_visitor_ip(),
        "user_agent": headers.get('User-Agent', ''),
        "referer": headers.get('Referer', ''),
        "origin": headers.get('Origin', ''),
        "accept_language": headers.get('Accept-Language', ''),
        "timestamp": frappe.utils.now_datetime().isoformat(),
    }
    
    # Parse user agent for device/browser info (basic)
    ua = metadata["user_agent"].lower()
    if 'mobile' in ua or 'android' in ua or 'iphone' in ua:
        metadata["device_type"] = "mobile"
    elif 'tablet' in ua or 'ipad' in ua:
        metadata["device_type"] = "tablet"
    else:
        metadata["device_type"] = "desktop"
    
    # Try to identify browser
    if 'chrome' in ua and 'edg' not in ua:
        metadata["browser"] = "Chrome"
    elif 'firefox' in ua:
        metadata["browser"] = "Firefox"
    elif 'safari' in ua and 'chrome' not in ua:
        metadata["browser"] = "Safari"
    elif 'edg' in ua:
        metadata["browser"] = "Edge"
    else:
        metadata["browser"] = "Other"
    
    return metadata


def validate_domain(token):
    """
    Validate that the request Origin is in the allowed_domains list.
    Returns: (is_valid, config_name or None, error_message or None)
    """
    origin = frappe.request.headers.get('Origin', '') or frappe.request.headers.get('Referer', '')
    
    if not origin:
        # Allow same-origin requests (no Origin header means same-site)
        return True, None, None
    
    # Parse the origin to get just the domain
    try:
        parsed = urlparse(origin)
        request_domain = parsed.netloc.lower()
        if ':' in request_domain:
            request_domain = request_domain.split(':')[0]  # Remove port
    except:
        request_domain = origin.lower()
    
    # Get the config for this token (use db.sql to bypass permissions)
    config_name = frappe.db.get_value("Chatbot Settings", {"api_token": token, "enabled": 1}, "name")
    if not config_name:
        return False, None, "Invalid token or chatbot disabled"
    
    # Get allowed domains using SQL to bypass permissions
    allowed_domains = frappe.db.sql("""
        SELECT domain FROM `tabChatbot Allowed Domain`
        WHERE parent = %s
    """, (config_name,), as_list=True)
    allowed_domains = [d[0] for d in allowed_domains] if allowed_domains else []
    
    # Check if request domain matches any allowed domain
    for allowed in allowed_domains:
        allowed = allowed.lower().strip()
        # Support wildcard subdomains (*.example.com)
        if allowed.startswith('*.'):
            base_domain = allowed[2:]
            if request_domain == base_domain or request_domain.endswith('.' + base_domain):
                return True, config_name, None
        elif request_domain == allowed:
            return True, config_name, None
    
    # If no domains configured, allow all (for testing)
    if not allowed_domains:
        return True, config_name, None
    
    return False, config_name, f"Domain {request_domain} not allowed"


def get_config_doc(token):
    """
    Get the full Chatbot Settings document for a token.
    Uses ignore_permissions=True because requests are from Guest users.
    """
    config_name = frappe.get_value("Chatbot Settings", {"api_token": token, "enabled": 1}, "name")
    if not config_name:
        return None
    return frappe.get_doc("Chatbot Settings", config_name, ignore_permissions=True)


# ═══════════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

@frappe.whitelist(allow_guest=True)
def get_config(token=None):
    """
    Get chatbot configuration for a given API token.
    Returns widget configuration for display.
    """
    
    if not token:
        frappe.local.response['http_status_code'] = 400
        return {"error": "Token required", "valid": False}
    
    # Validate domain
    is_valid, config_name, error = validate_domain(token)
    if not is_valid and error:
        frappe.local.response['http_status_code'] = 403
        return {"error": error, "valid": False}
    
    settings = frappe.db.get_value(
        "Chatbot Settings",
        {"api_token": token, "enabled": 1},
        ["bot_name", "welcome_message", "primary_color", "secondary_color", "widget_position", "bot_avatar"],
        as_dict=True
    )
    
    if not settings:
        frappe.local.response['http_status_code'] = 401
        return {"error": "Invalid token or chatbot disabled", "valid": False}
    
    settings["valid"] = True
    return settings


@frappe.whitelist(allow_guest=True)
def get_active_config():
    """
    Get the first active Chatbot Settings for same-site widgets.
    No token required - used when widget is embedded on the same Frappe site.
    """
    
    settings = frappe.db.get_value(
        "Chatbot Settings",
        {"enabled": 1},
        ["name", "bot_name", "welcome_message", "primary_color", "secondary_color", "widget_position", "api_token", "bot_avatar"],
        as_dict=True,
        order_by="creation asc"
    )
    
    if not settings:
        return {"error": "No active chatbot configured", "valid": False}
    
    settings["valid"] = True
    return settings


# Website visitors reach this through the Next.js proxy, so they all share one request IP:
# limit each chat session, and cap the total per IP (site-wide for the website) to bound AI cost.
MAX_QUERY_CHARS = 2000
MAX_HISTORY_TURNS = 20


def _prepare_turn(token, session_id, query, history, page_url):
    """Validate a chat request and open its session.

    Returns (config, session_name, chat_history), or a response dict when the request is refused.
    """
    if not token or not session_id or not query:
        return {"success": False, "response": "Missing parameters."}
    if len(query) > MAX_QUERY_CHARS:
        return {"success": False, "response": f"Please keep your message under {MAX_QUERY_CHARS} characters."}

    config = get_config_doc(token)
    if not config:
        return {"success": False, "response": "Invalid token."}

    # Ensure session exists (Hardened for concurrency)
    session_name = ensure_session(session_id, config.name, page_url)

    chat_history = []
    if history:
        try:
            chat_history = json.loads(history) if isinstance(history, str) else history
        except json.JSONDecodeError:
            chat_history = []
    if not isinstance(chat_history, list):
        chat_history = []
    return config, session_name, chat_history[-MAX_HISTORY_TURNS:]


def _finish_turn(config, session_name, query, ai_response):
    """Save both messages and bump the stats (the widget no longer calls save_message)."""
    try:
        save_message_batch(session_name, [
            {"content": query, "sender": "User"},
            {"content": str(ai_response), "sender": "Bot"}
        ])
        frappe.db.set_value("Chatbot Settings", config.name, {
            "total_messages": (config.total_messages or 0) + 2
        }, update_modified=False)
        frappe.db.commit()
    except Exception as e:
        frappe.log_error(f"Post-AI update error: {str(e)}", "Chatbot SaaS")


@frappe.whitelist(allow_guest=True)
@rate_limit(key="session_id", limit=30, seconds=10 * 60)
@rate_limit(limit=300, seconds=60 * 60)
def send_message(token=None, session_id=None, query=None, history=None, page_url=None):
    """
    Process a user message through the AI Agent.
    Returns AI response with full context.

    Args:
        token: API token for the chatbot
        session_id: Session identifier
        query: Current user message
        history: JSON string of chat history [{"role": "user"|"bot", "content": "..."}]
        page_url: URL of the page where chat is happening
    """
    try:
        prepared = _prepare_turn(token, session_id, query, history, page_url)
        if isinstance(prepared, dict):
            return prepared
        config, session_name, chat_history = prepared

        ai_response = call_ai_agent(config, query, session_id=session_name, chat_history=chat_history)
        _finish_turn(config, session_name, query, ai_response)

        return {
            "success": True,
            "response": ai_response,
            "session_id": session_id
        }

    except Exception as e:
        frappe.log_error(f"send_message error: {str(e)}\n{frappe.get_traceback()}", "Chatbot SaaS")
        return {"success": False, "response": "An error occurred."}


STREAM_HEADERS = {
    "Cache-Control": "no-store",
    # nginx (ERP and website) must pass each line through as it is written
    "X-Accel-Buffering": "no",
}


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(key="session_id", limit=30, seconds=10 * 60)
@rate_limit(limit=300, seconds=60 * 60)
def send_message_stream(token=None, session_id=None, query=None, history=None, page_url=None):
    """Same as send_message, but the reply is streamed as it is written.

    The body is newline-delimited JSON: {"type": "delta", "text": ...} lines while the
    model writes, then one {"type": "done", "success", "response", "session_id"} line
    carrying the full reply (which is what gets saved). Frappe keeps the request's
    site and database open until the body has been sent, so the generator can still
    save messages and create leads.
    """
    from werkzeug.wrappers import Response

    def line(event):
        return json.dumps(event, ensure_ascii=False) + "\n"

    try:
        prepared = _prepare_turn(token, session_id, query, history, page_url)
    except Exception as e:
        frappe.log_error(f"send_message_stream error: {str(e)}\n{frappe.get_traceback()}", "Chatbot SaaS")
        prepared = {"success": False, "response": "An error occurred."}
    if isinstance(prepared, dict):
        return Response(line({"type": "done", **prepared}), mimetype="application/x-ndjson", headers=STREAM_HEADERS)

    config, session_name, chat_history = prepared

    def events():
        parts = []
        success = True
        try:
            for delta in _reply_deltas(config, query, session_name, chat_history):
                parts.append(delta)
                yield line({"type": "delta", "text": delta})
        except Exception as e:
            frappe.log_error(f"send_message_stream error: {str(e)}\n{frappe.get_traceback()}", "Chatbot SaaS")
            success = bool(parts)
        final = "".join(parts).strip()
        if not final:
            final = "I'm having trouble with the AI service. Please try again later." if not success else \
                "I'm sorry, I couldn't generate a response. Please try again."
        _finish_turn(config, session_name, query, final)
        yield line({"type": "done", "success": success, "response": final, "session_id": session_id})

    return Response(events(), mimetype="application/x-ndjson", headers=STREAM_HEADERS)


@frappe.whitelist(allow_guest=True)
def get_history(token=None, session_id=None, limit=50):
    """
    Get chat history for a session.
    """
    if not token or not session_id:
        return []
    
    config = get_config_doc(token)
    if not config:
        return []
    
    # Only return a conversation that belongs to this token's bot
    if frappe.db.get_value("Conversation", session_id, "bot_settings") != config.name:
        return []
    
    session = frappe.get_doc("Conversation", session_id)
    
    messages = []
    for msg in session.messages[-int(limit):]:
        messages.append({
            "content": msg.content,
            "sender": msg.sender,
            "timestamp": str(msg.timestamp)
        })
    
    return messages


# ═══════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS - SESSION & MESSAGE MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════

def ensure_session(session_id, settings_name, page_url=None):
    """
    Get or create a Chatbot Session. Safe for concurrent Guest requests.
    """
    if frappe.db.exists("Conversation", session_id):
        return session_id
    
    # Capture metadata
    metadata = get_visitor_metadata()
    if page_url: metadata["page_url"] = page_url
    
    # Build session data
    session_data = {
        "doctype": "Conversation",
        "session_id": session_id,
        "bot_settings": settings_name,
        "status": "Active"
    }
    
    meta = frappe.get_meta("Conversation")
    if meta.has_field("metadata"):
        session_data["metadata"] = json.dumps(metadata)
    if meta.has_field("visitor_fingerprint"):
        session_data["visitor_fingerprint"] = metadata.get("ip_address")
    
    # Create session with IntegrityError handling for concurrent hits
    try:
        session = frappe.get_doc(session_data)
        session.insert(ignore_permissions=True)
        frappe.db.commit() # Essential call to lock the name for other threads
        return session_id
    except frappe.exceptions.DuplicateEntryError:
        # Another request created it same-millisecond, that's fine
        return session_id
    except Exception as e:
        frappe.log_error(f"Session creation error for {session_id}: {str(e)}", "Chatbot SaaS")
        return session_id # Fallback to return the ID anyway if it exists now


def save_message_batch(session_name, messages):
    """
    Saves a batch of messages using raw SQL for maximum concurrency safety.
    Bypasses TimestampMismatchErrors by avoiding document-level locks.
    """
    try:
        from frappe.model.naming import make_autoname
        from frappe.utils import now_datetime
        
        now = now_datetime()
        
        for msg in messages:
            # Generate a new random name for the message child record
            # Conversation Message usually doesn't have a specific naming rule, 
            # so we use a hex hash or random string.
            msg_name = make_autoname("hash")
            
            frappe.db.sql("""
                INSERT INTO `tabConversation Message` 
                (name, creation, modified, modified_by, owner, docstatus, idx, 
                 sender, content, timestamp, parent, parentfield, parenttype)
                VALUES (%s, %s, %s, %s, %s, 0, 0, %s, %s, %s, %s, %s, %s)
            """, (
                msg_name, now, now, "Administrator", "Administrator",
                msg["sender"], msg["content"], now,
                session_name, "messages", "Conversation"
            ))
            
        frappe.db.commit()
    except Exception as e:
        # Truncate error for Error Log Title field (140 char limit fallback)
        err_msg = str(e)[:100]
        frappe.log_error(f"SQL Message Save Error: {err_msg}", "Chatbot SaaS")


# ═══════════════════════════════════════════════════════════════════════════
# AI INTEGRATION - DIRECT & ROBUST
# ═══════════════════════════════════════════════════════════════════════════

KB_RESULTS = 5
KB_RESULT_CHARS = 1500
MAX_TOOL_ROUNDS = 4


def call_ai_agent(config, query, session_id=None, chat_history=None):
    """Reply to one chat message (the whole text at once). See _reply_deltas."""
    return "".join(_reply_deltas(config, query, session_id, chat_history)).strip() or \
        "I'm sorry, I couldn't generate a response. Please try again."


def _reply_deltas(config, query, session_id=None, chat_history=None):
    """Yield the reply as it is written.

    Fast path: search the Knowledge Base first and give the results to the model, so a
    normal reply is one model call instead of two (decide to search, then answer), and
    stream the model's text. If anything fails before the first word, fall back to the
    AI Agent's own loop (_call_ai_agent_legacy) so the visitor still gets an answer.
    """
    started = False
    try:
        for delta in _stream_reply(config, query, session_id, chat_history):
            started = True
            yield delta
    except Exception:
        if started:
            raise
        frappe.log_error(f"Chatbot fast path failed, using the agent loop\n{frappe.get_traceback()}", "Chatbot SaaS")
        yield _call_ai_agent_legacy(config, query, session_id=session_id, chat_history=chat_history)


def _agent_supports_fast_path(agent_doc):
    return not agent_doc.get("output_schema") and agent_doc.agent_type in (
        "ReAct Agent", "Conversational Agent", "Structured Chat Agent"
    )


def _system_prompt(agent_doc):
    parts = [
        (m.content or "").strip()
        for m in agent_doc.messages or []
        if (m.get("type") or "system").lower() == "system" and (m.get("content_type") or "text") == "text"
    ]
    return "\n\n".join(p for p in parts if p)


def _kb_context(agent_doc, query, chat_history):
    """Knowledge Base results for this message, as a note for the model (None without a KB)."""
    if not agent_doc.knowledge_base:
        return None
    search = query
    if len(query.split()) < 6:
        # "tell me more", "price?" - search with the previous question as well
        previous = next((m.get("content") for m in reversed(chat_history or []) if m.get("role") == "user"), None)
        if previous:
            search = f"{previous}\n{query}"
    try:
        store = frappe.get_doc("Knowledge Base", agent_doc.knowledge_base).get_vector_store()
        results = store.search(search, k=KB_RESULTS)
    except Exception:
        frappe.log_error(f"Chatbot Knowledge Base search failed\n{frappe.get_traceback()}", "Chatbot SaaS")
        return None

    lines = [
        "KNOWLEDGE BASE RESULTS for the visitor's latest message. The search has already been run "
        "for you (you have no search tool). Use these for facts, and share only links listed here, as "
        "markdown links with a descriptive name. Never cite or number these results in your reply "
        "(no [1], [2], source lists or footnotes)."
    ]
    for r in results or []:
        url = (r.get("metadata") or {}).get("url") or ""
        content = (r.get("content") or "").strip()[:KB_RESULT_CHARS]
        lines.append(f"Source: {url}\n{content}" if url else content)
    if len(lines) == 1:
        lines.append("(No matching Knowledge Base content was found.)")
    return "\n\n".join(lines)


def _chunk_text(chunk):
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def _stream_reply(config, query, session_id=None, chat_history=None):
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    if not config.ai_agent:
        yield "AI Agent not configured. Please ensure the chatbot config has a linked AI Agent."
        return

    original_user = frappe.session.user
    frappe.set_user("Administrator")  # guest visitors need the agent's keys and KB
    frappe.local.chatbot_bot_settings = config.name
    if session_id:
        frappe.local.chatbot_conversation = session_id
    try:
        agent_doc = frappe.get_doc("AI Agent", config.ai_agent)
        if not _agent_supports_fast_path(agent_doc):
            raise ValueError(f"AI Agent type {agent_doc.agent_type} uses the agent loop")
        service = agent_doc.agent_service
        tools = [t for t in service.get_tools() if t.name != "kb_search"]
        llm = service.get_llm()
        model = llm.bind_tools(tools) if tools else llm
        tools_by_name = {t.name: t for t in tools}

        messages = [SystemMessage(content=_system_prompt(agent_doc))]
        context = _kb_context(agent_doc, query, chat_history)
        if context:
            messages.append(SystemMessage(content=context))
        for m in chat_history or []:
            content = (m.get("content") or "").strip()
            if content:
                messages.append(HumanMessage(content=content) if m.get("role") == "user" else AIMessage(content=content))
        messages.append(HumanMessage(content=query))

        for _ in range(MAX_TOOL_ROUNDS):
            merged = None
            for chunk in model.stream(messages):
                merged = chunk if merged is None else merged + chunk
                text = _chunk_text(chunk)
                if text:
                    yield text
            calls = (getattr(merged, "tool_calls", None) or []) if merged is not None else []
            if not calls:
                return
            messages.append(AIMessage(content=merged.content or "", tool_calls=calls))
            for call in calls:
                tool = tools_by_name.get(call.get("name"))
                try:
                    result = tool.invoke(call.get("args") or {}) if tool else f"Unknown tool {call.get('name')}"
                except Exception as e:
                    frappe.log_error(f"Chatbot tool {call.get('name')} failed\n{frappe.get_traceback()}", "Chatbot SaaS")
                    result = f"Tool error: {e}"
                messages.append(ToolMessage(content=str(result), tool_call_id=call.get("id") or call.get("name") or "tool"))
    finally:
        for attr in ("chatbot_bot_settings", "chatbot_conversation"):
            if hasattr(frappe.local, attr):
                delattr(frappe.local, attr)
        frappe.set_user(original_user)


def _call_ai_agent_legacy(config, query, session_id=None, chat_history=None):
    """
    Invokes AI using the linked AI Agent DocType.
    Passes chat history to enable conversational memory across messages.
    
    Args:
        config: Chatbot Settings document
        query: Current user message
        session_id: Session identifier for context
        chat_history: List of previous messages [{"role": "user"|"bot", "content": "..."}]
    """
    if not config:
        return "AI config missing."
    
    # Check if AI Agent is linked
    if not config.ai_agent:
        return "AI Agent not configured. Please ensure the chatbot config has a linked AI Agent."
    
    # Elevation for guest access to keys/KB
    original_user = frappe.session.user
    frappe.set_user("Administrator")
    
    # Set context for tools (bot_settings and conversation)
    frappe.local.chatbot_bot_settings = config.name
    if session_id:
        frappe.local.chatbot_conversation = session_id
    
    try:
        # Get the linked AI Agent
        agent_doc = frappe.get_doc("AI Agent", config.ai_agent)
        
        # Get AgentService instance (handles KB, system prompts, tools automatically)
        agent_service = agent_doc.agent_service
        
        # Format chat history for the AI agent
        # This provides conversational context so AI remembers previous messages
        formatted_history = ""
        if chat_history and len(chat_history) > 0:
            history_lines = []
            for msg in chat_history:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "user":
                    history_lines.append(f"User: {content}")
                elif role == "bot":
                    history_lines.append(f"Assistant: {content}")
            formatted_history = "\n".join(history_lines)
        
        # Build the query with history context
        # This ensures the AI has full conversation context
        if formatted_history:
            enhanced_query = f"""Previous conversation:
{formatted_history}

Current user message: {query}"""
        else:
            enhanced_query = query
        
        # Invoke with the enhanced query containing history
        response = agent_service.invoke(query=enhanced_query)
        
        # Normalize response
        if response is None:
            return "I'm sorry, I couldn't generate a response. Please try again."
        
        if isinstance(response, str):
            final = response
        elif isinstance(response, dict):
            final = response.get("output") or response.get("content") or str(response)
        elif hasattr(response, 'content'):
            final = str(response.content)
        elif hasattr(response, 'system_prompt'):
            # Handle structured output from prompt generator
            final = str(response.system_prompt)
        else:
            final = str(response)
        
        # Handle empty or whitespace-only responses
        final = final.strip() if final else ""
        if not final:
            return "I'm sorry, I couldn't generate a response. Please try again."
            
        return final
        
    except frappe.DoesNotExistError:
        frappe.log_error(f"AI Agent {config.ai_agent} not found for config {config.name}", "Chatbot SaaS")
        return "AI Agent configuration error. Please contact support."
    except Exception as e:
        frappe.log_error(f"AI Invoke Error: {str(e)}\n{frappe.get_traceback()}", "Chatbot SaaS")
        return "I'm having trouble with the AI service. Please try again later."
    finally:
        # Clean up context
        if hasattr(frappe.local, 'chatbot_bot_settings'):
            delattr(frappe.local, 'chatbot_bot_settings')
        if hasattr(frappe.local, 'chatbot_conversation'):
            delattr(frappe.local, 'chatbot_conversation')
        frappe.set_user(original_user)


def sync_leads_to_webhooks():
    """
    Scheduled job (runs every few minutes) to sync unsent leads to 
    the project's lead_webhook_url if configured.
    """
    try:
        # Get all leads that haven't been sent to a webhook yet
        unsent_leads = frappe.get_all(
            "Chatbot Lead",
            filters={"issent_to_webhook": 0},
            fields=["name", "full_name", "email", "company_name", "notes", "bot_settings"],
            limit=20
        )
        
        if not unsent_leads:
            return
            
        settings_cache = {} # Cache bot settings to avoid redundant DB calls
        
        for lead in unsent_leads:
            try:
                # 1. Get Webhook URL for this lead's project
                bot_name = lead.bot_settings
                if bot_name not in settings_cache:
                    settings_cache[bot_name] = frappe.db.get_value(
                        "Chatbot Settings", bot_name, "lead_webhook_url"
                    )
                
                webhook_url = settings_cache[bot_name]
                
                # 2. Silently skip if no webhook is configured
                if not webhook_url:
                    continue
                
                # 3. Prepare payload
                payload = {
                    "lead_id": lead.name,
                    "full_name": lead.full_name,
                    "email": lead.email,
                    "company_name": lead.company_name,
                    "notes": lead.notes,
                    "event": "new_lead",
                    "timestamp": frappe.utils.now()
                }
                
                # 4. Attempt to send
                response = requests.post(webhook_url, json=payload, timeout=10)
                
                # 5. On success, mark as sent
                if 200 <= response.status_code < 300:
                    frappe.db.set_value("Chatbot Lead", lead.name, "issent_to_webhook", 1)
                    frappe.db.commit()
                else:
                    # Log failure but continue with next lead
                    frappe.log_error(
                        f"Webhook failure ({lead.name}): HTTP {response.status_code}\n{response.text}",
                        "Chatbot Lead Sync"
                    )
                    
            except Exception as e:
                # Silently skip network errors/timeouts to retry in next cycle
                frappe.log_error(f"Lead sync error for {lead.name}: {str(e)}", "Chatbot Lead Sync")
                continue
                
    except Exception as e:
        frappe.log_error(f"Major Lead Sync Error: {str(e)}\n{frappe.get_traceback()}", "Chatbot Lead Sync")
