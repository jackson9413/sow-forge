/* sow-forge — vanilla JS frontend */
(function () {
  const analyzeBtn = document.getElementById("analyze-btn");
  const sowInput = document.getElementById("sow-text");
  const saveBox = document.getElementById("save-session");
  const resultsCard = document.getElementById("results-card");
  const resultsContainer = document.getElementById("results-container");
  const historyContainer = document.getElementById("history-container");

  analyzeBtn.addEventListener("click", runAnalysis);
  sowInput.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      runAnalysis();
    }
  });

  loadHistory();

  async function runAnalysis() {
    const text = sowInput.value.trim();
    if (!text) {
      alert("Paste a SOW first.");
      sowInput.focus();
      return;
    }
    analyzeBtn.disabled = true;
    analyzeBtn.textContent = "Analyzing…";

    try {
      const resp = await fetch("/api/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text: text,
          save: saveBox.checked,
        }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.error || `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      renderResults(data);
      if (saveBox.checked) {
        loadHistory();
      }
    } catch (e) {
      resultsCard.hidden = false;
      resultsContainer.innerHTML = `<p class="muted">Error: ${escapeHtml(e.message)}</p>`;
    } finally {
      analyzeBtn.disabled = false;
      analyzeBtn.textContent = "Analyze SOW";
    }
  }

  function renderResults(data) {
    resultsCard.hidden = false;
    resultsContainer.innerHTML = "";

    const { profile, scoring, flags, missing, patterns, summary_markdown, session_id } = data;

    const banner = el("div", "score-banner");
    const scoreNum = el("div", "score-number");
    scoreNum.textContent = String(scoring.overall);
    if (scoring.overall >= 80) scoreNum.classList.add("good");
    else if (scoring.overall >= 50) scoreNum.classList.add("warn");
    else scoreNum.classList.add("bad");
    banner.appendChild(scoreNum);

    const verdictInfo = el("div");
    verdictInfo.appendChild(el("div", "verdict-label", scoring.verdict_label));
    verdictInfo.appendChild(
      el(
        "div",
        "verdict-meta",
        `${scoring.verdict} · session #${session_id || "n/a"}`
      )
    );
    banner.appendChild(verdictInfo);
    resultsContainer.appendChild(banner);

    // Profile grid
    const profileCard = el("div", "profile-grid");
    addProfileCell(profileCard, "Engagement type", profile.engagement_type || "—");
    addProfileCell(profileCard, "Fee model", profile.fee_model || "—");
    addProfileCell(profileCard, "Term", profile.term || "—");
    addProfileCell(profileCard, "Project value", profile.project_value_display || "—");
    addProfileCell(profileCard, "Deliverables", String(profile.deliverable_count || 0));
    addProfileCell(profileCard, "Effective date", profile.effective_date || "—");
    addProfileCell(profileCard, "Governing law", profile.governing_law || "—");
    addProfileCell(profileCard, "Venue", profile.venue || "—");
    addProfileCell(profileCard, "Parties", profile.parties_display || "—");
    addProfileCell(profileCard, "Word count", String(profile.input_word_count || 0));
    resultsContainer.appendChild(profileCard);

    // Score axes
    const axesTitle = el("div", "section-title", "Score axes");
    resultsContainer.appendChild(axesTitle);
    const axesList = el("div", "axis-list");
    scoring.axes.forEach((a) => {
      const row = el("div", "axis-row");
      const left = el("div");
      const bar = el("span", "bar");
      const barInner = el("span");
      barInner.style.width = a.raw_score + "%";
      if (a.raw_score < 30) barInner.classList.add("bad");
      else if (a.raw_score < 60) barInner.classList.add("warn");
      else barInner.classList.add("good");
      bar.appendChild(barInner);
      const name = el("span", "name");
      name.append(bar, document.createTextNode(`${a.label} (${a.weight}%)`));
      left.appendChild(name);
      const right = el("div", "score", String(a.raw_score));
      row.append(left, right);
      axesList.appendChild(row);
    });
    resultsContainer.appendChild(axesList);

    // Tabs
    const tabs = el("div", "tabs");
    const panes = el("div");

    const tabDefs = [
      { key: "patterns", label: `Risk patterns (${patterns.length})` },
      { key: "flags", label: `Red flags (${flags.filter((f) => !f.is_presence_check).length})` },
      { key: "missing", label: `Missing clauses (${missing.length})` },
      { key: "presence", label: `Healthy clauses (${flags.filter((f) => f.is_presence_check).length})` },
      { key: "summary", label: "Markdown summary" },
      { key: "json", label: "JSON" },
    ];

    tabDefs.forEach((t, idx) => {
      const tab = el("button", "tab" + (idx === 0 ? " active" : ""), t.key);
      tab.textContent = t.label;
      tab.addEventListener("click", () => {
        tabs.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
        panes.querySelectorAll(".tab-pane").forEach((x) => x.classList.remove("active"));
        tab.classList.add("active");
        panes.querySelector(`[data-pane="${t.key}"]`).classList.add("active");
      });
      tabs.appendChild(tab);
    });

    tabDefs.forEach((t, idx) => {
      const pane = el("div", "tab-pane" + (idx === 0 ? " active" : ""));
      pane.dataset.pane = t.key;
      if (t.key === "patterns") renderPatternPane(pane, patterns);
      else if (t.key === "flags") renderFlagPane(pane, flags);
      else if (t.key === "missing") renderMissingPane(pane, missing);
      else if (t.key === "presence") renderPresencePane(pane, flags);
      else if (t.key === "summary") renderSummaryPane(pane, summary_markdown);
      else if (t.key === "json") renderJsonPane(pane, data);
      panes.appendChild(pane);
    });

    resultsContainer.append(tabs, panes);

    // Export row
    const exportRow = el("div", "export-row");
    const copyMd = el("button", "ghost tiny", "copy-md");
    copyMd.textContent = "Copy markdown";
    copyMd.addEventListener("click", () => copyToClipboard(summary_markdown, copyMd));
    const downloadMd = el("button", "ghost tiny", "download-md");
    downloadMd.textContent = "Download .md";
    downloadMd.addEventListener("click", () => downloadText(summary_markdown, "sow-review.md", "text/markdown"));
    const downloadJson = el("button", "ghost tiny", "download-json");
    downloadJson.textContent = "Download .json";
    downloadJson.addEventListener("click", () => downloadText(JSON.stringify(data, null, 2), "sow-review.json", "application/json"));
    exportRow.append(copyMd, downloadMd, downloadJson);
    resultsContainer.appendChild(exportRow);
  }

  function renderPatternPane(pane, patterns) {
    if (patterns.length === 0) {
      pane.appendChild(el("p", "muted", "No risk-pattern combinations triggered."));
      return;
    }
    const list = el("div", "pattern-list");
    patterns.forEach((p) => {
      const item = el("div", `pattern ${p.severity}`);
      const header = el("div", "pattern-header");
      header.append(
        el("span", "pattern-id", p.id),
        severityTag(p.severity)
      );
      item.appendChild(header);
      item.appendChild(el("div", "pattern-triggered", p.label));
      item.appendChild(
        el("div", "pattern-triggered", `Triggered: ${p.triggered.join(" + ")}`)
      );
      list.appendChild(item);
    });
    pane.appendChild(list);
  }

  function renderFlagPane(pane, flags) {
    const negative = flags.filter((f) => !f.is_presence_check);
    if (negative.length === 0) {
      pane.appendChild(el("p", "muted", "No red flags detected. (Verify the SOW actually contains the clauses you expect.)"));
    } else {
      const list = el("div", "flag-list");
      negative.sort((a, b) => b.score - a.score);
      negative.forEach((f) => {
        const item = el("div", `flag ${f.severity}`);
        const header = el("div", "flag-header");
        header.append(
          el("span", "flag-id", f.id + " · " + f.axis),
          severityTag(f.severity)
        );
        item.appendChild(header);
        item.appendChild(el("div", "flag-desc", f.description));
        if (f.evidence) {
          const ev = el("span", "flag-evidence", f.evidence);
          item.appendChild(ev);
        }
        item.appendChild(el("span", "score-chip", "−" + f.score + " pts"));
        if (f.negotiation) {
          const neg = el("div", "negotiation");
          neg.appendChild(el("div", "label", "Counter-language"));
          neg.appendChild(el("div", "", null, f.negotiation));
          item.appendChild(neg);
        }
        list.appendChild(item);
      });
      pane.appendChild(list);
    }
  }

  function renderMissingPane(pane, missing) {
    if (missing.length === 0) {
      pane.appendChild(el("p", "muted", "No missing-clause gaps detected. Healthy."));
      return;
    }
    const list = el("div", "missing-list");
    const severityOrder = { high: 3, medium: 2, low: 1 };
    missing.sort((a, b) => (severityOrder[b.severity] || 0) - (severityOrder[a.severity] || 0));
    missing.forEach((m) => {
      const item = el("div", `missing ${m.severity}`);
      const header = el("div", "missing-header");
      header.append(
        el("span", "missing-id", m.id + " · " + m.axis),
        severityTag(m.severity)
      );
      item.appendChild(header);
      item.appendChild(el("div", "", null, m.label));
      item.appendChild(el("div", "missing-why", m.why));
      item.appendChild(el("span", "score-chip", "−" + m.score_if_missing + " pts"));
      list.appendChild(item);
    });
    pane.appendChild(list);
  }

  function renderPresencePane(pane, flags) {
    const positive = flags.filter((f) => f.is_presence_check);
    if (positive.length === 0) {
      pane.appendChild(el("p", "muted", "No healthy clauses detected."));
      return;
    }
    const list = el("div", "flag-list");
    positive.forEach((f) => {
      const item = el("div", "presence");
      const header = el("div", "flag-header");
      header.append(
        el("span", "flag-id", f.id + " · " + f.axis),
        severityTag("info")
      );
      item.appendChild(header);
      item.appendChild(el("div", "flag-desc", f.description));
      if (f.evidence) item.appendChild(el("span", "flag-evidence", f.evidence));
      list.appendChild(item);
    });
    pane.appendChild(list);
  }

  function renderSummaryPane(pane, md) {
    const pre = el("div", "markdown-summary");
    pre.textContent = md;
    pane.appendChild(pre);
  }

  function renderJsonPane(pane, data) {
    const pre = el("div", "markdown-summary");
    pre.textContent = JSON.stringify(data, null, 2);
    pane.appendChild(pre);
  }

  // ----- History -----
  async function loadHistory() {
    try {
      const resp = await fetch("/api/sessions");
      const json = await resp.json();
      renderHistory(json.sessions || []);
    } catch (e) {
      historyContainer.innerHTML = `<p class="muted">Could not load history: ${escapeHtml(e.message)}</p>`;
    }
  }

  function renderHistory(sessions) {
    historyContainer.innerHTML = "";
    if (!sessions.length) {
      historyContainer.appendChild(el("p", "muted", "No saved SOWs yet. Analyze one above to start."));
      return;
    }
    sessions.forEach((s) => {
      const item = el("div", "history-item");
      const score = el("div", "score");
      score.textContent = s.score != null ? String(s.score) : "—";
      if (s.score != null) {
        if (s.score >= 80) score.classList.add("good");
        else if (s.score >= 50) score.classList.add("warn");
        else score.classList.add("bad");
      }
      item.appendChild(score);

      const meta = el("div", "meta");
      meta.appendChild(el("div", "", `${s.engagement_type || "—"} · ${s.fee_model || "—"} · ${s.parties || "no parties detected"}`));
      const when = el("div", "when", formatDate(s.created_at));
      meta.appendChild(when);
      // Rating
      const ratingRow = el("div", "rating-row");
      for (let i = 1; i <= 5; i++) {
        const star = el("button", "star" + (i <= (s.rating || 0) ? " on" : ""));
        star.textContent = "★";
        star.title = `${i} star${i === 1 ? "" : "s"}`;
        star.addEventListener("click", () => rateSession(s.id, i));
        ratingRow.appendChild(star);
      }
      meta.appendChild(ratingRow);
      // Notes
      if (s.notes) meta.appendChild(el("div", "muted", s.notes));
      item.appendChild(meta);

      const actions = el("div");
      const view = el("button", "ghost tiny");
      view.textContent = "View";
      view.addEventListener("click", () => viewSession(s.id));
      actions.appendChild(view);
      item.appendChild(actions);

      historyContainer.appendChild(item);
    });
  }

  async function rateSession(id, rating) {
    try {
      await fetch(`/api/sessions/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rating }),
      });
      loadHistory();
    } catch (e) {
      console.error(e);
    }
  }

  async function viewSession(id) {
    try {
      const resp = await fetch(`/api/sessions/${id}`);
      const json = await resp.json();
      if (json.output) {
        renderResults(json.output);
        resultsCard.scrollIntoView({ behavior: "smooth" });
      }
    } catch (e) {
      console.error(e);
    }
  }

  // ----- Helpers -----
  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function addProfileCell(parent, k, v) {
    const cell = el("div", "profile-cell");
    cell.appendChild(el("span", "k", k));
    cell.appendChild(el("span", "v", v));
    parent.appendChild(cell);
  }

  function severityTag(sev) {
    const t = el("span", "severity-tag " + sev, sev.toUpperCase());
    return t;
  }

  function formatDate(iso) {
    if (!iso) return "";
    try {
      return new Date(iso).toLocaleString();
      } catch (e) {
      return iso;
      }
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  function copyToClipboard(text, btn) {
    navigator.clipboard.writeText(text).then(
      () => {
        const orig = btn.textContent;
        btn.textContent = "Copied ✓";
        setTimeout(() => { btn.textContent = orig; }, 1500);
      },
      () => alert("Copy failed.")
    );
  }

  function downloadText(text, filename, mime) {
    const blob = new Blob([text], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }
})();