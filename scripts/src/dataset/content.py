"""
The synthetic patients and their documents (decisions 4-9, 38, 39).

Everything is fictional: patients, doctors, laboratories and hospital.
Every page says so in its footer. Documents are clean and printed (no
scans, handwriting or noise; decision 4), in English with Indian
conventions (mg/dL, dd/mm/yyyy, 1-0-1, BD, SOS, brand names).

Each document lists the `cases` it exercises, including the accuracy bugs
of section 6 of the plan that clean documents can show. The content is
written out by hand, not drawn at random, so it is the same on every build.
"""

import re
from dataclasses import dataclass, field
from datetime import date

from dataset.layout import (
    Allergy,
    Columns,
    Condition,
    HeaderField,
    Heading,
    LabTest,
    Line,
    Medicine,
    Range,
    Remark,
    Row,
    Table,
    Title,
    Vital,
    allergy_line,
    conditions_line,
    fields_line,
    header,
    header_table,
    lab_lines,
    lab_panel,
    lab_table_flag_in_value,
    lab_table_ranges_first,
    remark_line,
    rng,
    rx_lines,
    rx_table,
    rx_table_strength,
    vitals_line,
)

FOOTER = "Synthetic test document: fictional patient, doctors and laboratory."


@dataclass(frozen=True)
class Patient:
    key: str
    name: str
    sex: str
    dob: date
    mrn: str  # the hospital's patient ID

    def age(self, on: date) -> int:
        return (
            on.year
            - self.dob.year
            - ((on.month, on.day) < (self.dob.month, self.dob.day))
        )


@dataclass
class Doc:
    id: str
    patient: str  # Patient.key the document is uploaded to
    type: str  # lab_report | prescription | other
    file_format: str  # pdf (text layer) | scan_pdf (images only) | png | jpg
    paper: str  # a4 | a5
    pages: list[list]
    cases: list[str] = field(default_factory=list)
    notes: str = ""
    # The routing signals (LC-nn) the clean document raises: what it is built
    # to send to review. None of them means it should end verified.
    expected_signals: list[str] = field(default_factory=list)

    def page_blocks(self) -> list[list]:
        """Each page as printed: its blocks, then the synthetic-data footer."""
        return [page + [Line(FOOTER, style="footer")] for page in self.pages]

    @property
    def file_name(self) -> str:
        ext = {"pdf": "pdf", "scan_pdf": "pdf", "png": "png", "jpg": "jpg"}
        return f"{self.id}_{self.type}.{ext[self.file_format]}"


PATIENTS = [
    Patient("meera", "Meera Iyer", "F", date(1967, 4, 12), "SH-100231"),
    Patient("rohan", "Rohan Kulkarni", "M", date(1980, 8, 3), "SH-100232"),
    Patient("fatima", "Fatima Shaikh", "F", date(1993, 1, 25), "SH-100233"),
    Patient("arjun", "Arjun Menon", "M", date(1958, 11, 30), "SH-100234"),
    # Held out (d18-d24).
    Patient("kabir", "Kabir Sethi", "M", date(1979, 3, 9), "SH-100235"),
    Patient("nandini", "Nandini Bose", "F", date(1988, 7, 21), "SH-100236"),
]  # fmt: skip
BY_KEY = {p.key: p for p in PATIENTS}

# Analyte keys the answer keys expect (the unit table of step 5 uses them).
# Tests not listed (urine routine and the like) have no analyte.
ANALYTES = {
    "Haemoglobin": "haemoglobin",
    "RBC Count": "rbc_count",
    "PCV": "pcv",
    "MCV": "mcv",
    "MCH": "mch",
    "MCHC": "mchc",
    "RDW-CV": "rdw_cv",
    "Platelet Count": "platelet_count",
    "Platelets": "platelet_count",
    "Total WBC Count": "wbc_count",
    "Total Leucocyte Count": "wbc_count",
    "HbA1c": "hba1c",
    "Fasting Blood Sugar": "glucose_fasting",
    "Fasting Plasma Glucose": "glucose_fasting",
    "Random Blood Sugar": "glucose_random",
    "Total Cholesterol": "cholesterol_total",
    "Triglycerides": "triglycerides",
    "HDL Cholesterol": "hdl_cholesterol",
    "LDL Cholesterol": "ldl_cholesterol",
    "VLDL Cholesterol": "vldl_cholesterol",
    "Blood Urea": "urea",
    "Blood Urea Nitrogen (BUN)": "bun",
    "Serum Creatinine": "creatinine",
    "Uric Acid": "uric_acid",
    "eGFR": "egfr",
    "Sodium": "sodium",
    "Potassium": "potassium",
    "Chloride": "chloride",
    "Serum Iron": "iron",
    "TIBC": "tibc",
    "Transferrin Saturation": "transferrin_saturation",
    "Serum Ferritin": "ferritin",
    "Total Bilirubin": "bilirubin_total",
    "Direct Bilirubin": "bilirubin_direct",
    "SGOT (AST)": "ast",
    "SGPT (ALT)": "alt",
    "Alkaline Phosphatase": "alp",
    "Total Protein": "protein_total",
    "Albumin": "albumin",
    "T3 (Total)": "t3_total",
    "T4 (Total)": "t4_total",
    "TSH": "tsh",
    "Vitamin D (25-OH)": "vitamin_d_25oh",
    "CRP": "crp",
    "Total PSA": "psa_total",
}


# -------------------------------------------------------------------
# Shared pieces
# -------------------------------------------------------------------
def fixed(m: float, n: float, ni: float) -> dict:
    """Expected schedule for a 'm-n-ni' frequency."""
    return {
        "morning": m,
        "noon": n,
        "night": ni,
        "times_per_day": sum(1 for x in (m, n, ni) if x),
    }


def cumm(value: float, printed_unit: str) -> dict:
    """Expected conversion of a count to /cumm, the canonical count unit."""
    return {"value": value, "unit": "/cumm", "from_unit": printed_unit}


TIMES = {"OD": {"times_per_day": 1}, "BD": {"times_per_day": 2}, "TDS": {"times_per_day": 3}}  # fmt: skip
SOS = {"as_needed": True}

HB = "Haemoglobin"
HBA1C_RANGES = (
    Range("Normal: < 5.7", high=5.7, label="Normal"),
    Range("Prediabetes: 5.7 - 6.4", low=5.7, high=6.4, label="Prediabetes"),
    Range("Diabetes: >= 6.5", low=6.5, label="Diabetes"),
)
CHOL_RANGES = (
    Range("Desirable: < 200", high=200, label="Desirable"),
    Range("Borderline: 200 - 239", low=200, high=239, label="Borderline"),
    Range("High: >= 240", low=240, label="High"),
)
LDL_RANGES = (
    Range("Optimal: < 100", high=100, label="Optimal"),
    Range("Near optimal: 100 - 129", low=100, high=129, label="Near optimal"),
    Range("Borderline high: 130 - 159", low=130, high=159, label="Borderline high"),
    Range("High: >= 160", low=160, label="High"),
)
VITD_RANGES = (
    Range("Deficiency: < 20", high=20, label="Deficiency"),
    Range("Insufficiency: 20 - 30", low=20, high=30, label="Insufficiency"),
    Range("Sufficiency: 30 - 100", low=30, high=100, label="Sufficiency"),
)


def age_sex(text: str, age: str, sex: str) -> Line:
    """One printed line holding both age and sex."""
    return Line(
        text, [HeaderField("patient.age", age), HeaderField("patient.sex", sex)]
    )


def clinician(text: str) -> Line:
    return Line(text, [HeaderField("details.clinicians", text)])


def facility_title(name: str) -> Title:
    return Title(name, [HeaderField("details.facility", name)])


def running_header(name: str, lab_id: str, page: int, pages: int) -> Line:
    return Line(f"Patient: {name} | Lab ID: {lab_id} | Page {page} of {pages}")


def end_of_report(pathologist: str) -> list:
    return [clinician(pathologist), Line("*** End of Report ***")]


# Sunrise Diagnostics: separate Flag column, one header field per line.
def sunrise_header(p: Patient, name: str, lab_id: str, ref: str,
                   collected: tuple[str, date], reported: tuple[str, date]) -> list:  # fmt: skip
    age = f"{p.age(reported[1])} Years"
    sex = {"F": "Female", "M": "Male"}[p.sex]
    return [
        facility_title("Sunrise Diagnostics"),
        Line("Plot 12, Baner Road, Pune 411045"),
        header("Patient Name", "patient.name", name),
        age_sex(f"Age / Sex: {age} / {sex}", age, sex),
        header("Lab ID", "patient.patient_id", lab_id),
        clinician(f"Ref. By: {ref}"),
        header("Collected On", "details.collection_date", *collected),
        header("Reported On", "details.report_date", *reported),
    ]


# Kaveri Clinical Laboratory: flag printed next to the result.
def kaveri_header(name: str, age: str, sex: str, patient_id: str, ref: str,
                  dated: tuple[str, str, date]) -> list:  # fmt: skip
    label, text, on = dated
    path = "details.collection_date" if "Collected" in label else "details.report_date"
    return [
        facility_title("Kaveri Clinical Laboratory"),
        Line("Kothrud, Pune 411038"),
        header("Name", "patient.name", name),
        age_sex(f"Age/Gender: {age}/{sex}", age, sex),
        header("Patient ID", "patient.patient_id", patient_id),
        clinician(f"Referred By: {ref}"),
        header(label, path, text, on),
    ]


HOSPITAL = "Sahaj Multispeciality Hospital"


def hospital_lab_header(p: Patient, ordering: str, collected: tuple[str, date],
                        reported: tuple[str, date]) -> list:  # fmt: skip
    age = f"{p.age(reported[1])} Years"
    sex = {"F": "Female", "M": "Male"}[p.sex]
    title = "Mr." if p.sex == "M" else "Ms."
    return [
        facility_title(f"{HOSPITAL} - Laboratory Services"),
        Line("Karve Road, Pune 411004"),
        header("Patient Name", "patient.name", f"{title} {p.name}"),
        age_sex(f"Age / Sex: {age} / {sex}", age, sex),
        header("UHID", "patient.patient_id", p.mrn),
        clinician(f"Ordering Physician: {ordering}"),
        header("Sample Collected", "details.collection_date", *collected),
        header("Report Date", "details.report_date", *reported),
    ]


DOCTORS = {
    "deshpande": (
        "Dr. Anil Deshpande",
        "MBBS, MD (General Medicine)",
        "Reg. No.: MMC-2009/04/0001",
    ),  # fmt: skip
    "rao": (
        "Dr. Kavita Rao",
        "MBBS, DM (Endocrinology)",
        "Reg. No.: MMC-2012/07/0002",
    ),  # fmt: skip
}


def rx_header(doctor: str, p: Patient | None, dated: tuple[str, date] | None,
              patient_line: str | None = None) -> list:  # fmt: skip
    """Prescription letterhead and patient lines. `dated=None` prints no date."""
    name, degree, reg = DOCTORS[doctor]
    blocks = [
        Title(name, [HeaderField("details.clinicians", name)]),
        Line(degree),
        Line(reg),
        Line(
            f"{HOSPITAL}, Karve Road, Pune", [HeaderField("details.facility", HOSPITAL)]
        ),
    ]
    if dated:
        blocks.append(header("Date", "details.prescription_date", *dated))
    on = dated[1] if dated else date(2025, 4, 1)
    age, sex = f"{p.age(on)} Y", p.sex
    blocks += [
        header("Patient", "patient.name", patient_line or p.name),
        age_sex(f"Age / Sex: {age} / {sex}", age, sex),
        header("UHID", "patient.patient_id", p.mrn),
    ]
    return blocks


