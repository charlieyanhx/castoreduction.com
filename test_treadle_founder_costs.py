"""The fixed costs a founder stated, worked out in code so two runs read the same lines."""
from __future__ import annotations

from packs.market_research_next.analysis import founder_costs


def context(**facts):
    return {"prep": {"venture": {"customer_inputs": {"intake": {"facts": facts}}}}}


CAFE = context(
    monthly_cost_estimate=("About $28,000 a month: rent $7,500, two baristas and a manager about "
                           "$14,000, beans and milk $4,000, utilities and the rest $2,500."),
    fixed_costs="Rent $7,500 a month, salaries about $14,000 a month, equipment lease $600 a month.",
    unit_economics="A $6 latte costs about $1.40 in beans, milk and cup.")


def test_both_answers_merge_and_ingredients_stay_in_the_unit_cost():
    lines = founder_costs.fixed_lines(CAFE)
    assert sorted(line["amount"] for line in lines) == [600, 2500, 7500, 14000]
    assert sum(line["amount"] for line in lines) == 24600


def test_with_no_unit_cost_ingredients_are_a_monthly_cost():
    facts = context(monthly_cost_estimate="About $5,000 a month: rent $3,000, beans $2,000.")
    assert sum(line["amount"] for line in founder_costs.fixed_lines(facts)) == 5000


def test_a_total_with_no_breakdown_gives_no_lines():
    # Summing what is priced would leave out the two developers.
    facts = context(monthly_cost_estimate="About $9,000 a month: two developers and hosting.",
                    fixed_costs="Developer time and about $400 a month in subscriptions.")
    assert founder_costs.fixed_lines(facts) == []


def test_lines_joined_by_and_are_read_apart():
    facts = context(monthly_cost_estimate=("About $2,000 a month in total: rent is $5,000 and "
                                           "salaries are $10,000."))
    assert [(l["name"], l["amount"]) for l in founder_costs.fixed_lines(facts)] == [
        ("rent", 5000), ("salaries", 10000)]


def test_a_yearly_amount_is_not_a_monthly_line():
    facts = context(monthly_cost_estimate=("About $5,000 a month: rent $3,000, staff $2,000, "
                                           "insurance $1,200 a year."))
    assert sum(line["amount"] for line in founder_costs.fixed_lines(facts)) == 5000


def test_no_answer_gives_no_lines():
    assert founder_costs.fixed_lines(context()) == []
    assert founder_costs.fixed_lines({}) == []


def test_two_costs_of_the_same_amount_are_both_kept():
    facts = context(monthly_cost_estimate=("About $2,200 a month: rent $1,200, insurance $500, "
                                           "payroll $500."))
    assert sum(line["amount"] for line in founder_costs.fixed_lines(facts)) == 2200


def test_an_amount_in_thousands_is_read_in_thousands():
    facts = context(monthly_cost_estimate="About $2,300 a month: rent $2k, utilities $300.")
    assert sum(line["amount"] for line in founder_costs.fixed_lines(facts)) == 2300


def test_only_monthly_amounts_are_lines():
    facts = context(monthly_cost_estimate=("About $5,000 a month: rent $3,000, staff $2,000, "
                                           "fit-out $30,000 one time, cleaning $200 a week, "
                                           "total $5,000."))
    assert sum(line["amount"] for line in founder_costs.fixed_lines(facts)) == 5000
