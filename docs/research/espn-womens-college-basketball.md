# What ESPN offers for women's college basketball

Research for issue #2. Written 2026-11-18, during the 2026-27 season (ESPN season `2027`, week 3 of the regular season).

**Question.** Which datasets do ESPN's public site and core endpoints offer for women's college basketball, and which of them are candidates for the first slice from fetch to gold?

**Short answer.** The daily **scoreboard** is the backbone: one call per day lists every Division I game for that day (past, live or scheduled), back to the 2000-01 season and ahead to the start of March (the end of the regular season), and its game ids drive every per-game dataset. The **summary** is the richest per-game dataset (team box, player box, plays, win probability, officials, one sportsbook line), but costs one call per game and its depth falls off sharply before about 2020. Teams, rosters, standings and rankings are small reference or snapshot datasets. The proposal (section 6) is the scoreboard alone as the first slice, with the summary's team box as the second dataset, driven by the scoreboard's game ids.

## How this was done, and how to read the claims

- **Calls:** 117 unauthenticated GET requests with plain `curl` (default client identification), sequential and at least 1.5 s apart, between 21:26 and 21:38 UTC on 2026-11-18. Every response is described here by structure only; none is committed. 113 returned 200 and 4 did not (two scoreboard date ranges answered 400, the site `calendar` path answered 404, the power index answered 400; see the table in section 5). No challenge page, no rate-limit response and no slowdown were seen; most calls took 0.05 to 0.3 s, the largest (6.4 MB) 1.4 s.
- **Sources read:** the ESPN wrappers in the sportsdataverse projects, as an index of what exists:
  - `wehoop` (R): <https://github.com/sportsdataverse/wehoop/tree/main/R>, mainly `espn_wbb_data.R`, `espn_basketball_event_betting_helpers.R`, `load_wbb.R`.
  - `sportsdataverse-py`: <https://github.com/sportsdataverse/sportsdataverse-py/tree/main/sportsdataverse/wbb>, mainly `wbb_schedule.py` and `wbb_espn_ext.py` (which lists about a hundred core-API paths with example URLs).
- **Tags.** Every claim carries one:
  - **[call]**: verified by a call on 2026-11-18. Where a claim rests on one or two samples, it says so.
  - **[source: file]**: read in one of the files above, not called.
  - **[unchecked]**: neither; stated as a question, not a fact.
  - **[estimate]**: arithmetic on [call] figures, with the inputs shown.

## 1. Hosts and ids

| Host | What it is | Cache-Control seen [call] |
|---|---|---|
| `site.api.espn.com/apis/site/v2/sports/basketball/womens-college-basketball/...` | page-shaped JSON: scoreboard, summary, teams, roster, team schedule, rankings, news, injuries | `max-age` from 1 to 159 s (scoreboard 1 to 7, summary 9, teams 125 to 159) |
| `site.api.espn.com/apis/v2/sports/basketball/womens-college-basketball/standings` | standings (note `apis/v2`, not `apis/site/v2`) | 1 to 60 s |
| `sports.core.api.espn.com/v2/sports/basketball/leagues/womens-college-basketball/...` | normalised resources linked by `$ref` URLs; paged lists (`count`, `pageIndex`, `pageSize`, `pageCount`, `items`) | `max-age=900, stale-while-revalidate=7200` on every core response |
| `cdn.espn.com/core/womens-college-basketball/scoreboard?xhr=1` | the scoreboard wrapped in web-page data (`content.sbData`, plus news and ads blocks) | 300 s |

