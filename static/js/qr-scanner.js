(() => {
  const resultBox = document.getElementById("scan-result");
  const confirmation = document.getElementById("scan-confirmation");
  const nameField = document.getElementById("scanned-name");
  const idField = document.getElementById("scanned-id");
  const confirmButton = document.getElementById("confirm-scan");
  const cancelButton = document.getElementById("cancel-scan");
  const switchCameraButton = document.getElementById("switch-camera");
  const rotateScreenButton = document.getElementById("rotate-screen");
  let cameras = [];
  let activeCameraIndex = 0;

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
      cameras = await Html5Qrcode.getCameras();
      if (!cameras || cameras.length === 0) {
        showResult("No camera was found on this device.", "danger");
        return;
      }
      activeCameraIndex = Math.min(activeCameraIndex, cameras.length - 1);
      await scanner.start(
        cameras[activeCameraIndex].id,
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

  switchCameraButton.addEventListener("click", async () => {
    if (busy) return;
    try {
      cameras = await Html5Qrcode.getCameras();
      if (!cameras || cameras.length < 2) {
        showResult("Only one camera is available on this device.", "warning");
        return;
      }
      await stopScanner();
      activeCameraIndex = (activeCameraIndex + 1) % cameras.length;
      await startScanner();
      showResult("Camera switched successfully.", "success");
    } catch (error) {
      showResult("Unable to switch camera. Check camera permissions.", "danger");
    }
  });

  rotateScreenButton.addEventListener("click", async () => {
    try {
      if (screen.orientation && screen.orientation.lock) {
        const next = screen.orientation.type.startsWith("portrait") ? "landscape" : "portrait";
        await screen.orientation.lock(next);
        showResult("Screen orientation changed.", "success");
      } else {
        showResult("Screen rotation is controlled by your device. Turn on auto-rotate and rotate your phone.", "info");
      }
    } catch (error) {
      showResult("Your browser does not allow locking screen orientation. Use your phone's auto-rotate setting.", "info");
    }
  });

  startScanner();
})();
