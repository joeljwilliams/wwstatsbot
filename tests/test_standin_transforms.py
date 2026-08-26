"""Deaths: /dead, /ad, /steal, and the role changes a death sets off.

Two things here are worth more than the rest.

**The transforms**, because a role that fails to change is invisible. A Wild Child whose
role model was eaten is a wolf from that moment, and a stand-in still listing them as a
Wild Child gives them the wrong achievements twice over — theirs, and not the pack's — with
nothing on screen to say so.

**/ad's guard**, because /ad is a full reset. The game bot's roster is the authority, so a
misread roster does not produce a small error: it produces a wholesale wrong one, marking
living players dead and vice versa. The roster states its own counts, so the parse is
checked before anything is written, and a disagreement changes nothing at all.
"""

from conftest import FakeEntity, FakeUpdate, FakeUser, bot_message, message
from test_standin_session import mention, player_message, reveal, start_session

import session
from handlers import gamesession

TEXT_MENTION = "text_mention"


def roster(alive, dead_rows=(), claimed=None, total=None):
    """The game bot's roster: living players mentioned, dead ones as plain text rows.

    Shaped like the real thing — "Players Alive: 11/16", every living name a text_mention,
    each dead row naming the role they were.
    """
    alive = list(alive)
    lines = ["Players Alive: {}/{}".format(claimed if claimed is not None else len(alive), total or 16)]
    for name, role_text in dead_rows:
        lines.append("{}: \N{SKULL} Dead - {}".format(name, role_text))
    for _, name in alive:
        lines.append("{}: \N{SLIGHTLY SMILING FACE} Alive".format(name))
    entities = [FakeEntity(TEXT_MENTION, user=FakeUser(uid, name)) for uid, name in alive]
    return bot_message("\n".join(lines), entities=entities)


async def ad(context, replied):
    msg = player_message("/ad", reply_to=replied)
    context.args = []
    await gamesession.follow_roster_cmd(FakeUpdate(message=msg), context)
    return msg


async def dead(context, target_name):
    msg = player_message("/dead " + target_name)
    context.args = target_name.split()
    await gamesession.dead_cmd(FakeUpdate(message=msg), context)
    return msg


# --- Transforms -------------------------------------------------------------


async def test_a_wild_childs_rolemodel_dying_makes_them_a_wolf(context):
    session_data = await start_session(context)
    await reveal(context, 3, "wc")
    session.set_model(session_data, 3, 2)

    msg = await dead(context, "omu")

    assert session_data["players"]["3"]["roles"] == ["werewolf"]
    assert mention(3, "J J") + " is now Werewolf" in msg.last_reply
    assert "rolemodel died" in msg.last_reply


async def test_a_turning_wild_child_becomes_a_plain_wolf_not_an_alpha(context):
    """The pack gains a member, not a leader — and Alpha-only achievements must not
    start appearing under someone who merely turned."""
    session_data = await start_session(context)
    await reveal(context, 1, "alpha_wolf")
    await reveal(context, 3, "wc")
    session.set_model(session_data, 3, 2)

    await dead(context, "omu")
    assert session_data["players"]["3"]["roles"] == ["werewolf"]


async def test_a_doppelgangers_rolemodel_dying_copies_their_role(context):
    session_data = await start_session(context)
    await reveal(context, 2, "seer")
    await reveal(context, 3, "dg")
    session.set_model(session_data, 3, 2)

    await dead(context, "omu")
    assert session_data["players"]["3"]["roles"] == ["seer"]


async def test_a_doppelganger_copies_the_role_their_model_ended_up_with(context):
    """The model may have transformed first; what is copied is what they actually were."""
    session_data = await start_session(context, players=[(1, "Ren"), (2, "omu"), (3, "J J"), (4, "Kay")])
    await reveal(context, 2, "wc")
    session.set_model(session_data, 2, 4)  # omu watches Kay
    await reveal(context, 3, "dg")
    session.set_model(session_data, 3, 2)  # J J shadows omu

    await dead(context, "Kay")  # omu turns wolf...
    await dead(context, "omu")  # ...and J J copies the wolf, not the wild child

    assert session_data["players"]["2"]["roles"] == ["werewolf"]
    assert session_data["players"]["3"]["roles"] == ["werewolf"]


