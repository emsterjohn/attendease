(() => {
  const resultBox = document.getElementById("scan-result");
  const confirmation = document.getElementById("scan-confirmation");
  const nameField = document.getElementById("scanned-name");
  const idField = document.getElementById("scanned-id");
  const confirmButton = document.getElementById("confirm-scan");
  const cancelButton = document.getElementById("cancel-scan");

  if (!window.Html5Qrcode) {
    resultBox.className = "alert alert-danger mt-3";
    resultBox.textContent = "QR scanner library could not load. Check your internet connection.";
    return;
  }

  const scanner = new Html5Qrcode("reader");
  let scannerRunning = false;
  let busy = false;
  let pendingToken = null;

  function showResult(message, kind = "info") {
    resultBox.className = `alert alert-${kind} mt-3`;
    resultBox.textContent = message;
    resultBox.classList.remove("d-none");
  }

  async function startScanner() {
    if (scannerRunning) return;
    try {
      const cameras = await Html5Qrcode.getCameras();
      if (!cameras || cameras.length === 0) {
        showResult("No camera was found on this device.", "danger");
        return;
      }
      await scanner.start(
        cameras[0].id,
        { fps: 10, qrbox: { width: 240, height: 240 } },
        onScanSuccess
      );
      scannerRunning = true;
    } catch (error) {
      showResult("Camera could not start. Allow camera permission and use HTTPS or localhost.", "danger");
    }
  }

  async function stopScanner() {
    if (!scannerRunning) return;
    try {
      await scanner.stop();
    } catch (error) {
      // Continue to the confirmation flow even if the camera was already stopped.
    }
    scannerRunning = false;
  }

  async function onScanSuccess(decodedText) {
    if (busy || pendingToken) return;
    busy = true;
    pendingToken = decodedText;
    await stopScanner();

    try {
      const response = await fetch("/api/scan/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: decodedText })
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
      resultBox.classList.add("d-none");
      if (data.already_recorded) {
        showResult(`${data.full_name} has already been marked ${data.existing_status} today at ${data.existing_time}.`, "warning");
      } else {
        showResult(`QR code identified: ${data.full_name}. Please confirm the identity below.`, "success");
      }
    } catch (error) {
      pendingToken = null;
      showResult("Could not contact the server. Please try scanning again.", "danger");
      busy = false;
      await startScanner();
    }
  }

  confirmButton.addEventListener("click", async () => {
    if (!pendingToken || confirmButton.disabled) return;
    confirmButton.disabled = true;
    try {
      const response = await fetch("/api/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: pendingToken })
      });
      const data = await response.json();
      confirmation.classList.add("d-none");
      if (response.ok) {
        showResult(`Attendance confirmed for ${data.full_name} (${data.student_id}). Status: ${data.status}. Date: ${data.attendance_date}, time: ${data.check_in}.`, "success");
      } else {
        showResult(`${data.full_name || nameField.textContent} (${data.student_id || idField.textContent}): ${data.message || "Attendance could not be recorded."}`, "warning");
      }
      pendingToken = null;
      busy = false;
      window.setTimeout(() => startScanner(), 1800);
    } catch (error) {
      showResult("Could not save attendance. Please try again.", "danger");
      pendingToken = null;
      busy = false;
      await startScanner();
    } finally {
      confirmButton.disabled = false;
    }
  });

  cancelButton.addEventListener("click", async () => {
    pendingToken = null;
    confirmation.classList.add("d-none");
    busy = false;
    showResult("Scan cancelled. You can scan another student.", "secondary");
    await startScanner();
  });

  startScanner();
})();
