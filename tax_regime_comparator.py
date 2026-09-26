"""
FastAPI service: compare Old Regime vs New Regime income tax liability
for a salaried individual in India.

Rates used: FY 2025-26 / Tax Year 2026-27 (Section 202, Income-tax Act
2025 / erstwhile Section 115BAC for the new regime). These are unchanged
by the Finance Bill 2026 as of this writing -- ALWAYS verify current
slabs/rebate/surcharge/cess against incometax.gov.in before relying on
this for an actual filing.

SCOPE (documented, not silently ignored):
  - Covers: salary income, HRA exemption (old regime), self-occupied
    home loan interest (old regime, Sec 24b), other-source income
    (bank/FD interest, dividends etc.), core Chapter VI-A deductions
    (old regime), standard deduction (both regimes), slab tax, the
    Section 87A-equivalent rebate, surcharge with marginal relief, and
    4% Health & Education Cess. Also reconciles the computed liability
    against TDS/TCS/advance tax already paid, to determine refund due
    or balance payable.
  - Does NOT cover: capital gains (special rates under Sections 111A/
    112/112A), business/professional income, presumptive taxation,
    or let-out house property. These need separate computation and
    are out of scope for this salaried-employee comparator.
  - Section 80D is accepted as a single input; the caller is
    responsible for having already applied the correct sub-limit
    (Rs 25,000 self/family, Rs 50,000 if parents insured are senior
    citizens) before passing it in.
  - Section 80TTA/80TTB is applied only to the `savings_interest`
    field. For senior citizens (80TTB covers interest on deposits
    generally, up to Rs 50,000), include all relevant deposit
    interest in `savings_interest`, not `other_sources_income`.

Run locally:
    pip install fastapi uvicorn
    uvicorn tax_regime_comparator:app --reload
Then POST to http://127.0.0.1:8000/compare-tax-regimes
"""

from enum import Enum
from typing import List, Literal, Optional, Tuple

from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="India Old vs New Tax Regime Comparator")

# --------------------------------------------------------------------------
# Constants -- FY 2025-26 / Tax Year 2026-27
# --------------------------------------------------------------------------

CESS_RATE = 0.04

# (upper bound of slab, rate) -- cumulative, applied via slab_tax()
NEW_REGIME_SLABS: List[Tuple[float, float]] = [
    (400_000, 0.00),
    (800_000, 0.05),
    (1_200_000, 0.10),
    (1_600_000, 0.15),
    (2_000_000, 0.20),
    (2_400_000, 0.25),
    (float("inf"), 0.30),
]

OLD_REGIME_STD_DEDUCTION = 50_000
NEW_REGIME_STD_DEDUCTION = 75_000

NEW_REGIME_REBATE_THRESHOLD = 1_200_000
NEW_REGIME_REBATE_MAX = 60_000

OLD_REGIME_REBATE_THRESHOLD = 500_000
OLD_REGIME_REBATE_MAX = 12_500

SECTION_80C_CAP = 150_000
SECTION_80CCD_1B_CAP = 50_000
SELF_OCCUPIED_HOME_LOAN_INTEREST_CAP = 200_000  # Section 24(b), old regime only

# Surcharge brackets: (income strictly greater than this, rate that then applies)
# New regime caps at 25% (no 37% slab); old regime goes up to 37%.
SURCHARGE_BRACKETS_OLD: List[Tuple[float, float]] = [
    (5_000_000, 0.10),    # > Rs 50 lakh, up to Rs 1 crore
    (10_000_000, 0.15),   # > Rs 1 crore, up to Rs 2 crore
    (20_000_000, 0.25),   # > Rs 2 crore, up to Rs 5 crore
    (50_000_000, 0.37),   # > Rs 5 crore
]
SURCHARGE_BRACKETS_NEW: List[Tuple[float, float]] = [
    (5_000_000, 0.10),
    (10_000_000, 0.15),
    (20_000_000, 0.25),   # stays at 25% beyond Rs 2 crore under the new regime
]


class AgeCategory(str, Enum):
    below_60 = "below_60"
    senior_60_to_80 = "senior_60_to_80"
    super_senior_above_80 = "super_senior_above_80"


