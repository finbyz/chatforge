"""
Chatbot Settings DocType
Manages chatbot configuration, AI Agent creation, and Knowledge Base integration.
"""
import frappe
from frappe.model.document import Document
import secrets


# ═══════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════

DEFAULT_PRIMARY_COLOR = "#4F46E5"
LLM_PROVIDER = "Google"
LLM_MODEL = "gemini/gemini-2.5-flash"
PROMPT_GENERATOR_AGENT = "Chatbot Prompt Generator"

FALLBACK_SYSTEM_PROMPT = """You are a friendly and knowledgeable AI assistant representing {business_name}. Your PRIMARY role is to help website visitors by answering their questions and providing useful information.

HOW YOU WORK:
You have access to a Knowledge Base containing all pages, products, services, and resources from the {business_name} website. Always search your Knowledge Base to answer questions accurately.

YOUR RESPONSIBILITIES (IN ORDER OF PRIORITY):

1. ALWAYS ANSWER THE USER'S QUESTION FIRST (MOST IMPORTANT)
   - This is your primary job - help the user with what they asked
   - Search your Knowledge Base to find accurate, relevant information
   - Provide a helpful, complete answer to their question
   - Include relevant page links from your Knowledge Base using markdown: [Page Title](URL)
   - Never skip answering just to collect information

2. THEN, NATURALLY COLLECT CONTACT DETAILS (SECONDARY)
   - AFTER answering their question, gently ask for one piece of information
   - Collect in this order across multiple messages:
     • Full Name - "By the way, who am I chatting with today?"
     • Email - "I'd love to share some additional resources - what's the best email to reach you?"
     • Company - "Are you exploring this for a particular company?"
   - Only ask for ONE detail per message, after you've answered their question
   - Once you have name and email, use the 'create_chatbot_lead' tool

RESPONSE STRUCTURE:
1. FIRST: Answer their question with helpful information from your Knowledge Base
2. THEN: Share any relevant page links you found
3. FINALLY: Naturally ask for one piece of contact info (if you don't have it yet)

WHEN YOU DON'T HAVE THE ANSWER:
"That's a great question! I don't have all the specific details on [topic], but I can connect you with our team who would be happy to help. Could I get your name and email?"

IMPORTANT RULES:
- ALWAYS answer the question first - this is your primary job
- Only share page links you actually find in your Knowledge Base
- Lead collection is secondary - never prioritize it over helping the user
- Be warm, friendly, and genuinely helpful
- Keep responses concise but complete"""


# ═══════════════════════════════════════════════════════════════════════════
# MAIN DOCUMENT CLASS
# ═══════════════════════════════════════════════════════════════════════════

