from django.conf import settings  # To access global project settings
from django.core.exceptions import ValidationError
from django.db import models
from ..managers import TenantManager
from .entitymembership import Company


# ---------- Audit / Event log ----------
class AuditLog(
    models.Model
):  # Gives accountability and traceability across whole system
    # Associate log entry with a tenant (multi-company setup)
    company = models.ForeignKey(
        Company,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    # Nullable because some actions might not belong to a specific company
    # (e.g., system-wide events).
    # Which user performed the action
    # (Nullable in case the action was automated
    # (e.g., background job, import script))
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True, on_delete=models.SET_NULL
    )
    # Type of event being logged
    action = models.CharField(
        max_length=50
    )  # Common choices: create, update, delete, post
    # What kind of object was affected
    object_type = models.CharField(
        max_length=100
    )  # (e.g., "Invoice", "JournalEntry", "Customer")
    # The primary key (or identifier) of the object
    object_id = models.CharField(max_length=100)
    # Store actual before/after details of what changed, in JSON format
    changes = models.JSONField(null=True, blank=True)
    # Timestamp when the event was logged
    created_at = models.DateTimeField(auto_now_add=True)

    # Enforce tenant scoping
    objects = TenantManager()

    class Meta:
        # Filter logs quickly
        indexes = [
            models.Index(fields=["company", "user"]),
            models.Index(fields=["company", "created_at"]),
        ]

    # Show created_at, user, action, object_type, and
    # object_id in admin dropdowns and debug logs
    def __str__(self):
        time = self.created_at
        usr = self.user
        action = self.action
        objType = self.object_type
        objId = self.object_id
        return f"[{time:%Y-%m-%d %H:%M}] {usr} {action} {objType}({objId})"

    def clean(self):
        # Ensure the user is a member of the company being logged
        if self.user and self.company:
            if not self.user.memberships.filter(
                company=self.company, is_active=True
            ).exists():
                raise ValidationError(
                    "AuditLog.user must be a member of AuditLog.company"
                )

    def save(self, *args, **kwargs):
        self.full_clean()  # run validations before saving
        return super().save(*args, **kwargs)
