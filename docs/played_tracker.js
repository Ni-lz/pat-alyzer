(function () {
  const STORAGE_KEY = "patalyzer.playedTickets.v2";

  function loadPlayedTickets() {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
    } catch {
      return [];
    }
  }

  function savePlayedTickets(records) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(records, null, 2));
  }

  function downloadText(filename, content, type) {
    const blob = new Blob([content], { type });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(url);
  }

  function parseNumberList(value) {
    if (!value) return [];
    return String(value)
      .split(",")
      .map((item) => parseInt(item.trim(), 10))
      .filter((number) => Number.isInteger(number));
  }

  function getLatestResultFromPage() {
    const bodyText = document.body.innerText || "";

    const dateMatch = bodyText.match(/Latest draw\s+(\d{4}-\d{2}-\d{2})/i);
    const numbersMatch = bodyText.match(/Numbers\s+([0-9,\s]+)\s+\+\s+([0-9,\s]+)/i);

    return {
      draw_date: dateMatch ? dateMatch[1] : "",
      numbers: numbersMatch ? parseNumberList(numbersMatch[1]) : [],
      stars: numbersMatch ? parseNumberList(numbersMatch[2]) : [],
    };
  }

  function findGeneratedTicketsTable() {
    const headings = Array.from(document.querySelectorAll("h2"));
    const ticketHeading = headings.find((heading) =>
      heading.innerText.toLowerCase().includes("generated candidate tickets")
    );

    if (!ticketHeading) return null;

    const card = ticketHeading.closest(".card") || ticketHeading.parentElement;
    if (!card) return null;

    return card.querySelector("table");
  }

  function getColumnIndexes(table) {
    const headers = Array.from(table.querySelectorAll("thead th")).map((th) =>
      th.innerText.trim().toLowerCase()
    );

    return {
      numbers: headers.indexOf("numbers"),
      stars: headers.indexOf("stars"),
      strategy: headers.indexOf("strategy"),
      score: headers.indexOf("final_strategy_score"),
      why: headers.indexOf("why_selected"),
    };
  }

  function addTrackerStyles() {
    const style = document.createElement("style");
    style.textContent = `
      .pat-tracker-panel {
        margin: 18px 0;
        padding: 18px;
        border: 1px solid rgba(255,255,255,.14);
        border-radius: 18px;
        background: rgba(15,23,42,.82);
      }
      .pat-tracker-actions {
        display: flex;
        flex-wrap: wrap;
        gap: 10px;
        margin: 12px 0;
      }
      .pat-tracker-actions button,
      .pat-tracker-actions label {
        border: 1px solid rgba(255,255,255,.18);
        background: rgba(96,165,250,.16);
        color: #e5f0ff;
        border-radius: 999px;
        padding: 8px 12px;
        cursor: pointer;
        font-size: 13px;
      }
      .pat-tracker-actions input[type="file"] {
        display: none;
      }
      .pat-tracker-small {
        color: #94a3b8;
        font-size: 13px;
        line-height: 1.45;
      }
      .pat-tracker-history {
        max-height: 360px;
        overflow: auto;
        border: 1px solid rgba(255,255,255,.10);
        border-radius: 12px;
      }
      .pat-tracker-history table {
        width: 100%;
        border-collapse: collapse;
        font-size: 13px;
      }
      .pat-tracker-history th,
      .pat-tracker-history td {
        padding: 8px 10px;
        border-bottom: 1px solid rgba(255,255,255,.08);
        text-align: left;
        vertical-align: top;
      }
      .pat-tracker-checkbox {
        transform: scale(1.1);
      }
      .table-wrap {
        max-height: 520px;
        overflow: auto !important;
      }
      .data-table th,
      .data-table td {
        padding: 7px 9px !important;
        font-size: 12px;
      }
      .data-table td {
        max-width: 360px;
      }
      details.pat-details summary {
        cursor: pointer;
        color: #93c5fd;
      }
    `;
    document.head.appendChild(style);
  }

  function insertTrackerPanel(table) {
    const card = table.closest(".card") || table.parentElement;

    const panel = document.createElement("div");
    panel.className = "pat-tracker-panel";
    panel.innerHTML = `
      <h2>Played ticket tracker</h2>
      <p class="pat-tracker-small">
        Select generated rows you actually played, then click <strong>I played selected rows</strong>.
        These records are saved only in this browser using localStorage. Export JSON regularly if you want a backup.
      </p>
      <div class="pat-tracker-actions">
        <button id="pat-save-played">I played selected rows</button>
        <button id="pat-check-results">Check played rows against latest draw</button>
        <button id="pat-export-json">Export played history JSON</button>
        <button id="pat-export-csv">Export played history CSV</button>
        <label>
          Import played history JSON
          <input id="pat-import-json" type="file" accept="application/json,.json">
        </label>
        <button id="pat-clear-history">Clear local history</button>
      </div>
      <div id="pat-tracker-status" class="pat-tracker-small"></div>
      <h3>Played history</h3>
      <div id="pat-played-history" class="pat-tracker-history"></div>
    `;

    card.parentElement.insertBefore(panel, card.nextSibling);
  }

  function addCheckboxesToTicketTable(table) {
    const headerRow = table.querySelector("thead tr");
    if (!headerRow || headerRow.querySelector(".pat-played-header")) return;

    const th = document.createElement("th");
    th.className = "pat-played-header";
    th.innerText = "Played?";
    headerRow.insertBefore(th, headerRow.firstChild);

    const rows = Array.from(table.querySelectorAll("tbody tr"));
    rows.forEach((row, index) => {
      const td = document.createElement("td");
      td.innerHTML = `<input class="pat-tracker-checkbox" type="checkbox" data-ticket-index="${index}">`;
      row.insertBefore(td, row.firstChild);
    });
  }

  function getSelectedTickets(table) {
    const indexes = getColumnIndexes(table);
    const latest = getLatestResultFromPage();

    return Array.from(table.querySelectorAll("tbody tr"))
      .filter((row) => row.querySelector(".pat-tracker-checkbox")?.checked)
      .map((row, rowIndex) => {
        const cells = Array.from(row.querySelectorAll("td"));
        const offset = 0;

        const numbers = parseNumberList(cells[indexes.numbers + offset]?.innerText);
        const stars = parseNumberList(cells[indexes.stars + offset]?.innerText);

        return {
          id: `${Date.now()}-${rowIndex}-${Math.random().toString(36).slice(2)}`,
          saved_at: new Date().toISOString(),
          generated_after_draw_date: latest.draw_date,
          matched_against_draw_date: "",
          numbers,
          stars,
          strategy: cells[indexes.strategy + offset]?.innerText || "generated_candidate",
          final_strategy_score: cells[indexes.score + offset]?.innerText || "",
          why_selected: cells[indexes.why + offset]?.innerText || "",
          main_matches: "",
          star_matches: "",
          result_checked: false,
        };
      })
      .filter(Boolean);
  }

  function renderHistory() {
    const records = loadPlayedTickets();
    const container = document.getElementById("pat-played-history");
    const status = document.getElementById("pat-tracker-status");

    if (!container) return;

    status.innerText = `${records.length} played ticket record(s) stored in this browser.`;

    if (!records.length) {
      container.innerHTML = `<p class="pat-tracker-small" style="padding: 12px;">No played rows stored yet.</p>`;
      return;
    }

    const rows = records
      .slice()
      .reverse()
      .map((record) => {
        return `
          <tr>
            <td>${record.saved_at || ""}</td>
            <td>${record.generated_after_draw_date || ""}</td>
            <td>${(record.numbers || []).join(", ")}</td>
            <td>${(record.stars || []).join(", ")}</td>
            <td>${record.matched_against_draw_date || ""}</td>
            <td>${record.main_matches === "" ? "" : record.main_matches}</td>
            <td>${record.star_matches === "" ? "" : record.star_matches}</td>
          </tr>
        `;
      })
      .join("");

    container.innerHTML = `
      <table>
        <thead>
          <tr>
            <th>Saved at</th>
            <th>Generated after draw</th>
            <th>Numbers</th>
            <th>Stars</th>
            <th>Checked against</th>
            <th>Main matches</th>
            <th>Star matches</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    `;
  }

  function wireButtons(table) {
    document.getElementById("pat-save-played")?.addEventListener("click", () => {
      const selected = getSelectedTickets(table);
      if (!selected.length) {
        alert("Select at least one generated ticket row first.");
        return;
      }

      const existing = loadPlayedTickets();
      savePlayedTickets([...existing, ...selected]);

      table.querySelectorAll(".pat-tracker-checkbox").forEach((checkbox) => {
        checkbox.checked = false;
      })
      .filter(Boolean);

      renderHistory();
    });

    document.getElementById("pat-check-results")?.addEventListener("click", () => {
      const latest = getLatestResultFromPage();

      if (!latest.draw_date || latest.numbers.length !== 5 || latest.stars.length !== 2) {
        alert("Could not read latest winning numbers from the page.");
        return;
      }

      const latestNumbers = new Set(latest.numbers);
      const latestStars = new Set(latest.stars);

      const records = loadPlayedTickets().map((record) => {
        const mainMatches = (record.numbers || []).filter((number) => latestNumbers.has(number)).length;
        const starMatches = (record.stars || []).filter((star) => latestStars.has(star)).length;

        return {
          ...record,
          matched_against_draw_date: latest.draw_date,
          main_matches: mainMatches,
          star_matches: starMatches,
          result_checked: true,
        };
      })
      .filter(Boolean);

      savePlayedTickets(records);
      renderHistory();
    });

    document.getElementById("pat-export-json")?.addEventListener("click", () => {
      downloadText(
        `pat-alyzer-played-history-${new Date().toISOString().slice(0, 10)}.json`,
        JSON.stringify(loadPlayedTickets(), null, 2),
        "application/json"
      );
    });

    document.getElementById("pat-export-csv")?.addEventListener("click", () => {
      const records = loadPlayedTickets();
      const header = [
        "saved_at",
        "generated_after_draw_date",
        "numbers",
        "stars",
        "matched_against_draw_date",
        "main_matches",
        "star_matches",
        "strategy",
        "final_strategy_score",
      ];

      const lines = [
        header.join(","),
        ...records.map((record) =>
          header
            .map((key) => `"${String(Array.isArray(record[key]) ? record[key].join(" ") : record[key] ?? "").replaceAll('"', '""')}"`)
            .join(",")
        ),
      ];

      downloadText(
        `pat-alyzer-played-history-${new Date().toISOString().slice(0, 10)}.csv`,
        lines.join("\n"),
        "text/csv"
      );
    });

    document.getElementById("pat-import-json")?.addEventListener("change", async (event) => {
      const file = event.target.files?.[0];
      if (!file) return;

      const text = await file.text();
      const imported = JSON.parse(text);

      if (!Array.isArray(imported)) {
        alert("Invalid import file. Expected a JSON array.");
        return;
      }

      const existing = loadPlayedTickets();
      savePlayedTickets([...existing, ...imported]);
      renderHistory();
    });

    document.getElementById("pat-clear-history")?.addEventListener("click", () => {
      if (!confirm("Clear all locally stored played ticket history in this browser?")) return;
      savePlayedTickets([]);
      renderHistory();
    });
  }

  function init() {
    addTrackerStyles();

    const table = findGeneratedTicketsTable();
    if (!table) return;

    addCheckboxesToTicketTable(table);
    insertTrackerPanel(table);
    wireButtons(table);
    renderHistory();
  }

  document.addEventListener("DOMContentLoaded", init);
})();

