(() => {
  'use strict';

  const notice = document.getElementById('firmwareNotice');
  const initialStateElement = document.getElementById('firmwareInitialState');
  let currentState = {};

  try {
    currentState = JSON.parse(initialStateElement?.textContent || '{}');
  } catch (_error) {
    currentState = {};
  }

  function showNotice(message, kind = 'success') {
    if (!notice) return;
    notice.textContent = message;
    notice.className = `alert alert-${kind}`;
    notice.setAttribute('role', kind === 'danger' ? 'alert' : 'status');
  }

  async function responseBody(response) {
    const text = await response.text();
    if (!text) return {};
    try {
      return JSON.parse(text);
    } catch (_error) {
      return {};
    }
  }

  async function request(url, options = {}) {
    const response = await fetch(url, {cache: 'no-store', ...options});
    const body = await responseBody(response);
    if (!response.ok) {
      throw new Error(body.error || `Request failed (${response.status})`);
    }
    return body;
  }

  async function whileBusy(button, busyLabel, action) {
    const originalLabel = button.textContent;
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    button.textContent = busyLabel;
    try {
      return await action();
    } finally {
      button.removeAttribute('aria-busy');
      button.textContent = originalLabel;
      renderDashboard(currentState);
    }
  }

  function parameterEntries(schema) {
    if (!schema || typeof schema !== 'object' || Array.isArray(schema)) return [];
    return Object.entries(schema).map(([name, definition]) => ({name, ...definition}));
  }

  function controlId(container, name) {
    const packageId = container.closest('[data-package-id]')?.dataset.packageId || 'package';
    return `firmware-${packageId}-${name}`.replace(/[^a-zA-Z0-9_-]/g, '-');
  }

  function appendHelp(container, input, definition) {
    const messages = [];
    if (definition.description) messages.push(definition.description);
    if (definition.min !== undefined && definition.max !== undefined) {
      messages.push(`Allowed range: ${definition.min} to ${definition.max}.`);
    } else if (definition.min !== undefined) {
      messages.push(`Minimum: ${definition.min}.`);
    } else if (definition.max !== undefined) {
      messages.push(`Maximum: ${definition.max}.`);
    }
    if (!messages.length) return;
    const help = document.createElement('span');
    help.id = `${input.id}-help`;
    help.className = 'firmware-parameter-description';
    help.textContent = messages.join(' ');
    input.setAttribute('aria-describedby', help.id);
    container.appendChild(help);
  }

  function buildControls(container) {
    let schema = {};
    let values = {};
    try {
      schema = JSON.parse(container.dataset.schema || '{}');
      values = JSON.parse(container.dataset.values || '{}');
    } catch (_error) {
      showNotice('A package has an invalid parameter schema and cannot be edited here.', 'danger');
      return;
    }

    parameterEntries(schema).forEach((definition) => {
      const wrapper = document.createElement('div');
      wrapper.className = 'firmware-parameter';
      const input = document.createElement('input');
      const id = controlId(container, definition.name);
      const labelText = definition.label || definition.name.replaceAll('_', ' ');
      const configuredValue = Object.hasOwn(values, definition.name)
        ? values[definition.name]
        : definition.default;
      input.id = id;
      input.dataset.parameterName = definition.name;
      input.dataset.parameterType = definition.type;
      input.addEventListener('input', () => { input.dataset.dirty = 'true'; });

      if (definition.type === 'bool') {
        wrapper.classList.add('form-check', 'firmware-boolean');
        input.type = 'checkbox';
        input.className = 'form-check-input';
        input.checked = Boolean(configuredValue);
        const label = document.createElement('label');
        label.className = 'form-check-label';
        label.htmlFor = id;
        label.textContent = labelText;
        wrapper.append(input, label);
        appendHelp(wrapper, input, definition);
      } else {
        const label = document.createElement('label');
        label.className = 'form-label mb-1 text-capitalize';
        label.htmlFor = id;
        label.textContent = labelText;
        wrapper.appendChild(label);

        if (definition.type === 'enum') {
          const select = document.createElement('select');
          select.id = id;
          select.className = 'form-select form-select-sm';
          select.dataset.parameterName = definition.name;
          select.dataset.parameterType = definition.type;
          select.addEventListener('change', () => { select.dataset.dirty = 'true'; });
          (definition.options || []).forEach((value) => {
            select.add(new Option(value, value, false, value === configuredValue));
          });
          wrapper.appendChild(select);
          appendHelp(wrapper, select, definition);
        } else {
          input.className = 'form-control form-control-sm';
          input.type = definition.type === 'color' ? 'color' : 'number';
          if (definition.type === 'int') input.step = '1';
          if (definition.type === 'float') input.step = 'any';
          if (definition.min !== undefined) input.min = definition.min;
          if (definition.max !== undefined) input.max = definition.max;
          input.value = configuredValue ?? (definition.type === 'color' ? '#ffffff' : '');
          wrapper.appendChild(input);
          appendHelp(wrapper, input, definition);
        }
      }
      container.appendChild(wrapper);
    });
  }

  function parameterValues(card) {
    const output = {};
    card.querySelectorAll('[data-parameter-name]').forEach((input) => {
      if (!input.checkValidity()) {
        input.reportValidity();
        throw new Error(`Check the value for ${input.dataset.parameterName.replaceAll('_', ' ')}.`);
      }
      const type = input.dataset.parameterType;
      let value;
      if (type === 'bool') {
        value = input.checked;
      } else if (type === 'int') {
        if (input.value.trim() === '') throw new Error(`${input.dataset.parameterName} is required.`);
        value = Number(input.value);
        if (!Number.isInteger(value)) throw new Error(`${input.dataset.parameterName} must be an integer.`);
      } else if (type === 'float') {
        if (input.value.trim() === '') throw new Error(`${input.dataset.parameterName} is required.`);
        value = Number(input.value);
        if (!Number.isFinite(value)) throw new Error(`${input.dataset.parameterName} must be numeric.`);
      } else {
        value = input.value;
      }
      output[input.dataset.parameterName] = value;
    });
    return output;
  }

  function packageName(packageId, state = currentState) {
    return state.animations?.find((item) => item.id === packageId)?.name || packageId;
  }

  function formatReportTime(value, ageSeconds) {
    if (typeof value !== 'number') return 'Receiver state is a status report, not live framebuffer readback.';
    const relative = typeof ageSeconds === 'number'
      ? ` (${Math.round(ageSeconds)} seconds ago)`
      : '';
    return `Last controller report: ${new Date(value * 1000).toLocaleTimeString()}${relative}. This is not live framebuffer readback.`;
  }

  function renderController(controller = {}) {
    const badge = document.getElementById('firmwareConnectionBadge');
    const physicalState = document.getElementById('firmwarePhysicalState');
    const reportedAt = document.getElementById('firmwareReportedAt');
    const stop = document.getElementById('firmwareStop');
    if (!badge || !physicalState || !reportedAt || !stop) return;

    if (controller.stale) {
      badge.textContent = 'Report stale';
      badge.className = 'badge bg-warning text-dark';
      const lastKnown = controller.active_package_id
        ? ` The last report named “${packageName(controller.active_package_id)}” as active.`
        : '';
      physicalState.textContent = `The controller report is stale.${lastKnown} Receivers may still be playing, but this dashboard cannot confirm current wall output.`;
    } else if (!controller.connected) {
      badge.textContent = 'No controller report';
      badge.className = 'badge bg-secondary';
      physicalState.textContent = 'Controller status is unavailable. Receivers may continue an earlier animation; current wall output is unknown.';
    } else if (controller.active_package_id) {
      badge.textContent = 'Controller reporting';
      badge.className = 'badge bg-success';
      physicalState.textContent = `Receivers report “${packageName(controller.active_package_id)}” is playing on the wall.`;
    } else {
      badge.textContent = 'Controller reporting';
      badge.className = 'badge bg-success';
      const modeMessages = {
        python: 'The wall is showing a streamed animation, not a receiver package.',
        animation: 'The wall is showing a streamed animation, not a receiver package.',
        painter: 'The wall is showing Painter output, not a receiver package.',
        idle: 'No receiver animation is reported playing on the wall.',
      };
      physicalState.textContent = modeMessages[controller.mode]
        || 'No receiver animation is reported playing on the wall.';
    }
    reportedAt.textContent = formatReportTime(controller.reported_at, controller.report_age_seconds);
    stop.disabled = !controller.active_package_id || controller.stale || !controller.connected;
  }

  function renderOperation(operation = {}) {
    const section = document.getElementById('firmwareOperation');
    const badge = document.getElementById('firmwareOperationBadge');
    const message = document.getElementById('firmwareOperationMessage');
    const progress = document.getElementById('firmwareOperationProgress');
    const progressBar = progress?.querySelector('.progress-bar');
    const error = document.getElementById('firmwareOperationError');
    if (!section || !badge || !message || !progress || !progressBar || !error) return;

    const state = operation.state || 'idle';
    section.hidden = state === 'idle';
    section.dataset.state = state;
    const labels = {
      probing: 'Checking',
      uploading: 'Installing',
      ready: 'Completed',
      retry: 'Needs attention',
      unsupported: 'Unsupported',
      degraded: 'State uncertain',
    };
    const messages = {
      probing: 'Checking receiver caches before the last requested installation.',
      uploading: 'Installing the last requested package across the receivers.',
      ready: 'The last receiver installation completed. This status is not tied to every library card.',
      retry: 'The last receiver installation did not complete. Use “Install on receivers” on the intended package to try again.',
      unsupported: 'At least one receiver lacks capabilities required by the last installation request.',
      degraded: 'The controller could not reconcile all receiver states. Do not assume playback stopped or parameters are uniform; retry the intended stop or start operation after checking receiver status.',
    };
    badge.textContent = labels[state] || state;
    badge.className = `badge ${state === 'ready' ? 'bg-success' : state === 'degraded' ? 'bg-danger' : state === 'retry' || state === 'unsupported' ? 'bg-warning text-dark' : 'bg-primary'}`;
    message.textContent = messages[state] || '';
    if (state === 'unsupported' && operation.unsupported_devices?.length) {
      const receivers = operation.unsupported_devices.map((index) => index + 1).join(', ');
      message.textContent += ` Affected receiver${operation.unsupported_devices.length === 1 ? '' : 's'}: ${receivers}.`;
    }
    if (state === 'degraded' && operation.degraded_devices?.length) {
      const receivers = operation.degraded_devices.map((index) => index + 1).join(', ');
      message.textContent += ` Unconfirmed receiver${operation.degraded_devices.length === 1 ? '' : 's'}: ${receivers}.`;
    }

    const percent = Math.round(Math.max(0, Math.min(1, Number(operation.progress) || 0)) * 100);
    progress.hidden = state !== 'probing' && state !== 'uploading';
    progress.setAttribute('aria-valuenow', String(percent));
    progressBar.style.width = `${percent}%`;
    progressBar.textContent = `${percent}%`;
    error.hidden = !operation.error;
    error.textContent = operation.error || '';
  }

  function renderCards(state) {
    const cards = [...document.querySelectorAll('.firmware-animation-card')];
    const pageIds = cards.map((card) => card.dataset.packageId).sort();
    const stateIds = (state.animations || []).map((item) => item.id).sort();
    if (pageIds.join('\n') !== stateIds.join('\n')) {
      window.location.reload();
      return;
    }

    const stale = Boolean(state.controller?.stale);
    cards.forEach((card) => {
      const item = state.animations.find((candidate) => candidate.id === card.dataset.packageId);
      if (!item) return;
      const active = Boolean(item.active);
      card.dataset.active = String(active);
      const location = card.querySelector('.firmware-location');
      const play = card.querySelector('.firmware-play');
      const apply = card.querySelector('.firmware-live');
      const remove = card.querySelector('.firmware-delete');
      if (location) {
        location.textContent = active ? (stale ? 'Last reported on wall' : 'Reported on wall') : 'In host library';
        location.className = `badge firmware-location ${active ? stale ? 'bg-warning text-dark' : 'bg-success' : 'bg-secondary'}`;
      }
      if (play && !play.hasAttribute('aria-busy')) play.textContent = active ? 'Restart on wall' : 'Start on wall';
      if (apply && !apply.hasAttribute('aria-busy')) apply.disabled = !active || stale;
      if (remove && !remove.hasAttribute('aria-busy')) {
        remove.disabled = active;
        remove.title = active ? 'Stop this receiver animation before removing it' : '';
      }
    });
  }

  function renderDashboard(state) {
    if (!state || typeof state !== 'object') return;
    currentState = state;
    renderController(state.controller);
    renderOperation(state.receiver_operation);
    renderCards(state);
  }

  async function refresh() {
    if (document.hidden) return;
    try {
      renderDashboard(await request('/api/firmware-animations'));
    } catch (error) {
      showNotice(`Could not refresh receiver status: ${error.message}`, 'danger');
    }
  }

  document.querySelectorAll('.firmware-parameters').forEach(buildControls);
  document.querySelectorAll('.firmware-preview-image').forEach((image) => {
    image.addEventListener('error', () => {
      image.hidden = true;
      const fallback = image.parentElement?.querySelector('.firmware-preview-placeholder');
      if (fallback) fallback.hidden = false;
    });
  });

  document.querySelectorAll('.firmware-animation-card').forEach((card) => {
    const id = encodeURIComponent(card.dataset.packageId);
    card.querySelector('.firmware-play')?.addEventListener('click', async (event) => {
      try {
        await whileBusy(event.currentTarget, 'Queueing…', async () => {
          await request(`/api/firmware-animations/${id}/play`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({parameters: parameterValues(card)}),
          });
          showNotice('Start request queued. Waiting for the next receiver report.');
          await refresh();
        });
      } catch (error) {
        showNotice(error.message, 'danger');
      }
    });

    card.querySelector('.firmware-live')?.addEventListener('click', async (event) => {
      try {
        await whileBusy(event.currentTarget, 'Applying…', async () => {
          await request(`/api/firmware-animations/${id}/parameters`, {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({parameters: parameterValues(card)}),
          });
          showNotice('Parameter update queued. Waiting for the next receiver report.');
          await refresh();
        });
      } catch (error) {
        showNotice(error.message, 'danger');
      }
    });

    card.querySelector('.firmware-install')?.addEventListener('click', async (event) => {
      try {
        await whileBusy(event.currentTarget, 'Queueing…', async () => {
          await request(`/api/firmware-animations/${id}/install`, {method: 'POST'});
          showNotice('Receiver installation queued. Progress will appear above the package library.');
          await refresh();
        });
      } catch (error) {
        showNotice(error.message, 'danger');
      }
    });

    card.querySelector('.firmware-delete')?.addEventListener('click', async (event) => {
      const name = card.dataset.packageName || card.dataset.packageId;
      const confirmed = window.confirm(
        `Remove “${name}” from the host library and all receiver caches? This cannot be undone from the dashboard.`
      );
      if (!confirmed) return;
      try {
        await whileBusy(event.currentTarget, 'Removing…', async () => {
          await request(`/api/firmware-animations/${id}`, {method: 'DELETE'});
          showNotice('Removal queued. The package will remain listed until the controller finishes.');
          await refresh();
        });
      } catch (error) {
        showNotice(error.message, 'danger');
      }
    });
  });

  document.getElementById('firmwareStop')?.addEventListener('click', async (event) => {
    try {
      await whileBusy(event.currentTarget, 'Stopping…', async () => {
        await request('/api/firmware-animations/stop', {method: 'POST'});
        showNotice('Stop request queued. Waiting for the next receiver report.');
        await refresh();
      });
    } catch (error) {
      showNotice(error.message, 'danger');
    }
  });

  document.getElementById('firmwareUploadForm')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!event.currentTarget.reportValidity()) return;
    const button = event.currentTarget.querySelector('button[type="submit"]');
    try {
      await whileBusy(button, 'Verifying…', async () => {
        await request('/api/firmware-animations/upload', {
          method: 'POST',
          body: new FormData(event.currentTarget),
        });
        event.currentTarget.reset();
        showNotice('Package verified and added. Receiver installation is queued.');
        await refresh();
      });
    } catch (error) {
      showNotice(error.message, 'danger');
    }
  });

  renderDashboard(currentState);
  window.setInterval(refresh, 2000);
})();
