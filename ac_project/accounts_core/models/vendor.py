from django.db import models        # ORM base classes to define database tables as Python classes
from .entitymembership import Company 
from ..managers import TenantManager
from django.core.exceptions import ValidationError  # Built-in way to raise validation errors
from .account import Account



class Vendor(models.Model):  # Mirrors Customer but for Accounts Payable (AP)
    
    company = models.ForeignKey(Company, on_delete=models.CASCADE) # Multi-tenant

    # Same fields as Customer, but now for suppliers/vendors
    name = models.CharField(max_length=200)
    contact_email = models.EmailField(null=True, blank=True)
    payment_terms_days = models.IntegerField(default=30)

    # FK to the Accounts Payable account in Chart of Accounts
    """ If set: when creating a Bill for this vendor, 
        the system books AP lines to this account. """
    default_ap_account = models.ForeignKey(
                                Account, 
                                null=True, blank=True, 
                                on_delete=models.SET_NULL,
                                related_name="vendors_default_ap", # Lets you see which vendors use a given AP account
                                help_text="Default AP account used for this vendor"
                            )

    # Enforce tenant scoping
    objects = TenantManager() 

    class Meta:
        indexes = [ 
                    models.Index(fields=["company", "name"]),
                    models.Index(fields=["company", "default_ap_account"]),
                ]

        # Vendor names must be unique per company
        constraints = [
          models.UniqueConstraint(fields=["company", "name"], 
                                  name="uq_company_vendor_name"),
        ]

    def __str__(self):
        return self.name

    
    def clean(self):
        # Ensure AR account belongs to the same company
        if self.default_ap_account and self.default_ap_account.company_id != self.company_id:
            raise ValidationError(
                "Default AP account must belong to the same company as the vendor."
            )

        # Only control accounts can be set as default AP (Vendor)
        if self.default_ap_account and not self.default_ap_account.is_control_account:
            raise ValidationError("Default AP account must be a control account") 
        return super().clean()

    def save(self, *args, **kwargs):
        self.full_clean()  # run validations before saving
        return super().save(*args, **kwargs)