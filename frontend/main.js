/* hostpanel-package-nodejs - frontend/main.js */
(function () {
  'use strict';

  const sdk = window.__hpkg_sdk;
  const { html, useEffect, useState, useCallback } = sdk;
  const { SdkConfirmModal, SdkDataTable } = sdk.components;
  const { useToast } = sdk.hooks;

  const DEFAULT_PORT = 31000;

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

  function AppFormModal({ title, mode, app, domains, ports, onClose, onSubmit }) {
    const firstDomain = app?.domain || domains[0]?.domain || '';
    const selectedInitial = domains.find(item => item.domain === firstDomain);
    const [name, setName] = useState(app?.name || '');
    const [domain, setDomain] = useState(firstDomain);
    const [appRoot, setAppRoot] = useState(app?.app_root || selectedInitial?.document_root || '');
    const [nodeVersion, setNodeVersion] = useState(app?.node_version || '22');
    const [port, setPort] = useState(app?.port || DEFAULT_PORT);
    const [entrypoint, setEntrypoint] = useState(app?.entrypoint || 'server.js');
    const [startCommand, setStartCommand] = useState(app?.start_command || '');
    const [envRows, setEnvRows] = useState(envToRows(app?.env));
    const [busy, setBusy] = useState(false);
    const [formError, setFormError] = useState('');

    const usedPorts = new Set((ports?.ports || []).filter(item => !item.available && item.app_id !== app?.id).map(item => item.port));

    const selectDomain = value => {
      setDomain(value);
      const selected = domains.find(item => item.domain === value);
      if (selected && mode === 'create') {
        setAppRoot(selected.document_root || '');
      }
    };

    const updateEnvRow = (index, field, value) => {
      setEnvRows(rows => rows.map((row, i) => i === index ? { ...row, [field]: value } : row));
    };

    const removeEnvRow = index => {
      setEnvRows(rows => rows.filter((_, i) => i !== index));
    };

    const save = async () => {
      setFormError('');
      if (!name.trim()) {
        setFormError('Application name is required');
        return;
      }
      if (!domain) {
        setFormError('Target domain is required');
        return;
      }
      if (usedPorts.has(Number(port))) {
        setFormError('Selected port is already assigned');
        return;
      }
      setBusy(true);
      try {
        await onSubmit({
          name: name.trim(),
          domain,
          app_root: appRoot.trim(),
          node_version: nodeVersion,
          port: Number(port),
          entrypoint: entrypoint.trim(),
          start_command: startCommand.trim(),
          env: rowsToEnv(envRows),
        });
      } catch (e) {
        setFormError(e.message || 'Something went wrong');
      } finally {
        setBusy(false);
      }
    };

    return html`
      <div class="modal-overlay" onClick=${e => e.target === e.currentTarget && onClose()}>
        <div class="modal animate-fade-in" style=${{ width: 720, maxWidth: 'calc(100vw - 32px)' }}>
          <div class="modal-header">
            <span class="modal-title">${title}</span>
            <button class="modal-close" onClick=${onClose} aria-label="Close">x</button>
          </div>
          <div class="modal-body" style=${{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
            <div class="field">
              <label>Application name</label>
              <input type="text" value=${name} onInput=${e => setName(e.target.value)} placeholder="API" />
            </div>
            <div class="field">
              <label>Target domain</label>
              <select value=${domain} onChange=${e => selectDomain(e.target.value)}>
                ${domains.map(item => html`
                  <option value=${item.domain}>${item.domain}</option>
                `)}
              </select>
            </div>
            <div class="field" style=${{ gridColumn: '1 / -1' }}>
              <label>Application root</label>
              <input type="text" value=${appRoot} onInput=${e => setAppRoot(e.target.value)} />
            </div>
            <div class="field">
              <label>Node version</label>
              <select value=${nodeVersion} onChange=${e => setNodeVersion(e.target.value)}>
                ${['18', '20', '22', '24'].map(version => html`
                  <option value=${version}>Node ${version}</option>
                `)}
              </select>
            </div>
            <div class="field">
              <label>Port</label>
              <input
                type="number"
                min=${ports?.min || 31000}
                max=${ports?.max || 31999}
                value=${port}
                onInput=${e => setPort(e.target.value)}
              />
            </div>
            <div class="field">
              <label>Entrypoint</label>
              <input type="text" value=${entrypoint} onInput=${e => setEntrypoint(e.target.value)} />
            </div>
            <div class="field">
              <label>Start command</label>
              <input
                type="text"
                value=${startCommand}
                placeholder=${'/opt/hostpanel/plugins/nodejs/bin/node-' + nodeVersion + ' ' + entrypoint}
                onInput=${e => setStartCommand(e.target.value)}
              />
            </div>
            <div style=${{ gridColumn: '1 / -1', display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style=${{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <span class="card-title" style=${{ marginBottom: 0, fontSize: 13 }}>Environment</span>
                <button class="btn btn-ghost btn-sm" onClick=${() => setEnvRows(rows => rows.concat({ key: '', value: '' }))}>
                  Add Variable
                </button>
              </div>
              ${envRows.map((row, index) => html`
                <div style=${{ display: 'grid', gridTemplateColumns: '180px 1fr auto', gap: 8 }}>
                  <input type="text" value=${row.key} placeholder="NODE_ENV" onInput=${e => updateEnvRow(index, 'key', e.target.value)} />
                  <input type="text" value=${row.value} placeholder="production" onInput=${e => updateEnvRow(index, 'value', e.target.value)} />
                  <button class="btn btn-ghost btn-sm" onClick=${() => removeEnvRow(index)} disabled=${envRows.length === 1}>Remove</button>
                </div>
              `)}
            </div>
            ${formError && html`
              <div style=${{ gridColumn: '1 / -1', color: 'var(--err)', fontSize: 12 }}>${formError}</div>
            `}
          </div>
          <div class="modal-footer">
            <button class="btn btn-ghost btn-sm" onClick=${onClose} disabled=${busy}>Cancel</button>
            <button class="btn btn-primary btn-sm" onClick=${save} disabled=${busy}>
              ${busy ? 'Working...' : (mode === 'create' ? 'Provision' : 'Save')}
            </button>
          </div>
        </div>
      </div>
    `;
  }

  function LogsModal({ app, onClose }) {
    const [logs, setLogs] = useState([]);
    const [loading, setLoading] = useState(true);

    const load = useCallback(() => {
      setLoading(true);
      sdk.fetch('GET', '/cpanelapi/nodejs/apps/' + encodeURIComponent(app.id) + '/logs')
        .then(data => setLogs(data || []))
        .finally(() => setLoading(false));
    }, [app.id]);

    useEffect(() => { load(); }, [load]);

    return html`
      <div class="modal-overlay" onClick=${e => e.target === e.currentTarget && onClose()}>
        <div class="modal animate-fade-in" style=${{ width: 760, maxWidth: 'calc(100vw - 32px)' }}>
          <div class="modal-header">
            <span class="modal-title">${'Logs - ' + app.name}</span>
            <button class="modal-close" onClick=${onClose} aria-label="Close">x</button>
          </div>
          <div class="modal-body">
            <pre class="log-output" style=${{ minHeight: 320, maxHeight: 460, overflow: 'auto' }}>
${loading ? 'Loading...' : (logs.map(row => `${row.created_at || ''} ${row.level || ''} ${row.message || ''}`).join('\n') || 'No logs yet')}
            </pre>
          </div>
          <div class="modal-footer">
            <button class="btn btn-ghost btn-sm" onClick=${load}>Refresh</button>
            <button class="btn btn-primary btn-sm" onClick=${onClose}>Close</button>
          </div>
        </div>
      </div>
    `;
  }

  function NodeJsPlugin() {
    const { ok } = useToast();
    const [apps, setApps] = useState([]);
    const [domains, setDomains] = useState([]);
    const [ports, setPorts] = useState(null);
    const [runtime, setRuntime] = useState({});
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [createOpen, setCreateOpen] = useState(false);
    const [editTarget, setEditTarget] = useState(null);
    const [deleteTarget, setDeleteTarget] = useState(null);
    const [logsTarget, setLogsTarget] = useState(null);
    const [busyAppId, setBusyAppId] = useState(null);

    const load = useCallback(() => {
      setLoading(true);
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

    useEffect(() => { load(); }, [load]);

    const createApp = async values => {
      await sdk.fetch('POST', '/cpanelapi/nodejs/apps', values);
      setCreateOpen(false);
      ok('Node.js application provisioned');
      load();
    };

    const updateApp = async values => {
      await sdk.fetch('PUT', '/cpanelapi/nodejs/apps/' + encodeURIComponent(editTarget.id), values);
      setEditTarget(null);
      ok('Node.js application updated');
      load();
    };

    const action = async (app, name) => {
      setBusyAppId(app.id);
      try {
        await sdk.fetch('POST', '/cpanelapi/nodejs/apps/' + encodeURIComponent(app.id) + '/' + name);
        ok('Application ' + name + ' requested');
        load();
      } finally {
        setBusyAppId(null);
      }
    };

    const deleteApp = async () => {
      await sdk.fetch('DELETE', '/cpanelapi/nodejs/apps/' + encodeURIComponent(deleteTarget.id));
      setDeleteTarget(null);
      ok('Node.js application deleted');
      load();
    };

    return html`
      <div class="page">
        <div class="page-header">
          <div>
            <h1 class="page-title">Node.js</h1>
            <p class="page-desc">Manage hosted Node.js applications</p>
          </div>
        </div>

        <div class="card" style=${{ marginBottom: 16 }}>
          <div style=${{ display: 'grid', gridTemplateColumns: 'repeat(6, minmax(0, 1fr))', gap: 12 }}>
            ${['node-18', 'node-20', 'node-22', 'node-24'].map(key => html`
              <div>
                <div style=${{ fontSize: 11, color: 'var(--muted)' }}>${key}</div>
                <div style=${{ fontFamily: 'monospace', fontSize: 13 }}>${runtime[key] || '-'}</div>
              </div>
            `)}
          </div>
        </div>

        <div class="card">
          <div style=${{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 16 }}>
            <span class="card-title" style=${{ marginBottom: 0 }}>Applications</span>
            <button class="btn btn-primary btn-sm" onClick=${() => setCreateOpen(true)} disabled=${!domains.length}>
              Add Application
            </button>
          </div>

          ${error
            ? html`
                <div class="empty">
                  <div class="empty-title" style=${{ color: 'var(--err)' }}>Could not load Node.js applications</div>
                  <div class="empty-desc">${error}</div>
                </div>
              `
            : html`
                <${SdkDataTable}
                  columns=${[
                    { key: 'name', label: 'Name' },
                    { key: 'username', label: 'Owner', type: 'mono' },
                    { key: 'domain', label: 'Domain' },
                    { key: 'node_version', label: 'Node' },
                    { key: 'port', label: 'Port', type: 'mono' },
                    { key: 'status', label: 'Status' },
                  ]}
                  rows=${apps}
                  loading=${loading}
                  empty=${{ title: 'No Node.js applications', desc: 'Provision an application for an existing domain or subdomain.' }}
                  renderActions=${row => {
                    const busy = busyAppId === row.id;
                    const isRunning = row.status === 'running';
                    const isStopped = row.status === 'stopped';
                    return html`
                      <button class="btn btn-ghost btn-sm" onClick=${() => action(row, 'start')} disabled=${busy || isRunning}>Start</button>
                      <button class="btn btn-ghost btn-sm" onClick=${() => action(row, 'stop')} disabled=${busy || isStopped}>Stop</button>
                      <button class="btn btn-ghost btn-sm" onClick=${() => action(row, 'restart')} disabled=${busy || isStopped}>Restart</button>
                      <button class="btn btn-ghost btn-sm" onClick=${() => setLogsTarget(row)} disabled=${busy}>Logs</button>
                      <button class="btn btn-ghost btn-sm" onClick=${() => setEditTarget(row)} disabled=${busy}>Edit</button>
                      <button class="btn btn-danger btn-sm" onClick=${() => setDeleteTarget(row)} disabled=${busy}>Delete</button>
                    `;
                  }}
                />
              `
          }
        </div>
      </div>

      ${createOpen && html`
        <${AppFormModal}
          title="Add Application"
          mode="create"
          domains=${domains}
          ports=${ports}
          onClose=${() => setCreateOpen(false)}
          onSubmit=${createApp}
        />
      `}

      ${editTarget && html`
        <${AppFormModal}
          title=${'Edit Application - ' + editTarget.name}
          mode="edit"
          app=${editTarget}
          domains=${domains}
          ports=${ports}
          onClose=${() => setEditTarget(null)}
          onSubmit=${updateApp}
        />
      `}

      ${logsTarget && html`
        <${LogsModal} app=${logsTarget} onClose=${() => setLogsTarget(null)} />
      `}

      ${deleteTarget && html`
        <${SdkConfirmModal}
          open=${true}
          title="Delete Application"
          message=${'Delete "' + deleteTarget.name + '"? The app service and proxy will be removed. Application files are preserved.'}
          danger=${true}
          onClose=${() => setDeleteTarget(null)}
          onConfirm=${deleteApp}
        />
      `}
    `;
  }

  sdk.register('nodejs', NodeJsPlugin);
})();
