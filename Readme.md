# 🇮🇳 India Tax Regime Comparator

A small FastAPI service that compares **Old Regime vs New Regime** income tax liability for a salaried individual in India — HRA, home loan interest, Chapter VI-A deductions, slab tax, rebate, surcharge with marginal relief, cess, and reconciliation against TDS already paid.

Every formula in this repo was independently derived from the statutory slab/rate structure and **verified against hand-worked examples** — not copied from a tax calculator and trusted blindly. See [Verification](#-verification) below.

> **Rates used:** FY 2025-26 / Tax Year 2026-27 (Section 202 of the Income-tax Act, 2025 — the successor to the old Section 115BAC). Tax law changes yearly; always confirm current figures against [incometax.gov.in](https://www.incometax.gov.in) before relying on this for a real filing.

---

## ✨ What it does

- Computes tax under **both regimes** from the same income inputs, side by side
- Implements the **HRA exemption** formula (least of three limits)
- Applies **Chapter VI-A deductions** (80C, 80CCD(1B), 80D, 80E, 80G, 80TTA/80TTB) — old regime only
- Handles **self-occupied home loan interest** (Section 24(b), capped at ₹2,00,000) — old regime only
- Computes the **Section 87A-equivalent rebate** for both regimes, at their respective thresholds
- Implements **surcharge with marginal relief** — not just a flat surcharge rate, but the actual statutory cap that prevents a small income increase from causing a disproportionate tax jump
- **Reconciles the final liability against TDS/TCS/advance tax already paid**, returning `refund_due`, `balance_payable`, or `fully_settled`
- Flags (without silently guessing at) **Section 234B interest exposure** when advance tax paid falls short of 90% of the final liability

## 🚫 What it deliberately doesn't do

This is a **salaried-employee comparator**, not a full ITR engine. Out of scope, on purpose:

| Not covered | Why |
|---|---|
| Capital gains (Sections 111A/112/112A) | Taxed at special flat rates outside the slab system — needs its own computation layer |
| Business/professional income, presumptive taxation (44AD/44ADA) | Different head of income entirely |
| Let-out house property | Only self-occupied property (Sec 24(b)) is modelled |
| Section 80D sub-limits | Accepted as a single input — the caller applies the correct ₹25,000/₹50,000 sub-limit before passing it in |
| Exact Section 234B/234C interest amounts | Flagged as exposure, not computed, since the real figure depends on the actual payment date |

If you extend this to cover any of the above, a PR is welcome — just keep the same "state the assumption, verify with a test" discipline the rest of the repo follows.

---

## 🚀 Quick start

```bash
git clone <your-repo-url>
cd <your-repo-name>

python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install fastapi uvicorn

# Run the self-test first — it recomputes a hand-worked example and asserts the match
python tax_regime_comparator.py

# Then start the API
uvicorn tax_regime_comparator:app --reload
```

Open **http://127.0.0.1:8000/docs** for the interactive Swagger UI — you can try every endpoint from the browser without writing a client.

---

## 📡 API reference

### `POST /compare-tax-regimes`

Computes and compares tax under both regimes.

<details>
<summary><b>Example request</b> — a case where the <i>old</i> regime wins</summary>

```json
{
  "salary": {
    "gross_salary": 1800000,
    "basic_salary": 900000,
    "hra_received": 450000,
    "rent_paid_annual": 540000,
    "is_metro": true
  },
  "other_income": {
    "savings_interest": 10000,
    "other_sources_income": 40000,
    "home_loan_interest_self_occupied": 200000
  },
  "old_regime_deductions": {
    "section_80c": 150000,
    "section_80ccd_1b": 50000,
    "section_80d": 25000,
    "section_80e": 0,
    "section_80g": 0
  },
  "age_category": "below_60"
}
```
</details>

<details>
<summary><b>Example response</b></summary>

```json
{
  "old_regime": {
    "gross_total_income": 1150000.0,
    "total_deductions_applied": 235000.0,
    "taxable_income": 915000.0,
    "tax_before_rebate": 95500.0,
    "rebate": 0.0,
    "tax_after_rebate": 95500.0,
    "surcharge": 0.0,
    "marginal_relief": 0.0,
    "cess": 3820.0,
    "total_tax_liability": 99320.0
  },
  "new_regime": {
    "gross_total_income": 1775000.0,
    "total_deductions_applied": 0.0,
    "taxable_income": 1775000.0,
    "tax_before_rebate": 155000.0,
    "rebate": 0.0,
    "tax_after_rebate": 155000.0,
    "surcharge": 0.0,
    "marginal_relief": 0.0,
    "cess": 6200.0,
    "total_tax_liability": 161200.0
  },
  "recommended_regime": "old",
  "tax_saved_by_recommended_regime": 61880.0
}
```
</details>

### `POST /reconcile-taxes`

Runs the comparison, picks a regime (or lets you specify one), and reconciles against TDS/TCS/advance tax already paid.

```json
{
  "tax_calculation": { "...": "same shape as above" },
  "taxes_paid": {
    "tds_deducted": 90000,
    "tcs_collected": 0,
    "advance_tax_paid": 0,
    "self_assessment_tax_already_paid": 0
  },
  "use_regime": "old"
}
```

`use_regime` only accepts `"old"` or `"new"` (enforced by the API — an invalid value is rejected with a 422, not silently ignored). Omit it to auto-select whichever regime is cheaper.

### `GET /`

Health check.

---

## 🧪 Verification

Every calculation function is checked against a manually worked example, baked directly into the file's `__main__` block:

| Scenario | Expected | Verified |
|---|---|---|
| ₹9.6L salary, HRA + 80C/80D, Mumbai | Old regime ₹37,336 / New regime ₹0 | ✅ |
| Marginal relief at the ₹50L/₹51L surcharge boundary | Relief of ₹64,250 | ✅ |
| Reconciliation: ₹0 liability, ₹38,000 TDS | Refund of ₹38,000 | ✅ |
| High HRA + home loan interest + maxed 80C/80CCD(1B)/80D | Old regime saves ₹61,880 over new | ✅ |

Run `python tax_regime_comparator.py` any time — it re-asserts these on every run, so a future edit that breaks the math fails loudly instead of silently.

---

## 🗂 Project structure

```
.
├── tax_regime_comparator.py   # everything: models, calculation logic, API, self-tests
└── README.md
```

Deliberately a single file for now — the calculation logic (`compute_old_regime`, `compute_new_regime`, `slab_tax`, `compute_surcharge_with_marginal_relief`, `reconcile_against_taxes_paid`) is usable standalone, without the FastAPI layer, if you just want to `import` the functions into another project.

---

## ⚠️ Disclaimer

This is an educational tool, not tax advice. Rates, slabs, thresholds and rebate limits change with each year's Finance Act — verify against [incometax.gov.in](https://www.incometax.gov.in) or a practising Chartered Accountant before relying on any figure this produces for an actual filing.

## 📄 License

MIT — do whatever you like with it, no warranty implied (see disclaimer above, which applies doubly to anything tax-related).