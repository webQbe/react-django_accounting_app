from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("accounts_core", "0017_replace_update_mv_trial_balance_running"),
    ]

    """ Profit & Loss (period-based):
    This materialized view is a Profit & Loss Statement per period, per entity:
       - It shows total income, total expenses, and net profit.
       - It's stored for speed.
       - The unique index makes sure you don't have duplicate rows for the same company & period.
    """

    operations = [
        migrations.RunSQL(
            """
                DROP MATERIALIZED VIEW IF EXISTS mv_pl_period CASCADE;
                CREATE MATERIALIZED VIEW mv_pl_period AS
                WITH jl_norm AS (
                    SELECT
                        md5(jl.company_id::text || '-' || COALESCE(je.period_id::text, '')) AS agg_id,
                        jl.company_id,
                        je.period_id,
                        COALESCE(jl.fx_rate, 1.0) AS fxr,
                        jl.debit_original,
                        jl.credit_original,
                        a.ac_type
                    FROM accounts_core_journalline jl
                    JOIN accounts_core_journalentry je ON je.id = jl.journal_id
                    JOIN accounts_core_account a ON a.id = jl.account_id
                    WHERE je.status = 'posted'
                )
                SELECT
                    jl_norm.agg_id AS id,
                    jl_norm.company_id,
                    jl_norm.period_id,

                    /* original currency: treat `income`/`revenue` as credits, `expense`/`cost` as debits */
                    SUM(CASE WHEN jl_norm.ac_type IN ('income','revenue') THEN jl_norm.credit_original ELSE 0 END) AS total_income_original,
                    SUM(CASE WHEN jl_norm.ac_type IN ('expense','cost') THEN jl_norm.debit_original ELSE 0 END) AS total_expense_original,
                    ( SUM(CASE WHEN jl_norm.ac_type IN ('income','revenue') THEN jl_norm.credit_original ELSE 0 END)
                        - SUM(CASE WHEN jl_norm.ac_type IN ('expense','cost') THEN jl_norm.debit_original ELSE 0 END)
                    ) AS net_profit_original,

                    /* local currency (apply fx rate) */
                    SUM(CASE WHEN jl_norm.ac_type IN ('income','revenue') THEN jl_norm.credit_original * jl_norm.fxr ELSE 0 END) AS total_income_local,
                    SUM(CASE WHEN jl_norm.ac_type IN ('expense','cost') THEN jl_norm.debit_original * jl_norm.fxr ELSE 0 END) AS total_expense_local,
                    (
                        SUM(CASE WHEN jl_norm.ac_type IN ('income','revenue') THEN jl_norm.credit_original * jl_norm.fxr ELSE 0 END)
                        - SUM(CASE WHEN jl_norm.ac_type IN ('expense','cost') THEN jl_norm.debit_original * jl_norm.fxr ELSE 0 END)
                    ) AS net_profit_local

                FROM jl_norm
                GROUP BY jl_norm.agg_id, jl_norm.company_id, jl_norm.period_id;

                CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_pl_period_company_period
                    ON mv_pl_period (company_id, period_id);
                """,
            reverse_sql="DROP MATERIALIZED VIEW mv_pl_period;",
        ),
    ]

    """  Version 0018: Direct-from-journal-lines rewrite:
        - No dependency on mv_jl_agg_period
        - Synthetic id added
    """
