"""Catálogo de ligas con prioridades, perfil de conteo y riesgo de error.

La idea central: **no todas las ligas merecen la misma confianza**. Las ligas
secundarias tienen menos liquidez, cuotas más blandas y comisiones de error más
frecuentes, pero justo por eso aparecen más ineficiencias. Este catálogo:

* Ordena las ligas por **tier** (1 = élite, 3 = nicho).
* Guarda priors de **goles, tarjetas y córners** por partido, que alimentan los
  modelos de conteo y se sobreescriben cuando ajustas con datos reales.
* Marca el **riesgo de error** de cada liga para priorizar alertas.

Los valores son *priors orientativos* de mercado, no verdades medidas. Cuando
tengas histórico, el ajuste del modelo sustituye estas cifras.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Iterable


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return "".join(c for c in text.lower() if c.isalnum())


@dataclass(frozen=True)
class LeagueProfile:
    key: str
    name: str
    country: str
    sport: str = "soccer"
    tier: int = 2
    goals: float = 2.6            # media de goles por partido
    cards: float = 4.2            # media de tarjetas totales
    corners: float = 10.0         # media de córners totales
    error_risk: str = "media"     # baja | media | alta
    aliases: tuple[str, ...] = field(default_factory=tuple)
    notes: str = ""

    @property
    def priority(self) -> int:
        """Mayor es más prioritario para buscar errores."""
        risk = {"baja": 0, "media": 1, "alta": 2}.get(self.error_risk, 1)
        tier_bonus = {1: 0, 2: 2, 3: 3}.get(self.tier, 1)
        return risk + tier_bonus


def _l(key, name, country, tier, goals, cards, corners, risk, aliases=(), notes=""):
    return LeagueProfile(key, name, country, "soccer", tier, goals, cards, corners, risk, tuple(aliases), notes)


SOCCER_LEAGUES = [
    # ---- Tier 1: élite, mercados eficientes -----------------------------
    _l("soccer_epl", "Premier League", "Inglaterra", 1, 2.85, 3.6, 10.5, "baja", ("England Premier League", "EPL")),
    _l("soccer_spain_la_liga", "LaLiga", "España", 1, 2.55, 5.2, 9.6, "baja", ("La Liga", "Primera Division")),
    _l("soccer_italy_serie_a", "Serie A", "Italia", 1, 2.70, 4.8, 10.3, "baja", ("Italy Serie A",)),
    _l("soccer_germany_bundesliga", "Bundesliga", "Alemania", 1, 3.15, 3.9, 10.2, "baja"),
    _l("soccer_france_ligue_one", "Ligue 1", "Francia", 1, 2.75, 4.4, 9.6, "baja"),
    _l("soccer_uefa_champs_league", "Champions League", "Europa", 1, 2.95, 3.9, 10.4, "baja", ("UCL",)),
    _l("soccer_uefa_europa_league", "Europa League", "Europa", 1, 2.90, 4.1, 10.2, "media", ("UEL",)),
    # ---- Tier 2: segundas divisiones y ligas fuertes no top -------------
    _l("soccer_england_championship", "Championship", "Inglaterra", 2, 2.60, 3.4, 10.6, "media", ("EFL Championship",)),
    _l("soccer_england_league1", "League One", "Inglaterra", 2, 2.65, 3.4, 10.8, "alta"),
    _l("soccer_spain_segunda_division", "LaLiga 2", "España", 2, 2.25, 5.8, 9.1, "media", ("Segunda Division",)),
    _l("soccer_italy_serie_b", "Serie B", "Italia", 2, 2.45, 5.3, 10.0, "alta"),
    _l("soccer_germany_bundesliga2", "Bundesliga 2", "Alemania", 2, 3.05, 4.3, 10.4, "media"),
    _l("soccer_portugal_primeira_liga", "Primeira Liga", "Portugal", 2, 2.60, 5.0, 10.0, "media"),
    _l("soccer_netherlands_eredivisie", "Eredivisie", "Países Bajos", 2, 3.25, 3.3, 10.4, "media"),
    _l("soccer_belgium_first_div", "Pro League", "Bélgica", 2, 2.95, 4.2, 10.1, "media"),
    _l("soccer_turkey_super_lig", "Süper Lig", "Turquía", 2, 2.85, 5.4, 10.0, "alta"),
    _l("soccer_greece_super_league", "Super League", "Grecia", 2, 2.40, 5.6, 9.4, "alta"),
    _l("soccer_austria_bundesliga", "Bundesliga", "Austria", 2, 3.00, 4.0, 9.9, "media"),
    _l("soccer_switzerland_superleague", "Super League", "Suiza", 2, 3.10, 4.2, 9.8, "media"),
    _l("soccer_denmark_superliga", "Superliga", "Dinamarca", 2, 2.95, 3.8, 10.1, "media"),
    _l("soccer_norway_eliteserien", "Eliteserien", "Noruega", 2, 3.05, 3.6, 10.3, "media"),
    _l("soccer_sweden_allsvenskan", "Allsvenskan", "Suecia", 2, 2.95, 3.9, 10.2, "media"),
    _l("soccer_poland_ekstraklasa", "Ekstraklasa", "Polonia", 2, 2.70, 4.9, 10.0, "alta"),
    _l("soccer_czech_first_league", "Fortuna Liga", "Chequia", 2, 2.85, 4.6, 10.2, "alta"),
    _l("soccer_croatia_1_hnl", "HNL", "Croacia", 2, 2.75, 5.0, 9.7, "alta"),
    _l("soccer_serbia_super_liga", "Super Liga", "Serbia", 2, 2.70, 5.2, 9.5, "alta"),
    _l("soccer_romania_liga_1", "Liga 1", "Rumanía", 2, 2.50, 5.4, 9.3, "alta"),
    _l("soccer_russia_premier_league", "Premier League", "Rusia", 2, 2.60, 4.8, 9.9, "alta"),
    _l("soccer_ukraine_premier_league", "Premier League", "Ucrania", 2, 2.55, 5.0, 9.6, "alta"),
    # ---- Tier 2: Américas y Asia ---------------------------------------
    _l("soccer_brazil_serie_a", "Brasileirão Série A", "Brasil", 2, 2.45, 5.3, 10.2, "media", ("Brazil Serie A",)),
    _l("soccer_brazil_serie_b", "Brasileirão Série B", "Brasil", 2, 2.30, 5.6, 9.8, "alta"),
    _l("soccer_argentina_primera_division", "Liga Profesional", "Argentina", 2, 2.20, 5.5, 9.3, "alta", ("Argentina Primera",)),
    _l("soccer_mexico_liga_mx", "Liga MX", "México", 2, 2.75, 4.9, 9.8, "media"),
    _l("soccer_usa_mls", "MLS", "EE. UU.", 2, 3.10, 3.8, 9.9, "media"),
    _l("soccer_japan_j_league", "J1 League", "Japón", 2, 2.70, 2.8, 9.9, "media"),
    _l("soccer_korea_kleague1", "K League 1", "Corea del Sur", 2, 2.55, 3.2, 9.4, "media"),
    _l("soccer_china_superleague", "Super League", "China", 2, 2.80, 4.4, 9.5, "alta"),
    _l("soccer_australia_aleague", "A-League", "Australia", 2, 3.05, 4.0, 10.1, "media"),
    _l("soccer_saudi_arabia_pro_league", "Saudi Pro League", "Arabia Saudí", 2, 2.90, 4.4, 9.6, "alta"),
    _l("soccer_qatar_stars_league", "Stars League", "Catar", 2, 2.95, 4.6, 9.4, "alta"),
    _l("soccer_egypt_premier_league", "Premier League", "Egipto", 2, 2.20, 4.8, 9.0, "alta"),
    _l("soccer_morocco_botola", "Botola Pro", "Marruecos", 2, 2.15, 4.6, 8.9, "alta"),
    _l("soccer_south_africa_psl", "Premier Soccer League", "Sudáfrica", 2, 2.25, 4.2, 9.2, "alta", ("South Africa PSL",)),
    # ---- Tier 3: nicho, máxima ineficiencia y máximo riesgo ------------
    _l("soccer_israel_ligat_ha_al", "Ligat ha'Al", "Israel", 3, 2.60, 5.1, 9.4, "alta"),
    _l("soccer_bulgaria_first_league", "First League", "Bulgaria", 3, 2.45, 5.3, 9.3, "alta"),
    _l("soccer_hungary_nb_i", "NB I", "Hungría", 3, 2.60, 5.0, 9.6, "alta"),
    _l("soccer_slovakia_super_liga", "Super Liga", "Eslovaquia", 3, 2.55, 5.0, 9.5, "alta"),
    _l("soccer_slovenia_prvaliga", "PrvaLiga", "Eslovenia", 3, 2.50, 5.1, 9.3, "alta"),
    _l("soccer_cyprus_first_division", "First Division", "Chipre", 3, 2.40, 5.2, 9.2, "alta"),
    _l("soccer_iceland_premier_league", "Besta deild", "Islandia", 3, 3.05, 4.4, 10.0, "alta"),
    _l("soccer_finland_veikkausliiga", "Veikkausliiga", "Finlandia", 3, 2.70, 4.6, 9.8, "alta"),
    _l("soccer_ireland_premier_division", "Premier Division", "Irlanda", 3, 2.65, 4.4, 9.9, "alta"),
    _l("soccer_kazakhstan_premier_league", "Premier League", "Kazajistán", 3, 2.50, 4.8, 9.3, "alta"),
    _l("soccer_uzbekistan_super_league", "Super League", "Uzbekistán", 3, 2.55, 4.7, 9.2, "alta"),
    _l("soccer_belarus_premier_league", "Vysheyshaya Liga", "Bielorrusia", 3, 2.60, 4.9, 9.4, "alta"),
    _l("soccer_india_isl", "Indian Super League", "India", 3, 2.80, 4.5, 9.1, "alta"),
    _l("soccer_vietnam_v_league_1", "V.League 1", "Vietnam", 3, 2.45, 4.6, 8.8, "alta"),
    _l("soccer_thailand_league_1", "Thai League 1", "Tailandia", 3, 2.85, 4.8, 9.2, "alta"),
    _l("soccer_indonesia_liga_1", "Liga 1", "Indonesia", 3, 2.70, 5.0, 9.0, "alta"),
    _l("soccer_malaysia_super_league", "Super League", "Malasia", 3, 2.75, 4.7, 9.0, "alta"),
]

OTHER_LEAGUES = [
    # Baloncesto
    LeagueProfile("basketball_nba", "NBA", "EE. UU.", "basketball", 1, 228.0, 0, 0, "baja"),
    LeagueProfile("basketball_euroleague", "Euroliga", "Europa", "basketball", 1, 158.0, 0, 0, "baja"),
    LeagueProfile("basketball_spain_acb", "ACB", "España", "basketball", 2, 160.0, 0, 0, "media"),
    LeagueProfile("basketball_nbl", "NBL", "Australia", "basketball", 2, 172.0, 0, 0, "media"),
    LeagueProfile("basketball_vtb", "VTB League", "Europa del Este", "basketball", 2, 165.0, 0, 0, "alta"),
    LeagueProfile("basketball_aba_league", "ABA Liga", "Balcanes", "basketball", 3, 160.0, 0, 0, "alta"),
    # Tenis
    LeagueProfile("tennis_atp", "ATP Tour", "Internacional", "tennis", 1, 0, 0, 0, "baja"),
    LeagueProfile("tennis_wta", "WTA Tour", "Internacional", "tennis", 1, 0, 0, 0, "baja"),
    LeagueProfile("tennis_challenger", "ATP Challenger", "Internacional", "tennis", 3, 0, 0, 0, "alta"),
    # MMA
    LeagueProfile("mma_mixed_martial_arts", "UFC / MMA", "Internacional", "mma", 1, 0, 0, 0, "baja"),
    # Tenis de mesa
    LeagueProfile("table_tennis_wtt", "WTT", "Internacional", "table_tennis", 1, 0, 0, 0, "media"),
    LeagueProfile("table_tennis_setka", "Setka Cup", "Internacional", "table_tennis", 3, 0, 0, 0, "alta"),
    LeagueProfile("table_tennis_tt_elite", "TT Elite Series", "Internacional", "table_tennis", 3, 0, 0, 0, "alta"),
]

ALL_LEAGUES = SOCCER_LEAGUES + OTHER_LEAGUES

_INDEX: dict[str, LeagueProfile] = {}


def _index(alias: str, profile: LeagueProfile) -> None:
    """Indexa un alias sin que una liga menor pise a la de élite.

    Hay muchas "Premier League" en el mundo. Si dos ligas comparten nombre,
    gana la de menor tier (la más conocida), que es la que el usuario espera.
    """
    norm = normalize(alias)
    if not norm:
        return
    existing = _INDEX.get(norm)
    if existing is None or profile.tier < existing.tier:
        _INDEX[norm] = profile


for _profile in ALL_LEAGUES:
    _index(_profile.key, _profile)
    _index(_profile.name, _profile)
    _index(f"{_profile.country} {_profile.name}", _profile)
    for _alias in _profile.aliases:
        _index(_alias, _profile)


def find(name_or_key: str | None) -> LeagueProfile | None:
    """Busca por clave, nombre o alias, ignorando acentos y mayúsculas."""
    if not name_or_key:
        return None
    norm = normalize(name_or_key)
    if norm in _INDEX:
        return _INDEX[norm]
    # Coincidencia parcial: "LaLiga 2" -> perfil concreto. Ante la duda,
    # preferimos la liga de menor tier y el nombre más largo.
    candidates = [p for key, p in _INDEX.items() if norm and (norm in key or key in norm)]
    if not candidates:
        return None
    return max(candidates, key=lambda p: (-p.tier, len(p.name)))


def by_sport(sport: str) -> list[LeagueProfile]:
    return sorted(
        [p for p in ALL_LEAGUES if p.sport == sport],
        key=lambda p: (p.tier, -p.priority, p.name),
    )


def prioritized(sport: str = "soccer", max_tier: int = 3) -> list[LeagueProfile]:
    """Ligas ordenadas por oportunidad de error (las blandas primero)."""
    leagues = [p for p in by_sport(sport) if p.tier <= max_tier]
    return sorted(leagues, key=lambda p: (-p.priority, p.tier, p.name))


def filter_keys(names: Iterable[str] | None) -> list[str]:
    """Convierte nombres de liga a claves; ignora lo que no exista."""
    if not names:
        return []
    keys = []
    for name in names:
        profile = find(name)
        if profile:
            keys.append(profile.key)
    return keys


def describe(sport: str | None = None, max_tier: int = 3) -> str:
    lines = []
    sports = [sport] if sport else sorted({p.sport for p in ALL_LEAGUES})
    for s in sports:
        lines.append(f"\n== {s.upper()} ==")
        lines.append(f"{'LIGA':34s} {'PAIS':14s} T  GOL  TAR  COR  ERROR")
        # Prioridad primero: las ligas con más riesgo de error, arriba.
        profiles = [p for p in prioritized(s, max_tier) if p.tier <= max_tier]
        for p in profiles:
            metrics = (
                f"{p.goals:4.1f} {p.cards:4.1f} {p.corners:4.1f}"
                if p.sport == "soccer"
                else f"{p.goals:4.0f}    -    -"
            )
            lines.append(
                f"{p.name[:34]:34s} {p.country[:14]:14s} {p.tier}  {metrics}  {p.error_risk}"
            )
    return "\n".join(lines)
