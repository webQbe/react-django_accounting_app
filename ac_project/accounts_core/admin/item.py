from django.contrib import admin

from accounts_core.models import FixedAsset, Item

from .mixins import TenantAdminMixin


# Register `Item` model
@admin.register(Item)
class ItemAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("id", "company", "sku", "name", "on_hand_quantity")
    search_fields = ("sku", "name")
    list_filter = ("company",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs


# Register `FixedAsset` model
@admin.register(FixedAsset)
class FixedAssetAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        "id",
        "company",
        "asset_code",
        "description",
        "purchase_date",
        "purchase_cost",
        "useful_life_years",
        "depreciation_method",
    )
    list_filter = ("company", "depreciation_method")
    search_fields = ("asset_code", "description")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs
