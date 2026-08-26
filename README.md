# Tournament Opponent Games Extractor

Local web app for extracting opponent games for a Chess-Results tournament.

## What it does

- Reads the tournament starting-rank list from a Chess-Results link.
- Uses each listed player's FIDE ID to download games from Chess-Results game search.
- Scans a local TWIC PGN file for the same listed players.
- Merges both sources into one deduplicated PGN.
- Renames non-listed opponents to `Hatsun` by default.
- Creates a summary CSV and per-player color PGN files.

## Run

```powershell
cd "C:\Users\GOWTHAM\OneDrive\ドキュメント\opponent game search\tournament_extractor_web"
py server.py
```

Open:

```text
http://127.0.0.1:8765
```

## Inputs

- Tournament name
- Chess-Results tournament link
- TWIC PGN path
- Output folder
- Opponent rename text

## Default paths

TWIC PGN:

```text
C:\Users\GOWTHAM\Downloads\July\opponent game research\Twic Last 3 year.pgn
```

Output folder:

```text
C:\Users\GOWTHAM\Downloads\Tournament opponent games extraction
```

## Output files

For a tournament named `Example Tournament`, the app creates:

```text
Example Tournament_TWIC_and_ChessResults_Hatsun.pgn
Example Tournament_TWIC_and_ChessResults_Hatsun_summary.csv
Example Tournament_ChessResults_only_Hatsun.pgn
Example Tournament_player_wise_TWIC_and_ChessResults_Hatsun\
```

## Notes

This app must run locally because browsers cannot reliably read a large PGN file from your disk or submit Chess-Results PGN download forms directly.
