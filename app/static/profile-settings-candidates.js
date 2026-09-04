(() => {
  const standardIds = new Set(['run-easy-v1','run-recovery-v1','run-free-v1','run-pace-v1','run-wave-v1','run-interval-400-v1','run-lsd-v1','bike-endurance-v1','bike-recovery-v1','bike-tempo-v1','bike-cadence-v1','bodyweight-full-v1','bodyweight-core-v1']);
  const sportLabels = { running: 'ランニング', cycling: 'インドアバイク', bodyweight: '自重トレーニング' };
  let candidates = [], environments = [], resetRequested = false;
  const field = (label, key, value, type = 'text', hint = '') => {
    const wrap = document.createElement('label'); wrap.className = 'field'; wrap.append(label);
    const input = document.createElement(type === 'textarea' ? 'textarea' : type === 'select' ? 'select' : 'input');
    if (type === 'select') Object.entries(sportLabels).forEach(([id, text]) => { const option = document.createElement('option'); option.value = id; option.textContent = text; input.append(option); }); else if (type !== 'textarea') input.type = type;
    input.dataset.key = key; input.value = value ?? ''; wrap.append(input);
    if (hint) { const small = document.createElement('small'); small.className = 'field-hint'; small.textContent = hint; wrap.append(small); }
    return wrap;
  };
  const pacePicker = seconds => {
    const wrap = document.createElement('div'); wrap.className = 'pace-picker';
    const title = document.createElement('p'); title.className = 'field'; title.textContent = '最速ペース上限（任意）'; wrap.append(title);
    const minutes = document.createElement('select'); minutes.dataset.key = 'fastest_pace_minutes';
    const empty = document.createElement('option'); empty.value = ''; empty.textContent = '指定なし'; minutes.append(empty);
    for (let value = 2; value <= 15; value += 1) { const option = document.createElement('option'); option.value = String(value); option.textContent = `${value}分`; minutes.append(option); }
    const remainder = Number.isFinite(seconds) ? seconds % 60 : 0; const minuteValue = Number.isFinite(seconds) ? Math.floor(seconds / 60) : '';
    minutes.value = String(minuteValue);
    const secs = document.createElement('select'); secs.dataset.key = 'fastest_pace_seconds';
    for (let value = 0; value < 60; value += 1) { const option = document.createElement('option'); option.value = String(value); option.textContent = `${String(value).padStart(2, '0')}秒`; secs.append(option); }
    secs.value = String(remainder); secs.disabled = !minuteValue; minutes.onchange = () => { secs.disabled = !minutes.value; };
    const hint = document.createElement('small'); hint.className = 'field-hint'; hint.textContent = '例: 3分04秒/km。AIはこれより速く設定しません。';
    wrap.append(minutes, secs, hint); return wrap;
  };
  const card = item => {
    const root = document.createElement('div'); root.className = 'workout-candidate'; root.dataset.id = item.id || '';
    const label = document.createElement('label'); label.className = 'tile';
    const checked = document.createElement('input'); checked.type = 'checkbox'; checked.dataset.key = 'enabled'; checked.checked = item.enabled !== false;
    const heading = document.createElement('span'); const title = document.createElement('strong'); title.textContent = item.title || '新しい候補'; const detail = document.createElement('small'); detail.textContent = item.description || '候補の内容を入力してください。'; heading.append(title, detail); label.append(checked, heading);
    const details = document.createElement('details'); details.className = 'candidate-example';
    const summary = document.createElement('summary'); summary.textContent = '候補を編集'; details.append(summary);
    if (!standardIds.has(item.id)) details.append(field('種目', 'sport', item.sport, 'select'));
    details.append(field('候補名', 'title', item.title), field('概要', 'description', item.description), field('最低時間（分）', 'minimum_minutes', item.minimum_minutes, 'number'));
    const structure = item.structure || {};
    if ((item.sport || structure.sport) === 'running') details.append(field('最大距離（km・任意）', 'maximum_distance_km', structure.maximum_distance_km, 'number', 'AIはこの距離を超えません。'), pacePicker(structure.fastest_pace_seconds_per_km));
    else details.append(field('最大時間（分・任意）', 'maximum_duration_minutes', structure.maximum_duration_minutes, 'number', 'AIはこの時間を超えません。'));
    details.append(field('構成メモ（任意）', 'example_structure', structure.freeform_example || '', 'textarea', '例はAIが調整するための土台です。フリーランは「体調に合わせる」と記入できます。'));
    const locationTitle = document.createElement('p'); locationTitle.className = 'field'; locationTitle.textContent = '利用できる場所・器具'; details.append(locationTitle);
    const locations = document.createElement('div'); locations.className = 'tiles'; (environments || []).forEach(env => { const option = document.createElement('label'); option.className = 'tile'; const input = document.createElement('input'); input.type = 'checkbox'; input.dataset.environment = env.display_name; const keys = item.required_environment_keywords || []; input.checked = keys.some(key => env.display_name.includes(key) || key.includes(env.display_name)); const text = document.createElement('span'); text.textContent = env.display_name; option.append(input, text); locations.append(option); }); details.append(locations);
    if (!standardIds.has(item.id)) { const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'remove'; remove.textContent = 'この候補を削除'; remove.onclick = () => { candidates = candidates.filter(candidate => candidate !== item); render(); }; details.append(remove); }
    root.append(label, details); return root;
  };
  const render = () => {
    const root = document.getElementById('workout-candidates'); if (!root) return; root.textContent = '';
    Object.entries(sportLabels).forEach(([sport, label]) => { const items = candidates.filter(item => item.sport === sport); if (!items.length) return; const title = document.createElement('h3'); title.className = 'candidate-group-title'; title.textContent = label; const list = document.createElement('div'); list.className = 'workout-candidate-list'; items.forEach(item => list.append(card(item))); root.append(title, list); });
  };
  const numberOrNull = value => value === '' ? null : Number(value);
  const collect = () => [...document.querySelectorAll('.workout-candidate')].map(card => { const value = key => card.querySelector(`[data-key="${key}"]`)?.value ?? ''; const sport = value('sport') || candidates.find(item => item.id === card.dataset.id)?.sport || 'running'; const paceMinutes = value('fastest_pace_minutes'); const paceSeconds = value('fastest_pace_seconds'); return { id: card.dataset.id || null, sport, title: value('title').trim(), description: value('description').trim(), minimum_minutes: Number(value('minimum_minutes')), maximum_distance_km: numberOrNull(value('maximum_distance_km')), fastest_pace_seconds_per_km: paceMinutes === '' ? null : Number(paceMinutes) * 60 + Number(paceSeconds), maximum_duration_minutes: numberOrNull(value('maximum_duration_minutes')), example_structure: value('example_structure').trim(), required_environment_keywords: [...card.querySelectorAll('[data-environment]:checked')].map(input => input.dataset.environment) }; });
  const install = () => {
    const save = document.getElementById('save'), original = save?.onclick; if (!save || !original) return;
    save.onclick = async () => { const originalFetch = window.fetch; window.fetch = (url, options) => { if (url === '/settings/profile/api' && options?.method === 'PUT') { const payload = JSON.parse(options.body); const values = resetRequested ? [] : collect(); if (values.some(item => !item.title || !item.description || !Number.isFinite(item.minimum_minutes))) throw Error('候補名・概要・最低時間を入力してください。'); payload.workout_candidates = values; payload.reset_workout_candidates = resetRequested; payload.enabled_workout_template_ids = resetRequested ? null : [...document.querySelectorAll('#workout-candidates [data-key="enabled"]:checked')].map(input => input.closest('.workout-candidate').dataset.id).filter(Boolean); options = { ...options, body: JSON.stringify(payload) }; } return originalFetch(url, options); }; try { await original(); } finally { window.fetch = originalFetch; } const response = await fetch('/settings/profile/api', { cache: 'no-store' }); if (response.ok) { const data = await response.json(); environments = data.training_environments; const selected = data.enabled_workout_template_ids; candidates = data.workout_candidates.map(item => ({ ...item, enabled: selected ? selected.includes(item.id) : true })); resetRequested = false; render(); document.getElementById('workout-candidates-empty').hidden = candidates.length > 0; } };
    document.getElementById('add-workout-candidate').onclick = () => { candidates.push({ id: null, sport: 'running', title: '', description: '', minimum_minutes: 30, required_environment_keywords: [], structure: {} }); resetRequested = false; render(); };
    document.getElementById('reset-workout-candidates').onclick = () => { resetRequested = true; candidates = []; render(); document.getElementById('workout-candidates-empty').hidden = false; };
    fetch('/settings/profile/api', { cache: 'no-store' }).then(response => response.ok ? response.json() : null).then(data => { if (!data) return; environments = data.training_environments; const selected = data.enabled_workout_template_ids; candidates = data.workout_candidates.map(item => ({ ...item, enabled: selected ? selected.includes(item.id) : true })); render(); document.getElementById('workout-candidates-empty').hidden = candidates.length > 0; });
  };
  setTimeout(install, 0);
})();
