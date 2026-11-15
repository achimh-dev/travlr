"""Wissen über Meilenprogramme, Amex-Membership-Rewards-Transfers (Deutschland) und lohnende Einlösungen.

Stand: November 2026. Quellen: meilenvergleich.com (Partnertabelle, Stand 14.09.2026), onlymilesaway.de
(Stand Oktober 2026), reisetopia.de, meilenoptimieren.com, mileguy.de, awardtravelfinder.com.
Kurse, Preise und Zuschläge ändern sich, es gibt Transfer-Boni und Abwertungen. Vor jedem Transfer
in der Amex-App prüfen! Transfers sind endgültig.
"""

from __future__ import annotations

from datetime import date

from .models import Profile, TransferBonus

STAND = "November 2026"

PROGRAMS: dict[str, dict] = {
    "amex_mr": {"name": "Amex Membership Rewards", "unit": "Punkte", "kind": "bank"},
    "miles_and_more": {
        "name": "Lufthansa Miles & More", "unit": "Meilen", "kind": "airline", "alliance": "Star Alliance",
    },
    "ba_club": {"name": "British Airways Club (Avios)", "unit": "Avios", "kind": "airline", "alliance": "oneworld"},
    "iberia_club": {"name": "Iberia Club (Avios)", "unit": "Avios", "kind": "airline", "alliance": "oneworld"},
    "qatar_privilege": {"name": "Qatar Privilege Club (Avios)", "unit": "Avios", "kind": "airline",
                        "alliance": "oneworld"},
    "flying_blue": {"name": "Air France-KLM Flying Blue", "unit": "Meilen", "kind": "airline", "alliance": "SkyTeam"},
    "sas_eurobonus": {"name": "SAS EuroBonus", "unit": "Punkte", "kind": "airline", "alliance": "SkyTeam"},
    "delta_skymiles": {"name": "Delta SkyMiles", "unit": "Meilen", "kind": "airline", "alliance": "SkyTeam"},
    "krisflyer": {"name": "Singapore KrisFlyer", "unit": "Meilen", "kind": "airline", "alliance": "Star Alliance"},
    "asia_miles": {"name": "Cathay Asia Miles", "unit": "Meilen", "kind": "airline", "alliance": "oneworld"},
    "emirates_skywards": {"name": "Emirates Skywards", "unit": "Meilen", "kind": "airline"},
}

# Amex MR (DE): wie viele MR-Punkte für wie viele Partnereinheiten, Mindesttransfer in MR-Punkten.
MR_TRANSFERS: dict[str, dict] = {
    "flying_blue": {"mr": 5, "miles": 4, "min": 625},
    "ba_club": {"mr": 5, "miles": 4, "min": 1000},
    "iberia_club": {"mr": 5, "miles": 4, "min": 1000},
    "sas_eurobonus": {"mr": 5, "miles": 4, "min": 1000},
    "qatar_privilege": {"mr": 3, "miles": 2, "min": 900,
                        "note": "günstiger über BA Club (5:4) und dann kostenlos 1:1 zu Qatar"},
    "krisflyer": {"mr": 3, "miles": 2, "min": 1500, "note": "Transfer kann bis zu 15 Werktage dauern"},
    "asia_miles": {"mr": 3, "miles": 2, "min": 900},
    "delta_skymiles": {"mr": 3, "miles": 2, "min": 3000},
    "emirates_skywards": {"mr": 2, "miles": 1, "min": 1000},
    "miles_and_more": {"mr": 3, "miles": 1, "min": 900, "via": "Payback (3:1, dann 1:1)",
                       "note": "kein direkter Partner, meist kein guter Deal"},
}

# Hotelpartner (nur zur Info in der Oberfläche; der Agent sucht Flüge).
MR_HOTEL_TRANSFERS: dict[str, dict] = {
    "Hilton Honors": {"mr": 1, "miles": 1, "min": 2500},
    "Marriott Bonvoy": {"mr": 3, "miles": 2, "min": 900},
    "Radisson Rewards": {"mr": 1, "miles": 2, "min": 1000},
    "ALL Accor": {"mr": 3, "miles": 1, "min": 900},
}

# Avios lassen sich zwischen diesen Programmen kostenlos 1:1 verschieben (über das BA-Konto).
AVIOS_FAMILY = ("ba_club", "iberia_club", "qatar_privilege")

