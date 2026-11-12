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

from dataclasses import dataclass, field
from datetime import date

from dataset.layout import (
    Allergy,
    Condition,
    HeaderField,
    Heading,
    LabTest,
    Line,
    Medicine,
    Range,
    Remark,
    Title,
    Vital,
    allergy_line,
    conditions_line,
    header,
    lab_panel,
    remark_line,
    rng,
    rx_table,
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
    phone: str  # the development login (one per patient until Phase B)

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
    Patient("meera", "Meera Iyer", "F", date(1967, 4, 12), "SH-100231", "+919000000001"),
    Patient("rohan", "Rohan Kulkarni", "M", date(1980, 8, 3), "SH-100232", "+919000000002"),
    Patient("fatima", "Fatima Shaikh", "F", date(1993, 1, 25), "SH-100233", "+919000000003"),
    Patient("arjun", "Arjun Menon", "M", date(1958, 11, 30), "SH-100234", "+919000000004"),
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

DOCUMENTS = [d01, d02, d03, d04, d05, d06, d07, d08, d09, d10, d11, d12, d13, d14, d15, d16, d17]  # fmt: skip

# The order the loader uploads in: deliberately not document-date order
# (bug 5), and patients interleaved.
UPLOAD_ORDER = ["d04", "d07", "d12", "d16", "d01", "d06", "d09", "d13", "d02", "d17",
                "d05", "d08", "d11", "d14", "d03", "d10", "d15"]  # fmt: skip

# Documents used while building and checking the prompts (every live check
# so far ran on d02 and d09): reported apart as dev, never as test results.
DEV_DOCUMENTS = ("d02", "d09")
