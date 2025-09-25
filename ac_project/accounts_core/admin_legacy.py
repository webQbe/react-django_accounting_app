from decimal import Decimal

from django import \
    forms  # Extended `AbstractUser` with extra fields needs custom admin forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import UserChangeForm as DjangoUserChangeForm
from django.contrib.auth.forms import \
    UserCreationForm as DjangoUserCreationForm
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Prefetch
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from . import models


# ---------- Base Admin Mixin ----------
class TenantAdminMixin:
    """
    Enforce tenant isolation in Django admin.
    Uses request.company (set by your CurrentCompanyMiddleware) or falls back to request.user.company.
    """

    def _get_request_company(self, request):
        # prefer request.company (middleware)
        # but fallback to request.user.company if present
        company = getattr(request, "company", None)
        if company is None:
            user = getattr(request, "user", None)
            if user and hasattr(user, "company"):
                company = getattr(user, "company")
        return company

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if qs is None:  # if super returned None, return an empty queryset instead
            from django.apps import apps

            return apps.get_model(
                self.model._meta.app_label, self.model._meta.model_name
            ).objects.none()

        company = self._get_request_company(request)

        # If superuser, show everything;
        # otherwise restrict to company if available
        if request.user.is_superuser:
            return qs
        if company is None:
            # If no company available in request, return none
            return qs.none()
        return qs.filter(company=company)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """
        Restrict foreignkey dropdowns to the current company where appropriate.
        Example: company field, account field, customer/vendor field that are company-scoped.
        """
        company = self._get_request_company(request)

        # If FK is to Company and user is not superuser,
        # restrict to user's company
        if db_field.name == "company" and not request.user.is_superuser:
            if company is not None:
                kwargs["queryset"] = db_field.related_model.objects.filter(
                    pk=company.pk
                )
            else:
                kwargs["queryset"] = db_field.related_model.objects.none()
            return super().formfield_for_foreignkey(db_field, request, **kwargs)

        # if related model has a `company` field,
        # restrict it to request's company
        rel_model = getattr(db_field, "related_model", None)
        if (
            rel_model is not None
            and hasattr(rel_model, "company")
            and not request.user.is_superuser
        ):
            if company is not None:
                kwargs["queryset"] = rel_model.objects.filter(company=company)
            else:
                kwargs["queryset"] = rel_model.objects.none()

        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        # Ensure object is always owned by company on save (unless superuser)
        if not request.user.is_superuser:
            company = self._get_request_company(request)
            if company is not None:
                obj.company = company
        super().save_model(request, obj, form, change)


# ---------- Helpful inline admin classes ----------


class JournalLineInline(
    TenantAdminMixin, admin.TabularInline
):  # admin.TabularInline: shows related objects in table format (rows under parent form)
    """Show JournalLine rows on JournalEntry page"""

    model = models.JournalLine
    extra = 0  # don’t show “empty” rows by default (prevents clutter)
    fields = (
        "account",
        "description",
        "debit_amount",
        "credit_amount",
        "invoice",
        "bill",
        "bank_transaction",
        "fixed_asset",
        "is_posted",
    )
    # fields appear in inline
    readonly_fields = (
        "is_posted",
    )  # fields that can be seen but not edited, always read-only → protects audit trail
    show_change_link = True  # each row has a link to full detail page
    ordering = ("id",)  # lines appear in creation order

    # Restrict company FK in dropdown
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related(
            "account", "invoice", "bill", "bank_transaction", "fixed_asset"
        )

    def get_readonly_fields(self, request, obj=None):
        # Once journal is `posted`, all its lines become completely locked
        if obj and obj.status == "posted":
            return list(self.readonly_fields) + [
                "account",
                "description",
                "debit_amount",
                "credit_amount",
                "invoice",
                "bill",
                "bank_transaction",
                "fixed_asset",
            ]
        return self.readonly_fields


class InvoiceLineForm(forms.ModelForm):
    class Meta:
        model = models.InvoiceLine
        exclude = ("company",)  # hide company from inline form

    def clean(self):
        # set company_id if possible (form.instance.invoice may have pk after admin saves parent)
        if getattr(self.instance, "invoice_id", None) and not getattr(
            self.instance, "company_id", None
        ):
            self.instance.company_id = (
                models.Invoice.objects.only("company_id")
                .get(pk=self.instance.invoice_id)
                .company_id
            )
        return super().clean()


