"""Unit tests for the data pipeline and the risk model's building blocks."""
import json
import pathlib
import sqlite3
import sys
from datetime import date

import numpy as np
import pandas as pd
import pytest

import build_data
import check_alerts
import macro_config
import pipeline
import validate_data

sys.path.insert(0, str(pathlib.Path(build_data.__file__).parent / "alerts"))
import digest  # noqa: E402  (alerts/digest.py, the monthly email)

# the lags as they were hard-coded in build_data.py before macro_config.py: they must not drift
OLD_LAG = {"EURIBOR_3M": 0, "EURUSD": 0, "HICP_DE": 1, "HICP_FOOD_DE": 1, "HICP_ENERGY_DE": 1,
           "IP_DE": 2, "EU_NATGAS": 1, "BRENT": 0,
           "HICP_ELEC_DE": 1, "HICP_GAS_DE": 1, "RETAIL_DE": 2, "FOODSERV_DE": 2, "CONF_DE": 0,
           "PPI_C10_DE": 1, "PPI_C24_DE": 1, "PPI_C17_DE": 1, "PPI_C22_DE": 1, "PPI_C16_DE": 1,
           "YC_1Y": 0, "YC_3Y": 0, "US_1Y": 0}


# ------------------------------------------------------------------ shared configuration
def test_one_copy_of_the_publication_lags():
    assert build_data.LAG is macro_config.LAG
    assert pipeline.SERIES is macro_config.SERIES
    assert macro_config.LAG == OLD_LAG


def test_every_factor_uses_a_fetched_series():
    fetched = set(macro_config.SERIES)
    for name, (sid, _) in build_data.FACTORS.items():
        assert sid in fetched, f"factor {name} reads {sid}, which the pipeline never downloads"


def test_min_wage_warning_fires_once_the_rise_is_in_force():
    y, m = map(int, macro_config.MIN_WAGE["effective"].split("-"))
    assert macro_config.min_wage_warning(date(y, m, 1)) is not None
    assert macro_config.min_wage_warning(date(y - 1, 12, 1) if m == 1 else date(y, m - 1, 1)) is None


# ------------------------------------------------------------------ pipeline
def test_to_monthly_averages_daily_values():
    s = pd.Series([1.0, 3.0, 10.0], index=["2024-01-05", "2024-01-20", "2024-02-01"])
    m = pipeline.to_monthly(s)
    assert m.to_dict() == {"2024-01": 2.0, "2024-02": 10.0}


def test_to_monthly_reads_ecb_month_codes():
    m = pipeline.to_monthly(pd.Series(["1.5", "2.5"], index=["2024-01", "2024-02"]))
    assert m.to_dict() == {"2024-01": 1.5, "2024-02": 2.5}


