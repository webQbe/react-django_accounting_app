from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from accounts_core.models import JournalEntry

# ---------- Admin actions ----------

@admin.action(description="Mark selected journals as Posted")
# Bulk-post multiple journal entries from Django admin list view
def post_journal_entries(
    modeladmin,  # `ModelAdmin` class for JournalEntry
    request,  # HTTP request object
    queryset,  # record what admin selected from list view
):
    """
    Admin action: attempt to post each selected JournalEntry safely.
    - Locks each JE row while posting (select_for_update) to avoid races.
    - Posts entries one-by-one inside their own transaction (safer for long lists).
    - Reports success / per-entry failures via admin messages.
    """
    # Only attempt to post entries which are not already posted.
    candidates = queryset.filter(status__in=["draft", "ready"])
    total = candidates.count()
    success = 0
    failures = 0

    # Process one journal entry per tiny transaction to avoid locking many rows at once.
    for je in candidates:
        try:
            with transaction.atomic():
                # re-load & lock the row to avoid race conditions
                je_locked = JournalEntry.objects.select_for_update().get(pk=je.pk)
                # call the model-level post logic (which itself is transactional/idempotent)
                je_locked.post(user=request.user)
            success += 1
        except ValidationError as exc:
            failures += 1
            modeladmin.message_user(
                request,
                _("Could not post JournalEntry %(pk)s: %(err)s") % {"pk": je.pk, "err": exc},
                level=messages.ERROR,
            )
        except Exception as exc:
            failures += 1
            # catch-all so one failure doesn't stop the whole batch
            modeladmin.message_user(
                request,
                _("Error posting JournalEntry %(pk)s: %(err)s") % {"pk": je.pk, "err": exc},
                level=messages.ERROR,
            )

    # Final summary message
    modeladmin.message_user(
        request,
        _("Posted %(success)d of %(total)d journal entries. %(failures)d failed.") % {
            "success": success,
            "total": total,
            "failures": failures,
        },
        level=messages.SUCCESS if failures == 0 else messages.WARNING,
    )

    # Set user
    for je in queryset:
        try:
            je.post(user=request.user)    # ensure user is passed
            modeladmin.message_user(request, f"Posted JE {je.pk}")
        except Exception as e:
            modeladmin.message_user(request, f"Failed to post JE {je.pk}: {e}", level=messages.ERROR)


# how it will show in the admin UI
post_journal_entries.short_description = _("Post selected journal entries (make immutable)")



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