async def test_the_apprentice_seer_is_promoted_when_the_seer_dies(context):
    session_data = await start_session(context)
    await reveal(context, 1, "seer")
    await reveal(context, 2, "apps")

    msg = await dead(context, "Ren")

    assert session_data["players"]["2"]["roles"] == ["seer"]
    assert "the seer is gone" in msg.last_reply


async def test_the_apprentice_is_not_promoted_in_a_game_with_no_seer(context):
    """Written as "no living seer, but there was one" so a first death cannot promote
    them in a game that never had a Seer to lose."""
    session_data = await start_session(context)
    await reveal(context, 1, "villager")
    await reveal(context, 2, "apps")

    await dead(context, "Ren")
    assert session_data["players"]["2"]["roles"] == ["apprentice_seer"]


async def test_the_traitor_turns_when_the_last_wolf_dies(context):
    session_data = await start_session(context)
    await reveal(context, 1, "werewolf")
    await reveal(context, 2, "traitor")

    msg = await dead(context, "Ren")

    assert session_data["players"]["2"]["roles"] == ["werewolf"]
    assert "the wolves are gone" in msg.last_reply


async def test_the_traitor_waits_for_the_last_wolf(context):
    session_data = await start_session(context)
    await reveal(context, 1, "werewolf")
    await reveal(context, 3, "alpha_wolf")
    await reveal(context, 2, "traitor")

    await dead(context, "Ren")
    assert session_data["players"]["2"]["roles"] == ["traitor"], "one wolf still stands"

    await dead(context, "J J")
    assert session_data["players"]["2"]["roles"] == ["werewolf"]


async def test_the_traitor_does_not_turn_in_a_wolfless_game(context):
    session_data = await start_session(context)
    await reveal(context, 1, "villager")
    await reveal(context, 2, "traitor")

    await dead(context, "Ren")
    assert session_data["players"]["2"]["roles"] == ["traitor"]


async def test_a_lover_dies_of_sorrow(context):
    session_data = await start_session(context)
    await reveal(context, 1, "villager")
    await reveal(context, 2, "villager")
    session.set_lover(session_data, 1, 2)

    msg = await dead(context, "Ren")

    assert session_data["players"]["2"]["alive"] is False
    assert "dies of sorrow" in msg.last_reply


async def test_a_death_of_sorrow_can_trigger_a_further_transform(context):
    """Transforms cascade, which is why they run to a fixed point rather than once."""
    session_data = await start_session(context)
    await reveal(context, 1, "villager")
    await reveal(context, 2, "seer")  # omu is the Seer *and* Ren's lover
    await reveal(context, 3, "apps")
    session.set_lover(session_data, 1, 2)

    await dead(context, "Ren")

    assert session_data["players"]["2"]["alive"] is False, "died of sorrow"
    assert session_data["players"]["3"]["roles"] == ["seer"], "and that promoted the apprentice"


async def test_nothing_transforms_when_nothing_should(context):
    await start_session(context)
    await reveal(context, 1, "villager")
    msg = await dead(context, "Ren")
    assert msg.last_reply == mention(1, "Ren") + " is dead."


# --- /dead ------------------------------------------------------------------


async def test_dead_marks_the_player_and_moves_them_in_the_roster(context):
    session_data = await start_session(context)
    await reveal(context, 1, "villager")
    await dead(context, "Ren")

    assert session_data["players"]["1"]["alive"] is False
    rendered, _ = gamesession.render_state(session_data)
    living, _, dead_section = rendered.partition("<b>Dead Players:</b>")
    assert "Ren" in dead_section and "Ren" not in living


async def test_dead_in_reply_targets_the_player_replied_to(context):
    session_data = await start_session(context)
    theirs = message("hi", from_user=FakeUser(2, "omu"))
    msg = player_message("/dead", reply_to=theirs)
    context.args = []
    await gamesession.dead_cmd(FakeUpdate(message=msg), context)

    assert session_data["players"]["2"]["alive"] is False


