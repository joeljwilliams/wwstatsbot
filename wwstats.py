import db
import templates as t


def chunks(items, n):
    """Yield successive n-sized chunks from items."""
    for i in range(0, len(items), n):
        yield items[i : i + n]


def _section(items, main, section_header):
    """Render a list of achievements into one or more 30-item Markdown messages,
    each prefixed with the MISSING total line and the section header."""
    lines = [t.ACHV_LINE.format(name=z["name"], desc=z["desc"]) for z in items]
    return [main + section_header + "".join(chunk) for chunk in chunks(lines, 30)]


def check(stats):
    """The /achievements report for one player, from their attained-achievement list.

    Takes the API's answer rather than fetching it. This module used to build its own URL
    against tgwerewolf.com and do its own GET, which made it the one lookup in the bot that
    api.py did not own — and therefore the one that no shared behaviour could ever reach.
    Handing it the list instead puts /achievements behind the same fetcher as everything
    else, and leaves this module doing only what it is named for: bucketing and chunking.
    """
    achvs = db.get_achievements()
    achv_names = {a["name"] for a in achvs}
    total = len(achvs)

    attained_count = len(stats)
    attained_names = [each["name"] for each in stats]
    not_via_playing = [z for z in achvs if z["name"] not in attained_names and z.get("not_via_playing")]
    inactive = [z for z in achvs if z["name"] not in attained_names and z.get("inactive")]
    missing = [
        z for z in achvs if z["name"] not in attained_names and not (z.get("inactive") or z.get("not_via_playing"))
    ]

    msgs = []
    attained = ""
    for each in stats:
        if each["name"] in achv_names:
            attained += "- {}\n".format(each["name"])
    msgs.append(t.ATTAINED_HEADER.format(attained=attained_count, total=total) + "```" + attained + "```")

    main = t.MISSING_MAIN.format(missing=total - attained_count, total=total)
    msgs += _section(missing, main, t.MISSING_HEADER.format(count=len(missing), total=total))
    msgs += _section(not_via_playing, main, t.NOT_VIA_PLAYING_HEADER.format(count=len(not_via_playing), total=total))
    msgs += _section(inactive, main, t.INACTIVE_HEADER.format(count=len(inactive), total=total))

    return msgs
