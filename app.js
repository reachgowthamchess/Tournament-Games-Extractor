const form = document.querySelector("#extractForm");
const runButton = document.querySelector("#runButton");
const resetButton = document.querySelector("#resetButton");
const statusPill = document.querySelector("#statusPill");
const phaseValue = document.querySelector("#phaseValue");
const progressValue = document.querySelector("#progressValue");
const gamesValue = document.querySelector("#gamesValue");
const logOutput = document.querySelector("#logOutput");
const logCount = document.querySelector("#logCount");
const resultPanel = document.querySelector("#resultPanel");
const finalPgn = document.querySelector("#finalPgn");
const summaryCsv = document.querySelector("#summaryCsv");
const playerDir = document.querySelector("#playerDir");

let pollTimer = null;

function setStatus(text, state = "") {
  statusPill.textContent = text;
  statusPill.className = `status-pill ${state}`.trim();
}

function setLogs(lines) {
  const safeLines = lines && lines.length ? lines : ["Waiting for a tournament..."];
  logOutput.textContent = safeLines.join("\n");
  logCount.textContent = `${safeLines.length} ${safeLines.length === 1 ? "line" : "lines"}`;
  logOutput.scrollTop = logOutput.scrollHeight;
}

function updateJobView(job) {
  const phase = job.phase || "unknown";
  phaseValue.textContent = phase;
  gamesValue.textContent = Number(job.games || 0).toLocaleString();
  setLogs(job.logs);

  if (job.progress && job.progress.total) {
    progressValue.textContent = `${job.progress.done}/${job.progress.total}`;
  } else if (phase === "twic") {
    progressValue.textContent = "TWIC scan";
  } else {
    progressValue.textContent = "-";
  }

  if (job.status === "running" || job.status === "queued") {
    setStatus("Running", "running");
    runButton.disabled = true;
  } else if (job.status === "complete") {
    setStatus("Complete");
    runButton.disabled = false;
    clearInterval(pollTimer);
    pollTimer = null;
    if (job.result) {
      resultPanel.hidden = false;
      finalPgn.textContent = job.result.final_pgn;
      summaryCsv.textContent = job.result.summary_csv;
      playerDir.textContent = job.result.player_dir;
      gamesValue.textContent = Number(job.result.unique_games || 0).toLocaleString();
      progressValue.textContent = `${job.result.players} players`;
    }
  } else if (job.status === "failed") {
    setStatus("Failed", "failed");
    runButton.disabled = false;
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function pollJob(jobId) {
  const response = await fetch(`/api/jobs/${jobId}`);
  const job = await response.json();
  updateJobView(job);
}

async function loadDefaults() {
  const response = await fetch("/api/defaults");
  const defaults = await response.json();
  document.querySelector("#twicStart").value = defaults.twic_start;
  document.querySelector("#twicEnd").value = defaults.twic_end;
  document.querySelector("#outputDir").value = defaults.output_dir;
  document.querySelector("#placeholder").value = defaults.placeholder;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  resultPanel.hidden = true;
  setStatus("Starting", "running");
  setLogs(["Starting extraction..."]);

  const payload = {
    tournament_name: document.querySelector("#tournamentName").value,
    tournament_url: document.querySelector("#tournamentUrl").value,
    twic_start: document.querySelector("#twicStart").value,
    twic_end: document.querySelector("#twicEnd").value,
    output_dir: document.querySelector("#outputDir").value,
    placeholder: document.querySelector("#placeholder").value,
  };

  runButton.disabled = true;
  let response;
  try {
    response = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (error) {
    setStatus("Failed", "failed");
    setLogs(["Could not connect to the local Python server. Restart it with: py server.py"]);
    runButton.disabled = false;
    return;
  }

  if (!response.ok) {
    const error = await response.json();
    setStatus("Failed", "failed");
    setLogs([error.error || "Could not start extraction."]);
    runButton.disabled = false;
    return;
  }

  const { id } = await response.json();
  await pollJob(id);
  pollTimer = setInterval(() => pollJob(id).catch(() => {
    setStatus("Failed", "failed");
    setLogs(["Lost connection to the local extractor server."]);
    runButton.disabled = false;
    clearInterval(pollTimer);
    pollTimer = null;
  }), 2000);
});

resetButton.addEventListener("click", () => {
  form.reset();
  loadDefaults();
  resultPanel.hidden = true;
  phaseValue.textContent = "Not started";
  progressValue.textContent = "-";
  gamesValue.textContent = "0";
  setStatus("Idle");
  setLogs([]);
});

loadDefaults();
