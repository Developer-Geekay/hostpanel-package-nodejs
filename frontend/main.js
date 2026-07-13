/* hostpanel-package-nodejs — frontend/main.js
 * SDK plugin: Node.js applications manager UI.
 * Registered as window.__hpkg_sdk.register('nodejs', NodeJsPlugin).
 */
(function () {
  'use strict';

  const sdk = window.__hpkg_sdk;
  const { html, useEffect, useState, useCallback, useMemo } = sdk;
  const { SdkConfirmModal } = sdk.components;
  const { useToast } = sdk.hooks;

  const DEFAULT_PORT = 31000;
  const STYLE_ID = 'node-plugin-styles';

  function injectStyles() {
    if (document.getElementById(STYLE_ID)) return;
    const s = document.createElement('style');
    s.id = STYLE_ID;
    s.textContent = `
      .node-log-pre { font-family: var(--font-mono); font-size: 11px; color: var(--text-2); background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 12px; white-space: pre-wrap; overflow-y: auto; max-height: 400px; margin: 0; min-height: 250px; }
      .node-spin { display: inline-block; width: 14px; height: 14px; border: 2px solid var(--border); border-top-color: currentColor; border-radius: 50%; animation: spin .65s linear infinite; vertical-align: middle; }
    `;
    document.head.appendChild(s);
  }

  function removeStyles() { document.getElementById(STYLE_ID)?.remove(); }

  function envToRows(env) {
    const entries = Object.entries(env || {});
    return entries.length ? entries.map(([key, value]) => ({ key, value })) : [{ key: '', value: '' }];
  }

  function rowsToEnv(rows) {
    const env = {};
    rows.forEach(row => {
      const key = String(row.key || '').trim();
      if (key) env[key] = String(row.value || '');
    });
    return env;
  }

  // ── Node.js Plugin Component ──────────────────────────────────────────────────

  function NodeJsPlugin() {
    const { ok, err: toastErr } = useToast();
    const [apps, setApps] = useState([]);
    const [domains, setDomains] = useState([]);
    const [ports, setPorts] = useState(null);
    const [runtime, setRuntime] = useState({});
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    // Selection / Form states
    const [selectedAppId, setSelectedAppId] = useState(null);
    const [addingNew, setAddingNew] = useState(false);
    const [searchQuery, setSearchQuery] = useState('');
    const [activeTab, setActiveTab] = useState('control');

    // Logs state
    const [logs, setLogs] = useState([]);
    const [logsLoading, setLogsLoading] = useState(false);

    // Edit/Create form state
    const [formName, setFormName] = useState('');
    const [formDomain, setFormDomain] = useState('');
    const [formAppRoot, setFormAppRoot] = useState('');
    const [formNodeVersion, setFormNodeVersion] = useState('22');
    const [formPort, setFormPort] = useState(DEFAULT_PORT);
    const [formEntrypoint, setFormEntrypoint] = useState('server.js');
    const [formStartCommand, setFormStartCommand] = useState('');
    const [formEnvRows, setFormEnvRows] = useState([{ key: '', value: '' }]);
    const [formBusy, setFormBusy] = useState(false);
    const [formError, setFormError] = useState('');

    // Action states
    const [busyAppId, setBusyAppId] = useState(null);
    const [deleteTarget, setDeleteTarget] = useState(null);

    const activeApp = useMemo(() => apps.find(a => a.id === selectedAppId), [apps, selectedAppId]);

    const load = useCallback((silent = false) => {
      if (!silent) setLoading(true);
      setError('');
      Promise.all([
        sdk.fetch('GET', '/cpanelapi/nodejs/apps'),
        sdk.fetch('GET', '/cpanelapi/nodejs/domains'),
        sdk.fetch('GET', '/cpanelapi/nodejs/ports'),
        sdk.fetch('GET', '/cpanelapi/nodejs/runtime'),
      ])
        .then(([appsData, domainData, portData, runtimeData]) => {
          setApps(appsData || []);
          setDomains(domainData || []);
          setPorts(portData || null);
          setRuntime(runtimeData || {});
        })
        .catch(e => setError(e.message || 'Failed to load Node.js applications'))
        .finally(() => setLoading(false));
    }, []);

    useEffect(() => {
      load();
      injectStyles();
      return removeStyles;
    }, [load]);

    // Logs Fetch
    const fetchLogs = useCallback(async (appId) => {
      setLogsLoading(true);
      try {
        const data = await sdk.fetch('GET', '/cpanelapi/nodejs/apps/' + encodeURIComponent(appId) + '/logs');
        setLogs(data || []);
      } catch (e) {
        toastErr('Failed to load application logs');
      } finally {
        setLogsLoading(false);
      }
    }, [toastErr]);

    // Handle Active Tab Change
    const handleTabChange = (tabId) => {
      setActiveTab(tabId);
      if (tabId === 'logs' && activeApp) {
        fetchLogs(activeApp.id);
      }
    };

    // Filtered Apps List
    const filteredApps = useMemo(() => {
      if (!searchQuery.trim()) return apps;
      const q = searchQuery.toLowerCase();
      return apps.filter(app =>
        app.name.toLowerCase().includes(q) ||
        app.domain.toLowerCase().includes(q)
      );
    }, [apps, searchQuery]);

    // Set Selected App (resets tab and config form)
    const selectApp = (app) => {
      setSelectedAppId(app.id);
      setAddingNew(false);
      setActiveTab('control');
      setFormError('');

      // Populate config form for Edit/Save
      setFormName(app.name);
      setFormDomain(app.domain);
      setFormAppRoot(app.app_root);
      setFormNodeVersion(app.node_version);
      setFormPort(app.port);
      setFormEntrypoint(app.entrypoint);
      setFormStartCommand(app.start_command || '');
      setFormEnvRows(envToRows(app.env));
    };

    // Trigger "+ Add Application" View
    const triggerAddView = () => {
      setAddingNew(true);
      setSelectedAppId(null);
      setFormError('');

      const firstDomain = domains[0]?.domain || '';
      const selectedInitial = domains.find(item => item.domain === firstDomain);

      setFormName('');
      setFormDomain(firstDomain);
      setFormAppRoot(selectedInitial?.document_root || '');
      setFormNodeVersion('22');
      setFormPort(ports?.min || DEFAULT_PORT);
      setFormEntrypoint('server.js');
      setFormStartCommand('');
      setFormEnvRows([{ key: '', value: '' }]);
    };

    // Form Change Helpers
    const selectFormDomain = (val) => {
      setFormDomain(val);
      const selected = domains.find(item => item.domain === val);
      if (selected && addingNew) {
        setFormAppRoot(selected.document_root || '');
      }
    };

    const updateEnvRow = (index, field, value) => {
      setFormEnvRows(rows => rows.map((row, i) => i === index ? { ...row, [field]: value } : row));
    };

    const addEnvRow = () => {
      setFormEnvRows(rows => rows.concat({ key: '', value: '' }));
    };

    const removeEnvRow = (index) => {
      setFormEnvRows(rows => rows.filter((_, i) => i !== index));
    };

    // Action Handlers
    const handlePowerAction = async (app, actionName) => {
      setBusyAppId(app.id);
      try {
        await sdk.fetch('POST', '/cpanelapi/nodejs/apps/' + encodeURIComponent(app.id) + '/' + actionName);
        ok(`Application ${actionName} requested`);
        load(true);
      } catch (e) {
        toastErr(e.message || `${actionName} action failed`);
      } finally {
        setBusyAppId(null);
      }
    };

    // Create App Submission
    const handleCreateSubmit = async (e) => {
      e.preventDefault();
      setFormError('');
      if (!formName.trim()) { setFormError('Application name is required'); return; }
      if (!formDomain) { setFormError('Target domain is required'); return; }

      const usedPorts = new Set((ports?.ports || []).filter(item => !item.available).map(item => item.port));
      if (usedPorts.has(Number(formPort))) {
        setFormError('Selected port is already assigned');
        return;
      }

      setFormBusy(true);
      try {
        const values = {
          name: formName.trim(),
          domain: formDomain,
          app_root: formAppRoot.trim(),
          node_version: formNodeVersion,
          port: Number(formPort),
          entrypoint: formEntrypoint.trim(),
          start_command: formStartCommand.trim(),
          env: rowsToEnv(formEnvRows),
        };
        const newApp = await sdk.fetch('POST', '/cpanelapi/nodejs/apps', values);
        ok('Node.js application provisioned');
        load();
        setAddingNew(false);
        if (newApp?.id) setSelectedAppId(newApp.id);
      } catch (e) {
        setFormError(e.message || 'Provisioning failed');
      } finally {
        setFormBusy(false);
      }
    };

    // Edit App Submission
    const handleEditSubmit = async (e) => {
      e.preventDefault();
      if (!activeApp) return;
      setFormError('');
      if (!formName.trim()) { setFormError('Application name is required'); return; }

      const usedPorts = new Set((ports?.ports || []).filter(item => !item.available && item.app_id !== activeApp.id).map(item => item.port));
      if (usedPorts.has(Number(formPort))) {
        setFormError('Selected port is already assigned');
        return;
      }

      setFormBusy(true);
      try {
        const values = {
          name: formName.trim(),
          domain: formDomain,
          app_root: formAppRoot.trim(),
          node_version: formNodeVersion,
          port: Number(formPort),
          entrypoint: formEntrypoint.trim(),
          start_command: formStartCommand.trim(),
          env: rowsToEnv(formEnvRows),
        };
        await sdk.fetch('PUT', '/cpanelapi/nodejs/apps/' + encodeURIComponent(activeApp.id), values);
        ok('Node.js application configuration saved');
        load(true);
      } catch (e) {
        setFormError(e.message || 'Update failed');
      } finally {
        setFormBusy(false);
      }
    };

    // Delete App Submission
    const handleDeleteApp = async () => {
      if (!deleteTarget) return;
      try {
        await sdk.fetch('DELETE', '/cpanelapi/nodejs/apps/' + encodeURIComponent(deleteTarget.id));
        ok(`Application "${deleteTarget.name}" deleted`);
        setDeleteTarget(null);
        setSelectedAppId(null);
        load();
      } catch (e) {
        toastErr(e.message || 'Deletion failed');
      }
    };

    return html`
      <div class="page" style=${{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, overflow: 'hidden', padding: '24px' }}>
        <div class="page-header" style=${{ flexShrink: 0, marginBottom: 16, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div>
            <h1 class="page-title">Node.js Apps</h1>
            <p class="page-desc">
              PM2 process manager · ${apps.filter(a => a.status === 'running').length} app${apps.filter(a => a.status === 'running').length !== 1 ? 's' : ''} running
            </p>
          </div>
          <div style=${{ display: 'flex', gap: 8 }}>
            <button class="btn btn-outline btn-sm" onClick=${load} title="Reload All">
              ↺ Reload All
            </button>
            <button class="btn btn-primary btn-sm" onClick=${triggerAddView}>
              + New App
            </button>
          </div>
        </div>

        <div class="split-view" style=${{ flex: 1, minHeight: 0 }}>
          
          <!-- Left Panel: Apps List & Add trigger -->
          <div class="split-left" style=${{ width: 280, display: 'flex', flexDirection: 'column' }}>
            <div class="split-pane-header" style=${{ padding: '12px 14px', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
              <div class="search-wrap" style=${{ margin: 0, flex: 1 }}>
                <input
                  type="text"
                  placeholder="Search apps..."
                  value=${searchQuery}
                  onInput=${e => setSearchQuery(e.target.value)}
                />
              </div>
              <button class="btn btn-primary btn-sm" style=${{ padding: '6px 10px', marginLeft: 8 }} onClick=${triggerAddView}>
                + Add
              </button>
            </div>

            <div class="split-scroll" style=${{ flex: 1, overflowY: 'auto' }}>
              ${loading && apps.length === 0
                ? html`<div style=${{ color: 'var(--text-3)', padding: 20, textAlign: 'center', fontSize: 12.5 }}>Loading apps…</div>`
                : filteredApps.length === 0
                  ? html`
                      <div class="empty" style=${{ padding: '32px 16px' }}>
                        <div class="empty-title">No applications</div>
                        <div class="empty-desc" style=${{ fontSize: 11 }}>Click "+ Add" to provision your first application.</div>
                      </div>
                    `
                  : filteredApps.map(app => {
                      const isSelected = selectedAppId === app.id;
                      const isRunning = app.status === 'running';
                      return html`
                        <div
                          key=${app.id}
                          class=${'list-item ' + (isSelected ? 'sel' : '')}
                          onClick=${() => selectApp(app)}
                        >
                          <div style=${{ display: 'flex', alignItems: 'center', gap: 10, width: '100%' }}>
                            <div style=${{
                              width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
                              background: isRunning ? '#22c55e' : '#ef4444',
                              boxShadow: isRunning ? '0 0 6px #22c55e88' : 'none'
                            }}></div>
                            <div style=${{ flex: 1, minWidth: 0 }}>
                              <div class="li-name" style=${{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>${app.name}</div>
                              <div class="li-sub" style=${{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                node v${app.node_version}${app.port ? ' · port ' + app.port : ''}
                              </div>
                            </div>
                            <span class=${'chip ' + (isRunning ? 'chip-green' : 'chip-red')} style=${{ fontSize: 10 }}>
                              ${isRunning ? 'online' : 'stopped'}
                            </span>
                          </div>
                        </div>
                      `;
                    })
              }
            </div>

            <!-- Runtime Versions Info Footer -->
            <div style=${{ padding: 14, borderTop: '1px solid var(--border)', background: 'var(--bg-3)', fontSize: 11, color: 'var(--text-3)' }}>
              <div style=${{ fontWeight: 600, textTransform: 'uppercase', fontSize: 9, letterSpacing: '0.08em', marginBottom: 6 }}>Available Node Runtimes</div>
              <div style=${{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px 8px' }}>
                ${['node-18', 'node-20', 'node-22', 'node-24'].map(key => html`
                  <div key=${key}>${key.replace('node-', 'v')}: <span class="mono">${runtime[key] || '—'}</span></div>
                `)}
              </div>
            </div>
          </div>

          <!-- Right Panel: App Details / Inline Forms -->
          <div class="split-right" style=${{ paddingLeft: 20, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
            
            ${addingNew ? html`
              <!-- Creation Form View -->
              <div class="animate-fade-in" style=${{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' }}>
                <div class="split-pane-header" style=${{ padding: '14px 20px', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
                  <h3 style=${{ margin: 0 }}>Provision Node.js Application</h3>
                </div>
                <div style=${{ flex: 1, overflowY: 'auto', padding: '20px 24px' }}>
                  <form onSubmit=${handleCreateSubmit} style=${{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                    <div class="field">
                      <label>Application Name</label>
                      <input type="text" value=${formName} onInput=${e => setFormName(e.target.value)} placeholder="e.g. backend-api" required />
                    </div>
                    <div class="field">
                      <label>Target Domain</label>
                      <select value=${formDomain} onChange=${e => selectFormDomain(e.target.value)}>
                        ${(() => {
                          const main = domains.filter(d => d.type !== 'subdomain');
                          const subs = domains.filter(d => d.type === 'subdomain');
                          return html`
                            ${main.length > 0 && html`
                              <optgroup label="Domains">
                                ${main.map(item => html`<option key=${item.domain} value=${item.domain}>${item.domain}</option>`)}
                              </optgroup>
                            `}
                            ${subs.length > 0 && html`
                              <optgroup label="Subdomains">
                                ${subs.map(item => html`<option key=${item.domain} value=${item.domain}>${item.domain}</option>`)}
                              </optgroup>
                            `}
                          `;
                        })()}
                      </select>
                    </div>
                    <div class="field" style=${{ gridColumn: '1 / -1' }}>
                      <label>Application Root Directory</label>
                      <input type="text" value=${formAppRoot} onInput=${e => setFormAppRoot(e.target.value)} required />
                    </div>
                    <div class="field">
                      <label>Node Version</label>
                      <select value=${formNodeVersion} onChange=${e => setFormNodeVersion(e.target.value)}>
                        ${['18', '20', '22', '24'].map(v => html`
                          <option value=${v}>Node ${v}</option>
                        `)}
                      </select>
                    </div>
                    <div class="field">
                      <label>Port</label>
                      <input
                        type="number"
                        min=${ports?.min || 31000}
                        max=${ports?.max || 31999}
                        value=${formPort}
                        onInput=${e => setFormPort(e.target.value)}
                        required
                      />
                    </div>
                    <div class="field">
                      <label>Entrypoint File</label>
                      <input type="text" value=${formEntrypoint} onInput=${e => setFormEntrypoint(e.target.value)} placeholder="server.js" required />
                    </div>
                    <div class="field">
                      <label>Start Command <span style=${{ textTransform: 'lowercase', opacity: 0.7 }}>(optional)</span></label>
                      <input
                        type="text"
                        value=${formStartCommand}
                        placeholder=${'/opt/hostpanel/plugins/nodejs/bin/node-' + formNodeVersion + ' ' + formEntrypoint}
                        onInput=${e => setFormStartCommand(e.target.value)}
                      />
                    </div>

                    <!-- Environment Grid Inside Creation -->
                    <div style=${{ gridColumn: '1 / -1', marginTop: 8 }}>
                      <div style=${{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
                        <span class="section-label" style=${{ margin: 0, border: 'none' }}>Environment Variables</span>
                        <button type="button" class="btn btn-ghost btn-xs" onClick=${addEnvRow}>+ Add Variable</button>
                      </div>
                      <div style=${{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                        ${formEnvRows.map((row, index) => html`
                          <div key=${index} style=${{ display: 'grid', gridTemplateColumns: '1fr 1fr auto', gap: 10 }}>
                            <input type="text" value=${row.key} placeholder="KEY" onInput=${e => updateEnvRow(index, 'key', e.target.value)} />
                            <input type="text" value=${row.value} placeholder="value" onInput=${e => updateEnvRow(index, 'value', e.target.value)} />
                            <button type="button" class="btn btn-ghost btn-xs" onClick=${() => removeEnvRow(index)} disabled=${formEnvRows.length === 1}>Remove</button>
                          </div>
                        `)}
                      </div>
                    </div>

                    ${formError && html`<div style=${{ gridColumn: '1 / -1', color: 'var(--err)', fontSize: 12 }}>${formError}</div>`}

                    <div style=${{ gridColumn: '1 / -1', display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 14 }}>
                      <button type="button" class="btn btn-ghost btn-sm" onClick=${() => setAddingNew(false)} disabled=${formBusy}>Cancel</button>
                      <button type="submit" class="btn btn-primary btn-sm" disabled=${formBusy}>
                        ${formBusy ? html`<span class="node-spin" /> Provisioning…` : 'Provision Application'}
                      </button>
                    </div>
                  </form>
                </div>
              </div>
            ` : activeApp ? html`
              <!-- Application Details Split Panel -->
              <div class="animate-fade-in" style=${{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' }}>
                
                <!-- App Detail Header -->
                <div style=${{ padding: '14px 20px', borderBottom: '1px solid var(--border)', flexShrink: 0, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                  <div>
                    <div style=${{ display: 'flex', alignItems: 'center', gap: 10 }}>
                      <div style=${{
                        width: 8, height: 8, borderRadius: '50%',
                        background: activeApp.status === 'running' ? '#22c55e' : '#ef4444',
                        boxShadow: activeApp.status === 'running' ? '0 0 8px #22c55e88' : 'none'
                      }}></div>
                      <span style=${{ fontSize: 17, fontWeight: 600, color: 'var(--text)', letterSpacing: '-0.4px' }}>${activeApp.name}</span>
                      <span class=${'chip ' + (activeApp.status === 'running' ? 'chip-green' : 'chip-red')}>
                        ${activeApp.status === 'running' ? 'online' : 'stopped'}
                      </span>
                    </div>
                    <div style=${{ fontSize: 12, color: 'var(--text-3)', marginTop: 4, marginLeft: 18 }}>
                      node v${activeApp.node_version}${activeApp.pm2_id != null ? ' · PM2 id #' + activeApp.pm2_id : ''} · port ${activeApp.port} · ${activeApp.app_root || activeApp.directory || '—'}
                    </div>
                  </div>
                  <div style=${{ display: 'flex', gap: 6 }}>
                    <button class="btn btn-outline btn-sm" onClick=${() => handleTabChange('logs')}>📋 Logs</button>
                    <button
                      class="btn btn-outline btn-sm"
                      style=${{ color: 'var(--amber)', borderColor: 'var(--amber-border, #f59e0b)' }}
                      disabled=${busyAppId === activeApp.id}
                      onClick=${() => handlePowerAction(activeApp, 'restart')}
                    >↺ Restart</button>
                    <button
                      class="btn btn-danger btn-sm"
                      disabled=${busyAppId === activeApp.id || activeApp.status === 'stopped'}
                      onClick=${() => handlePowerAction(activeApp, 'stop')}
                    >⏹ Stop</button>
                  </div>
                </div>

                <!-- Tabs Navigation -->
                <div class="tab-bar" style=${{ borderBottom: '1px solid var(--border)', padding: '0 20px', flexShrink: 0 }}>
                  <button class=${'tab' + (activeTab === 'control' ? ' active' : '')} onClick=${() => handleTabChange('control')}>Control</button>
                  <button class=${'tab' + (activeTab === 'config' ? ' active' : '')} onClick=${() => handleTabChange('config')}>Configuration</button>
                  <button class=${'tab' + (activeTab === 'logs' ? ' active' : '')} onClick=${() => handleTabChange('logs')}>Logs</button>
                  <button class=${'tab' + (activeTab === 'danger' ? ' active' : '')} onClick=${() => handleTabChange('danger')}>Danger Zone</button>
                </div>

                <!-- Tab Contents -->
                <div style=${{ flex: 1, overflowY: 'auto', padding: 20 }}>
                  
                  ${activeTab === 'control' && html`
                    <div class="animate-fade-in" style=${{ display: 'flex', flexDirection: 'column', gap: 20 }}>
                      
                      <!-- Stat Cards Row (4 cols like design) -->
                      <div style=${{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
                        <div class="stat-card">
                          <div class="stat-label">CPU</div>
                          <div class="stat-value">${activeApp.cpu != null ? activeApp.cpu + '%' : '—'}</div>
                          <div class="stat-sub">current</div>
                        </div>
                        <div class="stat-card">
                          <div class="stat-label">Memory</div>
                          <div class="stat-value">${activeApp.memory_mb != null ? activeApp.memory_mb + ' MB' : '—'}</div>
                          <div class="stat-sub">resident</div>
                        </div>
                        <div class="stat-card">
                          <div class="stat-label">Uptime</div>
                          <div class="stat-value">${activeApp.uptime || '—'}</div>
                          <div class="stat-sub">restarts: ${activeApp.restarts ?? '—'}</div>
                        </div>
                        <div class="stat-card">
                          <div class="stat-label">Port</div>
                          <div class="stat-value" style=${{ fontFamily: 'var(--font-mono)' }}>:${activeApp.port}</div>
                          <div class="stat-sub">node v${activeApp.node_version}</div>
                        </div>
                      </div>

                      <!-- Process Config + Runtime Info (2-col cards) -->
                      <div style=${{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
                        <div class="card" style=${{ padding: 16 }}>
                          <div style=${{ fontSize: 12, fontWeight: 600, color: 'var(--text-2)', marginBottom: 12 }}>⚙ Process Config</div>
                          <div style=${{ display: 'grid', gap: 8 }}>
                            ${[
                              ['Script', activeApp.entrypoint],
                              ['Port', ':' + activeApp.port],
                              ['Auto-restart', 'enabled'],
                              ['Node version', 'v' + activeApp.node_version],
                            ].map(([k, v]) => html`
                              <div key=${k} style=${{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '6px 0', borderBottom: '1px solid var(--border)' }}>
                                <span style=${{ fontSize: 12, color: 'var(--text-3)' }}>${k}</span>
                                <span class="mono" style=${{ fontSize: 12, color: 'var(--text-2)' }}>${v}</span>
                              </div>`)}
                          </div>
                        </div>
                        <div class="card" style=${{ padding: 16 }}>
                          <div style=${{ fontSize: 12, fontWeight: 600, color: 'var(--text-2)', marginBottom: 12 }}>⬡ Runtime Info</div>
                          <div style=${{ display: 'grid', gap: 8 }}>
                            ${[
                              ['App Root', activeApp.app_root || activeApp.directory || '—'],
                              ['Domain', activeApp.domain || '—'],
                              ['Owner', activeApp.username || '—'],
                              ['Status', activeApp.status],
                            ].map(([k, v]) => html`
                              <div key=${k} style=${{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '6px 0', borderBottom: '1px solid var(--border)' }}>
                                <span style=${{ fontSize: 12, color: 'var(--text-3)' }}>${k}</span>
                                <span style=${{ fontSize: 12, color: 'var(--text-2)', maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>${v}</span>
                              </div>`)}
                          </div>
                        </div>
                      </div>

                      <!-- Actions Card -->
                      <div class="card" style=${{ padding: 16 }}>
                        <div style=${{ fontSize: 12, fontWeight: 600, color: 'var(--text-2)', marginBottom: 12 }}>Actions</div>
                        <div style=${{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                          <button
                            class="btn btn-outline btn-sm"
                            disabled=${busyAppId === activeApp.id || activeApp.status === 'running'}
                            onClick=${() => handlePowerAction(activeApp, 'start')}
                          >▶ Start</button>
                          <button
                            class="btn btn-outline btn-sm"
                            disabled=${busyAppId === activeApp.id || activeApp.status === 'stopped'}
                            onClick=${() => handlePowerAction(activeApp, 'stop')}
                          >⏹ Stop</button>
                          <button
                            class="btn btn-outline btn-sm"
                            disabled=${busyAppId === activeApp.id || activeApp.status === 'stopped'}
                            onClick=${() => handlePowerAction(activeApp, 'restart')}
                          >↺ Restart</button>
                          <button
                            class="btn btn-danger btn-sm"
                            onClick=${() => setDeleteTarget(activeApp)}
                          >🗑 Delete</button>
                        </div>
                      </div>
                    </div>
                  `}

                  ${activeTab === 'config' && html`
                    <form onSubmit=${handleEditSubmit} class="animate-fade-in" style=${{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                      <div class="field" style=${{ gridColumn: '1 / -1' }}>
                        <label>Application Root Directory</label>
                        <input type="text" value=${formAppRoot} onInput=${e => setFormAppRoot(e.target.value)} required />
                      </div>
                      <div class="field">
                        <label>Node Version</label>
                        <select value=${formNodeVersion} onChange=${e => setFormNodeVersion(e.target.value)}>
                          ${['18', '20', '22', '24'].map(v => html`
                            <option value=${v}>Node ${v}</option>
                          `)}
                        </select>
                      </div>
                      <div class="field">
                        <label>Port</label>
                        <input
                          type="number"
                          min=${ports?.min || 31000}
                          max=${ports?.max || 31999}
                          value=${formPort}
                          onInput=${e => setFormPort(e.target.value)}
                          required
                        />
                      </div>
                      <div class="field">
                        <label>Entrypoint File</label>
                        <input type="text" value=${formEntrypoint} onInput=${e => setFormEntrypoint(e.target.value)} required />
                      </div>
                      <div class="field">
                        <label>Start Command <span style=${{ textTransform: 'lowercase', opacity: 0.7 }}>(optional)</span></label>
                        <input
                          type="text"
                          value=${formStartCommand}
                          placeholder=${'/opt/hostpanel/plugins/nodejs/bin/node-' + formNodeVersion + ' ' + formEntrypoint}
                          onInput=${e => setFormStartCommand(e.target.value)}
                        />
                      </div>

                      <!-- Config Env Variables Grid -->
                      <div style=${{ gridColumn: '1 / -1', marginTop: 8 }}>
                        <div style=${{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
                          <span class="section-label" style=${{ margin: 0, border: 'none' }}>Environment Variables</span>
                          <button type="button" class="btn btn-ghost btn-xs" onClick=${addEnvRow}>+ Add Variable</button>
                        </div>
                        <div style=${{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                          ${formEnvRows.map((row, index) => html`
                            <div key=${index} style=${{ display: 'grid', gridTemplateColumns: '1fr 1fr auto', gap: 10 }}>
                              <input type="text" value=${row.key} placeholder="KEY" onInput=${e => updateEnvRow(index, 'key', e.target.value)} />
                              <input type="text" value=${row.value} placeholder="value" onInput=${e => updateEnvRow(index, 'value', e.target.value)} />
                              <button type="button" class="btn btn-ghost btn-xs" onClick=${() => removeEnvRow(index)} disabled=${formEnvRows.length === 1}>Remove</button>
                            </div>
                          `)}
                        </div>
                      </div>

                      ${formError && html`<div style=${{ gridColumn: '1 / -1', color: 'var(--err)', fontSize: 12 }}>${formError}</div>`}

                      <div style=${{ gridColumn: '1 / -1', display: 'flex', justifyContent: 'flex-end', marginTop: 14 }}>
                        <button type="submit" class="btn btn-primary btn-sm" disabled=${formBusy}>
                          ${formBusy ? html`<span class="node-spin" /> Saving…` : 'Save Configuration'}
                        </button>
                      </div>
                    </form>
                  `}

                  ${activeTab === 'logs' && html`
                    <div class="animate-fade-in" style=${{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                      <div style=${{ display: 'flex', justifyContent: 'flex-end' }}>
                        <button class="btn btn-ghost btn-xs" onClick=${() => fetchLogs(activeApp.id)} disabled=${logsLoading}>
                          ${logsLoading ? 'Refreshing…' : '⟳ Refresh Logs'}
                        </button>
                      </div>
                      <pre class="node-log-pre">${logsLoading ? 'Loading logs…' : logs.length ? logs.map(row => `${row.created_at || ''} ${row.level || ''} ${row.message || ''}`).join('\n') : 'No logs generated yet'}</pre>
                    </div>
                  `}

                  ${activeTab === 'danger' && html`
                    <div class="animate-fade-in" style=${{ border: '1px solid rgba(239,68,68,0.2)', background: 'rgba(239,68,68,0.04)', padding: 18, borderRadius: 'var(--radius-lg)' }}>
                      <span style=${{ fontWeight: 600, color: 'var(--err)', fontSize: 14, display: 'block', marginBottom: 6 }}>Danger Zone</span>
                      <p style=${{ fontSize: 12.5, color: 'var(--text-2)', lineHeight: 1.6, marginBottom: 14 }}>
                        Deleting this application will permanently remove its process service and nginx proxy configuration. The application folder files will remain untouched.
                      </p>
                      <button class="btn btn-danger btn-sm" onClick=${() => setDeleteTarget(activeApp)}>
                        Delete Application
                      </button>
                    </div>
                  `}

                </div>
              </div>
            ` : html`
              <!-- Blank State -->
              <div class="empty" style=${{ flex: 1 }}>
                <div class="empty-icon" style=${{ fontSize: 32 }}>📦</div>
                <div class="empty-title">No Application Selected</div>
                <div class="empty-desc">Select a Node.js application from the left panel to manage it, or click "+ Add" to provision a new application.</div>
              </div>
            `}
          </div>
        </div>

        ${deleteTarget && html`
          <${SdkConfirmModal}
            open=${true}
            title="Delete Node.js Application"
            message=${'Delete application "' + deleteTarget.name + '"? The daemon service and domains routes are removed. Source files are safe.'}
            danger=${true}
            onClose=${() => setDeleteTarget(null)}
            onConfirm=${handleDeleteApp}
          />
        `}
      </div>
    `;
  }

  sdk.register('nodejs', NodeJsPlugin);
})();
