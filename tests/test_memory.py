"""Campaign memory: stale state trimmed, older turns condensed, the record kept whole.

Every turn used to re-send the entire campaign - each turn carrying its own snapshot of
the party's state - so a request grew with every turn and the campaign's total cost with
the square of its length, until a long game outgrew the model's context and stopped.
Now old snapshots are trimmed from what is sent, and once turns pile up a background job
condenses the older ones into a synopsis. The stored history is never cut.
"""

import asyncio
import json
import time

import pytest

import server
from game import dm, providers, rules
from game.services import memory
from .harness import new_table, prompt_of


SUMMARY = """SYNOPSIS:
The party crossed the salt marsh and met Aria Venn at the toll bridge.

PEOPLE AND PLACES:
- Aria Venn: keeps the toll bridge; owes Vess a favour

THREADS:
- Vess promised to find Aria's brother
"""


class Summariser:
    """Plays the cheap AI that condenses the transcript. Kept apart from the DM's stub,
    so a summary never eats a reply scripted for a turn."""

    id = "summariser"
    label = "Summariser (test)"
    free = True

    def __init__(self, reply=SUMMARY, fail=False):
        self.reply, self.fail, self.calls = reply, fail, []

    async def stream(self, system_blocks, messages, tools, images=None):
        self.calls.append({"system": system_blocks[0]["text"],
                           "prompt": messages[0]["content"]})
        if self.fail:
            raise providers.ProviderFailed("summariser: out of credit")
        yield {"type": "delta", "text": self.reply}
        yield {"type": "message", "content": [{"type": "text", "text": self.reply}],
               "stop_reason": "end_turn"}


@pytest.fixture
def small_memory(monkeypatch):
    """Condense early: keep 3 turns verbatim, summarise once more than 5 pile up."""
    monkeypatch.setattr(memory, "KEEP_TURNS", 3)
    monkeypatch.setattr(memory, "EVERY_TURNS", 2)

    def install(summariser):
        monkeypatch.setattr(memory, "summarizer_backends", lambda _id: [summariser])
        return summariser
    return install


def wait_for(predicate, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    return None


def stored_memory(table):
    return asyncio.run(server.A.repo.get_memory(table.id))


# --------------------------------------------------------------------------- #
# trimming
# --------------------------------------------------------------------------- #

def history_of(turns):
    party = [rules.new_character("Vess", "Elf", "Rogue")]
    out = []
    for i in range(turns):
        out += [{"role": "user", "content": dm.build_prompt(party, "Vess", f"action {i}")},
                {"role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}",
                                                   "name": "update_character", "input": {}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{i}",
                                              "content": "HP 8 -> 5\nParty state: [{...}]"}]},
                {"role": "assistant", "content": [{"type": "text", "text": f"narration {i}"}]}]
    return out


def test_only_the_current_turn_keeps_its_snapshot_of_the_party():
    history = history_of(3)
    before = json.dumps(history)
    sent = providers.trim_stale(history)
    assert all("<party_state>" not in m["content"] for m in sent[:8]
               if isinstance(m["content"], str))
    assert "<party_state>" in sent[8]["content"]
    assert sent[2]["content"][0]["content"] == "HP 8 -> 5"            # old echo gone
    assert "Party state" in sent[10]["content"][0]["content"]          # current kept
    assert "action 0" in sent[0]["content"]                            # the action stays
    assert json.dumps(history) == before                                # record untouched


def test_claude_codes_window_counts_turns_not_messages():
    """The limit said turns and counted messages, so '40' was about ten turns."""
    text = __import__("game.claude_code", fromlist=["x"]).render_transcript(
        history_of(12), limit=4)
    assert "action 8" in text and "action 11" in text and "action 7" not in text
    assert "<party_state>" in text                                      # the current one
    assert text.count("<party_state>") == 1


# --------------------------------------------------------------------------- #
# when, and what
# --------------------------------------------------------------------------- #

def test_a_summary_is_due_only_past_the_threshold(monkeypatch):
    monkeypatch.setattr(memory, "KEEP_TURNS", 3)
    monkeypatch.setattr(memory, "EVERY_TURNS", 2)
    assert memory.due(history_of(5), None) is None
    cut = memory.due(history_of(6), None)
    assert cut == 12                                   # turns 3, 4, 5 stay verbatim
    assert memory.due(history_of(8), {"upto": cut}) is None
    assert memory.due(history_of(9), {"upto": cut}) == 24


