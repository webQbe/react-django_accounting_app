from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db import transaction

# ---------- Admin actions ----------


@admin.action(description="Mark selected journals as Posted")
# Bulk-post multiple journal entries from Django admin list view
def post_journal_entries(
    modeladmin,  # `ModelAdmin` class for JournalEntry
    request,  # HTTP request object
    queryset,  # record what admin selected from list view
):
    """Attempt to post selected draft journal entries."""
    success = 0
    for je in queryset:  # Loop through all selected journal entries
        # Track how many got successfully posted
        try:
            # Wrap each posting in a DB transaction
            # Ensure either all steps succeed or DB rolls back
            with transaction.atomic():
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

    # After the loop, give user success message
    # for how many entries posted successfully
    modeladmin.message_user(
        request, f"Posted {success} JournalEntry(s).", level=messages.SUCCESS
    )


# Translatable text to show up in admin “Actions” dropdown
# post_journal_entries.short_description =
#   _("Post selected journal entries (make immutable)")


""" Add button/action that call invoice.transition_to("open") """


@admin.action(description="Mark selected invoices as Open")
def mark_inv_as_open(modeladmin, request, queryset):
    for inv in queryset:
        try:
            inv.transition_to("open")
            # enforces the rules coded in transition_to()
            # instead of letting admins bypass them
        except ValidationError as e:
            modeladmin.message_user(
                request, f"{inv}: {e}", level=messages.ERROR)


""" call invoice.transition_to("paid") """


@admin.action(description="Mark selected invoices as Paid")
def mark_inv_as_paid(modeladmin, request, queryset):
    for inv in queryset:
        try:
            inv.transition_to("paid")
        except ValidationError as e:
            modeladmin.message_user(
                request, f"{inv}: {e}", level=messages.ERROR)


""" Add button/action that call
    bank transaction.transition_to("partially_applied") """


@admin.action(
        description="Mark selected bank transactions as Partially applied")
def mark_as_partially_applied(modeladmin, request, queryset):
    for bt in queryset:
        try:
            bt.transition_to("partially_applied")
            # enforces the rules coded in transition_to()
            # instead of letting admins bypass them
        except ValidationError as e:
            modeladmin.message_user(
                request, f"{bt}: {e}", level=messages.ERROR)


""" call bank transaction.transition_to("fully_applied") """


@admin.action(description="Mark selected bank transactions as Fully applied")
def mark_as_fully_applied(modeladmin, request, queryset):
    for bt in queryset:
        try:
            bt.transition_to("fully_applied")
        except ValidationError as e:
            modeladmin.message_user(
                request, f"{bt}: {e}", level=messages.ERROR)


""" Add button/action that call bill.transition_to("posted") """


@admin.action(description="Mark selected bills as Posted")
def mark_bill_as_posted(modeladmin, request, queryset):
    for bill in queryset:
        try:
            bill.transition_to("posted")
        except ValidationError as e:
            modeladmin.message_user(
                request, f"{bill}: {e}", level=messages.ERROR)


""" call bill.transition_to("paid") """


@admin.action(description="Mark selected bills as Paid")
def mark_bill_as_paid(modeladmin, request, queryset):
    for bill in queryset:
        try:
            bill.transition_to("paid")
        except ValidationError as e:
            modeladmin.message_user(
                request, f"{bill}: {e}", level=messages.ERROR)
