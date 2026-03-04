import pandas as pd
import json
import random
from datetime import datetime, timedelta

# --- CONTRACT (save as delhivery_contract.json) ---
contract = {
    "provider": "Delhivery",
    "zones": {
        "A": {"upto_500g": 38, "500g_to_1kg": 42, "per_kg_above_1kg": 20},
        "B": {"upto_500g": 42, "500g_to_1kg": 47, "per_kg_above_1kg": 22},
        "C": {"upto_500g": 48, "500g_to_1kg": 54, "per_kg_above_1kg": 26},
        "D": {"upto_500g": 55, "500g_to_1kg": 62, "per_kg_above_1kg": 30},
        "E": {"upto_500g": 65, "500g_to_1kg": 74, "per_kg_above_1kg": 35}
    },
    "rto_rates": {"A": 30, "B": 35, "C": 40, "D": 45, "E": 55},
    "cod_fee_percent": 1.5,
    "cod_fee_minimum": 25,
    "contracted_surcharges": ["fuel_surcharge", "docket_charge", "oda_charge"],
    "weight_tolerance_kg": 0.05
}

with open("delhivery_contract.json", "w") as f:
    json.dump(contract, f, indent=2)
print("✅ Created delhivery_contract.json")

# --- INVOICE (save as delhivery_invoice.csv) ---
def calc_correct_charge(zone, weight, contract):
    rates = contract["zones"][zone]
    if weight <= 0.5:
        return rates["upto_500g"]
    elif weight <= 1.0:
        return rates["500g_to_1kg"]
    else:
        extra_kg = weight - 1.0
        return rates["500g_to_1kg"] + (extra_kg * rates["per_kg_above_1kg"])

random.seed(42)
base_date = datetime(2024, 3, 1)
zones = ["A", "B", "C", "D", "E"]
rows = []

# Errors to embed at specific row indices
ERRORS = {
    12: "weight_overcharge",
    28: "zone_mismatch",
    45: "rate_deviation",
    67: "duplicate_awb",
    68: "duplicate_awb",   # same AWB as 67
    82: "cod_overcharge",
    95: "rto_overcharge",
    110: "non_contracted_surcharge",
    130: "weight_overcharge",
    155: "zone_mismatch",
}

for i in range(1, 201):
    zone = random.choice(zones)
    weight = round(random.choice([0.3, 0.5, 0.75, 1.0, 1.5, 2.0, 2.5]), 2)
    cod_amount = random.choice([0, 0, 0, 499, 799, 999, 1499])
    date = (base_date + timedelta(days=random.randint(0, 27))).strftime("%Y-%m-%d")
    awb = f"DEL2024{i:05d}" if i not in [68] else "DEL2024000{:02d}".format(67)

    correct_fwd = round(calc_correct_charge(zone, weight, contract), 2)
    correct_rto = 0
    correct_cod = round(max(25, cod_amount * 0.015), 2) if cod_amount > 0 else 0
    handling = 0

    billed_weight = weight
    billed_zone = zone
    billed_fwd = correct_fwd
    billed_rto = correct_rto
    billed_cod = correct_cod

    err = ERRORS.get(i)
    if err == "weight_overcharge":
        billed_weight = weight + 0.7
        billed_fwd = round(calc_correct_charge(zone, billed_weight, contract), 2)
    elif err == "zone_mismatch":
        billed_zone = "E" if zone != "E" else "D"
        billed_fwd = round(calc_correct_charge(billed_zone, weight, contract), 2)
    elif err == "rate_deviation":
        billed_fwd = round(correct_fwd * 1.85, 2)
    elif err == "cod_overcharge":
        billed_cod = round(cod_amount * 0.025, 2) if cod_amount > 0 else 45
    elif err == "rto_overcharge":
        billed_rto = contract["rto_rates"][zone] * 2.5
    elif err == "non_contracted_surcharge":
        handling = 45

    docket = 15
    total = round(billed_fwd + billed_rto + billed_cod + handling + docket, 2)

    rows.append({
        "AWB_Number": awb,
        "Date": date,
        "Origin_Pincode": random.choice(["400001","110001","500001","600001"]),
        "Dest_Pincode": random.choice(["560001","380001","700001","302001","411001"]),
        "Billed_Weight_Kg": billed_weight,
        "Zone": billed_zone,
        "Forward_Charge": billed_fwd,
        "RTO_Charge": billed_rto,
        "COD_Amount": cod_amount,
        "COD_Fee": billed_cod,
        "Fuel_Surcharge": 0,
        "ODA_Charge": 0,
        "Docket_Charge": docket,
        "Handling_Charge": handling,
        "Total": total,
        "_actual_zone": zone,
        "_actual_weight": weight,
        "_correct_fwd": correct_fwd,
    })

df = pd.DataFrame(rows)
# Save public version (without hidden columns)
public_cols = [c for c in df.columns if not c.startswith("_")]
df[public_cols].to_csv("delhivery_invoice.csv", index=False)
print(f"✅ Created delhivery_invoice.csv — {len(df)} rows, {len(ERRORS)} embedded errors")
print(f"   Errors at rows: {sorted(ERRORS.keys())}")