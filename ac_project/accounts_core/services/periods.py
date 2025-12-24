from django.core.exceptions import ValidationError
from ..models.period import Period

""" 
    Posting date determines the period.
    Changing the date before posting should affect the period.
"""
def resolve_period(company, date):
    try:
        return Period.objects.get(
            company=company,
            start_date__lte=date,
            end_date__gte=date,
            is_closed=False,
        )
    except Period.DoesNotExist:
        raise ValidationError(
            f"No open accounting period for {date} in {company}"
        )
