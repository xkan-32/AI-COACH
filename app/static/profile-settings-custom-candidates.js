(() => {
  let customCandidates = [];
  const field = (label, type, value) => {
    const wrapper = document.createElement('label'); wrapper.className = 'field'; wrapper.append(label);
    const input = document.createElement(type === 'select' ? 'select' : 'input');
    if (type !== 'select') input.type = type;
    if (type === 'select') [['easy', '軽め'], ['moderate', 'やや高め']].forEach(([id, text]) => { const option = document.createElement('option'); option.value = id; option.textContent = text; input.append(option); });
    input.value = value; wrapper.append(input); return wrapper;
  };
  const render = () => {
    const root = document.getElementById('custom-running-candidates'); if (!root) return; root.textContent = '';
    customCandidates.forEach((item, index) => {
      const card = document.createElement('div'); card.className = 'goal'; card.dataset.id = item.id || '';
      const title = field('候補名', 'text', item.title); title.querySelector('input').maxLength = 80;
      const description = field('内容', 'text', item.description); description.querySelector('input').maxLength = 300;
      const intensity = field('強度', 'select', item.intensity);
      const minutes = field('最低時間（分）', 'number', item.minimum_minutes); minutes.querySelector('input').min = 10; minutes.querySelector('input').max = 240;
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'remove'; remove.textContent = '削除';
      remove.onclick = () => { const id = item.id; customCandidates.splice(index, 1); document.querySelector(`#workout-candidates input[value="${id}"]`)?.closest('label').remove(); render(); };
      card.append(title, description, intensity, minutes, remove); root.append(card);
    });
  };
  const refresh = async () => { const response = await fetch('/settings/profile/api', { cache: 'no-store' }); if (response.ok) { customCandidates = (await response.json()).custom_running_candidates || []; render(); } };
  setTimeout(() => {
    const save = document.getElementById('save'); const original = save?.onclick; if (!save || !original) return;
    save.onclick = async () => {
      const originalFetch = window.fetch;
      window.fetch = (url, options) => {
        if (url === '/settings/profile/api' && options?.method === 'PUT') {
          customCandidates = customCandidates.map((item, index) => { const card = document.querySelectorAll('#custom-running-candidates .goal')[index]; const values = card.querySelectorAll('input,select'); return { id: item.id || null, title: values[0].value.trim(), description: values[1].value.trim(), intensity: values[2].value, minimum_minutes: Number(values[3].value) }; });
          const payload = JSON.parse(options.body); payload.custom_running_candidates = customCandidates; options = { ...options, body: JSON.stringify(payload) };
        }
        return originalFetch(url, options);
      };
      try { await original(); } finally { window.fetch = originalFetch; }
      await refresh();
    };
    document.getElementById('add-running-candidate').onclick = () => { customCandidates.push({ title: '', description: '', intensity: 'easy', minimum_minutes: 30 }); render(); };
    document.getElementById('reset-workout-candidates').onclick = () => { customCandidates.forEach(item => document.querySelector(`#workout-candidates input[value="${item.id}"]`)?.closest('label').remove()); customCandidates = []; document.querySelectorAll('#workout-candidates input').forEach(input => { input.checked = true; }); render(); };
    refresh();
  }, 1);
})();