# seats.aero-Quellen → Programm, über das man bucht (für die Zuordnung zu Profil-Konten).
SEATS_AERO_SOURCE_TO_PROGRAM = {
    "flyingblue": "flying_blue",
    "eurobonus": "sas_eurobonus",
    "delta": "delta_skymiles",
    "singapore": "krisflyer",
    "emirates": "emirates_skywards",
    "qatar": "qatar_privilege",
    # oneworld-Verfügbarkeit (American, Qantas, Finnair …) ist meist auch mit Avios buchbar.
    "american": "ba_club",
    "qantas": "ba_club",
    "finnair": "ba_club",
    "alaska": "ba_club",
}

# Lohnende Einlösungen und Fallen. Bewusst als Spannen/Faustregeln formuliert; der Agent soll sie als
# Hinweis nutzen und konkrete Preise immer aus Suchergebnissen nehmen.
SWEET_SPOTS: list[dict] = [
    {
        "program": "flying_blue",
        "title": "Flying Blue Promo Rewards",
        "detail": "Jeden Monat neue Strecken mit Rabatt auf den Meilenpreis (zuletzt meist um 25 %, manchmal mehr). "
                  "Europa-Economy auf Air France/KLM ab ca. 8.000 Meilen pro Strecke (Light, ohne Aufgabegepäck); "
                  "auch Transavia ist mit Meilen buchbar.",
        "caveat": "Business ab Deutschland mit hohen Zuschlägen (Stichprobe Okt. 2026: ca. 410–470 € pro Richtung). "
                  "Für Langstrecke Business gegen den Barpreis rechnen.",
    },
    {
        "program": "ba_club",
        "title": "Avios auf der Kurzstrecke",
        "detail": "Europa-Prämien bei BA und Iberia ab ca. 10.000 Avios pro Strecke (Off-Peak), Preis nach "
                  "Entfernung und Saison. Gut für teure kurzfristige Europaflüge.",
        "caveat": "Die günstigen Zuzahlungen gelten vor allem ab Großbritannien; ab Deutschland sind Steuern höher.",
    },
    {
        "program": "iberia_club",
        "title": "Iberia Business über den Atlantik ab Madrid",
        "detail": "Off-Peak-Business Madrid–US-Ostküste nach der Abwertung 2025 ca. 40.500 Avios pro Strecke, "
                  "mit deutlich niedrigeren Zuschlägen als bei BA. Positionierung nach MAD einplanen.",
        "caveat": "Verfügbarkeit knapp; Off-Peak-Kalender prüfen.",
    },
    {
        "program": "ba_club",
        "title": "BA-Langstrecke meiden",
        "detail": "Auf eigenen Langstreckenflügen erhebt BA hohe Zuschläge (oft mehrere hundert Euro pro Strecke). "
                  "Avios lieber bei Iberia oder Partnern mit niedrigen Zuschlägen einsetzen.",
        "caveat": "",
    },
    {
        "program": "qatar_privilege",
        "title": "Qatar über Avios",
        "detail": "Qatar-Prämien mit Avios buchen: MR zu BA Club (5:4) und dann kostenlos zu Qatar Privilege Club "
                  "ist besser als der direkte Kurs 3:2.",
        "caveat": "",
    },
    {
        "program": "miles_and_more",
        "title": "Miles & More: Partner statt Lufthansa",
        "detail": "Seit Juni 2025 sind die Meilenpreise bei Lufthansa, SWISS, Austrian und City Airlines dynamisch "
                  "(seit März 2026 auch Discover und Air Dolomiti). Feste, oft günstigere Preise gibt es bei "
                  "Star-Alliance-Partnern. Meilenschnäppchen nur noch bei Partnern wie Brussels, ITA, LOT, "
                  "Eurowings, Luxair, Croatia: ab 12.000 Meilen, monatlich neu, nur online, nicht stornierbar.",
        "caveat": "Amex-Punkte nur über Payback (3:1) – fast nie lohnend.",
    },
    {
        "program": "krisflyer",
        "title": "KrisFlyer braucht Vorlauf",
        "detail": "Kurs 3:2 seit der Abwertung; Transfer kann bis zu 15 Werktage dauern. Nur bei bestätigter "
                  "Verfügbarkeit und genug Vorlauf.",
        "caveat": "",
    },
    {
        "program": "emirates_skywards",
        "title": "Emirates: schwacher Kurs",
        "detail": "MR zu Skywards nur 2:1. Selten die beste Wahl, nur wenn eine Prämie sonst nicht zu bekommen ist.",
        "caveat": "",
    },
]

GENERAL_RULES = (
    "Vor jedem Transfer Prämienverfügbarkeit im Zielprogramm bestätigen; Transfers sind endgültig. "
    "Gegenwert = (Barpreis − Steuern/Zuschläge der Prämie) / Meilen. Zuschläge gehören immer in die Rechnung."
)


def _n(x: int) -> str:
    return f"{x:,}".replace(",", ".")


