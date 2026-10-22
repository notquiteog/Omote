import { $, api, el } from './dom.js';

export function wireCommerce({ getRoot, onCompose }) {
  const dialog = el('dialog'); dialog.id = 'shop-dialog'; dialog.setAttribute('aria-labelledby', 'shop-title');
  // Static structure only. All saved text and remote content uses textContent.
  dialog.innerHTML = `<header class="shop-head"><div><p class="task-eyebrow">FROM IDEA TO SALE</p><h2 id="shop-title">Shop</h2></div><button type="button" id="shop-close" class="ghost">Close</button></header>
    <p class="meta">Compare suppliers, prepare listings and keep up with buyers.</p><p id="shop-project" class="meta"></p>
    <div class="shop-actions"><label>Use a website<select id="shop-site"><option value="https://www.facebook.com/marketplace/you/selling">Facebook Marketplace</option><option value="https://www.ebay.com/sh/ovw">eBay</option><option value="https://www.etsy.com/your/shops/me/dashboard">Etsy</option><option value="https://www.depop.com/">Depop</option><option value="https://www.alibaba.com/">Alibaba</option><option value="https://www.made-in-china.com/">Made-in-China</option><option value="https://www.dhgate.com/">DHgate</option></select></label><button id="shop-open-site" type="button">Open with Omote</button></div><p class="meta">Adds a request to your chat. No API needed for browser assistance. Sign in yourself once in Omote’s browser; it keeps that profile for future work. Each chat gets its own tab. Sites may ask you to sign in again.</p>
    <div class="shop-tabs" role="group" aria-label="Shop section"><button data-shop-tab="listing" aria-pressed="true">Listings</button><button data-shop-tab="supplier" aria-pressed="false">Suppliers</button><button data-shop-tab="replies" aria-pressed="false">Replies</button><button id="shop-refresh" class="ghost">Refresh</button></div>
    <p id="shop-status" role="status"></p><section id="shop-records"><button id="shop-new" class="primary">New listing</button><div id="shop-list"></div><form id="shop-form" hidden><h3 id="shop-form-title"></h3><div id="shop-fields"></div><details><summary>Costs, details and progress</summary><div id="shop-details" class="shop-grid"></div></details><div class="shop-actions"><button class="primary" type="submit">Save draft</button><button type="button" id="shop-cancel">Cancel</button></div></form><pre id="shop-preview" hidden></pre></section>
    <section id="shop-replies" hidden><h3>Reply while you’re away</h3><p class="meta">eBay can send your saved answer when a new message matches the full question. Other sites use Omote’s assisted browser workflow.</p><p id="shop-connection"></p>
      <details id="shop-connect-details"><summary>Connect eBay API (advanced)</summary><p class="meta">Use an eBay user access token with commerce.message and commerce.identity.readonly permissions. The token stays on this computer. When it expires, replies pause and you’ll need to reconnect.</p><a href="https://developer.ebay.com/api-docs/static/oauth-tokens.html" target="_blank" rel="noopener noreferrer">Get an eBay access token</a><form id="shop-connect"><label>eBay access token<input id="shop-token" type="password" autocomplete="off" required maxlength="16000"></label><button type="submit">Connect and check account</button></form></details>
      <button id="shop-disconnect" type="button" class="ghost" hidden>Disconnect and pause replies</button>
      <form id="shop-rule-form" hidden><div class="shop-grid"><label>eBay item ID<input name="item_id" required pattern="[0-9]{9,20}" placeholder="The number in the listing URL"></label><label>Maximum replies per 24 hours<input name="daily_limit" type="number" min="1" max="50" value="10" required></label></div><label>When the complete question is…<input name="question" required minlength="3" maxlength="500" placeholder="Is this still available?"></label><label>Send this exact reply<textarea name="reply" required maxlength="2000" placeholder="Write the answer you want buyers to receive."></textarea></label><label>Stop after (days)<input name="days" type="number" min="1" max="30" value="7" required></label><label class="shop-consent"><input type="checkbox" name="consent" required> I authorize this exact reply for this item and time period.</label><button type="submit" class="primary">Enable this reply</button></form>
      <p class="meta">Checks once a minute while Omote is running. At most one reply per conversation per rule. Case, spacing and final punctuation may differ; added requests do not match. Messages already present when you enable a rule are skipped. A send already in progress may finish when paused.</p><div id="shop-rules"></div><h3>Recent sends</h3><div id="shop-activity"></div></section>`;
  document.body.append(dialog);
  let root = '', data = null, tab = 'listing', editing = null, opened = 0;
  const status = text => { $('#shop-status').textContent = text; };
  async function request(path = '', body, method = 'GET') {
    const response = await api(`/api/commerce${path}${path.includes('?') ? '&' : '?'}root=${encodeURIComponent(root)}`, {
      method, ...(body === undefined ? {} : {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}),
    });
    if (!response) throw new Error('Omote is unavailable. Reopen Shop when it reconnects.');
    const value = await response.json();
    if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : 'Check the fields and try again.');
    return value;
  }
  const run = fn => async event => {
    event?.preventDefault();
    const button = event?.submitter || event?.currentTarget;
    if (button?.tagName === 'BUTTON' && button.id !== 'open-shop') button.disabled = true;
    try { await fn(); } catch (error) { status(error.message); }
    finally { if (button?.tagName === 'BUTTON') button.disabled = false; }
  };
  function action(label, fn) {
    const button = el('button', 'ghost small', label); button.type = 'button'; button.onclick = run(fn); return button;
  }
  function render() {
    $('#shop-project').textContent = `Project: ${root}`;
    $('#shop-records').hidden = tab === 'replies'; $('#shop-replies').hidden = tab !== 'replies';
    for (const button of dialog.querySelectorAll('[data-shop-tab]')) button.setAttribute('aria-pressed', String(button.dataset.shopTab === tab));
    $('#shop-new').textContent = tab === 'supplier' ? 'New supplier' : 'New listing';
    const items = (data?.records || []).filter(record => record.kind === tab);
    $('#shop-list').replaceChildren(...items.map(record => {
      const card = el('article', 'shop-card');
      card.append(el('strong', '', record.fields.title || record.fields.name), el('p', 'meta', `${record.fields.platform} · ${record.fields.status}`));
      const buttons = el('div', 'shop-actions');
      buttons.append(action('Edit', () => edit(record)), action('Review', async () => {
        const value = await request(`/records/${record.id}/prepare`);
        $('#shop-preview').textContent = record.kind === 'supplier' ? value.draft_message :
          `${value.listing_text}\n\nEstimated profit: ${value.currency} ${value.estimated_profit}\n${value.estimate_note}\n\n${value.ready ? 'Basic checks complete. Review the current site requirements before publishing.' : value.issues.join('\n')}`;
        $('#shop-preview').hidden = false;
      }), action('Continue with Omote', () => {
        onCompose(`Use the commerce tool to get and prepare ${record.kind} ${record.id} in project ${root}. ${record.kind === 'supplier' ? 'Help me research this supplier and negotiate written terms within the limits I give you.' : 'Help me list or update this item. Check current stock, eligibility and any existing listing first.'} Use the signed-in browser and show me the final message, listing or deal before committing. Save observed results back to this record.`, root);
        dialog.close();
      }), action('Delete', async () => {
        // Deliberate second click on this card; does not delete a remote listing.
        if (card.dataset.deleting !== 'yes') { card.dataset.deleting = 'yes'; buttons.lastChild.textContent = 'Confirm delete draft'; return; }
        await request(`/records/${record.id}?revision=${record.revision}`, undefined, 'DELETE'); await refresh();
      }));
      card.append(buttons); return card;
    }));
    if (!items.length) $('#shop-list').append(el('p', 'meta', tab === 'supplier' ? 'Keep quotes, delivery times and supplier agreements together. Start with a supplier link.' : 'Start with one item. Save its details once, then ask Omote to adapt it for each marketplace.'));
    $('#shop-connection').textContent = data?.ebay.connected ? `Connected as ${data.ebay.username}` : 'Connect eBay to enable automatic replies. For any site, ask Omote to help with its inbox in chat.';
    $('#shop-rule-form').hidden = !data?.ebay.connected;
    $('#shop-disconnect').hidden = !data?.ebay.connected;
    $('#shop-rules').replaceChildren(...(data?.rules || []).map(rule => {
      const active = rule.enabled && rule.expires * 1000 > Date.now();
      const card = el('article', 'shop-card');
      card.append(el('strong', '', `${active ? 'On' : 'Paused or expired'} · Item ${rule.item_id}`), el('p', '', `“${rule.question}” → “${rule.reply}”`),
        el('p', 'meta', `${rule.daily_limit} per 24 hours · until ${new Date(rule.expires * 1000).toLocaleString()}`));
      if (rule.error) card.append(el('p', '', rule.error));
      if (active) card.append(action('Pause', async () => { await request(`/rules/${rule.id}/pause`, {}, 'POST'); await refresh(); }));
      $('#shop-rule-form').dataset.account = data.ebay.username || '';
      return card;
    }));
    $('#shop-activity').replaceChildren(...(data?.activity || []).map(item => {
      const row = el('article', 'shop-card');
      row.append(el('strong', '', item.state === 'sent' ? 'Sent' : 'Outcome uncertain — check eBay before retrying'), el('p', '', item.data.reply || ''), el('p', 'meta', new Date(item.created * 1000).toLocaleString())); return row;
    }));
    if (!data?.activity.length) $('#shop-activity').append(el('p', 'meta', 'No messages sent from this project.'));
  }
  async function refresh() {
    const generation = opened;
    const result = await request();
    if (generation !== opened || !dialog.open) return;
    data = result; root = result.root; render();
    status(data.error || (data.last_check ? `Inbox last checked ${new Date(data.last_check * 1000).toLocaleTimeString()}` : 'Saved on this computer.'));
  }
  const platformOptions = {ebay: 'eBay', etsy: 'Etsy', depop: 'Depop', facebook: 'Facebook Marketplace', alibaba: 'Alibaba', madeinchina: 'Made-in-China', dhgate: 'DHgate', other: 'Other'};
  function field(name, label, type, options, value, target) {
    const wrapper = el('label', '', label);
    const node = el(type === 'textarea' ? 'textarea' : options ? 'select' : 'input');
    node.name = name; node.id = `shop-field-${name}`;
    if (options) for (const [key, text] of Object.entries(options)) { const option = el('option', '', text); option.value = key; node.append(option); }
    else if (type !== 'textarea') node.type = type || 'text';
    node.value = Array.isArray(value) ? value.join('\n') : value ?? '';
    if (['name', 'title', 'price', 'quantity', 'minimum_order'].includes(name)) node.required = true;
    if (type === 'number') { node.min = ['quantity', 'price', 'unit_cost', 'shipping_cost', 'other_cost', 'fee_percent'].includes(name) ? '0' : '1'; node.step = ['quantity', 'minimum_order'].includes(name) ? '1' : '.01'; }
    wrapper.append(node); $(target).append(wrapper);
  }
  function edit(record = null) {
    editing = record; const supplier = tab === 'supplier';
    const value = record?.fields || {platform: supplier ? 'alibaba' : 'ebay', currency: 'USD', price: '0', quantity: 1, minimum_order: 1, fulfillment: 'unknown', product_kind: 'general', status: supplier ? 'researching' : 'draft'};
    $('#shop-form').hidden = false; $('#shop-fields').replaceChildren(); $('#shop-details').replaceChildren(); $('#shop-preview').hidden = true;
    $('#shop-form-title').textContent = `${record ? 'Edit' : 'New'} ${supplier ? 'supplier' : 'listing'}`;
    const add = (name, label, type = 'text', options = null, detail = false) => field(name, label, type, options, value[name], detail ? '#shop-details' : '#shop-fields');
    add(supplier ? 'name' : 'title', supplier ? 'Supplier name' : 'Item title');
    add('platform', supplier ? 'Supplier site' : 'Where to sell', 'select', Object.fromEntries((supplier ? ['alibaba', 'madeinchina', 'dhgate', 'other'] : ['ebay', 'etsy', 'depop', 'facebook', 'other']).map(key => [key, platformOptions[key]])));
    if (supplier) { add('url', 'Supplier link', 'url'); add('product', 'Product'); add('terms', 'Quote and terms', 'textarea'); }
    else {
      add('description', 'Description', 'textarea'); add('price', 'Selling price', 'number');
      add('fulfillment', 'How will you fulfill orders?', 'select', {unknown: 'Choose…', owned: 'I own the stock', wholesale: 'Wholesale agreement', original_design: 'My design, made by a partner', retail_after_sale: 'Buy from another retailer after sale'});
      add('product_kind', 'Type of item', 'select', {general: 'General item', craft_supply: 'Craft supply', party_supply: 'Party supply', vintage: 'Vintage', handmade: 'Handmade', original_design: 'My original design'});
      add('shipping', 'Shipping and delivery', 'textarea'); add('returns', 'Return terms', 'textarea'); add('photos', 'Photo paths or URLs (one per line)', 'textarea');
    }
    add('currency', 'Currency (for example USD)', 'text', null, true);
    add('unit_cost', 'Item cost', 'number', null, true); add('shipping_cost', 'Shipping cost', 'number', null, true);
    if (supplier) { add('minimum_order', 'Minimum order', 'number', null, true); add('lead_time', 'Lead time', 'text', null, true); add('evidence', 'Observed quote or agreement evidence', 'textarea', null, true); }
    else {
      add('other_cost', 'Other costs per item', 'number', null, true); add('fee_percent', 'Selling fee (%)', 'number', null, true); add('quantity', 'Available quantity', 'number', null, true);
      add('source_url', 'Product source', 'url', null, true); add('eligibility_notes', 'Eligibility and wholesale agreement notes', 'textarea', null, true);
      add('published_url', 'Live listing URL', 'url', null, true); add('confirmation', 'Observed publication or sale evidence', 'textarea', null, true);
    }
    add('status', 'Progress', 'select', supplier ? {researching: 'Researching', contacted: 'Contacted', quoted: 'Quoted', agreed: 'Agreed'} : {draft: 'Draft', ready: 'Ready to review', published: 'Published', sold: 'Sold', ended: 'Ended'}, true);
    $('#shop-fields input').focus();
  }
  $('#shop-form').onsubmit = run(async () => {
    const fields = {...editing?.fields, ...Object.fromEntries(new FormData($('#shop-form')))};
    for (const key of ['price', 'unit_cost', 'shipping_cost', 'other_cost', 'fee_percent']) if (key in fields && !fields[key]) fields[key] = '0';
    for (const key of ['quantity', 'minimum_order']) if (key in fields) fields[key] = Number(fields[key]);
    if ('photos' in fields) fields.photos = fields.photos.split('\n').map(x => x.trim()).filter(Boolean);
    const result = await request('/records', {kind: tab, fields, id: editing?.id, base_revision: editing?.revision}, 'POST');
    $('#shop-form').hidden = true; editing = null; await refresh();
    status(result.checks?.issues.length ? `Draft saved. ${result.checks.issues.join(' ')}` : 'Saved on this computer.');
  });
  $('#shop-connect').onsubmit = run(async () => {
    const token = $('#shop-token').value; $('#shop-token').value = '';
    await request('/ebay/connect', {token}, 'POST'); $('#shop-connect-details').open = false; await refresh();
  });
  $('#shop-disconnect').onclick = run(async () => { await request('/ebay/connect', undefined, 'DELETE'); await refresh(); });
  $('#shop-rule-form').onsubmit = run(async () => {
    const fields = Object.fromEntries(new FormData($('#shop-rule-form'))); delete fields.consent;
    fields.daily_limit = Number(fields.daily_limit); fields.days = Number(fields.days);
    await request('/rules', fields, 'POST'); $('#shop-rule-form').reset(); await refresh(); status('Reply enabled. Only new matching messages will be answered.');
  });
  $('#shop-new').onclick = () => edit();
  $('#shop-open-site').onclick = run(() => {
    const site = $('#shop-site');
    onCompose(`Open ${site.selectedOptions[0].textContent} at ${site.value} using the browser tools and my saved browser profile. If sign-in is required, use browser_hand_over so I can sign in privately. Verify the signed-in account before working. Help me manage listings, relist items, negotiate with suppliers or answer messages; use my saved commerce records in project ${root}. Read the live page rather than guessing controls, and record actual outcomes.`, root);
    dialog.close();
  });
  $('#shop-cancel').onclick = () => { $('#shop-form').hidden = true; editing = null; };
  $('#shop-refresh').onclick = run(refresh);
  $('#shop-close').onclick = () => dialog.close();
  dialog.addEventListener('close', () => { opened++; $('#shop-token').value = ''; });
  for (const button of dialog.querySelectorAll('[data-shop-tab]')) button.onclick = () => { tab = button.dataset.shopTab; $('#shop-form').hidden = true; $('#shop-preview').hidden = true; editing = null; render(); };
  $('#open-shop').onclick = run(async () => {
    opened++; root = getRoot() || ''; data = null; editing = null;
    $('#shop-form').hidden = true; $('#shop-preview').hidden = true; render(); dialog.showModal(); status('Loading…'); await refresh();
  });
}
