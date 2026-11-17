import pandas as pd
from name_case import normalize_name, normalize_series


def test_basic_station_and_street_names():
    assert normalize_name("TORRE DE CALI B1") == "Torre de Cali B1"
    assert normalize_name("AV 2A ENTRE CL 12N Y 10N P1") == "Av 2A entre Cl 12N y 10N P1"
    assert normalize_name("CL 8 ENTRE KR 14 Y 14A") == "Cl 8 entre Kr 14 y 14A"


def test_acronyms_initials_and_parentheses():
    assert normalize_name("T. MENGA") == "T. Menga"
    assert normalize_name("C.C. UNICENTRO (PUNTO GANE)") == "C.C. Unicentro (Punto GANE)"
    assert normalize_name("PLAZOLETA JAIRO VARELA - CAM") == "Plazoleta Jairo Varela - CAM"


def test_accents_and_first_word_capitalised():
    assert normalize_name("LA NUEVA FLORESTA") == "La Nueva Floresta"
    assert normalize_name("ESTACIÓN MELÉNDEZ") == "Estación Meléndez"
    assert normalize_name("COLEGIO SAN JUAN BOSCO") == "Colegio San Juan Bosco"


def test_already_mixed_case_is_untouched():
    for n in ["Cl 8 entre Kr 3 y 4 (Cámara de Comercio de Cali)", "Torre de Cali B1", "Parada 12345"]:
        assert normalize_name(n) == n


def test_lowercase_and_non_strings_and_series():
    assert normalize_name("san bosco") == "San Bosco"
    assert normalize_name(None) is None
    s, n = normalize_series(pd.Series(["SAN BOSCO B1", "Centro", "A"]))
    assert list(s) == ["San Bosco B1", "Centro", "A"] and n == 1


if __name__ == "__main__":
    test_basic_station_and_street_names(); test_acronyms_initials_and_parentheses()
    test_accents_and_first_word_capitalised(); test_already_mixed_case_is_untouched()
    test_lowercase_and_non_strings_and_series()
    print("All name_case tests passed.")