def test_the_answer_is_split_into_synopsis_and_notes():
    synopsis, notes = memory.parse(SUMMARY)
    assert synopsis.startswith("The party crossed")
    assert "Aria Venn: keeps the toll bridge" in notes and "Threads" in notes
    assert memory.parse("Just a paragraph, no headings.") == \
        ("Just a paragraph, no headings.", "")


def test_the_free_ais_summarise_before_the_paid_ones(monkeypatch):
    def b(id, free):
        return type("B", (), {"id": id, "free": free})()
    paid, gemini, opus, ollama = b("paid", False), b("gemini", True), b("opus", False), b("ollama", True)
    monkeypatch.setattr(providers, "failover_order", lambda _id: [paid, gemini, opus, ollama])
    assert [x.id for x in memory.summarizer_backends("paid")] == \
        ["gemini", "ollama", "paid", "opus"]


# --------------------------------------------------------------------------- #
# whole campaigns
# --------------------------------------------------------------------------- #

def play(table, n, start=0):
    for i in range(start, start + n):
        table.act(f"I do thing number {i}.", f"Narration {i}.")


def test_a_long_campaign_is_condensed_and_the_dm_sent_less(app_client, small_memory):
    client, stub = app_client
    summariser = small_memory(Summariser())
    table = new_table(client, stub)
    table.begin("The salt marsh.")
    play(table, 5)                                     # begin + 5 = 6 turns: due

    mem = wait_for(lambda: stored_memory(table))
    assert mem and mem["synopsis"].startswith("The party crossed") and mem["turns"] == 3
    history = asyncio.run(server.A.repo.get_history(table.id))
    assert mem["upto"] == providers.turn_starts(history)[3]

    # the summariser read the old turns - without their stale party snapshots
    assert "I do thing number 0." in summariser.calls[0]["prompt"]
    assert "<party_state>" not in summariser.calls[0]["prompt"]

    # its notes are in the library, where search_lore reaches them
    docs = [d["name"] for d in asyncio.run(server.A.repo.lore_documents(table.id))]
    assert "Campaign memory" in docs

    table.act("I look around.", "Reeds.")
    call = stub.calls[-1]
    system = "\n".join(b.get("text", "") for b in call["system"])
    assert "<story_so_far>" in system and "met Aria Venn" in system
    sent = json.dumps(call["messages"], ensure_ascii=False)
    assert "I do thing number 0." not in sent          # condensed, not re-sent
    assert "I do thing number 4." in sent              # recent turns verbatim
    assert isinstance(call["messages"][0]["content"], str)   # opens on a turn, not a tool

    # and nothing was deleted
    history = asyncio.run(server.A.repo.get_history(table.id))
    assert "I do thing number 0." in json.dumps(history, ensure_ascii=False)


def test_requests_stay_bounded_however_long_the_campaign(app_client, small_memory):
    """FIXED - finding A: every turn re-sent the whole campaign, so requests grew without
    limit and a long game eventually outgrew the model's context."""
    client, stub = app_client
    small_memory(Summariser())
    table = new_table(client, stub)
    table.begin("It starts.")
    sizes = []
    for i in range(24):
        table.act(f"turn {i}", f"narration {i}")
        wait_for(lambda: not memory.due(asyncio.run(server.A.repo.get_history(table.id)),
                                        stored_memory(table)), timeout=3)
        sizes.append(len(stub.calls[-1]["messages"]))
    assert max(sizes) <= (memory.KEEP_TURNS + memory.EVERY_TURNS + 1) * 2
    assert sizes[-1] < len(asyncio.run(server.A.repo.get_history(table.id))) / 3


def test_a_failed_summary_changes_nothing_and_is_retried(app_client, small_memory):
    client, stub = app_client
    broken = small_memory(Summariser(fail=True))
    table = new_table(client, stub)
    table.begin("It starts.")
    play(table, 5)
    assert wait_for(lambda: broken.calls)              # it was attempted
    time.sleep(0.1)
    assert stored_memory(table) is None
    table.act("Still here?", "Yes.")                   # the game carries on

    small_memory(Summariser())                          # the claim was released: it retries
    table.act("And now?", "Now.")
    assert wait_for(lambda: stored_memory(table))


