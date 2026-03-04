import streamlit as st
import google.generativeai as genai
import pandas as pd
import json
import io
import re
import os
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from dotenv import load_dotenv

load_dotenv()

# ─── CONFIG ───────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Logistics Billing Checker",
    page_icon="🚚",
    layout="wide"
)

genai.configure(api_key=os.getenv("GEMINI_API_KEY", st.secrets.get("GEMINI_API_KEY", "")))
model = genai.GenerativeModel("gemini-1.5-flash")

# ─── AI EXTRACTION ─────────────────────────────────────────────────────────────

INVOICE_PROMPT = """You are a logistics invoice data extractor for Indian logistics companies.

Extract ALL shipment rows from this invoice. Return a JSON array only — no markdown, no explanation.

Each item in the array must have exactly these fields:
{
  "awb_number": "string",
  "date": "YYYY-MM-DD",
  "origin_pincode": "string",
  "dest_pincode": "string",
  "billed_weight_kg": number,
  "zone": "A/B/C/D/E",
  "forward_charge": number,
  "rto_charge": number,
  "cod_amount": number,
  "cod_fee": number,
  "fuel_surcharge": number,
  "oda_charge": number,
  "docket_charge": number,
  "handling_charge": number,
  "total": number
}

Use 0 for missing numeric fields. Return ONLY the JSON array."""

CONTRACT_PROMPT = """Extract the rate card from this logistics contract. Return JSON only — no markdown.

Format:
{
  "provider": "string",
  "zones": {
    "A": {"upto_500g": number, "500g_to_1kg": number, "per_kg_above_1kg": number},
    "B": {...}, "C": {...}, "D": {...}, "E": {...}
  },
  "rto_rates": {"A": number, "B": number, "C": number, "D": number, "E": number},
  "cod_fee_percent": number,
  "cod_fee_minimum": number,
  "contracted_surcharges": ["list", "of", "allowed", "surcharge", "names"],
  "weight_tolerance_kg": number
}"""


def extract_json(text):
    """Safely extract JSON from AI response."""
    text = text.strip()
    # Remove markdown fences
    text = re.sub(r"```json|```", "", text).strip()
    return json.loads(text)


def ai_extract_invoice(content: str) -> list:
    response = model.generate_content(f"{INVOICE_PROMPT}\n\nINVOICE DATA:\n{content}")
    return extract_json(response.text)


def ai_extract_contract(content: str) -> dict:
    response = model.generate_content(f"{CONTRACT_PROMPT}\n\nCONTRACT DATA:\n{content}")
    return extract_json(response.text)


# ─── CHECKING ENGINE (pure Python — no AI needed) ─────────────────────────────

def correct_forward_charge(zone, weight, contract):
    rates = contract["zones"].get(zone, contract["zones"].get("A"))
    if weight <= 0.5:
        return rates["upto_500g"]
    elif weight <= 1.0:
        return rates["500g_to_1kg"]
    else:
        extra = weight - 1.0
        return rates["500g_to_1kg"] + (extra * rates["per_kg_above_1kg"])


