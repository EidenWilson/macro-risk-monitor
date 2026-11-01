"""
One place for everything the pipeline and the model build share.

Before this file existed, the series list (with its publication lags) lived in pipeline.py and a second copy of
the lags lived in build_data.py. A lag changed in one file but not the other would silently let the risk model
use data before it was published (look-ahead bias). Both scripts now import from here.

Policy figures that change by law (the German minimum wage) also live here instead of inside build_data.py, with
a date check so the build warns when a figure has gone out of date.
"""
from datetime import date

# kind: rate  -> feature = 12m change in percentage points
#       index -> feature = 12m % change
#       level -> feature = 12m % change
# lag: months between end of reference period and public release (conservative)
# critical: the model cannot be built without it; the refresh fails instead of publishing a broken page
SERIES = {
    "EURIBOR_3M":     dict(source="ecb",  key="FM/M.U2.EUR.RT.MM.EURIBOR3MD_.HSTA", kind="rate",  lag=0, critical=True),
    "EURUSD":         dict(source="ecb",  key="EXR/M.USD.EUR.SP00.A",               kind="level", lag=0, critical=True),
    # HICP dataset replaced ICP on 2026-02-04 (ECOICOP 2); old ICP keys stop at 2025-12
    "HICP_DE":        dict(source="ecb",  key="HICP/M.DE.N.000000.4D0.INX",         kind="index", lag=1, critical=True),
    "HICP_FOOD_DE":   dict(source="ecb",  key="HICP/M.DE.N.010000.4D0.INX",         kind="index", lag=1, critical=True),
    "HICP_ENERGY_DE": dict(source="ecb",  key="HICP/M.DE.N.NRGY00.4D0.INX",         kind="index", lag=1, critical=True),
    # Germany is not in ECB STS/STBS; Eurostat: industry B-D, seasonally+calendar adj., 2021=100
    "IP_DE":          dict(source="eurostat", key="sts_inpr_m?geo=DE&nace_r2=B-D&s_adj=SCA&unit=I21&indic_bt=PRD",
                           kind="index", lag=2, critical=True),
    "EU_NATGAS":      dict(source="fred", key="PNGASEUUSDM",                        kind="level", lag=1),
    "BRENT":          dict(source="fred", key="DCOILBRENTEU",                       kind="level", lag=0, critical=True),
    # electricity and gas separately (HICP, ECOICOP 2 codes)
    "HICP_ELEC_DE":   dict(source="ecb",  key="HICP/M.DE.N.045100.4D0.INX",         kind="index", lag=1),
    "HICP_GAS_DE":    dict(source="ecb",  key="HICP/M.DE.N.045200.4D0.INX",         kind="index", lag=1),
    # demand: retail and food-service sales volumes, consumer confidence (Eurostat)
    "RETAIL_DE":      dict(source="eurostat", key="sts_trtu_m?geo=DE&nace_r2=G47&s_adj=SCA&unit=I21&indic_bt=VOL_SLS", kind="index", lag=2),
    "FOODSERV_DE":    dict(source="eurostat", key="sts_setu_m?geo=DE&nace_r2=I56&s_adj=SCA&unit=I21&indic_bt=VOL_SLS", kind="index", lag=2),
    "CONF_DE":        dict(source="eurostat", key="ei_bsco_m?geo=DE&indic=BS-CSMCI&s_adj=SA&unit=BAL", kind="rate", lag=0),
    # producer prices by sector (Eurostat, NACE C10 food, C24 metals, C17 paper, C22 plastics, C16 wood)
    "PPI_C10_DE":     dict(source="eurostat", key="sts_inpp_m?geo=DE&nace_r2=C10&s_adj=NSA&unit=I21&indic_bt=PRC_PRR", kind="index", lag=1),
    "PPI_C24_DE":     dict(source="eurostat", key="sts_inpp_m?geo=DE&nace_r2=C24&s_adj=NSA&unit=I21&indic_bt=PRC_PRR", kind="index", lag=1),
    "PPI_C17_DE":     dict(source="eurostat", key="sts_inpp_m?geo=DE&nace_r2=C17&s_adj=NSA&unit=I21&indic_bt=PRC_PRR", kind="index", lag=1),
    "PPI_C22_DE":     dict(source="eurostat", key="sts_inpp_m?geo=DE&nace_r2=C22&s_adj=NSA&unit=I21&indic_bt=PRC_PRR", kind="index", lag=1),
    "PPI_C16_DE":     dict(source="eurostat", key="sts_inpp_m?geo=DE&nace_r2=C16&s_adj=NSA&unit=I21&indic_bt=PRC_PRR", kind="index", lag=1),
    # rates for hedge costs: ECB AAA euro area spot curve, US 1-year Treasury
    "YC_1Y":          dict(source="ecb",  key="YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_1Y", kind="rate",  lag=0),
    "YC_3Y":          dict(source="ecb",  key="YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_3Y", kind="rate",  lag=0),
    "US_1Y":          dict(source="fred", key="DGS1",                               kind="rate",  lag=0),
}

# publication lag per series, derived from SERIES so there is only one copy
LAG = {sid: cfg["lag"] for sid, cfg in SERIES.items()}
CRITICAL = [sid for sid, cfg in SERIES.items() if cfg.get("critical")]

# German statutory minimum wage (Mindestlohn), EUR per hour. Mindestlohnkommission decision of June 2025:
# 13.90 from 2026-01-01, 14.60 from 2027-01-01. Update this when the next decision is published.
MIN_WAGE = {"from": 13.90, "to": 14.60, "effective": "2027-01"}


def min_wage_warning(today=None):
    """A message when the next step of the minimum wage has already taken effect, so the figure needs updating."""
    today = today or date.today()
    y, m = map(int, MIN_WAGE["effective"].split("-"))
    if (today.year, today.month) >= (y, m):
        return (f"macro_config.MIN_WAGE: the rise to {MIN_WAGE['to']} took effect in {MIN_WAGE['effective']}; "
                "set 'from' to it and enter the next decision of the Mindestlohnkommission")
    return None
