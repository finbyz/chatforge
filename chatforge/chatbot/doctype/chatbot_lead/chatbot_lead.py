# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
import json
import requests


class ChatbotLead(Document):
    def validate(self):
        # Validate email format
        if self.email:
            import re
            email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
            if not re.match(email_pattern, self.email):
                frappe.throw("Please enter a valid email address")
    
    def after_insert(self):
        # Update lead count in Chatbot Settings
        if self.bot_settings:
            self.update_chatbot_settings_stats()
            # Send lead to webhook
            self.send_lead_to_webhook()
    
    def update_chatbot_settings_stats(self):
        """Update total leads count in Chatbot Settings"""
        try:
            total_leads = frappe.db.count("Chatbot Lead", {"bot_settings": self.bot_settings})
            frappe.db.set_value("Chatbot Settings", self.bot_settings, "total_leads", total_leads, update_modified=False)
            frappe.db.commit()
        except Exception as e:
            frappe.log_error(f"Failed to update Chatbot Settings stats: {str(e)}", "Chatbot Lead")
    
    def send_lead_to_webhook(self):
        """Send lead data to configured webhook URL"""
        try:
            if not self.bot_settings:
                return
            
            # Get webhook URL from Chatbot Settings
            webhook_url = frappe.db.get_value("Chatbot Settings", self.bot_settings, "lead_webhook_url")
            
            if not webhook_url:
                # No webhook configured, skip silently
                return
            
            # Prepare lead data payload
            lead_data = {
                "event": "lead_created",
                "timestamp": frappe.utils.now_datetime().isoformat(),
                "lead": {
                    "id": self.name,
                    "full_name": self.full_name,
                    "email": self.email,
                    "company_name": self.company_name or "",
                    "status": self.status,
                    "notes": self.notes or "",
                    "created_at": str(self.creation),
                    "conversation_id": self.conversation or "",
                },
                "chatbot": {
                    "settings_id": self.bot_settings,
                    "bot_name": frappe.db.get_value("Chatbot Settings", self.bot_settings, "bot_name") or ""
                }
            }
            
            # Send webhook request asynchronously using background job
            frappe.enqueue(
                "chatforge.chatbot.doctype.chatbot_lead.chatbot_lead.send_webhook_request",
                queue="short",
                webhook_url=webhook_url,
                payload=lead_data,
                lead_name=self.name
            )
            
        except Exception as e:
            frappe.log_error(f"Failed to queue webhook for lead {self.name}: {str(e)}", "Chatbot Lead Webhook")


def send_webhook_request(webhook_url, payload, lead_name):
    """
    Background job to send webhook request.
    Runs asynchronously to avoid blocking lead creation.
    """
    try:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "FinByz-Chatbot-Webhook/1.0",
            "X-Webhook-Event": "lead_created"
        }
        
        response = requests.post(
            webhook_url,
            json=payload,
            headers=headers,
            timeout=30  # 30 second timeout
        )
        
        # Log successful webhook delivery
        if response.status_code in [200, 201, 202, 204]:
            frappe.logger().info(f"Webhook delivered successfully for lead {lead_name} to {webhook_url}")
        else:
            # Log failed webhook with response details
            frappe.log_error(
                f"Webhook failed for lead {lead_name}\n"
                f"URL: {webhook_url}\n"
                f"Status Code: {response.status_code}\n"
                f"Response: {response.text[:500]}",
                "Chatbot Lead Webhook Failed"
            )
    
    except requests.exceptions.Timeout:
        frappe.log_error(
            f"Webhook timeout for lead {lead_name}\nURL: {webhook_url}",
            "Chatbot Lead Webhook Timeout"
        )
    except requests.exceptions.ConnectionError as e:
        frappe.log_error(
            f"Webhook connection error for lead {lead_name}\nURL: {webhook_url}\nError: {str(e)}",
            "Chatbot Lead Webhook Connection Error"
        )
    except Exception as e:
        frappe.log_error(
            f"Webhook error for lead {lead_name}\nURL: {webhook_url}\nError: {str(e)}",
            "Chatbot Lead Webhook Error"
        )
