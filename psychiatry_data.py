from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class Psychiatrist:
    id: str
    name: str
    hospital: str
    rating: float
    fee_bdt: int
    title: str = "Consultant Psychiatrist"
    qualifications: str = ""
    # Availability controls what the user can select.
    # Weekdays: Monday=0 .. Sunday=6
    available_weekdays: tuple[int, ...] = (6, 0, 1, 2, 3)  # Sun–Thu by default
    available_times: tuple[str, ...] = ("10:00", "12:00", "15:00", "17:00")


# Real psychiatrists from LifeSpring Consultancy, Dhaka, Bangladesh.
# Source: https://appointment.lifespringint.com  &  bangladeshhealthalliance.com
FEATURED_PSYCHIATRISTS: list[Psychiatrist] = [
    Psychiatrist(
        id="psy-01",
        name="Dr. Sayedul Ashraf Kushal",
        hospital="LifeSpring Consultancy (Panthapath Branch)",
        rating=4.9,
        fee_bdt=1500,
        title="Lead Psychiatrist",
        qualifications="MBBS (Dhaka Medical College), MD (BSMMU), JSPN Fellow (Japan)",
        available_weekdays=(6, 0, 1, 2, 3),
        available_times=("10:00", "10:30", "11:00", "16:00", "16:30"),
    ),
    Psychiatrist(
        id="psy-02",
        name="Dr. Golam Mostofa Milon",
        hospital="LifeSpring Consultancy (Panthapath Branch)",
        rating=4.8,
        fee_bdt=1500,
        title="Senior Psychiatrist & Sexual Health Specialist",
        qualifications="MBBS, BCS (Health), MD (BSMMU)",
        available_weekdays=(6, 0, 2, 4),
        available_times=("12:00", "12:30", "18:00", "18:30"),
    ),
    Psychiatrist(
        id="psy-03",
        name="Dr. Munmun Jahan",
        hospital="LifeSpring Consultancy (Banani Branch)",
        rating=4.8,
        fee_bdt=1500,
        title="Consultant Psychiatrist & Psychotherapist",
        qualifications="MBBS, FCPS (Psychiatry)",
        available_weekdays=(6, 1, 3),
        available_times=("09:00", "09:30", "14:00", "14:30", "17:00"),
    ),
    Psychiatrist(
        id="psy-04",
        name="Dr. Nafia Farzana",
        hospital="Bangabandhu Sheikh Mujib Medical University (BSMMU)",
        rating=4.9,
        fee_bdt=1500,
        title="Consultant Psychiatrist",
        qualifications="MBBS, MD (Psychiatry)",
        available_weekdays=(6, 0, 1, 2),
        available_times=("11:00", "11:30", "17:00", "17:30"),
    ),
    Psychiatrist(
        id="psy-05",
        name="Dr. Rubaiyat Ferdush",
        hospital="LifeSpring Consultancy (Panthapath Branch)",
        rating=4.7,
        fee_bdt=1500,
        title="Consultant Psychiatrist",
        qualifications="MBBS, MCPS (Psychiatry)",
        available_weekdays=(0, 2, 4),
        available_times=("10:00", "10:30", "15:00", "15:30"),
    ),
    Psychiatrist(
        id="psy-06",
        name="Dr. A. M. Fariduzzaman",
        hospital="LifeSpring Consultancy (Panthapath Branch)",
        rating=4.7,
        fee_bdt=1500,
        title="Consultant Psychiatrist",
        qualifications="MBBS, MD (Psychiatry), BCS (Health)",
        available_weekdays=(6, 1, 3),
        available_times=("11:00", "11:30", "16:00", "16:30"),
    ),
    Psychiatrist(
        id="psy-07",
        name="Dr. Shafiul Alam",
        hospital="LifeSpring Consultancy (Banani Branch)",
        rating=4.6,
        fee_bdt=1500,
        title="Consultant Psychiatrist",
        qualifications="MBBS, MCPS (Psychiatry)",
        available_weekdays=(6, 0, 2, 4),
        available_times=("09:00", "09:30", "14:00", "14:30"),
    ),
    Psychiatrist(
        id="psy-08",
        name="Dr. Maheen Rahman",
        hospital="LifeSpring Consultancy (Panthapath Branch)",
        rating=4.8,
        fee_bdt=1500,
        title="Consultant Psychiatrist",
        qualifications="MBBS, FCPS (Psychiatry)",
        available_weekdays=(6, 0, 1, 3),
        available_times=("10:00", "10:30", "17:00", "17:30", "19:00"),
    ),
    Psychiatrist(
        id="psy-09",
        name="Dr. Mohammad Asif-Al-Naim",
        hospital="LifeSpring Consultancy (Banani Branch)",
        rating=4.7,
        fee_bdt=1500,
        title="Consultant Psychiatrist",
        qualifications="MBBS, MD (Psychiatry)",
        available_weekdays=(0, 1, 3),
        available_times=("12:00", "12:30", "18:00", "18:30"),
    ),
    Psychiatrist(
        id="psy-10",
        name="Dr. Humayra Shahjahan Hridi",
        hospital="LifeSpring Consultancy (Panthapath Branch)",
        rating=4.8,
        fee_bdt=1500,
        title="Child & Adolescent Psychiatrist",
        qualifications="MBBS, FCPS (Child & Adolescent Psychiatry)",
        available_weekdays=(6, 0, 2),
        available_times=("10:00", "10:30", "11:00", "15:00", "15:30"),
    ),
    Psychiatrist(
        id="psy-11",
        name="Dr. Touhida Ferdousi",
        hospital="LifeSpring Consultancy (Panthapath Branch)",
        rating=4.7,
        fee_bdt=1500,
        title="Child & Adolescent Psychiatrist",
        qualifications="MBBS, MD (Child & Adolescent Psychiatry)",
        available_weekdays=(6, 1, 4),
        available_times=("09:00", "09:30", "14:00", "14:30"),
    ),
    Psychiatrist(
        id="psy-12",
        name="Dr. Anika Basharat",
        hospital="LifeSpring Consultancy (Banani Branch)",
        rating=4.6,
        fee_bdt=1500,
        title="Consultant Psychiatrist",
        qualifications="MBBS, MD (Psychiatry)",
        available_weekdays=(6, 0, 2, 3),
        available_times=("11:00", "11:30", "16:00", "16:30"),
    ),
]


def list_featured_psychiatrists() -> list[Psychiatrist]:
    return list(FEATURED_PSYCHIATRISTS)


def get_psychiatrist_by_id(psychiatrist_id: str) -> Psychiatrist | None:
    for p in FEATURED_PSYCHIATRISTS:
        if p.id == psychiatrist_id:
            return p
    return None


def iter_psychiatrists() -> Iterable[Psychiatrist]:
    return FEATURED_PSYCHIATRISTS
