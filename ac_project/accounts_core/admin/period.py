from django.contrib import admin

from accounts_core.models import Period

from .mixins import TenantAdminMixin


# Register `Period` model
@admin.register(Period)
class PeriodAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        "id", "company", "name", "start_date", "end_date", "is_closed")
    list_filter = ("company", "is_closed")
    search_fields = ("name",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs
