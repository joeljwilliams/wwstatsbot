"""Extracting checkable players from a message's mention entities.

Only `text_mention` entities carry a full User (and therefore an id), and the stats API
is keyed by user id — so plain `@username` mentions are fundamentally uncheckable. They
are reported back to the user rather than silently dropped, which is the behaviour worth
pinning here.
"""

from conftest import FakeEntity, FakeUser, bot_message, message

from handlers import common, search

TEXT_MENTION = "text_mention"
MENTION = "mention"


def mention_entity(user, offset=0, length=5):
    return FakeEntity(TEXT_MENTION, offset=offset, length=length, user=user)


def username_entity(offset, length):
    return FakeEntity(MENTION, offset=offset, length=length)


def test_extracts_text_mentions_with_ids():
    msg = message(
        "Alice Bob",
        entities=[
            mention_entity(FakeUser(1, "Alice"), 0, 5),
            mention_entity(FakeUser(2, "Bob"), 6, 3),
        ],
    )
    users, unresolved = common.mentioned_users(msg)
    assert users == [(1, "Alice"), (2, "Bob")]
    assert unresolved == []


def test_plain_username_mentions_are_returned_as_unresolved():
    """@handles have no user id, so they can't be looked up — but must be reported."""
    msg = message(
        "hi @dave and @erin",
        entities=[
            username_entity(3, 5),  # @dave
            username_entity(13, 5),  # @erin
        ],
    )
    users, unresolved = common.mentioned_users(msg)
    assert users == []
    assert unresolved == ["@dave", "@erin"]


def test_mixed_mentions():
    msg = message(
        "Alice @dave",
        entities=[
            mention_entity(FakeUser(1, "Alice"), 0, 5),
            username_entity(6, 5),
        ],
    )
    users, unresolved = common.mentioned_users(msg)
    assert users == [(1, "Alice")]
    assert unresolved == ["@dave"]


def test_bots_are_skipped():
    """A mentioned bot has no player stats, so it could only pad the 'hasn't' list."""
    msg = message(
        "Alice WerewolfBot",
        entities=[
            mention_entity(FakeUser(1, "Alice"), 0, 5),
            mention_entity(FakeUser(42, "WerewolfBot", is_bot=True), 6, 11),
        ],
    )
    users, _ = common.mentioned_users(msg)
    assert users == [(1, "Alice")]


def test_duplicate_users_are_deduped_first_seen_first():
    msg = message(
        "Alice Alice",
        entities=[
            mention_entity(FakeUser(1, "Alice"), 0, 5),
            mention_entity(FakeUser(1, "Alice"), 6, 5),
        ],
    )
    users, _ = common.mentioned_users(msg)
    assert users == [(1, "Alice")]


def test_entity_without_a_user_object_is_ignored():
    msg = message("Alice", entities=[FakeEntity(TEXT_MENTION, 0, 5, user=None)])
    assert common.mentioned_users(msg) == ([], [])


def test_reads_caption_entities_on_media_messages():
    """Media messages carry text in `caption` with `caption_entities`."""
    msg = message(
        text=None,
        caption="Alice @dave",
        caption_entities=[
            mention_entity(FakeUser(1, "Alice"), 0, 5),
            username_entity(6, 5),
        ],
    )
    users, unresolved = common.mentioned_users(msg)
    assert users == [(1, "Alice")]
    assert unresolved == ["@dave"]


def test_no_entities_at_all():
    assert common.mentioned_users(message("just text")) == ([], [])


# --- The reply shape that reroutes /sch to the multi-player path ------------------


def test_bot_reply_with_players_is_detected():
    msg = bot_message("Alice", entities=[mention_entity(FakeUser(1, "Alice"), 0, 5)])
    assert search._is_bot_player_reply(msg) is True


def test_none_is_not_a_bot_player_reply():
    assert search._is_bot_player_reply(None) is False


def test_human_reply_is_not_a_bot_player_reply():
    """A human's message mentioning players must still mean 'check the author'."""
    msg = message(
        "Alice", from_user=FakeUser(1, "Alice", is_bot=False), entities=[mention_entity(FakeUser(2, "Bob"), 0, 5)]
    )
    assert search._is_bot_player_reply(msg) is False


def test_bot_reply_without_player_mentions_is_not_detected():
    assert search._is_bot_player_reply(bot_message("no mentions here")) is False


def test_bot_reply_mentioning_only_usernames_is_not_detected():
    """@handles aren't checkable, so there is nothing to fan out to."""
    msg = bot_message("@dave", entities=[username_entity(0, 5)])
    assert search._is_bot_player_reply(msg) is False


def test_bot_reply_mentioning_only_bots_is_not_detected():
    msg = bot_message("SomeBot", entities=[mention_entity(FakeUser(43, "SomeBot", is_bot=True), 0, 7)])
    assert search._is_bot_player_reply(msg) is False
