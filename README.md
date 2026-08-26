# Tournament Opponent Games Extractor

Local web app for extracting opponent games for a Chess-Results tournament.

## What it does

- Reads the tournament starting-rank list from a Chess-Results link.
- Uses each listed player's FIDE ID to download games from Chess-Results game search.
- Downloads TWIC weekly PGN zip files from the selected date range.
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
- TWIC start date
- TWIC end date
- Output folder
- Opponent rename text

## Default output folder

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

This app still needs the Python backend because browsers cannot reliably submit Chess-Results PGN download forms directly. TWIC files are downloaded automatically from the public TWIC archive.
