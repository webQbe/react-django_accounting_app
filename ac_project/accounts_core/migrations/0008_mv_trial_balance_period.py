from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('accounts_core', '0007_mv_jl_agg_period'),
    ]

    """ Trial Balance (period-based): 
        - This migration takes the detailed journal line aggregates (mv_jl_agg_period) and
          rolls them up into a trial balance per period.
        - This materialized view gives you trial balance totals per period,
         ready for reporting.
    """

    operations = [
        migrations.RunSQL(
            """
            CREATE MATERIALIZED VIEW mv_trial_balance_period AS
            SELECT
                m.company_id,
                m.period_id,
                m.account_id,
                m.account_code,
                m.account_name,
                m.account_type,
                SUM(m.total_debit) AS period_debit,
                SUM(m.total_credit) AS period_credit,
                SUM(m.net_amount) AS period_balance
            FROM mv_jl_agg_period m
            GROUP BY m.company_id, m.period_id, m.account_id, m.account_code, m.account_name, m.account_type;

            CREATE UNIQUE INDEX ux_mv_trial_balance_period_company_period_account
                ON mv_trial_balance_period (company_id, period_id, account_id);
            """,
            # If you roll back this migration, Django will drop the view
            reverse_sql="DROP MATERIALIZED VIEW mv_trial_balance_period;"
        ),

        """ SQL schema definition:
         - Creates a new stored query (materialized view) called mv_trial_balance_period.
         - Uses the previously created base aggregation matview as the source.
            - m is an alias for shorthand.
         - Aggregates totals per account per company per period:
            period_debit = sum of debits in that period
            period_credit = sum of credits
            period_balance = net amount
         - Groups rows so that the sums are calculated per company, period, and account.
         - Index creation
            - Adds a unique index so each (company, period, account) combination appears only once.
            - Speeds up queries filtering by company, period, or account.
        """
    ]