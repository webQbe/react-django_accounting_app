from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from ..managers import TenantManager


# ---------- Tenant / Company ----------
class Company(models.Model):

    """Tenant / Organization"""
    # Store company’s full display name
    name = models.CharField(max_length=200)

    slug = models.SlugField(  # A URL-friendly identifier
        max_length=80, unique=True  # no two companies can have the same slug
    )

    # Reference Currency model with ForeignKey
    default_currency = models.ForeignKey(
        "Currency",
        # don’t allow deleting a currency that a company depends on
        on_delete=models.PROTECT,
        related_name="companies",
    )
    # Link to a user account (creator or admin of company)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,  # use user model project is configured with
        null=True,
        blank=True,  # optional field
        on_delete=models.SET_NULL,
        # if user is deleted, company record stays,
        # but owner is set to NULL.
    )

    # Store default currency
    """ Important since all journal entries and invoices
    need to know which currency they belong to """
    currency_code = models.CharField(max_length=10, default="USD")

    # Store timestamp when the record is first created
    created_at = models.DateTimeField(auto_now_add=True)

    # Meta options
    class Meta:
        verbose_name_plural = "companies"

    # String Representation
    def __str__(self):
        return self.name


# ---------- Custom User (optional) ----------
class User(
    AbstractUser
):  # Replace built-in user with custom user to add add extra fields
    # Inherits from Django’s AbstractUser, so it keeps all the usual fields
    """
    Before you run your very first migrate,
    add: 'AUTH_USER_MODEL = "accounts_core.User"' to settings.py
    to avoid migration conflicts
    """
    # A link to a Company (your tenant)
    default_company = models.ForeignKey(
        "Company",
        # Nullable, user might exist before being assigned company
        null=True,
        blank=True,
        on_delete=models.SET_NULL,  # If the company is deleted,
        # don’t delete the user,just clear their default company
        # From Company side, see which users have the company as default
        related_name="default_users",
    )

    # Optional contact number field, can be left empty in forms
    phone = models.CharField(max_length=32, blank=True)

    # Enforce tenant scoping
    objects = TenantManager()

    """
        User.objects.create_user → makes a normal user.
        User.objects.create_superuser → makes a superuser
        (used by python manage.py createsuperuser)
    """

    class Meta:
        # helpful in multi-tenant setups
        indexes = [models.Index(fields=["default_company"])]

    # Controls how user is displayed
    def __str__(self):
        # Try to return full name
        return (
            self.get_full_name() or self.username
        )  # Fall back to username if no name is set


# ---------- EntityMembership ----------
class EntityMembership(
    models.Model
):  # Bridge table (or a "join model") between User and Company

    # Limit roles to predefined values
    # Django admin / forms will show a dropdown with these choices
    ROLE_CHOICES = [
        # full control (e.g., the person who created the company)
        ("owner", "Owner"),
        # can manage settings & users
        ("admin", "Admin"),
        (
            "accountant",
            "Accountant",
        ),  # can post journals, invoices, but maybe not delete companies
        ("viewer", "Viewer"),  # read-only access
    ]

    # Link to custom User
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        # If user is deleted, their memberships go too
        on_delete=models.CASCADE,
        related_name="memberships",  # See all companies users belong to
    )

    # Links to a Company record
    company = models.ForeignKey(
        "Company", on_delete=models.CASCADE, related_name="memberships"
    )

    # Store user’s role in the company
    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default="viewer",  # Defaults to "viewer" (safe, read-only)
    )

    # Suspend someone’s access without deleting the record
    is_active = models.BooleanField(default=True)
    """
        Think of it like a switch that controls
        whether a membership is “turned on” without throwing it away.
            If is_active=True → the membership is valid.
                The user has access to that company with their role.
            If is_active=False → the membership still exists in the database,
                but you can treat it as “suspended” or “revoked.”
    """

    # Automatically record when membership was created
    created_at = models.DateTimeField(auto_now_add=True)

    # Enforce tenant scoping
    objects = TenantManager()

    class Meta:
        # one user can only have one membership per company
        # (prevents duplicates)
        constraints = [
            models.UniqueConstraint(
                fields=["user", "company"], name="uq_user_company_membership"
            ),
        ]

        # make lookups fast
        # (important since almost every query will filter by company)
        indexes = [
            models.Index(fields=["company", "user"]),
        ]

    def __str__(self):
        # Make debugging/admin easier
        return f"{self.user} @ {self.company} ({self.role})"

    def clean(self):
        """
        Ensure that if the user has a default_company set, the user actually
        has a membership for that company.  Allow the current (unsaved)
        EntityMembership being validated to satisfy that requirement.
        """
        if self.user and self.user.default_company:
            # Use PKs for comparisons to avoid instance vs int mismatch
            default_company_pk = getattr(self.user.default_company, "pk", None)

            # Collect existing membership company_ids for the user, 
            if self.pk:
                existing_company_ids = list(
                    # excluding this record if updating
                    self.user.memberships.exclude(pk=self.pk).values_list("company_id", flat=True)
                )
            else:
                existing_company_ids = list(
                    self.user.memberships.values_list("company_id", flat=True)
                )

            # The default company must be among existing memberships of the user.
            # If not, allow it if the current (unsaved) membership is for that company.
            current_company_pk = getattr(self.company, "pk", None)
            if default_company_pk not in existing_company_ids and default_company_pk != current_company_pk:
                raise ValidationError(
                    f"Default company {self.user.default_company} must be a user's membership."
                )

    def save(self, *args, **kwargs):
        self.full_clean()  # run validations before saving
        return super().save(*args, **kwargs)
