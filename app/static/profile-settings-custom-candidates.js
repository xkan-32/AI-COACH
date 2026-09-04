(() => {
  let customCandidates = [];
  const field = (label, type, value, hint = '') => {
    const wrapper = document.createElement('label'); wrapper.className = 'field'; wrapper.append(label);
    const input = document.createElement(type === 'textarea' ? 'textarea' : 'input'); if (type !== 'textarea') input.type = type;
    input.value = value ?? ''; wrapper.append(input);
    if (hint) { const text = document.createElement('small'); text.className = 'field-hint'; text.textContent = hint; wrapper.append(text); }
    return wrapper;
  };
  const render = () => {
    const root = document.getElementById('custom-running-candidates'); if (!root) return; root.textContent = '';
    customCandidates.forEach((item, index) => {
      const card = document.createElement('div'); card.className = 'goal'; card.dataset.id = item.id || '';
      const title = field('候補名', 'text', item.title); title.querySelector('input').maxLength = 80;
      const description = field('概要', 'text', item.description); description.querySelector('input').maxLength = 300;
      const minutes = field('最低時間（分）', 'number', item.minimum_minutes); minutes.querySelector('input').min = 10; minutes.querySelector('input').max = 240;
      const distance = field('最大距離（km・任意）', 'number', item.maximum_distance_km, 'AIはこの距離を超えない範囲で組み立てます。'); distance.querySelector('input').min = 0.1; distance.querySelector('input').max = 100; distance.querySelector('input').step = 0.1;
      const fastest = field('最速ペース上限（秒/km・任意）', 'number', item.fastest_pace_seconds_per_km, '例: 330 は 5:30/km。AIはこれより速く設定しません。'); fastest.querySelector('input').min = 150; fastest.querySelector('input').max = 900;
      const structure = field('構成メモ（任意）', 'textarea', item.example_structure, '例: 2kmウォームアップ、1km速め＋1kmゆっくりを3回、1kmクールダウン。フリーランなら「体調に合わせる」と記入できます。'); structure.querySelector('textarea').maxLength = 600;
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'remove'; remove.textContent = '削除';
      remove.onclick = () => { const id = item.id; customCandidates.splice(index, 1); document.querySelector(`#workout-candidates input[value="${id}"]`)?.closest('.workout-candidate')?.remove(); render(); };
      card.append(title, description, minutes, distance, fastest, structure, remove); root.append(card);
    });
  };
  const numberOrNull = value => value === '' ? null : Number(value);
  const refresh = async () => { const response = await fetch('/settings/profile/api', { cache: 'no-store' }); if (response.ok) { customCandidates = (await response.json()).custom_running_candidates || []; render(); } };
  setTimeout(() => {
    const save = document.getElementById('save'), original = save?.onclick; if (!save || !original) return;
    save.onclick = async () => {
      const originalFetch = window.fetch;
      window.fetch = (url, options) => {
        if (url === '/settings/profile/api' && options?.method === 'PUT') {
          customCandidates = customCandidates.map((item, index) => { const card = document.querySelectorAll('#custom-running-candidates .goal')[index], values = card.querySelectorAll('input,textarea'); return { id: item.id || null, title: values[0].value.trim(), description: values[1].value.trim(), minimum_minutes: Number(values[2].value), maximum_distance_km: numberOrNull(values[3].value), fastest_pace_seconds_per_km: numberOrNull(values[4].value), example_structure: values[5].value.trim() }; });
          const payload = JSON.parse(options.body); payload.custom_running_candidates = customCandidates; options = { ...options, body: JSON.stringify(payload) };
        }
        return originalFetch(url, options);
      };
      try { await original(); } finally { window.fetch = originalFetch; }
      await refresh();
    };
    document.getElementById('add-running-candidate').onclick = () => { customCandidates.push({ title: '', description: '', minimum_minutes: 30, maximum_distance_km: null, fastest_pace_seconds_per_km: null, example_structure: '' }); render(); };
    document.getElementById('reset-workout-candidates').onclick = () => { customCandidates.forEach(item => document.querySelector(`#workout-candidates input[value="${item.id}"]`)?.closest('.workout-candidate')?.remove()); customCandidates = []; document.querySelectorAll('#workout-candidates input').forEach(input => { input.checked = true; }); render(); };
    refresh();
  }, 1);
})();
