import pandas as pd
import json
import random
import math
from datetime import datetime, timedelta

random.seed(42)

CONTRACTS = {
    "Delhivery": {
        "provider": "Delhivery",
        "zones": {
            "A": {"upto_500g": 38, "500g_to_1kg": 42, "per_kg_above_1kg": 20},
            "B": {"upto_500g": 42, "500g_to_1kg": 47, "per_kg_above_1kg": 22},
            "C": {"upto_500g": 48, "500g_to_1kg": 54, "per_kg_above_1kg": 26},
            "D": {"upto_500g": 55, "500g_to_1kg": 62, "per_kg_above_1kg": 30},
            "E": {"upto_500g": 65, "500g_to_1kg": 74, "per_kg_above_1kg": 35}
        },
        "rto_rates": {"A": 30, "B": 35, "C": 40, "D": 45, "E": 55},
        "cod_fee_percent": 1.5, "cod_fee_minimum": 25,
        "contracted_surcharges": ["fuel_surcharge", "docket_charge", "oda_charge"],
        "weight_slab_rounding": "ceil_500g", "weight_tolerance_kg": 0.05,
        "dimensional_weight_divisor": None
    },
    "BlueDart": {
        "provider": "BlueDart",
        "zones": {
            "A": {"upto_500g": 55, "500g_to_1kg": 65, "per_kg_above_1kg": 30},
            "B": {"upto_500g": 62, "500g_to_1kg": 74, "per_kg_above_1kg": 34},
            "C": {"upto_500g": 72, "500g_to_1kg": 86, "per_kg_above_1kg": 40},
            "D": {"upto_500g": 85, "500g_to_1kg": 102, "per_kg_above_1kg": 48},
            "E": {"upto_500g": 100, "500g_to_1kg": 120, "per_kg_above_1kg": 56}
        },
        "rto_rates": {"A": 45, "B": 52, "C": 62, "D": 73, "E": 86},
        "cod_fee_percent": 1.75, "cod_fee_minimum": 30,
        "contracted_surcharges": ["fuel_surcharge", "docket_charge"],
        "weight_slab_rounding": "ceil_500g", "weight_tolerance_kg": 0.1,
        "dimensional_weight_divisor": 5000
    },
    "Ecom Express": {
        "provider": "Ecom Express",
        "zones": {
            "A": {"upto_500g": 35, "500g_to_1kg": 39, "per_kg_above_1kg": 18},
            "B": {"upto_500g": 39, "500g_to_1kg": 44, "per_kg_above_1kg": 20},
            "C": {"upto_500g": 44, "500g_to_1kg": 50, "per_kg_above_1kg": 24},
            "D": {"upto_500g": 52, "500g_to_1kg": 59, "per_kg_above_1kg": 28},
            "E": {"upto_500g": 62, "500g_to_1kg": 71, "per_kg_above_1kg": 33}
        },
        "rto_rates": {"A": 28, "B": 32, "C": 37, "D": 43, "E": 52},
        "cod_fee_percent": 1.25, "cod_fee_minimum": 22,
        "contracted_surcharges": ["fuel_surcharge", "docket_charge", "oda_charge"],
        "weight_slab_rounding": "ceil_500g", "weight_tolerance_kg": 0.05,
        "dimensional_weight_divisor": None
    },
    "Shadowfax": {
        "provider": "Shadowfax",
        "zones": {
            "A": {"upto_500g": 32, "500g_to_1kg": 36, "per_kg_above_1kg": 16},
            "B": {"upto_500g": 36, "500g_to_1kg": 41, "per_kg_above_1kg": 18},
            "C": {"upto_500g": 42, "500g_to_1kg": 48, "per_kg_above_1kg": 22},
            "D": {"upto_500g": 50, "500g_to_1kg": 57, "per_kg_above_1kg": 26},
            "E": {"upto_500g": 60, "500g_to_1kg": 69, "per_kg_above_1kg": 31}
        },
        "rto_rates": {"A": 26, "B": 30, "C": 35, "D": 40, "E": 48},
        "cod_fee_percent": 1.0, "cod_fee_minimum": 20,
        "contracted_surcharges": ["fuel_surcharge", "docket_charge"],
        "weight_slab_rounding": "ceil_500g", "weight_tolerance_kg": 0.05,
        "dimensional_weight_divisor": None
    }
}

# Save contracts
for provider, contract in CONTRACTS.items():
    fname = provider.lower().replace(" ", "_") + "_contract.json"
    with open(fname, "w") as f:
        json.dump(contract, f, indent=2)
    print(f"Created {fname}")

PINCODE_PAIRS = [
    ("400001", "110001", "B"),  # Mumbai → Delhi
    ("400001", "560001", "B"),  # Mumbai → Bangalore
    ("110001", "400001", "B"),  # Delhi → Mumbai
    ("110001", "302001", "A"),  # Delhi → Jaipur
    ("560001", "600001", "B"),  # Bangalore → Chennai
    ("700001", "500001", "C"),  # Kolkata → Hyderabad
    ("380001", "400001", "A"),  # Ahmedabad → Mumbai
    ("411001", "400001", "A"),  # Pune → Mumbai
]