def get_old_regime_slabs(age_category: AgeCategory) -> List[Tuple[float, float]]:
    """Old regime basic exemption limit varies by age; slab rates above it do not."""
    if age_category == AgeCategory.super_senior_above_80:
        return [(500_000, 0.00), (1_000_000, 0.20), (float("inf"), 0.30)]
    if age_category == AgeCategory.senior_60_to_80:
        return [(300_000, 0.00), (500_000, 0.05), (1_000_000, 0.20), (float("inf"), 0.30)]
    return [(250_000, 0.00), (500_000, 0.05), (1_000_000, 0.20), (float("inf"), 0.30)]


# --------------------------------------------------------------------------
# Request / response models
# --------------------------------------------------------------------------

class SalaryDetails(BaseModel):
    gross_salary: float = Field(..., ge=0, description="Total gross salary for the year")
    basic_salary: float = Field(..., ge=0, description="Basic + DA component, needed for HRA calc")
    hra_received: float = Field(0, ge=0, description="HRA component actually received")
    rent_paid_annual: float = Field(0, ge=0, description="Actual rent paid for the year")
    is_metro: bool = Field(False, description="True if residing in Delhi/Mumbai/Kolkata/Chennai")


class OtherIncome(BaseModel):
    savings_interest: float = Field(0, ge=0, description="Savings a/c interest (and, for seniors, other deposit interest -- see module docstring)")
    other_sources_income: float = Field(0, ge=0, description="FD interest, dividends, and any other 'other sources' income")
    home_loan_interest_self_occupied: float = Field(0, ge=0, description="Interest on home loan for a SELF-OCCUPIED property")


class OldRegimeDeductions(BaseModel):
    section_80c: float = Field(0, ge=0, description="PPF/ELSS/life insurance/principal repayment etc., pre-cap")
    section_80ccd_1b: float = Field(0, ge=0, description="Additional voluntary NPS contribution, pre-cap")
    section_80d: float = Field(0, ge=0, description="Health insurance premium -- caller applies correct sub-limit")
    section_80e: float = Field(0, ge=0, description="Education loan interest, no statutory cap")
    section_80g: float = Field(0, ge=0, description="Eligible donations, caller applies correct % limit")


class TaxCalculationRequest(BaseModel):
    salary: SalaryDetails
    other_income: OtherIncome = OtherIncome()
    old_regime_deductions: OldRegimeDeductions = OldRegimeDeductions()
    age_category: AgeCategory = AgeCategory.below_60


class RegimeResult(BaseModel):
    gross_total_income: float
    total_deductions_applied: float
    taxable_income: float
    tax_before_rebate: float
    rebate: float
    tax_after_rebate: float
    surcharge: float
    marginal_relief: float
    cess: float
    total_tax_liability: float


class ComparisonResponse(BaseModel):
    old_regime: RegimeResult
    new_regime: RegimeResult
    recommended_regime: str
    tax_saved_by_recommended_regime: float


# --------------------------------------------------------------------------
# Core calculation functions (usable standalone, outside the API too)
# --------------------------------------------------------------------------

def compute_hra_exemption(basic_salary: float, hra_received: float,
                           rent_paid_annual: float, is_metro: bool) -> float:
    """Least of: HRA received / rent paid minus 10% of basic / 40-50% of basic."""
    if hra_received <= 0 or rent_paid_annual <= 0:
        return 0.0
    rent_less_threshold = max(rent_paid_annual - 0.10 * basic_salary, 0.0)
    city_limit = (0.50 if is_metro else 0.40) * basic_salary
    return max(min(hra_received, rent_less_threshold, city_limit), 0.0)


def slab_tax(taxable_income: float, slabs: List[Tuple[float, float]]) -> float:
    """Applies a cumulative slab schedule of (upper_bound, rate) pairs."""
    tax, lower_bound = 0.0, 0.0
    for upper_bound, rate in slabs:
        if taxable_income <= lower_bound:
            break
        tax += (min(taxable_income, upper_bound) - lower_bound) * rate
        lower_bound = upper_bound
    return round(tax, 2)


def compute_rebate(taxable_income: float, tax_before_rebate: float,
                    threshold: float, max_rebate: float) -> float:
    if taxable_income <= threshold:
        return round(min(tax_before_rebate, max_rebate), 2)
    return 0.0