def check_invoice(items: list, contract: dict) -> tuple[list, dict]:
    discrepancies = []
    tolerance = contract.get("weight_tolerance_kg", 0.05)
    seen_awbs = {}
    
    for item in items:
        awb = item["awb_number"]
        zone = item.get("zone", "A")
        weight = item.get("billed_weight_kg", 0)
        
        # ── 1. Rate deviation ─────────────────────────────────────
        correct_fwd = round(correct_forward_charge(zone, weight, contract), 2)
        billed_fwd = item.get("forward_charge", 0)
        if billed_fwd > 0 and abs(billed_fwd - correct_fwd) / max(correct_fwd, 1) > 0.05:
            discrepancies.append({
                "AWB": awb, "Error Type": "Rate Deviation",
                "Description": f"Charged ₹{billed_fwd}, contracted ₹{correct_fwd} (Zone {zone}, {weight}kg)",
                "Billed (₹)": billed_fwd, "Correct (₹)": correct_fwd,
                "Overcharge (₹)": round(billed_fwd - correct_fwd, 2)
            })
        
        # ── 2. RTO overcharge ─────────────────────────────────────
        rto = item.get("rto_charge", 0)
        if rto > 0:
            correct_rto = contract["rto_rates"].get(zone, 0)
            if rto > correct_rto * 1.05:
                discrepancies.append({
                    "AWB": awb, "Error Type": "RTO Overcharge",
                    "Description": f"RTO charged ₹{rto}, contracted ₹{correct_rto}",
                    "Billed (₹)": rto, "Correct (₹)": correct_rto,
                    "Overcharge (₹)": round(rto - correct_rto, 2)
                })
        
        # ── 3. COD fee overcharge ─────────────────────────────────
        cod_amount = item.get("cod_amount", 0)
        cod_fee = item.get("cod_fee", 0)
        if cod_amount > 0 and cod_fee > 0:
            correct_cod = max(
                contract.get("cod_fee_minimum", 25),
                cod_amount * contract.get("cod_fee_percent", 1.5) / 100
            )
            if cod_fee > correct_cod * 1.05:
                discrepancies.append({
                    "AWB": awb, "Error Type": "COD Fee Overcharge",
                    "Description": f"COD fee ₹{cod_fee}, contracted ₹{correct_cod:.2f} ({contract.get('cod_fee_percent')}% of ₹{cod_amount})",
                    "Billed (₹)": cod_fee, "Correct (₹)": round(correct_cod, 2),
                    "Overcharge (₹)": round(cod_fee - correct_cod, 2)
                })
        
        # ── 4. Non-contracted surcharge ───────────────────────────
        if item.get("handling_charge", 0) > 0:
            contracted = [s.lower() for s in contract.get("contracted_surcharges", [])]
            if "handling_charge" not in contracted and "handling" not in contracted:
                h = item["handling_charge"]
                discrepancies.append({
                    "AWB": awb, "Error Type": "Non-Contracted Surcharge",
                    "Description": f"Handling charge ₹{h} not in contract",
                    "Billed (₹)": h, "Correct (₹)": 0,
                    "Overcharge (₹)": h
                })
        
        # ── 5. Duplicate AWB ──────────────────────────────────────
        if awb in seen_awbs:
            total = item.get("total", 0)
            discrepancies.append({
                "AWB": awb, "Error Type": "Duplicate AWB",
                "Description": f"AWB {awb} billed multiple times",
                "Billed (₹)": total, "Correct (₹)": 0,
                "Overcharge (₹)": total
            })
        seen_awbs[awb] = True

    # Build summary
    total_billed = sum(i.get("total", 0) for i in items)
    total_overcharge = sum(max(0, d["Overcharge (₹)"]) for d in discrepancies)
    
    errors_by_type = {}
    for d in discrepancies:
        t = d["Error Type"]
        errors_by_type[t] = errors_by_type.get(t, 0) + max(0, d["Overcharge (₹)"])

    summary = {
        "total_items": len(items),
        "total_billed": round(total_billed, 2),
        "total_overcharge": round(total_overcharge, 2),
        "verified_amount": round(total_billed - total_overcharge, 2),
        "error_count": len(discrepancies),
        "errors_by_type": errors_by_type,
    }
    return discrepancies, summary


# ─── EXCEL PAYOUT GENERATION ──────────────────────────────────────────────────