def signature(doctor: str) -> Line:
    return Line(f"Signature: {DOCTORS[doctor][0]}")


# -------------------------------------------------------------------
# Meera Iyer: diabetes, hypertension, iron-deficiency anaemia
# -------------------------------------------------------------------
MEERA = BY_KEY["meera"]

d01 = Doc(
    "d01", "meera", "lab_report", "pdf", "a4",
    cases=["panel_remark_after_last_row", "multiple_ranges", "one_sided_range",
           "indian_digit_grouping", "test_remark", "non_numeric_values",
           "multi_page"],
    notes="Health check-up. The CBC comment follows the table whose last row is "
          "the WBC count; it belongs to the panel, not to WBC (bug 1).",
    pages=[
        [
            *sunrise_header(MEERA, "Mrs. Meera Iyer", "SD-24-061407", "Dr. Anil Deshpande",
                            ("14/06/2024 08:10 AM", date(2024, 6, 14)),
                            ("14/06/2024 02:45 PM", date(2024, 6, 14))),
            *lab_panel("COMPLETE BLOOD COUNT", [
                LabTest(HB, "10.4", "g/dL", (rng("12.0 - 15.0"),), "L"),
                LabTest("RBC Count", "4.1", "mill/cumm", (rng("3.8 - 4.8"),)),
                LabTest("PCV", "33.2", "%", (rng("36.0 - 46.0"),), "L"),
                LabTest("MCV", "76", "fL", (rng("83 - 101"),), "L"),
                LabTest("MCH", "25.4", "pg", (rng("27.0 - 32.0"),), "L"),
                LabTest("MCHC", "31.3", "g/dL", (rng("31.5 - 34.5"),), "L"),
                LabTest("RDW-CV", "16.2", "%", (rng("11.6 - 14.0"),), "H"),
                LabTest("Platelet Count", "2,45,000", "/cumm",
                        (Range("1,50,000 - 4,10,000", 150000, 410000),), num=245000),
                LabTest("Total WBC Count", "7,200", "/cumm",
                        (Range("4,000 - 10,000", 4000, 10000),), num=7200),
            ]),
            remark_line(Remark("Microcytic hypochromic anaemia.", "comment", "panel",
                               panel="COMPLETE BLOOD COUNT"), "Comment"),
        ],
        [
            running_header("Mrs. Meera Iyer", "SD-24-061407", 2, 3),
            *lab_panel("GLYCATED HAEMOGLOBIN", [
                LabTest("HbA1c", "8.1", "%", HBA1C_RANGES, "H"),
            ]),
            remark_line(Remark("HbA1c reflects average blood glucose over the past 2-3 months.",
                               "comment", "test", test_name="HbA1c",
                               panel="GLYCATED HAEMOGLOBIN"), "Interpretation"),
            *lab_panel("GLUCOSE", [
                LabTest("Fasting Blood Sugar", "148", "mg/dL", (rng("70 - 100"),), "H"),
            ]),
            Line("Method: Hexokinase"),
            *lab_panel("LIPID PROFILE", [
                LabTest("Total Cholesterol", "182", "mg/dL", CHOL_RANGES),
                LabTest("Triglycerides", "168", "mg/dL", (Range("< 150", high=150),), "H"),
                LabTest("HDL Cholesterol", "44", "mg/dL", (Range("> 50", low=50),), "L"),
                LabTest("LDL Cholesterol", "104", "mg/dL", LDL_RANGES),
                LabTest("VLDL Cholesterol", "33.6", "mg/dL", (Range("< 30", high=30),), "H"),
            ]),
        ],
        [
            running_header("Mrs. Meera Iyer", "SD-24-061407", 3, 3),
            *lab_panel("KIDNEY FUNCTION TEST", [
                LabTest("Blood Urea", "28", "mg/dL", (rng("15 - 40"),)),
                LabTest("Blood Urea Nitrogen (BUN)", "13.1", "mg/dL", (rng("7 - 20"),)),
                LabTest("Serum Creatinine", "0.9", "mg/dL", (rng("0.6 - 1.1"),)),
                LabTest("Uric Acid", "5.2", "mg/dL", (rng("2.6 - 6.0"),)),
            ]),
            *lab_panel("URINE ROUTINE", [
                LabTest("Colour", "Pale yellow", None, ()),
                LabTest("Appearance", "Clear", None, ()),
                LabTest("Specific Gravity", "1.020", None, (rng("1.005 - 1.030"),)),
                LabTest("Protein", "Nil", None, (Range("Nil"),)),
                LabTest("Glucose", "Trace", None, (Range("Nil"),), "*"),
                LabTest("Pus Cells", "2-4", "/hpf", (rng("0 - 5"),)),
            ]),
            *end_of_report("Dr. Neha Joshi, MD (Pathology)"),
        ],
    ],
)  # fmt: skip

d02 = Doc(
    "d02", "meera", "prescription", "png", "a5",
    cases=["vitals", "no_known_allergies", "history", "two_diagnoses_one_line",
           "medicine_without_strength", "unused_complaint_line"],
    notes="First visit after the June labs.",
    pages=[[
        *rx_header("deshpande", MEERA, ("20/06/2024", date(2024, 6, 20))),
        vitals_line([
            Vital("BP", "142/88", "mmHg", "blood_pressure", systolic=142, diastolic=88),
            Vital("Pulse", "82", "/min", "pulse", 82),
            Vital("Weight", "68", "kg", "weight", 68),
            Vital("SpO2", "98", "%", "spo2", 98),
        ]),
        allergy_line("Allergies", Allergy("NKDA", no_known_allergies=True)),
        Line("C/O: Fatigue and increased thirst for 2 months"),
        conditions_line("K/C/O", [Condition("Hypertension", "history")]),
        conditions_line("Diagnosis", [Condition("Type 2 diabetes mellitus", "diagnosis"),
                                      Condition("Microcytic anaemia", "diagnosis")]),
        Heading("Rx"),
        rx_table([
            Medicine("Tab.", "Glycomet", "500 mg", "1 tab", "1-0-1", "30 days", "After food",
                     schedule=fixed(1, 0, 1), duration_days=30),
            Medicine("Tab.", "Telma", "40 mg", "1 tab", "1-0-0", "30 days", "Before breakfast",
                     schedule=fixed(1, 0, 0), duration_days=30),
            Medicine("Tab.", "Livogen", None, "1 tab", "0-1-0", "30 days", "After lunch",
                     schedule=fixed(0, 1, 0), duration_days=30),
        ]),
        remark_line(Remark("Diet control and 30 minutes of brisk walking daily.",
                           "advice", "document"), "Advice"),
        remark_line(Remark("Review after 3 months with HbA1c and FBS.", "advice", "document")),
        signature("deshpande"),
    ]],
)  # fmt: skip

d03 = Doc(
    "d03", "meera", "lab_report", "scan_pdf", "a4",
    cases=["image_only_pdf", "flag_beside_result", "no_report_date",
           "document_remark_after_single_test", "test_remark", "multiple_ranges",
           "multi_page"],
    notes="A different lab, printed date only for collection. 'Suggest iron studies' "
          "is a pathologist's note under its own heading after the HbA1c section; it "
          "belongs to the document, not to HbA1c (bug 1).",
    pages=[
        [
            *kaveri_header("Meera Iyer", "57 Y", "F", "KCL-118842", "Dr. Anil Deshpande",
                           ("Sample Collected", "16-Dec-2024", date(2024, 12, 16))),
            *lab_panel("HAEMATOLOGY", [
                LabTest(HB, "11.2", "g/dL", (rng("12.0 - 15.0"),), "L"),
                LabTest("Total Leucocyte Count", "6.8", "10³/µL", (rng("4.0 - 10.0"),),
                        converted=cumm(6800, "10³/µL")),
                LabTest("Platelets", "2.31", "lakh/cumm", (rng("1.50 - 4.10"),),
                        converted=cumm(231000, "lakh/cumm")),
                LabTest("MCV", "79", "fL", (rng("83 - 101"),), "L"),
            ], flag_column=False),
        ],
        [
            running_header("Meera Iyer", "KCL-118842", 2, 2),
            *lab_panel("GLYCATED HAEMOGLOBIN", [
                LabTest("HbA1c", "7.4", "%", HBA1C_RANGES, "H"),
            ], flag_column=False),
            remark_line(Remark("Reflects average glycaemia over the preceding 8-12 weeks.",
                               "comment", "test", test_name="HbA1c",
                               panel="GLYCATED HAEMOGLOBIN"), "Interpretation"),
            *lab_panel("GLUCOSE", [
                LabTest("Fasting Plasma Glucose", "132", "mg/dL", (rng("70 - 100"),), "H"),
            ], flag_column=False),
            Heading("PATHOLOGIST'S REMARKS"),
            remark_line(Remark("Suggest iron studies to evaluate microcytosis.", "advice",
                               "document")),
            *end_of_report("Dr. Prakash Nair, MD (Pathology)"),
        ],
    ],
)  # fmt: skip

d04 = Doc(
    "d04", "meera", "prescription", "jpg", "a5",
    cases=["jpeg", "as_needed", "duration_in_months", "impression", "history",
           "uploaded_before_older_documents"],
    notes="Uploaded first although it is dated after the 2024 labs (bug 5: "
          "upload order is not document order).",
    pages=[[
        *rx_header("rao", MEERA, ("10/01/2025", date(2025, 1, 10))),
        vitals_line([
            Vital("BP", "136/84", "mmHg", "blood_pressure", systolic=136, diastolic=84),
            Vital("Pulse", "76", "/min", "pulse", 76),
            Vital("Weight", "66.5", "kg", "weight", 66.5),
        ]),
        allergy_line("Allergies", Allergy("NKDA", no_known_allergies=True)),
        conditions_line("K/C/O", [Condition("T2DM", "history"), Condition("HTN", "history")]),
        conditions_line("Impression", [Condition("Iron deficiency anaemia", "impression")]),
        Heading("Rx"),
        rx_table([
            Medicine("Tab.", "Glycomet GP 1", None, "1 tab", "1-0-1", "3 months", "Before food",
                     schedule=fixed(1, 0, 1)),
            Medicine("Tab.", "Telma", "40 mg", "1 tab", "1-0-0", "3 months", None,
                     schedule=fixed(1, 0, 0)),
            Medicine("Tab.", "Orofer XT", None, "1 tab", "BD", "3 months", "After food",
                     schedule=TIMES["BD"]),
            Medicine("Tab.", "Pan", "40 mg", "1 tab", "1-0-0", "15 days", "Before breakfast",
                     schedule=fixed(1, 0, 0), duration_days=15),
            Medicine("Tab.", "Dolo", "650 mg", "1 tab", "SOS", "5 days", "For fever",
                     schedule=SOS, duration_days=5),
        ]),
        remark_line(Remark("Repeat CBC, iron studies and HbA1c after 3 months.", "advice",
                           "document"), "Advice"),
        signature("rao"),
    ]],
)  # fmt: skip