async def test_dead_says_so_when_they_already_are(context):
    await start_session(context)
    await dead(context, "Ren")
    msg = await dead(context, "Ren")
    assert "already dead" in msg.last_reply


async def test_dead_with_no_target_explains_both_ways_of_giving_one(context):
    await start_session(context)
    msg = player_message("/dead")
    context.args = []
    await gamesession.dead_cmd(FakeUpdate(message=msg), context)
    assert "/dead" in msg.last_reply and "/ad" in msg.last_reply


# --- /ad --------------------------------------------------------------------


async def test_ad_marks_everyone_the_roster_omits_as_dead(context):
    session_data = await start_session(context)
    msg = await ad(context, roster([(1, "Ren"), (2, "omu")], total=4))

    assert session_data["players"]["3"]["alive"] is False
    assert session_data["players"]["4"]["alive"] is False
    assert session_data["players"]["1"]["alive"] is True
    assert "Now dead" in msg.last_reply


async def test_ad_revives_a_player_a_mistyped_dead_killed_off(context):
    """The game bot's roster is the authority. Being able to undo a bad /dead is the
    whole reason this is a reset rather than a diff."""
    session_data = await start_session(context)
    await dead(context, "omu")
    assert session_data["players"]["2"]["alive"] is False

    msg = await ad(context, roster([(1, "Ren"), (2, "omu"), (3, "J J"), (4, "Kay")], total=4))

    assert session_data["players"]["2"]["alive"] is True
    assert "Back among the living" in msg.last_reply


async def test_ad_learns_a_role_from_a_death_notice(context):
    """A player who never got round to /role still shapes everyone else's achievements."""
    session_data = await start_session(context)
    msg = await ad(
        context,
        roster([(1, "Ren"), (3, "J J"), (4, "Kay")], dead_rows=[("omu", "the Serial Killer \N{HOCHO}")], total=4),
    )

    assert session_data["players"]["2"]["roles"] == ["serial_killer"]
    assert "Learned from the death notices" in msg.last_reply


async def test_a_learned_role_never_overwrites_what_a_player_said_themselves(context):
    session_data = await start_session(context)
    await reveal(context, 2, "harlot")
    await ad(
        context,
        roster([(1, "Ren"), (3, "J J"), (4, "Kay")], dead_rows=[("omu", "the Serial Killer \N{HOCHO}")], total=4),
    )
    assert session_data["players"]["2"]["roles"] == ["harlot"]


async def test_a_roster_whose_counts_disagree_changes_nothing(context):
    """A full reset built on a misread would mark living players dead wholesale. The
    roster states its own counts, so the parse can be checked before anything is written."""
    session_data = await start_session(context)
    msg = await ad(context, roster([(1, "Ren")], claimed=3, total=4))

    assert "Nothing changed" in msg.last_reply
    for uid in ("1", "2", "3", "4"):
        assert session_data["players"][uid]["alive"] is True


async def test_a_roster_with_no_header_is_refused(context):
    session_data = await start_session(context)
    msg = player_message("/ad", reply_to=bot_message("some other message"))
    context.args = []
    await gamesession.follow_roster_cmd(FakeUpdate(message=msg), context)

    assert "Nothing changed" in msg.last_reply
    assert session_data["players"]["2"]["alive"] is True


async def test_ad_reports_when_the_roster_matches(context):
    await start_session(context)
    msg = await ad(context, roster([(1, "Ren"), (2, "omu"), (3, "J J"), (4, "Kay")], total=4))
    assert "nobody's status changed" in msg.last_reply


async def test_ad_without_a_reply_says_what_it_needs(context):
    await start_session(context)
    msg = player_message("/ad")
    context.args = []
    await gamesession.follow_roster_cmd(FakeUpdate(message=msg), context)
    assert "player list" in msg.last_reply


