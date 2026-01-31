"""
Create Chatbot Lead Tool for Chatbot SaaS
=========================================
LangChain tool to create Chatbot Lead records from chatbot conversations.
This tool collects name, email, and company from users and saves them.
"""

import frappe
import json
import re
from langchain.tools import tool


@tool("create_chatbot_lead", return_direct=False)
def create_chatbot_lead_tool(full_name: str, email: str, company_name: str = "", notes: str = "", bot_settings: str = "", conversation: str = "") -> str:
    """
    Create a new Chatbot Lead record from chatbot conversation.
    
    This tool collects user information (name, email, company) and creates a Chatbot Lead.
    It should be called ONLY when you have collected all three pieces of information.
    
    Args:
        full_name: Full name of the lead (required)
        email: Email address of the lead (required, must be valid format)
        company_name: Company name of the lead (optional)
        notes: Additional notes from the conversation (optional)
        bot_settings: Chatbot Settings document name (optional, for tracking)
        conversation: Conversation document name (optional, for tracking)
    
    Returns:
        JSON string with success status and lead details
    
    Example:
        full_name: "John Doe"
        email: "john@example.com"
        company_name: "Acme Corporation"
    """
    try:
        # Validate required fields
        if not full_name or not full_name.strip():
            return json.dumps({
                "success": False,
                "error": "Full name is required",
                "message": "I need your full name to proceed. Could you please provide it?"
            })
        
        if not email or not email.strip():
            return json.dumps({
                "success": False,
                "error": "Email is required",
                "message": "I need your email address to proceed. Could you please provide it?"
            })
        
        # Validate email format
        email = email.strip().lower()
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(email_pattern, email):
            return json.dumps({
                "success": False,
                "error": "Invalid email format",
                "message": f"The email '{email}' doesn't look quite right. Could you double-check it?"
            })
        
        full_name = full_name.strip()
        company_name = company_name.strip() if company_name else ""
        
        # Get bot_settings and conversation from context if not provided
        if not bot_settings:
            bot_settings = getattr(frappe.local, 'chatbot_bot_settings', None)
        if not conversation:
            conversation = getattr(frappe.local, 'chatbot_conversation', None)
        
        # Check for existing lead with same email and bot_settings
        filters = {"email": email}
        if bot_settings:
            filters["bot_settings"] = bot_settings
        
        existing_lead = frappe.db.exists("Chatbot Lead", filters)
        if existing_lead:
            return json.dumps({
                "success": True,
                "lead_name": existing_lead,
                "existing": True,
                "message": f"Thanks {full_name.split()[0]}! We already have your information on file. Our team will be in touch soon! 📞"
            })
        
        # Create the Chatbot Lead
        lead_data = {
            "doctype": "Chatbot Lead",
            "full_name": full_name,
            "email": email,
            "company_name": company_name,
            "notes": notes or "",
            "status": "New"
        }
        
        if bot_settings:
            lead_data["bot_settings"] = bot_settings
        
        if conversation:
            lead_data["conversation"] = conversation
        
        lead = frappe.get_doc(lead_data)
        lead.insert(ignore_permissions=True)
        frappe.db.commit()
        
        frappe.logger().info(f"Chatbot SaaS: Lead created - {lead.name} ({email})")
        
        return json.dumps({
            "success": True,
            "lead_name": lead.name,
            "full_name": full_name,
            "email": email,
            "company_name": company_name,
            "message": f"Thank you, {full_name.split()[0]}! ✅ I've saved your details. Our team will reach out to you shortly at {email}! 🎉"
        })
    
    except Exception as e:
        frappe.log_error(f"Create Chatbot Lead Error: {str(e)}", "Chatbot SaaS - Create Lead Tool")
        return json.dumps({
            "success": False,
            "error": str(e),
            "message": "I'm having trouble saving your information. Please try again or contact us directly."
        })