def test_panel_only_uses_what_was_published(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.executescript(pipeline.SCHEMA)
    periods = pd.period_range("2020-01", "2021-12", freq="M").astype(str)
    s = pd.Series(np.arange(len(periods), dtype=float), index=periods)
    pipeline.upsert(conn, "IP_DE", s, "now")           # lag 2
    pipeline.upsert(conn, "EURIBOR_3M", s, "now")      # lag 0
    panel = pipeline.build_panel(conn, end="2021-12")
    assert panel.loc["2021-12", "EURIBOR_3M"] == s["2021-12"]
    assert panel.loc["2021-12", "IP_DE"] == s["2021-10"], "IP is published two months late"


def test_upsert_counts_revisions(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.executescript(pipeline.SCHEMA)
    pipeline.upsert(conn, "BRENT", pd.Series({"2024-01": 80.0, "2024-02": 81.0}), "t1")
    n, revised = pipeline.upsert(conn, "BRENT", pd.Series({"2024-01": 80.0, "2024-02": 82.5, "2024-03": 79.0}), "t2")
    assert (n, revised) == (3, 1)


def test_quality_gate_stops_on_a_failed_critical_series():
    panel = pd.DataFrame({sid: [1.0] * 6 for sid in macro_config.SERIES},
                         index=pd.period_range("2026-01", "2026-06", freq="M").astype(str))
    assert pipeline.quality_gate(panel, failed=["CONF_DE"]) == []           # not critical: warn only
    assert pipeline.quality_gate(panel, failed=["HICP_DE"]) == ["HICP_DE: fetch failed"]


def test_quality_gate_stops_on_a_stale_critical_series():
    idx = pd.period_range("2025-01", "2026-06", freq="M").astype(str)
    panel = pd.DataFrame({sid: [1.0] * len(idx) for sid in macro_config.SERIES}, index=idx)
    panel.loc[idx[-6]:, "EURIBOR_3M"] = np.nan
    assert any("EURIBOR_3M" in p and "stale" in p for p in pipeline.quality_gate(panel, failed=[]))


def test_fetch_one_never_raises(monkeypatch):
    def boom(key, start):
        raise ValueError("format changed")
    monkeypatch.setitem(pipeline.FETCHERS, "ecb", boom)
    sid, s, err = pipeline.fetch_one("HICP_DE", macro_config.SERIES["HICP_DE"], "2000-01")
    assert (sid, s) == ("HICP_DE", None) and isinstance(err, ValueError)


# ------------------------------------------------------------------ model
def test_move_in_points_and_percent(raw):
    r = raw
    assert build_data.move(r, "rate", "2010-01", "2011-01") == pytest.approx(r["EURIBOR_3M"]["2011-01"] - r["EURIBOR_3M"]["2010-01"])
    assert build_data.move(r, "cpi", "2010-01", "2011-01") == pytest.approx(r["HICP_DE"]["2011-01"] / r["HICP_DE"]["2010-01"] - 1)


def test_classify_quadrants():
    # growth rising, inflation falling -> Recovery; both rising -> Overheating; etc.
    n = 60
    idx = pd.period_range("2010-01", periods=n, freq="M").astype(str)
    t = np.arange(n)
    ip = pd.Series(100 * np.exp(0.0005 * t ** 1.5), index=idx)        # accelerating growth
    hicp = pd.Series(100 * np.exp(0.004 * t - 0.00004 * t ** 2), index=idx)  # decelerating inflation
    reg, gd, idf = build_data.classify(pd.DataFrame({"IP_DE": ip, "HICP_DE": hicp}))
    assert reg.dropna().iloc[-1] == "Recovery"
    reg2, *_ = build_data.classify(pd.DataFrame({"IP_DE": ip, "HICP_DE": hicp.iloc[::-1].values * 0 + 100 * np.exp(0.0004 * t ** 1.5)}, index=idx))
    assert reg2.dropna().iloc[-1] == "Overheating"


def test_fast_filter_matches_the_old_quadratic_loop():
    from hmmlearn.hmm import GaussianHMM
    rng = np.random.default_rng(1)
    Z = np.vstack([rng.normal([0, 0], 1, (60, 2)), rng.normal([3, -2], 0.7, (60, 2)), rng.normal([-2, 3], 0.8, (60, 2))])
    m = GaussianHMM(n_components=3, covariance_type="full", n_iter=200, random_state=0).fit(Z)
    old = np.vstack([m.predict_proba(Z[: t + 1])[-1] for t in range(len(Z))])
    new = build_data.filtered_probs(m, Z)
    np.testing.assert_allclose(new, old, atol=1e-6)


def test_end_to_end_build_produces_valid_data(raw_csv, tmp_path):
    out = tmp_path / "data.json"
    build_data.main(csv=str(raw_csv), out=str(out))
    D = json.loads(out.read_text())
    errs = validate_data.check(D, today=date(2026, 10, 1))
    assert errs == [], errs
    assert D["meta"]["schema"] == 2 and D["meta"]["samples"] == len(D["samples"])
    assert set(D["current"]["hmm"]) and abs(sum(D["current"]["hmm"].values()) - 1) < 0.01
    # the Covid closures are kept out of the sales history
    i = 5 + D["factors"].index("retail")
    in_covid = [row for row in D["samples"] if "2019-04" <= row[0] <= "2022-03"]
    assert in_covid and all(row[i] is None for row in in_covid)



def test_headline_inflation_ahead_of_its_parts_waits_for_them(raw, tmp_path):
    """The daily refresh of 9 Nov 2026: October headline inflation was out, October food prices were not."""
    early = raw.copy()
    last = early.dropna(subset=["HICP_DE"]).index.max()
    early.loc[last, "HICP_FOOD_DE"] = np.nan
    csv = tmp_path / "raw.csv"
    early.to_csv(csv)
    out = tmp_path / "data.json"
    build_data.main(csv=str(csv), out=str(out))
    D = json.loads(out.read_text())
    assert D["current"]["asOf"] < last and D["current"]["food"] is not None
    assert validate_data.check(D, today=date(2026, 10, 1)) == []

# ------------------------------------------------------------------ data gate
def _good():
    return json.loads((pytest.importorskip("pathlib").Path(build_data.__file__).parent / "docs" / "data.json").read_text())


def _after(D, months):
    """A date some months after the data's own month. These tests read docs/data.json, which the refresh replaces every
    month, so a fixed date would turn into a false failure once the data moves past it."""
    y, m = map(int, D["current"]["asOf"].split("-"))
    m += months
    return date(y + (m - 1) // 12, (m - 1) % 12 + 1, 1)


def test_published_data_passes_the_gate():
    D = _good()
    assert validate_data.check(D, today=_after(D, 2)) == []


def test_gate_catches_stale_data():
    D = _good()
    assert not any("months old" in e for e in validate_data.check(D, today=_after(D, 4)))
    assert any("months old" in e for e in validate_data.check(D, today=_after(D, 6)))


def test_gate_catches_unit_errors_and_broken_rows():
    D = _good()
    D["current"]["euribor"] = 250.0                    # basis points read as percent
    D["samples"][3] = D["samples"][3][:-1]
    D["current"]["regime"] = "Boom"
    errs = validate_data.check(D, today=_after(D, 1))
    joined = " | ".join(errs)
    assert "euribor" in joined and "wrong length" in joined and "Boom" in joined


def test_gate_catches_a_missing_scenario():
    D = _good()
    del D["scenarios"]["2022 energy shock"]
    assert any("2022 energy shock" in e for e in validate_data.check(D, today=_after(D, 1)))


# ------------------------------------------------------------------ alerts
def _state(regime, hist, **cur):
    base = {"asOf": "2026-09", "regime": regime, "hmmState": "Expansion", "euribor": 2.0, "energy": 1.0, "food": 2.0, "hicp": 2.0}
    base.update(cur)
    return {"current": base, "hist": {"reg": hist}}


def test_first_month_of_a_new_regime_is_provisional():
    new = _state("Stagflation", ["Recovery"] * 5 + ["Stagflation"])
    old = _state("Recovery", ["Recovery"] * 5)
    lines = check_alerts.alert_lines(new, old)
    assert "provisional" in lines[0]


def test_confirmed_regime_is_not_provisional():
    new = _state("Stagflation", ["Recovery"] * 5 + ["Stagflation"] * 2)
    old = _state("Recovery", ["Recovery"] * 5 + ["Stagflation"])
    assert "provisional" not in check_alerts.alert_lines(new, old)[0]


def test_flip_back_is_called_noise():
    new = _state("Recovery", ["Recovery"] * 5 + ["Stagflation", "Recovery"])
    old = _state("Stagflation", ["Recovery"] * 5 + ["Stagflation"])
    assert any("noise" in x for x in check_alerts.alert_lines(new, old))


def test_rate_move_threshold():
    new = _state("Recovery", ["Recovery"], euribor=2.3)
    old = _state("Recovery", ["Recovery"], euribor=2.0)
    assert check_alerts.alert_lines(new, old) == ["- Euribor 3M moved from 2.0 to 2.3 in one update."]
    assert check_alerts.alert_lines(_state("Recovery", ["Recovery"], euribor=2.1), old) == []


# ------------------------------------------------------------------ the advisors' email (alerts/digest.py)
def _snap(regime, since, recent):
    names = ["Recovery", "Overheating", "Stagflation", "Contraction"]
    words = {"regimes": {r: r for r in names}, "desc": {r: "" for r in names}, "presets": {}, "month": "October 2026",
             "since": since, "wageMonth": "January 2027"}
    return {"asOf": "2026-10", "since": since, "regime": regime, "euribor": 2.0, "hicp": 2.0, "energy": 1.0, "ip": 0.5,
            "minWage": {"from": 13.9, "to": 14.6, "effective": "2027-01"}, "examples": {}, "recent": recent, "de": words, "en": words}


def test_email_marks_a_first_month_phase_as_provisional():
    subject, body = digest.digest(_snap("Contraction", "2026-10", ["Stagflation"] * 2 + ["Contraction"]), _snap("Stagflation", "2026-05", ["Stagflation"] * 3))
    assert "vorläufig" in subject and "provisionally" in subject
    assert "Vorläufig" in body and "Provisional" in body and "reverses" not in body


def test_email_states_a_phase_that_held_two_months_plainly():
    subject, body = digest.digest(_snap("Contraction", "2026-09", ["Stagflation", "Contraction", "Contraction"]), _snap("Stagflation", "2026-05", ["Stagflation"] * 3))
    assert "Economy now Contraction" in subject and "Konjunktur jetzt Contraction" in subject
    assert "Provisional" not in body and "Vorläufig" not in body


def test_email_calls_a_flip_back_noise():
    subject, body = digest.digest(_snap("Stagflation", "2026-10", ["Stagflation", "Contraction", "Stagflation"]), _snap("Contraction", "2026-09", ["Stagflation", "Contraction"]))
    assert "reverses" in body and "Rauschen" in body and "provisionally" in subject


# ------------------------------------------------------------------ no commit without new data
def test_rebuild_with_the_same_data_keeps_the_file(raw_csv, tmp_path):
    out = tmp_path / "data.json"
    build_data.main(csv=str(raw_csv), out=str(out))
    D = json.loads(out.read_text())
    D["meta"]["generatedAt"] = "2000-01-01T00:00:00+00:00"           # as if built long ago
    out.write_text(json.dumps(D, separators=(",", ":")))
    before = out.read_bytes()
    build_data.main(csv=str(raw_csv), out=str(out))
    assert out.read_bytes() == before, "same data: same file, so the refresh has nothing to commit"
    D["current"]["euribor"] += 1                                     # the previous file had other data
    out.write_text(json.dumps(D, separators=(",", ":")))
    build_data.main(csv=str(raw_csv), out=str(out))
    assert json.loads(out.read_text())["meta"]["generatedAt"] != "2000-01-01T00:00:00+00:00", "new data: new provenance"