class InvoiceLineInline(TenantAdminMixin, admin.TabularInline):
    """Shows invoice lines under an Invoice page"""

    model = models.InvoiceLine
    form = InvoiceLineForm
    exclude = ("company",)  #  users don’t need to set `company` manually
    extra = 0
    fields = ("item", "description", "quantity", "unit_price", "line_total", "account")
    readonly_fields = (
        "line_total",
    )  # `line_total` is computed automatically, so it’s read-only
    show_change_link = True

    # Restrict company FK in dropdown
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("item", "account")


class BillLineInline(TenantAdminMixin, admin.TabularInline):
    """Shows bill lines under a Bill page"""

    model = models.BillLine
    extra = 0
    fields = ("item", "description", "quantity", "unit_price", "line_total", "account")
    readonly_fields = ("line_total",)  # not editable
    show_change_link = True

    # Restrict company FK in dropdown
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("item", "account")


class BankTransactionInvoiceInline(TenantAdminMixin, admin.TabularInline):
    """Let staff apply a bank transaction against one or more invoices.
    Each row says: “this much from this transaction applies to that invoice.”"""

    model = models.BankTransactionInvoice
    extra = 0
    fields = ("invoice", "bank_transaction", "applied_amount")

    # Restrict company FK in dropdown
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("invoice", "account", "bank_transaction")


class BankTransactionBillInline(TenantAdminMixin, admin.TabularInline):
    """Let staff apply a bank transaction against one or more bills.
    Each row says: “this much from this transaction applies to that bill.”"""

    model = models.BankTransactionBill
    extra = 0
    fields = ("bill", "bank_transaction", "applied_amount")

    # Restrict company FK in dropdown
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("bill", "account", "bank_transaction")


# ---------- Admin actions ----------


# Bulk-post multiple journal entries from Django admin list view
@admin.action(description="Mark selected journals as Posted")
def post_journal_entries(
    modeladmin,  # `ModelAdmin` class for JournalEntry
    request,  #  HTTP request object
    queryset,  #  record what admin selected from list view
):
    """Attempt to post selected draft journal entries."""
    success = 0
    for je in queryset:  # Loop through all selected journal entries
        # Track how many got successfully posted
        try:
            # Wrap each posting in a DB transaction
            with transaction.atomic():  # Ensure either all steps succeed or DB rolls back
                # Call `post()` on `JournalEntry` model
                je.transition_to(
                    "posted", user=request.user
                )  # Pass `request.user`to record who posted it
            success += 1  # If no error → increment success counter
        except Exception as exc:  # Catch exception: ValidationError, etc.
            # Show error message in Django admin interface
            modeladmin.message_user(
                request,
                f"Could not post JournalEntry {je.pk}: {exc}",
                level=messages.ERROR,
            )

    # After the loop, give user success message for how many entries posted successfully
    modeladmin.message_user(
        request, f"Posted {success} JournalEntry(s).", level=messages.SUCCESS
    )


# Translatable text to show up in admin “Actions” dropdown
# post_journal_entries.short_description = _("Post selected journal entries (make immutable)")


""" Add button/action that call invoice.transition_to("open") """


@admin.action(description="Mark selected invoices as Open")
def mark_inv_as_open(modeladmin, request, queryset):
    for inv in queryset:
        try:
            inv.transition_to("open")
            # enforces the rules coded in transition_to() instead of letting admins bypass them
        except ValidationError as e:
            modeladmin.message_user(request, f"{inv}: {e}", level=messages.ERROR)


""" call invoice.transition_to("paid") """


@admin.action(description="Mark selected invoices as Paid")
def mark_inv_as_paid(modeladmin, request, queryset):
    for inv in queryset:
        try:
            inv.transition_to("paid")
        except ValidationError as e:
            modeladmin.message_user(request, f"{inv}: {e}", level=messages.ERROR)


""" Add button/action that call bank transaction.transition_to("partially_applied") """


