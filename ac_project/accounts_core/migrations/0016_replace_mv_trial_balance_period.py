from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("accounts_core", "0015_replace_update_mv_jl_agg_period"),
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
                md5(m.company_id::text || '-' || COALESCE(m.period_id::text, '') || '-' || m.account_id::text) AS id,
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
            reverse_sql="",
        ),
    ]

    """ Version 0016:
        - Adds deterministic PK:
            `md5(company_id || '-' || period_id || '-' || account_id) AS id`
    """