class ChatbotSettings(Document):
    
    # ─────────────────────────────────────────────────────────────────────
    # LIFECYCLE HOOKS
    # ─────────────────────────────────────────────────────────────────────
    
    def before_insert(self):
        if not self.api_token:
            self.api_token = secrets.token_urlsafe(32)
    
    def validate(self):
        if not self.primary_color:
            self.primary_color = DEFAULT_PRIMARY_COLOR
        self._clean_allowed_domains()
    
    def after_insert(self):
        """Setup chatbot on first creation."""
        self._setup_chatbot()
    
    def on_update(self):
        """Sync changes to linked AI Agent."""
        if self.is_new():
            return
        
        # Website URL changed - re-process everything
        if self.has_value_changed("website_url") and self.website_url:
            self._process_website()
            return
        
        # Sync is_processed status from KB links to sitemap_urls
        self._sync_kb_status()
        
        # Sync individual field changes to agent
        self._sync_to_agent()
    
    # ─────────────────────────────────────────────────────────────────────
    # CORE SETUP METHODS
    # ─────────────────────────────────────────────────────────────────────
    
    def _setup_chatbot(self):
        """Initial chatbot setup - process website or just create agent."""
        if self.website_url:
            self._process_website()
        else:
            self._ensure_ai_agent()
    
    def _process_website(self):
        """Scrape website, generate prompt, create KB, and setup agent."""
        try:
            from finbyzai.ai.utils.knowledge_base_utils import extract_text_from_web_url
            
            frappe.publish_realtime(
                "msgprint",
                {"message": "🔄 Extracting website content...", "title": "Processing"},
                user=frappe.session.user
            )
            
            # Extract content
            success, content, error = extract_text_from_web_url(self.website_url)
            if not success:
                frappe.throw(f"Failed to extract website content: {error}")
            
            frappe.publish_realtime(
                "msgprint",
                {"message": "🤖 Generating AI prompt...", "title": "Processing"},
                user=frappe.session.user
            )
            
            website_data = {
                "title": self.bot_name or "Business Website",
                "content": content[:5000] if content else "",
                "url": self.website_url
            }
            
            # Generate prompt and setup KB + Agent
            self.system_prompt = self._generate_system_prompt(website_data)
            frappe.db.set_value("Chatbot Settings", self.name, "system_prompt", self.system_prompt)
            
            kb_name = self._ensure_knowledge_base(add_url=True)
            self._ensure_ai_agent()
            self._add_lead_tool_to_agent()
            
            frappe.msgprint("✅ Website processed! AI prompt generated and Knowledge Base created.")
            
        except Exception as e:
            frappe.log_error(f"Website processing error: {str(e)}\n{frappe.get_traceback()}", "Chatbot SaaS")
            frappe.throw(f"Failed to process website: {str(e)}")
    
    def _sync_to_agent(self):
        """Sync field changes to linked AI Agent."""
        if not self.ai_agent:
            self._ensure_ai_agent()
            return
        
        try:
            if not frappe.db.exists("AI Agent", self.ai_agent):
                self._ensure_ai_agent()
                return
            
            agent_doc = frappe.get_doc("AI Agent", self.ai_agent)
            needs_save = False
            
            if self.has_value_changed("system_prompt"):
                self._update_agent_prompt(agent_doc)
                needs_save = True
            
            if self.has_value_changed("knowledge_base"):
                agent_doc.knowledge_base = self.knowledge_base or None
                needs_save = True
            
            if needs_save:
                agent_doc.save(ignore_permissions=True)
                
        except Exception as e:
            frappe.log_error(f"Failed to sync AI Agent: {str(e)}", "Chatbot SaaS")
    
    def _sync_kb_status(self):
        """Sync is_processed status from Knowledge Base links to sitemap_urls."""
        if not self.knowledge_base or not self.sitemap_urls:
            return
        
        try:
            if not frappe.db.exists("Knowledge Base", self.knowledge_base):
                return
            
            kb_doc = frappe.get_doc("Knowledge Base", self.knowledge_base)
            
            # Build a set of processed URLs from KB
            kb_processed_urls = {link.url for link in (kb_doc.links or []) if link.is_processed}
            
            # Update sitemap_urls to match KB status
            updated = False
            for row in self.sitemap_urls:
                should_be_processed = row.url in kb_processed_urls
                if row.is_processed != should_be_processed:
                    row.is_processed = 1 if should_be_processed else 0
                    updated = True
            
            if updated:
                # Use db.set_value to avoid recursion
                for row in self.sitemap_urls:
                    frappe.db.set_value(row.doctype, row.name, "is_processed", row.is_processed)
                    
        except Exception as e:
            frappe.log_error(f"Failed to sync KB status: {str(e)}", "Chatbot SaaS")
    
    # ─────────────────────────────────────────────────────────────────────
    # KNOWLEDGE BASE METHODS
    # ─────────────────────────────────────────────────────────────────────
    
    def _ensure_knowledge_base(self, add_url=False):
        """Create or get existing Knowledge Base, optionally add website URL."""
        try:
            scrubbed_name = frappe.scrub(self.bot_name)
            # Check both naming variants (with hyphen and underscore)
            kb_name_hyphen = f"{scrubbed_name.replace('_', '-')}_knowledge-base"
            kb_name_underscore = f"{scrubbed_name}_knowledge_base"
            
            kb_doc = None
            kb_name = None
            
            # Try to find existing KB with either naming convention
            if frappe.db.exists("Knowledge Base", kb_name_hyphen):
                kb_name = kb_name_hyphen
                kb_doc = frappe.get_doc("Knowledge Base", kb_name)
            elif frappe.db.exists("Knowledge Base", kb_name_underscore):
                kb_name = kb_name_underscore
                kb_doc = frappe.get_doc("Knowledge Base", kb_name)
            else:
                # Create new KB - let Frappe auto-generate name from title
                kb_doc = frappe.get_doc({
                    "doctype": "Knowledge Base",
                    "title": f"{self.bot_name} Knowledge Base",
                    "vector_store": "ChromaDB",
                    "embeding_model": frappe.db.get_value("LLM", "gemini-embedding-001", "name"),
                    "provider": "Google",
                })
                kb_doc.insert(ignore_permissions=True)
                kb_name = kb_doc.name
            
            # Add website URL if requested and not already present
            if add_url and self.website_url:
                url_exists = any(link.url == self.website_url for link in (kb_doc.links or []))
                if not url_exists:
                    kb_doc.append("links", {"url": self.website_url, "is_processed": 0})
                    kb_doc.skip_processing = True
                    kb_doc.save(ignore_permissions=True)
                    
                    frappe.enqueue(
                        "finbyzai.ai.doctype.knowledge_base.knowledge_base._run_process_items",
                        queue="long",
                        kb_name=kb_name,
                        timeout=3600
                    )
            
            # Update self reference
            self.knowledge_base = kb_name
            frappe.db.set_value("Chatbot Settings", self.name, "knowledge_base", kb_name)
            
            return kb_name
            
        except frappe.exceptions.DuplicateEntryError:
            # KB was created by another process, fetch it
            kb_name = frappe.db.get_value("Knowledge Base", {"title": f"{self.bot_name} Knowledge Base"}, "name")
            if kb_name:
                self.knowledge_base = kb_name
                frappe.db.set_value("Chatbot Settings", self.name, "knowledge_base", kb_name)
                return kb_name
            return None
        except Exception as e:
            frappe.log_error(f"KB creation error: {str(e)}", "Chatbot SaaS")
            return None
    
    # ─────────────────────────────────────────────────────────────────────
    # AI AGENT METHODS
    # ─────────────────────────────────────────────────────────────────────
    
    def _ensure_ai_agent(self):
        """Create or update the linked AI Agent."""
        try:
            if not frappe.db.exists("LLM Provider", LLM_PROVIDER):
                frappe.throw(f"LLM Provider '{LLM_PROVIDER}' not found.")
            if not frappe.db.exists("LLM", LLM_MODEL):
                frappe.throw(f"LLM Model '{LLM_MODEL}' not found.")
            
            agent_title = f"{self.bot_name} - AI Agent"
            existing_agent = frappe.db.get_value("AI Agent", {"title": agent_title}, "name")
            
            if existing_agent:
                # Update existing agent
                agent_doc = frappe.get_doc("AI Agent", existing_agent)
                agent_doc.llm_provider = LLM_PROVIDER
                agent_doc.llm = LLM_MODEL
                agent_doc.agent_type = "ReAct Agent"
                agent_doc.knowledge_base = self.knowledge_base or None
                self._update_agent_prompt(agent_doc)
                agent_doc.save(ignore_permissions=True)
                self.ai_agent = agent_doc.name
            else:
                # Create new agent
                agent_doc = frappe.get_doc({
                    "doctype": "AI Agent",
                    "title": agent_title,
                    "llm_provider": LLM_PROVIDER,
                    "llm": LLM_MODEL,
                    "agent_type": "ReAct Agent",
                    "knowledge_base": self.knowledge_base or None,
                    "enable_memory": 0,
                    "verbose_mode": 0,
                })
                
                if self.system_prompt:
                    agent_doc.append("messages", {
                        "type": "system",
                        "content_type": "text",
                        "content": self.system_prompt
                    })
                
                agent_doc.insert(ignore_permissions=True)
                self._add_lead_tool_to_agent(agent_doc)
                self.ai_agent = agent_doc.name
            
            frappe.db.set_value("Chatbot Settings", self.name, "ai_agent", self.ai_agent)
            
        except Exception as e:
            frappe.log_error(f"Failed to create AI Agent: {str(e)}", "Chatbot SaaS")
            frappe.throw(f"Failed to create AI Agent: {str(e)}")
    
    def _update_agent_prompt(self, agent_doc):
        """Update system prompt in AI Agent's messages."""
        agent_doc.messages = [msg for msg in agent_doc.messages if msg.type != "system"]
        if self.system_prompt and self.system_prompt.strip():
            agent_doc.append("messages", {
                "type": "system",
                "content_type": "text",
                "content": self.system_prompt.strip()
            })
    
    def _add_lead_tool_to_agent(self, agent_doc=None):
        """Add lead creation tool to the AI Agent."""
        try:
            if not agent_doc:
                if not self.ai_agent:
                    return
                agent_doc = frappe.get_doc("AI Agent", self.ai_agent)
            
            tool_name = "Create Chatbot Lead"
            
            # Ensure tool exists
            if not frappe.db.exists("AI Tool", tool_name):
                chatbot_module = self._ensure_chatbot_module()
                frappe.get_doc({
                    "doctype": "AI Tool",
                    "name": tool_name,
                    "description": "Create a Chatbot Lead with name, email, and company. Use when you have collected all three pieces of information.",
                    "is_custom": 1,
                    "from_package": "",
                    "module": chatbot_module
                }).insert(ignore_permissions=True)
            
            # CLEANUP: Remove invalid snake_case tool reference if present
            # This fixes the LinkValidationError: Could not find Row #1: Tool: create_chatbot_lead
            tools_modified = False
            if agent_doc.get("tools"):
                original_tools = agent_doc.tools
                agent_doc.tools = [t for t in agent_doc.tools if t.tool != "create_chatbot_lead"]
                if len(agent_doc.tools) != len(original_tools):
                    tools_modified = True

            # Add to agent if not already added
            if not any(t.tool == tool_name for t in (agent_doc.tools or [])):
                agent_doc.append("tools", {"tool": tool_name})
                tools_modified = True
            
            if tools_modified:
                agent_doc.save(ignore_permissions=True)
                
        except Exception as e:
            frappe.log_error(f"Failed to add lead tool: {str(e)}", "Chatbot SaaS")
    
    def _ensure_chatbot_module(self):
        """Ensure Chatbot module exists, create if not."""
        module = frappe.db.get_value("Module Def", {"module_name": "Chatbot"}, "name")
        if not module:
            doc = frappe.get_doc({
                "doctype": "Module Def",
                "module_name": "Chatbot",
                "app_name": "chatforge"
            })
            doc.insert(ignore_permissions=True)
            module = doc.name
        return module
    
    # ─────────────────────────────────────────────────────────────────────
    # PROMPT GENERATION
    # ─────────────────────────────────────────────────────────────────────
    
    def _generate_system_prompt(self, website_data):
        """Generate AI system prompt using the Chatbot Prompt Generator agent."""
        try:
            from finbyzai.ai.agent.agent_service import AgentService
            
            if not frappe.db.exists("AI Agent", PROMPT_GENERATOR_AGENT):
                return self._get_fallback_prompt(website_data.get("title", "our company"))
            
            agent_doc = frappe.get_doc("AI Agent", PROMPT_GENERATOR_AGENT)
            
            query = f"""WEBSITE INFORMATION:
URL: {website_data.get('url', self.website_url)}
Title: {website_data.get('title', 'N/A')}

WEBSITE CONTENT:
{website_data.get('content', '')[:4000]}"""
            
            response = AgentService(agent_doc).invoke(query=query)
            
            # Extract prompt from response
            if isinstance(response, str):
                generated = response.strip()
            elif isinstance(response, dict):
                generated = response.get("system_prompt") or response.get("output") or response.get("content", "")
                generated = str(generated).strip()
            else:
                generated = str(response).strip()
            
            if not generated or len(generated) < 50:
                return self._get_fallback_prompt(website_data.get("title", "our company"))
            
            return generated
            
        except Exception as e:
            frappe.log_error(f"Prompt generation error: {str(e)}", "Chatbot SaaS")
            return self._get_fallback_prompt(website_data.get("title", "our company"))
    
    def _get_fallback_prompt(self, business_name):
        """Return fallback system prompt."""
        return FALLBACK_SYSTEM_PROMPT.format(business_name=business_name)
    
    # ─────────────────────────────────────────────────────────────────────
    # UTILITY METHODS
    # ─────────────────────────────────────────────────────────────────────
    
    def _clean_allowed_domains(self):
        """Clean and normalize allowed domains."""
        if not self.allowed_domains:
            return
        for row in self.allowed_domains:
            domain = row.domain.strip().lower()
            for prefix in ("http://", "https://"):
                if domain.startswith(prefix):
                    domain = domain[len(prefix):]
            row.domain = domain.rstrip("/")
    
    @frappe.whitelist()
    def get_embed_code(self):
        """Generate the JavaScript embed code for this chatbot."""
        site_url = frappe.utils.get_url()
        if site_url.startswith("http://"):
            site_url = site_url.replace("http://", "https://", 1)
        
        widget_url = f"{site_url}/assets/chatforge/js/saas_widget.js"
        css_url = f"{site_url}/assets/chatforge/css/saas_widget.css"
        
        return f'''<!-- Chatbot Widget by FinByz -->
<link rel="stylesheet" href="{css_url}">
<script src="{widget_url}" data-token="{self.api_token}" data-host="{site_url}"></script>'''
    
    # ─────────────────────────────────────────────────────────────────────
    # SITEMAP PARSING HELPERS (used by fetch_sitemap_urls)
    # ─────────────────────────────────────────────────────────────────────
    
    def _parse_urlset(self, root, namespaces):
        """Parse URL entries from sitemap XML."""
        urls_data = []
        current_url = None
        
        for elem in root.iter():
            tag = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
            
            if tag == 'url':
                if current_url and current_url.get('url'):
                    urls_data.append(current_url)
                current_url = {'url': None, 'lastmod': None}
            elif tag == 'loc' and current_url is not None and elem.text:
                current_url['url'] = elem.text.strip()
            elif tag == 'lastmod' and current_url is not None and elem.text:
                current_url['lastmod'] = elem.text.strip()
        
        if current_url and current_url.get('url'):
            urls_data.append(current_url)
        return urls_data
    
    def _is_sitemap_index(self, root):
        """Check if root is a sitemap index."""
        for elem in root.iter():
            tag = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
            if tag == 'sitemap':
                return True
            if tag == 'url':
                return False
        return False
    
    def _get_sitemap_locs(self, root):
        """Extract sitemap locations from sitemap index."""
        locs = []
        in_sitemap = False
        for elem in root.iter():
            tag = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
            if tag == 'sitemap':
                in_sitemap = True
            elif tag == 'loc' and in_sitemap and elem.text:
                locs.append(elem.text.strip())
                in_sitemap = False
        return locs


