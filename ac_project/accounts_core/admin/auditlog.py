from django.contrib import admin

from accounts_core.models import AuditLog

from .mixins import TenantAdminMixin


# Register `AuditLog` model
@admin.register(AuditLog)
class AuditLogAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        "id",
        "company",
        "user",
        "action",
        "object_type",
        "object_id",
        "created_at",
    )
    search_fields = ("object_type", "object_id", "user__username")
    list_filter = ("company", "action", "created_at")

    # Fetch everything in one SQL join
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("company", "user")
