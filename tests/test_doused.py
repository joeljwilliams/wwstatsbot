"""Reading the Arsonist's doused list out of a forwarded message.

The game bot tells the arsonist in private, in prose:

    Do you want to douse another house or do you want to see all doused houses burn?

    You have already doused the house of: -mæd and Juenta and ᐝѕнαяиαѕ <🌸> 🥉

Forwarded into the chat, that is a count and a set of players nobody can tap. This turns it
into both. Two things make it more than a string split, and each is tested below:

* **" and " is inside real names.** It is the game bot's separator *and* two letters of
  names people play under, so the roster is used as evidence for where one house ends.
* **The names are hostile.** Angle brackets, emoji, and a leading dash that is *part of
  the name* rather than a bullet — all three appear in the real message above, and each has
  broken a parser in this repo before.
"""

from conftest import FakeContext, FakeEntity, FakeUpdate, FakeUser, message

import session
from handlers import gamesession

DOUSED = "You have already doused the house of: {}"
PREAMBLE = "Do you want to douse another house or do you want to see all doused houses burn?\n\n"


def roster(*players):
    """A chat with a running session over these players."""
    chat_data = {}
    session.start(chat_data, 1, [(uid, name) for uid, name in players], [], 1000.0)
    return chat_data


async def forwarded(text, chat_data=None, entities=None):
    msg = message(text, from_user=FakeUser(1, "Arsonist"), entities=entities)
    ctx = FakeContext(chat_data=chat_data if chat_data is not None else {})
    await gamesession.doused_forward(FakeUpdate(message=msg), ctx)
    return msg


def linked_names(*players):
    """A doused line whose names the game bot linked, as text_mention entities.

    Offsets are UTF-16 units, which is the whole reason these are built rather than
    hand-counted: these names are full of characters that cost two apiece.
    """
    body = PREAMBLE + DOUSED.format(" and ".join(name for _, name in players))
    units = len(PREAMBLE.encode("utf-16-le")) // 2 + len(DOUSED.format("").encode("utf-16-le")) // 2
    entities = []
    for uid, name in players:
        length = len(name.encode("utf-16-le")) // 2
        entities.append(FakeEntity("text_mention", units, length, FakeUser(uid, name)))
        units += length + len(" and ".encode("utf-16-le")) // 2
    return body, entities


# --- What counts as a doused list --------------------------------------------------


async def test_a_forward_that_is_not_a_doused_list_is_ignored():
    """This handler sees every forward in the chat, so silence is the default."""
    msg = await forwarded("Night falls. Everyone goes to sleep.")
    assert msg.replies == []


async def test_the_preamble_alone_says_nothing():
    """The question varies with what the arsonist can still do; only the line that carries
    names is matched."""
    msg = await forwarded(PREAMBLE)
    assert msg.replies == []


# --- Counting and listing ----------------------------------------------------------


async def test_the_houses_are_counted_against_the_living_roster():
    chat_data = roster((7, "Cinder"), (8, "KAI"), (9, "King"), (10, "Ash"))
    msg = await forwarded(PREAMBLE + DOUSED.format("Cinder and KAI and King"), chat_data)
    assert msg.last_reply.startswith("<b>Doused (3/4)</b>\n")


async def test_a_dead_player_is_out_of_the_denominator():
    """The arsonist douses living houses, so the living roster is what the count is of."""
    chat_data = roster((7, "Cinder"), (8, "KAI"), (9, "King"), (10, "Ash"))
    session.set_alive(session.get(chat_data), 10, False)
    msg = await forwarded(PREAMBLE + DOUSED.format("Cinder and KAI"), chat_data)
    assert msg.last_reply.startswith("<b>Doused (2/3)</b>\n")


async def test_each_house_is_one_row():
    chat_data = roster((7, "Cinder"), (8, "KAI"), (9, "King"))
    msg = await forwarded(PREAMBLE + DOUSED.format("Cinder and KAI and King"), chat_data)
    assert msg.last_reply.count("\N{FIRE}") == 3


async def test_a_matched_house_is_a_tappable_mention():
    chat_data = roster((7, "Cinder"), (8, "KAI"))
    msg = await forwarded(PREAMBLE + DOUSED.format("Cinder and KAI"), chat_data)
    assert "<a href='tg://user?id=7'>Cinder</a>" in msg.last_reply
    assert "<a href='tg://user?id=8'>KAI</a>" in msg.last_reply


async def test_a_single_house_needs_no_separator():
    chat_data = roster((7, "Cinder"), (8, "KAI"))
    msg = await forwarded(PREAMBLE + DOUSED.format("Cinder"), chat_data)
    assert msg.last_reply.startswith("<b>Doused (1/2)</b>\n")
    assert "<a href='tg://user?id=7'>Cinder</a>" in msg.last_reply


async def test_a_roster_too_small_to_hold_the_count_prints_no_denominator():
    """A session started from an older player list — or one a mistyped /dead shrank — has
    fewer names than the game does. "Doused (3/1)" is worse than no denominator at all."""
    chat_data = roster((7, "Cinder"))
    msg = await forwarded(PREAMBLE + DOUSED.format("Cinder and KAI and King"), chat_data)
    assert msg.last_reply.startswith("<b>Doused (3)</b>\n")


async def test_a_name_the_roster_does_not_know_is_still_listed():
    """An unrecognised name is still a doused house. Plain text rather than dropped —
    losing a house would understate the count, which is the whole point of the message."""
    chat_data = roster((7, "Cinder"), (8, "KAI"), (9, "King"))
    msg = await forwarded(PREAMBLE + DOUSED.format("Cinder and Stranger"), chat_data)
    assert msg.last_reply.startswith("<b>Doused (2/3)</b>\n")
    assert "\N{FIRE} Stranger\n" in msg.last_reply


# --- Hostile names -----------------------------------------------------------------


async def test_the_real_message_parses():
    """The exact line from a live game, names and all."""
    chat_data = roster((7, "-mæd"), (8, "Juenta"), (9, "ᐝѕнαяиαѕ <\N{CHERRY BLOSSOM}> \N{THIRD PLACE MEDAL}"))
    msg = await forwarded(
        PREAMBLE + DOUSED.format("-mæd and Juenta and ᐝѕнαяиαѕ <\N{CHERRY BLOSSOM}> \N{THIRD PLACE MEDAL}"),
        chat_data,
    )
    assert msg.last_reply.startswith("<b>Doused (3/3)</b>\n")
    assert "<a href='tg://user?id=7'>-mæd</a>" in msg.last_reply


async def test_angle_brackets_in_a_name_are_escaped():
    """Unescaped, one player's name takes the whole message down."""
    chat_data = roster((7, "Cinder"))
    msg = await forwarded(PREAMBLE + DOUSED.format("Cinder and <b>oops</b>"), chat_data)
    assert "&lt;b&gt;oops&lt;/b&gt;" in msg.last_reply
    assert "<b>oops</b>" not in msg.last_reply


async def test_an_ampersand_in_a_name_is_escaped():
    chat_data = roster((7, "Al & Sons"))
    msg = await forwarded(PREAMBLE + DOUSED.format("Al & Sons"), chat_data)
    assert "Al &amp; Sons" in msg.last_reply


# --- "and" inside a name -----------------------------------------------------------


async def test_a_name_containing_and_is_kept_whole_when_the_roster_knows_it():
    """The case a plain split gets wrong: one house, not two."""
    chat_data = roster((7, "Sand and Ashes"), (8, "KAI"))
    msg = await forwarded(PREAMBLE + DOUSED.format("Sand and Ashes and KAI"), chat_data)

    assert msg.last_reply.startswith("<b>Doused (2/2)</b>\n")
    assert "<a href='tg://user?id=7'>Sand and Ashes</a>" in msg.last_reply
    assert "<a href='tg://user?id=8'>KAI</a>" in msg.last_reply


async def test_the_greedy_merge_does_not_swallow_a_following_player():
    """Merging is only ever justified by a match, so two players who each resolve on their
    own must stay two — otherwise every list would collapse into one long name."""
    chat_data = roster((7, "Cinder"), (8, "KAI"))
    msg = await forwarded(PREAMBLE + DOUSED.format("Cinder and KAI"), chat_data)
    assert msg.last_reply.count("\N{FIRE}") == 2


async def test_without_a_session_the_plain_split_stands():
    """No roster means no evidence, and a guess with nothing to check it against is worse
    than the obvious reading. The count still holds, without a denominator."""
    msg = await forwarded(PREAMBLE + DOUSED.format("Cinder and KAI and King"))
    assert msg.last_reply.startswith("<b>Doused (3)</b>\n")
    assert "tg://user" not in msg.last_reply, "there is nobody to link to"


