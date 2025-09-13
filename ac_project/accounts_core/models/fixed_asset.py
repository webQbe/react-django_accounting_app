from django.db import models   # ORM base classes to define database tables as Python classes
from django.core.exceptions import ValidationError  # Built-in way to raise validation errors
from decimal import Decimal         # Used for exact decimal arithmetic (money values, accounting entries)
from ..managers import TenantManager
from .entitymembership import Company
from .account import Account
from .vendor import Vendor


# ---------- Fixed Assets ----------
class FixedAsset(models.Model): # tracks long-term assets and handle depreciation over time
    # Each fixed asset belongs to a company (multi-tenant)
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    # An optional identifier for the asset
    asset_code = models.CharField(max_length=80, null=True, blank=True)
    # Human-readable name/description
    description = models.CharField(max_length=400) 
    # Date the asset was bought
    purchase_date = models.DateField(null=True, blank=True) 
    # Acquisition cost - stored as Decimal for precision in accounting
    purchase_cost = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    # GL account that represents the asset 
    account = models.ForeignKey( 
        Account, 
        on_delete=models.PROTECT,
        null=True, blank=True, 
        limit_choices_to={"ac_type": "Asset"},
        help_text="GL account where this asset is capitalized",
    )
    # who sold it to you
    vendor = models.ForeignKey(
        Vendor, 
        null=True, blank=True,
        on_delete=models.PROTECT,
        help_text="Vendor from whom this asset was purchased",
    )
    # lifecycle state 
    status = models.CharField(
        max_length=20,
        choices=[
            ("draft", "Draft"),
            ("capitalized", "Capitalized"),
            ("disposed", "Disposed"),
        ],
        default="draft",
    )
    # Estimated lifespan in years (for depreciation)
    useful_life_years = models.IntegerField(null=True, blank=True) 
    depreciation_method = models.CharField(
                            max_length=30, 
                            default="straight_line"
                                """ How depreciation is calculated:
                                    "straight_line" = equal expense every year.
                                    Other methods could include "declining_balance", "units_of_production". """
                            )
    
    # Track how much depreciation has already been recorded
    accumulated_depreciation = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    """ Example: if a $12,000 asset is depreciated $4,000 per year, after 2 years this field = $8,000. """
    
    # Enforce tenant scoping
    objects = TenantManager() 
    
    class Meta:
        # Index makes lookup faster by company, asset_code
        # (since assets are often tracked by code)
        indexes = [models.Index(fields=["company", "asset_code"])]

        constraints = [
            # Within one company, each fixed asset must be unique
            # Across companies, duplicates are allowed
            models.UniqueConstraint(
                                    fields=["company", "asset_code"], 
                                    name="uq_fa_company_asset_code"
                                )
        ] 

    def __str__(self):
        # Return description when you print an asset in Django shell/admin
        return self.description
    
    def clean(self):
        # Tenancy checks
        # Ensure account chosen belongs to the same company
        if self.account and self.account.company != self.company:
            raise ValidationError("Account must belong to the same company.")
        # Ensure vendor chosen belongs to the same company
        if self.vendor and self.vendor.company != self.company:
            raise ValidationError("Vendor must belong to the same company.")

        # Prevent assets from being marked as depreciable when their lifespan hasn’t been set
        # Without a positive number of years, depreciation makes no sense
        if self.depreciation_method and (not self.useful_life_years or self.useful_life_years <= 0):
            raise ValidationError("Useful life must be > 0 if depreciation method is set")
        
        # Purchase cost cannot be negative
        if self.purchase_cost < 0:
            raise ValidationError("Purchase cost must be >= 0")
        
    def save(self, *args, **kwargs):
        self.full_clean()  # run validations before saving
        return super().save(*args, **kwargs)