d05 = Doc(
    "d05", "meera", "lab_report", "pdf", "a4",
    cases=["panel_remark_after_last_row", "trend_two_values", "multi_page"],
    notes="Follow-up: second Haemoglobin, HbA1c and creatinine for trends.",
    pages=[
        [
            *sunrise_header(MEERA, "Mrs. Meera Iyer", "SD-25-041288", "Dr. Kavita Rao",
                            ("12/04/2025 07:55 AM", date(2025, 4, 12)),
                            ("12/04/2025 01:10 PM", date(2025, 4, 12))),
            *lab_panel("COMPLETE BLOOD COUNT", [
                LabTest(HB, "11.9", "g/dL", (rng("12.0 - 15.0"),), "L"),
                LabTest("MCV", "81", "fL", (rng("83 - 101"),), "L"),
                LabTest("Platelet Count", "2,62,000", "/cumm",
                        (Range("1,50,000 - 4,10,000", 150000, 410000),), num=262000),
            ]),
            *lab_panel("IRON STUDIES", [
                LabTest("Serum Iron", "48", "µg/dL", (rng("50 - 170"),), "L"),
                LabTest("TIBC", "412", "µg/dL", (rng("250 - 400"),), "H"),
                LabTest("Transferrin Saturation", "11.7", "%", (rng("20 - 50"),), "L"),
                LabTest("Serum Ferritin", "9.8", "ng/mL", (rng("13 - 150"),), "L"),
            ]),
            remark_line(Remark("Iron deficiency picture.", "comment", "panel",
                               panel="IRON STUDIES"), "Comment"),
        ],
        [
            running_header("Mrs. Meera Iyer", "SD-25-041288", 2, 2),
            *lab_panel("GLYCATED HAEMOGLOBIN", [
                LabTest("HbA1c", "7.0", "%", HBA1C_RANGES, "H"),
            ]),
            *lab_panel("KIDNEY FUNCTION TEST", [
                LabTest("Serum Creatinine", "0.9", "mg/dL", (rng("0.6 - 1.1"),)),
            ]),
            *end_of_report("Dr. Neha Joshi, MD (Pathology)"),
        ],
    ],
)  # fmt: skip

# -------------------------------------------------------------------
# Rohan Kulkarni: one raised creatinine, statin without a lipid panel
# -------------------------------------------------------------------
ROHAN = BY_KEY["rohan"]

d06 = Doc(
    "d06", "rohan", "lab_report", "pdf", "a4",
    cases=["single_creatinine", "bun_in_kidney_panel", "one_sided_range",
           "document_remark", "multi_page"],
    notes="The only creatinine this patient has: one raised value must not become "
          "'acute kidney injury' or a trend (bugs 2, 3). BUN sits in the kidney "
          "panel (bug 6).",
    pages=[
        [
            *hospital_lab_header(ROHAN, "Dr. Anil Deshpande",
                                 ("03/02/2025 09:00 AM", date(2025, 2, 3)),
                                 ("03/02/2025 01:20 PM", date(2025, 2, 3))),
            *lab_panel("KIDNEY FUNCTION TEST", [
                LabTest("Blood Urea", "46", "mg/dL", (rng("15 - 40"),), "H"),
                LabTest("Blood Urea Nitrogen (BUN)", "21.5", "mg/dL", (rng("7 - 20"),), "H"),
                LabTest("Serum Creatinine", "1.6", "mg/dL", (rng("0.7 - 1.3"),), "H"),
                LabTest("eGFR", "54", "mL/min/1.73m²", (Range("> 60", low=60),), "L"),
                LabTest("Uric Acid", "7.8", "mg/dL", (rng("3.5 - 7.2"),), "H"),
            ]),
            *lab_panel("SERUM ELECTROLYTES", [
                LabTest("Sodium", "138", "mmol/L", (rng("135 - 145"),)),
                LabTest("Potassium", "4.6", "mmol/L", (rng("3.5 - 5.1"),)),
                LabTest("Chloride", "101", "mmol/L", (rng("98 - 107"),)),
            ]),
        ],
        [
            Line(f"Patient: Mr. {ROHAN.name} | UHID: {ROHAN.mrn} | Page 2 of 2"),
            *lab_panel("GLUCOSE", [
                LabTest("Random Blood Sugar", "112", "mg/dL", (rng("70 - 140"),)),
            ]),
            Heading("REMARKS"),
            remark_line(Remark("Kindly correlate clinically.", "comment", "document")),
            *end_of_report("Dr. Neha Joshi, MD (Pathology)"),
        ],
    ],
)  # fmt: skip

d07 = Doc(
    "d07", "rohan", "prescription", "png", "a5",
    cases=["statin_without_lipid_panel", "drug_allergy", "provisional_diagnosis",
           "bmi_and_height", "duration_in_months"],
    notes="Atorvastatin is prescribed but the patient has no lipid panel: the "
          "summary must not invent a lipid status (bug 7).",
    pages=[[
        *rx_header("deshpande", ROHAN, ("10/02/2025", date(2025, 2, 10))),
        vitals_line([
            Vital("BP", "148/94", "mmHg", "blood_pressure", systolic=148, diastolic=94),
            Vital("Pulse", "88", "/min", "pulse", 88),
            Vital("Weight", "84", "kg", "weight", 84),
            Vital("Height", "172", "cm", "height", 172),
            Vital("BMI", "28.4", "kg/m²", "bmi", 28.4),
        ]),
        allergy_line("Drug Allergy", Allergy("Sulfa drugs (rash)", "Sulfa drugs", "rash")),
        Line("C/O: Occasional headache"),
        conditions_line("Diagnosis", [Condition("Hypertension", "diagnosis")]),
        conditions_line("Provisional Diagnosis",
                        [Condition("Chronic kidney disease, to be evaluated",
                                   "provisional_diagnosis")]),
        Heading("Rx"),
        rx_table([
            Medicine("Tab.", "Amlong", "5 mg", "1 tab", "1-0-0", "1 month", None,
                     schedule=fixed(1, 0, 0)),
            Medicine("Tab.", "Atorva", "20 mg", "1 tab", "0-0-1", "1 month", "At bedtime",
                     schedule=fixed(0, 0, 1)),
            Medicine("Tab.", "Pantocid", "40 mg", "1 tab", "1-0-0", "14 days",
                     "Before breakfast", schedule=fixed(1, 0, 0), duration_days=14),
        ]),
        remark_line(Remark("Low salt diet.", "advice", "document"), "Advice"),
        remark_line(Remark("Repeat KFT after 4 weeks.", "advice", "document")),
        signature("deshpande"),
    ]],
)  # fmt: skip

d08 = Doc(
    "d08", "rohan", "lab_report", "scan_pdf", "a4",
    cases=["image_only_pdf", "flag_beside_result", "less_than_value", "one_sided_range"],
    notes="Liver and thyroid tests; no creatinine, so d06's stays the only one.",
    pages=[[
        *kaveri_header("Rohan Kulkarni", "44 Y", "M", "KCL-120051", "Dr. Anil Deshpande",
                       ("Reported", "20-May-2025 11:30 AM", date(2025, 5, 20))),
        *lab_panel("LIVER FUNCTION TEST", [
            LabTest("Total Bilirubin", "0.8", "mg/dL", (rng("0.2 - 1.2"),)),
            LabTest("Direct Bilirubin", "0.2", "mg/dL", (rng("0.0 - 0.3"),)),
            LabTest("SGOT (AST)", "32", "U/L", (rng("5 - 40"),)),
            LabTest("SGPT (ALT)", "48", "U/L", (rng("7 - 45"),), "H"),
            LabTest("Alkaline Phosphatase", "96", "U/L", (rng("40 - 129"),)),
            LabTest("Total Protein", "7.1", "g/dL", (rng("6.4 - 8.3"),)),
            LabTest("Albumin", "4.2", "g/dL", (rng("3.5 - 5.2"),)),
        ], flag_column=False),
        *lab_panel("THYROID", [
            LabTest("TSH", "2.4", "µIU/mL", (rng("0.27 - 4.20"),)),
        ], flag_column=False),
        *lab_panel("INFLAMMATORY MARKERS", [
            LabTest("CRP", "< 0.5", "mg/L", (Range("< 5.0", high=5.0),)),
        ], flag_column=False),
        *end_of_report("Dr. Prakash Nair, MD (Pathology)"),
    ]],
)  # fmt: skip

# -------------------------------------------------------------------
# Fatima Shaikh: hypothyroidism, vitamin D deficiency
# -------------------------------------------------------------------
FATIMA = BY_KEY["fatima"]

d09 = Doc(
    "d09", "fatima", "lab_report", "pdf", "a4",
    cases=["panel_remark", "trend_two_values"],
    notes="First TSH (raised).",
    pages=[[
        *sunrise_header(FATIMA, "Ms. Fatima Shaikh", "SD-24-090512", "Dr. Kavita Rao",
                        ("05/09/2024 08:30 AM", date(2024, 9, 5)),
                        ("05/09/2024 04:00 PM", date(2024, 9, 5))),
        *lab_panel("THYROID PROFILE", [
            LabTest("T3 (Total)", "1.1", "ng/mL", (rng("0.8 - 2.0"),)),
            LabTest("T4 (Total)", "6.2", "µg/dL", (rng("5.1 - 14.1"),)),
            LabTest("TSH", "8.9", "µIU/mL", (rng("0.27 - 4.20"),), "H"),
        ]),
        remark_line(Remark("TSH values may vary with the time of sample collection.",
                           "comment", "panel", panel="THYROID PROFILE"), "Note"),
        *lab_panel("HAEMOGRAM", [
            LabTest(HB, "12.6", "g/dL", (rng("12.0 - 15.0"),)),
            LabTest("Total WBC Count", "6,400", "/cumm",
                    (Range("4,000 - 10,000", 4000, 10000),), num=6400),
            LabTest("Platelet Count", "2,80,000", "/cumm",
                    (Range("1,50,000 - 4,10,000", 150000, 410000),), num=280000),
        ]),
        *end_of_report("Dr. Neha Joshi, MD (Pathology)"),
    ]],
)  # fmt: skip

d10 = Doc(
    "d10", "fatima", "prescription", "png", "a5",
    cases=["drug_allergy", "duration_in_weeks", "temperature"],
    notes="Started on levothyroxine after d09.",
    pages=[[
        *rx_header("rao", FATIMA, ("09/09/2024", date(2024, 9, 9))),
        vitals_line([
            Vital("BP", "118/76", "mmHg", "blood_pressure", systolic=118, diastolic=76),
            Vital("Pulse", "72", "/min", "pulse", 72),
            Vital("Weight", "61", "kg", "weight", 61),
            Vital("Temp", "98.4", "°F", "temperature", 98.4),
        ]),
        allergy_line("Allergies", Allergy("Penicillin - hives", "Penicillin", "hives")),
        conditions_line("Diagnosis", [Condition("Hypothyroidism", "diagnosis")]),
        Heading("Rx"),
        rx_table([
            Medicine("Tab.", "Thyronorm", "50 mcg", "1 tab", "1-0-0", "6 weeks",
                     "Empty stomach, 30 min before breakfast",
                     schedule=fixed(1, 0, 0), duration_days=42),
        ]),
        remark_line(Remark("Repeat TSH after 6 weeks.", "advice", "document"), "Advice"),
        signature("rao"),
    ]],
)  # fmt: skip