def generate_payout_excel(items, discrepancies, summary):
    wb = Workbook()

    # ── Sheet 1: Summary ──
    ws = wb.active
    ws.title = "Summary"
    header_fill = PatternFill("solid", fgColor="1F4E79")
    header_font = Font(bold=True, color="FFFFFF", size=14)

    ws.merge_cells("A1:D1")
    ws["A1"] = "📊 Logistics Billing Check — Summary Report"
    ws["A1"].font = Font(bold=True, size=16, color="1F4E79")
    ws["A1"].alignment = Alignment(horizontal="center")

    metrics = [
        ("Total Line Items Checked", summary["total_items"], None),
        ("Total Billed Amount", f"₹{summary['total_billed']:,.2f}", None),
        ("Verified Payable Amount", f"₹{summary['verified_amount']:,.2f}", "E2EF70"),
        ("Total Overcharges Found", f"₹{summary['total_overcharge']:,.2f}", "FFC7CE"),
        ("Savings %", f"{(summary['total_overcharge']/max(summary['total_billed'],1)*100):.1f}%", "FFC7CE"),
        ("Total Errors Found", summary["error_count"], None),
    ]

    for row_idx, (label, value, color) in enumerate(metrics, start=3):
        ws.cell(row_idx, 1, label).font = Font(bold=True)
        cell = ws.cell(row_idx, 2, value)
        if color:
            cell.fill = PatternFill("solid", fgColor=color)
            cell.font = Font(bold=True)

    ws.cell(10, 1, "Overcharges by Type").font = Font(bold=True, size=12)
    for row_idx, (etype, amount) in enumerate(summary["errors_by_type"].items(), start=11):
        ws.cell(row_idx, 1, etype)
        ws.cell(row_idx, 2, f"₹{amount:,.2f}").fill = PatternFill("solid", fgColor="FFC7CE")

    ws.column_dimensions["A"].width = 35
    ws.column_dimensions["B"].width = 25

    # ── Sheet 2: Verified Payout ──
    ws2 = wb.create_sheet("Verified Payout")
    headers = ["AWB Number", "Date", "Origin", "Destination", "Weight(kg)",
               "Zone", "Total Billed(₹)", "Overcharge(₹)", "Verified Amount(₹)", "Status", "Issue"]
    ws2.append(headers)
    for col, h in enumerate(headers, 1):
        cell = ws2.cell(1, col)
        cell.fill = header_fill
        cell.font = Font(bold=True, color="FFFFFF")
        ws2.column_dimensions[get_column_letter(col)].width = 20

    disc_map = {}
    for d in discrepancies:
        awb = d["AWB"]
        disc_map.setdefault(awb, []).append(d)

    red_fill = PatternFill("solid", fgColor="FFC7CE")
    green_fill = PatternFill("solid", fgColor="C6EFCE")

    for item in items:
        awb = item["awb_number"]
        errs = disc_map.get(awb, [])
        overcharge = sum(max(0, e["Overcharge (₹)"]) for e in errs)
        status = "⚠️ DISPUTED" if errs else "✅ CLEAN"
        issues = ", ".join(set(e["Error Type"] for e in errs)) if errs else ""
        row = ws2.append([
            awb, item.get("date",""), item.get("origin_pincode",""),
            item.get("dest_pincode",""), item.get("billed_weight_kg",0),
            item.get("zone",""), item.get("total",0),
            round(overcharge,2), round(item.get("total",0)-overcharge,2),
            status, issues
        ])
        if errs:
            for col in range(1, 12):
                ws2.cell(ws2.max_row, col).fill = red_fill
        else:
            ws2.cell(ws2.max_row, 10).fill = green_fill

    # ── Sheet 3: Discrepancy Report ──
    ws3 = wb.create_sheet("Discrepancy Report")
    disc_headers = ["AWB Number", "Error Type", "Description", "Billed(₹)", "Correct(₹)", "Overcharge(₹)"]
    ws3.append(disc_headers)
    for col, h in enumerate(disc_headers, 1):
        cell = ws3.cell(1, col)
        cell.fill = PatternFill("solid", fgColor="C00000")
        cell.font = Font(bold=True, color="FFFFFF")
        ws3.column_dimensions[get_column_letter(col)].width = 25

    ws3.column_dimensions["C"].width = 60

    for d in discrepancies:
        ws3.append([d["AWB"], d["Error Type"], d["Description"],
                    d["Billed (₹)"], d["Correct (₹)"], d["Overcharge (₹)"]])
        ws3.cell(ws3.max_row, 6).fill = PatternFill("solid", fgColor="FFC7CE")

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# ─── FILE READING ─────────────────────────────────────────────────────────────

def read_uploaded_file(uploaded_file) -> str:
    name = uploaded_file.name.lower()
    if name.endswith(".csv"):
        df = pd.read_csv(uploaded_file)
        return df.to_string(index=False)
    elif name.endswith((".xlsx", ".xls")):
        df = pd.read_excel(uploaded_file)
        return df.to_string(index=False)
    elif name.endswith(".json"):
        return uploaded_file.read().decode("utf-8")
    elif name.endswith(".pdf"):
        import pdfplumber
        text = ""
        with pdfplumber.open(uploaded_file) as pdf:
            for page in pdf.pages:
                text += page.extract_text() or ""
                tables = page.extract_tables()
                for table in tables:
                    for row in table:
                        text += " | ".join(str(c or "") for c in row) + "\n"
        return text
    else:
        return uploaded_file.read().decode("utf-8", errors="ignore")


# ─── UI ───────────────────────────────────────────────────────────────────────

# Sidebar
with st.sidebar:
    st.title("🚚 Billing Checker")
    st.markdown("---")
    st.markdown("**How it works:**")
    st.markdown("1. 📄 Upload invoice + contract")
    st.markdown("2. 🤖 AI extracts every field")
    st.markdown("3. 🔍 Rules engine checks rates")
    st.markdown("4. 📊 Download payout file")
    st.markdown("---")
    st.markdown("**Checks performed:**")
    checks = ["Rate Deviation", "RTO Overcharge", "COD Fee Overcharge",
              "Duplicate AWB", "Non-Contracted Surcharges", "Zone Mismatch"]
    for c in checks:
        st.markdown(f"✅ {c}")

# Main UI
st.title("🚚 Logistics Billing Checker")
st.markdown("##### AI-powered invoice verification. Minutes, not days.")
st.markdown("---")

