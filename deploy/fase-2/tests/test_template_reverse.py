# tests/test_template_reverse.py
import sys

import pytest

sys.path.insert(0, "/home/<agent>/workspace/scripts/lib")
import template_reverse as tr

# Los placeholders esperados se leen de tr.KEY_TO_PLACEHOLDER, nunca se
# escriben aquí como literal entre corchetes angulares -- ese literal es la
# misma cadena que el sed de despliegue sustituye globalmente, y corrompería
# las aserciones de este fichero al desplegarlo (ver el mismo aviso en
# template_reverse.py).
PH = tr.KEY_TO_PLACEHOLDER

IDENTITY = {
    "agent": "orion",
    "Agent": "Orion",
    "AGENT": "ORION",
    "vmid": "142",
    "ip_address": "192.168.1.50",
    "hostname": "ClaudeAgentOrion",
    "owner_name": "Iván López Mañas",
    "profession": "",
    "family": "",
    "tech_level": "",
    "use_cases": "",
    "tone_style": "",
    "language_preference": "",
}


class TestBuildSubstitutionPairs:
    def test_sorted_by_value_length_descending(self):
        pairs = tr.build_substitution_pairs(IDENTITY)
        lengths = [len(v) for v, _ in pairs]
        assert lengths == sorted(lengths, reverse=True)

    def test_empty_values_are_excluded(self):
        pairs = tr.build_substitution_pairs(IDENTITY)
        values = [v for v, _ in pairs]
        assert "" not in values

    def test_longest_value_is_the_hostname(self):
        pairs = tr.build_substitution_pairs(IDENTITY)
        assert pairs[0] == ("ClaudeAgentOrion", PH["hostname"])


class TestReverseContent:
    def test_basic_single_value(self):
        assert tr.reverse_content("Soy Orion.", IDENTITY) == f"Soy {PH['Agent']}."

    def test_longest_first_avoids_corrupting_hostname(self):
        text = "Vivo en ClaudeAgentOrion, soy Orion, en minúscula orion, en mayúsculas ORION."
        result = tr.reverse_content(text, IDENTITY)
        expected = (
            f"Vivo en {PH['hostname']}, soy {PH['Agent']}, "
            f"en minúscula {PH['agent']}, en mayúsculas {PH['AGENT']}."
        )
        assert result == expected
        # nunca debe quedar el hostname roto a medias
        assert "ClaudeAgent" + PH["Agent"] not in result

    def test_case_sensitive_distinguishes_the_three_forms(self):
        assert tr.reverse_content("orion", IDENTITY) == PH["agent"]
        assert tr.reverse_content("Orion", IDENTITY) == PH["Agent"]
        assert tr.reverse_content("ORION", IDENTITY) == PH["AGENT"]

    def test_owner_name_with_accents(self):
        assert tr.reverse_content("Hola Iván López Mañas", IDENTITY) == f"Hola {PH['owner_name']}"

    def test_no_matches_returns_text_unchanged(self):
        text = "Nada que ver por aquí."
        assert tr.reverse_content(text, IDENTITY) == text

    def test_empty_identity_values_never_touch_the_text(self):
        # str.replace("", X) insertaría X entre cada carácter si no se filtrara.
        text = "texto normal"
        result = tr.reverse_content(text, IDENTITY)
        assert result == text


class TestReverseFilename:
    def test_agent_value_becomes_bare_AGENT_literal(self):
        assert tr.reverse_filename("orion-context.py", IDENTITY) == "AGENT-context.py"

    def test_does_not_use_angle_bracket_placeholder(self):
        result = tr.reverse_filename("orion-context.py", IDENTITY)
        assert PH["agent"] not in result

    def test_no_match_returns_unchanged(self):
        assert tr.reverse_filename("common.py", IDENTITY) == "common.py"

    def test_empty_agent_value_returns_unchanged(self):
        identity = dict(IDENTITY, agent="")
        assert tr.reverse_filename("orion-context.py", identity) == "orion-context.py"


