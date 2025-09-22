from django.contrib import admin
from accounts_core.models import JournalLine, InvoiceLine, BillLine, BankTransactionInvoice, BankTransactionBill
from .mixins import TenantAdminMixin
from .forms import InvoiceLineForm

# ---------- Helpful inline admin classes ----------

class JournalLineInline(TenantAdminMixin, admin.TabularInline): # admin.TabularInline: shows related objects in table format (rows under parent form) 
    """ Show JournalLine rows on JournalEntry page """
    model = JournalLine 
    extra = 0  # don’t show “empty” rows by default (prevents clutter)
    fields = ("account", "description", "debit_amount", "credit_amount", "invoice", "bill", "bank_transaction", "fixed_asset", "is_posted")
    # fields appear in inline
    readonly_fields = ("is_posted",) # fields that can be seen but not edited, always read-only → protects audit trail
    show_change_link = True          # each row has a link to full detail page
    ordering = ("id",)               # lines appear in creation order

    # Restrict company FK in dropdown 
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("account", "invoice", "bill", "bank_transaction", "fixed_asset")

    def get_readonly_fields(self, request, obj=None):
        # Once journal is `posted`, all its lines become completely locked
        if obj and obj.status == "posted":
            return list(self.readonly_fields) + ["account", "description", "debit_amount", "credit_amount", "invoice", "bill", "bank_transaction", "fixed_asset"]
        return self.readonly_fields


class InvoiceLineInline(TenantAdminMixin, admin.TabularInline):
    """ Shows invoice lines under an Invoice page """
    model = InvoiceLine
    form = InvoiceLineForm
    exclude = ("company",) #  users don’t need to set `company` manually
    extra = 0
    fields = ("item", "description", "quantity", "unit_price", "line_total", "account")
    readonly_fields = ("line_total",) # `line_total` is computed automatically, so it’s read-only
    show_change_link = True

    # Restrict company FK in dropdown 
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("item", "account")


class BillLineInline(TenantAdminMixin, admin.TabularInline):
    """ Shows bill lines under a Bill page """
    model = BillLine
    extra = 0
    fields = ("item", "description", "quantity", "unit_price", "line_total", "account")
    readonly_fields = ("line_total",) # not editable
    show_change_link = True

    # Restrict company FK in dropdown 
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("item", "account")


class BankTransactionInvoiceInline(TenantAdminMixin, admin.TabularInline):
    """ Let staff apply a bank transaction against one or more invoices. 
        Each row says: “this much from this transaction applies to that invoice.”"""
    model = BankTransactionInvoice
    extra = 0
    fields = ("invoice", "bank_transaction","applied_amount")

    # Restrict company FK in dropdown 
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("invoice", "account", "bank_transaction")

    

class BankTransactionBillInline(TenantAdminMixin, admin.TabularInline):
    """ Let staff apply a bank transaction against one or more bills. 
        Each row says: “this much from this transaction applies to that bill.”"""
    model = BankTransactionBill
    extra = 0
    fields = ("bill", "bank_transaction", "applied_amount")

    # Restrict company FK in dropdown 
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("bill", "account", "bank_transaction")