d11 = Doc(
    "d11", "fatima", "lab_report", "pdf", "a4",
    cases=["trend_two_values", "multiple_ranges", "printed_flag_without_computed_flag"],
    notes="Second TSH (now in range; 'falling' is allowed, 'improving' is not). "
          "Vitamin D has a printed L but several ranges, so no computed flag.",
    pages=[[
        *sunrise_header(FATIMA, "Ms. Fatima Shaikh", "SD-25-010733", "Dr. Kavita Rao",
                        ("08/01/2025 08:20 AM", date(2025, 1, 8)),
                        ("08/01/2025 06:15 PM", date(2025, 1, 8))),
        *lab_panel("THYROID PROFILE", [
            LabTest("TSH", "3.1", "µIU/mL", (rng("0.27 - 4.20"),)),
        ]),
        *lab_panel("VITAMINS", [
            LabTest("Vitamin D (25-OH)", "18.4", "ng/mL", VITD_RANGES, "L"),
        ]),
        *end_of_report("Dr. Neha Joshi, MD (Pathology)"),
    ]],
)  # fmt: skip

d12 = Doc(
    "d12", "fatima", "prescription", "png", "a5",
    cases=["weekly_frequency", "duration_in_weeks", "uploaded_before_older_documents"],
    notes="A weekly dose has no daily schedule. Uploaded first (bug 5).",
    pages=[[
        *rx_header("rao", FATIMA, ("15/03/2025", date(2025, 3, 15))),
        vitals_line([
            Vital("BP", "116/74", "mmHg", "blood_pressure", systolic=116, diastolic=74),
            Vital("Weight", "60", "kg", "weight", 60),
        ]),
        allergy_line("Allergies", Allergy("Penicillin - hives", "Penicillin", "hives")),
        conditions_line("Diagnosis", [Condition("Hypothyroidism", "diagnosis"),
                                      Condition("Vitamin D deficiency", "diagnosis")]),
        Heading("Rx"),
        rx_table([
            Medicine("Tab.", "Thyronorm", "50 mcg", "1 tab", "1-0-0", "3 months",
                     "Empty stomach", schedule=fixed(1, 0, 0)),
            Medicine("Cap.", "Uprise-D3", "60K", "1 cap", "Once a week", "8 weeks",
                     "After food", duration_days=56),
        ]),
        remark_line(Remark("Repeat TSH and vitamin D after 3 months.", "advice",
                           "document"), "Advice"),
        signature("rao"),
    ]],
)  # fmt: skip

# -------------------------------------------------------------------
# Arjun Menon: dyslipidaemia, CAD; an undated note and a wrong-patient report
# -------------------------------------------------------------------
ARJUN = BY_KEY["arjun"]

d13 = Doc(
    "d13", "arjun", "lab_report", "pdf", "a4",
    cases=["multiple_ranges", "one_sided_range", "non_numeric_values", "multi_page"],
    notes="Lipid profile, HbA1c in the prediabetes band (no flag printed), urine.",
    pages=[
        [
            *hospital_lab_header(ARJUN, "Dr. Anil Deshpande",
                                 ("04/03/2025 08:45 AM", date(2025, 3, 4)),
                                 ("04/03/2025 03:30 PM", date(2025, 3, 4))),
            *lab_panel("LIPID PROFILE", [
                LabTest("Total Cholesterol", "236", "mg/dL", CHOL_RANGES, "H"),
                LabTest("Triglycerides", "210", "mg/dL", (Range("< 150", high=150),), "H"),
                LabTest("HDL Cholesterol", "38", "mg/dL", (Range("> 40", low=40),), "L"),
                LabTest("LDL Cholesterol", "156", "mg/dL", LDL_RANGES, "H"),
            ]),
            *lab_panel("GLYCATED HAEMOGLOBIN", [
                LabTest("HbA1c", "6.2", "%", HBA1C_RANGES),
            ]),
        ],
        [
            Line(f"Patient: Mr. {ARJUN.name} | UHID: {ARJUN.mrn} | Page 2 of 2"),
            *lab_panel("URINE ROUTINE", [
                LabTest("Urine Sugar", "Absent", None, (Range("Absent"),)),
                LabTest("Urine Protein", "Trace", None, (Range("Absent"),), "*"),
                LabTest("Pus Cells", "4-6", "/hpf", (rng("0 - 5"),), "H"),
            ]),
            *lab_panel("SERUM PSA", [
                LabTest("Total PSA", "3.1", "ng/mL", (Range("< 4.0", high=4.0),)),
            ]),
            *end_of_report("Dr. Neha Joshi, MD (Pathology)"),
        ],
    ],
)  # fmt: skip

d14 = Doc(
    "d14", "arjun", "prescription", "png", "a5",
    cases=["generic_name", "history", "no_known_allergies"],
    notes="Statin with a lipid panel on record (contrast with d07).",
    pages=[[
        *rx_header("deshpande", ARJUN, ("11/03/2025", date(2025, 3, 11))),
        vitals_line([
            Vital("BP", "130/80", "mmHg", "blood_pressure", systolic=130, diastolic=80),
            Vital("Pulse", "70", "/min", "pulse", 70),
            Vital("SpO2", "97", "%", "spo2", 97),
        ]),
        allergy_line("Allergies", Allergy("NKDA", no_known_allergies=True)),
        conditions_line("K/C/O", [Condition("CAD, post PTCA (2019)", "history")]),
        conditions_line("Diagnosis", [Condition("Dyslipidaemia", "diagnosis")]),
        Heading("Rx"),
        rx_table([
            Medicine("Tab.", "Rosuvas", "10 mg", "1 tab", "0-0-1", "3 months", "At bedtime",
                     schedule=fixed(0, 0, 1)),
            Medicine("Tab.", "Ecosprin", "75 mg", "1 tab", "0-1-0", "3 months", "After lunch",
                     schedule=fixed(0, 1, 0)),
            Medicine("Tab.", "Metformin", "500 mg", "1 tab", "OD", "3 months", "With dinner",
                     schedule=TIMES["OD"]),
        ]),
        remark_line(Remark("Low fat diet; avoid fried food.", "advice", "document"), "Advice"),
        remark_line(Remark("Repeat lipid profile after 3 months.", "advice", "document")),
        signature("deshpande"),
    ]],
)  # fmt: skip

d15 = Doc(
    "d15", "arjun", "prescription", "png", "a5",
    cases=["undated", "duration_not_given"],
    expected_signals=["LC-12"],
    notes="No date anywhere: the document is undated and is never used for "
          "ordering or trends.",
    pages=[[
        *rx_header("deshpande", ARJUN, None),
        Line("Medication card (continue as advised)"),
        Heading("Rx"),
        rx_table([
            Medicine("Tab.", "Rosuvas", "10 mg", "1 tab", "0-0-1", "Continue", None,
                     schedule=fixed(0, 0, 1)),
            Medicine("Tab.", "Ecosprin", "75 mg", "1 tab", "0-1-0", "Continue", None,
                     schedule=fixed(0, 1, 0)),
        ]),
        signature("deshpande"),
    ]],
)  # fmt: skip

d16 = Doc(
    "d16", "arjun", "lab_report", "scan_pdf", "a4",
    cases=["patient_mismatch", "image_only_pdf", "flag_beside_result"],
    expected_signals=["LC-13"],
    notes="Uploaded to Arjun Menon but printed for a different person (name, age "
          "and ID differ): the patient-match check must flag it.",
    pages=[[
        *kaveri_header("Arjun Mehta", "41 Y", "M", "KCL-120377", "Dr. S. Kulkarni",
                       ("Reported", "02-Apr-2025 10:05 AM", date(2025, 4, 2))),
        *lab_panel("HAEMATOLOGY", [
            LabTest(HB, "14.8", "g/dL", (rng("13.0 - 17.0"),)),
            LabTest("Total Leucocyte Count", "7.9", "10³/µL", (rng("4.0 - 10.0"),),
                    converted=cumm(7900, "10³/µL")),
            LabTest("Platelets", "2.65", "lakh/cumm", (rng("1.50 - 4.10"),),
                    converted=cumm(265000, "lakh/cumm")),
        ], flag_column=False),
        *end_of_report("Dr. Prakash Nair, MD (Pathology)"),
    ]],
)  # fmt: skip

d17 = Doc(
    "d17", "arjun", "other", "pdf", "a4",
    cases=["classified_other"],
    expected_signals=["LC-14"],
    notes="A chest X-ray report: not a supported type, so it is classified other, "
          "not parsed, and goes to needs_review.",
    pages=[[
        Title(f"{HOSPITAL} - Department of Radiology"),
        Line("Karve Road, Pune 411004"),
        Line(f"Patient Name: Mr. {ARJUN.name}"),
        Line(f"Age / Sex: {ARJUN.age(date(2025, 4, 20))} Years / Male"),
        Line(f"UHID: {ARJUN.mrn}"),
        Line("Date: 20/04/2025"),
        Heading("X-RAY CHEST PA VIEW"),
        Line("Findings: Both lung fields are clear. Cardiac silhouette is within normal limits."),
        Line("Costophrenic angles are clear. Bony thorax appears normal."),
        Line("Impression: No active lung lesion."),
        Line("Dr. R. Pillai, MD (Radiodiagnosis)"),
    ]],
)  # fmt: skip

# -------------------------------------------------------------------
# Held-out documents (d18-d24): two more patients, printed in layouts the
# documents above don't use: results as lines rather than a table, header
# details in a key-value table or several to a line, the range before the
# unit, flags as words or arrows, two columns, numbered medicine lines, the
# strength in its own column, a table continued on the next page, dates
# with the month in words. Never tune a prompt or a check on these; they
# are reported apart (split held_out).
# -------------------------------------------------------------------
KABIR = BY_KEY["kabir"]
NANDINI = BY_KEY["nandini"]
HEARTLINE = "Heartline Cardiac Clinic"


def doctor_letterhead(facility: str, name: str, degree: str, address: str) -> list:
    return [
        Title(facility, [HeaderField("details.facility", facility)]),
        Line(name, [HeaderField("details.clinicians", name)]),
        Line(degree),
        Line(address),
    ]


def kabir_rx_patient(on: date) -> Line:
    age = str(KABIR.age(on))
    return fields_line(
        f"Name: {KABIR.name} | Age/Sex: {age}/M | UHID: {KABIR.mrn}",
        HeaderField("patient.name", KABIR.name),
        HeaderField("patient.age", age),
        HeaderField("patient.sex", "M"),
        HeaderField("patient.patient_id", KABIR.mrn),
    )


