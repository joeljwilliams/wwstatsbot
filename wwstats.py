import db
import templates as t


def chunks(l, n):
    """Yield successive n-sized chunks from l."""
    for i in range(0, len(l), n):
        yield l[i:i + n]


def _section(items, main, section_header):
    """Render a list of achievements into one or more 30-item Markdown messages,
    each prefixed with the MISSING total line and the section header."""
    lines = [t.ACHV_LINE.format(name=z['name'], desc=z['desc']) for z in items]
    return [main + section_header + "".join(chunk) for chunk in chunks(lines, 30)]


async def check(userid, client):
    achvs = db.get_achievements()
    achv_names = {a['name'] for a in achvs}
    total = len(achvs)

    url = "https://tgwerewolf.com/stats/PlayerAchievements/?pid={}&json=true".format(userid)
    r = await client.get(url)
    stats = r.json()
    attained_count = len(stats)
    attained_names = [each['name'] for each in stats]
    not_via_playing = [z for z in achvs if z['name'] not in attained_names and z.get("not_via_playing")]
    inactive = [z for z in achvs if z['name'] not in attained_names and z.get("inactive")]
    missing = [z for z in achvs if z['name'] not in attained_names and not (z.get("inactive") or z.get("not_via_playing"))]

    msgs = []
    attained = ""
    for each in stats:
        if each['name'] in achv_names:
            attained += "- {}\n".format(each['name'])
    msgs.append(t.ATTAINED_HEADER.format(attained=attained_count, total=total) + "```" + attained + "```")

    main = t.MISSING_MAIN.format(missing=total - attained_count, total=total)
    msgs += _section(missing, main, t.MISSING_HEADER.format(count=len(missing), total=total))
    msgs += _section(not_via_playing, main, t.NOT_VIA_PLAYING_HEADER.format(count=len(not_via_playing), total=total))
    msgs += _section(inactive, main, t.INACTIVE_HEADER.format(count=len(inactive), total=total))

    return msgs