class TestMarkedSections:
    MIXED_DOC = (
        "# Orion\n"
        "<!-- TEMPLATE:BEGIN -->\n"
        "## Estructura\n"
        "Cosas agnósticas sobre Orion y su workspace.\n"
        "<!-- TEMPLATE:END -->\n"
        "## Perfil del usuario\n"
        "Nombre: Iván López Mañas\n"
        "<!-- TEMPLATE:BEGIN -->\n"
        "## Infra\n"
        "vmid 142\n"
        "<!-- TEMPLATE:END -->\n"
    )

    def test_has_marked_sections_true(self):
        assert tr.has_marked_sections(self.MIXED_DOC) is True

    def test_has_marked_sections_false_when_absent(self):
        assert tr.has_marked_sections("sin marcadores aquí") is False

    def test_extract_marked_sections_returns_each_block_in_order(self):
        sections = tr.extract_marked_sections(self.MIXED_DOC)
        assert len(sections) == 2
        assert "Estructura" in sections[0]
        assert "Infra" in sections[1]

    def test_extract_never_includes_unmarked_identity_content(self):
        sections = tr.extract_marked_sections(self.MIXED_DOC)
        joined = "\n".join(sections)
        assert "Iván López Mañas" not in joined

    def test_reverse_marked_sections_only_reverses_marked_content(self):
        reversed_sections = tr.reverse_marked_sections(self.MIXED_DOC, IDENTITY)
        assert PH["hostname"] not in "".join(reversed_sections)  # no aparece en esas secciones
        assert f"vmid {PH['vmid']}" in reversed_sections[1]
        # el nombre agnóstico "Orion" en la sección 0 sí se revierte
        assert PH["Agent"] in reversed_sections[0]

    def test_splice_replaces_sections_in_order(self):
        template = (
            "cabecera\n"
            "<!-- TEMPLATE:BEGIN -->\nviejo1\n<!-- TEMPLATE:END -->\n"
            "medio\n"
            "<!-- TEMPLATE:BEGIN -->\nviejo2\n<!-- TEMPLATE:END -->\n"
            "pie"
        )
        result = tr.splice_marked_sections(template, ["\nnuevo1\n", "\nnuevo2\n"])
        assert "nuevo1" in result and "viejo1" not in result
        assert "nuevo2" in result and "viejo2" not in result
        assert "cabecera" in result and "medio" in result and "pie" in result

    def test_splice_raises_on_marker_count_mismatch(self):
        template = "<!-- TEMPLATE:BEGIN -->x<!-- TEMPLATE:END -->"
        with pytest.raises(tr.MarkerMismatchError):
            tr.splice_marked_sections(template, ["a", "b"])

    def test_roundtrip_extract_reverse_splice(self):
        reversed_sections = tr.reverse_marked_sections(self.MIXED_DOC, IDENTITY)
        # plantilla existente con el mismo número de marcadores, contenido antiguo distinto
        existing_template = (
            "<!-- TEMPLATE:BEGIN -->\nantiguo\n<!-- TEMPLATE:END -->\n"
            "identidad del template, intacta\n"
            "<!-- TEMPLATE:BEGIN -->\nantiguo2\n<!-- TEMPLATE:END -->\n"
        )
        result = tr.splice_marked_sections(existing_template, reversed_sections)
        assert "identidad del template, intacta" in result
        assert PH["Agent"] in result
        assert PH["vmid"] in result


class TestAssertNoLeftoverIdentity:
    def test_raises_when_a_value_is_still_present(self):
        with pytest.raises(tr.IdentityLeakError):
            tr.assert_no_leftover_identity("todavía dice Orion aquí", IDENTITY)

    def test_passes_on_clean_reversed_text(self):
        text = tr.reverse_content("Soy Orion, vmid 142", IDENTITY)
        tr.assert_no_leftover_identity(text, IDENTITY)  # no debe lanzar

    def test_ignores_empty_values(self):
        # family="" no debe hacer saltar nada aunque el texto esté vacío de contenido
        tr.assert_no_leftover_identity("texto cualquiera", IDENTITY)