ERRORS = {
    15: "weight_overcharge",
    30: "zone_mismatch",
    48: "rate_deviation",
    62: "duplicate_awb",
    63: "duplicate_awb",   # same AWB as row 62
    78: "cod_overcharge",
    92: "rto_overcharge",
    105: "non_contracted_surcharge",
    120: "cod_on_prepaid",
    140: "weight_overcharge",
    160: "zone_mismatch",
}


def calc_charge(zone, weight, contract):
    if contract["weight_slab_rounding"] == "ceil_500g":
        weight = math.ceil(weight * 2) / 2
    r = contract["zones"][zone]
    if weight <= 0.5:   return r["upto_500g"]
    elif weight <= 1.0: return r["500g_to_1kg"]
    else:               return r["500g_to_1kg"] + (weight - 1.0) * r["per_kg_above_1kg"]


def generate_invoice(provider_name, n=200):
    contract = CONTRACTS[provider_name]
    rows, manifest_rows = [], []
    base = datetime(2024, 3, 1)

    for i in range(1, n + 1):
        origin, dest, correct_zone = random.choice(PINCODE_PAIRS)
        actual_wt = round(random.choice([0.2, 0.3, 0.5, 0.75, 1.0, 1.2, 1.5, 2.0]), 2)
        cod = random.choice([0, 0, 0, 499, 799, 999, 1499])
        date = (base + timedelta(days=random.randint(0, 27))).strftime("%Y-%m-%d")
        L = random.choice([10, 15, 20, 25, 30])
        W = random.choice([8, 10, 15, 20])
        H = random.choice([5, 8, 10, 15])

        div = contract.get("dimensional_weight_divisor")
        dim_wt = round((L * W * H) / div, 2) if div else None
        bill_wt = max(actual_wt, dim_wt) if dim_wt else actual_wt

        awb = f"{provider_name[:3].upper()}2024{i:05d}"
        if i == 63: awb = f"{provider_name[:3].upper()}2024{62:05d}"  # deliberate duplicate

        correct_fwd = round(calc_charge(correct_zone, bill_wt, contract), 2)
        correct_rto = 0
        correct_cod_fee = round(max(contract["cod_fee_minimum"], cod * contract["cod_fee_percent"] / 100), 2) if cod > 0 else 0
        handling = 0

        billed_wt = bill_wt
        billed_zone = correct_zone
        billed_fwd = correct_fwd
        billed_rto = correct_rto
        billed_cod = cod
        billed_cod_fee = correct_cod_fee

        err = ERRORS.get(i)
        if err == "weight_overcharge":
            billed_wt = bill_wt + 0.8
            billed_fwd = round(calc_charge(correct_zone, billed_wt, contract), 2)
        elif err == "zone_mismatch":
            billed_zone = "E" if correct_zone != "E" else "D"
            billed_fwd = round(calc_charge(billed_zone, billed_wt, contract), 2)
        elif err == "rate_deviation":
            billed_fwd = round(correct_fwd * 1.9, 2)
        elif err == "cod_overcharge":
            billed_cod_fee = round(cod * 0.03, 2) if cod > 0 else 60
        elif err == "rto_overcharge":
            billed_rto = contract["rto_rates"][correct_zone] * 2.5
        elif err == "non_contracted_surcharge":
            handling = 50
        elif err == "cod_on_prepaid":
            billed_cod = 0
            billed_cod_fee = 35

        total = round(billed_fwd + billed_rto + billed_cod_fee + handling + 15, 2)

        rows.append({
            "AWB_Number": awb, "Date": date,
            "Origin_Pincode": origin, "Dest_Pincode": dest,
            "Billed_Weight_Kg": billed_wt, "Zone": billed_zone,
            "Forward_Charge": billed_fwd, "RTO_Charge": billed_rto,
            "COD_Amount": billed_cod, "COD_Fee": billed_cod_fee,
            "Fuel_Surcharge": 0, "ODA_Charge": 0,
            "Docket_Charge": 15, "Handling_Charge": handling, "Total": total
        })
        manifest_rows.append({
            "AWB_Number": awb, "Date": date,
            "Actual_Weight_Kg": actual_wt,
            "Length_cm": L, "Width_cm": W, "Height_cm": H,
            "Dim_Weight_Kg": round((L * W * H) / 5000, 2)
        })

    fname = provider_name.lower().replace(" ", "_")
    pd.DataFrame(rows).to_csv(f"{fname}_invoice.csv", index=False)
    pd.DataFrame(manifest_rows).to_csv(f"{fname}_manifest.csv", index=False)
    print(f"Created {fname}_invoice.csv + {fname}_manifest.csv ({n} rows, {len(ERRORS)} embedded errors)")


for provider in CONTRACTS:
    generate_invoice(provider)

print("\nAll done! Files created:")
print("  Contracts: delhivery_contract.json, bluedart_contract.json, ecom_express_contract.json, shadowfax_contract.json")
print("  Invoices:  delhivery_invoice.csv, bluedart_invoice.csv, ecom_express_invoice.csv, shadowfax_invoice.csv")
print("  Manifests: delhivery_manifest.csv, bluedart_manifest.csv, ecom_express_manifest.csv, shadowfax_manifest.csv")