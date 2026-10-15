/* No background telemetry polling: opening this panel starts it, hiding stops it. */
import { $, api, el } from './dom.js';

const bytes = (value) => {
  if (!Number.isFinite(value)) return 'Unavailable';
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(0)} MiB`;
  return `${(value / 1024 ** 3).toFixed(1)} GiB`;
};
const percent = value => Number.isFinite(value) ? `${value.toFixed(0)}%` : 'Unavailable';

function metric(title, value, detail, usage) {
  const card = el('article', 'system-metric');
  card.append(el('h3', '', title), el('strong', '', value));
  if (Number.isFinite(usage)) {
    const bar = el('progress');
    bar.max = 100;
    bar.value = Math.max(0, Math.min(100, usage));
    bar.setAttribute('aria-label', `${title} usage`);
    card.appendChild(bar);
  }
  card.appendChild(el('p', 'meta', detail));
  return card;
}

export function wireSystem({ onPlan, onOpenChat }) {
  const dialog = $('#system-dialog');
  let timer = null;
  let paused = false;
  let inFlight = false;
  let revision = 0;

  function stop() { clearTimeout(timer); timer = null; }
  function active() { return dialog.open && !document.hidden; }
  function schedule() {
    stop();
    if (active() && !paused) timer = setTimeout(refresh, 2500);
  }
  function render(data) {
    const { cpu, memory, disk, gpu } = data;
    const cards = [
      metric('CPU', percent(cpu?.percent), `${cpu?.logical_cores || 'Unknown'} logical cores`, cpu?.percent),
      metric('RAM', percent(memory?.percent), memory ? `${bytes(memory.available_bytes)} available of ${bytes(memory.total_bytes)}` : 'Usage unavailable', memory?.percent),
      metric('Workspace disk', percent(disk?.percent), disk ? `${bytes(disk.free_bytes)} free of ${bytes(disk.total_bytes)}` : 'Usage unavailable', disk?.percent),
    ];
    for (const device of gpu?.devices || []) {
      const total = device.memory_total_bytes;
      const used = device.memory_used_bytes;
      const vram = Number.isFinite(total) && Number.isFinite(used) ? `${bytes(used)} / ${bytes(total)} VRAM` : 'VRAM unavailable';
      const extra = [Number.isFinite(device.temperature_c) ? `${device.temperature_c} °C` : '',
        Number.isFinite(device.power_watts) ? `${device.power_watts.toFixed(0)} W` : ''].filter(Boolean).join(' · ');
      cards.push(metric(device.name, percent(device.utilization_percent), `${vram}${extra ? ` · ${extra}` : ''}`, device.utilization_percent));
    }
    if (!gpu?.devices?.length) cards.push(metric('GPU', 'Unavailable', gpu?.note || 'GPU telemetry is not supported here.'));
    $('#system-metrics').replaceChildren(...cards);
    $('#system-runtimes').replaceChildren(...data.runtimes.map(runtime => {
      const item = el('li');
      item.append(el('span', '', runtime.name), el('span', 'meta', runtime.status));
      return item;
    }));
    $('#system-admin').textContent = data.admin?.note || 'Check system_info in chat for administrator access options.';
    const jobs = (data.work || []).map(work => {
      const item = el('div', 'system-job');
      const info = el('div');
      info.append(el('strong', '', work.title || 'Untitled chat'), el('p', 'meta', [
        work.waiting_on ? 'Waiting for you' : work.busy ? 'Working' : '',
        work.background ? `${work.background} background task${work.background === 1 ? '' : 's'}` : '',
      ].filter(Boolean).join(' · ')));
      const open = el('button', 'ghost small', 'Open chat');
      open.onclick = async () => {
        open.disabled = true;
        try { await onOpenChat(work.id); dialog.close(); }
        catch (error) { $('#system-status').textContent = error.message; }
        finally { open.disabled = false; }
      };
      item.append(info, open);
      return item;
    });
    if (!jobs.length) jobs.push(el('p', 'meta', 'No active chats or background chat tasks. Studio jobs stay in Studio.'));
    if (data.work_count > jobs.length) jobs.push(el('p', 'meta', `Showing 30 of ${data.work_count} active chats.`));
    $('#system-work').replaceChildren(...jobs);
    $('#system-status').textContent = `${paused ? 'Paused' : 'Updated'} ${new Date(data.sampled_at * 1000).toLocaleTimeString()}`
      + (data.notes.length ? ` · ${data.notes.join(' ')}` : '') + (gpu?.devices?.length && gpu.note ? ` · ${gpu.note}` : '');
  }
  async function refresh() {
    stop();
    if (inFlight || !active()) return;
    const version = revision;
    inFlight = true;
    $('#system-refresh').disabled = true;
    try {
      const response = await api('/api/system/usage');
      if (!response?.ok) throw new Error('System usage is unavailable. Check the Omote connection.');
      const data = await response.json();
      if (active() && version === revision) render(data);
    } catch (error) {
      if (active() && version === revision) {
        $('#system-status').textContent = `${error.message} Readings below may be out of date.`;
        $('#system-metrics').replaceChildren();
      }
    } finally {
      inFlight = false;
      $('#system-refresh').disabled = false;
      schedule();
    }
  }
  $('#open-system').onclick = () => { dialog.showModal(); $('#system-status').textContent = 'Checking…'; refresh(); };
  $('#system-close').onclick = () => dialog.close();
  dialog.addEventListener('close', () => { revision++; stop(); });
  $('#system-refresh').onclick = refresh;
  $('#system-pause').onclick = () => {
    paused = !paused;
    $('#system-pause').textContent = paused ? 'Resume updates' : 'Pause updates';
    $('#system-pause').setAttribute('aria-pressed', String(paused));
    $('#system-status').textContent = paused ? 'Updates paused. Refresh for a new reading.' : 'Checking…';
    if (paused) stop(); else refresh();
  };
  $('#system-plan').onclick = () => { dialog.close(); onPlan(); };
  document.addEventListener('visibilitychange', () => {
    revision++;
    if (document.hidden) stop(); else if (dialog.open && !paused) refresh();
  });
}
