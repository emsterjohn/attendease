(() => {
  const ids = {
    total: "live-total",
    present: "live-present",
    late: "live-late",
    absent: "live-absent"
  };
  async function refresh() {
    if (document.visibilityState !== "visible") return;
    try {
      const response = await fetch("/api/dashboard/live", {
        credentials: "same-origin",
        cache: "no-store"
      });
      if (!response.ok) return;
      const data = await response.json();
      if (!data.counts) return;
      Object.keys(ids).forEach(function(key) {
        const node = document.getElementById(ids[key]);
        if (node) node.textContent = data.counts[key] || 0;
      });
      const body = document.getElementById("live-recent");
      if (!body) return;
      body.replaceChildren();
      (data.recent || []).forEach(function(record) {
        const row = document.createElement("tr");
        [record.full_name, record.student_id, record.attendance_date, String(record.check_in || "").slice(0, 5)].forEach(function(value, index) {
          const cell = document.createElement("td");
          cell.textContent = value || "—";
          if (index === 0) cell.className = "fw-semibold";
          row.appendChild(cell);
        });
        const statusCell = document.createElement("td");
        const pill = document.createElement("span");
        pill.className = "status-pill " + String(record.status).toLowerCase();
        pill.textContent = record.status;
        statusCell.appendChild(pill);
        row.appendChild(statusCell);
        const methodCell = document.createElement("td");
        methodCell.textContent = record.method || "—";
        row.appendChild(methodCell);
        body.appendChild(row);
      });
      if (!body.children.length) {
        const row = document.createElement("tr");
        const cell = document.createElement("td");
        cell.colSpan = 6;
        cell.className = "empty-state";
        cell.textContent = "No attendance records yet.";
        row.appendChild(cell);
        body.appendChild(row);
      }
    } catch (error) {}
  }
  refresh();
  window.setInterval(refresh, 5000);
})();
