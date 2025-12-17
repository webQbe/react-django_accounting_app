from django.contrib import admin, messages
from .forms import FixedAssetAdminForm
from .mixins import TenantAdminMixin
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from accounts_core.models import FixedAsset, Item, Period
from ..services.depreciate import depreciate_asset


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
    form = FixedAssetAdminForm
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
    actions = ["run_depreciation_for_selected"]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs

    @admin.action(description="Run depreciation for selected assets (create JE)")
    def run_depreciation_for_selected(self, request, queryset):
        # Choose a period: either the latest open Period, or pass an id via UI/custom action form.
        try:
            period = Period.objects.filter(company__in=queryset.values_list("company", flat=True)).order_by("-start_date").first()
            if not period:
                messages.error(request, "No period found; please select or create a Period first.")
                return
        except Exception:
            messages.error(request, "Unable to determine period; please provide a period id in the action.")
            return

        # Use select_for_update inside a transaction
        try:
            count = 0
            with transaction.atomic():
                for asset in queryset.select_for_update():
                    # call service that creates the JE and posts it
                    je = depreciate_asset(asset.id, period.id, user=request.user)
                    count += 1
            messages.success(request, f"Depreciation recorded for {count} assets (Period {period}).")
        except Exception as e:
            messages.error(request, f"Error running depreciation: {e}")

