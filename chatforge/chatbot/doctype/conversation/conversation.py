
import frappe
from frappe.model.document import Document
import uuid

class Conversation(Document):
    def before_insert(self):
        if not self.session_id:
            self.session_id = str(uuid.uuid4())
