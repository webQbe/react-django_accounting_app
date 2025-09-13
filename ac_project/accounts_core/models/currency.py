from django.db import models        # ORM base classes to define database tables as Python classes

# ---------- Currency ----------
class Currency(models.Model): # Store a list of valid currencies
    """
    ISO currencies. Use currency.code FK in other tables instead of free-text.
    """
    # Set code as the primary key, so it uniquely identifies a currency
    code = models.CharField(max_length=3, primary_key=True)  # 'USD', 'EUR'
    # Human-readable name of the currency
    name = models.CharField(max_length=64)  # 'US Dollar'
    # Nullable display symbol ("$", "€", "¥")
    symbol = models.CharField(max_length=8, blank=True, null=True)  # '$'
    # Avoid mistakes like storing 12.345 for JPY (which has no sub-units)
    decimal_places = models.PositiveSmallIntegerField(default=2)

    def __str__(self):
        # Define how this model prints in Django admin
        return f"{self.code} ({self.symbol or ''})"

    class Meta:
        # Make admin display plural as “currencies” instead of default “currencys”
        verbose_name_plural = "currencies"