- League id `54`, sport id `40`; uids look like `s:40~l:54~e:<game_id>` [call].
- A game is an *event* with one *competition* of the same id; core paths repeat it: `events/<id>/competitions/<id>/...` [call].
- No response carried `ETag` or `Last-Modified` [call, all 117 headers]. Conditional requests are therefore not available as a change test [unchecked whether ESPN honours `If-Modified-Since` anyway].
- Core `$ref` links are `http://`, and some site responses (rankings, the summary's `pickcenter`) contain `$ref` links to an internal host `sports.core.api.espn.pvt` that cannot be followed from outside [call]. A fetcher that follows links has to rewrite them to `https://sports.core.api.espn.com`.

## 2. The datasets compared

"Ahead" and "back" are what answered on 2026-11-18. Every cell is from calls unless it says [unchecked]; details and sample sizes are in section 3.

| Dataset | Call | Grain of one response | Size | Ahead | Back | How it changes | Limits and quirks | Drives |
|---|---|---|---|---|---|---|---|---|
| **Scoreboard** | site `scoreboard?dates=YYYYMMDD&groups=50&limit=500` | all D-I games on one day (by US Eastern date) | 0.55 to 1.7 MB, about 14 KB per game; 41 to 120 games a day in samples | regular season to early March (one game on 2027-03-20); conference tournaments and NCAA tournament not yet listed | answers to 2000-11 (season 2001); 1999-11 returned 0 games | status, scores, linescores, attendance, records move until final; postponed games keep their id and status | `groups=50` needed, else about 10 featured games; `limit` truncates silently; no date ranges (400); first fetch of a rarely asked URL can be hours stale | game ids for summary and every core per-game path; calendar of game days |
| **Summary** | site `summary?event=<id>` | one game | 125 KB (no data) to 515 KB (full) | pre-game: venue, `predictor`, `pickcenter`, `lastFiveGames`; no box | box and plays back to about 2008 for some games; team box for nearly all games by 2019; plays for nearly all only between 2019 and 2022 | live: plays appended, box updated; after final, no change seen in 8 min | 390 to 525 plays returned in full; no 467 cap seen; top-level key order varies between identical fetches | athlete ids, official names, the line |
| **Calendar** | core `calendar/ondays`; also inside every scoreboard response (`leagues[0].calendar`) | list of game days | 3.6 KB | through 2027-02-28 (core) or 2027-03-20 (scoreboard with `groups=50`) | seasons list goes back to 1982 | grows as games are scheduled | site path `calendar` is 404; the core path ignores the season in it | which dates to fetch |
| **Week event lists** | core `seasons/<yr>/types/2/weeks/<n>/events?limit=1000` | ids of one week's games | 13 to 46 KB, 100 to 348 ids | weeks 3 to 16 populated; week 19 (conference tournaments) empty | 2024 and 2005 weeks answer | grows | ids only; one D-I home game against a non-D-I visitor seen on the scoreboard was missing here | per-game core paths |
| **Core per-game resources** | core `events/<id>/competitions/<id>/{status, competitors, competitors/<team>/statistics, competitors/<team>/roster, plays, probabilities, odds, officials}` | one facet of one game | 0.3 KB to 0.9 MB | as for summary | as for summary | plays carry `modified` and `valid` | 6 to 10 calls per game for what one summary holds | |
| **Odds** | core `.../competitions/<id>/odds`; summary `pickcenter`; scoreboard `competitions[0].odds` | one game, one item per sportsbook | 4 to 41 KB | lines on 10 of 68 games on game day; none for a game in January | 2024 final: 9 books with open and close; two other past games: none | open, close and current values | sparse; scoreboard and pickcenter drop lines that core still holds | |
| **Teams** | site `teams?limit=1000` | every D-I team | 1.5 MB, 363 teams | current | [unchecked] | [unchecked] | `groups=50` returned identical bytes | team ids, logos, colours |
| **Roster** | site `teams/<id>/roster` | one team's players and coach | 55 KB, 15 players | current season | [unchecked] for past seasons | through the season [unchecked] | one call per team, 363 a season | athlete ids |
| **Team schedule** | site `teams/<id>/schedule[?season=YYYY]` | one team's games for a season | 0.3 to 0.6 MB | to end of regular season | `season=2024` answered, 38 games including the NCAA final | as scoreboard | 363 calls per season | |
| **Standings** | `apis/v2/.../standings[?season=YYYY]` | every D-I team, by conference | 6.1 MB, 363 teams, 84 stats each | current | `season=2024` answered | changes after every game; a snapshot with no as-of date | heavy; one season per call | |
| **Rankings** | site `rankings`; core `seasons/<yr>/types/2/weeks/<n>/rankings/<poll>` | one poll for one week (AP, Coaches) | 30 KB per poll and week | the current week | core: 2024 week 10 and 2010 week 10 answered | a new poll each week | site `rankings?seasons=2024&weeks=10` returned an empty list | |
| Also present | site `news`, `injuries` (empty), cdn scoreboard; core `seasons/<yr>/athletes` (4,874 in 2027), groups, venues, coaches, awards | | | | | | power index is not offered for this league (400) | |

## 3. Each dataset in detail

### 3.1 Scoreboard

**The call.** `https://site.api.espn.com/apis/site/v2/sports/basketball/womens-college-basketball/scoreboard?dates=YYYYMMDD&groups=50&limit=500` [call].

- `groups=50` is Division I [source: `wbb_schedule.py` docstring; call: the response's `groups` key and the counts below]. Without it, the same date returned 10 games instead of 68, the featured ones, all with betting lines [call]. `groups=51` returned 10 games that look like D-I teams against non-D-I opponents [call, one sample; `wbb_schedule.py` calls 51 "Division II/Division III", which this sample does not obviously match]. `groups=100` returned 16 NCAA tournament games for 2024-03-23 [call; source: `wehoop` `parse_espn_wbb_scoreboard` docs "100 - NCAA Tournament"].
- `limit` truncates silently: `limit=5` returned 5 games and nothing in the response says more exist (top-level keys are only `leagues`, `groups`, `events`, `provider`, `eventsDate`) [call]. Leaving `limit` out with `groups=50` still returned all 119 games of 2024-01-20 [call], so the default is at least 119; the largest day sampled was 120 games [call]. `wehoop` uses `limit=1000` [source], `sportsdataverse-py` 500 [source].
- `dates=YYYYMMDD-YYYYMMDD` answered 400 `{"code":400,"message":"Failed to get events endpoint."}` for both a 7-day and a 2-day range [call; the 400 body is gzip-compressed even though the request did not ask for it]. Not tried again.
- `dates=2027` returned 500 games, all scheduled, dated 2027-01-01 to 2027-01-10, at 6.4 MB [call]: the first `limit` games of the calendar year, not a season. That matches `wbb_schedule.py`: "ESPN answers a year with its first scoreboard page (early January only)" [source].

**Grain and fields.** One response is a day; `events[]` holds one game each [call]. A game's date is the US Eastern day: the 2026-11-17 response included games at `2026-11-18T00:00Z` and `02:00Z` [call], so keying by the UTC date of `events[].date` puts evening games on the wrong day. Per game:

- `id`, `date`, `name`, `shortName`, `season {year, type, slug}`, `status {clock, period, type.name}` [call].
- `competitions[0]`: `attendance`, `neutralSite`, `conferenceCompetition`, `playByPlayAvailable`, `venue`, `notes`, `broadcasts`, `format`, and, depending on state, `situation` (live), `odds` (pre-game, some games), `headlines` (final) [call].
- `competitors[]` (home and away): `team` (id, names, logo), `score` (a string), `winner` (final only), `linescores` (per period), 15 team statistics (`points`, `rebounds`, `fieldGoalsMade`, ...), `leaders`, `curatedRank`, `records` (overall, home, road, vs. conference) [call].
- Status names seen: `STATUS_SCHEDULED`, `STATUS_IN_PROGRESS`, `STATUS_HALFTIME`, `STATUS_END_PERIOD`, `STATUS_FINAL`, `STATUS_CANCELED`, `STATUS_POSTPONED`, `STATUS_FORFEIT` [call].
- `leagues[0].calendar` lists every game day of the season: 123 dates to 2027-03-20 with `groups=50`, 108 to 2027-02-28 without [call].

**Ahead.** Sampled days had games listed through the start of March: 28 on 2026-12-10, 9 on Friday 2027-01-15, 6 on 2027-03-01, 1 on 2027-03-20 [call]; the calendar in the `groups=50` response lists game days to 2027-03-06 and then 2027-03-20 [call]. Week lists from the core API show full weeks of about 320 to 350 games through week 16 (mid-February) and an empty week 19 (8 to 14 March), the conference tournament week [call]. The 2027 NCAA final day (2027-04-04) returned 0 games [call]. So the schedule reaches about three months ahead now, and postseason games appear only when they are set [unchecked when].

**Back.** Samples, one Saturday in January per season unless noted, counting final games [call]:

| Season | Games | Linescores | Team stats | `playByPlayAvailable` |
|---|---|---|---|---|
| 2001 (2000-11-18) | 73 | 0 | 0 | 0 |
| 2002 | 106 | 0 | 0 | 0 |
| 2005 | 99 | 99 (halves) | 0 | 0 |
| 2008 | 115 | 115 | 28 | 29 |
| 2010 | 120 | 120 | 5 | 5 |
| 2015 | 109 | 109 (halves) | 6 | 6 |
| 2019 | 107 | 107 (quarters) | 107 | 6 |
| 2022 | 111 | 111 | 111 | 107 |
| 2024 | 117 | 117 | 117 | 116 |
| 2027 (2026-11-17) | 46 | 46 | 46 | 46 |

Before season 2001 the call still answers 200 but empty, and the response's `season` block falls back to the current season [call: 1999-11-20]. Periods changed from halves to quarters between 2015 and 2019 (the format block in each summary says which) [call].

**How it changes.**

- Live: status, clock, linescores, scores, team stats and leaders move; `situation.lastPlay` is present while live and dropped at the end; `winner` appears at final; the attendance went from 0 while live to 8,838 at final [call: one game, compared between the stale and the current copy of the `groups=51` scoreboard described under Quirks].
- A final day (2026-11-17) re-fetched after 10 minutes was unchanged leaf for leaf [call, one sample]. Whether scores or stats are corrected days later is [unchecked]; the core `corrections` resource exists but was empty for 2024 and 2027 [call].
- A postponed game keeps its id, its original date and `STATUS_POSTPONED` for good: 401574714 (2024-01-20) still says so. The same two teams have a game with a new id (401633359) on 2024-02-15 local time, which looks like the make-up game [call; that it is the make-up is an inference]. A canceled game stays listed with `STATUS_CANCELED` [call].

**Quirks.**

- **Stale first answers.** Two scoreboard URLs that are rarely requested came back with old content although their header said `max-age=1`: `groups=51` for the current day showed the state of about 18:55 UTC when fetched at 21:35, two and a half hours old, and was current when fetched again at 21:37; `limit=5` showed games as in progress that the `limit=500` response fetched 30 s earlier showed as final [call, 2 samples]. It looks like a cache that serves what it holds and refreshes behind it. A fetcher cannot trust a single fetch of a URL nobody else asks for to be current; a second fetch a little later, or a check on the response's own timestamps, is needed.
- **Payload weight.** About 14 KB per game, most of it links, logos, broadcasts and leaders [call].

### 3.2 Summary

**The call.** `https://site.api.espn.com/apis/site/v2/sports/basketball/womens-college-basketball/summary?event=<game_id>` [call]. Top-level blocks (present in all states): `header`, `boxscore`, `format`, `gameInfo`, `leaders`, `broadcasts`, `pickcenter`, `odds` (empty in every sample), `againstTheSpread`, `news`, `standings`, `videos`, `meta`, `wallclockAvailable`, `winprobability`. Present depending on state: `plays`, `article`; pre-game only: `predictor`, `lastFiveGames`, `ticketsInfo` [call].

**A full game** (401918851, final 2026-11-17, 515 KB) [call]:

- `boxscore.teams[]`: 24 team statistics each (`fieldGoalsMade-fieldGoalsAttempted` as `"19-60"`, percentages as strings).
- `boxscore.players[]`: per team, athletes (13 and 15) with `stats[]` aligned to `labels` `MIN, PTS, FG, 3PT, FT, REB, AST, TO, STL, BLK, OREB, DREB, PF`, plus `starter`, `didNotPlay`, `ejected`, athlete id, jersey and position.
- `plays[]`: 484, each with `id`, `sequenceNumber`, `type {id, text}`, `text`, `awayScore`, `homeScore`, `period`, `clock`, `scoringPlay`, `scoreValue`, `team`, `participants[]` (athlete ids), `wallclock`, `shootingPlay`, `coordinate {x, y}`, `pointsAttempted`, `shortDescription`.
- `winprobability[]`: 481 entries of `{homeWinPercentage, tiePercentage, playId}`.
- `gameInfo`: venue, attendance, 3 officials with names and positions.
- `pickcenter[]`: one DraftKings line (spread, total, spread odds, `favoriteAtOpen`).
- `meta`: `lastUpdatedAt`, `firstPlayWallClock`, `lastPlayWallClock`, `gameState` (`pre`, `in`, `post`); for this game `lastUpdatedAt` equals the last play's wall clock, 01:51:56 UTC, about 2 hours after tip-off.
- `header.competitions[0]`: `boxscoreSource` and `playByPlaySource` (`full`, `none`), `boxscoreAvailable`, `shotChartAvailable` and other flags.

**The 467-play question.** Five final games were compared with the core API's `plays` count: summaries held 390, 399, 411, 484 and 525 plays (two of them overtime games), and each equalled the core count [call]. A sixth, the 2024 national final, held 360 and was not compared. For women's college basketball no cap at 467 was seen; the earlier three summaries with 467 plays were not of these games, and their league is not recorded here.

**Play order.** In 401918851 the plays are in game-clock order, not id order: two plays at 7:58 and 7:57 of the fourth quarter entered at 01:30 to 01:31 UTC sit after a play at 7:59 entered at 01:39 [call, core `modified` and `wallclock`]. Ids and `sequenceNumber` are therefore not a time order; a play can be inserted before plays already received.

**Live and after.**

- During a live game (401916154) a second fetch 9 minutes later had 125 plays instead of 92: 33 new, none dropped, none of the 92 changed [call].
- A final summary fetched again after 8 minutes had the same content leaf for leaf, but **the bytes differed**: the top-level blocks came in a different order (`header` before `plays` in one, after in the other) [call]. A hash of the raw bytes will report a change where there is none; content comparison needs canonical JSON.
- Something is rewritten after the final: all 481 core win-probability entries of 401918851 carry `lastModified` between 03:03 and 04:56 UTC, one to three hours after the final play at 01:56, while its plays' `modified` times end at 01:56 [call].
- The core competition for 401918851 reported `boxscoreSource {description: "scrubbed"}`, `statsSource "scrubbed"`, `playByPlaySource "feed"`, `gameSource "basic/manual"` [call]. "Scrubbed" suggests a post-game review of the box score, which is a correction step, but when it happens and what it changes is [unchecked].

**Older and odd games** [call]:

- 2024 final (401637613): full box and 360 plays with coordinates; `pickcenter` empty although the core API holds 9 books for it.
- 2015 (400592375): box with 21 team stats, 310 plays, no coordinates, no win probability. Another 2015 game (400599260) has a player box but no team box and no plays.
- 2019 game without the pbp flag (401095054): full box, no plays.
- 2005 (250150024) and 2002 (220190222): no box, no plays, no officials; 125 to 140 KB of mostly empty blocks. 2005 says `boxscoreSource: "full"` and still has no box, so the source flag is not a promise.
- The `playByPlayAvailable` flag on the scoreboard matched the summary in every game where both were read (true: plays present; false: none) [call, 7 games].
- Scheduled (401903607, 401918917): no plays, a box with 12 team statistics per team (their meaning before tip-off not inspected), `predictor`, `lastFiveGames`; the game-day one had a DraftKings line.
- Canceled (401929303): every block present and empty.

### 3.3 Calendar

- Core `https://sports.core.api.espn.com/v2/sports/basketball/leagues/womens-college-basketball/seasons/2027/types/2/calendar/ondays` answered with `$ref` `.../calendar/ondays`, i.e. the league-level current calendar: 108 dates from 2026-11-01 to 2027-02-28 [call]. The season in the path did not show in the answer, so past calendars are [unchecked] by this path. `.../calendar` lists four: `ondays`, `offdays`, `blacklist`, `whitelist` [call].
- The site path `.../calendar` returned 404 [call], although `sportsdataverse-py` lists it [source: `wbb_espn_ext.py`].
- Each scoreboard response also carries the season's calendar (section 3.1), which makes a separate calendar dataset unnecessary for planning.
- Season types for 2027: four are listed [call]; type 2 is the regular season, 2026-11-01 to 2027-03-16, and type 3 the postseason, 2027-03-16 to 2027-04-07 [call]; type 4 is the off-season [source: `wbb_schedule.py` docstring]; type 1 was not fetched [unchecked]. The season as a whole runs 2026-07-13 to 2027-04-07 [call].
- `seasons?limit=200` lists 46 seasons, 1982 to 2027 [call]; scoreboards answered only from 2001 (section 3.1).

### 3.4 Core API events and per-game resources

- `events?dates=YYYYMMDD&groups=50&limit=500` returned 46 refs for 2026-11-17, the same count as the scoreboard; without `groups` 6 [call]. A range in `dates` was ignored and only its first day used (`$meta.parameters.dates: ["20261110"]`) [call]. `events?season=2027&seasontypes=2&groups=50` without dates returned today's 68 [call].
- `seasons/<yr>/types/2/weeks/<n>/events?limit=1000`: 345 ids for week 3; 45 of the 46 scoreboard games of 2026-11-17 and all 68 of 2026-11-18 were in it. The missing one is Indiana University Southeast at Southern Indiana [call]. 20 weeks in the 2027 regular season, 18 in 2005 [call].
- Per-game resources [call, for 401918851]: `status` (0.3 KB), `competitors` (team refs, `homeAway`, `winner`), `competitors/<team>/statistics` (defensive 7, general 23, offensive 43 statistics), `competitors/<team>/roster` (15 entries with `starter`, `didNotPlay`, per-player statistics refs), `plays?limit=1000` (484 items, 0.9 MB, each with `modified`, `valid`, `priority`, `personnel`, `probability`), `probabilities?limit=1000` (481, with `lastModified`), `odds`, `officials` (with official ids, which the summary lacks).
- `seasons/<yr>/types/2/corrections` returned an empty list for 2024 and 2027 [call]. What it is meant to hold is [unchecked].
- Paging: lists answer `pageSize` up to at least 1000 [call]; `wehoop` caps its own paging at 50 pages [source: `espn_basketball_event_helpers.R`].

### 3.5 Odds

- On game day the scoreboard carried `odds` on 10 of 68 games, all from DraftKings (`provider.id 100`): `details` ("MD -44.5"), `overUnder`, `spread`, and per team `favorite`, `underdog`, `favoriteAtOpen` [call]. The 10 featured games of the no-`groups` scoreboard were the same 10 [call].
- After the game the scoreboard drops the line (0 of 46 finals), the summary keeps it in `pickcenter` (3 of 3 finals of 2026-11-17 sampled) [call].
- Core `odds` gives per book `open`, `close` and `current` for spread, total and the price on each, plus `moneylineWinner` and `spreadWinner` [call]. The 2024 national final had 9 books (BetfairSportsbook, three Caesars state books, DraftKings, ESPN BET, MGM, SugarHouse, Unibet); a 2024 regular-season game and a 2015 game had none; a January 2027 game had none yet [call].
- `wehoop` says of men's college basketball that ESPN "does not carry NCAA basketball odds lines" [source: `espn_basketball_event_betting_helpers.R`]; for women's college basketball today that is not so, but coverage is a minority of games.

### 3.6 Teams, rosters, team schedules

- Site `teams?limit=1000`: 363 teams with id, names, abbreviation, colours, logos, `isActive`; `groups=50` returned the same bytes [call]. Core `seasons/2027/types/2/groups/50/teams` lists 357 [call]; standings list 363 [call]. Which six differ is [unchecked].
- Site `teams/<id>`: adds `groups` (conference id and parent 50), `record`, `standingSummary`, `nextEvent` [call].
- Site `teams/<id>/roster`: `athletes[]` with id, names, height, birthplace, jersey, position, `experience` (class), `injuries`, `status`; `coach[]` [call, South Carolina, 15 players]. Past-season rosters [unchecked]. Core `seasons/2027/teams/<id>/athletes` lists the same 15 as refs [call].
- Site `teams/<id>/schedule`: 31 games for 2027 (5 final, 26 scheduled, to 2027-02-28); `season=2024` returned 38 games including the postseason [call].

### 3.7 Standings

`https://site.api.espn.com/apis/v2/sports/basketball/womens-college-basketball/standings` returned Division I as 32 conferences, 363 entries, each with 84 statistics (overall, home, road and other splits of wins, losses, points for and against, streak, games behind), 6.1 MB [call]. `?season=2024` returned 33 conferences and 359 teams for 2023-24 [call]. No as-of date parameter was tried; as fetched it is a snapshot of the moment, and past seasons are their end state [unchecked whether a date parameter exists]. `wehoop` calls a `site.web.api.espn.com` variant with sort and level parameters [source: `espn_wbb_standings`].

### 3.8 Rankings

- Site `rankings`: AP Top 25 and Coaches Poll for the current week (week 3), each 25 ranked plus others receiving votes, with points, first-place votes, previous rank and record [call]. `?seasons=2024&weeks=10` returned an empty `rankings` list [call].
- Core `seasons/<yr>/types/2/weeks/<n>/rankings` lists the polls of a week; `.../rankings/1` is the AP poll. 2024 week 10 (dated 2024-01-08) and 2010 week 10 answered with 25 ranks each [call]. Each week is one call per poll.

## 4. What drives what

```
scoreboard (1 call per game day) ──► game ids ──► summary (1 call per game)
      │                                     └──► core per-game resources (odds, plays, officials ...)
      └─ calendar of game days (in the scoreboard response itself)
teams (1 call) ──► team ids ──► rosters, team schedules (1 call per team)
season ──► weeks ──► week event lists (ids only) ──► per-game resources
season / week ──► rankings (1 call per poll)
season ──► standings (1 call)
```

A full D-I season, by dataset [estimate]:

- **Scoreboard:** about 150 calls (123 listed game days so far plus the postseason) at 0.5 to 1.7 MB each: roughly 100 to 200 MB raw.
- **Summary:** 20 regular-season weeks, most of 320 to 350 games and some short (100 in the Christmas week), plus the postseason, give roughly 5,000 to 6,000 games; at 0.4 to 0.5 MB each, roughly 2 to 3 GB raw and 5,000 to 6,000 calls (about 2 hours of calls at one per 1.5 s).
- **Rankings:** about 40 calls. **Standings:** 1. **Teams:** 1. **Rosters:** 363.

## 5. Calls that did not return 200

| Call | Answer | Done next |
|---|---|---|
| scoreboard `dates=20261110-20261116&groups=50&limit=1000` | 400 "Failed to get events endpoint." | tried once more with a 2-day range |
| scoreboard `dates=20261115-20261116&groups=50&limit=500` | 400, same | stopped |
| site `.../womens-college-basketball/calendar` | 404 `{"code":404}` | used the core calendar and the scoreboard's calendar |
| `site.web.api.espn.com/apis/fitt/v3/.../powerindex` | 400 "Power index is not supported for sport basketball and league womens-college-basketball" | stopped |

## 6. Proposals for the first slice

The map (issue #1) asks for a dataset whose pipeline sets the conventions for every later one, built end to end and checked against the other two shapes. What a candidate has to exercise for that: one dataset driving another's fetches, re-fetch and correction, past seasons, and a gold table someone would use.

### Proposal A (recommended): the scoreboard alone

Fetch one day per call; raw is the day's response; bronze is one row per game per fetch; silver is one row per game (latest state, with status history kept or not, as decided); gold is a game results table (date, teams, scores, status, neutral site, conference game) and its team-game form (one row per team per game).

Why:

- **Few calls, whole league.** One call per game day; a full season back to 2001 is about 150 calls per season [estimate], so backfill and daily runs are both cheap enough to run and rerun while the conventions settle.
- **It drives the rest.** Its game ids and calendar are what every per-game dataset needs, so building it first gives the next dataset its fetch plan.
- **It changes after the fact**, in the ways silver has to absorb: scheduled to live to final, canceled, postponed (with, in the one case looked at, a new id for the make-up game), attendance and records filled in late.
- **Past and future in one dataset**: 26 seasons back and about three months ahead, so "how past seasons get loaded" and "re-fetch upcoming days" are both real.
- **It has a direct check** in the map's second shape: the sportsdataverse schedule files (`wbb_schedule_<season>`), built from ESPN, cover 2001 onward [source: `load_wbb.R`], so a gold game table can be compared against them season by season.

Weaknesses:

- Thin stats: 15 team totals, no players.
- The stale-first-answer quirk (section 3.1) and the US Eastern day: the fetch plan must re-fetch and must not key on the UTC date.
- Silent truncation by `limit`: the fetcher has to set it high and check counts against the calendar or the core event count.
- About 14 KB per game, mostly presentation; bronze will carry many columns no later layer wants.
- Little sign yet of corrections to finals (one re-fetch 10 minutes apart); the correction path may be exercised only by status changes.

### Proposal B: scoreboard plus summary, team box only to gold

The scoreboard as in A, and the summary as a second dataset fetched for each final game the scoreboard lists. Raw and bronze take the whole summary; silver and gold take only the team box (one row per team per game, 21 to 24 statistics) at first, with players and plays added later.

Why:

- Exercises **one dataset driving another** from the start, the part of the fetch plan the map singles out.
- The richest data and the one most likely to see real corrections ("scrubbed" box scores, plays inserted out of order).
- The gold table (team-game box) is what most later work wants.

Weaknesses:

- About 40 times the calls of A per season (roughly 5,000 to 6,000) and 2 to 3 GB raw per season [estimate]; a backfill is hours of fetching.
- The summary is a bundle of a dozen blocks of different grain (team, player, play, book, official); bronze for it is a design question in its own right.
- Depth varies sharply by season (no box before about 2008, few plays before about 2020), so "bad data" rules get tested early, which is useful but slows the first slice.
- Byte order varies between fetches of the same content; the raw layer has to compare canonical JSON.

### Proposal C: rankings

Weekly AP and Coaches polls from the core API, back to at least 2010.

Why: small (one 30 KB call per poll and week), a clean grain (poll, week, team), history by week that suits a point-in-time table.

Weaknesses: does not drive another dataset; the polls are published once and not corrected, so re-fetch and correction are not exercised; the gold use is narrow. A good second or third dataset, not a first.

### Not proposed

- **Standings:** 6 MB snapshots with no as-of date; a past season is only its end state. Better derived from the scoreboard in gold.
- **Core per-game resources:** the same content as the summary at 6 to 10 calls per game, linked by `$ref` with an internal host to rewrite. Only the `odds` resource is worth a dataset of its own later, because it alone keeps open and close lines and lines on past games.
- **Teams and rosters:** reference data that the scoreboard and summary already carry per game; useful once ids need names, later.
- **Week event lists and the calendar:** planning inputs, not datasets with a gold table; the scoreboard already carries the calendar.

## 7. Not checked

- When conference tournament and NCAA tournament games appear in the scoreboard, and whether placeholder games ("TBD vs TBD") are listed before the teams are known.
- Whether finals are ever corrected days or weeks later (score, box, plays), and what `boxscoreSource "scrubbed"` and the empty `corrections` resource stand for. This needs the same games fetched again over several days.
- How long after the final a summary is complete: one game's `lastUpdatedAt` equalled its last play, while its win-probability entries were modified up to three hours after the final; whether the summary's own blocks change in that window was not observed (the re-fetch 8 minutes apart came about 20 hours after the game).
- Whether a make-up game always gets a new id (one example).
- What `groups=51` is exactly, and which six teams are in the site's 363 but not in the core's 357.
- Past-season rosters, past calendars by season, a date parameter for standings.
- Scoreboards for seasons between the samples above; the depth table is one Saturday per season.
- Whether the stale-first-answer behaviour depends on the URL being new to the cache, on time of day, or on something else (two samples).
- Any rate limit: none was met at 117 calls in 12 minutes; a backfill at summary scale would be far more.
- The `cdn.espn.com` box score and play-by-play pages listed in `sportsdataverse-py` (only the cdn scoreboard was called).