def test_a_thai_campaign_is_remembered_in_thai(app_client, small_memory):
    client, stub = app_client
    summariser = small_memory(Summariser())
    table = new_table(client, stub, lang="th")
    table.begin("หนองเกลือ")
    play(table, 5)
    assert wait_for(lambda: summariser.calls)
    assert "Write the content of every section in Thai" in summariser.calls[0]["system"]
    wait_for(lambda: stored_memory(table))
    docs = [d["name"] for d in asyncio.run(server.A.repo.lore_documents(table.id))]
    assert "ความทรงจำของแคมเปญ" in docs


def test_memory_only_moves_forward(app_client):
    client, stub = app_client
    table = new_table(client, stub)
    repo = server.A.repo
    asyncio.run(repo.set_memory(table.id, {"upto": 20, "synopsis": "newer", "turns": 5}))
    kept = asyncio.run(repo.set_memory(table.id, {"upto": 8, "synopsis": "older", "turns": 2}))
    assert kept["synopsis"] == "newer"
    assert stored_memory(table)["synopsis"] == "newer"


def test_memory_travels_with_an_export(app_client, small_memory):
    client, stub = app_client
    small_memory(Summariser())
    table = new_table(client, stub)
    table.begin("It starts.")
    play(table, 5)
    assert wait_for(lambda: stored_memory(table))
    blob = client.get(f"/api/campaigns/{table.id}/export").json()
    back = asyncio.run(server.A.repo.import_campaign(blob))
    cid = back if isinstance(back, str) else back["id"]
    assert asyncio.run(server.A.repo.get_memory(cid))["synopsis"].startswith("The party")


def test_an_imported_memory_that_does_not_fit_its_history_is_dropped(app_client):
    client, _ = app_client
    blob = {"format": "ai-dm-campaign/1",
            "campaign": {"name": "odd", "history": [],
                         "memory": {"upto": 50, "synopsis": "from another campaign"}}}
    cid = client.post("/api/import", json=blob).json()["id"]
    assert asyncio.run(server.A.repo.get_memory(cid)) is None


# --------------------------------------------------------------------------- #
# a model with a small context
# --------------------------------------------------------------------------- #

def test_a_window_is_cut_to_fit_on_turn_boundaries():
    history = history_of(10)
    budget = providers.estimate_tokens(history[-8:]) + 10   # about two turns' worth
    sent = providers.fit_window(history, budget)
    assert providers.estimate_tokens(sent) <= budget
    assert isinstance(sent[0]["content"], str)              # opens on a turn
    assert sent[-1] is history[-1]                          # the current turn is whole


def test_the_current_turn_is_never_dropped_even_when_it_does_not_fit():
    history = history_of(3)
    assert providers.fit_window(history, 1) == history[8:]


def test_thai_is_budgeted_by_bytes_not_characters():
    """Characters / 4 would let a Thai window through at a fraction of its real size."""
    english, thai = "a" * 300, "ก" * 300
    assert providers.estimate_tokens(thai) == 3 * providers.estimate_tokens(english)


def test_a_small_context_model_is_sent_what_fits(app_client):
    """Ollama defaults to 8,192 tokens. Without fitting, it silently dropped the start of
    every long request - and the first version of the fitting crashed against it, which
    nothing caught, because no test used a small-context backend. This one does."""
    client, stub = app_client
    stub.context_tokens = 6000
    table = new_table(client, stub)
    table.begin("It starts.")
    for i in range(12):
        table.act(f"I do thing number {i} with some care and at some length.", "x" * 900)
    call = stub.calls[-1]
    budget = 6000 - (providers.estimate_tokens(call["system"])
                     + providers.estimate_tokens(call["tools"]) + dm.REPLY_ROOM)
    assert providers.estimate_tokens(call["messages"]) <= budget
    assert isinstance(call["messages"][0]["content"], str)
    assert "I do thing number 11" in json.dumps(call["messages"])
    assert "I do thing number 0 " not in json.dumps(call["messages"])
