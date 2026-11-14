"""Der Flug-Agent: Claude plant die Suchen, fragt alle konfigurierten Quellen ab und wählt die besten Optionen.

Ablauf:
1. Claude zerlegt den Reisewunsch anhand von Präferenzen und Profil in mehrere Suchen
   (flexible Daten, Alternativflughäfen, zwei One-Ways, Positionierungsflüge, andere Märkte …).
2. `search_flights` fragt jede konfigurierte Quelle ab (Google Flights via SerpApi, Duffel) und
   filtert harte Präferenzen im Code heraus. Die Zahl der Suchen ist durch ein Budget begrenzt.
3. Optional prüft `search_award_space` Prämienflüge (seats.aero) gegen die Meilenkonten im Profil.
4. Zum Schluss ruft Claude `recommend_options` auf und schreibt eine kurze Erklärung.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date
from typing import Literal, Protocol, TypedDict

import anthropic
from anthropic import beta_tool

from . import filters
from .config import Settings
from .geo import ground_calculator, home_airports
from .ground import trip_days
from .loyalty import PROGRAMS, SEATS_AERO_SOURCE_TO_PROGRAM, cents_per_mile, loyalty_brief
from .models import AwardOption, FlightOption, Preferences, Profile, SearchQuery, SearchResult


class FlightProvider(Protocol):
    name: str

    def search(self, q: SearchQuery, search_id: str) -> SearchResult: ...


class AwardProvider(Protocol):
    name: str

    def search(self, origins: list[str], destinations: list[str], start: str, end: str, cabin: str,
               search_id: str) -> tuple[list[AwardOption], str | None]: ...


_SUFFIX = {"serpapi": "g", "duffel": "d", "demo": ""}
MAX_AWARD_SEARCHES = 4

SYSTEM_PROMPT = """Du bist ein Flugsuch-Agent, der für den Nutzer die insgesamt günstigsten Flüge findet, \
die zu seinen Präferenzen passen. Du buchst nicht selbst: Der Nutzer bucht über den Link, direkt bei der Airline \
oder über ein Reisebüro.

## Werkzeuge
- `search_flights` fragt alle konfigurierten Quellen gleichzeitig ab (Google Flights = Marktüberblick inkl. \
Online-Reisebüros; Duffel = direkt buchbare Airline-Tarife). Harte Präferenzen (Umstiege, Reisedauer, \
Umstiegszeiten, ausgeschlossene Airlines) sind bereits herausgefiltert; `filtered_out` sagt, wie viele.
- Jede Suche kostet Kontingent. Plane gezielt und bündle: mehrere Flughäfen in EINER Suche angeben \
(z. B. origins "FRA,HHN,STR").
- `search_award_space` (falls vorhanden) zeigt Prämienflug-Verfügbarkeit über einen Datumsbereich.

