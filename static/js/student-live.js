(() => {
  let knownAttendance = null;
  let initialized = false;
  const notice = document.getElementById("student-attendance-notice");

  function statusMessage(status, checkIn) {
    const time = String(checkIn || "").slice(0, 5);
    if (status === "Present") {
      return {
        title: "You are marked Present",
        detail: time ? "Your attendance was recorded successfully at " + time + "." : "Your attendance was recorded successfully.",
        notice: "You are marked PRESENT. Your attendance was recorded at " + (time || "the recorded time") + ".",
        kind: "success"
      };
    }
    if (status === "Late") {
      return {
        title: "You are marked Late",
        detail: time ? "Your attendance was recorded at " + time + "." : "Your attendance was recorded successfully.",
        notice: "You are marked LATE. Your attendance was recorded at " + (time || "the recorded time") + ".",
        kind: "warning"
      };
    }
    return {
      title: "You are marked Absent",
      detail: "Your attendance status for today is Absent. Please contact your teacher if you believe this is incorrect.",
      notice: "Your attendance status has been updated to ABSENT. Please contact your teacher if you believe this is incorrect.",
      kind: "danger"
    };
  }

  function render(data) {
    const current = data.current;
    const title = document.getElementById("student-current-status");
    const time = document.getElementById("student-current-time");
    const badge = document.getElementById("student-current-badge");

    if (current) {
      const message = statusMessage(current.status, current.check_in);
      title.textContent = message.title;
      time.textContent = message.detail;
      badge.textContent = current.status;
      badge.className = "status-pill " + current.status.toLowerCase();

      const attendanceKey = [current.id, current.status, current.check_in].join(":");
      if (initialized && knownAttendance !== attendanceKey && notice) {
        notice.className = "alert alert-" + message.kind + " mb-4";
        notice.textContent = "Attendance update: " + message.notice;
      }
      knownAttendance = attendanceKey;
    } else {
      title.textContent = "Not recorded yet";
      time.textContent = "Your attendance status will appear here after your teacher scans your QR code.";
      badge.textContent = "Pending";
      badge.className = "status-pill unrecorded";
      knownAttendance = null;
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
      [record.attendance_date, String(record.check_in || "").slice(0, 5)].forEach(function(value) {
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