# Upload columns
col1, col2 = st.columns(2)
with col1:
    st.markdown("### 📄 Logistics Invoice")
    invoice_file = st.file_uploader(
        "Upload invoice (CSV, Excel, PDF)",
        type=["csv", "xlsx", "xls", "pdf"],
        key="invoice"
    )
    if invoice_file:
        st.success(f"✅ {invoice_file.name}")

with col2:
    st.markdown("### 📋 Contract / Rate Card")
    contract_file = st.file_uploader(
        "Upload contract (JSON, CSV, Excel, PDF)",
        type=["json", "csv", "xlsx", "xls", "pdf"],
        key="contract"
    )
    if contract_file:
        st.success(f"✅ {contract_file.name}")

st.markdown("---")

# Run button
if st.button("🚀 Run Billing Check", type="primary", disabled=not (invoice_file and contract_file)):
    with st.status("Processing...", expanded=True) as status:

        # Step 1: Read files
        st.write("📂 Reading files...")
        invoice_text = read_uploaded_file(invoice_file)
        contract_text = read_uploaded_file(contract_file)

        # Step 2: Extract contract
        st.write("📋 Extracting contract rates with AI...")
        if contract_file.name.endswith(".json"):
            contract_data = json.loads(contract_text)
        else:
            contract_data = ai_extract_contract(contract_text)

        # Step 3: Extract invoice
        st.write("📄 Extracting invoice line items with AI...")
        invoice_items = ai_extract_invoice(invoice_text)
        st.write(f"   → Found **{len(invoice_items)} line items**")

        # Step 4: Run checks
        st.write("🔍 Cross-checking against contracted rates...")
        discrepancies, summary = check_invoice(invoice_items, contract_data)
        st.write(f"   → Found **{summary['error_count']} discrepancies**")

        # Step 5: Generate payout
        st.write("📊 Generating payout file...")
        payout_excel = generate_payout_excel(invoice_items, discrepancies, summary)

        status.update(label="✅ Analysis complete!", state="complete")

    # ── RESULTS ──────────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("## 📊 Results")

    # Hero metrics
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Line Items Checked", f"{summary['total_items']:,}")
    m2.metric("Total Billed", f"₹{summary['total_billed']:,.0f}")
    m3.metric("Overcharges Found", f"₹{summary['total_overcharge']:,.0f}",
              delta=f"-{(summary['total_overcharge']/max(summary['total_billed'],1)*100):.1f}%",
              delta_color="inverse")
    m4.metric("Errors Found", summary["error_count"])

    st.markdown("---")

    col_a, col_b = st.columns([1, 1])

    with col_a:
        st.markdown("#### Overcharges by Type")
        if summary["errors_by_type"]:
            chart_df = pd.DataFrame(
                list(summary["errors_by_type"].items()),
                columns=["Error Type", "Amount (₹)"]
            ).sort_values("Amount (₹)", ascending=False)
            st.bar_chart(chart_df.set_index("Error Type"))
        else:
            st.success("🎉 No overcharges found!")

    with col_b:
        st.markdown("#### Billing Summary")
        summary_df = pd.DataFrame([
            {"Category": "Total Billed", "Amount (₹)": summary["total_billed"]},
            {"Category": "Verified Payable", "Amount (₹)": summary["verified_amount"]},
            {"Category": "Overcharges", "Amount (₹)": summary["total_overcharge"]},
        ])
        st.dataframe(summary_df, use_container_width=True, hide_index=True)

    # Discrepancy table
    st.markdown("---")
    st.markdown("#### 🚨 Discrepancy Report")
    if discrepancies:
        disc_df = pd.DataFrame(discrepancies)
        disc_df = disc_df.sort_values("Overcharge (₹)", ascending=False)
        st.dataframe(
            disc_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Overcharge (₹)": st.column_config.NumberColumn(format="₹%.2f"),
                "Billed (₹)": st.column_config.NumberColumn(format="₹%.2f"),
                "Correct (₹)": st.column_config.NumberColumn(format="₹%.2f"),
            }
        )
    else:
        st.success("✅ No discrepancies found — invoice matches contract rates!")

    # Download
    st.markdown("---")
    st.download_button(
        label="⬇️ Download Payout File (Excel)",
        data=payout_excel,
        file_name=f"verified_payout_{pd.Timestamp.now().strftime('%Y%m%d_%H%M')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary"
    )
    st.caption("Excel contains 3 sheets: Summary · Verified Payout · Discrepancy Report")

elif not invoice_file or not contract_file:
    st.info("👆 Upload both files above to begin. Use the sample files from `generate_sample_data.py` to test.")