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
        SELECT domain FROM `tabAllowed Domains`
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
        ["name", "bot_name", "welcome_message", "primary_color", "secondary_color", "widget_position", "llm_provider", "llm_model", "api_token", "bot_avatar"],
        as_dict=True,
        order_by="creation asc"
    )
    
    if not settings:
        return {"error": "No active chatbot configured", "valid": False}
    
    settings["valid"] = True
    return settings


@frappe.whitelist(allow_guest=True)
def init_session(token=None, visitor_id=None, page_url=None, metadata=None):
    """
    Initialize a new chat session with full visitor data capture.
    Returns session_id and bot configuration.
    """
    if not token:
        return {"error": "Token required"}
    
    # Validate domain
    is_valid, config_name, error = validate_domain(token)
    if not is_valid and error:
        frappe.local.response['http_status_code'] = 403
        return {"error": error}
    
    config = get_config_doc(token)
    if not config:
        return {"error": "Invalid token"}
    
    # Generate session ID
    import uuid
    session_id = f"sess_{uuid.uuid4().hex[:16]}"
    
    # Capture visitor metadata
    server_metadata = get_visitor_metadata()
    
    # Merge with client-provided metadata
    try:
        client_metadata = json.loads(metadata) if metadata else {}
    except:
        client_metadata = {}
    
    full_metadata = {**server_metadata, **client_metadata}
    if page_url:
        full_metadata["page_url"] = page_url
    
    # Build session data
    session_data = {
        "doctype": "Conversation",
        "session_id": session_id,
        "bot_settings": config.name,
        "visitor_fingerprint": visitor_id or full_metadata.get("ip_address"),
        "status": "Active"
    }
    
    # Only add metadata field if it exists in the DocType
    meta = frappe.get_meta("Conversation")
    if meta.has_field("metadata"):
        session_data["metadata"] = json.dumps(full_metadata)
    
    # Create session with all captured data
    session = frappe.get_doc(session_data)
    session.insert(ignore_permissions=True)
    frappe.db.commit()
    
    # Update config statistics
    update_config_stats(config.name)
    
    return {
        "session_id": session_id,
        "config": {
            "bot_name": config.bot_name,
            "bot_avatar": config.bot_avatar,
            "welcome_message": config.welcome_message,
            "primary_color": config.primary_color,
            "position": config.widget_position
        },
        "visitor_data": {
            "ip": full_metadata.get("ip_address"),
            "device": full_metadata.get("device_type"),
            "browser": full_metadata.get("browser")
        }
    }


@frappe.whitelist(allow_guest=True)
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
        if not token or not session_id or not query:
            return {"success": False, "response": "Missing parameters."}
        
        # Get config
        config = get_config_doc(token)
        if not config:
            return {"success": False, "response": "Invalid token."}
        
        # Ensure session exists (Hardened for concurrency)
        session_name = ensure_session(session_id, config.name, page_url)
        
        # Parse chat history from JSON string
        chat_history = []
        if history:
            try:
                chat_history = json.loads(history) if isinstance(history, str) else history
            except json.JSONDecodeError:
                chat_history = []
        
        # Call AI Agent using linked AI Agent (pass session_id and history for context)
        ai_response = call_ai_agent(config, query, session_id=session_name, chat_history=chat_history)
        
        # Save messages and update stats (only once - widget no longer calls save_message)
        try:
            save_message_batch(session_name, [
                {"content": query, "sender": "User"},
                {"content": str(ai_response), "sender": "Bot"}
            ])
            
            # Atomic update for stats on the config
            frappe.db.set_value("Chatbot Settings", config.name, {
                "total_messages": (config.total_messages or 0) + 2
            }, update_modified=False)
            frappe.db.commit()
        except Exception as e:
            frappe.log_error(f"Post-AI update error: {str(e)}", "Chatbot SaaS")
        
        return {
            "success": True,
            "response": ai_response,
            "session_id": session_id
        }
            
    except Exception as e:
        frappe.log_error(f"send_message error: {str(e)}\n{frappe.get_traceback()}", "Chatbot SaaS")
        return {"success": False, "response": "An error occurred."}


@frappe.whitelist(allow_guest=True)
def save_message(token=None, session_id=None, message=None, sender=None):
    """
    Save a single message to the session.
    """
    if not token or not session_id or not message or not sender:
        return {"status": "error", "message": "Missing parameters"}
    
    config = get_config_doc(token)
    if not config:
        return {"status": "error", "message": "Invalid token"}
    
    session_name = ensure_session(session_id, config.name)
    save_message_to_session(session_name, message, sender)
    
    return {"status": "success"}


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
    
    # Check if session exists
    if not frappe.db.exists("Conversation", session_id):
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


def save_message_to_session(session_name, content, sender):
    """Save a single message."""
    save_message_batch(session_name, [{"content": content, "sender": sender}])


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


def update_config_stats(config_name):
    """Update statistics on the config."""
    try:
        total_sessions = frappe.db.count("Conversation", {"bot_settings": config_name})
        total_messages = frappe.db.sql("""
            SELECT COUNT(m.name)
            FROM `tabConversation Message` m
            JOIN `tabConversation` s ON m.parent = s.name
            WHERE s.bot_settings = %s
        """, config_name)[0][0] or 0
        
        frappe.db.set_value("Chatbot Settings", config_name, {
            "total_sessions": total_sessions,
            "total_messages": total_messages
        }, update_modified=False)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════
# AI INTEGRATION - DIRECT & ROBUST
# ═══════════════════════════════════════════════════════════════════════════

def call_ai_agent(config, query, session_id=None, chat_history=None):
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
