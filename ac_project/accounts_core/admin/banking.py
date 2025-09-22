from django.contrib import admin
from django.utils.html import format_html
from accounts_core.models import BankAccount, BankTransaction, BankTransactionInvoice, BankTransactionBill, Currency
from .mixins import TenantAdminMixin
from .actions import mark_as_partially_applied, mark_as_fully_applied
from .inlines import BankTransactionInvoiceInline, BankTransactionBillInline

# Register `BankAccount` model
@admin.register(BankAccount)
class BankAccountAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("id", "company", "name", "account_number_masked", "currency_code", "last_reconciled_at")
    list_filter = ("company",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs

# Register `BankTransaction` model
@admin.register(BankTransaction)
class BankTransactionAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("id", "company", "bank_account", "payment_date", "amount", "payment_method", "reference", "status")
    list_filter = ("company", "bank_account", "payment_method", "payment_date", "status")
    actions = [mark_as_partially_applied, mark_as_fully_applied]
    inlines = [BankTransactionInvoiceInline, BankTransactionBillInline]

    # Fetch everything in one SQL join
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("company", "bank_account").prefetch_related(
            "banktransactioninvoice_set__invoice", # all invoices for each BT
            "banktransactionbill_set__bill"        # all bills for each BT
        )
        """  
        When you load invoices/bills for each bank transaction, 
        also grab linked Invoice/Bill row at the same time.
        """


# Register `BankTransactionInvoice` model
@admin.register(BankTransactionInvoice)
class BankTransactionInvoiceAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("id", "company", "bank_transaction", "invoice", "applied_amount")
    list_filter = ("company", "bank_transaction")
    search_fields = ("invoice__invoice_number",)

    # Fetch everything in one SQL join
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("company", "bank_transaction", "invoice")


# Register `BankTransactionBill` model
@admin.register(BankTransactionBill)
class BankTransactionBillAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("id", "company", "bank_transaction", "bill", "applied_amount")
    list_filter = ("company", "bank_transaction")
    search_fields = ("bill__bill_number",)

    # Fetch everything in one SQL join
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("company", "bank_transaction", "bill")


# Register `Currency` model
@admin.register(Currency)
class CurrencyAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("code", "name", "symbol", "decimal_places")
    search_fields = ("code", "name")
    ordering = ("code",)
    list_per_page = 50 # set pagination so that only 50 currencies show per page 
    # ISO currency tables can have \~180 entries
