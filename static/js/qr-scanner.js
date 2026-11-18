(() => {
  const resultBox = document.getElementById("scan-result");
  const confirmation = document.getElementById("scan-confirmation");
  const nameField = document.getElementById("scanned-name");
  const idField = document.getElementById("scanned-id");
  const confirmButton = document.getElementById("confirm-scan");
  const cancelButton = document.getElementById("cancel-scan");
  const flipButton = document.getElementById("flip-camera");
  const searchInput = document.getElementById("scan-student-search");
  if (!window.Html5Qrcode) {
    resultBox.className = "alert alert-danger mt-3";
    resultBox.textContent = "The QR scanner could not load. Check your internet connection.";
    return;
  }
  const scanner = new Html5Qrcode("reader");
  let cameras = [];
  let cameraIndex = 0;
  let scannerRunning = false;
  let busy = false;
  let pendingToken = null;
  let liveStudents = [];

  function showResult(message, kind) {
    resultBox.className = "alert alert-" + kind + " mt-3";
    resultBox.textContent = message;
    resultBox.classList.remove("d-none");
  }

  async function startScanner() {
    if (scannerRunning || busy) return;
    try {
      if (!cameras.length) cameras = await Html5Qrcode.getCameras();
      if (!cameras.length) {
        showResult("No camera was found on this device.", "danger");
        flipButton.disabled = true;
        return;
      }
      flipButton.disabled = cameras.length < 2;
      await scanner.start(
        cameras[cameraIndex].id,
        { fps: 10, qrbox: { width: 240, height: 240 } },
        handleScan
      );
      scannerRunning = true;
    } catch (error) {
      showResult("Camera could not start. Allow camera permission and use HTTPS or localhost.", "danger");
    }
  }

  async function stopScanner() {
    if (!scannerRunning) return;
    try { await scanner.stop(); } catch (error) {}
    scannerRunning = false;
  }

  async function handleScan(token) {
    if (busy || pendingToken) return;
    busy = true;
    pendingToken = token;
    await stopScanner();
    try {
      const response = await fetch("/api/scan/preview", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: token })
      });
      const data = await response.json();
      if (!response.ok || !data.ok) {
        pendingToken = null;
        showResult(data.message || "This QR code could not be identified.", "danger");
        busy = false;
        await startScanner();
        return;
      }
      nameField.textContent = data.full_name;
      idField.textContent = data.student_id;
      confirmation.classList.remove("d-none");
      if (data.already_recorded) {
        showResult(data.full_name + " has already been marked " + data.existing_status +
          " today at " + data.existing_time + ".", "warning");
      } else {
        showResult("QR code identified: " + data.full_name + ". Confirm the student's identity below.", "success");
      }
    } catch (error) {
      pendingToken = null;
      showResult("Could not contact the server. Please try again.", "danger");
      busy = false;
      await startScanner();
    }
  }

  function renderStudents() {
    const body = document.getElementById("scan-student-rows");
    if (!body) return;
    const query = (searchInput.value || "").trim().toLowerCase();
    const rows = liveStudents.filter(function(student) {
      return (student.full_name + " " + student.student_id).toLowerCase().includes(query);
    });
    body.replaceChildren();
    if (!rows.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 4;
      cell.className = "empty-state";
      cell.textContent = "No matching students found.";
      row.appendChild(cell);
      body.appendChild(row);
      return;
    }
    rows.forEach(function(student) {
      const row = document.createElement("tr");
      [student.full_name, student.student_id].forEach(function(value) {
        const cell = document.createElement("td");
        cell.textContent = value || "—";
        row.appendChild(cell);
      });
      const status = student.status || "Not Yet Scanned";
      const statusCell = document.createElement("td");
      const pill = document.createElement("span");
      pill.className = "status-pill " + (status === "Not Yet Scanned" ? "unrecorded" : status.toLowerCase());
      pill.textContent = status;
      statusCell.appendChild(pill);
      row.appendChild(statusCell);
      const timeCell = document.createElement("td");
      timeCell.textContent = student.check_in || "—";
      row.appendChild(timeCell);
      body.appendChild(row);
    });
  }

  async function refreshStudents() {
    try {
      const response = await fetch("/api/dashboard/live", {
        credentials: "same-origin",
        cache: "no-store"
      });
      if (!response.ok) return;
      const data = await response.json();
      liveStudents = data.students || [];
      renderStudents();
      const counts = data.counts || {};
      document.getElementById("scan-present-count").textContent = "Present " + (counts.present || 0);
      document.getElementById("scan-late-count").textContent = "Late " + (counts.late || 0);
      document.getElementById("scan-absent-count").textContent = "Absent " + (counts.absent || 0);
      const pending = Math.max(0, (counts.total || 0) - (counts.present || 0) -
        (counts.late || 0) - (counts.absent || 0));
      document.getElementById("scan-pending-count").textContent = "Not scanned " + pending;
    } catch (error) {}
  }

  confirmButton.addEventListener("click", async function() {
    if (!pendingToken || confirmButton.disabled) return;
    confirmButton.disabled = true;
    try {
      const response = await fetch("/api/scan", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: pendingToken })
      });
      const data = await response.json();
      confirmation.classList.add("d-none");
      if (response.ok && data.ok) {
        showResult("Attendance recorded. " + data.full_name + " is " + data.status +
          ". Scan time: " + data.check_in + ".", "success");
      } else {
        showResult((data.full_name || nameField.textContent) + ": " +
          (data.message || "Attendance could not be recorded."), "warning");
      }
      pendingToken = null;
      busy = false;
      await refreshStudents();
      window.setTimeout(startScanner, 1200);
    } catch (error) {
      showResult("Could not save attendance. Please try again.", "danger");
      pendingToken = null;
      busy = false;
      await startScanner();
    } finally {
      confirmButton.disabled = false;
    }
  });

  cancelButton.addEventListener("click", async function() {
    pendingToken = null;
    confirmation.classList.add("d-none");
    busy = false;
    showResult("Scan cancelled. You can scan another student.", "secondary");
    await startScanner();
  });

  flipButton.addEventListener("click", async function() {
    if (cameras.length < 2) {
      showResult("This device has only one available camera.", "warning");
      return;
    }
    const restart = scannerRunning;
    await stopScanner();
    cameraIndex = (cameraIndex + 1) % cameras.length;
    if (restart) await startScanner();
    showResult("Camera switched successfully.", "info");
  });

  searchInput.addEventListener("input", renderStudents);
  refreshStudents();
  window.setInterval(function() {
    if (document.visibilityState === "visible") refreshStudents();
  }, 5000);
  startScanner();
})();
