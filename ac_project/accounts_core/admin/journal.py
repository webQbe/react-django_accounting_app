from decimal import Decimal
from django.contrib import admin
from django.utils.html import format_html
from accounts_core.models import JournalEntry, JournalLine
from django.db.models import Prefetch
from .actions import post_journal_entries
from .inlines import JournalLineInline
from .mixins import TenantAdminMixin


# Register `JournalEntry` model
@admin.register(JournalEntry)
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
        journalline_qs = JournalLine.objects.select_related("account")
        return qs.select_related("company", "created_by").prefetch_related(
            Prefetch("lines", queryset=journalline_qs, to_attr="prefetched_lines")
        )
    """ For each JournalEntry, prefetch all its JournalLines, and
        within those lines also prefetch their linked Account objects.
        Use Prefetch with a select_related on the child queryset (reduces queries when accessing line.account).
    """

    """ Computed column for balance check """
    # Show total debits / total credits for each journal
    def balanced(self, obj):
        # check if entries balance with `compute_totals()` (model method)
        d, c = obj.compute_totals()
        # format: bold debits / small credits
        return format_html(
            "<b>{}</b> / <small>{}</small>",
            d or Decimal("0.00"),
            c or Decimal("0.00")
        )

    # set column header in admin
    balanced.short_description = "Debits / Credits"

    """ Make entries immutable once posted """

    def get_readonly_fields(self, request, obj=None):
        r = list(self.readonly_fields)
        # if posted:
        if obj and obj.status == "posted":
            # make fields readonly
            r += [
                "company", "date", "reference",
                "description", "status", "period"
                ]
            # This prevents someone from sneaking in
            # and editing a finalized journal
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
        # Only superusers can still change them
        # (like an override for emergencies)


# Register `JournalLine` model
@admin.register(JournalLine)
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
