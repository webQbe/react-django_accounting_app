from decimal import Decimal
from django.contrib import admin
from django.utils.html import format_html
from django.db.models import Prefetch
from .actions import post_journal_entries
from .inlines import JournalLineInline
from .mixins import TenantAdminMixin
from django.core.exceptions import PermissionDenied
from accounts_core.models import JournalEntry, JournalLine


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

    def get_inline_instances(self, request, obj=None):
        """
        Toggle JournalLineInline.show_change_link depending on parent JE state.
        show_change_link controls whether each inline row has a link to the object's
        change page in admin.
        """
        instances = super().get_inline_instances(request, obj)
        for inline in instances:
            if isinstance(inline, JournalLineInline):
                # show a link for posted journals so user can *view* the posted JournalLine
                inline.show_change_link = bool(obj and getattr(obj, "status", None) == "posted")
        return instances
    
    # Fetch everything in one SQL join
    def get_queryset(self, request):
        """ For each JournalEntry, prefetch all its JournalLines, and
        within those lines also prefetch their linked Account objects.
        Use Prefetch with a select_related on the child queryset (reduces queries when accessing line.account).
        """
        qs = super().get_queryset(request)
        journalline_qs = JournalLine.objects.select_related("account")
        return qs.select_related("company", "created_by").prefetch_related(
            Prefetch("lines", queryset=journalline_qs, to_attr="prefetched_lines")
        )

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
    
    # if this JournalLine belongs to a posted JE, make all model fields readonly
    def get_readonly_fields(self, request, obj=None):
        if obj and getattr(obj, "journal", None) and getattr(obj.journal, "status", None) == "posted":
            # return every concrete field name so admin shows them read-only
            return [f.name for f in self.model._meta.concrete_fields]
        return super().get_readonly_fields(request, obj)

    # adding a standalone JournalLine via admin list view should be prevented
    # because lines should be created only via JournalEntry inline
    def has_add_permission(self, request):
        return False
    
    # if JournalLine object is attached to a posted journal, block change
    def has_change_permission(self, request, obj=None):
        if obj and getattr(obj, "journal", None) and obj.journal.status == "posted":
            return False
        return super().has_change_permission(request, obj)
    
    # prevent deletion of lines that belong to a posted journal
    def has_delete_permission(self, request, obj=None):
        if obj and getattr(obj, "journal", None) and obj.journal.status == "posted":
            return False
        return super().has_delete_permission(request, obj)
    
    # prevent saving/POST requests for posted journal lines (but allow GET so view is visible)
    def change_view(self, request, object_id, form_url='', extra_context=None):
        obj = self.get_object(request, object_id)
        if obj and getattr(obj, "journal", None) and obj.journal.status == "posted":
            if request.method == "POST":
                # Block any attempt to POST (update) the object.
                raise PermissionDenied("Cannot edit a JournalLine belonging to a posted JournalEntry.")
        return super().change_view(request, object_id, form_url, extra_context=extra_context)