# ═══════════════════════════════════════════════════════════════════════════
# STANDALONE WHITELISTED FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def fetch_sitemap_urls(docname, sitemap_url):
    """Fetch URLs from sitemap and populate sitemap_urls child table."""
    import requests
    import xml.etree.ElementTree as ET
    
    try:
        if not docname or not sitemap_url:
            return {"success": False, "error": "Document name and Sitemap URL required."}
        
        if not sitemap_url.startswith(('http://', 'https://')):
            return {"success": False, "error": "Invalid URL format."}
        
        if not frappe.db.exists("Chatbot Settings", docname):
            return {"success": False, "error": f"Chatbot Settings '{docname}' not found."}
        
        doc = frappe.get_doc("Chatbot Settings", docname)
        doc.sitemap_url = sitemap_url
        
        # Fetch and parse sitemap
        response = requests.get(sitemap_url, timeout=60, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; FinByzBot/1.0)'
        })
        response.raise_for_status()
        root = ET.fromstring(response.content)
        
        # Parse URLs
        urls_data = []
        namespaces = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
        
        if doc._is_sitemap_index(root):
            for sitemap_loc in doc._get_sitemap_locs(root):
                try:
                    child_resp = requests.get(sitemap_loc, timeout=30, headers={
                        'User-Agent': 'Mozilla/5.0 (compatible; FinByzBot/1.0)'
                    })
                    child_resp.raise_for_status()
                    child_root = ET.fromstring(child_resp.content)
                    urls_data.extend(doc._parse_urlset(child_root, namespaces))
                except Exception as e:
                    frappe.log_error(f"Error fetching child sitemap {sitemap_loc}: {str(e)}", "Chatbot SaaS")
        else:
            urls_data = doc._parse_urlset(root, namespaces)
        
        if not urls_data:
            return {"success": False, "error": "No URLs found in sitemap."}
        
        # Keep processed URLs, rebuild table
        existing_processed = {row.url: row for row in (doc.sitemap_urls or []) if row.is_processed}
        doc.sitemap_urls = []
        
        for item in urls_data:
            url = item.get('url', '')
            title = _title_from_url(url)
            
            if url in existing_processed:
                row = existing_processed[url]
                doc.append("sitemap_urls", {
                    "url": row.url,
                    "page_title": row.page_title,
                    "is_selected": 0,
                    "is_processed": 1
                })
            else:
                doc.append("sitemap_urls", {
                    "url": url,
                    "page_title": title,
                    "is_selected": 0,
                    "is_processed": 0
                })
        
        doc.save(ignore_permissions=True)
        
        return {"success": True, "message": f"✅ Fetched {len(urls_data)} URLs from sitemap."}
        
    except requests.exceptions.RequestException as e:
        return {"success": False, "error": f"Failed to fetch sitemap: {str(e)}"}
    except ET.ParseError as e:
        return {"success": False, "error": f"Invalid sitemap XML: {str(e)}"}
    except Exception as e:
        frappe.log_error(f"Sitemap fetch error: {str(e)}\n{frappe.get_traceback()}", "Chatbot SaaS")
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def process_selected_urls(docname):
    """Process selected URLs from sitemap_urls child table."""
    try:
        if not docname or not frappe.db.exists("Chatbot Settings", docname):
            return {"success": False, "error": "Invalid document."}
        
        doc = frappe.get_doc("Chatbot Settings", docname)
        
        selected_urls = [row for row in (doc.sitemap_urls or []) if row.is_selected and not row.is_processed]
        if not selected_urls:
            return {"success": False, "error": "No URLs selected for processing."}
        
        # Ensure KB exists
        if not doc.knowledge_base:
            kb_name = doc._ensure_knowledge_base()
            if not kb_name:
                return {"success": False, "error": "Failed to create Knowledge Base."}
        
        kb_doc = frappe.get_doc("Knowledge Base", doc.knowledge_base)
        existing_links = {link.url for link in (kb_doc.links or [])}
        added_count = 0
        
        for row in selected_urls:
            if row.url not in existing_links:
                kb_doc.append("links", {"url": row.url, "is_processed": 0})
                added_count += 1
            row.is_processed = 1
            row.is_selected = 0
        
        kb_doc.skip_processing = True
        kb_doc.save(ignore_permissions=True)
        doc.save(ignore_permissions=True)
        
        frappe.enqueue(
            "finbyzai.ai.doctype.knowledge_base.knowledge_base._run_process_items",
            queue="long",
            kb_name=doc.knowledge_base,
            timeout=3600
        )
        
        return {"success": True, "message": f"✅ {added_count} URLs added. Processing started in background."}
        
    except Exception as e:
        frappe.log_error(f"Process URLs error: {str(e)}\n{frappe.get_traceback()}", "Chatbot SaaS")
        return {"success": False, "error": str(e)}


def _title_from_url(url):
    """Generate readable title from URL path."""
    from urllib.parse import urlparse, unquote
    
    parsed = urlparse(url)
    path = unquote(parsed.path.strip('/'))
    
    if not path:
        return "Homepage"
    
    segments = path.split('/')
    
    def clean(seg):
        if '.' in seg:
            seg = seg.rsplit('.', 1)[0]
        return seg.replace('-', ' ').replace('_', ' ').title()
    
    last = clean(segments[-1]) if segments else "Page"
    
    if len(segments) > 1:
        parent = clean(segments[-2])
        if parent and parent.lower() != last.lower() and len(parent) > 2:
            return f"{last} ({parent})"
    
    return last or "Page"
