(() => {
  const refreshCandidates = async () => {
    const response = await fetch('/settings/profile/api', { cache: 'no-store' });
    if (!response.ok) return;
    const data = await response.json();
    const root = document.getElementById('workout-candidates');
    const empty = document.getElementById('workout-candidates-empty');
    if (!root || !empty) return;
    root.textContent = '';
    const selected = data.enabled_workout_template_ids ?? data.workout_candidates.map(item => item.id);
    data.workout_candidates.forEach(item => {
      const label = document.createElement('label');
      label.className = 'tile';
      const input = document.createElement('input');
      input.type = 'checkbox'; input.value = item.id; input.checked = selected.includes(item.id);
      const text = document.createElement('span');
      text.textContent = item.title;
      const detail = document.createElement('small'); detail.textContent = item.description;
      text.append(document.createElement('br'), detail); label.append(input, text); root.append(label);
    });
    empty.hidden = data.workout_candidates.length > 0;
  };

  const install = () => {
    const save = document.getElementById('save');
    const original = save?.onclick;
    if (!save || !original) return;
    save.onclick = async () => {
      const originalFetch = window.fetch;
      window.fetch = (url, options) => {
        if (url === '/settings/profile/api' && options?.method === 'PUT') {
          const payload = JSON.parse(options.body);
          const choices = [...document.querySelectorAll('#workout-candidates input:checked')].map(input => input.value);
          payload.enabled_workout_template_ids = document.querySelector('#workout-candidates input') ? choices : null;
          options = { ...options, body: JSON.stringify(payload) };
        }
        return originalFetch(url, options);
      };
      try { await original(); } finally { window.fetch = originalFetch; }
      await refreshCandidates();
    };
    refreshCandidates();
  };
  setTimeout(install, 0);
})();
