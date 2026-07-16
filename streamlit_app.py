# streamlit_app.py — the Component Intelligence Engine dashboard (v1).
"""One part number in -> the full five-dimension workup out.

Run locally:   streamlit run streamlit_app.py
Cloud deploy:  Streamlit Community Cloud, keys in app Secrets.

The engine is unchanged underneath: this file only renders what the
existing modules produce. Passcode gate: set CIE_APP_PASSCODE (in .env
locally, in Secrets on the cloud); leave it blank for no gate.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import streamlit as st

# ---- secrets bridge: cloud Secrets -> environment, before engine config ----
def _bridge_secrets() -> None:
    try:
        for k, v in st.secrets.items():
            if isinstance(v, str) and k not in os.environ:
                os.environ[k] = v
    except Exception:
        pass  # no secrets file locally — .env handles it

_bridge_secrets()

from cie.compliance.scorer import score_compliance          # noqa: E402
from cie.ingestion.pipeline import IngestionPipeline, MpnNotFoundError  # noqa: E402
from cie.lifecycle.history import append_snapshot           # noqa: E402
from cie.lifecycle.scorer import score_lifecycle             # noqa: E402
from cie.manufacturer.observations import record_observation, stats_for  # noqa: E402
from cie.manufacturer.scorer import score_manufacturer       # noqa: E402

BAND_STYLE = {
    "healthy":  ("#1a7f37", "HEALTHY"),
    "watch":    ("#b58900", "WATCH"),
    "at_risk":  ("#d9730d", "AT RISK"),
    "critical": ("#c62828", "CRITICAL"),
}


def band_chip(band: str, score: int) -> str:
    color, label = BAND_STYLE.get(band, ("#666", band.upper()))
    return (f"<span style='background:{color};color:white;padding:2px 10px;"
            f"border-radius:12px;font-weight:600'>{label} {score}</span>")


@st.cache_resource
def get_pipeline() -> IngestionPipeline:
    return IngestionPipeline()


def gate() -> bool:
    from cie.config import get_settings
    passcode = (get_settings().app_passcode
                or os.environ.get("CIE_APP_PASSCODE", "")).strip()
    if not passcode:
        return True
    if st.session_state.get("authed"):
        return True
    st.title("Component Intelligence Engine")
    entered = st.text_input("Passcode", type="password")
    if entered:
        if entered == passcode:
            st.session_state["authed"] = True
            st.rerun()
        else:
            st.error("Incorrect passcode.")
    return False


def reasons_block(title: str, score_obj) -> None:
    with st.expander(f"{title} — itemized reasons "
                     f"(sum exactly to {score_obj.score})"):
        for r in score_obj.reasons:
            st.markdown(f"- **{r.signal}**: +{r.points} — {r.detail}")
        if score_obj.unknowns:
            st.markdown(f"*Unknowns (each costs modest points): "
                        f"{', '.join(score_obj.unknowns)}*")


def main() -> None:
    st.set_page_config(page_title="Component Intelligence Engine",
                       page_icon="🔍", layout="wide")
    if not gate():
        return

    st.title("🔍 Component Intelligence Engine")
    st.caption("BUAD583 capstone — sourcing-risk workup for electronic "
               "components. Unknowns are reported as risk signals, never "
               "guessed. Lead times are never invented.")

    mpn = st.text_input("Manufacturer part number",
                        placeholder="e.g. GRM21BR60G107ME15L")
    go = st.button("Analyze", type="primary")

    if not (go and mpn.strip()):
        return

    pipeline = get_pipeline()
    try:
        with st.spinner("Looking up identity, market, lifecycle, "
                        "maker, and compliance..."):
            record = pipeline.run(mpn.strip())
            snaps = append_snapshot(record, pipeline.settings)
            life = score_lifecycle(record, history_snapshots=snaps)
            probe = score_manufacturer(record)
            record_observation(record, probe.canonical, pipeline.settings)
            n, frac = stats_for(probe.canonical or record.manufacturer,
                                pipeline.settings)
            mfr = score_manufacturer(record, observed_sample=n,
                                     observed_dead_fraction=frac)
            comp = score_compliance(record)
    except MpnNotFoundError:
        st.error(f"'{mpn}' was not found at any configured source. "
                 f"Check the part number, or the part may not be carried "
                 f"by the currently configured distributors.")
        return
    except Exception as exc:
        st.error(f"Lookup failed: {exc}")
        return

    # ---- identity ----------------------------------------------------------
    st.subheader(f"{record.mpn} — {record.manufacturer}")
    idc1, idc2, idc3 = st.columns(3)
    idc1.markdown(f"**Category:** {record.category.value}")
    idc2.markdown(f"**Lifecycle:** {record.lifecycle_status.value}"
                  + (f"  \n*source says: '{record.lifecycle_status_raw}'*"
                     if record.lifecycle_status_raw else ""))
    idc3.markdown(f"**Resolved from query:** {record.query_mpn}")
    if record.mpn.upper().replace("-", "") != \
            record.query_mpn.upper().replace("-", "")[:len(record.mpn)]:
        pass  # cosmetic; the real weak-match logic below
    import re as _re
    _strip = lambda s: _re.sub(r"[^A-Z0-9]", "", s.upper())
    if not _strip(record.mpn).startswith(_strip(record.query_mpn)[:6]):
        st.warning("⚠ Weak match: the resolved part differs substantially "
                   "from what was queried. A human should verify this is "
                   "the same physical part before acting on the scores.")

    # ---- the three verdict cards -------------------------------------------
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("##### Lifecycle health")
        st.markdown(band_chip(life.band.value, life.score),
                    unsafe_allow_html=True)
    with c2:
        st.markdown("##### Manufacturer intelligence")
        st.markdown(band_chip(mfr.band.value, mfr.score),
                    unsafe_allow_html=True)
        if mfr.canonical:
            st.caption(f"recognized as {mfr.canonical} "
                       f"(observed sample: {mfr.observed_sample})")
    with c3:
        st.markdown("##### Compliance")
        st.markdown(band_chip(comp.band.value, comp.score),
                    unsafe_allow_html=True)

    reasons_block("Lifecycle health", life)
    reasons_block("Manufacturer intelligence", mfr)
    reasons_block("Compliance", comp)

    # ---- market view --------------------------------------------------------
    st.subheader("Market availability")
    total_stock = sum(o.stock_qty for o in record.offers)
    st.markdown(f"**Total stock across sources: {total_stock:,}**"
                + ("  — *out of stock everywhere*" if total_stock == 0 else ""))
    rows = []
    for o in record.offers:
        first_price = (f"{o.price_breaks[0].price} {o.price_breaks[0].currency}"
                       if o.price_breaks else "—")
        rows.append({
            "Distributor": o.distributor_name,
            "Stock": o.stock_qty,
            "MOQ": o.moq if o.moq is not None else "unknown",
            "Lead time (days)": (o.lead_time_days
                                 if o.lead_time_days is not None
                                 else "unstated"),
            "Unit price (qty 1 tier)": first_price,
            "Packaging": o.packaging.value,
            "Source": o.source.value,
        })
    if rows:
        st.dataframe(rows, use_container_width=True)
    else:
        st.markdown("*No distributor coverage found at configured sources.*")

    # ---- documents -----------------------------------------------------------
    if record.datasheet:
        st.markdown(f"**Datasheet:** [{record.datasheet.url}]"
                    f"({record.datasheet.url})")
    if record.parametrics:
        with st.expander(f"Parametrics ({len(record.parametrics)})"):
            for k, v in record.parametrics.items():
                st.markdown(f"- **{k}**: {v}")

    st.caption(f"Schema {record.schema_version} · spine: "
               f"{pipeline.spine_name} · sources queried: "
               f"{', '.join(record.meta.sources_queried)}"
               + (f" · failed: {', '.join(record.meta.sources_failed)}"
                  if record.meta.sources_failed else ""))


main()
