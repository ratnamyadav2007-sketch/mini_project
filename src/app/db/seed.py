from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Food, RecordType, ReferenceRange

DEMO_WARNING = (
    "DEMO ONLY: illustrative data, not clinical guidance. Replace with a reviewed source "
    "and population-specific thresholds before clinical use."
)

RECORD_TYPES = (
    {
        "code": "systolic_blood_pressure",
        "display_name": "Systolic blood pressure",
        "default_unit": "mmHg",
    },
    {"code": "body_temperature", "display_name": "Body temperature", "default_unit": "°C"},
    {"code": "blood_glucose", "display_name": "Blood glucose", "default_unit": "mg/dL"},
    {"code": "condition", "display_name": "Condition"},
    {"code": "medication", "display_name": "Medication"},
    {"code": "allergy", "display_name": "Allergy"},
    {"code": "vaccination", "display_name": "Vaccination"},
    {"code": "lab", "display_name": "Laboratory"},
    {"code": "vital", "display_name": "Vital"},
    {"code": "surgery", "display_name": "Surgery"},
    {"code": "visit_note", "display_name": "Visit note"},
    {"code": "blood_group", "display_name": "Blood group"},
)

REFERENCE_RANGES = (
    {
        "record_type_code": "systolic_blood_pressure",
        "unit": "mmHg",
        "lower_bound": 90,
        "upper_bound": 120,
        "population": "demo adult example",
    },
    {
        "record_type_code": "body_temperature",
        "unit": "°C",
        "lower_bound": 36,
        "upper_bound": 38,
        "population": "demo example; measurement method unspecified",
    },
    {
        "record_type_code": "blood_glucose",
        "unit": "mg/dL",
        "lower_bound": 70,
        "upper_bound": 140,
        "population": "demo example; timing/context unspecified",
    },
)

FOODS = (
    {
        "name": "Oats, dry",
        "nutrients_per_100g": {
            "energy_kcal": 389,
            "protein_g": 16.9,
            "carbohydrate_g": 66.3,
            "fiber_g": 10.6,
            "fat_g": 6.9,
        },
        "allergens": ["gluten"],
    },
    {
        "name": "Lentils, cooked",
        "nutrients_per_100g": {
            "energy_kcal": 116,
            "protein_g": 9.0,
            "carbohydrate_g": 20.1,
            "fiber_g": 7.9,
            "fat_g": 0.4,
        },
        "allergens": [],
    },
    {
        "name": "Banana, raw",
        "nutrients_per_100g": {
            "energy_kcal": 89,
            "protein_g": 1.1,
            "carbohydrate_g": 22.8,
            "fiber_g": 2.6,
            "fat_g": 0.3,
        },
        "allergens": [],
    },
    {
        "name": "Brown rice, cooked",
        "nutrients_per_100g": {
            "energy_kcal": 123,
            "protein_g": 2.7,
            "carbohydrate_g": 25.6,
            "fiber_g": 1.6,
            "fat_g": 1.0,
        },
        "allergens": [],
    },
    {
        "name": "Egg, boiled",
        "nutrients_per_100g": {
            "energy_kcal": 155,
            "protein_g": 12.6,
            "carbohydrate_g": 1.1,
            "fiber_g": 0,
            "fat_g": 10.6,
        },
        "allergens": ["egg"],
    },
    {
        "name": "Yogurt, plain",
        "nutrients_per_100g": {
            "energy_kcal": 61,
            "protein_g": 3.5,
            "carbohydrate_g": 4.7,
            "fiber_g": 0,
            "fat_g": 3.3,
        },
        "allergens": ["milk"],
    },
    {
        "name": "Salmon, cooked",
        "nutrients_per_100g": {
            "energy_kcal": 206,
            "protein_g": 22.1,
            "carbohydrate_g": 0,
            "fiber_g": 0,
            "fat_g": 12.4,
        },
        "allergens": ["fish"],
    },
)


def seed_reference_data(session: Session) -> None:
    record_types_by_code: dict[str, RecordType] = {}
    for values in RECORD_TYPES:
        record_type = session.scalar(select(RecordType).where(RecordType.code == values["code"]))
        if record_type is None:
            record_type = RecordType(**values)
            session.add(record_type)
            session.flush()
        record_types_by_code[record_type.code] = record_type

    for values in REFERENCE_RANGES:
        record_type = record_types_by_code[values["record_type_code"]]
        existing = session.scalar(
            select(ReferenceRange).where(
                ReferenceRange.record_type_id == record_type.id,
                ReferenceRange.unit == values["unit"],
                ReferenceRange.population == values["population"],
            )
        )
        if existing is None:
            session.add(
                ReferenceRange(
                    record_type_id=record_type.id,
                    unit=values["unit"],
                    lower_bound=values["lower_bound"],
                    upper_bound=values["upper_bound"],
                    population=values["population"],
                    source_note=DEMO_WARNING,
                )
            )

    for values in FOODS:
        food = session.scalar(select(Food).where(Food.name == values["name"]))
        if food is None:
            session.add(
                Food(
                    **values,
                    excluded_conditions=[],
                    source_note=DEMO_WARNING,
                )
            )
        else:
            food.nutrients_per_100g = values["nutrients_per_100g"]
            food.allergens = values["allergens"]
            food.excluded_conditions = []
            food.source_note = DEMO_WARNING

    session.flush()


def main() -> None:
    from app.db.session import SessionLocal

    with SessionLocal.begin() as session:
        seed_reference_data(session)


if __name__ == "__main__":
    main()
