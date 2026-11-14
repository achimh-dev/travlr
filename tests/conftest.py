import os

# Tests sollen keine Hintergrund-Jobs starten und keine echten Keys nutzen
os.environ["SCHEDULER_ENABLED"] = "false"
# Eine echte .env (z. B. auf der Synology) darf die Tests nicht beeinflussen: diese Werte stehen
# vor dem .env-Laden fest, der Loader überschreibt gesetzte Variablen nicht.
for key in ("ANTHROPIC_API_KEY", "SERPAPI_API_KEY", "DUFFEL_API_KEY", "SEATS_AERO_API_KEY", "APP_PASSWORD",
            "NTFY_TOPIC", "NTFY_TOKEN", "CF_ACCESS_TEAM_DOMAIN", "CF_ACCESS_AUD", "SESSION_SECRET",
            "CLOUDFLARE_TUNNEL_TOKEN", "PUBLIC_URL"):
    os.environ[key] = ""
os.environ.update({"APP_USERNAME": "admin", "HOME_AIRPORTS": "FRA", "NTFY_URL": "https://ntfy.sh",
                   "CURRENCY": "EUR", "MAX_SEARCHES": "10", "CACHE_TTL_HOURS": "6"})
