let previousBoot = null, previousHealth = null, initialized = false;
const element = id => document.getElementById(id);
function note(message) {
  if (!initialized) { element('events').replaceChildren(); initialized = true; }
  const row = document.createElement('li');
  row.textContent = `${new Date().toLocaleTimeString()}  ${message}`;
  element('events').prepend(row);
  while (element('events').children.length > 12) element('events').lastElementChild.remove();
}
async function poll() {
  try {
    const response = await fetch('/api/status', {cache:'no-store', signal:AbortSignal.timeout(3000)});
    if (!response.ok) throw Error(`HTTP ${response.status}`);
    const state = await response.json();
    const healthy = state.healthy;
    if (previousBoot && previousBoot !== state.boot_id) note(`New process observed. Boot ${state.boot_id.slice(0,8)}.`);
    if (healthy !== previousHealth) note(healthy ? 'Service responded healthy.' : 'Process responded, but health probes are failing.');
    previousBoot = state.boot_id; previousHealth = healthy;
    element('health').textContent = healthy ? 'Healthy' : 'Health check failing';
    element('detail').textContent = healthy ? 'The current process reports ready and live.' : 'Recovery requires the external supervisor or cloud orchestrator.';
    element('environment').textContent = state.environment === 'azure' ? 'Azure deployment' : 'Local process';
    element('uptime').textContent = `${state.uptime_seconds.toFixed(1)}s`;
    element('boot').textContent = state.boot_id.slice(0,8);
    element('requests').textContent = state.requests;
    document.body.dataset.health = healthy ? 'healthy' : 'failed';
  } catch (_) {
    if (previousHealth !== 'unreachable') note('No service response. Waiting to reconnect.');
    previousHealth = 'unreachable';
    element('health').textContent = 'Service unreachable';
    element('detail').textContent = 'No current health claim. Previously observed values may be stale.';
    document.body.dataset.health = 'failed';
  } finally { setTimeout(poll, 2000); }
}
poll();