d18 = Doc(
    "d18", "kabir", "lab_report", "pdf", "a4",
    cases=["results_as_lines", "header_fields_on_one_line", "flag_as_word",
           "date_month_in_words", "test_name_variant", "multiple_ranges",
           "one_sided_range", "document_remark"],
    notes="Held out. Results printed one per line with the range in brackets, no "
          "table; name, age and sex on one line; dates as '03 Mar 2025'.",
    pages=[[
        Title("Navjeevan Pathology Lab", [HeaderField("details.facility",
                                                      "Navjeevan Pathology Lab")]),
        Line("Shop 4, Aundh Road, Pune 411007"),
        fields_line("Patient: Mr. Kabir Sethi | Age: 45 Yrs | Sex: Male",
                    HeaderField("patient.name", "Mr. Kabir Sethi"),
                    HeaderField("patient.age", "45 Yrs"),
                    HeaderField("patient.sex", "Male")),
        fields_line("Lab No: NJ/2025/03114 | Ref: Dr. Farah Khan",
                    HeaderField("patient.patient_id", "NJ/2025/03114"),
                    HeaderField("details.clinicians", "Ref: Dr. Farah Khan")),
        fields_line("Sample Date: 03 Mar 2025 | Report Date: 03 Mar 2025",
                    HeaderField("details.collection_date", "03 Mar 2025",
                                date(2025, 3, 3)),
                    HeaderField("details.report_date", "03 Mar 2025", date(2025, 3, 3))),
        *lab_lines("LIPID PROFILE", [
            LabTest("Total Cholesterol", "238", "mg/dL", CHOL_RANGES),
            LabTest("Triglycerides", "210", "mg/dL", (Range("< 150", high=150),), "High"),
            LabTest("HDL Cholesterol", "36", "mg/dL", (Range("> 40", low=40),), "Low"),
            LabTest("LDL Cholesterol", "160", "mg/dL", LDL_RANGES, "High"),
        ]),
        *lab_lines("DIABETES SCREEN", [
            LabTest("Fasting Plasma Glucose", "112", "mg/dL", (rng("70 - 100"),),
                    "High"),
            LabTest("Glycated Haemoglobin (HbA1c)", "6.1", "%", HBA1C_RANGES,
                    analyte="hba1c"),
        ]),
        remark_line(Remark("Repeat lipid profile after 3 months on treatment.",
                           "advice", "document"), "Advice"),
        clinician("Dr. Prakash Menon, MD (Pathology)"),
        Line("-- End of report --"),
    ]],
)  # fmt: skip

d19 = Doc(
    "d19", "kabir", "prescription", "png", "a5",
    cases=["medicines_as_numbered_lines", "header_fields_on_one_line",
           "two_diagnoses_one_line", "as_needed", "route_given",
           "no_known_allergies", "vitals"],
    notes="Held out. A clinic letterhead, the patient's details on one line, "
          "and medicines as numbered lines instead of a table.",
    pages=[[
        *doctor_letterhead(HEARTLINE, "Dr. Farah Khan", "MBBS, MD, DM (Cardiology)",
                           "Baner, Pune 411045 | Reg. No.: MMC-2011/02/0003"),
        header("Date", "details.prescription_date", "05-03-2025", date(2025, 3, 5)),
        kabir_rx_patient(date(2025, 3, 5)),
        Line("BP: 148/94 mmHg | Pulse: 88/min | SpO2: 97%", [
            Vital("BP", "148/94", "mmHg", "blood_pressure", systolic=148, diastolic=94),
            Vital("Pulse", "88", "/min", "pulse", 88),
            Vital("SpO2", "97", "%", "spo2", 97),
        ]),
        Line("Dx: Essential hypertension; Dyslipidaemia", [
            Condition("Essential hypertension", "diagnosis"),
            Condition("Dyslipidaemia", "diagnosis"),
        ]),
        allergy_line("Allergy", Allergy("None known", no_known_allergies=True)),
        Heading("Rx"),
        *rx_lines([
            Medicine("Tab.", "Telma", "40 mg", "1 tab", "1-0-0", "x 30 days",
                     "After breakfast", schedule=fixed(1, 0, 0), duration_days=30),
            Medicine("Tab.", "Rosuvas", "10 mg", "1 tab", "0-0-1", "x 30 days",
                     "After dinner", schedule=fixed(0, 0, 1), duration_days=30),
            Medicine("Tab.", "Ecosprin", "75 mg", "1 tab", "0-1-0", "x 30 days",
                     "After lunch", schedule=fixed(0, 1, 0), duration_days=30),
            Medicine("Tab.", "Sorbitrate", "5 mg", "1 tab", "SOS", None,
                     "If chest pain", route="sublingual", schedule=SOS),
        ]),
        remark_line(Remark("Review after 4 weeks with lipid profile.", "advice",
                           "document"), "Advice"),
        Line("Signature: Dr. Farah Khan"),
    ]],
)  # fmt: skip

d20 = Doc(
    "d20", "nandini", "lab_report", "pdf", "a4",
    cases=["header_as_table", "range_before_unit", "flag_as_word",
           "test_name_variant", "counts_in_other_units", "document_remark",
           "one_sided_range"],
    notes="Held out. Patient details in a key-value table; results as "
          "Investigation | Observed Value | Flag | Biological Reference Interval | "
          "Units.",
    pages=[[
        Title("Medilink Diagnostics", [HeaderField("details.facility",
                                                   "Medilink Diagnostics")]),
        Line("FC Road, Shivajinagar, Pune 411005"),
        header_table([
            [("Patient Name", HeaderField("patient.name", "Ms. Nandini Bose"),
              "Ms. Nandini Bose"),
             ("Sample ID", HeaderField("patient.patient_id", "MD-250412-077"),
              "MD-250412-077")],
            [("Age / Gender", [HeaderField("patient.age", "36 Years"),
                               HeaderField("patient.sex", "Female")], "36 Years / Female"),
             ("Referred By", HeaderField("details.clinicians", "Dr. Suresh Pillai"),
              "Dr. Suresh Pillai")],
            [("Collected", HeaderField("details.collection_date", "12/04/2025 09:05",
                                       date(2025, 4, 12)), "12/04/2025 09:05"),
             ("Reported", HeaderField("details.report_date", "12/04/2025 17:40",
                                      date(2025, 4, 12)), "12/04/2025 17:40")],
        ]),
        *lab_table_ranges_first("THYROID FUNCTION TEST", [
            LabTest("Free T4", "0.82", "ng/dL", (rng("0.70 - 1.48"),), analyte=None),
            LabTest("TSH (Ultrasensitive)", "6.8", "µIU/mL", (rng("0.35 - 4.94"),),
                    "High", analyte="tsh"),
            LabTest("Anti-TPO Antibodies", "142", "IU/mL", (Range("< 34", high=34),),
                    "High", analyte=None),
        ]),
        *lab_table_ranges_first("COMPLETE BLOOD COUNT", [
            LabTest(HB, "11.1", "g/dL", (rng("12.0 - 15.0"),), "Low"),
            LabTest("MCV", "78", "fL", (rng("83 - 101"),), "Low"),
            LabTest("Total Leucocyte Count", "8.2", "10³/µL", (rng("4.0 - 10.0"),),
                    converted=cumm(8200, "10³/µL")),
            LabTest("Platelets", "3.10", "lakh/cumm", (rng("1.50 - 4.10"),),
                    converted=cumm(310000, "lakh/cumm")),
        ]),
        Heading("INTERPRETATION"),
        remark_line(Remark("Raised TSH with positive anti-TPO antibodies suggests "
                           "autoimmune thyroiditis; clinical correlation advised.",
                           "comment", "document")),
        clinician("Dr. Ritu Saxena, MD (Pathology)"),
    ]],
)  # fmt: skip

d21 = Doc(
    "d21", "nandini", "prescription", "jpg", "a4",
    cases=["two_column_layout", "header_fields_on_one_line", "weekly_frequency",
           "duration_in_weeks", "history", "unused_complaint_line",
           "date_with_dots"],
    notes="Held out. Clinical notes in the left column, the medicines in the "
          "right; the transcription reads the left column first.",
    pages=[[
        *doctor_letterhead("Pillai Family Clinic", "Dr. Suresh Pillai",
                           "MBBS, MD (Medicine)",
                           "Model Colony, Pune 411016 | Reg. No.: MMC-2007/11/0004"),
        fields_line("Patient: Mrs. Nandini Bose, 36 F, UHID SH-100236 | Date: 15.04.2025",
                    HeaderField("patient.name", "Mrs. Nandini Bose"),
                    HeaderField("patient.age", "36"),
                    HeaderField("patient.sex", "F"),
                    HeaderField("patient.patient_id", "SH-100236"),
                    HeaderField("details.prescription_date", "15.04.2025",
                                date(2025, 4, 15))),
        Columns(
            left=[
                Heading("Clinical notes"),
                Line("C/O: tiredness, weight gain x 3 months"),
                Line("BP: 122/80 mmHg | Pulse: 64/min | Wt: 71 kg", [
                    Vital("BP", "122/80", "mmHg", "blood_pressure", systolic=122,
                          diastolic=80),
                    Vital("Pulse", "64", "/min", "pulse", 64),
                    Vital("Wt", "71", "kg", "weight", 71),
                ]),
                conditions_line("H/o", [Condition("PCOS", "history")]),
                conditions_line("Diagnosis", [
                    Condition("Hashimoto's thyroiditis", "diagnosis")]),
            ],
            right=[
                Heading("Rx"),
                *rx_lines([
                    Medicine("Tab.", "Thyronorm", "25 mcg", "1 tab", "1-0-0", "8 weeks",
                             "Empty stomach", schedule=fixed(1, 0, 0),
                             duration_days=56),
                    Medicine("Sachet", "Calcirol", "60,000 IU", "1 sachet",
                             "Once a week", "8 weeks", "With milk", duration_days=56),
                    Medicine("Tab.", "Folvite", "5 mg", "1 tab", "0-1-0", "8 weeks",
                             None, schedule=fixed(0, 1, 0), duration_days=56),
                ]),
            ],
        ),
        remark_line(Remark("Repeat TSH after 8 weeks.", "advice", "document"),
                    "Advice"),
        Line("Signature: Dr. Suresh Pillai"),
    ]],
)  # fmt: skip

d22 = Doc(
    "d22", "kabir", "lab_report", "png", "a4",
    cases=["lab_report_as_image", "flag_as_arrow", "header_fields_on_one_line",
           "iso_date", "test_remark", "one_sided_range"],
    notes="Held out. A lab report uploaded as a picture (no text layer), flags as "
          "arrows after the value, dates as 2025-03-20.",
    pages=[[
        Title("CityCare Labs", [HeaderField("details.facility", "CityCare Labs")]),
        Line("Wakad, Pune 411057"),
        fields_line("Name: Mr. Kabir Sethi | Age/Sex: 46Y/M",
                    HeaderField("patient.name", "Mr. Kabir Sethi"),
                    HeaderField("patient.age", "46Y"),
                    HeaderField("patient.sex", "M")),
        fields_line("Reg. No: CC-88412 | Referred by: Dr. Farah Khan",
                    HeaderField("patient.patient_id", "CC-88412"),
                    HeaderField("details.clinicians", "Referred by: Dr. Farah Khan")),
        fields_line("Collected: 2025-03-20 | Reported: 2025-03-20",
                    HeaderField("details.collection_date", "2025-03-20",
                                date(2025, 3, 20)),
                    HeaderField("details.report_date", "2025-03-20", date(2025, 3, 20))),
        *lab_table_flag_in_value("RENAL FUNCTION", [
            LabTest("Blood Urea", "44", "mg/dL", (rng("15 - 40"),), "↑"),
            LabTest("Serum Creatinine", "1.4", "mg/dL", (rng("0.7 - 1.3"),), "↑"),
        ]),
        *lab_table_flag_in_value("eGFR", [
            LabTest("eGFR", "62", "mL/min/1.73m²", (Range("> 90", low=90),), "↓"),
        ]),
        remark_line(Remark("Calculated by the CKD-EPI 2021 equation.", "comment",
                           "test", test_name="eGFR", panel="eGFR"), "Note"),
        *lab_table_flag_in_value("ELECTROLYTES", [
            LabTest("Sodium", "139", "mmol/L", (rng("135 - 145"),)),
            LabTest("Potassium", "4.6", "mmol/L", (rng("3.5 - 5.1"),)),
            LabTest("Chloride", "101", "mmol/L", (rng("98 - 107"),)),
        ]),
        clinician("Dr. Imran Shaikh, DCP"),
    ]],
)  # fmt: skip