@admin.action(description="Mark selected bank transactions as Partially applied")
def mark_inv_as_partially_applied(modeladmin, request, queryset):
    for bt in queryset:
        try:
            bt.transition_to("partially_applied")
            # enforces the rules coded in transition_to() instead of letting admins bypass them
        except ValidationError as e:
            modeladmin.message_user(request, f"{bt}: {e}", level=messages.ERROR)


""" call bank transaction.transition_to("fully_applied") """


@admin.action(description="Mark selected bank transactions as Fully applied")
def mark_inv_as_fully_applied(modeladmin, request, queryset):
    for bt in queryset:
        try:
            bt.transition_to("fully_applied")
        except ValidationError as e:
            modeladmin.message_user(request, f"{bt}: {e}", level=messages.ERROR)


# ---------- ModelAdmin registrations ----------


# Register `Company` model in admin with this custom config
@admin.register(models.Company)
class CompanyAdmin(admin.ModelAdmin):
    """a clean admin table for browsing companies"""

    # columns shown in company list view
    list_display = ("id", "name", "slug", "currency_code", "created_at")
    search_fields = ("name", "slug")  # enable search by name and slug
    ordering = ("name",)  # sort companies alphabetically by default

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        # Fetch all memberships and their users in bulk
        return qs.prefetch_related("memberships__user")
        """ Django “stitches” memberships and users back onto each company """


