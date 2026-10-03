"""Conservación de conteos, disponibilidad temporal y validación de la referencia local."""

from pathlib import Path
import shutil

import numpy as np
import pandas as pd
import pytest

from predweem_twin.seasonal import load_local_seasonal_reference


ROOT = Path(__file__).parents[1]
COUNTS = Path("data/calibration/pergamino_2026_counts.csv")


def test_2026_conserves_each_observed_interval_and_does_not_invent_early_zeros():
    reference = load_local_seasonal_reference(ROOT, as_of="2027-03-27")
    counts = pd.read_csv(ROOT / COUNTS, parse_dates=["FECHA"])
    curve = reference.set_index("Julian_days")["Progreso_2026"]
    at_visits = curve.loc[counts.FECHA.dt.dayofyear].to_numpy()
    total = counts.PLM2.sum()
    np.testing.assert_allclose(at_visits * total, counts.PLM2.cumsum(), atol=1e-9)
    np.testing.assert_allclose(np.diff(at_visits) * total, counts.PLM2.iloc[1:], atol=1e-9)
    first_zero = int(counts.FECHA.iloc[0].dayofyear)
    assert curve.loc[:first_zero - 1].isna().all()
    assert curve.loc[int(counts.FECHA.iloc[-1].dayofyear):].eq(1).all()
    # El primer intervalo (01/04–13/04) se reparte entre visitas, no es un pico diario.
    assert curve.loc[first_zero] == 0 < curve.loc[first_zero + 1] < curve.loc[first_zero + 12]


def test_single_campaign_quantiles_are_monotone_and_bounded():
    reference = load_local_seasonal_reference(ROOT, as_of="2027-03-27")
    assert reference.N_Campanas.eq(1).all()
    for column in ["Progreso_P10", "Progreso_Mediano", "Progreso_P90"]:
        valid = reference[column].dropna()
        assert valid.between(0, 1).all() and (valid.diff().dropna() >= -1e-12).all()
    assert reference.attrs["source_2026"]["initial_zero_reference"]
    assert reference.attrs["source_2026"]["sample_count"] == 11


def test_additional_campaign_is_picked_up_and_gets_equal_weight(tmp_path):
    (tmp_path / COUNTS).parent.mkdir(parents=True)
    shutil.copy(ROOT / COUNTS, tmp_path / COUNTS)
    other = pd.DataFrame({"FECHA": ["2027-04-01", "2027-05-01", "2027-06-01"], "PLM2": [0.0, 100.0, 300.0]})
    (tmp_path / "data/reference").mkdir()
    other.to_csv(tmp_path / "data/reference/pergamino_2027_counts.csv", index=False)
    assert load_local_seasonal_reference(tmp_path, as_of="2027-05-31").Campanas_Anos.eq("2026").all()
    reference = load_local_seasonal_reference(tmp_path, as_of="2027-06-01")
    assert reference.N_Campanas.eq(2).all() and reference.Campanas_Anos.eq("2026, 2027").all()
    day = pd.Timestamp("2027-05-01").dayofyear
    expected = np.median([reference.loc[reference.Julian_days.eq(day), "Progreso_2026"].iloc[0],
                          reference.loc[reference.Julian_days.eq(day), "Progreso_2027"].iloc[0]])
    assert reference.loc[reference.Julian_days.eq(day), "Progreso_Mediano_Empirico"].iloc[0] == pytest.approx(expected)


@pytest.mark.parametrize("fault", ["negative", "nan", "duplicate", "first_count", "year", "order"])
def test_invalid_2026_reference_is_rejected(tmp_path, fault):
    (tmp_path / COUNTS).parent.mkdir(parents=True)
    counts = pd.read_csv(ROOT / COUNTS)
    if fault == "negative":
        counts.loc[2, "PLM2"] = -1
    elif fault == "nan":
        counts.loc[2, "PLM2"] = np.nan
    elif fault == "duplicate":
        counts.loc[2, "FECHA"] = counts.loc[1, "FECHA"]
    elif fault == "first_count":
        counts.loc[0, "PLM2"] = 1
    elif fault == "year":
        counts["FECHA"] = counts["FECHA"].str.replace("2026", "2027")
    else:
        counts = counts.iloc[::-1]
    counts.to_csv(tmp_path / COUNTS, index=False)
    with pytest.raises(ValueError, match="Conteos 2026"):
        load_local_seasonal_reference(tmp_path, as_of="2027-03-27")
