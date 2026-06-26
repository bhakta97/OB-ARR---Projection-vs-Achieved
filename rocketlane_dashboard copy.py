# Rocketlane Project Dashboard — Streamlit app for OB master view analytics
# Co-authored with CoCo
import streamlit as st
import pandas as pd
import plotly.express as px
from snowflake.connector import connect

st.set_page_config(page_title="Rocketlane Project Dashboard", layout="wide")



@st.cache_resource
def get_connection():
    return connect(
        account=st.secrets["snowflake"]["account"],
        user=st.secrets["snowflake"]["user"],
        password=st.secrets["snowflake"]["password"],
        role=st.secrets["snowflake"]["role"],
        warehouse=st.secrets["snowflake"]["warehouse"],
    )

def run_query(query):
    try:
        conn = get_connection()
        df = pd.read_sql(query, conn)
    except Exception as e:
        msg = str(e).lower()
        if "390114" in msg or "authentication token has expired" in msg or "session no longer exists" in msg or "390195" in msg:
            # Token expired or session invalid — drop cached connection and retry once
            try:
                conn.close()
            except Exception:
                pass
            get_connection.clear()
            conn = get_connection()
            df = pd.read_sql(query, conn)
        else:
            raise
    df.columns = [c.upper() for c in df.columns]
    for col in ["ACTUAL_START_DATE", "DUE_DATE", "ACTUAL_COMPLETED_DATE", "CANCELLED_DATE", "SNAPSHOT_MONTH", "CREATED_DATE"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


import pathlib as _pathlib
_ob_file = _pathlib.Path(__file__).with_name("OB master view.sql")
_ob_sql = _ob_file.read_text() if _ob_file.exists() else ""
OB_MASTER_REF = "KEKA_BRAIN.RAW.VW_RL_OB_MASTER"
Q_COMBINED = (
    'SELECT PROJECT_ID, PROJECT_NAME, PROJECT_OWNER, PROJECT_AGE,'
    ' CREATED_ON, ACTUAL_COMPLETED_DATE, DUE_DATE, CANCELLED_DATE,'
    ' CUSTOMER_SEGMENT, TYPE_OF_ACCOUNT, MARKET, PROJECT_STATUS,'
    ' "Primary Full Account?", UPSELL_DEAL_ATTRIBUTION,'
    ' UPSELL_RL_CANCEL_REASON,'
    ' TRY_TO_NUMBER("ARR_USD (Final)", 38, 6) AS ARR_USD_FINAL'
    ' FROM ' + OB_MASTER_REF +
    ' WHERE (UPSELL_DEAL_ATTRIBUTION = \'Primary\''
    '        OR "Primary Full Account?" = \'Primary Full Account\')'
    '   AND NOT (PROJECT_STATUS = \'Cancelled\' AND CANCELLED_DATE IS NULL)'
    '   AND NOT (PROJECT_STATUS = \'Completed\' AND ACTUAL_COMPLETED_DATE IS NULL)'
    '   AND NOT (PROJECT_STATUS = \'Proposed\' AND CREATED_ON < DATEADD(MONTH, -3, CURRENT_DATE()))'
)

@st.cache_data(ttl=300)
def load_combined():
    return run_query(Q_COMBINED)


Q_OB_MASTER = """
SELECT PROJECT_ID, ACTUAL_COMPLETED_DATE, CANCELLED_DATE,
       UPSELL_DEAL_ATTRIBUTION, UPSELL_RL_CANCEL_REASON,
       "ARR_USD (Final)" AS ARR_USD_FINAL
FROM KEKA_BRAIN.RAW.VW_RL_OB_MASTER
WHERE TYPE_OF_ACCOUNT LIKE '%Add On%'
"""

@st.cache_data(ttl=60)
def load_ob_master():
    return run_query(Q_OB_MASTER)


# --- Snapshot-based spillover logic ---
SNAPSHOT_TABLE = "KEKA_BRAIN.RAW.RL_OB_MASTER_SNAPSHOT"

Q_SNAPSHOT_DATES = f"SELECT DISTINCT SNAPSHOT_DATE FROM {SNAPSHOT_TABLE} ORDER BY SNAPSHOT_DATE"

@st.cache_data(ttl=300)
def load_snapshot_dates():
    return run_query(Q_SNAPSHOT_DATES)


def get_closest_snapshot_to_qtr_start(snapshot_dates_df, qtr_start):
    """Find the snapshot date closest to (and ideally on) the quarter start date."""
    if snapshot_dates_df.empty:
        return None
    dates = pd.to_datetime(snapshot_dates_df["SNAPSHOT_DATE"])
    qtr_start_ts = pd.Timestamp(qtr_start)
    abs_diff = (dates - qtr_start_ts).abs()
    closest_idx = abs_diff.idxmin()
    return dates.loc[closest_idx]


@st.cache_data(ttl=300)
def load_snapshot_for_date(snapshot_date):
    """Load spillover-relevant data from the snapshot table for a given snapshot date."""
    q = (
        f"SELECT PROJECT_ID, PROJECT_NAME, PROJECT_OWNER, PROJECT_AGE,"
        f" CREATED_ON, ACTUAL_COMPLETED_DATE, DUE_DATE, CANCELLED_DATE,"
        f" CUSTOMER_SEGMENT, TYPE_OF_ACCOUNT, MARKET, PROJECT_STATUS,"
        f" PRIMARY_FULL_ACCOUNT AS \"PRIMARY FULL ACCOUNT?\","
        f" UPSELL_DEAL_ATTRIBUTION, UPSELL_RL_CANCEL_REASON,"
        f" TRY_TO_NUMBER(ARR_USD_FINAL, 38, 6) AS ARR_USD_FINAL"
        f" FROM {SNAPSHOT_TABLE}"
        f" WHERE SNAPSHOT_DATE = '{snapshot_date.strftime('%Y-%m-%d')}'"
        f"   AND (UPSELL_DEAL_ATTRIBUTION = 'Primary'"
        f"        OR PRIMARY_FULL_ACCOUNT = 'Primary Full Account')"
        f"   AND NOT (PROJECT_STATUS = 'Cancelled' AND CANCELLED_DATE IS NULL)"
        f"   AND NOT (PROJECT_STATUS = 'Completed' AND ACTUAL_COMPLETED_DATE IS NULL)"
    )
    return run_query(q)


st.markdown("""
<style>
/* Make the first summary table bigger */
[data-testid="stDataFrame"] table {
    font-size: 1.1rem !important;
}
[data-testid="stDataFrame"] th {
    font-size: 1.15rem !important;
    font-weight: 700 !important;
}
/* Make metric cards smaller */
[data-testid="stMetric"] {
    padding: 0.3rem 0.5rem !important;
}
[data-testid="stMetric"] [data-testid="stMetricLabel"] {
    font-size: 0.75rem !important;
}
[data-testid="stMetric"] [data-testid="stMetricValue"] {
    font-size: 1.1rem !important;
}
</style>
""", unsafe_allow_html=True)

st.title("Rocketlane Project Dashboard")

if st.sidebar.button("Refresh Data", key="refresh_main"):
    st.cache_data.clear()
    st.rerun()

tab_upsell, tab_pipe2 = st.tabs([
    "OS Upsell", "ARR achieved & Forecast"
])

with tab_upsell:
    df_ob = load_ob_master()

    if not df_ob.empty:
        df_ob["ACTUAL_COMPLETED_DATE"] = pd.to_datetime(df_ob["ACTUAL_COMPLETED_DATE"], errors="coerce")
        df_ob["CANCELLED_DATE"] = pd.to_datetime(df_ob["CANCELLED_DATE"], errors="coerce")
        df_ob["ARR_USD_FINAL"] = pd.to_numeric(df_ob["ARR_USD_FINAL"], errors="coerce")

        primary = df_ob[df_ob["UPSELL_DEAL_ATTRIBUTION"] == "Primary"].copy()

        today = pd.Timestamp.now()
        q_index = (today.month - 1) // 3
        qtr_start = pd.Timestamp(today.year, q_index * 3 + 1, 1)
        fy_start = pd.Timestamp(today.year, 1, 1)

        ob_qtr = primary[
            (primary["ACTUAL_COMPLETED_DATE"] >= qtr_start)
            & (primary["ACTUAL_COMPLETED_DATE"] <= today)
        ]
        csm_qtr = primary[
            (primary["CANCELLED_DATE"] >= qtr_start)
            & (primary["CANCELLED_DATE"] <= today)
            & (primary["UPSELL_RL_CANCEL_REASON"] == "Taken up by CSM")
        ]
        ob_fy = primary[
            (primary["ACTUAL_COMPLETED_DATE"] >= fy_start)
            & (primary["ACTUAL_COMPLETED_DATE"] <= today)
        ]
        csm_fy = primary[
            (primary["CANCELLED_DATE"] >= fy_start)
            & (primary["CANCELLED_DATE"] <= today)
            & (primary["UPSELL_RL_CANCEL_REASON"] == "Taken up by CSM")
        ]

        ob_qtr_count = ob_qtr["PROJECT_ID"].nunique()
        csm_qtr_count = csm_qtr["PROJECT_ID"].nunique()
        ob_fy_count = ob_fy["PROJECT_ID"].nunique()
        csm_fy_count = csm_fy["PROJECT_ID"].nunique()

        ob_qtr_arr = ob_qtr.drop_duplicates("PROJECT_ID")["ARR_USD_FINAL"].sum()
        csm_qtr_arr = csm_qtr.drop_duplicates("PROJECT_ID")["ARR_USD_FINAL"].sum()
        ob_fy_arr = ob_fy.drop_duplicates("PROJECT_ID")["ARR_USD_FINAL"].sum()
        csm_fy_arr = csm_fy.drop_duplicates("PROJECT_ID")["ARR_USD_FINAL"].sum()

        m1, m2, m3, m4 = st.columns(4)
        m1.metric(f"Add-On Completed by OB (Q{q_index + 1} {today.year} till date)", ob_qtr_count)
        m1.caption(f"ARR (USD): ${ob_qtr_arr:,.0f}")
        m2.metric(f"Add-On Completed by CSM (Q{q_index + 1} {today.year} till date)", csm_qtr_count)
        m2.caption(f"ARR (USD): ${csm_qtr_arr:,.0f}")
        m3.metric(f"Add-On Completed by OB ({today.year} till date)", ob_fy_count)
        m3.caption(f"ARR (USD): ${ob_fy_arr:,.0f}")
        m4.metric(f"Add-On Completed by CSM ({today.year} till date)", csm_fy_count)
        m4.caption(f"ARR (USD): ${csm_fy_arr:,.0f}")
    else:
        st.warning("No data found in VW_RL_OB_MASTER.")

with tab_pipe2:
    df_all = load_combined()

    if df_all.empty:
        st.warning("No data available for Pipe 2.")
    else:
        df_all["ACTUAL_COMPLETED_DATE"] = pd.to_datetime(df_all["ACTUAL_COMPLETED_DATE"], errors="coerce")
        df_all["DUE_DATE"] = pd.to_datetime(df_all["DUE_DATE"], errors="coerce")
        df_all["CREATED_ON"] = pd.to_datetime(df_all["CREATED_ON"], errors="coerce")
        df_all["CANCELLED_DATE"] = pd.to_datetime(df_all["CANCELLED_DATE"], errors="coerce")

        def classify_market(mkt):
            if pd.isna(mkt) or str(mkt).strip() == "":
                return "US + ROW"
            mkt_u = str(mkt).upper().strip()
            if mkt_u == "INDIA":
                return "India"
            elif mkt_u in ("MENA", "MEA"):
                return "MEA"
            return "US + ROW"

        def classify_size(seg):
            if pd.isna(seg):
                return "Unknown"
            seg_upper = str(seg).upper()
            if "ENT" in seg_upper:
                return "ENT (>500)"
            elif "MM" in seg_upper:
                return "MM (101-500)"
            elif "SMB" in seg_upper:
                return "SMB (0-100)"
            return "Unknown"
        df_all["ARR_VAL"] = pd.to_numeric(df_all["ARR_USD_FINAL"], errors="coerce")
        df_all["MARKET_GROUP"] = df_all["MARKET"].apply(classify_market)
        df_all["SIZE_GROUP"] = df_all["CUSTOMER_SEGMENT"].apply(classify_size)

        today_ts = pd.Timestamp.now()
        q_idx = (today_ts.month - 1) // 3
        qtr_start_month = q_idx * 3 + 1
        qtr_months = [qtr_start_month, qtr_start_month + 1, qtr_start_month + 2]

        df_pipe2 = df_all.iloc[0:0].copy()

        month_names_map = {1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
                           7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec"}
        yr_suffix = str(today_ts.year)[2:]

        def get_biweek(dt):
            if pd.isna(dt):
                return None
            b = "B1" if dt.day <= 15 else "B2"
            mn = month_names_map.get(dt.month, str(dt.month))
            return f"{mn}'{str(dt.year)[2:]} {b}"

        segment_rows = [
            {"Segment": "IND ENT (>500)", "Type": "FA", "filter": "india_ent_full"},
            {"Segment": "IND MM (101-500)", "Type": "FA", "filter": "india_mm_full"},
            {"Segment": "IND SMB (0-100)", "Type": "FA", "filter": "india_smb_full"},
            {"Segment": "IND ENT (>500)", "Type": "AO", "filter": "india_ent_addon"},
            {"Segment": "IND MM (101-500)", "Type": "AO", "filter": "india_mm_addon"},
            {"Segment": "IND SMB (0-100)", "Type": "AO", "filter": "india_smb_addon"},
            {"Segment": "MEA", "Type": "FA + AO", "filter": "mea_all"},
            {"Segment": "US + ROW", "Type": "FA + AO", "filter": "us_row_all"},
        ]

        df_spill = df_all.copy()
        if not df_spill.empty:
            df_spill["ACTUAL_COMPLETED_DATE"] = pd.to_datetime(df_spill["ACTUAL_COMPLETED_DATE"], errors="coerce")
            df_spill["DUE_DATE"] = pd.to_datetime(df_spill["DUE_DATE"], errors="coerce")
            df_spill["CREATED_ON"] = pd.to_datetime(df_spill["CREATED_ON"], errors="coerce")
            df_spill["CANCELLED_DATE"] = pd.to_datetime(df_spill["CANCELLED_DATE"], errors="coerce")
            df_spill["ARR_VAL"] = pd.to_numeric(df_spill["ARR_USD_FINAL"], errors="coerce")
            df_spill["MARKET_GROUP"] = df_spill["MARKET"].apply(classify_market)
            df_spill["SIZE_GROUP"] = df_spill["CUSTOMER_SEGMENT"].apply(classify_size)

            is_fa_s = (
                df_spill["TYPE_OF_ACCOUNT"].fillna("").str.upper().str.contains("FULL ACCOUNT")
                & ~df_spill["TYPE_OF_ACCOUNT"].fillna("").str.upper().str.contains("SKIPPED")
                & ~df_spill["TYPE_OF_ACCOUNT"].fillna("").str.upper().str.contains("ADD ON")
            )
            is_ao_s = df_spill["TYPE_OF_ACCOUNT"].fillna("").str.upper().str.contains("ADD ON")

            def get_target_ttv(row):
                mg = row["MARKET_GROUP"]
                sg = row["SIZE_GROUP"]
                is_fa = bool(is_fa_s.loc[row.name]) if row.name in is_fa_s.index else False
                is_ao = bool(is_ao_s.loc[row.name]) if row.name in is_ao_s.index else False
                if mg == "US + ROW":
                    return 32
                if mg == "MEA":
                    return 90
                if mg == "India":
                    if is_fa:
                        if sg == "ENT (>500)":
                            return 120
                        elif sg == "MM (101-500)":
                            return 63
                        elif sg == "SMB (0-100)":
                            return 32
                    if is_ao:
                        return 26
                return 32

            needs_due_date = (
                df_spill["DUE_DATE"].isna()
                & df_spill["ACTUAL_COMPLETED_DATE"].isna()
                & (df_spill["PROJECT_STATUS"] != "Cancelled")
                & df_spill["CREATED_ON"].notna()
            )
            df_spill["RECALCULATED_DUE_DATE"] = pd.NaT
            if needs_due_date.any():
                ttv_days = df_spill.loc[needs_due_date].apply(get_target_ttv, axis=1)
                df_spill.loc[needs_due_date, "RECALCULATED_DUE_DATE"] = df_spill.loc[needs_due_date, "CREATED_ON"] + pd.to_timedelta(ttv_days, unit="D")
            df_spill["EFFECTIVE_DUE_DATE"] = df_spill["DUE_DATE"].combine_first(df_spill["RECALCULATED_DUE_DATE"])
            is_pf_s = df_spill["PRIMARY FULL ACCOUNT?"] == "Primary Full Account"
            is_pu_s = df_spill["UPSELL_DEAL_ATTRIBUTION"] == "Primary"

            curr_qtr_start = pd.Timestamp(today_ts.year, qtr_start_month, 1)
            curr_qtr_end = curr_qtr_start + pd.DateOffset(months=3) - pd.Timedelta(days=1)

            # --- Snapshot-based spillover identification ---
            snapshot_dates_df = load_snapshot_dates()
            closest_snapshot_date = get_closest_snapshot_to_qtr_start(snapshot_dates_df, curr_qtr_start)

            spillover_project_ids = set()
            if closest_snapshot_date is not None:
                df_snapshot = load_snapshot_for_date(closest_snapshot_date)
                if not df_snapshot.empty:
                    df_snapshot["ACTUAL_COMPLETED_DATE"] = pd.to_datetime(df_snapshot["ACTUAL_COMPLETED_DATE"], errors="coerce")
                    df_snapshot["CANCELLED_DATE"] = pd.to_datetime(df_snapshot["CANCELLED_DATE"], errors="coerce")
                    df_snapshot["CREATED_ON"] = pd.to_datetime(df_snapshot["CREATED_ON"], errors="coerce")
                    # Spillovers = projects created BEFORE quarter start, still open in the snapshot
                    snapshot_not_done = df_snapshot[
                        (df_snapshot["CREATED_ON"] < curr_qtr_start)
                        & df_snapshot["ACTUAL_COMPLETED_DATE"].isna()
                        & ~(df_snapshot["CANCELLED_DATE"].notna() & (df_snapshot["PROJECT_STATUS"] == "Cancelled"))
                        & ~((df_snapshot["PROJECT_STATUS"] == "Proposed") & (df_snapshot["CREATED_ON"] < curr_qtr_start - pd.DateOffset(months=3)))
                    ]
                    spillover_project_ids = set(snapshot_not_done["PROJECT_ID"].unique())
                st.caption(f"Spillover base: snapshot from {closest_snapshot_date.strftime('%Y-%m-%d')} ({len(spillover_project_ids)} projects)")

            spillover_mask = df_spill["PROJECT_ID"].isin(spillover_project_ids)

            created_curr_qtr = (df_spill["CREATED_ON"] >= curr_qtr_start) & (df_spill["CREATED_ON"] <= curr_qtr_end)

            def build_af_table(df_section, section_name, plan_col_name="Plan"):
                today_date = today_ts.normalize()
                current_biweek = get_biweek(today_date)

                af_bucket_order = []
                for m in qtr_months:
                    mn = month_names_map.get(m, str(m))
                    for b in ["B1", "B2"]:
                        bk = f"{mn}'{yr_suffix} {b}"
                        bk_start = pd.Timestamp(today_ts.year, m, 1 if b == "B1" else 16)
                        bk_end = pd.Timestamp(today_ts.year, m, 15) if b == "B1" else (pd.Timestamp(today_ts.year, m, 1) + pd.DateOffset(months=1) - pd.Timedelta(days=1))
                        if bk_end < today_date:
                            af_bucket_order.append((bk + " Completed(A)", "actual", bk))
                        elif bk_start <= today_date <= bk_end:
                            af_bucket_order.append((bk + " Completed(A)", "actual_current", bk))
                            af_bucket_order.append((bk + " Forecast", "forecast_current", bk))
                        else:
                            af_bucket_order.append((bk + " Forecast", "forecast", bk))

                df_section["BIWEEK_ACTUAL"] = df_section["ACTUAL_COMPLETED_DATE"].apply(get_biweek)
                df_section["BIWEEK_DUE"] = df_section["EFFECTIVE_DUE_DATE"].apply(get_biweek)
                df_section["IS_COMPLETED"] = df_section["ACTUAL_COMPLETED_DATE"].notna() & (df_section["ACTUAL_COMPLETED_DATE"] <= today_date)
                df_section["IS_CANCELLED"] = df_section["CANCELLED_DATE"].notna() & (df_section["PROJECT_STATUS"] == "Cancelled")
                df_section["IS_OVERDUE"] = (
                    df_section["ACTUAL_COMPLETED_DATE"].isna()
                    & ~(df_section["CANCELLED_DATE"].notna() & (df_section["PROJECT_STATUS"] == "Cancelled"))
                    & df_section["EFFECTIVE_DUE_DATE"].notna()
                    & (df_section["EFFECTIVE_DUE_DATE"] < today_date)
                )
                df_section["IS_FORECAST"] = (
                    df_section["ACTUAL_COMPLETED_DATE"].isna()
                    & ~(df_section["CANCELLED_DATE"].notna() & (df_section["PROJECT_STATUS"] == "Cancelled"))
                    & df_section["EFFECTIVE_DUE_DATE"].notna()
                )
                df_section.loc[df_section["IS_OVERDUE"], "BIWEEK_DUE"] = current_biweek

                def get_seg_mask(f_name, df):
                    fa_m = df["TYPE_OF_ACCOUNT"].fillna("").str.upper().str.contains("FULL ACCOUNT") & ~df["TYPE_OF_ACCOUNT"].fillna("").str.upper().str.contains("SKIPPED") & ~df["TYPE_OF_ACCOUNT"].fillna("").str.upper().str.contains("ADD ON")
                    ao_m = df["TYPE_OF_ACCOUNT"].fillna("").str.upper().str.contains("ADD ON")
                    pf_m = df["PRIMARY FULL ACCOUNT?"] == "Primary Full Account"
                    pu_m = df["UPSELL_DEAL_ATTRIBUTION"] == "Primary"
                    if f_name == "india_ent_full":
                        return (df["MARKET_GROUP"] == "India") & (df["SIZE_GROUP"] == "ENT (>500)") & fa_m & pf_m
                    elif f_name == "india_mm_full":
                        return (df["MARKET_GROUP"] == "India") & (df["SIZE_GROUP"] == "MM (101-500)") & fa_m & pf_m
                    elif f_name == "india_smb_full":
                        return (df["MARKET_GROUP"] == "India") & (df["SIZE_GROUP"] == "SMB (0-100)") & fa_m & pf_m
                    elif f_name == "india_ent_addon":
                        return (df["MARKET_GROUP"] == "India") & (df["SIZE_GROUP"] == "ENT (>500)") & ao_m & pu_m
                    elif f_name == "india_mm_addon":
                        return (df["MARKET_GROUP"] == "India") & (df["SIZE_GROUP"] == "MM (101-500)") & ao_m & pu_m
                    elif f_name == "india_smb_addon":
                        return (df["MARKET_GROUP"] == "India") & (df["SIZE_GROUP"] == "SMB (0-100)") & ao_m & pu_m
                    elif f_name == "mea_all":
                        return (df["MARKET_GROUP"] == "MEA") & (pf_m | pu_m)
                    elif f_name == "us_row_all":
                        return (df["MARKET_GROUP"] == "US + ROW") & (pf_m | pu_m)
                    return pd.Series([False] * len(df), index=df.index)

                col_headers = [plan_col_name] + [h[0] for h in af_bucket_order] + ["Taken up by CSM", "QTD Completed(F)", "Cancelled"]

                logos_data_s = []
                arr_data_s = []
                for seg_row in segment_rows:
                    mask = get_seg_mask(seg_row["filter"], df_section)
                    seg_df = df_section[mask]
                    logos_row = {"Segment": seg_row["Segment"], "Type": seg_row["Type"]}
                    arr_row = {"Segment": seg_row["Segment"], "Type": seg_row["Type"]}

                    logos_row[plan_col_name] = int(seg_df["PROJECT_ID"].nunique())
                    arr_row[plan_col_name] = seg_df["ARR_VAL"].sum()

                    for col_name, col_type, biweek_key in af_bucket_order:
                        if col_type == "actual":
                            bkt = seg_df[seg_df["IS_COMPLETED"] & (seg_df["BIWEEK_ACTUAL"] == biweek_key)]
                        elif col_type == "actual_current":
                            bkt = seg_df[seg_df["IS_COMPLETED"] & (seg_df["BIWEEK_ACTUAL"] == biweek_key)]
                        elif col_type == "forecast_current":
                            bkt = seg_df[seg_df["IS_FORECAST"] & (seg_df["BIWEEK_DUE"] == biweek_key)]
                        else:
                            bkt = seg_df[seg_df["IS_FORECAST"] & (seg_df["BIWEEK_DUE"] == biweek_key)]
                        logos_row[col_name] = int(bkt["PROJECT_ID"].nunique())
                        arr_row[col_name] = bkt["ARR_VAL"].sum()

                    taken_by_csm = seg_df[
                        seg_df["IS_CANCELLED"]
                        & (seg_df["UPSELL_RL_CANCEL_REASON"] == "Taken up By CSM")
                        & (seg_df["UPSELL_DEAL_ATTRIBUTION"] == "Primary")
                    ]
                    csm_logos = int(taken_by_csm["PROJECT_ID"].nunique())
                    csm_arr = taken_by_csm["ARR_VAL"].sum()

                    biweek_logos = sum(v for k, v in logos_row.items() if k not in ("Segment", "Type", plan_col_name))
                    biweek_arr = sum(v for k, v in arr_row.items() if k not in ("Segment", "Type", plan_col_name))
                    logos_row["QTD Completed(F)"] = biweek_logos + csm_logos
                    arr_row["QTD Completed(F)"] = biweek_arr + csm_arr

                    cancelled = seg_df[
                        seg_df["IS_CANCELLED"]
                        & ~(
                            (seg_df["UPSELL_RL_CANCEL_REASON"] == "Taken up By CSM")
                            & (seg_df["UPSELL_DEAL_ATTRIBUTION"] == "Primary")
                        )
                    ]
                    logos_row["Cancelled"] = int(cancelled["PROJECT_ID"].nunique())
                    arr_row["Cancelled"] = cancelled["ARR_VAL"].sum()
                    logos_row["Taken up by CSM"] = csm_logos
                    arr_row["Taken up by CSM"] = csm_arr
                    logos_data_s.append(logos_row)
                    arr_data_s.append(arr_row)

                overall_l = {"Segment": "Overall", "Type": "NA"}
                overall_a = {"Segment": "Overall", "Type": "NA"}
                for col in col_headers:
                    overall_l[col] = sum(r.get(col, 0) for r in logos_data_s)
                    overall_a[col] = sum(r.get(col, 0) for r in arr_data_s)
                logos_data_s.append(overall_l)
                arr_data_s.append(overall_a)

                df_l = pd.DataFrame(logos_data_s)
                df_a = pd.DataFrame(arr_data_s)
                display_c = ["Segment", "Type"] + col_headers
                return df_l, df_a, display_c

            df_spillover = df_spill[spillover_mask].copy()
            df_curr_qtr = df_spill[created_curr_qtr].copy()

            spill_deduped_summary = df_spillover.drop_duplicates(subset="PROJECT_ID") if not df_spillover.empty else pd.DataFrame()
            inflow_deduped_summary = df_curr_qtr.drop_duplicates(subset="PROJECT_ID") if not df_curr_qtr.empty else pd.DataFrame()

            spill_plan_arr = spill_deduped_summary["ARR_VAL"].sum() if not spill_deduped_summary.empty else 0
            inflow_plan_arr = inflow_deduped_summary["ARR_VAL"].sum() if not inflow_deduped_summary.empty else 0

            spill_completed_arr_summary = 0
            spill_forecast_arr_summary = 0
            if not spill_deduped_summary.empty:
                spill_comp = spill_deduped_summary[
                    spill_deduped_summary["ACTUAL_COMPLETED_DATE"].notna()
                    & (spill_deduped_summary["ACTUAL_COMPLETED_DATE"] >= curr_qtr_start)
                    & (spill_deduped_summary["ACTUAL_COMPLETED_DATE"] <= today_ts)
                ]
                spill_completed_arr_summary = spill_comp["ARR_VAL"].sum()
                spill_forecast_pending = spill_deduped_summary[
                    spill_deduped_summary["ACTUAL_COMPLETED_DATE"].isna()
                    & ~(spill_deduped_summary["CANCELLED_DATE"].notna() & (spill_deduped_summary["PROJECT_STATUS"] == "Cancelled"))
                    & spill_deduped_summary["EFFECTIVE_DUE_DATE"].notna()
                    & (spill_deduped_summary["EFFECTIVE_DUE_DATE"] >= curr_qtr_start)
                    & (spill_deduped_summary["EFFECTIVE_DUE_DATE"] <= curr_qtr_end)
                ]
                spill_csm_cancelled = spill_deduped_summary[
                    spill_deduped_summary["CANCELLED_DATE"].notna()
                    & (spill_deduped_summary["PROJECT_STATUS"] == "Cancelled")
                    & (spill_deduped_summary["UPSELL_RL_CANCEL_REASON"] == "Taken up By CSM")
                    & (spill_deduped_summary["UPSELL_DEAL_ATTRIBUTION"] == "Primary")
                    & (spill_deduped_summary["CANCELLED_DATE"] >= curr_qtr_start)
                    & (spill_deduped_summary["CANCELLED_DATE"] <= today_ts)
                ]
                spill_forecast_arr_summary = spill_completed_arr_summary + spill_forecast_pending["ARR_VAL"].sum() + spill_csm_cancelled["ARR_VAL"].sum()

            inflow_completed_arr_summary = 0
            inflow_forecast_arr_summary = 0
            if not inflow_deduped_summary.empty:
                inflow_comp = inflow_deduped_summary[
                    inflow_deduped_summary["ACTUAL_COMPLETED_DATE"].notna()
                    & (inflow_deduped_summary["ACTUAL_COMPLETED_DATE"] >= curr_qtr_start)
                    & (inflow_deduped_summary["ACTUAL_COMPLETED_DATE"] <= today_ts)
                ]
                inflow_completed_arr_summary = inflow_comp["ARR_VAL"].sum()
                inflow_forecast_pending = inflow_deduped_summary[
                    inflow_deduped_summary["ACTUAL_COMPLETED_DATE"].isna()
                    & ~(inflow_deduped_summary["CANCELLED_DATE"].notna() & (inflow_deduped_summary["PROJECT_STATUS"] == "Cancelled"))
                    & inflow_deduped_summary["EFFECTIVE_DUE_DATE"].notna()
                    & (inflow_deduped_summary["EFFECTIVE_DUE_DATE"] >= curr_qtr_start)
                    & (inflow_deduped_summary["EFFECTIVE_DUE_DATE"] <= curr_qtr_end)
                ]
                inflow_csm_cancelled = inflow_deduped_summary[
                    inflow_deduped_summary["CANCELLED_DATE"].notna()
                    & (inflow_deduped_summary["PROJECT_STATUS"] == "Cancelled")
                    & (inflow_deduped_summary["UPSELL_RL_CANCEL_REASON"] == "Taken up By CSM")
                    & (inflow_deduped_summary["UPSELL_DEAL_ATTRIBUTION"] == "Primary")
                    & (inflow_deduped_summary["CANCELLED_DATE"] >= curr_qtr_start)
                    & (inflow_deduped_summary["CANCELLED_DATE"] <= today_ts)
                ]
                inflow_forecast_arr_summary = inflow_completed_arr_summary + inflow_forecast_pending["ARR_VAL"].sum() + inflow_csm_cancelled["ARR_VAL"].sum()

            total_arr_in_pipe = spill_plan_arr + inflow_plan_arr
            total_golive_forecasted = spill_forecast_arr_summary + inflow_forecast_arr_summary
            spill_csm_arr = spill_csm_cancelled["ARR_VAL"].sum() if not spill_deduped_summary.empty else 0
            inflow_csm_arr = inflow_csm_cancelled["ARR_VAL"].sum() if not inflow_deduped_summary.empty else 0
            total_golive_actuals = spill_completed_arr_summary + spill_csm_arr + inflow_completed_arr_summary + inflow_csm_arr

            summary_data = {
                "": ["Total", "Pipe 1 Spillovers from Last Qtr", "Pipe 2 New Sales in Current Qtr"],
                "ARR in Pipe (QTD)": [
                    f"${total_arr_in_pipe:,.0f}",
                    f"${spill_plan_arr:,.0f}",
                    f"${inflow_plan_arr:,.0f}",
                ],
                "Total Go-Live Forecasted (QTD)": [
                    f"${total_golive_forecasted:,.0f}",
                    f"${spill_forecast_arr_summary:,.0f}",
                    f"${inflow_forecast_arr_summary:,.0f}",
                ],
                "Total Go-Live Actuals (QTD)": [
                    f"${total_golive_actuals:,.0f}",
                    f"${spill_completed_arr_summary + spill_csm_arr:,.0f}",
                    f"${inflow_completed_arr_summary + inflow_csm_arr:,.0f}",
                ],
            }
            st.markdown('<style>.summary-table-wrapper [data-testid="stDataFrame"] table { font-size: 1.3rem !important; } .summary-table-wrapper [data-testid="stDataFrame"] th { font-size: 1.35rem !important; font-weight: 800 !important; } .summary-table-wrapper [data-testid="stDataFrame"] td { padding: 0.75rem 1rem !important; }</style>', unsafe_allow_html=True)
            st.markdown('<div class="summary-table-wrapper">', unsafe_allow_html=True)
            st.dataframe(pd.DataFrame(summary_data), use_container_width=True, hide_index=True, height=150)
            st.markdown('</div>', unsafe_allow_html=True)

            st.divider()

            if not df_spillover.empty:
                st.subheader("Pipe 1: Spillovers")

                spill_deduped = df_spillover.drop_duplicates(subset="PROJECT_ID")
                spill_completed_qtd = spill_deduped[
                    spill_deduped["ACTUAL_COMPLETED_DATE"].notna()
                    & (spill_deduped["ACTUAL_COMPLETED_DATE"] >= curr_qtr_start)
                    & (spill_deduped["ACTUAL_COMPLETED_DATE"] <= today_ts)
                ]
                spill_pending_qtr = spill_deduped[
                    spill_deduped["ACTUAL_COMPLETED_DATE"].isna()
                    & ~(spill_deduped["CANCELLED_DATE"].notna() & (spill_deduped["PROJECT_STATUS"] == "Cancelled"))
                    & spill_deduped["EFFECTIVE_DUE_DATE"].notna()
                    & (spill_deduped["EFFECTIVE_DUE_DATE"] >= today_ts.normalize())
                    & (spill_deduped["EFFECTIVE_DUE_DATE"] <= curr_qtr_end)
                ]
                spill_cancelled_qtd = spill_deduped[
                    spill_deduped["CANCELLED_DATE"].notna()
                    & (spill_deduped["CANCELLED_DATE"] >= curr_qtr_start)
                    & (spill_deduped["CANCELLED_DATE"] <= today_ts)
                    & (spill_deduped["PROJECT_STATUS"] == "Cancelled")
                    & ~(
                        (spill_deduped["UPSELL_RL_CANCEL_REASON"] == "Taken up By CSM")
                        & (spill_deduped["UPSELL_DEAL_ATTRIBUTION"] == "Primary")
                    )
                ]

                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Spillover Completed QTD", int(spill_completed_qtd["PROJECT_ID"].nunique()))
                c2.metric("Spillover ARR Completed QTD", f"${spill_completed_qtd['ARR_VAL'].sum():,.0f}")
                c3.metric("Spillover Pending (Projected QTR)", int(spill_pending_qtr["PROJECT_ID"].nunique()))
                c4.metric("Spillover Pending ARR (Projected QTR)", f"${spill_pending_qtr['ARR_VAL'].sum():,.0f}")

                st.markdown("")
                cc1, cc2, cc3, cc4 = st.columns([1, 1, 1, 1])
                cc1.metric("Spillover Cancelled QTD (Logos)", int(spill_cancelled_qtd["PROJECT_ID"].nunique()))
                cc2.metric("Spillover Cancelled ARR QTD", f"${spill_cancelled_qtd['ARR_VAL'].sum():,.0f}")

                next_qtr_start = curr_qtr_end + pd.Timedelta(days=1)
                next_qtr_end = next_qtr_start + pd.DateOffset(months=3) - pd.Timedelta(days=1)
                spill_next_qtr = spill_deduped[
                    spill_deduped["ACTUAL_COMPLETED_DATE"].isna()
                    & ~(spill_deduped["CANCELLED_DATE"].notna() & (spill_deduped["PROJECT_STATUS"] == "Cancelled"))
                    & spill_deduped["EFFECTIVE_DUE_DATE"].notna()
                    & (spill_deduped["EFFECTIVE_DUE_DATE"] >= next_qtr_start)
                    & (spill_deduped["EFFECTIVE_DUE_DATE"] <= next_qtr_end)
                ]
                cc3.metric("Spillover Due Next QTR (Logos)", int(spill_next_qtr["PROJECT_ID"].nunique()))
                cc4.metric("Spillover Due Next QTR ARR", f"${spill_next_qtr['ARR_VAL'].sum():,.0f}")

                spill_overdue = spill_deduped[
                    spill_deduped["ACTUAL_COMPLETED_DATE"].isna()
                    & ~(spill_deduped["CANCELLED_DATE"].notna() & (spill_deduped["PROJECT_STATUS"] == "Cancelled"))
                    & spill_deduped["EFFECTIVE_DUE_DATE"].notna()
                    & (spill_deduped["EFFECTIVE_DUE_DATE"] < today_ts.normalize())
                ]
                st.markdown("")
                od1, od2, _, _ = st.columns([1, 1, 1, 1])
                od1.metric("Overdue (Logos)", int(spill_overdue["PROJECT_ID"].nunique()))
                od2.metric("Overdue ARR", f"${spill_overdue['ARR_VAL'].sum():,.0f}")

                spill_download = spill_deduped.copy()
                fa_dl = spill_download["TYPE_OF_ACCOUNT"].fillna("").str.upper().str.contains("FULL ACCOUNT") & ~spill_download["TYPE_OF_ACCOUNT"].fillna("").str.upper().str.contains("SKIPPED") & ~spill_download["TYPE_OF_ACCOUNT"].fillna("").str.upper().str.contains("ADD ON")
                ao_dl = spill_download["TYPE_OF_ACCOUNT"].fillna("").str.upper().str.contains("ADD ON")
                pf_dl = spill_download["PRIMARY FULL ACCOUNT?"] == "Primary Full Account"
                pu_dl = spill_download["UPSELL_DEAL_ATTRIBUTION"] == "Primary"
                spill_download = spill_download[(fa_dl & pf_dl) | (ao_dl & pu_dl)]

                st.download_button(
                    label="Download Spillover Base Data",
                    data=spill_download.to_csv(index=False).encode("utf-8"),
                    file_name="spillover_base_data.csv",
                    mime="text/csv",
                    key="download_spillover"
                )

                df_l3, df_a3, dcols3 = build_af_table(spill_deduped, "Spillovers")

                st.markdown(f"**Logos** (Total Spillover Projects: {int(spill_deduped['PROJECT_ID'].nunique())})")
                main_cols3 = [c for c in dcols3 if c != "Cancelled"]
                df_l3_display = df_l3[main_cols3].copy()
                df_l3_display["   "] = ""
                df_l3_display["Cancelled"] = df_l3["Cancelled"]
                st.dataframe(df_l3_display, use_container_width=True, hide_index=True)

                st.markdown("**ARR (USD)**")
                df_a3_d = df_a3.copy()
                for col in dcols3[2:]:
                    if col == "Cancelled":
                        continue
                    df_a3_d[col] = df_a3_d[col].apply(lambda x: f"${x:,.0f}" if pd.notna(x) and x != 0 else "$0")
                df_a3_d["Cancelled"] = df_a3_d["Cancelled"].apply(lambda x: f"${x:,.0f}" if pd.notna(x) and x != 0 else "$0")
                df_a3_display = df_a3_d[main_cols3].copy()
                df_a3_display["   "] = ""
                df_a3_display["Cancelled"] = df_a3_d["Cancelled"]
                st.dataframe(df_a3_display, use_container_width=True, hide_index=True)

                with st.expander("View Project Details by Segment"):
                    seg_options = [f"{r['Segment']} - {r['Type']}" for _, r in df_l3.iterrows()]
                    selected_seg = st.selectbox("Select a segment to see project details", seg_options, key="spill_seg_select")
                    sel_idx = seg_options.index(selected_seg)
                    if sel_idx < len(segment_rows):
                        seg_filter = segment_rows[sel_idx]["filter"]
                        fa_m2 = df_spillover["TYPE_OF_ACCOUNT"].fillna("").str.upper().str.contains("FULL ACCOUNT") & ~df_spillover["TYPE_OF_ACCOUNT"].fillna("").str.upper().str.contains("SKIPPED") & ~df_spillover["TYPE_OF_ACCOUNT"].fillna("").str.upper().str.contains("ADD ON")
                        ao_m2 = df_spillover["TYPE_OF_ACCOUNT"].fillna("").str.upper().str.contains("ADD ON")
                        pf_m2 = df_spillover["PRIMARY FULL ACCOUNT?"] == "Primary Full Account"
                        pu_m2 = df_spillover["UPSELL_DEAL_ATTRIBUTION"] == "Primary"
                        seg_mask_map = {
                            "india_ent_full":   (df_spillover["MARKET_GROUP"] == "India") & (df_spillover["SIZE_GROUP"] == "ENT (>500)") & fa_m2 & pf_m2,
                            "india_mm_full":    (df_spillover["MARKET_GROUP"] == "India") & (df_spillover["SIZE_GROUP"] == "MM (101-500)") & fa_m2 & pf_m2,
                            "india_smb_full":   (df_spillover["MARKET_GROUP"] == "India") & (df_spillover["SIZE_GROUP"] == "SMB (0-100)") & fa_m2 & pf_m2,
                            "india_ent_addon":  (df_spillover["MARKET_GROUP"] == "India") & (df_spillover["SIZE_GROUP"] == "ENT (>500)") & ao_m2 & pu_m2,
                            "india_mm_addon":   (df_spillover["MARKET_GROUP"] == "India") & (df_spillover["SIZE_GROUP"] == "MM (101-500)") & ao_m2 & pu_m2,
                            "india_smb_addon":  (df_spillover["MARKET_GROUP"] == "India") & (df_spillover["SIZE_GROUP"] == "SMB (0-100)") & ao_m2 & pu_m2,
                            "mea_all":          (df_spillover["MARKET_GROUP"] == "MEA") & (pf_m2 | pu_m2),
                            "us_row_all":       (df_spillover["MARKET_GROUP"] == "US + ROW") & (pf_m2 | pu_m2),
                        }
                        sel_df = df_spillover[seg_mask_map[seg_filter]]
                    else:
                        sel_df = df_spillover

                    if sel_df.empty:
                        st.info("No projects in this segment.")
                    else:
                        details_df = pd.DataFrame({
                            "Project Name": sel_df["PROJECT_NAME"],
                            "Project ID": sel_df["PROJECT_ID"],
                            "Owner": sel_df["PROJECT_OWNER"],
                            "ARR_USD (Final)": sel_df["ARR_VAL"].apply(lambda x: f"${float(x):,.0f}" if pd.notna(x) else "$0"),
                            "Due Date": pd.to_datetime(sel_df["DUE_DATE"]).dt.strftime("%Y-%m-%d"),
                            "Effective Due Date": pd.to_datetime(sel_df["EFFECTIVE_DUE_DATE"]).dt.strftime("%Y-%m-%d"),
                            "Project Age": sel_df["PROJECT_AGE"].apply(lambda x: f"{int(x)}d" if pd.notna(x) else "—"),
                            "Project Status": sel_df["PROJECT_STATUS"],
                        })
                        st.dataframe(details_df, use_container_width=True, hide_index=True)
            else:
                st.subheader("Pipe 1: Spillovers")
                st.info("No spillover projects found.")

            st.divider()

            if not df_curr_qtr.empty:
                st.subheader("Pipe 2: New Inflow This Quarter")

                inflow_deduped = df_curr_qtr.drop_duplicates(subset="PROJECT_ID")
                inflow_completed_qtd = inflow_deduped[
                    inflow_deduped["ACTUAL_COMPLETED_DATE"].notna()
                    & (inflow_deduped["ACTUAL_COMPLETED_DATE"] >= curr_qtr_start)
                    & (inflow_deduped["ACTUAL_COMPLETED_DATE"] <= today_ts)
                ]
                inflow_pending_qtr = inflow_deduped[
                    inflow_deduped["ACTUAL_COMPLETED_DATE"].isna()
                    & ~(inflow_deduped["CANCELLED_DATE"].notna() & (inflow_deduped["PROJECT_STATUS"] == "Cancelled"))
                    & inflow_deduped["EFFECTIVE_DUE_DATE"].notna()
                    & (inflow_deduped["EFFECTIVE_DUE_DATE"] >= today_ts.normalize())
                    & (inflow_deduped["EFFECTIVE_DUE_DATE"] <= curr_qtr_end)
                ]
                inflow_cancelled_qtd = inflow_deduped[
                    inflow_deduped["CANCELLED_DATE"].notna()
                    & (inflow_deduped["CANCELLED_DATE"] >= curr_qtr_start)
                    & (inflow_deduped["CANCELLED_DATE"] <= today_ts)
                    & (inflow_deduped["PROJECT_STATUS"] == "Cancelled")
                    & ~(
                        (inflow_deduped["UPSELL_RL_CANCEL_REASON"] == "Taken up By CSM")
                        & (inflow_deduped["UPSELL_DEAL_ATTRIBUTION"] == "Primary")
                    )
                ]

                c5, c6, c7, c8 = st.columns(4)
                c5.metric("New Inflow Completed QTD", int(inflow_completed_qtd["PROJECT_ID"].nunique()))
                c6.metric("New Inflow ARR Completed QTD", f"${inflow_completed_qtd['ARR_VAL'].sum():,.0f}")
                c7.metric("New Inflow Pending (Projected QTR)", int(inflow_pending_qtr["PROJECT_ID"].nunique()))
                c8.metric("New Inflow Pending ARR (Projected QTR)", f"${inflow_pending_qtr['ARR_VAL'].sum():,.0f}")

                st.markdown("")
                cc3, cc4, cc5, cc6 = st.columns([1, 1, 1, 1])
                cc3.metric("New Inflow Cancelled QTD (Logos)", int(inflow_cancelled_qtd["PROJECT_ID"].nunique()))
                cc4.metric("New Inflow Cancelled ARR QTD", f"${inflow_cancelled_qtd['ARR_VAL'].sum():,.0f}")

                inflow_next_qtr = inflow_deduped[
                    inflow_deduped["ACTUAL_COMPLETED_DATE"].isna()
                    & ~(inflow_deduped["CANCELLED_DATE"].notna() & (inflow_deduped["PROJECT_STATUS"] == "Cancelled"))
                    & inflow_deduped["EFFECTIVE_DUE_DATE"].notna()
                    & (inflow_deduped["EFFECTIVE_DUE_DATE"] >= next_qtr_start)
                    & (inflow_deduped["EFFECTIVE_DUE_DATE"] <= next_qtr_end)
                ]
                cc5.metric("New Inflow Due Next QTR (Logos)", int(inflow_next_qtr["PROJECT_ID"].nunique()))
                cc6.metric("New Inflow Due Next QTR ARR", f"${inflow_next_qtr['ARR_VAL'].sum():,.0f}")

                inflow_overdue = inflow_deduped[
                    inflow_deduped["ACTUAL_COMPLETED_DATE"].isna()
                    & ~(inflow_deduped["CANCELLED_DATE"].notna() & (inflow_deduped["PROJECT_STATUS"] == "Cancelled"))
                    & inflow_deduped["EFFECTIVE_DUE_DATE"].notna()
                    & (inflow_deduped["EFFECTIVE_DUE_DATE"] < today_ts.normalize())
                ]
                st.markdown("")
                od3, od4, _, _ = st.columns([1, 1, 1, 1])
                od3.metric("Overdue (Logos)", int(inflow_overdue["PROJECT_ID"].nunique()))
                od4.metric("Overdue ARR", f"${inflow_overdue['ARR_VAL'].sum():,.0f}")

                inflow_download = inflow_deduped.copy()
                fa_idl = inflow_download["TYPE_OF_ACCOUNT"].fillna("").str.upper().str.contains("FULL ACCOUNT") & ~inflow_download["TYPE_OF_ACCOUNT"].fillna("").str.upper().str.contains("SKIPPED") & ~inflow_download["TYPE_OF_ACCOUNT"].fillna("").str.upper().str.contains("ADD ON")
                ao_idl = inflow_download["TYPE_OF_ACCOUNT"].fillna("").str.upper().str.contains("ADD ON")
                pf_idl = inflow_download["PRIMARY FULL ACCOUNT?"] == "Primary Full Account"
                pu_idl = inflow_download["UPSELL_DEAL_ATTRIBUTION"] == "Primary"
                inflow_download = inflow_download[(fa_idl & pf_idl) | (ao_idl & pu_idl)]

                st.download_button(
                    label="Download New Inflow Base Data",
                    data=inflow_download.to_csv(index=False).encode("utf-8"),
                    file_name="new_inflow_base_data.csv",
                    mime="text/csv",
                    key="download_new_inflow"
                )

                df_l4, df_a4, dcols4 = build_af_table(inflow_deduped, "Current Quarter", plan_col_name="Sales Actual QTD")
                st.markdown("**Logos**")
                main_cols4 = [c for c in dcols4 if c != "Cancelled"]
                df_l4_display = df_l4[main_cols4].copy()
                df_l4_display["   "] = ""
                df_l4_display["Cancelled"] = df_l4["Cancelled"]
                st.dataframe(df_l4_display, use_container_width=True, hide_index=True)

                st.markdown("**ARR (USD)**")
                df_a4_d = df_a4.copy()
                for col in dcols4[2:]:
                    if col == "Cancelled":
                        continue
                    df_a4_d[col] = df_a4_d[col].apply(lambda x: f"${x:,.0f}" if pd.notna(x) and x != 0 else "$0")
                df_a4_d["Cancelled"] = df_a4_d["Cancelled"].apply(lambda x: f"${x:,.0f}" if pd.notna(x) and x != 0 else "$0")
                df_a4_display = df_a4_d[main_cols4].copy()
                df_a4_display["   "] = ""
                df_a4_display["Cancelled"] = df_a4_d["Cancelled"]
                st.dataframe(df_a4_display, use_container_width=True, hide_index=True)
            else:
                st.subheader("Pipe 2: New Inflow This Quarter")
                st.info("No projects created in current quarter found.")

            # --- Merged download: Pipe 1 + Pipe 2 combined ---
            st.divider()
            st.subheader("Combined Download")

            merged_parts = []
            if not df_spillover.empty:
                spill_dl = df_spillover.drop_duplicates(subset="PROJECT_ID").copy()
                fa_mdl = spill_dl["TYPE_OF_ACCOUNT"].fillna("").str.upper().str.contains("FULL ACCOUNT") & ~spill_dl["TYPE_OF_ACCOUNT"].fillna("").str.upper().str.contains("SKIPPED") & ~spill_dl["TYPE_OF_ACCOUNT"].fillna("").str.upper().str.contains("ADD ON")
                ao_mdl = spill_dl["TYPE_OF_ACCOUNT"].fillna("").str.upper().str.contains("ADD ON")
                pf_mdl = spill_dl["PRIMARY FULL ACCOUNT?"] == "Primary Full Account"
                pu_mdl = spill_dl["UPSELL_DEAL_ATTRIBUTION"] == "Primary"
                spill_dl = spill_dl[(fa_mdl & pf_mdl) | (ao_mdl & pu_mdl)]
                spill_dl["PIPE"] = "Pipe 1 - Spillover"
                merged_parts.append(spill_dl)

            if not df_curr_qtr.empty:
                inflow_dl = df_curr_qtr.drop_duplicates(subset="PROJECT_ID").copy()
                fa_midl = inflow_dl["TYPE_OF_ACCOUNT"].fillna("").str.upper().str.contains("FULL ACCOUNT") & ~inflow_dl["TYPE_OF_ACCOUNT"].fillna("").str.upper().str.contains("SKIPPED") & ~inflow_dl["TYPE_OF_ACCOUNT"].fillna("").str.upper().str.contains("ADD ON")
                ao_midl = inflow_dl["TYPE_OF_ACCOUNT"].fillna("").str.upper().str.contains("ADD ON")
                pf_midl = inflow_dl["PRIMARY FULL ACCOUNT?"] == "Primary Full Account"
                pu_midl = inflow_dl["UPSELL_DEAL_ATTRIBUTION"] == "Primary"
                inflow_dl = inflow_dl[(fa_midl & pf_midl) | (ao_midl & pu_midl)]
                inflow_dl["PIPE"] = "Pipe 2 - New Inflow"
                merged_parts.append(inflow_dl)

            if merged_parts:
                df_merged = pd.concat(merged_parts, ignore_index=True)
                st.download_button(
                    label="Download Combined (Pipe 1 + Pipe 2) Data",
                    data=df_merged.to_csv(index=False).encode("utf-8"),
                    file_name="combined_pipe1_pipe2_data.csv",
                    mime="text/csv",
                    key="download_combined"
                )
                st.caption(f"Combined: {len(df_merged)} projects ({merged_parts[0].shape[0] if len(merged_parts) > 0 else 0} spillover + {merged_parts[1].shape[0] if len(merged_parts) > 1 else 0} inflow)")
            else:
                st.info("No data available for combined download.")
