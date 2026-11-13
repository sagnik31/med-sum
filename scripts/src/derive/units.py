"""
The unit table (decision 12): how units are spelt, which test is which
analyte, and the fixed conversions between units of the same analyte.

Conversion is done here, in code, never by the model. A converted value is
a derived field; the printed value stays and is what's shown by default.
Analytes not in the table, and units the table doesn't know, are never
converted, so trends across different units are skipped for them.

Each analyte has a canonical unit: the one most of the dataset's labs
print. A value is converted only when its printed unit is a different unit.
"""

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal

from record.document import ConvertedValue


# -------------------------------------------------------------------
# Units
# -------------------------------------------------------------------
@dataclass(frozen=True)
class Unit:
    symbol: str  # canonical spelling
    dimension: str  # what it measures; units convert only within one
    factor: Decimal  # size in the dimension's base unit


def unit_key(text: str) -> str:
    """A unit's spelling for lookup: NFKC, case and spaces ignored, µ as u."""
    text = unicodedata.normalize("NFKC", text).casefold()
    text = text.replace("μ", "u").replace("×", "x").replace("mcg", "ug")
    return re.sub(r"\s+", "", text)


# (symbol, dimension, factor to the dimension's base unit, other spellings)
_UNIT_ROWS: list[tuple[str, str, str, tuple[str, ...]]] = [
    # Mass concentration; base g/L.
    ("g/L", "mass", "1", ()),
    ("g/dL", "mass", "10", ("gm/dL", "gm%", "g%")),
    ("mg/dL", "mass", "0.01", ("mg%",)),
    ("mg/L", "mass", "0.001", ("ug/mL",)),
    ("µg/dL", "mass", "0.00001", ()),
    ("µg/L", "mass", "0.000001", ()),
    ("ng/mL", "mass", "0.000001", ()),
    ("ng/dL", "mass", "0.00000001", ()),
    ("pg/mL", "mass", "0.000000001", ()),
    # Amount of substance; base mol/L.
    ("mmol/L", "molar", "0.001", ()),
    ("µmol/L", "molar", "0.000001", ()),
    ("nmol/L", "molar", "0.000000001", ()),
    ("pmol/L", "molar", "0.000000000001", ()),
    # Charge equivalents (electrolytes); base Eq/L.
    ("mEq/L", "equivalents", "0.001", ()),
    # Cell counts; base per µL (one µL is one cubic millimetre).
    ("/cumm", "count", "1",
     ("/mm³", "cells/cumm", "/µL", "cells/µL")),
    ("10³/µL", "count", "1000",
     ("10^3/µL", "x10³/µL", "x10^3/µL", "thou/µL", "K/µL", "10⁹/L", "10^9/L",
      "x10⁹/L", "x10^9/L")),
    ("lakh/cumm", "count", "100000",
     ("lakhs/cumm", "lakh/mm³")),
    ("mill/cumm", "count", "1000000",
     ("million/cumm", "millions/cumm", "10⁶/µL", "10^6/µL",
      "x10⁶/µL", "x10^6/µL", "10¹²/L", "10^12/L", "x10¹²/L", "x10^12/L")),
    # Enzyme and hormone activity; base U/L.
    ("U/L", "activity", "1", ("IU/L", "mIU/mL")),
    ("µIU/mL", "activity", "0.001", ("mIU/L",)),
    # Units that only ever stand alone here.
    ("%", "percent", "1", ()),
    ("fL", "volume", "1", ("µm³",)),
    ("pg", "cell_mass", "1", ()),
    ("mL/min/1.73m²", "filtration", "1", ()),
    ("/hpf", "per_field", "1", ("per hpf",)),
    ("mm/hr", "sedimentation", "1", ("mm/h", "mm/1st hr", "mm in 1st hr")),
]  # fmt: skip

UNITS: dict[str, Unit] = {}
for _symbol, _dimension, _factor, _spellings in _UNIT_ROWS:
    _unit = Unit(_symbol, _dimension, Decimal(_factor))
    for _spelling in (_symbol, *_spellings):
        _key = unit_key(_spelling)
        if _key in UNITS:
            raise ValueError(f"unit spelling {_spelling!r} is listed twice")
        UNITS[_key] = _unit


def lookup_unit(text: str | None) -> Unit | None:
    """The unit a printed unit text is, or None when the table doesn't know it."""
    if text is None or not text.strip():
        return None
    return UNITS.get(unit_key(text))


# -------------------------------------------------------------------
# Analytes
# -------------------------------------------------------------------
@dataclass(frozen=True)
class Analyte:
    key: str
    unit: str  # canonical unit symbol
    names: tuple[str, ...]  # test names as labs print them
    molar_mass: Decimal | None = None  # g/mol, for mass <-> amount
    valence: int | None = None  # for mmol/L <-> mEq/L

    @property
    def canonical(self) -> Unit:
        return UNITS[unit_key(self.unit)]


