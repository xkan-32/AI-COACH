(() => {
  const sportLabels = { running: 'ランニング', cycling: 'インドアバイク', bodyweight: '自重トレーニング' };
  const pace = seconds => Number.isFinite(seconds) ? `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}/km` : '';
  const detailLines = item => {
    const structure = item.structure || {}, lines = [];
    if (structure.maximum_distance_km) lines.push(`最大距離: ${structure.maximum_distance_km}km`);
    if (structure.fastest_pace_seconds_per_km) lines.push(`最速ペース上限: ${pace(structure.fastest_pace_seconds_per_km)}`);
    if (structure.maximum_duration_minutes) lines.push(`最大時間: ${structure.maximum_duration_minutes}分`);
    (structure.steps || []).forEach(step => {
      const values = [step.distance_km && `距離 ${step.distance_km}`, step.duration_minutes && `時間 ${step.duration_minutes}`, step.duration_seconds && `休憩 ${step.duration_seconds}秒`, step.pace, step.heart_rate, step.detail].filter(Boolean);
      lines.push(`${step.name}: ${values.join(' ／ ')}`);
    });
    if (structure.freeform_example) lines.push(`構成メモ: ${structure.freeform_example}`);
    if (structure.adjustment_guidance) lines.push(`AIの調整: ${structure.adjustment_guidance}`);
    return lines;
  };
  const candidateCard = (item, selected) => {
    const card = document.createElement('div'); card.className = 'workout-candidate';
    const label = document.createElement('label'); label.className = 'tile';
    const input = document.createElement('input'); input.type = 'checkbox'; input.value = item.id; input.checked = selected.includes(item.id);
    const text = document.createElement('span'), title = document.createElement('strong'), detail = document.createElement('small');
    title.textContent = item.title; detail.textContent = item.description; text.append(title, detail); label.append(input, text); card.append(label);
    const lines = detailLines(item);
    if (lines.length) {
      const disclosure = document.createElement('details'); disclosure.className = 'candidate-example';
      const summary = document.createElement('summary'); summary.textContent = '練習メニュー例・AIの調整範囲';
      const list = document.createElement('ul'); lines.forEach(line => { const row = document.createElement('li'); row.textContent = line; list.append(row); });
      disclosure.append(summary, list); card.append(disclosure);
    }
    return card;
  };
  const refreshCandidates = async () => {
    const response = await fetch('/settings/profile/api', { cache: 'no-store' }); if (!response.ok) return;
    const data = await response.json(), root = document.getElementById('workout-candidates'), empty = document.getElementById('workout-candidates-empty');
    if (!root || !empty) return; root.textContent = '';
    const selected = data.enabled_workout_template_ids ?? data.workout_candidates.map(item => item.id);
    Object.entries(sportLabels).forEach(([sport, label]) => {
      const items = data.workout_candidates.filter(item => item.sport === sport); if (!items.length) return;
      const heading = document.createElement('h3'); heading.className = 'candidate-group-title'; heading.textContent = label;
      const group = document.createElement('div'); group.className = 'workout-candidate-list'; items.forEach(item => group.append(candidateCard(item, selected))); root.append(heading, group);
    });
    empty.hidden = data.workout_candidates.length > 0;
  };
  const install = () => {
    const save = document.getElementById('save'), original = save?.onclick; if (!save || !original) return;
    save.onclick = async () => {
      const originalFetch = window.fetch;
      window.fetch = (url, options) => {
        if (url === '/settings/profile/api' && options?.method === 'PUT') {
          const payload = JSON.parse(options.body), choices = [...document.querySelectorAll('#workout-candidates input:checked')].map(input => input.value);
          payload.enabled_workout_template_ids = document.querySelector('#workout-candidates input') ? choices : null; options = { ...options, body: JSON.stringify(payload) };
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