# --- Names the game bot linked itself ----------------------------------------------


async def test_a_linked_list_needs_no_roster_at_all():
    """The message carries the ids, so this works in a chat that never ran /gs — which is
    most chats a forward lands in."""
    body, entities = linked_names((7, "Juenta"), (8, "KAI \N{SPARKLES}"), (9, "mui"))
    msg = await forwarded(body, entities=entities)

    assert msg.last_reply.startswith("<b>Doused (3)</b>\n")
    assert "<a href='tg://user?id=7'>Juenta</a>" in msg.last_reply
    assert "<a href='tg://user?id=8'>KAI \N{SPARKLES}</a>" in msg.last_reply


async def test_a_printed_mention_beats_the_roster():
    """The game bot naming a player outright is better evidence than a name match, and is
    right even for somebody the roster has never heard of."""
    chat_data = roster((99, "Juenta"))
    body, entities = linked_names((7, "Juenta"))
    msg = await forwarded(body, chat_data, entities=entities)
    assert "<a href='tg://user?id=7'>Juenta</a>" in msg.last_reply


async def test_an_html_mention_arrives_as_a_text_link():
    """The other spelling: <a href="tg://user?id=…"> is delivered as a text_link, not a
    text_mention, and carries the id in its url."""
    name = "Toto Sylvain"
    body = PREAMBLE + DOUSED.format(name)
    offset = len(PREAMBLE.encode("utf-16-le")) // 2 + len(DOUSED.format("").encode("utf-16-le")) // 2
    entities = [FakeEntity("text_link", offset, len(name), url="tg://user?id=42")]
    msg = await forwarded(body, entities=entities)

    assert "<a href='tg://user?id=42'>Toto Sylvain</a>" in msg.last_reply


async def test_an_ordinary_link_is_not_read_as_a_player():
    """A text_link to anywhere else names nobody."""
    name = "Juenta"
    body = PREAMBLE + DOUSED.format(name)
    offset = len(PREAMBLE.encode("utf-16-le")) // 2 + len(DOUSED.format("").encode("utf-16-le")) // 2
    entities = [FakeEntity("text_link", offset, len(name), url="https://example.com")]
    msg = await forwarded(body, entities=entities)

    assert msg.last_reply == "<b>Doused (1)</b>\n\N{FIRE} Juenta\n"


async def test_the_second_live_message_parses():
    """Eight houses, from a real game. Every name in it is awkward in a different way:
    script letters, a rare sign, an internal space, trailing punctuation, invisible Hangul
    filler padding, and emoji — and none of them is separated by anything but " and "."""
    players = (
        (1, "Juenta"),
        (2, "\U0001d4b7\u212f\U0001d4be \U000130fc"),
        (3, "Toto Sylvain"),
        (4, "KAI \N{SPARKLES}"),
        (5, "beardyshu . \u0781\u208a \u22b9 ."),
        (6, "\u3164\u3164 B\u03b1tman \u2219 \u269c"),
        (7, "Anoop Krishna \N{THIRD PLACE MEDAL}"),
        (8, "mui"),
    )
    body, entities = linked_names(*players)
    msg = await forwarded(body, entities=entities)

    assert msg.last_reply.startswith("<b>Doused (8)</b>\n")
    assert msg.last_reply.count("\N{FIRE}") == 8
    for uid, name in players:
        assert "tg://user?id={}".format(uid) in msg.last_reply, name


async def test_the_same_live_message_as_plain_text_still_counts_eight():
    """If the names ever arrive unlinked, the count must not change — it is the number the
    whole message exists to report."""
    names = [
        "Juenta",
        "\U0001d4b7\u212f\U0001d4be \U000130fc",
        "Toto Sylvain",
        "KAI \N{SPARKLES}",
        "beardyshu . \u0781\u208a \u22b9 .",
        "\u3164\u3164 B\u03b1tman \u2219 \u269c",
        "Anoop Krishna \N{THIRD PLACE MEDAL}",
        "mui",
    ]
    msg = await forwarded(PREAMBLE + DOUSED.format(" and ".join(names)))

    assert msg.last_reply.startswith("<b>Doused (8)</b>\n")
    assert msg.last_reply.count("\N{FIRE}") == 8
