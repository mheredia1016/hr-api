# HR API v26

Dashboard:
- Before lineups: shows active-roster projected hitter data like before.
- After official MLB lineups: switches to confirmed batting-order hitters only.
- API response includes lineupMode: projected or confirmed.

Discord pregame alerts:
- Still only post after official lineups are confirmed.

No new Railway variables required.


## SportsGameOdds HR links
Set these Railway variables:

```
SPORTSGAMEODDS_API_KEY=your_key
SPORTSGAMEODDS_MLB_LEAGUE_ID=MLB
SPORTSGAMEODDS_HR_BOOKMAKERS=fanduel,draftkings,hardrockbet
SPORTSGAMEODDS_CACHE_SECONDS=90
```

The API key is never sent to the browser. Each hitter row now includes a `sportsbooks` object when a matching HR prop is available.
