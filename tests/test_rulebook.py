"""The rulebook: section-aware lookup, offered only when a rulebook is installed.

Every test reads `tests/fixtures/rulebook/rules.md`, a small rulebook written for the
suite in its own words. It is shaped like the SRD - nested headings, the same word in
several sections, one section longer than an excerpt - but it is not SRD text, so the
tests run whether or not the real rulebook has been fetched.
"""

import asyncio
import os

import pytest

from game import dm, rulebook
from .harness import new_table, tool_results

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "rulebook")


@pytest.fixture
def book(monkeypatch):
    monkeypatch.setattr(rulebook, "RULEBOOK_DIR", FIXTURE)
    return rulebook


@pytest.fixture
def no_book(monkeypatch, tmp_path):
    monkeypatch.setattr(rulebook, "RULEBOOK_DIR", str(tmp_path / "nothing-here"))
    return rulebook


def found(query):
    return rulebook.search(query)[1]


# --------------------------------------------------------------------------- #
# reading it
# --------------------------------------------------------------------------- #

def test_sections_are_labelled_by_their_chapter(book):
    """Chapter > section, not the full trail: in the real SRD, Exhaustion is printed a
    size smaller than the other conditions, and the full trail nested it under Deafened."""
    paths = [s["path"] for s in book.sections()]
    assert "Test Rulebook > Grappled" in paths
    assert "Test Rulebook > Cover" in paths


def test_the_attribution_file_is_not_read_as_rules(book, tmp_path, monkeypatch):
    (tmp_path / "rules.md").write_text("# Cover\n\nHalf cover is +2.\n", encoding="utf-8")
    (tmp_path / "ATTRIBUTION.md").write_text("# Attribution\n\nWizards...\n", encoding="utf-8")
    monkeypatch.setattr(rulebook, "RULEBOOK_DIR", str(tmp_path))
    assert [s["title"] for s in rulebook.sections()] == ["Cover"]


# --------------------------------------------------------------------------- #
# finding things
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("query, first", [
    ("grappled", "Grappled"),                 # the condition, before the action
    ("grappling", "Grappling"),               # the action, before the condition
    ("how does grappling work?", "Grappling"),
    ("escape a grapple", "Grappling"),
    ("half cover", "Cover"),
    ("fall 30 feet damage", "Falling"),
    ("exhaustion levels", "Exhaustion"),
    ("PRONE", "Prone"),
])
def test_a_query_lands_on_the_section_it_is_about(book, query, first):
    assert found(query)[0] == first


def test_nothing_found_says_to_rule_and_say_so(book):
    text, titles = book.search("teleportation circle")
    assert titles == [] and "ruling" in text


def test_an_empty_question_is_answered_kindly(book):
    assert book.search("   the of a  ")[1] == []


def test_a_long_section_is_excerpted_around_the_answer(book):
    text, titles = book.search("hooded lantern light")
    assert titles[0] == "Long Passage"
    assert "lantern" in text and len(text) < 2200
    assert "…" in text                         # cut, and says so


def test_the_reply_names_its_source(book):
    assert book.search("cover")[0].startswith("From the SRD 5.1")


# --------------------------------------------------------------------------- #
# the DM
# --------------------------------------------------------------------------- #

def offered(cid=None, repo=None):
    return {t["name"] for t in asyncio.run(dm.tools_for(cid, repo))}


def test_no_rulebook_no_tool_and_no_instructions(no_book):
    assert "lookup_rule" not in offered()
    blocks = asyncio.run(dm.system_blocks("en"))
    assert not any("LOOKING UP RULES" in b["text"] for b in blocks)


def test_with_a_rulebook_the_tool_and_how_to_use_it(book):
    assert "lookup_rule" in offered()          # the terminal client gets it too
    blocks = asyncio.run(dm.system_blocks("th"))
    assert any("Query in English" in b["text"] for b in blocks)


def test_a_lookup_is_shown_to_the_table(book):
    text, event = asyncio.run(dm.run_tool("lookup_rule", {"query": "grappled"}, []))
    assert "speed becomes 0" in text
    assert event == {"kind": "rule", "query": "grappled", "found": ["Grappled", "Grappling"]}
    _, event = asyncio.run(dm.run_tool("lookup_rule", {"query": "teleportation"}, []))
    assert event is None                       # nothing found, nothing to show


def test_a_whole_turn_with_a_lookup(app_client, book):
    client, stub = app_client
    table = new_table(client, stub)
    table.begin("A brawl in the inn.")
    events = table.act("I grab the bandit and hold him down.",
                       [("lookup_rule", {"query": "grappling"})],
                       "You get a fistful of collar.")
    rule = next(e for e in events if e["kind"] == "rule")
    assert rule["found"][0] == "Grappling"
    assert any("Strength (Athletics)" in r for r in tool_results(stub.calls[-1]))


def test_the_campaign_carries_the_attribution_only_with_a_rulebook(app_client, monkeypatch,
                                                                   tmp_path):
    client, stub = app_client
    table = new_table(client, stub)
    monkeypatch.setattr(rulebook, "RULEBOOK_DIR", str(tmp_path / "none"))
    assert client.get(f"/api/campaigns/{table.id}").json()["rulebook"] is None
    monkeypatch.setattr(rulebook, "RULEBOOK_DIR", FIXTURE)
    credit = client.get(f"/api/campaigns/{table.id}").json()["rulebook"]
    assert "Wizards of the Coast LLC" in credit and "Creative Commons Attribution 4.0" in credit


# --------------------------------------------------------------------------- #
# the rulebook that ships
# --------------------------------------------------------------------------- #

SHIPPED = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "srd")


@pytest.fixture
def shipped(monkeypatch):
    if not os.path.isfile(os.path.join(SHIPPED, "srd-5.1.md")):
        pytest.skip("the SRD has not been fetched - run tools/fetch_srd.py")
    monkeypatch.setattr(rulebook, "RULEBOOK_DIR", SHIPPED)
    return rulebook


def test_the_shipped_rulebook_converted_cleanly(shipped):
    """Guards tools/fetch_srd.py: a bad run - headings lost, page footers back in the
    text - would leave the DM a worse rulebook without anything failing."""
    titles = {s["title"] for s in shipped.sections()}
    assert len(titles) > 1500
    # the conditions, all of them - Exhaustion is printed a size smaller than the rest,
    # and the first conversion lost it
    assert {"Blinded", "Charmed", "Deafened", "Exhaustion", "Frightened", "Grappled",
            "Incapacitated", "Invisible", "Paralyzed", "Petrified", "Poisoned", "Prone",
            "Restrained", "Stunned", "Unconscious"} <= titles
    text = open(os.path.join(SHIPPED, "srd-5.1.md"), encoding="utf-8").read()
    assert text.count("System Reference Document 5.1") <= 2       # the licence only


@pytest.mark.parametrize("query, first", [
    ("grappled", "Grappled"), ("half cover", "Cover"), ("falling damage", "Falling"),
    ("opportunity attack", "Opportunity Attacks"), ("death saving throws", "Death Saving Throws"),
    ("fireball", "Fireball"), ("goblin", "Goblin"), ("concentration", "Concentration"),
])
def test_real_questions_find_real_rules(shipped, query, first):
    assert shipped.search(query)[1][0] == first


def test_the_attribution_ships_beside_the_rules():
    path = os.path.join(SHIPPED, "ATTRIBUTION.md")
    if not os.path.isfile(path):
        pytest.skip("the SRD has not been fetched")
    assert rulebook.ATTRIBUTION in open(path, encoding="utf-8").read()
