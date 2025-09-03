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


# ---------- Admin actions ----------

# Bulk-post multiple journal entries from Django admin list view
def post_journal_entries(modeladmin, # `ModelAdmin` class for JournalEntry
                         request,    #  HTTP request object
                         queryset    #  record what admin selected from list view
                        ): 
    """Attempt to post selected draft journal entries."""
    success = 0
    for je in queryset: # Loop through all selected journal entries
        # Track how many got successfully posted
        try: 
             # Wrap each posting in a DB transaction
            with transaction.atomic():     # Ensure either all steps succeed or DB rolls back
                # Call `post()` on `JournalEntry` model
                je.post(user=request.user) # Pass `request.user`to record who posted it
            success += 1                   # If no error → increment success counter
        except Exception as exc:  # Catch exception: ValidationError, etc.
            # Show error message in Django admin interface
            modeladmin.message_user(request, f"Could not post JournalEntry {je.pk}: {exc}", level=messages.ERROR)
    
    # After the loop, give user success message for how many entries posted successfully
    modeladmin.message_user(request, f"Posted {success} JournalEntry(s).", level=messages.SUCCESS)

# Translatable text to show up in admin “Actions” dropdown
post_journal_entries.short_description = _("Post selected journal entries (make immutable)") 