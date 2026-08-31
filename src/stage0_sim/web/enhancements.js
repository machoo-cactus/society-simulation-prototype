document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy-target]");
  if (!button) return;
  const target = document.querySelector(button.dataset.copyTarget);
  if (!target) return;
  try {
    await navigator.clipboard.writeText(target.textContent);
    button.textContent = "Copied";
  } catch (error) {
    button.textContent = "Copy failed";
    console.error("Clipboard write failed", error);
  }
});
