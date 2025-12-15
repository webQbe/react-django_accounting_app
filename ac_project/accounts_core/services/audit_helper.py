from typing import Optional
from django.forms.models import model_to_dict
from ..models import AuditLog, Company

def log_action(
    *,
    action: str,
    instance,
    user=None,
    company: Optional[Company] = None,
    changes: dict | None = None,
):
    """
    Central audit logger.
    Safe to call multiple times (caller ensures idempotency).
    """

    if not company:
        company = getattr(instance, "company", None)

    AuditLog.objects.create(
        company=company,
        user=user,
        action=action,
        object_type=instance.__class__.__name__,
        object_id=str(instance.pk),
        changes=changes,
    )
