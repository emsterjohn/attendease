(() => {
  let knownRecordId = null;
  let initialized = false;
  const notice = document.getElementById("student-attendance-notice");
  function render(data) {
    const current = data.current;
    const title = document.getElementById("student-current-status");
    const time = document.getElementById("student-current-time");
    const badge = document.getElementById("student-current-badge");
    if (current) {
      title.textContent = current.status === "Present" ? "You are Present!" :
        current.status === "Late" ? "You are Late." : "You are Absent.";
      time.textContent = "Recorded on " + current.attendance_date + " at " + current.check_in + ".";
      badge.textContent = current.status;
      badge.className = "status-pill " + current.status.toLowerCase();
      if (initialized && knownRecordId !== current.id) {
        notice.className = "alert alert-success mb-4";
        notice.textContent = title.textContent + " Your attendance has been successfully recorded.";
      }
      knownRecordId = current.id;
    } else {
      title.textContent = "Not recorded yet";
      time.textContent = "Your attendance result will appear here after your teacher scans your QR code.";
      badge.textContent = "Pending";
      badge.className = "status-pill unrecorded";
    }
    initialized = true;
    ["present", "late", "absent"].forEach(function(key) {
      const node = document.getElementById("student-count-" + key);
      if (node) node.textContent = (data.counts || {})[key] || 0;
    });
    const body = document.getElementById("student-history-body");
    if (!body) return;
    body.replaceChildren();
    (data.records || []).forEach(function(record) {
      const row = document.createElement("tr");
      [record.attendance_date, record.check_in].forEach(function(value) {
        const cell = document.createElement("td");
        cell.textContent = value || "—";
        row.appendChild(cell);
      });
      const statusCell = document.createElement("td");
      const pill = document.createElement("span");
      pill.className = "status-pill " + record.status.toLowerCase();
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
      cell.colSpan = 4;
      cell.className = "empty-state";
      cell.textContent = "No attendance history yet.";
      row.appendChild(cell);
      body.appendChild(row);
    }
  }
  async function refresh() {
    if (document.visibilityState !== "visible") return;
    try {
      const response = await fetch("/api/dashboard/live", {
        credentials: "same-origin",
        cache: "no-store"
      });
      if (response.ok) {
        const data = await response.json();
        if (data.role === "student") render(data);
      }
    } catch (error) {}
  }
  refresh();
  window.setInterval(refresh, 5000);
})();
