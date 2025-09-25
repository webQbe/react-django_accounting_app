from django.contrib import admin
from django.db.models import Prefetch
from accounts_core.models import Bill, BillLine, Vendor
from .actions import mark_bill_as_paid, mark_bill_as_posted
from .inlines import BillLineInline
from .mixins import TenantAdminMixin


# Register `Bill` model
@admin.register(Bill)
class BillAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        "id",
        "company",
        "bill_number",
        "vendor",
        "date",
        "due_date",
        "status",
        "total",
        "outstanding_amount",
    )
    list_filter = ("company", "status", "date")
    actions = [mark_bill_as_posted, mark_bill_as_paid]
    search_fields = ("bill_number", "vendor__name")
    inlines = [BillLineInline]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if qs is None:
            return Bill.objects.none()
        # Fetch everything in one SQL join
        bill_lines_qs = BillLine.objects.select_related("item", "account")
        qs = qs.select_related("company", "vendor").prefetch_related(
            # Prefetch bill lines
            # so we can loop over bill.prefetched_lines without extra queries
            Prefetch(
                "lines",
                # reverse relation from Bill → BillLine
                # (because of related_name="lines")
                queryset=bill_lines_qs,
                # store prefetched results into bill.prefetched_lines
                to_attr="prefetched_lines",
            )
        )
        return qs

    """ Enforce immutability at admin level """

    def get_readonly_fields(self, request, obj=None):
        # If there is an bill with "paid" status
        if obj and obj.status == "paid":
            # Build a list of all field names
            # Returning that list means every field becomes read-only
            return [f.name for f in self.model._meta.fields]
        # If bill is not paid, fallback to normal behavior
        return super().get_readonly_fields(request, obj)

    def has_delete_permission(self, request, obj=None):
        # If there is an bill with "paid" status
        if obj and obj.status == "paid":
            return False  # removes “Delete” option from admin for that bill
        return super().has_delete_permission(request, obj)


# Register `BillLine` model
@admin.register(BillLine)
class BillLineAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("id", "company", "bill", "item", "line_total", "account")
    search_fields = ("description",)

    # Fetch everything in one SQL join
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("company", "bill", "item")


# Register `Vendor` model
@admin.register(Vendor)
class VendorAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        "id",
        "company",
        "name",
        "contact_email",
        "payment_terms_days",
        "default_ap_account",
    )
    search_fields = ("name",)
    list_filter = ("company",)

    # Fetch everything in one SQL join
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("company", "default_ap_account")