d23 = Doc(
    "d23", "nandini", "lab_report", "scan_pdf", "a4",
    cases=["table_continued_on_next_page", "image_only_pdf", "range_before_unit",
           "trend_two_values", "flag_as_word", "multi_page"],
    notes="Held out. A scan whose haematology table runs onto page 2 under "
          "'HAEMATOLOGY (contd.)'; the rows there still belong to HAEMATOLOGY. "
          "Second TSH for Nandini (after d20).",
    pages=[
        [
            Title("Apex Pathology", [HeaderField("details.facility", "Apex Pathology")]),
            Line("Kalyani Nagar, Pune 411006"),
            fields_line("Patient: Mrs. Nandini Bose | 36 Yrs / F | ID: AP-61930",
                        HeaderField("patient.name", "Mrs. Nandini Bose"),
                        HeaderField("patient.age", "36 Yrs"),
                        HeaderField("patient.sex", "F"),
                        HeaderField("patient.patient_id", "AP-61930")),
            clinician("Ref. Doctor: Dr. Suresh Pillai"),
            header("Report Date", "details.report_date", "18/06/2025",
                   date(2025, 6, 18)),
            *lab_table_ranges_first("HAEMATOLOGY", [
                LabTest(HB, "11.9", "g/dL", (rng("12.0 - 15.0"),), "Low"),
                LabTest("PCV", "36.8", "%", (rng("36.0 - 46.0"),)),
                LabTest("RBC Count", "4.3", "mill/cumm", (rng("3.8 - 4.8"),)),
                LabTest("MCV", "82", "fL", (rng("83 - 101"),), "Low"),
                LabTest("MCH", "27.6", "pg", (rng("27.0 - 32.0"),)),
            ]),
        ],
        [
            Line("Apex Pathology | Mrs. Nandini Bose | AP-61930 | Page 2 of 2"),
            *lab_table_ranges_first("HAEMATOLOGY (contd.)", [
                LabTest("Total WBC Count", "7,600", "/cumm",
                        (Range("4,000 - 10,000", 4000, 10000),), num=7600),
                LabTest("Platelet Count", "2,95,000", "/cumm",
                        (Range("1,50,000 - 4,10,000", 150000, 410000),), num=295000),
            ], panel="HAEMATOLOGY"),
            *lab_table_ranges_first("THYROID PROFILE", [
                LabTest("TSH", "2.9", "µIU/mL", (rng("0.35 - 4.94"),)),
                LabTest("Vitamin B12", "180", "pg/mL", (rng("211 - 911"),), "Low",
                        analyte=None),
            ]),
            clinician("Dr. Kiran Patwardhan, MD (Pathology)"),
        ],
    ],
)  # fmt: skip

d24 = Doc(
    "d24", "kabir", "prescription", "pdf", "a4",
    cases=["e_prescription_text_layer", "strength_in_own_column",
           "date_month_in_words", "named_frequency", "duration_in_days",
           "unused_date_line", "vitals"],
    notes="Held out. A typed e-prescription with a text layer; the strength in its "
          "own column, dates with the month in words, and a 'Next visit' date "
          "that is not the prescription date.",
    pages=[[
        *doctor_letterhead(HEARTLINE, "Dr. Farah Khan", "MBBS, MD, DM (Cardiology)",
                           "Baner, Pune 411045 | Reg. No.: MMC-2011/02/0003"),
        header("Date", "details.prescription_date", "14 April 2025", date(2025, 4, 14)),
        kabir_rx_patient(date(2025, 4, 14)),
        Line("BP: 132/84 mmHg; HR: 76 bpm", [
            Vital("BP", "132/84", "mmHg", "blood_pressure", systolic=132, diastolic=84),
            Vital("HR", "76", "bpm", "pulse", 76),
        ]),
        conditions_line("Impression", [
            Condition("Hypertension, better controlled", "impression")]),
        Heading("Medicines"),
        rx_table_strength([
            Medicine("Tab.", "Telma-AM", "40/5 mg", None, "OD", "30 days",
                     "Morning", schedule=TIMES["OD"], duration_days=30),
            Medicine("Tab.", "Rosuvas", "20 mg", None, "OD", "30 days", "Night",
                     schedule=TIMES["OD"], duration_days=30),
            Medicine("Tab.", "Glycomet", "500 mg", None, "BD", "30 days",
                     "After food", schedule=TIMES["BD"], duration_days=30),
            Medicine("Cap.", "Omez", "20 mg", None, "OD", "14 days",
                     "Before breakfast", schedule=TIMES["OD"], duration_days=14),
        ]),
        remark_line(Remark("Low-salt diet; walk 30 minutes a day.", "advice",
                           "document"), "Advice"),
        Line("Next visit: 14 May 2025"),
        Line("This is a computer-generated prescription."),
    ]],
)  # fmt: skip