# Register `AccountCategory` model
@admin.register(models.AccountCategory)
class AccountCategoryAdmin(TenantAdminMixin, admin.ModelAdmin):
    """admin users can quickly see categories per company"""

    list_display = ("id", "name", "company")
    list_filter = ("company",)  # Add sidebar filter
    search_fields = ("name",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs


# Register `Account` model
@admin.register(models.Account)
class AccountAdmin(TenantAdminMixin, admin.ModelAdmin):
    # show key accounting fields
    list_display = (
        "id",
        "company",
        "code",
        "name",
        "ac_type",
        "normal_balance",
        "is_active",
    )
    list_filter = ("company", "ac_type", "is_active")
    search_fields = ("code", "name")
    ordering = ("company", "code")  # accounts grouped by company, then sorted by code
    fieldsets = (
        # customize layout in edit form, all fields appear neatly grouped under "None"
        (
            None,
            {
                "fields": (
                    "company",
                    "code",
                    "name",
                    "ac_type",
                    "normal_balance",
                    "category",
                    "parent",
                    "is_active",
                )
            },
        ),
    )

    # Tenant Filtering
    def get_queryset(self, request):
        qs = super().get_queryset(request)  # TenantAdminMixin applies isolation
        return qs


# Register `Period` model
@admin.register(models.Period)
class PeriodAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("id", "company", "name", "start_date", "end_date", "is_closed")
    list_filter = ("company", "is_closed")
    search_fields = ("name",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs


# Register `Customer` model
@admin.register(models.Customer)
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
        """ Now Django won’t do a separate query for each company and default_ar_account 
            while rendering the list. """


# Register `Vendor` model
@admin.register(models.Vendor)
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


# Register `Item` model
@admin.register(models.Item)
class ItemAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("id", "company", "sku", "name", "on_hand_quantity")
    search_fields = ("sku", "name")
    list_filter = ("company",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs


# Register `JournalEntry` model
@admin.register(models.JournalEntry)
class JournalEntryAdmin(TenantAdminMixin, admin.ModelAdmin):
    """Basic admin display setup"""

    list_display = (
        "id",
        "company",
        "date",
        "reference",
        "status",
        "posted_at",
        "created_by",
        "balanced",
    )
    list_filter = ("company", "status", "date")
    search_fields = ("reference", "description", "id")
    readonly_fields = (
        "posted_at",
        "created_by",
    )  # users can see but not edit these (e.g., `posted_at`, `created_by`)
    inlines = [
        JournalLineInline
    ]  # allows editing JournalLines directly on JournalEntry page
    actions = [
        post_journal_entries
    ]  # adds a bulk action (“Post selected journal entries”) to list view

    # Fetch everything in one SQL join
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("company", "created_by").prefetch_related(
            "journalline_set__account"
        )
        """ For each JournalEntry, prefetch all its JournalLines, and 
            within those lines also prefetch their linked Account objects."""

    """ Computed column for balance check """

    def balanced(self, obj):  # Show total debits / total credits for each journal
        # check if entries balance with `compute_totals()` (model method)
        d, c = obj.compute_totals()
        # format: bold debits / small credits
        return format_html(
            "<b>{}</b> / <small>{}</small>", d or Decimal("0.00"), c or Decimal("0.00")
        )

    # set column header in admin
    balanced.short_description = "Debits / Credits"

    """ Make entries immutable once posted """

    def get_readonly_fields(self, request, obj=None):
        r = list(self.readonly_fields)
        # if posted: make fields readonly and prevent deletion/changes for data integrity
        if obj and obj.status == "posted":
            r += ["company", "date", "reference", "description", "status", "period"]
            # This prevents someone from sneaking in and editing a finalized journal
        return r

    """ Prevent deletion after posting """

    def has_delete_permission(self, request, obj=None):
        # prevent deletion of posted journals
        if obj and obj.status == "posted":
            return False  # If posted → deletion is blocked
        return super().has_delete_permission(request, obj)
        # Draft journals can still be deleted

    """ Restrict changes on posted journals """

    def has_change_permission(self, request, obj=None):
        # prevent non-superusers/normal users from editing posted journals
        if obj and obj.status == "posted" and not request.user.is_superuser:
            return False
        return super().has_change_permission(request, obj)
        # Only AccountAdminsuperusers can still change them (like an override for emergencies)


# Register `JournalLine` model
@admin.register(models.JournalLine)
class JournalLineAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        "id",
        "company",
        "journal",
        "account",
        "debit_original",
        "credit_original",
        "debit_local",
        "credit_local",
        "is_posted",
    )
    list_filter = ("company", "account")
    search_fields = ("description",)
    readonly_fields = ("is_posted",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs


# Register `Invoice` model
@admin.register(models.Invoice)
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
    actions = [mark_inv_as_open, mark_inv_as_paid]
    search_fields = ("invoice_number", "customer__name")
    inlines = [InvoiceLineInline]

    """ 
        For each Invoice, prefetch all its InvoiceLines, and 
        within those lines also prefetch their linked Item & Account objects.
    """

    def get_queryset(self, request):
        # queryset of invoices admin page will display - Invoice.objects.all()
        qs = super().get_queryset(request)
        # ensure qs is a QuerySet
        if qs is None:
            return models.Invoice.objects.none()
        # Fetch invoice lines, also fetch their related Item & Account
        invoice_lines_qs = models.InvoiceLine.objects.select_related("item", "account")
        # Use a SQL join so it fetches company & customer in the same query as Invoice
        qs = qs.select_related("company", "customer").prefetch_related(
            # Prefetch invoice lines
            # so we can loop over invoice.prefetched_lines without extra queries
            Prefetch(
                "lines",  # reverse relation from Invoice → InvoiceLine (because of related_name="lines")
                queryset=invoice_lines_qs,
                # store prefetched results into invoice.prefetched_lines
                to_attr="prefetched_lines",
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
@admin.register(models.InvoiceLine)
class InvoiceLineAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("id", "company", "invoice", "item", "line_total", "account")
    list_filter = ("company",)
    search_fields = ("description",)

    # Fetch everything in one SQL join
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("company", "invoice", "item")


# Register `Bill` model
@admin.register(models.Bill)
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
    search_fields = ("bill_number", "vendor__name")
    inlines = [BillLineInline]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if qs is None:
            return models.Bill.objects.none()
        # Fetch everything in one SQL join
        bill_lines_qs = models.BillLine.objects.select_related("item", "account")
        qs = qs.select_related("company", "vendor").prefetch_related(
            # Prefetch bill lines
            # so we can loop over bill.prefetched_lines without extra queries
            Prefetch(
                "lines",  # reverse relation from Bill → BillLine (because of related_name="lines")
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
@admin.register(models.BillLine)
class BillLineAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("id", "company", "bill", "item", "line_total", "account")
    search_fields = ("description",)

    # Fetch everything in one SQL join
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("company", "bill", "item")


# Register `BankAccount` model
@admin.register(models.BankAccount)
class BankAccountAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        "id",
        "company",
        "name",
        "account_number_masked",
        "currency_code",
        "last_reconciled_at",
    )
    list_filter = ("company",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs


# Register `BankTransaction` model
@admin.register(models.BankTransaction)
class BankTransactionAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        "id",
        "company",
        "bank_account",
        "payment_date",
        "amount",
        "payment_method",
        "reference",
        "status",
    )
    list_filter = (
        "company",
        "bank_account",
        "payment_method",
        "payment_date",
        "status",
    )
    actions = [mark_inv_as_partially_applied, mark_inv_as_fully_applied]
    inlines = [BankTransactionInvoiceInline, BankTransactionBillInline]

    # Fetch everything in one SQL join
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("company", "bank_account").prefetch_related(
            "banktransactioninvoice_set__invoice",  # all invoices for each BT
            "banktransactionbill_set__bill",  # all bills for each BT
        )
        """  
        When you load invoices/bills for each bank transaction, 
        also grab linked Invoice/Bill row at the same time.
        """


# Register `BankTransactionInvoice` model
@admin.register(models.BankTransactionInvoice)
class BankTransactionInvoiceAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("id", "company", "bank_transaction", "invoice", "applied_amount")
    list_filter = ("company", "bank_transaction")
    search_fields = ("invoice__invoice_number",)

    # Fetch everything in one SQL join
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("company", "bank_transaction", "invoice")


# Register `BankTransactionBill` model
@admin.register(models.BankTransactionBill)
class BankTransactionBillAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("id", "company", "bank_transaction", "bill", "applied_amount")
    list_filter = ("company", "bank_transaction")
    search_fields = ("bill__bill_number",)

    # Fetch everything in one SQL join
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("company", "bank_transaction", "bill")


# Register `FixedAsset` model
@admin.register(models.FixedAsset)
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


# Register `AccountBalanceSnapshot` model
@admin.register(models.AccountBalanceSnapshot)
class AccountBalanceSnapshotAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        "id",
        "company",
        "account",
        "snapshot_date",
        "debit_balance",
        "credit_balance",
    )
    list_filter = ("company", "snapshot_date")

    # Fetch everything in one SQL join
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("company", "account")


# Register `AuditLog` model
@admin.register(models.AuditLog)
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


# Register `Currency` model
@admin.register(models.Currency)
class CurrencyAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("code", "name", "symbol", "decimal_places")
    search_fields = ("code", "name")
    ordering = ("code",)
    list_per_page = 50  # set pagination so that only 50 currencies show per page
    # ISO currency tables can have \~180 entries


# Register EntityMembership model
@admin.register(models.EntityMembership)
class EntityMembershipAdmin(TenantAdminMixin, admin.ModelAdmin):
    # Show memberships
    list_display = ("user", "company", "role", "is_active", "created_at")
    list_filter = ("role", "is_active", "company")
    search_fields = ("user__username", "user__email", "company__name")
    readonly_fields = ("created_at",)  # prevent tampering with creation date
    ordering = ("company__name", "user__username")

    # Scope querysets by company
    # prevents someone from snooping into memberships of other companies
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        # Fetch everything in one SQL join
        qs = qs.select_related("company", "user")
        if request.user.is_superuser:  # Superusers see all memberships
            return qs
        # Non-superusers only see memberships of their companies
        allowed_company_ids = request.user.memberships.values_list(
            "company_id", flat=True
        )
        return qs.filter(company_id__in=allowed_company_ids)

    """    # Prevent “sneaking in” a membership for some unrelated company
        # Restrict available choices for company FK
        def formfield_for_foreignkey(self, db_field, request=None, **kwargs):
            
            Limit company or user choices when creating a membership in the admin:
            - non-superusers can only pick companies they belong to
            - optionally, limit user choices (so they can only add users that are co-members or invite new ones).
            
            field = super().formfield_for_foreignkey(db_field, request, **kwargs)
            if not request or request.user.is_superuser:
                return field

            if db_field.name == "company":
                allowed_company_ids = request.user.memberships.values_list("company_id", flat=True)
                # staff user can only assign a membership to their companies
                field.queryset = models.Company.objects.filter(id__in=allowed_company_ids)
            return field 
    """

    # Permission checks
    # To modify memberships
    def has_change_permission(self, request, obj=None):
        # Skip superusers
        if request.user.is_superuser:
            return True

        # Get company IDs where current user has owner/admin role
        user_company_ids = set(
            request.user.memberships.filter(role__in=("owner", "admin")).values_list(
                "company_id", flat=True
            )
        )

        if obj is None:
            # obj is None → decides if user can see change list view
            return bool(
                user_company_ids
            )  # True if user has at least one company where they’re Owner/Admin
            """ If we’re checking the general change permission (no specific object), 
              only allow access if the user is an Owner/Admin in at least one company. """

        # obj is not None → decides if user can edit a particular record
        return obj.company_id in user_company_ids
        """ You can only edit this membership if it belongs to 
            a company where you are an Owner/Admin. """

    # To delete memberships
    def has_delete_permission(self, request, obj=None):
        # needs permission to modify memberships
        return self.has_change_permission(request, obj)

    # To add memberships
    def has_add_permission(self, request):
        if request.user.is_superuser:  # Superusers bypass check
            return True
        # non-superusers must be Owner/Admin of at least one company to add new memberships
        return request.user.memberships.filter(role__in=("owner", "admin")).exists()


# -----------------------------
# Register custom admin forms
# ----------------------------


# Subclass `DjangoUserCreationForm` (form used when adding a new user)
class UserAdminCreationForm(DjangoUserCreationForm):
    class Meta(DjangoUserCreationForm.Meta):
        model = models.User  # Points `model` to custom User model
        fields = ("username", "email", "default_company")


# Subclass `DjangoUserChangeForm` (form used when editing an existing user)
class UserAdminChangeForm(DjangoUserChangeForm):
    # override `Meta` to include custom model & any extra fields
    class Meta(DjangoUserChangeForm.Meta):
        model = models.User
        fields = (
            "username",
            "email",
            "is_active",
            "is_staff",
            "is_superuser",
            "default_company",
        )


# Extend stock `DjangoUserAdmin`
@admin.register(models.User)  # Hook custom `User` model into Django Admin
class UserAdmin(DjangoUserAdmin):  # Inherit all good stuff from `DjangoUserAdmin`
    # Use custom forms you defined to create/edit views
    add_form = UserAdminCreationForm
    form = UserAdminChangeForm
    model = models.User

    # fields shown in list
    list_display = ("username", "email", "get_full_name", "is_staff", "default_company")
    list_filter = ("is_staff", "is_superuser", "is_active")
    search_fields = ("username", "email", "first_name", "last_name")
    ordering = ("username",)

    # Group fields logically on edit user page
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        (_("Personal info"), {"fields": ("first_name", "last_name", "email", "phone")}),
        # include default_company in fieldsets for edit and creation
        (_("Company / Defaults"), {"fields": ("default_company",)}),
        # Keep stock Django grouping (`permissions`, `important dates`)
        (
            _("Permissions"),
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
            },
        ),
        (_("Important dates"), {"fields": ("last_login", "date_joined")}),
    )

    # Control which fields appear when creating a new user in admin
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                # Include custom field `default_company` right away
                "fields": (
                    "username",
                    "email",
                    "default_company",
                    "password1",
                    "password2",
                ),
            },
        ),
    )

    # Queryset filtering (multi-tenant security)
    # Tenant scoping: limit visible users to memberships of the request.user's companies
    def get_queryset(self, request):
        qs = super().get_queryset(request)

        # Prevent cross-tenant leakage in multi-tenant setup
        if request.user.is_superuser:
            # superusers see all users
            return qs

        # non-superuser should only see users who share a company membership
        # Get a list of company IDs logged-in user belongs to
        allowed_company_ids = request.user.memberships.values_list(
            "company_id", flat=True
        )

        """ Return all users who have at least one membership in any of the companies 
            that I (the logged-in user) belong to. Don’t show duplicates """
        return qs.filter(memberships__company_id__in=allowed_company_ids).distinct()
        # In EntityMembership model → related_name="memberships"
        # So 'memberships__company_id__in' checks: User → EntityMembership → company_id
        # Filter only keeps users with 'allowed_company_ids'
        # .distinct() prevents a user who belongs to multiple companies appearing multiple times
