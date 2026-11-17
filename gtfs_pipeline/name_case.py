"""
Name case normalization
=======================
MobilityData's `mixed_case_recommended_field` warns when a name field is ALL CAPS
(or all lower case). Metro Cali's station names are ALL CAPS ("TORRE DE CALI B1").
This converts such names to readable Spanish title case:

    TORRE DE CALI B1                -> Torre de Cali B1
    AV 2A ENTRE CL 12N Y 10N P1     -> Av 2A entre Cl 12N y 10N P1
    CC UNICENTRO (PUNTO GANE)       -> Cc Unicentro (Punto GANE)

Rules: only names that are entirely upper (or lower) case are touched, so
already-correct mixed-case names (e.g. "Cl 8 entre Kr 3 y 4 (Cámara de
Comercio)") stay as they are. Connecting words stay lower case (except at the
start), tokens with digits (B1, 12N, 3A), single letters (T., C.C.), Roman
numerals and known acronyms stay upper case.
"""
import re

SMALL_WORDS = {"de", "del", "la", "las", "los", "el", "y", "e", "o", "en", "entre",
               "con", "a", "al", "por", "para", "sobre"}
ACRONYMS = {"CAM", "MIO", "GANE", "SENA", "UAO", "ICESI", "CDI", "EPS", "IPS", "CC",
            "CAI", "UIS", "SIB", "CRC", "UCEVA", "INEM", "DAGMA", "CVC", "EMCALI"}
ROMAN = {"II", "III", "IV", "VI", "VII", "VIII", "IX", "XI", "XII", "XIII"}  # not I/V/X (single letters), not CL/DI/MI
TOKEN = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9']+")


def needs_normalizing(name):
    letters = [c for c in name if c.isalpha()]
    if len(letters) < 2:
        return False
    return name == name.upper() or name == name.lower()


def normalize_name(name, extra_acronyms=()):
    if not isinstance(name, str) or not needs_normalizing(name):
        return name
    acr = ACRONYMS | {a.upper() for a in extra_acronyms}
    first = [True]

    def fix(m):
        tok = m.group(0)
        up = tok.upper()
        is_first = first[0]
        first[0] = False
        if any(c.isdigit() for c in tok):
            return up
        if not is_first and tok.lower() in SMALL_WORDS:
            return tok.lower()
        if len(tok) == 1:
            return up
        if up in acr:
            return up
        if up in ROMAN:
            return up
        return tok[:1].upper() + tok[1:].lower()

    return TOKEN.sub(fix, name)


def normalize_series(s):
    out = s.map(normalize_name)
    return out, int((out != s).sum())
