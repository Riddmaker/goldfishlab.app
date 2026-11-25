/* Customisation (B-10): the preview follows the form while you choose.
 *
 * The preview is four CSS variables on [data-look-preview] (looks.css.style).
 * A preset carries its own CSS in data-css; a colour comes from its text
 * field, the picker or a suggestion, and only a colour the browser accepts
 * reaches the preview (the server checks it again on save). An image you
 * have not saved yet is not shown: the page may not load local files
 * (img-src), so the preview shows it once it is saved.
 */
(function () {
  'use strict';

  const form = document.querySelector('[data-look-form]');
  const preview = document.querySelector('[data-look-preview]');
  if (!form || !preview) return;

  const valid = (value) => value !== '' && CSS.supports('color', value);
  const hex6 = (value) => {
    const probe = document.createElement('span');
    probe.style.color = value;
    document.body.appendChild(probe);
    const rgb = getComputedStyle(probe).color.match(/\d+/g) || [0, 0, 0];
    probe.remove();
    return '#' + rgb.slice(0, 3).map((n) => Number(n).toString(16).padStart(2, '0')).join('');
  };

  const chosen = (name) => form.querySelector(`input[name="${name}"]:checked`);
  const colour = (flag, name) => {
    const on = form.querySelector(`[data-look-flag="${flag}"]`).checked;
    const value = form.querySelector(`input[name="${name}"]`).value.trim();
    return on && valid(value) ? value : 'transparent';
  };

  function update() {
    for (const [name, variable] of [['mat', '--mat'], ['sleeve', '--sleeve']]) {
      const input = chosen(name);
      if (input && input.dataset.css) preview.style.setProperty(variable, input.dataset.css);
    }
    preview.style.setProperty('--sleeve-inner', colour('inner', 'inner_colour'));
    preview.style.setProperty('--mat-seam', colour('seam', 'seam_colour'));
  }

  for (const box of form.querySelectorAll('[data-look-colour]')) {
    const text = box.querySelector('input[type="text"]');
    const picker = box.querySelector('[data-look-picker]');
    const suggestions = box.querySelector('[data-look-suggestions]');
    picker.hidden = false;
    suggestions.hidden = false;
    if (valid(text.value.trim())) picker.value = hex6(text.value.trim());
    picker.addEventListener('input', () => { text.value = picker.value; update(); });
    text.addEventListener('input', () => {
      if (valid(text.value.trim())) picker.value = hex6(text.value.trim());
      update();
    });
    suggestions.addEventListener('click', (event) => {
      const button = event.target.closest('[data-colour]');
      if (!button) return;
      text.value = button.dataset.colour;
      picker.value = button.dataset.colour;
      const flag = form.querySelector(`[data-look-flag="${text.name === 'seam_colour' ? 'seam' : 'inner'}"]`);
      flag.checked = true;
      update();
    });
  }

  for (const file of form.querySelectorAll('[data-look-file]')) {
    // A file chosen means "my own image" for that one.
    file.addEventListener('change', () => {
      const own = form.querySelector(`input[name="${file.dataset.lookFile}"][value="own"]`);
      if (file.files.length && own) own.checked = true;
    });
  }

  form.addEventListener('change', update);
})();
