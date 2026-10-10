import { app } from '../../scripts/app.js';

// No wildcard postMessage targets, remote scripts, queue commands, or prototype patches.
// Rendering and serialization remain upstream's, including Nodes 2.0/subgraphs.
app.registerExtension({
  name: 'Omote.WorkbenchBridge',
  settings: [{ id: 'Omote.AllowedOrigins', name: 'Omote: trusted workspace origins', type: 'text', defaultValue: '',
    tooltip: 'Comma-separated exact origins, e.g. http://127.0.0.1:8477. These workspaces can load and read graphs in this editor.' }],
  setup() {
    window.addEventListener('message', async (event) => {
      const message = event.data;
      if (event.source !== window.parent || event.source === window || !message || message.channel !== 'omote.comfy.v1'
          || typeof message.id !== 'string' || message.id.length > 100) return;
      const configured = app.extensionManager?.setting?.get('Omote.AllowedOrigins')
        ?? app.ui?.settings?.getSettingValue('Omote.AllowedOrigins') ?? '';
      const origins = String(configured).split(',').map((value) => value.trim()).filter(Boolean);
      if (!origins.includes(event.origin) || event.origin === 'null') return;
      const reply = (payload) => event.source.postMessage({ channel: 'omote.comfy.v1', id: message.id, ...payload }, event.origin);
      try {
        if (message.action === 'hello') return reply({ ok: true, capabilities: ['capture', 'load'] });
        if (message.action === 'capture') {
          const prompt = await app.graphToPrompt();
          return reply({ ok: true, graph: prompt.workflow, prompt: prompt.output });
        }
        if (message.action === 'load') {
          if (!message.graph || !Array.isArray(message.graph.nodes) || message.graph.nodes.length > 5000
              || new TextEncoder().encode(JSON.stringify(message.graph)).byteLength > 4 * 1024 * 1024) throw new Error('Invalid workflow');
          await app.loadGraphData(message.graph);
          return reply({ ok: true });
        }
        throw new Error('Unsupported bridge action');
      } catch (error) { reply({ ok: false, error: String(error.message || error).slice(0,500) }); }
    });
  },
});