def get_surcharge_rate(total_income: float,
                        brackets: List[Tuple[float, float]]) -> Tuple[float, float]:
    """Returns (applicable_rate, lower_bound_of_that_bracket); (0, 0) if none applies."""
    rate, threshold = 0.0, 0.0
    for lower_bound, slab_rate in brackets:
        if total_income > lower_bound:
            rate, threshold = slab_rate, lower_bound
    return rate, threshold


def compute_surcharge_with_marginal_relief(
    taxable_income: float,
    tax_after_rebate: float,
    brackets: List[Tuple[float, float]],
    slabs: List[Tuple[float, float]],
) -> Tuple[float, float]:
    """
    Returns (net_surcharge, marginal_relief).

    Marginal relief caps (tax + surcharge) at a level such that it never
    exceeds [tax + surcharge at the threshold income] + [income above the
    threshold] -- exactly the statutory mechanism (see report Part III).
    """
    rate, threshold = get_surcharge_rate(taxable_income, brackets)
    if rate == 0.0:
        return 0.0, 0.0

    surcharge = round(tax_after_rebate * rate, 2)

    tax_at_threshold = slab_tax(threshold, slabs)
    prior_rate, _ = get_surcharge_rate(threshold, brackets)
    surcharge_at_threshold = round(tax_at_threshold * prior_rate, 2)
    capped_total = (tax_at_threshold + surcharge_at_threshold) + (taxable_income - threshold)

    actual_total = tax_after_rebate + surcharge
    marginal_relief = round(max(actual_total - capped_total, 0.0), 2)
    return round(surcharge - marginal_relief, 2), marginal_relief


def _finalize(gross_total_income: float, total_deductions: float, taxable_income: float,
              slabs: List[Tuple[float, float]], rebate_threshold: float, rebate_max: float,
              surcharge_brackets: List[Tuple[float, float]]) -> RegimeResult:
    taxable_income = round(max(taxable_income, 0.0), 2)
    tax_before_rebate = slab_tax(taxable_income, slabs)
    rebate = compute_rebate(taxable_income, tax_before_rebate, rebate_threshold, rebate_max)
    tax_after_rebate = round(tax_before_rebate - rebate, 2)
    surcharge, marginal_relief = compute_surcharge_with_marginal_relief(
        taxable_income, tax_after_rebate, surcharge_brackets, slabs
    )
    cess = round((tax_after_rebate + surcharge) * CESS_RATE, 2)
    total_tax_liability = round(tax_after_rebate + surcharge + cess, 2)
    return RegimeResult(
        gross_total_income=round(gross_total_income, 2),
        total_deductions_applied=round(total_deductions, 2),
        taxable_income=taxable_income,
        tax_before_rebate=tax_before_rebate,
        rebate=rebate,
        tax_after_rebate=tax_after_rebate,
        surcharge=surcharge,
        marginal_relief=marginal_relief,
        cess=cess,
        total_tax_liability=total_tax_liability,
    )


def compute_old_regime(req: TaxCalculationRequest) -> RegimeResult:
    hra_exempt = compute_hra_exemption(
        req.salary.basic_salary, req.salary.hra_received,
        req.salary.rent_paid_annual, req.salary.is_metro,
    )
    taxable_salary = req.salary.gross_salary - hra_exempt - OLD_REGIME_STD_DEDUCTION

    home_loan_interest = min(
        req.other_income.home_loan_interest_self_occupied,
        SELF_OCCUPIED_HOME_LOAN_INTEREST_CAP,
    )
    house_property_income = -home_loan_interest  # a loss, since interest exceeds nil rent

    gross_total_income = (
        taxable_salary + house_property_income
        + req.other_income.other_sources_income + req.other_income.savings_interest
    )

    d = req.old_regime_deductions
    section_80c = min(d.section_80c, SECTION_80C_CAP)
    section_80ccd_1b = min(d.section_80ccd_1b, SECTION_80CCD_1B_CAP)
    is_senior = req.age_category != AgeCategory.below_60
    section_80tta_ttb = min(req.other_income.savings_interest, 50_000 if is_senior else 10_000)

    total_deductions = (
        section_80c + section_80ccd_1b + d.section_80d + d.section_80e
        + d.section_80g + section_80tta_ttb
    )

    taxable_income = gross_total_income - total_deductions
    slabs = get_old_regime_slabs(req.age_category)
    return _finalize(
        gross_total_income, total_deductions, taxable_income, slabs,
        OLD_REGIME_REBATE_THRESHOLD, OLD_REGIME_REBATE_MAX, SURCHARGE_BRACKETS_OLD,
    )