# -------------------------------------------------------------------
# Arjun Menon again: a 60-row full-body check-up six months after d13
# -------------------------------------------------------------------
# The page that ends d25: the laboratory's test menu, printed small. Its
# transcription runs past the 4,096-token cap on a page, so the page is
# read again in two halves (top and bottom). Prose can't do it at a size
# a model still reads: measured live with qwen3.5:4b, a full page of notes
# in 4.6-point print (18,700 characters) took 3,769 output tokens, and the
# model dropped about a sixth of it. A table costs far more tokens per
# printed character (cell separators, codes, numbers), and its rows are
# separate lines that come out the same in both halves, which the join
# needs. The header has no result column, so it is not a results table.
METHODS = [
    (
        "HAEMATOLOGY",
        [
            (
                "Haemoglobin",
                "SLS-haemoglobin photometry on an automated five-part cell counter",
                "lower in pregnancy and after recent blood loss; higher at altitude and in smokers",
            ),
            (
                "RBC Count",
                "electrical impedance with hydrodynamic focusing",
                "read with haemoglobin and the red cell indices, never alone",
            ),
            (
                "PCV",
                "calculated from the RBC count and the MCV",
                "rises with dehydration and falls with over-hydration, without any change in red cell mass",
            ),
            (
                "MCV",
                "electrical impedance, from the red cell volume histogram",
                "low values suggest iron deficiency or thalassaemia trait; high values B12 or folate deficiency",
            ),
            (
                "MCH",
                "calculated from haemoglobin and the RBC count",
                "follows the MCV; a low MCH with a normal MCV is unusual and is checked on a smear",
            ),
            (
                "MCHC",
                "calculated from haemoglobin and the PCV",
                "a high MCHC can be an artefact of lipaemia or cold agglutinins and is then repeated on a warmed sample",
            ),
            (
                "RDW-CV",
                "calculated from the red cell volume histogram",
                "a raised RDW shows red cells of mixed sizes, as in early iron deficiency or after a transfusion",
            ),
            (
                "Platelet Count",
                "electrical impedance, confirmed on a stained smear when outside the reference interval",
                "clumping in EDTA can give a falsely low count; a citrate sample is then requested",
            ),
            (
                "Total WBC Count",
                "flow cytometry with fluorescent nucleic acid staining",
                "raised after exercise, stress, smoking and steroids; nucleated red cells are counted out",
            ),
            (
                "Neutrophils",
                "flow cytometry (five-part differential); a smear is reviewed when the analyser flags",
                "rise with bacterial infection, stress and steroids",
            ),
            (
                "Lymphocytes",
                "flow cytometry (five-part differential)",
                "rise with viral infections; reactive lymphocytes are reported from the smear",
            ),
            (
                "Eosinophils",
                "flow cytometry (five-part differential)",
                "rise with allergy, asthma and parasitic infection",
            ),
            (
                "Monocytes",
                "flow cytometry (five-part differential)",
                "rise in chronic infection and in recovery from an acute one",
            ),
            (
                "Basophils",
                "flow cytometry (five-part differential)",
                "rarely raised; a persistent rise is referred for haematology review",
            ),
            (
                "ESR",
                "modified Westergren method, read at one hour at room temperature",
                "rises with age, anaemia and pregnancy as well as inflammation; not specific to any disease",
            ),
        ],
    ),
    (
        "GLUCOSE AND LIPIDS",
        [
            (
                "Fasting Plasma Glucose",
                "hexokinase, enzymatic UV end point",
                "needs 8 to 10 hours without food; glucose falls in a sample left unseparated, so fluoride tubes are used",
            ),
            (
                "HbA1c",
                "ion-exchange HPLC, NGSP certified and IFCC standardised",
                "reflects the past two to three months; falsely low after blood loss or transfusion and with some haemoglobin variants",
            ),
            (
                "Total Cholesterol",
                "cholesterol oxidase-peroxidase (CHOD-PAP), enzymatic colorimetric",
                "changes little with a meal; acute illness lowers it for some weeks",
            ),
            (
                "Triglycerides",
                "glycerol phosphate oxidase-peroxidase (GPO-PAP), with glycerol blanking",
                "rises after food and alcohol; a 12-hour fast is advised for a lipid profile",
            ),
            (
                "HDL Cholesterol",
                "direct homogeneous enzymatic method",
                "lower in smokers and with obesity and inactivity; regular exercise raises it",
            ),
            (
                "LDL Cholesterol",
                "calculated by the Friedewald equation when triglycerides are below 400 mg/dL",
                "measured directly when triglycerides are 400 mg/dL or more",
            ),
            (
                "VLDL Cholesterol",
                "calculated as triglycerides divided by five",
                "not valid when triglycerides are 400 mg/dL or more",
            ),
        ],
    ),
    (
        "KIDNEY FUNCTION TEST",
        [
            (
                "Blood Urea",
                "urease-glutamate dehydrogenase, kinetic UV",
                "rises with a high-protein diet, dehydration and gastrointestinal bleeding, as well as with kidney disease",
            ),
            (
                "Blood Urea Nitrogen (BUN)",
                "calculated as urea divided by 2.14",
                "the same measurement as urea, expressed as its nitrogen",
            ),
            (
                "Serum Creatinine",
                "modified kinetic Jaffe method, traceable to IDMS",
                "depends on muscle mass; a recent large meat meal and some drugs raise it without any change in kidney function",
            ),
            (
                "Uric Acid",
                "uricase-peroxidase, enzymatic colorimetric",
                "rises with alcohol, a purine-rich diet, diuretics and fasting",
            ),
            (
                "eGFR",
                "calculated by the CKD-EPI 2021 creatinine equation, without a race term",
                "an estimate for adults with stable creatinine; not valid in acute illness, pregnancy or extremes of muscle mass",
            ),
        ],
    ),
    (
        "LIVER FUNCTION TEST",
        [
            (
                "Total Bilirubin",
                "diazo method with an accelerator",
                "rises with fasting and in Gilbert syndrome, a harmless inherited condition",
            ),
            (
                "Direct Bilirubin",
                "diazo method without an accelerator",
                "the conjugated fraction; rises when bile flow is obstructed",
            ),
            (
                "Indirect Bilirubin",
                "calculated as total minus direct bilirubin",
                "the unconjugated fraction; rises with red cell breakdown and in Gilbert syndrome",
            ),
            (
                "SGOT (AST)",
                "IFCC method without pyridoxal phosphate, kinetic UV",
                "also present in muscle and red cells; raised after strenuous exercise and in a haemolysed sample",
            ),
            (
                "SGPT (ALT)",
                "IFCC method without pyridoxal phosphate, kinetic UV",
                "more specific to the liver than AST; raised in fatty liver and with some drugs",
            ),
            (
                "Alkaline Phosphatase",
                "IFCC method, AMP buffer, kinetic colorimetric",
                "higher in growing children, in pregnancy and with bone disease",
            ),
            (
                "GGT",
                "IFCC method (Szasz), kinetic colorimetric",
                "raised by alcohol and by enzyme-inducing drugs; used to tell a liver from a bone source of ALP",
            ),
            (
                "Total Protein",
                "biuret method, end point",
                "rises slightly with prolonged tourniquet use and dehydration",
            ),
            (
                "Albumin",
                "bromocresol green, end point",
                "falls in acute illness, liver disease and protein loss through the kidneys or gut",
            ),
            (
                "Globulin",
                "calculated as total protein minus albumin",
                "a high globulin is followed up by protein electrophoresis",
            ),
            (
                "A/G Ratio",
                "calculated as albumin divided by globulin",
                "a ratio, with no unit",
            ),
        ],
    ),
    (
        "ELECTROLYTES AND MINERALS",
        [
            (
                "Sodium",
                "indirect ion-selective electrode",
                "falsely low in very lipaemic or high-protein samples measured by an indirect method",
            ),
            (
                "Potassium",
                "indirect ion-selective electrode",
                "falsely high in a haemolysed sample or one kept cold before separation",
            ),
            (
                "Chloride",
                "indirect ion-selective electrode",
                "moves with sodium in most conditions",
            ),
            (
                "Magnesium",
                "xylidyl blue, colorimetric end point",
                "falls with diuretics, proton pump inhibitors and alcohol",
            ),
            (
                "Calcium",
                "arsenazo III, colorimetric end point",
                "total calcium falls with albumin; a corrected or ionised calcium is advised when albumin is low",
            ),
            (
                "Phosphorus",
                "phosphomolybdate, UV end point",
                "rises after meals and in a haemolysed sample",
            ),
        ],
    ),
    (
        "SPECIAL TESTS",
        [
            (
                "T3 (Total)",
                "chemiluminescent immunoassay (CLIA)",
                "changes with binding proteins in pregnancy and with oral contraceptives",
            ),
            (
                "T4 (Total)",
                "chemiluminescent immunoassay (CLIA)",
                "changes with binding proteins; free T4 is advised when they may be abnormal",
            ),
            (
                "TSH",
                "third-generation chemiluminescent immunoassay (CLIA)",
                "varies through the day, highest at night; biotin supplements can interfere and are stopped two days before",
            ),
            (
                "Serum Iron",
                "ferrozine method, without deproteinisation",
                "varies widely through the day and after iron tablets; read with TIBC and ferritin",
            ),
            (
                "TIBC",
                "direct method with magnesium carbonate adsorption",
                "rises in iron deficiency and in pregnancy",
            ),
            (
                "Transferrin Saturation",
                "calculated as serum iron divided by TIBC, times 100",
                "low in iron deficiency; high values are followed up for iron overload",
            ),
            (
                "Serum Ferritin",
                "particle-enhanced immunoturbidimetric assay",
                "the best single marker of iron stores, but raised by inflammation and liver disease",
            ),
            (
                "Vitamin D (25-OH)",
                "chemiluminescent immunoassay (CLIA), total 25-OH vitamin D2 and D3",
                "lower in winter and with little time outdoors; cut-offs follow the Endocrine Society guideline",
            ),
            (
                "Vitamin B12",
                "competitive chemiluminescent immunoassay (CLIA)",
                "may be low with metformin and long-term acid-suppressing drugs; a borderline value can be checked with other markers",
            ),
            (
                "Total PSA",
                "chemiluminescent immunoassay (CLIA)",
                "rises after ejaculation, cycling and prostate examination; a 48-hour gap is advised before the sample",
            ),
        ],
    ),
    (
        "URINE ROUTINE",
        [
            (
                "Colour",
                "visual examination",
                "varies with fluid intake, diet and some medicines",
            ),
            (
                "Appearance",
                "visual examination",
                "cloudiness may come from crystals in a sample left standing",
            ),
            (
                "Specific Gravity",
                "reagent strip (ion exchange)",
                "reflects how concentrated the urine is",
            ),
            (
                "Protein",
                "reagent strip, confirmed by sulphosalicylic acid when positive",
                "a trace can follow exercise or fever",
            ),
            (
                "Glucose",
                "reagent strip (glucose oxidase), confirmed by Benedict's test when positive",
                "appears when blood glucose exceeds the kidney threshold",
            ),
            (
                "Pus Cells",
                "microscopy of the centrifuged deposit, per high-power field",
                "a few are normal; more than five per field suggests infection or contamination",
            ),
        ],
    ),
]
GENERAL_NOTES = [
    "All results relate only to the sample as received. Reference intervals are those of this laboratory for adults and may differ from other laboratories.",
    "A result outside its reference interval is not by itself a disease; about one healthy person in twenty falls outside any single interval.",
    "Results are to be interpreted by a registered medical practitioner, together with the history, the examination and other investigations.",
    "Values flagged H or L lie above or below the printed reference interval. Critical values are telephoned to the referring doctor.",
]


# Tests the laboratory offers beyond d25's: (name, section, sample, method).
OTHER_TESTS = [
    (
        "Prothrombin Time (PT/INR)",
        "COAGULATION",
        "Citrate (blue)",
        "clotting time, photo-optical",
    ),
    ("aPTT", "COAGULATION", "Citrate (blue)", "clotting time, photo-optical"),
    ("Fibrinogen", "COAGULATION", "Citrate (blue)", "Clauss method"),
    ("D-Dimer", "COAGULATION", "Citrate (blue)", "latex immunoturbidimetry"),
    (
        "Blood Group and Rh Type",
        "HAEMATOLOGY",
        "EDTA (lavender)",
        "column agglutination",
    ),
    (
        "Peripheral Smear Examination",
        "HAEMATOLOGY",
        "EDTA (lavender)",
        "Leishman stain, microscopy",
    ),
    ("Reticulocyte Count", "HAEMATOLOGY", "EDTA (lavender)", "flow cytometry"),
    ("Malaria Antigen", "HAEMATOLOGY", "EDTA (lavender)", "immunochromatography"),
    (
        "Post-prandial Plasma Glucose",
        "GLUCOSE AND LIPIDS",
        "Fluoride (grey)",
        "hexokinase",
    ),
    ("Random Plasma Glucose", "GLUCOSE AND LIPIDS", "Fluoride (grey)", "hexokinase"),
    (
        "Oral Glucose Tolerance Test",
        "GLUCOSE AND LIPIDS",
        "Fluoride (grey)",
        "hexokinase, 0 and 120 min",
    ),
    ("Apolipoprotein B", "GLUCOSE AND LIPIDS", "Serum (gold)", "immunoturbidimetry"),
    ("Lipoprotein (a)", "GLUCOSE AND LIPIDS", "Serum (gold)", "immunoturbidimetry"),
    (
        "Cystatin C",
        "KIDNEY FUNCTION TEST",
        "Serum (gold)",
        "particle-enhanced immunoturbidimetry",
    ),
    (
        "Urine Albumin/Creatinine Ratio",
        "KIDNEY FUNCTION TEST",
        "Spot urine",
        "immunoturbidimetry and Jaffe",
    ),
    (
        "24-hour Urine Protein",
        "KIDNEY FUNCTION TEST",
        "24-hour urine",
        "pyrogallol red",
    ),
    (
        "Serum Amylase",
        "LIVER FUNCTION TEST",
        "Serum (gold)",
        "CNPG3 substrate, kinetic",
    ),
    ("Serum Lipase", "LIVER FUNCTION TEST", "Serum (gold)", "colorimetric, kinetic"),
    ("LDH", "LIVER FUNCTION TEST", "Serum (gold)", "lactate to pyruvate, kinetic UV"),
    (
        "Serum Ammonia",
        "LIVER FUNCTION TEST",
        "EDTA on ice",
        "glutamate dehydrogenase, UV",
    ),
]
SECTION_CODE = {"HAEMATOLOGY": "HEM", "GLUCOSE AND LIPIDS": "GLU", "KIDNEY FUNCTION TEST": "KFT",
                "LIVER FUNCTION TEST": "LFT", "ELECTROLYTES AND MINERALS": "ELM",
                "SPECIAL TESTS": "SPL", "URINE ROUTINE": "URN", "COAGULATION": "COA",
                "SEROLOGY": "SER", "MICROBIOLOGY": "MIC"}  # fmt: skip
TUBE = {"HAEMATOLOGY": "EDTA (lavender)", "GLUCOSE AND LIPIDS": "Serum (gold)",
        "KIDNEY FUNCTION TEST": "Serum (gold)", "LIVER FUNCTION TEST": "Serum (gold)",
        "ELECTROLYTES AND MINERALS": "Serum (gold)", "SPECIAL TESTS": "Serum (gold)",
        "URINE ROUTINE": "Urine container"}  # fmt: skip
TUBE_OF_TEST = {"Fasting Plasma Glucose": "Fluoride (grey)", "HbA1c": "EDTA (lavender)"}
VOLUME = {"EDTA": "2 mL", "Serum": "3 mL", "Fluoride": "2 mL", "Citrate": "2.7 mL",
          "Heparin": "2 mL"}  # fmt: skip
TAT = {"HAEMATOLOGY": "4 h", "GLUCOSE AND LIPIDS": "Same day", "KIDNEY FUNCTION TEST": "6 h",
       "LIVER FUNCTION TEST": "6 h", "ELECTROLYTES AND MINERALS": "4 h",
       "SPECIAL TESTS": "Next day", "URINE ROUTINE": "2 h", "COAGULATION": "4 h",
       "SEROLOGY": "Next day", "MICROBIOLOGY": "48-72 h"}  # fmt: skip
STORE = {"EDTA": "24 h, 2-8 °C", "Serum": "7 d, 2-8 °C", "Fluoride": "24 h, 2-8 °C",
         "Citrate": "4 h, room temp.", "Heparin": "2 h, room temp."}  # fmt: skip
