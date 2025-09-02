from django.contrib import admin
from decimal import Decimal
from django.contrib import admin, messages
from django.db import transaction
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from . import models

# ---------- Helpful inline admin classes ----------

class JournalLineInline(admin.TabularInline): # admin.TabularInline: shows related objects in table format (rows under parent form) 
    """ Show JournalLine rows on JournalEntry page """
    model = models.JournalLine 
    extra = 0  # don’t show “empty” rows by default (prevents clutter)
    fields = ("account", "description", "debit_amount", "credit_amount", "invoice", "bill", "bank_transaction", "fixed_asset", "is_posted")
    # fields appear in inline
    readonly_fields = ("is_posted",) # fields that can be seen but not edited, always read-only → protects audit trail
    show_change_link = True          # each row has a link to full detail page
    ordering = ("id",)               # lines appear in creation order

    def get_readonly_fields(self, request, obj=None):
        # Once journal is `posted`, all its lines become completely locked
        if obj and obj.status == "posted":
            return list(self.readonly_fields) + ["account", "description", "debit_amount", "credit_amount", "invoice", "bill", "bank_transaction", "fixed_asset"]
        return self.readonly_fields


class InvoiceLineInline(admin.TabularInline):
    """ Shows invoice lines under an Invoice page """
    model = models.InvoiceLine
    extra = 0
    fields = ("item", "description", "quantity", "unit_price", "line_total", "account")
    readonly_fields = ("line_total",) # `line_total` is computed automatically, so it’s read-only
    show_change_link = True


class BillLineInline(admin.TabularInline):
    """ Shows bill lines under a Bill page """
    model = models.BillLine
    extra = 0
    fields = ("description", "quantity", "unit_price", "line_total", "account")
    readonly_fields = ("line_total",) # not editable
    show_change_link = True


class BankTransactionInvoiceInline(admin.TabularInline):
    """ Let staff apply a bank transaction against one or more invoices. 
        Each row says: “this much from this transaction applies to that invoice.”"""
    model = models.BankTransactionInvoice
    extra = 0
    fields = ("invoice", "applied_amount")
