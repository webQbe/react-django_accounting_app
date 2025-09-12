from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('accounts_core', '0012_update_mv_trial_balance_running'),
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
                CREATE MATERIALIZED VIEW mv_pl_period AS
                SELECT
                    m.company_id,
                    m.period_id,
                    SUM(CASE WHEN m.account_type IN ('Income') THEN m.net_amount_original ELSE 0 END) AS total_income_original,
                    SUM(CASE WHEN m.account_type IN ('Expense') THEN m.net_amount_original ELSE 0 END) AS total_expense_original,
                    SUM(CASE WHEN m.account_type IN ('Income') THEN m.net_amount_original ELSE 0 END)
                        - SUM(CASE WHEN m.account_type IN ('Expense') THEN m.net_amount_original ELSE 0 END) AS net_profit_original,
                    SUM(CASE WHEN m.account_type IN ('Income') THEN m.net_amount_local ELSE 0 END) AS total_income_local,
                    SUM(CASE WHEN m.account_type IN ('Expense') THEN m.net_amount_local ELSE 0 END) AS total_expense_local,
                    SUM(CASE WHEN m.account_type IN ('Income') THEN m.net_amount_local ELSE 0 END)
                        - SUM(CASE WHEN m.account_type IN ('Expense') THEN m.net_amount_local ELSE 0 END) AS net_profit_local
                FROM mv_jl_agg_period m
                WHERE m.account_type IN ('Income','Expense')
                GROUP BY m.company_id, m.period_id;

                CREATE UNIQUE INDEX ux_mv_pl_period_company_period
                    ON mv_pl_period (company_id, period_id);
                """,
                reverse_sql="DROP MATERIALIZED VIEW mv_pl_period;"
            ),     
        ]
    
    """  SQL schema definition:
            - Makes a materialized view called mv_pl_period to store the query result physically in the database.
            - Pull data FROM `mv_jl_agg_period`, base aggregation matview
              Fields:
                - m.company_id → identifies the company.
                - m.period_id → identifies the accounting period (e.g., Jan 2025).
            - Aggregates calculated for original & local currencies:
                - total_income → Add up all net amounts where account type is Income.
                - total_expense → Add up all net amounts where account type is Expense.
                - net_profit → Profit = Income - Expense.
            - 
            - The WHERE ensures we only include Income and Expense account types 
              (ignoring things like Assets or Liabilities).
            - Group the results per company & period.
            - UNIQUE INDEX enforces that each (entity_id, period_id) pair appears only once.
    """