## Spar-Strategien (je nach Präferenzen einsetzen)
1. Flexible Daten: innerhalb der erlaubten ±Tage die aussichtsreichsten Termine (Di/Mi/Sa oft günstiger als Fr/So). \
Preis-Insights "high" sind ein Signal, andere Daten zu testen.
2. Alternative Flughäfen: Nutze die Liste "Flughäfen in Fahrweite" aus dem Profil (falls vorhanden) und bündle sie \
in einer Suche. Die Anreisekosten fließen in den Gesamtpreis ein. Am Ziel ebenso (Mailand MXP/BGY/LIN, London LHR/LGW/STN/LTN, Paris CDG/ORY/BVA, New York JFK/EWR).
3. Zwei One-Way-Tickets statt Hin/Rück, auch bei unterschiedlichen Airlines (fair vergleichen: Summen bilden).
4. Positionierung (Langstrecke): Abflug ab einem anderen europäischen Hub (AMS, BRU, CPH, OSL, MAD, LIS, MXP, \
IST …) ist oft deutlich günstiger. Preis für die Anreise (günstiger Zubringerflug oder Bahn) addieren und Puffer \
einplanen, da getrennte Tickets.
5. Gabelflüge (Open Jaw): Hin nach A, zurück ab B, wenn das zur Reise passt.
6. Anderer Markt (nur wenn erlaubt): denselben Flug mit point_of_sale eines anderen Landes prüfen. Bei Zahlung \
in Fremdwährung das Auslandseinsatzentgelt der Karte einrechnen. Ergebnis als Experiment kennzeichnen.
7. Gesamtkosten statt Lockpreis: Gepäck laut Präferenzen, Anreise und Parken, Übernachtungen. Optionen enthalten \
dafür `ground_eur` (Anreise + Parken ab dem Heimatflughafen, Schätzung) und `total_eur` (Flug + Anreise). \
Vergleiche Abflughäfen immer über `total_eur`: Ein Flug ab AMS, der 60 € günstiger ist, aber 95 € Parken kostet, \
ist teurer als ab DTM per Taxi. Bei zwei One-Ways das Parken über die Gesamtdauer aus der Tabelle im Profil nehmen.
8. Meilen: Wenn ein Barpreis hoch ist (Langstrecke, Premium-Kabinen, Last-Minute), Prämienflüge prüfen und den \
Gegenwert in Cent pro Meile berechnen. Amex-Punkte vorher übertragen zu müssen ist ein Schritt mit Wartezeit; \
Transfers sind endgültig.

## Fakten, die du kennst (bei passender Gelegenheit kurz erwähnen, nicht belehren)
- Geräte-Mythos (iPhone/Mac teurer als Android): Studien (Northeastern 2014) fanden nur vereinzelte, kleine \
Effekte, meist bei Hotels auf einzelnen Portalen, eher Sortierung als Preis. Bei Flügen nicht belegt. \
Cookies/Inkognito: Consumer-Reports-Tests fanden kaum Belege. Real wirken: Mitgliederpreise (eingeloggt), \
gelegentliche App-Rabatte einzelner Airlines, Point of Sale, Ticket-Kombinationen.
- Lufthansa Group erhebt bei Buchung über klassische Reisebüro-Systeme (GDS) einen Aufschlag \
(Distribution Cost Charge, ca. 19 EUR pro Ticket bei Amadeus, Stand 2026). Wer über ein Reisebüro bucht, \
sollte nach einer NDC-Buchung fragen oder direkt buchen.

