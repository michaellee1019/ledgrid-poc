(function composerInteractions(global) {
    'use strict';

    const DIRECTIONS = Object.freeze({
        left: 'Move left', right: 'Move right', down: 'Soft drop',
        'rotate-left': 'Rotate left', 'rotate-right': 'Rotate right', drop: 'Drop',
    });

    function capabilities(component) {
        const raw = component?.browser_capabilities?.interactions
            || component?.interaction_capabilities;
        if (!raw || raw.schema !== 'ledgrid.composer-interaction-capabilities'
            || raw.schema_version !== 1
            || raw.provider !== component?.provider
            || raw.component_id !== component?.plugin_id) return null;
        const local = raw.local_preview;
        if (!local || typeof local !== 'object') return null;
        const point = local.point?.supported === true && local.point.kind === 'primary'
            ? {kind: 'primary', label: String(local.point.label || 'Interact with preview')}
            : null;
        const directions = Array.isArray(local.directions)
            ? [...new Set(local.directions.filter((value) => Object.hasOwn(DIRECTIONS, value)))]
            : [];
        return point || directions.length ? {point, directions} : null;
    }

    function mount({root, canvas, onInput}) {
        if (!(root instanceof HTMLElement) || !(canvas instanceof HTMLCanvasElement)) {
            throw new TypeError('Composer interaction controls require a panel and preview canvas.');
        }
        if (typeof onInput !== 'function') throw new TypeError('Composer interaction controls require a local input handler.');
        let current = null;
        let busy = false;

        const setStatus = (message, isError = false) => {
            const status = root.querySelector('[data-interaction-status]');
            if (!status) return;
            status.textContent = message;
            status.dataset.state = isError ? 'error' : 'ready';
        };

        const submit = async (payload) => {
            if (busy || !current) return;
            busy = true;
            root.querySelectorAll('button').forEach((button) => { button.disabled = true; });
            try {
                const result = await onInput(payload);
                setStatus(result?.accepted === false ? 'The local preview did not accept that input.' : 'Applied to local preview only.');
            } catch (error) {
                setStatus(error?.message || 'The local preview could not accept that input.', true);
            } finally {
                busy = false;
                root.querySelectorAll('button').forEach((button) => { button.disabled = false; });
            }
        };

        const onCanvasPointer = (event) => {
            if (!current?.point || busy) return;
            const bounds = canvas.getBoundingClientRect();
            if (!bounds.width || !bounds.height) return;
            const x = Math.max(0, Math.min(canvas.width - Number.EPSILON,
                (event.clientX - bounds.left) * canvas.width / bounds.width));
            const y = Math.max(0, Math.min(canvas.height - Number.EPSILON,
                (event.clientY - bounds.top) * canvas.height / bounds.height));
            void submit({kind: 'point', x, y, strength: 1});
        };

        canvas.addEventListener('pointerup', onCanvasPointer);
        root.addEventListener('click', (event) => {
            const button = event.target.closest('[data-preview-direction]');
            if (!button || !root.contains(button) || !current?.directions.includes(button.dataset.previewDirection)) return;
            void submit({kind: 'direction', direction: button.dataset.previewDirection});
        });

        return Object.freeze({
            update(component) {
                current = capabilities(component);
                root.hidden = !current;
                canvas.classList.toggle('preview-canvas--interactive', Boolean(current?.point));
                if (!current) {
                    root.replaceChildren();
                    return;
                }
                const fragment = document.createDocumentFragment();
                const heading = document.createElement('h2');
                heading.textContent = 'Preview controls';
                fragment.appendChild(heading);
                const note = document.createElement('p');
                note.textContent = current.point
                    ? `${current.point.label} by tapping the preview. Controls stay on this device.`
                    : 'Directional controls stay on this device.';
                fragment.appendChild(note);
                if (current.directions.length) {
                    const controls = document.createElement('div');
                    controls.className = 'interaction-direction-controls';
                    controls.setAttribute('aria-label', 'Local preview directional controls');
                    current.directions.forEach((direction) => {
                        const button = document.createElement('button');
                        button.type = 'button';
                        button.dataset.previewDirection = direction;
                        button.textContent = DIRECTIONS[direction];
                        controls.appendChild(button);
                    });
                    fragment.appendChild(controls);
                }
                const status = document.createElement('p');
                status.dataset.interactionStatus = '';
                status.setAttribute('role', 'status');
                status.setAttribute('aria-live', 'polite');
                status.textContent = 'Local preview only. No wall command is available.';
                fragment.appendChild(status);
                root.replaceChildren(fragment);
            },
            dispose() {
                canvas.removeEventListener('pointerup', onCanvasPointer);
                root.replaceChildren();
                root.hidden = true;
                canvas.classList.remove('preview-canvas--interactive');
                current = null;
            },
        });
    }

    global.LEDGridComposerInteractions = Object.freeze({capabilities, mount});
})(window);