def program_name(key: str) -> str:
    return PROGRAMS.get(key, {}).get("name", key)


def active_bonuses(profile: Profile, today: date | None = None) -> dict[str, TransferBonus]:
    today = today or date.today()
    out: dict[str, TransferBonus] = {}
    for b in profile.transfer_bonuses:
        if b.valid_until and b.valid_until < today.isoformat():
            continue
        if b.program not in out or b.bonus_pct > out[b.program].bonus_pct:
            out[b.program] = b
    return out


def transfer_yield(mr_points: int, program: str, bonus_pct: float = 0) -> int:
    t = MR_TRANSFERS.get(program)
    if not t or mr_points < t["min"]:
        return 0
    return int(mr_points * t["miles"] / t["mr"] * (1 + bonus_pct / 100))


def reachable_miles(profile: Profile, program: str, today: date | None = None) -> int:
    """Vorhandene Meilen im Programm plus das, was per MR-Transfer (inkl. aktivem Bonus) erreichbar ist.
    Avios-Programme teilen sich den Bestand, weil Avios kostenlos zwischen ihnen verschoben werden."""
    balances = {a.program: a.balance for a in profile.accounts}
    family = AVIOS_FAMILY if program in AVIOS_FAMILY else (program,)
    own = sum(balances.get(p, 0) for p in family)
    bonuses = active_bonuses(profile, today)
    mr = balances.get("amex_mr", 0)
    best = max((transfer_yield(mr, p, bonuses[p].bonus_pct if p in bonuses else 0) for p in family), default=0)
    return own + best


def loyalty_brief(profile: Profile, today: date | None = None) -> str:
    """Textbaustein für den Agenten: Kontostände, erreichbare Meilen, Boni, Sweet Spots."""
    balances = {a.program: a.balance for a in profile.accounts}
    lines = []
    for a in profile.accounts:
        p = PROGRAMS.get(a.program, {"name": a.program, "unit": ""})
        status = f", Status {a.status}" if a.status else ""
        lines.append(f"- {p['name']}: {_n(a.balance)} {p['unit']}{status}")
    bonuses = active_bonuses(profile, today)
    mr = balances.get("amex_mr", 0)
    if mr:
        lines.append(f"Amex-MR-Transfers (DE, Stand {STAND}, vor Transfer in der Amex-App prüfen):")
        for prog, t in MR_TRANSFERS.items():
            b = bonuses.get(prog)
            reachable = transfer_yield(mr, prog, b.bonus_pct if b else 0)
            via = f" via {t['via']}" if "via" in t else ""
            bonus = f", aktuell +{b.bonus_pct:g} % Bonus" + (f" bis {b.valid_until}" if b.valid_until else "") if b else ""
            note = f" ({t['note']})" if "note" in t else ""
            lines.append(
                f"  - {program_name(prog)}: {t['mr']}:{t['miles']}{via}{bonus} → bis zu {_n(reachable)}, "
                f"insgesamt erreichbar {_n(reachable_miles(profile, prog, today))}{note}"
            )
        lines.append("  Avios sind zwischen BA Club, Iberia Club und Qatar Privilege Club kostenlos 1:1 übertragbar.")
    elif bonuses:
        lines.append("Transfer-Boni: " + ", ".join(f"{program_name(k)} +{v.bonus_pct:g} %" for k, v in bonuses.items()))
    lines.append(
        f"Meilen nur empfehlen, wenn der Gegenwert ≥ {profile.min_value_cent_per_mile} ct pro Meile/Punkt liegt. "
        + GENERAL_RULES
    )
    lines.append(f"Lohnende Einlösungen und Fallen (Stand {STAND}, Richtwerte, nicht als Preis zitieren):")
    for s in SWEET_SPOTS:
        lines.append(f"  - {s['title']}: {s['detail']}" + (f" Haken: {s['caveat']}" if s["caveat"] else ""))
    return "\n".join(lines)


def credits_brief(profile: Profile) -> str:
    if not profile.credits:
        return ""
    lines = ["Guthaben und Gutscheine (nur anrechnen, wenn die Buchung über den genannten Weg möglich ist):"]
    for c in profile.credits:
        cond = f" – {c.condition}" if c.condition else ""
        until = f", gültig bis {c.valid_until}" if c.valid_until else ""
        lines.append(f"- {c.name}: {c.amount_eur:g} €{cond}{until}")
    return "\n".join(lines)


def cents_per_mile(cash_price: float, miles: int, award_taxes: float = 0) -> float | None:
    if not miles:
        return None
    return round((cash_price - award_taxes) * 100 / miles, 2)
