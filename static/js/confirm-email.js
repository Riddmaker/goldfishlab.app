// The page behind the link in the confirmation mail: confirm right away.
// Only a POST confirms (a GET would let mail link scanners confirm addresses
// nobody clicked), so this submits the page's one form as soon as it opens.
// Without JavaScript the form is a plain "Confirm my email" button.
document.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector("form[data-autosubmit]");
  if (form) {
    form.submit();
  }
});
