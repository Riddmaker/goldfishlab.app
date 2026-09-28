// The import page's drop zone: show which file was chosen, and light up while
// a file is dragged over it. An enhancement only - the real <input type=file>
// lies over the whole zone, so choosing and dropping both work without this.
document.addEventListener("DOMContentLoaded", () => {
  for (const zone of document.querySelectorAll("[data-dropzone]")) {
    const input = zone.querySelector("input[type=file]");
    const name = zone.querySelector("[data-dropzone-name]");
    const show = () => {
      name.textContent = input.files.length ? input.files[0].name : "";
      zone.classList.toggle("has-file", input.files.length > 0);
    };
    input.addEventListener("change", show);
    input.addEventListener("dragenter", () => zone.classList.add("is-dragover"));
    for (const type of ["dragleave", "drop"]) {
      input.addEventListener(type, () => zone.classList.remove("is-dragover"));
    }
    // A reload or the back button can restore a file the browser remembers.
    show();
  }
});
