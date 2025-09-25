from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("accounts_core", "0010_update_mv_jl_agg_period"),
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
                SUM(m.total_debit_original) AS period_debit_original,
                SUM(m.total_credit_original) AS period_credit_original,
                SUM(m.net_amount_original) AS period_balance_original,
                SUM(m.total_debit_local) AS period_debit_local,
                SUM(m.total_credit_local) AS period_credit_local,
                SUM(m.net_amount_local) AS period_balance_local
            FROM mv_jl_agg_period m
            GROUP BY m.company_id, m.period_id, m.account_id, m.account_code, m.account_name, m.account_type;

            CREATE UNIQUE INDEX ux_mv_trial_balance_period_company_period_account
                ON mv_trial_balance_period (company_id, period_id, account_id);
            """,
            # If you roll back this migration, Django will drop the view
            reverse_sql="DROP MATERIALIZED VIEW mv_trial_balance_period;",
        ),
    ]

    """ SQL schema definition:
         - Creates a new stored query (materialized view) called mv_trial_balance_period.
         - Uses the previously created base aggregation matview as the source.
            - m is an alias for shorthand.
         - Aggregates totals per account per company per period in original & local currencies:
         - Groups rows so that the sums are calculated per company, period, and account.
         - Index creation
            - Adds a unique index so each (company, period, account) combination appears only once.
            - Speeds up queries filtering by company, period, or account.
    """
