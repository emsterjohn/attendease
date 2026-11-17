// Refresh today's attendance statuses while the student directory is open.
(() => {
  window.setInterval(() => {
    if (document.visibilityState === "visible" && !document.querySelector(":focus")) {
      window.location.reload();
    }
  }, 10000);
})();
