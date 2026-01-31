"""
Chatbot Settings DocType
Manages chatbot configuration, AI Agent creation, and Knowledge Base integration.
"""
import frappe
from frappe.model.document import Document
import secrets
from finbyzai.ai.agent.agent_service import AgentService


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
   - Once you have name and email, use the 'Create Chatbot Lead' tool

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
    def before_insert(self):
        if not self.api_token:
            self.api_token = secrets.token_urlsafe(32)
    
    def validate(self):
        # Set default primary color
        if not self.primary_color:
            self.primary_color = DEFAULT_PRIMARY_COLOR

        # Prevent name conflicts on new records
        if self.is_new():
            agent_exists = frappe.db.exists(
                "AI Agent",
                {"agent_name": self.bot_name}
            )

            kb_exists = frappe.db.exists(
                "Knowledge Base",
                {"title": f"{self.bot_name} Knowledge Base"}
            )

            if agent_exists or kb_exists:
                frappe.throw(
                    _("Chatbot name already exists. Please choose a different name.")
                )

        self._clean_allowed_domains()
    
    def after_insert(self):
        """Setup chatbot on first creation."""
        self.setup_chatbot()
    
    def on_update(self):
        """Sync changes to linked AI Agent."""
        if self.is_new():
            return
    
    def setup_chatbot(self): 
        self.create_knowledgebase_if_not_exist()
        
        self.create_agent_if_not_exist()
        
    def create_knowledgebase_if_not_exist(self):
        if self.knowledge_base:
            return
        if frappe.db.exists("Knowledge Base", frappe.scrub( f"{self.bot_name} Knowledge Base")):
            self.knowledge_base = frappe.scrub(f"{self.bot_name} Knowledge Base")
            return
        kb_doc = frappe.get_doc({
            "doctype": "Knowledge Base",
            "title": f"{self.bot_name} Knowledge Base",
            "vector_store": "ChromaDB",
            "embeding_model": frappe.db.get_value(
                "LLM", "gemini-embedding-001", "name"
            ),
            "provider": "Google",
        })

        if self.website_url:
            kb_doc.append("links", {
                "url": self.website_url
            })

        kb_doc.insert(ignore_permissions=True)

        self.knowledge_base = kb_doc.name
        
    def create_agent_if_not_exist(self):
        if self.ai_agent: return
        if frappe.db.exists("AI Agent", f"{self.bot_name} - AI Agent"):
            self.ai_agent = f"{self.bot_name} - AI Agent"
            return
        
        system_prompt_agent = frappe.get_doc("AI Agent", PROMPT_GENERATOR_AGENT)
        system_prompt = system_prompt_agent.agent_service.invoke(**{
            "url": self.website_url,
        }) or FALLBACK_SYSTEM_PROMPT
        self.system_prompt = system_prompt
        
        agent_doc = frappe.get_doc({
            "doctype": "AI Agent",
            "title": f"{self.bot_name} - AI Agent",
            "llm_provider": LLM_PROVIDER,
            "llm": LLM_MODEL,
            "agent_type": "ReAct Agent",
            "knowledge_base": self.knowledge_base or None,
        })
        agent_doc.append("messages", {
            "type": "system",
            "content_type": "text",
            "content": system_prompt
        })
        
        agent_doc.insert()
        self.ai_agent = agent_doc.name
    
    
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
        
        kb_doc = frappe.get_doc("Knowledge Base", doc.knowledge_base)
        existing_links = {link.url for link in (kb_doc.links or [])}
        added_count = 0
        
        for row in selected_urls:
            if row.url not in existing_links:
                kb_doc.append("links", {"url": row.url, "is_processed": 0})
                added_count += 1
            row.is_processed = 1
            row.is_selected = 0
        
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