async def test_ad_runs_the_transforms_the_deaths_imply(context):
    session_data = await start_session(context)
    await reveal(context, 1, "seer")
    await reveal(context, 2, "apps")

    msg = await ad(context, roster([(2, "omu"), (3, "J J"), (4, "Kay")], total=4))

    assert session_data["players"]["2"]["roles"] == ["seer"]
    assert "the seer is gone" in msg.last_reply


async def test_ad_is_silent_without_a_session(context):
    msg = player_message("/ad", reply_to=roster([(1, "Ren")]))
    context.args = []
    await gamesession.follow_roster_cmd(FakeUpdate(message=msg), context)
    assert msg.replies == []


# --- /steal -----------------------------------------------------------------


async def test_the_thief_swaps_roles_with_their_target(context):
    session_data = await start_session(context)
    await reveal(context, 1, "thief")
    await reveal(context, 2, "seer")

    msg = player_message("/steal omu")
    context.args = ["omu"]
    await gamesession.steal_cmd(FakeUpdate(message=msg), context)

    assert session_data["players"]["1"]["roles"] == ["seer"]
    assert session_data["players"]["2"]["roles"] == ["thief"], "the robbed player becomes the Thief"
    assert "stole" in msg.last_reply


async def test_a_stolen_rolemodel_travels_with_the_role(context):
    """A stolen Wild Child means nothing without whoever they were watching."""
    session_data = await start_session(context)
    await reveal(context, 1, "thief")
    await reveal(context, 2, "wc")
    session.set_model(session_data, 2, 3)

    context.args = ["omu"]
    await gamesession.steal_cmd(FakeUpdate(message=player_message("/steal omu")), context)

    assert session_data["players"]["1"]["model"] == 3
    assert session_data["players"]["2"]["model"] is None


async def test_only_the_thief_can_steal(context):
    session_data = await start_session(context)
    await reveal(context, 1, "villager")
    await reveal(context, 2, "seer")

    msg = player_message("/steal omu")
    context.args = ["omu"]
    await gamesession.steal_cmd(FakeUpdate(message=msg), context)

    assert "Only the Thief" in msg.last_reply
    assert session_data["players"]["2"]["roles"] == ["seer"]


async def test_the_protected_roles_cannot_be_stolen_from(context):
    """Wolves, the serial killer and cultists are out of reach — a rules violation worth
    reporting rather than a swap worth recording."""
    session_data = await start_session(context)
    await reveal(context, 1, "thief")

    for role_name, role_id in (("werewolf", "werewolf"), ("sk", "serial_killer"), ("cult", "cultist")):
        await reveal(context, 2, role_name)
        msg = player_message("/steal omu")
        context.args = ["omu"]
        await gamesession.steal_cmd(FakeUpdate(message=msg), context)

        assert "out of reach" in msg.last_reply, role_id
        assert session_data["players"]["2"]["roles"] == [role_id]
        assert session_data["players"]["1"]["roles"] == ["thief"]


async def test_the_arsonist_and_the_sorcerer_can_be_stolen_from(context):
    """Confirmed by the group: only wolves, the SK and the cult are protected."""
    for role_name, role_id in (("arsonist", "arsonist"), ("sorcerer", "sorcerer")):
        context.chat_data.clear()
        session_data = await start_session(context)
        await reveal(context, 1, "thief")
        await reveal(context, 2, role_name)

        context.args = ["omu"]
        await gamesession.steal_cmd(FakeUpdate(message=player_message("/steal omu")), context)
        assert session_data["players"]["1"]["roles"] == [role_id]


async def test_stealing_from_someone_who_has_not_revealed_says_so(context):
    await start_session(context)
    await reveal(context, 1, "thief")
    msg = player_message("/steal omu")
    context.args = ["omu"]
    await gamesession.steal_cmd(FakeUpdate(message=msg), context)
    assert "hasn't revealed" in msg.last_reply


async def test_steal_is_silent_without_a_session(context):
    msg = player_message("/steal omu")
    context.args = ["omu"]
    await gamesession.steal_cmd(FakeUpdate(message=msg), context)
    assert msg.replies == []
