import csv
import hashlib
import io
import json
import re
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from collections import defaultdict
from datetime import date, datetime
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen


APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
DEFAULT_OUTPUT_DIR = Path(r"C:\Users\GOWTHAM\Downloads\Tournament opponent games extraction")
GAME_SEARCH_URL = "https://s1.chess-results.com/PartieSuche.aspx?lan=1&SNode=S0"
TWIC_ARCHIVE_URL = "https://theweekinchess.com/twic"
MAX_LINES_VALUE = "5"

TAG_RE = re.compile(rb'^\[([A-Za-z0-9_]+)\s+"(.*)"\]\s*$')
SPACES_RE = re.compile(r"\s+")
BRACKET_RE = re.compile(r"\s*[\(\[\{].*?[\)\]\}]")
PUNCT_RE = re.compile(r"[.,]")
BAD_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\|?*]')
MOVE_NUMBER_RE = re.compile(r"\d+\.(?:\.\.)?")
COMMENT_RE = re.compile(r"\{[^}]*\}|\([^()]*\)")
NAG_RE = re.compile(r"\$\d+")
RESULT_RE = re.compile(r"\b(?:1-0|0-1|1/2-1/2|\*)\b")

JOBS = {}
JOBS_LOCK = threading.Lock()


def clean_spaces(value):
    return SPACES_RE.sub(" ", value or "").strip()


def strip_brackets(value):
    return clean_spaces(BRACKET_RE.sub("", value or ""))


def canonical(value):
    value = strip_brackets(value)
    value = PUNCT_RE.sub(" ", value)
    return clean_spaces(value.casefold())


def safe_name(value):
    value = BAD_FILENAME_CHARS_RE.sub("_", value or "")
    value = clean_spaces(value.replace(",", " "))
    return value.rstrip(". ") or "tournament"


def split_name_variants(name):
    name = strip_brackets(clean_spaces(name)).rstrip(",")
    variants = {name}
    if "," in name:
        family, given = [clean_spaces(x) for x in name.split(",", 1)]
        if family and given:
            variants.add(f"{family} {given}")
            variants.add(f"{given} {family}")
            tokens = given.split()
            if len(tokens) >= 2:
                variants.add(f"{family}, {tokens[0]}")
                variants.add(f"{family} {tokens[0]}")
                variants.add(f"{tokens[0]} {family}")
    else:
        parts = name.split()
        if len(parts) > 1:
            family = parts[-1]
            given = " ".join(parts[:-1])
            variants.add(f"{family}, {given}")
            variants.add(f"{family} {given}")
    return variants


class StartingRankParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_h2 = False
        self.saw_starting_rank = False
        self.in_table = False
        self.in_row = False
        self.in_cell = False
        self.current_cell = []
        self.current_row = []
        self.rows = []

    def handle_starttag(self, tag, attrs):
        if tag == "h2":
            self.in_h2 = True
        elif tag == "table" and self.saw_starting_rank and not self.in_table:
            self.in_table = True
        elif tag == "tr" and self.in_table:
            self.in_row = True
            self.current_row = []
        elif tag in {"td", "th"} and self.in_row:
            self.in_cell = True
            self.current_cell = []

    def handle_endtag(self, tag):
        if tag == "h2":
            self.in_h2 = False
        elif tag == "table" and self.in_table:
            self.in_table = False
        elif tag == "tr" and self.in_row:
            if self.current_row:
                self.rows.append(self.current_row)
            self.in_row = False
        elif tag in {"td", "th"} and self.in_cell:
            self.current_row.append(clean_spaces(" ".join(self.current_cell)))
            self.in_cell = False

    def handle_data(self, data):
        text = clean_spaces(data)
        if not text:
            return
        if self.in_h2 and text == "Starting rank":
            self.saw_starting_rank = True
        if self.in_cell:
            self.current_cell.append(text)


class SearchFormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields = {}
        self.select_name = None
        self.select_set = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input":
            name = attrs.get("name")
            if not name:
                return
            typ = attrs.get("type", "text")
            if typ in {"hidden", "text", "date"}:
                self.fields[name] = attrs.get("value", "")
        elif tag == "select":
            self.select_name = attrs.get("name")
            self.select_set = False
        elif tag == "option" and self.select_name and not self.select_set:
            self.fields[self.select_name] = attrs.get("value", "")
            self.select_set = True

    def handle_endtag(self, tag):
        if tag == "select":
            self.select_name = None


class TwicArchiveParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_row = False
        self.in_cell = False
        self.current_cell = []
        self.current_row = []
        self.rows = []
        self.cell_link = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr":
            self.in_row = True
            self.current_row = []
        elif tag in {"td", "th"} and self.in_row:
            self.in_cell = True
            self.current_cell = []
            self.cell_link = None
        elif tag == "a" and self.in_cell:
            self.cell_link = attrs.get("href")

    def handle_endtag(self, tag):
        if tag == "tr" and self.in_row:
            if self.current_row:
                self.rows.append(self.current_row)
            self.in_row = False
        elif tag in {"td", "th"} and self.in_cell:
            text = clean_spaces(" ".join(self.current_cell))
            self.current_row.append({"text": text, "href": self.cell_link})
            self.in_cell = False
            self.cell_link = None

    def handle_data(self, data):
        if self.in_cell:
            text = clean_spaces(data)
            if text:
                self.current_cell.append(text)


def fetch_bytes(url):
    if url.startswith("https://theweekinchess.com/"):
        if url.lower().endswith(".zip"):
            with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
                tmp_path = Path(tmp.name)
            try:
                script = (
                    "$ProgressPreference='SilentlyContinue'; "
                    f"Invoke-WebRequest -Uri '{url}' -UseBasicParsing -OutFile '{tmp_path}'"
                )
                subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, capture_output=True)
                return tmp_path.read_bytes()
            finally:
                if tmp_path.exists():
                    tmp_path.unlink()
        script = (
            "$ProgressPreference='SilentlyContinue'; "
            f"(Invoke-WebRequest -Uri '{url}' -UseBasicParsing).Content"
        )
        completed = subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, capture_output=True)
        return completed.stdout

    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html,application/xhtml+xml,application/zip,*/*",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    return urlopen(request, timeout=120).read()


def fetch_text(url):
    return fetch_bytes(url).decode("utf-8", errors="replace")


def parse_date(value):
    return datetime.strptime(value, "%Y-%m-%d").date()


def load_twic_issues(start_date, end_date):
    parser = TwicArchiveParser()
    parser.feed(fetch_text(TWIC_ARCHIVE_URL))
    issues = []
    for row in parser.rows:
        if len(row) < 4:
            continue
        issue = row[0]["text"]
        issue_date = row[1]["text"]
        pgn_cell = row[3]
        if not issue.isdigit() or not re.match(r"\d{4}-\d{2}-\d{2}$", issue_date):
            continue
        if pgn_cell["text"].upper() != "PGN" or not pgn_cell["href"]:
            continue
        dt = parse_date(issue_date)
        if start_date <= dt <= end_date:
            href = pgn_cell["href"]
            if href.startswith("/"):
                href = "https://theweekinchess.com" + href
            issues.append({"issue": issue, "date": issue_date, "url": href})
    return issues


def iter_twic_zip_games(zip_bytes):
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for member in zf.namelist():
            if member.lower().endswith(".pgn"):
                with zf.open(member) as fh:
                    yield from split_games(fh.read())


def load_players(url):
    parser = StartingRankParser()
    parser.feed(fetch_text(url))
    if not parser.rows:
        raise RuntimeError("No starting-rank table found on the Chess-Results page.")

    headers = parser.rows[0]
    if "Name" not in headers or "FideID" not in headers:
        raise RuntimeError("Starting-rank table must contain Name and FideID columns.")

    name_index = headers.index("Name")
    fide_index = headers.index("FideID")
    players = []
    seen = set()
    for row in parser.rows[1:]:
        if len(row) <= max(name_index, fide_index) or not row[0].isdigit():
            continue
        name = clean_spaces(row[name_index]).rstrip(",")
        fide_id = clean_spaces(row[fide_index])
        if not name or not fide_id.isdigit() or fide_id in seen:
            continue
        players.append({"name": name, "fide_id": fide_id})
        seen.add(fide_id)

    if not players:
        raise RuntimeError("No players with FideID were parsed from the tournament list.")
    return players


def build_lookup(players):
    lookup = defaultdict(set)
    for player in players:
        for variant in split_name_variants(player["name"]):
            lookup[canonical(variant)].add(player["name"])
    return lookup


def player_variant_keys(player_name):
    return {canonical(variant) for variant in split_name_variants(player_name)}


def parse_headers(game_lines):
    headers = {}
    for line in game_lines:
        if not line.startswith(b"["):
            if line.strip():
                break
            continue
        match = TAG_RE.match(line.rstrip(b"\r\n"))
        if not match:
            continue
        tag = match.group(1).decode("ascii", errors="ignore")
        raw = match.group(2)
        for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                headers[tag] = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
    return headers


def split_games(pgn_bytes):
    game_lines = []
    for line in pgn_bytes.splitlines(keepends=True):
        if line.startswith(b"[Event ") and game_lines:
            yield game_lines
            game_lines = [line]
        else:
            game_lines.append(line)
    if game_lines:
        yield game_lines


def iter_pgn_games(path):
    game_lines = []
    with path.open("rb") as fh:
        for line in fh:
            if line.startswith(b"[Event ") and game_lines:
                yield game_lines
                game_lines = [line]
            else:
                game_lines.append(line)
    if game_lines:
        yield game_lines


def rewrite_headers(game_lines, replacement_by_side):
    rewritten = []
    for line in game_lines:
        changed = False
        for side, new_name in replacement_by_side.items():
            prefix = f'[{side} "'.encode("ascii")
            if line.startswith(prefix):
                newline = b"\r\n" if line.endswith(b"\r\n") else b"\n"
                rewritten.append(f'[{side} "{new_name}"]'.encode("utf-8") + newline)
                changed = True
                break
        if not changed:
            rewritten.append(line)
    return b"".join(rewritten)


def normalize_moves(game_bytes):
    text = game_bytes.decode("utf-8", errors="replace")
    moves = " ".join(line for line in text.splitlines() if not line.startswith("["))
    previous = None
    while previous != moves:
        previous = moves
        moves = COMMENT_RE.sub(" ", moves)
    moves = NAG_RE.sub(" ", moves)
    moves = MOVE_NUMBER_RE.sub(" ", moves)
    moves = RESULT_RE.sub(" ", moves)
    return clean_spaces(moves).casefold()


def game_fingerprint(headers, output_game):
    key = "\x1f".join(
        [
            clean_spaces(headers.get("Date", "")),
            canonical(headers.get("White", "")),
            canonical(headers.get("Black", "")),
            clean_spaces(headers.get("Result", "")),
            normalize_moves(output_game),
        ]
    )
    return hashlib.sha256(key.encode("utf-8", errors="replace")).hexdigest()


def get_form_template(opener):
    html = opener.open(Request(GAME_SEARCH_URL, headers={"User-Agent": "Mozilla/5.0"}), timeout=60).read().decode(
        "utf-8", errors="replace"
    )
    parser = SearchFormParser()
    parser.feed(html)
    return parser.fields


def download_player_pgn(opener, form_template, fide_id):
    data = dict(form_template)
    data.update(
        {
            "ctl00$P1$Txt_FideID": fide_id,
            "ctl00$P1$combo_anzahl_zeilen": MAX_LINES_VALUE,
            "ctl00$P1$combo_spielerfarbe": "-",
            "ctl00$P1$combo_ergebnis": "-",
            "ctl00$P1$cb_DownLoadPGN": "Download as PGN-File",
        }
    )
    request = Request(
        GAME_SEARCH_URL,
        data=urlencode(data).encode("utf-8"),
        headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/x-www-form-urlencoded"},
    )
    return opener.open(request, timeout=120).read()


def update_job(job_id, **kwargs):
    with JOBS_LOCK:
        JOBS[job_id].update(kwargs)


def add_log(job_id, message):
    with JOBS_LOCK:
        JOBS[job_id]["logs"].append(message)
        JOBS[job_id]["updated_at"] = time.time()


def run_extraction(job_id, config):
    try:
        tournament_name = clean_spaces(config["tournament_name"])
        tournament_url = clean_spaces(config["tournament_url"])
        twic_start = parse_date(config["twic_start"])
        twic_end = parse_date(config["twic_end"])
        output_root = Path(config["output_dir"])
        placeholder = clean_spaces(config.get("placeholder") or "Hatsun")

        if not tournament_name:
            raise RuntimeError("Tournament name is required.")
        parsed = urlparse(tournament_url)
        if parsed.scheme not in {"http", "https"} or "chess-results.com" not in parsed.netloc:
            raise RuntimeError("Enter a valid Chess-Results tournament URL.")
        if twic_start > twic_end:
            raise RuntimeError("TWIC start date must be before or equal to TWIC end date.")

        stem = safe_name(tournament_name)
        output_root.mkdir(parents=True, exist_ok=True)
        final_pgn = output_root / f"{stem}_TWIC_and_ChessResults_{safe_name(placeholder)}.pgn"
        summary_csv = output_root / f"{stem}_TWIC_and_ChessResults_{safe_name(placeholder)}_summary.csv"
        chessresults_pgn = output_root / f"{stem}_ChessResults_only_{safe_name(placeholder)}.pgn"
        player_dir = output_root / f"{stem}_player_wise_TWIC_and_ChessResults_{safe_name(placeholder)}"
        player_dir.mkdir(parents=True, exist_ok=True)
        for path in player_dir.glob("*.pgn"):
            path.unlink()
        for path in (final_pgn, summary_csv, chessresults_pgn):
            if path.exists():
                path.unlink()

        update_job(job_id, status="running", phase="players")
        add_log(job_id, "Reading Chess-Results starting rank table...")
        players = load_players(tournament_url)
        lookup = build_lookup(players)
        forced_keys_by_player = {player["name"]: player_variant_keys(player["name"]) for player in players}
        add_log(job_id, f"Parsed {len(players)} players with FIDE IDs.")

        counts = {
            player["name"]: {"White": 0, "Black": 0, "ChessResults": 0, "TWIC": 0, "Downloaded": 0}
            for player in players
        }
        out_handles = {}
        seen_final = set()
        seen_by_bucket = defaultdict(set)
        source_entries = {"ChessResults": 0, "TWIC": 0}

        def get_handle(player_name, color):
            key = (player_name, color)
            if key not in out_handles:
                out_handles[key] = (player_dir / f"{safe_name(player_name)} {color}.pgn").open("wb")
            return out_handles[key]

        def matching_listed(side_name):
            return lookup.get(canonical(side_name), set())

        def process_game(game_lines, source, final_fh, cr_fh=None, forced_player=None):
            game_bytes = b"".join(game_lines)
            headers = parse_headers(game_lines)
            if not headers.get("White") and not headers.get("Black"):
                return

            white_listed = matching_listed(headers.get("White", ""))
            black_listed = matching_listed(headers.get("Black", ""))
            if forced_player:
                forced_keys = forced_keys_by_player[forced_player]
                white_key = canonical(headers.get("White", ""))
                black_key = canonical(headers.get("Black", ""))
                if white_key in forced_keys:
                    white_listed.add(forced_player)
                if black_key in forced_keys:
                    black_listed.add(forced_player)
                if forced_player not in white_listed and forced_player not in black_listed:
                    if white_listed and not black_listed:
                        black_listed.add(forced_player)
                    elif black_listed and not white_listed:
                        white_listed.add(forced_player)
            if not white_listed and not black_listed:
                return

            replacement_by_side = {}
            if not white_listed:
                replacement_by_side["White"] = placeholder
            if not black_listed:
                replacement_by_side["Black"] = placeholder
            output_game = rewrite_headers(game_lines, replacement_by_side) if replacement_by_side else game_bytes
            fingerprint = game_fingerprint(headers, output_game)
            source_entries[source] += 1

            if fingerprint not in seen_final:
                seen_final.add(fingerprint)
                final_fh.write(output_game.rstrip() + b"\n\n")
                if cr_fh:
                    cr_fh.write(output_game.rstrip() + b"\n\n")

            for color, matched_names in (("White", white_listed), ("Black", black_listed)):
                for matched_name in matched_names:
                    bucket = (matched_name, color)
                    bucket_key = (fingerprint, source)
                    if bucket_key in seen_by_bucket[bucket]:
                        continue
                    seen_by_bucket[bucket].add(bucket_key)
                    get_handle(matched_name, color).write(output_game.rstrip() + b"\n\n")
                    counts[matched_name][color] += 1
                    counts[matched_name][source] += 1

        opener = build_opener(HTTPCookieProcessor(CookieJar()))
        form_template = get_form_template(opener)
        update_job(job_id, phase="chessresults", progress={"done": 0, "total": len(players)})

        try:
            with final_pgn.open("wb") as final_fh, chessresults_pgn.open("wb") as cr_fh:
                for index, player in enumerate(players, start=1):
                    pgn_bytes = download_player_pgn(opener, form_template, player["fide_id"])
                    downloaded = 0
                    for game_lines in split_games(pgn_bytes):
                        headers = parse_headers(game_lines)
                        if headers.get("White") or headers.get("Black"):
                            downloaded += 1
                            process_game(game_lines, "ChessResults", final_fh, cr_fh, player["name"])
                    counts[player["name"]]["Downloaded"] = downloaded
                    if index == 1 or index % 10 == 0 or index == len(players):
                        add_log(job_id, f"Chess-Results: {index}/{len(players)} players, {len(seen_final)} unique games.")
                    update_job(job_id, progress={"done": index, "total": len(players)}, games=len(seen_final))
                    time.sleep(0.12)

                update_job(job_id, phase="twic", progress={"done": 0, "total": None})
                add_log(job_id, "Reading TWIC archive...")
                twic_issues = load_twic_issues(twic_start, twic_end)
                if not twic_issues:
                    add_log(job_id, "No TWIC issues found in the selected date range.")
                else:
                    add_log(job_id, f"Found {len(twic_issues)} TWIC issue(s) in the selected date range.")
                update_job(job_id, progress={"done": 0, "total": len(twic_issues)})
                for issue_index, issue in enumerate(twic_issues, start=1):
                    add_log(job_id, f"TWIC {issue['issue']} ({issue['date']}): downloading PGN zip...")
                    zip_bytes = fetch_bytes(issue["url"])
                    scanned = 0
                    for game_lines in iter_twic_zip_games(zip_bytes):
                        scanned += 1
                        process_game(game_lines, "TWIC", final_fh)
                    add_log(
                        job_id,
                        f"TWIC {issue['issue']}: scanned {scanned:,} games, {len(seen_final)} unique games.",
                    )
                    update_job(job_id, progress={"done": issue_index, "total": len(twic_issues)}, games=len(seen_final))
        finally:
            for handle in out_handles.values():
                handle.close()

        with summary_csv.open("w", newline="", encoding="utf-8-sig") as csv_fh:
            writer = csv.writer(csv_fh)
            writer.writerow(
                [
                    "Player Name",
                    "FideID",
                    "Downloaded Entries",
                    "White Games",
                    "Black Games",
                    "Total Player Entries",
                    "ChessResults Entries",
                    "TWIC Entries",
                ]
            )
            for player in players:
                row = counts[player["name"]]
                writer.writerow(
                    [
                        player["name"],
                        player["fide_id"],
                        row["Downloaded"],
                        row["White"],
                        row["Black"],
                        row["White"] + row["Black"],
                        row["ChessResults"],
                        row["TWIC"],
                    ]
                )

        result = {
            "players": len(players),
            "unique_games": len(seen_final),
            "chessresults_entries": source_entries["ChessResults"],
            "twic_entries": source_entries["TWIC"],
            "player_files": len(out_handles),
            "final_pgn": str(final_pgn),
            "summary_csv": str(summary_csv),
            "player_dir": str(player_dir),
        }
        add_log(job_id, f"Done. Wrote {len(seen_final)} unique games.")
        update_job(job_id, status="complete", phase="complete", result=result, games=len(seen_final))
    except Exception as exc:
        add_log(job_id, f"Error: {exc}")
        update_job(job_id, status="failed", phase="failed", error=str(exc))


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def log_message(self, format, *args):
        return

    def send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/defaults":
            self.send_json(
                {
                    "twic_start": "2026-01-01",
                    "twic_end": date.today().isoformat(),
                    "output_dir": str(DEFAULT_OUTPUT_DIR),
                    "placeholder": "Hatsun",
                }
            )
            return
        if self.path.startswith("/api/jobs/"):
            job_id = self.path.rsplit("/", 1)[-1]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                payload = dict(job) if job else None
            if not payload:
                self.send_json({"error": "Job not found"}, status=404)
                return
            self.send_json(payload)
            return
        return super().do_GET()

    def do_POST(self):
        if self.path != "/api/jobs":
            self.send_json({"error": "Not found"}, status=404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            self.send_json({"error": "Invalid JSON"}, status=400)
            return

        job_id = uuid.uuid4().hex
        with JOBS_LOCK:
            JOBS[job_id] = {
                "id": job_id,
                "status": "queued",
                "phase": "queued",
                "logs": ["Job queued."],
                "created_at": time.time(),
                "updated_at": time.time(),
                "games": 0,
                "progress": {"done": 0, "total": None},
            }
        thread = threading.Thread(target=run_extraction, args=(job_id, payload), daemon=True)
        thread.start()
        self.send_json({"id": job_id}, status=202)


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print("Tournament extractor running at http://127.0.0.1:8765")
    print("Press Ctrl+C to stop.")
    server.serve_forever()


if __name__ == "__main__":
    main()
