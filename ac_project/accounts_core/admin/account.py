from django.contrib import admin
from django.contrib import admin
from django.utils.html import format_html
from accounts_core.models import Account, AccountCategory, AccountBalanceSnapshot
from .mixins import TenantAdminMixin


# Register `Account` model 
@admin.register(Account)
class AccountAdmin(TenantAdminMixin, admin.ModelAdmin):
    # show key accounting fields
    list_display = ("id", "company", "code", "name", "ac_type", "normal_balance", "is_active")
    list_filter = ("company", "ac_type", "is_active")
    search_fields = ("code", "name")
    ordering = ("company", "code") # accounts grouped by company, then sorted by code
    fieldsets = ( 
        # customize layout in edit form, all fields appear neatly grouped under "None"
        (None, {"fields": ("company", "code", "name", "ac_type", "normal_balance", "category", "parent", "is_active")}),
    )
    # Tenant Filtering
    def get_queryset(self, request):
        qs = super().get_queryset(request) # TenantAdminMixin applies isolation
        return qs 
    

# Register `AccountCategory` model 
@admin.register(AccountCategory)
class AccountCategoryAdmin(TenantAdminMixin, admin.ModelAdmin):
    """ admin users can quickly see categories per company """
    list_display = ("id", "name", "company")
    list_filter = ("company",) # Add sidebar filter 
    search_fields = ("name",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs
    

# Register `AccountBalanceSnapshot` model
@admin.register(AccountBalanceSnapshot)
class AccountBalanceSnapshotAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("id", "company", "account", "snapshot_date", "debit_balance", "credit_balance")
    list_filter = ("company", "snapshot_date")

    # Fetch everything in one SQL join
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("company", "account")
