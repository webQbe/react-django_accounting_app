from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import models
from ..managers import TenantManager
from .account import Account
from .entitymembership import Company


# ---------- Items (optional product/service) ----------
class Item(models.Model):  # Represents something a company sells & purchases

    # Multi-tenant: each item belongs to a company
    company = models.ForeignKey(
        Company,
        # If the company is deleted, its items are deleted too (CASCADE)
        on_delete=models.CASCADE,
    )
    # Stock Keeping Unit (optional unique code per item)
    # useful if the business only sells services.
    sku = models.CharField(
        max_length=80,
        null=True,
        blank=True,
    )

    # Required human-readable name of the item
    name = models.CharField(max_length=200)

    # FK → Revenue account (Chart of Accounts)
    """ Example: Item "Web Hosting" → posts to "4000: Sales Revenue". """
    # If an Account is set,
    # invoices for this item auto-post revenue lines to it.
    sales_account = models.ForeignKey(
        Account,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="items_sales_account",
    )

    # FK → Expense account for purchases/bills
    """ Example:
    "Printer Paper" → posts to "6000: Office Supplies Expense". """
    purchase_account = models.ForeignKey(
        Account,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,  # item stays without default account
        related_name="items_purchase_account",
    )

    # Current stock level of the item
    on_hand_quantity = models.DecimalField(
        max_digits=14,
        decimal_places=4,  # Allow precise tracking
        default=0,  # Default = 0
    )

    # store standard prices per product
    default_unit_price = models.DecimalField(
        max_digits=18, decimal_places=4, default=Decimal("0.00")
    )

    # Enforce tenant scoping
    objects = TenantManager()

    class Meta:
        # for fast lookups
        indexes = [models.Index(fields=["company", "name"])]

        # Ensure each SKU is unique within a company
        constraints = [
            models.UniqueConstraint(
                fields=["company", "sku"], name="uq_company_item_sku"
            )
        ]

    def __str__(self):
        return self.name

    """ Can’t create an Item for Company A
    but point it to an Account from Company B """

    def clean(self):
        sac = self.sales_account
        if sac and sac.company_id != self.company_id:
            raise ValidationError(
                "Sales account must belong to the same company as the item."
            )
        if (
            self.purchase_account
            and self.purchase_account.company_id != self.company_id
        ):
            raise ValidationError(
                "Purchase account must belong to the same company as the item."
            )

    def save(self, *args, **kwargs):
        self.full_clean()  # run validations before saving
        return super().save(*args, **kwargs)
