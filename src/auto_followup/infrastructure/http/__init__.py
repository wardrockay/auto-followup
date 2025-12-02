"""
HTTP Client Package.

External service clients:
- Odoo CRM API
- Mail-writer service
"""

from auto_followup.infrastructure.http.odoo_client import (
    get_odoo_client,
    OdooClient,
    OdooLead,
)
from auto_followup.infrastructure.http.mail_writer_client import (
    FollowupEmailRequest,
    FollowupEmailResponse,
    get_mail_writer_client,
    MailWriterClient,
)


__all__ = [
    # Odoo
    "get_odoo_client",
    "OdooClient",
    "OdooLead",
    # Mail-writer
    "FollowupEmailRequest",
    "FollowupEmailResponse",
    "get_mail_writer_client",
    "MailWriterClient",
]
