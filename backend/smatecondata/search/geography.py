"""Geography resolution in Arabic, English and French (spec 0B.1, 0K).

Maps country and region names typed in any of the three languages onto ISO
3166-1 alpha-3 codes, and expands the region presets the spec calls for
(Maghreb, MENA, OECD, EU, ...).

Coverage is focused on the regions this product is aimed at -- North Africa,
MENA, and the major reference economies -- and is extended by adding rows to
``COUNTRY_ALIASES`` rather than by changing code.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass

from ..core.models import Language
from .normalize import normalize_all

# iso3 -> (english, french, arabic, *extra aliases)
COUNTRY_ALIASES: dict[str, tuple[str, ...]] = {
    # --- Maghreb / North Africa -----------------------------------------
    "DZA": ("Algeria", "Algerie", "الجزائر", "DZ", "ALG"),
    "MAR": ("Morocco", "Maroc", "المغرب", "MA", "MOR"),
    "TUN": ("Tunisia", "Tunisie", "تونس", "TN"),
    "LBY": ("Libya", "Libye", "ليبيا", "LY"),
    "MRT": ("Mauritania", "Mauritanie", "موريتانيا", "MR"),
    "EGY": ("Egypt", "Egypte", "مصر", "EG"),
    "SDN": ("Sudan", "Soudan", "السودان", "SD"),
    # --- Mashreq / Gulf ---------------------------------------------------
    "JOR": ("Jordan", "Jordanie", "الأردن", "JO"),
    "LBN": ("Lebanon", "Liban", "لبنان", "LB"),
    "SYR": ("Syria", "Syrie", "سوريا", "SY"),
    "IRQ": ("Iraq", "Irak", "العراق", "IQ"),
    "SAU": ("Saudi Arabia", "Arabie Saoudite", "السعودية", "المملكة العربية السعودية", "SA"),
    "ARE": ("United Arab Emirates", "Emirats Arabes Unis", "الإمارات", "UAE", "AE"),
    "QAT": ("Qatar", "Qatar", "قطر", "QA"),
    "KWT": ("Kuwait", "Koweit", "الكويت", "KW"),
    "OMN": ("Oman", "Oman", "عمان", "OM"),
    "BHR": ("Bahrain", "Bahrein", "البحرين", "BH"),
    "YEM": ("Yemen", "Yemen", "اليمن", "YE"),
    "PSE": ("Palestine", "Palestine", "فلسطين", "PS"),
    "ISR": ("Israel", "Israel", "إسرائيل", "IL"),
    "IRN": ("Iran", "Iran", "إيران", "IR"),
    "TUR": ("Turkiye", "Turquie", "تركيا", "Turkey", "TR"),
    # --- Africa -----------------------------------------------------------
    "NGA": ("Nigeria", "Nigeria", "نيجيريا", "NG"),
    "ZAF": ("South Africa", "Afrique du Sud", "جنوب أفريقيا", "ZA"),
    "KEN": ("Kenya", "Kenya", "كينيا", "KE"),
    "ETH": ("Ethiopia", "Ethiopie", "إثيوبيا", "ET"),
    "GHA": ("Ghana", "Ghana", "غانا", "GH"),
    "SEN": ("Senegal", "Senegal", "السنغال", "SN"),
    "CIV": ("Cote d'Ivoire", "Cote d'Ivoire", "ساحل العاج", "Ivory Coast", "CI"),
    # --- Europe -----------------------------------------------------------
    "FRA": ("France", "France", "فرنسا", "FR"),
    "DEU": ("Germany", "Allemagne", "ألمانيا", "DE"),
    "ITA": ("Italy", "Italie", "إيطاليا", "IT"),
    "ESP": ("Spain", "Espagne", "إسبانيا", "ES"),
    "PRT": ("Portugal", "Portugal", "البرتغال", "PT"),
    "GBR": ("United Kingdom", "Royaume-Uni", "المملكة المتحدة", "UK", "Britain", "GB"),
    "NLD": ("Netherlands", "Pays-Bas", "هولندا", "NL"),
    "BEL": ("Belgium", "Belgique", "بلجيكا", "BE"),
    "CHE": ("Switzerland", "Suisse", "سويسرا", "CH"),
    "SWE": ("Sweden", "Suede", "السويد", "SE"),
    "NOR": ("Norway", "Norvege", "النرويج", "NO"),
    "POL": ("Poland", "Pologne", "بولندا", "PL"),
    "GRC": ("Greece", "Grece", "اليونان", "GR"),
    # --- Americas ---------------------------------------------------------
    "USA": ("United States", "Etats-Unis", "الولايات المتحدة", "US", "USA", "America"),
    "CAN": ("Canada", "Canada", "كندا", "CA"),
    "MEX": ("Mexico", "Mexique", "المكسيك", "MX"),
    "BRA": ("Brazil", "Bresil", "البرازيل", "BR"),
    "ARG": ("Argentina", "Argentine", "الأرجنتين", "AR"),
    "CHL": ("Chile", "Chili", "تشيلي", "CL"),
    # --- Asia / Pacific ---------------------------------------------------
    "CHN": ("China", "Chine", "الصين", "CN"),
    "JPN": ("Japan", "Japon", "اليابان", "JP"),
    "IND": ("India", "Inde", "الهند", "IN"),
    "KOR": ("South Korea", "Coree du Sud", "كوريا الجنوبية", "Korea", "KR"),
    "IDN": ("Indonesia", "Indonesie", "إندونيسيا", "ID"),
    "MYS": ("Malaysia", "Malaisie", "ماليزيا", "MY"),
    "PAK": ("Pakistan", "Pakistan", "باكستان", "PK"),
    "AUS": ("Australia", "Australie", "أستراليا", "AU"),
    "RUS": ("Russia", "Russie", "روسيا", "RU"),
}

# Region presets (spec 0K "reusable country groups").
REGIONS: dict[str, tuple[str, ...]] = {
    "maghreb": ("DZA", "MAR", "TUN", "LBY", "MRT"),
    "north_africa": ("DZA", "MAR", "TUN", "LBY", "EGY", "MRT", "SDN"),
    "mena": ("DZA", "MAR", "TUN", "LBY", "EGY", "MRT", "SDN", "JOR", "LBN",
             "SYR", "IRQ", "SAU", "ARE", "QAT", "KWT", "OMN", "BHR", "YEM",
             "PSE", "ISR", "IRN"),
    "gcc": ("SAU", "ARE", "QAT", "KWT", "OMN", "BHR"),
    "eu": ("FRA", "DEU", "ITA", "ESP", "PRT", "NLD", "BEL", "SWE", "POL", "GRC"),
    "g7": ("USA", "GBR", "FRA", "DEU", "ITA", "CAN", "JPN"),
    "brics": ("BRA", "RUS", "IND", "CHN", "ZAF"),
    "sub_saharan_africa": ("NGA", "ZAF", "KEN", "ETH", "GHA", "SEN", "CIV"),
}

REGION_ALIASES: dict[str, tuple[str, ...]] = {
    "maghreb": ("Maghreb", "Maghreb countries", "المغرب العربي", "بلدان المغرب العربي"),
    "north_africa": ("North Africa", "Afrique du Nord", "شمال أفريقيا", "شمال افريقيا"),
    "mena": ("MENA", "Middle East and North Africa",
             "Moyen-Orient et Afrique du Nord", "الشرق الأوسط وشمال أفريقيا"),
    "gcc": ("GCC", "Gulf Cooperation Council", "Conseil de cooperation du Golfe",
            "مجلس التعاون الخليجي", "دول الخليج"),
    "eu": ("EU", "European Union", "Union Europeenne", "الاتحاد الأوروبي"),
    "g7": ("G7", "Group of Seven", "مجموعة السبع"),
    "brics": ("BRICS", "بريكس"),
    "sub_saharan_africa": ("Sub-Saharan Africa", "Afrique subsaharienne",
                           "أفريقيا جنوب الصحراء"),
}


@dataclass(frozen=True)
class GeoMatch:
    iso3: str
    matched_text: str
    via_region: str | None = None


class GeographyResolver:
    """Resolve country/region mentions in any of the three languages."""

    def __init__(self) -> None:
        self._alias_to_iso3: dict[str, str] = {}
        self._alias_to_region: dict[str, str] = {}
        # Longest aliases first so "South Africa" wins over "Africa".
        self._ordered_aliases: list[tuple[str, str, bool]] = []

        for iso3, names in COUNTRY_ALIASES.items():
            for name in names:
                for form in normalize_all(name):
                    if form:
                        self._alias_to_iso3.setdefault(form, iso3)
        for region, names in REGION_ALIASES.items():
            for name in names:
                for form in normalize_all(name):
                    if form:
                        self._alias_to_region.setdefault(form, region)

        for form, iso3 in self._alias_to_iso3.items():
            self._ordered_aliases.append((form, iso3, False))
        for form, region in self._alias_to_region.items():
            self._ordered_aliases.append((form, region, True))
        self._ordered_aliases.sort(key=lambda t: len(t[0]), reverse=True)

    def iso3_for(self, name: str) -> str | None:
        if len(name) == 3 and name.upper() in COUNTRY_ALIASES:
            return name.upper()
        for form in normalize_all(name):
            if form in self._alias_to_iso3:
                return self._alias_to_iso3[form]
        return None

    def expand_region(self, name: str) -> tuple[str, ...]:
        for form in normalize_all(name):
            region = self._alias_to_region.get(form)
            if region:
                return REGIONS[region]
        return ()

    def find_in_query(self, query: str,
                      language: Language | None = None) -> list[GeoMatch]:
        """Every country/region mentioned, de-duplicated, order preserved.

        Matching walks longest-alias-first over a normalised copy and blanks out
        each hit, so ``South Africa`` cannot also register as ``Africa``.
        """
        from .normalize import normalize  # local import avoids a cycle at load

        haystack = f" {normalize(query, language)} "
        matches: list[GeoMatch] = []
        seen: set[str] = set()

        for form, target, is_region in self._ordered_aliases:
            if not form:
                continue
            needle = f" {form} "
            if needle not in haystack:
                continue
            # Consume the match so shorter aliases cannot re-match the text.
            haystack = haystack.replace(needle, " \x00 ")
            if is_region:
                for iso3 in REGIONS[target]:
                    if iso3 not in seen:
                        seen.add(iso3)
                        matches.append(GeoMatch(iso3, form, via_region=target))
            elif target not in seen:
                seen.add(target)
                matches.append(GeoMatch(target, form))

        return matches

    @staticmethod
    def display_name(iso3: str, language: Language = Language.EN) -> str:
        names = COUNTRY_ALIASES.get(iso3.upper())
        if not names:
            return iso3.upper()
        index = {Language.EN: 0, Language.FR: 1, Language.AR: 2}[language]
        return names[index] if index < len(names) else names[0]


@functools.lru_cache(maxsize=1)
def get_geography_resolver() -> GeographyResolver:
    return GeographyResolver()


YEAR_RE = re.compile(r"\b(1[89]\d{2}|20\d{2}|21\d{2})\b")


def extract_years(query: str) -> tuple[int | None, int | None]:
    """Pull a start/end year pair out of a free-form query.

    Arabic-Indic digits are folded by the normaliser first, so
    ``من ٢٠٠٠ إلى ٢٠٢٥`` yields ``(2000, 2025)``.
    """
    from .normalize import normalize

    years = [int(y) for y in YEAR_RE.findall(normalize(query))]
    if not years:
        return None, None
    if len(years) == 1:
        return years[0], None
    return min(years), max(years)
