"""Wissen über Meilenprogramme und Amex-Membership-Rewards-Transfers (Deutschland).

Stand: November 2026, laut deutschen Meilen-Ratgebern (reisetopia.de, meilenscout.com,
onlymilesaway.de). Umrechnungskurse ändern sich und es gibt Transfer-Boni. Vor jedem
Transfer in der Amex-App prüfen! Transfers sind endgültig.
"""

from __future__ import annotations

from .models import Profile

PROGRAMS: dict[str, dict] = {
    "amex_mr": {
        "name": "Amex Membership Rewards",
        "unit": "Punkte",
        "kind": "bank",
    },
    "miles_and_more": {
        "name": "Lufthansa Miles & More",
        "unit": "Meilen",
        "kind": "airline",
        "alliance": "Star Alliance",
        "notes": "Prämienflüge mit Lufthansa, SWISS, Austrian, Brussels, ITA und Star-Alliance-Partnern. "
        "Auf Lufthansa-Group-Flügen hohe Zuschläge; Partner-Prämien oft günstiger in Zuschlägen.",
    },
    "ba_club": {
        "name": "British Airways Club (Avios)",
        "unit": "Avios",
        "kind": "airline",
        "alliance": "oneworld",
        "notes": "Avios. Bei BA-Langstrecke hohe Zuschläge; Kurzstrecke und Partner wie Iberia, Qatar, "
        "American, Finnair, Japan Airlines oft attraktiv. Avios sind zu Iberia Club und Qatar Privilege Club übertragbar.",
    },
    "iberia_club": {"name": "Iberia Club (Avios)", "unit": "Avios", "kind": "airline", "alliance": "oneworld"},
    "flying_blue": {"name": "Air France-KLM Flying Blue", "unit": "Meilen", "kind": "airline", "alliance": "SkyTeam"},
    "sas_eurobonus": {"name": "SAS EuroBonus", "unit": "Punkte", "kind": "airline", "alliance": "SkyTeam"},
}

# Amex MR (DE): wie viele MR-Punkte für wie viele Partnermeilen.
MR_TRANSFERS: dict[str, dict] = {
    "ba_club": {"mr": 5, "miles": 4},
    "iberia_club": {"mr": 5, "miles": 4},
    "flying_blue": {"mr": 5, "miles": 4},
    "sas_eurobonus": {"mr": 5, "miles": 4},
    "miles_and_more": {"mr": 3, "miles": 1, "via": "Payback (3:1, dann 1:1). Meist kein guter Deal"},
}

# seats.aero-Quellen → Programm, über das man bucht (für die Zuordnung zu Profil-Konten).
SEATS_AERO_SOURCE_TO_PROGRAM = {
    "flyingblue": "flying_blue",
    "eurobonus": "sas_eurobonus",
    # Avios-Programme: oneworld-Verfügbarkeit (American, Qantas, Finnair …) ist meist auch mit Avios buchbar.
    "american": "ba_club",
    "qantas": "ba_club",
    "finnair": "ba_club",
    "alaska": "ba_club",
}


def loyalty_brief(profile: Profile) -> str:
    """Textbaustein für den Agenten: Kontostände und erreichbare Meilen pro Programm."""
    balances = {a.program: a.balance for a in profile.accounts}
    lines = []
    for a in profile.accounts:
        p = PROGRAMS.get(a.program, {"name": a.program, "unit": ""})
        status = f", Status {a.status}" if a.status else ""
        lines.append(f"- {p['name']}: {a.balance:,} {p['unit']}{status}".replace(",", "."))
    mr = balances.get("amex_mr", 0)
    if mr:
        lines.append("Amex-MR-Transfers (DE, vor Transfer in der Amex-App prüfen):")
        for prog, t in MR_TRANSFERS.items():
            reachable = mr * t["miles"] // t["mr"]
            via = f" via {t['via']}" if "via" in t else ""
            total = reachable + balances.get(prog, 0)
            lines.append(
                f"  - {PROGRAMS[prog]['name']}: {t['mr']}:{t['miles']}{via} → bis zu {reachable:,} "
                f"(+ vorhandene = {total:,})".replace(",", ".")
            )
    lines.append(
        f"Meilen nur empfehlen, wenn der Gegenwert ≥ {profile.min_value_cent_per_mile} ct pro Meile/Punkt liegt "
        "(Gegenwert = (Barpreis − Zuschläge/Steuern der Prämie) / Meilen)."
    )
    return "\n".join(lines)


def cents_per_mile(cash_price: float, miles: int, award_taxes: float = 0) -> float | None:
    if not miles:
        return None
    return round((cash_price - award_taxes) * 100 / miles, 2)
