from django.core.exceptions import \
    ValidationError  # Built-in way to raise validation errors
from django.db import \
    models  # ORM base classes to define database tables as Python classes

from ..managers import TenantManager
from .account import Account
from .entitymembership import Company


# ---------- Customer ----------
# Represents client who receives invoices (AR side)
class Customer(models.Model):
    # Multi-tenant: every customer belongs to a single company.
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    """ Example:
        Company A can have its own customers separate from Company B.
    """

    # The customer’s legal or trade name
    name = models.CharField(max_length=200)

    # Optional contact for billing/communication
    contact_email = models.EmailField(null=True, blank=True)

    # Standard credit terms
    payment_terms_days = models.IntegerField(default=30)
    """ Example: If terms = 30 → invoice due 30 days after issue. """

    # FK to the Accounts Receivable account in Chart of Accounts
    """ If set: when creating an invoice for this customer,
        the system automatically books AR lines to that account.
    """
    default_ar_account = models.ForeignKey(
        Account,
        null=True,
        blank=True,
        # If AR account is deleted/disabled,
        # customer record isn’t broken, it just loses its default AR link.
        on_delete=models.SET_NULL,
        # Make reverse lookups possible
        # i.e., which customers use this AR account as default
        related_name="customers_default_ar",
        help_text="Default AR account used for this customer",
    )

    # Enforce tenant scoping
    objects = TenantManager()

    class Meta:

        indexes = [
            models.Index(fields=["company", "name"]),
            models.Index(fields=["company", "default_ar_account"]),
        ]

        # Enforce uniqueness per tenant
        constraints = [
            models.UniqueConstraint(
                fields=["company", "name"], name="uq_company_customer_name"
            ),
        ]

    # Display customer name in admin/UI
    def __str__(self):
        return self.name

    def clean(self):
        ar = self.default_ar_account
        # Ensure AR account belongs to the same company
        if (
            ar
            and ar.company_id != self.company_id
        ):
            raise ValidationError(
                "Default AR account & customer must belong to the same company"
            )

        # Only control accounts can be set as default AR (Customer)

        if ar and not ar.is_control_account:
            raise ValidationError(
                "Default AR account must be a control account")

        return super().clean()

    def save(self, *args, **kwargs):
        self.full_clean()  # run validations before saving
        return super().save(*args, **kwargs)
