// Open/close <dialog> modals declared with data-open-dialog / data-close-dialog.
document.addEventListener("click", (e) => {
  const opener = e.target.closest("[data-open-dialog]");
  if (opener) {
    const dialog = document.getElementById(opener.dataset.openDialog);
    dialog.showModal();
    dialog.querySelector("input:not([type=hidden])")?.focus();
    return;
  }
  if (e.target.closest("[data-close-dialog]")) {
    e.target.closest("dialog").close();
    return;
  }
  // Click on the backdrop (outside the dialog box) closes it.
  if (e.target.tagName === "DIALOG") e.target.close();
});
