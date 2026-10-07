"""The Claude Code backend's tool bridge, driven by a fake SDK.

Claude Code runs its own tool loop: the game hands it the DM's tools as an in-process
MCP server, and Claude Code calls them back. That callback is the honesty guarantee for
anyone playing on a Claude subscription - the dice it reports must come from
`dm.run_tool`, not from the model.

Nothing else in the suite reaches this path, and it was rewired when the DM stopped
reading storage directly (Claude Code is now lent the tools and a `call_tool`, instead of
reaching back into `dm` for them). So a fake SDK plays Claude Code here: it calls each
registered tool handler the way the real one would, then answers.
"""

import asyncio

import pytest

import server
from game import claude_code, dm, providers, rules


class Text:
    def __init__(self, text):
        self.text = text


class Assistant:
    def __init__(self, content):
        self.content = content
        self.error = None


class Result:
    is_error = False


class FakeClaudeCode:
    """Stands in for `claude_agent_sdk`: records what it was offered, and calls tools."""

    def __init__(self, calls, reply="The lock gives with a dull click."):
        self.calls = calls              # [(tool name, args)] to make, in order
        self.reply = reply
        self.offered = None             # allowed_tools it was given
        self.results = []               # what each tool call returned

    def tool(self, name, description, schema):
        def wrap(handler):
            return (name, handler)
        return wrap

    def create_sdk_mcp_server(self, name, version, built):
        return {"name": name, "tools": dict(built)}

    async def query(self, prompt, options):
        self.prompt, self.system = prompt, options.system_prompt
        self.offered = list(options.allowed_tools)
        handlers = options.mcp_servers[claude_code.MCP_SERVER]["tools"]
        for name, args in self.calls:
            out = await handlers[name](args)
            self.results.append(out["content"][0]["text"])
        yield Assistant([Text(self.reply)])
        yield Result()


@pytest.fixture
def fake_sdk(monkeypatch):
    def install(calls, reply="The lock gives with a dull click."):
        sdk = FakeClaudeCode(calls, reply)
        for name in ("query", "tool", "create_sdk_mcp_server"):
            monkeypatch.setattr(claude_code, name, getattr(sdk, name))
        monkeypatch.setattr(claude_code, "TextBlock", Text)
        monkeypatch.setattr(claude_code, "AssistantMessage", Assistant)
        monkeypatch.setattr(claude_code, "ResultMessage", Result)
        monkeypatch.setattr(claude_code, "StreamEvent", type("StreamEvent", (), {}))
        monkeypatch.setattr(claude_code, "ToolUseBlock", type("ToolUseBlock", (), {}))
        monkeypatch.setattr(claude_code, "SDK_ERROR", "")
        backend = claude_code.ClaudeCodeBackend("claude-code", "Claude (test)", "claude-test")
        monkeypatch.setattr(providers, "failover_order", lambda current_id: [backend])
        return sdk
    return install


def turn(cid, characters, action, repo):
    async def go():
        history = []
        events = [e async for e in dm.take_turn(history, characters, characters[0]["name"],
                                                 action, "en", "claude-code", None,
                                                 cid, repo=repo)]
        return events, history
    return asyncio.run(go())


def a_campaign(lore=False):
    repo = server.A.repo

    async def make():
        campaign = await repo.create_campaign("The Bridge")
        char = rules.new_character("Vess", "Elf", "Rogue")
        await repo.add_character(campaign["id"], char, "tok")
        if lore:
            await repo.add_lore(campaign["id"], "Aria.md", "Aria Venn keeps the toll bridge.")
        return campaign["id"], await repo.party(campaign["id"])
    cid, party = asyncio.run(make())
    return cid, party, repo


def test_the_dice_come_from_python_not_from_claude(fake_sdk):
    sdk = fake_sdk([("roll_dice", {"notation": "1d20+5", "reason": "Vess: Sleight of Hand",
                                   "mode": "normal"})])
    cid, party, repo = a_campaign()
    events, history = turn(cid, party, "I pick the lock.", repo)

    dice = [e for e in events if e["kind"] == "dice"]
    assert len(dice) == 1 and 6 <= dice[0]["total"] <= 25
    assert str(dice[0]["total"]) in sdk.results[0]           # the model was told the truth
    assert any(e["kind"] == "narration" for e in events)
    assert history[-1]["role"] == "assistant"


def test_claude_code_is_offered_exactly_this_campaigns_tools(fake_sdk):
    sdk = fake_sdk([])
    cid, party, repo = a_campaign()
    turn(cid, party, "I look around.", repo)
    offered = {t.split("__")[-1] for t in sdk.offered}
    assert offered == {"roll_dice", "update_character", "equip_armor", "set_effect",
                       "use_spell_slot", "long_rest", "roll_initiative", "next_turn",
                       "end_combat"}
    assert all(t.startswith(f"mcp__{claude_code.MCP_SERVER}__") for t in sdk.offered)


def test_the_library_reaches_claude_code_through_the_repository(fake_sdk):
    """search_lore is only offered when there are documents, and reading them goes
    through the repository the turn was given - the path that stopped touching
    `store` directly."""
    sdk = fake_sdk([("search_lore", {"query": "Aria", "document": ""})])
    cid, party, repo = a_campaign(lore=True)
    events, _ = turn(cid, party, "Who keeps the bridge?", repo)

    assert "search_lore" in {t.split("__")[-1] for t in sdk.offered}
    assert "toll bridge" in sdk.results[0]
    assert any(e["kind"] == "lore" for e in events)


def test_a_sheet_change_made_through_claude_code_lands_on_the_sheet(fake_sdk):
    sdk = fake_sdk([("update_character", {
        "character_name": "Vess", "hp_change": -3, "xp_gain": 0, "gold_change": 0,
        "add_items": [], "remove_items": [], "add_conditions": [], "remove_conditions": [],
        "level_up": False, "reason": "a needle trap"})])
    cid, party, repo = a_campaign()
    before = party[0]["hp"]
    events, _ = turn(cid, party, "I pick the lock.", repo)

    assert party[0]["hp"] == before - 3
    assert any(e["kind"] == "sheet" for e in events)


def test_claude_code_gets_the_synopsis_and_only_the_recent_turns(fake_sdk):
    """Claude Code used to see the last 40 *messages* - ten-odd turns - and nothing of
    what came before. Now older turns reach it as the campaign's synopsis."""
    sdk = fake_sdk([])
    cid, party, repo = a_campaign()
    history = []
    for i in range(6):
        history += [{"role": "user", "content": f"Vess acts: old deed {i}"},
                    {"role": "assistant", "content": [{"type": "text", "text": f"Then {i}."}]}]
    memory = {"upto": 8, "synopsis": "Vess robbed the toll-keeper and fled north."}

    async def go():
        return [e async for e in dm.take_turn(history, party, "Vess", "I keep running.",
                                               "en", "claude-code", None, cid, repo=repo,
                                               memory=memory)]
    asyncio.run(go())
    assert "robbed the toll-keeper" in sdk.system and "<story_so_far>" in sdk.system
    assert "old deed 3" not in sdk.prompt                 # condensed
    assert "old deed 4" in sdk.prompt and "I keep running." in sdk.prompt
