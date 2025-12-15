from django.contrib import admin, messages
from django.db.models import Prefetch
from django.db import transaction
from django.urls import path
from django.shortcuts import get_object_or_404, redirect
from accounts_core.models import Customer, Invoice, InvoiceLine
from ..services.update import open_invoice
from .actions import mark_inv_as_open, mark_inv_as_paid
from .inlines import InvoiceLineInline
from .mixins import TenantAdminMixin


# Register `Invoice` model
@admin.register(Invoice)
class InvoiceAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        "id",
        "company",
        "invoice_number",
        "customer",
        "date",
        "due_date",
        "status",
        "total",
        "outstanding_amount",
    )
    list_filter = ("company", "status", "date")
    actions = [mark_inv_as_open, mark_inv_as_paid, "post_selected_invoices"]
    change_form_template = "admin/accounts_core/invoice/change_form.html"
    search_fields = ("invoice_number", "customer__name")
    inlines = [InvoiceLineInline]

    @admin.action(description="Post selected invoices")
    def post_selected_invoices(self, request, queryset):
        success = 0
        for inv in queryset:
            try:
                with transaction.atomic():
                    # call the service that posts the invoice JE and transitions status
                    open_invoice(inv, user=request.user)
                success += 1
            except Exception as exc:
                self.message_user(
                    request,
                    f"Failed to post invoice {inv.pk}: {exc}",
                    level=messages.ERROR,
                )
        self.message_user(request, f"Posted {success} of {queryset.count()} invoices.", level=messages.SUCCESS)

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path('<path:object_id>/post/', self.admin_site.admin_view(self.post_invoice_view), name='accounts_core_invoice_post'),
        ]
        return custom + urls

    def post_invoice_view(self, request, object_id):
        inv = get_object_or_404(Invoice, pk=object_id)
        try:
            open_invoice(inv, user=request.user)
            messages.success(request, f"Invoice {inv} posted.")
        except Exception as exc:
            messages.error(request, f"Failed to post invoice: {exc}")
        # send user back to invoice change page
        return redirect(request.META.get("HTTP_REFERER") or f"../../{object_id}/change/")

    """
        For each Invoice, prefetch all its InvoiceLines, and
        within those lines also prefetch their linked Item & Account objects.
    """
    def get_queryset(self, request):
        # queryset of invoices admin page will display - Invoice.objects.all()
        qs = super().get_queryset(request)
        # ensure qs is a QuerySet
        if qs is None:
            return Invoice.objects.none()
        # Fetch invoice lines, also fetch their related Item & Account
        invoice_lines_qs = InvoiceLine.objects.select_related(
            "item", "account")
        # Use a SQL join so it fetches company & customer
        # in the same query as Invoice
        qs = qs.select_related("company", "customer").prefetch_related(
            # Prefetch invoice lines
            # so we can loop over invoice.prefetched_lines
            # without extra queries
            Prefetch(
                "lines",
                queryset=invoice_lines_qs,
                # store prefetched results into invoice.prefetched_lines
                to_attr="prefetched_lines",
                # reverse relation from Invoice → InvoiceLine
                # (because of related_name="lines")
            )
        )
        return qs

    """ Enforce immutability at admin level """

    def get_readonly_fields(self, request, obj=None):
        # If there is an invoice with "paid" status
        if obj and obj.status == "paid":
            # Build a list of all field names
            # Returning that list means every field becomes read-only
            return [f.name for f in self.model._meta.fields]
        # If invoice is not paid, fallback to normal behavior
        return super().get_readonly_fields(request, obj)

    def has_delete_permission(self, request, obj=None):
        # If there is an invoice with "paid" status
        if obj and obj.status == "paid":
            return False  # removes “Delete” option from admin for that invoice
        return super().has_delete_permission(request, obj)


# Register `InvoiceLine` model
@admin.register(InvoiceLine)
class InvoiceLineAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        "id", "company", "invoice", "item", "line_total", "account")
    list_filter = ("company",)
    search_fields = ("description",)

    # Fetch everything in one SQL join
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("company", "invoice", "item")


# Register `Customer` model
@admin.register(Customer)
class CustomerAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        "id",
        "company",
        "name",
        "contact_email",
        "payment_terms_days",
        "default_ar_account",
    )
    search_fields = ("name", "contact_email")
    list_filter = ("company",)

    # Fetch everything in one SQL join
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        # Join related tables in initial query
        return qs.select_related("company", "default_ar_account")
    """ Now Django won't do a separate query
    for each company and default_ar_account
    while rendering the list. """