# Molar masses (g/mol): glucose 180.16, cholesterol 386.65, triglycerides
# as triolein 885.7, urea 60.06, BUN as its two nitrogen atoms 28.014,
# creatinine 113.12, uric acid 168.11, bilirubin 584.66, iron 55.845,
# T3 650.98, T4 776.87, 25-OH vitamin D3 400.64; Na, K, Cl by atomic mass.
_ANALYTE_ROWS: list[tuple] = [
    # Haematology
    ("haemoglobin", "g/dL", ("haemoglobin", "hemoglobin", "hb", "hgb")),
    ("rbc_count", "mill/cumm", ("rbc count", "total rbc count", "rbc",
                                "red blood cell count")),
    ("pcv", "%", ("pcv", "packed cell volume", "haematocrit", "hematocrit", "hct")),
    ("mcv", "fL", ("mcv", "mean corpuscular volume")),
    ("mch", "pg", ("mch", "mean corpuscular haemoglobin")),
    ("mchc", "g/dL", ("mchc", "mean corpuscular haemoglobin concentration")),
    ("rdw_cv", "%", ("rdw cv", "rdw")),
    ("platelet_count", "/cumm", ("platelet count", "platelets", "plt")),
    ("wbc_count", "/cumm", ("total wbc count", "wbc count", "wbc",
                            "total leucocyte count", "total leukocyte count",
                            "tlc", "white blood cell count")),
    # Glucose
    ("hba1c", "%", ("hba1c", "hb a1c", "glycated haemoglobin",
                    "glycosylated haemoglobin")),
    ("glucose_fasting", "mg/dL", ("fasting blood sugar", "fbs",
                                  "fasting plasma glucose", "fpg",
                                  "fasting blood glucose", "glucose fasting"),
     "180.16"),
    ("glucose_random", "mg/dL", ("random blood sugar", "rbs",
                                 "random plasma glucose", "random blood glucose",
                                 "glucose random"), "180.16"),
    # Lipids
    ("cholesterol_total", "mg/dL", ("total cholesterol", "cholesterol total",
                                    "cholesterol"), "386.65"),
    ("triglycerides", "mg/dL", ("triglycerides", "tg"), "885.7"),
    ("hdl_cholesterol", "mg/dL", ("hdl cholesterol", "hdl", "hdl c"), "386.65"),
    ("ldl_cholesterol", "mg/dL", ("ldl cholesterol", "ldl", "ldl c"), "386.65"),
    ("vldl_cholesterol", "mg/dL", ("vldl cholesterol", "vldl"), "386.65"),
    # Kidney
    ("urea", "mg/dL", ("urea",), "60.06"),
    ("bun", "mg/dL", ("urea nitrogen", "urea nitrogen bun", "bun"), "28.014"),
    ("creatinine", "mg/dL", ("creatinine",), "113.12"),
    ("uric_acid", "mg/dL", ("uric acid",), "168.11"),
    ("egfr", "mL/min/1.73m²", ("egfr",)),
    # Electrolytes
    ("sodium", "mmol/L", ("sodium", "na"), "22.99", 1),
    ("potassium", "mmol/L", ("potassium", "k"), "39.098", 1),
    ("chloride", "mmol/L", ("chloride", "cl"), "35.45", 1),
    # Iron
    ("iron", "µg/dL", ("iron",), "55.845"),
    ("tibc", "µg/dL", ("tibc", "total iron binding capacity"), "55.845"),
    ("transferrin_saturation", "%", ("transferrin saturation", "tsat")),
    ("ferritin", "ng/mL", ("ferritin",)),
    # Liver
    ("bilirubin_total", "mg/dL", ("total bilirubin", "bilirubin total"), "584.66"),
    ("bilirubin_direct", "mg/dL", ("direct bilirubin", "bilirubin direct",
                                   "conjugated bilirubin"), "584.66"),
    ("ast", "U/L", ("sgot ast", "sgot", "ast", "aspartate aminotransferase")),
    ("alt", "U/L", ("sgpt alt", "sgpt", "alt", "alanine aminotransferase")),
    ("alp", "U/L", ("alkaline phosphatase", "alp")),
    ("protein_total", "g/dL", ("total protein", "protein total")),
    ("albumin", "g/dL", ("albumin",)),
    # Thyroid
    ("t3_total", "ng/mL", ("t3 total", "total t3", "t3"), "650.98"),
    ("t4_total", "µg/dL", ("t4 total", "total t4", "t4"), "776.87"),
    ("tsh", "µIU/mL", ("tsh", "thyroid stimulating hormone")),
    # Others
    ("vitamin_d_25oh", "ng/mL", ("vitamin d 25 oh", "25 oh vitamin d",
                                 "vitamin d", "25 hydroxy vitamin d"), "400.64"),
    ("crp", "mg/L", ("crp", "c reactive protein")),
    ("psa_total", "ng/mL", ("total psa", "psa total", "psa")),
]  # fmt: skip

# A leading specimen word doesn't change the test: "Serum Creatinine" is
# creatinine. "Urine" is not dropped: urine glucose is not blood glucose.
_SPECIMEN = re.compile(r"^(?:serum|plasma|blood|s)\s+")