def compute_new_regime(req: TaxCalculationRequest) -> RegimeResult:
    # No HRA exemption, no self-occupied home loan interest deduction,
    # no Chapter VI-A deductions (barring employer NPS, not modelled here).
    taxable_salary = req.salary.gross_salary - NEW_REGIME_STD_DEDUCTION
    gross_total_income = (
        taxable_salary + req.other_income.other_sources_income + req.other_income.savings_interest
    )
    taxable_income = gross_total_income  # no deductions applied
    return _finalize(
        gross_total_income, 0.0, taxable_income, NEW_REGIME_SLABS,
        NEW_REGIME_REBATE_THRESHOLD, NEW_REGIME_REBATE_MAX, SURCHARGE_BRACKETS_NEW,
    )


# --------------------------------------------------------------------------
# Reconcile against TDS/TCS/advance tax and determine the outcome
# --------------------------------------------------------------------------

class TaxesPaid(BaseModel):
    tds_deducted: float = Field(0, ge=0, description="Total TDS deducted -- from Form 16 + Form 16A, cross-checked against Form 26AS")
    tcs_collected: float = Field(0, ge=0, description="Total TCS collected on your PAN, if any")
    advance_tax_paid: float = Field(0, ge=0, description="Advance tax paid in quarterly instalments during the year, if any")
    self_assessment_tax_already_paid: float = Field(0, ge=0, description="Any self-assessment tax already deposited before this reconciliation")


class ReconciliationResult(BaseModel):
    regime_used: str
    total_tax_liability: float
    total_taxes_paid: float
    outcome: str  # "refund_due" | "balance_payable" | "fully_settled"
    amount: float
    advance_tax_shortfall_flag: bool
    note: str


def reconcile_against_taxes_paid(
    regime_result: RegimeResult, taxes_paid: TaxesPaid, regime_label: str
) -> ReconciliationResult:
    """
    Compares the final computed tax liability against everything already
    paid (TDS + TCS + advance tax + any self-assessment tax already
    deposited) and determines whether a refund is due, a balance is
    payable, or the two exactly match.

    Also flags -- but does not itself compute the rupee amount of --
    Section 234B interest exposure: this applies whenever TDS + TCS +
    advance tax paid *during the year* falls short of 90% of the final
    liability. The actual interest depends on the date the shortfall is
    eventually paid, which this reconciliation step does not know.
    """
    total_paid = round(
        taxes_paid.tds_deducted + taxes_paid.tcs_collected
        + taxes_paid.advance_tax_paid + taxes_paid.self_assessment_tax_already_paid,
        2,
    )
    liability = regime_result.total_tax_liability
    difference = round(total_paid - liability, 2)

    if difference > 0:
        outcome, amount = "refund_due", difference
        note = "Excess tax paid will be refunded once the return is processed under Section 143(1)."
    elif difference < 0:
        outcome, amount = "balance_payable", abs(difference)
        note = "Pay the balance as self-assessment tax before filing to avoid Section 234A interest on late payment."
    else:
        outcome, amount = "fully_settled", 0.0
        note = "Taxes already paid exactly match the computed liability."

    pre_filing_payments = taxes_paid.tds_deducted + taxes_paid.tcs_collected + taxes_paid.advance_tax_paid
    advance_tax_shortfall_flag = liability > 10_000 and pre_filing_payments < 0.90 * liability

    if advance_tax_shortfall_flag:
        note += (
            " Note: TDS/TCS/advance tax paid during the year was below 90% of the final "
            "liability -- Section 234B interest (1% per month, from 1 April until paid) "
            "may apply on the shortfall. This function flags the exposure only; it does "
            "not compute the exact interest, which depends on the actual payment date."
        )

    return ReconciliationResult(
        regime_used=regime_label,
        total_tax_liability=liability,
        total_taxes_paid=total_paid,
        outcome=outcome,
        amount=round(amount, 2),
        advance_tax_shortfall_flag=advance_tax_shortfall_flag,
        note=note,
    )


