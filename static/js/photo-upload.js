// "A deck from photos" (B-14): shrink the photos before they are sent.
//
// A phone photo is 3 to 8 MB; the model reads titles at 1600 pixels on the
// long edge, which is a few hundred kB. Drawing a photo on a canvas also
// leaves its EXIF (place, time, phone) behind. An enhancement only: without
// it, or when a browser cannot do it, the photos go as they are and the
// server shrinks them (decks/photos.py).
const EDGE = 1600;
const QUALITY = 0.85;

async function shrink(file, index) {
  const bitmap = await createImageBitmap(file, {imageOrientation: "from-image"});
  const scale = Math.min(1, EDGE / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close();
  const blob = await new Promise((done) => canvas.toBlob(done, "image/jpeg", QUALITY));
  if (!blob) throw new Error("not drawn");
  return new File([blob], `photo-${index + 1}.jpg`, {type: "image/jpeg"});
}

document.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector("[data-photo-form]");
  if (!form) return;
  const input = form.querySelector("input[type=file]");
  const zone = form.querySelector("[data-dropzone]");
  const name = form.querySelector("[data-dropzone-name]");
  const button = form.querySelector("[data-photo-submit]");
  const status = form.querySelector("[data-photo-status]");
  const max = Number(input.dataset.maxPhotos);
  const label = button.textContent;
  let sending = false;

  const show = () => {
    const count = input.files.length;
    name.textContent = count ? Array.from(input.files, (file) => file.name).join(", ") : "";
    zone.classList.toggle("has-file", count > 0);
    input.setCustomValidity(count > max ? status.dataset.tooMany || `≤ ${max}` : "");
  };
  input.addEventListener("change", show);
  input.addEventListener("dragenter", () => zone.classList.add("is-dragover"));
  for (const type of ["dragleave", "drop"]) {
    input.addEventListener(type, () => zone.classList.remove("is-dragover"));
  }
  show();

  // Back from the next page with the browser's cache: the button works again.
  window.addEventListener("pageshow", () => {
    sending = false;
    button.disabled = false;
    button.textContent = label;
  });

  form.addEventListener("submit", async (event) => {
    if (sending) return;
    event.preventDefault();
    sending = true;
    button.disabled = true;
    button.textContent = button.dataset.busy;
    try {
      const files = await Promise.all(Array.from(input.files, shrink));
      const shrunk = new DataTransfer();
      files.forEach((file) => shrunk.items.add(file));
      input.files = shrunk.files;
    } catch {
      // As they are: the server shrinks them too.
    }
    form.submit();
  });
});
