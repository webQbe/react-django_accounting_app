from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils.translation import gettext_lazy as _
from accounts_core.models import Company, EntityMembership, User
from .forms import UserAdminChangeForm, UserAdminCreationForm
from .mixins import TenantAdminMixin


# Register `Company` model in admin with this custom config
@admin.register(Company)
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


# Extend stock `DjangoUserAdmin`
@admin.register(User)  # Hook custom `User` model into Django Admin
# Inherit all good stuff from `DjangoUserAdmin`
class UserAdmin(DjangoUserAdmin):
    # Use custom forms you defined to create/edit views
    add_form = UserAdminCreationForm
    form = UserAdminChangeForm
    model = User

    # fields shown in list
    list_display = (
        "username", "email", "get_full_name", "is_staff", "default_company")
    list_filter = ("is_staff", "is_superuser", "is_active")
    search_fields = ("username", "email", "first_name", "last_name")
    ordering = ("username",)

    # Group fields logically on edit user page
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        (_("Personal info"), {"fields":
                              ("first_name", "last_name", "email", "phone")}),
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
    # Tenant scoping:
    # limit visible users to memberships of the request.user's companies
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

        """
            Return all users who have at least one membership
            in any of the companies that I (the logged-in user) belong to.
            Don't show duplicates
        """
        return qs.filter(
            memberships__company_id__in=allowed_company_ids).distinct()
        # In EntityMembership model → related_name="memberships"
        # So 'memberships__company_id__in' checks:
        # User → EntityMembership → company_id
        # Filter only keeps users with 'allowed_company_ids'
        # .distinct() prevents a user who belongs to multiple companies
        # appearing multiple times


# Register EntityMembership model
@admin.register(EntityMembership)
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

    # Permission checks
    # To modify memberships
    def has_change_permission(self, request, obj=None):
        # Skip superusers
        if request.user.is_superuser:
            return True

        # Get company IDs where current user has owner/admin role
        user_company_ids = set(
            request.user.memberships.filter(
                role__in=("owner", "admin")).values_list(
                "company_id", flat=True
            )
        )

        if obj is None:
            # obj is None → decides if user can see change list view
            # True if user has at least one company where they’re Owner/Admin
            return bool(
                user_company_ids
            )
        """
              If we’re checking the general change permission
              (no specific object), only allow access if user is an Owner/Admin
              in at least one company.
        """

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
        # non-superusers must be Owner/Admin
        # of at least one company to add new memberships
        return request.user.memberships.filter(
            role__in=("owner", "admin")).exists()