## Regeln
- Präferenz "price": Preis klar vor Komfort. Viele Umstiege und lange Reisen sind ok, solange die harten Grenzen \
eingehalten sind. "balanced": Preis-Leistung. "comfort": wenige Umstiege und gute Zeiten haben Gewicht.
- Preise von Hin/Rück-Suchen gelten für beide Strecken; One-Way-Preise nur für eine.
- Erfinde keine Preise, Flüge oder Meilenwerte. Nutze nur Optionen aus deinen Ergebnissen und deren IDs.
- Getrennte Tickets/Self-Transfer: auf das Risiko hinweisen (kein Anschlussschutz, Gepäck neu aufgeben).
- Halte dich an das Suchbudget. Wenn es aufgebraucht ist, empfiehl mit dem, was du hast.
- Rufe am Ende GENAU EINMAL `recommend_options` mit 1-4 Optionen auf (z. B. günstigste, beste Preis-Leistung, \
mit Meilen, schnellste).
- Danach eine kurze Zusammenfassung auf Deutsch in Markdown: was du ausprobiert hast und was es gebracht hat, \
die Empfehlung, Haken. Maximal ca. 250 Wörter, keine Tabellen (die Oberfläche zeigt die Flüge bereits an)."""


class Pick(TypedDict):
    option_ids: list[str]
    award_ids: list[str]
    title: str
    reason: str


class AgentRun:
    """Zustand einer einzelnen Nutzeranfrage (Suchen, Optionen, Empfehlungen, Fortschritts-Events)."""

    def __init__(
        self,
        settings: Settings,
        providers: list[FlightProvider],
        award_provider: AwardProvider | None = None,
        prefs: Preferences | None = None,
        profile: Profile | None = None,
        client: anthropic.Anthropic | None = None,
        knowledge: str = "",
    ):
        self.settings = settings
        self.providers = providers
        self.award_provider = award_provider
        self.prefs = prefs or Preferences()
        self.profile = profile or Profile(home_airports=settings.home_airports)
        self.ground = ground_calculator(self.profile, self.prefs.max_ground_travel_h)
        self.client = client
        self.knowledge = knowledge  # freigegebene Community-Tipps
        self.max_searches = settings.max_searches
        self.searches: list[list[SearchResult]] = []  # pro Tool-Aufruf eine Liste (eine je Quelle)
        self.award_searches: list[dict] = []
        self.options: dict[str, FlightOption] = {}
        self.awards: dict[str, AwardOption] = {}
        self.picks: list[dict] = []
        self.events: list[dict] = []

        run = self

        @beta_tool
        def search_flights(
            origins: str,
            destinations: str,
            outbound_date: str,
            return_date: str = "",
            stops: Literal["any", "nonstop", "max1", "max2"] = "any",
            point_of_sale: str = "de",
        ) -> str:
            """Sucht Flüge in allen konfigurierten Quellen. Personen, Kabine und Gepäck kommen aus den Präferenzen.

            Args:
                origins: Kommagetrennte IATA-Codes der Abflughäfen, z. B. "FRA,HHN". Mehrere Flughäfen in einer Suche sparen Budget.
                destinations: Kommagetrennte IATA-Codes der Zielflughäfen, z. B. "LIS" oder "MXP,BGY,LIN".
                outbound_date: Hinflugdatum im Format YYYY-MM-DD.
                return_date: Rückflugdatum YYYY-MM-DD. Leer lassen für One-Way.
                stops: Umstiegsfilter an der Quelle: any, nonstop, max1 oder max2.
                point_of_sale: Zweistelliger Ländercode des Marktes, aus dem gesucht wird (Standard "de"). Andere Märkte nur, wenn die Präferenzen es erlauben.
            """
            return run.do_search(origins=origins, destinations=destinations, outbound_date=outbound_date,
                                 return_date=return_date, stops=stops, point_of_sale=point_of_sale)

        @beta_tool
        def search_award_space(origins: str, destinations: str, start_date: str, end_date: str) -> str:
            """Sucht Prämienflug-Verfügbarkeit (Meilen) für eine Strecke über einen Datumsbereich (eine Richtung). Kabine aus den Präferenzen.

            Args:
                origins: Kommagetrennte IATA-Codes der Abflughäfen.
                destinations: Kommagetrennte IATA-Codes der Zielflughäfen.
                start_date: Erster Tag des Bereichs, YYYY-MM-DD.
                end_date: Letzter Tag des Bereichs, YYYY-MM-DD (höchstens 14 Tage nach start_date).
            """
            return run.do_award_search(origins=origins, destinations=destinations, start_date=start_date,
                                       end_date=end_date)

        @beta_tool
        def recommend_options(picks: list[Pick]) -> str:
            """Legt die finalen Empfehlungen fest, die dem Nutzer als Karten angezeigt werden. Genau einmal am Ende aufrufen.

            Args:
                picks: 1-4 Empfehlungen. option_ids: Flug-IDs aus den Suchergebnissen (bei getrennten Tickets alle IDs). award_ids: Prämienflug-IDs, falls mit Meilen bezahlt wird, sonst []. Bei einer Meilen-Empfehlung die vergleichbare Bar-Option zusätzlich in option_ids angeben, dann wird der Gegenwert berechnet. title: kurzes Label wie "Günstigste Option". reason: 1-3 Sätze Begründung inkl. Haken und ggf. Meilen-Gegenwert.
            """
            return run.do_recommend(picks)

        self.tools = [search_flights, recommend_options]
        if award_provider is not None and self.prefs.consider_miles:
            self.tools.insert(1, search_award_space)

    # --- Tool-Implementierungen -----------------------------------------------
    def do_search(self, **kw) -> str:
        if len(self.searches) >= self.max_searches:
            return _err(f"Suchbudget ({self.max_searches}) aufgebraucht. Bitte jetzt mit den vorhandenen Ergebnissen empfehlen.")
        p = self.prefs
        try:
            pos = (kw.get("point_of_sale") or "de").strip().lower()
            if len(pos) != 2 or not pos.isalpha():
                raise ValueError(f"'{pos}' ist kein zweistelliger Ländercode")
            if pos != "de" and not p.try_other_markets:
                return _err("Andere Märkte sind in den Präferenzen nicht freigegeben. Bitte point_of_sale 'de' verwenden.")
            q = SearchQuery(
                origins=_codes(kw["origins"]),
                destinations=_codes(kw["destinations"]),
                outbound_date=_iso_date(kw["outbound_date"]),
                return_date=_iso_date(kw["return_date"]) if kw.get("return_date") else None,
                adults=p.adults,
                carry_on_bags=p.carry_on_bags,
                stops=kw.get("stops") or "any",
                travel_class=p.cabin,
                currency=self.settings.currency,
                point_of_sale=pos,
            )
        except (ValueError, KeyError) as exc:
            return _err(f"Ungültige Suchparameter: {exc}")
        if q.return_date and q.return_date < q.outbound_date:
            return _err("Rückflugdatum liegt vor dem Hinflugdatum.")

        sid = f"S{len(self.searches) + 1}"
        self.events.append({"type": "search_started", "search_id": sid, "label": q.label()})
        providers = [pr for pr in self.providers if pos == "de" or pr.name != "duffel"]  # Duffel kennt keinen Markt
        results = [pr.search(q, sid + _SUFFIX.get(pr.name, "")) for pr in providers]
        for r in results:
            r.options, r.filtered_out = filters.apply(r.options, p)
            for o in r.options:
                end = o.return_segments[-1].arrival if o.return_segments else q.return_date
                o.ground = self.ground.cost(o.segments[0].from_airport, trip_days(q.outbound_date, end))
            for opt in r.options:
                self.options[opt.id] = opt
        self.searches.append(results)

        all_opts = [o for r in results for o in r.options]
        cheapest = min((o.price for o in all_opts if o.price is not None), default=None)
        errors = [f"{r.source}: {r.error}" for r in results if r.error]
        self.events.append({
            "type": "search_done", "search_id": sid, "label": q.label(), "count": len(all_opts),
            "cheapest": cheapest, "currency": q.currency, "error": "; ".join(errors) or None,
            "cached": all(r.cached for r in results), "filtered_out": sum(r.filtered_out for r in results),
        })
        payload = {
            "search_id": sid,
            "query": q.label(),
            "sources": [
                {
                    "source": r.source,
                    "price_note": r.options[0].price_note if r.options else None,
                    "price_insights": r.insights.model_dump() if r.insights else None,
                    "filtered_out": r.filtered_out,
                    "error": r.error,
                    "options": [o.compact() for o in sorted(r.options, key=lambda o: o.price or 1e9)[:10]],
                }
                for r in results
            ],
            "searches_left": self.max_searches - len(self.searches),
        }
        return json.dumps(payload, ensure_ascii=False)

    def do_award_search(self, **kw) -> str:
        if self.award_provider is None:
            return _err("Keine Prämienflug-Quelle konfiguriert.")
        if len(self.award_searches) >= MAX_AWARD_SEARCHES:
            return _err(f"Budget für Prämiensuchen ({MAX_AWARD_SEARCHES}) aufgebraucht.")
        try:
            origins, dests = _codes(kw["origins"]), _codes(kw["destinations"])
            start, end = _iso_date(kw["start_date"]), _iso_date(kw["end_date"])
        except (ValueError, KeyError) as exc:
            return _err(f"Ungültige Parameter: {exc}")
        if end < start or (date.fromisoformat(end) - date.fromisoformat(start)).days > 14:
            return _err("Datumsbereich muss 0-14 Tage umfassen.")
        sid = f"A{len(self.award_searches) + 1}"
        label = f"Meilen {','.join(origins)} → {','.join(dests)}, {start} bis {end}"
        self.events.append({"type": "search_started", "search_id": sid, "label": label})
        awards, error = self.award_provider.search(origins, dests, start, end, self.prefs.cabin, sid)
        for a in awards:
            self.awards[a.id] = a
        self.award_searches.append({"search_id": sid, "label": label, "count": len(awards), "error": error})
        self.events.append({"type": "search_done", "search_id": sid, "label": label, "count": len(awards),
                            "cheapest": None, "error": error, "miles_min": awards[0].miles if awards else None})
        balances = {a.program: a.balance for a in self.profile.accounts}
        return json.dumps({
            "search_id": sid,
            "error": error,
            "hint": "Preise pro Person und Richtung. program = Programm, über das gebucht wird. "
                    "user_program = passendes Konto im Profil (falls vorhanden).",
            "awards": [
                {**a.compact(), "user_program": SEATS_AERO_SOURCE_TO_PROGRAM.get(a.program),
                 "user_balance": balances.get(SEATS_AERO_SOURCE_TO_PROGRAM.get(a.program, ""), None)}
                for a in awards
            ],
        }, ensure_ascii=False)

    def do_recommend(self, picks: list[Pick]) -> str:
        valid, unknown = [], []
        for p in picks[:4]:
            ids = [i for i in p.get("option_ids", []) if i in self.options]
            aids = [i for i in p.get("award_ids", []) or [] if i in self.awards]
            unknown += [i for i in p.get("option_ids", []) if i not in self.options]
            unknown += [i for i in p.get("award_ids", []) or [] if i not in self.awards]
            if ids or aids:
                valid.append({"option_ids": ids, "award_ids": aids, "title": p.get("title", ""),
                              "reason": p.get("reason", "")})
        if unknown and not valid:
            return _err(f"Unbekannte IDs: {unknown}. Bitte nur IDs aus den Suchergebnissen verwenden.")
        self.picks = valid
        msg: dict = {"ok": True, "saved": len(valid)}
        if unknown:
            msg["ignored_unknown_ids"] = unknown
        return json.dumps(msg)

    # --- Agentenschleife --------------------------------------------------------
    def user_message(self, request: str, today: date) -> str:
        sources = ", ".join(pr.name for pr in self.providers)
        award = "ja (seats.aero)" if any(t.name == "search_award_space" for t in self.tools) else "nein"
        return (
            f"Heute ist {today.isoformat()} ({_WEEKDAYS[today.weekday()]}). Währung: {self.settings.currency}.\n"
            f"Suchbudget: {self.max_searches} Flugsuchen. Quellen: {sources}. Prämienflug-Suche: {award}.\n\n"
            f"## Präferenzen\n{prefs_brief(self.prefs)}\n\n"
            f"## Profil\nHeimatflughafen(s): {self.profile.home_airports}\n"
            + self.radius_brief()
            + f"Zahlungskarte: {self.profile.card} (Auslandseinsatzentgelt {self.profile.card_fx_fee_pct} %)\n"
            + (loyalty_brief(self.profile) if self.prefs.consider_miles else "Meilen: nicht berücksichtigen.")
            + (f"\nNotizen: {self.profile.notes}" if self.profile.notes else "")
            + (f"\n\n{self.knowledge}" if self.knowledge else "")
            + f"\n\n## Reisewunsch\n{request.strip()}"
        )

    def radius_brief(self) -> str:
        pr = self.profile
        if pr.home_lat is None or pr.home_lon is None:
            return ""
        nearby = home_airports(pr, self.prefs.max_ground_travel_h)
        if not nearby:
            return ""
        return (f"Wohnort: {pr.home_location or 'gesetzt'}. Flughäfen in Fahrweite, vom Nutzer ausgewählt "
                "(nur diese als Abflugorte nutzen, außer bei Positionierungsflügen), mit Anreise und Parken "
                "(Schätzung, gesamt für die Reise):\n" + self.ground.brief() + "\n")

    def run(self, request: str, today: date | None = None) -> Iterator[dict]:
        """Führt den Agenten aus und liefert Fortschritts-Events; das letzte Event hat type == "result"."""
        if self.client is None:
            self.client = anthropic.Anthropic()
        today = today or date.today()
        runner = self.client.beta.messages.tool_runner(
            model=self.settings.claude_model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            tools=self.tools,
            messages=[{"role": "user", "content": self.user_message(request, today)}],
            output_config={"effort": self.settings.claude_effort},
            max_iterations=self.max_searches + MAX_AWARD_SEARCHES + 6,
            # Bei einer (seltenen) Ablehnung durch Sicherheitsfilter automatisch auf ein passendes Modell ausweichen.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )

        yield {"type": "status", "text": "Agent plant die Suche …"}
        final_text = ""
        last = None
        sent = 0
        for message in runner:
            last = message
            yield from self.events[sent:]
            sent = len(self.events)
            texts = [b.text for b in message.content if b.type == "text" and b.text.strip()]
            if any(b.type == "tool_use" for b in message.content):
                if any(b.type == "tool_use" and b.name == "recommend_options" for b in message.content):
                    yield {"type": "status", "text": "Stelle Empfehlungen zusammen …"}
            elif texts:
                final_text = "\n\n".join(texts)
        yield from self.events[sent:]

        if last is not None and last.stop_reason == "refusal":
            yield {"type": "error", "text": "Die Anfrage wurde vom Modell abgelehnt. Bitte anders formulieren."}
            return
        yield self.result(final_text)

    def pick_ground(self, opts: list[FlightOption]):
        """Anreise für eine Empfehlung: Abflughafen des ersten Flugs, Parkdauer bis zur letzten Ankunft."""
        if not opts:
            return None
        if len(opts) == 1:
            return opts[0].ground
        first, last = opts[0], opts[-1]
        end = (last.return_segments or last.segments)[-1].arrival
        return self.ground.cost(first.segments[0].from_airport, trip_days(first.departure, end))

    def result(self, summary_md: str) -> dict:
        picks = []
        for p in self.picks:
            opts = [self.options[i] for i in p["option_ids"]]
            awards = [self.awards[i] for i in p["award_ids"]]
            entry = {**p, "options": [o.model_dump() for o in opts], "awards": [a.model_dump() for a in awards],
                     "total_price": _sum_prices(opts) if opts and not awards else None}
            if awards:
                entry["total_miles"] = sum(a.miles for a in awards) * self.prefs.adults
                taxes = [a.taxes for a in awards]
                entry["total_taxes"] = None if None in taxes else round(sum(taxes) * self.prefs.adults, 2)
                cash = _sum_prices(opts)
                if cash is not None:
                    cpm = cents_per_mile(cash, entry["total_miles"], entry["total_taxes"] or 0)
                    entry["cents_per_mile"] = cpm
                    entry["miles_worth_it"] = cpm is not None and cpm >= self.profile.min_value_cent_per_mile
            g = self.pick_ground(opts)
            if g is not None:
                entry["ground"] = g.model_dump()
                if entry["total_price"] is not None and g.days is not None:
                    entry["total_with_ground"] = round(entry["total_price"] + g.total)
            picks.append(entry)
        all_opts = sorted(self.options.values(), key=lambda o: (o.price is None, o.price or 0))
        return {
            "type": "result",
            "summary_md": summary_md,
            "picks": picks,
            "options": [o.model_dump() for o in all_opts],
            "awards": [a.model_dump() for a in sorted(self.awards.values(), key=lambda a: a.miles)],
            "searches": [
                {"search_id": r.search_id, "source": r.source, "label": r.query.label(), "count": len(r.options),
                 "url": r.google_flights_url, "error": r.error, "cached": r.cached, "filtered_out": r.filtered_out,
                 "insights": r.insights.model_dump() if r.insights else None}
                for group in self.searches for r in group
            ] + [{**a, "source": "seats_aero"} for a in self.award_searches],
            "prefs": self.prefs.model_dump(),
            "min_value_cent_per_mile": self.profile.min_value_cent_per_mile,
            "demo": any(r.demo for group in self.searches for r in group) or any(a.demo for a in self.awards.values()),
        }


_WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
_PRIORITY = {"price": "Preis zuerst (Komfort zweitrangig)", "balanced": "Preis-Leistung", "comfort": "Komfort zuerst"}


def prefs_brief(p: Preferences) -> str:
    def opt(v, fmt):
        return "egal" if v is None else fmt.format(v)

    lines = [
        f"- Priorität: {_PRIORITY[p.priority]}",
        f"- Reisende: {p.adults} Erw., Kabine: {p.cabin}",
        f"- Datumsflexibilität: ±{p.flex_days} Tage",
        f"- Gepäck: {p.carry_on_bags} großes Handgepäck, {p.checked_bags} Aufgabegepäck pro Person",
        f"- Harte Grenzen (bereits gefiltert): max. Umstiege {opt(p.max_stops, '{}')}, "
        f"max. Reisezeit {opt(p.max_duration_h, '{} h')}, Umstieg min. {opt(p.min_layover_min, '{} min')}, "
        f"max. {opt(p.max_layover_h, '{} h')}, Übernachtung beim Umstieg {'ok' if p.allow_overnight_layover else 'nein'}",
        f"- Getrennte Tickets/Self-Transfer erlaubt: {'ja' if p.allow_separate_tickets else 'nein'}",
        f"- Positionierung (anderer Abflugort) erlaubt: {'ja' if p.allow_positioning else 'nein'}, "
        f"max. Anreise {'laut Profil-Auswahl' if p.max_ground_travel_h is None else f'{p.max_ground_travel_h} h'}",
        f"- Andere Märkte (Point of Sale) testen: {'ja' if p.try_other_markets else 'nein'}",
        f"- Meilen berücksichtigen: {'ja' if p.consider_miles else 'nein'}",
    ]
    if p.avoid_airlines:
        lines.append(f"- Ausgeschlossene Airlines: {', '.join(p.avoid_airlines)}")
    if p.checked_bags:
        lines.append("  (Aufgabegepäck ist in Google-Preisen meist NICHT enthalten; bei Low-Cost grob 25-50 EUR pro "
                     "Strecke und Gepäckstück addieren.)")
    if p.notes.strip():
        lines.append(f"- Weitere Wünsche: {p.notes.strip()}")
    return "\n".join(lines)


def _err(msg: str) -> str:
    return json.dumps({"error": msg}, ensure_ascii=False)


def _codes(raw: str) -> list[str]:
    codes = [c.strip().upper() for c in raw.split(",") if c.strip()]
    if not codes or any(len(c) != 3 or not c.isalpha() for c in codes):
        raise ValueError(f"'{raw}' sind keine gültigen IATA-Codes (z. B. 'FRA,HHN')")
    return codes


def _iso_date(raw: str) -> str:
    return date.fromisoformat(raw.strip()).isoformat()


def _sum_prices(opts: list[FlightOption]) -> float | None:
    prices = [o.price for o in opts]
    return None if not prices or any(p is None for p in prices) else round(sum(prices), 2)


__all__ = ["AgentRun", "PROGRAMS", "prefs_brief"]