def name_key(test_name: str) -> str:
    """A test name for lookup: NFKC, lower case, punctuation as spaces."""
    text = unicodedata.normalize("NFKC", test_name).casefold()
    text = re.sub(r"[^\w]+", " ", text).strip()
    return _SPECIMEN.sub("", text)


ANALYTES: dict[str, Analyte] = {}
_BY_NAME: dict[str, Analyte] = {}
for _row in _ANALYTE_ROWS:
    _key, _unit_symbol, _names, *_rest = _row
    _analyte = Analyte(
        _key,
        _unit_symbol,
        _names,
        Decimal(_rest[0]) if _rest else None,
        _rest[1] if len(_rest) > 1 else None,
    )
    ANALYTES[_key] = _analyte
    for _name in _names:
        _nk = name_key(_name)
        if _nk in _BY_NAME:
            raise ValueError(f"test name {_name!r} is listed twice")
        _BY_NAME[_nk] = _analyte


# Every word that is part of some listed test name. A qualifier made of
# other words ("Ultrasensitive", "CLIA") says how the test was done, not
# which test it is.
# A specimen other than blood is a different test even though no listed
# name has the word: "Creatinine (Urine)" is not serum creatinine.
_NAME_WORDS = frozenset(w for nk in _BY_NAME for w in nk.split()) | {
    "urine", "urinary", "csf", "stool", "fluid", "ascitic", "pleural", "synovial",
}  # fmt: skip

# A trailing parenthesised qualifier: "TSH (Ultrasensitive)".
_QUALIFIER = re.compile(r"^(?P<name>.*\S)\s*\((?P<qualifier>[^()]*)\)\s*$")


def lookup_analyte(test_name: str | None) -> Analyte | None:
    """
    The analyte a printed test name is, or None when it isn't in the table.

    The whole name is looked up first ("T3 (Total)" is "t3 total"). Failing
    that, a trailing parenthesised qualifier is ignored, but only when it
    can't name a different test: it is another name of the same analyte
    ("Glycated Haemoglobin (HbA1c)"), or none of its words is part of any
    listed name ("TSH (Ultrasensitive)"). "Cholesterol (HDL)" and
    "Bilirubin (Direct)" keep no analyte, since "HDL" and "Direct" would
    pick another test than the bare name does.
    """
    if not test_name:
        return None
    found = _BY_NAME.get(name_key(test_name))
    if found is not None:
        return found
    m = _QUALIFIER.match(unicodedata.normalize("NFKC", test_name))
    if m is None:
        return None
    base = _BY_NAME.get(name_key(m["name"]))
    if base is None:
        return None
    qualifier = name_key(m["qualifier"])
    if _BY_NAME.get(qualifier) is base:
        return base
    if not qualifier or any(w in _NAME_WORDS for w in qualifier.split()):
        return None
    return base


# -------------------------------------------------------------------
# Conversion
# -------------------------------------------------------------------
def conversion_factor(unit: Unit, analyte: Analyte) -> Decimal | None:
    """What a value in `unit` is multiplied by to be in the analyte's unit."""
    target = analyte.canonical
    if unit.dimension == target.dimension:
        return unit.factor / target.factor
    # Through mol/L: mass by molar mass, equivalents by valence.
    to_molar = _to_molar(unit, analyte)
    from_molar = _to_molar(target, analyte)
    if to_molar is None or from_molar is None:
        return None
    return to_molar / from_molar


def _to_molar(unit: Unit, analyte: Analyte) -> Decimal | None:
    """The factor taking a value in `unit` to mol/L, when the analyte allows it."""
    if unit.dimension == "molar":
        return unit.factor
    if unit.dimension == "mass" and analyte.molar_mass is not None:
        return unit.factor / analyte.molar_mass
    if unit.dimension == "equivalents" and analyte.valence is not None:
        return unit.factor / analyte.valence
    return None


def convertible(unit_text: str | None, analyte: Analyte) -> bool | None:
    """Whether a printed unit fits the analyte; None when the unit is unknown."""
    unit = lookup_unit(unit_text)
    if unit is None:
        return None
    return conversion_factor(unit, analyte) is not None


def convert(
    value: float | None, unit_text: str | None, analyte: Analyte | None
) -> ConvertedValue | None:
    """
    The value in the analyte's canonical unit, when it was printed in a
    different unit the table can convert. Rounded to the printed value's
    significant digits, and at least 4.
    """
    if value is None or analyte is None:
        return None
    unit = lookup_unit(unit_text)
    if unit is None or unit.symbol == analyte.unit:
        return None
    factor = conversion_factor(unit, analyte)
    if factor is None:
        return None
    printed = Decimal(repr(value))
    result = _round_significant(printed * factor, max(4, _significant(printed)))
    return ConvertedValue(value=float(result), unit=analyte.unit, from_unit=unit_text)


def _significant(d: Decimal) -> int:
    digits = d.normalize().as_tuple().digits
    return len(digits)


def _round_significant(d: Decimal, digits: int) -> Decimal:
    if d == 0:
        return d
    exponent = d.adjusted() - digits + 1
    return d.quantize(Decimal(1).scaleb(exponent))