FASTING = {"GLUCOSE AND LIPIDS": "10-12 h", "LIVER FUNCTION TEST": "Preferred"}
RUN_DAYS = {
    "SPECIAL TESTS": "Mon-Sat",
    "SEROLOGY": "Mon-Sat",
    "MICROBIOLOGY": "Daily, read at 48 h",
}
DEPARTMENT = {"HAEMATOLOGY": "Haematology", "COAGULATION": "Haematology",
              "URINE ROUTINE": "Clinical Pathology", "SPECIAL TESTS": "Immunoassay",
              "SEROLOGY": "Serology", "MICROBIOLOGY": "Microbiology"}  # fmt: skip
NOT_ACCREDITED = {"SEROLOGY", "MICROBIOLOGY"}
BASE_PRICE = {"HAEMATOLOGY": 150, "GLUCOSE AND LIPIDS": 120, "KIDNEY FUNCTION TEST": 180,
              "LIVER FUNCTION TEST": 200, "ELECTROLYTES AND MINERALS": 220,
              "SPECIAL TESTS": 450, "URINE ROUTINE": 100, "COAGULATION": 350,
              "SEROLOGY": 400, "MICROBIOLOGY": 600}  # fmt: skip


def _short_method(method: str) -> str:
    """A method as the menu prints it: up to its first comma or "on an"."""
    return re.split(r",| on an ", method)[0]


def _menu_rows() -> list[Row]:
    entries = [(name, panel, TUBE_OF_TEST.get(name, TUBE[panel]), _short_method(method))
               for panel, tests in METHODS for name, method, _ in tests]  # fmt: skip
    entries += OTHER_TESTS
    rows, seen = [], {}
    for name, section, tube, method in entries:
        seen[section] = seen.get(section, 0) + 1
        kind = tube.split()[0]
        rows.append(Row([
            f"{SECTION_CODE[section]}-{seen[section]:03d}", name, tube,
            VOLUME.get(kind, "As supplied"), STORE.get(kind, "See note"), TAT[section],
            FASTING.get(section, "No"), RUN_DAYS.get(section, "Daily"),
            DEPARTMENT.get(section, "Clinical Biochemistry"),
            "No" if section in NOT_ACCREDITED else "Yes",
            f"{BASE_PRICE[section] + 30 * (seen[section] % 7)}", method,
        ]))  # fmt: skip
    return rows


TEST_MENU = Table(
    [
        "Code",
        "Test",
        "Sample",
        "Vol.",
        "Stability",
        "TAT",
        "Fasting",
        "Runs",
        "Department",
        "Accredited",
        "Rs",
        "Method",
    ],
    _menu_rows(),
    style="fine",
)
d25 = Doc(
    "d25", "arjun", "lab_report", "pdf", "a4",
    cases=["sixty_results", "page_parsed_in_parts", "dense_page_over_output_cap",
           "trend_two_values", "multiple_ranges", "non_numeric_values", "multi_page"],
    notes="Sixty results on three pages, 27 on each of the first two: more than "
          "one parse call reads, so those pages are parsed in parts cut between "
          "rows. The fourth page is the laboratory's test menu in small print, "
          "whose transcription runs past a page call's output cap, so it is read "
          "again in two halves. Lipids lower than d13's on the statin; HbA1c still in "
          "the prediabetes band.",
    pages=[
        [
            *sunrise_header(ARJUN, "Mr. Arjun Menon", "SD-25-092011", "Dr. Anil Deshpande",
                            ("20/09/2025 07:50 AM", date(2025, 9, 20)),
                            ("20/09/2025 04:10 PM", date(2025, 9, 20))),
            *lab_panel("HAEMATOLOGY", [
                LabTest(HB, "13.8", "g/dL", (rng("13.0 - 17.0"),)),
                LabTest("RBC Count", "4.7", "mill/cumm", (rng("4.5 - 5.5"),)),
                LabTest("PCV", "42.1", "%", (rng("40.0 - 50.0"),)),
                LabTest("MCV", "89.6", "fL", (rng("83 - 101"),)),
                LabTest("MCH", "29.4", "pg", (rng("27.0 - 32.0"),)),
                LabTest("MCHC", "32.8", "g/dL", (rng("31.5 - 34.5"),)),
                LabTest("RDW-CV", "13.1", "%", (rng("11.6 - 14.0"),)),
                LabTest("Platelet Count", "2,18,000", "/cumm",
                        (Range("1,50,000 - 4,10,000", 150000, 410000),), num=218000),
                LabTest("Total WBC Count", "8,300", "/cumm",
                        (Range("4,000 - 10,000", 4000, 10000),), num=8300),
                LabTest("Neutrophils", "61", "%", (rng("40 - 80"),)),
                LabTest("Lymphocytes", "29", "%", (rng("20 - 40"),)),
                LabTest("Eosinophils", "5", "%", (rng("1 - 6"),)),
                LabTest("Monocytes", "4", "%", (rng("2 - 10"),)),
                LabTest("Basophils", "1", "%", (rng("0 - 2"),)),
                LabTest("ESR", "12", "mm/hr", (rng("0 - 15"),)),
            ]),
            *lab_panel("GLUCOSE AND LIPIDS", [
                LabTest("Fasting Plasma Glucose", "108", "mg/dL", (rng("70 - 100"),), "H"),
                LabTest("HbA1c", "6.0", "%", HBA1C_RANGES),
                LabTest("Total Cholesterol", "168", "mg/dL", CHOL_RANGES),
                LabTest("Triglycerides", "142", "mg/dL", (Range("< 150", high=150),)),
                LabTest("HDL Cholesterol", "38", "mg/dL", (Range("> 40", low=40),), "L"),
                LabTest("LDL Cholesterol", "94", "mg/dL", LDL_RANGES),
                LabTest("VLDL Cholesterol", "28.4", "mg/dL", (Range("< 30", high=30),)),
            ]),
            *lab_panel("KIDNEY FUNCTION TEST", [
                LabTest("Blood Urea", "31", "mg/dL", (rng("15 - 40"),)),
                LabTest("Blood Urea Nitrogen (BUN)", "14.5", "mg/dL", (rng("7 - 20"),)),
                LabTest("Serum Creatinine", "1.0", "mg/dL", (rng("0.7 - 1.3"),)),
                LabTest("Uric Acid", "6.1", "mg/dL", (rng("3.5 - 7.2"),)),
                LabTest("eGFR", "84", "mL/min/1.73m²", (Range("> 60", low=60),)),
            ]),
        ],
        [
            running_header("Mr. Arjun Menon", "SD-25-092011", 2, 4),
            *lab_panel("LIVER FUNCTION TEST", [
                LabTest("Total Bilirubin", "0.9", "mg/dL", (rng("0.3 - 1.2"),)),
                LabTest("Direct Bilirubin", "0.2", "mg/dL", (Range("< 0.3", high=0.3),)),
                LabTest("Indirect Bilirubin", "0.7", "mg/dL", (rng("0.2 - 0.8"),)),
                LabTest("SGOT (AST)", "28", "U/L", (Range("< 40", high=40),)),
                LabTest("SGPT (ALT)", "34", "U/L", (Range("< 41", high=41),)),
                LabTest("Alkaline Phosphatase", "88", "U/L", (rng("40 - 129"),)),
                LabTest("GGT", "31", "U/L", (Range("< 60", high=60),)),
                LabTest("Total Protein", "7.3", "g/dL", (rng("6.4 - 8.3"),)),
                LabTest("Albumin", "4.4", "g/dL", (rng("3.5 - 5.2"),)),
                LabTest("Globulin", "2.9", "g/dL", (rng("2.0 - 3.5"),)),
                LabTest("A/G Ratio", "1.5", None, (rng("1.1 - 2.5"),)),
            ]),
            *lab_panel("ELECTROLYTES AND MINERALS", [
                LabTest("Sodium", "140", "mmol/L", (rng("135 - 145"),)),
                LabTest("Potassium", "4.2", "mmol/L", (rng("3.5 - 5.1"),)),
                LabTest("Chloride", "103", "mmol/L", (rng("98 - 107"),)),
                LabTest("Magnesium", "2.1", "mg/dL", (rng("1.6 - 2.6"),)),
                LabTest("Calcium", "9.4", "mg/dL", (rng("8.6 - 10.2"),)),
                LabTest("Phosphorus", "3.6", "mg/dL", (rng("2.5 - 4.5"),)),
            ]),
            *lab_panel("SPECIAL TESTS", [
                LabTest("T3 (Total)", "1.2", "ng/mL", (rng("0.8 - 2.0"),)),
                LabTest("T4 (Total)", "7.8", "µg/dL", (rng("5.1 - 14.1"),)),
                LabTest("TSH", "2.1", "µIU/mL", (rng("0.27 - 4.20"),)),
                LabTest("Serum Iron", "96", "µg/dL", (rng("65 - 175"),)),
                LabTest("TIBC", "318", "µg/dL", (rng("250 - 450"),)),
                LabTest("Transferrin Saturation", "30.2", "%", (rng("20 - 50"),)),
                LabTest("Serum Ferritin", "142", "ng/mL", (rng("30 - 400"),)),
                LabTest("Vitamin D (25-OH)", "34.2", "ng/mL", VITD_RANGES),
                LabTest("Vitamin B12", "412", "pg/mL", (rng("211 - 911"),)),
                LabTest("Total PSA", "1.4", "ng/mL", (Range("< 4.0", high=4.0),)),
            ]),
        ],
        [
            running_header("Mr. Arjun Menon", "SD-25-092011", 3, 4),
            *lab_panel("URINE ROUTINE", [
                LabTest("Colour", "Pale yellow", None, ()),
                LabTest("Appearance", "Clear", None, ()),
                LabTest("Specific Gravity", "1.015", None, (rng("1.005 - 1.030"),)),
                LabTest("Protein", "Nil", None, (Range("Nil"),)),
                LabTest("Glucose", "Nil", None, (Range("Nil"),)),
                LabTest("Pus Cells", "1-2", "/hpf", (rng("0 - 5"),)),
            ]),
        ],
        [
            running_header("Mr. Arjun Menon", "SD-25-092011", 4, 4),
            Heading("TEST MENU AND METHODS"),
            TEST_MENU,
            *[Line(note, style="fine") for note in GENERAL_NOTES],
            *end_of_report("Dr. Neha Joshi, MD (Pathology)"),
        ],
    ],
)  # fmt: skip

DOCUMENTS = [d01, d02, d03, d04, d05, d06, d07, d08, d09, d10, d11, d12, d13, d14, d15, d16, d17,
             d18, d19, d20, d21, d22, d23, d24, d25]  # fmt: skip

# The order the loader uploads in: deliberately not document-date order
# (bug 5), and patients interleaved.
UPLOAD_ORDER = ["d04", "d07", "d12", "d16", "d01", "d06", "d09", "d13", "d02", "d17",
                "d05", "d08", "d11", "d14", "d03", "d10", "d15",
                "d22", "d20", "d24", "d18", "d23", "d19", "d21", "d25"]  # fmt: skip

# Documents used while building and checking the prompts (every live check
# so far ran on d02 and d09): reported apart as dev, never as test results.
DEV_DOCUMENTS = ("d02", "d09")
# Documents in layouts the others don't use, for measuring only: never look
# at them while changing a prompt or a check.
HELD_OUT_DOCUMENTS = ("d18", "d19", "d20", "d21", "d22", "d23", "d24")