class FullReconciliationRequest(BaseModel):
    tax_calculation: TaxCalculationRequest
    taxes_paid: TaxesPaid
    use_regime: Optional[Literal["old", "new"]] = Field(
        None, description="'old' or 'new' -- if omitted, uses whichever regime has the lower liability"
    )


class FullReconciliationResponse(BaseModel):
    comparison: ComparisonResponse
    reconciliation: ReconciliationResult


# --------------------------------------------------------------------------
# API endpoints
# --------------------------------------------------------------------------

@app.post("/compare-tax-regimes", response_model=ComparisonResponse)
def compare_tax_regimes(payload: TaxCalculationRequest) -> ComparisonResponse:
    old_result = compute_old_regime(payload)
    new_result = compute_new_regime(payload)

    if new_result.total_tax_liability <= old_result.total_tax_liability:
        recommended = "new"
    else:
        recommended = "old"

    saved = abs(old_result.total_tax_liability - new_result.total_tax_liability)
    return ComparisonResponse(
        old_regime=old_result,
        new_regime=new_result,
        recommended_regime=recommended,
        tax_saved_by_recommended_regime=round(saved, 2),
    )


@app.post("/reconcile-taxes", response_model=FullReconciliationResponse)
def reconcile_taxes(payload: FullReconciliationRequest) -> FullReconciliationResponse:
    """
    Runs the old-vs-new comparison, then reconciles the chosen regime's
    liability against taxes already paid (TDS/TCS/advance tax/self-
    assessment tax) to determine refund due or balance payable.
    """
    comparison = compare_tax_regimes(payload.tax_calculation)
    chosen_regime = payload.use_regime or comparison.recommended_regime
    regime_result = comparison.old_regime if chosen_regime == "old" else comparison.new_regime
    reconciliation = reconcile_against_taxes_paid(regime_result, payload.taxes_paid, chosen_regime)
    return FullReconciliationResponse(comparison=comparison, reconciliation=reconciliation)


@app.get("/")
def health_check() -> dict:
    return {"status": "ok", "service": "India Old vs New Tax Regime Comparator"}


# --------------------------------------------------------------------------
# Self-test reproducing the worked example (Ananya Verma, FY 2025-26):
# expected old regime tax = 37,336 ; new regime tax = 0
# --------------------------------------------------------------------------

if __name__ == "__main__":
    example = TaxCalculationRequest(
        salary=SalaryDetails(
            gross_salary=960_000, basic_salary=500_000, hra_received=240_000,
            rent_paid_annual=216_000, is_metro=True,
        ),
        other_income=OtherIncome(savings_interest=8_000, other_sources_income=45_000),
        old_regime_deductions=OldRegimeDeductions(section_80c=150_000, section_80d=22_000),
    )
    result = compare_tax_regimes(example)
    print(result.model_dump_json(indent=2))
    assert result.old_regime.total_tax_liability == 37_336.0, "Old regime mismatch"
    assert result.new_regime.total_tax_liability == 0.0, "New regime mismatch"
    print("\nSelf-test passed: matches the manually worked example.")

    # Reconciliation: employer deducted TDS of Rs 38,000 through the year
    # (based on the OLD regime, since that's what she declared to her
    # employer), but she elects the NEW regime at filing time. Expected:
    # new-regime liability = 0, so the full Rs 38,000 TDS becomes a refund.
    recon_request = FullReconciliationRequest(
        tax_calculation=example,
        taxes_paid=TaxesPaid(tds_deducted=38_000),
        use_regime="new",
    )
    recon_response = reconcile_taxes(recon_request)
    print(recon_response.reconciliation.model_dump_json(indent=2))
    assert recon_response.reconciliation.outcome == "refund_due"
    assert recon_response.reconciliation.amount == 38_000.0
    print("\nReconciliation self-test passed: Rs 38,000 refund due, as manually computed.")